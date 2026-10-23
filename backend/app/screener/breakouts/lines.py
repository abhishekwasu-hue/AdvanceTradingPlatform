"""U6 BK4: B5 trendline / channel breakout and B6 flag / pennant / triangle breakout as causal, bar-by-bar series (spec
docs/specs/ATP_BREAKOUT_SCREENERS_ADDENDUM.md B5 / B6; behind `screener_breakouts` AND `screener_breakouts_bk4`).

Lines are the platform's trendline rule (app/price_action/trendlines.py, the Trade port that the CH3b chart layer draws),
evaluated as of every bar t on the bars BEFORE it (tested equal to `find_trendline` on the prefix):
  * swings: a bar that is the extreme of the 2 x `tl_order` + 1 bars around it, known `tl_order` bars later;
  * a falling resistance over swing highs (a rising support over swing lows): anchors A < B at least `tl_min_gap` bars
    apart, 0 < |slope| <= `tl_max_slope` x the median bar range (the angle filter), no close beyond the line from A to
    the bar before t, at least `tl_min_touches` swings within `tl_touch_tol` median ranges of it; the newest B and, for
    it, the oldest A;
  * the age filter: A at most `tl_max_bars` bars before t (the Trade finder has none; it also bounds the cost).
Each anchor pair's first close beyond its line is computed once, so the finder only runs on the bars where some line
breaks - the result is the same as running it on every bar.

  B5 line      the first close above the falling resistance (up) / below the rising support (down) by `min_break_atr` ATR
     channel   the first close above the far edge of a rising channel (up) / below that of a falling one (down): the parallel
               through the farthest extreme between the support line's anchors (`trendlines.channel_of`); measured move = the edge
               plus the channel's width
  B6 triangle  a falling resistance and a rising support, both current, converging (narrower now than where they both
               start); the close above the resistance by `min_break_atr` ATR on volume; measured move = the level plus the
               triangle's height at its start (measured move). Ascending / descending triangles with a flat side are not
               detected (the trendline rule needs a sloped line) - a flat top is a B1 range (open question BK4-2)
     flag      a pole of at least `pole_min_atr` ATR within `pole_max_bars` bars, then `flag_min_bars`-`flag_max_bars` bars that
               stay under the pole's top and give back at most `flag_max_retrace` of it, then a close above their high by
               `min_break_atr` ATR on volume; measured move = the level plus the pole
     pennant   a flag whose consolidation contracts (its second half's range below its first half's)

The down side is the up rule on mirrored prices (high <-> -low): the falling resistance mirrors to the rising support.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import numpy as np

if TYPE_CHECKING:  # pragma: no cover - typing only (screens.py imports this module)
    from app.screener.breakouts.screens import Bars, _Side

SCREENS: Dict[str, str] = {"B5": "Trendline / channel breakout", "B6": "Flag / pennant / triangle breakout"}

_EPS = 1e-9                                 # trendlines.find_trendline's tolerance for "a close beyond the line"


def swing_positions(x: np.ndarray, order: int, high: bool) -> np.ndarray:
    """Bars that are the max (min) of the 2 x order + 1 bars around them - app.price_action.swings.swing_indices."""
    n = len(x)
    w = 2 * order + 1
    if n < w:
        return np.zeros(0, dtype=int)
    win = np.lib.stride_tricks.sliding_window_view(x, w)
    ext = win.max(axis=1) if high else win.min(axis=1)
    return np.flatnonzero(x[order:n - order] == ext) + order


def ref_ranges(h: np.ndarray, lo: np.ndarray, n: int = 20, min_bars: int = 10) -> np.ndarray:
    """rr[t] = trendlines.ref_range of the bars before t: the median of the last n finite ranges among the last 4n bars
    (NaN below min_bars)."""
    rng = h - lo
    out = np.full(len(h) + 1, np.nan)
    if np.isfinite(rng).all():                                  # the common case: a median of the bars before
        for t in range(min_bars, min(n, len(rng)) + 1):          # fewer than n bars yet: all of them
            out[t] = float(np.median(rng[:t]))
        if len(rng) >= n:
            out[n:] = np.median(np.lib.stride_tricks.sliding_window_view(rng, n), axis=1)
        return out
    for t in range(len(h) + 1):
        arr = rng[max(0, t - 4 * n):t]
        arr = arr[np.isfinite(arr)]
        if len(arr) >= min_bars:
            out[t] = float(np.median(arr[-n:]))
    return out


@dataclass(frozen=True)
class Line:
    a: int
    b: int
    m: float
    c0: float                               # the line's value at bar 0: value(x) = m x + c0
    broken: float                           # the first bar from A whose close is beyond the line (inf: none within reach)
    edge: float = 0.0                       # support lines: the channel offset (the highest high above the line in [A, B])
    edge_broken: float = np.inf             # support lines: the first bar after B whose close is above the channel edge

    def at(self, x: float) -> float:
        return self.m * x + self.c0


PAIR_CELLS = 2_000_000          # cells of the pairs x bars grid computed at once (about 16 MB per float array)


class Lines:
    """One kind of line in the side's orientation - "resistance" (falling, over swing highs, broken by a close above) or
    "support" (rising, over swing lows, broken by a close below) - with every anchor pair's first break."""

    def __init__(self, b: "Bars", s: "_Side", p: Dict[str, Any], kind: str, rr: np.ndarray, edges: bool = False) -> None:
        self.kind, self.rr, self.n = kind, rr, b.n
        self.order, self.gap = int(p["tl_order"]), int(p["tl_min_gap"])
        self.max_slope, self.tol, self.touches = float(p["tl_max_slope"]), float(p["tl_touch_tol"]), int(p["tl_min_touches"])
        self.max_bars = int(p["tl_max_bars"])
        high = kind == "resistance"
        self.y = s.h if high else s.l
        self.idx = swing_positions(self.y, self.order, high)
        sign = -1.0 if high else 1.0
        c = s.c
        idx = self.idx
        ai, bi = np.nonzero((idx[None, :] - idx[:, None] >= self.gap) & (idx[None, :] - idx[:, None] <= self.max_bars))
        a_arr, b_arr = idx[ai], idx[bi]
        m = (self.y[b_arr] - self.y[a_arr]) / np.maximum(b_arr - a_arr, 1)
        keep = m * sign > 0
        a_arr, b_arr, m = a_arr[keep], b_arr[keep], m[keep]
        c0 = self.y[a_arr] - m * a_arr
        span = np.arange(self.max_bars + 1)
        first = np.full(len(a_arr), np.inf)
        edge = np.zeros(len(a_arr))
        edge_broken = np.full(len(a_arr), np.inf)
        # BK4 review (LOW): the pairs x (tl_max_bars + 1) grid in chunks of rows, so memory is bounded by PAIR_CELLS
        # cells whatever the number of anchor pairs (the results are row by row, so equal to the one-grid computation)
        rows = max(1, PAIR_CELLS // (self.max_bars + 1))
        for lo in range(0, len(a_arr), rows):
            sl = slice(lo, lo + rows)
            a_c, m_c, c0_c = a_arr[sl], m[sl], c0[sl]
            xs = a_c[:, None] + span[None, :]                                # each pair's bars from A on, within the age limit
            valid = xs <= self.n - 1
            xc = np.minimum(xs, self.n - 1)
            lines = m_c[:, None] * xs + c0_c[:, None]
            cx = c[xc]
            with np.errstate(invalid="ignore"):
                beyond = valid & ((cx > lines + _EPS) if high else (cx < lines - _EPS))
            first[sl] = np.where(beyond.any(axis=1), a_c + np.argmax(beyond, axis=1), np.inf)
            if edges:                                                        # support: the channel's far edge (trendlines.channel_of)
                col = b_arr[sl] - a_c
                gap_hi = np.maximum.accumulate(np.where(valid, s.h[xc] - lines, -np.inf), axis=1)
                edge_c = gap_hi[np.arange(len(a_c)), col]
                edge[sl] = edge_c
                with np.errstate(invalid="ignore"):
                    above = valid & (cx > lines + edge_c[:, None] + _EPS) & (span[None, :] > col[:, None])
                edge_broken[sl] = np.where(above.any(axis=1), a_c + np.argmax(above, axis=1), np.inf)
        self.A, self.B, self.M, self.C0, self.BRK, self.EDGE, self.EDGE_BRK = a_arr, b_arr, m, c0, first, edge, edge_broken
        self.absM = np.abs(m)
        self._lines: Dict[int, Line] = {}

    def line_at(self, k: int) -> Line:
        if k not in self._lines:
            self._lines[k] = Line(int(self.A[k]), int(self.B[k]), float(self.M[k]), float(self.C0[k]), float(self.BRK[k]),
                                  float(self.EDGE[k]), float(self.EDGE_BRK[k]))
        return self._lines[k]

    def line(self, a: int, b: int) -> Optional[Line]:
        hit = np.flatnonzero((self.A == a) & (self.B == b))
        return self.line_at(int(hit[0])) if len(hit) else None

    def select(self, t: int) -> Optional[Line]:
        """The line as of bar t (bars before t): trendlines.find_trendline's choice - the newest second anchor B and, for it,
        the oldest valid first anchor A - with the age filter; evaluated over every anchor pair at once (tested equal to the
        finder on each prefix)."""
        rr = self.rr[t]
        if not np.isfinite(rr) or rr <= 0 or not len(self.A):
            return None
        last = t - 1 - self.order
        cand = np.flatnonzero((self.B <= last) & (self.A >= t - self.max_bars) & (self.absM <= self.max_slope * rr) & (self.BRK >= t))
        if not len(cand):
            return None
        lo = np.searchsorted(self.idx, t - self.max_bars)
        known = self.idx[lo:np.searchsorted(self.idx, last, side="right")]   # touches count swings at or after A only
        yk = self.y[known]
        tol = self.tol * rr
        bs = self.B[cand]
        while len(cand):                                   # the newest second anchor first, as the finder
            top = bs.max()
            sel = bs == top
            group = cand[sel]
            near = np.abs(yk[None, :] - (self.M[group, None] * known[None, :] + self.C0[group, None])) <= tol
            touches = (near & (known[None, :] >= self.A[group, None])).sum(axis=1)
            good = group[touches >= self.touches]
            if len(good):
                return self.line_at(int(good[np.argmin(self.A[good])]))   # and for it the oldest first anchor
            cand, bs = cand[~sel], bs[~sel]
        return None

    def candidates(self, attr: str, close: np.ndarray, buf: np.ndarray) -> List[int]:
        """Bars where some line (attr broken) or channel edge (attr edge_broken) is first closed beyond by the buffer too: the
        only bars the chosen line can break on with a signal (the finder runs there only)."""
        if not len(self.A):
            return []
        t = self.BRK if attr == "broken" else self.EDGE_BRK
        fin = np.isfinite(t)
        if not fin.any():
            return []
        k = np.flatnonzero(fin)
        tt = t[k].astype(int)
        level = self.M[k] * tt + self.C0[k] + (self.EDGE[k] if attr != "broken" else 0.0)
        with np.errstate(invalid="ignore"):
            hit = np.isfinite(buf[tt]) & (close[tt] > level + buf[tt])
        k, tt = k[hit], tt[hit]
        # the chosen line at t must be one of these pairs, so one of them must itself be a valid line at t (B known, the
        # angle filter with t's median range, enough touches) - else the finder cannot return a line that signals
        rr = self.rr[tt]
        with np.errstate(invalid="ignore"):
            ok = np.isfinite(rr) & (rr > 0) & (self.B[k] <= tt - 1 - self.order) & (self.absM[k] <= self.max_slope * rr)
        k, tt, rr = k[ok], tt[ok], rr[ok]
        if not len(k) or not len(self.idx):
            return []
        sw = self.idx[None, :]
        near = np.abs(self.y[self.idx][None, :] - (self.M[k, None] * sw + self.C0[k, None])) <= (self.tol * rr)[:, None]
        on = (sw >= self.A[k, None]) & (sw <= (tt - 1 - self.order)[:, None])
        good = (near & on).sum(axis=1) >= self.touches
        return sorted(set(tt[good].tolist()))


def _lines(b: "Bars", s: "_Side", p: Dict[str, Any], kind: str, edges: bool = False) -> Lines:
    rr = b.memo(("tl_rr",), lambda: ref_ranges(b.h, b.l))
    key = ("tl_lines", kind, s.sign, edges, *(p[k] for k in ("tl_order", "tl_min_gap", "tl_max_slope", "tl_touch_tol", "tl_min_touches", "tl_max_bars")))
    return b.memo(key, lambda: Lines(b, s, p, kind, rr, edges))


def _fill(out: Dict[str, np.ndarray], t: int, level: float, length: int, base_low: float, event: str, move: float = np.nan) -> None:
    out["raw"][t] = True
    out["level"][t] = out["fail_level"][t] = level
    out["base_len"][t] = length
    out["base_high"][t] = level
    out["base_low"][t] = base_low
    out["event"][t] = event
    out["measured_move"][t] = move


# ------------------------------------------------------------------------------------------------------------------ B5
def b5(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    buf = float(p["min_break_atr"]) * b.atr_prev
    if p["tl_mode"] == "line":
        res = _lines(b, s, p, "resistance")
        for t in res.candidates("broken", s.c, buf):
            line = res.select(t)
            if line is not None and np.isfinite(buf[t]) and s.c[t] > line.at(t) + buf[t]:
                _fill(out, t, line.at(t), t - line.a, float(np.min(s.l[line.a:t])), "TRENDLINE")
        return
    sup = _lines(b, s, p, "support", edges=True)
    for t in sup.candidates("edge_broken", s.c, buf):
        line = sup.select(t)
        if line is None or line.edge_broken < t or not np.isfinite(buf[t]):
            continue
        top = line.at(t) + line.edge
        if s.c[t] > top + buf[t]:
            _fill(out, t, top, t - line.a, line.at(t), "CHANNEL", move=top + line.edge)


# ------------------------------------------------------------------------------------------- the trend-side channel line
def channel_line(b: "Bars", s: "_Side", p: Dict[str, Any], edge_touches: int = 0) -> np.ndarray:
    """Idea library IDEA-12 (ScreenQL ChannelLine): the trend-side line of the channel B5 reads, on EVERY bar, in the side's
    orientation - the rising support line of a rising channel (up; the down side's mirrored prices give the falling
    resistance of a falling channel). At bar t it is the channel b5 itself would read at t (`Lines.select` on the bars
    before t: the newest second anchor, the touches, the angle and age filters, no close through the line from its first
    anchor to the bar before t), projected to t; NaN when there is none, or when the channel already broke: a close beyond
    its far edge before t (that is B5's channel breakout). `edge_touches` (0: not checked, the B5 channel as it is) also
    asks for that many swing highs on the far edge (within the touch tolerance, from the first anchor to the last swing
    known before t), so both lines have reactions. Only bars before t decide (no look-ahead); the line's value at t is its
    projection, so the bar's own touch and close are read against it."""
    sup = _lines(b, s, p, "support", edges=True)
    out = np.full(b.n, np.nan)
    if not len(sup.A):
        return out
    highs = swing_positions(s.h, sup.order, True) if edge_touches > 0 else np.zeros(0, dtype=int)
    for t in range(int(sup.B.min()) + sup.order + 1, b.n):
        line = sup.select(t)
        if line is None or line.edge_broken < t:
            continue
        if edge_touches > 0:
            known = highs[(highs >= line.a) & (highs <= t - 1 - sup.order)]
            far = line.m * known + line.c0 + line.edge
            if int((np.abs(s.h[known] - far) <= sup.tol * sup.rr[t]).sum()) < edge_touches:
                continue
        out[t] = line.at(t)
    return out


# ------------------------------------------------------------------------------------------------------------------ B6
def _triangle(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray], vol_ok: np.ndarray, buf: np.ndarray) -> None:
    res, sup = _lines(b, s, p, "resistance"), _lines(b, s, p, "support")
    for t in res.candidates("broken", s.c, buf):
        if not (vol_ok[t] and np.isfinite(buf[t])):
            continue
        top, bot = res.select(t), sup.select(t)
        if top is None or bot is None:
            continue
        x0 = max(top.a, bot.a)
        start, now = top.at(x0) - bot.at(x0), top.at(t) - bot.at(t)
        if not (0 < now < start) or not s.c[t] > top.at(t) + buf[t]:
            continue
        _fill(out, t, top.at(t), t - x0, bot.at(t), "TRIANGLE", move=top.at(t) + start)


def _flag(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray], vol_ok: np.ndarray, buf: np.ndarray) -> None:
    lmin, lmax = int(p["flag_min_bars"]), int(p["flag_max_bars"])
    pole_n, pole_k, retrace = int(p["pole_max_bars"]), float(p["pole_min_atr"]), float(p["flag_max_retrace"])
    pennant = p["pattern"] == "pennant"
    for t in range(lmin + 2, b.n):
        if not (vol_ok[t] and np.isfinite(buf[t])) or not s.c[t] > np.max(s.h[t - lmin:t]) + buf[t]:
            continue                                                   # cheap filter: any flag's high is at least this
        for length in range(lmin, min(lmax, t - 2) + 1):
            w0, top_bar = t - length, t - length - 1
            hw, lw = s.h[w0:t], s.l[w0:t]
            level, top = float(np.max(hw)), float(s.h[top_bar])
            if not s.c[t] > level + buf[t] or level > top:
                continue
            j0 = max(0, top_bar - pole_n)
            start = j0 + int(np.argmin(s.l[j0:top_bar + 1]))
            pole = top - float(s.l[start])
            a = b.atr[start]
            if not (np.isfinite(a) and a > 0 and pole >= pole_k * a and top >= np.max(s.h[start:top_bar + 1])):
                continue
            if top - float(np.min(lw)) > retrace * pole:
                continue
            if pennant:
                half = length // 2
                if not np.ptp(np.r_[hw[half:], lw[half:]]) < np.ptp(np.r_[hw[:half], lw[:half]]):
                    continue
            _fill(out, t, level, length, float(np.min(lw)), "PENNANT" if pennant else "FLAG", move=level + pole)
            break


def b6(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    vol_ok = b.vol_ok(float(p["vol_mult"]))
    buf = float(p["min_break_atr"]) * b.atr_prev
    if p["pattern"] == "triangle":
        _triangle(b, s, p, out, vol_ok, buf)
    else:
        _flag(b, s, p, out, vol_ok, buf)


RULES = {"B5": b5, "B6": b6}

__all__ = ["SCREENS", "RULES", "Line", "Lines", "swing_positions", "ref_ranges", "b5", "b6", "channel_line"]
