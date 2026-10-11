"""Zone lifecycle - states, events, the transition table and the bar-by-bar tracker (owner spec, deliberate change from Trade).

Trade kills a zone (FILLED) the first time price comes back into it. ATP keeps it and tracks what happened:

    fresh --TOUCH / GAP_THROUGH--> tested            price came back (or jumped across an edge) - Trade's "filled" moment
    fresh / tested --FLIP--> flipped                  closed through the zone and held on the other side (opposite role)
    flipped --FLIP--> tested                          closed back through and held: the original role again
    any live state --STRUCTURAL_BREAK--> invalidated  price broke the structural swing beyond the zone (terminal)

`track_zone` turns bars into those events (Z7a-2). The rules, each a setting:
  * the zone acts on one side: SUPPORT (price above it) or RESISTANCE (price below). It starts on its origin side -
    demand / bullish order block / up gap = support, supply / bearish order block / down gap = resistance, a line level
    (S/R, dynamic S/R) = by the price when it was first stored;
  * TOUCH: a bar overlaps the zone band coming from outside (one per visit - the touch count). GAP_THROUGH: no overlap,
    but price jumped from the previous close to this open across a band edge ("tested by gap");
  * FLIP: `flip_confirm_closes` closes in a row beyond the far edge (below a support, above a resistance) by more than
    `flip_buffer_pct` % - the zone now acts on the other side;
  * STRUCTURAL_BREAK: a close beyond the structural swing beyond the zone by more than `structure_buffer_pct` % - for a
    support the latest confirmed swing low (fractal, `structure_swing_order`) below the band, for a resistance the latest
    confirmed swing high above it - taken as known when tracking starts and again at each flip; when none exists yet,
    the first one confirmed later is adopted. It is checked before the flip on the same bar (a close through the zone
    AND the structure is the zone's death, not a flip). A touch, however deep, is never a death.
No look-ahead: an event on bar k reads bars <= k only; a swing at bar i counts from bar i + order (when it is confirmed).
Tracking starts after the bar the zone is CONFIRMED on (an order block / base on its impulse bar), never earlier, and
resumes after `last_bar_at` - so the storage job advances a stored tracker night by night over the new bars only.
"""
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from app.price_action.swings import swing_indices
from app.price_action.zones.settings import ZoneSettings


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
    # a second FLIP puts the zone back in its original role - it has been tested, it is no longer acting flipped
    ZoneState.FLIPPED: {ZoneEvent.TOUCH: ZoneState.FLIPPED, ZoneEvent.GAP_THROUGH: ZoneState.FLIPPED,
                        ZoneEvent.FLIP: ZoneState.TESTED, ZoneEvent.STRUCTURAL_BREAK: ZoneState.INVALIDATED},
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


# --- the bar-by-bar tracker (Z7a-2) ---------------------------------------------------------------------------------------
SUPPORT_SIDE, RESISTANCE_SIDE = "SUPPORT", "RESISTANCE"
ORIGIN_SIDE: Dict[str, str] = {"BULLISH_OB": SUPPORT_SIDE, "DEMAND_ZONE": SUPPORT_SIDE, "UP_GAP": SUPPORT_SIDE,
                               "BEARISH_OB": RESISTANCE_SIDE, "SUPPLY_ZONE": RESISTANCE_SIDE, "DOWN_GAP": RESISTANCE_SIDE}


def origin_side(zone_type: str, zone_low: float, zone_high: float, price: Optional[float]) -> str:
    """The side a zone starts on: from its type, else (line levels) from the price - mid above the price = resistance."""
    if zone_type in ORIGIN_SIDE:
        return ORIGIN_SIDE[zone_type]
    if price is None:
        raise ValueError(f"origin_side: a price is needed for a {zone_type!r} level")
    return RESISTANCE_SIDE if (zone_low + zone_high) / 2 > price else SUPPORT_SIDE


def _opposite(side: str) -> str:
    return RESISTANCE_SIDE if side == SUPPORT_SIDE else SUPPORT_SIDE


def _iso(t: Any) -> str:
    return pd.Timestamp(t).isoformat()


@dataclass
class ZoneTracker:
    """A zone's lifecycle so far - plain JSON-able fields, stored with the zone and advanced on the next bars."""
    origin_side: str
    side: str                                   # the side it acts on now
    band_low: float                             # the zone (a line level: level - tolerance)
    band_high: float
    state: str = ZoneState.FRESH.value
    touches: int = 0                            # visits (TOUCH + GAP_THROUGH events)
    flips: int = 0
    beyond: int = 0                             # closes in a row beyond the far edge (towards a flip)
    inside: bool = False                        # the last bar overlapped the band
    structure: Optional[float] = None           # the structural swing beyond the zone on the acting side
    structure_at: Optional[str] = None          # that swing's bar
    structure_checked: bool = False             # the structure known at the start has been looked up
    first_touch: Optional[Dict[str, Any]] = None
    last_event: Optional[Dict[str, Any]] = None
    invalidated_at: Optional[str] = None
    last_bar_at: Optional[str] = None           # the last bar processed (tracking resumes after it)
    events: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ZoneTracker":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


def new_tracker(zone_low: float, zone_high: float, side: str, tol: float = 0.0, start_at: Any = None) -> ZoneTracker:
    """A fresh tracker for a zone (tol widens a line level into a band). start_at: the bar it is known on (tracking
    begins after it)."""
    if side not in (SUPPORT_SIDE, RESISTANCE_SIDE):
        raise ValueError(f"side must be {SUPPORT_SIDE} or {RESISTANCE_SIDE}, not {side!r}")
    return ZoneTracker(origin_side=side, side=side, band_low=float(zone_low) - tol, band_high=float(zone_high) + tol,
                       last_bar_at=None if start_at is None else _iso(start_at))


Swings = Tuple[Sequence[int], Sequence[int]]


def frame_swings(bars: pd.DataFrame, settings: ZoneSettings) -> Swings:
    """The fractal swings the structure rule uses (compute once per frame and pass to track_zone for many zones)."""
    return swing_indices(bars, settings.structure_swing_order)


def _align(t: Any, ts: pd.Series) -> pd.Timestamp:
    """A stamp comparable with the frame's stamps (naive vs tz-aware made consistent; naive = UTC)."""
    t = pd.Timestamp(t)
    tz = getattr(ts.dt, "tz", None)
    if tz is not None and t.tzinfo is None:
        return t.tz_localize("UTC").tz_convert(tz)
    if tz is None and t.tzinfo is not None:
        return t.tz_convert("UTC").tz_localize(None)
    return t


def _structure(side: str, lo: float, hi: float, swings: Swings, highs: np.ndarray, lows: np.ndarray, order: int,
               known_by: int) -> Optional[int]:
    """The latest swing beyond the band confirmed by bar `known_by` (index), or None."""
    hi_idx, lo_idx = swings
    if side == SUPPORT_SIDE:
        cands = [i for i in lo_idx if i + order <= known_by and lows[i] < lo]
    else:
        cands = [i for i in hi_idx if i + order <= known_by and highs[i] > hi]
    return max(cands) if cands else None


def track_zone(tracker: ZoneTracker, bars: pd.DataFrame, settings: Optional[ZoneSettings] = None, start_after: Any = None,
               swings: Optional[Swings] = None) -> ZoneTracker:
    """Advance `tracker` over the bars stamped after max(start_after, tracker.last_bar_at) (bars oldest first, columns
    timestamp / open / high / low / close). Mutates and returns the tracker. An invalidated tracker never changes."""
    s = settings if settings is not None else ZoneSettings()
    if tracker.state == ZoneState.INVALIDATED.value or bars is None or not len(bars):
        return tracker
    bars = bars.reset_index(drop=True)
    ts = pd.to_datetime(bars["timestamp"])
    after = tracker.last_bar_at if tracker.last_bar_at is not None else start_after
    first = 0 if after is None else int(np.searchsorted(ts.to_numpy(), _align(after, ts).to_datetime64(), side="right"))
    n = len(bars)
    if first >= n:
        return tracker
    o, h = bars["open"].to_numpy(float), bars["high"].to_numpy(float)
    low, c = bars["low"].to_numpy(float), bars["close"].to_numpy(float)
    order = s.structure_swing_order
    sw = swings if swings is not None else frame_swings(bars, s)
    confirmed_at: Dict[int, List[Tuple[str, int]]] = {}
    for i in sw[0]:
        confirmed_at.setdefault(i + order, []).append(("high", i))
    for i in sw[1]:
        confirmed_at.setdefault(i + order, []).append(("low", i))
    lo, hi = tracker.band_low, tracker.band_high
    fbuf, sbuf = s.flip_buffer_pct / 100, s.structure_buffer_pct / 100

    def set_structure(idx: Optional[int]) -> None:
        if idx is None:
            tracker.structure, tracker.structure_at = None, None
        else:
            tracker.structure = float(low[idx] if tracker.side == SUPPORT_SIDE else h[idx])
            tracker.structure_at = _iso(ts.iloc[idx])

    def emit(event: ZoneEvent, k: int) -> None:
        ev = {"event": event.value, "time": _iso(ts.iloc[k]), "price": float(c[k])}
        tracker.state = transition(tracker.state, event).value
        tracker.last_event = ev
        if event in (ZoneEvent.TOUCH, ZoneEvent.GAP_THROUGH):
            tracker.touches += 1
            if tracker.first_touch is None:
                tracker.first_touch = ev
        tracker.events.append(ev)
        if s.max_events_kept and len(tracker.events) > s.max_events_kept:
            del tracker.events[:-s.max_events_kept]
        elif not s.max_events_kept:
            tracker.events.clear()

    if not tracker.structure_checked:                       # the structure known on the bar tracking starts after
        tracker.structure_checked = True
        set_structure(_structure(tracker.side, lo, hi, sw, h, low, order, first - 1))
    for k in range(first, n):
        if tracker.structure is None:                       # adopt the first swing beyond confirmed from now on
            for kind, i in confirmed_at.get(k, []):
                if (tracker.side == SUPPORT_SIDE and kind == "low" and low[i] < lo) or \
                        (tracker.side == RESISTANCE_SIDE and kind == "high" and h[i] > hi):
                    set_structure(i)
        ref = tracker.structure
        if ref is not None and ((tracker.side == SUPPORT_SIDE and c[k] < ref * (1 - sbuf)) or
                                (tracker.side == RESISTANCE_SIDE and c[k] > ref * (1 + sbuf))):
            emit(ZoneEvent.STRUCTURAL_BREAK, k)
            tracker.invalidated_at = _iso(ts.iloc[k])
            tracker.last_bar_at = _iso(ts.iloc[k])
            return tracker
        overlap = bool(h[k] >= lo and low[k] <= hi)
        if overlap and not tracker.inside:
            emit(ZoneEvent.TOUCH, k)
        elif not overlap and k > 0:
            g_lo, g_hi = min(c[k - 1], o[k]), max(c[k - 1], o[k])
            if g_lo <= lo <= g_hi or g_lo <= hi <= g_hi:
                emit(ZoneEvent.GAP_THROUGH, k)
        tracker.inside = overlap
        beyond = c[k] < lo * (1 - fbuf) if tracker.side == SUPPORT_SIDE else c[k] > hi * (1 + fbuf)
        tracker.beyond = tracker.beyond + 1 if beyond else 0
        if tracker.beyond >= s.flip_confirm_closes:
            emit(ZoneEvent.FLIP, k)
            tracker.flips += 1
            tracker.side = _opposite(tracker.side)
            tracker.beyond = 0
            set_structure(_structure(tracker.side, lo, hi, sw, h, low, order, k))
    tracker.last_bar_at = _iso(ts.iloc[n - 1])
    return tracker
