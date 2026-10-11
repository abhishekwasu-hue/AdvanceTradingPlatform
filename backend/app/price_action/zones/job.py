"""The market-zones job (Z7a-2): per followed instrument, read the shared lake bars that CLOSED by the run time, build
the snapshot (detection + lifecycle + level memory against the previous snapshot) and store it as-of.

Flag `market_zones` (off by default). Which instruments: MARKET_ZONES_SYMBOLS (platform, when the flag is on for
everyone) plus each tenant's followed instruments (market_zone_settings.enabled, when the flag is on for that tenant).
A tenant whose resolved settings equal the platform's shares the platform snapshot; otherwise it gets its own scope.
Inputs are the lake's shared candles only (md_candles - no tenant column, nothing private), read point-in-time
(`ingested_at <= run time`) so a replayed run sees what was known then.

Bars: the lake labels a bar by its END; the zone functions take START-labelled frames (Trade's convention) plus a
`close_at` column. A timeframe the lake does not hold is rebuilt from 1-minute bars, bucketed from the session open,
keeping only buckets that closed by the run time (a last bucket that ends after the run time is dropped unless the
session has closed) - so a higher timeframe never carries a later bar. The run's `as_of` is the close of the last bar used.

The worker calls `run_all` once per IST day after MARKET_ZONES_HOUR_IST (nightly). `run_instrument(session, key, now=...)`
is the bar-close entry point for a later trigger: the same code with the bar's close time.
"""
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import MarketZoneRunRecord
from app.market_lake.asof import candles_as_of
from app.price_action.zones.settings import ZoneSettings
from app.price_action.zones.snapshot import build_snapshot
from app.price_action.zones.store import (PLATFORM_SCOPE, followed, latest_run, normalise_key, platform_settings, rows_of, run_exists,
                                          settings_for, settings_hash, tenant_scope, write_run)

logger = logging.getLogger(__name__)

FLAG = "market_zones"
TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
LAKE_LABELS = {"1m": ("1m",), "3m": ("3m",), "5m": ("5m",), "15m": ("15m",), "30m": ("30m",), "1h": ("60m", "1h"), "4h": ("240m", "4h"),
               "1d": ("1d", "day"), "1w": ("1w", "week")}
FRAME_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "close_at"]


def _hm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def needed_days(s: ZoneSettings) -> Dict[str, Tuple[int, Optional[int]]]:
    """{timeframe: (days to read, recent days for dynamic S/R or None)} for every frame the settings use."""
    out: Dict[str, Tuple[int, Optional[int]]] = {}
    for tf in {s.sr_timeframe, s.zone_timeframe, s.gap_timeframe}:
        out[tf] = (s.history_days, None)
    for tf in s.dynamic_sr_timeframes:
        recent = int(s.recent_days_by_tf.get(tf, 20))
        days, _ = out.get(tf, (0, None))
        out[tf] = (max(days, recent), recent)
    return out


def bars_to_frame(rows: List[Any], timeframe: str, s: ZoneSettings) -> pd.DataFrame:
    """Lake rows (END-labelled) -> a START-labelled UTC frame with `close_at` (the bar's end)."""
    if not rows:
        return pd.DataFrame(columns=FRAME_COLUMNS)
    end = pd.to_datetime([r.ts for r in rows], utc=True)
    if timeframe in TF_MINUTES:
        start = end - pd.Timedelta(minutes=TF_MINUTES[timeframe])
    elif timeframe == "1d":                                  # a daily bar starts at that session's open
        local = end.tz_convert(s.session_timezone)
        open_t = _hm(s.session_open)
        start = (local.normalize() + pd.Timedelta(hours=open_t.hour, minutes=open_t.minute)).tz_convert("UTC")
    else:
        start = end - pd.Timedelta(days=7)
    return pd.DataFrame({"timestamp": start, "open": [float(r.open) for r in rows], "high": [float(r.high) for r in rows],
                         "low": [float(r.low) for r in rows], "close": [float(r.close) for r in rows],
                         "volume": [int(r.volume or 0) for r in rows], "close_at": end})


def resample_closed(df1m: pd.DataFrame, minutes: int, as_of: Any, s: ZoneSettings) -> pd.DataFrame:
    """1-minute START-labelled bars -> `minutes` bars bucketed from the session open, only buckets closed by `as_of`
    (a bucket cut short by the session close is complete once its last minute - the close - is in)."""
    if df1m is None or not len(df1m):
        return pd.DataFrame(columns=FRAME_COLUMNS)
    tz = s.session_timezone
    open_t, close_t = _hm(s.session_open), _hm(s.session_close)
    offset = (open_t.hour * 60 + open_t.minute) % minutes
    local = df1m.assign(timestamp=pd.to_datetime(df1m["timestamp"], utc=True).dt.tz_convert(tz),
                        close_at=pd.to_datetime(df1m["close_at"], utc=True).dt.tz_convert(tz)).set_index("timestamp")
    agg = local.resample(f"{minutes}min", origin="start_day", offset=pd.Timedelta(minutes=offset), label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "close_at": "max"}).dropna(subset=["open"])
    agg = agg.reset_index()
    as_of_ts = pd.Timestamp(as_of)
    as_of_ts = as_of_ts.tz_localize("UTC") if as_of_ts.tzinfo is None else as_of_ts
    bucket_end = agg["timestamp"] + pd.Timedelta(minutes=minutes)
    session_done = agg["close_at"].map(lambda t: t.time() >= close_t)
    keep = (bucket_end <= as_of_ts) | session_done
    # the bar closes at its bucket's end, or - cut short by the session close - with its last minute
    agg["close_at"] = agg["close_at"].where(session_done, bucket_end)
    out = agg[keep].copy()
    out["timestamp"] = out["timestamp"].dt.tz_convert("UTC")
    out["close_at"] = pd.to_datetime(out["close_at"], utc=True)
    return out[FRAME_COLUMNS].reset_index(drop=True)


async def load_frame(session: AsyncSession, key: str, timeframe: str, start: datetime, end: datetime, s: ZoneSettings) -> Optional[pd.DataFrame]:
    """The lake's bars of `timeframe` with END in (start, end], as known at `end`; rebuilt from 1-minute bars when the
    lake has none of that timeframe. None when there are no bars at all."""
    for label in LAKE_LABELS.get(timeframe, (timeframe,)):
        rows = await candles_as_of(session, key, label, start, end, as_of=end)
        if rows:
            return bars_to_frame(rows, timeframe, s)
    if timeframe in TF_MINUTES and timeframe != "1m":
        rows = await candles_as_of(session, key, "1m", start, end, as_of=end)
        if rows:
            df = resample_closed(bars_to_frame(rows, "1m", s), TF_MINUTES[timeframe], end, s)
            return df if len(df) else None
    return None


async def load_frames(session: AsyncSession, key: str, s: ZoneSettings, now: datetime) -> Tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame], Dict[str, int]]:
    """(history frames, recent frames for dynamic S/R, bars read per timeframe) - every bar closed by `now`."""
    frames: Dict[str, pd.DataFrame] = {}
    dyn: Dict[str, pd.DataFrame] = {}
    counts: Dict[str, int] = {}
    for tf, (days, recent) in sorted(needed_days(s).items()):
        df = await load_frame(session, key, tf, now - timedelta(days=days), now, s)
        if df is None or not len(df):
            continue
        counts[tf] = len(df)
        if tf in (s.sr_timeframe, s.zone_timeframe, s.gap_timeframe):
            frames[tf] = df
        if recent is not None:
            dyn[tf] = df[df["close_at"] > pd.Timestamp(now).tz_convert("UTC") - pd.Timedelta(days=recent)].reset_index(drop=True)
    return frames, dyn, counts


def _last_close(frames: List[pd.DataFrame]) -> Tuple[Optional[datetime], Optional[float]]:
    live = [f for f in frames if f is not None and len(f)]
    if not live:
        return None, None
    last = max(live, key=lambda f: pd.Timestamp(f["close_at"].iloc[-1]))
    return pd.Timestamp(last["close_at"].iloc[-1]).to_pydatetime(), float(last["close"].iloc[-1])


async def run_instrument(session: AsyncSession, key: str, *, settings: Optional[ZoneSettings] = None, scope: str = PLATFORM_SCOPE,
                         tenant_id: Optional[int] = None, now: Optional[datetime] = None) -> Optional[MarketZoneRunRecord]:
    """Build and store one snapshot as of `now` (default: the current time). Returns the run, or None when there are no
    bars or the snapshot for that as_of is already stored (idempotent). The caller commits."""
    s = settings if settings is not None else platform_settings()
    key = normalise_key(key)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    frames, dyn, counts = await load_frames(session, key, s, now)
    as_of, price = _last_close([*frames.values(), *dyn.values()])
    if as_of is None:
        return None
    if await run_exists(session, scope, key, as_of):
        return None
    prev_run = await latest_run(session, scope, key, as_of, strictly_before=True)
    rows = build_snapshot(frames, dyn, s, previous=await rows_of(session, prev_run))
    return await write_run(session, scope=scope, tenant_id=tenant_id, key=key, as_of=as_of, settings=s, rows=rows, price=price,
                           bars=counts, previous_run_id=None if prev_run is None else prev_run.id, now=now)


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


async def run_all(session: AsyncSession, now: Optional[datetime] = None) -> Dict[str, int]:
    """One pass over every planned instrument (flag-gated). A failure is logged and rolled back; the others still run."""
    now = now or datetime.now(timezone.utc)
    report = {"runs": 0, "unchanged": 0, "failed": 0}
    for (scope, key), (tenant_id, s) in sorted((await plan(session)).items()):
        try:
            run = await run_instrument(session, key, settings=s, scope=scope, tenant_id=tenant_id, now=now)
            await session.commit()
            report["runs" if run is not None else "unchanged"] += 1
        except Exception:  # noqa: BLE001 - one instrument's problem never stops the others
            logger.exception("Market zones: %s %s failed", scope, key)
            await session.rollback()
            report["failed"] += 1
    return report


def due(now: datetime, last_day: Any) -> bool:
    """Once per IST day, from MARKET_ZONES_HOUR_IST on."""
    from app.market_data.calendar import IST
    local = now.astimezone(IST)
    return last_day != local.date() and local.hour >= config.MARKET_ZONES_HOUR_IST
