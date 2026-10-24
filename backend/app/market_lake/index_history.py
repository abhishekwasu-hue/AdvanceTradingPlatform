"""DATA-H (owner, 24 Oct): the index history the chart reader and the Copilot read - backfilled, kept current, flagged
for the closing auction and reported in `atp.sh health`.

What is stored (DH1):
- Daily bars for LAKE_BACKFILL_DAILY_YEARS (Weekly is built from them on read, as the chart layer already does) and
  1-minute bars for LAKE_BACKFILL_INTRADAY_DAYS (1H and 15M are built from them on read; at least the reader's longest
  intraday lookback, 1H = 180 days) of every index in LAKE_BACKFILL_INDICES (NIFTY, BANKNIFTY, SENSEX, FINNIFTY).
- Source: the broker's own history API (read-only candle endpoints: `get_historical_data`, and `get_intraday_candles`
  for a session that closed today), never an order path.

Whose session and where the rows go (data terms, docs/DATA_SOURCES.md; HB2 / TM-7 / BSH-1):
- Read ONLY through HB2's owner session: HISTORY_OWNER_TENANT_ID confirmed by HISTORY_OWNER_TENANT_NAME, the session the
  pinned HISTORY_OWNER_USER_ID (else the lowest-id active OWNER of that tenant) linked himself at a Tier A broker
  (`intraday_history.owner_session`). No other user's or organisation's session is ever tried.
- Stored ONLY in that tenant's PRIVATE store (`md_tenant_candles`), source `broker:<name>:u<holder id>`: within the
  tenant only that member reads them (BT1b), and the shared lake (`md_candles`) is never written - another organisation
  never sees these bars or anything built from them. Readers: app/market_lake/own_bars.py.

Resumable and idempotent (DH1): fixed request windows (Daily: a calendar year; 1-minute: LAKE_MINUTE_WINDOW_DAYS days
from a Monday) with one `lake_backfill_windows` row each - done windows are never asked again; the window that still
grows is `open` and asked again on the next pass (identical bars are skipped, a changed bar becomes the next version).
Each window is written and recorded in one transaction, so a stopped pass leaves nothing half written. One pass at a
time (advisory lock). Rate: every request takes a token from the credential's shared API budget (app/market_data/
api_budget.py, the same one the charts use) and a pause; a refused token ends the pass (resumed later). A pass has a
time budget. Broker messages are shortened and scrubbed of anything token-like before they are stored or printed.

Kept current (DH2): the trading worker starts a pass once per IST day from LAKE_BACKFILL_HOUR_IST (after the close);
`atp.sh deploy` runs `run --if-missing` (exits at once when nothing is missing).

Closing auction (DH3; generic - the Trade reader's own rule is not in this public repository): per session with both a
Daily bar and 1-minute bars, a 1-minute bar after continuous trading ended (its start at or after the session close) is
flagged `post_close`; a Daily high / low beyond the continuous session's extremes is flagged `auction_high` /
`auction_low` with the continuous extreme; a Daily close that differs from the last continuous price is recorded as
`official_close`. The readers drop / cap those bars (own_bars.apply_flags), so no structure or zone is built from them.

CLI (inside the backend container; `atp.sh lake-backfill [--check]` and `atp.sh health` call it):
    python -m app.market_lake.index_history check      what is missing, whose session, DH6 proof - nothing written
    python -m app.market_lake.index_history run [--if-missing] [--budget-seconds N] [--indices NIFTY,...]
    python -m app.market_lake.index_history health     bars, first / last date per index per timeframe
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import math
import re
import sys
import time as monotonic_clock
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import Float, cast, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import LakeBackfillWindowRecord, MdBarFlagRecord, MdCandleRecord, MdTenantCandleRecord
from app.market_data.calendar import EXCHANGE_SESSIONS, IST
from app.market_lake.ingest import LakeBar, write_candles_bulk

logger = logging.getLogger(__name__)

LOCK_KEY = 0x48420005                     # pg advisory lock id ("HB" 0005); HB0 0001, HB1 0002, HB2 0003
DAILY, MINUTE = "day", "1m"
TIMEFRAMES = (DAILY, MINUTE)
BROKER_INTERVAL = {DAILY: "day", MINUTE: "1min"}
LAKE_MINUTE_WINDOW_DAYS = 7               # calendar days per 1-minute request (well under the broker's range limit)
SESSION = EXCHANGE_SESSIONS["NSE"]        # NSE and BSE indices share the 09:15-15:30 IST continuous session
NO_DATA_FINAL_DAYS = 10                   # an empty window older than this is final (before the index, beyond the depth)
SHORT_TOLERANCE_DAYS = 10                 # health: history starting this many days after the target still counts as full
BSE_INDEX_PREFIX = "BSE_INDEX:"
W = LakeBackfillWindowRecord
_TOKENISH = re.compile(r"[A-Za-z0-9_\-+/=.]{24,}")
Clock = Callable[[], datetime]


class BackfillConfigError(RuntimeError):
    """The owner or his session is not configured - nothing is read."""


def scrub(message: Any, limit: int = 200) -> str:
    """A broker's message, shortened, with anything token-like removed (no secret reaches a log or the table)."""
    return _TOKENISH.sub("[...]", " ".join(str(message).split()))[:limit]


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def _wall() -> datetime:
    return datetime.now(timezone.utc)


# --- what --------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Target:
    name: str                 # NIFTY
    key: str                  # the lake key the readers ask for (NSE_INDEX:NIFTY50)
    symbol: str               # the broker's name (NIFTY 50)
    exchange: str             # NSE / BSE


def lake_key_for(name: str) -> str:
    """The lake key of an index by its trading name: NSE indices as the chart reader asks for them
    (app/ai/tools/copilot.lake_key, "NIFTY" -> NSE_INDEX:NIFTY50), BSE indices as BSE_INDEX:<NAME>."""
    from app.instruments import master
    upper = " ".join(name.strip().upper().split())
    if master.INDEX_EXCHANGE.get(upper) == "BSE":
        return f"{BSE_INDEX_PREFIX}{upper}"
    from app.ai.tools.copilot import lake_key
    return lake_key(upper)


def target(name: str) -> Target:
    from app.instruments import master
    upper = " ".join(name.strip().upper().split())
    symbol = master.INDEX_SYMBOLS.get(upper)
    if symbol is None:
        raise ValueError(f"{name!r} is not an index the backfill knows (known: {', '.join(sorted(master.INDEX_SYMBOLS))})")
    return Target(upper, lake_key_for(upper), symbol, master.INDEX_EXCHANGE.get(upper, "NSE"))


def targets(names: Optional[Sequence[str]] = None) -> Tuple[List[Target], List[str]]:
    out: Dict[str, Target] = {}
    notes: List[str] = []
    for raw in (names if names else config.LAKE_BACKFILL_INDICES):
        try:
            t = target(raw)
        except ValueError as exc:
            notes.append(f"skipped: {exc}")
            continue
        out.setdefault(t.key, t)
    return list(out.values()), notes


def is_target_key(key: str) -> bool:
    try:
        return key in {t.key for t in targets()[0]}
    except Exception:  # noqa: BLE001 - a wording helper never fails a read
        return False


# --- windows -----------------------------------------------------------------------------------------------------------
def first_day(tf: str, last_closed: date) -> date:
    if tf == DAILY:
        return date(last_closed.year - config.LAKE_BACKFILL_DAILY_YEARS, last_closed.month, min(last_closed.day, 28))
    return last_closed - timedelta(days=config.LAKE_BACKFILL_INTRADAY_DAYS)


def windows(tf: str, first: date, last: date) -> List[Tuple[date, date]]:
    """Fixed request windows covering [first, last], NEWEST first: Daily by calendar year, 1-minute by
    LAKE_MINUTE_WINDOW_DAYS days from a Monday. Fixed, so a window keeps its identity from pass to pass."""
    out: List[Tuple[date, date]] = []
    if first > last:
        return out
    if tf == DAILY:
        for year in range(last.year, first.year - 1, -1):
            out.append((date(year, 1, 1), date(year, 12, 31)))
        return out
    span = LAKE_MINUTE_WINDOW_DAYS
    start = last - timedelta(days=last.weekday())                      # the Monday of `last`'s week
    start -= timedelta(days=((start - date(2001, 1, 1)).days % span))  # 2001-01-01 is a Monday: a fixed grid
    while start + timedelta(days=span - 1) >= first:
        out.append((start, start + timedelta(days=span - 1)))
        start -= timedelta(days=span)
    return out


@dataclass(frozen=True)
class Window:
    target: Target
    tf: str
    start: date               # the fixed window
    end: date
    ask_from: date            # the part asked for: the window clipped to the history wanted and the last closed day
    ask_to: date
    status: Optional[str] = None    # what the last pass recorded (None: never asked)

    @property
    def complete(self) -> bool:
        return self.end <= self.ask_to


def is_missing(w: Window) -> bool:
    """A window never asked for, or whose last request failed (an `open` window is kept current by the daily pass)."""
    return w.status in (None, "failed")


async def _known(session: AsyncSession, tenant_id: int, user_id: int) -> Dict[Tuple[str, str, date], str]:
    rows = await session.execute(select(W.instrument_key, W.timeframe, W.window_start, W.status).where(
        W.tenant_id == tenant_id, W.user_id == user_id))
    return {(k, tf, ws): status for k, tf, ws, status in rows}


async def plan(session: AsyncSession, tenant_id: int, user_id: int, tgts: Sequence[Target], last_closed: date) -> List[Window]:
    """The windows still to ask for: Daily first (the reader's first need), then 1-minute; newest first in each."""
    known = await _known(session, tenant_id, user_id)
    out: List[Window] = []
    for tf in TIMEFRAMES:
        first = first_day(tf, last_closed)
        for t in tgts:
            for ws, we in windows(tf, first, last_closed):
                if known.get((t.key, tf, ws)) in ("done", "no_data"):
                    continue
                out.append(Window(t, tf, ws, we, max(ws, first), min(we, last_closed), known.get((t.key, tf, ws))))
    return out


# --- bars --------------------------------------------------------------------------------------------------------------
def session_close(day: date) -> datetime:
    return datetime.combine(day, SESSION.close, tzinfo=IST).astimezone(timezone.utc)


def to_lake_bars(t: Target, tf: str, raw: Iterable[Any], fetched_at: datetime) -> Tuple[List[LakeBar], int]:
    """Broker bars (labelled by START; a naive time is IST) -> lake bars labelled by END, UTC: a Daily bar ends at the
    session close of its IST date (the lake's Daily convention), a 1-minute bar one minute after its start. Bars still
    forming at `fetched_at` are dropped (counted). A bar without a usable price is skipped, never filled in; two different
    bars with one END keep the later one the broker sent."""
    from app.brokers.timestamps import parse_broker_timestamp
    seen: Dict[datetime, LakeBar] = {}
    forming = 0
    for b in raw:
        start = parse_broker_timestamp(getattr(b, "timestamp", None))
        if start is None:
            continue
        try:
            values = [float(getattr(b, n)) for n in ("open", "high", "low", "close")]
            volume = float(getattr(b, "volume", 0) or 0)
        except (TypeError, ValueError):
            continue
        if any(not math.isfinite(v) or v <= 0 for v in values) or not math.isfinite(volume) or volume < 0:
            continue
        if tf == DAILY:
            end = session_close(start.astimezone(IST).date())
        else:
            end = _utc(start) + timedelta(minutes=1)
        if end > _utc(fetched_at):
            forming += 1
            continue
        seen[end] = LakeBar(t.key, tf, end, values[0], values[1], values[2], values[3], int(volume))
    return [seen[k] for k in sorted(seen)], forming


# --- DH3: the closing auction ------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FlagRow:
    key: str
    timeframe: str
    ts: datetime
    kind: str
    value: Optional[float]
    detail: str


def auction_flags(key: str, daily: Dict[date, LakeBar], minutes: Sequence[LakeBar], eps: float = 1e-6) -> List[FlagRow]:
    """Pure DH3 rule (generic - only the flags): per IST session,
    - every 1-minute bar that STARTS at or after the session close (continuous trading has ended) -> post_close;
    - with that session's Daily bar and its continuous 1-minute bars: a Daily high above / low below the continuous
      extremes -> auction_high / auction_low (value: the continuous extreme); a Daily close different from the last
      continuous price -> official_close (value: the Daily close, the session's official close)."""
    out: List[FlagRow] = []
    by_day: Dict[date, List[LakeBar]] = {}
    for m in minutes:
        end = _utc(m.ts)
        day = (end - timedelta(minutes=1)).astimezone(IST).date()
        if end > session_close(day):
            flat = m.open == m.high == m.low == m.close
            out.append(FlagRow(key, MINUTE, end, "post_close", None, "flat bar after continuous trading" if flat else "bar after continuous trading"))
            continue
        by_day.setdefault(day, []).append(m)
    for day, cont in sorted(by_day.items()):
        bar = daily.get(day)
        if bar is None or not cont:
            continue
        hi = max(m.high for m in cont)
        lo = min(m.low for m in cont)
        last = sorted(cont, key=lambda m: _utc(m.ts))[-1].close
        ts = _utc(bar.ts)
        if bar.high > hi + eps:
            out.append(FlagRow(key, DAILY, ts, "auction_high", hi, f"Daily high {bar.high:g} set after continuous trading (continuous high {hi:g})"))
        if bar.low < lo - eps:
            out.append(FlagRow(key, DAILY, ts, "auction_low", lo, f"Daily low {bar.low:g} set after continuous trading (continuous low {lo:g})"))
        if abs(bar.close - last) > eps:
            out.append(FlagRow(key, DAILY, ts, "official_close", bar.close, f"last continuous price {last:g}"))
    return out


async def write_flags(session: AsyncSession, tenant_id: int, rows: Sequence[FlagRow]) -> int:
    """Insert the flags not stored yet; a stored flag whose value changed is updated (a corrected bar). Returns the
    number of flags written or changed. Does not commit."""
    if not rows:
        return 0
    keys = sorted({r.key for r in rows})
    lo, hi = min(r.ts for r in rows), max(r.ts for r in rows)
    existing = {(_utc(f.ts), f.instrument_key, f.timeframe, f.kind): f for f in await session.scalars(select(MdBarFlagRecord).where(
        MdBarFlagRecord.tenant_id == tenant_id, MdBarFlagRecord.instrument_key.in_(keys), MdBarFlagRecord.ts >= lo, MdBarFlagRecord.ts <= hi))}
    n = 0
    for r in rows:
        old = existing.get((_utc(r.ts), r.key, r.timeframe, r.kind))
        if old is None:
            session.add(MdBarFlagRecord(tenant_id=tenant_id, instrument_key=r.key, timeframe=r.timeframe, ts=r.ts, kind=r.kind,
                                        value=r.value, detail=r.detail[:500]))
            n += 1
        elif (None if old.value is None else round(float(old.value), 4)) != (None if r.value is None else round(float(r.value), 4)):
            old.value, old.detail = r.value, r.detail[:500]
            n += 1
    return n


async def flag_days(session: AsyncSession, tenant_id: int, user_id: int, key: str, first: date, last: date) -> int:
    """DH3 over the stored private bars of [first, last] (the member's own rows): computes and writes the flags."""
    from app.market_lake.tenant_store import holder_visible
    lo = datetime.combine(first, time(0, 0), tzinfo=IST).astimezone(timezone.utc)
    hi = datetime.combine(last + timedelta(days=1), time(0, 0), tzinfo=IST).astimezone(timezone.utc)

    async def read(tf: str) -> List[LakeBar]:
        rows = await session.execute(select(MdTenantCandleRecord.ts, MdTenantCandleRecord.version, cast(MdTenantCandleRecord.open, Float),
                                            cast(MdTenantCandleRecord.high, Float), cast(MdTenantCandleRecord.low, Float),
                                            cast(MdTenantCandleRecord.close, Float)).where(
            MdTenantCandleRecord.tenant_id == tenant_id, MdTenantCandleRecord.instrument_key == key, MdTenantCandleRecord.timeframe == tf,
            MdTenantCandleRecord.ts > lo, MdTenantCandleRecord.ts <= hi, holder_visible(MdTenantCandleRecord.source, user_id)).order_by(
            MdTenantCandleRecord.ts, MdTenantCandleRecord.version))
        best: Dict[datetime, LakeBar] = {}
        for ts, _v, o, h, low, c in rows:
            best[_utc(ts)] = LakeBar(key, tf, _utc(ts), o, h, low, c)
        return [best[k] for k in sorted(best)]
    daily = {(_utc(b.ts) - timedelta(minutes=1)).astimezone(IST).date(): b for b in await read(DAILY)}
    return await write_flags(session, tenant_id, auction_flags(key, daily, await read(MINUTE)))


# --- who ---------------------------------------------------------------------------------------------------------------
async def owner(session: AsyncSession, now: datetime) -> Tuple[int, Any, Any]:
    """(tenant_id, user, credential) - HB2's owner and his own Tier A session. BackfillConfigError when the owner is not
    configured or has no usable session today (never another user's)."""
    from app.market_lake import intraday_history
    try:
        tenant_id = await intraday_history.owner_tenant(session)
    except intraday_history.IntradayConfigError as exc:
        raise BackfillConfigError(str(exc)) from None
    found = await intraday_history.owner_session(session, tenant_id, now)
    if found is None:
        raise BackfillConfigError("no usable broker session of the owner's own today (log in to the broker in ATP; no other "
                                  "user's session is ever used)")
    return tenant_id, found[0], found[1]


def _default_adapter(record: Any) -> Any:
    from app.brokers.token_lifecycle import build_adapter
    return build_adapter(record)


def _budgeted(inner: Any, record: Any) -> Any:
    from app.market_data import api_budget
    return api_budget.BudgetedAdapter(inner, record.id, record.broker_name, wait_seconds=10.0)


async def _try_lock(session: AsyncSession) -> bool:
    if session.get_bind().dialect.name != "postgresql":
        return True
    return bool(await session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_KEY}))


async def _holidays(session: AsyncSession, first_year: int, last_year: int) -> Set[date]:
    from app.market_data.calendar import load_holidays
    out: Set[date] = set()
    for year in range(first_year, last_year + 1):
        out |= set(await load_holidays(session, SESSION.holiday_calendar, year) or [])
    return out


async def last_closed_day(session: AsyncSession, now: datetime) -> date:
    from app.market_lake import intraday_history
    today = now.astimezone(IST).date()
    return intraday_history.last_closed_day(now, await _holidays(session, today.year - 1, today.year))


# --- one pass ----------------------------------------------------------------------------------------------------------
@dataclass
class PassReport:
    status: str = "ok"            # ok | nothing_missing | locked | not_configured | no_session | rate_limited | login_refused | time_budget
    tenant_id: Optional[int] = None
    holder: Optional[int] = None
    broker: Optional[str] = None
    planned: int = 0
    requests: int = 0
    done: int = 0
    open: int = 0
    no_data: int = 0
    failed: int = 0
    inserted: int = 0
    corrected: int = 0
    skipped: int = 0
    dropped_forming: int = 0
    flags: int = 0
    remaining: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.status in ("ok", "nothing_missing") and self.failed == 0 and self.remaining == 0

    def lines(self) -> List[str]:
        head = f"lake-backfill: {self.status}"
        if self.status in ("not_configured", "no_session", "locked"):
            return [head] + [f"  note: {n}" for n in self.notes[:3]]
        if self.broker:
            head += f" (via the owner's own {self.broker} session, holder user {self.holder}; rows -> organisation {self.tenant_id}'s private store)"
        out = [head, f"  windows: {self.planned} to ask, {self.requests} requests; {self.done} done, {self.open} still open, "
                     f"{self.no_data} without data, {self.failed} failed, {self.remaining} left for the next pass",
               f"  bars: {self.inserted} new, {self.corrected} corrected, {self.skipped} unchanged, {self.dropped_forming} still forming "
               f"(not stored); closing-auction flags written: {self.flags}"]
        out += [f"  note: {n}" for n in self.notes[:6]]
        return out


async def _record(session: AsyncSession, tenant_id: int, user_id: int, w: Window, status: str, bars: int, error: Optional[str]) -> None:
    row = await session.scalar(select(W).where(W.tenant_id == tenant_id, W.user_id == user_id, W.instrument_key == w.target.key,
                                               W.timeframe == w.tf, W.window_start == w.start))
    if row is None:
        row = W(tenant_id=tenant_id, user_id=user_id, instrument_key=w.target.key, timeframe=w.tf, window_start=w.start, window_end=w.end,
                attempts=0, bars=0)
        session.add(row)
    row.status, row.error, row.attempts, row.updated_at = status, error, int(row.attempts or 0) + 1, _wall()
    if bars:
        row.bars = bars


async def _fetch(adapter: Any, w: Window, now: datetime) -> List[Any]:
    """One request (two when the window holds a session that closed today: the broker serves today's 1-minute bars
    only from its intraday endpoint)."""
    lo = datetime.combine(w.ask_from, time(0, 0), tzinfo=IST)
    hi = datetime.combine(w.ask_to, time(23, 59), tzinfo=IST)
    raw = list(await adapter.get_historical_data(w.target.symbol, w.target.exchange, BROKER_INTERVAL[w.tf], lo, hi) or [])
    if w.tf == MINUTE and w.ask_to == now.astimezone(IST).date() and hasattr(adapter, "get_intraday_candles"):
        raw += list(await adapter.get_intraday_candles(w.target.symbol, w.target.exchange, "1min") or [])
    return raw


async def run_pass(session_factory: Any, *, now: Optional[datetime] = None, names: Optional[Sequence[str]] = None,
                   budget_seconds: Optional[float] = None, pause: Optional[float] = None, if_missing: bool = False,
                   adapter_factory: Optional[Callable[[Any], Any]] = None, wrap: Optional[Callable[[Any, Any], Any]] = None,
                   clock: Optional[Clock] = None, max_requests: Optional[int] = None) -> PassReport:
    """One pass over the missing windows until done, out of time or out of rate budget (resumed by the next pass)."""
    now = now or _wall()
    clock = clock or _wall
    report = PassReport()
    budget = config.LAKE_BACKFILL_BUDGET_SECONDS if budget_seconds is None else budget_seconds
    deadline = monotonic_clock.monotonic() + budget
    pause = config.LAKE_BACKFILL_PAUSE_SECONDS if pause is None else pause
    tgts, notes = targets(names)
    report.notes += notes
    async with session_factory() as lock_session:
        if not await _try_lock(lock_session):
            report.status = "locked"
            report.notes.append("another backfill pass is running")
            return report
        try:
            async with session_factory() as session:
                try:
                    tenant_id, user, record = await owner(session, now)
                except BackfillConfigError as exc:
                    report.status = "no_session" if "session" in str(exc) else "not_configured"
                    report.notes.append(str(exc))
                    return report
                report.tenant_id, report.holder, report.broker = tenant_id, int(user.id), record.broker_name
                last_closed = await last_closed_day(session, now)
                todo = await plan(session, tenant_id, int(user.id), tgts, last_closed)
            report.planned = len(todo)
            if not todo:
                report.status = "nothing_missing"
                return report
            if if_missing and not any(is_missing(w) for w in todo):
                report.status = "nothing_missing"           # only windows already asked: the worker's daily pass keeps them
                report.remaining = 0
                return report
            from app.brokers.exceptions import BrokerAuthenticationError
            from app.market_data.api_budget import BudgetUsed
            from app.market_lake.intraday_history import holder_source
            inner = (adapter_factory or _default_adapter)(record)
            source = holder_source(record.broker_name, int(user.id))
            for i, w in enumerate(todo):
                if monotonic_clock.monotonic() > deadline or (max_requests is not None and report.requests >= max_requests):
                    report.status = "time_budget"
                    report.remaining = len(todo) - i
                    break
                adapter = (wrap or _budgeted)(inner, record)
                try:
                    raw = await _fetch(adapter, w, now)
                except BudgetUsed:
                    report.status = "rate_limited"
                    report.remaining = len(todo) - i
                    report.notes.append("the broker rate budget is in use - the next pass resumes here")
                    break
                except BrokerAuthenticationError:
                    report.status = "login_refused"
                    report.remaining = len(todo) - i
                    report.notes.append("the broker refused the owner's login - log in to the broker in ATP again")
                    break
                except Exception as exc:  # noqa: BLE001 - one window's broker error is recorded and retried later
                    report.requests += 1
                    report.failed += 1
                    why = scrub(f"{type(exc).__name__}: {exc}")
                    report.notes.append(f"{w.target.name} {w.tf} {w.ask_from}..{w.ask_to}: {why}")
                    async with session_factory() as session:
                        await _record(session, tenant_id, int(user.id), w, "failed", 0, why)
                        await session.commit()
                    continue
                report.requests += 1
                bars, forming = to_lake_bars(w.target, w.tf, raw, clock())
                bars = [b for b in bars if w.ask_from <= (_utc(b.ts) - timedelta(minutes=1)).astimezone(IST).date() <= w.ask_to]
                report.dropped_forming += forming
                async with session_factory() as session:
                    try:
                        res = await write_candles_bulk(session, bars, source, tenant_id=tenant_id, ingested_at=_utc(clock()))
                        if w.tf == MINUTE and bars:
                            report.flags += await flag_days(session, tenant_id, int(user.id), w.target.key, w.ask_from, w.ask_to)
                        if bars:
                            status = "done" if w.complete else "open"
                        else:
                            status = "no_data" if w.complete and (last_closed - w.end).days > NO_DATA_FINAL_DAYS else "open"
                        await _record(session, tenant_id, int(user.id), w, status, len(bars), None)
                        await session.commit()
                    except Exception as exc:  # noqa: BLE001 - nothing of this window is kept; retried on the next pass
                        await session.rollback()
                        logger.exception("DATA-H: storing %s %s %s failed", w.target.key, w.tf, w.start)
                        report.failed += 1
                        report.notes.append(f"{w.target.name} {w.tf} {w.ask_from}..{w.ask_to}: not stored ({type(exc).__name__})")
                        continue
                report.inserted += res.inserted
                report.corrected += res.corrected
                report.skipped += res.skipped
                report.done += status == "done"
                report.open += status == "open"
                report.no_data += status == "no_data"
                if pause:
                    await asyncio.sleep(pause)
            logger.info("DATA-H backfill: %s", " | ".join(report.lines()))
            return report
        finally:
            await lock_session.rollback()


# --- DH4: coverage -----------------------------------------------------------------------------------------------------
HEALTH_TFS = ("W", "D", "1H", "15M")


@dataclass
class Coverage:
    name: str
    tf: str
    bars: int
    first: Optional[date]
    last: Optional[date]
    source: str               # own | shared | -
    flag: str                 # ok | MISSING | SHORT
    why: str = ""

    def line(self) -> str:
        span = f"{self.first} .. {self.last}" if self.first else "-"
        tail = self.flag + (f" ({self.why})" if self.why else "")
        return f"  {self.name:<10} {self.tf:<4} {self.bars:>6} bars  {span:<24} {self.source:<6} {tail}"


def _buckets(ends: Iterable[datetime], minutes: int) -> Tuple[int, Optional[date], Optional[date]]:
    """Distinct `minutes` bars (aligned to the session open) the 1-minute bar ENDs build, and their first / last day.
    A bar after continuous trading builds nothing (DH3)."""
    seen: Set[Tuple[date, int]] = set()
    for end in ends:
        start = _utc(end) - timedelta(minutes=1)
        ist = start.astimezone(IST)
        open_ = datetime.combine(ist.date(), SESSION.open, tzinfo=IST)
        if not (open_ <= ist < datetime.combine(ist.date(), SESSION.close, tzinfo=IST)):
            continue
        seen.add((ist.date(), int((ist - open_).total_seconds() // (minutes * 60))))
    if not seen:
        return 0, None, None
    days = sorted(d for d, _ in seen)
    return len(seen), days[0], days[-1]


async def _ends(session: AsyncSession, key: str, labels: Sequence[str], scope: Optional[Tuple[int, int]], since: datetime) -> List[datetime]:
    from app.charts.price_action_layer import SHARED_SOURCES
    from app.market_lake.tenant_store import holder_visible
    if scope is None:
        rows = await session.scalars(select(MdCandleRecord.ts).where(
            MdCandleRecord.instrument_key == key, MdCandleRecord.timeframe.in_(list(labels)), MdCandleRecord.ts > since,
            or_(*[MdCandleRecord.source.like(f"{p}%") for p in SHARED_SOURCES])).distinct())
    else:
        rows = await session.scalars(select(MdTenantCandleRecord.ts).where(
            MdTenantCandleRecord.tenant_id == scope[0], MdTenantCandleRecord.instrument_key == key,
            MdTenantCandleRecord.timeframe.in_(list(labels)), MdTenantCandleRecord.ts > since,
            holder_visible(MdTenantCandleRecord.source, scope[1])).distinct())
    return [_utc(t) for t in rows]


async def coverage(session: AsyncSession, scope: Optional[Tuple[int, int]], last_closed: date,
                   names: Optional[Sequence[str]] = None) -> List[Coverage]:
    """Per index per timeframe (W, D, 1H, 15M) what the reader can read: the source holding more bars (the owner's own
    store or the shared lake), its bar count and first / last date; MISSING (none) or SHORT (starts later than the
    history wanted, or ends before the last closed session)."""
    out: List[Coverage] = []
    tgts, _ = targets(names)
    daily_first = first_day(DAILY, last_closed)
    intra_first = first_day(MINUTE, last_closed)
    for t in tgts:
        since_d = datetime.combine(daily_first - timedelta(days=400), time(0, 0), tzinfo=IST)
        since_i = datetime.combine(intra_first - timedelta(days=30), time(0, 0), tzinfo=IST)
        best_d = await _ends(session, t.key, ("1d", "day"), None, since_d)
        src_d = "shared"
        if scope is not None:
            own_d = await _ends(session, t.key, ("1d", "day"), scope, since_d)
            if len(own_d) > len(best_d):
                best_d, src_d = own_d, "own"
        best_m = await _ends(session, t.key, ("1m",), None, since_i)
        src_m = "shared"
        if scope is not None:
            own_m = await _ends(session, t.key, ("1m",), scope, since_i)
            if len(own_m) > len(best_m):
                best_m, src_m = own_m, "own"
        days = sorted({e.astimezone(IST).date() for e in best_d})
        weeks = sorted({d - timedelta(days=d.weekday()) for d in days})
        rows = [("W", len(weeks), weeks[0] if weeks else None, days[-1] if days else None, src_d, daily_first),
                ("D", len(days), days[0] if days else None, days[-1] if days else None, src_d, daily_first)]
        for label, minutes in (("1H", 60), ("15M", 15)):
            n, f, la = _buckets(best_m, minutes)
            rows.append((label, n, f, la, src_m, intra_first))
        for tf, n, f, la, src, want in rows:
            if not n:
                out.append(Coverage(t.name, tf, 0, None, None, "-", "MISSING"))
                continue
            why = []
            if f is not None and (f - want).days > SHORT_TOLERANCE_DAYS:
                why.append(f"starts {f}, wanted from {want}")
            if la is not None and la < last_closed:
                why.append(f"ends {la}, last session {last_closed}")
            out.append(Coverage(t.name, tf, n, f, la, src, "SHORT" if why else "ok", "; ".join(why)))
    return out


async def missing(session: AsyncSession, now: datetime) -> Optional[int]:
    """Windows never asked for or failed (`is_missing`); None when the owner is not configured."""
    from app.market_lake import own_bars
    scope = await own_bars.history_owner(session)
    if scope is None:
        return None
    todo = await plan(session, scope[0], scope[1], targets()[0], await last_closed_day(session, now))
    return sum(1 for w in todo if is_missing(w))


# --- check (nothing written) -------------------------------------------------------------------------------------------
def round_to(value: Optional[float], step: int) -> bool:
    return value is not None and abs(value / step - round(value / step)) < 1e-9


async def check(session_factory: Any, *, now: Optional[datetime] = None, adapter_factory: Optional[Callable[[Any], Any]] = None,
                wrap: Optional[Callable[[Any, Any], Any]] = None) -> List[str]:
    """What a pass would do and the DH6 proof (the stored official close beside the broker's own last Daily close, per
    index) - the database is only read; the broker gets one Daily request per index."""
    from app.market_lake import own_bars
    now = now or _wall()
    lines: List[str] = []
    tgts, notes = targets()
    async with session_factory() as session:
        last_closed = await last_closed_day(session, now)
        scope = await own_bars.history_owner(session)
        try:
            tenant_id, user, record = await owner(session, now)
            lines.append(f"lake-backfill --check: owner organisation {tenant_id}, holder user {user.id}, his own {record.broker_name} "
                         f"session; rows go to that organisation's private store only")
        except BackfillConfigError as exc:
            record = None
            lines.append(f"lake-backfill --check: {exc}")
        if scope is not None:
            todo = await plan(session, scope[0], scope[1], tgts, last_closed)
            per: Dict[Tuple[str, str], int] = {}
            for w in todo:
                per[(w.target.name, w.tf)] = per.get((w.target.name, w.tf), 0) + 1
            lines.append(f"  last closed session {last_closed}; windows to ask: {len(todo)} (about {len(todo)} requests)")
            for (name, tf), n in sorted(per.items()):
                lines.append(f"    {name:<10} {'Daily' if tf == DAILY else '1-minute':<9} {n} window(s)")
        lines += [f"  note: {n}" for n in notes]
        closes = {t.key: await own_bars.official_close(session, t.key, scope) for t in tgts}
    adapter = None
    if record is not None:
        adapter = (wrap or _budgeted)((adapter_factory or _default_adapter)(record), record)
    lines.append("  DH6 last close (stored official close | broker's last Daily close):")
    for t in tgts:
        stored = closes.get(t.key)
        broker_txt = "-"
        if adapter is not None:
            try:
                lo = datetime.combine(last_closed - timedelta(days=10), time(0, 0), tzinfo=IST)
                hi = datetime.combine(last_closed, time(23, 59), tzinfo=IST)
                bars, _ = to_lake_bars(t, DAILY, await adapter.get_historical_data(t.symbol, t.exchange, "day", lo, hi), now)
                if bars:
                    b = bars[-1]
                    broker_txt = f"{b.close:g} on {(_utc(b.ts) - timedelta(minutes=1)).astimezone(IST).date()}"
            except Exception as exc:  # noqa: BLE001 - the check reports, never raises
                broker_txt = f"not read ({scrub(type(exc).__name__)})"
        stored_txt = f"{stored['close']:g} on {stored['date']} ({stored['source']})" if stored else "none stored"
        strike = " - a multiple of 50 (looks like a strike)" if stored and round_to(stored["close"], 50) else ""
        lines.append(f"    {t.name:<10} {stored_txt}{strike} | {broker_txt}")
    return lines


# --- worker ------------------------------------------------------------------------------------------------------------
def nightly_due(now: datetime, last_day: Optional[date]) -> Optional[date]:
    """The IST date a daily pass is due for: from LAKE_BACKFILL_HOUR_IST, once per day."""
    ist = now.astimezone(IST)
    if ist.hour < config.LAKE_BACKFILL_HOUR_IST or last_day == ist.date():
        return None
    return ist.date()


# --- CLI ---------------------------------------------------------------------------------------------------------------
async def _health_lines(session_factory: Any, now: datetime) -> List[str]:
    from app.market_lake import own_bars
    async with session_factory() as session:
        scope = await own_bars.history_owner(session)
        last_closed = await last_closed_day(session, now)
        rows = await coverage(session, scope, last_closed)
    bad = sum(1 for r in rows if r.flag != "ok")
    head = (f"market history (W / D / 1H / 15M per index; last session {last_closed}; "
            f"{'the owner store and the shared lake' if scope else 'the shared lake only - the owner is not configured'}): "
            f"{'all ok' if not bad else f'{bad} MISSING / SHORT'}")
    return [head] + [r.line() for r in rows]


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="index_history", description="DATA-H: index history for the chart reader")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("health")
    run = sub.add_parser("run")
    run.add_argument("--if-missing", action="store_true")
    run.add_argument("--budget-seconds", type=float, default=None)
    run.add_argument("--indices", default="")
    return p


async def _main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    from app.db.session import _session_factory
    now = _wall()
    if args.cmd == "health":
        print("\n".join(await _health_lines(_session_factory, now)))
        return 0
    if args.cmd == "check":
        print("\n".join(await check(_session_factory, now=now)))
        return 0
    names = [n for n in args.indices.split(",") if n.strip()] or None
    report = await run_pass(_session_factory, now=now, names=names, budget_seconds=args.budget_seconds, if_missing=args.if_missing)
    print("\n".join(report.lines()))
    if not args.if_missing and report.status in ("ok", "nothing_missing", "time_budget", "rate_limited"):
        print("\n".join(await _health_lines(_session_factory, now)))
    return {"not_configured": 2, "no_session": 3, "login_refused": 3}.get(report.status, 0)


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(_main(argv))


if __name__ == "__main__":
    sys.exit(main())
