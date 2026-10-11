"""S1a (ADR-0021 §2): the field and function registry - the only list of what a screen may use.

Each entry is a Factor (a number with a unit), a Filter (true/false) or a Classifier (a category). The validator
types a screen against these signatures; the S1b runtime attaches an implementation to each name (and a fixture test
per entry). Nothing outside this registry can appear in a screen, so user text never reaches code.

Units keep comparisons honest: `close > RSI(14)` (a price against an oscillator) is refused; a bare literal or a
parameter takes the unit of the other side. `same` means the unit of the first argument.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

NUM, BOOL, CAT, STR = "num", "bool", "cat", "str"
MAX_WINDOW = 500


@dataclass(frozen=True)
class Arg:
    name: str
    type: str                       # num / bool / str / window (a whole number of bars, literal or parameter)
    default: Optional[object] = None
    required: bool = True


@dataclass(frozen=True)
class Spec:
    name: str
    kind: str                       # factor / filter / classifier
    returns: str                    # num / bool / cat
    unit: Optional[str]             # price / volume / contracts / pct / index / ratio / count / same / None
    args: Tuple[Arg, ...] = ()
    kwargs: Tuple[Arg, ...] = ()
    varargs: bool = False           # Greatest(a, b, ...): every positional argument is the first Arg's type
    cost: float = 1.0
    timeframed: bool = True         # accepts [offset] and @timeframe
    cross_sectional: bool = False   # needs the whole universe at once (Rank, PercentileRank)
    doc: str = ""
    same_unit_args: Tuple[int, ...] = field(default=())     # positions whose units must agree (CrossAbove(a, b))


FIELDS: Dict[str, Spec] = {s.name: s for s in (
    Spec("open", "factor", NUM, "price", doc="bar open"),
    Spec("high", "factor", NUM, "price", doc="bar high"),
    Spec("low", "factor", NUM, "price", doc="bar low"),
    Spec("close", "factor", NUM, "price", doc="bar close"),
    Spec("volume", "factor", NUM, "volume", doc="bar volume"),
    Spec("oi", "factor", NUM, "contracts", doc="open interest (derivatives)"),
)}

_X = Arg("x", NUM)
_N = Arg("n", "window")

FUNCTIONS: Dict[str, Spec] = {s.name: s for s in (
    Spec("SMA", "factor", NUM, "same", (_X, _N), doc="simple moving average of x over n bars"),
    Spec("EMA", "factor", NUM, "same", (_X, _N), doc="exponential moving average"),
    Spec("RSI", "factor", NUM, "index", (Arg("n", "window", 14, False),), doc="RSI of close (0-100)"),
    Spec("ADX", "factor", NUM, "index", (Arg("n", "window", 14, False),), doc="ADX (0-100)"),
    Spec("ATR", "factor", NUM, "price", (Arg("n", "window", 14, False),), doc="average true range"),
    Spec("VWAP", "factor", NUM, "price", (), cost=1.5, doc="session VWAP"),
    Spec("Max", "factor", NUM, "same", (_X, _N), doc="highest x over n bars"),
    Spec("Min", "factor", NUM, "same", (_X, _N), doc="lowest x over n bars"),
    Spec("Greatest", "factor", NUM, "same", (_X,), varargs=True, timeframed=False, doc="largest of the arguments"),
    Spec("Least", "factor", NUM, "same", (_X,), varargs=True, timeframed=False, doc="smallest of the arguments"),
    Spec("Count", "factor", NUM, "count", (_N, Arg("cond", BOOL)), doc="bars in the last n where cond held"),
    Spec("CountStreak", "factor", NUM, "count", (Arg("cond", BOOL),), cost=2.0, doc="consecutive bars, up to now, where cond held"),
    Spec("CrossAbove", "filter", BOOL, None, (Arg("a", NUM), Arg("b", NUM)), same_unit_args=(0, 1), doc="a crossed above b on this bar"),
    Spec("CrossBelow", "filter", BOOL, None, (Arg("a", NUM), Arg("b", NUM)), same_unit_args=(0, 1), doc="a crossed below b on this bar"),
    Spec("PctChange", "factor", NUM, "pct", (_X, _N), doc="% change of x over n bars"),
    Spec("ZScore", "factor", NUM, "ratio", (_X, _N), doc="(x - mean) / stdev over n bars"),
    Spec("Lag", "factor", NUM, "same", (_X, _N), doc="x, n bars ago"),
    Spec("Rank", "factor", NUM, "count", (_X,), cost=3.0, cross_sectional=True, timeframed=False, doc="rank of x across the universe (1 = highest)"),
    Spec("PercentileRank", "factor", NUM, "pct", (_X,), (Arg("by", CAT, None, False),), cost=3.0, cross_sectional=True, timeframed=False,
         doc="percentile of x across the universe, optionally within each group of a classifier"),
    Spec("Sector", "classifier", CAT, None, (), timeframed=False, doc="NSE sector (as of the run date)"),
    Spec("Industry", "classifier", CAT, None, (), timeframed=False, doc="NSE industry (as of the run date)"),
    Spec("McapBucket", "classifier", CAT, None, (), timeframed=False, doc="AMFI large / mid / small"),
    Spec("IndexMember", "filter", BOOL, None, (Arg("index", STR),), timeframed=False, doc="member of the index on the run date"),
    Spec("IsFnO", "filter", BOOL, None, (), timeframed=False, doc="in the F&O segment on the run date"),
)}


def describe() -> Dict[str, Dict[str, object]]:
    """The registry as data (for the builder's palette and the docs page)."""
    out: Dict[str, Dict[str, object]] = {}
    for spec in (*FIELDS.values(), *FUNCTIONS.values()):
        out[spec.name] = {"kind": spec.kind, "returns": spec.returns, "unit": spec.unit, "args": [a.name for a in spec.args],
                          "kwargs": [a.name for a in spec.kwargs], "varargs": spec.varargs, "timeframed": spec.timeframed,
                          "field": spec.name in FIELDS, "doc": spec.doc}
    return out


__all__ = ["FIELDS", "FUNCTIONS", "Spec", "Arg", "describe", "MAX_WINDOW", "NUM", "BOOL", "CAT", "STR"]
