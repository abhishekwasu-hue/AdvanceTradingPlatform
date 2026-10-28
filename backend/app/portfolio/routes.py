"""Phase M / V4.4: `GET /api/portfolio/exposure` - the tenant's open book with live prices when a
broker session is usable (else entry prices, flagged)."""
from typing import Dict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.brokers.gates import data_sources
from app.brokers.token_lifecycle import build_adapter, token_is_usable
from app.db.models import BrokerCredentialRecord, User
from app.db.session import get_session
from app.portfolio import engine
from app.risk_engine.routes import get_tenant_risk_config
from app.core import config as app_config

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("/risk")
async def risk(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """N1 F1 (flag `portfolio_risk`): the caller's own PAPER book as a risk view - exposure, open risk at the stops by
    bucket, beta to NIFTY 50, direction. Read-only; another member's book is never included."""
    from app.platform.controls import require_flag
    from app.portfolio import risk_data
    await require_flag(session, risk_data.FLAG, user.tenant_id)
    out = await risk_data.risk_view(session, user)
    from app.portfolio import caps                             # N1 F3: the user's own limits on new entries (F2), when on
    limits = await caps.limits_of(session, user) if await caps.enabled(session, user.tenant_id) else None
    out["limits"] = ({"max_sector_pct": limits.max_sector_pct, "max_correlated_risk_pct": limits.max_correlated_risk_pct,
                      "max_open_positions": limits.max_open_positions} if limits is not None else None)
    return out


@router.get("/exposure")
async def exposure(live_prices: bool = Query(default=True), user: User = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)) -> dict:
    trades = await engine.open_trades(session, user.tenant_id)
    risk = await get_tenant_risk_config(user.tenant_id, session)
    # DEF-0: the organisation's own capital, else the PAPER account's starting balance - never a built-in 100,000.
    capital = float(risk.capital) if risk is not None and risk.capital is not None else float(app_config.PAPER_STARTING_CAPITAL)
    prices: Dict[str, float] = {}
    price_source = "entry"
    if live_prices and trades:
        from app.market_data import live_access
        exchange_of = {t.symbol: (t.exchange or "NSE") for t in trades}
        if await live_access.byob_on(session, user.tenant_id):
            # BT1b: prices only through this user's own confirmed session, for the symbols inside its budget; the rest
            # (and everything without such a session) are valued at entry.
            try:
                own = await live_access.user_source(session, user, sorted(exchange_of.items()), priority=live_access.Priority.POSITION)
                adapter = own.adapter()
                symbols = [s for s in exchange_of if own.allows(s, exchange_of[s])]
            except live_access.LiveDataRefused:
                adapter, symbols = None, []
        else:
            stored = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == user.tenant_id)))
            usable = [r for r in data_sources(stored) if token_is_usable(r)]    # Part 1B: no gated price source
            adapter = build_adapter(usable[0]) if usable else None
            symbols = list({t.symbol for t in trades})
        if adapter is not None:
            for symbol in symbols:
                try:
                    prices[symbol] = float(await adapter.get_ltp_for_symbol(symbol, next(t.exchange or "NSE" for t in trades if t.symbol == symbol)))
                except Exception:  # noqa: BLE001 - fall back to entry for that symbol
                    continue
            price_source = "ltp" if prices else "entry"
    # Phase P3: express every symbol in the tenant's base currency before aggregating.
    from app.db.models import Tenant
    from app.fx import service as fx
    tenant = await session.get(Tenant, user.tenant_id)
    base_ccy = (getattr(tenant, "base_currency", None) or "INR").upper()
    rates = await fx.load_rates(session)
    fx_used, fx_missing = {}, []
    converted = dict(prices)
    for trade in trades:
        ccy = fx.quote_currency(trade.symbol)
        if ccy == base_ccy:
            continue
        try:
            factor = fx.convert(1.0, ccy, base_ccy, rates)
        except fx.FxError:
            fx_missing.append(f"{ccy}->{base_ccy}")
            continue
        fx_used[f"{ccy}->{base_ccy}"] = factor
        converted[trade.symbol] = (prices.get(trade.symbol) or float(trade.entry_price)) * factor
    snap = engine.snapshot(trades, converted, capital=capital, realised_today=await engine.realised_today(session, user.tenant_id))
    out = snap.to_dict()
    out["price_source"] = price_source
    out["priced_symbols"] = sorted(prices)
    out["base_currency"] = base_ccy
    out["fx_rates_used"] = fx_used
    out["fx_missing"] = sorted(set(fx_missing))
    return out
