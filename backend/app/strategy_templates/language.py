"""Strategy templates: the signal text (owner order section 6, policy 11l), built from fixed tokens.

A template never writes free text into a signal: it lists token ids, and each token is a phrase in structure /
indicator words. The only things filled in are the timeframe names ("15-minute"), from a fixed table. So the text
can carry no price, no indicator value and no buy / sell / target / stop wording - `guard` checks every phrase, and
the tests run it over every token and every library template's rendered text.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence

TOKENS: Dict[str, str] = {
    # D1 Supertrend flip
    "supertrend_turned_bullish": "Supertrend turned bullish on the {timeframe} chart",
    "supertrend_turned_bearish": "Supertrend turned bearish on the {timeframe} chart",
    "context_supertrend_agrees": "the {context_timeframe} Supertrend direction agrees",
}

TIMEFRAME_WORDS: Dict[str, str] = {"1m": "1-minute", "3m": "3-minute", "5m": "5-minute", "15m": "15-minute", "30m": "30-minute",
                                   "1h": "1-hour", "1d": "Daily", "1w": "Weekly", "1M": "Monthly"}
PLACEHOLDERS = ("timeframe", "context_timeframe")

# Words a signal may never carry (policy 11l): trade instructions, levels and outcome words. Matched as whole words,
# case-insensitively, also inside hyphenated words ("stop-loss").
FORBIDDEN_WORDS = ("buy", "buying", "bought", "sell", "selling", "sold", "target", "targets", "stop", "stops", "stoploss", "sl",
                   "loss", "profit", "profits", "entry", "enter", "exit", "long", "short", "order", "trade", "trades", "price",
                   "guaranteed", "sure", "recommend", "recommended", "should", "must")
_WORD = re.compile(r"[A-Za-z]+")
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_TF_DIGITS = re.compile("|".join(re.escape(w) for w in sorted(TIMEFRAME_WORDS.values(), key=len, reverse=True)))


def guard(text: str) -> List[str]:
    """The problems of a phrase or a rendered text (empty = clean): a forbidden word, a digit outside a timeframe name
    (a price or a value), an unknown placeholder, or a character outside plain English text."""
    problems: List[str] = []
    for word in _WORD.findall(text):
        if word.lower() in FORBIDDEN_WORDS:
            problems.append(f"forbidden word {word!r}")
    for name in _PLACEHOLDER.findall(text):
        if name not in PLACEHOLDERS:
            problems.append(f"unknown placeholder {{{name}}}")
    rest = _TF_DIGITS.sub("", _PLACEHOLDER.sub("", text))
    if re.search(r"[0-9]", rest):
        problems.append("a number (signal text carries no prices or values)")
    if re.search(r"[^A-Za-z0-9 ,;.\-'{}_()]", text):
        problems.append("a character outside plain English text")
    return problems


def render(tokens: Sequence[str], *, timeframe: str, context_timeframe: Optional[str] = None) -> str:
    """The sentence for a signal: the phrases joined with "; ", capitalised, ending with a full stop."""
    words = {"timeframe": TIMEFRAME_WORDS[timeframe],
             "context_timeframe": TIMEFRAME_WORDS[context_timeframe] if context_timeframe else "higher-timeframe"}
    phrases = [TOKENS[t].format(**words) for t in tokens]
    text = "; ".join(phrases)
    return (text[:1].upper() + text[1:] + ".") if text else ""


def unknown_tokens(tokens: Iterable[str]) -> List[str]:
    return [t for t in tokens if t not in TOKENS]


__all__ = ["TOKENS", "TIMEFRAME_WORDS", "FORBIDDEN_WORDS", "guard", "render", "unknown_tokens"]
