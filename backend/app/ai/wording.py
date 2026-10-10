"""ATP review 11: the Copilot's banned-word check, applied on the server to what a model wrote before anyone reads it.

The same list the frontend lints its own UI strings with (`frontend/src/copilot/compliance.test.ts`, BANNED) - words
that make an explanation sound like advice or a promise: recommend, best, "for you", match %, guarantee, assured,
risk-free, sure-shot, profit claims - plus the Marathi forms (खात्रीशीर, हमखास, गॅरंटी, निश्चित / नक्की नफा,
शिफारस करतो). A model's disclaimer is not a claim: an English word preceded by a negation ("not a recommendation",
"no guarantee", "nothing is risk-free") and a Marathi word followed by नाही within a few words pass.

`banned_terms(text)` names what matched; callers retry the model once naming the words, else fall back to the
rule-based text (never show the unchecked reply).
"""
from __future__ import annotations

import re
from typing import List, Pattern, Tuple

_EN: List[Tuple[str, Pattern[str]]] = [
    ("recommend", re.compile(r"\brecommend(?:ed|s|ing|ations?)?\b", re.I)),
    ("best", re.compile(r"\bbest\b", re.I)),
    ("for you", re.compile(r"\bfor you\b(?!\s+to\b)", re.I)),          # "for you to decide" puts the decision with the trader
    ("match %", re.compile(r"\bmatch\s*%|%\s*match\b|\bmatch(?:ing)? score\b", re.I)),
    ("guarantee", re.compile(r"\bguarantee(?:d|s)?\b", re.I)),
    ("assured / risk-free / sure-shot", re.compile(r"\bassured\b|\brisk[- ]free\b|\bsure[- ]?shot\b", re.I)),
    ("profit claim", re.compile(r"\b(?:sure|certain|easy|guaranteed) (?:profit|returns?|gains?)\b|\bdouble your\b", re.I)),
]
_MR: List[Tuple[str, Pattern[str]]] = [
    ("खात्रीशीर", re.compile(r"खात्रीशीर")),
    ("हमखास", re.compile(r"हमखास")),
    ("गॅरंटी", re.compile(r"गॅरंटी|गॅरेंटी|ग्यारंटी")),
    ("निश्चित / नक्की नफा", re.compile(r"(?:निश्चित|नक्की|पक्का)\s+(?:नफा|फायदा|परतावा)")),
    ("शिफारस करतो", re.compile(r"शिफारस\s+(?:करतो|करते|करतात|करू|करेन|केली)")),
]
_NEGATION_BEFORE = re.compile(r"(?:\bnot|\bno|\bnever|n't|\bnothing|\bwithout|\bnor|\bneither|\bcannot)\b(?:\W+\w+){0,3}\W*$", re.I)
_NEGATION_AFTER = re.compile(r"^(?:\s*\S+){0,3}?\s*(?:नाही|नसतो|नसते|नसतात|नव्हे)")


def banned_terms(text: str) -> List[str]:
    """The banned words `text` uses as claims (negated mentions excluded), in list order; [] = clean."""
    if not text:
        return []
    found: List[str] = []
    for name, pattern in _EN:
        if any(not _NEGATION_BEFORE.search(text[max(0, m.start() - 40):m.start()]) for m in pattern.finditer(text)):
            found.append(name)
    for name, pattern in _MR:
        if any(not _NEGATION_AFTER.search(text[m.end():m.end() + 40]) for m in pattern.finditer(text)):
            found.append(name)
    return found


def check_wording(text: str) -> Tuple[bool, List[str]]:
    bad = banned_terms(text)
    return (not bad), bad


__all__ = ["banned_terms", "check_wording"]
