"""Support / resistance levels - classical (clustered major swings) and dynamic (pivot-channel, Pine "Dynamic v2").

Ported from Trade@eafc872: signals.py (find_swings, filter_major_swings, find_support_resistance_levels) and
sr_dynamic.py (find_pivots_indexed, compute_dynamic_sr). Swing detection reuses `app.price_action.swings.swing_indices`.
Pure functions; a level computed on bars [0, t] uses only those bars (a pivot is confirmed `prd` bars after it).

Output shape (both): {"support": [{"level", "touches"}, ...], "resistance": [...]} - support below the price, resistance
above it, strongest first.
"""
import math
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.price_action.swings import swing_indices
from app.price_action.zones.settings import ZoneSettings


def _s(settings: Optional[ZoneSettings]) -> ZoneSettings:
    return settings if settings is not None else ZoneSettings()


def filter_major_swings(df: pd.DataFrame, swing_high_idx: List[int], swing_low_idx: List[int],
                        min_move_pct: float = 0.5) -> Tuple[List[int], List[int]]:
    """Keep only major swings (a ZigZag-style magnitude filter over fractal swings): a swing counts when it is at least
    `min_move_pct` % away from the last confirmed major swing of the other kind; consecutive same-kind swings keep only the
    more extreme one; a smaller move is noise and is dropped. Returns subsets of the given indices."""
    combined = sorted([(i, "high") for i in swing_high_idx] + [(i, "low") for i in swing_low_idx])
    if not combined:
        return [], []
    highs, lows = df["high"].to_numpy(float), df["low"].to_numpy(float)

    def price_at(idx: int, typ: str) -> float:
        return float(highs[idx] if typ == "high" else lows[idx])

    major_high: List[int] = []
    major_low: List[int] = []
    last_idx, last_type = combined[0]
    last_price = price_at(last_idx, last_type)
    (major_high if last_type == "high" else major_low).append(last_idx)
    for idx, typ in combined[1:]:
        price = price_at(idx, typ)
        if typ == last_type:
            if (typ == "high" and price > last_price) or (typ == "low" and price < last_price):
                (major_high if typ == "high" else major_low)[-1] = idx
                last_idx, last_price = idx, price
            continue
        move_pct = abs(price - last_price) / last_price * 100 if last_price else 0
        if move_pct >= min_move_pct:
            (major_high if typ == "high" else major_low).append(idx)
            last_idx, last_type, last_price = idx, typ, price
    return major_high, major_low


def cluster_prices(prices: List[float], tolerance_pct: float) -> List[Dict[str, Any]]:
    """Sorted chain clustering: a price joins the current cluster when within tolerance_pct % of the cluster's last
    price. Each cluster -> {"level": mean rounded to 2 decimals, "touches": size}."""
    if not prices:
        return []
    ordered = sorted(prices)
    clusters = [[ordered[0]]]
    for p in ordered[1:]:
        if abs(p - clusters[-1][-1]) / clusters[-1][-1] * 100 <= tolerance_pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [{"level": round(sum(c) / len(c), 2), "touches": len(c)} for c in clusters]


def find_support_resistance_levels(df: pd.DataFrame, order: Optional[int] = None, cluster_tolerance_pct: Optional[float] = None,
                                   top_n: Optional[int] = None, settings: Optional[ZoneSettings] = None) -> Dict[str, Any]:
    """All swing highs / lows clustered within the tolerance, ranked by touch count (then nearest to the price): the
    strongest `top_n` resistances above the last close and supports below it. Arguments left as None come from settings
    (sr_swing_order, sr_cluster_tolerance_pct, sr_top_n). Trade's own function defaults to top_n=3."""
    s = _s(settings)
    order = s.sr_swing_order if order is None else order
    tol = s.sr_cluster_tolerance_pct if cluster_tolerance_pct is None else cluster_tolerance_pct
    top_n = s.sr_top_n if top_n is None else top_n
    if df.empty:
        return {"support": [], "resistance": [], "current_price": None}
    sh_idx, sl_idx = swing_indices(df, order)
    current_price = float(df["close"].iloc[-1])
    highs, lows = df["high"].to_numpy(float), df["low"].to_numpy(float)
    resistance = sorted([c for c in cluster_prices([float(highs[i]) for i in sh_idx], tol) if c["level"] > current_price],
                        key=lambda c: (-c["touches"], c["level"] - current_price))[:top_n]
    support = sorted([c for c in cluster_prices([float(lows[i]) for i in sl_idx], tol) if c["level"] < current_price],
                     key=lambda c: (-c["touches"], current_price - c["level"]))[:top_n]
    return {"support": support, "resistance": resistance, "current_price": current_price}


def find_pivots_indexed(df: pd.DataFrame, prd: int = 10) -> List[Tuple[int, float]]:
    """Pine ta.pivothigh / ta.pivotlow: bar i is a pivot when it is the max (min) of [i - prd, i + prd]. Like Pine's
    `ph ? ph : pl`, a bar that is both keeps only the high. Returns [(bar index, value)] oldest first; the pivot is
    confirmed on bar index + prd."""
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    pivots: List[Tuple[int, float]] = []
    for i in range(prd, len(df) - prd):
        if highs[i] == highs[i - prd:i + prd + 1].max():
            pivots.append((i, highs[i]))
        elif lows[i] == lows[i - prd:i + prd + 1].min():
            pivots.append((i, lows[i]))
    return pivots


def compute_dynamic_sr(df: pd.DataFrame, settings: Optional[ZoneSettings] = None, current_price: Optional[float] = None) -> Dict[str, Any]:
    """The Pine "Support Resistance - Dynamic v2" (LonesomeTheBlue, MPL-2.0) levels, exactly as Trade computes them:
    on the last pivot-event bar (last pivot + prd) take the latest dyn_maxnumpp pivots, the channel width
    dyn_channel_w_pct % of that bar's dyn_channel_lookback-bar range, grow a channel around each pivot, keep the
    dyn_maxnumsr strongest non-overlapping channels with at least dyn_min_strength pivots. Level = channel mid (rounded to
    dyn_mintick when set, else 2 decimals). At or above the price -> resistance, below -> support."""
    s = _s(settings)
    prd = s.dyn_prd
    empty: Dict[str, Any] = {"support": [], "resistance": []}
    if len(df) < 2 * prd + 10:
        return empty
    indexed = find_pivots_indexed(df, prd)
    if len(indexed) < s.dyn_min_strength:
        return empty
    pivotvals = [float(v) for _, v in reversed(indexed)][:s.dyn_maxnumpp]          # newest first, like Pine's unshift
    event_bar = indexed[-1][0] + prd
    window = df.iloc[max(0, event_bar - (s.dyn_channel_lookback - 1)):event_bar + 1]
    cwidth = (window["high"].max() - window["low"].min()) * s.dyn_channel_w_pct / 100

    def sr_vals(ind: int) -> Tuple[float, float, int]:
        lo = hi = pivotvals[ind]
        numpp = 0
        for cpp in pivotvals:
            wdth = (hi - cpp) if cpp <= lo else (cpp - lo)
            if wdth <= cwidth:
                if cpp <= hi:
                    lo = min(lo, cpp)
                else:
                    hi = max(hi, cpp)
                numpp += 1
        return hi, lo, numpp

    sr_up: List[float] = []
    sr_dn: List[float] = []
    sr_strength: List[int] = []

    def find_loc(strength: int) -> int:
        ret = len(sr_strength)
        for i in range(len(sr_strength) - 1, -1, -1):
            if strength <= sr_strength[i]:
                break
            ret = i
        return ret

    def check_sr(hi: float, lo: float, strength: int) -> bool:
        for i in range(len(sr_up)):
            if (lo <= sr_up[i] <= hi) or (lo <= sr_dn[i] <= hi):
                if strength >= sr_strength[i]:
                    sr_strength.pop(i)
                    sr_up.pop(i)
                    sr_dn.pop(i)
                    return True
                return False
        return True

    for ind in range(len(pivotvals)):
        hi, lo, strength = sr_vals(ind)
        if check_sr(hi, lo, strength):
            loc = find_loc(strength)
            if loc < s.dyn_maxnumsr and strength >= s.dyn_min_strength:
                sr_strength.insert(loc, strength)
                sr_up.insert(loc, hi)
                sr_dn.insert(loc, lo)
                if len(sr_strength) > s.dyn_maxnumsr:
                    sr_strength.pop()
                    sr_up.pop()
                    sr_dn.pop()

    price = float(df["close"].iloc[-1]) if current_price is None else float(current_price)
    result: Dict[str, Any] = {"support": [], "resistance": []}
    for up, dn, strength in zip(sr_up, sr_dn, sr_strength):
        mid_raw = (up + dn) / 2
        mid = round(math.floor(mid_raw / s.dyn_mintick + 0.5) * s.dyn_mintick, 2) if s.dyn_mintick else round(mid_raw, 2)
        result["resistance" if mid >= price else "support"].append({"level": mid, "touches": int(strength)})
    result["support"].sort(key=lambda x: -x["touches"])
    result["resistance"].sort(key=lambda x: -x["touches"])
    return result
