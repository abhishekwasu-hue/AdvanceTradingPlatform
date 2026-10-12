"""N1 L1: the level gate's settings at the pinned Trade revision (every value is Trade's default; goldens check them).

* `GATE_DEFAULTS`   - Trade `bots_common/level_reaction.py::DEFAULTS` (the gate's own numbers).
* `METHOD_DEFAULTS` - the keys of Trade `decision3/settings.py::DEFAULTS` the gate's decision3 functions read.
* `BREAK_DEFAULTS`  - the keys of Trade `elliott/settings.py::DEFAULTS` `break_from` reads. `break_accept_closes` (3) is
  Trade's newer time-acceptance rule; ATP's other break callers keep it off (`pa_settings.DEFAULTS` has no such key).
  Inside the gate it is inert, as in Trade: `break_from` looks only at [j, j + break_no_reclaim_bars] = [j, j + 1], and three
  closes beyond the level need bar j + 2. It takes effect only with `break_no_reclaim_bars` >= 2 (a golden case covers 3).
"""
from typing import Any, Dict, Mapping, Optional

TRADE_REVISION = "1e6c9ac65e2ac0b859065c4db2a3eabf2a65c08c"

GATE_DEFAULTS: Dict[str, Any] = {
    "min_rr": 3.0,                  # minimum reward:risk
    "max_armed_bars": 8,            # no commitment within this many closed bars after arming -> timeout
    "mr_n": 20,                     # median range = median (high - low) of this many previous closed bars
    "approach_bars": 3,             # the approach = the arming candle and the ones before it, this many
    "approach_wide_mr": 1.5,        # an impulsive candle: range >= this x median range ...
    "approach_close_frac": 0.25,    # ... closing within this fraction of its range at the approach-side end
    "approach_min_wide": 2,         # this many such candles in a row -> impulsive drive (no entry)
    "sl_buffer_mr": 0.25,           # SL beyond the far edge (and the armed-period extreme) by this x median range
    "wait_next_candle": False,      # True: CONFIRMED only if the candle after the commitment does not close back inside
    "allow_parent_correction": False,   # entries while a parent correction runs (A1)? default no (fail-safe)
    "grade_b_lot_mult": 1.0,        # grade B -> lots x this
    "trend_retry_min": 15,          # (L2) a failed trend read is not retried for this many minutes
}

METHOD_DEFAULTS: Dict[str, Any] = {
    "power_shift_min": 2,
    "power_a_rule": "relative",
    "commit_dom_mult": 1.5,
    "study_min_bars": 4,
    "pattern_eq_mr": 0.5,
    "power_overlap": 0.5,
    "commit_body": 0.5,
    "commit_beyond": "close",
    "pin_wick_min": 0.5,
    "star_body_max": 0.3,
    "tweezer_tol": 0.1,
    "commit_close_frac": 2 / 3,
    "entry_start": "09:30",
    "entry_end": "15:15",
    "candle_avg_n": 5,
    "doji_body_max": 0.1,
    "power_fallback_bars": 8,
}

BREAK_DEFAULTS: Dict[str, Any] = {
    "median_range_n": 20,
    "break_buffer_mr": 0.25,
    "break_displacement_confirm": True,
    "break_close_loc": 0.30,
    "strength_min": 1.2,
    "break_no_reclaim_bars": 1,
    "break_accept_closes": 3,
}


def gate_settings(over: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Trade `level_reaction.settings`: known keys only, a None value ignored, cast to the default's type."""
    s = dict(GATE_DEFAULTS)
    for key, value in (over or {}).items():
        if key in GATE_DEFAULTS and value is not None:
            default = GATE_DEFAULTS[key]
            s[key] = bool(value) if isinstance(default, bool) else type(default)(value)
    return s
