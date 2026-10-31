"""M71 PL-1(c): a one-time, PACED backfill of 1-minute history per symbol, then only the gaps.

What it fills (extends DATA-H, app/market_lake/index_history.py - same table, same grid, same private store):
- The 1-minute bars (5m / 15m / 1H are built from them on read, app/market_lake/tenant_store.resample) of what the history
  owner HIMSELF uses: the indices (the "indices" system watchlist and LAKE_BACKFILL_INDICES), his own watchlists and, with
  MARKET_BACKFILL_INCLUDE_FNO, the F&O stocks - for MARKET_BACKFILL_INTRADAY_DAYS back from the last closed session.
- Data terms (M71 PL-5, provisional default M71Q-2): read only through HB2's owner session (`intraday_history.
  owner_session`) or, for a gap, the session the named credential's own user linked; stored only in that user's tenant
  PRIVATE store (`md_tenant_candles`, source `broker:<name>:u<holder>`), so the bars serve only that user. Another user's
  watchlist is never fetched through the owner's credential, and the shared lake is never written.
- Daily / Weekly never call the broker: they come from the NSE bhavcopy already ingested (`md_candles`, sources
  nse_bhav:cm / nse_bhav:index). A symbol without them is recorded as a data-quality gap - never fetched, never filled.

Bookkeeping per (symbol, interval) - `lake_backfill_windows` (DATA-H's table, no migration): one row per fixed request
window of the 7-day grid (`index_history.windows`), per (tenant, holder, instrument, "1m"). done (complete; never asked
again) | open (the window still grows: asked again only when the store lacks the newest closed session) | no_data |
failed (retried) | gap (queued by `request_gap_fill`: the day range in the row's note). `series_state` adds the first /
last stored bar, the backfill-complete flag and the last error per symbol.

Chunking and pacing: consecutive pending windows of one symbol are asked in ONE request up to the broker's published range
per 1-minute request (MINUTE_DAYS_PER_REQUEST); each request first takes a spare token of the session's RateBudget
(`acquire_spare`: MARKET_BACKFILL_RESERVE of both buckets stays free for the trading worker's exits; the worker hands over
the same budget) and counts against MARKET_BACKFILL_MAX_REQUESTS per rolling 30 minutes. A refused token, the cap, the time
budget, a 429 or a refused login ends the pass; the next pass resumes from the table. One pass at a time (advisory lock).

No look-ahead and no invented bars: a bar still forming at the fetch time is dropped; a missing minute stays missing (the
store and the quality report show it); nothing is copied or zero-filled.

CLI (inside the backend container):
    python -m app.market_lake.backfill run [--budget-seconds N] [--symbols NIFTY,RELIANCE]
    python -m app.market_lake.backfill gaps                 drain the gap queue now
    python -m app.market_lake.backfill progress [--symbols ...]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time as monotonic_clock
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.rate_budget import RateBudget, limits_for
from app.core import config
from app.db.models import BrokerCredentialRecord, LakeBackfillWindowRecord, MdCandleRecord, MdTenantCandleRecord, User, WatchlistRecord
from app.market_data.calendar import IST, is_trading_day
from app.market_lake import index_history as dh
from app.market_lake import intraday_history as hb2
from app.market_lake import quality
from app.market_lake.ingest import write_candles_bulk

logger = logging.getLogger(__name__)

MINUTE = dh.MINUTE                         # "1m": the one intraday timeframe stored; the others are built on read
DAILY = dh.DAILY                           # "day": NSE bhavcopy only
GRID_DAYS = dh.LAKE_MINUTE_WINDOW_DAYS     # the fixed window grid shared with DATA-H (one bookkeeping row per window)
# Calendar days one 1-minute historical request may span, from each broker's docs: Upstox v2 "1minute: last 1 month till
# the end date" (28 = whole grid weeks inside it), Kite "minute: 60 days" (56). Any other broker: one grid window.
MINUTE_DAYS_PER_REQUEST: dict[str, int] = {"upstox": 28, "zerodha": 56}
BHAV_SOURCES = ("nse_bhav:cm", "nse_bhav:index")
DAILY_GAP_SOURCE = "nse_bhav"
DAILY_INTERVALS = frozenset({"day", "1d", "d", "daily", "week", "1w", "w", "weekly"})
_INTRADAY = re.compile(r"^\d{1,3}\s*(m|min|minute|minutes|h|hour|hours)$")
GAP, SETTLED = "gap", ("done", "no_data")
LOCK_KEY = 0x48420071                      # pg advisory lock id ("HB" 0071): one backfill pass at a time
GAP_LOCK_KEY = 0x48420072                  # one gap drain at a time
CAP_WINDOW_SECONDS = 30 * 60
STALE_DAILY_DAYS = 7                       # a bhavcopy series whose last bar is older than this is reported as a gap
_GAP_NOTE = re.compile(r"^gap (\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})(?: (NSE|BSE):([^;]+))?")
W = LakeBackfillWindowRecord

Clock = Callable[[], datetime]
BudgetFor = Callable[[int, str], RateBudget]
AdapterFactory = Callable[[BrokerCredentialRecord], Any]

# Requests per credential in the rolling 30 minutes (monotonic seconds), shared by the passes of this process.
_recent: dict[int, deque[float]] = {}


def _wall() -> datetime:
    return datetime.now(UTC)


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def minute_days_per_request(broker: str) -> int:
    return max(GRID_DAYS, int(MINUTE_DAYS_PER_REQUEST.get((broker or "").lower(), GRID_DAYS)))


def is_daily(interval: str) -> bool:
    return " ".join((interval or "").split()).lower() in DAILY_INTERVALS


def is_intraday(interval: str) -> bool:
    return bool(_INTRADAY.match(" ".join((interval or "").split()).lower()))


# --- what --------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Symbol:
    key: str                  # the lake key the readers ask for (index_history.lake_key_for)
    symbol: str               # the broker's name ("NIFTY 50", "RELIANCE")
    exchange: str             # NSE / BSE

    @property
    def target(self) -> dh.Target:
        return dh.Target(self.symbol, self.key, self.symbol, self.exchange)


def to_symbol(symbol: str, exchange: str = "NSE") -> Symbol:
    """A watchlist / universe row -> Symbol. Indices by their broker name; NSE cash stocks; anything else ValueError."""
    from app.instruments import master
    name = " ".join((symbol or "").strip().upper().split())
    venue = (exchange or "NSE").strip().upper().removesuffix("_INDEX")
    if not name:
        raise ValueError("an empty symbol")
    name = master.INDEX_SYMBOLS.get(name, name)
    underlying = master.underlying_of(name)
    if underlying in master.INDEX_SYMBOLS:
        venue = master.INDEX_EXCHANGE.get(underlying, "NSE")
    key = dh.lake_key_for(name)
    if venue not in ("NSE", "BSE") or (venue == "BSE" and not key.startswith(dh.BSE_INDEX_PREFIX)):
        raise ValueError(f"{venue}:{name} is not an NSE stock or an index (only those are backfilled)")
    return Symbol(key, name, venue)


async def universe(session: AsyncSession, owner_user_id: int, symbols: Sequence[str] | None = None) -> tuple[list[Symbol], list[str]]:
    """What the owner himself uses: the indices (system list + LAKE_BACKFILL_INDICES), his own watchlists and (with
    MARKET_BACKFILL_INCLUDE_FNO) the F&O stocks. `symbols` replaces it (CLI). Other users' watchlists are never read."""
    from app.watchlists import service as wl_service
    from app.watchlists import system as wl_system
    rows: list[tuple[str, str]] = []
    if symbols:
        rows = [(s.rpartition(":")[2], s.rpartition(":")[0] or "NSE") for s in symbols]
    else:
        rows += [(t.symbol, t.exchange) for t in dh.targets()[0]]
        keys = ["indices"] + (["fno-stocks"] if config.MARKET_BACKFILL_INCLUDE_FNO else [])
        for key in keys:
            row = await wl_system.get(session, key)
            if row is not None:
                rows += [(i["symbol"], i["exchange"]) for i in wl_system.watch_items(row)]
        for wl in await session.scalars(select(WatchlistRecord).where(WatchlistRecord.user_id == owner_user_id)):
            rows += [(i["symbol"], i.get("exchange") or "NSE") for i in wl_service.items_of(wl)]
    out: dict[str, Symbol] = {}
    notes: list[str] = []
    for sym, venue in rows:
        try:
            s = to_symbol(sym, venue)
        except ValueError as exc:
            notes.append(f"skipped: {exc}")
            continue
        out.setdefault(s.key, s)
    return [out[k] for k in sorted(out)], notes


# --- bookkeeping -------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Pending:
    sym: Symbol
    start: date               # the fixed grid window
    end: date
    ask_from: date
    ask_to: date
    status: str | None

    @property
    def complete(self) -> bool:
        return self.end <= self.ask_to


def gap_note(lo: date, hi: date, sym: Symbol | None = None) -> str:
    """The queued range (and the broker's name of the symbol) kept in the window row's note."""
    return f"gap {lo.isoformat()}..{hi.isoformat()}" + (f" {sym.exchange}:{sym.symbol}" if sym else "")


def gap_range(note: str | None) -> tuple[date, date] | None:
    m = _GAP_NOTE.match(note or "")
    return (date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))) if m else None


def gap_symbol(key: str, note: str | None) -> Symbol | None:
    m = _GAP_NOTE.match(note or "")
    if m and m.group(3) and m.group(4):
        return Symbol(key, m.group(4).strip(), m.group(3))
    return _symbol_for_key(key)


def first_needed(last_closed: date) -> date:
    return last_closed - timedelta(days=config.MARKET_BACKFILL_INTRADAY_DAYS)


async def _has_close_bar(session: AsyncSession, tenant_id: int, user_id: int, key: str, day: date) -> bool:
    """The store already holds `day`'s last continuous 1-minute bar (from any writer this holder may read: the live feed,
    HB2, DATA-H or this backfill) - then the newest session needs no request."""
    from app.market_lake.tenant_store import holder_visible
    end = dh.session_close(day)
    found = await session.scalar(select(MdTenantCandleRecord.ts).where(
        MdTenantCandleRecord.tenant_id == tenant_id, MdTenantCandleRecord.instrument_key == key,
        MdTenantCandleRecord.timeframe == MINUTE, MdTenantCandleRecord.ts == end,
        holder_visible(MdTenantCandleRecord.source, user_id)).limit(1))
    return found is not None


async def plan(session: AsyncSession, tenant_id: int, user_id: int, syms: Sequence[Symbol], last_closed: date) -> list[Pending]:
    """Windows still to ask for, newest first per symbol. Settled windows never; an `open` window only while the store
    lacks the last closed session (the newest window) and was not asked since that session's bars were final (any
    `open` window: at most once per closed session); `gap` windows belong to the gap drain."""
    first = first_needed(last_closed)
    final_at = datetime.combine(last_closed, hb2.BARS_FINAL, tzinfo=IST)
    keys = [s.key for s in syms]
    known: dict[tuple[str, date], tuple[str | None, datetime | None]] = {}
    for k, w_start, w_status, w_updated in await session.execute(select(W.instrument_key, W.window_start, W.status, W.updated_at).where(
            W.tenant_id == tenant_id, W.user_id == user_id, W.timeframe == MINUTE, W.instrument_key.in_(keys))):
        known[(k, w_start)] = (w_status, w_updated)
    out: list[Pending] = []
    for s in syms:
        current: bool | None = None
        for ws, we in dh.windows(MINUTE, first, last_closed):
            status, updated = known.get((s.key, ws), (None, None))
            if status in SETTLED or status == GAP:
                continue
            if status == "open" and updated is not None and _utc(updated) >= final_at:
                continue                                          # asked after the newest session's bars were final
            if status == "open" and ws <= last_closed <= we:      # the newest window: asked only if the store lacks it
                if current is None:
                    current = await _has_close_bar(session, tenant_id, user_id, s.key, last_closed)
                if current:
                    continue
            out.append(Pending(s, ws, we, max(ws, first), min(we, last_closed), status))
    return out


def chunks(todo: Sequence[Pending], max_days: int) -> list[list[Pending]]:
    """Consecutive windows of ONE symbol (newest first) grouped while one request spans at most `max_days` days."""
    out: list[list[Pending]] = []
    for p in todo:
        last = out[-1] if out else None
        if (last is not None and last[0].sym == p.sym and last[-1].start == p.end + timedelta(days=1)
                and (last[0].ask_to - p.ask_from).days + 1 <= max_days):
            last.append(p)
        else:
            out.append([p])
    return out


async def _record(session: AsyncSession, tenant_id: int, user_id: int, key: str, ws: date, we: date, *, status: str | None,
                  bars: int = 0, error: str | None = None, count_attempt: bool = True) -> None:
    row = await session.scalar(select(W).where(W.tenant_id == tenant_id, W.user_id == user_id, W.instrument_key == key,
                                               W.timeframe == MINUTE, W.window_start == ws))
    if row is None:
        row = W(tenant_id=tenant_id, user_id=user_id, instrument_key=key, timeframe=MINUTE, window_start=ws, window_end=we,
                attempts=0, bars=0, status=status or "open")
        session.add(row)
    if status is not None:
        row.status = status
    row.error, row.updated_at = error, _wall()
    if count_attempt:
        row.attempts = int(row.attempts or 0) + 1
    if bars:
        row.bars = bars


# --- the gap queue -----------------------------------------------------------------------------------------------------
async def request_gap_fill(symbol: str, interval: str, start: datetime, end: datetime, *,
                           credential: BrokerCredentialRecord | int | None = None, session_factory: Any = None,
                           now: datetime | None = None) -> None:
    """QUEUES a gap of `symbol`'s bars between `start` and `end` (aware datetimes; naive = IST, as the brokers send them)
    for the paced gap drain - e.g. the live feed after a reconnect. Never calls the broker and never raises (the caller is
    a feed loop): a bad argument or a database error is logged and nothing is queued.

    - `credential`: whose session fills it and whose private store gets the bars (a credential row or its id); None =
      the history owner's own session (HB2's rule).
    - Intraday intervals (1m, 5m, 15m, 1H, ...) queue the 1-minute windows that overlap the range (the others are
      built from them). Dedupe: one row per window; a second request for an overlapping range widens the queued range.
    - Daily / Weekly never use the broker: a gap there is recorded as a data-quality event (the bhavcopy fills it)."""
    try:
        sym = to_symbol(*symbol.rsplit(":", 1)[::-1]) if ":" in symbol else to_symbol(symbol)
        lo, hi = sorted((_ist(start), _ist(end)))
        factory = session_factory or _default_session_factory()
        if is_daily(interval):
            async with factory() as session:
                await quality.record(session, [quality.QualityEvent(
                    "gap", sym.key, DAILY, _utc(lo), DAILY_GAP_SOURCE,
                    f"Daily bars missing {lo.date()}..{hi.date()} - filled only from the NSE bhavcopy, never from the broker")])
            return
        if not is_intraday(interval):
            raise ValueError(f"{interval!r} is not an interval")
        async with factory() as session:
            tenant_id, user_id = await _holder(session, credential)
            today = (now or _wall()).astimezone(IST).date()
            first_day = max(lo.date(), today - timedelta(days=config.MARKET_BACKFILL_INTRADAY_DAYS))
            last_day = min(hi.date(), today)
            if first_day > last_day:
                return
            for ws, we in dh.windows(MINUTE, first_day, last_day):
                row = await session.scalar(select(W).where(W.tenant_id == tenant_id, W.user_id == user_id, W.instrument_key == sym.key,
                                                           W.timeframe == MINUTE, W.window_start == ws))
                g_lo, g_hi = max(ws, first_day), min(we, last_day)
                old = gap_range(row.error) if row is not None and row.status == GAP else None
                if old is not None:
                    g_lo, g_hi = min(g_lo, old[0]), max(g_hi, old[1])
                await _record(session, tenant_id, user_id, sym.key, ws, we, status=GAP, error=gap_note(g_lo, g_hi, sym),
                              count_attempt=False)
            await session.commit()
    except Exception:  # noqa: BLE001 - a feed loop calls this: a queue failure is logged, never raised into it
        logger.exception("M71 gap fill not queued for %s %s %s..%s", symbol, interval, start, end)


def _ist(ts: datetime) -> datetime:
    return ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)


def _default_session_factory() -> Any:
    from app.db.session import _session_factory
    return _session_factory


async def _holder(session: AsyncSession, credential: BrokerCredentialRecord | int | None) -> tuple[int, int]:
    if credential is None:
        tenant_id = await hb2.owner_tenant(session)
        user = await hb2.owner_user(session, tenant_id)
        if user is None:
            raise hb2.IntradayConfigError("the history owner has no active user")
        return tenant_id, int(user.id)
    record = credential if isinstance(credential, BrokerCredentialRecord) else await session.get(BrokerCredentialRecord, int(credential))
    if record is None or record.user_id is None:
        raise ValueError(f"credential {credential!r} not found or not linked by a user")
    return int(record.tenant_id), int(record.user_id)


# --- one pass ------------------------------------------------------------------------------------------------------------
@dataclass
class PassReport:
    # ok | nothing_missing | not_configured | no_session | trading_hours | locked | rate_limited | login_refused
    # | request_cap | time_budget | disabled
    status: str = "ok"
    tenant_id: int | None = None
    holder: int | None = None
    broker: str | None = None
    symbols: int = 0
    planned: int = 0
    requests: int = 0
    done: int = 0
    open: int = 0
    no_data: int = 0
    failed: int = 0
    gaps_filled: int = 0
    inserted: int = 0
    corrected: int = 0
    skipped: int = 0
    dropped_forming: int = 0
    daily_gaps: int = 0
    remaining: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.status in ("ok", "nothing_missing") and self.failed == 0 and self.remaining == 0

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["notes"] = self.notes[:50]
        out["complete"] = self.complete
        return out


@dataclass
class _Ctx:
    """What one holder's requests need: the session, the adapter, the budget and the limits of this pass."""
    tenant_id: int
    user_id: int
    record: BrokerCredentialRecord
    adapter: Any
    budget: RateBudget
    source: str
    reserve: float
    deadline: float
    cap: int
    max_requests: int | None
    clock: Clock


def _cap_left(credential_id: int, cap: int) -> int:
    q = _recent.setdefault(credential_id, deque())
    now = monotonic_clock.monotonic()
    while q and now - q[0] > CAP_WINDOW_SECONDS:
        q.popleft()
    return cap - len(q)


async def _take(ctx: _Ctx, report: PassReport) -> str | None:
    """None when one request may go out now (a spare rate-budget token was taken); else the stop status."""
    if monotonic_clock.monotonic() >= ctx.deadline:
        return "time_budget"
    if ctx.max_requests is not None and report.requests >= ctx.max_requests:
        return "time_budget"
    if _cap_left(ctx.record.id, ctx.cap) <= 0:
        return "request_cap"
    remaining = max(0.0, ctx.deadline - monotonic_clock.monotonic())
    if not await ctx.budget.acquire_spare(ctx.reserve, deadline=ctx.budget.now() + remaining):
        return "rate_limited"
    _recent.setdefault(ctx.record.id, deque()).append(monotonic_clock.monotonic())
    report.requests += 1
    return None


def _stop_status(exc: BaseException) -> str | None:
    kind = hb2._classify(exc)
    return {"rate_limited": "rate_limited", "auth_failed": "login_refused"}.get(kind)


async def _fetch(ctx: _Ctx, report: PassReport, sym: Symbol, lo: date, hi: date, today: date, *,
                 intraday_today: bool) -> tuple[list[Any] | None, str | None]:
    """The broker's 1-minute bars of [lo, hi]: the historical endpoint for past days, the intraday endpoint for a session
    of today (the historical one does not serve today). Each request is paced. -> (bars, None) or (None, stop|error)."""
    raw: list[Any] = []
    hist_hi = min(hi, today - timedelta(days=1))
    try:
        if lo <= hist_hi:
            stop = await _take(ctx, report)
            if stop:
                return None, stop
            raw += list(await ctx.adapter.get_historical_data(sym.symbol, sym.exchange, "1min", datetime.combine(lo, time(0, 0), tzinfo=IST),
                                                              datetime.combine(hist_hi, time(23, 59), tzinfo=IST)) or [])
        if intraday_today and hi >= today and hasattr(ctx.adapter, "get_intraday_candles"):
            stop = await _take(ctx, report)
            if stop:
                return None, stop
            raw += list(await ctx.adapter.get_intraday_candles(sym.symbol, sym.exchange, "1min") or [])
    except Exception as exc:  # noqa: BLE001 - classified: a 429 / refused login ends the pass, anything else fails the chunk
        return None, _stop_status(exc) or f"error:{dh.scrub(f'{type(exc).__name__}: {exc}')}"
    return raw, None


async def _store(session_factory: Any, ctx: _Ctx, report: PassReport, sym: Symbol, raw: list[Any], parts: Sequence[tuple[date, date, date, date]],
                 *, final_for: Callable[[date, date, int], str]) -> None:
    """Writes the closed bars of every (window_start, window_end, ask_from, ask_to) part and records each window - in one
    transaction (nothing half written)."""
    bars, forming = dh.to_lake_bars(sym.target, MINUTE, raw, ctx.clock())
    report.dropped_forming += forming
    async with session_factory() as session:
        try:
            for ws, we, a_lo, a_hi in parts:
                mine = [b for b in bars if a_lo <= (_utc(b.ts) - timedelta(minutes=1)).astimezone(IST).date() <= a_hi]
                if mine:
                    res = await write_candles_bulk(session, mine, ctx.source, tenant_id=ctx.tenant_id, ingested_at=_utc(ctx.clock()))
                    await dh.flag_days(session, ctx.tenant_id, ctx.user_id, sym.key, a_lo, a_hi)
                    report.inserted += res.inserted
                    report.corrected += res.corrected
                    report.skipped += res.skipped
                status = final_for(we, a_hi, len(mine))
                await _record(session, ctx.tenant_id, ctx.user_id, sym.key, ws, we, status=status, bars=len(mine))
                report.done += status == "done"
                report.open += status == "open"
                report.no_data += status == "no_data"
            await session.commit()
        except Exception as exc:  # noqa: BLE001 - nothing of this request is kept; retried on the next pass
            await session.rollback()
            logger.exception("M71 backfill: storing %s failed", sym.key)
            report.failed += len(parts)
            report.notes.append(f"{sym.symbol}: not stored ({type(exc).__name__})")


async def _try_lock(session: AsyncSession, key: int) -> bool:
    if session.get_bind().dialect.name != "postgresql":
        return True
    return bool(await session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}))


def _default_adapter(record: BrokerCredentialRecord) -> Any:
    from app.brokers.token_lifecycle import build_adapter
    return build_adapter(record)


async def _session_of(session: AsyncSession, tenant_id: int, user_id: int, now: datetime, owner: tuple[int, int] | None) -> BrokerCredentialRecord | None:
    """The holder's OWN usable Tier A session (BT1): HB2's owner rule for the owner; else that user's own credential."""
    if owner == (tenant_id, user_id):
        found = await hb2.owner_session(session, tenant_id, now, user_id)
        return found[1] if found else None
    from app.market_data.entitlement import own_records, pick_session
    user = await session.get(User, user_id)
    if user is None or user.tenant_id != tenant_id or not user.is_active:
        return None
    record = pick_session(await own_records(session, user), now)
    if record is None:
        return None
    from app.secrets_store.envelope import ensure_tenant_key
    await ensure_tenant_key(session, tenant_id)
    return record


def _ctx(tenant_id: int, user_id: int, record: BrokerCredentialRecord, *, budget_for: BudgetFor | None, adapter_factory: AdapterFactory | None,
         reserve: float | None, budget_seconds: float | None, max_requests: int | None, clock: Clock) -> _Ctx:
    budget = budget_for(tenant_id, f"{record.broker_name}@{record.account_label or 'primary'}") if budget_for else \
        RateBudget(limits_for(record.broker_name))
    seconds = float(config.MARKET_BACKFILL_BUDGET_SECONDS if budget_seconds is None else budget_seconds)
    return _Ctx(tenant_id, user_id, record, (adapter_factory or _default_adapter)(record), budget,
                hb2.holder_source(record.broker_name, user_id),
                config.MARKET_BACKFILL_RESERVE if reserve is None else reserve, monotonic_clock.monotonic() + seconds,
                max(1, int(config.MARKET_BACKFILL_MAX_REQUESTS)), max_requests, clock)


async def daily_gaps(session: AsyncSession, syms: Sequence[Symbol], last_closed: date) -> int:
    """Daily / Weekly come only from the NSE bhavcopy: a symbol without bhavcopy Daily bars (or whose last one is stale)
    is recorded as a data-quality gap. Nothing is fetched and nothing is filled. -> events written."""
    if not syms:
        return 0
    rows = {k: (lo, hi) for k, lo, hi in await session.execute(
        select(MdCandleRecord.instrument_key, func.min(MdCandleRecord.ts), func.max(MdCandleRecord.ts)).where(
            MdCandleRecord.instrument_key.in_([s.key for s in syms]), MdCandleRecord.timeframe == DAILY,
            MdCandleRecord.source.in_(BHAV_SOURCES)).group_by(MdCandleRecord.instrument_key))}
    events: list[quality.QualityEvent] = []
    for s in syms:
        got = rows.get(s.key)
        if got is None:
            events.append(quality.QualityEvent("gap", s.key, DAILY, None, DAILY_GAP_SOURCE,
                                               "no NSE bhavcopy Daily bars stored - Daily / Weekly are never read from the broker"))
        elif (last_closed - _utc(got[1]).astimezone(IST).date()).days > STALE_DAILY_DAYS:
            events.append(quality.QualityEvent("gap", s.key, DAILY, _utc(got[1]), DAILY_GAP_SOURCE,
                                               f"the NSE bhavcopy Daily bars end on {_utc(got[1]).astimezone(IST).date()}"))
    return await quality.record(session, events, commit=False)


def _backfill_status(last_closed: date) -> Callable[[date, date, int], str]:
    def final(we: date, a_hi: date, bars: int) -> str:
        complete = we <= a_hi
        if bars:
            return "done" if complete else "open"
        return "no_data" if complete and (last_closed - we).days > dh.NO_DATA_FINAL_DAYS else "open"
    return final


async def run_backfill(session_factory: Any, *, now: datetime | None = None, clock: Clock | None = None,
                       budget_for: BudgetFor | None = None, adapter_factory: AdapterFactory | None = None,
                       symbols: Sequence[str] | None = None, max_requests: int | None = None,
                       budget_seconds: float | None = None, reserve: float | None = None) -> PassReport:
    """One backfill pass for the owner's universe (off-hours only), resumable; a second pass on the same symbols makes
    zero history requests once the windows are settled (and the newest session is in the store)."""
    clock = clock or _wall
    now = now or clock()
    report = PassReport()
    if not config.MARKET_BACKFILL_ENABLED:
        report.status = "disabled"
        return report
    async with session_factory() as lock_session:
        if not await _try_lock(lock_session, LOCK_KEY):
            report.status = "locked"
            return report
        try:
            async with session_factory() as session:
                try:
                    tenant_id = await hb2.owner_tenant(session)
                    user = await hb2.owner_user(session, tenant_id)
                except hb2.IntradayConfigError as exc:
                    report.status = "not_configured"
                    report.notes.append(str(exc))
                    return report
                if user is None:
                    report.status = "not_configured"
                    return report
                report.tenant_id, report.holder = tenant_id, int(user.id)
                today = now.astimezone(IST).date()
                holidays = await dh._holidays(session, today.year - 1, today.year)
                if not hb2.quiet_hours(now, holidays):
                    report.status = "trading_hours"
                    report.notes.append("NSE trading hours: the backfill waits for the close (gaps are still drained)")
                    return report
                syms, notes = await universe(session, int(user.id), symbols)
                report.notes += notes
                report.symbols = len(syms)
                last_closed = hb2.last_closed_day(now, holidays)
                report.daily_gaps = await daily_gaps(session, syms, last_closed)
                await session.commit()
                todo = await plan(session, tenant_id, int(user.id), syms, last_closed)
                report.planned = len(todo)
                if not todo:
                    report.status = "nothing_missing"
                    return report
                record = await _session_of(session, tenant_id, int(user.id), now, (tenant_id, int(user.id)))
            if record is None:
                report.status = "no_session"
                report.remaining = len(todo)
                report.notes.append("no usable broker session of the owner's own today (log in to the broker in ATP)")
                return report
            report.broker = record.broker_name
            ctx = _ctx(tenant_id, int(user.id), record, budget_for=budget_for, adapter_factory=adapter_factory, reserve=reserve,
                       budget_seconds=budget_seconds, max_requests=max_requests, clock=clock)
            groups = chunks(todo, minute_days_per_request(record.broker_name))
            final = _backfill_status(last_closed)
            for i, group in enumerate(groups):
                lo, hi = min(p.ask_from for p in group), max(p.ask_to for p in group)
                raw, why = await _fetch(ctx, report, group[0].sym, lo, hi, today, intraday_today=hi == today)
                if raw is None and why and not why.startswith("error:"):
                    report.status = why
                    report.remaining = sum(len(g) for g in groups[i:])
                    break
                if raw is None:
                    report.failed += len(group)
                    report.notes.append(f"{group[0].sym.symbol} {lo}..{hi}: {why}")
                    async with session_factory() as session:
                        for p in group:
                            await _record(session, tenant_id, int(user.id), p.sym.key, p.start, p.end, status="failed", error=(why or "")[6:])
                        await session.commit()
                    continue
                await _store(session_factory, ctx, report, group[0].sym, raw, [(p.start, p.end, p.ask_from, p.ask_to) for p in group],
                             final_for=final)
            logger.info("M71 backfill: %s", {k: v for k, v in report.as_dict().items() if k != "notes"})
            return report
        finally:
            await lock_session.rollback()


async def drain_gaps(session_factory: Any, *, now: datetime | None = None, clock: Clock | None = None,
                     budget_for: BudgetFor | None = None, adapter_factory: AdapterFactory | None = None,
                     max_requests: int | None = None, budget_seconds: float | None = None, reserve: float | None = None) -> PassReport:
    """Fills the queued gaps (any time, paced like the backfill), each through its holder's own session. A gap window
    whose request fails stays queued (with the error) and is asked again on the next drain."""
    clock = clock or _wall
    now = now or clock()
    report = PassReport()
    if not config.MARKET_BACKFILL_ENABLED:
        report.status = "disabled"
        return report
    async with session_factory() as lock_session:
        if not await _try_lock(lock_session, GAP_LOCK_KEY):
            report.status = "locked"
            return report
        try:
            async with session_factory() as session:
                rows = list(await session.scalars(select(W).where(W.timeframe == MINUTE, W.status == GAP).order_by(
                    W.tenant_id, W.user_id, W.instrument_key, W.window_start.desc())))
                if not rows:
                    report.status = "nothing_missing"
                    return report
                try:
                    o_tenant = await hb2.owner_tenant(session)
                    o_user = await hb2.owner_user(session, o_tenant)
                    owner = (o_tenant, int(o_user.id)) if o_user is not None else None
                except hb2.IntradayConfigError:
                    owner = None
                today = now.astimezone(IST).date()
                holidays = await dh._holidays(session, today.year - 1, today.year)
                last_closed = hb2.last_closed_day(now, holidays)
                session_today = is_trading_day(today, holidays) and now.astimezone(IST).time() >= dh.SESSION.open
                holders: dict[tuple[int, int], BrokerCredentialRecord | None] = {}
                for r in rows:
                    hk = (int(r.tenant_id), int(r.user_id or 0))
                    if hk not in holders:
                        holders[hk] = await _session_of(session, hk[0], hk[1], now, owner) if r.user_id else None
            report.planned = len(rows)
            ctxs: dict[tuple[int, int], _Ctx] = {}
            for i, r in enumerate(rows):
                hk = (int(r.tenant_id), int(r.user_id or 0))
                record = holders.get(hk)
                if record is None:
                    report.remaining += 1
                    if "no_session" not in report.notes:
                        report.notes.append("no_session")
                    continue
                if hk not in ctxs:
                    ctxs[hk] = _ctx(hk[0], hk[1], record, budget_for=budget_for, adapter_factory=adapter_factory, reserve=reserve,
                                    budget_seconds=budget_seconds, max_requests=max_requests, clock=clock)
                ctx = ctxs[hk]
                # Past days: the whole window in one request (so a window settled after a gap is truly complete); today:
                # the intraday endpoint only when the queued range reaches today.
                rng = gap_range(r.error) or (r.window_start, r.window_end)
                first = first_needed(last_closed)
                lo = max(r.window_start, first) if rng[0] < today else today
                hi = min(r.window_end, today if rng[1] >= today else today - timedelta(days=1))
                sym = gap_symbol(r.instrument_key, r.error)
                if sym is None or lo > hi:
                    async with session_factory() as session:
                        await _record(session, hk[0], hk[1], r.instrument_key, r.window_start, r.window_end, status="open",
                                      error=None if sym else "not a symbol the backfill can ask for")
                        await session.commit()
                    continue
                raw, why = await _fetch(ctx, report, sym, lo, hi, today, intraday_today=session_today)
                if raw is None and why and not why.startswith("error:"):
                    report.status = why
                    report.remaining += len(rows) - i
                    break
                if raw is None:
                    report.failed += 1
                    async with session_factory() as session:      # stays queued: the range is kept in the note
                        await _record(session, hk[0], hk[1], r.instrument_key, r.window_start, r.window_end, status=GAP,
                                      error=f"{gap_note(rng[0], rng[1], sym)}; last error: {(why or '')[6:]}")
                        await session.commit()
                    continue

                def final(we: date, a_hi: date, bars: int, _lc: date = last_closed) -> str:
                    return "done" if we <= _lc and bars else "open"
                await _store(session_factory, ctx, report, sym, raw, [(r.window_start, r.window_end, lo, hi)], final_for=final)
                report.gaps_filled += 1
            if report.status == "ok" and report.remaining:
                report.status = "no_session"
            return report
        finally:
            await lock_session.rollback()


def _symbol_for_key(key: str) -> Symbol | None:
    """The broker symbol of a stored lake key (the gap queue keeps only the key): indices by name, NSE stocks as is."""
    from app.instruments import master
    for name, broker_symbol in master.INDEX_SYMBOLS.items():
        if dh.lake_key_for(name) == key:
            return Symbol(key, broker_symbol, master.INDEX_EXCHANGE.get(name, "NSE"))
    venue, _, rest = key.partition(":")
    if venue == "NSE" and rest:
        return Symbol(key, rest, "NSE")
    return None


# --- per (symbol, interval) state ------------------------------------------------------------------------------------------
async def series_state(session: AsyncSession, tenant_id: int, user_id: int, syms: Sequence[Symbol], last_closed: date) -> list[dict[str, Any]]:
    """Per symbol: 1-minute first / last stored bar (this holder's view), windows by status, backfill complete, last
    error; Daily first / last bhavcopy bar."""
    from app.market_lake.tenant_store import holder_visible
    out: list[dict[str, Any]] = []
    todo = {p.sym.key for p in await plan(session, tenant_id, user_id, syms, last_closed)}
    for s in syms:
        lo, hi = (await session.execute(select(func.min(MdTenantCandleRecord.ts), func.max(MdTenantCandleRecord.ts)).where(
            MdTenantCandleRecord.tenant_id == tenant_id, MdTenantCandleRecord.instrument_key == s.key, MdTenantCandleRecord.timeframe == MINUTE,
            holder_visible(MdTenantCandleRecord.source, user_id)))).one()
        statuses: dict[str, int] = {}
        for st, n in await session.execute(select(W.status, func.count()).where(
                W.tenant_id == tenant_id, W.user_id == user_id, W.instrument_key == s.key, W.timeframe == MINUTE).group_by(W.status)):
            statuses[st] = int(n)
        err = await session.scalar(select(W.error).where(
            W.tenant_id == tenant_id, W.user_id == user_id, W.instrument_key == s.key, W.timeframe == MINUTE, W.error.is_not(None),
            W.status != GAP).order_by(W.updated_at.desc()).limit(1))
        d_lo, d_hi = (await session.execute(select(func.min(MdCandleRecord.ts), func.max(MdCandleRecord.ts)).where(
            MdCandleRecord.instrument_key == s.key, MdCandleRecord.timeframe == DAILY, MdCandleRecord.source.in_(BHAV_SOURCES)))).one()
        out.append({"key": s.key, "symbol": s.symbol, "interval": MINUTE,
                    "first_bar": _utc(lo).isoformat() if lo else None, "last_bar": _utc(hi).isoformat() if hi else None,
                    "windows": statuses, "backfill_complete": s.key not in todo and not statuses.get(GAP) and not statuses.get("failed"),
                    "last_error": err, "daily_first": _utc(d_lo).isoformat() if d_lo else None,
                    "daily_last": _utc(d_hi).isoformat() if d_hi else None})
    return out


# --- CLI -------------------------------------------------------------------------------------------------------------------
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.market_lake.backfill", description="M71 PL-1c: paced 1-minute backfill, then gaps only")
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--budget-seconds", type=float, default=None)
    run.add_argument("--symbols", default="")
    sub.add_parser("gaps")
    prog = sub.add_parser("progress")
    prog.add_argument("--symbols", default="")
    return p


async def _main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    factory = _default_session_factory()
    symbols = [s.strip() for s in getattr(args, "symbols", "").split(",") if s.strip()] or None
    if args.cmd == "run":
        report = await run_backfill(factory, symbols=symbols, budget_seconds=args.budget_seconds)
        print(report.as_dict())
        return 0 if report.status in ("ok", "nothing_missing") else 1
    if args.cmd == "gaps":
        report = await drain_gaps(factory)
        print(report.as_dict())
        return 0 if report.status in ("ok", "nothing_missing") else 1
    async with factory() as session:
        tenant_id = await hb2.owner_tenant(session)
        user = await hb2.owner_user(session, tenant_id)
        if user is None:
            print("no history owner user")
            return 1
        now = _wall()
        today = now.astimezone(IST).date()
        last_closed = hb2.last_closed_day(now, await dh._holidays(session, today.year - 1, today.year))
        syms, _ = await universe(session, int(user.id), symbols)
        for row in await series_state(session, tenant_id, int(user.id), syms, last_closed):
            print(row)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    return asyncio.run(_main(argv))


if __name__ == "__main__":
    sys.exit(main())
