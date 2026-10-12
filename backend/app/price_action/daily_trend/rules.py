"""N1 L2: the Elliott wave rules the Daily leg reader uses, ported from Trade `patterns2/rules.py` (`impulse`, `wedge`,
`triangle`) and `patterns2/rules2.py` (`zigzag`, `flat`) at the pinned revision. Pure functions of the wave-end prices
x = [x0, x1, ..., xn] (n waves present); `done` = the last wave is complete. Logic unchanged; texts in English.
"""
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.price_action.daily_trend.settings import PATTERN_DEFAULTS


def beyond(a: float, b: float, dirn: int) -> bool:
    """a is beyond b in direction dirn (equal is not beyond)."""
    return (a - b) * dirn > 0


def reached(c_end: float, a_end: float, d: int) -> bool:
    """C reached A's end (equal counts)."""
    return (c_end - a_end) * d >= 0


def _d(x: Sequence[float]) -> int:
    return 1 if x[1] > x[0] else -1


def _L(x: Sequence[float]) -> List[float]:
    return [abs(x[i + 1] - x[i]) for i in range(len(x) - 1)]


def _complete(n: int, k: int, done: bool) -> bool:
    """Wave k (1-based) is complete: a wave follows it, or it is the last one and done."""
    return n > k or (n == k and done)


def triangle(x: Sequence[float], done: bool = False, kind: str = "contracting", barrier_tol: float = 0.0) -> Tuple[bool, bool]:
    """Contracting: D inside B, E inside C. Expanding: C beyond A, D beyond B, E beyond C. Returns (valid, barrier)."""
    n = len(x) - 1
    if n < 2:
        return True, False
    d = _d(x)
    if kind == "contracting":
        if n >= 4 and beyond(x[4], x[2], -d):
            return False, False
        if n >= 5 and beyond(x[5], x[3], d):
            return False, False
        return True, bool(n >= 4 and abs(x[4] - x[2]) <= barrier_tol)
    if _complete(n, 3, done) and not beyond(x[3], x[1], d):
        return False, False
    if _complete(n, 4, done) and not beyond(x[4], x[2], -d):
        return False, False
    if _complete(n, 5, done) and not beyond(x[5], x[3], d):
        return False, False
    return True, False


def wedge(x: Sequence[float], done: bool = False, kind: str = "contracting") -> bool:
    """2 not beyond 1's start; 3 beyond 1's end; 4 not beyond 2's end; contracting 3<1, 4<2, 5<3 / expanding the reverse;
    5 beyond 3's end."""
    n = len(x) - 1
    if n < 2:
        return True
    d = _d(x)
    L = _L(x)
    if beyond(x[2], x[0], -d):
        return False
    if _complete(n, 3, done) and not beyond(x[3], x[1], d):
        return False
    con = kind == "contracting"
    if n >= 3:
        if con and L[2] >= L[0]:
            return False
        if not con and _complete(n, 3, done) and L[2] <= L[0]:
            return False
    if n >= 4:
        if beyond(x[4], x[2], -d):
            return False
        if con and L[3] >= L[1]:
            return False
        if not con and _complete(n, 4, done) and L[3] <= L[1]:
            return False
    if n >= 5:
        if con and L[4] >= L[2]:
            return False
        if not con and _complete(n, 5, done) and L[4] <= L[2]:
            return False
        if _complete(n, 5, done) and not beyond(x[5], x[3], d):
            return False
    return True


def impulse(x: Sequence[float], done: bool = False) -> bool:
    """2 not beyond 1's start; 3 beyond 1's end; 3 not the shortest; 4 not inside 1's territory; 5 beyond 3's end."""
    n = len(x) - 1
    if n < 2:
        return True
    d = _d(x)
    L = _L(x)
    if beyond(x[2], x[0], -d):
        return False
    if _complete(n, 3, done) and not beyond(x[3], x[1], d):
        return False
    if n >= 4 and not beyond(x[4], x[1], d):
        return False
    if n >= 5:
        if L[2] < L[0] and L[4] > L[2]:
            return False
        if _complete(n, 5, done) and not beyond(x[5], x[3], d):
            return False
    return True


def zigzag(x: Sequence[float], done: bool = False, s: Optional[Mapping[str, Any]] = None) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Type: a complete B/A below `zz_max_ba` allows a zigzag (B can only grow, so a running B is checked too)."""
    s = PATTERN_DEFAULTS if s is None else s
    n = len(x) - 1
    if n >= 2:
        L = _L(x)
        b = L[1] / L[0] if L[0] else float("inf")
        if b >= float(s["zz_max_ba"]):
            return False, [], {}
    return True, [], {}


def flat(x: Sequence[float], done: bool = False, s: Optional[Mapping[str, Any]] = None) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Type: a complete B/A >= `flat_min_ba`. Strong evidence: B > `flat_b_max` x A. Sub-type once C is there."""
    s = PATTERN_DEFAULTS if s is None else s
    n = len(x) - 1
    if n < 2:
        return True, [], {"sub": "pending"}
    L = _L(x)
    b = L[1] / L[0] if L[0] else float("inf")
    if _complete(n, 2, done) and b < float(s["flat_min_ba"]):
        return False, [], {}
    strong = ["flat B > 2A"] if b > float(s["flat_b_max"]) else []
    if n < 3:
        return True, strong, {"sub": "pending"}
    d = _d(x)
    em = float(s["flat_expanded_min"])
    if b > em and beyond(x[3], x[1], d):
        sub = "expanded"
    elif b > 1.0 and not reached(x[3], x[1], d):
        sub = "running" if done else "pending"
    else:
        sub = "regular" if (done or reached(x[3], x[1], d)) else "pending"
    return True, strong, {"sub": sub}
