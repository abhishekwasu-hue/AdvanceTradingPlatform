"""Scored multi-timeframe S/R levels - Trade's "S/R levels v3" (recency, reaction, confluence, key levels, flips, gaps).

Ported from Trade@eafc872 sr_levels_v3.py (compute_sr_v3, select_display_levels and their helpers; the chart-line
formatting `to_chart_lines` belongs to the chart layer, Z7a-3). Pivots come from levels.find_pivots_indexed (the same
Pine pivots as dynamic S/R); every knob is in ZoneSettings.sr_v3 with Trade's SRConfig values as defaults.

  A. Recency - newer pivots weigh more (exponential decay, half-life in days, slower for higher timeframes).
  B. Reaction - how many ATRs price moved away from the pivot in the next 2 x prd bars.
  C. Confluence - pivots from several timeframes in one zone score more; touches are taken from the strongest timeframe,
     never summed across timeframes (one swing shows on every timeframe).
  D. Key levels - previous day high / low / close (PDH / PDL / PDC) and previous week high / low (PWH / PWL).
  E. Role reversal - acceptance-ratio flip beyond the level, and whether it was retested.
  F. Unfilled (or partly filled) overnight gaps as zones.
  G. One 0-100 score + grade A / B / C - a ranking, not a probability.
Role is by price: RESISTANCE above, SUPPORT below, ZONE when the price is inside (within a few points of) the zone.

Differences from Trade (presentation only): timeframe keys are ATP's ("5m", "15m", "30m", "1h", "4h", "1d", "1w"); tags
are ASCII (GAP_UP / GAP_DOWN / FLIP_RETESTED / REJxN / BOTH_SIDES / CHOPxN for Trade's arrows, ticks, x signs and the
Marathi both-sides tag); tz-aware stamps are read in settings.session_timezone. Inputs are never mutated; too little
data -> an empty list, never an exception.
"""
import datetime
from typing import Any, Dict, List, Mapping, Optional

import numpy as np
import pandas as pd

from app.indicators.volatility import true_range
from app.price_action.zones.levels import find_pivots_indexed
from app.price_action.zones.compute import exchange_time
from app.price_action.zones.settings import TF_MINUTES, SRV3Settings, ZoneSettings

TF_ORDER = ("5m", "15m", "30m", "1h", "4h", "1d", "1w")
TF_SHORT = {"5m": "5M", "15m": "15M", "30m": "30M", "1h": "1H", "4h": "4H", "1d": "D", "1w": "W"}
_COLS = ["timestamp", "open", "high", "low", "close"]


def _prep(df: Optional[pd.DataFrame], tz: str) -> pd.DataFrame:
    """A clean copy: naive exchange-time stamps, sorted, de-duplicated, no NaN in OHLC. Missing columns -> empty."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty or any(c not in df.columns for c in _COLS):
        return pd.DataFrame(columns=_COLS)
    out = df[_COLS].copy()
    ts = pd.to_datetime(out["timestamp"], errors="coerce")
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert(tz).dt.tz_localize(None)
    out["timestamp"] = ts
    for c in _COLS[1:]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out.dropna().sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def compute_atr(df: Optional[pd.DataFrame], period: int = 14) -> float:
    """Simple ATR: mean true range of the last `period` bars (the first bar's TR is its high - low). Too little -> 0.0."""
    if df is None or len(df) < 2:
        return 0.0
    tr = true_range(df[["high", "low", "close"]].astype(float)).dropna()
    if tr.empty:
        return 0.0
    value = tr.tail(int(period)).mean()
    return float(value) if np.isfinite(value) else 0.0


def recency_weight(age_days: float, half_life_days: float) -> float:
    """1.0 now, 0.5 after one half-life, and so on (a negative age counts as 0)."""
    if half_life_days <= 0:
        return 1.0
    return float(0.5 ** (max(float(age_days), 0.0) / float(half_life_days)))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# --- A + B: pivots ------------------------------------------------------------------------------------------------------
def extract_pivots(df: pd.DataFrame, tf: str, cfg: SRV3Settings, now: pd.Timestamp) -> List[Dict[str, Any]]:
    """Confirmed pivots of one timeframe within cfg.lookback_days, each with its recency weight and reaction (0..1, and
    the uncapped raw value). A pivot is confirmed prd bars later, so the last prd bars hold none and nothing repaints."""
    prd = int(cfg.prd.get(tf, 10))
    if df is None or len(df) < 2 * prd + 5:
        return []
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    atr = compute_atr(df, cfg.atr_period)
    cutoff = now - pd.Timedelta(days=float(cfg.lookback_days.get(tf, 5)))
    out = []
    for idx, value in find_pivots_indexed(df, prd):
        ts = df["timestamp"].iloc[idx]
        if ts < cutoff:
            continue
        is_high = value == highs[idx]
        n_after = min(2 * prd, len(df) - 1 - idx)
        reaction, reaction_raw = 0.0, 0.0
        if n_after > 0 and atr > 0:
            if is_high:
                move = float(value) - float(lows[idx + 1: idx + 1 + n_after].min())
            else:
                move = float(highs[idx + 1: idx + 1 + n_after].max()) - float(value)
            reaction_raw = max(move / atr / cfg.reaction_atr_full, 0.0)
            reaction = _clamp(reaction_raw, 0.0, 1.0)
        age_days = (now - ts).total_seconds() / 86400.0
        out.append({"price": float(value), "kind": "H" if is_high else "L", "ts": ts, "tf": tf,
                    "weight": recency_weight(age_days, cfg.recency_half_life_by_tf.get(tf, cfg.recency_half_life_days)),
                    "reaction": float(reaction), "reaction_raw": float(reaction_raw)})
    return out


# --- D: key levels ------------------------------------------------------------------------------------------------------
def _daily_from_intraday(df: Optional[pd.DataFrame]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "high", "low", "close"])
    d = df.assign(date=df["timestamp"].dt.date)
    return d.groupby("date").agg(high=("high", "max"), low=("low", "min"), close=("close", "last")).reset_index()


def session_reference_date(now: pd.Timestamp, session_end: str = "15:15") -> datetime.date:
    """The day "previous day" is relative to: today while the session runs; once the last bar is at / after the session
    end, the next weekday - so after the close (or before the next open) PDH / PDL is the session that just ended."""
    ref = now.date()
    end_h, end_m = (int(x) for x in str(session_end).split(":"))
    if now.time() >= datetime.time(end_h, end_m):
        ref += datetime.timedelta(days=1)
        while ref.weekday() >= 5:
            ref += datetime.timedelta(days=1)
    return ref


def compute_key_levels(daily_df: Optional[pd.DataFrame], fallback_df: Optional[pd.DataFrame], now_date: datetime.date,
                       tz: str = "Asia/Kolkata") -> List[Dict[str, Any]]:
    """PDH / PDL / PDC (the last trading day before now_date) and PWH / PWL (the whole previous Monday-Sunday week).
    Days missing from daily_df are filled from the intraday fallback. Returns [{"name", "price"}]."""
    daily = _prep(daily_df, tz)
    daily = _daily_from_intraday(daily) if len(daily) else pd.DataFrame(columns=["date", "high", "low", "close"])
    intraday_daily = _daily_from_intraday(fallback_df)
    if len(daily) and len(intraday_daily):
        daily = pd.concat([daily, intraday_daily[~intraday_daily["date"].isin(daily["date"])]], ignore_index=True)
    elif len(intraday_daily):
        daily = intraday_daily
    if daily.empty:
        return []
    daily = daily.sort_values("date").reset_index(drop=True)
    levels: List[Dict[str, Any]] = []
    prev_days = daily[daily["date"] < now_date]
    if not prev_days.empty:
        last = prev_days.iloc[-1]
        levels += [{"name": "PDH", "price": float(last["high"])}, {"name": "PDL", "price": float(last["low"])},
                   {"name": "PDC", "price": float(last["close"])}]
    week_start = now_date - datetime.timedelta(days=now_date.weekday())
    prev_week_start = week_start - datetime.timedelta(days=7)
    prev_week = daily[(daily["date"] >= prev_week_start) & (daily["date"] < week_start)]
    if not prev_week.empty:
        levels += [{"name": "PWH", "price": float(prev_week["high"].max())}, {"name": "PWL", "price": float(prev_week["low"].min())}]
    return levels


# --- F: gaps ------------------------------------------------------------------------------------------------------------
def find_unfilled_gaps(df: Optional[pd.DataFrame], min_gap_pct: float = 0.20, max_age_days: int = 10,
                       now: Optional[pd.Timestamp] = None) -> List[Dict[str, Any]]:
    """Overnight gaps (the previous session's last bar vs the next session's first bar, no overlap) not yet fully
    filled; a partly filled gap keeps only the unfilled part. Returns [{"kind", "low", "high", "date", "fill_pct"}]."""
    if df is None or len(df) < 2:
        return []
    now = now if now is not None else df["timestamp"].iloc[-1]
    work = df.assign(_date=df["timestamp"].dt.date)
    dates = sorted(work["_date"].unique())
    out = []
    for i in range(1, len(dates)):
        prev_rows = work[work["_date"] == dates[i - 1]]
        next_rows = work[work["_date"] == dates[i]]
        if prev_rows.empty or next_rows.empty:
            continue
        if (now - pd.Timestamp(dates[i])).days > max_age_days:
            continue
        prev_last, first = prev_rows.iloc[-1], next_rows.iloc[0]
        if first["low"] > prev_last["high"]:
            kind, g_low, g_high = "UP_GAP", float(prev_last["high"]), float(first["low"])
        elif first["high"] < prev_last["low"]:
            kind, g_low, g_high = "DOWN_GAP", float(first["high"]), float(prev_last["low"])
        else:
            continue
        if (g_high - g_low) < g_low * min_gap_pct / 100.0:
            continue
        since = work[work["timestamp"] >= first["timestamp"]]
        original = g_high - g_low
        if kind == "UP_GAP":
            deepest = float(since["low"].min())
            if deepest <= g_low:
                continue                                  # fully filled
            if deepest < g_high:
                g_high = deepest                          # partly filled - the rest remains
        else:
            highest = float(since["high"].max())
            if highest >= g_high:
                continue
            if highest > g_low:
                g_low = highest
        out.append({"kind": kind, "low": g_low, "high": g_high, "date": dates[i],
                    "fill_pct": round(100.0 * (1 - (g_high - g_low) / original), 1)})
    return out


# --- E: role reversal ---------------------------------------------------------------------------------------------------
def detect_role_reversal(zone_low: float, zone_high: float, origin: Optional[str], df: Optional[pd.DataFrame], since_ts: Any,
                         buffer_pct: float = 0.10, confirm_bars: int = 2) -> Dict[str, bool]:
    """origin "R" (was resistance) / "S" (was support). Flipped: after since_ts, confirm_bars consecutive closes beyond the
    zone (with buffer %) and the last close still on that side; retested: price came back into the zone afterwards and
    held. (Trade keeps this older consecutive-close rule beside the acceptance-ratio one used by compute_sr_v3.)"""
    none = {"flipped": False, "retested": False}
    if origin not in ("R", "S") or df is None or df.empty:
        return none
    after = df[df["timestamp"] > since_ts]
    n = len(after)
    confirm_bars = max(int(confirm_bars), 1)
    if n < confirm_bars:
        return none
    close, high, low = after["close"].to_numpy(), after["high"].to_numpy(), after["low"].to_numpy()
    buf = buffer_pct / 100.0
    beyond = close > zone_high * (1 + buf) if origin == "R" else close < zone_low * (1 - buf)
    confirmed_at, run = None, 0
    for i in range(n):
        run = run + 1 if beyond[i] else 0
        if run >= confirm_bars:
            confirmed_at = i
            break
    if confirmed_at is None:
        return none
    still_holding = close[-1] >= zone_low if origin == "R" else close[-1] <= zone_high
    if not still_holding:
        return none
    retested = False
    for j in range(confirmed_at + 1, n):
        if (origin == "R" and low[j] <= zone_high and close[j] >= zone_low) or (origin == "S" and high[j] >= zone_low and close[j] <= zone_high):
            retested = True
            break
    return {"flipped": True, "retested": retested}


def detect_flip_acceptance(level: float, band: float, origin: Optional[str], df: Optional[pd.DataFrame], since_ts: Any,
                           min_ratio: float = 0.6, min_beyond: int = 2) -> Dict[str, Any]:
    """Acceptance-ratio flip: from the first close beyond level +/- band, at least min_ratio of the closes are beyond, at
    least min_beyond of them, and the last close still holds beyond (or within the band). Catches choppy breaks too."""
    none = {"flipped": False, "retested": False, "accept_ratio": 0.0}
    if origin not in ("R", "S") or df is None or df.empty or since_ts is None:
        return none
    after = df[df["timestamp"] > since_ts]
    if after.empty:
        return none
    close, high, low = after["close"].to_numpy(), after["high"].to_numpy(), after["low"].to_numpy()
    beyond = close > level + band if origin == "R" else close < level - band
    if not beyond.any():
        return none
    first = int(np.argmax(beyond))
    seg = beyond[first:]
    ratio = float(seg.mean())
    result: Dict[str, Any] = {"flipped": False, "retested": False, "accept_ratio": round(ratio, 2)}
    if int(seg.sum()) < max(int(min_beyond), 1) or ratio < min_ratio:
        return result
    holding = close[-1] > level - band if origin == "R" else close[-1] < level + band
    if not holding:
        return result
    result["flipped"] = True
    for j in range(first + 1, len(close)):
        if (origin == "R" and low[j] <= level + band and close[j] >= level - band) or \
                (origin == "S" and high[j] >= level - band and close[j] <= level + band):
            result["retested"] = True
            break
    return result


def count_crossings(df: Optional[pd.DataFrame], level: float, band: float, since_ts: Any) -> int:
    """How many times closes after since_ts switched sides of the level (closes inside level +/- band ignored)."""
    if df is None or df.empty:
        return 0
    last, n = 0, 0
    for c in df.loc[df["timestamp"] > since_ts, "close"].to_numpy():
        side = 1 if c > level + band else -1 if c < level - band else 0
        if side and last and side != last:
            n += 1
        if side:
            last = side
    return n


def count_rejections(df: Optional[pd.DataFrame], level: float, band: float, since_ts: Any, atr: float, cfg: SRV3Settings) -> Dict[str, int]:
    """Separate rejections at level +/- band after since_ts (consecutive qualifying bars = one). Resistance rejection:
    high >= level - band, close <= level - band, open <= level + band; support: mirror. Counting starts once price has
    left by band + reject_depart_atr ATR. "strong" = a follow-through of reject_follow_atr ATR within reject_follow_bars."""
    out = {"count": 0, "strong": 0}
    if df is None or df.empty or since_ts is None or band <= 0:
        return out
    after = df[df["timestamp"] > since_ts]
    if after.empty:
        return out
    o, h, l, c = (after[k].to_numpy() for k in ("open", "high", "low", "close"))
    depart = band + cfg.reject_depart_atr * max(atr, 0.0)
    departed, prev_hit, n = False, False, len(after)
    for i in range(n):
        if not departed:
            departed = abs(c[i] - level) >= depart
            prev_hit = False
            continue
        r_rej = h[i] >= level - band and c[i] <= level - band and o[i] <= level + band
        s_rej = l[i] <= level + band and c[i] >= level + band and o[i] >= level - band
        hit = bool(r_rej or s_rej)
        if hit and not prev_hit:
            if cfg.reject_redepart:
                departed = False
            out["count"] += 1
            window = slice(i + 1, i + 1 + int(cfg.reject_follow_bars))
            if atr > 0 and i + 1 < n:
                if r_rej and float(l[window].min()) <= level - cfg.reject_follow_atr * atr:
                    out["strong"] += 1
                elif s_rej and float(h[window].max()) >= level + cfg.reject_follow_atr * atr:
                    out["strong"] += 1
        prev_hit = hit
    return out


# --- clustering + scoring -----------------------------------------------------------------------------------------------
def cluster_items(items: List[Dict[str, Any]], tol: float) -> List[List[Dict[str, Any]]]:
    """Group by price; a group never spans more than 2 x tol (no chaining everything into one zone)."""
    clusters: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    for item in sorted(items, key=lambda it: it["price"]):
        if current and item["price"] - current[0]["price"] > 2.0 * tol:
            clusters.append(current)
            current = []
        current.append(item)
    if current:
        clusters.append(current)
    return clusters


def _pivot_anchor_score(p: Dict[str, Any], cfg: SRV3Settings) -> float:
    raw = _clamp(p.get("reaction_raw", p["reaction"]), 0.0, 3.0)
    return cfg.tf_factor.get(p["tf"], 1.0) * (0.5 + 0.5 * raw)


def _ts_key(ts: Any) -> int:
    try:
        return -pd.Timestamp(ts).value if ts is not None else 0
    except (TypeError, ValueError):
        return 0


def _pick_anchor(members: List[Dict[str, Any]], cfg: SRV3Settings) -> Optional[Dict[str, Any]]:
    """The zone's line sits on its most decisive swing's exact price (higher timeframe on a tie, then the OLDEST - the
    origin swing); a key level has its own anchor score."""
    best, best_key = None, None
    for m in members:
        if m["kind"] in ("H", "L"):
            key = (_pivot_anchor_score(m, cfg), cfg.tf_factor.get(m["tf"], 1.0), _ts_key(m.get("ts")))
        else:
            key = (cfg.key_anchor_score, 99.0, 0)
        if best_key is None or key > best_key:
            best, best_key = m, key
    return best


def _build_zone(members: List[Dict[str, Any]], cfg: SRV3Settings, tol: Optional[float] = None) -> Dict[str, Any]:
    pivots = [m for m in members if m["kind"] in ("H", "L")]
    key_names = sorted({m["name"] for m in members if m["kind"] == "KEY"})
    prices = [m["price"] for m in members]
    weights = [m["weight"] * cfg.tf_factor.get(m["tf"], 1.0) if m["kind"] in ("H", "L") else 3.0 for m in members]
    mid = float(np.average(prices, weights=weights)) if sum(weights) > 0 else float(np.mean(prices))
    per_tf: Dict[str, List[Dict[str, Any]]] = {}
    for p in pivots:
        per_tf.setdefault(p["tf"], []).append(p)
    touches_raw = max((sum(p["weight"] for p in ps) * cfg.tf_factor.get(tf, 1.0) for tf, ps in per_tf.items()), default=0.0)
    best_tf = max(per_tf, key=lambda tf: sum(p["weight"] for p in per_tf[tf]) * cfg.tf_factor.get(tf, 1.0)) if per_tf else None
    n_high = sum(1 for p in pivots if p["kind"] == "H")
    n_low = sum(1 for p in pivots if p["kind"] == "L")
    anchor = _pick_anchor(members, cfg)
    level = float(anchor["price"]) if anchor is not None else mid
    core_prices = ([p for p in prices if abs(p - level) <= 0.5 * tol] or [level]) if tol else prices
    if anchor is not None and anchor["kind"] in ("H", "L"):
        origin: Optional[str] = "R" if anchor["kind"] == "H" else "S"
    else:
        origin = "R" if n_high > n_low else "S" if n_low > n_high else None
    return {
        "low": float(min(prices)), "high": float(max(prices)), "mid": mid, "level": level,
        "core_low": float(min(core_prices)), "core_high": float(max(core_prices)),
        "anchor": None if anchor is None else {"price": float(anchor["price"]), "tf": anchor.get("tf"), "kind": anchor["kind"],
                                               "ts": anchor.get("ts"), "name": anchor.get("name")},
        "origin": origin, "rejections": 0, "rej_strong": 0, "accept_ratio": 0.0,
        "tfs": [tf for tf in TF_ORDER if tf in per_tf],
        "pivot_count": len(per_tf[best_tf]) if best_tf else 0,
        "n_high": n_high, "n_low": n_low, "touches_raw": float(touches_raw),
        "reaction": max((p["reaction"] * (0.5 + 0.5 * p["weight"]) for p in pivots), default=0.0),
        "keys": key_names, "gap": None, "flipped": False, "retested": False,
        "polarity": n_high >= 1 and n_low >= 1,
        "last_pivot_ts": max((p["ts"] for p in pivots), default=None),
    }


def _score_zone(zone: Dict[str, Any], cfg: SRV3Settings) -> None:
    comp = {
        "touches": cfg.w_touch * _clamp(zone["touches_raw"] / cfg.touch_full, 0.0, 1.0),
        "reaction": cfg.w_reaction * _clamp(zone["reaction"], 0.0, 1.0),
        "confluence": min(sum(cfg.tf_confluence_points.get(tf, 0.0) for tf in zone["tfs"]), cfg.w_confluence_cap),
        "key_level": min(sum(cfg.key_level_points.get(k, 0.0) for k in zone["keys"]), cfg.w_key_cap),
        "polarity": cfg.w_polarity if zone["polarity"] else 0.0,
        "role_reversal": (cfg.w_flip if zone["flipped"] else 0.0) + (cfg.w_retest if zone["flipped"] and zone["retested"] else 0.0),
        "gap": cfg.w_gap if zone["gap"] else 0.0,
        "rejections": cfg.w_reject * _clamp((zone.get("rejections", 0) + 0.5 * zone.get("rej_strong", 0)) / cfg.reject_full, 0.0, 1.0),
    }
    if zone["gap"] and not zone["tfs"] and not zone["keys"]:
        comp["gap"] = 30.0                                  # a stand-alone gap zone's base score (Trade constant)
    if cfg.min_base_points and comp["touches"] + comp["key_level"] < cfg.min_base_points:
        comp["role_reversal"] = comp["rejections"] = comp["polarity"] = 0.0
    score = _clamp(sum(comp.values()), 0.0, 100.0)
    if cfg.chop_filter and zone.get("crossings", 0) > cfg.chop_max_crossings:
        comp["chop"] = round(min(score, cfg.grade_b - 0.1) - score, 1)
        score = min(score, cfg.grade_b - 0.1)
    zone["components"] = {k: round(v, 1) for k, v in comp.items()}
    zone["score"] = round(score, 1)
    zone["grade"] = "A" if score >= cfg.grade_a else "B" if score >= cfg.grade_b else "C"


def _tags(zone: Dict[str, Any]) -> List[str]:
    tags = [TF_SHORT[tf] for tf in zone["tfs"]] + list(zone["keys"])
    if zone["gap"]:
        tags.append("GAP_UP" if zone["gap"]["kind"] == "UP_GAP" else "GAP_DOWN")
    if zone["flipped"]:
        tags.append("FLIP_RETESTED" if zone["retested"] else "FLIP")
    if zone.get("rejections", 0) >= 2:
        tags.append(f"REJx{zone['rejections']}")
    if zone["polarity"] and not zone["flipped"]:
        tags.append("BOTH_SIDES")
    if zone.get("components", {}).get("chop"):
        tags.append(f"CHOPx{zone['crossings']}")
    return tags


def _closed_by(df: pd.DataFrame, tf: str, as_of: Optional[pd.Timestamp]) -> pd.DataFrame:
    """Prepared bars (naive exchange time) that had closed by as_of: stamp + bar length <= as_of."""
    if as_of is None or df.empty:
        return df
    return df[df["timestamp"] + pd.Timedelta(minutes=TF_MINUTES[tf]) <= as_of].reset_index(drop=True)


def compute_sr_v3(frames: Mapping[str, Optional[pd.DataFrame]], daily_df: Optional[pd.DataFrame] = None,
                  current_price: Optional[float] = None, settings: Optional[ZoneSettings] = None, as_of: Any = None) -> Dict[str, Any]:
    """frames {timeframe: bars} (any of TF_ORDER) -> {"levels": [... best score first ...], "meta": {...}}. Each level:
    level (the line), low / high (the zone), role (SUPPORT / RESISTANCE / ZONE by the price), score, grade, tags, tfs,
    pivot_count, components, distance_pct (+ above the price).
    as_of (a clock time, naive exchange time or tz-aware): every frame and daily_df is first cut to the bars that had
    CLOSED by then (stamp + bar length <= as_of), so nothing later - nor the unfinished part of a bar - is used."""
    s = settings if settings is not None else ZoneSettings()
    cfg, tz = s.sr_v3, s.session_timezone
    cut = exchange_time(as_of, tz) if as_of is not None else None
    prepared = {tf: _closed_by(_prep(df, tz), tf, cut) for tf, df in (frames or {}).items() if tf in TF_ORDER}
    prepared = {tf: df for tf, df in prepared.items() if not df.empty}
    if daily_df is not None and cut is not None:
        daily_df = _closed_by(_prep(daily_df, tz), "1d", cut)
    if not prepared:
        return {"levels": [], "meta": {"frames": [], "reason": "no data"}}

    now = max(df["timestamp"].iloc[-1] for df in prepared.values())
    finest = next(tf for tf in TF_ORDER if tf in prepared)
    try:
        price = float(current_price) if current_price is not None else float("nan")
    except (TypeError, ValueError):
        price = float("nan")
    if not (np.isfinite(price) and price > 0):
        price = float(prepared[finest]["close"].iloc[-1])

    ref_tf = "15m" if "15m" in prepared else finest
    atr_ref = compute_atr(prepared[ref_tf], cfg.atr_period)
    tol = _clamp(cfg.tol_atr_mult * atr_ref, price * cfg.tol_min_pct / 100.0, price * cfg.tol_max_pct / 100.0)

    items: List[Dict[str, Any]] = []
    for tf, df in prepared.items():
        items += extract_pivots(df, tf, cfg, now)
    key_source = prepared.get("15m", prepared[finest])
    for key in compute_key_levels(daily_df, key_source, session_reference_date(now, cfg.session_end), tz):
        items.append({"price": key["price"], "kind": "KEY", "name": key["name"], "tf": None, "weight": 1.0})
    zones = [_build_zone(c, cfg, tol) for c in cluster_items(items, tol)] if items else []

    flip_df = prepared.get(cfg.flip_frame, prepared[finest])
    flip_atr = compute_atr(flip_df, cfg.atr_period)
    band = max(cfg.flip_band_frac * tol, cfg.flip_min_break_atr * flip_atr)
    chop_since = now - pd.Timedelta(days=float(cfg.chop_lookback_days))
    for z in zones:
        if cfg.chop_filter:
            z["crossings"] = count_crossings(flip_df, z["level"], cfg.reject_band_frac * tol, chop_since)
        anchor = z["anchor"]
        since = anchor["ts"] if anchor and anchor.get("ts") is not None else z["last_pivot_ts"]
        if since is None or z["origin"] is None:
            continue
        verdict = detect_flip_acceptance(z["level"], band, z["origin"], flip_df, since, cfg.flip_accept_ratio, cfg.flip_min_beyond)
        z["flipped"], z["retested"], z["accept_ratio"] = verdict["flipped"], verdict["retested"], verdict["accept_ratio"]
        rej = count_rejections(flip_df, z["level"], cfg.reject_band_frac * tol, since, flip_atr, cfg)
        z["rejections"], z["rej_strong"] = rej["count"], rej["strong"]

    gap_source = prepared.get("15m", prepared[finest])
    for gap in find_unfilled_gaps(gap_source, cfg.gap_min_pct, cfg.gap_max_age_days, now):
        # a gap joins the zone whose tolerance holds one of its edges (where price reacts while filling), never a zone
        # that merely runs through the middle of a large gap
        overlapping = [z for z in zones if any(z["low"] - tol <= edge <= z["high"] + tol for edge in (gap["low"], gap["high"]))]
        if overlapping:
            for z in zones:
                _score_zone(z, cfg)
            target = max(overlapping, key=lambda z: z["score"])
            if target["gap"] is None:
                target["gap"] = gap
            continue
        near_edge = gap["high"] if gap["kind"] == "UP_GAP" else gap["low"]
        in_gap = gap["low"] <= price <= gap["high"]
        zones.append({
            "low": gap["low"], "high": gap["high"], "mid": (gap["low"] + gap["high"]) / 2.0,
            "level": (gap["low"] + gap["high"]) / 2.0 if in_gap else near_edge,
            "tfs": [], "pivot_count": 0, "n_high": 0, "n_low": 0, "touches_raw": 0.0, "reaction": 0.0,
            "keys": [], "gap": gap, "flipped": False, "retested": False, "polarity": False, "last_pivot_ts": None,
            "core_low": gap["low"], "core_high": gap["high"], "anchor": None, "origin": None,
            "rejections": 0, "rej_strong": 0, "accept_ratio": 0.0,
        })

    levels = []
    for z in zones:
        _score_zone(z, cfg)
        zt = min(max(cfg.zone_touch_pts, cfg.zone_touch_tol_frac * tol), tol)
        z["role"] = "RESISTANCE" if price < z["low"] - zt else "SUPPORT" if price > z["high"] + zt else "ZONE"
        z["distance_pct"] = round((z["level"] - price) / price * 100.0, 2)
        z["tags"] = _tags(z)
        z["gap_kind"] = z["gap"]["kind"] if z["gap"] else None
        z.pop("last_pivot_ts", None)
        levels.append(z)
    levels.sort(key=lambda z: (-z["score"], abs(z["distance_pct"])))
    return {"levels": levels, "meta": {
        "frames": sorted(prepared, key=TF_ORDER.index), "price": price, "now": now, "tol": round(tol, 2),
        "atr_ref": round(atr_ref, 2), "ref_tf": ref_tf, "pivot_count": sum(1 for i in items if i["kind"] in ("H", "L")),
    }}


def select_display_levels(levels: List[Dict[str, Any]], settings: Optional[ZoneSettings] = None) -> List[Dict[str, Any]]:
    """Levels with score >= min_score within max_distance_pct, the best max_levels of them, always including the nearest
    eligible level on each side of the price. Highest level first."""
    cfg = (settings if settings is not None else ZoneSettings()).sr_v3
    eligible = [z for z in levels if z["score"] >= cfg.min_score and abs(z["distance_pct"]) <= cfg.max_distance_pct]
    chosen = eligible[:max(int(cfg.max_levels), 0)]
    for side in ("RESISTANCE", "SUPPORT"):
        pool = [z for z in eligible if z["role"] == side]
        if pool and not any(z["role"] == side for z in chosen):
            chosen.append(min(pool, key=lambda z: abs(z["distance_pct"])))
    return sorted(chosen, key=lambda z: -z["level"])
