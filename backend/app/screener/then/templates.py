"""N1 X1: the scan Then template - what it may hold and how it reads back."""
from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

from app.db.models import ExecutionIntentRecord, ScanThenTemplateRecord
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


# N1 X2: the template owner's Auto PAPER arming, kept in `gate_settings_json` under this key (no migration). Disarmed
# is the safe default: a missing, malformed or foreign key (another user, another mode) is "not armed".
ARM_KEY = "_auto_paper_arm"
ARM_CONFIRM = "AUTO_PAPER"


def _stored(row: Optional[ScanThenTemplateRecord]) -> Dict[str, Any]:
    if row is None:
        return {}
    try:
        stored = json.loads(row.gate_settings_json or "{}")
    except (TypeError, ValueError):
        stored = {}
    return stored if isinstance(stored, dict) else {}


def gate_overrides(row: Optional[ScanThenTemplateRecord]) -> Dict[str, Any]:
    """The person's gate overrides (the arming key left out)."""
    return {k: v for k, v in _stored(row).items() if k != ARM_KEY}


def settings_of(row: ScanThenTemplateRecord) -> Dict[str, Any]:
    return gate_settings(gate_overrides(row))


def arm_of(row: Optional[ScanThenTemplateRecord]) -> Optional[Dict[str, Any]]:
    """The arming ({at, by}) when it is valid: an AUTO_PAPER template, armed by its own owner. Anything else is None."""
    if row is None or row.mode != "AUTO_PAPER" or row.user_id is None:
        return None
    arm = _stored(row).get(ARM_KEY)
    if not isinstance(arm, dict):
        return None
    at, by = arm.get("at"), arm.get("by")
    if not isinstance(at, str) or not at or isinstance(by, bool) or not isinstance(by, int) or by != row.user_id:
        return None
    return {"at": at, "by": by}


def is_armed(row: Optional[ScanThenTemplateRecord]) -> bool:
    return arm_of(row) is not None


def set_arm(row: ScanThenTemplateRecord, user_id: Optional[int], at: Optional[str]) -> None:
    """Arms (user_id and at given) or disarms (None) the template, keeping its gate overrides."""
    stored = gate_overrides(row)
    if user_id is not None and at:
        stored[ARM_KEY] = {"at": at, "by": int(user_id)}
    row.gate_settings_json = json.dumps(stored, sort_keys=True)


def as_dict(row: Optional[ScanThenTemplateRecord], rule_id: int, include_status: bool = True,
            auto_paper: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if row is None:
        return {"rule_id": rule_id, "configured": False, "enabled": False, "mode": "NOTIFY", "confirm_tf": DEFAULT_TF, "gate": {},
                "gate_defaults": dict(GATE_DEFAULTS), "modes": list(MODES), "confirm_tfs": list(CONFIRM_TFS),
                "status_shown": include_status,
                "auto_paper": auto_paper or {"available": False, "armed": False, "why": "no template", "today": 0, "max_per_day": 0}}
    try:
        status = json.loads(row.last_status_json) if (row.last_status_json and include_status) else {}
    except ValueError:
        status = {}
    return {"rule_id": rule_id, "configured": True, "enabled": bool(row.enabled), "mode": row.mode, "confirm_tf": row.confirm_tf,
            "gate": gate_overrides(row), "gate_defaults": dict(GATE_DEFAULTS), "modes": list(MODES),
            "confirm_tfs": list(CONFIRM_TFS), "user_id": row.user_id,
            "last_bar_at": row.last_bar_at.isoformat() if row.last_bar_at else None,
            "last_problem": row.last_problem if include_status else None,
            "status": status, "status_shown": include_status, "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "auto_paper": auto_paper or {"available": False, "armed": is_armed(row), "why": "", "today": 0, "max_per_day": 0}}


def intent_dict(row: ExecutionIntentRecord) -> Dict[str, Any]:
    """An intent as the Then drawer lists it: the setup (levels, gate status and lines) and what the core decided."""
    try:
        evidence = json.loads(row.evidence_json or "{}")
        signal = json.loads(row.signal_json or "{}")
    except ValueError:
        evidence, signal = {}, {}
    ticket = evidence.get("ticket") or {}
    return {"id": row.id, "symbol": row.symbol, "direction": row.direction, "mode": row.requested_mode, "status": row.status,
            "route": row.route, "reason": row.reason, "ai_action_id": row.ai_action_id, "notification_id": row.notification_id,
            "gate_status": evidence.get("gate_status"), "entry": signal.get("entry"), "stop_loss": signal.get("stop_loss"),
            "target": signal.get("target1"), "rr": ticket.get("rr"), "grade": ticket.get("grade"), "study": ticket.get("study"),
            "commit_line": ticket.get("commit_line"), "confirm_ts": ticket.get("confirm_ts"),
            "created_at": row.created_at.isoformat() if row.created_at else None}
