"""Analyze a chart: the signal-language check (CLAUDE.md §3, "Signals, not advice").

Result text describes structure (swings, areas, lines, divergence, commitment, change of character) and conditions. It
never carries trade-call words: no buy / sell, no target, no stop / stop-loss. The platform's AI output filter already
withholds a text that reads as a call or advice; this check is stricter and word-level: a text that contains one of
these words at all is withheld (contract.text), whatever the sentence around it says. The frontend runs the same list
on everything it renders (frontend/src/chartAnalyze/signalLanguage.ts, through frontend/src/signals/language.ts), so an
older stored result is guarded too.

"buyer" / "seller" (the method's area sides) are structure words and are NOT matched: the patterns match whole words.
"""
from typing import List

# Handover 11l: the list and the check live in the one shared signal-language module; this module keeps its names for
# its callers (contract.text, strikes, the tests). The behaviour is identical (tests/test_signal_language.py).
from app.signals import language as _language

_WORDS = _language.CALL_WORDS
_SL = _language.SL_WORD


def call_words(text: str) -> List[str]:
    """The trade-call words in `text`, lower-cased, in order (empty when the text is clean)."""
    return _language.call_words(text)


def is_clean(text: str) -> bool:
    return _language.is_clean(text)
