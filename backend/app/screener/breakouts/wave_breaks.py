"""Wave templates W4 + R7 (docs/design/WAVE_TEMPLATES_REVIEW.md WB6): the break of an anchored wave line and the retest
of a broken wave line / wave end, as causal bar-by-bar series (behind the flag `screener_waves`).

* Break (the BK1 rule): the first close beyond the line by k x ATR (the ATR of the bars before, `Bars.atr_prev`), on
  the side asked. A line is the count's own (app/price_action/waves/levels.py): two anchors from the wave's pivots,
  projected to each bar. One break per line: a line is ARMED by a close on its near side and fires once; a line that is
  already beyond the close when it first becomes known (a new swing moved its anchor) waits for a close back on the near
  side first, so an old move is never reported as a new break. No volume requirement.
* Wave end (G6): a finished wave's end as a fixed level (a line of no slope), with the same rule.
* Retest (R7, the extension of RetestEntry with `of = "wave_line"` / `"wave_end"`): after a break at bar i of the line
  with anchors a, the B9 rule of `retest.py` with the level read at each bar j as the BROKEN line's value at j (the
  anchors it had when it broke, even when the count has moved on since): price leaves the level by `retest_tol_atr`
  ATR, then within `retest_max_bars` comes back and holds it - a logical reversal at the level (`retest_reversal`) or a
  touch - closing at or beyond it; a close back inside first ends the watch. One retest per break; when a later break's
  watch covers a bar, that bar belongs to the later break. The existing RetestEntry choices (range / swing / donchian /
  trendline) do not come here: their path is unchanged.

The down side is the up rule on mirrored prices. A value at bar t uses bars 0..t only. Signals only, places no orders.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.price_action.waves.levels import Anchor, project

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.screener.breakouts.screens import Bars, _Side

WAVE_RETEST_OF: Tuple[str, ...] = ("wave_line", "wave_end")


def line_breaks(b: "Bars", s: "_Side", values: np.ndarray, anchors: Sequence[Optional[Anchor]], k: float) -> List[Tuple[int, Anchor]]:
    """(bar, anchors) of each break of the lines `anchors` (one per bar, real prices) on side `s`, by k x ATR."""
    buf = k * b.atr_prev
    armed: set = set()
    fired: set = set()
    out: List[Tuple[int, Anchor]] = []
    for t in range(b.n):
        a = anchors[t]
        if a is None or a in fired or not np.isfinite(values[t]):
            continue
        level = s.sign * float(values[t])
        if a in armed and np.isfinite(buf[t]) and s.c[t] > level + buf[t]:
            fired.add(a)
            out.append((t, a))
        elif s.c[t] <= level:
            armed.add(a)
    return out


def break_series(n: int, breaks: Sequence[Tuple[int, Anchor]]) -> np.ndarray:
    out = np.zeros(n, dtype=bool)
    for t, _ in breaks:
        out[t] = True
    return out


def retests(b: "Bars", s: "_Side", breaks: Sequence[Tuple[int, Anchor]], max_bars: int, reversal: bool, tol_atr: float) -> np.ndarray:
    """The retest bars of the breaks (B9's rule with the broken line's value at each bar)."""
    from app.screener.breakouts.retest import _Reversals
    tol = tol_atr * np.nan_to_num(b.atr_prev, nan=0)
    rev = _Reversals(b, s) if reversal else None
    reach = rev.reach if rev is not None else 1
    claimed: Dict[int, int] = {}
    watches: List[List[Tuple[int, float]]] = []
    for w, (i, a) in enumerate(breaks):
        left, bars = False, []
        for j in range(i + 1, min(b.n, i + 1 + int(max_bars))):
            level = s.sign * project(a, j)
            if s.c[j] < level:                                   # back inside before a retest: the break failed
                break
            if left and j - i >= reach and s.c[j] >= level:       # only bars closing at or beyond the level are read
                bars.append((j, level))
                claimed[j] = w
            if s.l[j] > level + tol[j]:
                left = True
        watches.append(bars)
    out = np.zeros(b.n, dtype=bool)
    for w, bars in enumerate(watches):
        for j, level in bars:                                    # in order; the first retest that holds ends this watch
            if claimed[j] != w:
                continue
            touched = rev.at(j, level) if rev is not None else bool(s.l[j] <= level + tol[j])
            if touched:
                out[j] = True
                break
    return out


__all__ = ["WAVE_RETEST_OF", "line_breaks", "break_series", "retests"]
