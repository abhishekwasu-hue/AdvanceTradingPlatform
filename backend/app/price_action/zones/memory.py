"""Level memory - support / resistance levels carried across days instead of recomputed from scratch every night.

Ported from Trade@eafc872 level_memory.py (zone_tolerance, last_touch_time, merge_levels, existing_from_zones,
remember_dyn_sr, apply_memory_to_zone_rows). Golden parity: tests/test_zones_memory_parity.py. Pure functions - the
storage job (`job.py`) hands in the previous run's rows and stores what comes out.

The rule, per family and timeframe (Trade's, unchanged):
  1. a fresh level within `tol` of a remembered live level keeps the OLD price and formed time; strength = the larger
     (levels do not "jump" from day to day);
  2. a remembered level not found today is kept while price came within `tol` of it in the last `retire_days` days, or it
     formed in that window - and it is not more than `max_distance_pct` % from the price;
  3. a broken level is not deleted: its role comes from the price (support broken -> resistance);
  4. two levels within `tol` of each other keep only the better one (found today > stronger > nearer the price), at most
     `max_levels` per timeframe.
`tol` = memory_atr_mult x ATR(memory_atr_period) of that timeframe's bars, bounded to [memory_tol_min_pct,
memory_tol_max_pct] % of the price.

ATP differences, all deliberate: the knobs come from ZoneSettings (Trade hard-codes them; the defaults are Trade's); the
retire days per timeframe use ATP labels ("1h" for Trade's "60M"); `apply_memory` works on ATP zone rows (lists of dicts,
`timeframe` column) and also covers classical S/R (Trade remembers only dynamic S/R); roles are by price unless the
caller asks otherwise (Trade's NIFTY bots kept the stored role - `role_by_price=False` is kept for that parity).
"""
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import pandas as pd

from app.price_action.zones.settings import ZoneSettings

SUPPORT, RESISTANCE = "SUPPORT", "RESISTANCE"
# Families of line levels that memory applies to: zone_type prefix -> ATP families. "" = classical S/R (SUPPORT/RESISTANCE).
DYNAMIC_PREFIX, CLASSIC_PREFIX = "DYNAMIC_SR_", ""


def _s(settings: Optional[ZoneSettings]) -> ZoneSettings:
    return settings if settings is not None else ZoneSettings()


def zone_tolerance(df: Optional[pd.DataFrame], price: float, settings: Optional[ZoneSettings] = None) -> float:
    """Tolerance in price units: memory_atr_mult x ATR (simple mean of the true range over memory_atr_period bars of this
    timeframe), bounded to [memory_tol_min_pct, memory_tol_max_pct] % of the price; the minimum when the ATR is unknown."""
    s = _s(settings)
    price = float(price)
    lo, hi = price * s.memory_tol_min_pct / 100, price * s.memory_tol_max_pct / 100
    if df is None or len(df) < s.memory_atr_period + 1:
        return lo
    h, low, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    tr = pd.concat([h - low, (h - c.shift()).abs(), (low - c.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(s.memory_atr_period).mean().iloc[-1])
    if atr != atr:                                                   # NaN
        return lo
    return min(max(atr * s.memory_atr_mult, lo), hi)


def last_touch_time(df: Optional[pd.DataFrame], level: float, tol: float) -> Optional[pd.Timestamp]:
    """Stamp of the last bar that came within tol of the level (low <= level + tol and high >= level - tol), or None."""
    if df is None or not len(df):
        return None
    hit = df[(df["low"].astype(float) <= level + tol) & (df["high"].astype(float) >= level - tol)]
    return pd.Timestamp(hit["timestamp"].iloc[-1]) if len(hit) else None


def _ts(x: Any, tz: str) -> Optional[pd.Timestamp]:
    """A comparable stamp: tz-aware stamps become naive wall-clock time in the exchange zone (Trade: IST)."""
    try:
        t = pd.Timestamp(x)
    except (TypeError, ValueError):
        return None
    if t is pd.NaT:
        return None
    return t.tz_convert(tz).tz_localize(None) if t.tzinfo is not None else t


_UNSET = object()


def merge_levels(existing: Sequence[Mapping[str, Any]], fresh: Sequence[Mapping[str, Any]], df: Optional[pd.DataFrame], price: float,
                 now: Any, retire_days: Optional[int] = None, max_distance_pct: Optional[float] = None, max_levels: Optional[int] = None,
                 role_by_price: bool = True, settings: Optional[ZoneSettings] = None, stamp: Any = _UNSET) -> List[Dict[str, Any]]:
    """existing: [{"level", "strength", "formed_date"(, "role")}] - the timeframe's live remembered levels.
    fresh: [{"level", "touches"(, "role")}] - today's computation (support + resistance together).
    df: the timeframe's bars (for the touch check). Returns [{"level", "strength", "formed_date", "fresh", "role"}] sorted by
    level. Arguments left None come from settings (memory_retire_days, memory_max_distance_pct, memory_max_levels).
    `stamp` is the formed time written on a new level (default: `now` read as Trade does - naive exchange time)."""
    s = _s(settings)
    retire_days = s.memory_retire_days if retire_days is None else retire_days
    max_distance_pct = s.memory_max_distance_pct if max_distance_pct is None else max_distance_pct
    max_levels = s.memory_max_levels if max_levels is None else max_levels
    tz = s.session_timezone
    price = float(price)
    now_ts = _ts(now, tz)
    if now_ts is None:
        raise ValueError(f"merge_levels: `now` is not a time ({now!r})")
    new_stamp = now_ts if stamp is _UNSET else stamp
    tol = zone_tolerance(df, price, s)
    cutoff = now_ts - pd.Timedelta(days=retire_days)
    out: List[Dict[str, Any]] = []
    used: set = set()
    for f in fresh:
        lv = float(f["level"])
        match: Optional[int] = None
        for i, e in enumerate(existing):
            if i not in used and abs(float(e["level"]) - lv) <= tol and (match is None or
                                                                          abs(float(e["level"]) - lv) < abs(float(existing[match]["level"]) - lv)):
                match = i
        if match is not None:
            used.add(match)
            e = existing[match]
            out.append({"level": float(e["level"]), "strength": max(float(e.get("strength") or 0), float(f.get("touches") or 0)),
                        "formed_date": e.get("formed_date") or new_stamp, "fresh": True, "role": f.get("role")})
        else:
            out.append({"level": lv, "strength": float(f.get("touches") or 0), "formed_date": new_stamp, "fresh": True, "role": f.get("role")})
    for i, e in enumerate(existing):
        if i in used:
            continue
        lv = float(e["level"])
        if abs(lv - price) / price * 100 > max_distance_pct:
            continue
        formed = _ts(e.get("formed_date"), tz)
        touched = last_touch_time(df, lv, tol)
        touched_ts = _ts(touched, tz) if touched is not None else None
        if (touched_ts is not None and touched_ts >= cutoff) or (formed is not None and formed >= cutoff):
            out.append({"level": lv, "strength": float(e.get("strength") or 0), "formed_date": e.get("formed_date") or new_stamp, "fresh": False,
                        "role": e.get("role")})
    out.sort(key=lambda z: (not z["fresh"], -z["strength"], abs(z["level"] - price)))
    kept: List[Dict[str, Any]] = []
    for z in out:
        if all(abs(z["level"] - k["level"]) > tol for k in kept):
            kept.append(z)
        if len(kept) >= max_levels:
            break
    for z in kept:
        if role_by_price or z.get("role") not in (SUPPORT, RESISTANCE):
            z["role"] = SUPPORT if z["level"] <= price else RESISTANCE
    return sorted(kept, key=lambda z: z["level"])


def retire_days_for(timeframe: str, settings: Optional[ZoneSettings] = None) -> int:
    """memory_retire_days_by_tf[timeframe], else memory_retire_days (Trade NSE_RETIRE_DAYS, default 30)."""
    s = _s(settings)
    return int(s.memory_retire_days_by_tf.get(timeframe, s.memory_retire_days))


def role_of(zone_type: str) -> str:
    return SUPPORT if SUPPORT in str(zone_type) else RESISTANCE


def _is_family(zone_type: str, prefix: str) -> bool:
    return zone_type in (f"{prefix}{SUPPORT}", f"{prefix}{RESISTANCE}")


def existing_from_zones(rows: Optional[Iterable[Mapping[str, Any]]], timeframe: str, prefix: str = DYNAMIC_PREFIX) -> List[Dict[str, Any]]:
    """The live remembered levels of one family + timeframe from stored zone rows -> merge_levels' `existing` format.
    Live = any state but invalidated (Trade: status ACTIVE)."""
    out = []
    for r in rows or []:
        if r.get("timeframe") != timeframe or not _is_family(str(r.get("zone_type")), prefix) or r.get("state") == "invalidated":
            continue
        out.append({"level": float(r["zone_low"]), "strength": r.get("strength"), "formed_date": r.get("formed_at"),
                    "role": role_of(str(r["zone_type"]))})
    return out


def remember_dyn_sr(dyn_sr: Optional[Mapping[str, Any]], rows: Optional[Iterable[Mapping[str, Any]]], timeframe: str, df: Optional[pd.DataFrame],
                    now: Any, settings: Optional[ZoneSettings] = None, role_by_price: bool = True) -> Optional[Mapping[str, Any]]:
    """compute_dynamic_sr() output + the stored levels -> the same {"support": [...], "resistance": [...]} with memory
    (Trade remember_dyn_sr; Trade fixes the role there, ATP takes it from the price unless role_by_price=False)."""
    if df is None or not len(df):
        return dyn_sr
    fresh = [{"level": z["level"], "touches": z["touches"], "role": role}
             for key, role in (("support", SUPPORT), ("resistance", RESISTANCE)) for z in (dyn_sr or {}).get(key, [])]
    merged = merge_levels(existing_from_zones(rows, timeframe), fresh, df, float(df["close"].iloc[-1]), now,
                          retire_days=retire_days_for(timeframe, settings), role_by_price=role_by_price, settings=settings)
    out: Dict[str, List[Dict[str, Any]]] = {"support": [], "resistance": []}
    for z in merged:
        out["support" if z["role"] == SUPPORT else "resistance"].append({"level": z["level"], "touches": z["strength"]})
    return out


def apply_memory(rows: List[Dict[str, Any]], previous: Optional[Iterable[Mapping[str, Any]]], frames: Mapping[str, Optional[pd.DataFrame]],
                 now: Any, settings: Optional[ZoneSettings] = None, prefix: str = DYNAMIC_PREFIX,
                 role_by_price: bool = True) -> List[Dict[str, Any]]:
    """The nightly refresh with memory (Trade apply_memory_to_zone_rows): for each timeframe in `frames` (its bars), the
    rows of one family (`prefix` DYNAMIC_SR_ or "" for classical S/R) are replaced by the merge of today's levels and the
    previous run's live levels; every other row is untouched. A new level is stamped `now`; a remembered one keeps its
    formed time. Merged rows carry "remembered" (True when not found today). settings.memory_enabled False -> unchanged."""
    s = _s(settings)
    if not rows or not s.memory_enabled:
        return rows
    previous = list(previous or [])
    out = rows
    for tf, df in frames.items():
        if df is None or not len(df):
            continue
        mine = [r for r in out if r.get("timeframe") == tf and _is_family(str(r["zone_type"]), prefix)]
        fresh = [{"level": float(r["zone_low"]), "touches": float(r.get("strength") or 0), "role": role_of(str(r["zone_type"]))} for r in mine]
        merged = merge_levels(existing_from_zones(previous, tf, prefix), fresh, df, float(df["close"].iloc[-1]), now,
                              retire_days=retire_days_for(tf, s), role_by_price=role_by_price, settings=s, stamp=now)
        keep = [r for r in out if not (r.get("timeframe") == tf and _is_family(str(r["zone_type"]), prefix))]
        new_rows = [{"zone_type": f"{prefix}{z['role']}", "timeframe": tf, "zone_low": z["level"], "zone_high": z["level"],
                     "strength": z["strength"], "formed_at": z["formed_date"], "confirmed_at": z["formed_date"], "state": "fresh",
                     "first_touch": None, "remembered": not z["fresh"]} for z in merged]
        out = keep + new_rows
    return out
