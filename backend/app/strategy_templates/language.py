"""Strategy templates: the signal text (owner order section 6, policy 11l), built from fixed tokens.

A template never writes free text into a signal: it lists token ids, and each token is a phrase in structure /
indicator words. The only things filled in are the timeframe names ("15-minute"), from a fixed table. So the text
can carry no price, no indicator value and no buy / sell / target / stop wording - `guard` checks every phrase, and
the tests run it over every token and every library template's rendered text.

Family G (review finding WB7, owner question WAVE-4): the guard refuses digits, so wave labels are written in words
("wave two", "wave B", "the fifth wave": `WAVE_WORDS`, `TYPE_WORDS`, `CLARITY_WORDS`), and two more placeholders name the
wave-count timeframe and the higher timeframe (`{wave_timeframe}` from the setting `wave_tf`, `{higher_timeframe}` from
`higher_tf`). The invalidation is said in words ("beyond the start of wave A"), never as a price.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence

TOKENS: Dict[str, str] = {
    # D1 Supertrend flip
    "supertrend_turned_bullish": "Supertrend turned bullish on the {timeframe} chart",
    "supertrend_turned_bearish": "Supertrend turned bearish on the {timeframe} chart",
    "context_supertrend_agrees": "the {context_timeframe} Supertrend direction agrees",
    # Family G (wave templates): structure words only - wave labels in words, no number, no price
    "wave_two_pullback_ended": "the wave two pullback ended at an area on the {wave_timeframe} chart",
    "wave_four_pullback_ended": "the wave four pullback ended at an area on the {wave_timeframe} chart",
    "wave_b_pullback_ended": "the wave B pullback ended at an area on the {wave_timeframe} chart",
    "wave_x_pullback_ended": "the wave X pullback of a double three ended at an area on the {wave_timeframe} chart",
    "correction_may_be_complete": "the correction on the {wave_timeframe} chart may be complete",
    "fifth_wave_may_be_complete": "the fifth wave on the {wave_timeframe} chart may be complete",
    "minor_trend_resumed": "the minor trend resumed",
    "counter_trend_c_leg": "a counter-trend C leg",
    "structure_turned_up": "the {timeframe} structure turned up",
    "structure_turned_down": "the {timeframe} structure turned down",
    "higher_trend_agrees": "the {higher_timeframe} trend agrees",
    "wave_count_clear": "the wave count is clear",
    "alternate_count_agrees": "the alternate count points the same way",
    "alternate_count_disagrees": "the alternate count points the other way",
    "void_beyond_start_of_wave_one": "the count is void beyond the start of wave one",
    "void_beyond_end_of_wave_one": "the count is void if wave four reaches the end of wave one",
    "void_beyond_start_of_wave_a": "the count is void beyond the start of wave A",
    "void_beyond_end_of_wave_b": "the count is void beyond the end of wave B",
    "void_beyond_corrective_extreme": "the count is void beyond the extreme of the corrective wave",
}

# Wave labels, types and clarity in words (the counter's values: app/price_action/waves/counter.py)
WAVE_WORDS: Dict[str, str] = {"IMP_1": "wave one", "IMP_2": "wave two", "IMP_3": "wave three", "IMP_4": "wave four", "IMP_5": "the fifth wave",
                              "COR_A": "wave A", "COR_B": "wave B", "COR_C": "wave C", "COR_D": "wave D", "COR_E": "wave E",
                              "UNCLEAR": "an unclear count"}
TYPE_WORDS: Dict[str, str] = {"IMPULSE": "impulse", "DIAGONAL": "diagonal", "ZIGZAG": "zigzag", "FLAT": "flat", "EXPANDED_FLAT": "expanded flat",
                              "TRIANGLE": "triangle", "DOUBLE": "double three", "UNCLEAR": "type not clear yet"}
CLARITY_WORDS: Dict[str, str] = {"CLEAR": "the count is clear", "ALT_AGREES": "the alternate count points the same way",
                                 "ALT_DISAGREES": "the alternate count points the other way", "UNCLEAR": "the count is not clear"}

TIMEFRAME_WORDS: Dict[str, str] = {"1m": "1-minute", "3m": "3-minute", "5m": "5-minute", "15m": "15-minute", "30m": "30-minute",
                                   "1h": "1-hour", "1d": "Daily", "1w": "Weekly", "1M": "Monthly"}
PLACEHOLDERS = ("timeframe", "context_timeframe", "wave_timeframe", "higher_timeframe")
# Family G: the placeholders filled from a template's timeframe settings (the setting a template must declare to use one)
TIMEFRAME_SETTING_PLACEHOLDERS: Dict[str, str] = {"wave_timeframe": "wave_tf", "higher_timeframe": "higher_tf"}

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


def render(tokens: Sequence[str], *, timeframe: str, context_timeframe: Optional[str] = None, wave_timeframe: Optional[str] = None,
           higher_timeframe: Optional[str] = None) -> str:
    """The sentence for a signal: the phrases joined with "; ", capitalised, ending with a full stop."""
    words = {"timeframe": TIMEFRAME_WORDS[timeframe],
             "context_timeframe": TIMEFRAME_WORDS[context_timeframe] if context_timeframe else "higher-timeframe",
             "wave_timeframe": TIMEFRAME_WORDS[wave_timeframe] if wave_timeframe else "wave-count",
             "higher_timeframe": TIMEFRAME_WORDS[higher_timeframe] if higher_timeframe else "higher-timeframe"}
    phrases = [TOKENS[t].format(**words) for t in tokens]
    text = "; ".join(phrases)
    return (text[:1].upper() + text[1:] + ".") if text else ""


def unknown_tokens(tokens: Iterable[str]) -> List[str]:
    return [t for t in tokens if t not in TOKENS]


def wave_words(label: Optional[str]) -> str:
    """A wave label, type or clarity value in words (empty for a missing value)."""
    if not label:
        return ""
    return WAVE_WORDS.get(label) or TYPE_WORDS.get(label) or CLARITY_WORDS.get(label) or ""


__all__ = ["TOKENS", "TIMEFRAME_WORDS", "FORBIDDEN_WORDS", "PLACEHOLDERS", "TIMEFRAME_SETTING_PLACEHOLDERS", "WAVE_WORDS", "TYPE_WORDS",
           "CLARITY_WORDS", "guard", "render", "unknown_tokens", "wave_words"]
