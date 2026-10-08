"""Settings for the ported price-action logic (reversal, breaks, causal swings, level strength).

Ported from Trade@0df3e09d0aa5942e3352327d5b998c5f6939ac61, elliott/settings.py (the "degrees", "breaks", "trigger" and
"candle" sections) and price_action/candles.py / price_action/legs.py (module constants of the score mode and LegConfig).

Every threshold is a setting and every distance is a multiple of the market's own noise (median candle range or ATR),
never fixed points. `validate()` returns a cleaned copy plus the list of problems (bad values fall back to the default,
the caller decides whether to show the errors).
"""
from typing import Any, Dict, List, Tuple

DEFAULTS: Dict[str, Any] = {
    # --- reversal: which behaviour (both Trade versions are kept and selectable) -----------------------------------------
    # "composite": elliott/reversal.py (E2 + C1) - 0..1 score from wick / close location / body / time / divergence.
    # "score100":  price_action/candles.py (#241) - 0..100 score from wick / close location / bounce / sweep / speed.
    "reversal_mode": "composite",
    # --- noise ------------------------------------------------------------------------------------------------------------
    "median_range_n": 20,             # median (high - low) of this many closed bars before the window
    "atr_len": 14,
    # --- composite reversal (elliott/reversal.py) -------------------------------------------------------------------------
    "touch_reclaim_window": 3,        # composite of the last 1..N closed candles (hammer / engulfing / star in one rule)
    "touch_tol_mr": 0.0,              # a level counts as touched within this many median ranges
    "strength_min": 1.2,              # composite range >= this x median range ("a real push")
    "strength_max": 2.5,              # composite range > this x median range = exhaustion / news spike
    "strength_cap_mode": "fixed",     # fixed: above strength_max always rejects; logic: rejects only when it closes weak
    "strength_risk_guard_mult": 0.0,  # logic mode: still reject above this x median range (0 = off)
    "indecision_band": [0.40, 0.60],  # close location inside this band = indecision -> follow-through needed
    "rejection_weights": [0.30, 0.30, 0.20, 0.20, 0.00],   # wick, close location, body, time, divergence
    "rejection_min": 0.60,
    "reclaim_ref": "touched_level",   # touched_level: close back above the deepest level touched; zone_high: above the zone top
    "path_checks": False,             # N >= 2: the last candle may not give back more than half the composite range
    "n3_penalty": 0.0,
    "body_term_mode": "bull_body",    # bull_body | body_or_reclaim
    "min_body_frac": 0.10,
    "min_body_or_reclaim": False,
    "w_reclaim_depth": 0.0,
    "w_overlap": 0.0,
    "followthrough_mode": "legacy",   # legacy: N=3 indecisive + next candle in direction => N+1; addendum: see reversal.py
    "followthrough_max_bars": 1,
    "followthrough_score_gate": False,
    "followthrough_score_relax": 0.05,
    # --- score100 reversal (price_action/candles.py #241) -----------------------------------------------------------------
    "score_min": 60.0,
    "score_touch_tol_pct": 0.10,      # touch tolerance as % of the level (the #241 rule)
    "score_weights": {"wick": 30.0, "close_loc": 20.0, "bounce": 20.0, "sweep": 15.0},
    "score_speed": [15.0, 10.0, 5.0],  # points for N = 1, 2, 3 (N = 4 follow-through gets 0)
    "score_bounce_full_mr": 2.0,      # bounce scores full at this many median ranges off the low
    "score_min_median_candles": 10,
    # --- real break vs false break (elliott/breaks.py) --------------------------------------------------------------------
    "break_buffer_mr": 0.25,          # a close must be this many median ranges beyond the level
    "break_displacement_confirm": True,
    "break_close_loc": 0.30,          # displacement: the close sits in this fraction of the range at the break-side end
    "break_no_reclaim_bars": 1,       # acceptance: this many following closes stay beyond (0 = one close decides)
    "break_retest_confirm": True,     # a failed retest (rejected from the broken side) also confirms
    "count_inv_basis": "real_break",  # real_break | wick
    "inv_buffer_mr": 0.5,
    # --- causal swings (elliott/swings.py) --------------------------------------------------------------------------------
    "degree_levels": 4,
    "swing_mode": "atr",              # atr | pct | fractal
    "swing_atr_mult": [1.5, 3.0, 6.0, 12.0],
    "swing_pct": [0.15, 0.30, 0.60, 1.20],
    "swing_fractal_r": [2, 4, 8, 16],
    "similarity_balance_min": 1 / 3,
    "structure_tf": "5m",
    "degree_tf_mode": "auto_by_bars",  # auto_by_bars | fixed
    "degree_tf": ["5m", "15m", "1h", "1d"],
    "auto_tfs": ["5m", "15m", "30m", "1h", "1d"],
    "tf_bars_min": 8,
    "tf_bars_max": 40,
    # --- level strength and zone events (price_action/level_strength.py, legs.py LegConfig) -------------------------------
    "zone_median_n": 20,
    "displacement_body_mr": 1.5,      # displacement candle: body >= this x median range ...
    "displacement_body_frac": 0.6,    # ... and body >= this fraction of its range
    "zone_n_reclaim": 2,              # a close beyond the zone is a BREAK unless a close is back inside within this many bars
    "zone_recency_tau_bars": 200.0,
    "zone_round_steps": {"NIFTY": [100, 500, 1000], "BANKNIFTY": [500, 1000], "SENSEX": [500, 1000], "DEFAULT": [100, 500, 1000]},
    "zone_tpo_sessions": 5,
}

TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 375}
CHOICES = {
    "reversal_mode": ("composite", "score100"),
    "strength_cap_mode": ("fixed", "logic"),
    "reclaim_ref": ("touched_level", "zone_high"),
    "body_term_mode": ("bull_body", "body_or_reclaim"),
    "followthrough_mode": ("legacy", "addendum"),
    "count_inv_basis": ("real_break", "wick"),
    "swing_mode": ("atr", "pct", "fractal"),
    "degree_tf_mode": ("auto_by_bars", "fixed"),
}
# The candle keys a real-break retest never changes (Trade `core_candle`): candle experiments must not move breaks.
CANDLE_KEYS = ("strength_cap_mode", "strength_risk_guard_mult", "w_reclaim_depth", "w_overlap", "path_checks", "n3_penalty",
               "body_term_mode", "min_body_frac", "min_body_or_reclaim", "followthrough_mode", "followthrough_max_bars",
               "followthrough_score_gate", "followthrough_score_relax")


def _as_list(v: Any, cast) -> List:
    if isinstance(v, str):
        v = [x for x in v.replace(";", ",").split(",") if x.strip()]
    return [cast(x) for x in v]


# Allowed ranges of the numeric settings (inclusive). Integers default to 1..500, floats to 0..1000.
INT_RANGES: Dict[str, Tuple[int, int]] = {"followthrough_max_bars": (0, 10), "break_no_reclaim_bars": (0, 50), "degree_levels": (1, 6),
                                          "touch_reclaim_window": (1, 50)}
FLOAT_RANGES: Dict[str, Tuple[float, float]] = {"score_min": (0.0, 100.0), "similarity_balance_min": (0.0, 1.0),
                                                "break_close_loc": (0.0, 1.0), "min_body_frac": (0.0, 1.0),
                                                "displacement_body_frac": (0.0, 1.0)}


def validate(overrides: Dict[str, Any] = None) -> Tuple[Dict[str, Any], List[str]]:
    """Defaults + overrides, checked. Unknown keys are an error; a bad value falls back to its default."""
    clean = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    errors: List[str] = []
    for k, v in (overrides or {}).items():
        if k not in DEFAULTS:
            errors.append(f"unknown setting {k!r}")
            continue
        d = DEFAULTS[k]
        try:
            if isinstance(d, bool):
                clean[k] = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(d, int):
                clean[k] = int(v)
            elif isinstance(d, float):
                clean[k] = float(v)
            elif isinstance(d, list):
                clean[k] = _as_list(v, type(d[0]))
            elif isinstance(d, dict):
                clean[k] = dict(v)
            else:
                clean[k] = str(v)
        except (TypeError, ValueError):
            errors.append(f"{k}: invalid value {v!r} - default used")
            clean[k] = DEFAULTS[k]
        if k in CHOICES and clean[k] not in CHOICES[k]:
            errors.append(f"{k}: must be one of {CHOICES[k]} - default used")
            clean[k] = DEFAULTS[k]
        if isinstance(d, (int, float)) and not isinstance(d, bool):
            lo, hi = (INT_RANGES.get(k, (1, 500)) if isinstance(d, int) else FLOAT_RANGES.get(k, (0.0, 1000.0)))
            if not (lo <= clean[k] <= hi):                  # also false for NaN
                errors.append(f"{k}: must be between {lo} and {hi} - default used")
                clean[k] = DEFAULTS[k]
    if clean["strength_min"] >= clean["strength_max"]:
        errors.append("strength_min must be below strength_max - defaults used")
        clean["strength_min"], clean["strength_max"] = DEFAULTS["strength_min"], DEFAULTS["strength_max"]
    w = clean["rejection_weights"]
    if len(w) != 5 or any(x < 0 for x in w) or sum(w[:3]) <= 0:
        errors.append("rejection_weights: five non-negative weights with wick+close+body > 0 - default used")
        clean["rejection_weights"] = list(DEFAULTS["rejection_weights"])
    band = clean["indecision_band"]
    if len(band) != 2 or not 0.0 <= band[0] < band[1] <= 1.0:
        errors.append("indecision_band: two values 0 <= low < high <= 1 - default used")
        clean["indecision_band"] = list(DEFAULTS["indecision_band"])
    if len(clean["score_speed"]) != 3:
        errors.append("score_speed: three values (N = 1, 2, 3) - default used")
        clean["score_speed"] = list(DEFAULTS["score_speed"])
    key = {"atr": "swing_atr_mult", "pct": "swing_pct", "fractal": "swing_fractal_r"}[clean["swing_mode"]]
    used = [key] + (["degree_tf"] if clean["degree_tf_mode"] == "fixed" else [])
    for k in used:
        vals = clean[k][:clean["degree_levels"]]
        order = [TF_MIN.get(x, 0) for x in vals] if k == "degree_tf" else vals
        if len(vals) < clean["degree_levels"] or any(b <= a for a, b in zip(order, order[1:])):
            errors.append(f"{k}: needs {clean['degree_levels']} values that grow with the degree - default used")
            clean[k] = list(DEFAULTS[k])
    if clean["inv_buffer_mr"] < clean["break_buffer_mr"]:
        errors.append("inv_buffer_mr below break_buffer_mr - raised to it")
        clean["inv_buffer_mr"] = clean["break_buffer_mr"]
    return clean, errors


def settings(**overrides: Any) -> Dict[str, Any]:
    """Validated settings; raises ValueError on any problem (for code paths that must not silently fall back)."""
    clean, errors = validate(overrides)
    if errors:
        raise ValueError("; ".join(errors))
    return clean


def core_candle(s: Dict[str, Any]) -> Dict[str, Any]:
    """The candle keys at their defaults - used by the failed-retest break check so candle experiments never move breaks."""
    if all(s.get(k) == DEFAULTS[k] for k in CANDLE_KEYS):
        return s
    return {**s, **{k: DEFAULTS[k] for k in CANDLE_KEYS}}
