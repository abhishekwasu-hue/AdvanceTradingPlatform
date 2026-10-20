"""UI2-4a2-pre: batch quotes for the Charts page's watchlist - `POST /api/market-data/quotes`.

Up to MAX_ITEMS (exchange, symbol) pairs in one request; each comes back with its last price and its change from the
previous session's close, or as missing with the reason. Read-only and tenant-scoped, behind the same broker gate and
entitlement guard as the single-symbol `/ltp` (`candles_routes`): the organisation's first usable broker session, or -
with the bring-your-own-broker tier on (BT1) - the user's own confirmed session, symbols outside its budget answering
EOD-only per item (all of them outside: a 409 with `X-Data-Tier: C`).

Where the prices come from, cheapest for the broker allowance first:
1. **The broker's multi-quote call** where the adapter has one (`BrokerInterface.multi_quote`: Kite, Upstox, Dhan,
   Fyers, Angel One): ONE broker call for the whole list, counted ONCE against the credential's quote budget
   (`api_budget.take`, the same budget as `/ltp` - a quote never uses the history burst).
2. Otherwise (and for anything that call did not price): **the live feed's fresh ticks** - the session's own, then the
   shared feed of the same broker - which cost nothing.
3. Then **one quote per symbol within the budget**: the price `/ltp` shared in the last few seconds first, else a broker
   call that takes a token; the request waits for tokens at most QUOTE_WAIT_SECONDS in all, and after the first refusal
   the remaining symbols are marked "budget used" at once (they load on the next poll).

Never sample data: a symbol with no real price (none returned, a zero or a failure) comes back `missing: true` with
`ltp: null` and the reason - never 0. Rate limited per user (the panel polls every 10 s while visible). Nothing is
stored and no order path is reached.
"""
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.brokers.models import Quote
from app.brokers.token_lifecycle import build_adapter
from app.core.rate_limit import user_rate_limit
from app.db.models import BrokerCredentialRecord, User
from app.db.session import get_session, release_connection
from app.market_data import api_budget, entitlement
from app.market_data.candles_routes import _byob, _eod_context, _pick_record, _tier_c
from app.market_data.entitlement import Entitlement
from app.market_data.fanin import Priority
from app.market_data.freshness import quote_is_stale
from app.market_data.stream import TickCache, tick_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market-data", tags=["market-data"])

MAX_ITEMS = 50
QUOTE_WAIT_SECONDS = 2.0       # in all, per request, for per-symbol quote tokens (the batch call waits like /ltp)
# The watchlist polls every 10 s while its tab is visible (6 a minute); 30 leaves room for a list switch or a second tab.
quotes_rate_limit = user_rate_limit("market_data_quotes", limit=30, window_seconds=60)

Pair = Tuple[str, str]          # (exchange, symbol)


class QuoteItem(BaseModel):
    symbol: str = Field(min_length=1, max_length=60)
    exchange: str = Field(default="NSE", min_length=2, max_length=10)

    @field_validator("symbol", "exchange")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("must not be blank")
        return v


class QuotesBody(BaseModel):
    items: List[QuoteItem] = Field(min_length=1, max_length=MAX_ITEMS)
    broker: Optional[str] = Field(default=None, description="a stored broker; omitted = the first usable session")
    account_label: str = Field(default="primary", max_length=50)


def _missing(pair: Pair, reason: str, **extra: Any) -> dict:
    return {"symbol": pair[1], "exchange": pair[0], "ltp": None, "prev_close": None, "change": None, "change_pct": None,
            "timestamp": None, "age_seconds": None, "stale": None, "stale_reason": None, "source": None, "missing": True,
            "error": reason, **extra}


def _priced(pair: Pair, ltp: Any, *, prev_close: Any, timestamp: Optional[datetime], source: str, now: datetime,
            unknown_age_is_stale: bool) -> Optional[dict]:
    """The row for a real price, or None when there is none (no value, zero or negative: never shown as a price)."""
    try:
        price = float(ltp)
    except (TypeError, ValueError):
        return None
    if not price > 0:
        return None
    prev = float(prev_close) if isinstance(prev_close, (int, float)) and prev_close > 0 else None
    change = round(price - prev, 4) if prev is not None else None
    if timestamp is not None and timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    reason = quote_is_stale(timestamp, now) if timestamp is not None else None
    if timestamp is None and unknown_age_is_stale:
        reason = "the broker gave no time for this price, so its age is unknown"
    return {"symbol": pair[1], "exchange": pair[0], "ltp": price, "prev_close": prev, "change": change,
            "change_pct": round(change / prev * 100, 4) if change is not None and prev else None,
            "timestamp": timestamp.isoformat() if timestamp is not None else None,
            "age_seconds": round((now - timestamp).total_seconds(), 1) if timestamp is not None else None,
            "stale": reason is not None, "stale_reason": reason, "source": source, "missing": False, "error": None}


def _from_quote(pair: Pair, quote: Optional[Quote], source: str, now: datetime, byob: bool) -> Optional[dict]:
    if quote is None:
        return None
    return _priced(pair, quote.ltp, prev_close=quote.prev_close, timestamp=quote.timestamp, source=source, now=now,
                   unknown_age_is_stale=byob)


async def _from_ticks(pair: Pair, broker: str, feeds: List[TickCache], now: datetime, byob: bool) -> Optional[dict]:
    exchange, symbol = pair
    for feed in feeds:
        tick = await feed.fresh(broker, exchange, symbol, now)
        if tick is not None:
            return _priced(pair, tick.ltp, prev_close=None, timestamp=tick.best_ts, source="tick", now=now, unknown_age_is_stale=byob)
    return None


@router.post("/quotes", dependencies=[Depends(quotes_rate_limit)])
async def quotes(body: QuotesBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    pairs: List[Pair] = list(dict.fromkeys((item.exchange, item.symbol) for item in body.items))
    now = datetime.now(timezone.utc)
    byob = await _byob(session, user)
    ents: Dict[Tuple[str, str], Entitlement] = {}
    live: Optional[Entitlement] = None
    if byob:
        # BT1: the user's own confirmed session only, symbols inside its budget only (a watchlist ranks below charts).
        ents = await entitlement.resolve_many(session, user, [(s, e) for e, s in pairs], broker=body.broker,
                                              account_label=body.account_label if body.broker else None,
                                              priority=Priority.WATCHLIST, now=now)
        live = next((e for e in ents.values() if e.live), None)
        if live is None or live.record is None or live.session is None:
            raise _tier_c(ents)
        record: BrokerCredentialRecord = live.record
        # The session's own ticks, then the shared live feed of the same broker (the price is public market data).
        feeds = [TickCache(namespace=live.session.namespace), tick_cache]
    else:
        record = await _pick_record(session, user.tenant_id, body.broker, body.account_label)
        feeds = [tick_cache]
    eod_date, holidays = await _eod_context(session, ents)
    await release_connection(session)       # the broker calls below can take seconds; no pool connection is held

    rows: Dict[Pair, dict] = {}
    for pair in pairs:
        ent = ents.get((pair[1], pair[0]))
        if byob and (ent is None or not ent.live):
            rows[pair] = _missing(pair, ent.reason if ent and ent.reason else "EOD data only for this symbol",
                                  data_tier=ent.badge(eod_date=eod_date, now=now, holidays=holidays) if ent else None)
    wanted = [p for p in pairs if p not in rows]
    adapter = build_adapter(record)
    broker_calls = 0
    budget_used = False
    warnings: List[str] = []
    batch_error: Optional[str] = None

    # 1. The broker's multi-quote call: one call, one token, for the whole list.
    if wanted and getattr(adapter, "multi_quote", False):
        if await api_budget.take(record.id, record.broker_name):
            broker_calls += 1
            try:
                got = await adapter.get_quotes_batch(wanted) or {}
            except Exception as exc:  # noqa: BLE001 - the broker's words next to each symbol; ticks may still price some
                logger.info("Batch quote failed via %s: %s", record.broker_name, exc)
                got = {}
                batch_error = f"{record.broker_name}: {type(exc).__name__}: {exc}"[:300]
                warnings.append(batch_error)
            for pair in wanted:
                row = _from_quote(pair, got.get(pair), "quote", now, byob)
                if row is not None:
                    rows[pair] = row
        else:
            budget_used = True
    batch_tried = broker_calls > 0 or budget_used

    # 2. Fresh ticks of the live feed (free).
    for pair in [p for p in wanted if p not in rows]:
        row = await _from_ticks(pair, record.broker_name, feeds, now, byob)
        if row is not None:
            rows[pair] = row

    # 3. One quote per symbol within the budget - only where there is no multi-quote call (with one, its answer stands:
    #    a symbol it did not price is missing, not worth a second call per symbol).
    unpriced: List[Pair] = []          # asked of the broker, no price back
    if not batch_tried:
        deadline = time.monotonic() + QUOTE_WAIT_SECONDS
        for pair in [p for p in wanted if p not in rows]:
            row = await _shared_quote(record, pair, now, byob)                 # what /ltp fetched moments ago: free
            if row is None and not budget_used:
                row, calls, budget_used = await _one_quote(adapter, record, pair, now, byob, deadline)
                broker_calls += calls
                if row is None and calls:
                    unpriced.append(pair)
            if row is not None:
                rows[pair] = row
    else:
        unpriced = [p for p in wanted if p not in rows] if broker_calls else []

    for pair in wanted:
        if pair in rows:
            continue
        if pair in unpriced or not budget_used:
            rows[pair] = _missing(pair, batch_error or "no price from the broker for this symbol - check it is the broker's trading symbol "
                                                     "on this exchange")
        else:
            rows[pair] = _missing(pair, "budget used - the broker's rate budget for this session is in use; the price loads on the next refresh",
                                  budget_used=True)
    if budget_used:
        warnings.append("budget used: some prices were not fetched this time to keep the broker's rate limit for the trading worker")

    items = [rows[p] for p in pairs]
    result: Dict[str, Any] = {"source": {"broker": record.broker_name, "account_label": record.account_label},
                              "fetched_at": datetime.now(timezone.utc).isoformat(), "broker_calls": broker_calls,
                              "items": items, "missing": sum(1 for r in items if r["missing"]), "warnings": warnings}
    if live is not None:
        stamps = [datetime.fromisoformat(r["timestamp"]) for r in items if r.get("timestamp")]
        # The stalest price decides (a list is only as fresh as its oldest price); none timed = age unknown = stale.
        result["data_tier"] = live.badge(as_of=min(stamps) if stamps else None, now=now)
    return result


async def _shared_quote(record: BrokerCredentialRecord, pair: Pair, now: datetime, byob: bool) -> Optional[dict]:
    exchange, symbol = pair
    shared = await api_budget.cached_quote(record.id, exchange, symbol)
    if shared is None:
        return None
    ts = datetime.fromisoformat(shared["timestamp"]) if shared.get("timestamp") else None
    return _priced(pair, shared.get("ltp"), prev_close=None, timestamp=ts, source=shared.get("source") or "quote", now=now,
                   unknown_age_is_stale=byob)


async def _one_quote(adapter: Any, record: BrokerCredentialRecord, pair: Pair, now: datetime, byob: bool,
                     deadline: float) -> Tuple[Optional[dict], int, bool]:
    """(the row or None, broker calls made, refused): one symbol's quote, every broker call taking a token first (the
    quote, then - when the adapter's quote carries nothing - the bare LTP). Refused = no token within what is left of
    the request's wait. A failure is a missing row with the broker's words."""
    exchange, symbol = pair
    calls = 0

    async def token() -> bool:
        return await api_budget.take(record.id, record.broker_name, max(0.0, deadline - time.monotonic()))
    try:
        if not await token():
            return None, calls, True
        calls += 1
        row = _from_quote(pair, await adapter.get_quote_for_symbol(symbol, exchange), "quote", now, byob)
        if row is None:
            if not await token():
                return None, calls, True
            calls += 1
            row = _priced(pair, await adapter.get_ltp_for_symbol(symbol, exchange), prev_close=None, timestamp=None,
                          source="ltp", now=now, unknown_age_is_stale=byob)
    except Exception as exc:  # noqa: BLE001 - one symbol's failure must not lose the others
        logger.info("Quote failed for %s:%s via %s: %s", exchange, symbol, record.broker_name, exc)
        return _missing(pair, f"{record.broker_name}: {type(exc).__name__}: {exc}"[:300]), calls, False
    return row, calls, False
