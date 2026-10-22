"""Wave reading settings (owner question WAVE-6): the ratios that decide the corrective TYPE, as settings.

The zigzag / flat / expanded-flat type of a correction depends on B/A ratios. They are the ONLY numbers the wave
reading uses, and they live here as settings with today's values (the values the generic rules have always used,
`app/price_action/daily_trend/settings.py PATTERN_DEFAULTS`; a test keeps the two equal). The reading rules in this
package carry no number of their own (a test greps the modules for ratio literals). The Daily trend keeps its own
fixed values; a template that exposes these settings puts them in its parameters, so its params hash covers them.

* `zz_max_ba`         - a zigzag's B retraces less than this share of A (B can only grow, so a running B is checked too).
* `flat_min_ba`       - a flat's finished B retraces at least this share of A.
* `flat_b_max`        - a flat B beyond this multiple of A is only "strong evidence" (never a refusal).
* `flat_expanded_min` - a flat is EXPANDED once B is beyond this multiple of A and C has gone beyond A's end.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

WAVE_DEFAULTS: Dict[str, float] = {
    "zz_max_ba": 0.90,
    "flat_min_ba": 0.618,
    "flat_b_max": 2.0,
    "flat_expanded_min": 1.05,
}

# Inclusive bounds a user value must keep (the settings form shows them). A ratio is a share or multiple of wave A.
WAVE_BOUNDS: Dict[str, Tuple[float, float]] = {
    "zz_max_ba": (0.5, 1.0),
    "flat_min_ba": (0.382, 1.0),
    "flat_b_max": (1.0, 5.0),
    "flat_expanded_min": (1.0, 2.0),
}

WAVE_LABELS: Dict[str, str] = {
    "zz_max_ba": "Zigzag: largest B (share of A)",
    "flat_min_ba": "Flat: smallest B (share of A)",
    "flat_b_max": "Flat: B beyond this multiple of A is strong evidence",
    "flat_expanded_min": "Expanded flat: B beyond this multiple of A",
}


# The area a wave template reads at the end of a corrective wave (review finding WB11, owner question WAVE-3): swing zones
# by default - computed from the bars, so every backtest period has them - market zones only when the user picks them
# (stored snapshots behind their own flag: no snapshot, no area, never a refusal), or none.
AREA_SOURCES: Tuple[str, ...] = ("swing_zones", "market_zones", "none")
AREA_SOURCE_DEFAULT = "swing_zones"


def validate(overrides: Optional[Mapping[str, Any]] = None) -> Tuple[Dict[str, float], List[str]]:
    """The defaults updated with `overrides`, checked: unknown keys and values outside their bounds are problems (the
    default stays in place), and a zigzag limit below the flat minimum leaves a gap no correction reads - also a problem."""
    out = dict(WAVE_DEFAULTS)
    problems: List[str] = []
    for key, value in (overrides or {}).items():
        if key not in WAVE_DEFAULTS:
            problems.append(f"unknown wave setting {key!r}")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append(f"{WAVE_LABELS[key]} must be a number")
            continue
        low, high = WAVE_BOUNDS[key]
        if not low <= float(value) <= high:
            problems.append(f"{WAVE_LABELS[key]} must be between {low:g} and {high:g}")
            continue
        out[key] = float(value)
    if out["zz_max_ba"] < out["flat_min_ba"]:
        problems.append("the zigzag's largest B must not be below the flat's smallest B")
    return out, problems


def settings(overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, float]:
    """Validated settings; raises ValueError with every problem at once."""
    out, problems = validate(overrides)
    if problems:
        raise ValueError("; ".join(problems))
    return out


__all__ = ["WAVE_DEFAULTS", "WAVE_BOUNDS", "WAVE_LABELS", "AREA_SOURCES", "AREA_SOURCE_DEFAULT", "validate", "settings"]
