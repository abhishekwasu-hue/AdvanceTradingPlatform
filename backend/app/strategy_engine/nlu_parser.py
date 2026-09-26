"""A deterministic, rule-based natural-language parser for the Strategy Builder - not a call to
an external LLM (no AI-provider credentials exist in this deployment, and calling one on a
trading-strategy description would be exactly the kind of "presents a guess as a fact" behavior
this platform refuses to do elsewhere). Instead this recognizes a fixed, documented set of
phrasings and turns them into the exact same `Condition`/`Operand`/`CustomStrategyConfig`
building blocks the no-code Strategy Builder already uses - it never invents a condition it
isn't confident about; any sentence it can't parse comes back as a warning naming the exact text,
so the user always sees precisely what was understood (and what wasn't) before saving anything.

Supported phrasings (case-insensitive):
- Entry direction: a sentence containing "buy"/"go long"/"long when"/"enter long" (and not a sell
  keyword) is a long-entry sentence; "sell"/"go short"/"short when"/"enter short" is a
  short-entry sentence. Multiple conditions in one sentence are joined with "and".
- Indicators: EMA, SMA, RSI, ADX, ATR, SUPERTREND, PLUS_DI, MINUS_DI, CLOSE/OPEN/HIGH/LOW/"price".
  A period can come as "RSI(14)", "RSI 14", or "14 RSI"/"14-period RSI"; omitted, it defaults to
  the same period-14 default `Operand` itself uses.
- Comparisons: "crosses above"/"crosses over", "crosses below"/"crosses under", ">"/"above"/
  "greater than"/"over", "<"/"below"/"less than"/"under", ">="/"greater than or equal to",
  "<="/"less than or equal to".
- Risk parameters (their own sentences, anywhere in the text): "<N>x ATR stop loss" or "stop loss
  of <N>x ATR", "ATR period <N>", "target risk reward of <N>" (optionally "... to <M>"),
  "minimum risk reward of <N>", "<N>min timeframe" / "<N> minute timeframe".

Anything else is reported back verbatim as a warning, never silently dropped or guessed at.
"""
import re
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

from app.strategy_engine.declarative import Condition, Operand, Operator

_INDICATOR_ALTERNATION = r"EMA|SMA|RSI|ADX|ATR|SUPERTREND|PLUS_DI|MINUS_DI"

_INDICATOR_WITH_PAREN = re.compile(rf"\b({_INDICATOR_ALTERNATION})\s*\(\s*(\d+)\s*\)", re.I)
_PERIOD_BEFORE_INDICATOR = re.compile(rf"\b(\d+)\s*(?:-\s*period)?\s*({_INDICATOR_ALTERNATION})\b", re.I)
_INDICATOR_PERIOD_AFTER = re.compile(rf"\b({_INDICATOR_ALTERNATION})\s+(\d+)\b", re.I)
_BARE_INDICATOR = re.compile(rf"\b({_INDICATOR_ALTERNATION}|CLOSE|OPEN|HIGH|LOW)\b", re.I)
_PRICE_WORD = re.compile(r"\bprice\b", re.I)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

_OPERATOR_PATTERNS: List[Tuple["re.Pattern[str]", Operator]] = [
    (re.compile(r"crosses\s+above|crosses\s+over", re.I), "CROSSES_ABOVE"),
    (re.compile(r"crosses\s+below|crosses\s+under", re.I), "CROSSES_BELOW"),
    (re.compile(r">=|greater than or equal to", re.I), "GTE"),
    (re.compile(r"<=|less than or equal to", re.I), "LTE"),
    (re.compile(r">|is above|above|greater than|over", re.I), "GT"),
    (re.compile(r"<|is below|below|less than|under", re.I), "LT"),
]

_BUY_KEYWORDS = re.compile(r"\b(buy|go\s+long|long\s+when|enter\s+long|long\s+entry)\b", re.I)
_SELL_KEYWORDS = re.compile(r"\b(sell|go\s+short|short\s+when|enter\s+short|short\s+entry)\b", re.I)

_STOP_LOSS_ATR = re.compile(
    r"([\d.]+)\s*x?\s*(?:times\s*)?atr\s*(?:stop\s*loss|stop)|stop\s*loss\s*(?:of\s*)?([\d.]+)\s*x?\s*(?:times\s*)?atr",
    re.I,
)
_ATR_PERIOD = re.compile(r"atr\s*period\s*(?:of\s*)?(\d+)|(\d+)\s*[- ]period\s*atr", re.I)
_TARGET_RR = re.compile(
    r"target\s*(?:risk[\s-]*reward|rr|r:r)\s*(?:of\s*)?([\d.]+)(?:\s*(?:to|:)\s*([\d.]+))?", re.I,
)
_MIN_RR = re.compile(r"min(?:imum)?\s*(?:risk[\s-]*reward|rr|r:r)\s*(?:of\s*)?([\d.]+)", re.I)
_TIMEFRAME = re.compile(r"\b(\d+)\s*[- ]?min(?:ute)?s?\b", re.I)


class ParsedStrategyPreview(BaseModel):
    """Same shape as `CustomStrategyConfig`, minus its "at least one condition" validation - a
    parse can legitimately come back empty (nothing recognized) and that's still a valid preview
    to show the user, not an error. Convert to a real `CustomStrategyConfig` only at save time.
    """

    name: str = "Parsed Strategy"
    timeframe: str = "5min"
    long_conditions: List[Condition] = Field(default_factory=list)
    short_conditions: List[Condition] = Field(default_factory=list)
    stop_loss_atr_mult: float = 1.0
    atr_period: int = 14
    target_rr: Tuple[float, float] = (1.5, 2.0)
    min_rr: float = 1.2


class ParseResult(BaseModel):
    config: ParsedStrategyPreview
    interpreted: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


def _parse_operand(text: str) -> Optional[Operand]:
    text = text.strip()
    if not text:
        return None
    m = _INDICATOR_WITH_PAREN.search(text)
    if m:
        return Operand(type="indicator", indicator=m.group(1).upper(), period=int(m.group(2)))
    m = _PERIOD_BEFORE_INDICATOR.search(text)
    if m:
        return Operand(type="indicator", indicator=m.group(2).upper(), period=int(m.group(1)))
    m = _INDICATOR_PERIOD_AFTER.search(text)
    if m:
        return Operand(type="indicator", indicator=m.group(1).upper(), period=int(m.group(2)))
    if _PRICE_WORD.search(text):
        return Operand(type="indicator", indicator="CLOSE")
    m = _BARE_INDICATOR.search(text)
    if m:
        return Operand(type="indicator", indicator=m.group(1).upper())
    m = _NUMBER.search(text)
    if m:
        return Operand(type="value", value=float(m.group(0)))
    return None


def _parse_condition(clause: str) -> Tuple[Optional[Condition], Optional[str]]:
    clause = clause.strip()
    if not clause:
        return None, None
    for pattern, operator in _OPERATOR_PATTERNS:
        m = pattern.search(clause)
        if not m:
            continue
        left = _parse_operand(clause[: m.start()])
        right = _parse_operand(clause[m.end() :])
        if left is not None and right is not None:
            return Condition(left=left, operator=operator, right=right), None
        break  # an operator matched but an operand didn't - don't fall through to a weaker one
    return None, f'Could not understand condition: "{clause}"'


def _classify_sentence(sentence: str) -> str:
    has_buy = bool(_BUY_KEYWORDS.search(sentence))
    has_sell = bool(_SELL_KEYWORDS.search(sentence))
    if has_buy and not has_sell:
        return "LONG"
    if has_sell and not has_buy:
        return "SHORT"
    return "GLOBAL"


def _strip_direction_prefix(sentence: str, classification: str) -> str:
    pattern = _BUY_KEYWORDS if classification == "LONG" else _SELL_KEYWORDS
    m = pattern.search(sentence)
    rest = sentence[m.end() :] if m else sentence
    return re.sub(r"^\s*(when|if|:)\s*", "", rest, flags=re.I)


def parse_strategy_description(text: str, name: str = "Parsed Strategy") -> ParseResult:
    config = ParsedStrategyPreview(name=name)
    interpreted: List[str] = []
    warnings: List[str] = []

    # A period not immediately followed by a digit ends a sentence; a decimal point (e.g. "1.5",
    # where the digit comes right after) never does - a sentence-ending period is always followed
    # by whitespace/end-of-string before any next digit even appears.
    sentences = [s.strip() for s in re.split(r"\.(?!\d)|[\n;]+", text) if s.strip()]

    for sentence in sentences:
        classification = _classify_sentence(sentence)

        if classification in ("LONG", "SHORT"):
            rest = _strip_direction_prefix(sentence, classification)
            for clause in re.split(r"\band\b", rest, flags=re.I):
                condition, warning = _parse_condition(clause)
                if condition is not None:
                    bucket = config.long_conditions if classification == "LONG" else config.short_conditions
                    bucket.append(condition)
                    interpreted.append(f"{'Long' if classification == 'LONG' else 'Short'} entry: {condition.label()}")
                elif warning is not None:
                    warnings.append(warning)
            continue

        matched_anything = False

        m = _STOP_LOSS_ATR.search(sentence)
        if m:
            value = float(m.group(1) or m.group(2))
            config.stop_loss_atr_mult = value
            interpreted.append(f"Stop loss: {value:g}x ATR")
            matched_anything = True

        m = _ATR_PERIOD.search(sentence)
        if m:
            value = int(m.group(1) or m.group(2))
            config.atr_period = value
            interpreted.append(f"ATR period: {value}")
            matched_anything = True

        m = _TARGET_RR.search(sentence)
        if m:
            t1 = float(m.group(1))
            t2 = float(m.group(2)) if m.group(2) else round(t1 + 0.5, 2)
            config.target_rr = (t1, t2)
            interpreted.append(f"Target R:R: {t1:g} / {t2:g}")
            matched_anything = True

        m = _MIN_RR.search(sentence)
        if m:
            value = float(m.group(1))
            config.min_rr = value
            interpreted.append(f"Minimum R:R to trade: {value:g}")
            matched_anything = True

        m = _TIMEFRAME.search(sentence)
        if m:
            config.timeframe = f"{m.group(1)}min"
            interpreted.append(f"Timeframe: {config.timeframe}")
            matched_anything = True

        if not matched_anything:
            warnings.append(f'Could not understand: "{sentence}"')

    return ParseResult(config=config, interpreted=interpreted, warnings=warnings)
