"""Wave levels, ratios and anchored wave lines as per-bar series (W2 / W3 / W4; review findings WB4, WB5, WB6).

Built on the count of `series.py` (the main reading only - the alternate is evidence, never a level, WB8). Every value
on bar t reads the count known at t and the bars up to t: the confirmed wave ends of the main reading, the running end
(the extreme since the last confirmed swing, as known at t) and the lower-degree swings confirmed by t. A value is NaN
where it does not apply (no count, the wrong family, a wave not reached yet), so the engine treats these series as
sparse (`engine.SPARSE_SERIES`, WB4).

Levels (W2) - prices:

* `origin`: the start of the current impulse, or of wave A (of W in a double three).
* `end(wave)`: the end of a FINISHED wave of the main reading ("1".."4" in an impulse, "A".."D" in a correction);
  "current" is the running end as known at the bar. A wave still running has no end yet (NaN).
* `invalidation`: the hard-rule level of the main count, per type (WB5):
  - wave two -> the start of wave one; wave three -> the start of wave one until wave three has passed the end of wave
    one, then the end of wave two; wave four -> the end of wave one (impulse) or the end of wave two (diagonal: wave four
    may overlap wave one but never passes wave two's end); the fifth wave -> the end of wave four;
  - wave B of a zigzag -> the start of wave A. The zigzag's own limit is a ratio SETTING (the B/A ratio `zz_max_ba`,
    reachable as `retrace(zz_max_ba)`), not a hard level;
  - wave B of a flat (and of an expanded flat) -> none: a flat B may go beyond the start of A (NaN);
  - wave C (any type) -> the end of wave B; triangle wave D -> the end of wave B, wave E -> the end of wave C;
  - wave one and wave A -> none (nothing to be void against yet).
  "Wave three is never the shortest" is NOT an invalidation level: it caps the fifth wave on its winning side, so it is
  the separate `cap` (evidence; as an invalidation it would always be on the wrong side - S6 invalid geometry).
* `cap`: the fifth wave of an impulse whose wave three is shorter than wave one may not be longer than wave three: the
  end of wave four plus wave three's length in the impulse's direction. NaN elsewhere.
* `beyond_origin`: TRUE while a wave against the first wave (wave two / four, B / D) has gone beyond the origin, as
  known at the bar - by any amount. G7 arms on "FLAT with B beyond the start of A": the expanded flat itself is decided
  only once wave C has passed the end of wave A (`rules.flat`), so during B the type stays FLAT (WAVE-8).

Ratios (W3) - the thresholds are always the user's settings; this module holds no ratio of its own:

* `retrace(ratio)`: the price `ratio` of the previous (finished) wave back from its end.
* `projection(base, ratio)`: the running wave's start plus `ratio` x the base wave's length, in the running wave's
  direction (C = A x ratio from the end of B, wave three = wave one x ratio from the end of wave two).
* `ratio(base)`: the running wave's length so far / the base wave's length - the retracement (base "previous") or the
  extension (base "1" / "A" ...) as a value; a template compares it with its own settings.

Anchored wave lines (W4, WB6) - two anchors of the count's own pivots, projected to each bar (the straight-line math of
`price_action/trendlines.py`); no volume requirement, a flat side is just a line of no slope:

* `zero_two` / `one_three` / `two_four`: the line through the reading's points 0-2 (the 0-2 line of an impulse; the
  0-B base line of a zigzag channel), 1-3 (a triangle's A-C side, a diagonal's 1-3 line) and 2-4 (the 2-4 line of an
  impulse or a diagonal; a triangle's B-D side). Both anchors must be finished wave ends.
* `channel_<wave>` / `channel_current`: a wave's own line on its start side - from the wave's start through its
  lower-degree swings of the same kind (the lows of a rising wave, the highs of a falling one), the outermost one, so
  every inner swing confirmed by the bar lies on or beyond it. The channel of wave B is the line a C-direction break
  crosses (the corrective channel); it stays defined after the wave ended, while the count still holds the wave.

Each line value comes with its anchors: a break or a retest follows ONE line (the anchors it had when it broke).
Signals only: nothing here places, sizes or exits an order.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from app.price_action.daily_trend.rules import beyond as _beyond
from app.price_action.trendlines import Trendline
from app.price_action.waves import counter as wc
from app.price_action.waves.series import WAVE_DEGREES, WaveSeries, _fingerprint, wave_series
from app.price_action.waves.settings import settings as wave_settings

IMPULSE_WAVES: Tuple[str, ...] = ("1", "2", "3", "4", "5")
CORRECTION_WAVES: Tuple[str, ...] = ("A", "B", "C", "D", "E")
CURRENT = "current"
PREVIOUS = "previous"
# WaveEnd: a finished wave's end (the fifth wave / wave E is always the running one of its reading) or the running end
END_CHOICES: Tuple[str, ...] = IMPULSE_WAVES[:-1] + CORRECTION_WAVES[:-1] + (CURRENT,)
# The finished waves a retest can follow (a fixed level); the running end moves, so it is not one
FIXED_END_CHOICES: Tuple[str, ...] = IMPULSE_WAVES[:-1] + CORRECTION_WAVES[:-1]
# The base wave of a projection or a ratio
BASE_CHOICES: Tuple[str, ...] = (PREVIOUS,) + IMPULSE_WAVES[:-1] + CORRECTION_WAVES[:-1]
# WaveVolumeLower (the wave templates' volume companion): a wave of the reading (finished, or the running one; current = the
# running wave) against a finished base wave (previous = the wave before it)
VOLUME_WAVES: Tuple[str, ...] = IMPULSE_WAVES + CORRECTION_WAVES + (CURRENT,)
VOLUME_BASES: Tuple[str, ...] = (PREVIOUS,) + IMPULSE_WAVES[:-1] + CORRECTION_WAVES[:-1]
POINT_LINES: Dict[str, Tuple[int, int]] = {"zero_two": (0, 2), "one_three": (1, 3), "two_four": (2, 4)}
CHANNEL_PREFIX = "channel_"
CHANNEL_LINES: Tuple[str, ...] = tuple(CHANNEL_PREFIX + w for w in IMPULSE_WAVES + CORRECTION_WAVES) + (CHANNEL_PREFIX + CURRENT,)
LINES: Tuple[str, ...] = tuple(POINT_LINES) + CHANNEL_LINES

Anchor = Tuple[int, float, int, float]           # (bar, price, bar, price): two points of a line, in bar positions


def project(anchor: Anchor, bar: float) -> float:
    """The line through the anchors at `bar` (the trendlines' straight line, `Trendline.at`)."""
    b1, p1, b2, p2 = anchor
    slope = (p2 - p1) / (b2 - b1)
    return Trendline("support", slope, p1 - slope * b1, b1, b2, 2, 0, float("nan")).at(bar)


def level_anchor(bar: int, price: float) -> Anchor:
    """A fixed level as a line of no slope (a wave end a retest follows)."""
    return (bar, price, bar + 1, price)


def _wave_index(name: str, reading: wc.Reading) -> Optional[int]:
    """The 1-based position of a wave name in the reading's family (None for the other family)."""
    names = IMPULSE_WAVES if reading.family == "impulse" else CORRECTION_WAVES
    return names.index(name) + 1 if name in names else None


@dataclass
class WaveLevels:
    """The main reading and the running end on every bar, and the level / ratio / line series read from them."""
    index: pd.Index
    main: np.ndarray                      # object: the main Reading known at the bar, None without a count
    running: np.ndarray                   # the running end's price as known at the bar (NaN without a count)
    running_bar: np.ndarray               # its extreme bar (-1 without a count)
    inner: List[wc.Swing] = field(default_factory=list)
    _memo: Dict[Tuple[Any, ...], Any] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.index)

    def _each(self) -> Any:
        for t in range(self.size):
            r = self.main[t]
            if r is not None:
                yield t, r

    def _empty(self) -> np.ndarray:
        return np.full(self.size, np.nan)

    # --- W2 levels -------------------------------------------------------------------------------------------------------
    def origin(self) -> np.ndarray:
        out = self._empty()
        for t, r in self._each():
            out[t] = r.points[0]
        return out

    def end(self, wave: str) -> np.ndarray:
        if wave not in END_CHOICES:
            raise ValueError(f"wave {wave!r} is not one of {', '.join(END_CHOICES)}")
        out = self._empty()
        for t, r in self._each():
            if wave == CURRENT:
                out[t] = self.running[t]
                continue
            i = _wave_index(wave, r)
            if i is not None and i < len(r.points) - 1:
                out[t] = r.points[i]
        return out

    def end_line(self, wave: str) -> Tuple[np.ndarray, List[Optional[Anchor]]]:
        """A finished wave's end as a fixed level with its anchors (for a break and its retest)."""
        if wave not in FIXED_END_CHOICES:
            raise ValueError(f"wave {wave!r} is not one of {', '.join(FIXED_END_CHOICES)}")
        values = self._empty()
        anchors: List[Optional[Anchor]] = [None] * self.size
        for t, r in self._each():
            i = _wave_index(wave, r)
            if i is not None and i < len(r.points) - 1:
                values[t] = r.points[i]
                anchors[t] = level_anchor(r.bars[i], r.points[i])
        return values, anchors

    def invalidation(self) -> np.ndarray:
        out = self._empty()
        for t, r in self._each():
            out[t] = self._invalidation(r, float(self.running[t]))
        return out

    @staticmethod
    def _invalidation(r: wc.Reading, running: float) -> float:
        x, n, d = r.points, len(r.points) - 1, r.direction
        if r.family == "impulse":
            if n == 2:
                return x[0]
            if n == 3:
                return x[2] if _beyond(running, x[1], d) else x[0]
            if n == 4:
                return x[2] if r.type == wc.DIAGONAL else x[1]
            if n == 5:
                return x[4]
            return float("nan")
        if n == 2:
            return x[0] if r.type == wc.ZIGZAG else float("nan")      # a flat B has no hard level
        if n == 3:
            return x[2]
        if n == 4:
            return x[2]                                               # triangle D: the end of B
        if n == 5:
            return x[3]                                               # triangle E: the end of C
        return float("nan")

    def cap(self) -> np.ndarray:
        out = self._empty()
        for t, r in self._each():
            x = r.points
            if r.type == wc.IMPULSE and len(x) - 1 == len(IMPULSE_WAVES):
                one, three = abs(x[1] - x[0]), abs(x[3] - x[2])
                if three < one:
                    out[t] = x[4] + r.direction * three
        return out

    def beyond_origin(self) -> np.ndarray:
        out = np.zeros(self.size, dtype=bool)
        for t, r in self._each():
            n = len(r.points) - 1
            if n % 2 == 0 and _beyond(float(self.running[t]), r.points[0], -r.direction):
                out[t] = True
        return out

    # --- W3 ratios (the ratio is always the caller's setting) ----------------------------------------------------------
    def _base(self, r: wc.Reading, base: str) -> Optional[float]:
        """The length of a finished base wave of the reading (None when it is not finished or of the other family)."""
        n = len(r.points) - 1
        i = n - 1 if base == PREVIOUS else _wave_index(base, r)
        if i is None or not 1 <= i <= n - 1:
            return None
        return abs(r.points[i] - r.points[i - 1])

    @staticmethod
    def _running_direction(r: wc.Reading) -> int:
        n = len(r.points) - 1
        return r.direction if n % 2 == 1 else -r.direction

    def retrace(self, ratio: float) -> np.ndarray:
        out = self._empty()
        for t, r in self._each():
            x, n = r.points, len(r.points) - 1
            if n >= 2:
                out[t] = x[n - 1] - ratio * (x[n - 1] - x[n - 2])
        return out

    def projection(self, base: str, ratio: float) -> np.ndarray:
        if base not in BASE_CHOICES:
            raise ValueError(f"base wave {base!r} is not one of {', '.join(BASE_CHOICES)}")
        out = self._empty()
        for t, r in self._each():
            length = self._base(r, base)
            if length is not None:
                out[t] = r.points[-2] + self._running_direction(r) * ratio * length
        return out

    def ratio(self, base: str = PREVIOUS) -> np.ndarray:
        if base not in BASE_CHOICES:
            raise ValueError(f"base wave {base!r} is not one of {', '.join(BASE_CHOICES)}")
        out = self._empty()
        for t, r in self._each():
            length = self._base(r, base)
            if length:
                out[t] = abs(float(self.running[t]) - r.points[-2]) / length
        return out

    # --- the volume companion (a comparison of two waves' mean volume; no number) ---------------------------------------
    def volume_lower(self, volume: np.ndarray, wave: str, base: str = PREVIOUS) -> np.ndarray:
        """TRUE where the mean volume of `wave` - finished: its bars from the bar after its start to its end bar; the
        running wave: from the bar after its start to the bar BEFORE this one, so a trigger bar's own volume is never
        counted in the wave it may end - is below the mean volume of the finished `base` wave (`previous`: the wave before
        `wave`). FALSE where either wave is not in the reading (not reached, the other family, the base not finished), the
        running wave has no bar before this one yet, or a bar of either span has no volume (missing, or an index's zero
        volume: no average to compare). Reads only bars up to each bar."""
        if wave not in VOLUME_WAVES:
            raise ValueError(f"wave {wave!r} is not one of {', '.join(VOLUME_WAVES)}")
        if base not in VOLUME_BASES:
            raise ValueError(f"base wave {base!r} is not one of {', '.join(VOLUME_BASES)}")
        v = np.asarray(volume, dtype=float)
        bad = ~np.isfinite(v) | (v <= 0)
        csum = np.concatenate(([0], np.cumsum(np.where(bad, 0, v))))
        cbad = np.concatenate(([0], np.cumsum(bad.astype(np.int64))))

        def mean(lo: int, hi: int) -> Optional[float]:            # bars lo..hi, both included
            if hi < lo or cbad[hi + 1] - cbad[lo] > 0:
                return None
            return float((csum[hi + 1] - csum[lo]) / (hi - lo + 1))

        out = np.zeros(self.size, dtype=bool)
        for t, r in self._each():
            n = len(r.points) - 1
            i = n if wave == CURRENT else _wave_index(wave, r)
            if i is None or i > n:
                continue
            j = i - 1 if base == PREVIOUS else _wave_index(base, r)
            if j is None or not 1 <= j < n or j == i:
                continue
            end = t - 1 if i == n else int(r.bars[i])
            this, other = mean(int(r.bars[i - 1]) + 1, end), mean(int(r.bars[j - 1]) + 1, int(r.bars[j]))
            out[t] = this is not None and other is not None and this < other
        return out

    # --- W4 anchored wave lines ----------------------------------------------------------------------------------------
    def line(self, name: str) -> Tuple[np.ndarray, List[Optional[Anchor]]]:
        """The line's value on every bar and the anchors it had there (None where it is not defined)."""
        if name not in LINES:
            raise ValueError(f"wave line {name!r} is not one of {', '.join(LINES)}")
        key = ("line", name)
        if key not in self._memo:
            values = self._empty()
            anchors: List[Optional[Anchor]] = [None] * self.size
            for t, r in self._each():
                a = self._point_line(r, name) if name in POINT_LINES else self._channel(r, name, t)
                if a is not None:
                    anchors[t] = a
                    values[t] = project(a, t)
            self._memo[key] = (values, anchors)
        return self._memo[key]

    @staticmethod
    def _point_line(r: wc.Reading, name: str) -> Optional[Anchor]:
        i, j = POINT_LINES[name]
        if j > len(r.points) - 2 or r.bars[j] == r.bars[i]:
            return None                                                # both anchors must be finished wave ends
        return (r.bars[i], r.points[i], r.bars[j], r.points[j])

    def _channel(self, r: wc.Reading, name: str, t: int) -> Optional[Anchor]:
        n = len(r.points) - 1
        wave = name[len(CHANNEL_PREFIX):]
        i = n if wave == CURRENT else _wave_index(wave, r)
        if i is None or i > n:
            return None
        start_bar, start = r.bars[i - 1], r.points[i - 1]
        rising = (r.points[i] if i < n else float(self.running[t])) > start
        end_bar = r.bars[i] if i < n else int(self.running_bar[t])
        kind, side = ("L", 1) if rising else ("H", -1)
        best: Optional[Tuple[float, int, float]] = None
        lo = bisect_left(self._inner_bars, start_bar + 1)
        for s in self.inner[lo:]:
            if s.bar >= end_bar:
                break
            if s.kind != kind or s.confirm_bar > t:
                continue
            slope = side * (s.price - start) / (s.bar - start_bar)
            if best is None or slope <= best[0]:                       # the outermost swing (a tie: the later one)
                best = (slope, s.bar, s.price)
        if best is None:
            return None
        return (start_bar, start, best[1], best[2])

    @property
    def _inner_bars(self) -> List[int]:
        key = ("inner_bars",)
        if key not in self._memo:
            self._memo[key] = [s.bar for s in self.inner]
        return self._memo[key]


def levels_from(ws: WaveSeries, high: np.ndarray, low: np.ndarray) -> WaveLevels:
    """The per-bar main reading and running end from a wave series (and the bars' highs / lows it was read on)."""
    size = len(ws.index)
    main = np.full(size, None, dtype=object)
    running, running_bar = np.full(size, np.nan), np.full(size, -1, dtype=int)
    counts = ws.counts
    for k, c in enumerate(counts):
        t0, t1 = c.bar, (counts[k + 1].bar if k + 1 < len(counts) else size)
        r = c.main
        if r is None or t0 >= t1:
            continue
        main[t0:t1] = r
        last, up = r.bars[-2], r.points[-1] > r.points[-2]
        best, best_bar = float("nan"), -1
        for j in range(last + 1, t1):
            v = float(high[j] if up else low[j])
            if best_bar < 0 or (v >= best if up else v <= best):      # equal prices: the later bar (as series._running_end)
                best, best_bar = v, j
            if j >= t0:
                running[j], running_bar[j] = best, best_bar
    inner = sorted(ws.inner, key=lambda s: (s.bar, s.confirm_bar))
    return WaveLevels(ws.index, main, running, running_bar, inner)


def wave_levels(bars: pd.DataFrame, degree: int = 1, settings: Optional[Mapping[str, Any]] = None,
                pa: Optional[Mapping[str, Any]] = None) -> WaveLevels:
    """The wave levels of `bars` at swing degree `degree` (1-3) with the wave settings (default: today's values)."""
    ws = wave_series(bars, degree, settings, pa)
    return levels_from(ws, bars["high"].to_numpy(float), bars["low"].to_numpy(float))


_CACHE: "OrderedDict[Tuple[str, int, str], WaveLevels]" = OrderedDict()
_CACHE_SIZE = 64


def cached_wave_levels(bars: pd.DataFrame, degree: int, settings: Optional[Mapping[str, Any]] = None) -> WaveLevels:
    """`wave_levels` with the default price-action settings, memoised by the frame's content (one per frame and degree
    for every wave level, ratio and line function of a screen); the count itself comes from `cached_wave_series`."""
    from app.price_action import pa_settings
    from app.price_action.waves.series import cached_wave_series
    if degree not in WAVE_DEGREES:
        raise ValueError(f"wave degree {degree} is not one of {', '.join(map(str, WAVE_DEGREES))}")
    key = (_fingerprint(bars), int(degree), repr(sorted(wave_settings(settings).items())) + repr(pa_settings.settings()))
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit
    value = levels_from(cached_wave_series(bars, degree, settings), bars["high"].to_numpy(float), bars["low"].to_numpy(float))
    _CACHE[key] = value
    if len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return value


__all__ = ["WaveLevels", "wave_levels", "cached_wave_levels", "levels_from", "project", "level_anchor", "Anchor", "LINES", "POINT_LINES",
           "CHANNEL_LINES", "END_CHOICES", "FIXED_END_CHOICES", "BASE_CHOICES", "VOLUME_WAVES", "VOLUME_BASES", "IMPULSE_WAVES", "CORRECTION_WAVES", "CURRENT", "PREVIOUS"]
