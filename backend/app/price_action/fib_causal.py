"""M99 B1-b: Fibonacci levels and the nearest price cluster per bar, from CONFIRMED swings (flag `m99_primitives`).

Swings: the causal swing engine (app/price_action/causal_swings.py `degree_pivots`, the price-action settings). A pivot
counts only from the bar that CONFIRMED it (price came back from the extreme by the degree's threshold) - never from the
extreme bar itself - so a value at bar t uses the bars up to t only, and cutting the bars at t gives the same value.

With the confirmed pivots known at bar t, oldest to newest ..., P3, P2, P1 (P1 the latest):

* "retrace": the retracement of the last swing P2 -> P1 (fib_levels.retracement, A = P2, B = P1);
* "extend":  the extension of the swing before it, P3 -> P2, laid from P1 (A = P3, B = P2, C = P1);
* "golden":  the golden projection from P2 through P1 (start = P2, mid = P1: the end of the move from P2 when P1 sits at
  the given share of it).

The nearest cluster at bar t is read over the level set known at t (price_clusters.find_clusters): the retracements of
the last swing and of the swing before it (P3 -> P2), the extensions of P3 -> P2 from P1, the golden projections from P2
through P1 (each with the caller's ratios; default the fib_levels reference lists) and the two latest swing prices (P1,
P2). The ATR of "atr" mode is causal_swings.atr with the settings' atr_len, as known at t.

NaN where it does not apply (too few confirmed pivots, no cluster, no ATR yet). Evidence only - no target, no order.
"""
from __future__ import annotations

from bisect import bisect_right
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from app.price_action import causal_swings as cs
from app.price_action import fib_levels as fl
from app.price_action import price_clusters as pc

FIB_KINDS = ("retrace", "extend", "golden")
SOURCE_FIB = "fib"
SOURCE_SWING = "swing"


def _settings(settings: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    from app.price_action import pa_settings
    return dict(settings) if settings is not None else pa_settings.settings()


def swing_frame(bars: pd.DataFrame) -> pd.DataFrame:
    """The swing engine's frame of `bars` (as the screener's swing functions build it: OHLC, the index as both stamps)."""
    frame = pd.DataFrame({k: bars[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
    frame["timestamp"] = frame["bar_end"] = bars.index
    return frame


def confirmed_pivots(bars: pd.DataFrame, degree: int, settings: Optional[Mapping[str, Any]] = None) -> List[cs.Pivot]:
    """The degree's confirmed pivots in confirmation order."""
    s = _settings(settings)
    if not 0 <= degree < len(s["swing_atr_mult"]):
        raise ValueError(f"swing degree {degree} is not one of 0-{len(s['swing_atr_mult']) - 1}")
    return [p for p in cs.degree_pivots(swing_frame(bars), degree, s) if p.confirmed_idx is not None]


def known_counts(pivots: Sequence[cs.Pivot], n: int) -> np.ndarray:
    """On every bar t of n: how many of `pivots` (confirmation order) are confirmed by t."""
    confirm = [int(p.confirmed_idx) for p in pivots]                       # type: ignore[arg-type]
    return np.array([bisect_right(confirm, t) for t in range(n)], dtype=int)


def fib_value(known: Sequence[cs.Pivot], kind: str, ratio: float) -> float:
    """The level of `kind` at `ratio` from the known pivots (the latest last); NaN when too few are known."""
    if kind not in FIB_KINDS:
        raise ValueError(f"fib kind {kind!r} is not one of {', '.join(FIB_KINDS)}")
    if kind == "retrace" and len(known) >= 2:
        return fl.retracement(known[-2].price, known[-1].price, ratio)
    if kind == "extend" and len(known) >= 3:
        return fl.extension(known[-3].price, known[-2].price, known[-1].price, ratio)
    if kind == "golden" and len(known) >= 2 and ratio > 0:
        return fl.golden_projection(known[-2].price, known[-1].price, ratio)
    return float("nan")


def level_set(known: Sequence[cs.Pivot], retrace_ratios: Optional[Sequence[float]] = None,
              extend_ratios: Optional[Sequence[float]] = None, golden_ratios: Optional[Sequence[float]] = None) -> List[pc.PriceLevel]:
    """The labelled levels a cluster is read over, from the known pivots (the latest last)."""
    out: List[pc.PriceLevel] = []
    if len(known) >= 2:
        p2, p1 = known[-2].price, known[-1].price
        out += [pc.PriceLevel(x.price, x.label, SOURCE_FIB) for x in fl.retracement_levels(p2, p1, retrace_ratios, "last swing")]
        out += [pc.PriceLevel(x.price, x.label, SOURCE_FIB) for x in fl.golden_projection_levels(p2, p1, golden_ratios, "last swing")]
    if len(known) >= 3:
        p3, p2, p1 = known[-3].price, known[-2].price, known[-1].price
        out += [pc.PriceLevel(x.price, x.label, SOURCE_FIB) for x in fl.retracement_levels(p3, p2, retrace_ratios, "swing before")]
        out += [pc.PriceLevel(x.price, x.label, SOURCE_FIB) for x in fl.extension_levels(p3, p2, p1, extend_ratios, "swing before")]
    for p in known[-2:]:
        out.append(pc.PriceLevel(p.price, "swing high" if p.kind == "H" else "swing low", SOURCE_SWING))
    return out


def fib_series(bars: pd.DataFrame, kind: str, ratio: float, degree: int = 0,
               settings: Optional[Mapping[str, Any]] = None) -> np.ndarray:
    """The `kind` level at `ratio` on every bar, from the pivots confirmed by that bar."""
    pivots = confirmed_pivots(bars, degree, settings)
    counts = known_counts(pivots, len(bars))
    out = np.full(len(bars), np.nan)
    memo: Dict[int, float] = {}
    for t, m in enumerate(counts):
        if m not in memo:
            memo[m] = fib_value(pivots[:m], kind, ratio)
        out[t] = memo[m]
    return out


def cluster_series(bars: pd.DataFrame, band: float, mode: str = pc.PCT, degree: int = 0,
                   settings: Optional[Mapping[str, Any]] = None, min_members: int = 2,
                   retrace_ratios: Optional[Sequence[float]] = None, extend_ratios: Optional[Sequence[float]] = None,
                   golden_ratios: Optional[Sequence[float]] = None) -> Dict[str, np.ndarray]:
    """The cluster nearest the close on every bar, over the level set known at that bar:
    {"centre", "low", "high", "count"} (NaN / 0 where there is none) and "labels" (a tuple per bar, empty where none)."""
    if mode not in pc.BAND_MODES:
        raise ValueError(f"band mode {mode!r} is not one of {', '.join(pc.BAND_MODES)}")
    s = _settings(settings)
    pivots = confirmed_pivots(bars, degree, s)
    counts = known_counts(pivots, len(bars))
    n = len(bars)
    close = bars["close"].astype(float).to_numpy()
    atr = cs.atr(swing_frame(bars), int(s["atr_len"])) if mode == pc.ATR else np.full(n, np.nan)
    centre, low, high = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    count = np.zeros(n, dtype=int)
    labels = np.empty(n, dtype=object)
    labels[:] = [()] * n
    sets: Dict[int, List[pc.PriceLevel]] = {}
    pct_clusters: Dict[int, List[pc.Cluster]] = {}
    for t in range(n):
        m = int(counts[t])
        if m < 2:
            continue
        if m not in sets:
            sets[m] = level_set(pivots[:m], retrace_ratios, extend_ratios, golden_ratios)
        if mode == pc.PCT:
            if m not in pct_clusters:
                pct_clusters[m] = pc.find_clusters(sets[m], band, pc.PCT, min_members=min_members)
            clusters = pct_clusters[m]
        else:
            clusters = pc.find_clusters(sets[m], band, pc.ATR, atr=float(atr[t]), min_members=min_members)
        c = pc.nearest_cluster(clusters, float(close[t]))
        if c is None:
            continue
        centre[t], low[t], high[t], count[t], labels[t] = c.centre, c.low, c.high, c.count, c.labels
    return {"centre": centre, "low": low, "high": high, "count": count, "labels": labels}


def distance_pct(close: np.ndarray, level: np.ndarray) -> np.ndarray:
    """(close - level) / level x 100; NaN where the level is missing or zero."""
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (close - level) / np.abs(level) * 100.0
    out[~np.isfinite(out)] = np.nan
    return out


__all__ = ["FIB_KINDS", "SOURCE_FIB", "SOURCE_SWING", "swing_frame", "confirmed_pivots", "known_counts", "fib_value", "level_set",
           "fib_series", "cluster_series", "distance_pct"]
