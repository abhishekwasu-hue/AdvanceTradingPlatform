"""N1 E1: the execution core's routing policy - pure, no I/O.

Inputs are facts the dispatcher reads fresh for the intent's organisation (approval-only mode, the Phase 0 PAPER-only
lock) plus the mode the producer asked for. The answer is one route and its reason. The table is the whole policy:

| requested mode | approval-only on        | approval-only off                        |
|----------------|-------------------------|------------------------------------------|
| NOTIFY         | NOTIFY                  | NOTIFY                                   |
| ONE_CLICK      | PROPOSE                 | PROPOSE                                  |
| AUTO_PAPER     | PROPOSE (needs a person)| PAPER if Auto PAPER is enabled (X2), else PROPOSE |
| AUTO_LIVE      | REFUSE                  | REFUSE (N1 places no API order; the phase0_paper_only lock says so too) |

Anything unknown is refused. Levels are checked before a proposal or a PAPER entry: a proposal without a stop on the
right side is refused, never raised.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

NOTIFY, ONE_CLICK, AUTO_PAPER, AUTO_LIVE = "NOTIFY", "ONE_CLICK", "AUTO_PAPER", "AUTO_LIVE"
MODES = (NOTIFY, ONE_CLICK, AUTO_PAPER, AUTO_LIVE)

ROUTE_NOTIFY, ROUTE_PROPOSE, ROUTE_PAPER, ROUTE_REFUSE = "NOTIFY", "PROPOSE", "PAPER", "REFUSE"
ROUTES = (ROUTE_NOTIFY, ROUTE_PROPOSE, ROUTE_PAPER, ROUTE_REFUSE)

WHY_LIVE = "Auto LIVE is not available: ATP places no broker API order in N1 (PAPER only)"
WHY_LIVE_LOCKED = "Auto LIVE refused: the Phase 0 PAPER-only lock (phase0_paper_only) is on"
WHY_AUTO_APPROVAL = "Approval-only mode is on: Auto PAPER becomes a proposal a person approves"
WHY_AUTO_OFF = "Auto PAPER is not enabled for this organisation: a proposal instead"


@dataclass(frozen=True)
class PolicyInputs:
    mode: str
    approval_required: bool        # Z3 approval_only_entries for the organisation
    phase0_locked: bool            # Z10-c phase0_paper_only for the organisation
    auto_paper_enabled: bool = False   # U4 X2's switch; the E1 dispatcher always passes False


@dataclass(frozen=True)
class Decision:
    route: str
    reason: str


def decide(inputs: PolicyInputs) -> Decision:
    mode = (inputs.mode or "").strip().upper()
    if mode not in MODES:
        return Decision(ROUTE_REFUSE, f"Unknown mode '{inputs.mode}'")
    if mode == AUTO_LIVE:
        return Decision(ROUTE_REFUSE, WHY_LIVE_LOCKED if inputs.phase0_locked else WHY_LIVE)
    if mode == NOTIFY:
        return Decision(ROUTE_NOTIFY, "Notify only")
    if mode == ONE_CLICK:
        return Decision(ROUTE_PROPOSE, "One-click: a PAPER entry proposal")
    if inputs.approval_required:
        return Decision(ROUTE_PROPOSE, WHY_AUTO_APPROVAL)
    if not inputs.auto_paper_enabled:
        return Decision(ROUTE_PROPOSE, WHY_AUTO_OFF)
    return Decision(ROUTE_PAPER, "Auto PAPER")


def level_problems(direction: str, entry: Optional[float], stop_loss: Optional[float], target1: Optional[float], *,
                   need_stop: bool) -> List[str]:
    """Why these levels cannot make an entry (empty = fine). A long has its stop below and target above the entry; a
    short the reverse. `need_stop`: a proposal or a PAPER entry needs a stop; a notification does not."""
    problems: List[str] = []
    side = (direction or "").upper()
    if side not in ("LONG", "SHORT"):
        problems.append(f"Direction must be LONG or SHORT, not '{direction}'")
        return problems
    if entry is None or not entry > 0:
        problems.append("An entry price above zero is needed")
        return problems
    long = side == "LONG"
    if stop_loss is None:
        if need_stop:
            problems.append("A stop loss is needed")
    elif (long and not stop_loss < entry) or (not long and not stop_loss > entry):
        problems.append(f"The stop {stop_loss:g} is on the wrong side of the entry {entry:g} for a {side.lower()}")
    if target1 is not None and ((long and not target1 > entry) or (not long and not target1 < entry)):
        problems.append(f"Target 1 {target1:g} is on the wrong side of the entry {entry:g} for a {side.lower()}")
    return problems
