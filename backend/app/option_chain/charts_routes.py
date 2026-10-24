"""OC5 (owner's order, handover 2c): `GET /api/option-chain/charts?symbol=&expiry=&strikes=10&interval=5` - the series
behind the Option chain "Charts" view (app/option_chain/charts.py), behind the `ui_v2` flag (404 while off, like
`GET /api/option-chain/analyses`).

* **The chain now** is read through the caller's organisation's broker session exactly as the analyses read it (or, with
  the bring-your-own-broker data tier on, the caller's own session), counted in the same `market_data_chains` usage and
  per-user rate limit (`chains_rate_limit`, shared with the chain and the analyses).
* **The session** is the OI collector's stored slots of today (`oi_snapshots` / `strike_oi_snapshots`), today's
  first-seen OI per strike (`oi_day_baselines`, used only when today's first stored slot is this expiry) and the last
  stored chain of earlier sessions (the IV change). With the own-broker tier on none of these shared rows are read.
* **Plain states, never sample data:** `no_broker`, `unavailable`, `no_data`, `ok` (as the analyses). A chart without
  two readings yet says it builds through the session.

Read-only: it stores nothing but the usage count, and no order path is reached (ADR-0006).
"""
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.billing.service import meter
from app.core import config
from app.db.models import User
from app.db.session import get_session
from app.market_data import candles_routes
from app.option_chain import analyses, as_of, charts, snapshots
from app.option_chain.analyses_routes import NO_BROKER, NO_CHAIN, UNAVAILABLE, _read_chain, _record, _require_flag
from app.option_chain.snapshots import IST

router = APIRouter(prefix="/api/option-chain", tags=["option-chain"])
EARLIER_SESSIONS = 15        # stored sessions read for the IV change (the Trade baseline keeps up to 10 sideways ones among them)


def _state(base: Dict[str, Any], state: str, message: str) -> Dict[str, Any]:
    return {**base, "state": state, "message": message, "charts": [], "facts": None}


async def _session_data(session: AsyncSession, tenant_id: int, underlying: str, chain_expiry: str, now: datetime):
    """(today's stored readings, the baselines when they belong to this expiry, the earlier sessions' closes)."""
    today = now.astimezone(IST).date()
    slots = await snapshots.day_chains(session, underlying, today, tenant_id=tenant_id)
    readings = [charts.Reading(s.captured_at, s.chain, s.future_price, s.future_oi) for s in slots]
    baselines: Dict[float, Any] = {}
    if slots and analyses._same_expiry(slots[0].chain.expiry, chain_expiry):
        baselines = await snapshots.baselines(session, underlying, today, tenant_id=tenant_id)
    closes = await snapshots.recent_session_closes(session, underlying, today, EARLIER_SESSIONS, tenant_id=tenant_id)
    earlier = [charts.EarlierSession(c.day, c.last.captured_at, analyses.clean_spot(c.last.chain), c.prices) for c in closes]
    return readings, baselines, earlier


@router.get("/charts", dependencies=[Depends(_require_flag), Depends(candles_routes.chains_rate_limit)])
async def option_chain_charts(symbol: str = Query(..., min_length=1, max_length=30), expiry: Optional[date] = Query(default=None),
                              strikes: int = Query(default=charts.DEFAULT_EACH_SIDE, ge=1, le=charts.MAX_EACH_SIDE),
                              interval: int = Query(default=5), user: User = Depends(get_current_user),
                              session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """OI by strike and its change today, OI per strike / in total / PCR / max pain / the ATM straddle / ATM IV through
    the session, the IV smile and the future's price vs OI, for one underlying and expiry (the broker's nearest when
    none is given), over ATM and `strikes` listed strikes either side; `interval` 5, 10 or 15 minutes."""
    try:
        underlying = snapshots.normalise_underlying(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Enter an index or stock name, for example NIFTY.") from exc
    if interval not in charts.INTERVALS:
        raise HTTPException(status_code=422, detail="Pick a 5, 10 or 15 minute view.")
    now = datetime.now(timezone.utc)
    base: Dict[str, Any] = {"symbol": underlying, "expiry": expiry.isoformat() if expiry else None, "generated_at": now.isoformat(),
                            "strikes": strikes, "interval": interval}

    record, byob, refusal = await _record(session, user, underlying)
    if record is None:
        return _state(base, "no_broker", refusal or NO_BROKER)
    chain, failure = await _read_chain(record, underlying, expiry)
    if chain is None:
        return _state(base, "unavailable", failure or UNAVAILABLE)
    base["expiry"] = (chain.expiry or "")[:10] or base["expiry"]
    await meter(session, user.tenant_id, "market_data_chains", quantity=1, source="api",
                metadata={"broker": record.broker_name, "expiry": base["expiry"], "use": "option_chain_charts"}, commit=True)
    if not chain.rows:
        return _state(base, "no_data", NO_CHAIN)

    readings: List[charts.Reading] = []
    baselines: Dict[float, Any] = {}
    earlier: List[charts.EarlierSession] = []
    if not byob:
        readings, baselines, earlier = await _session_data(session, user.tenant_id, underlying, chain.expiry, now)
    settings = await snapshots.settings_for(session, user.tenant_id, underlying)
    body = charts.compute(chain, readings, now, each_side=strikes, interval=interval, baselines=baselines, earlier=earlier,
                          settings=settings, collect_span=config.OI_BANNER_COLLECT_SPAN, history_off=byob)
    spot = analyses.clean_spot(chain).underlying_ltp
    # B2a: the chain's capture time, or the last session close while the market is closed (never the request time).
    when = await as_of.chain_as_of(session, underlying, now)
    spot, spot_source = await as_of.resolve_spot(user, session, underlying, chain, spot, when, record.broker_name, record.account_label)
    return {**base, "state": "ok", "message": None, "spot": spot, "spot_source": spot_source, "data_as_of": when.at.isoformat(),
            "market_closed": when.closed,
            "source": {"broker": record.broker_name, "account_label": record.account_label}, **body}


__all__ = ["router", "EARLIER_SESSIONS"]
