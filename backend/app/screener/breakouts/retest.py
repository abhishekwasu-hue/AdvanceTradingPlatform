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

Strategy templates R7 (owner order section 0): `retest_of` also takes `channel` - the B5 channel breakout (the far edge of
a rising channel up, of a falling one down) - and a channel retest reads the broken edge's value AT each bar after the
breakout (the line projected along its slope), not the value frozen at the breakout bar. `retest_events` is the one
place the retests are found: the B9 screen and the template companion RetestEntryRef (app/screener/runtime.py) both
read it, so a template's invalidation is the same retest the setup fired on.

Owner decision TPL-Q18 (23 Oct): `trendline` keeps the level frozen at its breakout bar, byte-identical (pinned in
tests/test_tpl9_c4_trendline_retest.py), and `trendline_projected` is a NEW choice - the same B5 line breakout (line mode),
with the broken line read at each bar after the breakout (the falling resistance up, the rising support down, projected
along its slope), as the channel edge is. Template C4 uses it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import numpy as np

if TYPE_CHECKING:  # pragma: no cover - typing only (screens.py imports this module)
    from app.screener.breakouts.screens import Bars, _Side

SCREENS: Dict[str, str] = {"B9": "Retest entry (second chance)"}
BASES: Dict[str, str] = {"range": "B1", "swing": "B2", "donchian": "B10", "trendline": "B5", "channel": "B5",
                         "trendline_projected": "B5"}
# R7: the retests whose level is read along the broken line at each bar. `trendline` keeps the level frozen at its
# breakout bar, as before (owner decision TPL-Q18: byte-identical); `trendline_projected` is its projected twin (C4).
PROJECTED: tuple = ("channel", "trendline_projected")


@dataclass(frozen=True)
class Retest:
    """One retest that held, in the side's orientation (`_Side`: the down side's prices negated): the breakout bar, the
    retest bar, the level at the retest bar, the base's high / low at the breakout, and the broken edge's line (a channel
    retest: slope and value at bar 0, else None - a fixed level)."""
    breakout: int
    bar: int
    level: float
    base_high: float
    base_low: float
    slope: Optional[float] = None
    c0: Optional[float] = None

    def level_at(self, x: int) -> float:
        """The broken level at bar x (a fixed level: itself; a channel edge: projected along its line)."""
        if self.slope is None or self.c0 is None:
            return self.level
        return self.slope * x + self.c0


def base_params(p: Dict[str, Any]) -> Dict[str, Any]:
    """The parameters the base breakout runs with: a channel retest follows the B5 rule in channel mode, a projected
    trendline retest the B5 rule in line mode (a `trendline` retest runs with the given parameters, as before)."""
    if p["retest_of"] == "channel":
        return {**p, "tl_mode": "channel"}
    if p["retest_of"] == "trendline_projected":
        return {**p, "tl_mode": "line"}
    return p


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


def retest_events(b: "Bars", s: "_Side", p: Dict[str, Any]) -> List[Retest]:
    """Every retest that held (in the order of their breakouts), with the B9 rule above; a channel retest reads the
    broken far edge at each bar (the support line b5 chose at the breakout bar, plus the channel's width), a projected
    trendline retest the broken line itself at each bar (the resistance line b5 chose at the breakout bar)."""
    from app.screener.breakouts.screens import _RULES, _empty, one_per_leg
    of = p["retest_of"]
    base = BASES[of]
    bp = base_params(p)
    o = _empty(b.n)
    _RULES[base](b, s, bp, o)
    sig = one_per_leg(b, s, o["raw"], o["fail_level"], int(p["leg_degree"]), int(p["leg_rearm_bars"]))
    tol = float(p["retest_tol_atr"]) * np.nan_to_num(b.atr_prev, nan=0.0)
    need_rev = bool(p["retest_reversal"])
    rev = _Reversals(b, s) if need_rev else None
    reach = rev.reach if rev is not None else 1
    sup = None
    if of in PROJECTED:
        from app.screener.breakouts.lines import _lines
        if of == "channel":
            sup = _lines(b, s, bp, "support", edges=True)      # the channels b5 chose from (memoised on the bars)
        else:
            sup = _lines(b, s, bp, "resistance")               # the lines b5 broke in line mode (memoised on the bars)
    watch = []                                             # (breakout bar, (bar, level there) of each eligible bar, line)
    lv = np.full(b.n, np.nan)                              # the level of the latest watch that reached each bar
    for ii in np.flatnonzero(sig):
        i = int(ii)
        level = float(o["level"][i])
        if not np.isfinite(level):
            continue
        line = None
        if sup is not None:
            chosen = sup.select(i)                         # the channel / line b5 broke at i, projected
            if chosen is not None:                         # (a line's edge is 0: the line itself)
                line = (chosen.m, chosen.c0 + (chosen.edge if of == "channel" else 0.0))
        left, bars = False, []
        for j in range(i + 1, min(b.n, i + 1 + int(p["retest_max_bars"]))):
            lvj = level if line is None else line[0] * j + line[1]
            if s.c[j] < lvj:                               # back inside before a retest: the breakout failed
                break
            if left and j - i >= reach and s.c[j] >= lvj:     # a retest bar closes at or beyond the level: only those are read
                bars.append((j, lvj))
                lv[j] = lvj
            if s.l[j] > lvj + tol[j]:
                left = True
        watch.append((i, bars, line))
    out: List[Retest] = []
    for i, bars, line in watch:
        for j, lvj in bars:                                # in order; the first retest that holds ends this breakout's watch
            if rev is not None:
                if lv[j] != lvj:                           # a later breakout's watch took the bar: its level, its retest
                    continue
                touched = rev.at(j, lvj)
            else:
                touched = bool(s.l[j] <= lvj + tol[j])
            if touched:
                out.append(Retest(i, j, lvj, float(o["base_high"][i]), float(o["base_low"][i]),
                                  None if line is None else float(line[0]), None if line is None else float(line[1])))
                break
    return out


def b9(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    base = BASES[p["retest_of"]]
    for r in retest_events(b, s, p):
        j = r.bar
        out["raw"][j] = True
        out["level"][j] = out["fail_level"][j] = r.level
        out["base_len"][j] = j - r.breakout
        out["base_high"][j], out["base_low"][j] = r.base_high, r.base_low
        out["event"][j] = f"RETEST_{base}"


RULES = {"B9": b9}

__all__ = ["SCREENS", "BASES", "PROJECTED", "RULES", "Retest", "b9", "base_params", "retest_events", "fast_median_range"]
