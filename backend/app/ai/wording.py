"""ATP review 11: the Copilot's banned-word check, applied on the server to what a model wrote before anyone reads it.

The same list the frontend lints its own UI strings with (`frontend/src/copilot/compliance.test.ts`, BANNED) - words
that make an explanation sound like advice or a promise: recommend, best, "for you", match %, guarantee, assured,
risk-free, sure-shot, profit claims - plus the Marathi forms (खात्रीशीर, हमखास, गॅरंटी, निश्चित / नक्की नफा,
शिफारस करतो). A model's disclaimer is not a claim: an English word negated in its own clause ("not a recommendation", "no
guarantee", "no strategy is risk-free", "guarantees nothing") and a Marathi word followed by नाही within its clause
pass - but never through "no doubt" / "without question" / "isn't it", which strengthen a claim. "Best bid / ask /
practice / effort" and "at best" are ordinary phrases.

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
_CLAUSE = re.compile(r"[.!?;:,\n]")
# A negation governs a term only inside its own clause, at most two words before it, and never through
# "no doubt" / "without question" / "isn't it" (those make a claim stronger, not weaker).
_NEGATION_BEFORE = re.compile(
    r"(?:\bnot|\bno|\bnever|n't|\bnothing|\bwithout|\bnor|\bneither|\bcannot)\b"
    r"(?:\s+(?!doubt\b|question\b|it\b|that\b)[\w-]+){0,2}\s*$", re.I)
_NEGATION_AFTER_EN = re.compile(r"^\w*\s+(?:nothing|no\b)", re.I)              # "guarantees nothing", "guarantee no ..."
_NEGATION_AFTER = re.compile(r"^(?:\s*(?!शंका)\S+){0,3}?\s*(?:नाही|नसतो|नसते|नसतात|नव्हे)")
# Ordinary phrases that use a banned word without claiming anything.
_ALLOWED = re.compile(r"\bbest\s+(?:bid|ask|offer|practices?|efforts?)\b|\bat\s+best\b", re.I)


def _clause_before(text: str, at: int) -> str:
    head = text[max(0, at - 60):at]
    cut = max((m.end() for m in _CLAUSE.finditer(head)), default=0)
    return head[cut:]


def _clause_after(text: str, at: int) -> str:
    tail = text[at:at + 60]
    m = _CLAUSE.search(tail)
    return tail[:m.start()] if m else tail


def banned_terms(text: str) -> List[str]:
    """The banned words `text` uses as claims (negated mentions and ordinary phrases excluded), in list order."""
    if not text:
        return []
    text = _ALLOWED.sub(" ", text)
    found: List[str] = []
    for name, pattern in _EN:
        for m in pattern.finditer(text):
            if _NEGATION_BEFORE.search(_clause_before(text, m.start())) or _NEGATION_AFTER_EN.search(_clause_after(text, m.end() - 1)):
                continue
            found.append(name)
            break
    for name, pattern in _MR:
        if any(not _NEGATION_AFTER.search(_clause_after(text, m.end())) for m in pattern.finditer(text)):
            found.append(name)
    return found


def check_wording(text: str) -> Tuple[bool, List[str]]:
    bad = banned_terms(text)
    return (not bad), bad


__all__ = ["banned_terms", "check_wording"]
