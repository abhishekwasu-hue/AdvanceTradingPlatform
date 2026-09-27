"""Phase M / V4.4: `GET /api/portfolio/exposure` - the tenant's open book with live prices when a
broker session is usable (else entry prices, flagged)."""
from typing import Dict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.brokers.token_lifecycle import build_adapter, token_is_usable
from app.db.models import BrokerCredentialRecord, User
from app.db.session import get_session
from app.portfolio import engine
from app.risk_engine.routes import get_tenant_risk_config

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("/exposure")
async def exposure(live_prices: bool = Query(default=True), user: User = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)) -> dict:
    trades = await engine.open_trades(session, user.tenant_id)
    risk = await get_tenant_risk_config(user.tenant_id, session)
    capital = float(risk.capital) if risk is not None else 100_000.0
    prices: Dict[str, float] = {}
    price_source = "entry"
    if live_prices and trades:
        stored = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == user.tenant_id)))
        usable = [r for r in stored if token_is_usable(r)]
        if usable:
            adapter = build_adapter(usable[0])
            for symbol in {t.symbol for t in trades}:
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
