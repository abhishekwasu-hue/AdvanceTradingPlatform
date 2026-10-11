"""The market-zones job (Z7a-2): per followed instrument, read the shared lake bars that CLOSED by the run time, build
the snapshot (detection + lifecycle + level memory against the previous snapshot) and store it as-of.

Flag `market_zones` (off by default). Which instruments: MARKET_ZONES_SYMBOLS (platform, when the flag is on for
everyone) plus each tenant's followed instruments (market_zone_settings.enabled, when the flag is on for that tenant).
A tenant whose resolved settings equal the platform's shares the platform snapshot; otherwise it gets its own scope.
Inputs are the lake's shared candles only (md_candles - no tenant column; HB0's tenant-private md_tenant_candles is never
read), point-in-time (`ingested_at <= run time`), so a replayed run sees what was known then.

Bars: the lake labels a bar by its END; the zone functions take START-labelled frames (Trade's convention) plus a
`close_at` column. A timeframe the lake does not hold is rebuilt in memory from the instrument's 1-minute bars (read
once per instrument), bucketed from its exchange's session open (app.market_data.calendar); a bucket counts only once
its end has passed the run time, or once its last minute is the exchange's regular close (the short 15:15-15:30 hour on
NSE; MCX runs to 23:30) - so a higher timeframe never carries a bar still forming. Bars are read as plain tuples (no ORM
objects), at most `max_bars_per_frame` per frame and at most MAX_DAYS_BY_TF days per timeframe (a rebuild from 1-minute
bars at most REBUILD_MAX_DAYS), whatever a tenant sets.

Never on the trading loop: the worker starts `run_nightly` as its own task (own sessions, one at a time, cancelled on
stop) once per IST day from MARKET_ZONES_HOUR_IST (default 20, after HB1's 19:00 EOD ingest). The CPU part (snapshot)
runs in a thread. A run has a time budget (MARKET_ZONES_BUDGET_SECONDS) and is resumable: what is already stored for the
same as_of is skipped, and the day counts as done only when the whole plan has been worked through. A Postgres advisory
lock keeps a second replica out. `run_instrument(session, key, now=...)` is the bar-close entry point for a later
trigger: the same code with the bar's close time.
"""
import asyncio
import logging
import time as clock
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import Float, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import MarketZoneRunRecord, MdCandleRecord
from app.market_data.calendar import EXCHANGE_SESSIONS, IST, ExchangeSession, session_family
from app.price_action.zones.settings import ZoneSettings
from app.price_action.zones.snapshot import build_snapshot, family_of, is_line
from app.price_action.zones.store import (PLATFORM_SCOPE, followed, latest_run, normalise_key, platform_settings, rows_of, run_exists,
                                          settings_for, settings_hash, tenant_scope, write_run)

logger = logging.getLogger(__name__)

FLAG = "market_zones"
LOCK_KEY = 0x5A374102                                   # pg advisory lock id ("Z7A" 02)
INTRADAY_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
LAKE_LABELS = {"1m": ("1m",), "3m": ("3m",), "5m": ("5m",), "15m": ("15m",), "30m": ("30m",), "1h": ("60m", "1h"), "4h": ("240m", "4h"),
               "1d": ("1d", "day"), "1w": ("1w", "week")}
# The most history any setting may make the job read per timeframe (a tenant can set history_days up to 3650).
MAX_DAYS_BY_TF = {"1m": 30, "3m": 45, "5m": 90, "15m": 365, "30m": 365, "1h": 730, "4h": 1825, "1d": 3650, "1w": 3650}
FRAME_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "close_at"]
REBUILD_MAX_DAYS = 180                                  # 1-minute history read to rebuild a timeframe the lake does not hold
FETCH_PARTITION = 5000                                  # rows per streamed partition (the loop is handed back between them)


def exchange_session(key: str) -> ExchangeSession:
    """The regular session of the instrument's exchange family ("NSE:X" -> NSE, "MCX:X" -> MCX, crypto venues -> 24x7)."""
    return EXCHANGE_SESSIONS[session_family(key.split(":", 1)[0])]


def needed_days(s: ZoneSettings) -> Dict[str, Tuple[int, Optional[int]]]:
    """{timeframe: (days to read, recent days for dynamic S/R or None)} for every frame the settings use, each bounded by
    MAX_DAYS_BY_TF."""
    out: Dict[str, Tuple[int, Optional[int]]] = {}
    for tf in {s.sr_timeframe, s.zone_timeframe, s.gap_timeframe}:
        out[tf] = (s.history_days, None)
    for tf in s.dynamic_sr_timeframes:
        recent = int(s.recent_days_by_tf.get(tf, 20))
        days, _ = out.get(tf, (0, None))
        out[tf] = (max(days, recent), recent)
    return {tf: (min(days, MAX_DAYS_BY_TF.get(tf, 3650)), None if recent is None else min(recent, MAX_DAYS_BY_TF.get(tf, 3650)))
            for tf, (days, recent) in out.items()}


def bars_to_frame(df: pd.DataFrame, timeframe: str, sess: ExchangeSession) -> pd.DataFrame:
    """END-labelled bars (columns ts / open / high / low / close / volume) -> a START-labelled UTC frame with `close_at`."""
    if df is None or not len(df):
        return pd.DataFrame(columns=FRAME_COLUMNS)
    end = pd.to_datetime(df["ts"], utc=True)
    if timeframe in INTRADAY_MINUTES:
        start = end - pd.Timedelta(minutes=INTRADAY_MINUTES[timeframe])
    elif timeframe == "1d":                                  # a daily bar starts at that session's open
        local = end.dt.tz_convert(IST)
        start = (local.dt.normalize() + pd.Timedelta(hours=sess.open.hour, minutes=sess.open.minute)).dt.tz_convert("UTC")
    else:
        start = end - pd.Timedelta(days=7)
    return pd.DataFrame({"timestamp": start.to_numpy(), "open": df["open"].astype(float).to_numpy(), "high": df["high"].astype(float).to_numpy(),
                         "low": df["low"].astype(float).to_numpy(), "close": df["close"].astype(float).to_numpy(),
                         "volume": df["volume"].fillna(0).astype("int64").to_numpy(), "close_at": end.to_numpy()}).assign(
        timestamp=lambda d: pd.to_datetime(d["timestamp"], utc=True), close_at=lambda d: pd.to_datetime(d["close_at"], utc=True))


def resample_closed(df1m: pd.DataFrame, minutes: int, as_of: Any, sess: ExchangeSession) -> pd.DataFrame:
    """1-minute START-labelled bars -> `minutes` bars bucketed from the exchange's session open. A bucket counts once its
    end is <= as_of, or once its last minute ends at the exchange's regular close (a bucket cut short by the close). It
    closes at its end, or - cut short - at that last minute. A special session's short last bucket (Muhurat) counts once
    its end has passed."""
    if df1m is None or not len(df1m):
        return pd.DataFrame(columns=FRAME_COLUMNS)
    offset = (sess.open.hour * 60 + sess.open.minute) % minutes
    local = df1m.assign(timestamp=pd.to_datetime(df1m["timestamp"], utc=True).dt.tz_convert(IST),
                        close_at=pd.to_datetime(df1m["close_at"], utc=True).dt.tz_convert(IST)).set_index("timestamp")
    agg = local.resample(f"{minutes}min", origin="start_day", offset=pd.Timedelta(minutes=offset), label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "close_at": "max"}).dropna(subset=["open"])
    agg = agg.reset_index()
    as_of_ts = pd.Timestamp(as_of)
    as_of_ts = as_of_ts.tz_localize("UTC") if as_of_ts.tzinfo is None else as_of_ts
    bucket_end = agg["timestamp"] + pd.Timedelta(minutes=minutes)
    at_close = agg["close_at"].map(lambda t: not sess.always_open and t.time() == sess.close) & (bucket_end > agg["close_at"])
    keep = (bucket_end <= as_of_ts) | at_close
    agg["close_at"] = agg["close_at"].where(at_close, bucket_end)
    out = agg[keep].copy()
    out["timestamp"] = out["timestamp"].dt.tz_convert("UTC")
    out["close_at"] = pd.to_datetime(out["close_at"], utc=True)
    return out[FRAME_COLUMNS].reset_index(drop=True)


RowTuple = Tuple[Any, ...]


async def _fetch(session: AsyncSession, key: str, label: str, start: datetime, end: datetime) -> List[RowTuple]:
    """Raw bars with END in (start, end] known at `end` (ingested_at <= end): plain tuples, prices cast to float in SQL,
    streamed in partitions so the event loop is handed back between them. Nothing is built on the loop."""
    query = select(MdCandleRecord.ts, MdCandleRecord.version, cast(MdCandleRecord.open, Float), cast(MdCandleRecord.high, Float),
                   cast(MdCandleRecord.low, Float), cast(MdCandleRecord.close, Float), MdCandleRecord.volume).where(
        MdCandleRecord.instrument_key == key, MdCandleRecord.timeframe == label, MdCandleRecord.ts > start, MdCandleRecord.ts <= end,
        MdCandleRecord.ingested_at <= end).order_by(MdCandleRecord.ts, MdCandleRecord.version).execution_options(yield_per=FETCH_PARTITION)
    rows: List[RowTuple] = []
    result = await session.stream(query)
    async for part in result.partitions(FETCH_PARTITION):
        rows.extend(part)
        await asyncio.sleep(0)
    return rows


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_US = timedelta(microseconds=1)


def _epoch_ns(t: datetime) -> int:
    return ((t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)) - _EPOCH) // _US * 1000


def raw_frame(rows: List[RowTuple], timeframe: str, sess: ExchangeSession) -> pd.DataFrame:
    """Raw rows -> per END the highest version -> a START-labelled frame (runs in the worker thread). Columns are built
    with plain Python loops and vectorised conversions - no long C call over all rows that would keep the GIL from the
    event loop's thread (a DataFrame built from a list of tuples does exactly that)."""
    if not rows:
        return pd.DataFrame(columns=FRAME_COLUMNS)
    ns = np.array([_epoch_ns(r[0]) for r in rows], dtype="int64")
    df = pd.DataFrame({"ts": pd.to_datetime(ns, utc=True), "version": np.array([r[1] for r in rows], dtype="int64"),
                       "open": np.array([r[2] for r in rows], dtype=float), "high": np.array([r[3] for r in rows], dtype=float),
                       "low": np.array([r[4] for r in rows], dtype=float), "close": np.array([r[5] for r in rows], dtype=float),
                       "volume": np.array([r[6] or 0 for r in rows], dtype="int64")})
    return bars_to_frame(df.drop_duplicates("ts", keep="last").reset_index(drop=True), timeframe, sess)


@dataclass
class Frames:
    frames: Dict[str, pd.DataFrame] = field(default_factory=dict)        # history frames
    dyn: Dict[str, pd.DataFrame] = field(default_factory=dict)           # recent frames for dynamic S/R
    counts: Dict[str, int] = field(default_factory=dict)                 # bars read per timeframe


def warmup_days(timeframe: str, bars: int, sess: ExchangeSession) -> int:
    """Calendar days that hold about `bars` bars of a timeframe (weekends included) - the extra history read before the
    window so a zone near its start has its average range and structural swing look-back inside the frame."""
    if bars <= 0:
        return 0
    if timeframe in INTRADAY_MINUTES:
        per_day = max(1, ((sess.close.hour * 60 + sess.close.minute) - (sess.open.hour * 60 + sess.open.minute)) // INTRADAY_MINUTES[timeframe])
        return int(bars / per_day * 7 / 5) + 4
    if timeframe == "1w":
        return bars * 7 + 7
    return int(bars * 7 / 5) + 6


def _plan_reads(s: ZoneSettings, sess: ExchangeSession, now: datetime, extend_to: Optional[Dict[str, datetime]]) -> Dict[str, datetime]:
    starts: Dict[str, datetime] = {}
    history = {s.sr_timeframe, s.zone_timeframe, s.gap_timeframe}
    for tf, (days, _) in needed_days(s).items():
        cap = MAX_DAYS_BY_TF.get(tf, 3650)
        if tf in history:
            days = days + warmup_days(tf, s.warmup_bars, sess)
        start = now - timedelta(days=min(days, cap))
        if extend_to and extend_to.get(tf) is not None:
            start = max(min(start, extend_to[tf] - timedelta(days=1)), now - timedelta(days=cap))
        starts[tf] = start
    return starts


def assemble(native: Dict[str, Tuple[str, List[RowTuple]]], m1: List[RowTuple], starts: Dict[str, datetime], s: ZoneSettings,
             sess: ExchangeSession, now: datetime, extend_to: Optional[Dict[str, datetime]]) -> Frames:
    """Raw rows -> the frames (native timeframes, timeframes rebuilt from the 1-minute rows) - all of the DataFrame work,
    in one call on the worker thread."""
    plan = needed_days(s)
    raw: Dict[str, pd.DataFrame] = {tf: raw_frame(rows, tf, sess) for tf, (_, rows) in native.items()}
    if m1:
        m1f = raw_frame(m1, "1m", sess)
        for tf in plan:
            if tf in raw or tf not in INTRADAY_MINUTES or tf == "1m":
                continue
            df = resample_closed(m1f[m1f["close_at"] > pd.Timestamp(starts[tf])], INTRADAY_MINUTES[tf], now, sess)
            if len(df):
                raw[tf] = df
    out = Frames()
    for tf, df in raw.items():
        df = df.tail(s.max_bars_per_frame).reset_index(drop=True)
        out.counts[tf] = len(df)
        if tf in (s.sr_timeframe, s.zone_timeframe, s.gap_timeframe):
            out.frames[tf] = df
        recent = plan[tf][1]
        if recent is not None:
            since = pd.Timestamp(now).tz_convert("UTC") - pd.Timedelta(days=recent)
            if extend_to and extend_to.get(tf) is not None:
                since = min(since, pd.Timestamp(extend_to[tf]).tz_convert("UTC") - pd.Timedelta(days=1))
            out.dyn[tf] = df[df["close_at"] > since].reset_index(drop=True)
    return out


async def load_frames(session: AsyncSession, key: str, s: ZoneSettings, now: datetime,
                      extend_to: Optional[Dict[str, datetime]] = None) -> Frames:
    """Every frame the settings use, bars closed by `now` only (history frames with `warmup_bars` extra bars before the
    window). extend_to {tf: time}: read back at least to that time (a remembered level's tracker resumes after it - its
    bars must be there), still within MAX_DAYS_BY_TF. The reads stream on the loop; every DataFrame is built in a thread."""
    sess = exchange_session(key)
    starts = _plan_reads(s, sess, now, extend_to)
    native: Dict[str, Tuple[str, List[RowTuple]]] = {}
    missing: List[str] = []
    for tf in sorted(starts):
        for label in LAKE_LABELS.get(tf, (tf,)):
            rows = await _fetch(session, key, label, starts[tf], now)
            if rows:
                native[tf] = (label, rows)
                break
        else:
            if tf in INTRADAY_MINUTES and tf != "1m":
                missing.append(tf)
    m1: List[RowTuple] = []
    if missing:                                              # one 1-minute read for every rebuilt timeframe
        start = max(min(starts[tf] for tf in missing), now - timedelta(days=REBUILD_MAX_DAYS))
        m1 = native["1m"][1] if "1m" in native and starts["1m"] <= start else await _fetch(session, key, "1m", start, now)
    return await asyncio.to_thread(assemble, native, m1, starts, s, sess, now, extend_to)


async def source_probe(session: AsyncSession, key: str, s: ZoneSettings, now: datetime) -> Tuple[Optional[datetime], Optional[datetime]]:
    """(newest bar END, newest ingest) the run would read - cheap aggregates. Equal to what a stored run read means the
    run would come out the same, so a resumed pass skips it without loading anything."""
    sess = exchange_session(key)
    starts = _plan_reads(s, sess, now, None)
    labels = {label for tf in starts for label in LAKE_LABELS.get(tf, (tf,))}
    if any(tf in INTRADAY_MINUTES for tf in starts):
        labels.add("1m")
    row = (await session.execute(select(func.max(MdCandleRecord.ts), func.max(MdCandleRecord.ingested_at)).where(
        MdCandleRecord.instrument_key == key, MdCandleRecord.timeframe.in_(sorted(labels)), MdCandleRecord.ts > min(starts.values()),
        MdCandleRecord.ts <= now, MdCandleRecord.ingested_at <= now))).one()
    return (_as_utc(row[0]) if row[0] is not None else None, _as_utc(row[1]) if row[1] is not None else None)


def _last_close(frames: List[pd.DataFrame]) -> Tuple[Optional[datetime], Optional[float]]:
    live = [f for f in frames if f is not None and len(f)]
    if not live:
        return None, None
    last = max(live, key=lambda f: pd.Timestamp(f["close_at"].iloc[-1]))
    return pd.Timestamp(last["close_at"].iloc[-1]).to_pydatetime(), float(last["close"].iloc[-1])


def _resume_points(previous: List[Dict[str, Any]], s: ZoneSettings) -> Dict[str, datetime]:
    """{timeframe: the earliest last bar a remembered live line level saw} - the read must reach back to it."""
    out: Dict[str, datetime] = {}
    for r in previous:
        if not is_line(str(r["zone_type"])) or r.get("state") == "invalidated" or family_of(str(r["zone_type"])) not in s.memory_families:
            continue
        seen = (r.get("tracker") or {}).get("last_bar_at")
        if seen:
            t = pd.Timestamp(seen)
            t = t.tz_localize(IST) if t.tzinfo is None else t
            t = t.tz_convert("UTC").to_pydatetime()
            tf = str(r["timeframe"])
            out[tf] = min(out.get(tf, t), t)
    return out


@dataclass
class InstrumentResult:
    status: str                                  # stored / exists / no_data
    run: Optional[MarketZoneRunRecord] = None


async def run_instrument(session: AsyncSession, key: str, *, settings: Optional[ZoneSettings] = None, scope: str = PLATFORM_SCOPE,
                         tenant_id: Optional[int] = None, now: Optional[datetime] = None) -> InstrumentResult:
    """Build and store one snapshot as of `now` (default: the current time; timezone-aware). no_data when the lake has no
    bars for it; exists when the snapshot for that as_of is already stored (idempotent). The caller commits."""
    s = settings if settings is not None else platform_settings()
    key = normalise_key(key)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("run_instrument: `now` must be timezone-aware")
    probe = await source_probe(session, key, s, now)
    if probe[0] is None:
        return InstrumentResult("no_data")
    prev_run = await latest_run(session, scope, key, now)
    if prev_run is not None and prev_run.settings_hash == settings_hash(s) and \
            (_utc_or_none(prev_run.source_last_ts), _utc_or_none(prev_run.source_ingested_at)) == probe:
        return InstrumentResult("exists")                # nothing new in the lake since that run: same snapshot, not reloaded
    previous = await rows_of(session, prev_run)
    loaded = await load_frames(session, key, s, now, extend_to=_resume_points(previous, s))
    as_of, price = _last_close([*loaded.frames.values(), *loaded.dyn.values()])
    if as_of is None:
        return InstrumentResult("no_data")
    if await run_exists(session, scope, key, as_of):
        return InstrumentResult("exists")
    if prev_run is not None and _as_utc(prev_run.as_of) > as_of:       # a later snapshot exists (replay) - merge with the one before
        prev_run = await latest_run(session, scope, key, as_of, strictly_before=True)
        previous = await rows_of(session, prev_run)
    rows = await asyncio.to_thread(build_snapshot, loaded.frames, loaded.dyn, s, previous)
    run = await write_run(session, scope=scope, tenant_id=tenant_id, key=key, as_of=as_of, settings=s, rows=rows, price=price,
                          bars=loaded.counts, previous_run_id=None if prev_run is None else prev_run.id, now=now, source=probe)
    return InstrumentResult("stored", run)


def _as_utc(t: datetime) -> datetime:
    return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


def _utc_or_none(t: Optional[datetime]) -> Optional[datetime]:
    return None if t is None else _as_utc(t)


async def plan(session: AsyncSession) -> Dict[Tuple[str, str], Tuple[Optional[int], ZoneSettings]]:
    """{(scope, instrument_key): (tenant_id, settings)} - what a run would compute, deduplicated across tenants."""
    from app.platform.controls import flag_enabled
    jobs: Dict[Tuple[str, str], Tuple[Optional[int], ZoneSettings]] = {}
    plat = platform_settings()
    plat_hash = settings_hash(plat)
    if await flag_enabled(session, FLAG):
        for raw in config.MARKET_ZONES_SYMBOLS:
            try:
                jobs[(PLATFORM_SCOPE, normalise_key(raw))] = (None, plat)
            except ValueError:
                logger.warning("MARKET_ZONES_SYMBOLS: %r is not an instrument key - skipped", raw)
    for tenant_id, key in await followed(session):
        if not await flag_enabled(session, FLAG, tenant_id):
            continue
        s = await settings_for(session, tenant_id, key)
        if settings_hash(s) == plat_hash:
            jobs.setdefault((PLATFORM_SCOPE, key), (None, plat))
        else:
            jobs[(tenant_scope(tenant_id), key)] = (tenant_id, s)
    return jobs


async def any_enabled(session: AsyncSession) -> bool:
    """The flag is on for everyone or for at least one tenant (asked every cycle - turning it on tonight still runs)."""
    from app.platform.controls import feature_flags
    entry = (await feature_flags(session)).get(FLAG) or {}
    return bool(entry.get("on") or entry.get("tenants"))


async def _try_lock(session: AsyncSession) -> bool:
    """A transaction-scoped advisory lock on the lock session: released by its rollback at the end - or by the database
    when the connection goes away - so a failed unlock can never leave it held."""
    if session.get_bind().dialect.name != "postgresql":
        return True
    return bool(await session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_KEY}))


async def run_nightly(session_factory: Any, now: Optional[datetime] = None, budget_seconds: Optional[float] = None) -> Dict[str, Any]:
    """One pass over the plan, each instrument in its own session, until done or out of time. `complete` is True only when
    every planned instrument was worked through (stored, already there, no data or failed) - the caller marks the day
    done only then; a later pass resumes (stored snapshots are skipped)."""
    now = now or datetime.now(timezone.utc)
    budget = config.MARKET_ZONES_BUDGET_SECONDS if budget_seconds is None else budget_seconds
    deadline = clock.monotonic() + budget
    report: Dict[str, Any] = {"runs": 0, "unchanged": 0, "no_data": 0, "failed": 0, "pending": 0, "complete": False, "locked": False}
    async with session_factory() as lock_session:
        if not await _try_lock(lock_session):
            report["locked"] = True
            logger.info("Market zones: another replica holds the job lock - skipped")
            return report
        try:
            async with session_factory() as session:
                jobs = sorted((await plan(session)).items())
            no_data: List[str] = []
            for i, ((scope, key), (tenant_id, s)) in enumerate(jobs):
                if clock.monotonic() > deadline:
                    report["pending"] = len(jobs) - i
                    logger.warning("Market zones: time budget (%ss) used - %s instruments left for the next pass", budget, report["pending"])
                    return report
                async with session_factory() as session:
                    try:
                        res = await run_instrument(session, key, settings=s, scope=scope, tenant_id=tenant_id, now=now)
                        await session.commit()
                    except Exception:  # noqa: BLE001 - one instrument's problem never stops the others
                        logger.exception("Market zones: %s %s failed", scope, key)
                        await session.rollback()
                        report["failed"] += 1
                        continue
                report[{"stored": "runs", "exists": "unchanged", "no_data": "no_data"}[res.status]] += 1
                if res.status == "no_data":
                    no_data.append(f"{scope} {key}")
            if no_data:
                logger.warning("Market zones: no bars in the shared lake for %s instrument(s): %s", len(no_data), ", ".join(no_data[:50]))
            report["complete"] = True
            logger.info("Market zones: %s", report)
            return report
        finally:
            await lock_session.rollback()                    # ends the lock's transaction: the lock is released


EOD_WAIT_HOURS = 3                                       # after this long past MARKET_ZONES_HOUR_IST, run without HB1's day


async def eod_ready(session: AsyncSession, day: date, now: datetime) -> bool:
    """Has HB1 finished the day's EOD ingest? True when the `history_backfill` flag is off (HB1 does not feed the lake),
    when the day's cash file has reached a final state (done / no_file / blocked / refused / failed), or - so a stuck
    archive never costs the night - once EOD_WAIT_HOURS have passed since MARKET_ZONES_HOUR_IST."""
    from app.db.models import HistoryBackfillFileRecord
    from app.market_lake import bhavcopy_backfill
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, bhavcopy_backfill.FLAG):
        return True
    if now.astimezone(IST).hour >= config.MARKET_ZONES_HOUR_IST + EOD_WAIT_HOURS:
        logger.warning("Market zones: HB1 has not finished %s - running on what the lake holds", day)
        return True
    status = await session.scalar(select(HistoryBackfillFileRecord.status).where(
        HistoryBackfillFileRecord.segment == "cm", HistoryBackfillFileRecord.trade_date == day))
    return status is not None and status not in ("pending", "running")


def nightly_due(now: datetime, last_day: Optional[date]) -> Optional[date]:
    """The IST date to run for, or None (before MARKET_ZONES_HOUR_IST, or that day is already done)."""
    local = now.astimezone(IST)
    if local.hour < config.MARKET_ZONES_HOUR_IST or last_day == local.date():
        return None
    return local.date()
