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

from typing import TYPE_CHECKING, Any, Dict, List

import numpy as np
import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - typing only (screens.py imports this module)
    from app.screener.breakouts.screens import Bars, _Side

SCREENS: Dict[str, str] = {"B9": "Retest entry (second chance)"}
BASES: Dict[str, str] = {"range": "B1", "swing": "B2", "donchian": "B10", "trendline": "B5"}


def _reversal_reach() -> int:
    """Candles the composite reversal can read before its bar (its window plus the legacy follow-through candle)."""
    from app.price_action import pa_settings
    s = pa_settings.settings()
    reach = int(s["touch_reclaim_window"]) + 1
    if s.get("followthrough_mode", "legacy") != "legacy":
        reach += int(s.get("followthrough_max_bars", 1))
    return reach


def _exact_touch() -> bool:
    """The reversal rule's touch has no tolerance (touch_tol_mr 0, the default): a candle must reach the level itself."""
    from app.price_action import pa_settings
    return float(pa_settings.settings()["touch_tol_mr"]) <= 0


def _reversals(b: "Bars", s: "_Side", lv: np.ndarray, bars: List[int]) -> np.ndarray:
    """At each bar of `bars`: a logical reversal at lv[bar] - the composite rule of app/price_action/reversal.py exactly as
    runtime._reversal_series (ReversalAt) runs it, on real prices (bearish for the down side). Its candles, median range
    and settings are built once per frame and shared by both sides."""
    from app.price_action import pa_settings
    from app.price_action import reversal as rv
    from app.price_action.breaks import median_range
    settings = pa_settings.settings()
    if settings["reversal_mode"] != "composite":
        raise ValueError(f"the retest reads the composite reversal rule, not {settings['reversal_mode']!r}")

    def build() -> Any:
        frame = pd.DataFrame({"open": b.o, "high": b.h, "low": b.l, "close": b.c})
        mr = median_range(frame, settings["median_range_n"])
        return rv.Bars(frame, mr), settings["touch_tol_mr"] * np.where(np.isfinite(mr), mr, 0.0)
    rb, tol = b.memo(("reversal_bars",), build)
    out = np.zeros(b.n, dtype=bool)
    dirn = 1 if s.sign > 0 else -1
    reach = _reversal_reach()
    for j in sorted(set(bars)):
        level = float(s.back(lv[j]))
        # as _reversal_series: a bar whose last `reach` candles never reached the level cannot pass the touch test
        if np.isfinite(level) and float(np.min(s.l[max(0, j - reach + 1):j + 1])) <= lv[j] + tol[j]:
            out[j] = bool(rv.evaluate(rb, int(j), dirn, [level], float(tol[j]), settings)["ok"])
    return out


def b9(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    from app.screener.breakouts.screens import _RULES, _empty, one_per_leg
    base = BASES[p["retest_of"]]
    o = _empty(b.n)
    _RULES[base](b, s, p, o)
    sig = one_per_leg(b, s, o["raw"], o["fail_level"], int(p["leg_degree"]), int(p["leg_rearm_bars"]))
    tol = float(p["retest_tol_atr"]) * np.nan_to_num(b.atr_prev, nan=0.0)
    need_rev = bool(p["retest_reversal"])
    reach = _reversal_reach() if need_rev else 1
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
    if need_rev and _exact_touch() and not any(np.min(s.l[max(0, j - reach + 1):j + 1]) <= level for _, level, bars in watch for j in bars):
        return                                             # no candle near a broken level: no reversal can touch it (cheap exit)
    if need_rev:
        rev = _reversals(b, s, lv, [j for _, _, bars in watch for j in bars])
    for i, level, bars in watch:
        for j in bars:
            if need_rev and lv[j] != level:               # a later breakout's watch took the bar: its level, its retest
                continue
            touched = bool(rev[j]) if need_rev else bool(s.l[j] <= level + tol[j])
            if touched:
                out["raw"][j] = True
                out["level"][j] = out["fail_level"][j] = level
                out["base_len"][j] = j - i
                out["base_high"][j], out["base_low"][j] = o["base_high"][i], o["base_low"][i]
                out["event"][j] = f"RETEST_{base}"
                break


RULES = {"B9": b9}

__all__ = ["SCREENS", "BASES", "RULES", "b9"]
