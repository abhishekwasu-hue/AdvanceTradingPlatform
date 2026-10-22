"""The causal wave counter (W1, review finding WB1): which wave is running, as known at one bar.

Generic wave principle only (review finding WB2): this module reads the generic rule functions
(`daily_trend/rules.py`: impulse, wedge, zigzag, flat, triangle), `read_counter` (for FINISHED waves only) and
`leg_points` (a wave's inner swings). It never reads the Daily trend fold or anything named after one trader's method;
a test enforces the import list.

A count is read from swings of ONE degree d (each degree-d swing is one wave; a larger structure is read at d + 1):

* the waves: the last confirmed degree-d swings plus the running end (the extreme since the last confirmed swing, as
  known at the bar). The last wave is still running, so the rules are checked with `done=False`;
* candidate origins: each of the last confirmed swings (at most five waves back - an impulse or a triangle has five);
* per origin, the readings whose rules hold: impulse family (IMPULSE; DIAGONAL when wave four overlaps wave one but the
  wedge rules hold) and correction family (ZIGZAG, FLAT / EXPANDED_FLAT with the B/A ratios of the settings, TRIANGLE
  for its D / E legs, DOUBLE W-X-Y once X is finished);
* each FINISHED wave's inner structure (its swings one degree lower, confirmed by the bar) is read by `read_counter`
  and must fit its role (motive for 1 / 3 / 5 / A of a zigzag, corrective for 2 / 4 / B, a complete correction for W).
  A wave with no inner swings fits either role but is not clear (owner question WAVE-5).

Main count (owner question WAVE-1): the larger-degree context decides impulse vs correction (a first wave with the
degree d + 1 swing direction is wave one, against it is wave A); then the oldest valid origin; a tie on the larger
direction the readings imply is UNCLEAR. The next reading is the alternate (evidence only).

Labels: IMP_1 .. IMP_5, COR_A .. COR_E (D / E only in a triangle), UNCLEAR. A double three reads W / X / Y as A / B / C
with type DOUBLE. While only wave A has started, the correction's type is not known yet (type UNCLEAR).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from app.price_action.daily_trend import rules as R
from app.price_action.daily_trend.legs import CORRECTIVE, MOTIVE, leg_points, read_counter

COMPLETE_CORRECTIONS = ("zigzag", "flat", "triangle", "combination")     # a W of a double three is one of these

IMPULSE, DIAGONAL, ZIGZAG, FLAT, EXPANDED_FLAT, TRIANGLE, DOUBLE, UNCLEAR = (
    "IMPULSE", "DIAGONAL", "ZIGZAG", "FLAT", "EXPANDED_FLAT", "TRIANGLE", "DOUBLE", "UNCLEAR")
TYPES: Tuple[str, ...] = (IMPULSE, DIAGONAL, ZIGZAG, FLAT, EXPANDED_FLAT, TRIANGLE, DOUBLE, UNCLEAR)
MOTIVE_TYPES = (IMPULSE, DIAGONAL)
IMPULSE_LABELS: Tuple[str, ...] = ("IMP_1", "IMP_2", "IMP_3", "IMP_4", "IMP_5")
CORRECTION_LABELS: Tuple[str, ...] = ("COR_A", "COR_B", "COR_C", "COR_D", "COR_E")
STATES: Tuple[str, ...] = IMPULSE_LABELS + CORRECTION_LABELS + (UNCLEAR,)
CLEAR, ALT_AGREES, ALT_DISAGREES = "CLEAR", "ALT_AGREES", "ALT_DISAGREES"
CLARITY: Tuple[str, ...] = (CLEAR, ALT_AGREES, ALT_DISAGREES, UNCLEAR)
MAX_WAVES = len(IMPULSE_LABELS)          # the most waves one reading holds (an impulse, a triangle)
THREE = 3                                # the waves of a zigzag, a flat and a double three (A / W, B / X, C / Y)
# Roles of a finished wave's inner structure
MOTIVE_ROLE, CORRECTIVE_ROLE, ANY_ROLE, COMPLETE_ROLE = "motive", "corrective", "any", "complete"


class Swing(NamedTuple):
    """A swing point as the counter reads it: H / L, its price, its extreme bar and the bar it became known on."""
    kind: str
    price: float
    bar: int
    confirm_bar: int


@dataclass(frozen=True)
class Reading:
    """One valid reading of the waves from one origin."""
    label: str                         # IMP_n / COR_x
    type: str                          # IMPULSE ... DOUBLE, or UNCLEAR while only wave A has started
    origin: int                        # the origin's position in the swings given (0 = the oldest)
    points: Tuple[float, ...]          # wave ends from the origin to the running end
    bars: Tuple[int, ...]              # their extreme bars
    direction: int                     # +1 the first wave rose, -1 it fell
    trend: int                         # the larger direction the reading implies (impulse: its direction; correction: against it)
    clear: bool                        # every finished wave's inner structure was read and fits its role
    alt_only: bool = False             # only ever an alternate (a double three while X is still running)

    @property
    def family(self) -> str:
        return "impulse" if self.label in IMPULSE_LABELS else "correction"


@dataclass(frozen=True)
class WaveCount:
    """The count as known at `bar`: the main reading, the alternate and the clarity."""
    bar: int
    state: str                         # a STATES value
    type: str                          # a TYPES value
    clarity: str                       # a CLARITY value
    main: Optional[Reading]            # None when the state is UNCLEAR
    alternate: Optional[Reading]
    context: int                       # the degree d + 1 swing direction known at the bar (+1 up, -1 down, 0 unknown)
    readings: Tuple[Reading, ...] = ()

    @property
    def alt_state(self) -> Optional[str]:
        return self.alternate.label if self.alternate is not None else None

    @property
    def alt_type(self) -> Optional[str]:
        return self.alternate.type if self.alternate is not None else None


def _roles(kind: str, finished: int) -> List[str]:
    """The role of each finished wave's inner structure in a reading of `kind`."""
    if kind == IMPULSE:
        return [MOTIVE_ROLE if i % 2 == 0 else CORRECTIVE_ROLE for i in range(finished)]
    if kind == DIAGONAL:
        return [ANY_ROLE if i % 2 == 0 else CORRECTIVE_ROLE for i in range(finished)]
    if kind == ZIGZAG:
        return [MOTIVE_ROLE, CORRECTIVE_ROLE][:finished]
    if kind == DOUBLE:
        return [COMPLETE_ROLE, CORRECTIVE_ROLE][:finished]
    return [CORRECTIVE_ROLE] * finished                         # flat, expanded flat, triangle


def _fits(role: str, family: str) -> bool:
    if role == COMPLETE_ROLE:
        return family in COMPLETE_CORRECTIONS
    if family == "single":                                      # no inner swings: fits either role (but is not clear)
        return True
    if role == MOTIVE_ROLE:
        return family in MOTIVE
    if role == CORRECTIVE_ROLE:
        return family in CORRECTIVE
    return True


def _structure_types(x: Sequence[float], s: Mapping[str, Any]) -> List[Tuple[str, bool]]:
    """The reading types whose price rules hold for wave ends `x` (the last wave running): (type, alt_only)."""
    n = len(x) - 1
    out: List[Tuple[str, bool]] = []
    if n <= MAX_WAVES:
        impulse_ok = R.impulse(x, done=False)
        if impulse_ok:
            out.append((IMPULSE, False))
        elif n >= MAX_WAVES - 1 and (R.wedge(x, done=False, kind="contracting") or R.wedge(x, done=False, kind="expanding")):
            out.append((DIAGONAL, False))
    if n == 1:
        out.append((UNCLEAR, False))                            # wave A: the correction's type is not known yet
    elif n <= THREE:
        if R.zigzag(x, False, s)[0]:
            out.append((ZIGZAG, False))
        flat_ok, _, info = R.flat(x, False, s)
        if flat_ok:
            out.append((EXPANDED_FLAT if info.get("sub") == "expanded" else FLAT, False))
        d = 1 if x[1] > x[0] else -1
        if not R.beyond(x[2], x[0], -d):                        # X never beyond W's start
            out.append((DOUBLE, n < THREE))                     # while X is running it is only an alternate
    elif n <= MAX_WAVES:
        if any(R.triangle(x, done=False, kind=k)[0] for k in ("contracting", "expanding")):
            out.append((TRIANGLE, False))
    return out


def readings(swings: Sequence[Swing], end: Swing, inner: Sequence[Swing], context: int, settings: Mapping[str, Any]) -> List[Reading]:
    """Every valid reading from each candidate origin, ranked: the context first (WAVE-1), then the oldest origin, then
    the simpler type (the order of TYPES)."""
    pts = list(swings) + [end]
    upto = end.confirm_bar
    inner_family: dict = {}

    def family_of(i: int) -> str:
        """`read_counter` on the inner swings of the finished wave pts[i] -> pts[i + 1] (confirmed by the bar)."""
        if i not in inner_family:
            a, b = pts[i], pts[i + 1]
            prices, _ = leg_points((), a.bar, a.price, b.bar, b.price, upto, inner)
            inner_family[i] = read_counter(prices, settings)["family"] if len(prices) > 2 else "single"
        return inner_family[i]

    out: List[Reading] = []
    first = max(0, len(swings) - MAX_WAVES)
    for j in range(first, len(swings)):
        x = [p.price for p in pts[j:]]
        if any(a == b for a, b in zip(x, x[1:])):
            continue                                            # a wave of no length reads nothing
        n = len(x) - 1
        direction = 1 if x[1] > x[0] else -1
        for kind, alt_only in _structure_types(x, settings):
            roles = _roles(kind, n - 1)
            fams = [family_of(j + i) for i in range(n - 1)]
            if not all(_fits(r, f) for r, f in zip(roles, fams)):
                continue
            motive = kind in MOTIVE_TYPES
            label = (IMPULSE_LABELS if motive else CORRECTION_LABELS)[n - 1]
            out.append(Reading(label, kind, j, tuple(x), tuple(p.bar for p in pts[j:]), direction,
                               direction if motive else -direction, all(f != "single" for f in fams), alt_only))

    def rank(r: Reading) -> Tuple[int, int, int]:
        return (0 if context == 0 or r.trend == context else 1, r.origin, TYPES.index(r.type))
    return sorted(out, key=rank)


def _alternate(main: Reading, others: Sequence[Reading]) -> Optional[Reading]:
    """The second reading (evidence only): the competing label from the main count's own origin (e.g. wave B against wave
    two), else the best other reading of more than one wave with a different label, else another type from the same
    origin (a flat against a zigzag). A one-wave reading from the last swing ("a new wave started") is never the
    alternate: it is always possible, so it says nothing."""
    same = [r for r in others if r.origin == main.origin]
    return (next((r for r in same if r.label != main.label), None)
            or next((r for r in others if r.label != main.label and len(r.points) > 2), None)
            or next((r for r in same if r.type != main.type), None))


def count(swings: Sequence[Swing], end: Swing, inner: Sequence[Swing], context: int, settings: Mapping[str, Any]) -> WaveCount:
    """The count at the bar `end` was read on. `swings`: the confirmed degree-d swings known by then (oldest first; only
    the last five are read); `end`: the running end; `inner`: the degree d - 1 swings (only those confirmed by the bar
    are read); `context`: the degree d + 1 swing direction known at the bar (+1 / -1, 0 unknown)."""
    ranked = readings(swings, end, inner, context, settings)
    candidates = [r for r in ranked if not r.alt_only]
    bar = end.confirm_bar
    if not candidates:
        return WaveCount(bar, UNCLEAR, UNCLEAR, UNCLEAR, None, ranked[0] if ranked else None, context, tuple(ranked))
    main = candidates[0]

    def tier(r: Reading) -> Tuple[int, int]:
        return (0 if context == 0 or r.trend == context else 1, r.origin)
    if any(tier(r) == tier(main) and r.trend != main.trend for r in candidates[1:]):
        return WaveCount(bar, UNCLEAR, UNCLEAR, UNCLEAR, None, main, context, tuple(ranked))
    alternate = _alternate(main, [r for r in ranked if r is not main])
    if not main.clear:
        clarity = UNCLEAR
    elif alternate is None:
        clarity = CLEAR
    else:
        clarity = ALT_AGREES if alternate.trend == main.trend else ALT_DISAGREES
    return WaveCount(bar, main.label, main.type, clarity, main, alternate, context, tuple(ranked))


__all__ = ["Swing", "Reading", "WaveCount", "readings", "count", "STATES", "TYPES", "CLARITY", "IMPULSE_LABELS", "CORRECTION_LABELS",
           "IMPULSE", "DIAGONAL", "ZIGZAG", "FLAT", "EXPANDED_FLAT", "TRIANGLE", "DOUBLE", "UNCLEAR", "CLEAR", "ALT_AGREES",
           "ALT_DISAGREES", "COMPLETE_CORRECTIONS"]
