"""Zone detection - order blocks, demand / supply bases, unfilled overnight gaps, role by price and the first-touch event.

Ported from Trade@eafc872: market_zones.py (detect_order_blocks, detect_demand_supply_zones, compute_current_role,
is_zone_mitigated), signals.py (find_order_block, is_retesting_order_block) and mtf_pullback_strategy.py
(detect_gap, find_overnight_gaps). Pure functions on a bar frame with columns timestamp / open / high / low / close,
oldest first. Nothing here reads the database or the network.

Deliberate change (owner spec): Trade marks a zone FILLED (dead) the first time price comes back into it. ATP does not:
`first_touch_event` reports that same first contact as an event - TOUCH (a bar overlapped the zone) or GAP_THROUGH (price
jumped across a zone edge between one close and the next open), whichever came first - and the lifecycle
(`lifecycle.py`) turns it into fresh -> tested. A zone dies only on a structural break (Z7a-2).

No look-ahead: an order block / base is known on its impulse bar (`confirmed_at`), never earlier; the average range at a
bar uses that bar and the bars before it; the first-touch scan only reads the bars it is given.
"""
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.price_action.zones.settings import ZoneSettings

BULLISH_OB, BEARISH_OB = "BULLISH_OB", "BEARISH_OB"
DEMAND_ZONE, SUPPLY_ZONE = "DEMAND_ZONE", "SUPPLY_ZONE"
UP_GAP, DOWN_GAP = "UP_GAP", "DOWN_GAP"
ROLE_RESISTANCE, ROLE_SUPPORT = "RESISTANCE_SUPPLY", "SUPPORT_DEMAND"
TOUCH, GAP_THROUGH = "TOUCH", "GAP_THROUGH"


def _s(settings: Optional[ZoneSettings]) -> ZoneSettings:
    return settings if settings is not None else ZoneSettings()


def average_range(df: pd.DataFrame, window: int, min_periods: int) -> pd.Series:
    """Mean (high - low) of the last `window` bars including the current one; NaN until `min_periods` bars exist."""
    return (df["high"] - df["low"]).rolling(window, min_periods=min_periods).mean()


def impulse_flags(df: pd.DataFrame, settings: Optional[ZoneSettings] = None) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(impulsive up, impulsive down, average range) per bar. Impulsive = body >= impulse_mult x average range."""
    s = _s(settings)
    body = (df["close"] - df["open"]).abs()
    avg = average_range(df, s.avg_window, s.avg_min_periods)
    big = body >= s.impulse_mult * avg
    up = ((df["close"] > df["open"]) & big).to_numpy(bool)
    down = ((df["close"] < df["open"]) & big).to_numpy(bool)
    return up, down, avg.to_numpy(float)


def detect_order_blocks(df: pd.DataFrame, settings: Optional[ZoneSettings] = None) -> List[Dict[str, Any]]:
    """Order block = the last opposite-colour candle before an impulsive candle (scan starts at bar `avg_window`).
    Bullish: the last candle with close < open before an impulsive up candle; bearish: mirror. One block per impulse
    (two impulses can name the same candle - Trade keeps both, so does this).
    Returns [{"zone_type", "zone_low", "zone_high", "formed_at", "formed_bar", "confirmed_at", "confirmed_bar"}]."""
    s = _s(settings)
    up, down, _ = impulse_flags(df, s)
    o, c = df["open"].to_numpy(float), df["close"].to_numpy(float)
    h, l = df["high"].to_numpy(float), df["low"].to_numpy(float)
    ts = df["timestamp"]
    blocks: List[Dict[str, Any]] = []
    for i in range(s.avg_window, len(df)):
        if up[i]:
            j = i - 1
            while j >= 0 and c[j] >= o[j]:
                j -= 1
            kind = BULLISH_OB
        elif down[i]:
            j = i - 1
            while j >= 0 and c[j] <= o[j]:
                j -= 1
            kind = BEARISH_OB
        else:
            continue
        if j >= 0:
            blocks.append({"zone_type": kind, "zone_low": float(l[j]), "zone_high": float(h[j]), "formed_at": ts.iloc[j],
                           "formed_bar": j, "confirmed_at": ts.iloc[i], "confirmed_bar": i})
    return blocks


def detect_demand_supply_zones(df: pd.DataFrame, settings: Optional[ZoneSettings] = None) -> List[Dict[str, Any]]:
    """Demand / supply zone = the tight base right before an impulsive candle: up to `base_lookback` candles, walking
    back from the bar before the impulse while each range <= base_tightness_mult x the impulse bar's average range.
    Zone = base low .. base high; formed at the LAST base candle (Trade's fix - the first-touch check starts after the
    base, not inside it). Demand before an impulsive up candle, supply before an impulsive down candle."""
    s = _s(settings)
    up, down, avg = impulse_flags(df, s)
    h, l = df["high"].to_numpy(float), df["low"].to_numpy(float)
    rng = h - l
    ts = df["timestamp"]
    zones: List[Dict[str, Any]] = []
    for i in range(s.avg_window, len(df)):
        if not (up[i] or down[i]):
            continue
        base_start, count = i - 1, 0
        while base_start >= 0 and count < s.base_lookback:
            if rng[base_start] > s.base_tightness_mult * avg[i]:
                break
            base_start -= 1
            count += 1
        base_start += 1
        if base_start > i - 1:
            continue
        zones.append({"zone_type": DEMAND_ZONE if up[i] else SUPPLY_ZONE, "zone_low": float(l[base_start:i].min()),
                      "zone_high": float(h[base_start:i].max()), "formed_at": ts.iloc[i - 1], "formed_bar": i - 1,
                      "base_start_bar": base_start, "confirmed_at": ts.iloc[i], "confirmed_bar": i})
    return zones


def role_by_price(zone_low: float, zone_high: float, price: float) -> str:
    """Whatever the zone's origin, its role now comes from the price: zone mid above price -> RESISTANCE_SUPPLY,
    otherwise SUPPORT_DEMAND (Trade compute_current_role)."""
    return ROLE_RESISTANCE if (zone_low + zone_high) / 2 > price else ROLE_SUPPORT


def first_touch_event(zone_low: float, zone_high: float, bars_after: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """The first contact with the zone in `bars_after` (bars after the zone formed, oldest first), or None - whichever
    of these happens first:
      TOUCH        a bar's [low, high] overlaps [zone_low, zone_high];
      GAP_THROUGH  price jumped from one bar's close to the next bar's open across zone_low or zone_high.
    On the same bar a TOUCH wins (the bar itself traded in the zone). An event exists exactly where Trade's
    is_zone_mitigated returns True - Trade then kills the zone, ATP only moves it fresh -> tested. Without open / close
    columns only overlaps are checked (as in Trade)."""
    if bars_after is None or bars_after.empty:
        return None
    h, l = bars_after["high"].to_numpy(float), bars_after["low"].to_numpy(float)
    ts = bars_after["timestamp"] if "timestamp" in bars_after.columns else pd.Series(bars_after.index)
    hits = np.flatnonzero((h >= zone_low) & (l <= zone_high))
    touch = int(hits[0]) if len(hits) else None
    gap = None
    if "open" in bars_after.columns and "close" in bars_after.columns:
        o, c = bars_after["open"].to_numpy(float), bars_after["close"].to_numpy(float)
        lo, hi = np.minimum(c[:-1], o[1:]), np.maximum(c[:-1], o[1:])
        crossed = np.flatnonzero(((lo <= zone_low) & (zone_low <= hi)) | ((lo <= zone_high) & (zone_high <= hi)))
        gap = int(crossed[0]) + 1 if len(crossed) else None
    if touch is not None and (gap is None or touch <= gap):
        return {"event": TOUCH, "bar": touch, "time": ts.iloc[touch]}
    if gap is not None:
        return {"event": GAP_THROUGH, "bar": gap, "time": ts.iloc[gap]}
    return None


def detect_gap(prev_high: float, prev_low: float, curr_high: float, curr_low: float) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """UP_GAP / DOWN_GAP when two consecutive bars do not overlap at all; (kind, gap_low, gap_high) or (None, None, None)."""
    if curr_low > prev_high:
        return UP_GAP, prev_high, curr_low
    if curr_high < prev_low:
        return DOWN_GAP, curr_high, prev_low
    return None, None, None


def session_dates(ts: pd.Series, tz: str) -> np.ndarray:
    """Calendar date of each bar in the exchange's time zone (tz-aware stamps are converted; naive ones are taken as-is)."""
    t = pd.to_datetime(ts)
    if getattr(t.dt, "tz", None) is not None:
        t = t.dt.tz_convert(tz)
    return t.dt.date.to_numpy()


def find_overnight_gaps(df: pd.DataFrame, settings: Optional[ZoneSettings] = None) -> List[Dict[str, Any]]:
    """True overnight gaps only: the last bar of one session vs the first bar of the next, no overlap, height >=
    gap_min_pct % of the gap low. Returns [{"zone_type", "zone_low", "zone_high", "formed_at", "formed_bar"}]."""
    s = _s(settings)
    if df is None or len(df) < 2:
        return []
    dates = session_dates(df["timestamp"], s.session_timezone)
    h, l = df["high"].to_numpy(float), df["low"].to_numpy(float)
    ts = df["timestamp"]
    starts = np.flatnonzero(np.r_[True, dates[1:] != dates[:-1]])
    gaps: List[Dict[str, Any]] = []
    for k in range(1, len(starts)):
        first, prev_last = int(starts[k]), int(starts[k]) - 1
        kind, glo, ghi = detect_gap(h[prev_last], l[prev_last], h[first], l[first])
        if kind and glo is not None and ghi is not None and (ghi - glo) >= glo * s.gap_min_pct / 100:
            gaps.append({"zone_type": kind, "zone_low": float(glo), "zone_high": float(ghi), "formed_at": ts.iloc[first],
                         "formed_bar": first, "confirmed_at": ts.iloc[first], "confirmed_bar": first})
    return gaps


def find_order_block(df: pd.DataFrame, direction: str, settings: Optional[ZoneSettings] = None) -> Optional[Dict[str, Any]]:
    """The most recent order block for `direction` (BULLISH / BEARISH) whose impulse is already complete inside `df`:
    the last bearish (bullish) candle followed within ob_impulse_lookforward bars by a move of at least
    ob_impulse_min_move_pct % from its close; searched over the last ob_search_lookback bars (Trade signals.find_order_block)."""
    s = _s(settings)
    n, fwd = len(df), s.ob_impulse_lookforward
    if n < fwd + 2:
        return None
    o, c = df["open"].to_numpy(float), df["close"].to_numpy(float)
    h, l = df["high"].to_numpy(float), df["low"].to_numpy(float)
    search_start = max(0, n - s.ob_search_lookback - fwd)
    for i in range(n - fwd - 1, search_start - 1, -1):
        if direction == "BULLISH" and c[i] < o[i]:
            move = (h[i + 1:i + 1 + fwd].max() - c[i]) / c[i] * 100
        elif direction == "BEARISH" and c[i] > o[i]:
            move = (c[i] - l[i + 1:i + 1 + fwd].min()) / c[i] * 100
        else:
            continue
        if move >= s.ob_impulse_min_move_pct:
            return {"ob_high": float(h[i]), "ob_low": float(l[i]), "ob_index": i}
    return None


def is_retesting_order_block(df: pd.DataFrame, ob: Optional[Dict[str, Any]], direction: str,
                             settings: Optional[ZoneSettings] = None) -> bool:
    """Is the last bar back at the order block (within ob_retest_tolerance_pct % of the block height)?"""
    s = _s(settings)
    if ob is None:
        return False
    last_low, last_high = float(df["low"].iloc[-1]), float(df["high"].iloc[-1])
    tol = (ob["ob_high"] - ob["ob_low"]) * (s.ob_retest_tolerance_pct / 100) if ob["ob_high"] != ob["ob_low"] else 0.01
    if direction == "BULLISH":
        return last_low <= ob["ob_high"] + tol
    return last_high >= ob["ob_low"] - tol
