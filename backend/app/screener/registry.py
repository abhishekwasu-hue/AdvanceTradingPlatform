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
    literal_only: bool = False      # Z10-a: a setting read once (a literal or a $parameter), never an expression per bar
    bounds: Optional[Tuple[float, float]] = None   # U6 BK1: inclusive range of a number (checked at validation)
    pattern: Optional[str] = None   # AMW-3: a text argument's required form (a regular expression it must match in full)


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
    price_args: Tuple[int, ...] = ()  # S5-A2: positions that must be prices (a level), not an oscillator or a count
    needs: Tuple[str, ...] = ()       # U5 D2: data beyond bars - "reference" (sector, index, F&O lists) or "option_chain"
    degree_min_bars: Tuple[int, ...] = ()  # S5-A4: min_bars by the `degree` argument (swings need more history per degree)
    flag: Optional[str] = None             # U6 BK1: an operator flag that must be on for a screen to use it (API layer)
    also_flags: Tuple[str, ...] = ()       # U6 BK3: further operator flags that must be on too (the family flag stays `flag`)
    timeframes: Tuple[str, ...] = ()       # U6 BK1: the only timeframes it runs on (empty = any)
    window_bars: Tuple[Tuple[str, int], ...] = ()   # U6 BK1: bars per window unit by timeframe (weeks -> 5 daily bars)
    window_sum: bool = False               # U6 BK1: the window arguments add up (ATR warm-up + percentile lookback)
    min_sessions: int = 0                  # U6 BK2: whole sessions of history it needs (intraday; on top of min_bars)
    sessions_arg: Optional[str] = None     # U6 BK2: the argument that sets min_sessions (prior sessions + the current one)
    tf_multiple_args: Tuple[str, ...] = ()  # U6 BK2: minute arguments that must be a whole number of the timeframe's bars
    fetch_degree_bars: Tuple[int, ...] = ()  # U6 BK1: bars to FETCH by degree (fetch sizing only; min_bars decides refusal)
    # AMW-3: further bars needed when an argument takes a value - (argument, value, bars), added to min_bars
    # (Pattern: a v2 candle reads ATR(14); a confirmation status reads the bar after the pattern)
    choice_extra_bars: Tuple[Tuple[str, object, int], ...] = ()


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
# AMW-3 (flag `screenql_amw_functions`): candle v2 names (candlestick_patterns.pattern_masks_v2) and the next-bar status
PATTERN_NAMES_V2: Tuple[str, ...] = ("bullish_strong_body", "bearish_strong_body", "bullish_gap_reversal", "bearish_gap_reversal")
NEUTRAL_PATTERNS: Tuple[str, ...] = ("doji", "inside_bar")         # no direction, so no confirmation status
CONFIRM_STATES: Tuple[str, ...] = ("any", "confirmed", "failed", "unconfirmed")
PATTERN_V2_EXTRA_BARS = 12                                        # min_bars 3 + 12 = 15: ATR(14) has its first value on bar 14
AMW_FLAG = "screenql_amw_functions"
SWING_DEGREES: Tuple[int, ...] = (0, 1, 2, 3)     # D0 smallest .. D3 largest threshold (app/price_action/pa_settings.py)
SWING_MIN_BARS = 100                              # ATR warm-up plus room for a few confirmed pivots
# S5-A4: history by swing degree. ZigZag pivots are path-dependent: with less history the last pivot can differ from the
# one a longer history gives. Measured on session-shaped samples (left-truncated vs full): D0 agrees from 100 bars, D1
# from 250, D2 from 600; D3 is given 1200 and is still documented as fetch-dependent (SC-14).
SWING_DEGREE_MIN_BARS: Tuple[int, ...] = (100, 250, 600, 1200)
REVERSAL_MIN_BARS = 25                            # the median range of 20 bars before a window of up to 4 candles
BREAK_EXTRA_BARS = 21                             # RealBreak: the median range (20 bars) before the earliest candidate, plus one

_DEGREE = Arg("degree", NUM, 0, False, choices=SWING_DEGREES)
_DEGREE1 = Arg("degree", NUM, 1, False, choices=SWING_DEGREES)
_SIDE = Arg("side", STR, choices=("up", "down"))
_CONFIRM = Arg("confirm", STR, "any", False, choices=CONFIRM_STATES)
_CONFIRM_BARS = tuple(("confirm", state, 1) for state in CONFIRM_STATES[1:])
_HHMM = r"([01][0-9]|2[0-3]):[0-5][0-9]"
BREAKOUT_FLAG = "screener_breakouts"
BREAKOUT_BK3_FLAG = "screener_breakouts_bk3"        # U6 BK3: VCP + indicator breakouts, on top of `screener_breakouts`
BREAKOUT_BK4_FLAG = "screener_breakouts_bk4"        # U6 BK4: trendline / pattern / gap / retest, on top of `screener_breakouts`


def _bk(name: str, arg: str, type_: str, required: bool = False, choices: Tuple[object, ...] = ()) -> Arg:
    """A breakout argument whose default and range are the breakout parameter's (app/screener/breakouts/params.py)."""
    from app.screener.breakouts.params import BY_NAME
    p = BY_NAME[name]
    bounds = (float(p.low), float(p.high)) if p.low is not None and p.high is not None else None
    # a NUM setting is read once for the whole series (Z10-a literal_only): a literal or a $parameter, never an expression
    return Arg(arg, type_, None if required else p.default, required, choices=choices or p.choices, bounds=bounds,
               literal_only=type_ == NUM)


BREAKOUT_MIN_BARS = 50                            # ATR warm-up plus a leg pivot before the first signal
# U6 BK2: the intraday screens read the same time of day in the previous `tod_sessions` sessions (20) plus the current one
INTRADAY_SCREEN_TFS = ("1m", "3m", "5m", "15m")
INTRADAY_SERIES_TFS = ("1m", "3m", "5m", "15m", "30m", "1h")
TOD_SESSIONS = 20
_V_TOD = _bk("tod_vol_mult", "v", NUM)
_VWAP = _bk("vwap_agree", "vwap", BOOL)
_SKIP = _bk("skip_first_min", "skip", NUM)
_K = _bk("min_break_atr", "k", NUM)
_X = Arg("x", NUM)
_N = Arg("n", "window")
# U6 BK3 history: the trend template reads a year of sessions (tt_year_bars 250) and the slow SMA's rise (200 + 21 bars);
# the MA breakout reads the slope / consolidation window behind the average (10 + 1 bars); the regime breakout the RSI /
# ADX warm-up (ADX settles after about 2 x 14 bars) behind its window.
_BK3 = (BREAKOUT_BK3_FLAG,)
_BK4 = (BREAKOUT_BK4_FLAG,)
# U6 BK4 history: a trendline's first anchor may be tl_max_bars (120) back with its swing window before it; a retest of a
# swing break reads the D1 swings (250 bars, SWING_DEGREE_MIN_BARS); a gap reads the previous session (intraday: whole sessions)
LINE_MIN_BARS = 125
RETEST_MIN_BARS = 250
GAP_TFS = ("1m", "3m", "5m", "15m", "1d")
GAP_CLASSES = ("GAP_AND_GO", "GAP_HOLD", "GAP_FILL")
_TT_ARG = _bk("tt_filter", "template", BOOL)
_DRY_ARG = _bk("vcp_vol_dryup", "dryup", BOOL)
VCP_MIN_BARS = 251
MA_EXTRA_BARS = 11
REGIME_EXTRA_BARS = 30
# Z7a-3: market-zone functions (app/screener/zones.py) - the stored zone snapshots, behind the `market_zones` flag
ZONES_FLAG = "market_zones"
_ZROLE = Arg("role", STR, "any", False, choices=("any", "demand", "supply"))
_ZTOL = Arg("tolerance_pct", NUM, 0.5, False)
_ZFAMILY = Arg("family", STR, "all", False, choices=("all", "zones", "levels"))
_ZDOC = (" - the live zone nearest to the close within tolerance_pct % of it (role by the close: demand below, supply above;"
         " family zones = order blocks / demand-supply bases / gaps, levels = S/R lines), from the stored snapshot as of this"
         " bar's close")

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
    Spec("Supertrend", "factor", NUM, "price", (Arg("n", "window", 10, False), Arg("k", NUM, 3.0, False, literal_only=True)), doc="Supertrend line"),
    Spec("BBUpper", "factor", NUM, "price", (Arg("n", "window", 20, False), Arg("k", NUM, 2.0, False, literal_only=True)), doc="upper Bollinger band"),
    Spec("BBMid", "factor", NUM, "price", (Arg("n", "window", 20, False), Arg("k", NUM, 2.0, False, literal_only=True)), doc="middle Bollinger band"),
    Spec("BBLower", "factor", NUM, "price", (Arg("n", "window", 20, False), Arg("k", NUM, 2.0, False, literal_only=True)), doc="lower Bollinger band"),
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
    Spec("PatternBullish", "filter", BOOL, None, (_CONFIRM,), cost=2.0, timeframed=False,
         doc="a bullish candlestick pattern on the last bar (confirm: any; or the status the last bar gave a bullish pattern on the"
             " bar before - confirmed / failed / unconfirmed)"),
    Spec("PatternBearish", "filter", BOOL, None, (_CONFIRM,), cost=2.0, timeframed=False,
         doc="a bearish candlestick pattern on the last bar (confirm: any; or the status the last bar gave a bearish pattern on the"
             " bar before - confirmed / failed / unconfirmed)"),
    Spec("NearSupport", "filter", BOOL, None, (Arg("tolerance_pct", NUM, literal_only=True), Arg("swing", "window", 3, False)), cost=4.0, timeframed=False,
         doc="the close is within tolerance_pct of a support zone edge"),
    Spec("NearResistance", "filter", BOOL, None, (Arg("tolerance_pct", NUM, literal_only=True), Arg("swing", "window", 3, False)), cost=4.0, timeframed=False,
         doc="the close is within tolerance_pct of a resistance zone edge"),
    # S5-A: price action as series (bar by bar, causal), so they work with offsets, @timeframe, Count and alerts
    Spec("Pattern", "filter", BOOL, None, (Arg("name", STR, choices=PATTERN_NAMES + PATTERN_NAMES_V2), _CONFIRM), cost=2.0, min_bars=3,
         choice_extra_bars=tuple(("name", v, PATTERN_V2_EXTRA_BARS) for v in PATTERN_NAMES_V2) + _CONFIRM_BARS,
         doc="the named candlestick pattern on this bar (closed bars only; uses this bar and up to two before it). confirm: any"
             " (the pattern on this bar), or the status this bar gave the pattern on the bar before - confirmed (close beyond its far"
             " side), failed (beyond the other side), unconfirmed (inside its range)"),
    Spec("SwingHigh", "factor", NUM, "price", (_DEGREE,), cost=2.0, min_bars=SWING_MIN_BARS, degree_min_bars=SWING_DEGREE_MIN_BARS,
         doc="the last CONFIRMED swing high: known only once price has come back from it by the degree's threshold"),
    Spec("SwingLow", "factor", NUM, "price", (_DEGREE,), cost=2.0, min_bars=SWING_MIN_BARS, degree_min_bars=SWING_DEGREE_MIN_BARS,
         doc="the last CONFIRMED swing low: known only once price has come back from it by the degree's threshold"),
    Spec("SwingDirection", "classifier", CAT, None, (_DEGREE,), cost=2.0, min_bars=SWING_MIN_BARS, degree_min_bars=SWING_DEGREE_MIN_BARS, values=("UP", "DOWN"),
         doc="UP after a confirmed swing low, DOWN after a confirmed swing high (missing before the first: never matches)"),
    Spec("SwingZoneStrength", "factor", NUM, "index", (Arg("side", STR, choices=("low", "high")), _DEGREE), cost=4.0,
         min_bars=SWING_MIN_BARS, degree_min_bars=SWING_DEGREE_MIN_BARS,
         doc="0-100 strength of the zone at the last confirmed swing low (support) or high (resistance): the trade-port"
             " level_strength score (departure, short base, recency, role reversal) from bars up to this one"),
    Spec("SwingZoneDistance", "factor", NUM, "ratio", (Arg("side", STR, choices=("low", "high")), _DEGREE), cost=4.0,
         min_bars=SWING_MIN_BARS, degree_min_bars=SWING_DEGREE_MIN_BARS,
         doc="close minus the mid of that swing zone, in median ranges (positive above the zone)"),
    Spec("ReversalAt", "filter", BOOL, None, (Arg("level", NUM), Arg("direction", STR, choices=("bullish", "bearish"))), cost=4.0,
         min_bars=REVERSAL_MIN_BARS, price_args=(0,),
         doc="price logically reversed at `level` on this bar (the trade-port reversal rule: touch, reclaim, strength, close"
             " location; hammer / engulfing / star are one rule) - bullish at support, bearish at resistance"),
    Spec("RealBreak", "filter", BOOL, None, (Arg("level", NUM), Arg("side", STR, choices=("above", "below")),
                                             Arg("n", "window", 20, False)), cost=4.0, min_bars=REVERSAL_MIN_BARS, price_args=(0,),
         extra_bars=BREAK_EXTRA_BARS,
         doc="a REAL break of `level` (this bar's value) was confirmed within the last n bars: a close beyond it by a buffer,"
             " then displacement, acceptance (no reclaim) or a failed retest - a wick or a reclaimed close is a false break"),
    # AMW-3 (flag `screenql_amw_functions`): the AMW screener's predicates as series - closed bars, causal
    Spec("StDev", "factor", NUM, "same", (_X, _N), flag=AMW_FLAG,
         doc="population standard deviation of x over n bars (divides by n, like Pine's ta.stdev)"),
    Spec("RsiDivergence", "filter", BOOL, None, (Arg("side", STR, choices=("bullish", "bearish")), Arg("lookback", "window", 15, False), _DEGREE),
         cost=3.0, min_bars=SWING_MIN_BARS, degree_min_bars=SWING_DEGREE_MIN_BARS, flag=AMW_FLAG,
         doc="regular RSI(14) divergence between the last two CONFIRMED swing lows (bullish: a lower low with a higher RSI) or"
             " highs (bearish: a higher high with a lower RSI), the latest swing's extreme at most `lookback` bars before this bar"),
    Spec("SwingLabel", "classifier", CAT, None, (Arg("kind", STR, choices=("high", "low")), _DEGREE), cost=2.0, min_bars=SWING_MIN_BARS,
         degree_min_bars=SWING_DEGREE_MIN_BARS, values=("HH", "LH", "HL", "LL"), flag=AMW_FLAG,
         doc="HH / LH (kind high) or HL / LL (kind low): the last CONFIRMED swing of that kind against the one before it (an equal"
             " price reads lower); missing before the second swing of that kind"),
    Spec("TimeOfDay", "filter", BOOL, None, (Arg("start", STR, pattern=_HHMM), Arg("end", STR, pattern=_HHMM)), cost=0.5,
         timeframes=INTRADAY_SERIES_TFS, flag=AMW_FLAG,
         doc="the bar starts between start and end (HH:MM, exchange time, both included; start after end wraps past midnight)"),
    Spec("MedianRange", "factor", NUM, "price", (Arg("n", "window", 20, False),), extra_bars=1,
         doc="median (high - low) of the n bars before this one (this bar excluded): the market's own noise"),
    Spec("PCR", "factor", NUM, "ratio", (), cost=2.0, timeframed=False, doc="put-call OI ratio of the supplied option chain", needs=("option_chain",)),
    Spec("ChainBias", "classifier", CAT, None, (), cost=2.0, timeframed=False,
         values=("BULLISH", "BEARISH", "NEUTRAL", "CONFLICTING"), doc="option-chain bias: BULLISH / BEARISH / NEUTRAL / CONFLICTING", needs=("option_chain",)),
    Spec("MaxPainDistancePct", "factor", NUM, "pct", (), cost=2.0, timeframed=False, doc="|underlying - max pain| as % of the underlying", needs=("option_chain",)),
    Spec("Sector", "classifier", CAT, None, (), timeframed=False, doc="NSE sector (as of the run date)", needs=("reference",)),
    Spec("Industry", "classifier", CAT, None, (), timeframed=False, doc="NSE industry (as of the run date)", needs=("reference",)),
    Spec("McapBucket", "classifier", CAT, None, (), timeframed=False, doc="AMFI large / mid / small", needs=("reference",)),
    Spec("IndexMember", "filter", BOOL, None, (Arg("index", STR),), timeframed=False, doc="member of the index on the run date", needs=("reference",)),
    Spec("IsFnO", "filter", BOOL, None, (), timeframed=False, doc="in the F&O segment on the run date", needs=("reference",)),
    # U6 BK1: breakout screens (app/screener/breakouts) - one signal per leg, closed bars only; behind `screener_breakouts`.
    # The defaults below are the breakout parameters' defaults (app/screener/breakouts/params.py, tested equal).
    Spec("RangeBreakout", "filter", BOOL, None, (_SIDE, _bk("range_n", "n", "window"), _bk("min_break_atr", "k", NUM), _bk("vol_mult", "v", NUM),
                                                 _bk("range_mode", "mode", STR)),
         cost=4.0, min_bars=BREAKOUT_MIN_BARS, flag=BREAKOUT_FLAG,
         doc="B1: close beyond the n-bar range (mode auto: the consolidation before the bar) by k ATR on volume >= v x average"),
    Spec("SwingBreakout", "filter", BOOL, None, (_SIDE, _DEGREE1, _bk("min_break_atr", "k", NUM)), cost=4.0, min_bars=SWING_MIN_BARS,
         degree_min_bars=SWING_DEGREE_MIN_BARS, flag=BREAKOUT_FLAG,
         doc="B2: close beyond the last CONFIRMED swing high (up) / low (down) of the degree by k ATR"),
    Spec("SwingBreakType", "classifier", CAT, None, (_SIDE, _DEGREE1, _bk("min_break_atr", "k", NUM)), cost=4.0, min_bars=SWING_MIN_BARS,
         degree_min_bars=SWING_DEGREE_MIN_BARS, values=("BOS", "CHOCH", "BREAK", ""), flag=BREAKOUT_FLAG,
         doc="B2: on a SwingBreakout bar, BOS (with the swing trend), CHOCH (against it) or BREAK (range / no trend yet); empty on"
             " other bars"),
    Spec("NRBreakout", "filter", BOOL, None, (_SIDE, _bk("nr_setup", "setup", STR), _bk("vol_mult", "v", NUM), _bk("min_break_atr", "k", NUM)),
         cost=3.0, min_bars=BREAKOUT_MIN_BARS, flag=BREAKOUT_FLAG,
         doc="B4: the bar after an inside / NR4 / NR7 bar closes beyond it by k ATR on volume >= v x average"),
    Spec("NewHighBreakout", "filter", BOOL, None, (_SIDE, _bk("high_weeks", "weeks", "window"), _bk("vol_mult", "v", NUM), _bk("min_break_atr", "k", NUM)),
         cost=3.0, min_bars=BREAKOUT_MIN_BARS, flag=BREAKOUT_FLAG, timeframes=("1d", "1w"), window_bars=(("1d", 5), ("1w", 1)),
         doc="B7 (Daily / Weekly): close beyond the high (up) / low (down) of the previous N calendar weeks by k ATR on volume >= v x"
             " average; no match until the history covers the N weeks"),
    Spec("RangeExpansion", "filter", BOOL, None, (_SIDE, _bk("expansion_k", "k", NUM), _bk("vol_mult", "v", NUM)), cost=3.0,
         min_bars=BREAKOUT_MIN_BARS, flag=BREAKOUT_FLAG,
         doc="B13: bar range >= k x ATR with the close in the top (up) / bottom (down) third, volume >= v x average"),
    Spec("VolumeBreakout", "filter", BOOL, None, (_SIDE, _bk("shock_mult", "v", NUM), _bk("range_n", "n", "window")), cost=3.0,
         min_bars=BREAKOUT_MIN_BARS, flag=BREAKOUT_FLAG,
         doc="B14: volume >= v x average with a directional candle at or through the n-bar high (up) / low (down)"),
    Spec("RelVolume", "factor", NUM, "ratio", (_bk("vol_avg_n", "n", "window"),), extra_bars=1, flag=BREAKOUT_FLAG,
         doc="volume / the mean volume of the n bars before this one"),
    Spec("ATRPercentile", "factor", NUM, "pct", (_bk("atr_n", "n", "window"), _bk("atr_pct_lookback", "lookback", "window")), cost=2.0,
         window_sum=True, flag=BREAKOUT_FLAG,
         doc="percentile (0-100) of the previous bar's ATR / close among the last `lookback` bars (needs n + lookback bars)"),
    # Refusal vs fetch: min_bars 1 - a context frame shorter than the trend's warm-up (dow_min_bars, 40 at D0) reads as
    # missing (unknown), which `DowTrend(0)@tf != "DOWN"` treats as not passing (cautious); the fetch is sized for the
    # swing history (100 / 250 / 600 / 1200 bars by degree) so the trend can be read.
    Spec("DowTrend", "classifier", CAT, None, (_DEGREE,), cost=2.0, min_bars=1, fetch_degree_bars=SWING_DEGREE_MIN_BARS,
         values=("UP", "DOWN", "RANGE"), flag=BREAKOUT_FLAG,
         doc="Dow trend from CONFIRMED swings: UP (higher high + higher low), DOWN (lower high + lower low), RANGE; missing before"
             " both are labelled and on the frame's first 40 (dow_min_bars) / 250 / 600 / 1200 bars (by degree). With @tf it is the higher"
             " timeframe's trend from its closed bars"),
    # U6 BK2: intraday breakouts on the exchange session (app/screener/breakouts/intraday.py, session.py) - closed bars, one
    # signal per leg; the session clock comes from the exchange calendar. Defaults = the breakout parameters' defaults.
    Spec("ORBreakout", "filter", BOOL, None, (_SIDE, _bk("or_minutes", "minutes", NUM), _V_TOD, _VWAP, _SKIP, _K), cost=3.0,
         min_bars=BREAKOUT_MIN_BARS, min_sessions=TOD_SESSIONS + 1, timeframes=INTRADAY_SCREEN_TFS, flag=BREAKOUT_FLAG,
         tf_multiple_args=("minutes",),
         doc="B16: the first close beyond the high (up) / low (down) of the session's first `minutes` by k ATR, with relative volume"
             " by time of day >= v and the close on the breakout side of the session VWAP (vwap); no signal in the first `skip`"
             " minutes; once per side per session"),
    Spec("DayLevelBreak", "filter", BOOL, None, (_SIDE, _bk("day_level", "level", STR),
                                                 _V_TOD, _VWAP, _SKIP, _K), cost=3.0,
         min_bars=BREAKOUT_MIN_BARS, min_sessions=TOD_SESSIONS + 1, timeframes=INTRADAY_SCREEN_TFS, flag=BREAKOUT_FLAG,
         doc="B17: the first close beyond the first hour's, the previous session's, the first candle's or the pre-open bars' high (up)"
             " / low (down) by k ATR, confirmed like ORBreakout"),
    Spec("DayExtremeBreak", "filter", BOOL, None, (_SIDE, _V_TOD, _VWAP, _SKIP, _K), cost=3.0,
         min_bars=BREAKOUT_MIN_BARS, min_sessions=TOD_SESSIONS + 1, timeframes=INTRADAY_SCREEN_TFS, flag=BREAKOUT_FLAG,
         doc="B18: a close beyond the session's high (up) / low (down) so far by k ATR, with relative volume by time of day >= v and"
             " VWAP agreement; once per leg"),
    Spec("RelVolumeTOD", "factor", NUM, "ratio", (_bk("tod_sessions", "sessions", "window"), _bk("tod_mode", "mode", STR)),
         cost=2.0, sessions_arg="sessions", timeframes=INTRADAY_SERIES_TFS, flag=BREAKOUT_FLAG,
         doc="volume (mode cumulative: the session's volume so far) / the mean of the same time of day in the previous `sessions`"
             " sessions (prior sessions only; at least 5)"),
    Spec("SessionVWAP", "factor", NUM, "price", (), cost=1.5, timeframes=INTRADAY_SERIES_TFS, flag=BREAKOUT_FLAG,
         doc="VWAP from the session open on the exchange calendar (pre-open bars left out); equals VWAP on regular-session bars"),
    # U6 BK3: B3 VCP and the indicator breakouts B10 / B11 / B12 / B15 (app/screener/breakouts/vcp.py, indicator_breaks.py) -
    # closed bars, one signal per leg (VCPReady: a state); behind `screener_breakouts` AND `screener_breakouts_bk3`.
    Spec("VCPBreakout", "filter", BOOL, None, (_SIDE, _bk("vcp_min_contractions", "contractions", NUM), _bk("vcp_ratio", "ratio", NUM),
                                               _bk("vol_mult", "v", NUM), _K, _TT_ARG, _DRY_ARG), cost=4.0, min_bars=VCP_MIN_BARS, timeframes=("1d",),
         flag=BREAKOUT_FLAG, also_flags=_BK3,
         doc="B3 (Daily): the first close above the pivot (the last contraction's high) by k ATR on volume >= v x average, after at"
             " least `contractions` successive contractions each shallower than ratio x the one before, volume drying up through the"
             " base (dryup; indices without volume need FALSE and v 0) and the trend template holding (template)"),
    Spec("VCPReady", "filter", BOOL, None, (_SIDE, _bk("vcp_min_contractions", "contractions", NUM), _bk("vcp_ratio", "ratio", NUM),
                                            _bk("vcp_ready_pct", "pct", NUM), _TT_ARG, _DRY_ARG), cost=4.0, min_bars=VCP_MIN_BARS, timeframes=("1d",),
         flag=BREAKOUT_FLAG, also_flags=_BK3,
         doc="B3 Watchlist (Daily): true on every bar a VCP base is ready - the rules of VCPBreakout hold, the close is at most pct %"
             " under the pivot and has not closed above it since the last contraction's low"),
    Spec("DonchianBreakout", "filter", BOOL, None, (_SIDE, _bk("donchian_n", "n", "window"), _K), cost=3.0, min_bars=BREAKOUT_MIN_BARS,
         flag=BREAKOUT_FLAG, also_flags=_BK3,
         doc="B10: close beyond the highest high (up) / lowest low (down) of the previous n bars by k ATR (Donchian / Turtle 20 or 55)"),
    Spec("SqueezeBreakout", "filter", BOOL, None, (_SIDE, _bk("squeeze_mode", "mode", STR), _bk("bb_n", "n", "window"),
                                                   _bk("squeeze_lookback", "lookback", "window"), _bk("vol_mult", "v", NUM)),
         cost=4.0, min_bars=BREAKOUT_MIN_BARS, window_sum=True, flag=BREAKOUT_FLAG, also_flags=_BK3,
         doc="B11: mode bollinger - a close outside the n-bar Bollinger band on volume >= v x average within a few bars of the band"
             " width's lowest of `lookback` bars; mode ttm - the release of the TTM squeeze (bands back outside the Keltner channel)"
             " with momentum on the breakout side"),
    Spec("MABreakout", "filter", BOOL, None, (_SIDE, _bk("ma_n", "n", "window"), _bk("ma_type", "type", STR), _K), cost=3.0,
         min_bars=BREAKOUT_MIN_BARS, extra_bars=MA_EXTRA_BARS, flag=BREAKOUT_FLAG, also_flags=_BK3,
         doc="B12: a close above (up) / below (down) a flat or rising (falling) n-bar moving average by k ATR and beyond every close of"
             " the consolidation around it (the 10 bars before, within 1 ATR of the line); once per leg"),
    Spec("RegimeBreakout", "filter", BOOL, None, (_SIDE, _bk("rsi_band", "band", NUM), _bk("regime_n", "n", "window"),
                                                  _bk("adx_low", "adx", NUM), _K), cost=3.0, min_bars=BREAKOUT_MIN_BARS,
         extra_bars=REGIME_EXTRA_BARS, flag=BREAKOUT_FLAG, also_flags=_BK3,
         doc="B15: RSI leaves the 50 +/- band it held for n bars, ADX rises from at or below adx with the DI of the side leading, and"
             " the close clears the n-bar high (up) / low (down) by k ATR"),
    # U6 BK4: trendline / channel (B5), flag / pennant / triangle (B6), gap (B8) and retest (B9) breakouts
    # (app/screener/breakouts/lines.py, gaps.py, retest.py); behind `screener_breakouts` AND `screener_breakouts_bk4`.
    Spec("TrendlineBreakout", "filter", BOOL, None, (_SIDE, _bk("tl_mode", "mode", STR), _bk("tl_min_touches", "touches", NUM), _K), cost=4.0,
         min_bars=LINE_MIN_BARS, flag=BREAKOUT_FLAG, also_flags=_BK4,
         doc="B5: mode line - the first close above the falling resistance (up) / below the rising support (down) trendline with at least"
             " `touches` swings, by k ATR; mode channel - beyond the far edge of a rising (up) / falling (down) channel"),
    Spec("PatternBreakout", "filter", BOOL, None, (_SIDE, _bk("pattern", "pattern", STR), _bk("vol_mult", "v", NUM), _K), cost=4.0,
         min_bars=LINE_MIN_BARS, flag=BREAKOUT_FLAG, also_flags=_BK4,
         doc="B6: a close above (up) / below (down) a flag, a pennant or a converging triangle by k ATR on volume >= v x average"),
    Spec("GapBreakout", "filter", BOOL, None, (_SIDE, _bk("gap_level", "level", STR), _bk("gap_hold_min", "hold", NUM)), cost=3.0,
         min_bars=BREAKOUT_MIN_BARS, min_sessions=2, timeframes=GAP_TFS, tf_multiple_args=("hold",), flag=BREAKOUT_FLAG, also_flags=_BK4,
         doc="B8: the session opened beyond the previous session's high (up) / low (down) - level base: the last 20 sessions' - and every"
             " close of its first `hold` minutes stayed beyond it (Daily: the day's close); true on the bar that ends the window"),
    Spec("GapType", "classifier", CAT, None, (_SIDE, _bk("gap_level", "level", STR), _bk("gap_hold_min", "hold", NUM)), cost=3.0,
         min_bars=BREAKOUT_MIN_BARS, min_sessions=2, timeframes=GAP_TFS, tf_multiple_args=("hold",), values=GAP_CLASSES + ("",),
         flag=BREAKOUT_FLAG, also_flags=_BK4,
         doc="B8: on the bar that ends a gap's hold window, GAP_AND_GO (held, close at or beyond the open), GAP_HOLD (held, faded"
             " toward the level) or GAP_FILL (a close back inside the level); empty on other bars"),
    Spec("RetestEntry", "filter", BOOL, None, (_SIDE, _bk("retest_of", "of", STR), _bk("retest_max_bars", "bars", NUM),
                                               _bk("retest_reversal", "reversal", BOOL)), cost=4.0, min_bars=RETEST_MIN_BARS,
         flag=BREAKOUT_FLAG, also_flags=_BK4,
         doc="B9: after a range / swing / Donchian / trendline breakout, price left the level and came back within `bars` bars to"
             " hold it - a logical reversal at the level (reversal) or a touch - closing at or beyond it; once per breakout"),
    # Z7a-3: market zones (demand / supply, order blocks, S/R, gaps with their lifecycle) - research only, places no orders
    Spec("NearZone", "filter", BOOL, None, (_ZROLE, _ZTOL, _ZFAMILY), cost=3.0, min_bars=1, needs=("zones",), flag=ZONES_FLAG,
         doc="true when there is a zone of that role" + _ZDOC),
    Spec("ZoneRole", "classifier", CAT, None, (_ZTOL, _ZFAMILY), cost=3.0, min_bars=1, values=("DEMAND", "SUPPLY"), needs=("zones",),
         flag=ZONES_FLAG, doc="DEMAND or SUPPLY: the role of that zone (missing when there is none)" + _ZDOC),
    Spec("ZoneState", "classifier", CAT, None, (_ZROLE, _ZTOL, _ZFAMILY), cost=3.0, min_bars=1, values=("FRESH", "TESTED", "FLIPPED"),
         needs=("zones",), flag=ZONES_FLAG, doc="FRESH / TESTED / FLIPPED: that zone's lifecycle state (missing when there is none)" + _ZDOC),
    Spec("ZoneStrength", "factor", NUM, "index", (_ZROLE, _ZTOL, _ZFAMILY), cost=3.0, min_bars=1, needs=("zones",), flag=ZONES_FLAG,
         doc="0-100 strength of that zone (family, timeframe, state, touches; NaN when there is none)" + _ZDOC),
)}

ZONE_FUNCTIONS: Tuple[str, ...] = tuple(name for name, spec in FUNCTIONS.items() if spec.flag == ZONES_FLAG)
BREAKOUT_FUNCTIONS: Tuple[str, ...] = tuple(name for name, spec in FUNCTIONS.items() if spec.flag == BREAKOUT_FLAG)
BREAKOUT_BK3_FUNCTIONS: Tuple[str, ...] = tuple(name for name, spec in FUNCTIONS.items() if BREAKOUT_BK3_FLAG in spec.also_flags)
BREAKOUT_BK4_FUNCTIONS: Tuple[str, ...] = tuple(name for name, spec in FUNCTIONS.items() if BREAKOUT_BK4_FLAG in spec.also_flags)


# Z10-a (U3 G2): functions answered for the LAST bar only - the structure / pattern / zone filters, the option chain,
# reference data as of the run date and the cross-sectional ranks. Read as a series, their single answer would be
# repeated on every earlier bar, so a function that reads a history (READS_HISTORY) would count, average or compare
# today's answer as if it had held all along. The validator refuses that combination.
LAST_BAR_ONLY = frozenset(name for name, spec in FUNCTIONS.items() if not spec.timeframed and not spec.varargs)
READS_HISTORY = frozenset({"SMA", "EMA", "Max", "Min", "Count", "CountStreak", "CrossAbove", "CrossBelow", "PctChange", "ZScore", "Lag", "StDev"})
AMW_FUNCTIONS: Tuple[str, ...] = tuple(name for name, spec in FUNCTIONS.items() if spec.flag == AMW_FLAG)


def describe() -> Dict[str, Dict[str, object]]:
    """The registry as data (for the builder's palette and the docs page)."""
    out: Dict[str, Dict[str, object]] = {}
    for spec in (*FIELDS.values(), *FUNCTIONS.values()):
        out[spec.name] = {"kind": spec.kind, "returns": spec.returns, "unit": spec.unit, "args": [a.name for a in spec.args],
                          "choices": {a.name: list(a.choices) for a in spec.args if a.choices}, "values": list(spec.values),
                          "defaults": {a.name: a.default for a in spec.args if a.default is not None},
                          "types": {a.name: a.type for a in spec.args}, "cross_sectional": spec.cross_sectional,
                          "literal": [a.name for a in spec.args if a.literal_only],
                          "kwargs": [a.name for a in spec.kwargs], "varargs": spec.varargs, "timeframed": spec.timeframed,
                          "field": spec.name in FIELDS, "needs": list(spec.needs), "doc": spec.doc}
        if spec.timeframes:                                          # U6 BK2: only the entries limited to some timeframes
            out[spec.name]["timeframes"] = list(spec.timeframes)
        if any(a.pattern for a in spec.args):                        # AMW-3: only the entries with a text form (TimeOfDay)
            out[spec.name]["patterns"] = {a.name: a.pattern for a in spec.args if a.pattern}
    return out


__all__ = ["FIELDS", "FUNCTIONS", "LAST_BAR_ONLY", "READS_HISTORY", "Spec", "Arg", "describe", "BREAKOUT_FUNCTIONS", "BREAKOUT_FLAG", "ZONE_FUNCTIONS",
           "BREAKOUT_BK3_FLAG", "BREAKOUT_BK3_FUNCTIONS", "BREAKOUT_BK4_FLAG", "BREAKOUT_BK4_FUNCTIONS",
           "ZONES_FLAG", "MAX_WINDOW", "AMW_FLAG", "AMW_FUNCTIONS", "PATTERN_NAMES_V2", "NEUTRAL_PATTERNS", "CONFIRM_STATES",
           "PATTERN_NAMES", "SWING_DEGREES", "NUM", "BOOL", "CAT", "STR"]
