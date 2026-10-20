"""UI2-4a2-pre: batch quotes for the Charts page's watchlist - `POST /api/market-data/quotes`.

Up to MAX_ITEMS (exchange, symbol) pairs in one request; each comes back with its last price and its change from the
previous session's close, or as missing with the reason. Read-only and tenant-scoped, behind the same broker gate and
entitlement guard as the single-symbol `/ltp` (`candles_routes`): the organisation's first usable broker session, or -
with the bring-your-own-broker tier on (BT1) - the user's own confirmed session, symbols outside its budget answering
EOD-only per item (all of them outside: a 409 with `X-Data-Tier: C`).

Where the prices come from, cheapest for the broker allowance first:
1. **Fresh ticks** of the live feed (free). Under BT1 only the session's own ticks, exactly as BT1 `/ltp` reads them;
   whether a BT1 user may read the shared feed is LD-10's open owner question, and if it is allowed it is added for
   `/ltp` and this route together.
2. **Quotes cached for this credential** in the last seconds (`api_budget.cached_quote`: what `/ltp` or another
   watchlist poll fetched) (free).
3. **The broker's multi-quote call** where the adapter has one (`BrokerInterface.multi_quote`: Kite, Upstox, Dhan,
   Fyers, Angel One), for the symbols still unpriced: ONE broker call, counted ONCE against the credential's quote
   budget (`api_budget.take`, the `/ltp` budget - a quote never uses the history burst). One such fetch per credential
   at a time (single-flight): a second poll waits for the first one's rows in the shared cache. Every row it brings is
   stored per credential and symbol for BATCH_CACHE_SECONDS, so `/ltp` and the next poll reuse it.
4. Otherwise **one quote per symbol**, each broker call taking a token: at most PER_SYMBOL_MAX_CALLS calls per request,
   and none started after QUOTE_WAIT_SECONDS; the remaining symbols say "budget used" and load on the next poll.

The previous session's close is kept per broker, symbol and IST trading day, so a price from a tick or from `/ltp`'s
cache still shows its change once a quote of the day has given the close.

Never sample data: a symbol with no real price (none returned, a zero or a failure) comes back `missing: true` with
`ltp: null` and the reason - never 0. Rate limited per user (the panel polls every 10 s while visible). Nothing is
stored beyond those short caches and no order path is reached.
"""
import asyncio
import logging
import re
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.brokers.base import BrokerInterface
from app.brokers.models import Quote
from app.brokers.timestamps import IST
from app.brokers.token_lifecycle import build_adapter
from app.charts.drawings import EXCHANGE_RE, SYMBOL_RE
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
QUOTE_WAIT_SECONDS = 2.0       # per request: no broker call starts after this, and no token wait runs past it
PER_SYMBOL_MAX_CALLS = 5       # per request, on the per-symbol path (an adapter without a multi-quote call)
BATCH_CACHE_SECONDS = 8        # a batch row shared per credential and symbol (the panel polls every 10 s)
BATCH_LOCK = ("_WATCHLIST", "_BATCH")      # the single-flight lock's (exchange, symbol) slot in api_budget's fetch locks
PREV_CLOSE_SECONDS = 3600      # the previous close, per broker, symbol and IST day (the cache's longest TTL)
# The watchlist polls every 10 s while its tab is visible (6 a minute); 30 leaves room for a list switch or a second tab.
quotes_rate_limit = user_rate_limit("market_data_quotes", limit=30, window_seconds=60)

Pair = Tuple[str, str]          # (exchange, symbol)
_SYMBOL = re.compile(SYMBOL_RE)
_EXCHANGE = re.compile(EXCHANGE_RE)
BUDGET_REASON = "budget used - the broker's rate budget for this session is in use; the price loads on the next refresh"
NO_PRICE_REASON = "no price from the broker for this symbol - check it is the broker's trading symbol on this exchange"


class QuoteItem(BaseModel):
    symbol: str = Field(min_length=1, max_length=60)
    exchange: str = Field(default="NSE", min_length=2, max_length=10)

    @field_validator("exchange")
    @classmethod
    def _exchange(cls, v: str) -> str:
        v = v.strip().upper()
        if not _EXCHANGE.match(v):
            raise ValueError("exchange must be one of NSE, BSE, NFO, BFO, MCX, CDS, CRYPTO, NYSE, NASDAQ")
        return v

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, v: str) -> str:
        # A plain trading symbol only: letters, digits, space and & . _ : - (the chart's own rule). "|" and "," are
        # refused, so nothing can pass a raw Upstox instrument key or a second key into a batch call.
        v = " ".join(v.split()).upper()
        if not _SYMBOL.match(v):
            raise ValueError("symbol may hold only letters, digits, spaces and & . _ : - (at most 40)")
        return v

    @model_validator(mode="after")
    def _one_exchange(self) -> "QuoteItem":
        # "NSE:RELIANCE" with exchange NSE is RELIANCE; an "EXCH:" prefix naming another exchange is refused (an adapter
        # that splits "EXCH:SYMBOL" itself would otherwise quote the other exchange).
        if ":" in self.symbol:
            prefix, rest = self.symbol.split(":", 1)
            if prefix != self.exchange or not rest or ":" in rest:
                raise ValueError("symbol names another exchange than `exchange`")
            self.symbol = rest
        return self


class QuotesBody(BaseModel):
    items: List[QuoteItem] = Field(min_length=1, max_length=MAX_ITEMS)
    broker: Optional[str] = Field(default=None, description="a stored broker; omitted = the first usable session")
    account_label: str = Field(default="primary", max_length=50)


# --- rows ----------------------------------------------------------------------------------------------------------------

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
    if timestamp is not None and timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    reason = quote_is_stale(timestamp, now) if timestamp is not None else None
    if timestamp is None and unknown_age_is_stale:
        reason = "the broker gave no time for this price, so its age is unknown"
    row = {"symbol": pair[1], "exchange": pair[0], "ltp": price, "prev_close": None, "change": None, "change_pct": None,
           "timestamp": timestamp.isoformat() if timestamp is not None else None,
           "age_seconds": round((now - timestamp).total_seconds(), 1) if timestamp is not None else None,
           "stale": reason is not None, "stale_reason": reason, "source": source, "missing": False, "error": None}
    return _with_prev_close(row, prev)


def _with_prev_close(row: dict, prev: Optional[float]) -> dict:
    if prev is None or not prev > 0:
        return row
    change = round(row["ltp"] - prev, 4)
    return {**row, "prev_close": prev, "change": change, "change_pct": round(change / prev * 100, 4)}


def _from_quote(pair: Pair, quote: Optional[Quote], source: str, now: datetime, byob: bool) -> Optional[dict]:
    if quote is None:
        return None
    return _priced(pair, quote.ltp, prev_close=quote.prev_close, timestamp=quote.timestamp, source=source, now=now,
                   unknown_age_is_stale=byob)


def _ltp_body(record: BrokerCredentialRecord, row: dict, quote: Optional[Quote], now: datetime) -> dict:
    """A priced row in `/ltp`'s own shape, for the shared per-credential quote cache `/ltp` reads."""
    return {"symbol": row["symbol"], "exchange": row["exchange"], "source_broker": record.broker_name, "fetched_at": now.isoformat(),
            "ltp": row["ltp"], "timestamp": row["timestamp"], "age_seconds": row["age_seconds"], "stale": bool(row["stale"]),
            "stale_reason": row["stale_reason"], "source": row["source"], "bid": quote.bid if quote else None,
            "ask": quote.ask if quote else None, "volume": (quote.volume or None) if quote else None, "prev_close": row["prev_close"]}


# --- the previous close, per broker, symbol and IST trading day ----------------------------------------------------------
_PREV_LOCAL: Dict[str, Tuple[float, float]] = {}       # key -> (expires at, close)


def _prev_key(broker: str, pair: Pair, day: date) -> str:
    return f"pclose:{broker.lower()}:{day.isoformat()}:{pair[0]}:{pair[1]}"


async def _prev_close_get(broker: str, pair: Pair, day: date) -> Optional[float]:
    from app.core.rate_limit import redis_backed
    key = _prev_key(broker, pair, day)
    hit = _PREV_LOCAL.get(key)
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    if redis_backed():
        from app.cache.client import cache_get
        raw = await cache_get(key)
        try:
            return float(raw) if raw else None
        except ValueError:
            return None
    return None


async def _prev_close_put(broker: str, pair: Pair, day: date, close: float) -> None:
    from app.core.rate_limit import redis_backed
    key = _prev_key(broker, pair, day)
    _PREV_LOCAL[key] = (time.monotonic() + PREV_CLOSE_SECONDS, close)
    if redis_backed():
        from app.cache.client import cache_set
        await cache_set(key, repr(close), PREV_CLOSE_SECONDS)


def reset() -> None:
    """Tests: forget the in-process previous closes."""
    _PREV_LOCAL.clear()


# --- sources -------------------------------------------------------------------------------------------------------------

async def _from_ticks(pair: Pair, broker: str, feeds: List[TickCache], now: datetime, byob: bool) -> Optional[dict]:
    exchange, symbol = pair
    for feed in feeds:
        tick = await feed.fresh(broker, exchange, symbol, now)
        if tick is not None:
            return _priced(pair, tick.ltp, prev_close=None, timestamp=tick.best_ts, source="tick", now=now, unknown_age_is_stale=byob)
    return None


async def _from_cache(record: BrokerCredentialRecord, pair: Pair, now: datetime, byob: bool) -> Optional[dict]:
    """What `/ltp` or another poll fetched for this credential in the last seconds."""
    shared = await api_budget.cached_quote(record.id, pair[0], pair[1])
    if shared is None:
        return None
    try:
        ts = datetime.fromisoformat(shared["timestamp"]) if shared.get("timestamp") else None
    except (TypeError, ValueError):
        ts = None
    row = _priced(pair, shared.get("ltp"), prev_close=shared.get("prev_close"), timestamp=ts, source=shared.get("source") or "quote",
                  now=now, unknown_age_is_stale=byob)
    return {**row, "shared": True} if row is not None else None


def _has_quote_call(adapter: Any) -> bool:
    """Whether the adapter (under its wrappers) has its own single-symbol quote call - the interface default answers
    None without asking the broker, so a token spent on it would buy nothing."""
    inner = adapter
    for _ in range(4):
        nxt = vars(inner).get("inner") if hasattr(inner, "__dict__") else None
        if nxt is None:
            break
        inner = nxt
    return type(inner).get_quote_for_symbol is not BrokerInterface.get_quote_for_symbol


class _Fill:
    """One request's state while it prices its list."""

    def __init__(self, record: BrokerCredentialRecord, adapter: Any, now: datetime, byob: bool) -> None:
        self.record, self.adapter, self.now, self.byob = record, adapter, now, byob
        self.rows: Dict[Pair, dict] = {}
        self.reasons: Dict[Pair, str] = {}          # an unpriced symbol's own reason (the broker's words)
        self.broker_calls = 0
        self.spare_tokens = 0                       # a batch token taken for a call the adapter then did not make
        self.warnings: List[str] = []

    def unpriced(self, pairs: List[Pair]) -> List[Pair]:
        return [p for p in pairs if p not in self.rows]

    async def keep(self, pair: Pair, row: dict, quote: Optional[Quote] = None) -> None:
        self.rows[pair] = row
        await api_budget.store_quote(self.record.id, pair[0], pair[1], _ltp_body(self.record, row, quote, self.now), ttl=BATCH_CACHE_SECONDS)

    async def from_cache(self, pairs: List[Pair]) -> None:
        for pair in self.unpriced(pairs):
            row = await _from_cache(self.record, pair, self.now, self.byob)
            if row is not None:
                self.rows[pair] = row

    async def batch(self, pairs: List[Pair]) -> bool:
        """The multi-quote call for the still-unpriced symbols, one fetch per credential at a time. False when the
        adapter turned out to have no call for this list (the per-symbol path then uses the token already taken)."""
        deadline = time.monotonic() + QUOTE_WAIT_SECONDS
        while True:
            holder = await api_budget.claim_fetch(self.record.id, *BATCH_LOCK)
            if holder is not None:
                break
            # Another poll on this credential is fetching: its rows land in the shared cache - take those, and fetch
            # whatever it did not cover once it is done.
            await asyncio.sleep(0.1)
            await self.from_cache(pairs)
            if not self.unpriced(pairs) or time.monotonic() >= deadline:
                return True
        try:
            await self.from_cache(pairs)            # what the fetch we waited for brought
            wanted = self.unpriced(pairs)
            if not wanted:
                return True
            if not await api_budget.take(self.record.id, self.record.broker_name, max(0.0, deadline - time.monotonic())):
                return True
            try:
                got = await self.adapter.get_quotes_batch(wanted)
            except Exception as exc:  # noqa: BLE001 - the broker's words next to each symbol
                self.broker_calls += 1
                logger.info("Batch quote failed via %s: %s", self.record.broker_name, exc)
                error = f"{self.record.broker_name}: {type(exc).__name__}: {exc}"[:300]
                self.warnings.append(error)
                self.reasons.update({p: error for p in wanted})
                return True
            if got is None:
                self.spare_tokens += 1
                return False
            self.broker_calls += 1
            for pair in wanted:
                quote = got.get(pair)
                row = _from_quote(pair, quote, "quote", self.now, self.byob)
                if row is not None:
                    await self.keep(pair, row, quote)
                else:
                    self.reasons[pair] = NO_PRICE_REASON
            return True
        finally:
            await api_budget.release_fetch(self.record.id, *BATCH_LOCK, holder)

    async def per_symbol(self, pairs: List[Pair]) -> None:
        """One broker call per symbol (its quote call, or its LTP call when it has none), each taking a token: at most
        PER_SYMBOL_MAX_CALLS, none started after QUOTE_WAIT_SECONDS; the rest are "budget used"."""
        deadline = time.monotonic() + QUOTE_WAIT_SECONDS
        quote_call = _has_quote_call(self.adapter)
        calls = 0
        for pair in self.unpriced(pairs):
            if calls >= PER_SYMBOL_MAX_CALLS or time.monotonic() >= deadline:
                return
            if self.spare_tokens:
                self.spare_tokens -= 1
            elif not await api_budget.take(self.record.id, self.record.broker_name, max(0.0, deadline - time.monotonic())):
                return
            if time.monotonic() >= deadline:          # the token came, but too late to start a call
                return
            calls += 1
            self.broker_calls += 1
            exchange, symbol = pair
            try:
                if quote_call:
                    quote = await self.adapter.get_quote_for_symbol(symbol, exchange)
                    row = _from_quote(pair, quote, "quote", self.now, self.byob)
                else:
                    quote = None
                    row = _priced(pair, await self.adapter.get_ltp_for_symbol(symbol, exchange), prev_close=None, timestamp=None,
                                  source="ltp", now=self.now, unknown_age_is_stale=self.byob)
            except Exception as exc:  # noqa: BLE001 - one symbol's failure must not lose the others
                logger.info("Quote failed for %s:%s via %s: %s", exchange, symbol, self.record.broker_name, exc)
                self.reasons[pair] = f"{self.record.broker_name}: {type(exc).__name__}: {exc}"[:300]
                continue
            if row is None:
                self.reasons[pair] = NO_PRICE_REASON
            else:
                await self.keep(pair, row, quote)


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
        # The session's own ticks only, as BT1 /ltp reads them (never the shared feed - LD-10's open owner question).
        feeds = [TickCache(namespace=live.session.namespace)]
    else:
        record = await _pick_record(session, user.tenant_id, body.broker, body.account_label)
        feeds = [tick_cache]
    eod_date, holidays = await _eod_context(session, ents)
    await release_connection(session)       # the broker calls below can take seconds; no pool connection is held

    eod_rows: Dict[Pair, dict] = {}
    for pair in pairs:
        ent = ents.get((pair[1], pair[0]))
        if byob and (ent is None or not ent.live):
            eod_rows[pair] = _missing(pair, ent.reason if ent and ent.reason else "EOD data only for this symbol",
                                      data_tier=ent.badge(eod_date=eod_date, now=now, holidays=holidays) if ent else None)
    wanted = [p for p in pairs if p not in eod_rows]
    adapter = build_adapter(record)
    fill = _Fill(record, adapter, now, byob)

    for pair in wanted:                                         # 1. fresh ticks
        row = await _from_ticks(pair, record.broker_name, feeds, now, byob)
        if row is not None:
            fill.rows[pair] = row
    await fill.from_cache(wanted)                               # 2. this credential's shared quotes
    if fill.unpriced(wanted):
        batched = bool(getattr(adapter, "multi_quote", False)) and await fill.batch(wanted)   # 3. one multi-quote call
        if not batched:
            await fill.per_symbol(wanted)                       # 4. per symbol, capped

    # The previous close of the IST day: remembered from quotes that carry it, given to rows that do not.
    day = now.astimezone(IST).date()
    for pair, row in list(fill.rows.items()):
        if row["prev_close"] is not None:
            await _prev_close_put(record.broker_name, pair, day, row["prev_close"])
        else:
            prev = await _prev_close_get(record.broker_name, pair, day)
            if prev is not None:
                fill.rows[pair] = _with_prev_close(row, prev)

    rows: Dict[Pair, dict] = {**eod_rows, **fill.rows}
    for pair in fill.unpriced(wanted):
        reason = fill.reasons.get(pair)
        rows[pair] = _missing(pair, reason) if reason else _missing(pair, BUDGET_REASON, budget_used=True)
    warnings = list(fill.warnings)
    if any(r.get("budget_used") for r in rows.values()):
        warnings.append("budget used: some prices were not fetched this time to keep the broker's rate limit for the trading worker")

    items = [rows[p] for p in pairs]
    result: Dict[str, Any] = {"source": {"broker": record.broker_name, "account_label": record.account_label},
                              "fetched_at": datetime.now(timezone.utc).isoformat(), "broker_calls": fill.broker_calls,
                              "items": items, "missing": sum(1 for r in items if r["missing"]), "warnings": warnings}
    if live is not None:
        priced = [r for r in items if not r["missing"]]
        # The stalest price decides (a list is only as fresh as its oldest price); a price without a time has an unknown
        # age, so the badge is then stale, as it is with no price at all.
        timed = [datetime.fromisoformat(r["timestamp"]) for r in priced if r.get("timestamp")]
        as_of = min(timed) if priced and len(timed) == len(priced) else None
        result["data_tier"] = live.badge(as_of=as_of, now=now)
    return result
