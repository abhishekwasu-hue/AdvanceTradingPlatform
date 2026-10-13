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

import bisect
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
# A number starts where a digit run starts (review of #230, MED perf: no backtracking into a run of digits).
_NUMBER = r"(?<![\d,.])₹?\s*(?<![\d,.])\d[\d,]*(?:\.\d+)?"


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
    verb_line: Pattern


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
    sep = r"\s*(?:[:\-–—>=]+\s*)?"
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
        verb_line=re.compile(rf"(?im)^([ \t]*(?:{en})[ \t]*[:\-–—>=]*)[ \t]*\n(?=[ \t]*\S)"),
    )


# --- instruments --------------------------------------------------------------------------------------------------------

_TOKEN = re.compile(r"[^\s,;:]+")


def _words(text: str) -> List[str]:
    return [w.strip("\"'.,!?;:()[]{}\u0964\u0965") for w in _TOKEN.findall(text or "")]


def _is_ticker(word: str, L: Lists) -> bool:
    return bool(_TICKER.fullmatch(word)) and word.upper() not in L.not_tickers


_QTY_UNITS = {"lots", "lot", "shares", "share", "qty", "units", "unit", "stocks"}


def _instrument_at_start(rest: str, L: Lists, *, allow_level: bool) -> Optional[str]:
    """The instrument (or share word / price level) the words of `rest` open with, past at most five filler words
    ("all", "a few shares of", "two lots of", "10 shares of")."""
    words = [w for w in _words(rest) if w]
    i = 0
    while i < len(words) and i < 5 and not _is_ticker(words[i], L):
        w, nxt = words[i].lower(), (words[i + 1].lower() if i + 1 < len(words) else "")
        if re.fullmatch(r"\d+", w) and nxt in _QTY_UNITS:           # "10 shares of": a quantity, not a level
            i += 1
            continue
        if w not in L.fillers:
            break
        if w in L.share_words and nxt not in ("of", "in"):
            break                                               # "Buy this stock now": the share word is the object
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


# --- detection ----------------------------------------------------------------------------------------------------------

_SENTENCE = re.compile(r"(?<!\d)[.!?](?!\d)|[\n\u0964\u0965]")
# Clause breaks inside a sentence: the scope of a "when", a negation or a past marker (review of #230: never the sentence).
_CLAUSE = re.compile(r"[,;—–]|\s-\s|\sbut\s|\sand then\s", re.I)
# A record line: a status / log line with a past or passive status word, a quantity field or an approval state - only a
# line that opens with a label ("Status:", "PAPER order placed:", "Proposal:") and reads like a record is not a call.
_LABEL = re.compile(r"^\s*(?P<label>[^\W\d][\w &'/-]{0,40}?)\s*(?:#?\s*\d+\s*)?:")
_RECORD = re.compile(r"\b(?:placed|filled|executed|drafted|bought|sold|closed|exited|cancelled|canceled|rejected|pending|awaiting|"
                     r"needs your approval|was|were|squeezed|triggered)\b|\b\d+\s*(?:shares?|qty|lots?|units?)\b|\bqty\s*[:=]?\s*\d+|"
                     r"\bx\s*\d+\b", re.I)
_FIELD_LABELS = {"side"}                                          # a field of an order record: the line is the record itself
_RULE_LABELS = {"rule", "rules", "entry rule", "exit rule", "strategy", "template", "condition", "setup rule"}
_HOWTO_LABEL = re.compile(r"^\s*how[ -]?to\b", re.I)
_NOUN_PHRASE = re.compile(r"^\s*(?:\S+\s+){0,1}?(?:was|were|is|are|has|have|had|remain|remained|stay|stayed|appear|appeared|"
                          r"get|got|shown|listed)\b", re.I)
_PRONOUN_OBJECT = re.compile(r"^\s*(?:(?:some|a|bit|little|few)\s+)*(?:more|it|them|this|that|those|these)?\s*(?:now|here|today)?\s*$", re.I)
_DEVA_SAYING_LEAD = re.compile(r"(?:असे|अस|ऐसा|ऐसी|ऐसे|यह|ये)\s+(?:\S+\s+){0,3}$")
_OBJECT_MARKERS = ("ला", "ना", "को", "में", "मध्ये")
_GENITIVES = ("चा", "ची", "चे", "च्या", "का", "की", "के")


def sentences(text: str) -> List[str]:
    return [s for s in _SENTENCE.split(text) if s and s.strip()]


def _paragraphs(text: str) -> List[str]:
    return [p for p in re.split(r"\n[ \t]*\n", text) if p.strip()]


def _clause(s: str, start: int, end: int) -> str:
    """The clause of sentence `s` around s[start:end] (clause breaks: , ; dashes, "but")."""
    lo = max((m.end() for m in _CLAUSE.finditer(s, 0, start)), default=0)
    nxt = _CLAUSE.search(s, end)
    return s[lo:nxt.start() if nxt else len(s)]


def record_line(s: str) -> bool:
    label = _LABEL.match(s)
    if label is None:
        return False
    return label.group("label").strip().lower() in _FIELD_LABELS or bool(_RECORD.search(s))


def _rule_line(s: str) -> bool:
    label = _LABEL.match(s)
    return label is not None and label.group("label").strip().lower() in _RULE_LABELS


def _negated(s: str, start: int, L: Lists) -> bool:
    """A negated saying that governs the call at `start`: before it in the same clause, or ending in ':' right before it
    ("We never say: Sell RELIANCE"); a Marathi / Hindi one after it, led by असे / ऐसा ("RELIANCE खरेदी करा, असे आम्ही सांगत
    नाही")."""
    for m in L.negated.finditer(s):
        if m.start() < start:
            between = s[m.end():start]
            if not _CLAUSE.search(between) or re.match(r"^\s*(?:\w+\s+){0,2}?:", between):
                return True
        elif re.search(r"[\u0900-\u097f]", m.group(0)) and _DEVA_SAYING_LEAD.search(s[max(0, m.start() - 60):m.start()]):
            return True
    return False


def _strip_markers(words: List[str]) -> List[str]:
    while words and words[-1] in _OBJECT_MARKERS:               # "RELIANCE ला खरेदी करा", "TCS को खरीदें": object markers
        words = words[:-1]
    return words


def _instrument_at_end(before: str, L: Lists, *, gap: int) -> Optional[str]:
    """The instrument (or share word) that ends `before`, past at most `gap` words that are not genitives (object
    markers ला / ना / को / में are skipped)."""
    words = _strip_markers([w for w in _words(before) if w])
    for skip in range(0, gap + 1):
        if skip > len(words):
            break
        head = words[:len(words) - skip] if skip else words
        between = words[len(words) - skip:] if skip else []
        if any(w in _GENITIVES for w in between):
            return None
        head = _strip_markers(head)
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


def _has_instrument(text: str, L: Lists) -> bool:
    words = [w for w in _words(text) if w]
    if any(_is_ticker(w, L) for w in words):
        return True
    return any(instrument_names.prefix_len(words[i:i + instrument_names.MAX_WORDS]) for i in range(len(words)))


def _object(rest: str, L: Lists, s: str, *, allow_level: bool) -> Optional[str]:
    """What an imperative / modal verb is aimed at (None = not a call): an instrument, a share word or a price level after
    the fillers; "a signal / setup / rating on RELIANCE"; a pronoun ("buy more") when the sentence names an instrument.
    A noun phrase ("Sell HDFCBANK was the most common signal") is not a call."""
    if L.descriptive.match(rest):
        tail = L.descriptive.sub("", rest, count=1)
        tail = re.sub(r"^\s*(?:on|in|for|of)\s+", "", tail, flags=re.I)
        obj = _instrument_at_start(tail, L, allow_level=False)
        if obj is None or _NOUN_PHRASE.match(tail[tail.find(obj.split()[-1]) + len(obj.split()[-1]):] if obj.split()[-1] in tail else ""):
            return None
        return obj
    legs = re.match(r"^\s*(?:(?:the|some|a few|more)\s+)?(?:calls?|puts?|options?|futures?|ce|pe)\s+(?:on|in|of)\s+", rest, re.I)
    if legs:                                                    # "Buy calls on NIFTY": an option leg of the instrument
        obj = _instrument_at_start(rest[legs.end():], L, allow_level=False)
        if obj:
            return obj
    obj = _instrument_at_start(rest, L, allow_level=allow_level)
    if obj is not None:
        last = obj.split()[-1]
        after = rest[rest.find(last) + len(last):] if last in rest else ""
        if _NOUN_PHRASE.match(re.sub(r"^\s*(?:positions?|shares?|stocks?|calls?|puts?|orders?|signals?)\b", "", after, flags=re.I)):
            return None
        return obj
    if _PRONOUN_OBJECT.match(rest) and _has_instrument(s, L):
        return "it"
    return None


def _sentence_calls(s: str, L: Lists) -> List[str]:
    found: List[str] = []
    record, rule = record_line(s), _rule_line(s)
    if record:
        return found

    def add(label: str, what: str) -> None:
        item = f"call: {label} {what}".strip().lower()[:60]
        if item not in found:
            found.append(item)

    def exempt(m: "re.Match[str]", *, howto_ok: bool = False) -> bool:
        clause = _clause(s, m.start(), m.end())
        if _negated(s, m.start(), L):
            return True
        if rule and L.condition.search(clause):                  # "Rule: Buy NIFTY when RSI < 30" - a rule subject
            return True
        if howto_ok and _HOWTO_LABEL.match(s) and L.howto.search(s):
            return True
        return False
    for m in L.imperative.finditer(s):
        if exempt(m, howto_ok=True):
            continue
        obj = _object(m.group("rest"), L, s, allow_level=True)
        if obj:
            add(m.group("verb"), obj)
    for pattern in (L.modal, L.gerund):
        for m in pattern.finditer(s):
            if exempt(m, howto_ok=True):
                continue
            obj = _object(m.group("rest"), L, s, allow_level=False)
            if obj:
                add(m.group("verb"), obj)
    for m in L.colon.finditer(s):
        if L.descriptive.match(m.group("after")) or _negated(s, m.start("verb"), L):
            continue
        obj = _instrument_at_end(m.group("obj"), L, gap=0)
        if obj and obj.lower() not in L.share_words:
            add(f"{obj}:", m.group("verb"))
    for m in L.deva.finditer(s):
        if _negated(s, m.start(), L):
            continue
        verb = m.group("verb")
        obj = _instrument_at_end(s[max(0, m.start() - 80):m.start()], L, gap=2) or _instrument_at_start(s[m.end():m.end() + 80], L, allow_level=False)
        if obj:
            add(obj, verb)
    for m in L.deva_light.finditer(s):
        if _negated(s, m.start(), L):
            continue
        obj = _instrument_at_end(s[max(0, m.start() - 80):m.start()], L, gap=0)
        if obj:
            add(obj, m.group("verb"))
    for m in L.opinion.finditer(s):
        obj = _instrument_at_start(m.group("rest"), L, allow_level=False)
        if obj and not _negated(s, m.start(), L):
            add("pick", obj)
    for m in L.rated.finditer(s):
        obj = _instrument_at_end(s[max(0, m.start() - 80):m.start()], L, gap=0)
        if obj and not _negated(s, m.start(), L):
            add(obj, "rated a buy / sell")
    for pattern in L.certainty:
        for hit in pattern.finditer(s):
            if L.past.search(_clause(s, hit.start(), hit.end())) or _negated(s, hit.start(), L):
                continue                                          # past tense in ITS clause only ("as it did last week" is not)
            add("level with certainty", hit.group(0).strip()[:30])
            break
    return found


_TEMPLATE_CONTEXT = re.compile(r"\b(?:per|in|from)\s+(?:the|your|this)\s+(?:template|rule|strategy|plan|backtest)\b|"
                               r"\b(?:template|strategy|rule|plan)'s\b", re.I)


def _paragraph_levels(p: str, L: Lists) -> bool:
    """A stop and a target with price levels, plus an entry level or an instrument, in one paragraph. Not a call when the
    sentence holding the stop or the target is a record, past / backtest text, a template's levels or negated."""
    stops, targets = list(L.stop.finditer(p)), list(L.target.finditer(p))
    if not stops or not targets:
        return False
    bounds = [0] + [m.end() for m in _SENTENCE.finditer(p)] + [len(p) + 1]        # sentence starts, computed once
    seen: set = set()
    for m in stops + targets:
        k = bisect.bisect_right(bounds, m.start()) - 1
        if k in seen:
            continue
        seen.add(k)
        sentence = p[bounds[k]:bounds[k + 1]]
        if (L.past.search(sentence) or record_line(sentence) or _TEMPLATE_CONTEXT.search(sentence)
                or _negated(sentence, len(sentence), L)):
            return False
    if L.entry.search(p):
        return True
    return _has_instrument(p, L)


def detect(text: str, path: str) -> List[str]:
    """The call labels in `text` (module docstring), in order of appearance."""
    if len(text or "") > MAX_CHARS:
        return [f"call: text too long to check (over {MAX_CHARS} characters)"]
    L = lists(path)
    body = L.verb_line.sub(r"\1 ", normalise(text))            # "Buy\nRELIANCE": a verb alone on its line opens the next one
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
        trade_word = re.compile(r"\b(?:buy|sell|short|hold|exit|accumulate|purchase|square\s*off|cover|long)\b", re.I)
        for s in sentences(body):
            plain = set(_sentence_calls(s, L))
            words = len(trade_word.findall(s))
            for reading in deobfuscated(" ".join(s.split())):
                if len(trade_word.findall(reading)) <= words:
                    continue                                    # nothing was disguised as a trade word in this reading
                for item in _sentence_calls(reading, L):
                    if item not in plain and item not in found:
                        found.append(f"{item} (disguised)")
    return found


__all__ = ["detect", "normalise", "lists", "sentences", "MAX_CHARS"]
