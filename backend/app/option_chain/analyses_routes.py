"""OC2 (owner's order, PART C1): `GET /api/option-chain/analyses?symbol=&expiry=` - the six built-in option chain
analyses (app/option_chain/analyses.py) for the caller's own broker data, behind the `ui_v2` flag (404 while off, as
`GET /api/signals/feed`).

* **The chain** is read through the caller's organisation's broker session, exactly as `POST /api/market-data/option-chains`
  reads it (or, with the bring-your-own-broker data tier on, through the caller's own session inside its budget), and is
  counted in the same `market_data_chains` usage and per-user rate limit.
* **Earlier snapshots of the session** are the OI collector's stored slots for today (`oi_snapshots`,
  app/option_chain/snapshots.py), kept only when they hold the same expiry. With the bring-your-own-broker tier on
  (B1) they are the user's OWN earlier reads instead: every chain read here through his own session is stored as his
  session snapshot (app/option_chain/own_snapshots.py, tenant- and user-private) and read back by him only; until one
  exists the path tabs say the path through the session starts now.
* **Plain states, never sample data:** `no_broker` (no usable broker session), `unavailable` (the broker did not
  return a chain), `no_data` (an empty chain), `ok`. Only `ok` carries analyses.

It stores the usage count and, with the own-broker tier on, the user's own chain read (B1); no order path is reached
(ADR-0006).
"""
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.billing.service import meter
from app.brokers.models import OptionChain
from app.db.models import BrokerCredentialRecord, User
from app.db.session import get_session
from app.market_data import candles_routes, entitlement
from app.market_data.fanin import Priority
from app.option_chain import analyses, as_of, dish, own_snapshots, snapshots
from app.option_chain.snapshots import IST

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/option-chain", tags=["option-chain"])
FLAG = "ui_v2"
DISH_FLAG = "markets_view"   # MO4 slice 2: the "What the chain shows" summary in the answer
EXCHANGE = "NFO"            # an option chain is read in the F&O segment (as the option-chains route budgets it)

NO_BROKER = "Connect your broker and log in to it today to see these analyses. They use your own broker's option chain."
UNAVAILABLE = "Your broker did not return the option chain just now. Try again in a moment."
NOT_OFFERED = "Your broker's option chain is not available here."
NO_CHAIN = "Your broker returned no strikes for this symbol and expiry."


async def _record(session: AsyncSession, user: User, underlying: str) -> tuple:
    """(credential, own-broker tier on, refusal sentence). Mirrors the option-chains route's choice of session."""
    if await candles_routes._byob(session, user):
        ents = await entitlement.resolve_many(session, user, [(underlying, EXCHANGE)], priority=Priority.CHART)
        ent = ents.get((underlying, EXCHANGE))
        if ent is None or not ent.live or ent.record is None:
            return None, True, (ent.reason if ent is not None and ent.reason else NO_BROKER)
        return ent.record, True, None
    try:
        return await candles_routes._pick_record(session, user.tenant_id, None, "primary"), False, None
    except HTTPException:
        return None, False, NO_BROKER


async def _history(session: AsyncSession, tenant_id: int, underlying: str, now: datetime) -> List[analyses.Snapshot]:
    """Today's stored collector slots of this organisation for the underlying, read before `now` (the expiry is matched
    in analyses.py)."""
    slots = await snapshots.day_chains(session, underlying, now.astimezone(IST).date(), tenant_id=tenant_id)
    return [analyses.Snapshot(s.captured_at, s.chain) for s in slots if s.captured_at < now]


async def _own_history(session: AsyncSession, user: User, underlying: str, now: datetime) -> List[analyses.Snapshot]:
    """B1: the user's own stored reads of today (own-broker tier), read before `now` (every expiry; matched in analyses.py)."""
    stored = await own_snapshots.day(session, user, underlying, now.astimezone(IST).date())
    return [analyses.Snapshot(s.captured_at, s.chain()) for s in stored if s.captured_at < now]


def _state(base: Dict[str, Any], state: str, message: str) -> Dict[str, Any]:
    return {**base, "state": state, "message": message, "analyses": []}


async def _require_flag(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> None:
    """404 while `ui_v2` is off. Listed before the rate limit, so a request to a switched-off page spends no budget."""
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": FLAG})


# The flag first, then the same per-user budget as POST /api/market-data/option-chains (each call is one broker chain read).
@router.get("/analyses", dependencies=[Depends(_require_flag), Depends(candles_routes.chains_rate_limit)])
async def option_chain_analyses(symbol: str = Query(..., min_length=1, max_length=30), expiry: Optional[date] = Query(default=None),
                                user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Support / resistance from OI, OI build-up per strike, PCR, max pain, the expected move and the IV skew for one
    underlying and expiry (the broker's nearest expiry when none is given)."""
    try:
        underlying = snapshots.normalise_underlying(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Enter an index or stock name, for example NIFTY.") from exc
    now = datetime.now(timezone.utc)
    base: Dict[str, Any] = {"symbol": underlying, "expiry": expiry.isoformat() if expiry else None, "generated_at": now.isoformat()}

    record, byob, refusal = await _record(session, user, underlying)
    if record is None:
        return _state(base, "no_broker", refusal or NO_BROKER)
    from app.market_data import hub as data_hub
    chain, failure = await _read_chain(record, underlying, expiry, shared=await data_hub.hub_on(session, user.tenant_id))
    if chain is None:
        return _state(base, "unavailable", failure or UNAVAILABLE)
    base["expiry"] = (chain.expiry or "")[:10] or base["expiry"]
    # Counted like POST /api/market-data/option-chains: every chain the broker returned, an empty one too.
    await meter(session, user.tenant_id, "market_data_chains", quantity=1, source="api",
                metadata={"broker": record.broker_name, "expiry": base["expiry"], "use": "option_chain_analyses"}, commit=True)
    if not chain.rows:
        return _state(base, "no_data", NO_CHAIN)

    read = chain                                             # B1: stored as read (below), the spot as the broker sent it
    if byob:                                                 # his own earlier reads are his session
        history = await _own_history(session, user, underlying, now)
    else:
        history = await _history(session, user.tenant_id, underlying, now)
    chain = analyses.clean_spot(chain)                       # a spot of 0 (some brokers' "no price") is missing, not a number
    # B2: the chain's capture time, or the last session close while the market is closed (never the request time); a
    # closed market's missing or strike-rounded spot is the index's last close from our own candles, labelled.
    when = await as_of.chain_as_of(session, underlying, now)
    spot, spot_source = await as_of.resolve_spot(user, session, underlying, chain, chain.underlying_ltp, when,
                                                 record.broker_name, record.account_label)
    if spot != chain.underlying_ltp:
        chain = chain.model_copy(update={"underlying_ltp": spot})
    kept = analyses.session_history(chain, history)
    from app.market_data.calendar import family_holidays
    from app.option_chain.chain_view import spot_instrument
    closed = await family_holidays(session, spot_instrument(underlying)[1])   # B3: trading days to expiry (BSE for SENSEX)
    results = analyses.compute_all(chain, history, now, holidays=closed)
    out: Dict[str, Any] = {**base, "state": "ok", "message": None, "spot": chain.underlying_ltp, "spot_source": spot_source,
                           "data_as_of": when.at.isoformat(), "market_closed": when.closed,
                           "source": {"broker": record.broker_name, "account_label": record.account_label},
                           "session": {"snapshots": len(kept), "since": kept[0].at.isoformat() if kept else None, "note": None},
                           "near_atm_strikes": analyses.NEAR_ATM_STRIKES,
                           "analyses": results}
    # MO4 slice 2: "What the chain shows" (app/option_chain/dish.py) from the same six analyses - with `markets_view` on only.
    from app.platform.controls import flag_enabled
    if await flag_enabled(session, DISH_FLAG, user.tenant_id):
        from app.me import prefs
        own, _ = await prefs.read_one("option_chain", user, session)      # B4: his own PCR zone edges
        zones = own.get("pcr_zones") or {}
        raw = [zones.get(k) for k in ("very_low_below", "low_below", "balanced_max", "high_max")]
        edges = [float(e) for e in raw if isinstance(e, (int, float))]
        out["dish"] = dish.build(results, chain, history, pcr_edges=edges if len(edges) == 4 else None)
    if byob:                                                 # B1: this read becomes his session snapshot (last: the answer is built)
        await own_snapshots.record(session, user, record, underlying, read, now)
    return out


async def _read_chain(record: BrokerCredentialRecord, underlying: str, expiry: Optional[date], *,
                      shared: bool = False) -> Tuple[Optional[OptionChain], Optional[str]]:
    """(chain, None) or (None, the plain sentence). Building the adapter is inside the guard too (a bad stored credential).
    `shared` (TM Part 1, flag data_hub): the connection's chain snapshot of the last few seconds when there is one."""
    try:
        adapter = candles_routes.build_adapter(record)
        if shared:
            from app.market_data import hub as data_hub
            return await data_hub.shared_chain(record.id, underlying, expiry, lambda: adapter.get_option_chain(underlying, expiry)), None
        return await adapter.get_option_chain(underlying, expiry), None
    except NotImplementedError:
        return None, NOT_OFFERED
    except Exception as exc:  # noqa: BLE001 - any broker failure is the same plain state for the page
        logger.info("Option chain analyses: chain read failed for %s via %s: %s", underlying, record.broker_name, type(exc).__name__)
        return None, UNAVAILABLE
