"""Analyze a chart: the signal-language check (CLAUDE.md §3, "Signals, not advice").

Result text describes structure (swings, areas, lines, divergence, commitment, change of character) and conditions. It
never carries trade-call words: no buy / sell, no target, no stop / stop-loss. The platform's AI output filter already
withholds a text that reads as a call or advice; this check is stricter and word-level: a text that contains one of
these words at all is withheld (contract.text), whatever the sentence around it says. The frontend runs the same list
on everything it renders (frontend/src/chartAnalyze/signalLanguage.ts), so an older stored result is guarded too.

"buyer" / "seller" (the method's area sides) are structure words and are NOT matched: the patterns match whole words.
"""
import re

# Whole words only, any case, except "SL", which is matched only in capitals (a lower-case "sl" is not a trading word).
_WORDS = re.compile(
    r"\b(?:buy|buys|buying|bought|sell|sells|selling|sold|targets?|targeted|targeting|stops?|stop[\s-]?loss(?:es)?|stoploss(?:es)?)\b",
    re.IGNORECASE,
)
_SL = re.compile(r"\bSL\b")


def call_words(text: str) -> list[str]:
    """The trade-call words in `text`, lower-cased, in order (empty when the text is clean)."""
    if not text:
        return []
    found = [m.group(0).lower() for m in _WORDS.finditer(text)]
    found += ["sl" for _ in _SL.finditer(text)]
    return found


def is_clean(text: str) -> bool:
    return not call_words(text)
