"""H-C1 c (review of #230): trade calls aimed at an instrument in English AI text. `call_terms(text)` returns short labels
(empty = no call); the output filter blocks a text with any. The word lists are data (the "calls" block of
`data/advice_terms.json`).

**English only.** ATP answers in English. A text with any Devanagari (or other Indic-script) character is not read: it
answers one label (`SCRIPT_LABEL`) and is withheld - fail closed, never a pattern list per language.

**Normalisation first** (`normalise`): NFKC (full-width and mathematical letters), Unicode format characters removed
(zero-width space / joiner, bidi marks), Latin look-alikes from Cyrillic / Greek mapped (`app.support.normalize`),
markdown emphasis and code marks removed, and list markers, bullets, block quotes, quotes, brackets and pictographs
(emoji) turned into clause breaks. Newlines are kept (paragraphs).

**What is a call** (each one blocked):

* an imperative trade verb opening a clause - at the start, after a sentence end, a list marker, ``,`` ``;`` ``:``,
  `` - `` / ``->`` or a bracket, optionally after "please / then / just / time to ..." - followed (past at most four
  filler words such as "all", "a few shares of") by an instrument, a share word or a price level ("Sell RELIANCE",
  "- Buy: RELIANCE", "Exit all INFY positions", "Buy 25000 CE above 120"); a trade verb on a level when the sentence
  names an instrument ("INFY: weak, short below 1490"); "pick / scoop it up near 1450";
* ``INSTRUMENT: buy`` (not "NIFTY: buy signals were rare");
* a modal call ("I would / I'd / one could buy RELIANCE"), "consider buying RELIANCE", "my pick is RELIANCE",
  "RELIANCE is a buy";
* a stop AND a target with price levels in one paragraph, plus an entry level ("entry", "buy above", "long above") or an
  instrument - across lines and sentences;
* a price level with certainty ("RELIANCE to 3000 for sure", "will surely reach 3000", "three thousand for sure");
* any of the above once a disguised spelling is undone ("B u y RELIANCE", "8uy RELIANCE").

An **instrument** is an upper-case ticker (not a platform or indicator acronym) or a name from the instrument index
(`app.ai.instrument_names`: the instrument master plus a seed), in any case.

**Not calls:** percentages ("stop 1%, target 2%"), status / log / backtest / rule / how-to text ("Status: BUY RELIANCE
filled", "Backtest trade 3: entry 2480, stop 2430, target 2580", "Rule: Buy NIFTY when RSI < 30", "How to: Buy RELIANCE in
paper mode"), "when <condition>" rules, negated sayings ("We never say: Sell RELIANCE"), past-tense certainty ("hit 25000
for sure on Friday") and a candle's price fields ("TCS: close 4050, RSI 62"; `_price_field`). A modal call with a price
level but no instrument ("One could buy the 25000 CE above 120") stays a framed specific call (output_filter), as the
spec allows.

Texts longer than `MAX_CHARS` are not read: they answer one label ("too long to check") and are blocked.
"""
from __future__ import annotations

import bisect
import functools
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import FrozenSet, List, Optional, Pattern, Sequence, Tuple

from app.ai import instrument_names

MAX_CHARS = 40_000
# English only: any Devanagari (or other Indic-script) character withholds the text (fail closed).
_INDIC = re.compile("[\u0900-\u0dff\ua8e0-\ua8ff\u1cd0-\u1cff]")
SCRIPT_LABEL = "call: text not in English (Devanagari / Indic script) - withheld"
_WORD = "\\w"
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
    t = t.translate(_CONFUSABLES).translate(_ARROWS)
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
    # LOW10: [ \t]* after the anchor (a newline is an anchor of its own), so runs of newlines stay linear
    # round 5: "Open the chart and buy RELIANCE" - " and " / " then " open a clause too (an object is still required);
    # round 6: not after "buy" - "buy and hold" / "buy and sell" are verbs of their own (an object is still required, so
    # "Buy and hold NIFTY returned 12%" stays a benchmark's name)
    start = r"(?:^|(?<=[\n.!?;:,(—–])|(?<=\s-)|(?<=->)|(?<=\sand)(?<!buy\sand)|(?<=\sthen))[ \t]*"
    prefix = rf"(?:(?:{_alt(calls.get('clause_prefixes', []))})\s+){{0,2}}"
    # #230 review round 4: a lookahead, so a first verb-shaped word ("Long story short, buy RELIANCE") never consumes the
    # real call after it - finditer then tries every later position too.
    rest = r"(?=(?P<rest>[^\n.!?;]{0,120}))"
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
        # Round 12 (perf): anchored at the separator - the instrument before it is read back from there (`_sentence_calls`),
        # never by a lazy scan from every position ("- a - a ..." floods were quadratic in the verb alternation).
        colon=re.compile(rf"\s*[:\-–—]\s*(?P<verb>{en})(?![{_WORD}-])(?=(?P<after>[^\n.!?;]{{0,40}}))", re.I),
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
        negated=re.compile(rf"(?<![{_WORD}])(?:{_alt(neg.get('en', []))})(?![{_WORD}])", re.I),
        howto=re.compile(r"\b(?:paper mode|in the app|button|page|tab|click|tap|screen|menu|watchlist|symbol box)\b", re.I),
        verb_line=re.compile(rf"(?im)^([ \t]*(?:{en})[ \t]*[:\-–—>=]*)[ \t]*\n(?=[ \t]*\S)"),
    )


# --- instruments --------------------------------------------------------------------------------------------------------

_TOKEN = re.compile(r"[^\s,;:]+")


def _words(text: str) -> List[str]:
    return [w.strip("\"'.,!?;:()[]{}") for w in _TOKEN.findall(text or "")]


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
        if (re.fullmatch(r"\d+(?:\.\d+)?(?:%|pc)|\d+/\d+", w) and nxt == "of") or w in _PART_WORDS:   # "50% of", "half (of)": a quantity
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

_SENTENCE = re.compile(r"(?<!\d)[.!?](?!\d)|\n")
# Clause breaks inside a sentence: the scope of a "when", a negation or a past marker (review of #230: never the sentence).
_CLAUSE = re.compile(r"[,;—–]|\s-\s|\sbut\s|\sand then\s", re.I)
_CLAUSE_OR_COLON = re.compile(_CLAUSE.pattern + r"|:", re.I)
# Words that make a line a call now, not a record or a rule ("buy RELIANCE again tomorrow", "when the market opens").
_NOW = re.compile(r"\b(?:now|today|tomorrow|tonight|again|next|asap|immediately|right away|this week|opens?|open|you|your)\b", re.I)
# A record line: a status / log line with a past or passive status word, a quantity field or an approval state - only a
# line that opens with a label ("Status:", "PAPER order placed:", "Proposal:") and reads like a record is not a call.
_LABEL = re.compile(r"^\s*(?P<label>[^\W\d][\w &'/-]{0,40}?)\s*(?:#?\s*\d+\s*)?:")
_RECORD = re.compile(r"\b(?:placed|filled|executed|drafted|bought|sold|closed|exited|cancelled|canceled|rejected|pending|awaiting|"
                     r"needs your approval|squeezed|triggered)\b|\b\d+\s*(?:shares?|qty|lots?|units?)\b|\bqty\s*[:=]?\s*\d+|"
                     r"\bx\s*\d+\b|\b(?:BUY|SELL)\s+\d+\s+[A-Z][A-Z0-9&-]+\s+@?\s*(?:at\s+)?₹?\d")
# A continuation of a record line: a quantity or an approval state after the order ("..., 10 shares, awaiting your approval").
_RECORD_FIELD = re.compile(r"^\s*(?:\d+\s*(?:shares?|qty|lots?|units?)|qty\s*[:=]?\s*\d+|x\s*\d+|awaiting (?:your )?approval|"
                           r"needs your approval|pending|filled|placed|paper)\b[^\n]{0,30}$", re.I)
# Record lines open with one of these labels (review of #230, round 3: a fixed set, never "Summary:" / "Takeaway:").
_RECORD_LABELS = {"status", "order", "your order", "order status", "paper order", "paper order placed", "order placed",
                  "order filled", "proposal", "drafted", "log", "trade log", "signal log", "journal", "fill", "filled", "executed", "side"}
_FIELD_LABELS = {"side"}                                          # a field of an order record: the line is the record itself
_LOG_LABELS = {"backtest", "backtest trade", "back-test", "trade log", "log", "journal", "history", "trade", "simulated trade"}
_RULE_LABELS = {"rule", "rules", "entry rule", "exit rule", "strategy", "template", "condition", "setup rule"}
# A rule's condition: an indicator or a price comparison ("when RSI < 30", "when the close crosses above the 20-day high").
_RULE_CONDITION = re.compile(r"\bwhen\b[^\n]*?(?:[<>=]|\b(?:rsi|sma|ema|macd|adx|atr|vwap|close|price|crosses?|above|below|breaks?|"
                             r"high|low|volume|moving average|supertrend|bollinger)\b)", re.I)
_HOWTO_LABEL = re.compile(r"^\s*how[ -]?to\b", re.I)
_NOUN_PHRASE = re.compile(r"^\s*(?:\S+\s+){0,1}?(?:was|were|is|are|has|have|had|remain|remained|stay|stayed|appear|appeared|"
                          r"get|got|shown|listed|rose|rises|fell|falls|jumped|dropped|climbed|surged|slipped|declined|gained|lost|"
                          r"eased|rallied|spiked|doubled|halved|increased|decreased|returned|returns|outperformed|underperformed|beat|lagged|"
                          r"delivered|earned|yielded)\b", re.I)
_PRONOUN_OBJECT = re.compile(r"^\s*(?:(?:some|a|bit|little|few)\s+)*(?:more|it|them|this|that|those|these)?\s*(?:now|here|today)?\s*$", re.I)
_PRONOUN_ALWAYS = re.compile(r"^\s*(?:it|them|this one|that one)\s*(?:now|here|today|too|again)?\s*$", re.I)
_WATCHLIST_TAIL = re.compile(r"^\s*to\s+(?:your|the|a|my)?\s*(?:watch\s*list|watchlist|chart|list|screen|screener|alerts?|alert rule|"
                             r"backtest|strategy|template|comparison(?: panel| view)?|panel)\b", re.I)
# App how-to wording: the instrument names a page / chart / view of the app, not a trade ("Exit the RELIANCE page",
# "Grab the RELIANCE chart from the Charts tab", "Open RELIANCE in a new tab").
_UI_TAIL = re.compile(r"^\s*(?:'s\s+)?(?:chart|page|tab|view|panel|card|screen)s?\b|"
                      r"^\s*(?:from|in|on|for)\s+(?:the|your|a|this)?\s*(?:[\w']+\s+){0,3}(?:view|page|tab|panel|screen)\b", re.I)
# The how-to exemptions above apply only to app verbs, never to a trade verb or with anything about now (round 5).
@functools.lru_cache(maxsize=256)
def _acts_now(s: str) -> bool:
    """Whether sentence `s` says "take it / act now" anywhere - once per sentence, not once per verb."""
    return bool(_ACT_NOW.search(s))


_HOWTO_NOW = re.compile(r"\b(?:now|today|tomorrow|tonight|asap|immediately|right away|this (?:week|session|expiry)|intraday|eod|"
                        r"end of (?:the )?day|(?:before|by|into|at|near) (?:the )?(?:close|open|bell|results|earnings|expiry|event|"
                        r"announcement|eod|end of (?:the )?day|weekend))\b", re.I)
_HOWTO_VERBS = frozenset({"add", "grab", "open", "view", "plot", "exit", "hold", "pin", "show", "load", "remove", "close"})
# Round 6: exit / close / hold are trades on their own ("Close INFY from the positions tab" is a sell). They keep only the
# "the RELIANCE page / chart" exemption, where the page itself is the object, never "from / in / on / for the ... tab".
_TRADE_HOWTO_VERBS = frozenset({"exit", "close", "hold"})
# A page of the book (positions, holdings, orders, portfolio) never makes a trade verb an app instruction.
_BOOK_PAGE = re.compile(r"\b(?:positions?|holdings?|orders?|order book|portfolio|trades?|tradebook|trade book|ticket)\b", re.I)
# "Open" is a trade only with a side or a position ("Open RELIANCE long", "Open a position in INFY"); "Open RELIANCE" alone
# is the app (the multi-word "open a long in" / "open long" verbs catch the rest).
# Round 7: close / open / hold followed by a level, a number or "price / of" are the bar's fields ("TCS: close 4050",
# "Close price: INFY 1500", "hold above 1650 kept the trend"), in the imperative and the "TICKER: verb" forms alike.
_PART_WORDS = frozenset({"half", "quarter", "part", "rest", "remainder", "third"})
_FIELD_VERBS = frozenset({"close", "open", "hold"})
# Round 8: a preposition counts only before a price or a named level, "of" only before a number, and a number only when it is
# not a quantity ("close 50% / 2 lots / 100 shares" is an order); a copula or comparative after the verb is a recap
# ("TCS: close was flat", "open gap-up, close higher", "Close/Open ratio").
_PRICE_FIELD = re.compile(
    r"^[\s,:\-–—]*(?:"
    r"(?P<prep>at|above|below|near|over|under|around|in|within)\s+(?:(?:a|an|the|its|record)\s+)?(?:₹|\d|(?:\d+[\s-]*(?:day|week|month|period|session)s?\s+)?"
    r"(?:sma|ema|dma|wma|vwap|high|low|52[\s-]*week|average|moving average|band|level|support|resistance|range|record|"
    r"day'?s|week'?s|session'?s|top|bottom|upper|lower|quartile|half)\b)"
    r"|(?P<of>of\s+₹?\d)"
    r"|(?P<pct>\d[\d,.]*\s*%\s+(?:above|below|higher|lower|up|down|over|from)\b)"
    r"|(?P<bare>₹?\d+(?:,\d{2,3})*(?:\.\d+)?(?!\s*(?:%|percent\b|per\s*cent\b|pc\b|shares?\b|lots?\b|qty\b|quantity\b|units?\b|contracts?\b|nos\b|"
    r"stocks?\b|of\b|/\s*\d|lakhs?\b|crores?\b|k\b|\d|\.\d|,\d)))"
    r"|(?P<cop>(?:was|is|were)\s+(?:flat|unchanged|higher|lower|up|down|above|below|near|at|around|₹?\d|the\s+(?:highest|lowest)))"
    r"|(?P<pair>and\s+(?:open|close|high|low|volume)\b)"
    r"|(?P<label>(?:price|prices|level|levels|ratio)\b|/)"
    r"|(?P<word>(?:unchanged|flat|higher|lower|gap|gapped)\b)"
    r")", re.I)

# --- what may follow a price field (review rounds 10-12) ----------------------------------------------------------------
# Round 12: a structural reading, not an open whitelist. Every tail is a closed form (or a bounded one the advice / order
# checks read whole), and three checks run on EVERYTHING after the field, in every clause of its sentence:
#   * an advice / order word (`_ADVICE_HARD`), or an advice adjective that does not describe a level or a candle
#     (`_ADVICE_ADJ`: "a good sign", "key support" and "the best close" describe; "is a smart move", "is the safest" advise);
#   * a later clause that opens a condition, a purpose or an appositive verdict (", if it breaks", ", to be safe",
#     ", the obvious choice");
#   * a subordinate clause (as / since / while / amid / despite / after / post / following / because) must report the past.
# "close / open N" (a bare value) is the bar's field; "close / open at / above N" is order-shaped and needs past-tense
# evidence after a subordinator; "hold above / at N" is an order unless a predicate follows it ("hold above 4000 is intact",
# "... kept the trend", "... since Monday") or it is "hold of N".
_N = r"₹?\d[\d,]*(?:\.\d+)?"
_WORD_ = r"(?:[^\s,;]|,(?=\d))+"
_MORE = rf"(?:\s+{_WORD_}){{0,8}}"                                  # a bounded rest: the advice / order checks read it whole
_DAYS = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_MONTHS = r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
_SPAN = r"sessions?|days?|weeks?|months?|years?|quarters?|bars?|candles?"
_COUNT = r"\d+|two|three|four|five|six|seven|eight|nine|ten|a\s+few|several"
_LEVEL_NOUN = (r"close|closes|closing|open|opening|high|highs|low|lows|level|levels|support|resistance|sign|signs|candle|candles|print|"
               r"bar|session|day|week|month|quarter|year|gain|gains|rally|reversal|breakout|breakdown|base|zone|area|volume|"
               r"start|finish|showing|reading|week's|day's|month's|year's|top|bottom|peak|trough|record|test|retest|run|streak")
# Advice adjectives: advice unless a level / candle noun follows (one word may sit between) or a period possessive leads
# ("the week's best"); "fine" only as the whole complement of a bare field ("INFY: close 1500 is fine.").
_ADVICE_ADJ = re.compile(r"\b(?:wise|wisest|advisable|recommended|prudent|ideal|optimal|preferable|preferred|suggested|safe|safer|safest|"
                         r"needed|key|good|great|fine|best|better|smart|smarter|smartest|sensible|sound|obvious|easiest|simplest|"
                         r"cleanest|surest|clear)\b", re.I)
_ADVICE_FOLLOWED = re.compile(rf"\s+(?:[\w%.-]+\s+)?(?:{_LEVEL_NOUN}|results|earnings|numbers|data|news|guidance|performers?|"
                             r"performance|cues|demand|deal\s+wins|wins|quarter|show)\b|(?<=smart)\s+money\b|"
                             r"\s+than\s+(?!\w+ing\b)", re.I)
_PERIOD_POSSESSIVE = re.compile(r"(?:week|month|day|session|year|quarter|decade)'s\s+$", re.I)
_ADVICE_HARD = re.compile(
    r"\b(?:a must|worth it|makes sense|my pick|our pick|best bet|no[- ]?brainer|you|your|yours|plans?|strateg(?:y|ies)|"
    r"hedge(?!\s+funds?\b)|hedging|precaution(?:ary)?|protect(?:s|ing)?|as long as|while you can|choice|"
    r"step(?!\s+(?:up|down|higher|lower)\b)|entry|(?<!net\s)(?:re-?)?(?:buy|sell)(?!-(?:side|off)\b)|re-?enter|accumulate|"
    r"add|average\s+(?:down|up)|trim|(?:as|for|on)\s+(?:a\s+|the\s+)?confirmation|"
    r"the\s+right\s+(?:call|move|thing|choice|time|way|step|play|trade))\b", re.I)
# Instructions inside the field's own clause ("... below the 20-DMA and walk away", "... if it breaks", "... for upside").
_IN_CLAUSE_ORDER = re.compile(
    r"\band\s+(?:then\s+)?(?:add|buy|sell|exit|book|hold|wait|stay|remain|keep|walk|move|ride|lock|take|avoid|consider|switch|rotate|"
    r"reduce|trim|cut(?!\s+(?:its|their)\b)|go|re-?enter|re-?buy|step)\b|\b(?:or|else|otherwise)\s+(?:lose|regret|risk|miss|face|get)\b|"
    r"\b(?:if|unless|in\s+case|until|till|so\s+that|in\s+order\s+to)\b|\b(?:before|ahead\s+of)\s+(?!(?:that|then)\b)\w|"
    r"\bfor\s+(?:more\s+|further\s+|the\s+|some\s+)?(?:upside|downside|gains?|profits?|safety|protection|a\s+(?:quick|fast|short|bounce|"
    r"trade|swing|move|rise|rally|target))\b|\bfor\s+(?:exiting|selling|buying|booking|adding|entering|holding|accumulating|shorting)\b|"
    r"\b(?:booking|taking|locking)\s+(?:in\s+)?(?:profits?|gains?)\b|\bworth\s+(?![₹\d])|\bopportunity\b|"
    r"\b(?:go|going|stay|staying)\s+(?:long|short)\b", re.I)
_PURPOSE = re.compile(r"\bto\s+(?:protect|stay|be\s+safe|keep|lock|limit|avoid|cut|reduce|play|book|secure|guard|preserve|save|"
                      r"manage|control|minimi[sz]e|cap|stop|exit|trim|exit|enter|catch|ride|capture|bank|benefit|gain|profit)\b", re.I)
# A later clause that is a condition, a purpose, an instruction or a verdict on the action, never a recap.
_ORDER_CLAUSE = re.compile(
    r"^\s*(?:if|unless|in\s+case|until|till|before|ahead\s+of|or(?!\s+(?:₹?\d|about|nearly|roughly|almost|around)\b)|else|otherwise|"
    r"so(?!\s+(?:far|the|that\s+the)\b)|then(?![-\w])(?!\s+₹?\d)|please|"
    r"just(?!\s+(?:above|below|under|over|shy|short\s+of|off|at|near|around|₹?\d|\d|up|down|higher|lower)\b)|"
    r"no\s+(?:hesitation|questions?(?:\s+asked)?|doubt|doubts|delay|second\s+thoughts|excuses|ifs)|full\s+stop|period\s*$|"
    r"end\s+of\s+(?:story|discussion)|without\s+(?:hesitation|fail|delay|question)|"
    r"to\s+(?!₹?\d)[a-z]|(?:and\s+)?(?:then\s+(?!₹?\d)[a-z]|add|buy|sell|exit(?!\s+polls?\b)|book\s+(?:profits?|gains?|it)|hold|wait|stay|"
    r"remain|keep|walk|move|ride|lock|take|avoid|consider|look\s+to|prefer|try|go|let|switch|rotate|reduce|trim|cut)\b|"
    r"(?:(?:that|it|this)(?:'s|\s+is)\s+)?(?:(?:the|a|an|my|our|your)\s+(?:[\w-]+\s+){0,2}|(?:right|smart|good|wise|great|safe|"
    r"safest|sensible|prudent|obvious|easy|clear|best)\s+)(?:call|play|way|trade|move|plan|pick|"
    r"bet|idea|step|choice|option|thing|approach|course|decision)\s*$)", re.I)
# A verdict on the action in the next clause or sentence ("INFY: close at 1500. That is the plan.").
_VERDICT = re.compile(
    r"^\s*(?-i:[Tt]hat|[Tt]his|[Ii]t)(?:'s|\s+(?:is|was|would\s+be|remains|seems|looks))\s+(?:(?:the|a|an|my|our)\s+)?(?:[\w-]+\s+){0,2}?"
    r"(?:plan|call|trade|move|idea|play|way|step|choice|pick|advice|view|strategy|bet|thing\s+to\s+do|wise|prudent|advisable|"
    r"recommended|ideal|optimal|best|smart|sensible|safest|safer|right)\b", re.I)
# Subordinate clauses: present tense (a reason, a condition now) never reports a bar; past evidence (a past-tense verb, a
# past time) does. An order-shaped field (at / above N, or hold) needs the evidence; a bare field ("close 4050 after
# results", "close 4050 amid sector weakness") only needs the present tense absent.
_SUBORDINATE = re.compile(r"^(?P<lead>as\s+of|as|since|while|whilst|amid|amidst|despite|after|post|following|because|given)\s+(?P<body>.*)$",
                          re.I | re.S)
_PRESENT = re.compile(r"\b(?:is|are|am|isn't|aren't|has|have|hasn't|haven't|holds|remains|stays|looks|seems|keeps|trades|sits|"
                      r"stands|can|can't|cannot|will|won't|shall|should|must|could|would|may|might|do|does|need|needs|want|wants|"
                      r"let|lets|(?:it|that|this|he|she|there|what|which|they|you|we)'(?:s|re|ll|d))\b", re.I)
_PAST_VERB = re.compile(
    r"\b(?:(?!(?:need|feed|speed|seed|breed|indeed|hundred|embed|shed)\b)\w{2,}(?<!e)ed|rose|fell|ran|broke|came|made|saw|hit|"
    r"held|led|won|lost|beat|met|slid|sank|shrank|grew|cut|took|went|did|was|were|had|sold|bought|gave|got|set|"
    r"ago|yesterday|earlier|last\s+(?:week|month|session|year|quarter|time|{days})|previous|prior)\b".replace("{days}", _DAYS), re.I)
_SESSION_NOUN = re.compile(
    rf"\b(?:{_DAYS}|gap(?:[- ]?(?:up|down))?|gapped|(?:{_COUNT})\s+(?:{_SPAN})|\d{{1,2}}\s+(?:{_MONTHS})\w*|(?:{_MONTHS})\w*\s+\d{{1,2}}|"
    r"the\s+(?:open|opening|bell|start|lows?|highs?|breakout|breakdown))\b", re.I)
_SINCE_LEADS = frozenset({"since", "as of"})
# After a bare value, "after / amid / despite" may take a closed noun phrase naming an event or a market condition.
_NOUN_LEADS = frozenset({"after", "post", "following", "amid", "amidst", "despite"})
_EVENT_NP = re.compile(
    r"(?:(?:the|a|an|its|their|q[1-4]|quarterly|weak|weaker|strong|stronger|sector|broad|broader|market|global|heavy|light|fii|dii|"
    r"mixed|firm|soft|upbeat|poor|good|bad|it|bank|index|sectoral|overall|foreign|domestic|institutional|profit|gap-up|gap-down|"
    r"wider|widespread|early|late|intraday|monthly|weekly|last-hour|final-hour|closing|afternoon|morning|sharp|late-day)\s+){0,3}"
    r"(?:results|earnings|numbers|data|news|guidance|commentary|weakness|strength|markets?|selling|buying|volatility|cues|"
    r"gap(?:[- ]?(?:up|down))?(?:\s+open)?|selloff|sell-off|rally|bounce|dip|flows|outflows|inflows|expiry|announcement|update|"
    r"booking|pressure|demand|supply|tone|sentiment|trade|session|open|opening|holiday)"
    rf"(?:\s+(?:on\s+(?:{_DAYS})|yesterday|today|last\s+\w+|this\s+(?:week|month|morning)|on\s+\d{{1,2}}\s+(?:{_MONTHS})\w*))?", re.I)
_AFTER_LEADS = frozenset({"after", "post", "following"})
# After a bare value only (never after "close at N", where they read as a purpose): "to end the week up 2%", "to gain 1.2%
# on the day", "to cap a 3-session rally", "before results", "until the last-hour sell-off".
_STREAK = r"(?:\d+|two|three|four|five|six|seven)[- ](?:session|day|week|month)\s+(?:\w+\s+)?(?:rally|slide|streak|run|fall|decline|rise|climb)"
_RECAP_INF = re.compile(
    rf"\bto\s+(?:(?:end|finish|close|settle)\s+(?:the\s+(?:week|session|day|month|year|quarter)\b(?:\s+(?:up|down|higher|lower|flat)\b)?|"
    rf"{_N}\s*%?\s+(?:up|down|higher|lower)\b|(?:up|down|higher|lower|flat)\b)"
    rf"|(?:gain|lose|shed|rise|fall|climb|slip|add)\s+{_N}\s*(?:%|points?|pts)"
    rf"|(?:cap|stop|snap|end|extend|halt|break)\s+(?:a|its|the)\s+{_STREAK}"
    r"|extend\s+(?:its\s+|the\s+)?(?:gains?|losses|rally|slide|streak)\b)", re.I)
_EVENT_TIME = re.compile(rf"\b(?:before|ahead\s+of|until|till)\s+(?:{_EVENT_NP.pattern})", re.I)


def _subordinate_ok(lead: str, body: str, strict: bool) -> bool:
    """Whether "<lead> <body>" after a price field reports the past ("as IT rallied", "since Monday", "after 3 sessions
    below"), never a reason or a condition now ("as it is weak", "since the trend is up", "while you can")."""
    lead = re.sub(r"\s+", " ", lead.lower())
    if len(body.split()) > 10 or _PRESENT.search(body) or lead in ("because", "given"):
        return False                                            # a reason is never a recap tail (as / since + a reason
    if not strict and lead in _NOUN_LEADS and _EVENT_NP.fullmatch(body.strip(" .")):
        return True                                             # "close 4050 after results / amid sector weakness"
    if _PAST_VERB.search(body):
        return True
    if lead in _SINCE_LEADS:
        return bool(_SESSION_NOUN.search(body))
    if lead in _AFTER_LEADS:                                    # "after 3 sessions below", "after a gap-up" - never "after
        return bool(re.search(rf"\b(?:gap(?:[- ]?(?:up|down))?|(?:{_COUNT})\s+(?:{_SPAN}))\b", body, re.I))   # results"
    return False


_COPULA = r"(?:is|was|were|has\s+been|had\s+been|remains|remained|stays|stayed)"
# A complement noun that reports the bar ("is a 3-week high", "is a strong close", "is a good sign"), never "is the level /
# the plan / the move", which name what to do.
_COMP_NOUN = (r"close|closes|closing|open|opening|high|highs|low|lows|record|candle|print|bar|gain|loss|rally|reversal|breakout|"
              r"breakdown|top|bottom|peak|trough|sign|streak|run|base|finish|session|day|week|month|quarter|year|showing|start|week's\s+\w+|day's\s+\w+|month's\s+\w+")
_SUPERLATIVE = (r"highest|lowest|strongest|weakest|biggest|largest|smallest|widest|narrowest|tightest|steepest|sharpest|deepest|"
                r"longest|shortest|best|worst|firmest|softest")
_SCOPE = rf"(?:\s+(?:in|of|for|since|over|across|this|on|among|ever|yet|so\s+far|of\s+the)\b{_MORE})?"
_COMPLEMENT = (
    rf"(?:the\s+(?:(?:week|month|day|session|year|quarter)'?s\s+)?(?:{_SUPERLATIVE})(?:\s+(?:[\w-]+\s+)?(?:{_LEVEL_NOUN}))?{_SCOPE}"
    rf"|(?:the|a|an)\s+(?:[\w%.,'-]+\s+){{0,2}}(?:{_COMP_NOUN}){_MORE}"
    rf"|(?:a|an)\s+{_N}\s*%?\s+(?:move|gain|loss|rise|fall|drop|jump|slide){_MORE}"
    rf"|(?:key|major|strong|firm|immediate|near-term|minor|crucial|important)\s+(?:support|resistance|level|pivot)\b{_MORE}"
    r"|(?:intact|flat|unchanged|higher|lower|up|down|above|below|near|at|tested|broken|breached|held|lost|reclaimed|confirmed|seen|"
    rf"recorded|reached|hit|rejected|retested|weak|weaker|strong|stronger|firm|firmer|steady|muted|volatile|in\s+place|bullish|bearish|"
    rf"positive|negative|constructive|encouraging|notable|significant)\b{_MORE}"
    rf"|{_N}{_MORE})")
# Predicates that make the field the subject of a report ("hold above 1650 kept the trend", "close above 2500 should
# confirm the breakout"): the only tails a "hold above / at N" may take, besides "since <a past time>".
_PREDICATE = (
    rf"{_COPULA}\s+{_COMPLEMENT}"
    r"|(?:should|would|could|might|may)\s+(?:confirm|invalidate|signal|mark|indicate|show|suggest)\b" + _MORE +
    r"|(?:would|could|might|may)\s+be\s+(?:bullish|bearish|positive|negative|constructive|significant|a\s+(?:breakout|breakdown|signal))\b" + _MORE +
    r"|(?:kept|held|lasted|marked|marks|came|confirm\w*|invalidat\w*|signal\w*|show\w*|set|made|hit|printed|stayed|remained|ended|"
    r"finished|rose|fell|gained|lost|matters|counts|stands|holds|continues|continued|failed|broke|gave\s+way|faded|reversed|"
    r"capped|limited|defined|survived|cracked|gave|came)\b" + _MORE)
# "hold above 4000 for 5 sessions" is an order (hold it for five sessions); "... for 5 sessions so far" and "... for the
# last 5 sessions" report one.
_HOLD_TAIL = re.compile(rf"(?:{_PREDICATE}"
                        rf"|(?:for|over|in)\s+(?:{_COUNT})\s+(?:{_SPAN})\s+(?:so\s+far|running|straight|in\s+a\s+row)"
                        rf"|(?:for|over|in)\s+the\s+(?:last|past|previous)\s+(?:{_COUNT})\s+(?:{_SPAN}))", re.I)
_PER = (r"(?:as\s+per|per|according\s+to)\s+(?:the\s+)?(?:[\w'-]+\s+){0,2}?(?:nse|bse|exchange|bhavcopy|data|feed|filings?|quotes?|"
        r"tape|terminal|vendor|records?|figures?|numbers?)"
        r"|in\s+line\s+with\s+(?:the\s+)?(?:[\w'-]+\s+){0,2}?(?:sector|index|market|markets|peers?|benchmark|nifty|sensex|street|"
        r"estimates?|expectations?|consensus|trend|pack|group|bank\s+nifty|banknifty)")
_RECAP_TAIL = re.compile(
    r"(?:"
    r"|₹?\d[\d,.]*\s*%?"
    r"|(?:at|near|around)\s+₹?\d[\d,.]*\s*%?"
    r"|(?:by|within)\s+₹?[\d.,]+(?!\s*(?:days?|sessions?|hours?|hrs?|pm|am|mins?|minutes?|o'?clock)\b)\s*(?:%|points?|pts)?" + _MORE +
    r"|(?:quartile|half|decile|percentile|band|zone|range|end)\b" + _MORE +
    r"|(?:support|resistance|level|zone|area|floor|ceiling|moving average|average)\b" + _MORE +
    r"|(?:above|below|near|inside|within|under|over)\s+(?:the|its|a)?\s*(?:\d+[- ]?)?(?:-?(?:day|week|month|period|session)s?\s+)?"
    r"(?:dma|sma|ema|wma|band|range|average|moving average|high|low|vwap|upper band|lower band|cloud|channel|box)\b" + _MORE +
    r"|(?:near|above|below|at|around)\s+(?:the\s+)?(?:consensus|street|analysts?'?|brokerage)\s+(?:target|estimate)s?(?:\s+price)?" +
    rf"|from\s+{_N}\s*%?(?:\s+(?:a\s+(?:day|week|month|year)\s+ago|\d+\s+(?:{_SPAN})\s+ago|last\s+\w+|yesterday|earlier|on\s+\w+|"
    r"the\s+(?:previous|prior)\s+\w+|in\s+\w+))?"
    r"|now\s+(?:sits|trades|stands|at|above|below|near|up|down|higher|lower|" + _N + r"\s*%?\s+(?:above|below|higher|lower|up|down|over|from))\b" + _MORE +
    r"|on\s+a\s+gap(?:[- ]?(?:up|down))?\b" + _MORE +
    r"|on\s+\d+(?:\.\d+)?x?\s*(?:volume|times)?"
    r"|(?:again|once\s+more|twice|thrice)(?:\s+(?:this|last)\s+(?:week|month|session|year|quarter))?"
    r"|(?:\w+)\s+(?:days|sessions)\s+running"
    rf"|{_PER}"
    rf"|[-–—]\s*(?:a|an|the)\s+(?:[\w%.,'-]+\s+){{0,3}}(?:high|low|record|gain|loss|close|level|top|bottom|peak|trough|print|"
    r"breakout|breakdown|streak|run|rise|fall|drop|move)"
    r"|(?:for|over|across|during|in|on)\s+(?:the|this|that)\s+(?:week|session|day|month|year|quarter)\b" + _MORE +
    r"|all\s+(?:day|session|week)\b" + _MORE +
    r"|at\s+the\s+bell\b" + _MORE +
    r"|with\s+(?:rsi|volume|volumes|oi|macd|adx|atr|vwap|pcr|delivery)(?:\s*\(?\d+\)?)?(?:\s+(?:up|down|at|above|below|near|of|over|under|"
    r"rising|falling|higher|lower|flat|steady))?(?:\s+₹?\d[\d,.]*\s*[%x]?)?"
    r"|in\s+(?:a|the)\s+(?:\w+\s+)?(?:range|band|zone|channel|box)\b" + _MORE +
    r"|(?:its|the|a)\s+(?:\S+\s+){0,3}(?:price|high|low|level|average|close|open)\b" + _MORE +
    r"|(?:on|with)\s+(?:[\w-]+\s+){0,2}volumes?\b" + _MORE +
    r"|and\s+(?:held|closed|ended|finished|rose|fell|gained|lost|trimmed|extended|snapped|capped|added|shed|slipped|climbed|stayed|"
    r"remained|marked|posted|logged|(?:cut|hit|set|put)\s+(?:its|their))\b" + _MORE +
    r"|(?:(?:up|down)(?!\s+(?:to|till|until)\b)|higher|lower|flat|unchanged|green|red)\b" + _MORE +
    r"|(?:vs\.?|versus|compared\s+(?:to|with))\s+(?!(?:holding|selling|buying|exiting|staying|waiting|adding|booking)\b)" + _WORD_ + _MORE +
    r"|against\s+(?:₹?\d|the|its|last|yesterday|previous|prior|a\b)" + _WORD_.replace("+", "*") + _MORE +
    r"|(?:for|over|across|in|during)\s+(?:the\s+)?(?:last|past|previous|first|second|third|fourth|fifth|\d+)\s*"
    r"(?!more\b|next\b)(?:\w+\s+)?(?:sessions?|days?|weeks?|months?|bars?|candles?)\b" + _MORE +
    r"|(?:today|yesterday|last\s+\w+|this\s+morning|on\s+\S+\s+(?:results|earnings|expiry)\s+day\b" + _MORE + r")"
    r"|(?:and\s+)?(?:open|close|high|low|volume|vwap|rsi|macd|adx|atr|oi|pcr|sma|ema|dma|range)\b" + _MORE +
    r"|to\s+₹?\d[\d,.]*\s+range\b" + _MORE +
    rf"|{_PREDICATE}"
    r")", re.I)
# Only after a bare value ("close 4050"), never after an order-shaped field or "hold": a gain is a target there.
_BARE_TAIL = re.compile(
    rf"(?=[^\d]*\d)for\s+a\s+(?:{_N}\s*%\s+)?(?:(?:daily|weekly|monthly|quarterly|yearly|session|day's|week's|\d+-(?:day|week|session))\s+)?"
    rf"(?:gain|loss|rise|fall|drop|decline|advance)(?:\s+of\s+{_N}\s*%?)?"
    r"|is\s+fine"
    rf"|(?:{_RECAP_INF.pattern})" + _MORE + rf"|(?:{_EVENT_TIME.pattern})" + _MORE, re.I)
# Order words anywhere in the sentence ("INFY: close at 1500, then re-enter at 1450", "... !").
_FIELD_ORDER = re.compile(r"!|\b(?:(?P<target>target)(?![-\w])(?!\s+(?:price|date)\b)|sl\s*(?:at\s*|of\s*|:\s*)?₹?\d|stop[- ]?loss|stop\s+(?:at\s*|of\s*|:\s*)?₹?\d|"
                          r"now(?!\s+(?:sits|trades|stands|at|above|below|near|up|down|higher|lower|\d))|then\s+(?:re-?enter|buy|sell|exit|add|short|book|hold|close|go|take)|re-?enter|do it|"
                          r"don'?t sell|do not sell|exit(?!\s+polls?)|stop[- ]?loss|book (?:profits?|gains?|losses?)|take (?:profits?|gains?)|"
                          r"lock (?:in )?(?:gains?|profits?)|ride it|immediately|asap|right away|urgently|pronto|at once|"
                          r"without delay|quickly|tomorrow|tonight|if you (?:own|hold|have)|you should|you must|better to|"
                          r"(?:can|will|could|may|should)\s+(?:go|move|head|rally|run|climb)\s+(?:to|towards?|till))\b", re.I)
_FIELD_PAIR = re.compile(r"\b(?:open|high|low|close|at|near|above|below)\s+₹?\d", re.I)
_FIELD_CONNECTORS = frozenset({"on", "in", "with", "all", "your", "the", "of", "-", "–", "—", "for", "my", "our", "this", "and"})
# Round 12 (MED-1): a target names a third party's number ("analysts see a target of 1600", "brokerage target 4500") only
# outside the field's own clause; anywhere else it is the call's target.
_THIRD_PARTY = re.compile(r"\b(?:analysts?|brokerages?|brokers?|consensus|street|research\s+houses?|estimates?)\b", re.I)
_SEMI_CLAUSE = re.compile(r";|\n|[.?!](?!\d)")


_RECAP_LISTS: "dict[str, bool]" = {}


def _recap_list(s: str, L: "Lists") -> bool:
    """Whether sentence `s` lists several fields or instruments ("Open 1490, close 1500 in INFY"; "near 1500 on INFY, near
    4050 on TCS") - a recap, once per sentence."""
    hit = _RECAP_LISTS.get(s)
    if hit is None:
        hit = (len(_FIELD_PAIR.findall(s)) >= 2                      # each instrument with its own value, never one shared level
               or len(re.findall(r"\d[\d,.]*\s+(?:for|on|in)\s+[A-Z][A-Z0-9&]+", s)) >= 2)
        if len(_RECAP_LISTS) > 256:
            _RECAP_LISTS.clear()
        _RECAP_LISTS[s] = hit
    return hit


@functools.lru_cache(maxsize=256)
def _semi_clauses(sentence: str) -> Tuple[int, ...]:
    """The end offsets of the ;-clause breaks of `sentence`, once per sentence."""
    return tuple(b.end() for b in _SEMI_CLAUSE.finditer(sentence))


def _semi_clause(sentence: str, at: int) -> Tuple[int, int]:
    """The (start, end) of the ;-clause of `sentence` holding offset `at`."""
    ends = _semi_clauses(sentence)
    i = bisect.bisect_right(ends, at)
    return (ends[i - 1] if i else 0), (ends[i] - 1 if i < len(ends) else len(sentence))


@functools.lru_cache(maxsize=256)
def _order_hits(sentence: str) -> Tuple[Tuple[int, bool], ...]:
    """The order words of sentence `sentence` as (offset, third party?) - once per sentence, not once per field. The flag
    is True for a target whose ;-clause names a third party (analysts, a brokerage, consensus)."""
    hits = []
    for m in _FIELD_ORDER.finditer(sentence):
        third = False
        if m.group("target"):
            lo, hi = _semi_clause(sentence, m.start())
            third = bool(_THIRD_PARTY.search(sentence[lo:hi]))
        hits.append((m.start(), third))
    return tuple(hits)


def _orders_in(sentence: str, at: int = -1) -> bool:
    """Whether sentence `sentence` holds an order word for a field at offset `at`: a third party's target in another
    ;-clause does not count ("TCS: close 4050; brokerage target 4500 unchanged")."""
    if not sentence:
        return False
    hits = _order_hits(sentence)
    if not hits or at < 0:
        return bool(hits)
    if all(not third for _, third in hits):
        return True
    lo, hi = _semi_clause(sentence, at)
    return any(not third or lo <= start < hi for start, third in hits)


_CLAUSE_END = re.compile(r";|[.?!](?!\d)")
_SENTENCE_END = re.compile(r"[.?!](?!\d)")


@functools.lru_cache(maxsize=1024)
def _advice_after(rest: str, bare: bool) -> bool:
    """Whether the field's own ;-clause in `rest` (the text after a price field) advises: an advice / order word, an
    advice adjective that does not describe a level, a candle, an event or a comparison, a superlative verdict ("is the
    safest"), a purpose or an instruction. Later ;-clauses are a recap's own ("; the company plans a buyback") and get
    the clause checks (`_later_clause_order`) only."""
    end = _CLAUSE_END.search(rest)
    own = rest[:end.start()] if end else rest
    if bare:                                                    # "to end the week up 2%", "up 2% before results"
        own = _EVENT_TIME.sub(" ", _RECAP_INF.sub(" ", own))
    if _ADVICE_HARD.search(own):
        return True
    for m in _ADVICE_ADJ.finditer(own):
        word = m.group(0).lower()
        if _ADVICE_FOLLOWED.match(own, m.end()) or _PERIOD_POSSESSIVE.search(own[max(0, m.start() - 20):m.start()]):
            continue                                            # "a good sign", "key support", "the week's best"
        if word == "fine" and bare and re.fullmatch(r"\s*is\s*", own[:m.start()]) and not own[m.end():].strip(" ."):
            continue                                            # "INFY: close 1500 is fine." - the whole complement
        return True
    if _PURPOSE.search(own) or _IN_CLAUSE_ORDER.search(own):
        return True                                             # "... to protect capital", "... to stay safe"
    if re.search(rf"\b(?:is|=)\s+the\s+(?!{_SUPERLATIVE}\b)\w+est\b(?!\s+(?:[\w-]+\s+)?(?:{_LEVEL_NOUN})\b)", own, re.I):
        return True                                             # "is the cleanest": a verdict on the action
    return False


def _later_clause_order(clauses: Sequence[str], strict: bool) -> bool:
    """Whether a later clause after a price field is a condition / purpose / verdict, or a subordinate clause that does
    not report the past (", since it is weak")."""
    return any(_clause_order(c.strip(" ."), strict) for c in clauses)


@functools.lru_cache(maxsize=2048)
def _clause_order(c: str, strict: bool) -> bool:
    """`_later_clause_order` for one clause, once per clause text."""
    if not c:
        return False
    if not strict and (_RECAP_INF.match(c) or _EVENT_TIME.match(c)):
        return False                                            # ", to finish 2% higher for the week"
    if strict and re.match(r"so\b", c, re.I):
        return True                                             # "close at 1500, so the risk is capped": a rationale
    if _ORDER_CLAUSE.match(c) or _VERDICT.match(c):
        return True
    if len(c.split()) <= 4 and (_ADVICE_HARD.search(c) or any(
            not _ADVICE_FOLLOWED.match(c, m.end()) for m in _ADVICE_ADJ.finditer(c))):
        return True                                             # a bare verdict fragment ("; a must", "; prudent")
    sub = _SUBORDINATE.match(c)
    if sub is None or re.match(r"as\s+per\b", c, re.I):
        return False
    return not _subordinate_ok(sub.group("lead"), sub.group("body"), strict)


def _recap_tail(tail: str, kind: str, verb: str) -> bool:
    """Whether `tail` (the rest of the field's own clause) is a recap tail for a field of `kind` (bare / prep / of / ...)
    of `verb`."""
    t = tail.strip(" .")
    hold = verb == "hold" and kind not in ("of", "cop", "label")
    sub = _SUBORDINATE.match(t)
    if sub and not re.match(r"as\s+per\b", t, re.I):
        lead = sub.group("lead").lower()
        if hold and lead not in _SINCE_LEADS:
            return False                                        # "hold above 4000 after results / despite the noise"
        return _subordinate_ok(lead, sub.group("body"), strict=kind != "bare" or hold)
    if hold:
        return bool(_HOLD_TAIL.fullmatch(t))                    # "hold above 4000 is intact / kept the trend"
    if kind == "bare" and _BARE_TAIL.fullmatch(t):
        return True
    if kind == "at" and re.match(r"(?:by|within)\b", t, re.I):
        return False                                            # "close at 1500 by 2%": a move needs a direction
    return bool(_RECAP_TAIL.fullmatch(t))


def _field_kind(m: "re.Match[str]") -> str:
    if m.group("prep"):
        return "at" if m.group("prep").lower() in ("at", "near", "around", "in", "within") else "prep"
    for kind in ("of", "pct", "bare", "cop", "pair", "label", "word"):
        if m.group(kind):
            return kind
    return "word"


def _field_ok(verb: str, kind: str, rest: str, L: "Lists") -> bool:
    """Whether `rest` (everything after a price field of `kind`, up to the sentence end) keeps the field a recap."""
    v = verb.lower()
    sentence_end = _SENTENCE_END.search(rest)                   # the field's own sentence; the next one only for a verdict
    if sentence_end and _VERDICT.match(rest[sentence_end.end():sentence_end.end() + 120]):
        return False                                            # "INFY: close at 1500. That is the plan."
    rest = rest[:sentence_end.end()] if sentence_end else rest
    parts = re.split(r";|,(?!\d)", rest[:200])                # never inside "2,000"
    nxt = [w for w in _words(parts[1]) if w] if len(parts) > 1 else []
    if len(nxt) == 1 and _is_ticker(nxt[0].strip(".!?"), L):
        return False                                            # "Close at 2450, RELIANCE."
    if _advice_after(rest[:200], kind == "bare"):
        return False
    strict = kind != "bare" or v == "hold"
    if _later_clause_order(parts[1:], strict):
        return False
    return _recap_tail(parts[0], kind, v)


def _price_field(verb: str, text: str, L: "Lists", sentence: str = "", at: int = -1) -> bool:
    """Whether close / open / hold + `text` reads as the bar's price field ("close 4050", "close above 2450", "close was
    flat"): only when what follows the field is a recap tail (`_recap_tail`), with no advice after it (`_advice_after`)
    and no order word in the sentence. `at` is the offset of `text` in `sentence` (review round 12, LOW: never a search,
    which found the first copy of a repeated text)."""
    if verb.lower() not in _FIELD_VERBS:
        return False
    if sentence and at >= 0:                                    # "close 4050 for a 1.2% gain": past decimal points, and
        end = re.search(r"\n", sentence[at:at + 300])          # into a next sentence a digit hid ("1500. That is the plan.")
        text = sentence[at:at + (end.start() if end else 300)]
    m = _PRICE_FIELD.match(text)
    if m is None or (not sentence and _FIELD_ORDER.search(text[:200])) or _orders_in(sentence, at if at >= 0 else -1):
        return False
    kind = _field_kind(m)
    if kind == "label":
        return True                                             # "Close price: INFY 1500" - a label, not an order
    rest = re.sub(r"^(?:[\w₹%-]|\.(?=\d))*", "", text[m.end():])   # the rest of the number / level word the match ended in
    if verb.lower() == "hold" and re.match(r"\s*and\s+(?:close|open)\b", rest):
        return False                                            # "hold above 4000 and close at 4500": a target
    return _field_ok(verb, kind, rest, L)


# Words a level is never "in" or "on" as an instrument: quarters and sectors ("close 4050; Q2 results ...", "IT was ...").
_NOT_LEVEL_TICKERS = frozenset({"Q1", "Q2", "Q3", "Q4", "H1", "H2", "FY", "IT", "PSU", "PSUS", "FMCG", "BFSI", "MNC", "CPSE", "OMC", "OMCS"})
# Follow-ups of #230 round 12: a trade verb on a level when the sentence names an instrument ("INFY: weak, short below
# 1490"), and "pick / scoop it up near 1450".
_LEVEL_TRADE_VERBS = frozenset({"short", "sell", "buy", "long", "go long", "go short", "short sell", "accumulate", "load up", "add"})
_ON_LEVEL = re.compile(r"^\s*(?:below|under|above|over|near|around|at|on\s+a\s+(?:break|move|dip)\s+(?:below|above|under|over|to))\s+₹?\d", re.I)
_PICK_UP = re.compile(r"(?:^|(?<=[\n,;:.!?—–])|(?<=\sand)|(?<=\sthen))[ \t]*(?P<verb>pick|scoop|snap|mop|gobble|grab|load)\s+"
                      r"(?:it|them|some|these|those|this|that|more|shares)\s+up\s+(?:near|at|around|below|under|above|on\s+dips?\s+(?:to|near|at))\s+₹?\d",
                      re.I)
_OPEN_TRADE = re.compile(r"^\s*(?:(?:an?|new|fresh|the)\s+)*(?:long|short|position|trade)s?\b", re.I)
# "Add a 20 EMA on the RELIANCE chart": an indicator length, not a price level.
_INDICATOR_LENGTH = re.compile(r"^\s*(?:an?\s+|the\s+)?\d+[\s-]*(?:period\s+|day\s+|bar\s+)?"
                               r"(?:EMA|SMA|DMA|WMA|MA|RSI|VWAP|ATR|ADX|MACD|bollinger|supertrend|CCI|OBV)\b", re.I)
_GERUND_SUBJECT = re.compile(r"(?:^|(?<=[\n.!?;:,]))[ \t]*(?P<verb>buying|selling|shorting|accumulating|adding|going long on|going short on|"
                             r"investing in)\s+(?P<rest>[^\n.!?;]{0,80}?)\s+(?:makes sense|is a good idea|is worth it|is worth a try|"
                             r"looks good|is wise|is sensible|is the move|is the play|could work|should work|is smart)\b", re.I)
_ACT_NOW = re.compile(r"\b(?:take it|take this|take the trade|act now|go for it|grab it|enter now|jump in)\b", re.I)


def sentences(text: str) -> List[str]:
    return [s for s in _SENTENCE.split(text) if s and s.strip()]


def _paragraphs(text: str) -> List[str]:
    return [p for p in re.split(r"\n[ \t]*\n", text) if p.strip()]


@functools.lru_cache(maxsize=256)
def _clause_breaks(s: str) -> Tuple[Tuple[int, ...], Tuple[int, ...]]:
    """The (start, end) offsets of every clause break in `s`, computed once per sentence (review LOW: scanning from the
    sentence start for every match was quadratic on long inputs)."""
    spans = [(m.start(), m.end()) for m in _CLAUSE.finditer(s)]
    return tuple(a for a, _ in spans), tuple(b for _, b in spans)


def _clause(s: str, start: int, end: int) -> str:
    """The clause of sentence `s` around s[start:end] (clause breaks: , ; dashes, "but")."""
    starts, ends = _clause_breaks(s)
    # the last break that ends at or before `start` (its end bounds the clause on the left) ...
    i = bisect.bisect_right(ends, start) - 1
    while i >= 0 and starts[i] >= start:                       # a break must lie wholly before `start`, as finditer(s, 0, start)
        i -= 1
    lo = ends[i] if i >= 0 else 0
    # ... and the first break starting at or after `end`
    j = bisect.bisect_left(starts, end)
    return s[lo:starts[j] if j < len(starts) else len(s)]


def record_line(s: str, verb_start: Optional[int] = None) -> bool:
    """A record line (an order status, a proposal, a log line): it opens with a record label, and the first clause after
    the label - the one holding the verb at `verb_start` - reads like a record (a status word, a quantity, an order
    field), optionally followed by record fields only ("..., 10 shares, awaiting your approval"). Never a call made now
    ("again tomorrow"), never an imperative opening a later clause."""
    label = _LABEL.match(s)
    if label is None or label.group("label").strip().lower() not in _RECORD_LABELS:
        return False
    body_start = label.end()
    first_break = _CLAUSE.search(s, body_start)
    first_end = first_break.start() if first_break else len(s)
    if verb_start is not None and verb_start >= first_end:
        return False                                            # an imperative opening a later clause is never a record
    first = s[body_start:first_end]
    if _NOW.search(first):
        return False
    if label.group("label").strip().lower() in _FIELD_LABELS:
        return True
    if _RECORD.search(first):
        return True
    rest = [c for c in _CLAUSE.split(s[first_end:]) if c and c.strip()]
    return bool(rest) and all(_RECORD_FIELD.match(c) for c in rest)


def _rule_line(s: str) -> bool:
    """A rule's own text: a rule label and a "when" with an indicator / price condition, nothing about now or you."""
    label = _LABEL.match(s)
    if label is None or label.group("label").strip().lower() not in _RULE_LABELS:
        return False
    return bool(_RULE_CONDITION.search(s)) and not _NOW.search(s)


@functools.lru_cache(maxsize=256)
def _negations(s: str, pattern: Pattern[str]) -> Tuple["re.Match[str]", ...]:
    """The negated sayings of sentence `s`, found once (they were re-scanned for every verb: quadratic on long input)."""
    return tuple(pattern.finditer(s))


def _negated(s: str, verb_start: int, L: Lists) -> bool:
    """A negated saying that governs the verb at `verb_start`: before it with no clause break in between, or ending in ':'
    DIRECTLY after the negated verb ("We never say: Sell RELIANCE", not "We never recommend stocks: buy RELIANCE")."""
    negs = _negations(s, L.negated)
    if not negs:
        return False
    # Only a negation inside the verb's own clause (no , ; dash "but" break before the verb) can govern it - every other
    # one has a break between and fails the test below anyway; skipping them keeps long inputs linear.
    starts, ends = _clause_breaks(s)
    i = bisect.bisect_right(ends, verb_start) - 1
    lo = ends[i] if i >= 0 else 0
    for m in negs:
        if m.start() < lo and m.end() <= lo:
            continue
        if m.start() < verb_start:
            between = s[m.end():verb_start]
            colon = re.match(r"^\s*:", between)
            if colon:
                if not _CLAUSE_OR_COLON.search(between[colon.end():]):
                    return True
            elif not _CLAUSE_OR_COLON.search(between):
                return True
    return False


def _instrument_at_end(before: str, L: Lists) -> Optional[str]:
    """The instrument (or share word) that ends `before`."""
    words = [w for w in _words(before) if w]
    if not words:
        return None
    last = words[-1]
    if _is_ticker(last, L):
        return last
    n = instrument_names.suffix_len(words)
    if n:
        return " ".join(words[-n:])
    if last.lower() in L.share_words:
        return last
    return None


@functools.lru_cache(maxsize=256)
def _has_instrument(text: str, L: Lists) -> bool:
    """Whether `text` names an instrument - cached: the sentence-wide checks ask once per verb."""
    words = [w for w in _words(text) if w]
    if any(_is_ticker(w, L) for w in words):
        return True
    return any(instrument_names.prefix_len(words[i:i + instrument_names.MAX_WORDS]) for i in range(len(words)))


def _object(rest: str, L: Lists, s: str, *, allow_level: bool, verb: str = "", at: int = -1) -> Optional[str]:
    """What an imperative / modal verb is aimed at (None = not a call): an instrument, a share word or a price level after
    the fillers; "a signal / setup / rating on RELIANCE"; a pronoun ("buy more") when the sentence names an instrument.
    A noun phrase ("Sell HDFCBANK was the most common signal") is not a call. `at` is the offset of `rest` in `s`."""
    if _price_field(verb, rest, L, s, at):
        return None
    if verb.lower() in _FIELD_VERBS and re.match(r"[\s,:\-–—]*(?:/|(?:(?:at|above|below|near|over|under|around)\s+)?₹?\d)", rest):
        # "Close above 2450 in INFY", "Hold above 2500 - RELIANCE": an order on the instrument named after the level - unless
        # a recap predicate follows it ("Close above 1500 in INFY was the week's best") or the sentence lists several
        # fields or instruments ("Open 1490, close 1500 in INFY"; "Close near 1500 on INFY, near 4050 on TCS")
        full = rest
        if s and at >= 0:
            end = re.search(r"\n|[.?!](?!\d)", s[at:at + 200])
            full = s[at:at + (end.end() if end else 200)]
        if not _orders_in(s) and _recap_list(s, L) and not _advice_after(full, False):
            return None
        field = _PRICE_FIELD.match(full)
        kind = _field_kind(field) if field else "prep"
        own = _CLAUSE_END.search(full)
        words = [w for w in _words(full[:own.start() if own else 80][:80]) if w]     # the field's own clause only
        for word in words[:6]:
            ticker = word.rstrip(".!?")
            if _is_ticker(ticker, L) and ticker.upper() not in _NOT_LEVEL_TICKERS:
                hit = re.search(rf"(?<![\w&]){re.escape(ticker)}(?![\w&])", full)
                after = full[hit.end():] if hit else ""
                if after.strip(" .,;") and not _orders_in(s, at) and _field_ok(verb, kind, after, L):
                    return None
                return ticker                                   # "Close above 2450", "SMA(close, 20)": the candle, not an order
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
    v = verb.lower()
    howto = v in _HOWTO_VERBS and not _HOWTO_NOW.search(rest) and not _acts_now(s)
    if howto and _INDICATOR_LENGTH.match(rest):
        return None
    if v in _LEVEL_TRADE_VERBS and _ON_LEVEL.match(rest) and _has_instrument(s, L):
        return re.sub(r"\s+", " ", rest.strip())[:20]          # "INFY: weak, short below 1490"
    lead_the = re.match(r"^\s*the\s+", rest, re.I)
    obj = _instrument_at_start(rest, L, allow_level=allow_level)
    if obj is not None:
        last = obj.split()[-1]
        after = rest[rest.find(last) + len(last):] if last in rest else ""
        if v == "open" and not (_OPEN_TRADE.match(after) or _OPEN_TRADE.match(rest)):
            return None                                         # "Open RELIANCE", "Open INFY in a new tab": the app
        page_object = re.match(r"^\s*(?:'s\s+)?(?:chart|page|tab|view|panel|card|screen)", after, re.I)
        if v in _TRADE_HOWTO_VERBS:
            ui = howto and bool(lead_the) and page_object is not None
        else:
            ui = howto and bool(_UI_TAIL.match(after)) and bool(lead_the or page_object or re.match(r"^\s*(?:from|in|on|for)\b", after, re.I))
        if ui and not _BOOK_PAGE.search(after[:60]):
            return None                                         # app how-to wording about a page / chart / view
        if _NOUN_PHRASE.match(re.sub(r"^\s*(?:positions?|shares?|stocks?|calls?|puts?|orders?|signals?)\b", "", after, flags=re.I)):
            return None
        if howto and v not in _TRADE_HOWTO_VERBS and _WATCHLIST_TAIL.match(after):
            return None                                         # "Add RELIANCE to your watchlist": not a trade
        return obj
    if _PRONOUN_ALWAYS.match(rest) or (_PRONOUN_OBJECT.match(rest) and _has_instrument(s, L)):
        return "it"
    return None


def _sentence_calls(s: str, L: Lists) -> List[str]:
    found: List[str] = []
    rule = _rule_line(s)

    def add(label: str, what: str) -> None:
        item = f"call: {label} {what}".strip().lower()[:60]
        if item not in found:
            found.append(item)

    def exempt(m: "re.Match[str]", *, howto_ok: bool = False) -> bool:
        verb_start = m.start("verb")
        clause = _clause(s, verb_start, m.end())
        if _negated(s, verb_start, L) or record_line(s, verb_start):
            return True
        if rule and L.condition.search(clause):                  # "Rule: Buy NIFTY when RSI < 30" - a rule's own text
            return True
        if howto_ok and _HOWTO_LABEL.match(s) and L.howto.search(s):
            return True
        return False
    for m in L.imperative.finditer(s):
        if exempt(m, howto_ok=True):
            continue
        obj = _object(m.group("rest"), L, s, allow_level=True, verb=m.group("verb"), at=m.start("rest"))
        if obj:
            add(m.group("verb"), obj)
    for pattern in (L.modal, L.gerund):
        for m in pattern.finditer(s):
            if exempt(m, howto_ok=True):
                continue
            obj = _object(m.group("rest"), L, s, allow_level=False, verb=m.group("verb"), at=m.start("rest"))
            if obj:
                add(m.group("verb"), obj)
    for m in _PICK_UP.finditer(s):
        if _negated(s, m.start("verb"), L) or record_line(s, m.start("verb")) or not _has_instrument(s, L):
            continue
        add(m.group("verb"), m.group(0).strip()[:30])
    for m in _GERUND_SUBJECT.finditer(s):
        obj = _instrument_at_start(m.group("rest"), L, allow_level=False)
        if obj and not _negated(s, m.start("verb"), L):
            add(m.group("verb"), obj)
    taken = 0
    for m in L.colon.finditer(s):
        head = s[max(taken, m.start() - 60):m.start()]
        head = head[max(head.rfind(c) for c in "\n.!?;:") + 1:]
        if not head:
            continue                                              # nothing names an object before the separator
        taken = m.end()
        rated = re.match(r"^\s*(?:rating|call)\b", m.group("after"), re.I)
        if (L.descriptive.match(m.group("after")) and not rated) or _negated(s, m.start("verb"), L) or record_line(s, m.start("verb")):
            continue
        if _price_field(m.group("verb"), s[m.end("verb"):], L, s, m.end("verb")):
            continue                                              # "TCS: close 4050, RSI 62" - a price field, not a call
        obj = _instrument_at_end(head, L)
        if obj and obj.lower() not in L.share_words:
            add(f"{obj}:", m.group("verb"))
    for m in L.opinion.finditer(s):
        obj = _instrument_at_start(m.group("rest"), L, allow_level=False)
        if obj and not _negated(s, m.start(), L):
            add("pick", obj)
    for m in L.rated.finditer(s):
        obj = _instrument_at_end(s[max(0, m.start() - 80):m.start()], L)
        if obj and not _negated(s, m.start(), L):
            add(obj, "rated a buy / sell")
    for pattern in L.certainty:
        for hit in pattern.finditer(s):
            clause = re.sub(r"\b(?:as|like)\s+(?:it|they|he|she)\s+(?:did|had|was|were)\b.*$", "", _clause(s, hit.start(), hit.end()), flags=re.I)
            future = re.search(r"\b(?:will|shall|going to|gonna)\b", clause, re.I)
            if (L.past.search(clause) and not future) or _negated(s, hit.start(), L):
                continue                                          # past tense in ITS clause only ("as it did last week" is not)
            add("level with certainty", hit.group(0).strip()[:30])
            break
    return found


_TEMPLATE_CONTEXT = re.compile(r"\b(?:per|in|from)\s+(?:the|your|this)\s+(?:template|rule|strategy|plan|backtest)\b|"
                               r"\b(?:template|strategy|rule|plan)'s\b", re.I)


def _paragraph_levels(p: str, L: Lists) -> bool:
    """A stop and a target with price levels, plus an entry level or an instrument, in one paragraph. Not a call when every
    stop / target sits in a past clause ("The trade was closed at the target 2600 after the stop 2450 moved up"), or in a
    sentence that is a log / backtest record or a template's levels with nothing about now ("Backtest trade 3: entry
    2480, stop 2430, target 2580"; "... per the template") - clause by clause, never the whole sentence or paragraph."""
    stops, targets = list(L.stop.finditer(p)), list(L.target.finditer(p))
    if not stops or not targets:
        return False
    bounds = [0] + [m.end() for m in _SENTENCE.finditer(p)] + [len(p) + 1]        # sentence starts, computed once
    exempt_all = True
    for m in stops + targets:
        k = bisect.bisect_right(bounds, m.start()) - 1
        sentence = p[bounds[k]:bounds[k + 1]]
        rel = m.start() - bounds[k]
        clause = _clause(sentence, rel, rel + (m.end() - m.start()))
        label = _LABEL.match(sentence)
        logged = label is not None and label.group("label").strip().lower() in _LOG_LABELS
        quiet = not _NOW.search(sentence) and not _ACT_NOW.search(sentence)
        if (L.past.search(clause) and not re.search(r"\b(?:will|now|today)\b", clause, re.I)) or _negated(sentence, rel, L):
            continue
        if quiet and (logged or _TEMPLATE_CONTEXT.search(sentence) or record_line(sentence)):
            continue
        exempt_all = False
        break
    if exempt_all:
        return False
    if L.entry.search(p):
        return True
    return _has_instrument(p, L)


_POSSESSIVE = re.compile(r"\b([A-Z][A-Z0-9&]{1,19})['\u2019]s\b")


def detect(text: str, path: str) -> List[str]:
    """The call labels in `text` (module docstring), in order of appearance."""
    if len(text or "") > MAX_CHARS:
        return [f"call: text too long to check (over {MAX_CHARS} characters)"]
    if _INDIC.search(text or ""):
        return [SCRIPT_LABEL]                                   # English only: never read, always withheld
    L = lists(path)
    _has_instrument.cache_clear()                              # the instrument index may have grown since the last text
    body = L.verb_line.sub(r"\1 ", normalise(text))            # "Buy\nRELIANCE": a verb alone on its line opens the next one
    body = _POSSESSIVE.sub(r"\1", body)                        # "Buy RELIANCE's chart breakout": the ticker itself
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


__all__ = ["detect", "normalise", "lists", "sentences", "MAX_CHARS", "SCRIPT_LABEL"]
