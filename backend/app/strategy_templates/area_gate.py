"""M55 T5: the ported level gate as a causal SERIES over one frame of closed bars - the template path of "Pullback to a
demand / supply area" (strategy template A1). No new rule: every decision is the ported code, called as the scan Then
runner calls it (app/screener/then/runner.py `evaluate_template`):

* trend side - the Daily trend fold (`daily_trend.fold`) over the Daily candles known at the previous day's close, then
  `gate.context_from_state` (UP: demand areas only, DOWN: supply areas only, RANGE: both edges, otherwise none); fewer
  than `daily_trend.context.MIN_DAILY_BARS` Daily candles -> no entries (Trade's `trend_context`).
* areas - the Z7a market zones (`zones.snapshot.build_snapshot`) of the bars that had closed before the day's first bar,
  the live ones (not invalidated), each distinct zone its own area (`then.areas.area_of`, strongest first, at most
  `then.areas.MAX_AREAS`); areas are never merged.
* visit, trigger, invalidation - `gate.safe_evaluate` per area on the day's closed bars, exactly as the runner calls it
  (the other areas are the targets of its room check). An entry exists only on the commitment candle itself; long and
  short on one candle -> neither (the runner's conflict rule); several areas on one side -> the runner's `_pick`.
  The invalidation is the gate's own level beyond the area's far edge (`sl`).

Causal: the value at bar t reads bars <= t only (the gate is replayed on the closed bars up to t; the areas and the
trend are those known before the day began). Caches are keyed by a hash of exactly the bars each result reads, so a
later bar never changes an earlier answer and a cache hit equals a recomputation (tests/test_m55_t5_pullback_area.py).

Differences from the scan Then runner (data, not rules - listed in the PR): the Daily candles are built from the frame
given (not 10 years of end-of-day files), the zones are rebuilt from the frame (no remembered levels across nights, and a
zone family on a timeframe finer than the signal timeframe is not available), and the method / break numbers the runner
keeps at Trade's defaults are settings here (same defaults). Pure: no I/O, no orders, no broker.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.price_action.level_gate import gate as G
from app.price_action.level_gate.settings import BREAK_DEFAULTS, GATE_DEFAULTS, METHOD_DEFAULTS, gate_settings

IST = "Asia/Kolkata"
# The decision3 keys the gate's functions read with a candle time; the gate calls them without one (they are inert in the
# gate), and a clock window is not a structure rule (CLAUDE.md section 5) - so they are not settings.
METHOD_INERT = ("entry_start", "entry_end")
# Gate keys that are not signal rules: lots per grade (sizing is the user's) and the trend read's retry clock (operations).
GATE_NOT_SIGNAL = ("grade_b_lot_mult", "trend_retry_min")
GATE_KEYS = tuple(k for k in GATE_DEFAULTS if k not in GATE_NOT_SIGNAL)
METHOD_KEYS = tuple(k for k in METHOD_DEFAULTS if k not in METHOD_INERT)
BREAK_KEYS = tuple(BREAK_DEFAULTS)
SETTING_KEYS = GATE_KEYS + METHOD_KEYS + BREAK_KEYS
SIGNAL_TFS = ("5m", "15m")              # the runner's confirmation timeframes (then.templates.CONFIRM_TFS)
ZONE_TFS = ("5m", "15m", "30m", "1h")   # the zone frames that can be built from a 5m / 15m frame
_CACHE_SIZE = 4096
_DAY_CACHE: OrderedDict[tuple[Any, ...], Any] = OrderedDict()
CACHE_ON = True                         # tests turn it off to check the uncached computation


@dataclass(frozen=True)
class GateSeries:
    """Per bar: a long / short entry on that candle, and its invalidation level (NaN elsewhere)."""
    index: pd.Index
    long: np.ndarray
    short: np.ndarray
    long_stop: np.ndarray
    short_stop: np.ndarray


def clear_cache() -> None:
    _DAY_CACHE.clear()


def _cached(key: tuple[Any, ...], make: Any) -> Any:
    if not CACHE_ON:
        return make()
    if key in _DAY_CACHE:
        _DAY_CACHE.move_to_end(key)
        return _DAY_CACHE[key]
    value = _DAY_CACHE[key] = make()
    while len(_DAY_CACHE) > _CACHE_SIZE:
        _DAY_CACHE.popitem(last=False)
    return value


def split_settings(over: Mapping[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The user's settings -> (gate, method, break) dicts: the ported defaults, each known key replaced, cast to the
    default's type (`gate_settings` for the gate's own keys)."""
    over = {k: v for k, v in (over or {}).items() if v is not None}
    gs = gate_settings({k: v for k, v in over.items() if k in GATE_KEYS})

    def merged(defaults: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
        out = dict(defaults)
        for k in keys:
            if k in over:
                d = defaults[k]
                out[k] = bool(over[k]) if isinstance(d, bool) else (str(over[k]) if isinstance(d, str) else type(d)(over[k]))
        return out
    return gs, merged(METHOD_DEFAULTS, METHOD_KEYS), merged(BREAK_DEFAULTS, BREAK_KEYS)


def _wall(index: pd.Index) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(index)
    return idx.tz_convert(IST).tz_localize(None) if idx.tz is not None else idx


def _zone_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["open", "high", "low", "close", "volume"]].copy() if "volume" in df.columns else \
        df[["open", "high", "low", "close"]].assign(volume=0.0)
    out.insert(0, "timestamp", _wall(df.index))
    return out.reset_index(drop=True)


def _day_hashes(bars: pd.DataFrame, starts: list[int]) -> list[str]:
    """Chained hashes: h[k] covers every bar before day k's first bar (h[len(starts)] covers all bars)."""
    cols = np.ascontiguousarray(bars[["open", "high", "low", "close", "volume"]].to_numpy(float))
    stamps = np.ascontiguousarray(pd.DatetimeIndex(bars["timestamp"]).asi8)
    out, h = [], hashlib.sha1(b"m55t5", usedforsecurity=False)
    bounds = list(starts) + [len(bars)]
    prev = 0
    for b in bounds:
        h.update(stamps[prev:b].tobytes())
        h.update(cols[prev:b].tobytes())
        out.append(h.copy().hexdigest())
        prev = b
    return out


def _contexts(bars: pd.DataFrame, days: list[pd.Timestamp], gs: Mapping[str, Any]) -> dict[pd.Timestamp, dict[str, Any]]:
    """Each day's trend context: the Daily fold's last state known before the day (Trade `trend_context`)."""
    from app.price_action.daily_trend import fold as F
    from app.price_action.daily_trend.context import MIN_DAILY_BARS
    g = bars.groupby(bars["timestamp"].dt.normalize())
    daily = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last()})
    d = F.prepare(daily.rename_axis("timestamp").reset_index())
    states = F.fold(d) if len(d) else []
    stamps = d["timestamp"].to_numpy()
    out: dict[pd.Timestamp, dict[str, Any]] = {}
    for day in days:
        k = int(np.searchsorted(stamps, np.datetime64(day), side="left"))       # Daily candles BEFORE the day
        if k < MIN_DAILY_BARS:
            out[day] = {"ok": False, "trend": "UNKNOWN", "sides": set(), "reason": f"{G.R_NO_TREND} (Daily data too short: {k} bars)"}
        else:
            out[day] = G.context_from_state(states[k - 1], gs, asof=str(pd.Timestamp(stamps[k - 1]).date()))
    return out


def _zone_frames(f: pd.DataFrame, tf: str) -> dict[str, pd.DataFrame]:
    from app.screener import nodes as n
    from app.screener.runtime import resample
    out = {tf: _zone_frame(f)}
    for other in ZONE_TFS:
        if n.TF_MINUTES[other] > n.TF_MINUTES[tf]:
            out[other] = _zone_frame(resample(f, tf, other))
    return out


def _areas(frames: Mapping[str, pd.DataFrame], as_of: pd.Timestamp) -> list[tuple[float, float]]:
    """The live zones as of `as_of` (bars closed by then), as distinct areas, strongest first (`then.areas.areas_for`)."""
    from app.price_action.zones.chart import zone_strength
    from app.price_action.zones.compute import cut_as_of
    from app.price_action.zones.settings import ZoneSettings
    from app.price_action.zones.snapshot import build_snapshot, live_rows
    from app.screener.then.areas import MAX_AREAS, area_of
    s = ZoneSettings()
    long_tfs = {s.sr_timeframe, s.zone_timeframe, s.gap_timeframe}
    cut: dict[str, pd.DataFrame] = {}
    dyn: dict[str, pd.DataFrame] = {}
    for tf, df in frames.items():
        c = cut_as_of(df, as_of, tf)
        if c is None or not len(c):
            continue
        ts = pd.to_datetime(c["timestamp"])
        if tf in long_tfs:
            cut[tf] = c[(ts > as_of - pd.Timedelta(days=s.history_days)).to_numpy()].tail(s.max_bars_per_frame).reset_index(drop=True)
        if tf in s.dynamic_sr_timeframes:
            recent = int(s.recent_days_by_tf.get(tf, 20))
            dyn[tf] = c[(ts > as_of - pd.Timedelta(days=recent)).to_numpy()].reset_index(drop=True)
    if not cut and not dyn:
        return []
    snapshot: list[Mapping[str, Any]] = list(build_snapshot(cut, dyn, s))   # live_rows reads rows, never writes
    rows = sorted(live_rows(snapshot), key=lambda r: (-zone_strength(r), str(r.get("zone_key"))))
    out: list[tuple[float, float]] = []
    for row in rows:
        area = area_of(row["zone_low"], row["zone_high"])
        if area is not None and area not in out:
            out.append(area)
        if len(out) >= MAX_AREAS:
            break
    return out


def _margin(gs: Mapping[str, Any], s3: Mapping[str, Any], es: Mapping[str, Any]) -> int:
    """Bars before the day's first bar handed to the gate: enough for every look-back it makes (median ranges, the
    fallback windows, the candle averages), so the gate reads the same values as on the whole history."""
    return 3 * max(int(gs["mr_n"]), int(es["median_range_n"]), 2 * int(s3["power_fallback_bars"]), int(s3["candle_avg_n"]),
                   int(gs["approach_bars"])) + 10


def _day_entries(bars: pd.DataFrame, s0: int, e: int, areas: list[tuple[float, float]], ctx: Mapping[str, Any],
                 gs: Mapping[str, Any], s3: Mapping[str, Any], es: Mapping[str, Any]) -> list[tuple[int, int, float]]:
    """(bar, +1 / -1, invalidation) for every entry candle of one day - the runner's per-bar loop, replayed on the closed
    bars up to each bar. A bar is evaluated for an area only when the gate could enter there (the close beyond the area's
    inner edge on a side the trend allows, and a visit within the armed window); every other bar cannot be an entry."""
    from app.screener.then.runner import _pick
    if not ctx.get("ok") or not areas:
        return []
    cut = max(0, s0 - _margin(gs, s3, es))
    sub = bars.iloc[cut:e].reset_index(drop=True)
    hi_a, lo_a, c_a = sub["high"].to_numpy(float), sub["low"].to_numpy(float), sub["close"].to_numpy(float)
    start = s0 - cut
    reach = int(gs["max_armed_bars"]) + 1
    allowed = {(side, a): G.side_allowed(ctx, side, a) for a in areas for side in (G.BULL, G.BEAR)}
    out: list[tuple[int, int, float]] = []
    for t in range(start, len(sub)):
        entries: dict[str, list[tuple[tuple[float, float], dict[str, Any]]]] = {}
        w0 = max(start, t - reach)
        for area in areas:
            lo, hi = area
            side = G.BULL if c_a[t] > hi else (G.BEAR if c_a[t] < lo else None)
            if side is None or not allowed[(side, area)]:
                continue
            if not bool(np.any((lo_a[w0:t + 1] <= hi) & (hi_a[w0:t + 1] >= lo))):
                continue
            others = [a for a in areas if a != area]
            r = G.safe_evaluate(sub.iloc[:t + 1], area, ctx, others, start, gs, s3, es)
            if r.get("entry"):
                entries.setdefault(str(r.get("side")), []).append((area, r))
        if len(entries) != 1:
            continue                                                       # none, or long and short on one candle
        side, found = next(iter(entries.items()))
        _, r = _pick(found)
        out.append((cut + t, 1 if side == G.BULL else -1, float(r["sl"])))
    return out


def _settings_key(gs: Mapping[str, Any], s3: Mapping[str, Any], es: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(sorted((k, repr(v)) for d in (gs, s3, es) for k, v in d.items()))


def _ctx_key(ctx: Mapping[str, Any]) -> str:
    return repr(sorted((k, sorted(v) if isinstance(v, set) else v) for k, v in ctx.items()))


def gate_series(f: pd.DataFrame, tf: str, over: Mapping[str, Any] | None = None) -> GateSeries:
    """The gate's entries on every bar of `f` (closed bars, index = bar start, columns open / high / low / close[,
    volume]) on the signal timeframe `tf`, with the user's settings `over` (the ported defaults otherwise)."""
    n_bars = len(f)
    long_, short_ = np.zeros(n_bars, bool), np.zeros(n_bars, bool)
    lstop, sstop = np.full(n_bars, np.nan), np.full(n_bars, np.nan)
    if tf not in SIGNAL_TFS:
        raise ValueError(f"the level gate runs on the {' / '.join(SIGNAL_TFS)} chart, not {tf}")
    if n_bars == 0:
        return GateSeries(f.index, long_, short_, lstop, sstop)
    gs, s3, es = split_settings(over)
    bars = _zone_frame(f)
    days_of = bars["timestamp"].dt.normalize()
    change = np.r_[True, days_of.to_numpy()[1:] != days_of.to_numpy()[:-1]]
    starts = [int(i) for i in np.nonzero(change)[0]]
    days = [pd.Timestamp(days_of.iloc[i]) for i in starts]
    hashes = _day_hashes(bars, starts)
    contexts = _contexts(bars, days, gs)
    frames: dict[str, pd.DataFrame] | None = None
    skey = _settings_key(gs, s3, es)
    for k, (s0, day) in enumerate(zip(starts, days)):
        e = starts[k + 1] if k + 1 < len(starts) else n_bars
        ctx = contexts[day]
        if not ctx.get("ok"):
            continue
        as_of = pd.Timestamp(bars["timestamp"].iloc[s0])

        def make_areas() -> list[tuple[float, float]]:
            nonlocal frames
            if frames is None:
                frames = _zone_frames(f, tf)
            return _areas(frames, as_of)
        areas = _cached(("areas", tf, hashes[k], str(as_of)), make_areas)
        found = _cached(("day", tf, hashes[k + 1], s0, e, tuple(areas), _ctx_key(ctx), skey),
                        lambda: _day_entries(bars, s0, e, areas, ctx, gs, s3, es))
        for t, d, stop in found:
            if d > 0:
                long_[t], lstop[t] = True, stop
            else:
                short_[t], sstop[t] = True, stop
    return GateSeries(f.index, long_, short_, lstop, sstop)


__all__ = ["GateSeries", "gate_series", "split_settings", "clear_cache", "SETTING_KEYS", "GATE_KEYS", "METHOD_KEYS", "BREAK_KEYS",
           "SIGNAL_TFS", "METHOD_INERT", "GATE_NOT_SIGNAL"]
