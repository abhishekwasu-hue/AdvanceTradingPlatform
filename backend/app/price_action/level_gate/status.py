"""N1 X1: the gate's reason codes, ported from Trade `bots_common/level_gate_bot.py` (`STATUS`, `ARMED_WHY`, `status_of`)
at the pinned revision - the codes the Trade bots write on their signal log ("SKIPPED_GATE_*"), here on the scan Then
template's status and the intent's evidence. Pure; `GATE_CONFIRMED` is ATP's code for a live entry (Trade enters there)."""
from typing import Any, Dict, Mapping

from app.price_action.level_gate import gate as LR

STATUS: Dict[str, str] = {
    LR.R_AGAINST: "SKIPPED_GATE_AGAINST_TREND", LR.R_NO_TREND: "SKIPPED_GATE_NO_TREND", LR.R_IMPULSIVE: "SKIPPED_GATE_IMPULSIVE",
    LR.R_NO_COMMIT: "SKIPPED_GATE_WAITING_COMMITMENT", LR.R_BROKEN: "SKIPPED_GATE_BROKEN", LR.R_TIMEOUT: "SKIPPED_GATE_TIMEOUT",
    LR.R_RR: "SKIPPED_GATE_RR", LR.R_NO_TARGET: "SKIPPED_GATE_NO_TARGET", LR.R_ERROR: "SKIPPED_GATE_ERROR",
}
ARMED_WHY: Dict[str, str] = {"absorbing": "SKIPPED_GATE_ABSORPTION", "low_volume": "SKIPPED_GATE_LOW_VOLUME"}
CONFIRMED_ENTRY = "GATE_CONFIRMED"


def status_of(r: Mapping[str, Any]) -> str:
    """Trade `level_gate_bot.status_of`: one code for a gate result (a live entry -> GATE_CONFIRMED)."""
    if r.get("entry"):
        return CONFIRMED_ENTRY
    why = str(r.get("reason") or "")
    if r.get("state") == LR.ARMED:
        for k, v in ARMED_WHY.items():
            if r.get(k):
                return v
    for k, v in STATUS.items():
        if why.startswith(k):
            return v
    if r.get("state") == LR.ARMED:
        return STATUS[LR.R_NO_COMMIT]
    if r.get("state") == LR.CONFIRMED:
        return "SKIPPED_GATE_ALREADY_CONFIRMED"
    if r.get("state") == "ERROR":
        return STATUS[LR.R_ERROR]
    return "SKIPPED_GATE_NOT_AT_AREA"
