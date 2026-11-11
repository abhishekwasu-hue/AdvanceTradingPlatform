"""M99 B1-b: Fibonacci levels of a swing as pure functions (owner order M99, flag `m99_primitives`).

Three families, each over prices the caller gives (a swing pair or three swing points); nothing here reads bars:

* retracement of a swing A -> B: `B - r x (B - A)` - measured back from the swing's END (r = 0 is B, r = 1 is A);
* extension / projection of A -> B from C (A-B-C): `C + r x (B - A)` - the length of A -> B laid from C in the direction
  of A -> B (r = 1: a move from C equal to A -> B);
* golden section of a range S -> E:
  - `golden_split`: the points that divide S -> E, `S + g x (E - S)`;
  - `golden_projection`: the end F of a move from S when E is the share g of it, `F = S + (E - S) / g` (g = 0.5: the
    even split, F = S + 2 x (E - S)).

The ratios are always a parameter (a list the caller passes). The defaults below are reference values only, labelled
REFERENCE_LABEL - the user edits them before use; no ratio here is a rule. Every result is a plain price (or a labelled
`FibLevel`): no target, no stop, no order. Signals only.

Same maths as the wave levels (app/price_action/waves/levels.py `WaveLevels.retrace` / `projection`), which apply it to
the wave count's own points; those stay as they are (tests/test_m99_fib_levels.py pins the parity).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

REFERENCE_LABEL = "reference - edit before use"
# Reference ratios (REFERENCE_LABEL): a starting list for the user's own settings, never a rule
REFERENCE_RETRACEMENT_RATIOS: Tuple[float, ...] = (0.382, 0.5, 0.618, 0.786)
REFERENCE_EXTENSION_RATIOS: Tuple[float, ...] = (0.618, 1.0, 1.272, 1.618, 2.0, 2.618)
REFERENCE_GOLDEN_RATIOS: Tuple[float, ...] = (0.382, 0.5, 0.618)
# The golden ratio itself (a constant of mathematics, (1 + sqrt 5) / 2), for callers that build their own lists
GOLDEN_RATIO = (1.0 + math.sqrt(5.0)) / 2.0

RETRACE = "retrace"
EXTEND = "extend"
GOLDEN_SPLIT = "golden_split"
GOLDEN_PROJECTION = "golden_projection"
KINDS: Tuple[str, ...] = (RETRACE, EXTEND, GOLDEN_SPLIT, GOLDEN_PROJECTION)


@dataclass(frozen=True)
class FibLevel:
    kind: str          # one of KINDS
    ratio: float
    price: float
    label: str         # plain words, e.g. "retrace 0.618" (no price, no id)


def _ratio(r: float, *, positive: bool = False) -> float:
    v = float(r)
    if not math.isfinite(v) or v < 0 or (positive and v == 0):
        raise ValueError(f"ratio {r!r} must be a finite number {'above' if positive else 'at or above'} 0")
    return v


def _price(p: float, name: str) -> float:
    v = float(p)
    if not math.isfinite(v):
        raise ValueError(f"{name} {p!r} must be a finite price")
    return v


def _label(kind: str, ratio: float, tag: str = "") -> str:
    words = {RETRACE: "retrace", EXTEND: "extend", GOLDEN_SPLIT: "golden split", GOLDEN_PROJECTION: "golden projection"}[kind]
    return f"{words} {ratio:g}" + (f" of {tag}" if tag else "")


# ------------------------------------------------------------------------------------------------------- single values
def retracement(a: float, b: float, ratio: float) -> float:
    """The price `ratio` of the swing A -> B back from its end B."""
    a, b, r = _price(a, "a"), _price(b, "b"), _ratio(ratio)
    return b - r * (b - a)


def extension(a: float, b: float, c: float, ratio: float) -> float:
    """The length of A -> B times `ratio`, laid from C in the direction of A -> B."""
    a, b, c, r = _price(a, "a"), _price(b, "b"), _price(c, "c"), _ratio(ratio)
    return c + r * (b - a)


def golden_split(start: float, end: float, ratio: float) -> float:
    """The point that divides the range start -> end at `ratio` of its length from the start."""
    s, e, g = _price(start, "start"), _price(end, "end"), _ratio(ratio)
    return s + g * (e - s)


def golden_projection(start: float, mid: float, ratio: float) -> float:
    """The end F of a move from `start` when `mid` sits at the share `ratio` of it: F = start + (mid - start) / ratio."""
    s, m, g = _price(start, "start"), _price(mid, "mid"), _ratio(ratio, positive=True)
    return s + (m - s) / g


# ------------------------------------------------------------------------------------------------------- level sets
def retracement_levels(a: float, b: float, ratios: Optional[Sequence[float]] = None, tag: str = "") -> List[FibLevel]:
    rs = REFERENCE_RETRACEMENT_RATIOS if ratios is None else tuple(ratios)
    return [FibLevel(RETRACE, float(r), retracement(a, b, r), _label(RETRACE, float(r), tag)) for r in rs]


def extension_levels(a: float, b: float, c: float, ratios: Optional[Sequence[float]] = None, tag: str = "") -> List[FibLevel]:
    rs = REFERENCE_EXTENSION_RATIOS if ratios is None else tuple(ratios)
    return [FibLevel(EXTEND, float(r), extension(a, b, c, r), _label(EXTEND, float(r), tag)) for r in rs]


def golden_split_levels(start: float, end: float, ratios: Optional[Sequence[float]] = None, tag: str = "") -> List[FibLevel]:
    rs = REFERENCE_GOLDEN_RATIOS if ratios is None else tuple(ratios)
    return [FibLevel(GOLDEN_SPLIT, float(g), golden_split(start, end, g), _label(GOLDEN_SPLIT, float(g), tag)) for g in rs]


def golden_projection_levels(start: float, mid: float, ratios: Optional[Sequence[float]] = None, tag: str = "") -> List[FibLevel]:
    rs = REFERENCE_GOLDEN_RATIOS if ratios is None else tuple(ratios)
    return [FibLevel(GOLDEN_PROJECTION, float(g), golden_projection(start, mid, g), _label(GOLDEN_PROJECTION, float(g), tag))
            for g in rs]


__all__ = ["FibLevel", "REFERENCE_LABEL", "REFERENCE_RETRACEMENT_RATIOS", "REFERENCE_EXTENSION_RATIOS", "REFERENCE_GOLDEN_RATIOS",
           "GOLDEN_RATIO", "KINDS", "RETRACE", "EXTEND", "GOLDEN_SPLIT", "GOLDEN_PROJECTION", "retracement", "extension",
           "golden_split", "golden_projection", "retracement_levels", "extension_levels", "golden_split_levels",
           "golden_projection_levels"]
