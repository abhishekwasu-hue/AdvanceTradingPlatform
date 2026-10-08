"""Level / zone strength (indicator-free), the approach rule and zone events - sweep, real break, failed breakout.

Ported from Trade@0df3e09d0aa5942e3352327d5b998c5f6939ac61, price_action/level_strength.py (median range and the
displacement test from price_action/legs.py). Measurement and reporting only - no trading gate.

Zone (from any engine): {"low", "high", "kind", "formed_at" (optional timestamp)}. kind SUPPORT/DEMAND (holds from
below) or RESISTANCE/SUPPLY (caps from above); other kinds take their side from the price. Every measure is AS OF bar
`t`: only bars <= t are used.

Features: departure_mr (range of the 1-3 candles after the origin / median range - a strong departure is strength),
base_bars (candles in the zone before the origin - a short base is fresh imbalance), touches (separate visits after the
origin), recency (exp(-bars since the last contact / tau)), round_dist_mr (zone mid to the nearest round number / median
range - a feature only), tpo_share (share of recent sessions' closes inside the zone - acceptance), role_reversal (broken,
then retested from the other side and held).
Approach: a HEALTHY_PULLBACK into a strong zone -> REACTION candidate (the reversal check then decides an entry); a
STRONG_IMPULSE into a zone -> BREAK candidate (no reversal entry).
Events: SWEEP (wick beyond, close inside) - BREAK (close beyond the far edge, no reclaim within n bars) - BREAK_CASCADE
(BREAK + a displacement candle) - FAILED_BREAKOUT (close beyond, back inside within n bars). `bar` = the break bar;
`known_at` = the bar on which the outcome became certain (BREAK / FAILED: bar + n).
"""
import math
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from app.price_action import pa_settings

SUPPORT_KINDS = ("SUPPORT", "DEMAND")
RESIST_KINDS = ("RESISTANCE", "SUPPLY")
REACTION_CANDIDATE, BREAK_CANDIDATE = "REACTION_CANDIDATE", "BREAK_CANDIDATE"
SWEEP, BREAK, BREAK_CASCADE, FAILED_BREAKOUT = "SWEEP", "BREAK", "BREAK_CASCADE", "FAILED_BREAKOUT"
# Leg labels the strength score and the approach rule understand (any leg classifier may supply them).
STRONG_IMPULSE, WEAK_IMPULSE, HEALTHY_PULLBACK, REVERSAL = "STRONG_IMPULSE", "WEAK_IMPULSE", "HEALTHY_PULLBACK", "REVERSAL"
DEFAULT_WEIGHTS = {"departure_mr": 0.25, "origin_strong": 0.25, "base_short": 0.15, "recency": 0.15, "tpo_low": 0.10,
                   "role_reversal": 0.10, "touches": 0.0}


def _s(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return settings if settings is not None and "zone_median_n" in settings else pa_settings.settings(**(settings or {}))


def median_range_incl(df: pd.DataFrame, n: int) -> np.ndarray:
    """At bar i: median (high - low) of bars [i - n + 1, i] (all closed); at least n // 2 bars, NaN before."""
    rng = df["high"].astype(float) - df["low"].astype(float)
    return rng.rolling(n, min_periods=max(n // 2, 1)).median().to_numpy(float)


def side_of(zone: Dict[str, Any], price: float) -> int:
    """+1 support (price above), -1 resistance (price below), 0 inside / undecided. A given kind wins."""
    k = str(zone.get("kind", "")).upper()
    if k in SUPPORT_KINDS:
        return 1
    if k in RESIST_KINDS:
        return -1
    if price > zone["high"]:
        return 1
    if price < zone["low"]:
        return -1
    return 0


def prep_bars(df: pd.DataFrame, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """df -> arrays reused across many zones (speed in long backtests)."""
    s = _s(settings)
    df = df.reset_index(drop=True)
    return {"o": df["open"].to_numpy(float), "h": df["high"].to_numpy(float), "l": df["low"].to_numpy(float),
            "c": df["close"].to_numpy(float), "ts": pd.to_datetime(df["timestamp"]).to_numpy("datetime64[ns]"),
            "mr": median_range_incl(df, s["zone_median_n"]), "df": df}


def origin_index(df: pd.DataFrame, zone: Dict[str, Any], ts: Optional[np.ndarray] = None) -> Optional[int]:
    """`formed_at` -> bar index (the last bar at that time), or None."""
    fa = zone.get("formed_at")
    if fa is None or (isinstance(fa, float) and math.isnan(fa)):
        return None
    if ts is None:
        ts = pd.to_datetime(df["timestamp"]).to_numpy("datetime64[ns]")
    t = pd.Timestamp(fa)
    t = t.tz_localize(None) if t.tzinfo else t
    i = int(np.searchsorted(ts, np.datetime64(t), side="right")) - 1
    return i if i >= 0 else None


def _visits(h: np.ndarray, l: np.ndarray, lo: float, hi: float):
    inside = (l <= hi) & (h >= lo)
    starts = np.flatnonzero(inside & ~np.r_[False, inside[:-1]])
    return starts, inside


def round_distance(mid: float, symbol: str, settings: Optional[Dict[str, Any]] = None) -> float:
    steps_by = _s(settings)["zone_round_steps"]
    steps = steps_by.get(str(symbol).upper(), steps_by["DEFAULT"])
    return min(abs(mid - round(mid / s) * s) for s in steps)


def tpo_share(df_fine: Optional[pd.DataFrame], lo: float, hi: float, t_end, sessions: int) -> Optional[float]:
    """Share of the last `sessions` sessions' closes (up to t_end) inside the zone - time at price with 1-minute data."""
    if df_fine is None or not len(df_fine):
        return None
    ts = pd.to_datetime(df_fine["timestamp"])
    m = ts <= pd.Timestamp(t_end)
    if not m.any():
        return None
    sub = df_fine[m]
    days = ts[m].dt.normalize()
    keep = days >= sorted(days.unique())[-sessions] if days.nunique() >= sessions else days == days
    c = sub["close"].to_numpy(float)[keep.to_numpy()]
    return round(float(((c >= lo) & (c <= hi)).mean()), 4) if len(c) else None


def strength_features(zone: Dict[str, Any], df: pd.DataFrame, t: int, symbol: str = "NIFTY", legs: Optional[Sequence[Any]] = None,
                      df_fine: Optional[pd.DataFrame] = None, settings: Optional[Dict[str, Any]] = None,
                      bars: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Zone + bars (oldest first) + as-of bar `t` -> features. Only bars <= t. `legs` (optional): objects with start_bar,
    known_at, label, score (only legs with known_at <= t are used)."""
    s = _s(settings)
    bars = bars or prep_bars(df, s)
    df = bars["df"]
    t = min(int(t), len(df) - 1)
    h, l, c, mr = bars["h"], bars["l"], bars["c"], bars["mr"]
    mr_t = mr[t] if np.isfinite(mr[t]) and mr[t] > 0 else float(np.nanmedian(h[:t + 1] - l[:t + 1]))
    lo, hi = float(zone["low"]), float(zone["high"])
    mid = (lo + hi) / 2
    oi = origin_index(df, zone, bars["ts"])
    if oi is not None and oi > t:
        oi = None                                                    # a zone formed after t is unknown as of t
    f: Dict[str, Any] = {"width_mr": round((hi - lo) / mr_t, 3), "dist_mr": round((c[t] - mid) / mr_t, 3), "side": side_of(zone, c[t]),
                         "origin_known": oi is not None}
    if oi is not None:
        a, b = oi + 1, min(oi + 3, t)
        f["departure_mr"] = round(float((h[a:b + 1].max() - l[a:b + 1].min()) / mr_t), 3) if b >= a else None
        base, j = 0, oi
        while j >= 0 and l[j] <= hi and h[j] >= lo:
            base += 1
            j -= 1
        f["base_bars"] = base
    else:
        f["departure_mr"], f["base_bars"] = None, None
    start = (oi + 1) if oi is not None else max(t - 400, 0)
    while start <= t and l[start] <= hi and h[start] >= lo:          # the departure itself is not a touch
        start += 1
    starts, _ = _visits(h[start:t + 1], l[start:t + 1], lo, hi)
    f["touches"] = int(len(starts))
    last = (start + int(starts[-1])) if len(starts) else (oi if oi is not None else start)
    f["bars_since"] = int(t - last)
    f["recency"] = round(math.exp(-(t - last) / s["zone_recency_tau_bars"]), 4)
    f["round_dist_mr"] = round(round_distance(mid, symbol, s) / mr_t, 3)
    f["tpo_share"] = tpo_share(df_fine, lo, hi, df["timestamp"].iloc[t], s["zone_tpo_sessions"]) if df_fine is not None else None
    rr = False
    seg_c = c[start:t + 1]
    side0 = side_of(zone, c[oi]) if oi is not None else side_of(zone, c[max(start - 1, 0)])   # the role at the origin
    if side0 != 0 and len(seg_c):
        broke = np.flatnonzero(seg_c < lo) if side0 > 0 else np.flatnonzero(seg_c > hi)
        if len(broke):
            k0 = int(broke[0])
            after_h, after_l, after_c = h[start + k0 + 1:t + 1], l[start + k0 + 1:t + 1], c[start + k0 + 1:t + 1]
            rr = bool(((after_h >= lo) & (after_c < lo)).any()) if side0 > 0 else bool(((after_l <= hi) & (after_c > hi)).any())
    f["role_reversal"] = rr
    f["origin_label"], f["origin_score"] = None, None
    if legs is not None and oi is not None:
        cands = [lg for lg in legs if lg.known_at <= t and abs(lg.start_bar - oi) <= 3]
        if cands:
            lg = min(cands, key=lambda x: abs(x.start_bar - oi))
            f["origin_label"], f["origin_score"] = lg.label, lg.score
    return f


def strength_score(f: Dict[str, Any], weights: Optional[Dict[str, float]] = None) -> float:
    """Features -> 0-100. `touches` weighs 0 by default: its sign must be measured on in-sample data, not assumed."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    dep = min((f.get("departure_mr") or 0.0) / 3.0, 1.0)
    origin = 1.0 if f.get("origin_label") == STRONG_IMPULSE else 0.5 if f.get("origin_label") in (WEAK_IMPULSE, REVERSAL) else 0.0
    base = 0.0 if f.get("base_bars") is None else 1.0 / (1.0 + max(f["base_bars"] - 1, 0) / 3.0)
    tpo = f.get("tpo_share")
    tpo_low = 0.5 if tpo is None else max(0.0, 1.0 - tpo * 5.0)          # little time at price = imbalance, not acceptance
    touches = min(f.get("touches", 0), 5) / 5.0
    score = (w["departure_mr"] * dep + w["origin_strong"] * origin + w["base_short"] * base + w["recency"] * f.get("recency", 0.0)
             + w["tpo_low"] * tpo_low + w["role_reversal"] * (1.0 if f.get("role_reversal") else 0.0) + w["touches"] * touches)
    tot = sum(abs(v) for v in w.values()) or 1.0
    return round(100.0 * max(score, 0.0) / tot, 1)


def approach(current_leg: Any, zone: Optional[Dict[str, Any]], strength: float, min_strength: float = 50.0):
    """While the current leg moves toward the zone: HEALTHY_PULLBACK + strong zone -> REACTION_CANDIDATE; STRONG_IMPULSE ->
    BREAK_CANDIDATE; otherwise None. `current_leg` needs direction (+1 / -1), end_price and label."""
    if current_leg is None or zone is None:
        return None, "no leg or zone"
    side = side_of(zone, current_leg.end_price)
    toward = (side > 0 and current_leg.direction < 0) or (side < 0 and current_leg.direction > 0)
    if not toward:
        return None, "the leg is not moving toward the zone"
    if current_leg.label == STRONG_IMPULSE:
        return BREAK_CANDIDATE, "a strong impulse into the zone - more likely to break; no reversal entry"
    if current_leg.label == HEALTHY_PULLBACK and strength >= min_strength:
        return REACTION_CANDIDATE, f"a healthy pullback into a strong zone (strength {strength:.0f}) - the reversal check decides"
    return None, f"{current_leg.label} / strength {strength:.0f} - no rule applies"


def zone_events(zone: Dict[str, Any], df: pd.DataFrame, start: int, end: int, settings: Optional[Dict[str, Any]] = None,
                mr: Optional[np.ndarray] = None) -> List[Dict[str, Any]]:
    """Events within bars [start, end]. "Beyond" = below the low for a support, above the high for a resistance.
    BREAK / FAILED are decided on the break bar + zone_n_reclaim, and only when those bars exist (no lookahead)."""
    s = _s(settings)
    n_reclaim = s["zone_n_reclaim"]
    df = df.reset_index(drop=True)
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    mr = median_range_incl(df, s["zone_median_n"]) if mr is None else mr
    ts = pd.to_datetime(df["timestamp"])
    lo, hi = float(zone["low"]), float(zone["high"])
    side = side_of(zone, c[max(start - 1, 0)])
    if side == 0:
        return []
    end = min(int(end), len(df) - 1)
    out: List[Dict[str, Any]] = []
    i = max(int(start), 0)
    while i <= end:
        beyond_wick = (l[i] < lo) if side > 0 else (h[i] > hi)
        beyond_close = (c[i] < lo) if side > 0 else (c[i] > hi)
        if beyond_close:
            j_end = i + n_reclaim
            if j_end > end:
                break                                                   # not enough bars to decide yet
            reclaimed = any(((c[j] >= lo) if side > 0 else (c[j] <= hi)) for j in range(i + 1, j_end + 1))
            if reclaimed:
                typ = FAILED_BREAKOUT
            else:
                body, rng = abs(c[i] - o[i]), max(h[i] - l[i], 1e-12)
                disp = np.isfinite(mr[i]) and body >= s["displacement_body_mr"] * mr[i] and body / rng >= s["displacement_body_frac"]
                typ = BREAK_CASCADE if disp else BREAK
            out.append({"type": typ, "bar": i, "known_at": j_end, "time": ts.iloc[i], "price": float(c[i])})
            if typ in (BREAK, BREAK_CASCADE):
                break                                                   # broken - later events belong to its new role
            i = j_end + 1
            continue
        if beyond_wick:
            out.append({"type": SWEEP, "bar": i, "known_at": i, "time": ts.iloc[i], "price": float(l[i] if side > 0 else h[i])})
        i += 1
    return out
