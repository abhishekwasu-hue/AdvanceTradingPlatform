"""N4 CH3: the price-action chart layer - swings, legs, real breaks, swing levels and the Dow trend per degree, computed
from the shared lake bars as of a time.

GET /api/charts/layers/price-action?instrument=NSE:BANKNIFTY&tf=15m&at=<ISO time>&start=<ISO time>&degree=1&debug=false
  * As of `at` (timezone-aware; default now, a future time reads as now): only bars whose END is <= `at` are read
    (closed bars; nothing from a later bar), so the layer at bar N never changes when bars after N are added or altered -
    replay recomputes the layer as of each replayed bar (docs/design/CHARTING.md "Replay shows no future data").
  * The window: bars ending in (`start`, `at`]. `start` defaults to `at` minus DEFAULT_DAYS[tf]; a replay passes the
    start of its first step, so every step reads the same history and only adds bars. At most MAX_DAYS[tf] days and
    MAX_BARS bars - a longer window is refused, never silently cut.
  * Provenance: only the shared-licensed lake sources (SHARED_SOURCES: NSE bhavcopy, vendor, the operator's history
    import), preferred in that order, then the highest version; never `stream:*` / `broker:*` rows recorded through one
    organisation's broker session. A correction (a later version) counts only once ingested by `at`; a bar's first
    version counts by its END, so imported history can be replayed. A rebuild from 1-minute bars reads at most
    REBUILD_MAX_DAYS, and every read is row-capped (refused with 422, never cut).
  * The same computation is kept CACHE_SECONDS (key, timeframe, window, as-of minute); at most 120 requests a minute
    per user (replay Play asks once a second).
  * Engine: app/price_action/causal_swings.py (confirmed pivots per degree: a pivot exists from the bar that confirmed
    it), app/price_action/breaks.py (real breaks, causal), default settings (app/price_action/pa_settings.py).
  * `degree` (0 smallest .. 3 largest; default DEFAULT_DEGREE) is what the chart shows by default - "significant levels
    only". `debug=true` returns every degree, the tentative (still moving) pivots, broken levels and the thresholds.
  * The trend per degree (HH + HL up, LH + LL down, otherwise range) is always returned: the top-down panel.
  * Every number the chart draws is in the response, with its text; `fingerprint` is the SHA-256 of the computed part
    (canonical JSON), the same bars and settings give the same bytes.
Behind the `chart_price_action` flag (off by default -> 503). Reference data from past prices: nothing here places or
proposes an order.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Float, case, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.rate_limit import user_rate_limit
from app.db.models import MdCandleRecord, User
from app.db.session import get_session
from app.platform.controls import require_flag
from app.price_action import causal_swings as cs
from app.price_action.breaks import first_real_break, median_range
from app.price_action.pa_settings import settings as pa_defaults

router = APIRouter(prefix="/api/charts/layers", tags=["charts"])
FLAG = "chart_price_action"
SCHEMA = "pa-layer/1"
NOTE = "Swings, legs, breaks and levels from past prices only - reference, not signals or recommendations."
NO_BARS = "no lake bars for this instrument and timeframe in the window"
TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "1d")
Timeframe = Literal["1m", "3m", "5m", "15m", "30m", "1h", "1d"]
LAKE_LABELS = {"1m": ("1m",), "3m": ("3m",), "5m": ("5m",), "15m": ("15m",), "30m": ("30m",), "1h": ("60m", "1h"), "1d": ("1d", "day")}
INTRADAY_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
# history read by default (about 800-1,100 bars of a regular NSE session) and the most a request may ask for
DEFAULT_DAYS = {"1m": 3, "3m": 7, "5m": 15, "15m": 45, "30m": 90, "1h": 180, "1d": 1095}
MAX_DAYS = {tf: days * 4 for tf, days in DEFAULT_DAYS.items()}
MAX_BARS = 5000
DEFAULT_DEGREE = 2                    # 6 x ATR on the default settings: the significant swings only
MAX_BREAK_PIVOTS = 40                 # the newest confirmed pivots per degree whose break is looked for
MAX_LEVELS_PER_SIDE = 3               # unbroken swing levels drawn above and below the last close (debug: all)
CONCEPTS = ("swings", "legs", "breaks", "levels", "trend")
FETCH_PARTITION = 5000
FRAME_COLS = ["timestamp", "bar_end", "open", "high", "low", "close", "volume"]
# Provenance (BT1b, data terms): only sources licensed for every organisation are read, in this preference order -
# NSE bhavcopy (HB1), a market-data vendor, the operator's history import into the shared lake (HB0). Never
# `stream:<broker>` / `broker:<name>` rows: those were recorded through one organisation's own broker session.
SHARED_SOURCES = ("nse_bhav:", "vendor:", "r2:")
REBUILD_MAX_DAYS = 180                # 1-minute history read to rebuild a timeframe (the market-zones job's cap)
ROWS_PER_BAR = 3                      # raw rows allowed per bar read (sources x versions) before the read is refused
MIN_TIME = datetime(1990, 1, 1, tzinfo=timezone.utc)
CACHE_SECONDS = 30.0
CACHE_SIZE = 128
rate_limited = user_rate_limit("chart_price_action", limit=120, window_seconds=60)   # replay Play asks once a second


class WindowTooLarge(ValueError):
    """A read that would exceed its row cap - refused, never cut."""


# ---------------------------------------------------------------------------------------------------------------------
# Pure part: closed bars -> the layer
# ---------------------------------------------------------------------------------------------------------------------
def _utc(t: Any) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _epoch(t: Any) -> int:
    return int(_utc(t).timestamp())


def _iso(t: Any) -> Optional[str]:
    return None if t is None else _utc(t).to_pydatetime().isoformat()


def price_text(x: float) -> str:
    return f"{x:,.2f}".replace(",", "")


def _num(x: float, digits: int = 6) -> float:
    return float(round(float(x), digits))


def _swing_label(kind: str, price: float, previous: Optional[float]) -> str:
    if previous is None:
        return kind
    if price > previous:
        return "HH" if kind == "H" else "HL"
    if price < previous:
        return "LH" if kind == "H" else "LL"
    return "EH" if kind == "H" else "EL"


def _trend(pivots: Sequence[cs.Pivot]) -> Tuple[str, str]:
    """Dow: the last two confirmed highs and lows - higher high + higher low = up, lower high + lower low = down,
    anything else = range; fewer than two of either = none."""
    highs = [p.price for p in pivots if p.kind == "H"][-2:]
    lows = [p.price for p in pivots if p.kind == "L"][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return "none", "fewer than two confirmed highs and lows"
    hh, hl = highs[1] > highs[0], lows[1] > lows[0]
    lh, ll = highs[1] < highs[0], lows[1] < lows[0]
    basis = f"{'HH' if hh else 'LH' if lh else 'EH'} {price_text(highs[1])} + {'HL' if hl else 'LL' if ll else 'EL'} {price_text(lows[1])}"
    if hh and hl:
        return "up", basis
    if lh and ll:
        return "down", basis
    return "range", basis


def _pivot_row(p: cs.Pivot, label: str) -> Dict[str, Any]:
    word = "swing high" if p.kind == "H" else "swing low"
    return {"degree": p.degree, "kind": p.kind, "label": label, "price": _num(p.price), "price_text": price_text(p.price),
            "time": _epoch(p.ts), "status": p.status,
            "confirmed_time": None if p.confirmed_at is None else _epoch(p.confirmed_at),
            "confirmed_at": _iso(p.confirmed_at),
            "text": f"{label} {price_text(p.price)}" if p.status == "confirmed" else f"{word} so far {price_text(p.price)} (not confirmed)"}


def _leg_row(a: cs.Pivot, b: cs.Pivot) -> Dict[str, Any]:
    change = b.price - a.price
    pct = change / a.price * 100.0 if a.price else 0.0
    bars = b.bar_idx - a.bar_idx
    return {"degree": b.degree, "direction": "up" if change > 0 else "down" if change < 0 else "flat", "status": b.status,
            "from": {"time": _epoch(a.ts), "price": _num(a.price)}, "to": {"time": _epoch(b.ts), "price": _num(b.price)},
            "change": _num(change), "change_pct": round(pct, 2), "bars": int(bars),
            "text": f"{'+' if change >= 0 else ''}{pct:.2f}% · {bars} bars"}


def compute_layer(frame: pd.DataFrame, tf: str, *, degree: int = DEFAULT_DEGREE, debug: bool = False,
                  settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Closed bars (timestamp = bar start, bar_end, OHLC; ascending, nothing after the as-of time) -> the layer content.
    A pure function of the bars it is given: the caller cuts them at `at`, so a later bar can never reach it."""
    s = settings or pa_defaults()
    levels_n = int(s["degree_levels"])
    if not 0 <= degree < levels_n:
        raise ValueError(f"degree must be 0..{levels_n - 1}")
    frame = frame.reset_index(drop=True)
    n = len(frame)
    out: Dict[str, Any] = {"schema": SCHEMA, "tf": tf, "bars": n, "degree": degree, "debug": debug,
                           "first_bar": _epoch(frame["timestamp"].iloc[0]) if n else None,
                           "last_bar": _epoch(frame["timestamp"].iloc[-1]) if n else None,
                           "as_of": _iso(frame["bar_end"].iloc[-1]) if n else None,
                           "last_close": _num(frame["close"].iloc[-1]) if n else None,
                           "swings": [], "legs": [], "breaks": [], "levels": [], "trend": []}
    if n == 0:
        return out
    shown = list(range(levels_n)) if debug else [degree]
    mr = median_range(frame, s["median_range_n"])
    last_close = float(frame["close"].iloc[-1])
    ts, be = frame["timestamp"].to_numpy(), frame["bar_end"].to_numpy()
    degrees_dbg: List[Dict[str, Any]] = []
    for d in range(levels_n):
        confirmed = cs.degree_pivots(frame, d, s, tf)
        tentative = cs.tentative_pivot(frame, confirmed, d, tf=tf)
        trend, basis = _trend(confirmed)
        out["trend"].append({"degree": d, "tf": tf, "trend": trend, "basis": basis, "confirmed": len(confirmed),
                             "text": f"D{d} {trend}" + ("" if trend == "none" else f" ({basis})")})
        if debug:
            thr = cs.threshold(frame, d, s) if s["swing_mode"] != "fractal" else None
            last_thr = None if thr is None or not np.isfinite(thr[-1]) else _num(thr[-1])
            degrees_dbg.append({"degree": d, "swing_mode": s["swing_mode"],
                                "setting": (s["swing_atr_mult"] if s["swing_mode"] == "atr" else s["swing_pct"] if s["swing_mode"] == "pct"
                                            else s["swing_fractal_r"])[d],
                                "threshold_last": last_thr, "confirmed": len(confirmed),
                                "tentative": None if tentative is None else _pivot_row(tentative, tentative.kind)})
        if d not in shown:
            continue
        last_price: Dict[str, Optional[float]] = {"H": None, "L": None}
        for p in confirmed:
            out["swings"].append(_pivot_row(p, _swing_label(p.kind, p.price, last_price[p.kind])))
            last_price[p.kind] = p.price
        for a, b in zip(confirmed, confirmed[1:]):
            out["legs"].append(_leg_row(a, b))
        if debug and tentative is not None:
            out["swings"].append(_pivot_row(tentative, _swing_label(tentative.kind, tentative.price, last_price[tentative.kind])))
            if confirmed:
                out["legs"].append(_leg_row(confirmed[-1], tentative))
        # real breaks of the newest confirmed swings (searched from the bar after the one that confirmed the swing)
        live: List[Dict[str, Any]] = []
        for p in confirmed[-MAX_BREAK_PIVOTS:]:
            side = "above" if p.kind == "H" else "below"
            start = (p.confirmed_idx if p.confirmed_idx is not None else p.bar_idx) + 1
            idx = first_real_break(frame, start, p.price, side, s, mr) if start < n else None
            word = "swing high" if p.kind == "H" else "swing low"
            if idx is not None:
                out["breaks"].append({"degree": d, "side": side, "level": _num(p.price), "level_text": price_text(p.price),
                                      "pivot_time": _epoch(p.ts), "time": _epoch(ts[idx]), "confirmed_time": _epoch(be[idx]),
                                      "close": _num(frame["close"].iloc[idx]),
                                      "text": f"break {side} {word} {price_text(p.price)}"})
            level = {"degree": d, "kind": "resistance" if p.kind == "H" else "support", "price": _num(p.price),
                     "price_text": price_text(p.price), "pivot_time": _epoch(p.ts), "status": "broken" if idx is not None else "live",
                     "broken_time": None if idx is None else _epoch(ts[idx]),
                     "text": f"{word} {price_text(p.price)} · D{d}" + (" (broken)" if idx is not None else "")}
            if idx is None:
                live.append(level)
            elif debug:
                out["levels"].append(level)
        if debug:
            out["levels"].extend(live)
        else:
            # significant only: the nearest live levels above and below the last close
            above = sorted((lv for lv in live if lv["price"] >= last_close), key=lambda lv: (lv["price"], -lv["pivot_time"]))[:MAX_LEVELS_PER_SIDE]
            below = sorted((lv for lv in live if lv["price"] < last_close), key=lambda lv: (-lv["price"], -lv["pivot_time"]))[:MAX_LEVELS_PER_SIDE]
            out["levels"].extend(above + below)
    out["levels"].sort(key=lambda lv: (-lv["price"], lv["degree"], lv["pivot_time"]))
    out["breaks"].sort(key=lambda b: (b["time"], b["degree"], b["pivot_time"]))
    if debug:
        out["debug_info"] = {"degrees": degrees_dbg, "median_range_last": None if not np.isfinite(mr[-1]) else _num(mr[-1]),
                             "settings": {k: s[k] for k in ("swing_mode", "swing_atr_mult", "swing_pct", "swing_fractal_r", "atr_len",
                                                             "median_range_n", "break_buffer_mr", "break_no_reclaim_bars",
                                                             "break_displacement_confirm", "strength_min", "break_close_loc")}}
    return out


def canonical(content: Dict[str, Any]) -> str:
    return json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(content: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical(content).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------------------------------
# Lake bars
# ---------------------------------------------------------------------------------------------------------------------
RowTuple = Tuple[Any, ...]


async def _fetch(session: AsyncSession, key: str, label: str, start: datetime, end: datetime, limit: int) -> List[RowTuple]:
    """Raw bars with END in (start, end] (the lake labels a bar by its end, so a bar still forming at `end` is never
    read), from the shared-licensed sources only, streamed so the event loop is handed back between partitions.
    Per END the preferred source wins, then its highest version - rows are ordered so the winner comes last (the
    conversion keeps the last row per END). A correction (version > 1) counts only once it was ingested by `end`
    (point-in-time); a bar's first version counts by its END, so history imported after the fact can be replayed.
    More than `limit` rows -> WindowTooLarge (refused, never cut)."""
    rank = case(*[(MdCandleRecord.source.like(f"{prefix}%"), i) for i, prefix in enumerate(SHARED_SOURCES)], else_=len(SHARED_SOURCES))
    query = select(MdCandleRecord.ts, MdCandleRecord.version, cast(MdCandleRecord.open, Float), cast(MdCandleRecord.high, Float),
                   cast(MdCandleRecord.low, Float), cast(MdCandleRecord.close, Float), MdCandleRecord.volume).where(
        MdCandleRecord.instrument_key == key, MdCandleRecord.timeframe == label, MdCandleRecord.ts > start, MdCandleRecord.ts <= end,
        or_(*[MdCandleRecord.source.like(f"{prefix}%") for prefix in SHARED_SOURCES]),
        or_(MdCandleRecord.version == 1, MdCandleRecord.ingested_at <= end)).order_by(
        MdCandleRecord.ts, rank.desc(), MdCandleRecord.version, MdCandleRecord.source).limit(limit + 1).execution_options(yield_per=FETCH_PARTITION)
    rows: List[RowTuple] = []
    result = await session.stream(query)
    async for part in result.partitions(FETCH_PARTITION):
        rows.extend(part)
        if len(rows) > limit:
            raise WindowTooLarge(f"the window holds more than {limit} lake rows on {label}; choose a later `start`")
        await asyncio.sleep(0)
    return rows


async def _has_rows(session: AsyncSession, key: str, label: str, start: datetime, end: datetime) -> bool:
    """Whether the window holds any shared-licensed row of `label` (one indexed lookup)."""
    return (await session.scalar(select(MdCandleRecord.ts).where(
        MdCandleRecord.instrument_key == key, MdCandleRecord.timeframe == label, MdCandleRecord.ts > start, MdCandleRecord.ts <= end,
        or_(*[MdCandleRecord.source.like(f"{prefix}%") for prefix in SHARED_SOURCES])).limit(1))) is not None


def _to_frame(rows: List[RowTuple], tf: str, key: str, end: datetime, rebuild: bool) -> pd.DataFrame:
    """Raw rows -> closed `tf` bars (timestamp = start, bar_end) - in the worker thread. Shares the market-zones job's
    lake conversions (highest version per bar, NSE-session buckets for a timeframe rebuilt from 1-minute bars)."""
    from app.price_action.zones import job
    sess = job.exchange_session(key)
    if rebuild:
        df = job.resample_closed(job.raw_frame(rows, "1m", sess), INTRADAY_MINUTES[tf], end, sess)
    else:
        df = job.raw_frame(rows, tf, sess)
    if not len(df):
        return pd.DataFrame(columns=FRAME_COLS)
    df = df.rename(columns={"close_at": "bar_end"})
    df = df[pd.to_datetime(df["bar_end"], utc=True) <= pd.Timestamp(end)]       # closed by `end`, whatever the source
    return df[["timestamp", "bar_end", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


async def load_frame(session: AsyncSession, key: str, tf: str, start: datetime, end: datetime,
                     max_bars: Optional[int] = None) -> Tuple[pd.DataFrame, Optional[str]]:
    """The lake's own `tf` bars, else `tf` rebuilt from its 1-minute bars (intraday). (frame, source label)."""
    for label in LAKE_LABELS[tf]:
        rows = await _fetch(session, key, label, start, end, (max_bars or MAX_BARS) * ROWS_PER_BAR)
        if rows:
            return await asyncio.to_thread(_to_frame, rows, tf, key, end, False), f"lake:{label}"
    if tf in INTRADAY_MINUTES and tf != "1m":
        if end - start > timedelta(days=REBUILD_MAX_DAYS) and await _has_rows(session, key, "1m", start, end):
            raise WindowTooLarge(f"{tf} is rebuilt from 1-minute bars for at most {REBUILD_MAX_DAYS} days; choose a later `start`")
        rows = await _fetch(session, key, "1m", start, end, (max_bars or MAX_BARS) * INTRADAY_MINUTES[tf] * ROWS_PER_BAR)
        if rows:
            return await asyncio.to_thread(_to_frame, rows, tf, key, end, True), "lake:1m"
    return pd.DataFrame(columns=FRAME_COLS), None


def build(frame: pd.DataFrame, tf: str, degree: int, debug: bool) -> Dict[str, Any]:
    content = compute_layer(frame, tf, degree=degree, debug=debug)
    content["fingerprint"] = fingerprint(content)
    return content


# ---------------------------------------------------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------------------------------------------------
def _aware(t: Optional[datetime], name: str) -> Optional[datetime]:
    if t is not None and t.tzinfo is None:
        raise HTTPException(status_code=422, detail=f"`{name}` must carry its time zone (for example 2026-10-09T15:30:00+05:30)")
    if t is not None and t < MIN_TIME:
        raise HTTPException(status_code=422, detail=f"`{name}` must be in 1990 or later")
    return None if t is None else t.astimezone(timezone.utc)


_CACHE: "OrderedDict[tuple, Tuple[float, Dict[str, Any]]]" = OrderedDict()


def _cached(key: tuple) -> Optional[Dict[str, Any]]:
    hit = _CACHE.get(key)
    if hit is None or hit[0] < time.monotonic():
        _CACHE.pop(key, None)
        return None
    _CACHE.move_to_end(key)
    return hit[1]


def _remember(key: tuple, content: Dict[str, Any]) -> None:
    _CACHE[key] = (time.monotonic() + CACHE_SECONDS, content)
    _CACHE.move_to_end(key)
    while len(_CACHE) > CACHE_SIZE:
        _CACHE.popitem(last=False)


def cache_clear() -> None:
    _CACHE.clear()


@router.get("/price-action", dependencies=[Depends(rate_limited)])
async def price_action_layer(instrument: str = Query(..., min_length=1, max_length=100), tf: Timeframe = "15m",
                             at: Optional[datetime] = None, start: Optional[datetime] = None,
                             degree: int = Query(default=DEFAULT_DEGREE, ge=0, le=3), debug: bool = False,
                             user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    from app.price_action.zones.store import normalise_key
    now = datetime.now(timezone.utc)
    at_utc, start_utc = _aware(at, "at"), _aware(start, "start")
    # a live read is as of the current minute (bars that ended by then), so repeated live reads share one computation
    when = now.replace(second=0, microsecond=0) if at_utc is None or at_utc > now else at_utc
    begin = start_utc if start_utc is not None else when - timedelta(days=DEFAULT_DAYS[tf])
    if begin >= when:
        raise HTTPException(status_code=422, detail="`start` must be before `at`")
    if when - begin > timedelta(days=MAX_DAYS[tf]):
        raise HTTPException(status_code=422, detail=f"the window may span at most {MAX_DAYS[tf]} days on {tf}; choose a later `start`")
    head = {"instrument": instrument, "at": _iso(when), "start": _iso(begin), "source": None, "concepts": list(CONCEPTS), "note": NOTE}
    try:
        key: Optional[str] = normalise_key(instrument)
    except ValueError:
        key = None
    if key is None or key == "*":
        # not an instrument the lake keys (an option contract, odd characters): the chart just has no layer
        return {**head, **build(pd.DataFrame(columns=["timestamp", "bar_end", "open", "high", "low", "close"]), tf, degree, debug), "reason": NO_BARS}
    cache_key = (key, tf, begin, when, degree, debug)
    hit = _cached(cache_key)
    if hit is None:
        try:
            frame, source = await load_frame(session, key, tf, begin, when)
        except WindowTooLarge as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if len(frame) > MAX_BARS:
            raise HTTPException(status_code=422, detail=f"the window holds {len(frame)} bars, more than {MAX_BARS}; choose a later `start`")
        content = await asyncio.to_thread(build, frame, tf, degree, debug)
        hit = {"source": source, **content, "reason": None if len(frame) else NO_BARS}
        _remember(cache_key, hit)
    return {**head, "instrument": key, **hit}


__all__ = ["router", "FLAG", "compute_layer", "build", "load_frame", "canonical", "fingerprint"]
