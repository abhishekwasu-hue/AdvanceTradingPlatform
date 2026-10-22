"""FNO-3 (owner order ATP_SESSION_FNO_WORKSPACE.md, sections 1 and 4): the symbol hub's reads. Behind `ui_v2` (404 while
it is off), like the other v2 option chain reads (app/option_chain/routes.py, OC1).

* `GET /api/option-chain/{symbol}/hub`      whether the symbol has F&O, its lot size and next expiries - the instrument
  master only (platform-wide data, no broker call). The hub shows its Option chain and Futures tabs only when it has F&O.
* `GET /api/option-chain/{symbol}/futures`  the futures chain: every listed future with LTP, change, OI, OI change and
  basis vs the spot, and the rollover near -> next (app/option_chain/futures_chain.py). The prices come from a broker
  session exactly as `POST /api/market-data/quotes` picks one: the organisation's first usable session, or - with the
  bring-your-own-broker tier on (BT1) - the user's own confirmed session within its symbol budget; 409 when none can
  serve it (the page shows the broker gate's words). ONE multi-quote broker call where the adapter has one, else one
  quote per contract, each drawing a token from the session's API budget (`api_budget.take`). Rate limited per user.

Reads only: nothing here places, changes or suggests an order (ADR-0006). No sample data: a price that cannot be read
is missing, with the reason.
"""
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.brokers.models import Quote
from app.brokers.token_lifecycle import build_adapter
from app.core.rate_limit import user_rate_limit
from app.db.models import BrokerCredentialRecord, User
from app.db.session import get_session, release_connection
from app.market_data import api_budget, entitlement
from app.market_data.calendar import family_holidays, market_session_status
from app.market_data.candles_routes import _byob, _pick_record, _tier_c
from app.market_data.entitlement import Entitlement
from app.market_data.fanin import Priority
from app.option_chain import chain_view, futures_chain
from app.option_chain.routes import _holiday_family, _symbol, _v2_only

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/option-chain", tags=["option-chain"])

# The Futures tab polls every 15 s while it is shown (4 a minute); 20 leaves room for a symbol switch or a second tab.
futures_rate_limit = user_rate_limit("option_chain_futures", limit=20, window_seconds=60)
# The hub facts are read once per symbol a page opens; 60 a minute is far above a person's pace.
hub_rate_limit = user_rate_limit("option_chain_hub", limit=60, window_seconds=60)
QUOTE_WAIT_SECONDS = 2.0          # no broker call starts after this (the budget is shared with the trading worker)
Pair = Tuple[str, str]
BUDGET_REASON = "The broker's rate budget for this session is in use; the price loads on the next refresh."


@router.get("/{underlying}/hub", dependencies=[Depends(hub_rate_limit)])
async def hub(underlying: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _v2_only(session, user)
    name = _symbol(underlying)
    holidays = await family_holidays(session, _holiday_family(name))
    return await futures_chain.hub_info(session, underlying, chain_view.today_ist(), sorted(holidays))


async def _quotes(record: BrokerCredentialRecord, pairs: List[Pair]) -> Tuple[Dict[Pair, Quote], Dict[Pair, str], int]:
    """(quotes, reasons for the pairs without one, broker calls made) - one multi-quote call where the adapter has one,
    else one quote per pair; every call takes a token from the session's API budget first."""
    adapter = build_adapter(record)
    deadline = time.monotonic() + QUOTE_WAIT_SECONDS
    got: Dict[Pair, Quote] = {}
    reasons: Dict[Pair, str] = {}
    calls = 0
    spare = 0                     # a token taken for a batch call the adapter then did not make

    async def token() -> bool:
        return time.monotonic() < deadline and await api_budget.take(record.id, record.broker_name, max(0.0, deadline - time.monotonic()))

    if getattr(adapter, "multi_quote", False):
        if not await token():
            return {}, {p: BUDGET_REASON for p in pairs}, 0
        try:
            batch = await adapter.get_quotes_batch(pairs)
        except Exception as exc:  # noqa: BLE001 - the broker's words next to each contract
            logger.info("Futures chain batch quote failed via %s: %s", record.broker_name, exc)
            return {}, {p: f"{record.broker_name}: {type(exc).__name__}"[:200] for p in pairs}, 1
        if batch is not None:
            return {p: q for p, q in batch.items() if p in pairs}, {}, 1
        spare = 1                 # no call for this list after all: the token goes to the first per-symbol call
    for i, pair in enumerate(pairs):
        if spare:
            spare = 0
        elif not await token():
            reasons.update({p: BUDGET_REASON for p in pairs[i:] if p not in got})
            break
        calls += 1
        try:
            quote = await adapter.get_quote_for_symbol(pair[1], pair[0])
        except Exception as exc:  # noqa: BLE001 - one contract's failure must not lose the others
            logger.info("Futures chain quote failed for %s:%s via %s: %s", pair[0], pair[1], record.broker_name, exc)
            reasons[pair] = f"{record.broker_name}: {type(exc).__name__}"[:200]
            continue
        if quote is not None:
            got[pair] = quote
    return got, reasons, calls


@router.get("/{underlying}/futures", dependencies=[Depends(futures_rate_limit)])
async def futures(underlying: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _v2_only(session, user)
    name = _symbol(underlying)
    now = datetime.now(timezone.utc)
    today = chain_view.today_ist(now)
    listed = await futures_chain.listed_futures(session, name, today)
    spot_symbol, spot_exchange = chain_view.spot_instrument(name)
    if not listed:
        return {"underlying": name, "spot": {"symbol": spot_symbol, "exchange": spot_exchange, "ltp": None, "change": None, "change_pct": None},
                "rows": [], "rollover": None, "oi_change": {"status": "none", "source": None, "since": None},
                "message": f"{name} has no futures in the instrument list.", "fetched_at": now.isoformat()}
    spot_pair: Pair = (spot_exchange, spot_symbol)
    pairs: List[Pair] = [spot_pair] + [(f.exchange, f.tradingsymbol) for f in listed]
    live: Optional[Entitlement] = None
    if await _byob(session, user):
        # BT1: the user's own confirmed session only, symbols inside its budget only.
        ents = await entitlement.resolve_many(session, user, [(s, e) for e, s in pairs], priority=Priority.CHART, now=now)
        live = next((e for e in ents.values() if e.live), None)
        if live is None or live.record is None:
            raise _tier_c(ents)
        record: BrokerCredentialRecord = live.record
        refused = {(e, s): (ent.reason or "EOD data only for this symbol") for (s, e), ent in ents.items() if not ent.live}
        wanted = [p for p in pairs if p not in refused]
    else:
        record = await _pick_record(session, user.tenant_id, None, "primary")
        refused, wanted = {}, pairs
    status = await market_session_status(session, now, spot_exchange)
    await release_connection(session)       # the broker calls below can take seconds; no pool connection is held
    quotes, reasons, calls = await _quotes(record, wanted)
    view = futures_chain.futures_view(name, listed, quotes, spot_pair, now=now, errors={**reasons, **refused})
    state, changes = await futures_chain.oi_change_state(user.tenant_id, name, {r["tradingsymbol"]: r["oi"] for r in view["rows"]}, now,
                                                         status.is_open)
    for row in view["rows"]:
        row["oi_change"] = changes.get(row["tradingsymbol"])
    view.update(oi_change=state, market_open=status.is_open, source={"broker": record.broker_name, "account_label": record.account_label},
                broker_calls=calls, message=None)
    if live is not None:
        view["data_tier"] = live.badge(now=now)
    if wanted and not quotes and reasons and all(r == BUDGET_REASON for r in reasons.values()):
        raise HTTPException(status_code=429, detail=BUDGET_REASON, headers={"Retry-After": "5"})
    return view
