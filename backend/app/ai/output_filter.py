"""H-C1 c: the server-side advice and guarantee filter on every LLM text the platform shows.

Two kinds of finding:

* **blocked** - guarantee words ("guaranteed", "sure-shot", खात्रीशीर, हमखास, ...) and advice phrasing ("I recommend",
  "you should buy", ...). The caller treats a blocked reply like a failed grounding check: one retry that names the
  words, then the rule-based text. The LLM's words are never shown.
* **framed** - a specific buy/sell call next to a price, strike or option leg. The text is kept, with an educational
  framing line in the answer's language appended.

The word lists are data (`app/ai/data/advice_terms.json`, or the file named by `AI_ADVICE_TERMS_FILE`), checked in
both languages whatever the answer language. A term right after a negation ("not a recommendation", "never
guaranteed") or right before a Marathi one ("शिफारस नाही") is allowed. Every finding is counted in
`atp_ai_output_filtered_total{kind}` and returned to the caller to show in the response.
"""
import json
import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Pattern, Tuple

log = logging.getLogger(__name__)

DEFAULT_FILE = Path(__file__).resolve().parent / "data" / "advice_terms.json"
_WORD = r"\wऀ-ॿ"            # Devanagari vowel signs are not \w: count the whole block as word characters


@dataclass(frozen=True)
class Terms:
    blocked: Pattern
    negated_before: Pattern
    negated_after: Pattern
    action: Pattern
    framing: dict


@dataclass
class Screened:
    text: str
    blocked: List[str] = field(default_factory=list)
    framed: bool = False

    @property
    def ok(self) -> bool:
        return not self.blocked

    def flags(self) -> dict:
        return {"blocked": self.blocked, "framed": self.framed}


def _alternation(words: List[str]) -> str:
    return "|".join(re.escape(w) for w in sorted({w.strip() for w in words if w.strip()}, key=len, reverse=True))


def _bounded(words: List[str]) -> str:
    return rf"(?<![{_WORD}])(?:{_alternation(words)})(?![{_WORD}])"


@lru_cache(maxsize=4)
def _load(path: str) -> Terms:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    blocked = [w for kind in ("guarantee", "advice") for lang in ("en", "mr") for w in data.get(kind, {}).get(lang, [])]
    neg = data.get("negations", {})
    verbs = data["action_verbs"]["en"] + data["action_verbs"]["mr"]
    # A specific call: an action verb and, in the same clause and within a few words either side, a price, a strike or an
    # option leg ("buy 25000 CE", "25200 च्या वर खरेदी").
    verb = rf"(?<![{_WORD}])(?:{_alternation(verbs)})(?![{_WORD}-])"
    level = rf"(?:\d[\d,]*(?:\.\d+)?|(?<![{_WORD}])(?:CE|PE|call|put|कॉल|पुट)(?![{_WORD}]))"
    action = rf"{verb}[^.!?\n।]{{0,40}}?{level}|{level}[^.!?\n।]{{0,40}}?{verb}"
    return Terms(blocked=re.compile(_bounded(blocked), re.IGNORECASE),
                 negated_before=re.compile(rf"(?:{_bounded(neg.get('before', []))})(?:\W+[{_WORD}]+){{0,2}}\W*$", re.IGNORECASE),
                 negated_after=re.compile(rf"^\W*(?:[{_WORD}]+\W+)?(?:{_bounded(neg.get('after', []))})", re.IGNORECASE),
                 action=re.compile(action, re.IGNORECASE), framing=data["framing"])


def terms() -> Terms:
    return _load(os.environ.get("AI_ADVICE_TERMS_FILE") or str(DEFAULT_FILE))


def blocked_terms(text: str) -> List[str]:
    """The guarantee/advice terms in `text` that are not negated, lower-cased, in order of appearance."""
    t = terms()
    found: List[str] = []
    for m in t.blocked.finditer(text or ""):
        before, after = text[max(0, m.start() - 30):m.start()], text[m.end():m.end() + 20]
        if t.negated_before.search(before) or t.negated_after.search(after):
            continue
        word = m.group(0).lower()
        if word not in found:
            found.append(word)
    return found


def is_specific_call(text: str) -> bool:
    return bool(terms().action.search(text or ""))


def framing(lang: str) -> str:
    f = terms().framing
    return f["mr"] if lang == "mr" else f["en"]


def screen(text: str, lang: str = "en", *, where: str = "ai") -> Screened:
    """Blocked terms (the caller must not show the text) or the text, framed when it makes a specific call."""
    from app.observability.metrics import AI_OUTPUT_FILTERED
    out = Screened(text=text or "")
    out.blocked = blocked_terms(out.text)
    if out.blocked:
        AI_OUTPUT_FILTERED.labels(kind="blocked", where=where).inc()
        log.info("AI output filter: %s reply blocked for %s", where, ", ".join(out.blocked[:5]))
        return out
    if is_specific_call(out.text):
        line = framing(lang)
        if line not in out.text:
            out.text = f"{out.text.rstrip()}\n\n{line}"
        out.framed = True
        AI_OUTPUT_FILTERED.labels(kind="framed", where=where).inc()
    return out


def retry_hint(blocked: List[str]) -> str:
    return (f"Your previous answer used words that are not allowed: {', '.join(blocked[:8])}. Answer again without promises "
            "of profit, guarantees or recommendations - describe conditions and risks neutrally.")


def check(text: str, lang: str = "en", *, where: str = "ai") -> Tuple[bool, Optional[str], str]:
    """(ok, text to show or None, why) - the shape the narrate loops use."""
    s = screen(text, lang, where=where)
    if not s.ok:
        return False, None, f"advice/guarantee words: {', '.join(s.blocked[:5])}"
    return True, s.text, "framed" if s.framed else "ok"


__all__ = ["screen", "check", "blocked_terms", "is_specific_call", "framing", "retry_hint", "Screened"]
