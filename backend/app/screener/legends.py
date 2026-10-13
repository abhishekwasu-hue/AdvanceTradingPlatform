"""U7 LG1 (docs/specs/ATP_LEGENDS_SCREENS_ADDENDUM.md): the Legends library - short cards about publicly described
trading methods, each with a working ScreenQL screen modelled on the method.

* Content is data: `data/legends.json` (the cards) and `data/legends_sources.json` (the public works they cite), both
  changed by a reviewed PR. Components read them from the API (`GET /api/screener/legends`), never from code.
* `lint()` is the library's rule set (spec §1, §5): every card line cites at least one source from the sources file;
  a card is at most five lines (who, style, what the screen looks for, timeframe, source); no advice wording (the
  taxonomy's list: the Copilot's output filter plus the screener's own words), no performance promise, no claim of
  endorsement, no first person and no quotation (a card describes a method, it never speaks for a person); no
  trademarked method name in a title; English only; the screen compiles with its default parameters, each declared
  parameter is used and its default lies inside its documented range. `load()` refuses a library that fails it.
* Every card id is a pre-built screen of the template source `legends`, registered here with the flag
  `screener_legends`: the taxonomy's CI guard (`taxonomy.coverage_problems`) fails when a card is not filed, and
  `link_problems()` checks that the screen a card links to is filed under the Legends method with the card's name.
* The disclaimer is the spec's wording, fixed here and added to the library header and to every card by the API.

Educational only (ADR-0006): a card describes how a published method reasons; it never ranks, scores or recommends a
screen, a symbol or a person.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.screener import compile_screen
from app.screener import taxonomy as tx

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_FILE = DATA_DIR / "legends.json"
SOURCES_FILE = DATA_DIR / "legends_sources.json"
SOURCE = "legends"                      # the taxonomy template source and the taxonomy method id
METHOD = "legends"
FLAG = "screener_legends"
# U7 LG2: the second slice of cards, behind its own flag on top of FLAG. A card's `slice` says which one it belongs to
# (default lg1). Every card is filed under the one `legends` source; the taxonomy hides the LG2 screens while their
# flag is off (`register_screen_flags`), and the API leaves the LG2 cards out.
FLAG_LG2 = "screener_legends_lg2"
SLICES: Dict[str, Optional[str]] = {"lg1": None, "lg2": FLAG_LG2}        # slice -> the flag it needs beyond FLAG
LINES: Tuple[str, ...] = ("who", "style", "looks_for", "timeframe")   # plus the source line: five lines in all
MAX_CARD_LINES = 5
MAX_LINE_CHARS = tx.MAX_DEFINITION_CHARS
MAX_NOTE_CHARS = 240
MAX_REVIEW_NOTE_CHARS = 400
MAX_NAME_CHARS = tx.MAX_NAME_CHARS
SOURCE_KINDS: Tuple[str, ...] = ("book", "paper", "article")        # published, publicly available works only
# LG1-2: the bibliographic check of a source against public catalogue records (title, authors, first-edition year,
# publisher), and a card's status from it. "verified" needs evidence (public URLs); "unverified" needs a note saying why.
REVIEW_STATUSES: Tuple[str, ...] = ("verified", "unverified")
# Spec §1, word for word. `{name}` is the legend's name on a card and "the people named" in the library header.
DISCLAIMER = "Inspired by publicly described methods. Not affiliated with, endorsed by, or representing {name}. Educational tool, not investment advice."
HEADER = DISCLAIMER.format(name="the people named")
NOTE = ("Each card describes a published method and an approximation of it as a screen. It is not a ranking of the "
        "method, the screen or any symbol.")

# Spec §1: descriptive titles, never a registered or branded method name (checked case-insensitively in titles).
TRADEMARK_TERMS: Tuple[str, ...] = ("CAN SLIM", "CANSLIM", "SEPA", "VCP", "Holy Grail", "Turtle Soup", "ConnorsRSI",
                                    "Connors RSI", "Superperformance", "Super Performance")
# No performance promise: a card describes what a method looks for, never how it did or will do.
PERFORMANCE_TERMS: Tuple[str, ...] = ("profit", "profits", "profitable", "profitability", "return", "returns", "outperform",
                                      "outperforms", "outperformed", "outperformance", "beat the market", "beats the market",
                                      "win rate", "winning rate", "winners", "success rate", "successful", "proven", "money",
                                      "million", "millions", "fortune", "rich", "wealth", "edge", "high probability",
                                      "super performance", "superperformance", "accurate", "accuracy", "reliable")
# No endorsement or affiliation claim: the disclaimer (added by code) is the only place these ideas appear.
ENDORSEMENT_TERMS: Tuple[str, ...] = ("endorse", "endorsed", "endorses", "endorsement", "approved", "approves", "approval",
                                      "official", "officially", "authorised", "authorized", "certified", "affiliated",
                                      "affiliation", "partner", "partnered", "partnership", "sponsored", "sponsor", "licensed",
                                      "licence", "license", "on behalf of", "in collaboration", "personally", "exclusive",
                                      "trusted by", "used by", "as used by", "favourite", "favorite")
# A card speaks about a method in the third person; it never speaks as the person (impersonation).
_FIRST_PERSON = re.compile(r"(?<![\w'])(?:I|I'm|I've|I'd|I'll|me|my|mine|myself|we|We|our|Our|ours|us)(?![\w'])")
_QUOTE = re.compile(r"[\"“”«»]")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_CARD_ID = re.compile(r"^legends\.[a-z][a-z0-9_]*$")
# A capitalised run of two or more words ("Richard Dennis", "Linda Bradford Raschke") reads as a person's name.
_NAME = re.compile(r"(?<![\w-])[A-Z][a-z]+(?:\s+(?:[A-Z]\.\s*)?[A-Z][a-z]+)+(?![\w-])")
_SOURCE_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_PARAM = re.compile(r"^[a-z][a-z0-9_]*$")


def _bounded(words: Sequence[str]) -> re.Pattern[str]:
    alternation = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{alternation})(?![\w-])", re.IGNORECASE)


_PERFORMANCE = _bounded(PERFORMANCE_TERMS)
_ENDORSEMENT = _bounded(ENDORSEMENT_TERMS)
_TRADEMARK = _bounded(TRADEMARK_TERMS)


class LegendsError(ValueError):
    """The Legends library failed its lint rules; `problems` says what to fix."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = list(problems)


@dataclass(frozen=True)
class Source:
    id: str
    kind: str
    title: str
    authors: Tuple[str, ...]
    year: int
    publisher: str
    covers: str
    review: str
    review_note: str = ""
    evidence: Tuple[str, ...] = ()

    def citation(self) -> str:
        """`Title (Year), Author and Author` - the card's source line."""
        authors = " and ".join(self.authors)
        return f"{self.title} ({self.year}), {authors}"

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "title": self.title, "authors": list(self.authors), "year": self.year,
                "publisher": self.publisher, "covers": self.covers, "review": self.review, "review_note": self.review_note,
                "evidence": list(self.evidence), "citation": self.citation()}


@dataclass(frozen=True)
class Line:
    text: str
    cite: Tuple[str, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "cite": list(self.cite)}


@dataclass(frozen=True)
class Param:
    name: str
    default: float
    min: float
    max: float
    doc: str

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "default": self.default, "min": self.min, "max": self.max, "doc": self.doc}


@dataclass(frozen=True)
class Card:
    id: str
    legend: str
    short: str
    name: str
    lines: Dict[str, Line]
    approximation: str
    source: str
    base_tf: str
    params: Tuple[Param, ...]
    review: str = "unverified"
    review_note: str = ""
    slice: str = "lg1"

    def cited(self) -> List[str]:
        """Every source id the card cites, in order of first use."""
        return list(dict.fromkeys(c for key in LINES for c in self.lines[key].cite))

    def defaults(self) -> Dict[str, Any]:
        return {p.name: p.default for p in self.params}


@dataclass(frozen=True)
class Library:
    version: str
    cards: Tuple[Card, ...]
    sources: Dict[str, Source]

    def ids(self) -> List[str]:
        return [c.id for c in self.cards]

    def card(self, ident: str) -> Optional[Card]:
        return next((c for c in self.cards if c.id == ident), None)

    def of(self, slices: Sequence[str]) -> Tuple[Card, ...]:
        """The cards of these slices, in file order."""
        return tuple(c for c in self.cards if c.slice in slices)


# --- lint ------------------------------------------------------------------------------------------------------------

def english_problems(where: str, text: str) -> List[str]:
    return [f"{where}: English only (Devanagari found)"] if _DEVANAGARI.search(text or "") else []


def wording_problems(where: str, text: str) -> List[str]:
    """Advice, performance, endorsement, first person and quotation in one card text (empty when clean)."""
    out: List[str] = []
    advice = tx.advice_words(text)
    if advice:
        out.append(f"{where}: advice wording is not allowed ({', '.join(advice)})")
    perf = list(dict.fromkeys(m.lower() for m in _PERFORMANCE.findall(text or "")))
    if perf:
        out.append(f"{where}: performance wording is not allowed ({', '.join(perf)})")
    endorse = list(dict.fromkeys(m.lower() for m in _ENDORSEMENT.findall(text or "")))
    if endorse:
        out.append(f"{where}: endorsement wording is not allowed ({', '.join(endorse)}) - the disclaimer says it")
    first = list(dict.fromkeys(_FIRST_PERSON.findall(text or "")))
    if first:
        out.append(f"{where}: first person is not allowed ({', '.join(first)}) - describe the method, never speak as the person")
    if _QUOTE.search(text or ""):
        out.append(f"{where}: no quotation marks - a card never quotes a person")
    return out + english_problems(where, text)


def _text(where: str, raw: Any, limit: int, problems: List[str]) -> str:
    text = raw if isinstance(raw, str) else ""
    if not text.strip():
        problems.append(f"{where}: empty")
    elif "\n" in text or len(text) > limit:
        problems.append(f"{where}: at most {limit} characters on one line ({len(text)} given)")
    problems += wording_problems(where, text)
    return text.strip()


def people_named(texts: Sequence[str], cited: Sequence[Source]) -> List[str]:
    """Every person a card's text names (L2: the disclaimer must name each of them): capitalised runs of two or more
    words outside the cited works' titles, and any cited author named by surname alone."""
    titles = sorted({t for s in cited for t in (s.title, s.title.split(":")[0])}, key=len, reverse=True)
    found: List[str] = []
    for text in texts:
        rest = text
        for title in titles:
            rest = rest.replace(title, " ")
        found += _NAME.findall(rest)
        for author in (a for s in cited for a in s.authors):
            surname = author.split()[-1]
            if re.search(rf"(?<![\w-]){re.escape(surname)}(?![\w-])", rest) and not any(surname in f for f in found):
                found.append(author)
    return list(dict.fromkeys(found))


def _review(where: str, row: Dict[str, Any], problems: List[str]) -> Tuple[str, str]:
    """(status, note) of a source or card: a status from REVIEW_STATUSES; an unverified one says why in its note."""
    review = str(row.get("review", ""))
    if review not in REVIEW_STATUSES:
        problems.append(f"{where}: review must be one of {', '.join(REVIEW_STATUSES)} ({review!r} given)")
    note = row.get("review_note", "")
    note = note.strip() if isinstance(note, str) else ""
    if review == "unverified" and not note:
        problems.append(f"{where}: an unverified entry says why in review_note")
    if "\n" in note or len(note) > MAX_REVIEW_NOTE_CHARS:
        problems.append(f"{where} review_note: at most {MAX_REVIEW_NOTE_CHARS} characters on one line")
    problems += english_problems(f"{where} review_note", note)
    return review, note


def _sources(raw: Any, problems: List[str]) -> Dict[str, Source]:
    out: Dict[str, Source] = {}
    rows = raw.get("sources") if isinstance(raw, dict) else None
    if not isinstance(rows, list) or not rows:
        problems.append("sources: none listed")
        return out
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append(f"sources[{i}]: not an object")
            continue
        ident = str(row.get("id", ""))
        where = f"source {ident or i!r}"
        if not _SOURCE_ID.match(ident):
            problems.append(f"{where}: id must be lower_snake_case")
        if ident in out:
            problems.append(f"{where}: duplicate id")
        kind = str(row.get("kind", ""))
        if kind not in SOURCE_KINDS:
            problems.append(f"{where}: kind {kind!r} is not a published public work ({', '.join(SOURCE_KINDS)})")
        title, covers = str(row.get("title", "")).strip(), str(row.get("covers", "")).strip()
        authors = row.get("authors")
        if not title:
            problems.append(f"{where}: needs a title")
        if not isinstance(authors, list) or not authors or not all(isinstance(a, str) and a.strip() for a in authors):
            problems.append(f"{where}: needs its authors")
            authors = []
        year = row.get("year")
        if not isinstance(year, int) or isinstance(year, bool) or not 1800 <= year <= 2100:
            problems.append(f"{where}: needs a publication year")
            year = 0
        if not covers:
            problems.append(f"{where}: say what it covers (what a reviewer checks the cards against)")
        for key in ("title", "publisher", "covers"):
            problems += english_problems(f"{where} {key}", str(row.get(key, "")))
        problems += [p for a in authors for p in english_problems(f"{where} author", a)]
        review, note = _review(where, row, problems)
        evidence: Tuple[str, ...] = ()
        raw_evidence = row.get("evidence", [])
        if not isinstance(raw_evidence, list) or not all(isinstance(u, str) and u.startswith("https://") for u in raw_evidence):
            problems.append(f"{where}: evidence must be a list of public https:// URLs")
        elif review == "verified" and not raw_evidence:
            problems.append(f"{where}: a verified source lists its evidence (catalogue / ISBN record URLs)")
        else:
            evidence = tuple(raw_evidence)
        if review == "verified" and not str(row.get("publisher", "")).strip():
            problems.append(f"{where}: a verified source names its publisher")
        out[ident] = Source(ident, kind, title, tuple(a.strip() for a in authors), int(year), str(row.get("publisher", "")).strip(),
                            covers, review, note, evidence)
    return out


def _params(where: str, raw: Any, problems: List[str]) -> Tuple[Param, ...]:
    out: List[Param] = []
    seen: Set[str] = set()
    for i, row in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(row, dict):
            problems.append(f"{where} params[{i}]: not an object")
            continue
        name = str(row.get("name", ""))
        if not _PARAM.match(name) or name in seen:
            problems.append(f"{where} params[{i}]: name {name!r} must be unique lower_snake_case")
        seen.add(name)
        nums: List[float] = [float(v) for k in ("default", "min", "max")
                             if isinstance(v := row.get(k), (int, float)) and not isinstance(v, bool)]
        if len(nums) != 3:
            problems.append(f"{where} ${name}: default, min and max must be numbers")
            continue
        default, low, high = nums
        if not low < high or not low <= default <= high:
            problems.append(f"{where} ${name}: default {default:g} must lie inside its documented range [{low:g}, {high:g}]")
        doc = _text(f"{where} ${name} doc", row.get("doc"), MAX_LINE_CHARS, problems)
        value = row["default"]
        out.append(Param(name, value, row["min"], row["max"], doc))
    return tuple(out)


def _screen(where: str, raw: Any, problems: List[str]) -> Tuple[str, str, Tuple[Param, ...]]:
    raw = raw if isinstance(raw, dict) else {}
    source, base_tf = str(raw.get("source", "")).strip(), str(raw.get("base_tf", ""))
    params = _params(where, raw.get("params"), problems)
    if not source:
        problems.append(f"{where}: needs its ScreenQL source")
        return source, base_tf, params
    _, validated = compile_screen(source, base_tf=base_tf, params={p.name: p.default for p in params})
    if not validated.ok:
        problems.append(f"{where}: the screen does not compile ({'; '.join(p.message for p in validated.problems)})")
    return source, base_tf, params


def _cards(raw: Any, sources: Dict[str, Source], problems: List[str]) -> Tuple[Card, ...]:
    out: List[Card] = []
    seen: Set[str] = set()
    for i, row in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(row, dict):
            problems.append(f"cards[{i}]: not an object")
            continue
        ident = str(row.get("id", ""))
        where = f"card {ident or i!r}"
        if not _CARD_ID.match(ident):
            problems.append(f"{where}: id must be legends.<lower_snake_case> (it is the screen's taxonomy id)")
        if ident in seen:
            problems.append(f"{where}: duplicate id")
        seen.add(ident)
        legend = _text(f"{where} legend", row.get("legend"), MAX_NAME_CHARS, problems)
        short = str(row.get("short", "")).strip()
        if not short or short.lower() not in legend.lower():
            problems.append(f"{where}: short name {short!r} must be part of the legend's name")
        name = _text(f"{where} name", row.get("name"), MAX_NAME_CHARS, problems)
        if short and not name.startswith(f"{short}-style "):
            problems.append(f"{where}: name must read '{short}-style <screen>' (spec §1)")
        marks = list(dict.fromkeys(m for m in _TRADEMARK.findall(name)))
        if marks:
            problems.append(f"{where} name: no trademarked or branded method name in a title ({', '.join(marks)}) - use a descriptive name")
        lines: Dict[str, Line] = {}
        for key in LINES:
            raw_line = row.get(key)
            line: Dict[str, Any] = raw_line if isinstance(raw_line, dict) else {}
            text = _text(f"{where} {key}", line.get("text"), MAX_LINE_CHARS, problems)
            raw_cite = line.get("cite")
            cite: List[Any] = raw_cite if isinstance(raw_cite, list) else []
            if not cite:
                problems.append(f"{where} {key}: cite at least one source (every claim about a method needs one)")
            for c in cite:
                if c not in sources:
                    problems.append(f"{where} {key}: cites {c!r}, which is not in legends_sources.json")
            lines[key] = Line(text, tuple(str(c) for c in cite))
        approximation = _text(f"{where} approximation", row.get("approximation"), MAX_NOTE_CHARS, problems)
        source, base_tf, params = _screen(where, row.get("screen"), problems)
        texts = [lines[k].text for k in LINES] + [approximation] + [p.doc for p in params]
        cited = [sources[c] for c in dict.fromkeys(c for k in LINES for c in lines[k].cite) if c in sources]
        problems += [f"{where}: names {who!r}, so the disclaimer must too - add them to `legend`"
                     for who in people_named(texts, cited) if who.split()[-1].lower() not in legend.lower()]
        review, review_note = _review(where, row, problems)
        unchecked = [s.id for s in cited if s.review != "verified"]
        if review == "verified" and unchecked:
            problems.append(f"{where}: verified, but it cites unverified sources ({', '.join(unchecked)})")
        part = str(row.get("slice", "lg1"))
        if part not in SLICES:
            problems.append(f"{where}: slice must be one of {', '.join(SLICES)} ({part!r} given)")
        unknown = set(row) - {"id", "legend", "short", "name", "approximation", "screen", "review", "review_note", "slice", *LINES}
        if unknown:
            problems.append(f"{where}: unknown fields {', '.join(sorted(unknown))}")
        out.append(Card(ident, legend, short, name, lines, approximation, source, base_tf, params, review, review_note, part))
    if not out:
        problems.append("cards: none defined")
    if len(LINES) + 1 > MAX_CARD_LINES:          # the source line is the fifth
        problems.append(f"a card has {len(LINES) + 1} lines; the spec allows {MAX_CARD_LINES}")
    return tuple(out)


def parse(cards: Dict[str, Any], sources: Dict[str, Any]) -> Tuple[Library, List[str]]:
    """(library, lint problems). Problems are listed, not raised, so a test shows all of them at once."""
    problems: List[str] = []
    srcs = _sources(sources, problems)
    parsed = _cards(cards.get("cards") if isinstance(cards, dict) else None, srcs, problems)
    cited = {c for card in parsed for c in card.cited()}
    for ident in sorted(set(srcs) - cited):
        problems.append(f"source {ident!r} is listed but no card cites it (remove it or cite it)")
    version = str(cards.get("version", "")) if isinstance(cards, dict) else ""
    if not version:
        problems.append("version: missing")
    return Library(version, parsed, srcs), problems


def _read(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def lint(cards_path: Path = DATA_FILE, sources_path: Path = SOURCES_FILE) -> List[str]:
    return parse(_read(cards_path), _read(sources_path))[1]


@lru_cache(maxsize=4)
def _load(cards_path: str, sources_path: str) -> Library:
    library, problems = parse(_read(Path(cards_path)), _read(Path(sources_path)))
    if problems:
        raise LegendsError(problems)
    return library


def load(cards_path: Path = DATA_FILE, sources_path: Path = SOURCES_FILE) -> Library:
    """The library, linted; a file that fails its lint rules is refused (CI catches it first)."""
    return _load(str(cards_path), str(sources_path))


def link_problems(library: Optional[Library] = None, taxonomy: Optional[tx.Taxonomy] = None) -> List[str]:
    """The screen each card links to exists: filed in the taxonomy under source and method Legends, with the card's
    name. (The taxonomy's own guard checks the other way: every card is filed.)"""
    lib = library or load()
    tax = taxonomy or tx.load()
    filed = {s.id: s for s in tax.screens}
    problems: List[str] = []
    for card in lib.cards:
        screen = filed.get(card.id)
        if screen is None:
            problems.append(f"card {card.id!r} links to a screen that is not filed in the taxonomy")
            continue
        if screen.source != SOURCE or screen.method != METHOD:
            problems.append(f"card {card.id!r}: its screen must be filed with source and method {METHOD!r}")
        if screen.name != card.name:
            problems.append(f"card {card.id!r}: taxonomy name {screen.name!r} differs from the card's {card.name!r}")
    for screen in tax.screens:
        if screen.source == SOURCE and lib.card(screen.id) is None:
            problems.append(f"taxonomy screen {screen.id!r} has no Legends card")
    return problems


# --- the API shape ---------------------------------------------------------------------------------------------------

def card_payload(card: Card, library: Library, taxonomy: Optional[tx.Taxonomy] = None) -> Dict[str, Any]:
    tax = taxonomy or tx.load()
    filed = next((s for s in tax.screens if s.id == card.id), None)
    cited = [library.sources[c] for c in card.cited()]
    return {
        "id": card.id, "legend": card.legend, "name": card.name,
        "lines": {key: card.lines[key].as_dict() for key in LINES},
        "source_line": "Source: " + "; ".join(s.citation() for s in cited),
        "sources": [s.as_dict() for s in cited],
        "approximation": card.approximation,
        "disclaimer": DISCLAIMER.format(name=card.legend),
        "review": {"status": card.review, "note": card.review_note},
        "screen": {"id": card.id, "source": card.source, "base_tf": card.base_tf, "params": [p.as_dict() for p in card.params]},
        "filing": {"method": filed.method, "setup": filed.setup, "tags": dict(filed.tags)} if filed else None,
    }


def payload(library: Library, taxonomy: Optional[tx.Taxonomy] = None, slices: Sequence[str] = ("lg1",)) -> Dict[str, Any]:
    """The library as an organisation sees it: the cards of `slices` (LG1 by default; the API adds LG2 with its flag)."""
    tax = taxonomy or tx.load()
    return {"version": library.version, "header": HEADER, "note": NOTE,
            "cards": [card_payload(c, library, tax) for c in library.of(slices)]}


def _ids() -> List[str]:
    return load().ids()


def _screen_flags() -> Dict[str, str]:
    return {c.id: flag for c in load().cards if (flag := SLICES.get(c.slice))}


# The taxonomy's CI guard: every card is a pre-built screen of this source, hidden while the flag is off; an LG2 screen
# is also hidden while its own flag is off.
tx.register_source(SOURCE, _ids, flag=FLAG)
tx.register_screen_flags(SOURCE, _screen_flags)


__all__ = ["DATA_FILE", "DISCLAIMER", "FLAG", "FLAG_LG2", "SLICES", "HEADER", "LINES", "MAX_CARD_LINES", "NOTE", "REVIEW_STATUSES", "SOURCES_FILE", "Card", "Library",
           "LegendsError", "Line", "Param", "Source", "card_payload", "english_problems", "link_problems", "lint", "load", "parse",
           "payload", "people_named", "wording_problems"]


if __name__ == "__main__":
    # Regenerates the frontend's samples:
    #   python -m app.screener.legends > ../frontend/src/screener/legendsSample.json
    #   python -m app.screener.legends taxonomy > ../frontend/src/screener/taxonomyLegendsSample.json
    # The second is the taxonomy as an organisation with `screener_legends` on sees it (other default-off sources hidden).
    import sys

    import app.main  # noqa: F401 - registers every template source (into the imported modules, not this __main__ copy)
    from app.screener import legends as _lg
    from app.screener import taxonomy as _tx
    if sys.argv[1:] == ["taxonomy"]:
        hidden = [name for name in _tx.default_hidden() if name != _lg.SOURCE]
        print(json.dumps(_tx.payload(_tx.load(), hidden_sources=hidden, hidden_ids=_tx.default_hidden_ids()), indent=1, ensure_ascii=False))
    else:
        print(json.dumps(_lg.payload(_lg.load()), indent=1, ensure_ascii=False))
