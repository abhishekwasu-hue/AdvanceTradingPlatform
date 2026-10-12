"""U6 BK4: B9 retest entry (second chance) as a causal, bar-by-bar series (spec docs/specs/ATP_BREAKOUT_SCREENERS_ADDENDUM.md
B9; behind `screener_breakouts` AND `screener_breakouts_bk4`).

The breakout comes first: `retest_of` picks the fixed-level breakout it follows - range (B1), swing (B2), donchian (B10)
or trendline (B5) - with that screen's own parameters and its one-signal-per-leg rule. After a breakout at bar i with
level L:
  * price must leave the level first: a bar after i whose low is above L + `retest_tol_atr` ATR;
  * the retest is the first later bar j, within `retest_max_bars` of i, that comes back to the level and holds:
    with `retest_reversal` (default) a logical reversal at L ending on j (app/price_action/reversal.py's composite rule,
    the commitment forms ReversalAt and the pullback screens use; its candles never include the breakout bar), otherwise
    a low within `retest_tol_atr` ATR of L; in both cases j closes at or beyond L;
  * a close back inside L before that ends the watch: the breakout failed, there is no retest.
One retest per breakout (no leg dedupe needed). A value at bar j uses bars 0..j only. The down side is the up rule on
mirrored prices (a bearish reversal at a broken support).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

import numpy as np

if TYPE_CHECKING:  # pragma: no cover - typing only (screens.py imports this module)
    from app.screener.breakouts.screens import Bars, _Side

SCREENS: Dict[str, str] = {"B9": "Retest entry (second chance)"}
BASES: Dict[str, str] = {"range": "B1", "swing": "B2", "donchian": "B10", "trendline": "B5"}


def _reversal_reach(s: Optional[Dict[str, Any]] = None) -> int:
    """Candles the composite reversal can read before its bar (its window plus the legacy follow-through candle)."""
    if s is None:
        from app.price_action import pa_settings
        s = pa_settings.settings()
    reach = int(s["touch_reclaim_window"]) + 1
    if s.get("followthrough_mode", "legacy") != "legacy":
        reach += int(s.get("followthrough_max_bars", 1))
    return reach


def fast_median_range(h: np.ndarray, lo: np.ndarray, n: int) -> np.ndarray:
    """app.price_action.breaks.median_range on arrays: at bar i the median (high - low) of bars i-n..i-1, NaN for the first
    n bars and wherever the window holds a NaN (pandas' rolling median with min_periods n)."""
    rng = h - lo
    out = np.full(len(rng), np.nan)
    if len(rng) > n:
        win = np.lib.stride_tricks.sliding_window_view(rng[:-1], n)          # window k = bars k..k+n-1 -> bar k+n
        out[n:] = np.median(win, axis=1)
    return out


class _Reversals:
    """A logical reversal at a level on bar j - the composite rule of app/price_action/reversal.py exactly as
    runtime._reversal_series (ReversalAt) runs it, on real prices (bearish for the down side). Built lazily: the candles,
    median range and settings only when a bar first reaches a level, once per frame (shared by both sides)."""

    def __init__(self, b: "Bars", s: "_Side") -> None:
        from app.price_action import pa_settings
        self.b, self.s = b, s
        self.settings = pa_settings.settings()
        if self.settings["reversal_mode"] != "composite":
            raise ValueError(f"the retest reads the composite reversal rule, not {self.settings['reversal_mode']!r}")
        self.reach = _reversal_reach(self.settings)
        self.exact = float(self.settings["touch_tol_mr"]) <= 0
        self.dirn = 1 if s.sign > 0 else -1
        self._built: Any = None

    def _build(self) -> Any:
        if self._built is None:
            from app.price_action import reversal as rv
            b, settings = self.b, self.settings

            def make() -> Any:
                mr = fast_median_range(b.h, b.l, int(settings["median_range_n"]))     # = breaks.median_range (tested)
                rb = rv.Bars.__new__(rv.Bars)                    # the arrays rv.Bars(frame, mr) holds, without building a frame
                rb.frame, rb.o, rb.h, rb.l, rb.c, rb.mr, rb.n = None, b.o, b.h, b.l, b.c, mr, b.n
                return rb, settings["touch_tol_mr"] * np.where(np.isfinite(mr), mr, 0.0)
            self._built = b.memo(("reversal_bars",), make)
        return self._built

    def at(self, j: int, level: float) -> bool:
        """`level` in the side's orientation."""
        from app.price_action import reversal as rv
        low = float(np.min(self.s.l[max(0, j - self.reach + 1):j + 1]))
        if self.exact and not low <= level:                     # never reached the level: the touch test cannot pass (no setup)
            return False
        rb, tol = self._build()
        if not low <= level + tol[j]:                      # as _reversal_series: the reach filter, with its tolerance
            return False
        return bool(rv.evaluate(rb, int(j), self.dirn, [float(self.s.back(level))], float(tol[j]), self.settings)["ok"])


def _reversals(b: "Bars", s: "_Side", lv: np.ndarray, bars: List[int]) -> np.ndarray:
    """At each bar of `bars`: _Reversals.at(bar, lv[bar]) (tests compare it with ReversalAt's series)."""
    r = _Reversals(b, s)
    out = np.zeros(b.n, dtype=bool)
    for j in sorted(set(bars)):
        if np.isfinite(lv[j]):
            out[j] = r.at(int(j), float(lv[j]))
    return out


def b9(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    from app.screener.breakouts.screens import _RULES, _empty, one_per_leg
    base = BASES[p["retest_of"]]
    o = _empty(b.n)
    _RULES[base](b, s, p, o)
    sig = one_per_leg(b, s, o["raw"], o["fail_level"], int(p["leg_degree"]), int(p["leg_rearm_bars"]))
    tol = float(p["retest_tol_atr"]) * np.nan_to_num(b.atr_prev, nan=0.0)
    need_rev = bool(p["retest_reversal"])
    rev = _Reversals(b, s) if need_rev else None
    reach = rev.reach if rev is not None else 1
    watch = []                                             # (breakout bar, level, eligible bars)
    lv = np.full(b.n, np.nan)
    for ii in np.flatnonzero(sig):
        i = int(ii)
        level = float(o["level"][i])
        if not np.isfinite(level):
            continue
        left, bars = False, []
        for j in range(i + 1, min(b.n, i + 1 + int(p["retest_max_bars"]))):
            if s.c[j] < level:                             # back inside before a retest: the breakout failed
                break
            if left and j - i >= reach and s.c[j] >= level:   # a retest bar closes at or beyond the level: only those are read
                bars.append(j)
                lv[j] = level
            if s.l[j] > level + tol[j]:
                left = True
        watch.append((i, level, bars))
    if not watch:
        return
    for i, level, bars in watch:
        for j in bars:                                     # in order; the first retest that holds ends this breakout's watch
            if rev is not None:
                if lv[j] != level:                         # a later breakout's watch took the bar: its level, its retest
                    continue
                touched = rev.at(j, level)
            else:
                touched = bool(s.l[j] <= level + tol[j])
            if touched:
                out["raw"][j] = True
                out["level"][j] = out["fail_level"][j] = level
                out["base_len"][j] = j - i
                out["base_high"][j], out["base_low"][j] = o["base_high"][i], o["base_low"][i]
                out["event"][j] = f"RETEST_{base}"
                break


RULES = {"B9": b9}

__all__ = ["SCREENS", "BASES", "RULES", "b9", "fast_median_range"]
