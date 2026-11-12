"""M99 P5: the wave rules as a checklist - every rule of a reading with its status and the price where it breaks.

Pure functions of the wave-end prices x = [x0, x1, ..., xn] (x0 the origin, n waves present, the last one possibly still
running; `done` = the last wave is complete). The generic rule functions (`daily_trend/rules.py`: impulse, wedge,
zigzag, triangle) answer valid / not valid; this module lists WHICH rule holds, which one breaks and which one is still
open, so a count can be shown with its reasons and its invalidation lines. It never changes those rule functions: the
parity tests pin that a reading the rule functions accept has no broken hard rule here, and the other way round.

Hard rules (a broken one means the count is wrong, not the market):

* impulse - wave 2 never beyond the start of wave 1; wave 3 never the shortest of waves 1, 3 and 5; wave 4 never into
  wave 1's price territory. The generic rules also ask wave 3 to pass the end of wave 1 (a structure check, listed);
  a fifth wave that stops short of the end of wave 3 is a TRUNCATED fifth (a note, not a broken rule - the generic rule
  function refuses it, so the parity test reads `valid and not truncated`).
* diagonal (contracting wedge) - wave 2 never beyond the start of wave 1; wave 3 beyond the end of wave 1 and shorter
  than wave 1; wave 4 shorter than wave 2 and never beyond the end of wave 2; wave 5 shorter than wave 3 and beyond the
  end of wave 3. Expanding: the lengths the other way round. Wave 4 entering wave 1's territory is what makes it a
  diagonal (a note when it does not).
* zigzag - wave B never beyond the start of wave A; wave C passing the end of wave A (a note when it does not: truncated).
* triangle (contracting / barrier) - C never beyond the end of A, D never beyond the end of B, E never beyond the end of C.
  Expanding: each of C, D, E beyond the end of the wave two before it.

Each check carries the price where it would break (None when a length decides it). The checks of the running wave that
are still open are the count's INVALIDATION LINES. Nothing here reads bars, places, sizes or exits anything. A wave count
is an interpretation of the bars, never a fact about the market (COUNT_NOTE).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.price_action.daily_trend.rules import beyond

COUNT_NOTE = "Count is an interpretation"

IMPULSE, DIAGONAL, ZIGZAG, TRIANGLE = "impulse", "diagonal", "zigzag", "triangle"
KINDS: tuple[str, ...] = (IMPULSE, DIAGONAL, ZIGZAG, TRIANGLE)
CONTRACTING, EXPANDING = "contracting", "expanding"

PASS, FAIL, OPEN = "pass", "fail", "open"
HARD, STRUCTURE, NOTE = "hard", "structure", "note"


@dataclass(frozen=True)
class Check:
    rule: str                 # a short id, e.g. "w2_within_w1"
    words: str                # the rule in plain words
    status: str               # PASS / FAIL / OPEN (not decided yet: the wave it needs is still running or not there)
    kind: str                 # HARD (breaks the count) / STRUCTURE (a shape check of the generic rules) / NOTE
    level: float | None = None    # the price where the rule breaks (for the running wave: an invalidation line)
    wave: int = 0             # the wave (1-based) the rule is about


@dataclass(frozen=True)
class Validation:
    kind: str
    direction: int            # +1 the first wave rose, -1 it fell
    checks: tuple[Check, ...]

    @property
    def violations(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.status == FAIL and c.kind in (HARD, STRUCTURE))

    @property
    def valid(self) -> bool:
        return not self.violations

    @property
    def notes(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.kind == NOTE and c.status == FAIL)

    @property
    def truncated(self) -> bool:
        return any(c.rule in ("w5_beyond_w3", "c_beyond_a") and c.status == FAIL for c in self.checks)

    def open_lines(self) -> tuple[Check, ...]:
        """The invalidation lines of the running count: open checks that carry a price."""
        return tuple(c for c in self.checks if c.status == OPEN and c.level is not None and c.kind in (HARD, STRUCTURE))


def _lengths(x: Sequence[float]) -> list[float]:
    return [abs(x[i + 1] - x[i]) for i in range(len(x) - 1)]


def _complete(n: int, k: int, done: bool) -> bool:
    return n > k or (n == k and done)


def _status(fail: bool, decided: bool) -> str:
    return FAIL if fail else (PASS if decided else OPEN)


def _impulse(x: Sequence[float], done: bool) -> list[Check]:
    n, d, L = len(x) - 1, (1 if x[1] > x[0] else -1), _lengths(x)
    out: list[Check] = []
    if n >= 2:
        out.append(Check("w2_within_w1", "Wave 2 does not go beyond the start of wave 1",
                         _status(beyond(x[2], x[0], -d), _complete(n, 2, done)), HARD, x[0], 2))
    if n >= 3:
        out.append(Check("w3_beyond_w1", "Wave 3 goes beyond the end of wave 1",
                         _status(_complete(n, 3, done) and not beyond(x[3], x[1], d), n > 3 or beyond(x[3], x[1], d) or done),
                         STRUCTURE, None, 3))
    else:
        out.append(Check("w3_beyond_w1", "Wave 3 goes beyond the end of wave 1", OPEN, STRUCTURE, None, 3))
    if n >= 4:
        out.append(Check("w4_out_of_w1", "Wave 4 does not enter wave 1's price territory",
                         _status(not beyond(x[4], x[1], d), _complete(n, 4, done)), HARD, x[1], 4))
    else:
        out.append(Check("w4_out_of_w1", "Wave 4 does not enter wave 1's price territory", OPEN, HARD, None, 4))
    if n >= 5:
        shortest = L[2] < L[0] and L[4] > L[2]
        cap = x[4] + d * L[2] if L[2] < L[0] else None
        out.append(Check("w3_not_shortest", "Wave 3 is not the shortest of waves 1, 3 and 5",
                         _status(shortest, done), HARD, cap, 5))
        out.append(Check("w5_beyond_w3", "Wave 5 goes beyond the end of wave 3 (if not: a truncated fifth)",
                         _status(done and not beyond(x[5], x[3], d), done or beyond(x[5], x[3], d)), NOTE, None, 5))
    else:
        out.append(Check("w3_not_shortest", "Wave 3 is not the shortest of waves 1, 3 and 5", OPEN, HARD, None, 5))
    return out


def _diagonal(x: Sequence[float], done: bool, kind: str) -> list[Check]:
    n, d, L = len(x) - 1, (1 if x[1] > x[0] else -1), _lengths(x)
    con = kind == CONTRACTING
    out: list[Check] = []
    shorter, longer = "shorter", "longer"
    rel = shorter if con else longer

    def size_check(rule: str, a: int, b: int, wave: int, level: float | None) -> None:
        """Wave a+1 is shorter (contracting) / longer (expanding) than wave b+1. A running wave only grows: in a
        contracting wedge it fails as soon as it is too long; in an expanding one it is decided only when complete."""
        if n < a + 1:
            out.append(Check(rule, f"Wave {a + 1} is {rel} than wave {b + 1}", OPEN, HARD, None, wave))
            return
        if con:
            fail = L[a] >= L[b]
            out.append(Check(rule, f"Wave {a + 1} is {rel} than wave {b + 1}", _status(fail, _complete(n, a + 1, done)), HARD, level, wave))
        else:
            decided = _complete(n, a + 1, done)
            out.append(Check(rule, f"Wave {a + 1} is {rel} than wave {b + 1}", _status(decided and L[a] <= L[b], decided or L[a] > L[b]),
                             HARD, None, wave))

    if n >= 2:
        out.append(Check("w2_within_w1", "Wave 2 does not go beyond the start of wave 1",
                         _status(beyond(x[2], x[0], -d), _complete(n, 2, done)), HARD, x[0], 2))
    if n >= 3:
        out.append(Check("w3_beyond_w1", "Wave 3 goes beyond the end of wave 1",
                         _status(_complete(n, 3, done) and not beyond(x[3], x[1], d), n > 3 or beyond(x[3], x[1], d) or done),
                         STRUCTURE, None, 3))
    size_check("w3_vs_w1", 2, 0, 3, (x[2] + d * L[0]) if con and n == 3 else None)
    if n >= 4:
        out.append(Check("w4_within_w2", "Wave 4 does not go beyond the end of wave 2",
                         _status(beyond(x[4], x[2], -d), _complete(n, 4, done)), HARD, x[2], 4))
        out.append(Check("w4_overlaps_w1", "Wave 4 enters wave 1's price territory (what makes it a diagonal)",
                         _status(beyond(x[4], x[1], d), _complete(n, 4, done)), NOTE, None, 4))
    else:
        out.append(Check("w4_within_w2", "Wave 4 does not go beyond the end of wave 2", OPEN, HARD, None, 4))
    size_check("w4_vs_w2", 3, 1, 4, (x[3] - d * L[1]) if con and n == 4 else None)
    size_check("w5_vs_w3", 4, 2, 5, (x[4] + d * L[2]) if con and n == 5 else None)
    if n >= 5:
        out.append(Check("w5_beyond_w3", "Wave 5 goes beyond the end of wave 3",
                         _status(_complete(n, 5, done) and not beyond(x[5], x[3], d), done or beyond(x[5], x[3], d)),
                         STRUCTURE, None, 5))
    else:
        out.append(Check("w5_beyond_w3", "Wave 5 goes beyond the end of wave 3", OPEN, STRUCTURE, None, 5))
    return out


def _zigzag(x: Sequence[float], done: bool) -> list[Check]:
    n, d = len(x) - 1, (1 if x[1] > x[0] else -1)
    out: list[Check] = []
    if n >= 2:
        out.append(Check("b_within_a", "Wave B does not go beyond the start of wave A",
                         _status(beyond(x[2], x[0], -d), _complete(n, 2, done)), HARD, x[0], 2))
    else:
        out.append(Check("b_within_a", "Wave B does not go beyond the start of wave A", OPEN, HARD, None, 2))
    if n >= 3:
        out.append(Check("c_within_b", "Wave C does not go back beyond the end of wave B", _status(beyond(x[3], x[2], -d), done), HARD,
                         x[2], 3))
        out.append(Check("c_beyond_a", "Wave C goes beyond the end of wave A (if not: a truncated C)",
                         _status(done and not beyond(x[3], x[1], d), done or beyond(x[3], x[1], d)), NOTE, None, 3))
    return out


def _triangle(x: Sequence[float], done: bool, shape: str) -> list[Check]:
    """Contracting / barrier: C never beyond the end of A, D never beyond the end of B, E never beyond the end of C (the
    generic `rules.triangle` checks D and E; the classic rule for C is listed here too). Expanding: each of C,
    D, E goes beyond the end of the wave two before it (decided when the wave is complete)."""
    n, d = len(x) - 1, (1 if x[1] > x[0] else -1)
    con = shape == CONTRACTING
    out: list[Check] = []
    # wave k (3..5) against the end of wave k - 2: C vs A (direction d), D vs B (-d), E vs C (d)
    for k, name, ref in ((3, "C", "A"), (4, "D", "B"), (5, "E", "C")):
        sign = d if k % 2 == 1 else -d
        rule = f"{name.lower()}_{'within' if con else 'beyond'}_{ref.lower()}"
        words = f"Wave {name} {'does not go' if con else 'goes'} beyond the end of wave {ref}"
        if n < k:
            out.append(Check(rule, words, OPEN, HARD, None, k))
        elif con:
            out.append(Check(rule, words, _status(beyond(x[k], x[k - 2], sign), _complete(n, k, done)), HARD, x[k - 2], k))
        else:
            decided = _complete(n, k, done)
            out.append(Check(rule, words, _status(decided and not beyond(x[k], x[k - 2], sign), decided or beyond(x[k], x[k - 2], sign)),
                             HARD, None, k))
    return out


def validate(x: Sequence[float], kind: str, done: bool = False, shape: str = CONTRACTING) -> Validation:
    """Every rule of a reading of `kind` over wave ends `x` (at least the origin and one wave end), with its status."""
    if kind not in KINDS:
        raise ValueError(f"reading kind {kind!r} is not one of {', '.join(KINDS)}")
    if shape not in (CONTRACTING, EXPANDING):
        raise ValueError(f"shape {shape!r} is not contracting or expanding")
    pts = [float(v) for v in x]
    if len(pts) < 2 or pts[1] == pts[0]:
        raise ValueError("a reading needs an origin and a first wave of some length")
    if kind == IMPULSE:
        checks = _impulse(pts, done)
    elif kind == DIAGONAL:
        checks = _diagonal(pts, done, shape)
    elif kind == ZIGZAG:
        checks = _zigzag(pts, done)
    else:
        checks = _triangle(pts, done, shape)
    return Validation(kind, 1 if pts[1] > pts[0] else -1, tuple(checks))


# ----------------------------------------------------------------------------------------------- the triangle's thrust
def line_at(b1: float, p1: float, b2: float, p2: float, bar: float) -> float:
    """The straight line through (b1, p1) and (b2, p2) at `bar` (bars as positions)."""
    if b2 == b1:
        raise ValueError("a line needs two different bars")
    return p1 + (p2 - p1) / (b2 - b1) * (bar - b1)


def thrust_width(points: Sequence[float], bars: Sequence[int]) -> float | None:
    """The width of a contracting triangle: the A-C line (through the ends of A and C) and the B-D line (through the ends
    of B and D) extended back to the bar where wave A began; the vertical gap between them there. None when the lines do
    not converge toward the triangle's end (not a contracting shape) or the points are not there."""
    if len(points) < 5 or len(bars) < 5:
        return None
    b = [float(v) for v in bars[:5]]
    p = [float(v) for v in points[:5]]
    if b[1] == b[3] or b[2] == b[4]:
        return None
    at_origin = abs(line_at(b[1], p[1], b[3], p[3], b[0]) - line_at(b[2], p[2], b[4], p[4], b[0]))
    at_d = abs(line_at(b[1], p[1], b[3], p[3], b[4]) - p[4])
    if not at_origin > at_d:
        return None
    return at_origin


def thrust_projection(points: Sequence[float], bars: Sequence[int], e_end: float, trend: int) -> float | None:
    """The post-triangle thrust as a PROJECTION (a minimum estimate of the move after the triangle, never a target): the
    width laid from the end of wave E in the direction of the larger trend (`trend`, +1 / -1)."""
    w = thrust_width(points, bars)
    return None if w is None else float(e_end) + (1 if trend > 0 else -1) * w


__all__ = ["COUNT_NOTE", "KINDS", "IMPULSE", "DIAGONAL", "ZIGZAG", "TRIANGLE", "CONTRACTING", "EXPANDING", "PASS", "FAIL", "OPEN",
           "HARD", "STRUCTURE", "NOTE", "Check", "Validation", "validate", "line_at", "thrust_width", "thrust_projection"]
