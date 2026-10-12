"""N1 L2: the Daily trend fold's settings at the pinned Trade revision (every value is Trade's default; goldens check them).

* `DEFAULTS`         - the keys of Trade `decision3/settings.py::DEFAULTS` the fold reads.
* `LEGS_DEFAULTS`    - the keys of Trade `legs2/settings2.py::DEFAULTS` the leg-nature check reads.
* `PATTERN_DEFAULTS` - the keys of Trade `patterns2/settings2.py::DEFAULTS` the zigzag / flat rules read.
"""
from typing import Any, Dict, Mapping, Optional

TRADE_REVISION = "1e6c9ac65e2ac0b859065c4db2a3eabf2a65c08c"

DEFAULTS: Dict[str, Any] = {
    "daily_trend_mode": "impulse",           # Q15: impulse-degree Dow ("minor" is a Trade what-if, not ported)
    "corr_degree_rule": "internal_pullback", # Q22: a same-degree correction goes beyond the impulse's largest inner pullback
    "corr_degree_frac": 0.5,                 # only for corr_degree_rule = "ratio" (the older what-if rule)
    "daily_start_nature_gate": True,         # Q28: a trend starts only with an impulse-natured leg
    "daily_parent_gate": True,               # A1: a counter impulse does not reverse the trend while the parent origin holds
    "q49_impulse_origin": "complete_impulse",  # Q49 (a): the origin of the impulse a counter-move corrects
    "range_after_trend": True,               # Q27: origin broken + two equal highs and two equal lows -> RANGE
    "maturity_sigma_d": 0.5,                 # "near" the target / the origin = this x sigma_D
    "daily_swing_method": "pivot",           # pivot (N bars each side) / dc (directional change, k x sigma_D)
    "daily_pivot_n": 2,
    "daily_dc_k": 1.0,
    "daily_sigma_sessions": 10,              # sigma_D = median Daily range of this many sessions
    "daily_min_sessions": 10,                # this many Daily candles or fewer -> UNKNOWN
    "range_eq_sigma_d": 0.25,                # "about equal" highs / lows = within this x sigma_D
}

LEGS_DEFAULTS: Dict[str, Any] = {
    "baseline_legs": 40,
    "baseline_min": 10,
    "c_hi": 0.60,
    "c_lo": 0.40,
    "short_leg_bars": 2,
}

PATTERN_DEFAULTS: Dict[str, Any] = {
    "zz_max_ba": 0.90,
    "flat_min_ba": 0.618,
    "flat_b_max": 2.0,
    "flat_expanded_min": 1.05,
}


def load(overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Trade `decision3.settings.load`: the defaults updated with the overrides."""
    s = dict(DEFAULTS)
    s.update(overrides or {})
    return s
