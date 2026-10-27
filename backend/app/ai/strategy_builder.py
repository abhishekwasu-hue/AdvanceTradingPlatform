"""Item 5 (owner's handover), job 6: the strategy builder - the trader's own idea, in his own words, turned into a DRAFT
paper strategy made only of building blocks ATP already has.

What it does (and what it never does):

* It reads the trader's words with fixed rules (no model) and maps each part onto the no-code Strategy Builder's
  building blocks: the `CustomStrategyConfig` contract (`app/strategy_engine/declarative.py` - the same contract the
  Strategy Builder, the deterministic parser and the AI generator save), its indicators and its comparisons. A part
  that has no building block is listed under "Not supported yet" (with a pointer to the Screener when ATP's ScreenQL
  registry can look for it today); a part it cannot read is quoted back, never guessed.
* Every number is the trader's setting. A number his words state is used as written; a number they do not state stays
  EMPTY and is listed under "Values you decide" (an indicator length, a level, the stop distance, the profit objective,
  the minimum reward to risk, the timeframe, the side). The draft never fills in a stop, an objective, a size or a rule
  he did not state. The one value taken from elsewhere is his OWN saved minimum reward to risk (his risk limits),
  labelled as such and changeable.
* Nothing is saved, started or sent. The draft goes to the Paper bot page, where the trader reviews it, fills in his
  values, saves it as his strategy and starts it on paper himself (ADR-0006: the AI never places, blocks or changes an
  order). Pure functions only, so every rule here is unit-tested.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# --- the building blocks ------------------------------------------------------------------------------------------------

# The timeframes a saved strategy runs on (the Strategy Builder's list plus the generator's 3-minute) and the higher
# timeframes an operand may read (the Strategy Builder's "on 15min" choice: completed bars only).
TIMEFRAMES = ("1min", "3min", "5min", "15min", "30min", "60min")
HIGHER_TIMEFRAMES = ("5min", "15min", "30min", "60min")
TF_WORDS = {"1min": "1-minute", "3min": "3-minute", "5min": "5-minute", "15min": "15-minute", "30min": "30-minute", "60min": "1-hour"}
TIMEFRAMES_TEXT = "ATP's strategies run on 1, 3, 5, 15, 30 or 60-minute candles."

NEEDS_PERIOD = frozenset({"EMA", "SMA", "RSI", "ADX", "PLUS_DI", "MINUS_DI", "ATR", "SUPERTREND", "BB_UPPER", "BB_MID", "BB_LOWER",
                          "VOLUME_SMA", "OR_HIGH", "OR_LOW"})
NEEDS_MULTIPLIER = frozenset({"SUPERTREND", "BB_UPPER", "BB_MID", "BB_LOWER"})
OPERATOR_WORDS = {"GT": "is above", "LT": "is below", "GTE": "is at or above", "LTE": "is at or below", "CROSSES_ABOVE": "crosses above",
                  "CROSSES_BELOW": "crosses below"}

HOW_I_BUILD = ("I use only ATP's own building blocks (the same ones as Build a strategy). I don't add a stop, an objective, a size "
               "or any rule you did not state: every number is your setting. Nothing is saved or started until you do it yourself.")
NO_BACKTEST = ("I don't know how this idea did in the past: I can test the draft on past data once every value is yours (say them in "
               "your idea and ask me to test it), or test it on the Backtesting page after you save it.")
NOT_TESTED = ("I don't know how this idea did in the past: I have not tested it yet. Press \"Test it on past data\" and I will run it "
              "through ATP's backtest engine.")
TEST_LABEL = "Test it on past data"
TEST_SUFFIX = " Test it on past data."
EXAMPLE = ("Turn my idea into a strategy: on the 15-minute chart, go long when the 20 EMA crosses above the 50 EMA and RSI 14 is "
           "above 50. Stop 1.5 x ATR 14, objective 2R.")
LEFT_OUT = "The draft leaves these parts out, so it is not your whole idea yet: decide whether it still fits."

# Parts of an idea ATP cannot build into a strategy yet: (pattern, plain label, ScreenQL functions that can look for it
# today, a ready-made strategy that uses it). The Screener pointer is shown only when the registry has the function.
UNSUPPORTED: Tuple[Tuple[str, str, Tuple[str, ...], Optional[str]], ...] = (
    (r"\bdivergen", "RSI divergence", ("RsiDivergence",), None),
    (r"\bmacd\b", "MACD", ("MACD",), "macd_ema_trend_5m"),
    (r"\b(engulfing|pin ?bar|hammer|doji|inside bar|outside bar|morning star|evening star|shooting star|candlestick|candle pattern)",
     "candle patterns", ("Pattern",), None),
    (r"\b(demand|supply|order block|liquidity|sweep|break of structure|bos|choch|change of character|handover|seller area|buyer area|"
     r"support|resistance|zones?)\b", "price-action areas and structure (zones, breaks of structure, sweeps)", ("NearZone",), None),
    (r"\b(trend ?lines?|channel|triangle|wedge|flag pattern|head and shoulders|double top|double bottom|cup and handle)\b",
     "chart patterns and trendlines", ("TrendlineBreakout", "PatternBreakout"), None),
    (r"\b(\d+[- ]?(day|week|bar|candle)s?'? (high|low)|52[- ]week|all[- ]time high|new highs?|new lows?|highest high|lowest low)\b",
     "a break of the highest high or lowest low of many candles", ("NewHighBreakout", "DonchianBreakout"), "swing_breakout_d"),
    (r"\b(pull ?backs?|pulls back|retests?|retracement)\b", "a pullback or retest", (), "swing_ema_pullback_d"),
    (r"\b(news|results|earnings|budget|rbi|fed|policy meeting|announcement)\b", "news and events", (), None),
    (r"\b(p/?e ratio|pe ratio|eps|fundamentals?|sales growth|profit growth|roe|debt|market cap)\b", "company fundamentals", (), None),
    (r"\b(open interest|oi|pcr|implied volatility|iv|greeks?|delta|gamma|theta|vega|max pain|option chain)\b",
     "option chain data (open interest, PCR, implied volatility, greeks)", (), None),
    (r"\b(fii|dii)\b", "FII / DII flows", (), None),
    (r"\bvix\b", "India VIX", (), None),
    (r"\b(\d{1,2}(:\d{2})? ?(am|pm)|o'clock|first hour|last hour|morning|afternoon|monday|tuesday|wednesday|thursday|friday|expiry day)\b",
     "a time-of-day or day-of-week rule", (), None),
    (r"\btrail(ing)?\b", "a trailing stop inside the strategy (set it on the bot form instead: More ways to trade, exits)", (), None),
    (r"\b(martingale|average down|averaging|pyramid(ing)?|add to (my )?(winners|losers)|scale in)\b",
     "adding to a position (averaging or pyramiding)", (), None),
    (r"\b(straddle|strangle|iron condor|spread|hedge)\b", "an option structure (pick it on the bot form instead: More ways to trade)",
     (), None),
    (r"\b(all stocks|every stock|many stocks|scan|top gainers|top losers|basket|watchlist)\b",
     "running on many symbols at once (a paper strategy runs on one symbol; the Screener scans many)", (), None),
    (r"\b(daily|weekly|monthly|day chart|week chart|1d|1w|swing trades?|swing trading|positional)\b", "daily or weekly candles in your own rules", (),
     "swing_ema_pullback_d"),
    (r"\b(stochastic|ichimoku|cci|williams|fibonacci|fib|pivot|heikin|renko|parabolic|psar|obv|mfi|keltner|donchian|camarilla|aroon)\b",
     "this indicator", (), None),
)
INDICATOR_NAMES = {"stochastic": "Stochastic", "ichimoku": "Ichimoku", "cci": "CCI", "williams": "Williams %R", "fibonacci": "Fibonacci",
                   "fib": "Fibonacci", "pivot": "pivot points", "heikin": "Heikin-Ashi", "renko": "Renko", "parabolic": "Parabolic SAR",
                   "psar": "Parabolic SAR", "obv": "OBV", "mfi": "MFI", "keltner": "Keltner channels", "donchian": "Donchian channels",
                   "camarilla": "Camarilla levels", "aroon": "Aroon"}


# --- the draft ----------------------------------------------------------------------------------------------------------

@dataclass
class Rule:
    side: Optional[str]                          # "long" / "short" / None (side not stated)
    left: Dict[str, Any]
    operator: Optional[str]                      # None: a cross whose direction the words do not state (asked)
    right: Dict[str, Any]
    words: str = ""                              # the trader's own words for this part
    read_as: Optional[str] = None                # an interpretation the trader should check ("I read ... as ...")

    def condition(self) -> Dict[str, Any]:
        return {"left": dict(self.left), "operator": self.operator, "right": dict(self.right)}


@dataclass
class Draft:
    idea: str
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    rules: List[Rule] = field(default_factory=list)
    stop_loss_atr_mult: Optional[float] = None
    atr_period: Optional[int] = None
    target_rr: Optional[Tuple[float, float]] = None
    min_rr: Optional[float] = None
    min_rr_source: Optional[str] = None          # "your words" / "your risk limits"
    lots: Optional[int] = None
    unsupported: List[str] = field(default_factory=list)
    unread: List[str] = field(default_factory=list)
    read_as: List[str] = field(default_factory=list)
    ready_made: List[str] = field(default_factory=list)     # Library strategy ids close to a part ATP cannot build

    @property
    def empty(self) -> bool:
        return not (self.rules or self.unsupported or self.unread)

    def asks(self) -> List[Dict[str, str]]:
        """Every value the trader must still decide, in the order the review card shows them."""
        out: List[Dict[str, str]] = []
        if any(r.side is None for r in self.rules):
            out.append({"key": "side", "label": "The side: long, short or both"})
        if self.timeframe is None:
            out.append({"key": "timeframe", "label": "The timeframe: the candles your rules read"})
        per_rule = []
        for i, r in enumerate(self.rules):
            items = [(f"rules.{i}.{part}.{key}", label) for part in ("left", "right") for key, label in _operand_asks(r.__dict__[part])]
            if len(items) == 2 and items[0][1] == items[1][1]:   # the same value on both sides of one rule: the first and the second
                items = [(items[0][0], items[0][1].replace("The ", "The first ", 1)), (items[1][0], items[1][1].replace("The ", "The second ", 1))]
            if r.operator is None:
                items.append((f"rules.{i}.operator", "The direction of the cross: above or below"))
            per_rule.append(items)
        labels = [label for items in per_rule for _, label in items]
        for i, items in enumerate(per_rule):
            for key, label in items:
                side = self.rules[i].side
                if labels.count(label) > 1:          # the same value in two rules: say which rule it belongs to
                    label = f"{label} ({side + ' side, ' if side else ''}rule {i + 1})"
                out.append({"key": key, "label": label})
        if self.stop_loss_atr_mult is None:
            out.append({"key": "stop_loss_atr_mult", "label": "Where the idea is wrong: the stop distance, in ATRs from the entry"})
        if self.atr_period is None:
            out.append({"key": "atr_period", "label": "The ATR length the stop distance uses"})
        if self.target_rr is None:
            out.append({"key": "target_rr", "label": "Your profit objective, in R (a multiple of the risk)"})
        if self.min_rr is None:
            out.append({"key": "min_rr", "label": "Your minimum reward to risk for a trade to count"})
        return out

    def as_dict(self) -> Dict[str, Any]:
        sides = [s for s in ("long", "short") if any(r.side == s for r in self.rules)]
        return {
            "idea": self.idea, "name": draft_name(self), "symbol": self.symbol, "timeframe": self.timeframe, "sides": list(sides),
            "rules": [{"side": r.side, "condition": r.condition(), "text": rule_text(r, self.timeframe), "words": r.words} for r in self.rules],
            "settings": {"stop_loss_atr_mult": self.stop_loss_atr_mult, "atr_period": self.atr_period,
                         "target_rr": list(self.target_rr) if self.target_rr else None, "min_rr": self.min_rr},
            "min_rr_source": self.min_rr_source, "lots": self.lots, "asks": self.asks(), "unsupported": list(self.unsupported),
            "unread": list(self.unread), "read_as": list(self.read_as), "ready_made": list(self.ready_made),
            "complete": bool(self.rules) and not self.asks(), "mode": "PAPER",
        }


def _operand_asks(op: Dict[str, Any]) -> List[Tuple[str, str]]:
    if op.get("type") == "value":
        return [] if op.get("value") is not None else [("value", f"The level for {op.get('of') or 'this rule'}")]
    ind, out = op.get("indicator"), []
    if ind in NEEDS_PERIOD and op.get("period") is None:
        role = f"{op['role']} " if op.get("role") else ""
        out.append(("period", "The opening range length, in minutes" if ind in ("OR_HIGH", "OR_LOW") else f"The {role}{_ind_name(ind)} length"))
    if ind in NEEDS_MULTIPLIER and op.get("multiplier") is None:
        out.append(("multiplier", "The Supertrend multiplier" if ind == "SUPERTREND" else "The Bollinger band width, in standard deviations"))
    return out


# --- words for the trader -----------------------------------------------------------------------------------------------

def _ind_name(ind: Optional[str]) -> str:
    return {"PLUS_DI": "+DI", "MINUS_DI": "-DI", "BB_UPPER": "Bollinger band", "BB_MID": "Bollinger band", "BB_LOWER": "Bollinger band",
            "VOLUME_SMA": "average volume", "SUPERTREND": "Supertrend"}.get(str(ind), str(ind))


def _n(value: Any) -> str:
    x = float(value)
    return str(int(x)) if x.is_integer() else f"{x:g}"


def operand_text(op: Dict[str, Any]) -> str:
    if op.get("type") == "value":
        return _n(op["value"]) if op.get("value") is not None else "a level you decide"
    ind, p, m = op.get("indicator"), op.get("period"), op.get("multiplier")
    length = _n(p) if p is not None else None
    text = {
        "CLOSE": "the price (candle close)", "OPEN": "the candle's open", "HIGH": "the candle's high", "LOW": "the candle's low",
        "VWAP": "VWAP", "DAY_OPEN": "the day's open", "PDH": "the previous day's high", "PDL": "the previous day's low",
        "PDC": "the previous day's close", "VOLUME": "the volume",
    }.get(str(ind))
    if text is None:
        if ind in ("EMA", "SMA"):
            role = f"{op['role']} " if op.get("role") else ""
            text = f"the {length} {ind}" if length else f"the {role}{ind} (length to decide)"
        elif ind in ("RSI", "ADX", "PLUS_DI", "MINUS_DI", "ATR"):
            text = f"{_ind_name(ind)} ({length})" if length else f"{_ind_name(ind)} (length to decide)"
        elif ind == "SUPERTREND":
            text = f"Supertrend ({length or 'length to decide'}, {_n(m) if m is not None else 'multiplier to decide'})"
        elif ind in ("BB_UPPER", "BB_MID", "BB_LOWER"):
            which = {"BB_UPPER": "upper", "BB_MID": "middle", "BB_LOWER": "lower"}[str(ind)]
            text = f"the {which} Bollinger band ({length or 'length to decide'}, {_n(m) if m is not None else 'width to decide'})"
        elif ind == "VOLUME_SMA":
            text = f"its {length}-candle average" if length else "its average (length to decide)"
        elif ind in ("OR_HIGH", "OR_LOW"):
            which = "high" if ind == "OR_HIGH" else "low"
            text = f"the {which} of the first {length} minutes" if length else f"the opening range {which} (minutes to decide)"
        else:
            text = str(ind)
    if op.get("timeframe"):
        text += f" on the {TF_WORDS.get(op['timeframe'], op['timeframe'])} chart"
    return text


def rule_text(rule: Rule, base_tf: Optional[str] = None) -> str:
    left, right = operand_text(rule.left), operand_text(rule.right)
    words = OPERATOR_WORDS[rule.operator] if rule.operator else "crosses (above or below: yours to decide)"
    text = f"{left[0].upper()}{left[1:]} {words} {right}."
    return text


def draft_name(d: Draft) -> str:
    seen: List[str] = []
    for r in d.rules:
        for op in (r.left, r.right):
            ind = op.get("indicator") if op.get("type") == "indicator" else None
            word = {"OR_HIGH": "opening range", "OR_LOW": "opening range", "PDH": "previous day", "PDL": "previous day", "PDC": "previous day",
                    "BB_UPPER": "Bollinger", "BB_MID": "Bollinger", "BB_LOWER": "Bollinger", "VOLUME_SMA": "volume", "VOLUME": "volume",
                    "PLUS_DI": "DI", "MINUS_DI": "DI", "SUPERTREND": "Supertrend"}.get(str(ind), ind)
            if word and word not in ("CLOSE", "OPEN", "HIGH", "LOW", "DAY_OPEN") and word not in seen:
                seen.append(word)
    return ("My " + " + ".join(seen[:3]) + " idea") if seen else "My idea"


# --- reading the words --------------------------------------------------------------------------------------------------

_NUM = r"(\d+(?:\.\d+)?)"
_TF = re.compile(r"\b(\d{1,2})\s*-?\s*(?:m|min|mins|minute|minutes)\b(?:\s*(?:chart|candles?|bars?|timeframe|tf))?|"
                 r"\b(hourly|1\s*-?\s*h(?:our|r)?|one hour|60\s*-?\s*min(?:ute)?s?)\b(?:\s*(?:chart|candles?|bars?|timeframe|tf))?")
_OPENING_MINUTES = re.compile(r"\b(?:first|opening)\s+(\d{1,3})\s*-?\s*(?:m|min|mins|minute|minutes)\b")
_MIN_RR = re.compile(rf"\bmin(?:imum)?\.?\s*(?:reward[- ]to[- ]risk|risk[- ]?(?:to[- ])?reward|r:r|rr)\s*(?:of|is|at|=)?\s*(?:1\s*:\s*)?{_NUM}")
_STOP_ATR = re.compile(rf"\b(?:stop(?:[- ]?loss)?|sl)\b(?:\s+(?:at|of|is|=|distance|by))*\s*{_NUM}\s*(?:x|times)?\s*(?:the\s+)?atr\b|"
                       rf"{_NUM}\s*(?:x|times)?\s*atr\s*(?:\(\s*\d+\s*\)\s*|\d+\s+)?(?:stop(?:[- ]?loss)?|sl)\b")
_ATR_PERIOD = re.compile(r"\batr\s*(?:\(\s*(\d{1,3})\s*\)|(?:period|length)\s*(?:of\s*)?(\d{1,3})|(\d{1,3})\b(?!\s*(?:x|times)))|"
                         r"\b(\d{1,3})\s*-?\s*(?:period|bar|candle)\s+atr\b")
_STOP_WORD = re.compile(r"\b(?:stop(?:[- ]?loss)?|sl)\b")
_TARGET_R = re.compile(rf"\b(?:target|targets|objective|objectives|take[- ]?profit|tp|book(?:ing)? (?:profit|at))\b[^.;]*?{_NUM}\s*r\b"
                       rf"(?:\s*(?:and|then|,|/|to)\s*{_NUM}\s*r\b)?|"
                       rf"\b(?:reward[- ]to[- ]risk|risk[- ]?(?:to[- ])?reward|r:r|rr)\s*(?:of|is|at|=)?\s*(?:1\s*:\s*)?{_NUM}|"
                       rf"(?<![\d.]){_NUM}\s*r\b(?:\s*(?:and|then|,|/|to)\s*{_NUM}\s*r\b)?|"
                       rf"\b(?:target|objective)\b[^.;\d]*?{_NUM}\s*(?:x|times)\s*(?:the\s+|my\s+)?risk\b")
_TARGET_WORD = re.compile(r"\b(?:target|targets|objective|take[- ]?profit|tp|book(?:ing)? profit)\b")
_LOTS = re.compile(r"\b(\d{1,3})\s*lots?\b")
_EXIT = re.compile(r"^\s*(?:and\s+)?(?:exit|close|book|square ?off|get out)\b")
_LONG = re.compile(r"\b(long|buy|buying|bullish)\b")
_SHORT = re.compile(r"\b(short|shorting|sell|selling|bearish)\b")
_PREAMBLE = re.compile(r"^(?:please\s+|pls\s+|can you\s+|could you\s+)?(?:turn|make|build|convert|create|write|draft|put)\b[^:]*?"
                       r"\b(?:strategy|strategies|rules|bot)\b(?:\s+and\s+test\s+it)?\s*[:,\-]?\s*")
_PREAMBLE_2 = re.compile(r"^(?:here is |this is )?(?:my|the) (?:trading )?(?:idea|strategy|rules|plan)(?: is)?\s*[:,\-]?\s*")
_FILLER = re.compile(r"\b(?:on|for|the|a|an|chart|charts|candle|candles|bar|bars|timeframe|tf|in|of|my|idea|strategy|enter|entry|go|take|"
                     r"trade|trades|trading|when|whenever|if|once|it|is|then|only|also|i|want|to|with|and|use|using|at|time|lots?|"
                     r"long|short|buy|sell|bullish|bearish|side|both|sides|please|test|paper|this|that|rules?|be|should|will|just|"
                     r"x|times|stop|loss|sl|target|objective|r|rr|atr|period|min|minimum|reward|risk|nse|bse|index|stock|shares?|"
                     r"future|futures|option|options|nifty|banknifty|bank|finnifty|midcpnifty|sensex|50|entries|signal|signals|setup|"
                     r"look|looks|for|wait|waits|forms?|appears?|there|are|have|has|get|gets|candlestick)\b")
MARK = "\u00a7"                                   # stands where a part listed under "Not supported yet" was

_MA = r"(?:(\d{1,3})\s*-?\s*(?:period\s*|day\s*|bar\s*)?)?(ema|sma|dma|moving average|ma)\b(?:\s*\(?\s*(\d{1,3})\s*\)?)?"
_CROSS = re.compile(rf"(?:(fast(?:er)?|slow(?:er)?)\s+)?{_MA}\s*(?:line\s+)?(?:cross(?:es|ed|ing)?|cuts?)\s*(above|over|up(?:wards)?|below|under|down(?:wards)?)?"
                    rf"\s*(?:through\s+)?(?:the\s+)?(?:(fast(?:er)?|slow(?:er)?)\s+)?{_MA}")
_CMP: Tuple[Tuple[str, str], ...] = (
    (r"crosses (?:above|over)|crossed (?:above|over)|cross (?:above|over)|breaks? (?:out )?above|broke above|reclaims?|rises above|moves above|"
     r"closes? back above|breaks? out of", "CROSSES_ABOVE"),
    (r"crosses (?:below|under)|crossed (?:below|under)|cross (?:below|under)|breaks? (?:down )?below|broke below|falls below|drops below|"
     r"moves below|closes? back below|breaks? down", "CROSSES_BELOW"),
    (r">=|at or above|greater than or equal to", "GTE"),
    (r"<=|at or below|less than or equal to", "LTE"),
    (r"closes? above|is above|stays above|holds above|trades above|above|over|>|greater than|higher than", "GT"),
    (r"closes? below|is below|stays below|holds below|trades below|below|under|<|less than|lower than", "LT"),
    (r"\bbreaks?\b|\bcrosses\b|\bcross\b", "BREAK"),
)
_CMP_RE = [(re.compile(rf"(?<![\w>=<])(?:{p})(?![\w])" if not p.startswith(">") and not p.startswith("<") else rf"(?:{p})"), op) for p, op in _CMP]


def _ind(name: str, period: Optional[int] = None, multiplier: Optional[float] = None) -> Dict[str, Any]:
    return {"type": "indicator", "indicator": name, "period": period, "multiplier": multiplier, "timeframe": None}


def _value(v: Optional[float], of: Optional[str] = None) -> Dict[str, Any]:
    return {"type": "value", "value": v, **({"of": of} if of else {})}


def _int(text: Optional[str]) -> Optional[int]:
    return int(text) if text else None


def parse_operand(text: str) -> Optional[Dict[str, Any]]:
    """One side of a comparison as a building block; a length or level the words do not state stays None."""
    t = text.strip()
    if not t:
        return None
    m = re.search(r"\brsi\b\s*(?:\(\s*(\d{1,3})\s*\)|(\d{1,3})\b)?|\b(\d{1,3})\s*-?\s*(?:period\s+)?rsi\b", t)
    if m:
        return _ind("RSI", _int(m.group(1) or m.group(2) or m.group(3)))
    m = re.search(r"(?:\+\s*di|plus\s*di|\+di)\b", t)
    if m:
        return _ind("PLUS_DI", _int((re.search(r"di\s*\(?\s*(\d{1,3})", t) or [None, None])[1]))
    m = re.search(r"(?:-\s*di|minus\s*di|-di)\b", t)
    if m:
        return _ind("MINUS_DI", _int((re.search(r"di\s*\(?\s*(\d{1,3})", t) or [None, None])[1]))
    m = re.search(r"\badx\b\s*(?:\(\s*(\d{1,3})\s*\)|(\d{1,3})\b)?", t)
    if m:
        return _ind("ADX", _int(m.group(1) or m.group(2)))
    m = re.search(rf"\bsuper ?trend\b\s*(?:\(\s*(\d{{1,3}})\s*[, ]\s*{_NUM}\s*\)|(\d{{1,3}})\s*[, ]\s*{_NUM}\b|(\d{{1,3}})\b)?", t)
    if m:
        return _ind("SUPERTREND", _int(m.group(1) or m.group(3) or m.group(5)),
                    float(m.group(2) or m.group(4)) if (m.group(2) or m.group(4)) else None)
    m = re.search(r"\b(upper|lower|middle|mid)\s+(?:bollinger\s+)?band\b|\bbollinger\s+(upper|lower|middle|mid)\b|\bbb\s*(upper|lower|mid)\b", t)
    if m:
        which = next(g for g in m.groups() if g)
        name = {"upper": "BB_UPPER", "lower": "BB_LOWER", "middle": "BB_MID", "mid": "BB_MID"}[which]
        pm = re.search(rf"band\w*\s*\(?\s*(\d{{1,3}})\s*[, ]\s*{_NUM}", t)
        return _ind(name, _int(pm.group(1)) if pm else None, float(pm.group(2)) if pm else None)
    m = re.search(r"\b(?:previous|prev|yesterday'?s?|last)\s+(?:day'?s?\s+|session'?s?\s+)?(high|low|close)\b|\b(pdh|pdl|pdc)\b", t)
    if m:
        key = (m.group(1) or {"pdh": "high", "pdl": "low", "pdc": "close"}[m.group(2)])
        return _ind({"high": "PDH", "low": "PDL", "close": "PDC"}[key])
    m = re.search(r"\b(?:opening range|or|first\s+(\d{1,3})\s*-?\s*(?:m|min|mins|minute|minutes)'?s?)\s*(high|low)\b|\b(high|low) of the first\s+(\d{1,3})\s*-?\s*"
                  r"(?:m|min|mins|minute|minutes)\b|\bopening range (high|low)\b", t)
    if m:
        which = m.group(2) or m.group(3) or m.group(5)
        return _ind("OR_HIGH" if which == "high" else "OR_LOW", _int(m.group(1) or m.group(4)))
    m = re.search(r"\b(?:day'?s open|today'?s open|day open|opening price)\b", t)
    if m:
        return _ind("DAY_OPEN")
    m = re.search(r"\bvwap\b", t)
    if m:
        return _ind("VWAP")
    m = re.search(r"\b(?:average volume|volume average|avg volume|its average|the average|volume sma|volume ma)\b(?:\s*\(?\s*(\d{1,3})\s*\)?)?|"
                  r"\b(\d{1,3})\s*-?\s*(?:candle|bar|period|day)?\s*average(?: volume)?\b", t)
    if m:
        return _ind("VOLUME_SMA", _int(m.group(1) or m.group(2)))
    m = re.search(r"\bvolume\b", t)
    if m:
        return _ind("VOLUME")
    m = re.search(_MA, t)
    if m:
        kind = m.group(2)
        return _ind("EMA" if kind == "ema" else "SMA", _int(m.group(1) or m.group(3)))
    m = re.search(r"\b(?:candle'?s?|bar'?s?)\s+(high|low|open)\b", t)
    if m:
        return _ind(m.group(1).upper())
    if re.search(r"\b(price|close|closes|closing|candle|it|market|spot|ltp)\b", t) or re.fullmatch(r"[a-z&]{2,15}", t):
        return _ind("CLOSE")
    m = re.search(rf"(?<![\w.]){_NUM}(?![\w.])", t)
    if m:
        return _value(float(m.group(1)))
    return None


def _cross_op(direction: Optional[str], side: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """The operator for a cross: the stated direction, else the side's natural one (named under "How I read your words")."""
    d = (direction or "").lower()
    if d.startswith(("above", "over", "up")):
        return "CROSSES_ABOVE", None
    if d.startswith(("below", "under", "down")):
        return "CROSSES_BELOW", None
    if side == "long":
        return "CROSSES_ABOVE", "crosses (read as: crosses above, on the long side)"
    if side == "short":
        return "CROSSES_BELOW", "crosses (read as: crosses below, on the short side)"
    return None, None


def _special(clause: str, side: Optional[str]) -> Optional[List[Rule]]:
    """Phrases that name a whole rule: a Supertrend flip, an opening-range break, +DI / -DI, volume above its average,
    RSI overbought / oversold, a strong trend by ADX."""
    c = clause
    m = re.search(r"\bsuper ?trend\b[^.]*?\b(turns|flips|changes|is|stays|goes)?\s*(green|bullish|up|positive|red|bearish|down|negative)\b", c)
    if m and not re.search(r"\b(above|below|crosses|over|under)\b", c):
        up = m.group(2) in ("green", "bullish", "up", "positive")
        flip = m.group(1) in ("turns", "flips", "changes", "goes")
        st = parse_operand(c[:m.start(2)]) or _ind("SUPERTREND")
        op = ("CROSSES_ABOVE" if up else "CROSSES_BELOW") if flip else ("GT" if up else "LT")
        return [Rule(side, _ind("CLOSE"), op, st, clause, f"Supertrend {m.group(2)} (read as: the price {OPERATOR_WORDS[op]} Supertrend)")]
    m = re.search(r"\b(opening range breakout|orb|opening range break(?:out)?|breaks? (?:out of )?the opening range|"
                  r"breaks? the first\s+\d{1,3}\s*-?\s*(?:m|min|mins|minute|minutes)'?s? range)\b", c)
    if m:
        minutes = _OPENING_MINUTES.search(c)
        period = int(minutes.group(1)) if minutes else None
        if side == "short" or re.search(r"\b(down|below|low)\b", c):
            return [Rule(side, _ind("CLOSE"), "CROSSES_BELOW", _ind("OR_LOW", period), clause, None)]
        if side == "long" or re.search(r"\b(up|above|high)\b", c):
            return [Rule(side, _ind("CLOSE"), "CROSSES_ABOVE", _ind("OR_HIGH", period), clause, None)]
        return [Rule("long", _ind("CLOSE"), "CROSSES_ABOVE", _ind("OR_HIGH", period), clause, "opening range breakout (read as: a break above "
                     "the range on the long side and below it on the short side)"),
                Rule("short", _ind("CLOSE"), "CROSSES_BELOW", _ind("OR_LOW", period), clause, None)]
    if re.search(r"(?:\+\s*di|plus\s*di)\b[^.]*\b(above|over|>|crosses above)\b[^.]*(?:-\s*di|minus\s*di)\b", c):
        op = "CROSSES_ABOVE" if "cross" in c else "GT"
        return [Rule(side, _ind("PLUS_DI"), op, _ind("MINUS_DI"), clause, None)]
    if re.search(r"(?:-\s*di|minus\s*di)\b[^.]*\b(above|over|>|crosses above)\b[^.]*(?:\+\s*di|plus\s*di)\b", c):
        op = "CROSSES_ABOVE" if "cross" in c else "GT"
        return [Rule(side, _ind("MINUS_DI"), op, _ind("PLUS_DI"), clause, None)]
    m = re.search(r"\b(high|rising|strong|big|heavy|spike in|surge in|above[- ]average)\s+volume\b|\bvolume\s+(spike|surge|is high|rises|picks up|confirms|"
                  r"confirmation|above (?:its |the )?average)\b|\bwith volume\b|^\s*volume(?: confirmation)?\s*$", c)
    if m:
        if re.search(rf"{_NUM}\s*(?:x|times)\b", c):
            return None                                # a multiple of the average: not a building block (listed as not supported)
        avg = re.search(r"average\s*\(?\s*(\d{1,3})\s*\)?|(\d{1,3})\s*-?\s*(?:candle|bar|period|day)\s+average", c)
        read = None if "average" in m.group(0) else f"{m.group(0).strip()} (read as: the volume is above its average)"
        return [Rule(side, _ind("VOLUME"), "GT", _ind("VOLUME_SMA", _int(avg.group(1) or avg.group(2)) if avg else None), clause, read)]
    m = re.search(r"\brsi\b[^.]*?\b(oversold|overbought)\b|\b(oversold|overbought)\b[^.]*\brsi\b", c)
    if m:
        word = m.group(1) or m.group(2)
        rsi = parse_operand(c) or _ind("RSI")
        level = re.search(rf"(?<![\w.]){_NUM}(?![\w.])", re.sub(r"\brsi\s*\(?\s*\d{1,3}\s*\)?", " ", c))
        op = "LT" if word == "oversold" else "GT"
        return [Rule(side, rsi, op, _value(float(level.group(1)) if level else None, f"RSI {word}"), clause,
                     None if level else f"RSI {word} (read as: RSI {OPERATOR_WORDS[op]} the level you call {word})")]
    m = re.search(r"\b(strong trend|trending|trend is strong|adx is strong|adx rising|adx strong)\b", c)
    if m and not re.search(r"\badx\b[^.]*\b(above|below|over|under|>|<)\b", c):
        adx = parse_operand(c) if "adx" in c else _ind("ADX")
        return [Rule(side, adx or _ind("ADX"), "GT", _value(None, "ADX"), clause, f"{m.group(1)} (read as: ADX is above a level you decide)")]
    return None


FIXED_LEVEL_RULE: List[Rule] = []                # parse_clause's answer for "the price is above 24000" (not a building block)


def parse_clause(clause: str, side: Optional[str]) -> Optional[List[Rule]]:
    """One clause as rules, or None when no building block matches it."""
    special = _special(clause, side)
    if special is not None:
        return special
    m = _CROSS.search(clause)
    if m:
        op, read = _cross_op(m.group(5), side)
        first = _ind("EMA" if m.group(3) == "ema" else "SMA", _int(m.group(2) or m.group(4)))
        second = _ind("EMA" if m.group(8) == "ema" else "SMA", _int(m.group(7) or m.group(9)))
        roles = [("fast" if (g or "").startswith("fast") else "slow" if g else None) for g in (m.group(1), m.group(6))]
        if roles[0] or roles[1]:                        # "the fast EMA crosses the slow EMA": his words name the two averages
            first["role"] = roles[0] or ("slow" if roles[1] == "fast" else "fast")
            second["role"] = roles[1] or ("slow" if first["role"] == "fast" else "fast")
        if op is None:
            return [Rule(side, first, None, second, clause, None)]          # the direction is asked, never guessed
        reads = [read] if read else []
        if m.group(3) in ("ma", "dma", "moving average") or m.group(8) in ("ma", "dma", "moving average"):
            reads.append("moving average (read as: a simple moving average, SMA)")
        return [Rule(side, first, op, second, clause, "; ".join(reads) or None)]
    for pattern, op in _CMP_RE:
        hit = pattern.search(clause)
        if not hit:
            continue
        before, after = clause[:hit.start()], clause[hit.end():]
        right = parse_operand(after)
        left = parse_operand(before) or (_ind("CLOSE") if right is not None and right.get("type") == "indicator" else None)
        if left is None or right is None:
            return None
        if left.get("type") == "value" and right.get("type") == "value":
            return None
        if right.get("type") == "value" and left.get("indicator") in ("CLOSE", "OPEN", "HIGH", "LOW"):
            return FIXED_LEVEL_RULE                    # a fixed price level: an ATP rule compares building blocks, not one price
        read = None
        if op == "BREAK":
            low_side = right.get("indicator") in ("PDL", "OR_LOW", "BB_LOWER")
            high_side = right.get("indicator") in ("PDH", "OR_HIGH", "BB_UPPER")
            op = "CROSSES_BELOW" if low_side or (not high_side and side == "short") else "CROSSES_ABOVE"
            if not (low_side or high_side):
                if side is None:
                    return None
                read = f"{hit.group(0).strip()} (read as: {OPERATOR_WORDS[op]}, on the {side} side)"
        if left.get("indicator") == "VOLUME" and right.get("type") == "indicator" and right.get("indicator") in ("EMA", "SMA"):
            right = _ind("VOLUME_SMA", right.get("period"))
        if right.get("type") == "value" and right.get("value") is None:
            right = _value(None, _ind_name(left.get("indicator")))
        return [Rule(side, left, op, right, clause, read)]
    return None


def _timeframe_of(minutes: int) -> Optional[str]:
    tf = f"{minutes}min"
    return tf if tf in TIMEFRAMES else None


def _tf_tokens(text: str) -> List[Tuple[int, int, Optional[str], str]]:
    """(start, end, timeframe or None when not available, the words) for every timeframe the text names; the opening
    range's "first 15 minutes" is not a timeframe."""
    out = []
    for m in _TF.finditer(text):
        before = text[max(0, m.start() - 9):m.start()]
        if re.search(r"(first|opening)\s*$", before) or re.search(r"\b(?:range|high|low)\b", text[m.end():m.end() + 7]):
            continue
        minutes = int(m.group(1)) if m.group(1) else 60
        out.append((m.start(), m.end(), _timeframe_of(minutes), m.group(0)))
    return out


def _side_of(sentence: str) -> Optional[str]:
    long, short = bool(_LONG.search(sentence)), bool(_SHORT.search(sentence))
    return "long" if long and not short else "short" if short and not long else None


# CP6-2: the trader asks for the honest test of his draft ("... and test it", "backtest my idea: ...", "how did it do in the past").
_TEST_LEAD = re.compile(r"^(?:please\s+|pls\s+|now\s+|can you\s+|could you\s+)?(?:back-?test|test)\s+(?:this|my|the)?\s*"
                        r"(?:idea|strategy|draft|rules|setup)?(?:\s+on (?:the )?past data)?\s*[:,\-]\s*")
_TEST_PHRASE = re.compile(r"[,;]?\s*(?:\band\s+)?(?:\bthen\s+)?(?:\bplease\s+)?\b(?:back-?test|test)\s+(?:it|this|that|the draft|my draft|"
                          r"the idea|my idea|the strategy|my strategy|the rules|my rules)\b(?:\s+on (?:the )?past (?:data|candles|charts?))?"
                          r"(?:\s+for me)?\s*[.!]?|[,;]?\s*(?:\band\s+)?\bhow (?:did|does|would) (?:it|this|that|my idea|my strategy|my draft|the draft)(?: idea| strategy)? "
                          r"(?:do|have done|perform|have performed)(?: in the past| on past data)?\s*\??|[,;]?\s*(?:\band\s+)?\bbacktest\b\s*[.!]?",
                          re.IGNORECASE)


def test_asked(message: str) -> bool:
    """Whether the trader asks for the test on past data (a backtest of his draft)."""
    low = (message or "").lower().replace("’", "'")
    return bool(_TEST_LEAD.match(low.strip()) or _TEST_PHRASE.search(low))


def without_test_ask(message: str) -> str:
    """His words without the ask for a test, so "and test it" is never read as a part of the idea."""
    text = (message or "").strip()
    m = _TEST_LEAD.match(text.lower())
    if m:
        text = text[m.end():]
    return re.sub(r"\s+", " ", _TEST_PHRASE.sub(" ", text)).strip()


def strip_preamble(message: str) -> str:
    text = re.sub(r"\s+", " ", (message or "").replace("’", "'").replace("–", "-").replace("—", "-")).strip()
    low = text.lower()
    for pattern in (_PREAMBLE, _PREAMBLE_2):
        m = pattern.match(low)
        if m:
            text, low = text[m.end():], low[m.end():]
    return text.strip(" :,-")


def build(message: str, symbol: Optional[str] = None, own_min_rr: Optional[float] = None) -> Draft:
    """The draft for the trader's words. `own_min_rr` is his OWN saved minimum reward to risk (risk limits), or None."""
    idea = strip_preamble(without_test_ask(message))
    d = Draft(idea=idea, symbol=symbol)
    text = idea.lower()
    if not re.search(r"[a-z]", text):
        return d

    # The settings his words state (each span is removed before the rules are read).
    spans: List[Tuple[int, int]] = []
    m = _MIN_RR.search(text)
    if m:
        d.min_rr, d.min_rr_source = float(m.group(1)), "your words"
        spans.append(m.span())
    m = _STOP_ATR.search(text)
    if m:
        d.stop_loss_atr_mult = float(m.group(1) or m.group(2))
        spans.append(m.span())
    m = _ATR_PERIOD.search(_mask(text, spans) if not m else text)
    if m:
        d.atr_period = int(next(g for g in m.groups() if g))
        spans.append(m.span())
    m = _TARGET_R.search(_mask(text, spans))
    if m:
        g = [x for x in m.groups() if x]
        first = float(g[0])
        d.target_rr = (first, float(g[1]) if len(g) > 1 else first)
        spans.append(m.span())
    m = _LOTS.search(text)
    if m:
        d.lots = int(m.group(1))
        spans.append(m.span())
    m = _OPENING_MINUTES.search(text)
    opening = int(m.group(1)) if m else None
    if m:
        spans.append(m.span())
    masked = _mask(text, spans)
    m = _STOP_WORD.search(masked)
    if m and d.stop_loss_atr_mult is None:
        d.unsupported.append("a stop at a chart point, in points or in percent: an ATP strategy's stop is a distance in ATRs from the entry "
                             "- tell me that distance")
        masked = _mask_to_clause_end(masked, m.start())
    m = _TARGET_WORD.search(masked)
    if m and d.target_rr is None:
        d.unsupported.append("an objective in points, in percent or at a chart point: an ATP strategy's objective is in R (a multiple of "
                             "the risk) - tell me that multiple")
        masked = _mask_to_clause_end(masked, m.start())
    m = re.search(rf"{_NUM}\s*(?:x|times)\s*(?:the\s+|its\s+)?(?:average|avg)?\s*volume|volume[^.,;]*?{_NUM}\s*(?:x|times)[^.,;]*", masked)
    if m:
        d.unsupported.append("volume as a multiple of its average (ATP can check the volume above its average)")
        masked = masked[:m.start()] + MARK + " " * (m.end() - m.start() - 1) + masked[m.end():]
    if d.min_rr is None and own_min_rr is not None:
        d.min_rr, d.min_rr_source = float(own_min_rr), "your risk limits"

    # Timeframes: the lowest one named is the strategy's; a higher one named inside a part makes that part read it.
    tokens = _tf_tokens(masked)
    named = sorted({tf for _, _, tf, _ in tokens if tf}, key=TIMEFRAMES.index)
    for _, _, tf, words in tokens:
        if tf is None:
            n = re.match(r"\d+", words.strip())
            d.unsupported.append(f"{n.group(0) + '-minute' if n else words.strip()} candles ({TIMEFRAMES_TEXT})")
    d.timeframe = named[0] if named else None

    # Parts with no building block (read on the text without the settings, so "stop 1.5 x ATR" is not a rule).
    for pattern, label, screen_fns, ready in UNSUPPORTED:
        for hit in re.finditer(pattern, masked):
            name = INDICATOR_NAMES.get(hit.group(1), hit.group(1)) if label == "this indicator" else label
            line = name + (". The Screener can look for it today" if screen_fns and _screener_has(screen_fns) else "")
            if line not in d.unsupported:
                d.unsupported.append(line)
            if ready and ready not in d.ready_made:
                d.ready_made.append(ready)
            masked = masked[:hit.start()] + MARK + " " * (hit.end() - hit.start() - 1) + masked[hit.end():]

    # The rules, part by part: a part that names a side starts it; a part without one belongs to the side before it.
    parts: List[str] = []
    for sentence in re.split(r"\.(?!\d)|[;\n]+", masked):
        parts += [p for p in re.split(r"(?=\b(?:go long|go short|enter long|enter short|buy|sell|long|short)\b)", sentence) if p.strip(" ,")]
    sides = [_side_of(p) for p in parts]
    current = next((s for s in sides if s), None)
    for part, side in zip(parts, sides):
        current = side or current
        if _EXIT.search(part):
            d.unsupported.append("an exit on a condition (an ATP strategy exits at its stop or its objective; set time or trailing exits on the "
                                 "bot form)")
            continue
        for clause in re.split(r",|\band\b|\bwhile\b|\bplus\b|\balso\b|\bthen\b", part):
            _read_clause(d, clause, current, tokens, named, opening)
    d.unsupported = list(dict.fromkeys(d.unsupported))
    return d


def _mask(text: str, spans: Sequence[Tuple[int, int]]) -> str:
    out = list(text)
    for a, b in spans:
        out[a:b] = " " * (b - a)
    return "".join(out)


def _mask_to_clause_end(text: str, start: int) -> str:
    end = re.search(r"[.,;\n]|\band\b", text[start:])
    stop = start + (end.start() if end else len(text) - start)
    return text[:start] + MARK + " " * (stop - start - 1) + text[stop:]


def _screener_has(names: Sequence[str]) -> bool:
    try:
        from app.screener.registry import FUNCTIONS
    except Exception:  # noqa: BLE001 - without the registry no pointer is shown
        return False
    return any(n in FUNCTIONS for n in names)


FIXED_LEVEL = ("a fixed price level (an ATP strategy compares building blocks - the price, an average, VWAP, a band, the previous "
               "day's high - not one price)")


def _read_clause(d: Draft, clause: str, side: Optional[str], tokens: Sequence[Tuple[int, int, Optional[str], str]],
                 named: Sequence[str], opening: Optional[int]) -> None:
    raw = clause.strip(" ,:-")
    if not raw:
        return
    tf = next((t for _, _, t, w in tokens if t and w.strip() in raw), None)
    body = raw
    for _, _, _, w in tokens:
        body = body.replace(w.strip(), " ")
    had_mark = MARK in body
    body = re.sub(r"\s+", " ", body.replace(MARK, " ")).strip()
    body = re.sub(r"^(?:(?:and|when|whenever|if|once|after|only|also|go|enter|take|a|an|the|with)\s+)*(?:long|short|buy|sell)?\s*"
                  r"(?:(?:when|whenever|if|once|after|only|with)\s+)*", "", body).strip()
    if d.symbol:
        body = re.sub(rf"^{re.escape(d.symbol.lower())}\b\s*(?:(?:when|if|once)\s+)?", "", body).strip()
    rules = parse_clause(body, side) if body else None
    if rules == FIXED_LEVEL_RULE:
        if FIXED_LEVEL not in d.unsupported:
            d.unsupported.append(FIXED_LEVEL)
        return
    if not rules:
        left = _FILLER.sub(" ", body)
        if d.symbol:
            left = re.sub(rf"\b{re.escape(d.symbol.lower())}\b", " ", left)
        if re.search(r"[a-z]{3,}", left) and not had_mark:
            d.unread.append(re.sub(r"\s+", " ", raw.replace(MARK, " ")).strip())
        return
    for r in rules:
        for op in (r.left, r.right):
            if op.get("indicator") in ("OR_HIGH", "OR_LOW") and op.get("period") is None and opening:
                op["period"] = opening
        if tf and named and tf != named[0] and tf in HIGHER_TIMEFRAMES:
            for op in (r.left, r.right):
                if op.get("type") == "indicator" and op.get("indicator") != "CLOSE":
                    op["timeframe"] = tf
        if r.read_as and r.read_as not in d.read_as:
            d.read_as.append(r.read_as)
        d.rules.append(r)


# --- the draft as the engine's strategy (CP6-2) --------------------------------------------------------------------------

def _operand(op: Dict[str, Any]) -> Dict[str, Any]:
    """A draft operand as the engine's `Operand`: fields its building block does not use keep the engine's neutral values
    (they are never read for it), exactly as the review card saves it (frontend `strategyDraft.ts` `operandFor`)."""
    out: Dict[str, Any] = {"type": op["type"], "value": 0.0, "indicator": op.get("indicator") or "CLOSE", "period": 14, "multiplier": 3.0,
                           "timeframe": op.get("timeframe")}
    if op["type"] == "value":
        out["value"] = op.get("value")
    if op.get("period") is not None:
        out["period"] = op["period"]
    if op.get("multiplier") is not None:
        out["multiplier"] = op["multiplier"]
    return out


def config_of(d: Draft) -> Any:
    """The draft as a `CustomStrategyConfig`, or None while any value is still his to decide - the engine's own defaults
    (a stop, an objective, a reward to risk) are never used in place of a value he did not give."""
    from app.strategy_engine.declarative import CustomStrategyConfig, own_min_rr
    if not d.rules or d.asks() or d.timeframe is None or None in (d.stop_loss_atr_mult, d.atr_period, d.target_rr, d.min_rr):
        return None
    longs = [{"left": _operand(r.left), "operator": r.operator, "right": _operand(r.right)} for r in d.rules if r.side == "long"]
    shorts = [{"left": _operand(r.left), "operator": r.operator, "right": _operand(r.right)} for r in d.rules if r.side == "short"]
    if not longs and not shorts:
        return None
    return CustomStrategyConfig.model_validate({
        "name": draft_name(d), "timeframe": d.timeframe, "long_conditions": longs, "short_conditions": shorts,
        "stop_loss_atr_mult": d.stop_loss_atr_mult, "atr_period": d.atr_period, "target_rr": list(d.target_rr or ()), "min_rr": own_min_rr(d.min_rr)})


# --- the answer's lines -------------------------------------------------------------------------------------------------

def side_title(side: Optional[str], timeframe: Optional[str]) -> str:
    where = f" on the {TF_WORDS[timeframe]} chart" if timeframe else ""
    if side == "long":
        return f"Long side: all of these hold{where}"
    if side == "short":
        return f"Short side: all of these hold{where}"
    return f"Side to decide: all of these hold{where}"


def settings_lines(d: Draft) -> List[str]:
    out = []
    if d.stop_loss_atr_mult is not None:
        atr = f" ({d.atr_period})" if d.atr_period else ""
        out.append(f"Where the idea is wrong: {_n(d.stop_loss_atr_mult)} x ATR{atr} from the entry.")
    elif d.atr_period is not None:
        out.append(f"ATR length for the stop distance: {d.atr_period}.")
    if d.target_rr is not None:
        a, b = d.target_rr
        out.append(f"Profit objective: {_n(a)}R." if a == b else f"Profit objectives: {_n(a)}R, then {_n(b)}R.")
    if d.min_rr is not None:
        src = " (from your own risk limits; change it if you like)" if d.min_rr_source == "your risk limits" else ""
        out.append(f"Minimum reward to risk: {_n(d.min_rr)}{src}.")
    if d.lots is not None:
        out.append(f"Size: {d.lots} {'lot' if d.lots == 1 else 'lots'}, on the bot form.")
    return out


__all__ = ["Draft", "Rule", "build", "strip_preamble", "parse_clause", "parse_operand", "operand_text", "rule_text", "side_title",
           "settings_lines", "draft_name", "config_of", "test_asked", "without_test_ask", "HOW_I_BUILD", "NO_BACKTEST", "NOT_TESTED",
           "TEST_LABEL", "TEST_SUFFIX", "EXAMPLE", "LEFT_OUT", "TIMEFRAMES", "TIMEFRAMES_TEXT"]
