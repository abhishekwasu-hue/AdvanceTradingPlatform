"""Zone lifecycle - the state enum and the transition function (owner spec, deliberate change from Trade).

Trade kills a zone (FILLED) the first time price comes back into it. ATP keeps it and tracks what happened:

    fresh --TOUCH / GAP_THROUGH--> tested          price came back (or jumped across an edge) - Trade's "filled" moment
    fresh / tested --FLIP--> flipped                closed through the zone and held on the other side (opposite role)
    any live state --STRUCTURAL_BREAK--> invalidated  price broke the structural swing beyond the zone (terminal)

A flipped zone that is touched again stays flipped (its current role still comes from the price - see
detect.role_by_price). Detecting FLIP and STRUCTURAL_BREAK on bars, and storing states, is Z7a-2; this module only
defines the states, the events and the pure transition so that every later piece shares one table.
"""
from enum import Enum
from typing import Dict, Iterable


class ZoneState(str, Enum):
    FRESH = "fresh"
    TESTED = "tested"
    FLIPPED = "flipped"
    INVALIDATED = "invalidated"


class ZoneEvent(str, Enum):
    TOUCH = "TOUCH"                        # a bar overlapped the zone
    GAP_THROUGH = "GAP_THROUGH"            # close -> next open jumped across a zone edge without a bar overlapping it
    FLIP = "FLIP"                          # closed through the zone and held beyond it (role reversed)
    STRUCTURAL_BREAK = "STRUCTURAL_BREAK"  # the structural swing beyond the zone broke


_TRANSITIONS: Dict[ZoneState, Dict[ZoneEvent, ZoneState]] = {
    ZoneState.FRESH: {ZoneEvent.TOUCH: ZoneState.TESTED, ZoneEvent.GAP_THROUGH: ZoneState.TESTED,
                      ZoneEvent.FLIP: ZoneState.FLIPPED, ZoneEvent.STRUCTURAL_BREAK: ZoneState.INVALIDATED},
    ZoneState.TESTED: {ZoneEvent.TOUCH: ZoneState.TESTED, ZoneEvent.GAP_THROUGH: ZoneState.TESTED,
                       ZoneEvent.FLIP: ZoneState.FLIPPED, ZoneEvent.STRUCTURAL_BREAK: ZoneState.INVALIDATED},
    ZoneState.FLIPPED: {ZoneEvent.TOUCH: ZoneState.FLIPPED, ZoneEvent.GAP_THROUGH: ZoneState.FLIPPED,
                        ZoneEvent.FLIP: ZoneState.FLIPPED, ZoneEvent.STRUCTURAL_BREAK: ZoneState.INVALIDATED},
    ZoneState.INVALIDATED: {},
}


def transition(state: ZoneState | str, event: ZoneEvent | str) -> ZoneState:
    """The state after `event`. INVALIDATED is terminal (every event leaves it unchanged). Unknown names raise ValueError."""
    st, ev = ZoneState(state), ZoneEvent(event)
    return _TRANSITIONS[st].get(ev, st)


def replay(events: Iterable[ZoneEvent | str], start: ZoneState | str = ZoneState.FRESH) -> ZoneState:
    """Fold a chronological event sequence into the final state."""
    st = ZoneState(start)
    for ev in events:
        st = transition(st, ev)
    return st


def is_live(state: ZoneState | str) -> bool:
    """Every state but INVALIDATED is still a zone to draw and to trade against."""
    return ZoneState(state) is not ZoneState.INVALIDATED
