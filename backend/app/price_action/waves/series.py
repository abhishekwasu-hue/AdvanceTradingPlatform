"""The wave count as a per-bar series (W1 WaveState / WaveType / WaveAlt / WaveClarity), causal and incremental.

* Swings: the causal swing engine (`app/price_action/causal_swings.py`, the price-action settings) at the wave degree
  d (1-3, review finding WB12), at d - 1 for the inner structure and at d + 1 for the larger-degree context. A swing is
  known only from the bar that confirmed it.
* Incremental (R3): the count is recomputed only on a bar that confirmed a new degree-d swing. Every other bar carries
  the last count forward, so a value never changes between two confirmations, and a later change shows from its own
  bar on - an earlier bar's value is never rewritten (S8: no repainting).
* Causal: the count on bar t reads only the bars up to t (the swings confirmed by t, the running end up to t), so it
  equals the count computed on the data cut at t (the truncation tests).

Degree 2 and 3 swings depend on how much history was fetched (ZigZag pivots are path-dependent, review finding WB12):
those counts carry the "fetch-dependent" note of the swing functions.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from app.price_action import causal_swings as cs
from app.price_action.waves import counter as wc
from app.price_action.waves.settings import settings as wave_settings

WAVE_DEGREES: Tuple[int, ...] = (1, 2, 3)        # degree 0 has no lower degree to read a wave's inner structure


@dataclass
class WaveSeries:
    """The count on every bar of a frame; `counts` holds each recompute (on a newly confirmed degree-d swing)."""
    index: pd.Index
    state: np.ndarray                    # object: a STATES value, None before the first count
    type: np.ndarray
    alt: np.ndarray                      # the alternate's label, None when there is none
    clarity: np.ndarray
    counts: List[wc.WaveCount] = field(default_factory=list)

    @property
    def event_bars(self) -> List[int]:
        return [c.bar for c in self.counts]

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame({"state": self.state, "type": self.type, "alt": self.alt, "clarity": self.clarity}, index=self.index)


def _pivot_frame(bars: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame({k: bars[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
    frame["timestamp"] = frame["bar_end"] = bars.index
    return frame


def _swings(frame: pd.DataFrame, degree: int, pa: Mapping[str, Any]) -> List[wc.Swing]:
    if not 0 <= degree < len(pa["swing_atr_mult"]):
        return []
    return [wc.Swing(p.kind, float(p.price), int(p.bar_idx), int(p.confirmed_idx))
            for p in cs.degree_pivots(frame, degree, dict(pa)) if p.confirmed_idx is not None]


def _running_end(last: wc.Swing, high: np.ndarray, low: np.ndarray, t: int) -> wc.Swing:
    """The extreme after the last confirmed swing, from the bars up to t (equal prices: the later bar), read at t."""
    a = last.bar + 1
    if last.kind == "H":
        seg = low[a:t + 1]
        j = a + len(seg) - 1 - int(np.argmin(seg[::-1]))
        return wc.Swing("L", float(low[j]), j, t)
    seg = high[a:t + 1]
    j = a + len(seg) - 1 - int(np.argmax(seg[::-1]))
    return wc.Swing("H", float(high[j]), j, t)


def wave_series(bars: pd.DataFrame, degree: int = 1, settings: Optional[Mapping[str, Any]] = None,
                pa: Optional[Mapping[str, Any]] = None) -> WaveSeries:
    """The count on every bar of `bars` (closed bars, open / high / low / close) at swing degree `degree` (1-3), with the
    wave settings (default: today's values) and the price-action settings (default: `pa_settings.settings()`)."""
    if degree not in WAVE_DEGREES:
        raise ValueError(f"wave degree {degree} is not one of {', '.join(map(str, WAVE_DEGREES))}")
    from app.price_action import pa_settings
    s = wave_settings(settings)
    p = dict(pa) if pa is not None else pa_settings.settings()
    size = len(bars)
    state = np.full(size, None, dtype=object)
    typ, alt, clarity = state.copy(), state.copy(), state.copy()
    out = WaveSeries(bars.index, state, typ, alt, clarity)
    if size < 3:
        return out
    frame = _pivot_frame(bars)
    high, low = frame["high"].to_numpy(float), frame["low"].to_numpy(float)
    main, inner, outer = _swings(frame, degree, p), _swings(frame, degree - 1, p), _swings(frame, degree + 1, p)
    last_at: Dict[int, int] = {}                       # confirm bar -> the last degree-d swing confirmed on it
    for k, sw in enumerate(main):
        last_at[sw.confirm_bar] = k
    outer_bars = [o.confirm_bar for o in outer]
    for t in sorted(last_at):
        k = last_at[t]
        if t <= main[k].bar:
            continue
        end = _running_end(main[k], high, low, t)
        known_outer = int(np.searchsorted(outer_bars, t, side="right"))
        context = 0 if known_outer == 0 else (1 if outer[known_outer - 1].kind == "L" else -1)
        c = wc.count(main[max(0, k - wc.MAX_WAVES + 1):k + 1], end, inner, context, s)
        out.counts.append(c)
        state[t:], typ[t:], alt[t:], clarity[t:] = c.state, c.type, c.alt_state, c.clarity
    return out


# One count per (frame content, degree): a screen that reads WaveState, WaveType, WaveAlt and WaveClarity on the same frame
# computes it once. Keyed by the frame's bytes, so a different frame (another symbol, a cut, a changed bar) never shares.
_CACHE: "OrderedDict[Tuple[str, int, str], WaveSeries]" = OrderedDict()
_CACHE_SIZE = 64


def _fingerprint(bars: pd.DataFrame) -> str:
    h = hashlib.sha256()
    h.update(np.asarray(bars.index.asi8 if isinstance(bars.index, pd.DatetimeIndex) else np.arange(len(bars))).tobytes())
    for k in ("high", "low", "close", "open"):
        h.update(bars[k].to_numpy(dtype=float).tobytes())
    return h.hexdigest()


def cached_wave_series(bars: pd.DataFrame, degree: int, settings: Optional[Mapping[str, Any]] = None) -> WaveSeries:
    """`wave_series` with the default price-action settings, memoised by the frame's content."""
    from app.price_action import pa_settings
    key = (_fingerprint(bars), int(degree), repr(sorted(wave_settings(settings).items())) + repr(pa_settings.settings()))
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit
    value = wave_series(bars, degree, settings)
    _CACHE[key] = value
    if len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return value


__all__ = ["WaveSeries", "wave_series", "cached_wave_series", "WAVE_DEGREES"]
