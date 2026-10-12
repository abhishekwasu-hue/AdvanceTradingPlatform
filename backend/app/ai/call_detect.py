"""H-C1 c (review of #230): trade calls aimed at an instrument, in English, Marathi, Hindi and romanised Hindi / Marathi,
whatever the answer language. `call_terms(text)` returns short labels (empty = no call); the output filter blocks a text
with any. The word lists are data (the "calls" block of `data/advice_terms.json`).

**Normalisation first** (`normalise`): NFKC (full-width and mathematical letters), Unicode format characters removed
(zero-width space / joiner, bidi marks), Latin look-alikes from Cyrillic / Greek mapped (`app.support.normalize`), the
Devanagari nukta dropped ("ख़रीदें" reads "खरीदें"), markdown emphasis and code marks removed, and list markers, bullets,
block quotes, quotes, brackets and pictographs (emoji) turned into clause breaks. Newlines are kept (paragraphs).

**What is a call** (each one blocked):

* an imperative trade verb opening a clause - at the start, after a sentence end, a list marker, ``,`` ``;`` ``:``,
  `` - `` / ``->`` or a bracket, optionally after "please / then / just / time to ..." - followed (past at most four
  filler words such as "all", "a few shares of") by an instrument, a share word or a price level ("Sell RELIANCE",
  "- Buy: RELIANCE", "Exit all INFY positions", "Buy 25000 CE above 120");
* ``INSTRUMENT: buy`` (not "NIFTY: buy signals were rare");
* a Marathi / Hindi / romanised trade verb next to an instrument or a share word ("RELIANCE खरेदी करा", "यह शेयर
  खरीदें", "रिलायंस खरीदें", "TCS kharido"); the light verbs (घ्या, ले लो) only right after the instrument;
* a modal call ("I would / I'd / one could buy RELIANCE"), "consider buying RELIANCE", "my pick is RELIANCE",
  "RELIANCE is a buy";
* a stop AND a target with price levels in one paragraph, plus an entry level ("entry", "buy above", "long above") or an
  instrument - across lines and sentences;
* a price level with certainty ("RELIANCE to 3000 for sure", "will surely reach 3000", "three thousand for sure");
* any of the above once a disguised spelling is undone ("B u y RELIANCE", "8uy RELIANCE").

An **instrument** is an upper-case ticker (not a platform or indicator acronym) or a name from the instrument index
(`app.ai.instrument_names`: the instrument master plus a seed with Marathi / Hindi spellings), in any case.

**Not calls:** percentages ("stop 1%, target 2%"), status / log / backtest / rule / how-to text ("Status: BUY RELIANCE
filled", "Backtest trade 3: entry 2480, stop 2430, target 2580", "Rule: Buy NIFTY when RSI < 30", "How to: Buy RELIANCE in
paper mode"), "when <condition>" rules, negated sayings ("We never say: Sell RELIANCE", "...असे आम्ही सांगत नाही") and
past-tense certainty ("hit 25000 for sure on Friday"). A modal call with a price level but no instrument ("One could buy
the 25000 CE above 120") stays a framed specific call (output_filter), as the spec allows.

Texts longer than `MAX_CHARS` are not read: they answer one label ("too long to check") and are blocked.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import FrozenSet, List, Optional, Pattern, Sequence, Tuple

from app.ai import instrument_names

MAX_CHARS = 40_000
_WORD = "\\w\u0900-\u0963\u0966-\u097f"            # Devanagari letters and signs are word characters; the dandas are not
_TICKER = re.compile(r"[A-Z][A-Z0-9&]{1,19}(?:-[A-Z]{1,4})?")
_NUMBER = r"₹?\s*\d[\d,]*(?:\.\d+)?"


def _alt(words: Sequence[str]) -> str:
    return "|".join(re.escape(w) for w in sorted({w.strip() for w in words if w and w.strip()}, key=len, reverse=True))


# --- normalisation ------------------------------------------------------------------------------------------------------

_MARKDOWN = re.compile(r"[*_`~]+")
_LINE_MARKER = re.compile(r"(?m)^[ \t]*(?:>+|[-*+•·▪◦‣●]|\d{1,3}[.)])[ \t]+")
_BREAKS = re.compile(r"[\"“”«»()\[\]{}|•·▪◦‣●]")
_ARROWS = str.maketrans({"→": "->", "⇒": "->", "➜": "->", "➔": "->", "⟶": "->", "‘": "'", "’": "'"})


def normalise(text: str) -> str:
    from app.support.normalize import _CONFUSABLES
    t = unicodedata.normalize("NFKC", text or "")
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Cf")
    t = t.translate(_CONFUSABLES).translate(_ARROWS).replace("\u093c", "")
    t = re.sub("[\ufe00-\ufe0f]", "", t)                                           # emoji variation selectors
    t = "".join("\n" if unicodedata.category(ch) in ("So", "Sk") else ch for ch in t)   # pictographs (emoji) break a clause
    t = _MARKDOWN.sub("", t)
    t = _LINE_MARKER.sub("", t)
    t = _BREAKS.sub("\n", t)
    t = re.sub(r"(?<!\w)'|'(?!\w)", "\n", t)                                       # quotes, never apostrophes ("I'd")
    return re.sub(r"[ \t]+", " ", t)


# --- the word lists -----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Lists:
    imperative: Pattern
    modal: Pattern
    gerund: Pattern
    colon: Pattern
    deva: Pattern
    deva_light: Pattern
    opinion: Pattern
    rated: Pattern
    certainty: Tuple[Pattern, ...]
    stop: Pattern
    target: Pattern
    entry: Pattern
    descriptive: Pattern
    fillers: FrozenSet[str]
    share_words: FrozenSet[str]
    not_tickers: FrozenSet[str]
    context: Pattern
    condition: Pattern
    past: Pattern
    negated: Pattern
    howto: Pattern


@lru_cache(maxsize=4)
def lists(path: str) -> Lists:
    from app.ai.grounding import ACRONYMS
    calls = json.loads(open(path, encoding="utf-8").read()).get("calls") or {}
    verbs = calls.get("verbs", {})
    en = _alt(verbs.get("en", []))
    light_words = set(calls.get("light_verbs", []))
    deva = _alt([v for v in verbs.get("mr", []) + verbs.get("hi", []) + verbs.get("romanised", []) if v not in light_words])
    light = _alt(sorted(light_words))
    # LOW10: [ \t]* after the anchor (a newline is an anchor of its own), so runs of newlines stay linear
    start = r"(?:^|(?<=[\n.!?;:,(\u0964\u0965—–])|(?<=\s-)|(?<=->))[ \t]*"
    prefix = rf"(?:(?:{_alt(calls.get('clause_prefixes', []))})\s+){{0,2}}"
    rest = r"(?P<rest>[^\n.!?;\u0964]{0,120})"
    sep = r"\s*(?:[:\-–—>]+\s*)?"
    number_words = _alt(calls.get("number_words", []))
    number = rf"(?:{_NUMBER}|(?<![{_WORD}])(?:(?:{number_words})[\s-]*)+(?![{_WORD}]))"
    certainty = _alt(calls.get("certainty", []))
    lead = _alt(calls.get("certainty_lead", []))
    moves = _alt(calls.get("moves", []))
    level = lambda words: re.compile(rf"(?<![{_WORD}])(?:{_alt(words)})(?![{_WORD}])\s*[:=@-]?\s*(?:of\s+|at\s+|near\s+|above\s+|below\s+)?{_NUMBER}(?![\d.,]*\s*%)", re.I)  # noqa: E731
    neg = calls.get("negated_sayings", {})
    return Lists(
        imperative=re.compile(rf"{start}{prefix}(?P<verb>{en})(?![{_WORD}-]){sep}{rest}", re.I),
        modal=re.compile(rf"(?<![{_WORD}])(?:{_alt(calls.get('modal_subjects', []))})\s*(?:{_alt(calls.get('modal_verbs', []))})\s+"
                         rf"(?:(?:also|still|definitely|probably|personally|now|happily|just)\s+)?(?P<verb>{en})(?![{_WORD}-]){sep}{rest}", re.I),
        gerund=re.compile(rf"(?<![{_WORD}])(?:consider|considering|worth|try)\s+(?P<verb>{_alt(calls.get('gerunds', []))})(?![{_WORD}-]){sep}{rest}", re.I),
        colon=re.compile(rf"(?P<obj>[^\n.!?;:\u0964]{{1,60}}?)\s*[:\-–—]\s*(?P<verb>{en})(?![{_WORD}-])(?P<after>[^\n.!?;\u0964]{{0,40}})", re.I),
        deva=re.compile(rf"(?<![{_WORD}])(?P<verb>{deva})(?![{_WORD}])", re.I),
        deva_light=re.compile(rf"(?<![{_WORD}])(?P<verb>{light})(?![{_WORD}])", re.I),
        opinion=re.compile(rf"(?<![{_WORD}])(?:my|our|top|best)\s+(?:top\s+)?(?:{_alt(calls.get('opinion_nouns', []))})\s*(?:for\s+\w+\s+)?(?:is|are|=|:|-)\s*{rest}", re.I),
        rated=re.compile(rf"\s+(?:is|looks like|looks|seems like|seems|remains|stays|is rated)\s+(?:a|an)?\s*(?:{_alt(calls.get('rating_words', []))})(?![{_WORD}-])", re.I),
        certainty=(
            re.compile(rf"(?<![{_WORD}])(?:{moves})\s+{number}\s*(?:[{_WORD}]+\s+)?(?:{certainty})(?![{_WORD}])", re.I),
            re.compile(rf"{number}\s*(?:(?:{moves})\s+)?(?:{certainty})(?![{_WORD}])", re.I),
            re.compile(rf"(?<![{_WORD}])(?:will\s+)?(?:{lead})\s+(?:{moves})\s+{number}", re.I),
        ),
        stop=level(calls.get("stop", [])), target=level(calls.get("target", [])), entry=level(calls.get("entry", [])),
        descriptive=re.compile(rf"^\s*(?:{_alt(calls.get('descriptive_nouns', []))})(?![{_WORD}])", re.I),
        fillers=frozenset(w.lower() for w in calls.get("fillers", [])),
        share_words=frozenset(w.lower() for w in calls.get("share_words", [])),
        not_tickers=frozenset(t.upper() for t in calls.get("not_tickers", [])) | frozenset(ACRONYMS),
        context=re.compile(rf"(?:^|\n)\s*(?:{_alt(calls.get('context_labels', []))})(?:\s*#?\s*\d+)?\s*[:\-]|"
                           rf"(?<![{_WORD}])(?:{_alt(calls.get('context_phrases', []))})(?![{_WORD}])", re.I),
        condition=re.compile(rf"(?<![{_WORD}])(?:{_alt(calls.get('condition_words', []))})(?![{_WORD}])", re.I),
        past=re.compile(rf"(?<![{_WORD}])(?:{_alt(calls.get('past_markers', []))})(?![{_WORD}])|\d{{4}}-\d{{2}}-\d{{2}}|trade\s*#?\s*\d+", re.I),
        negated=re.compile(rf"(?<![{_WORD}])(?:{_alt(neg.get('en', []) + neg.get('mr', []) + neg.get('hi', []))})(?![{_WORD}])", re.I),
        howto=re.compile(r"\b(?:paper mode|in the app|button|page|tab|click|tap|screen|menu|watchlist|symbol box)\b", re.I),
    )


# --- instruments --------------------------------------------------------------------------------------------------------

_TOKEN = re.compile(r"[^\s,;:]+")


def _words(text: str) -> List[str]:
    return [w.strip("\"'.,!?;:()[]{}\u0964\u0965") for w in _TOKEN.findall(text or "")]


def _is_ticker(word: str, L: Lists) -> bool:
    return bool(_TICKER.fullmatch(word)) and word.upper() not in L.not_tickers


def _instrument_at_start(rest: str, L: Lists, *, allow_level: bool) -> Optional[str]:
    """The instrument (or share word / price level) the words of `rest` open with, past at most four filler words."""
    words = [w for w in _words(rest) if w]
    i = 0
    while i < len(words) and i < 4 and words[i].lower() in L.fillers and not _is_ticker(words[i], L):
        nxt = words[i + 1].lower() if i + 1 < len(words) else ""
        if words[i].lower() in L.share_words and nxt not in ("of", "in"):
            break                                   # "Buy this stock now": the share word is the object
        i += 1
    tail = words[i:]
    if not tail:
        return None
    first = tail[0]
    if _is_ticker(first, L):
        return first
    n = instrument_names.prefix_len(tail)
    if n:
        return " ".join(tail[:n])
    if first.lower() in L.share_words:
        return first
    if allow_level and re.fullmatch(r"₹?\d[\d,]*(?:\.\d+)?", first):
        after = rest[rest.find(first) + len(first):]
        if not re.match(r"\s*%", after):                     # a percentage is not a price level
            return first
    return None


def _instrument_at_end(before: str, L: Lists, *, gap: int) -> Optional[str]:
    """The instrument (or share word) that ends `before`, past at most `gap` words that are not genitives."""
    words = [w for w in _words(before) if w]
    for skip in range(0, gap + 1):
        if skip > len(words):
            break
        head = words[:len(words) - skip] if skip else words
        between = words[len(words) - skip:] if skip else []
        if any(w in ("चा", "ची", "चे", "च्या", "का", "की", "के", "ला", "ना", "को") for w in between):
            return None
        if not head:
            break
        last = head[-1]
        if _is_ticker(last, L):
            return last
        n = instrument_names.suffix_len(head)
        if n:
            return " ".join(head[-n:])
        if last.lower() in L.share_words:
            return last
    return None


# --- detection ----------------------------------------------------------------------------------------------------------

_SENTENCE = re.compile(r"(?<!\d)[.!?](?!\d)|[\n\u0964\u0965]")


def sentences(text: str) -> List[str]:
    return [s for s in _SENTENCE.split(text) if s and s.strip()]


def _paragraphs(text: str) -> List[str]:
    return [p for p in re.split(r"\n[ \t]*\n", text) if p.strip()]


def _sentence_calls(s: str, L: Lists) -> List[str]:
    found: List[str] = []
    if L.negated.search(s) or L.context.search(s):
        return found

    def add(label: str, what: str) -> None:
        item = f"call: {label} {what}".strip().lower()[:60]
        if item not in found:
            found.append(item)
    for m in L.imperative.finditer(s):
        rest = m.group("rest")
        if L.descriptive.match(rest) or L.condition.search(rest):
            continue
        obj = _instrument_at_start(rest, L, allow_level=True)
        if obj:
            add(m.group("verb"), obj)
    for pattern in (L.modal, L.gerund):
        for m in pattern.finditer(s):
            rest = m.group("rest")
            if L.descriptive.match(rest) or L.condition.search(rest) or L.howto.search(rest):
                continue
            obj = _instrument_at_start(rest, L, allow_level=False)
            if obj:
                add(m.group("verb"), obj)
    for m in L.colon.finditer(s):
        if L.descriptive.match(m.group("after")):
            continue
        obj = _instrument_at_end(m.group("obj"), L, gap=0)
        if obj and obj.lower() not in L.share_words:
            add(f"{obj}:", m.group("verb"))
    for m in L.deva.finditer(s):
        verb = m.group("verb")
        obj = _instrument_at_end(s[max(0, m.start() - 80):m.start()], L, gap=2) or _instrument_at_start(s[m.end():m.end() + 80], L, allow_level=False)
        if obj:
            add(obj, verb)
    for m in L.deva_light.finditer(s):
        obj = _instrument_at_end(s[max(0, m.start() - 80):m.start()], L, gap=0)
        if obj:
            add(obj, m.group("verb"))
    for m in L.opinion.finditer(s):
        obj = _instrument_at_start(m.group("rest"), L, allow_level=False)
        if obj:
            add("pick", obj)
    for m in L.rated.finditer(s):
        obj = _instrument_at_end(s[max(0, m.start() - 80):m.start()], L, gap=0)
        if obj:
            add(obj, "rated a buy / sell")
    if not L.past.search(s):
        for pattern in L.certainty:
            hit = pattern.search(s)
            if hit:
                add("level with certainty", hit.group(0).strip()[:30])
                break
    return found


def _paragraph_levels(p: str, L: Lists) -> bool:
    """A stop and a target with price levels, plus an entry level or an instrument, in one paragraph (not a log)."""
    if L.past.search(p) or L.context.search(p) or L.negated.search(p):
        return False
    if not (L.stop.search(p) and L.target.search(p)):
        return False
    if L.entry.search(p):
        return True
    return any(_is_ticker(w, L) for w in _words(p)) or any(instrument_names.prefix_len(_words(p)[i:]) for i in range(len(_words(p))))


def detect(text: str, path: str) -> List[str]:
    """The call labels in `text` (module docstring), in order of appearance."""
    if len(text or "") > MAX_CHARS:
        return [f"call: text too long to check (over {MAX_CHARS} characters)"]
    L = lists(path)
    body = normalise(text)
    found: List[str] = []
    for s in sentences(body):
        for item in _sentence_calls(s, L):
            if item not in found:
                found.append(item)
    for p in _paragraphs(body):
        if _paragraph_levels(p, L):
            item = "call: entry / stop / target levels"
            if item not in found:
                found.append(item)
    if not found:
        from app.support.normalize import deobfuscated
        for s in sentences(body):
            plain = set(_sentence_calls(s, L))
            for reading in deobfuscated(" ".join(s.split())):
                for item in _sentence_calls(reading, L):
                    if item not in plain and item not in found:
                        found.append(f"{item} (disguised)")
    return found


__all__ = ["detect", "normalise", "lists", "sentences", "MAX_CHARS"]
