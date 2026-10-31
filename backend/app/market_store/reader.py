"""M71 PL-3 + PL-1(a): ONE store-first read for every feature (owner order M71; inventory docs/design/M71_READ_PATHS.md).

Order of reads (`read_bars`, `read_many`):
1. **The own store.**
   - Daily / Weekly: the shared lake's NSE bhavcopy bars (licensed sources only, `bar_source.lake_sources`), adjusted
     for splits and bonuses on record - every NSE symbol, ZERO broker calls. Weekly is built from them. Another
     exchange is not in the files: the result is cold and the caller keeps its own path.
   - Intraday: the reader's OWN 1-minute rows in the private store (`md_tenant_candles`), built into 3m ... 1H on read
     from the exchange's session open (the closing-auction flags applied, as `own_bars.fetch_rows`). M71Q-2 (PL-5
     terms): only rows read through the reader's own broker session (`broker:<name>:u<id>`); the tenant's other rows
     only for the tenant's OWNER. Never another member's rows, never the shared index scope - one user's broker data
     is never shown to another user.
2. **Only the missing NEWEST bars by REST**, and only when the store's last bar is older than the last CLOSED bar of
   the interval: 1-minute bars after the store's last one, at most TOPUP_MAX_DAYS back, through the reader's own
   session only (`RestSource`; M71Q-2), every call paced by the credential's shared API budget
   (`api_budget.BudgetedAdapter`, on the worker's `rate_budget` limits - neither is changed here). The topped-up bars
   are kept per credential until the interval's next bar closes, so a second run on the same symbols makes no history
   call. Daily / Weekly never call the broker.
3. **Never the full history per run.** History older than the store's first bar (or older than the top-up window) is
   reported as a gap and handed to the gap-fill queue when it exists (`request_gap_fill`, PL-1c); else it is recorded in
   the result only.

No look-ahead: only bars closed by `now` (a daily bar once its session has closed, a week once its last session has).
Gaps are never filled with zeros or copied bars. Nothing here writes a bar.
"""
from __future__ import annotations

import importlib
import json
import logging
import time as clock
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import Float, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import OHLCVBar
from app.market_data.calendar import EXCHANGE_SESSIONS, IST, is_trading_day, session_family
from app.market_store.models import (
    COLD,
    CONNECT_FOR_INTRADAY,
    DAILY,
    INTRADAY_MINUTES,
    NEWEST,
    NEWEST_BUDGET,
    NEWEST_DAILY,
    NOT_STORED,
    OLDER,
    WEEKLY,
    BarsResult,
    Gap,
    RestSource,
    canonical,
    empty_frame,
)

logger = logging.getLogger(__name__)

MINUTE = timedelta(minutes=1)
TOPUP_MAX_DAYS = 7                     # the newest bars a run may read by REST (calendar days); older = a queued gap
OLDER_GAP_SLACK_DAYS = 5               # a store that starts within this of the window's start has no older gap (holidays)
ROWS_PER_DAY = 400                     # one NSE session is 375 one-minute bars
CACHE_PREFIX = "ms:topup"
CACHE_MAX_SECONDS = 3600
_LOCAL_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
LOCAL_CACHE_MAX = 4096
STORE_FAMILIES = ("NSE",)              # the session family whose bars the store holds (NSE, BSE, NFO, BFO)
GAP_FILL_MODULES = ("app.market_lake.gap_fill", "app.market_lake.backfill", "app.market_lake.intraday_history",
                    "app.market_lake.index_history")


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


# --- the calendar: the last CLOSED bar ------------------------------------------------------------------------------------

def last_closed_day(now: datetime, holidays: set[date], exchange: str = "NSE") -> date:
    """The newest session whose Daily bar is final at `now`: today once its session has closed, else the one before."""
    spec = EXCHANGE_SESSIONS[session_family(exchange)]
    ist = now.astimezone(IST)
    day = ist.date() if ist.time() >= spec.close else ist.date() - timedelta(days=1)
    while not is_trading_day(day, holidays):
        day -= timedelta(days=1)
    return day


def last_closed_end(now: datetime, minutes: int, holidays: set[date], exchange: str = "NSE") -> datetime:
    """The END (UTC) of the newest `minutes` bar closed at `now`, buckets from the session open, the last one cut at the
    close. Before a session's first bar has closed (or on a holiday) it is the previous session's close."""
    spec = EXCHANGE_SESSIONS[session_family(exchange)]
    ist = now.astimezone(IST)
    today = ist.date()
    if is_trading_day(today, holidays):
        open_ = datetime.combine(today, spec.open, tzinfo=IST)
        close = datetime.combine(today, spec.close, tzinfo=IST)
        period = timedelta(minutes=minutes)
        if ist >= close:
            return close.astimezone(UTC)
        if ist >= open_ + period:
            return (open_ + ((ist - open_) // period) * period).astimezone(UTC)
    day = today - timedelta(days=1)
    while not is_trading_day(day, holidays):
        day -= timedelta(days=1)
    return datetime.combine(day, spec.close, tzinfo=IST).astimezone(UTC)


def next_close_after(end: datetime, minutes: int, holidays: set[date], exchange: str = "NSE") -> datetime:
    """When the bar after the one ending at `end` closes (what a topped-up copy is kept until)."""
    spec = EXCHANGE_SESSIONS[session_family(exchange)]
    ist = end.astimezone(IST)
    day = ist.date()
    close = datetime.combine(day, spec.close, tzinfo=IST)
    if is_trading_day(day, holidays) and ist < close:
        return min(ist + timedelta(minutes=minutes), close).astimezone(UTC)
    day += timedelta(days=1)
    while not is_trading_day(day, holidays):
        day += timedelta(days=1)
    open_ = datetime.combine(day, spec.open, tzinfo=IST)
    return min(open_ + timedelta(minutes=minutes), datetime.combine(day, spec.close, tzinfo=IST)).astimezone(UTC)


async def holidays_for(session: AsyncSession, exchange: str) -> set[date]:
    from app.market_data.calendar import family_holidays
    try:
        return await family_holidays(session, exchange)
    except Exception:  # noqa: BLE001 - no calendar rows: weekends only (the caller's bars still read)
        logger.info("no holiday calendar for %s; weekends only", exchange)
        return set()


# --- gap-fill queue (PL-1c) -----------------------------------------------------------------------------------------------

def _gap_fill() -> Any:
    """PL-1c's `request_gap_fill(symbol, interval, start, end)` once it is on main; None until then."""
    for name in GAP_FILL_MODULES:
        try:
            module = importlib.import_module(name)
        except Exception:  # noqa: BLE001 - a module that does not exist (yet)
            continue
        fn = getattr(module, "request_gap_fill", None)
        if callable(fn):
            return fn
    return None


async def _queue(symbol: str, interval: str, gap: Gap) -> Gap:
    fn = _gap_fill()
    if fn is None:
        return gap
    try:
        out = fn(symbol, interval, gap.start, gap.end)
        if hasattr(out, "__await__"):
            await out
    except Exception:  # noqa: BLE001 - queuing is best effort; the gap is still reported
        logger.warning("gap fill request failed for %s %s", symbol, interval, exc_info=True)
        return gap
    return Gap(gap.start, gap.end, gap.kind, gap.reason, queued=True)


# --- Daily / Weekly: the NSE bhavcopy (zero broker calls) -----------------------------------------------------------------

def eod_served(exchange: str) -> bool:
    return (exchange or "NSE").strip().upper() == "NSE"


async def _eod_frame(session: AsyncSession, symbol: str, first: date, last: date) -> pd.DataFrame:
    """The licensed Daily bars of [first, last] (India dates), stamped like a broker's Daily candle."""
    from app.ai.tools.copilot import lake_key
    from app.backtest import bar_source
    from app.market_lake import adjust
    from app.market_lake.bhavcopy_backfill import bar_end
    key = lake_key(symbol)
    sources = await bar_source.lake_sources(session, key)
    if not sources:
        return empty_frame()
    sym = key.split(":", 1)[-1]
    rows = await adjust.candles(session, key, bar_source.LAKE_TIMEFRAME, bar_end(first - timedelta(days=1)), bar_end(last),
                                symbol=sym, exchange="NSE", kind="INDEX" if key.startswith("NSE_INDEX:") else "EQ", adjusted=True,
                                sources=sources)
    df = bar_source.to_frame(rows)
    return df if len(df) else empty_frame()


def _week_closed(week_first: pd.Timestamp, last_day: date, holidays: set[date]) -> bool:
    """A week's bar is closed once every session of that week up to Friday is at or before `last_day`."""
    start = week_first.tz_convert(IST).date() if week_first.tzinfo is not None else week_first.date()
    friday = start + timedelta(days=4 - start.weekday())
    day = friday
    while day >= start and not is_trading_day(day, holidays):
        day -= timedelta(days=1)
    return day <= last_day


async def read_eod(session: AsyncSession, symbol: str, exchange: str, interval: str, start: datetime, end: datetime, *,
                   now: datetime | None = None, holidays: set[date] | None = None) -> BarsResult:
    from app.backtest import bar_source
    interval = canonical(interval)
    now = now or datetime.now(UTC)
    sym = symbol.strip().upper()
    out = BarsResult(sym, (exchange or "NSE").strip().upper(), interval)
    if not eod_served(exchange):
        out.gaps.append(Gap(_utc(start), _utc(end), COLD, NOT_STORED))
        return out
    hol = holidays if holidays is not None else await holidays_for(session, "NSE")
    closed = last_closed_day(now, hol)
    first = _utc(start).astimezone(IST).date()
    last = min(_utc(end).astimezone(IST).date(), closed)
    df = await _eod_frame(session, sym, first, last) if first <= last else empty_frame()
    if len(df):
        df = df[[d <= last for d in df.index.tz_convert(IST).date]]      # never a bar of a session not closed yet
    if df.empty:
        out.gaps.append(await _queue(sym, interval, Gap(_utc(start), _utc(end), COLD, NOT_STORED)))
        return out
    days = list(df.index.tz_convert(IST).date)
    if days[0] > first + timedelta(days=OLDER_GAP_SLACK_DAYS):
        out.gaps.append(await _queue(sym, interval, Gap(_utc(start), df.index[0].to_pydatetime(), OLDER,
                                                        "Older daily history is not stored yet.")))
    if days[-1] < last:
        out.gaps.append(Gap(df.index[-1].to_pydatetime(), _utc(end), NEWEST, NEWEST_DAILY))
    out.stored_bars = int(len(df))
    if interval == WEEKLY:
        df = bar_source.weekly(df)
        if len(df) and not _week_closed(df.index[-1], closed, hol):
            df = df.iloc[:-1]                                             # the week still trading is not a closed bar
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
    out.frame = df[["open", "high", "low", "close", "volume"]].astype(float)
    out.source = bar_source.LAKE_SOURCE
    return out


# --- intraday: the reader's OWN 1-minute rows ------------------------------------------------------------------------------

def intraday_key(symbol: str, exchange: str) -> str | None:
    """The store key of an NSE-family symbol ("NIFTY" -> NSE_INDEX:NIFTY50, "RELIANCE" -> NSE:RELIANCE), else None."""
    ex = (exchange or "NSE").strip().upper()
    if session_family(ex) not in STORE_FAMILIES:
        return None
    if ex in ("NSE", "BSE"):
        from app.market_lake.index_history import lake_key_for
        return lake_key_for(symbol)
    return f"{ex}:{' '.join(symbol.strip().upper().split()).replace(' ', '')}"


def _is_owner(user: Any) -> bool:
    return str(getattr(user, "role", "") or "").upper() in ("OWNER", "USERROLE.OWNER")


def may_top_up(user: Any, record: Any) -> bool:
    """M71Q-2: a credential the reader linked himself, or - for the tenant's OWNER - the tenant's own credential."""
    if user is None or record is None:
        return False
    if getattr(record, "tenant_id", None) != getattr(user, "tenant_id", None):
        return False
    return getattr(record, "user_id", None) == getattr(user, "id", None) or _is_owner(user)


async def own_rows(session: AsyncSession, user: Any, key: str, start: datetime, end: datetime, *, limit: int,
                   dh3: bool = True) -> list[tuple[Any, ...]]:
    """The reader's own 1-minute rows with END in (start, end] (highest visible version per END; a correction counts
    once ingested), DH3 flags applied unless `dh3` is False (the engine's mode: rows as stored). M71Q-2: his own broker session's rows; the tenant's other rows for the OWNER
    only. More than `limit` rows raises ValueError (refused, never cut)."""
    from app.db.models import MdTenantCandleRecord as R
    from app.market_lake import own_bars
    from app.market_lake.tenant_store import HOLDER_PREFIX, holder_visible
    tenant_id, user_id = int(getattr(user, "tenant_id", 0) or 0), int(getattr(user, "id", 0) or 0)
    if tenant_id <= 0 or user_id <= 0:
        return []
    whose = holder_visible(R.source, user_id) if _is_owner(user) else R.source.like(f"{HOLDER_PREFIX}%:u{user_id}")
    query = select(R.ts, R.version, cast(R.open, Float), cast(R.high, Float), cast(R.low, Float), cast(R.close, Float), R.volume).where(
        R.tenant_id == tenant_id, R.instrument_key == key, R.timeframe == "1m", whose,
        or_(R.version == 1, R.ingested_at <= end), R.ts > start, R.ts <= end).order_by(R.ts, R.version)
    best: dict[datetime, tuple[Any, ...]] = {}
    for row in (await session.execute(query)).all():
        best[_utc(row[0])] = tuple(row)
        if len(best) > limit:
            raise ValueError(f"the window holds more than {limit} one-minute bars; choose a later start")
    rows = [best[t] for t in sorted(best)]
    if not rows or not dh3:
        return rows
    return own_bars.apply_flags(rows, await own_bars.flags(session, tenant_id, key, "1m", start, end))


def _minute_frame(bars: Sequence[OHLCVBar]) -> pd.DataFrame:
    """1-minute START-stamped bars -> the frame `job.resample_closed` takes (timestamp, OHLCV, close_at)."""
    from app.price_action.zones.job import FRAME_COLUMNS
    if not bars:
        return pd.DataFrame(columns=FRAME_COLUMNS)
    ts = pd.to_datetime([_utc(b.timestamp) for b in bars], utc=True)
    return pd.DataFrame({"timestamp": ts, "open": [float(b.open) for b in bars], "high": [float(b.high) for b in bars],
                         "low": [float(b.low) for b in bars], "close": [float(b.close) for b in bars],
                         "volume": [float(b.volume) for b in bars], "close_at": ts + pd.Timedelta(minutes=1)})


def build_interval(bars: Sequence[OHLCVBar], interval: str, now: datetime, exchange: str = "NSE") -> pd.DataFrame:
    """1-minute bars -> closed `interval` bars from the session open (cut at the close), START-stamped UTC index. ONE
    resampler: the lake's `price_action.zones.job.resample_closed` (the one the chart layer builds stored 1-minute
    bars with, `charts.price_action_layer._to_frame`)."""
    from app.price_action.zones import job
    minutes = INTRADAY_MINUTES[canonical(interval)]
    df1 = _minute_frame(sorted(bars, key=lambda b: _utc(b.timestamp)))
    if not len(df1):
        return empty_frame()
    df1 = df1.drop_duplicates("timestamp", keep="last")
    sess = EXCHANGE_SESSIONS[session_family(exchange)]
    # a bucket counts as closed only up to the newest minute the bars hold: a bucket whose later minutes were not read
    # (the store behind, no top-up) is not complete and is left out, never shown as a closed bar
    as_of = min(pd.Timestamp(_utc(now)), pd.Timestamp(df1["close_at"].max()))
    if minutes == 1:
        df = df1[df1["close_at"] <= as_of]
    else:
        df = job.resample_closed(df1, minutes, as_of, sess)
    if not len(df):
        return empty_frame()
    out = pd.DataFrame({c: df[c].astype(float).to_numpy() for c in ("open", "high", "low", "close", "volume")},
                       index=pd.DatetimeIndex(pd.to_datetime(df["timestamp"], utc=True), name="timestamp"))
    if df1["volume"].isna().all():
        out["volume"] = float("nan")                       # no volume stored (an index): missing, not 0
    return out.sort_index()


def _row_bars(rows: Sequence[tuple[Any, ...]]) -> list[OHLCVBar]:
    """Store rows (END-labelled) -> START-stamped 1-minute bars (END - 1 minute): the boundary where the lake's END
    labels become the START labels the screener and charts use. A missing volume stays missing (NaN), never 0."""
    return [OHLCVBar(timestamp=_utc(r[0]) - MINUTE, open=float(r[2]), high=float(r[3]), low=float(r[4]), close=float(r[5]),
                     volume=float("nan") if r[6] is None else float(r[6])) for r in rows]


# --- the newest bars by REST (paced, cached per credential) ---------------------------------------------------------------

def paced_adapter(rest: RestSource) -> Any:
    """THE one place a top-up's broker adapter is paced: every history call takes a token from the credential's shared
    API budget first (`api_budget.BudgetedAdapter`, on the worker's `rate_budget` limits). When PL-2's budgeted
    data-adapter factory lands, this line is the switch."""
    from app.market_data import api_budget
    return api_budget.BudgetedAdapter(rest.adapter, rest.record_id, rest.broker, wait_seconds=rest.wait_seconds)


def _cache_get(name: str) -> list[dict[str, Any]] | None:
    hit = _LOCAL_CACHE.get(name)
    if hit is None:
        return None
    if hit[0] <= clock.monotonic():
        _LOCAL_CACHE.pop(name, None)
        return None
    return hit[1]


async def _cached(name: str) -> list[OHLCVBar] | None:
    raw = _cache_get(name)
    if raw is None:
        from app.cache.client import cache_get
        text = await cache_get(name)
        if text is None:
            return None
        try:
            raw = json.loads(text)
        except ValueError:
            return None
    return [OHLCVBar.model_validate(b) for b in raw]


async def _keep(name: str, bars: list[OHLCVBar], ttl: int) -> None:
    if ttl <= 0:
        return
    raw = [b.model_dump(mode="json") for b in bars]
    if len(_LOCAL_CACHE) >= LOCAL_CACHE_MAX:
        now = clock.monotonic()
        for stale in [k for k, (exp, _) in _LOCAL_CACHE.items() if exp <= now] or list(_LOCAL_CACHE)[: LOCAL_CACHE_MAX // 4]:
            _LOCAL_CACHE.pop(stale, None)
    _LOCAL_CACHE[name] = (clock.monotonic() + ttl, raw)
    from app.cache.client import cache_set
    await cache_set(name, json.dumps(raw), ttl)


def reset_cache() -> None:
    _LOCAL_CACHE.clear()


async def _fetch_newest(rest: Any, symbol: str, exchange: str, after: datetime, now: datetime, holidays: set[date]) -> list[OHLCVBar]:
    """The 1-minute bars with START >= `after` and END <= `now`: past sessions through the history endpoint, the running
    one through the intraday endpoint - at most two calls."""
    spec = EXCHANGE_SESSIONS[session_family(exchange)]
    today = now.astimezone(IST).date()
    today_open = datetime.combine(today, spec.open, tzinfo=IST).astimezone(UTC)
    trading_today = is_trading_day(today, holidays) and now >= today_open
    raw: list[Any] = []
    if after < (today_open if trading_today else now):
        hist_end = min(now, today_open - MINUTE) if trading_today else now
        raw += list(await rest.get_historical_data(symbol, exchange, "1min", after, hist_end) or [])
    if trading_today:
        raw += list(await rest.get_intraday_candles(symbol, exchange, "1min") or [])
    out: dict[datetime, OHLCVBar] = {}
    for b in raw:
        ts = _utc(b.timestamp)
        if ts >= after and ts + MINUTE <= now:
            out[ts] = OHLCVBar(timestamp=ts, open=float(b.open), high=float(b.high), low=float(b.low), close=float(b.close),
                               volume=float("nan") if b.volume is None else float(b.volume))
    return [out[t] for t in sorted(out)]


# --- one read -------------------------------------------------------------------------------------------------------------

async def read_intraday(session: AsyncSession, symbol: str, exchange: str, interval: str, start: datetime, end: datetime, *,
                        user: Any = None, rest: RestSource | None = None, budgeted: Any = None, now: datetime | None = None,
                        holidays: set[date] | None = None, dh3: bool = True) -> BarsResult:
    """Phase 1 (the store, one DB read) and phase 2 (the newest bars) of one intraday read."""
    stored = await store_intraday(session, symbol, exchange, interval, start, end, user=user, now=now, dh3=dh3)
    hol = holidays if holidays is not None else await holidays_for(session, exchange)
    return await top_up(stored, rest=rest, budgeted=budgeted, now=now, holidays=hol)


class _Stored(BarsResult):
    """A store read waiting for its top-up: the raw 1-minute bars and the window."""
    minutes: list[OHLCVBar]
    start: datetime
    end: datetime


async def store_intraday(session: AsyncSession, symbol: str, exchange: str, interval: str, start: datetime, end: datetime, *,
                         user: Any = None, now: datetime | None = None, dh3: bool = True) -> _Stored:
    interval = canonical(interval)
    now = now or datetime.now(UTC)
    sym = symbol.strip().upper()
    out = _Stored(sym, (exchange or "NSE").strip().upper(), interval)
    out.start, out.end, out.minutes = _utc(start), min(_utc(end), _utc(now)), []
    key = intraday_key(sym, out.exchange)
    if key is None or user is None:
        return out
    days = max(1, (out.end - out.start).days + 1)
    try:
        rows = await own_rows(session, user, key, out.start, out.end, limit=ROWS_PER_DAY * (days + 1), dh3=dh3)
    except ValueError:
        logger.info("store window too large for %s %s", sym, interval)
        rows = []
    out.minutes = _row_bars(rows)
    return out


async def top_up(stored: _Stored, *, rest: RestSource | None, budgeted: Any = None, now: datetime | None = None,
                 holidays: set[date]) -> BarsResult:
    """Phase 2 of an intraday read (no database): the newest bars after the store's last one by REST when the store is
    behind the last closed bar, then the closed `interval` frame and the gaps."""
    from app.market_data import api_budget
    now = _utc(now or datetime.now(UTC))
    interval, sym, ex = stored.interval, stored.symbol, stored.exchange
    out = BarsResult(sym, ex, interval)
    minutes = INTRADAY_MINUTES[interval]
    if not stored.minutes:
        reason = CONNECT_FOR_INTRADAY if rest is None else NOT_STORED
        out.gaps.append(await _queue(sym, interval, Gap(stored.start, stored.end, COLD, reason)))
        return out
    last_end = _utc(stored.minutes[-1].timestamp) + MINUTE
    first_start = _utc(stored.minutes[0].timestamp)
    if first_start > stored.start + timedelta(days=OLDER_GAP_SLACK_DAYS):
        out.gaps.append(await _queue(sym, interval, Gap(stored.start, first_start, OLDER, "Older intraday history is not stored yet.")))
    want_end = min(last_closed_end(now, minutes, holidays, ex), stored.end)
    newest: list[OHLCVBar] = []
    source = "lake:intraday"
    if last_end < want_end:
        after = max(last_end, now - timedelta(days=TOPUP_MAX_DAYS))
        if after > last_end:
            out.gaps.append(await _queue(sym, interval, Gap(last_end, after, OLDER, "Part of the recent history is not stored yet.")))
        if rest is None:
            out.gaps.append(Gap(last_end, want_end, NEWEST, CONNECT_FOR_INTRADAY))
        else:
            name = f"{CACHE_PREFIX}:{rest.record_id}:{ex}:{sym}:{after.isoformat()}:{want_end.isoformat()}"
            cached = await _cached(name)
            if cached is not None:
                newest = cached
            else:
                adapter = budgeted if budgeted is not None else paced_adapter(rest)
                before = int(getattr(adapter, "taken", 0))
                try:
                    newest = await _fetch_newest(adapter, sym, ex, after, now, holidays)
                    ttl = int((next_close_after(want_end, minutes, holidays, ex) - now).total_seconds())
                    await _keep(name, newest, max(1, min(CACHE_MAX_SECONDS, ttl)))
                except api_budget.BudgetUsed:
                    out.gaps.append(Gap(last_end, want_end, NEWEST, NEWEST_BUDGET))
                except Exception as exc:  # noqa: BLE001 - the stored bars still serve; the newest are a gap
                    logger.info("newest bars not read for %s:%s: %s", ex, sym, exc)
                    out.gaps.append(Gap(last_end, want_end, NEWEST, "The newest bars could not be read from your broker."))
                out.rest_calls = int(getattr(adapter, "taken", 0)) - before
            if newest:
                source = f"lake:intraday+broker:{rest.broker}"
    merged = {_utc(b.timestamp): b for b in stored.minutes}
    for b in newest:
        merged.setdefault(_utc(b.timestamp), b)          # the store's own bar wins where both have one
    out.frame = build_interval([merged[t] for t in sorted(merged)], interval, now, ex)
    out.stored_bars, out.topped_up_bars = len(stored.minutes), len(newest)
    out.source = source if out.served else ""
    return out


async def read_bars(session: AsyncSession, symbol: str, interval: str, start: datetime, end: datetime, *, user: Any = None,
                    purpose: str = "feature", exchange: str = "NSE", rest: RestSource | None = None,
                    now: datetime | None = None, dh3: bool = True) -> BarsResult:
    """One (symbol, interval) store-first read over [start, end]: the own store, then only the newest missing bars by
    REST through `rest` (the reader's own session; Daily / Weekly never), never the full history. `purpose` labels the
    read in the log (screener / backtest / chart ...). `dh3`: the closing-auction flags applied on read (True, the
    charts' and the screener's mode) or the rows as stored (False, the engine's mode)."""
    name = canonical(interval)
    if name in (DAILY, WEEKLY):
        got = await read_eod(session, symbol, exchange, name, start, end, now=now)
    else:
        got = await read_intraday(session, symbol, exchange, name, start, end, user=user, rest=rest, now=now, dh3=dh3)
    logger.debug("market_store %s %s:%s %s -> %s (%d bars, %d REST)", purpose, exchange, symbol, name, got.source or "cold",
                 len(got.frame), got.rest_calls)
    return got


async def read_many(session: AsyncSession, symbols: Sequence[str], interval: str, start: datetime, end: datetime, *,
                    user: Any = None, purpose: str = "scan", exchange: str = "NSE", rest: RestSource | None = None,
                    now: datetime | None = None, release: Any = None, dh3: bool = True) -> dict[str, BarsResult]:
    """The batch read for a scan: every symbol's store bars first (database only, one calendar read), then `release`
    (an async callable, e.g. LD-7's `release_connection`) before the REST top-ups, which share ONE paced adapter (one
    budget allowance for the run) - at most one top-up per symbol, none on a store hit."""
    name = canonical(interval)
    now = _utc(now or datetime.now(UTC))
    out: dict[str, BarsResult] = {}
    hol = await holidays_for(session, exchange)
    syms = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))
    if name in (DAILY, WEEKLY):
        for s in syms:
            out[s] = await read_eod(session, s, exchange, name, start, end, now=now, holidays=hol)
        if release is not None:
            await release()
        return out
    stored = {s: await store_intraday(session, s, exchange, name, start, end, user=user, now=now, dh3=dh3) for s in syms}
    if release is not None:
        await release()
    budgeted = None if rest is None else paced_adapter(rest)
    for s in syms:
        out[s] = await top_up(stored[s], rest=rest, budgeted=budgeted, now=now, holidays=hol)
    logger.debug("market_store %s %s x%d -> %d REST", purpose, name, len(syms), sum(r.rest_calls for r in out.values()))
    return out


def window_for(interval: str, lookback_days: int, now: datetime | None = None) -> tuple[datetime, datetime]:
    """[now - lookback_days, now] - the window a feature's lookback asks for."""
    end = _utc(now or datetime.now(UTC))
    return end - timedelta(days=max(1, int(lookback_days))), end


__all__ = ["read_bars", "read_many", "read_eod", "read_intraday", "store_intraday", "top_up", "own_rows", "may_top_up",
           "intraday_key", "eod_served", "build_interval", "paced_adapter", "last_closed_day", "last_closed_end", "next_close_after", "window_for",
           "reset_cache", "TOPUP_MAX_DAYS"]

