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
    choices: Tuple[object, ...] = ()  # S5-A: the only values allowed (a literal or a parameter); empty = any


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
    extra_bars: int = 0             # bars needed beyond the window (CrossAbove compares with the bar before)
    min_bars: int = 0               # S5-A: a floor on the bars needed (swings need history before the first pivot)
    values: Tuple[str, ...] = ()    # S5-A: a classifier's only values (a literal outside them is refused); empty = open


FIELDS: Dict[str, Spec] = {s.name: s for s in (
    Spec("open", "factor", NUM, "price", doc="bar open"),
    Spec("high", "factor", NUM, "price", doc="bar high"),
    Spec("low", "factor", NUM, "price", doc="bar low"),
    Spec("close", "factor", NUM, "price", doc="bar close"),
    Spec("volume", "factor", NUM, "volume", doc="bar volume"),
    Spec("oi", "factor", NUM, "contracts", doc="open interest (derivatives)"),
)}

# S5-A: names map to app/price_action/candlestick_patterns.py detectors (with a direction where the detector has two)
PATTERN_NAMES: Tuple[str, ...] = (
    "doji", "hammer", "shooting_star", "bullish_engulfing", "bearish_engulfing", "morning_star", "evening_star",
    "bullish_pin_bar", "bearish_pin_bar", "inside_bar", "bullish_outside_bar", "bearish_outside_bar",
    "bullish_rejection", "bearish_rejection",
)
SWING_DEGREES: Tuple[int, ...] = (0, 1, 2, 3)     # D0 smallest .. D3 largest threshold (app/price_action/pa_settings.py)
SWING_MIN_BARS = 100                              # ATR warm-up plus room for a few confirmed pivots

_DEGREE = Arg("degree", NUM, 0, False, choices=SWING_DEGREES)
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
    Spec("CrossAbove", "filter", BOOL, None, (Arg("a", NUM), Arg("b", NUM)), same_unit_args=(0, 1), extra_bars=1, doc="a crossed above b on this bar"),
    Spec("CrossBelow", "filter", BOOL, None, (Arg("a", NUM), Arg("b", NUM)), same_unit_args=(0, 1), extra_bars=1, doc="a crossed below b on this bar"),
    Spec("PctChange", "factor", NUM, "pct", (_X, _N), doc="% change of x over n bars"),
    Spec("ZScore", "factor", NUM, "ratio", (_X, _N), doc="(x - mean) / stdev over n bars"),
    Spec("Lag", "factor", NUM, "same", (_X, _N), doc="x, n bars ago"),
    Spec("Rank", "factor", NUM, "count", (_X,), cost=3.0, cross_sectional=True, timeframed=False, doc="rank of x across the universe (1 = highest)"),
    Spec("PercentileRank", "factor", NUM, "pct", (_X,), (Arg("by", CAT, None, False),), cost=3.0, cross_sectional=True, timeframed=False,
         doc="percentile of x across the universe, optionally within each group of a classifier"),
    # S1c: the Strategy Builder / scanner indicators (same implementation as app/strategy_engine/declarative.py)
    Spec("PlusDI", "factor", NUM, "index", (Arg("n", "window", 14, False),), doc="+DI"),
    Spec("MinusDI", "factor", NUM, "index", (Arg("n", "window", 14, False),), doc="-DI"),
    Spec("Supertrend", "factor", NUM, "price", (Arg("n", "window", 10, False), Arg("k", NUM, 3.0, False)), doc="Supertrend line"),
    Spec("BBUpper", "factor", NUM, "price", (Arg("n", "window", 20, False), Arg("k", NUM, 2.0, False)), doc="upper Bollinger band"),
    Spec("BBMid", "factor", NUM, "price", (Arg("n", "window", 20, False), Arg("k", NUM, 2.0, False)), doc="middle Bollinger band"),
    Spec("BBLower", "factor", NUM, "price", (Arg("n", "window", 20, False), Arg("k", NUM, 2.0, False)), doc="lower Bollinger band"),
    Spec("DayOpen", "factor", NUM, "price", (), doc="the session's opening price"),
    Spec("PDH", "factor", NUM, "price", (), cost=1.5, doc="previous session high"),
    Spec("PDL", "factor", NUM, "price", (), cost=1.5, doc="previous session low"),
    Spec("PDC", "factor", NUM, "price", (), cost=1.5, doc="previous session close"),
    Spec("ORHigh", "factor", NUM, "price", (Arg("minutes", "window"),), doc="opening-range high, visible once the range is complete"),
    Spec("ORLow", "factor", NUM, "price", (Arg("minutes", "window"),), doc="opening-range low, visible once the range is complete"),
    # S1c: the scanner's structure and option filters - evaluated on the last closed bar only
    Spec("Trend", "classifier", CAT, None, (Arg("swing", "window", 3, False),), cost=3.0, timeframed=False,
         values=("UPTREND", "DOWNTREND", "RANGE"),
         doc="market-structure trend: UPTREND / DOWNTREND / RANGE"),
    Spec("StructureEvent", "classifier", CAT, None, (Arg("swing", "window", 3, False),), cost=3.0, timeframed=False,
         values=("BOS_BULLISH", "BOS_BEARISH", "CHOCH_BULLISH", "CHOCH_BEARISH", ""),
         doc="latest structure event: BOS_BULLISH / BOS_BEARISH / CHOCH_BULLISH / CHOCH_BEARISH (empty when none)"),
    Spec("PatternBullish", "filter", BOOL, None, (), cost=2.0, timeframed=False, doc="a bullish candlestick pattern on the last bar"),
    Spec("PatternBearish", "filter", BOOL, None, (), cost=2.0, timeframed=False, doc="a bearish candlestick pattern on the last bar"),
    Spec("NearSupport", "filter", BOOL, None, (Arg("tolerance_pct", NUM), Arg("swing", "window", 3, False)), cost=4.0, timeframed=False,
         doc="the close is within tolerance_pct of a support zone edge"),
    Spec("NearResistance", "filter", BOOL, None, (Arg("tolerance_pct", NUM), Arg("swing", "window", 3, False)), cost=4.0, timeframed=False,
         doc="the close is within tolerance_pct of a resistance zone edge"),
    # S5-A: price action as series (bar by bar, causal), so they work with offsets, @timeframe, Count and alerts
    Spec("Pattern", "filter", BOOL, None, (Arg("name", STR, choices=PATTERN_NAMES),), cost=2.0, min_bars=3,
         doc="the named candlestick pattern on this bar (closed bars only; uses this bar and up to two before it)"),
    Spec("SwingHigh", "factor", NUM, "price", (_DEGREE,), cost=2.0, min_bars=SWING_MIN_BARS,
         doc="the last CONFIRMED swing high: known only once price has come back from it by the degree's threshold"),
    Spec("SwingLow", "factor", NUM, "price", (_DEGREE,), cost=2.0, min_bars=SWING_MIN_BARS,
         doc="the last CONFIRMED swing low: known only once price has come back from it by the degree's threshold"),
    Spec("SwingDirection", "classifier", CAT, None, (_DEGREE,), cost=2.0, min_bars=SWING_MIN_BARS, values=("UP", "DOWN"),
         doc="UP after a confirmed swing low, DOWN after a confirmed swing high (missing before the first: never matches)"),
    Spec("MedianRange", "factor", NUM, "price", (Arg("n", "window", 20, False),), extra_bars=1,
         doc="median (high - low) of the n bars before this one (this bar excluded): the market's own noise"),
    Spec("PCR", "factor", NUM, "ratio", (), cost=2.0, timeframed=False, doc="put-call OI ratio of the supplied option chain"),
    Spec("ChainBias", "classifier", CAT, None, (), cost=2.0, timeframed=False,
         values=("BULLISH", "BEARISH", "NEUTRAL", "CONFLICTING"), doc="option-chain bias: BULLISH / BEARISH / NEUTRAL / CONFLICTING"),
    Spec("MaxPainDistancePct", "factor", NUM, "pct", (), cost=2.0, timeframed=False, doc="|underlying - max pain| as % of the underlying"),
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
                          "choices": {a.name: list(a.choices) for a in spec.args if a.choices}, "values": list(spec.values),
                          "kwargs": [a.name for a in spec.kwargs], "varargs": spec.varargs, "timeframed": spec.timeframed,
                          "field": spec.name in FIELDS, "doc": spec.doc}
    return out


__all__ = ["FIELDS", "FUNCTIONS", "Spec", "Arg", "describe", "MAX_WINDOW", "PATTERN_NAMES", "SWING_DEGREES", "NUM", "BOOL", "CAT", "STR"]
