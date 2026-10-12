"""U6 BK3: B3 volatility contraction (VCP-style) + breakout, and its Watchlist variant, as a causal bar-by-bar series
(spec docs/specs/ATP_BREAKOUT_SCREENERS_ADDENDUM.md B3; behind `screener_breakouts` AND `screener_breakouts_bk3`).

The base is read from its own swings: a causal zigzag over the highs and lows, where a swing high is confirmed on the
bar whose low has fallen `vcp_pivot_atr` x ATR (ATR of the bars before that bar) below it (a swing low: the mirror) - the
swing size follows the market's own noise, the contraction depths are then measured in %. A contraction is a
confirmed swing high and the confirmed swing low after it; its depth is the drop in % of the high. At bar t the base is the
run of successive contractions that ends with the last confirmed swing low, walked back while
  * each later contraction is shallower than `vcp_ratio` x the one before it (spec: "each smaller than the previous"),
  * no later high is above an earlier one (the left side of the base is its top),
  * the first high is at most `vcp_max_bars` bars back (an older contraction ends the walk - it is not part of this base).
A contraction that joins the run but is deeper than `vcp_max_depth_pct` rejects the base (its left side is too deep).
The base needs `vcp_min_contractions` contractions and `vcp_min_bars` bars from its first high. The pivot is the last
contraction's high. Optional rules (all editable, app/screener/breakouts/params.py):
  * volume dry-up (`vcp_vol_dryup`): the last contraction's mean volume is below the first's;
  * the trend template (`tt_filter`, Minervini): close > fast SMA > mid SMA > slow SMA, the slow SMA above its value
    `tt_slow_rising_n` bars ago, the close within `tt_near_high_pct` % of the year's high and at least `tt_above_low_pct` %
    above the year's low (`tt_year_bars`). The RS rating is not part of it (cross-sectional - open question BK3-1).

Signals (`vcp_mode`):
  * breakout: the first close above the pivot by `min_break_atr` x ATR (ATR of the bars before) on volume >= `vol_mult` x
    average, with every rule holding on swings confirmed by the bar BEFORE (the break bar never confirms its own base);
    one per leg (screens.one_per_leg).
  * watchlist: on every bar where every rule holds on the swings confirmed by that bar, the close is at or below the pivot
    and at most `vcp_ready_pct` % under it, and no close since the last swing low went above the pivot ("ready, not yet
    broken"). A state, listed on every bar it holds - not deduplicated per leg.

Every value at bar j uses bars 0..j only. The down side is the up rule on mirrored prices (a mirrored base under a
falling trend), as for every breakout screen.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - typing only (screens.py imports this module)
    from app.screener.breakouts.screens import Bars, _Side

SCREENS: Dict[str, str] = {"B3": "Volatility contraction (VCP-style) breakout"}
TIMEFRAMES: Tuple[str, ...] = ("1d",)       # the trend template is a daily rule (50 / 150 / 200 sessions, the 52-week range)


@dataclass(frozen=True)
class Pivot:
    confirmed: int          # the bar on whose close the swing became known
    bar: int                # the bar of the extreme
    kind: str               # "H" / "L"
    price: float            # in the side's orientation


def zigzag(h: np.ndarray, lo: np.ndarray, size: np.ndarray) -> List[Pivot]:
    """Causal zigzag on (oriented) highs and lows, in confirmation order (alternating H / L): a reversal of at least
    size[t] (a price distance known at bar t; NaN or 0: no reversal is read on that bar). A reversal on the bar that also
    made (or tied) the new extreme is not counted (the order inside a bar is unknown); an equal extreme moves to the later
    bar. When a swing is confirmed at bar t, the new opposite extreme is the most extreme bar AFTER the pivot up to t (as
    app.price_action.causal_swings.zigzag_pivots re-scans) - never just bar t, which would skip a deeper low / higher high
    between the pivot and t (BK3 review M1). With a positive size the pivots equal zigzag_pivots' exactly, ties included
    (tested on tick-rounded prices)."""
    out: List[Pivot] = []
    n = len(h)
    finite = np.flatnonzero(np.isfinite(h) & np.isfinite(lo))
    if not len(finite):
        return out
    start = int(finite[0])                      # seeded from the first bar with prices (a NaN bar 0 would stall it)
    mode = 0                                    # 0: no swing yet, 1: tracking a high, -1: tracking a low

    def lowest_after(i: int, t: int) -> Tuple[float, int]:
        seg = lo[i + 1:t + 1]
        j = int(np.nanargmin(seg))              # bar t is finite, so the segment has a value
        return float(seg[j]), i + 1 + j

    def highest_after(i: int, t: int) -> Tuple[float, int]:
        seg = h[i + 1:t + 1]
        j = int(np.nanargmax(seg))
        return float(seg[j]), i + 1 + j

    hi, hi_i, lw, lw_i = float(h[start]), start, float(lo[start]), start
    for t in range(start + 1, n):
        ht, lt, thr = float(h[t]), float(lo[t]), float(size[t])
        if not (np.isfinite(ht) and np.isfinite(lt)):
            continue
        # an equal high / low moves the extreme to the later bar, so a reversal is never read on a bar that ties it
        # (as zigzag_pivots: `>=` / `<=` tracking, distances measured as extreme - price)
        if mode >= 0 and ht >= hi:
            hi, hi_i = ht, t
        if mode <= 0 and lt <= lw:
            lw, lw_i = lt, t
        if not np.isfinite(thr) or thr <= 0:                    # the yardstick is not known yet: extremes only
            continue
        if mode >= 0 and hi_i < t and hi - lt >= thr:
            out.append(Pivot(t, hi_i, "H", hi))
            mode = -1
            lw, lw_i = lowest_after(hi_i, t)
        elif mode <= 0 and lw_i < t and ht - lw >= thr:
            out.append(Pivot(t, lw_i, "L", lw))
            mode = 1
            hi, hi_i = highest_after(lw_i, t)
    return out


@dataclass(frozen=True)
class Base:
    contractions: Tuple[Tuple[Pivot, Pivot, float], ...]     # (high, low, depth %) oldest first

    @property
    def first_high(self) -> Pivot:
        return self.contractions[0][0]

    @property
    def pivot(self) -> Pivot:
        return self.contractions[-1][0]

    @property
    def last_low(self) -> Pivot:
        return self.contractions[-1][1]


def base_at(piv: List[Pivot], k: int, t: int, p: Dict[str, Any]) -> Optional[Base]:
    """The base on the first k pivots, as seen at bar t (None when the rules do not hold)."""
    if k < 2 or piv[k - 1].kind != "L":
        return None
    ratio, max_depth = float(p["vcp_ratio"]), float(p["vcp_max_depth_pct"])
    earliest = t - int(p["vcp_max_bars"])
    chain: List[Tuple[Pivot, Pivot, float]] = []
    i = k - 1
    while i >= 1 and piv[i].kind == "L" and piv[i - 1].kind == "H":
        hi, lw = piv[i - 1], piv[i]
        if hi.bar < earliest or hi.price == 0:
            break
        depth = (hi.price - lw.price) / abs(hi.price) * 100.0
        if chain:
            later_hi, _, later_depth = chain[-1]
            if not later_depth < ratio * depth or later_hi.price > hi.price:
                break
        if depth > max_depth:                                   # the base's own left side is too deep: no VCP
            return None
        chain.append((hi, lw, depth))
        i -= 2
    if len(chain) < int(p["vcp_min_contractions"]):
        return None
    base = Base(tuple(reversed(chain)))
    if t - base.first_high.bar < int(p["vcp_min_bars"]):
        return None
    return base


def trend_template(s: "_Side", p: Dict[str, Any]) -> np.ndarray:
    """Minervini's trend template on every bar, in the side's orientation (NaN history: False)."""
    n = len(s.c)
    if not p["tt_filter"]:
        return np.ones(n, dtype=bool)
    c = pd.Series(s.c)

    def sma(k: int) -> np.ndarray:
        return c.rolling(k, min_periods=k).mean().to_numpy(float)

    fast, mid, slow = sma(int(p["tt_ma_fast"])), sma(int(p["tt_ma_mid"])), sma(int(p["tt_ma_slow"]))
    k = int(p["tt_slow_rising_n"])
    slow_then = np.full(n, np.nan)
    if k < n:
        slow_then[k:] = slow[:n - k]
    year = int(p["tt_year_bars"])
    hi = pd.Series(s.h).rolling(year, min_periods=year).max().to_numpy(float)
    lw = pd.Series(s.l).rolling(year, min_periods=year).min().to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        near = (hi - s.c) / np.abs(hi) * 100.0 <= float(p["tt_near_high_pct"])
        above = (s.c - lw) / np.abs(lw) * 100.0 >= float(p["tt_above_low_pct"])
        ok = (s.c > fast) & (fast > mid) & (mid > slow) & (slow > slow_then) & near & above
    return np.asarray(ok & np.isfinite(slow_then) & np.isfinite(hi), dtype=bool)


def _dry(v: np.ndarray, base: Base) -> bool:
    first_hi, first_lo, _ = base.contractions[0]
    last_hi, last_lo, _ = base.contractions[-1]
    a = v[first_hi.bar:first_lo.bar + 1]
    z = v[last_hi.bar:last_lo.bar + 1]
    if not len(a) or not len(z) or not np.isfinite(a).all() or not np.isfinite(z).all():
        return False                                            # no volume data: the rule cannot hold (indices: turn it off)
    return bool(z.mean() < a.mean())


def b3(b: "Bars", s: "_Side", p: Dict[str, Any], out: Dict[str, np.ndarray]) -> None:
    piv = zigzag(s.h, s.l, float(p["vcp_pivot_atr"]) * b.atr_prev)
    tt = trend_template(s, p)
    watch = p["vcp_mode"] == "watchlist"
    dryup = bool(p["vcp_vol_dryup"])
    ready = float(p["vcp_ready_pct"]) / 100.0
    vol_ok = b.vol_ok(float(p["vol_mult"]))
    buf = float(p["min_break_atr"]) * b.atr_prev
    k = 0
    for t in range(b.n):
        # breakout: swings confirmed by the bar before; watchlist: by this bar (nothing breaks on it)
        limit = t if watch else t - 1
        while k < len(piv) and piv[k].confirmed <= limit:
            k += 1
        if k < 2 or piv[k - 1].kind != "L" or not tt[t]:
            continue
        pivot_price = piv[k - 2].price                         # the last high before the last low: the would-be pivot
        c = float(s.c[t])
        if watch:
            if not (c <= pivot_price and c >= pivot_price - ready * abs(pivot_price)):
                continue
        elif not (np.isfinite(buf[t]) and c > pivot_price + buf[t] and vol_ok[t]):
            continue
        base = base_at(piv, k, t, p)
        if base is None or (dryup and not _dry(b.v, base)):
            continue
        since = s.c[base.last_low.bar + 1:t]
        if np.any(since > pivot_price):                         # broken before: not the first close above, not "ready"
            continue
        out["raw"][t] = True
        out["level"][t] = out["fail_level"][t] = pivot_price
        out["base_len"][t] = t - base.first_high.bar
        out["base_high"][t] = pivot_price
        out["base_low"][t] = base.last_low.price               # the last contraction's low (the stop reference for stop_mode base)
        out["event"][t] = f"VCP{len(base.contractions)}"


RULES = {"B3": b3}


__all__ = ["SCREENS", "TIMEFRAMES", "RULES", "Pivot", "Base", "zigzag", "base_at", "trend_template", "b3"]
