"""Z5 (Trade port): the chance a short option strike is breached before expiry - Trade repo `strike_breach_model.py`.

The question the model answers: "a short strike this far away - how likely is the price to be beyond it at expiry (or to
touch it before)?" and its inverse "how far away must the strike be for a breach risk of X %?". It is a logistic
regression fitted in the Trade repo on NIFTY daily data (in-sample 2015-2021, calibrated on 2022 to 2024-03, sealed
holdout untouched). ATP does not refit: the frozen coefficients of Trade's full model on z_rv are copied below (Trade
`docs/reports/strike_breach_model_coef.json`, models "close" and "touch", the full z_rv model), and the inputs are built
exactly as Trade builds them:

* distance  z = |ln(K / spot)| / (sigma20 x sqrt(h)); sigma20 = standard deviation (ddof 1) of the last 20 daily
  close-to-close log returns; h = sessions from the entry day to expiry, both counted (DTE + 1).
* regime from ADX14 (Wilder) of the previous days: above 25 a trend - "with" when the +DI/-DI direction times the side
  is positive (an uptrend for a short put), "against" when negative; below 20 a range; else mid.
* the entry weekday, the 0-DTE flag, the side (put / call) and the daily market structure (with / against / neutral).
  ATP has no port of Trade's StructureTracker yet, so callers pass the structure when they know it and neutral (0)
  otherwise - neutral is what Trade's own distance table uses.
* outcome "close": beyond the strike at the expiry close (expires in the money - the main one); "touch": beyond it at
  any time before expiry (for stop-based management).

Every feature uses only bars before the entry day (no look-ahead; a test checks it). The fit covers DTE 0-9 (the near
and next weekly expiry) and z between 0.25 and 3; outside those the result is flagged `extrapolated`. It is a model
estimate for research - never a forecast or a recommendation - and it was fitted on NIFTY only: for another underlying
it is NIFTY's behaviour applied (Trade's `predict_frozen` does the same). Pure functions; nothing here touches an order.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

import numpy as np

GRID = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)
Z_RANGE = (GRID[0], GRID[-1])
MAX_FITTED_DTE = 9
TARGETS = (0.05, 0.10, 0.20)
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
REGIMES = ("trend_with", "trend_against", "mid", "range")
OUTCOMES = ("close", "touch")
MIN_BARS = 30                       # sigma20 needs 21 closes, ADX14 needs 28 bars
FIT_NOTE = "Model estimate (logistic fit on NIFTY daily data 2015-2021, calibrated 2022-2024); not a forecast or a recommendation."

NAMES = ("const", "z", "z2", "ln_h", "dte0", "wd_Tue", "wd_Wed", "wd_Thu", "wd_Fri", "reg_trend_with", "reg_trend_against", "reg_range",
         "struct_with", "struct_against", "call")
# Frozen in the Trade repo (strike_breach_model_coef.json, ridge 1.0); same order as NAMES.
COEF: Dict[str, Sequence[float]] = {
    "close": (-0.256622060869769, -1.7429828462873491, 0.016298396033448237, 0.10028690904445194, -0.14519479601597657, -0.05184054051208463,
              -0.056286173424190185, 0.018748196785060955, 0.01943193651076746, 0.01759606701141398, 0.18355776429560347, 0.20300882964971162,
              -0.10970851998710532, -0.039684493685137566, 0.10437774690067356),
    "touch": (1.3478120596160699, -2.9767685071336896, 0.3587693672844141, 0.22463073366757053, -0.08701431621548247, -0.05026179275071399,
              -0.005269966896931339, 0.036078089019641386, 0.04505388760527111, 0.0036283397708166097, 0.21359203114659847, 0.16150418232964855,
              0.0980051369462553, 0.04210967470506068, -0.2719458422253948),
}


# --- indicators (Trade `wilder`, `daily_features`) ----------------------------------------------------------------------

def wilder(x: Union[Sequence[float], np.ndarray], n: int) -> np.ndarray:
    """Wilder's smoothing: the mean of the first n values, then out[i] = out[i-1] + (x[i] - out[i-1]) / n."""
    arr = np.asarray(x, float)
    out = np.full(len(arr), np.nan)
    if len(arr) < n:
        return out
    out[n - 1] = np.nanmean(arr[:n])
    for i in range(n, len(arr)):
        out[i] = out[i - 1] + (arr[i] - out[i - 1]) / n
    return out


def indicator_series(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> Dict[str, np.ndarray]:
    """sigma20, ATR14, ADX14 and the +DI/-DI direction at every bar, using that bar and the ones before it. The value
    for an entry on day i is the one at bar i-1 (Trade lags the same series by one day)."""
    h, lo, c = np.asarray(highs, float), np.asarray(lows, float), np.asarray(closes, float)
    if not len(h) == len(lo) == len(c):
        raise ValueError("highs, lows and closes must have the same length")
    pc = np.r_[np.nan, c[:-1]]
    with np.errstate(invalid="ignore", divide="ignore"):
        tr = np.nanmax(np.c_[h - lo, np.abs(h - pc), np.abs(lo - pc)], axis=1)
        tr[0] = h[0] - lo[0]
        up, dn = np.r_[np.nan, np.diff(h)], np.r_[np.nan, -np.diff(lo)]
        pdm = np.where((up > dn) & (up > 0), up, 0.0)
        ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
        atr = wilder(tr, 14)
        pdi = 100 * wilder(pdm, 14) / atr
        ndi = 100 * wilder(ndm, 14) / atr
        dx = 100 * np.abs(pdi - ndi) / (pdi + ndi)
        adx = np.full(len(dx), np.nan)
        first = np.where(np.isfinite(dx))[0]
        if len(first) >= 14:
            adx[first[0]:] = wilder(dx[first[0]:], 14)
        lr = np.r_[np.nan, np.diff(np.log(c))]
    sigma = np.full(len(c), np.nan)
    for i in range(20, len(c)):
        window = lr[i - 19:i + 1]
        if np.all(np.isfinite(window)):
            sigma[i] = float(np.std(window, ddof=1))
    return {"sigma20": sigma, "atr14": atr, "adx": adx, "di_dir": np.sign(pdi - ndi)}


def features_from_bars(bars: Sequence[Mapping[str, float]]) -> Optional[Dict[str, float]]:
    """The inputs for an entry on the session after the last bar (each bar: high, low, close; oldest first). None when
    the history is too short or a value is not finite."""
    if len(bars) < MIN_BARS:
        return None
    series = indicator_series([b["high"] for b in bars], [b["low"] for b in bars], [b["close"] for b in bars])
    out = {k: float(v[-1]) for k, v in series.items()}
    if not all(math.isfinite(v) for v in out.values()) or out["sigma20"] <= 0:
        return None
    return out


def regime_of(adx: float, align: float) -> str:
    """Trade `regime_of`: ADX above 25 is a trend (with / against by the DI direction x side), below 20 a range."""
    if adx > 25:
        return "trend_with" if align > 0 else ("trend_against" if align < 0 else "mid")
    return "range" if adx < 20 else "mid"


# --- the model ---------------------------------------------------------------------------------------------------------

def _row(z: float, h: int, weekday: int, regime: str, struct: int, call: bool) -> np.ndarray:
    x = {"const": 1.0, "z": z, "z2": z * z, "ln_h": math.log(h), "dte0": float(h == 1), "call": float(call),
         "struct_with": float(struct > 0), "struct_against": float(struct < 0)}
    for wd in range(1, 5):
        x[f"wd_{WEEKDAYS[wd]}"] = float(weekday == wd)
    for r in ("trend_with", "trend_against", "range"):
        x[f"reg_{r}"] = float(regime == r)
    return np.array([x[name] for name in NAMES])


def breach_probability(z: float, h: int, weekday: int, regime: str, *, struct: int = 0, call: bool = False, outcome: str = "close") -> float:
    """P(breach) from the frozen coefficients (Trade `predict` / `predict_frozen`). `weekday` 0 = Monday; `struct` +1 with
    the side, -1 against, 0 neutral; `call` True for a short call (strike above), False for a short put."""
    if outcome not in COEF:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    if h < 1:
        raise ValueError("h (sessions to expiry, both counted) must be at least 1")
    v = float(_row(z, h, weekday, regime, struct, call) @ np.asarray(COEF[outcome]))
    return 1.0 / (1.0 + math.exp(-float(np.clip(v, -35, 35))))


def required_z(target: float, h: int, weekday: int, regime: str, *, struct: int = 0, call: bool = False, outcome: str = "close",
               zmax: float = Z_RANGE[1]) -> Optional[float]:
    """Trade `required_z`: the distance z at which P(breach) equals `target` (bisection; a farther strike is less likely
    breached). None when the target is outside what z in [0, zmax] reaches (no extrapolation past the fitted grid)."""
    lo, hi = 0.0, zmax
    p_lo = breach_probability(lo, h, weekday, regime, struct=struct, call=call, outcome=outcome)
    p_hi = breach_probability(hi, h, weekday, regime, struct=struct, call=call, outcome=outcome)
    if p_lo < target or p_hi > target:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if breach_probability(mid, h, weekday, regime, struct=struct, call=call, outcome=outcome) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def z_of(strike: float, spot: float, sigma20: float, h: int) -> float:
    return abs(math.log(float(strike) / float(spot))) / (float(sigma20) * math.sqrt(h))


def distance_pct_for(z: float, sigma20: float, h: int, *, call: bool) -> float:
    """The strike distance, % of spot, that a distance z means today (Trade's table: K = spot x e^(-+ z sigma sqrt h))."""
    move = z * sigma20 * math.sqrt(h)
    return (math.exp(move) - 1.0) * 100.0 if call else (1.0 - math.exp(-move)) * 100.0


def sessions_to_expiry(as_of: date, expiry: date, holidays: Iterable[date] = ()) -> int:
    """Trading sessions from `as_of` to `expiry`, both counted (weekends and the given holidays skipped); 0 when expired."""
    if expiry < as_of:
        return 0
    off = set(holidays)
    n, day = 0, as_of
    while day <= expiry:
        if day.weekday() < 5 and day not in off:
            n += 1
        day += timedelta(days=1)
    return n


def short_strike_breach(legs: Sequence[Mapping[str, Any]], *, spot: float, as_of: date, features: Mapping[str, float], structure: int = 0,
                        holidays: Iterable[date] = (), targets: Sequence[float] = TARGETS) -> List[Dict[str, Any]]:
    """For every short option leg (direction SELL, CE or PE, with strike and expiry): the chance of a breach at the
    expiry close and of a touch before it, the distance z, the regime, and the distance needed for each target risk.
    `structure` is the daily market structure (+1 up, -1 down, 0 unknown / neutral); for each leg it becomes "with" or
    "against" the side as in Trade (an uptrend is "with" a short put)."""
    sigma, adx, di_dir = float(features["sigma20"]), float(features["adx"]), float(features["di_dir"])
    holiday_set = set(holidays)
    out: List[Dict[str, Any]] = []
    for index, leg in enumerate(legs):
        if leg.get("direction") != "SELL" or leg.get("option_type") not in ("CE", "PE"):
            continue
        call = leg["option_type"] == "CE"
        side = -1 if call else 1                         # Trade: +1 put (strike below), -1 call (strike above)
        strike = float(leg["strike"])
        expiry = leg["expiry"] if isinstance(leg["expiry"], date) else date.fromisoformat(str(leg["expiry"])[:10])
        h = sessions_to_expiry(as_of, expiry, holiday_set)
        item: Dict[str, Any] = {"leg": index, "option_type": leg["option_type"], "strike": strike, "side": "call" if call else "put",
                                "expiry": expiry.isoformat(), "sessions": h, "dte": h - 1 if h else None}
        if h < 1:
            out.append({**item, "status": "expired", "prob_close": None, "prob_touch": None})
            continue
        if (call and strike <= spot) or (not call and strike >= spot):
            out.append({**item, "status": "in_the_money", "note": "The price is already beyond this strike", "prob_close": None, "prob_touch": None})
            continue
        regime = regime_of(adx, di_dir * side)
        struct = int(np.sign(structure * side))
        weekday = as_of.weekday() if as_of.weekday() < 5 else 0
        z = z_of(strike, spot, sigma, h)
        needed = {}
        for target in targets:
            zt = required_z(target, h, weekday, regime, struct=struct, call=call)
            needed[f"{round(target * 100):g}"] = None if zt is None else {
                "z": round(zt, 3), "distance_pct": round(distance_pct_for(zt, sigma, h, call=call), 3),
                "strike": round(spot * (math.exp(zt * sigma * math.sqrt(h)) if call else math.exp(-zt * sigma * math.sqrt(h))), 2)}
        out.append({**item, "status": "ok", "z": round(z, 4), "distance_pct": round(abs(strike / spot - 1.0) * 100.0, 3), "regime": regime,
                    "weekday": WEEKDAYS[weekday], "struct": struct,
                    "prob_close": round(breach_probability(z, h, weekday, regime, struct=struct, call=call, outcome="close"), 4),
                    "prob_touch": round(breach_probability(z, h, weekday, regime, struct=struct, call=call, outcome="touch"), 4),
                    "extrapolated": not (Z_RANGE[0] <= z <= Z_RANGE[1]) or h - 1 > MAX_FITTED_DTE, "required_distance": needed})
    return out


def report(legs: Sequence[Mapping[str, Any]], *, spot: float, as_of: date, bars: Sequence[Mapping[str, float]], structure: int = 0,
           holidays: Iterable[date] = ()) -> Dict[str, Any]:
    """The builder's / proposal card's block: the features used and one row per short leg, or why there is none."""
    features = features_from_bars(bars)
    if features is None:
        return {"available": False, "reason": f"Needs at least {MIN_BARS} daily bars of the underlying", "legs": [], "note": FIT_NOTE}
    rows = short_strike_breach(legs, spot=spot, as_of=as_of, features=features, structure=structure, holidays=holidays)
    return {"available": True, "features": {k: round(v, 6) for k, v in features.items()}, "legs": rows, "note": FIT_NOTE,
            "reason": None if rows else "No short option leg"}
