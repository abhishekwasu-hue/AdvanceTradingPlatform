"""Trendlines (falling resistance over swing highs, rising support over swing lows) and their channels, from closed bars.

Ported from Trade@1e6c9ac: price_action/trendlines.py (`detect_trendlines`, without the 30m -> 60m resampler and the
touch audit), opportunity_engine/detectors/patterns.py (`Trendline`, `find_trendline`) and opportunity_engine/measures.py
(`ref_range`). Logic unchanged; parity with Trade's own output on the frozen fixtures: tests/test_ch3b_trade_parity.py.
Swings: `app.price_action.swings.swing_indices` (the port of Trade signals.find_swings).

Rule: A < B confirmed swings, B - A >= min_gap, 0 < slope x sign <= max_slope_k x ref_range, no close beyond the line from
A to the last closed bar, at least `min_touches` swings on the line (tolerance 0.25 x ref_range). The newest B, and for it
the oldest valid A (the longest line). A swing counts only once `order` bars after it have closed (no look-ahead).

`channel_of` (ATP, not in Trade): the parallel line through the farthest opposite extreme between the two anchors - the
line's own `between_ext` region (Trade keeps that extreme as a target hint) - so the channel is drawn from the same line.
Reference only - nothing here places an order.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.price_action.swings import swing_indices

KINDS = (("resistance", -1, "DESC_RESISTANCE"), ("support", +1, "ASC_SUPPORT"))


def ref_range(df: pd.DataFrame, n: int = 20, min_bars: int = 10) -> float:
    """The median (high - low) of the last n closed FULL bars (a `bar_is_full` False bar is left out); NaN below min_bars."""
    if df is None or len(df) == 0:
        return float("nan")
    d = df
    if "bar_is_full" in d.columns:
        d = d[d["bar_is_full"].astype(bool)]
    if len(d) == 0:
        return float("nan")
    arr = np.asarray((d["high"] - d["low"]).values[-(4 * int(n)):], dtype="float64")
    arr = arr[np.isfinite(arr)]
    if len(arr) < int(min_bars):
        return float("nan")
    return float(np.median(arr[-int(n):]))


@dataclass
class Trendline:
    side: str                   # "support" (over swing lows; a close below breaks it) | "resistance" (over swing highs)
    m: float                    # slope (per bar)
    b: float                    # the value at x = 0 (in the given frame's index)
    a: int                      # first anchor swing
    b_idx: int                  # second anchor swing
    touches: int                # swings touching the line, anchors included
    after_b: int                # touches after B
    between_ext: float          # the opposite extreme between A and B (support: the highest high) - a target hint

    def at(self, x: float) -> float:
        return self.m * x + self.b


def find_trendline(df: pd.DataFrame, rr: float, side: str, slope_sign: int, order: int = 3, min_gap: int = 5, max_slope_k: float = 0.5,
                   tol: Optional[float] = None, min_touches: int = 2) -> Optional[Trendline]:
    """df = closed bars (high / low / close). `side` support | resistance, `slope_sign` +1 (rising) / -1 (falling)."""
    if rr is None or not np.isfinite(rr) or rr <= 0 or len(df) < 2 * order + min_gap + 2:
        return None
    n = len(df)
    hi, lo, cl = df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float)
    sh, sl = swing_indices(df, window=order)
    idx = [i for i in (sl if side == "support" else sh) if i <= n - 1 - order]
    y = lo if side == "support" else hi
    tol = 0.25 * rr if tol is None else tol
    for bi in range(len(idx) - 1, 0, -1):
        B = idx[bi]
        for A in idx[:bi]:
            if B - A < min_gap:
                break
            m = (y[B] - y[A]) / (B - A)
            if not (0 < m * slope_sign <= max_slope_k * rr):
                continue
            b0 = y[A] - m * A
            xs = np.arange(A, n)
            line = m * xs + b0
            broken = (cl[A:] < line - 1e-9).any() if side == "support" else (cl[A:] > line + 1e-9).any()
            if broken:
                continue
            pts = [i for i in idx if i >= A and abs(y[i] - (m * i + b0)) <= tol]
            if len(pts) < min_touches:
                continue
            ext = float(hi[A:B + 1].max()) if side == "support" else float(lo[A:B + 1].min())
            return Trendline(side=side, m=float(m), b=float(b0), a=int(A), b_idx=int(B), touches=len(pts), after_b=sum(1 for i in pts if i > B),
                             between_ext=ext)
    return None


def detect_trendlines(df: pd.DataFrame, min_touches: int = 2, order: int = 3, min_gap: int = 5, max_slope_k: float = 0.5) -> List[Dict[str, Any]]:
    """df = closed bars (oldest first). The newest valid line of each kind (falling resistance, rising support):
    [{kind, side, slope, a_ts, a_price, b_ts, b_price, end_ts, end_price, next_price, touches, touch_points, rr, line}]."""
    if df is None or len(df) < 2 * order + min_gap + 2:
        return []
    df = df.reset_index(drop=True)
    rr = ref_range(df)
    if not np.isfinite(rr) or rr <= 0:
        return []
    sh, sl = swing_indices(df, window=order)
    n, out = len(df), []
    for side, sign, kind in KINDS:
        tl = find_trendline(df, rr, side, sign, order=order, min_gap=min_gap, max_slope_k=max_slope_k, min_touches=min_touches)
        if tl is None:
            continue
        y = df["high" if side == "resistance" else "low"].to_numpy(float)
        idx = [i for i in (sh if side == "resistance" else sl) if tl.a <= i <= n - 1 - order]
        pts = [i for i in idx if abs(y[i] - tl.at(i)) <= 0.25 * rr]
        out.append({
            "kind": kind, "side": side, "slope": round(tl.m, 4), "rr": rr, "line": tl, "touches": len(pts),
            "a_ts": pd.Timestamp(df["timestamp"].iloc[tl.a]), "a_price": round(tl.at(tl.a), 2),
            "b_ts": pd.Timestamp(df["timestamp"].iloc[tl.b_idx]), "b_price": round(tl.at(tl.b_idx), 2),
            "end_ts": pd.Timestamp(df["timestamp"].iloc[n - 1]), "end_price": round(tl.at(n - 1), 2),
            "next_price": round(tl.at(n), 2),
            "touch_points": [(pd.Timestamp(df["timestamp"].iloc[i]), round(float(y[i]), 2)) for i in pts],
        })
    return out


def channel_of(tl: Trendline, df: pd.DataFrame) -> Dict[str, Any]:
    """The parallel line through the farthest opposite extreme between the anchors: for a support line the high furthest
    above it in [A, B], for a resistance line the low furthest below. {offset, at_bar} - the channel line is
    tl.at(x) + offset."""
    hi, lo = df["high"].to_numpy(float), df["low"].to_numpy(float)
    xs = np.arange(tl.a, tl.b_idx + 1)
    line = tl.m * xs + tl.b
    gaps = (hi[tl.a:tl.b_idx + 1] - line) if tl.side == "support" else (lo[tl.a:tl.b_idx + 1] - line)
    j = int(np.argmax(gaps)) if tl.side == "support" else int(np.argmin(gaps))
    return {"offset": float(gaps[j]), "at_bar": int(tl.a + j)}
