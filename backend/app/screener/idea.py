"""Handover 11j "idea -> scan": a plain idea -> a ScreenQL draft from REGISTRY functions only, part by part.

Extends the plain-language screen box (`app/screener/nl.py`, flag `screener_copilot`) with a deterministic reader that
needs no AI key: each phrase it knows maps to ONE fixed condition built from registry functions (`Trend`, `NearZone`,
`RsiDivergence`, `RSI`, `SMA`, `IsFnO`, ...). Nothing is invented:

* a phrase it does not know comes back as "not supported yet", with the user's own words, and is left out of the screen;
* trade wording ("buy", "sell", "target", "stop") is never turned into a condition: a scan finds matches, the trade
  decision stays the user's;
* a default the phrase did not state (a 20-bar volume average, "near" = within 5 RSI points) is listed as an assumption
  the user can edit in the blocks;
* every part is validated and flag-gated by the route exactly like a typed screen, so a function this organisation has
  not switched on, or a timeframe finer than the scan's, comes back with the validator's fixed reason.

English only (owner directive, 13 Oct, as the box): an idea with characters outside ASCII is "not supported yet" as a
whole. Nothing runs and nothing is stored: the draft opens as editable blocks in the Screener.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

DISCLAIMER = "A draft scan from your idea: check each block, edit it, then run it. Matches are filters passed, not advice."
NOT_SUPPORTED = "not supported yet"
TRADE_WORDS_WHY = "a scan finds matches; the trade decision (and its levels) stays yours"
MAX_IDEA_CHARS = 1000

# Timeframe words -> ScreenQL timeframes.
_TF_WORDS: Tuple[Tuple[str, str], ...] = (
    (r"monthly|1 ?mo(?:nth)?\b", "1M"), (r"weekly|1 ?w(?:eek|k)?\b", "1w"), (r"daily|1 ?d(?:ay)?\b|day chart", "1d"),
    (r"hourly|1 ?h(?:our|r)?\b|60 ?-?min(?:ute)?s?\b", "1h"), (r"30 ?-?min(?:ute)?s?\b|30 ?m\b", "30m"),
    (r"15 ?-?min(?:ute)?s?\b|15 ?m\b", "15m"), (r"5 ?-?min(?:ute)?s?\b|5 ?m\b", "5m"),
)
_TF_RE = [(re.compile(rf"(?<![\w-])(?:{p})", re.IGNORECASE), tf) for p, tf in _TF_WORDS]
_ORDER = ("1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w", "1M")

# Words that carry no condition on their own (left over after the known phrases are taken out).
_FILLER = frozenset("""a an the in into on onto with at of to and or but is are be being that which where who when while then
stocks stock shares share symbols symbol names name scrips scrip find show me list scan screen screens for all any from by it its
has have having still also only just near around close closer chart charts timeframe tf time frame look looking want i we my
some there their this these those like as so very same plus also one other more less than up down market markets setup setups
idea ideas""".split())
# Trade wording: an instruction at the start of a leftover ("buy when ..."), or a level word anywhere ("target 10%").
_TRADE = re.compile(r"^(?:buy|sell|short|go long|go short)\b|\b(?:target|targets|stop|stops|stop-loss|stoploss|sl|entry|enter|exit)\b",
                    re.IGNORECASE)


@dataclass
class Part:
    """One recognised phrase: the user's words, the condition (ScreenQL, registry functions only), what it reads in
    plain words, and the defaults it assumed."""
    rule: str
    phrase: str
    condition: str
    reads: str
    assumptions: List[str] = field(default_factory=list)
    span: Tuple[int, int] = (0, 0)
    tf: str = ""                       # the chart the phrase named ("" = the scan's own timeframe)

    def as_dict(self) -> Dict[str, object]:
        return {"rule": self.rule, "phrase": self.phrase, "condition": self.condition, "reads": self.reads, "assumptions": list(self.assumptions)}


@dataclass
class Draft:
    parts: List[Part]
    unsupported: List[Dict[str, str]]


Builder = Callable[[re.Match, str], Optional[Tuple[str, str, List[str]]]]


def _at(tf: str) -> str:
    return f"@{tf}" if tf else ""


def _tf_name(tf: str) -> str:
    return {"1M": "Monthly", "1w": "Weekly", "1d": "Daily", "1h": "1-hour", "30m": "30-minute", "15m": "15-minute", "5m": "5-minute"}.get(tf, "")


def _on(tf: str) -> str:
    return f" on the {_tf_name(tf)} chart" if tf else ""


def _side(m: re.Match, default: Optional[str] = None) -> Optional[str]:
    word = (m.groupdict().get("side") or "").lower()
    return "bullish" if word.startswith("bull") else "bearish" if word.startswith("bear") else default


def _trend(kind: str) -> Builder:
    names = {"UP": ("UPTREND", "up"), "DOWN": ("DOWNTREND", "down"), "RANGE": ("RANGE", "sideways (a range)")}

    def build(m: re.Match, tf: str) -> Tuple[str, str, List[str]]:
        cat, words = names[kind]
        if tf:   # Trend() reads the scan's own timeframe only; the Dow trend from confirmed swings reads another one
            return f'DowTrend(){_at(tf)} == "{kind}"', f"the trend from confirmed swings is {words}{_on(tf)}", []
        return f'Trend() == "{cat}"', f"the market-structure trend is {words}", []
    return build


def _zone(role: str) -> Builder:
    side = "buyer" if role == "demand" else "seller"

    def build(m: re.Match, tf: str) -> Tuple[str, str, List[str]]:
        return (f'NearZone("{role}"){_at(tf)}', f"the close is at a {side} area (a {role} zone){_on(tf)}",
                ["at the area = within 0.5% of the nearest live zone (the usual setting)"])
    return build


def _rsi(m: re.Match, tf: str) -> Optional[Tuple[str, str, List[str]]]:
    word = (m.group("op") or "").lower()
    value = float(m.group("value"))
    if not 0 <= value <= 100:
        return None
    v = f"{value:g}"
    at = _at(tf)
    if word in ("above", "over", "greater than", ">", "more than"):
        return f"RSI(){at} > {v}", f"RSI is above {v}{_on(tf)}", ["RSI over 14 bars (the usual setting)"]
    if word in ("below", "under", "less than", "<"):
        return f"RSI(){at} < {v}", f"RSI is below {v}{_on(tf)}", ["RSI over 14 bars (the usual setting)"]
    lo, hi = max(0.0, value - 5), min(100.0, value + 5)
    return (f"RSI(){at} >= {lo:g} AND RSI(){at} <= {hi:g}", f"RSI is between {lo:g} and {hi:g}{_on(tf)}",
            [f"near {v} = within 5 points of it", "RSI over 14 bars (the usual setting)"])


def _rsi_zone(kind: str) -> Builder:
    def build(m: re.Match, tf: str) -> Tuple[str, str, List[str]]:
        if kind == "oversold":
            return f"RSI(){_at(tf)} < 30", f"RSI is below 30{_on(tf)}", ["oversold = RSI below 30", "RSI over 14 bars (the usual setting)"]
        return f"RSI(){_at(tf)} > 70", f"RSI is above 70{_on(tf)}", ["overbought = RSI above 70", "RSI over 14 bars (the usual setting)"]
    return build


def _divergence(m: re.Match, tf: str) -> Tuple[str, str, List[str]]:
    side = _side(m)
    at = _at(tf)
    if side:
        return f'RsiDivergence("{side}"){at}', f"a {side} RSI divergence between the last two confirmed swings{_on(tf)}", []
    return (f'(RsiDivergence("bullish"){at} OR RsiDivergence("bearish"){at})', f"an RSI divergence (either side){_on(tf)}",
            ["no side named, so either side"])


def _volume(kind: str) -> Builder:
    def build(m: re.Match, tf: str) -> Tuple[str, str, List[str]]:
        at = _at(tf)
        if kind == "low":
            return (f"volume{at} < SMA(volume, 20){at}", f"volume is below its 20-bar average{_on(tf)}",
                    ["falling volume = this bar's volume below its 20-bar average"])
        return (f"volume{at} > SMA(volume, 20){at} * 1.5", f"volume is more than 1.5 times its 20-bar average{_on(tf)}",
                ["rising volume = more than 1.5 times the 20-bar average"])
    return build


def _fno(m: re.Match, tf: str) -> Tuple[str, str, List[str]]:
    return "IsFnO()", "the symbol is in the F&O segment", []


def _average(m: re.Match, tf: str) -> Optional[Tuple[str, str, List[str]]]:
    n = int(m.group("n"))
    if not 1 <= n <= 500:
        return None
    kind = (m.group("kind") or "").lower()
    fn = "EMA" if kind == "ema" else "SMA"
    cmp = ">" if m.group("dir").lower() in ("above", "over") else "<"
    at = _at(tf)
    words = "above" if cmp == ">" else "below"
    assumed = [] if kind in ("ema", "sma") else ["a moving average = the simple one (SMA)"]
    return f"close{at} {cmp} {fn}(close, {n}){at}", f"the close is {words} its {n}-bar {fn}{_on(tf)}", assumed


def _structure(event: str) -> Builder:
    words = {"CHOCH": "a change of character", "BOS": "a break of structure"}[event]

    def build(m: re.Match, tf: str) -> Tuple[str, str, List[str]]:
        side = _side(m)
        if side:
            return f'StructureEvent() == "{event}_{side.upper()}"', f"the latest structure event is a {side} {words[2:]}", []
        return (f'StructureEvent() IN ("{event}_BULLISH", "{event}_BEARISH")', f"the latest structure event is {words}",
                ["no side named, so either side"])
    return build


_PATTERNS = {"hammer": "hammer", "shooting star": "shooting_star", "doji": "doji", "bullish engulfing": "bullish_engulfing",
             "bearish engulfing": "bearish_engulfing", "morning star": "morning_star", "evening star": "evening_star",
             "bullish pin bar": "bullish_pin_bar", "bearish pin bar": "bearish_pin_bar", "inside bar": "inside_bar"}


def _pattern(m: re.Match, tf: str) -> Tuple[str, str, List[str]]:
    words = re.sub(r"\s+", " ", m.group(0).lower().replace("-", " ")).strip()
    name = _PATTERNS[words]
    return f'Pattern("{name}"){_at(tf)}', f"a {words} candle on the last closed bar{_on(tf)}", []


def _candle(m: re.Match, tf: str) -> Tuple[str, str, List[str]]:
    side = _side(m, "bullish")
    fn = "PatternBullish" if side == "bullish" else "PatternBearish"
    return f"{fn}(){_at(tf)}", f"a {side} candlestick pattern on the last closed bar{_on(tf)}", []


def _breakout(direction: str) -> Builder:
    def build(m: re.Match, tf: str) -> Tuple[str, str, List[str]]:
        words = "above the last confirmed swing high" if direction == "up" else "below the last confirmed swing low"
        return f'SwingBreakout("{direction}"){_at(tf)}', f"a close {words}{_on(tf)}", ["the swing breakout's own defaults"]
    return build


def _retest(m: re.Match, tf: str) -> Tuple[str, str, List[str]]:
    return f'RetestEntry("up"){_at(tf)}', f"after a breakout, the move came back to the level{_on(tf)}", ["the upside retest (say \"breakdown retest\" for the other side)"]


def _retest_down(m: re.Match, tf: str) -> Tuple[str, str, List[str]]:
    return f'RetestEntry("down"){_at(tf)}', f"after a breakdown, the move came back to the level{_on(tf)}", []


def _new_extreme(direction: str) -> Builder:
    def build(m: re.Match, tf: str) -> Tuple[str, str, List[str]]:
        words = "a new 52-week high" if direction == "up" else "a new 52-week low"
        return f'NewHighBreakout("{direction}")', f"{words} (Daily / Weekly)", []
    return build


def _swing_label(kind: str, label: str) -> Builder:
    words = {"HL": "a higher low", "LH": "a lower high", "HH": "a higher high", "LL": "a lower low"}[label]

    def build(m: re.Match, tf: str) -> Tuple[str, str, List[str]]:
        return f'SwingLabel("{kind}"){_at(tf)} == "{label}"', f"the last confirmed swing is {words}{_on(tf)}", []
    return build


def _adx(m: re.Match, tf: str) -> Tuple[str, str, List[str]]:
    value = m.groupdict().get("value")
    v = f"{float(value):g}" if value else "25"
    assumed = [] if value else ["a strong trend = ADX above 25"]
    return f"ADX(){_at(tf)} > {v}", f"ADX is above {v}{_on(tf)}", assumed


def _vwap(m: re.Match, tf: str) -> Tuple[str, str, List[str]]:
    above = m.group("dir").lower() in ("above", "over")
    return f"close {'>' if above else '<'} VWAP()", f"the close is {'above' if above else 'below'} the session VWAP", []


def _supertrend(m: re.Match, tf: str) -> Tuple[str, str, List[str]]:
    up = m.group("dir").lower() in ("bullish", "green", "up", "positive")
    return f"SupertrendDir(){_at(tf)} {'>' if up else '<'} 0", f"the Supertrend direction is {'up' if up else 'down'}{_on(tf)}", ["Supertrend 10, 3 (the defaults)"]


_SIDE = r"(?:(?P<side>bullish|bearish|bull|bear)\s+)?"
RULES: Sequence[Tuple[str, "re.Pattern[str]", Builder]] = [
    ("trend_up", re.compile(r"\b(?:up[- ]?trend|trending up|rising trend|higher highs and higher lows)\b", re.I), _trend("UP")),
    ("trend_down", re.compile(r"\b(?:down[- ]?trend|trending down|falling trend|lower highs and lower lows)\b", re.I), _trend("DOWN")),
    ("range", re.compile(r"\b(?:sideways|range[- ]?bound|ranging|in a range)\b", re.I), _trend("RANGE")),
    ("demand_zone", re.compile(r"\b(?:demand|buyer|support)\s+(?:zone|area)s?\b", re.I), _zone("demand")),
    ("supply_zone", re.compile(r"\b(?:supply|seller|resistance)\s+(?:zone|area)s?\b", re.I), _zone("supply")),
    ("rsi_divergence", re.compile(rf"\b{_SIDE}(?:rsi\s+)?divergences?\b", re.I), _divergence),
    ("rsi_level", re.compile(r"\brsi(?:\s*\(?\s*14\s*\)?)?\s+(?:is\s+)?(?P<op>above|over|greater than|more than|>|below|under|less than|<|near|around|at|about)"
                             r"\s+(?P<value>\d{1,3}(?:\.\d+)?)\b", re.I), _rsi),
    ("rsi_oversold", re.compile(r"\b(?:rsi\s+)?oversold\b", re.I), _rsi_zone("oversold")),
    ("rsi_overbought", re.compile(r"\b(?:rsi\s+)?overbought\b", re.I), _rsi_zone("overbought")),
    ("volume_low", re.compile(r"\b(?:(?:falling|declining|low|light|lower|decreasing)\s+volume|volume\s+(?:is\s+)?(?:falling|declining|drying up|dries up|low|contracting)"
                              r"|(?:drying|dry)[- ]up\s+volume)\b", re.I), _volume("low")),
    ("volume_high", re.compile(r"\b(?:(?:rising|high|heavy|strong|increasing|above[- ]average)\s+volume|volume\s+(?:is\s+)?(?:rising|spiking|expanding|surging|spike|surge))\b", re.I),
     _volume("high")),
    ("fno", re.compile(r"(?<![\w&])(?:f\s?&\s?o|fno|f and o|futures and options)(?![\w&])", re.I), _fno),
    ("moving_average", re.compile(r"\b(?P<dir>above|over|below|under)\s+(?:the\s+|its\s+)?(?P<n>\d{1,3})[- ]?(?:day|bar|period)?[- ]?"
                                  r"(?P<kind>ema|sma|dma|moving average|ma)\b", re.I), _average),
    ("choch", re.compile(rf"\b{_SIDE}(?:change of character|choch)\b", re.I), _structure("CHOCH")),
    ("bos", re.compile(rf"\b{_SIDE}(?:break of structure|bos)\b", re.I), _structure("BOS")),
    ("pattern", re.compile(r"\b(?:hammer|shooting[- ]star|doji|bullish engulfing|bearish engulfing|morning[- ]star|evening[- ]star|"
                           r"bullish pin[- ]bar|bearish pin[- ]bar|inside[- ]bar)\b", re.I), _pattern),
    ("candle", re.compile(r"\b(?P<side>bullish|bearish)\s+(?:candle|candlestick)(?:\s+pattern)?s?\b", re.I), _candle),
    ("new_high", re.compile(r"\b(?:52[- ]week highs?|new (?:52[- ]week )?highs?)\b", re.I), _new_extreme("up")),
    ("new_low", re.compile(r"\b(?:52[- ]week lows?|new (?:52[- ]week )?lows?)\b", re.I), _new_extreme("down")),
    ("retest_down", re.compile(r"\bbreak[- ]?down\s+(?:and\s+)?retest\b", re.I), _retest_down),
    ("retest", re.compile(r"\b(?:breakout\s+(?:and\s+)?)?retest(?:ing|ed|s)?(?:\s+(?:after|of)\s+(?:a\s+|the\s+)?break[- ]?out)?\b", re.I), _retest),
    ("breakout", re.compile(r"\bbreak(?:ing)?[- ]?outs?\b", re.I), _breakout("up")),
    ("breakdown", re.compile(r"\bbreak(?:ing)?[- ]?downs?\b", re.I), _breakout("down")),
    ("higher_low", re.compile(r"\bhigher lows?\b", re.I), _swing_label("low", "HL")),
    ("lower_high", re.compile(r"\blower highs?\b", re.I), _swing_label("high", "LH")),
    ("adx", re.compile(r"\b(?:strong trend|adx\s+(?:is\s+)?(?:above|over)\s+(?P<value>\d{1,2}(?:\.\d+)?))\b", re.I), _adx),
    ("vwap", re.compile(r"\b(?P<dir>above|over|below|under)\s+(?:the\s+)?(?:session\s+)?vwap\b", re.I), _vwap),
    ("supertrend", re.compile(r"\bsupertrend\s+(?:is\s+)?(?P<dir>bullish|green|up|positive|bearish|red|down|negative)\b", re.I), _supertrend),
]


def _timeframe_near(text: str, start: int, end: int) -> Optional[str]:
    """A timeframe word in the same clause as the phrase, nearest to it (e.g. "Daily uptrend", "a 1H demand zone",
    "uptrend on the weekly chart")."""
    left = max(text.rfind(c, 0, start) for c in ",;.")
    rights = [i for i in (text.find(c, end) for c in ",;.") if i >= 0]
    clause_start, clause_end = left + 1, (min(rights) if rights else len(text))
    best: Optional[Tuple[int, str]] = None
    for pattern, tf in _TF_RE:
        for m in pattern.finditer(text, clause_start, clause_end):
            if start <= m.start() < end:
                continue
            gap = start - m.end() if m.end() <= start else m.start() - end
            if best is None or gap < best[0]:
                best = (gap, tf)
    return best[1] if best else None


def _leftovers(text: str, taken: List[Tuple[int, int]]) -> List[Dict[str, str]]:
    """The user's words no rule took, clause by clause, when they carry meaning (fillers and timeframe words alone are
    not a condition)."""
    chars = list(text)
    for a, b in taken:
        for i in range(a, b):
            chars[i] = " "
    rest = "".join(chars)
    out: List[Dict[str, str]] = []
    for clause in re.split(r"[,;.\n]|\band\b|\bbut\b|\bwith\b|\bwhile\b|\bthen\b", rest, flags=re.IGNORECASE):
        stripped = clause
        for pattern, _ in _TF_RE:
            stripped = pattern.sub(" ", stripped)
        words = [w for w in re.findall(r"[A-Za-z0-9%&$#@'-]+", stripped) if w.lower().strip("'-") not in _FILLER]
        if not words:
            continue
        tokens = re.sub(r"\s+", " ", stripped).strip(" -'").split(" ")
        while tokens and tokens[0].lower() in _FILLER:
            tokens.pop(0)
        while tokens and tokens[-1].lower() in _FILLER:
            tokens.pop()
        phrase = " ".join(tokens)
        why = TRADE_WORDS_WHY if _TRADE.search(phrase) else NOT_SUPPORTED
        if not any(o["phrase"] == phrase for o in out):
            out.append({"phrase": phrase[:120], "why": why})
    return out


def read_idea(text: str, base_tf: str = "1d") -> Draft:
    """The parts of an idea this reader knows, and the rest as "not supported yet". Pure: no registry check here (the
    route validates and gates every part)."""
    text = (text or "")[:MAX_IDEA_CHARS]
    if any(ord(ch) > 127 for ch in text):
        return Draft([], [{"phrase": "the idea", "why": "only English is supported yet"}])
    parts: List[Part] = []
    taken: List[Tuple[int, int]] = []
    for rule, pattern, build in RULES:
        for m in pattern.finditer(text):
            span = m.span()
            if any(a < span[1] and span[0] < b for a, b in taken):
                continue
            tf = _timeframe_near(text, *span)
            if tf == base_tf or tf not in _ORDER:
                tf = None
            built = build(m, tf or "")
            if built is None:
                continue
            condition, reads, assumptions = built
            taken.append(span)
            if any(p.condition == condition for p in parts):
                continue
            parts.append(Part(rule, re.sub(r"\s+", " ", m.group(0)).strip(), condition, reads, assumptions, span, tf or ""))
    parts.sort(key=lambda p: p.span[0])
    return Draft(parts, _leftovers(text, taken))


def joined(conditions: Sequence[str]) -> str:
    """The screen: the conditions joined with AND (an OR part keeps its parentheses)."""
    return " AND ".join(c if " OR " not in c or c.startswith("(") else f"({c})" for c in conditions)


__all__ = ["DISCLAIMER", "NOT_SUPPORTED", "TRADE_WORDS_WHY", "MAX_IDEA_CHARS", "RULES", "Part", "Draft", "read_idea", "joined"]
