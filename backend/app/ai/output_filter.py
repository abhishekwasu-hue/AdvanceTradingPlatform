"""H-C1 c: the server-side advice and guarantee filter on every LLM text the platform shows.

Two kinds of finding:

* **blocked** - guarantee words ("guaranteed", "sure-shot", खात्रीशीर, हमखास, ...) and advice phrasing ("I recommend",
  "you should buy", ...). The caller treats a blocked reply like a failed grounding check: one retry that names the
  words, then the rule-based text. The LLM's words are never shown.
* **framed** - a specific buy/sell call next to a price, strike or option leg. The text is kept, with an educational
  framing line in the answer's language appended.
* **blocked as a call** (fix of the Phase 0 gap) - a trade call aimed at an instrument, in any supported language:
  an imperative trade verb opening a clause next to a ticker or a share word ("Sell RELIANCE", "Go long RELIANCE",
  "Accumulate RELIANCE", "RELIANCE: buy the dip", "RELIANCE खरेदी करा", "यह शेयर खरीदें"), an entry / stop / target
  set with numbers ("Entry RELIANCE 2500 SL 2450 TGT 2600"), and a price level with certainty ("RELIANCE to 3000 for
  sure"). Descriptive analysis ("price closed above SMA 20", "buyers absorbed the selling", "the stop-loss rule in your
  template") passes - the corpus test holds both sides.

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
    calls: Tuple[Tuple[str, Pattern], ...] = ()
    stop: Optional[Pattern] = None
    target: Optional[Pattern] = None
    entry: Optional[Pattern] = None
    not_tickers: frozenset = frozenset()


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
    blocked = [w for kind in ("guarantee", "advice") for lang in ("en", "mr", "hi") for w in data.get(kind, {}).get(lang, [])]
    neg = data.get("negations", {})
    verbs = data["action_verbs"]["en"] + data["action_verbs"]["mr"]
    # A specific call: an action verb and, in the same clause and within a few words either side, a price, a strike or an
    # option leg ("buy 25000 CE", "25200 च्या वर खरेदी").
    verb = rf"(?<![{_WORD}])(?:{_alternation(verbs)})(?![{_WORD}-])"
    level = rf"(?:\d[\d,]*(?:\.\d+)?|(?<![{_WORD}])(?:CE|PE|call|put|कॉल|पुट)(?![{_WORD}]))"
    action = rf"{verb}[^.!?\n।]{{0,40}}?{level}|{level}[^.!?\n।]{{0,40}}?{verb}"
    calls = data.get("calls") or {}
    return Terms(blocked=re.compile(_bounded(blocked), re.IGNORECASE),
                 negated_before=re.compile(rf"(?:{_bounded(neg.get('before', []))})(?:\W+[{_WORD}]+){{0,2}}\W*$", re.IGNORECASE),
                 negated_after=re.compile(rf"^\W*(?:[{_WORD}]+\W+)?(?:{_bounded(neg.get('after', []))})", re.IGNORECASE),
                 action=re.compile(action, re.IGNORECASE), framing=data["framing"], **_call_patterns(calls))


TICKER = r"(?P<tic>[A-Z][A-Z0-9&]{1,19}(?:-[A-Z]{1,4})?)(?![\w&])"
NUMBER = r"₹?\s*\d[\d,]*(?:\.\d+)?"


def _call_patterns(calls: dict) -> dict:
    """The call detectors (module docstring): (label, pattern) pairs plus the entry / stop / target level patterns."""
    if not calls:
        return {}
    verbs = calls.get("verbs", {})
    en = _alternation(verbs.get("en", []))
    deva = _alternation(verbs.get("mr", []) + verbs.get("hi", []))
    shares = _alternation(calls.get("share_words", []))
    start = r"(?:^|(?<=[.!?\n:;।]))\s*(?:(?i:please|now|so)\s+)?"
    obj = r"(?:(?i:the|some|more|in|on|into|of|out of|this|these)\s+)?"
    pairs = [
        # "Sell RELIANCE", "Go long RELIANCE", "Book profits in RELIANCE", "Buy this share"
        ("imperative", re.compile(rf"{start}(?P<verb>(?i:{en}))\s+{obj}(?:{TICKER}|(?i:{shares})(?![{_WORD}]))")),
        # "RELIANCE: buy the dip" / "RELIANCE - sell"
        ("ticker-colon", re.compile(rf"{TICKER}\s*[:\-–—]\s*(?P<verb>(?i:{en}))(?![\w-])")),
        # "RELIANCE खरेदी करा", "यह शेयर खरीदें", "खरेदी करा RELIANCE"
        ("devanagari", re.compile(rf"(?:{TICKER}|(?<![{_WORD}])(?:{shares})(?![{_WORD}]))\s+(?:[{_WORD}]+\s+){{0,2}}?(?P<verb>{deva})(?![{_WORD}])")),
        ("devanagari", re.compile(rf"(?<![{_WORD}])(?P<verb>{deva})\s+{TICKER}")),
        # "RELIANCE to 3000 for sure", "3000 पर्यंत नक्की"
        ("certainty", re.compile(rf"(?<![{_WORD}])(?i:{_alternation(calls.get('moves', []))})\s+{NUMBER}\s*(?:[{_WORD}]+\s+)?"
                                 rf"(?i:{_alternation(calls.get('certainty', []))})(?![{_WORD}])|(?<![\w.])\d[\d,]*(?:\.\d+)?\s*"
                                 rf"(?i:{_alternation(calls.get('moves', []))})\s+(?i:{_alternation(calls.get('certainty', []))})(?![{_WORD}])")),
    ]
    level = lambda words: re.compile(rf"(?<![{_WORD}])(?i:{_alternation(words)})(?![{_WORD}])\s*[:=@-]?\s*(?:of\s+|at\s+)?{NUMBER}")  # noqa: E731
    return {"calls": tuple(pairs), "stop": level(calls.get("stop", [])), "target": level(calls.get("target", [])),
            "entry": level(calls.get("entry", [])), "not_tickers": frozenset(t.upper() for t in calls.get("not_tickers", []))}


def call_terms(text: str) -> List[str]:
    """Trade calls aimed at an instrument (module docstring), as short labels in order of appearance."""
    t = terms()
    found: List[str] = []
    body = text or ""
    for label, pattern in t.calls:
        for m in pattern.finditer(body):
            tic = m.groupdict().get("tic")
            if tic is not None and tic.upper() in t.not_tickers:
                continue
            item = f"call: {m.group(0).strip()[:40].lower()}"
            if item not in found:
                found.append(item)
    if t.stop is not None and t.target is not None and t.entry is not None:
        for sentence in re.split(r"(?<=[.!?\n।])", body):
            if t.stop.search(sentence) and t.target.search(sentence):
                tickers = [x.group("tic") for x in re.finditer(TICKER, sentence) if x.group("tic").upper() not in t.not_tickers]
                if t.entry.search(sentence) or tickers:
                    item = "call: entry / stop / target levels"
                    if item not in found:
                        found.append(item)
    return found


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
    return found + [c for c in call_terms(text) if c not in found]


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


__all__ = ["screen", "check", "blocked_terms", "call_terms", "is_specific_call", "framing", "retry_hint", "Screened"]
