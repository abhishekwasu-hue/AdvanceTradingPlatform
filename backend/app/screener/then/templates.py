"""N1 X1: the scan Then template - what it may hold and how it reads back."""
from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

from app.db.models import ScanThenTemplateRecord
from app.price_action.level_gate.settings import GATE_DEFAULTS, gate_settings

FLAG = "scan_then"
MODES = ("NOTIFY", "ONE_CLICK", "AUTO_PAPER")          # AUTO_PAPER stays a proposal (core policy); AUTO_LIVE is never saved
CONFIRM_TFS: Dict[str, int] = {"5m": 5, "15m": 15}      # Trade: 5M Instant / Classic confirm on 5m, 15M SRv2 / MCX on 15m
DEFAULT_TF = "5m"

# The gate settings a person may change, with their bounds (Trade's defaults otherwise).
GATE_BOUNDS: Dict[str, Any] = {
    "min_rr": (1.0, 10.0), "max_armed_bars": (2, 40), "mr_n": (5, 100), "approach_bars": (1, 10), "approach_wide_mr": (0.5, 5.0),
    "approach_close_frac": (0.0, 1.0), "approach_min_wide": (1, 10), "sl_buffer_mr": (0.0, 3.0),
    "wait_next_candle": bool, "allow_parent_correction": bool,
}


class TemplateError(ValueError):
    """A template the platform does not accept (the reason is shown as is)."""


def check_mode(mode: str) -> str:
    value = (mode or "").strip().upper()
    if value == "AUTO_LIVE":
        raise TemplateError("Auto LIVE is not available: ATP places no broker API order (PAPER only)")
    if value not in MODES:
        raise TemplateError(f"mode must be one of {', '.join(MODES)}")
    return value


def check_gate(over: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Only known keys within their bounds; returns the overrides as stored (Trade's defaults fill the rest at run time)."""
    out: Dict[str, Any] = {}
    for key, value in (over or {}).items():
        if key not in GATE_BOUNDS:
            raise TemplateError(f"unknown gate setting '{key}' - one of {', '.join(sorted(GATE_BOUNDS))}")
        bound = GATE_BOUNDS[key]
        if bound is bool:
            if not isinstance(value, bool):
                raise TemplateError(f"{key} must be true or false")
            out[key] = value
            continue
        lo, hi = bound
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TemplateError(f"{key} must be a number")
        cast = type(GATE_DEFAULTS[key])(value)
        if not lo <= cast <= hi:
            raise TemplateError(f"{key} must be between {lo:g} and {hi:g}")
        out[key] = cast
    return out


def settings_of(row: ScanThenTemplateRecord) -> Dict[str, Any]:
    try:
        stored = json.loads(row.gate_settings_json or "{}")
    except ValueError:
        stored = {}
    return gate_settings(stored if isinstance(stored, dict) else {})


def as_dict(row: Optional[ScanThenTemplateRecord], rule_id: int, include_status: bool = True) -> Dict[str, Any]:
    if row is None:
        return {"rule_id": rule_id, "configured": False, "enabled": False, "mode": "NOTIFY", "confirm_tf": DEFAULT_TF, "gate": {},
                "gate_defaults": dict(GATE_DEFAULTS), "modes": list(MODES), "confirm_tfs": list(CONFIRM_TFS)}
    try:
        status = json.loads(row.last_status_json) if (row.last_status_json and include_status) else {}
    except ValueError:
        status = {}
    return {"rule_id": rule_id, "configured": True, "enabled": bool(row.enabled), "mode": row.mode, "confirm_tf": row.confirm_tf,
            "gate": json.loads(row.gate_settings_json or "{}"), "gate_defaults": dict(GATE_DEFAULTS), "modes": list(MODES),
            "confirm_tfs": list(CONFIRM_TFS), "user_id": row.user_id,
            "last_bar_at": row.last_bar_at.isoformat() if row.last_bar_at else None,
            "last_problem": row.last_problem if include_status else None,
            "status": status, "updated_at": row.updated_at.isoformat() if row.updated_at else None}
