"""Real break vs false break of a price level - one definition for invalidation, exits and chart annotations.

Ported from Trade@0df3e09d0aa5942e3352327d5b998c5f6939ac61, elliott/breaks.py.

No fixed points - the market's own noise and strength decide:
  MR  = median (high - low) of the previous `median_range_n` CLOSED bars (the current bar excluded)
  buf = break_buffer_mr x MR
  Candidate: bar t closes beyond the level by buf (side "below": close < L - buf; "above": close > L + buf).
  A real break is confirmed by whichever comes first:
    (a) Displacement - bar t itself is strong (range >= strength_min x MR) and closes within `break_close_loc` of its
        break-side end (when `break_displacement_confirm`).
    (b) Acceptance - the next `break_no_reclaim_bars` bars also close beyond the level (no reclaim). 0 = one close decides.
    (c) Failed retest - after a reclaim, the level is retested from the broken side and rejected by a logical reversal
        (`reversal.retest_fn`; on when `break_retest_confirm`).
  Otherwise (a wick, or a weak close followed by a reclaim) it is a false break: the candidate is dropped.
Causal: the confirm index c is decided only from bars <= c, so "broken by bar t?" equals c <= t (truncation invariant).
"""
from typing import Callable, Dict, Optional

import numpy as np
import pandas as pd

RetestFn = Callable[[pd.DataFrame, int, float, str, int], Optional[int]]


def median_range(frame: pd.DataFrame, n: int) -> np.ndarray:
    """At bar i: the median (high - low) of bars [i - n, i - 1] (the current bar excluded). NaN for the first n bars."""
    rng = frame["high"].astype(float) - frame["low"].astype(float)
    return rng.shift(1).rolling(n, min_periods=n).median().to_numpy(float)


def _beyond(close: float, level: float, buf: float, side: str) -> bool:
    return close < level - buf if side == "below" else close > level + buf


def _back_inside(close: float, level: float, side: str) -> bool:
    return close >= level if side == "below" else close <= level


def _first(x: Optional[int], y: Optional[int]) -> Optional[int]:
    if x is None:
        return y
    return x if y is None else min(x, y)


def first_real_break(frame: pd.DataFrame, start: int, level: float, side: str, s: Dict, mr: Optional[np.ndarray] = None,
                     end: Optional[int] = None, retest_fn: Optional[RetestFn] = None) -> Optional[int]:
    """Confirm index of the first REAL break of `level` within [start, end], or None.
    side: "below" (price breaking down through the level) or "above"."""
    if side not in ("below", "above"):
        raise ValueError("side must be 'below' or 'above'")
    h, l, c = (frame[k].to_numpy(float) for k in ("high", "low", "close"))
    n = len(c)
    end = n - 1 if end is None else min(end, n - 1)
    mr = median_range(frame, s["median_range_n"]) if mr is None else mr
    k = s["break_no_reclaim_bars"]
    t = max(start, 0)
    best_r: Optional[int] = None                    # (c) a failed retest found earlier - "whichever comes first"
    while t <= end:
        if best_r is not None and t > best_r:
            return best_r
        m = mr[t]
        if not np.isfinite(m) or not _beyond(c[t], level, s["break_buffer_mr"] * m, side):
            t += 1
            continue
        rng = h[t] - l[t]
        if s["break_displacement_confirm"] and rng >= s["strength_min"] * m and rng > 0:
            loc = (c[t] - l[t]) / rng if side == "below" else (h[t] - c[t]) / rng     # distance from the break-side end
            if loc <= s["break_close_loc"]:
                return _first(t, best_r)                                              # (a) displacement
        if k == 0:
            return _first(t, best_r)
        ok, j = True, t
        for j in range(t + 1, t + k + 1):
            if j > end:
                return best_r                                                         # not decided yet - no future bars
            if _back_inside(c[j], level, side):
                ok = False
                break
            mj, rj = mr[j], h[j] - l[j]
            if (s["break_displacement_confirm"] and np.isfinite(mj) and rj > 0 and rj >= s["strength_min"] * mj
                    and _beyond(c[j], level, s["break_buffer_mr"] * mj, side)
                    and ((c[j] - l[j]) / rj if side == "below" else (h[j] - c[j]) / rj) <= s["break_close_loc"]):
                return _first(j, best_r)                                              # displacement inside the window
        if ok:
            return _first(t + k, best_r)                                              # (b) acceptance
        if retest_fn is not None:
            r = retest_fn(frame, t, level, side, end)
            if r is not None:
                best_r = _first(r, best_r)                                            # (c) failed retest - an earlier confirm wins
        t = j + 1 if not ok else t + 1                                                # false break: resume after the reclaim
    return best_r


def first_wick_break(frame: pd.DataFrame, start: int, level: float, side: str, end: Optional[int] = None) -> Optional[int]:
    """Strict basis: the first wick beyond the level (count_inv_basis = wick)."""
    x = frame["low"].to_numpy(float) if side == "below" else frame["high"].to_numpy(float)
    end = len(x) - 1 if end is None else min(end, len(x) - 1)
    for t in range(max(start, 0), end + 1):
        if (x[t] < level) if side == "below" else (x[t] > level):
            return t
    return None


class BreakCache:
    """(level, side, start, basis) -> first confirm index, scanning only up to the asked bar (never reads later bars).
    A found confirm index is permanent (it depends only on bars <= itself); "not yet" is re-scanned for a later bar."""

    def __init__(self, frame: pd.DataFrame, s: Dict) -> None:
        self.frame, self.s = frame, s
        self.mr = median_range(frame, s["median_range_n"])
        self.retest: Optional[RetestFn] = None
        if s.get("break_retest_confirm") and s["count_inv_basis"] != "wick":
            from app.price_action.reversal import retest_fn                          # imported here to avoid a cycle
            self.retest = retest_fn(frame, s, self.mr)
        self._found: Dict[tuple, int] = {}
        self._clear: Dict[tuple, int] = {}

    def broken_by(self, start: int, level: float, side: str, t_idx: int) -> bool:
        return self.confirm_index(start, level, side, t_idx) is not None

    def confirm_index(self, start: int, level: float, side: str, t_idx: int) -> Optional[int]:
        """Index of the real break confirmed by bar t_idx (None if none yet)."""
        key = (int(start), round(float(level), 6), side, self.s["count_inv_basis"])
        found = self._found.get(key)
        if found is not None:
            return found if found <= t_idx else None
        if self._clear.get(key, -1) >= t_idx:
            return None
        if self.s["count_inv_basis"] == "wick":
            found = first_wick_break(self.frame, start, level, side, end=t_idx)
        else:
            found = first_real_break(self.frame, start, level, side, self.s, mr=self.mr, end=t_idx, retest_fn=self.retest)
        if found is None:
            self._clear[key] = t_idx
            return None
        self._found[key] = found
        return found if found <= t_idx else None


def frame_index_at(frame: pd.DataFrame, t) -> int:
    """Index of the last bar that ended by `t` (bar_end <= t); -1 if none."""
    return int(np.searchsorted(frame["bar_end"].to_numpy(), np.datetime64(pd.Timestamp(t)), side="right")) - 1
