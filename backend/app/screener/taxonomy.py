"""U9 TX1 (docs/specs/ATP_SCREENER_TAXONOMY_ADDENDUM.md): the screen library's taxonomy - two axes (method x setup
type), the tag vocabulary and the filing of every pre-built screen.

* The taxonomy is data: `data/taxonomy.json`, changed by a reviewed PR. Components read it from the API
  (`GET /api/screener/taxonomy`), never from a hardcoded list.
* `lint()` is the library's lint rule set (spec §3): every screen is filed on both axes, its tags come from the
  vocabulary, its definition fits in two lines, and no label or definition uses advice wording (the Copilot's output
  filter word list plus the screener's own words, SCREENER_SPEC §6). `load()` refuses a file that fails it.
* Pre-built screens come from template sources. A backend template module (U6 BK1 / BK2 breakouts, U7 Legends)
  registers its ids at import with `register_source("breakouts", ids_fn, flag="screener_breakouts")` and files each id
  in the JSON with that `source`; `coverage_problems()` then reports an unfiled template, a filed id its source does
  not have, and a source nobody registered - CI runs it, so a new template cannot ship untagged. The frontend's
  starter templates (`frontend/src/screener/starters.json`) are the one frontend source; the test suite checks them.
* A source registered with a flag is hidden (its screens and their counts) while that flag is off for the tenant.

Informational only (ADR-0006): the taxonomy files screens; it never ranks, scores or recommends one.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

DATA_FILE = Path(__file__).resolve().parent / "data" / "taxonomy.json"
FRONTEND_SOURCES: Tuple[str, ...] = ("starter",)      # filed from frontend/src/screener/starters.json (checked by tests)
TAG_KEYS: Tuple[str, ...] = ("timeframe_family", "direction", "data_tier", "difficulty", "universe_default")
LIST_TAGS: Tuple[str, ...] = ("timeframe_family", "data_tier")    # a screen may carry several of these
MAX_DEFINITION_CHARS = 160                            # "definition <= 2 lines": two 80-character lines, no line break
MAX_NAME_CHARS = 60
# SCREENER_SPEC §6: results are matches / candidates, never buy / sell / recommended / target. On top of the Copilot's
# advice and guarantee list (app/ai/data/advice_terms.json).
SCREENER_WORDS: Tuple[str, ...] = ("buy", "sell", "recommend", "recommended", "recommendation", "recommends", "target",
                                   "targets", "best", "top pick", "must own")
_SCREENER_WORDS = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(w) for w in sorted(SCREENER_WORDS, key=len, reverse=True)) + r")(?![\w-])",
                             re.IGNORECASE)
NOTE = "Screens are filed by how they reason and what they look for. Filing is not a ranking and not advice."
_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")


class TaxonomyError(ValueError):
    """The taxonomy file failed its lint rules; `problems` says what to fix."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = list(problems)


@dataclass(frozen=True)
class Axis:
    id: str
    code: str
    label: str
    definition: str
    user_built: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "code": self.code, "label": self.label, "definition": self.definition, "user_built": self.user_built}


@dataclass(frozen=True)
class Screen:
    id: str
    source: str
    name: str
    definition: str
    method: str
    setup: str
    tags: Dict[str, Any]
    evidence_ready: bool = False
    fitness_available: bool = False
    regimes: Tuple[str, ...] = ()           # U9 TX3: the market regimes it is designed for (empty = any)

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "source": self.source, "name": self.name, "definition": self.definition, "method": self.method,
                "setup": self.setup, "tags": dict(self.tags), "evidence_ready": self.evidence_ready,
                "fitness_available": self.fitness_available, "regimes": list(self.regimes)}


@dataclass(frozen=True)
class Taxonomy:
    version: str
    methods: Tuple[Axis, ...]
    setups: Tuple[Axis, ...]
    tags: Dict[str, Tuple[Dict[str, str], ...]]
    screens: Tuple[Screen, ...]
    regimes: Tuple[Dict[str, str], ...] = ()     # U9 TX3: the regime vocabulary ({id, axis, label})

    def screen_ids(self, source: Optional[str] = None) -> List[str]:
        return [s.id for s in self.screens if source is None or s.source == source]


@dataclass(frozen=True)
class Source:
    """A template source: where pre-built screens come from, the ids it has today, and the flag that hides it."""
    name: str
    ids: Callable[[], Iterable[str]]
    flag: Optional[str] = None


_SOURCES: Dict[str, Source] = {}


def register_source(name: str, ids: Callable[[], Iterable[str]], *, flag: Optional[str] = None) -> None:
    """A backend template module calls this at import (re-registering replaces). `ids` is called lazily, so the module's
    own template list stays its single source of truth."""
    if not _ID.match(name) or name in FRONTEND_SOURCES:
        raise ValueError(f"template source name {name!r} is not allowed")
    _SOURCES[name] = Source(name, ids, flag)


def unregister_source(name: str) -> None:
    _SOURCES.pop(name, None)


def sources() -> Dict[str, Source]:
    return dict(_SOURCES)


def advice_words(text: str) -> List[str]:
    """Advice / guarantee wording in a label or definition (empty when clean)."""
    from app.ai.output_filter import blocked_terms, is_specific_call
    found = [w.lower() for w in _SCREENER_WORDS.findall(text or "")]
    found += blocked_terms(text or "")
    if is_specific_call(text or ""):
        found.append("a specific buy / sell call")
    return list(dict.fromkeys(found))


def _axis(raw: Any, kind: str, problems: List[str]) -> Tuple[Axis, ...]:
    out: List[Axis] = []
    seen: Set[str] = set()
    for i, row in enumerate(raw if isinstance(raw, list) else []):
        where = f"{kind}[{i}]"
        if not isinstance(row, dict):
            problems.append(f"{where}: not an object")
            continue
        ident, label, definition = str(row.get("id", "")), str(row.get("label", "")).strip(), str(row.get("definition", "")).strip()
        if not _ID.match(ident):
            problems.append(f"{where}: id {ident!r} must be lower_snake_case")
        if ident in seen:
            problems.append(f"{where}: duplicate id {ident!r}")
        seen.add(ident)
        if not label or not definition:
            problems.append(f"{where} ({ident}): needs a label and a definition")
        out.append(Axis(ident, str(row.get("code", "")), label, definition, bool(row.get("user_built", False))))
    if not out:
        problems.append(f"{kind}: none defined")
    return tuple(out)


def _vocab(raw: Any, problems: List[str]) -> Dict[str, Tuple[Dict[str, str], ...]]:
    out: Dict[str, Tuple[Dict[str, str], ...]] = {}
    raw = raw if isinstance(raw, dict) else {}
    for key in TAG_KEYS:
        values = raw.get(key)
        if not isinstance(values, list) or not values:
            problems.append(f"tags.{key}: missing or empty")
            out[key] = ()
            continue
        out[key] = tuple({"id": str(v.get("id", "")), "label": str(v.get("label", ""))} for v in values if isinstance(v, dict))
        ids = [v["id"] for v in out[key]]
        if len(ids) != len(set(ids)) or any(not _ID.match(v) for v in ids):
            problems.append(f"tags.{key}: ids must be unique lower_snake_case")
    for key in raw:
        if key not in TAG_KEYS:
            problems.append(f"tags.{key}: unknown tag family (known: {', '.join(TAG_KEYS)})")
    return out


REGIME_AXES: Tuple[str, ...] = ("trend", "volatility")


def _regime_vocab(raw: Any, problems: List[str]) -> Tuple[Dict[str, str], ...]:
    """U9 TX3: the regimes a screen may declare, each on one axis (trend: trending / range-bound; volatility)."""
    if raw is None:
        return ()
    out: List[Dict[str, str]] = []
    for v in raw if isinstance(raw, list) else []:
        entry = {"id": str(v.get("id", "")), "axis": str(v.get("axis", "")), "label": str(v.get("label", ""))} if isinstance(v, dict) else {}
        if not entry or not _ID.match(entry["id"]) or entry["axis"] not in REGIME_AXES:
            problems.append(f"regimes: each needs a lower_snake_case id and an axis ({', '.join(REGIME_AXES)})")
            continue
        out.append(entry)
    if len({e["id"] for e in out}) != len(out):
        problems.append("regimes: ids must be unique")
    return tuple(out)


def _screen_regimes(where: str, value: Any, vocab: Tuple[Dict[str, str], ...], problems: List[str]) -> Tuple[str, ...]:
    if value is None:
        return ()
    values = list(value) if isinstance(value, list) else []
    axis_of = {e["id"]: e["axis"] for e in vocab}
    bad = [v for v in values if v not in axis_of]
    if not isinstance(value, list) or bad:
        problems.append(f"{where}: regimes must be a list from the vocabulary ({', '.join(axis_of)})")
        return ()
    axes = [axis_of[v] for v in values]
    if len(axes) != len(set(axes)):
        problems.append(f"{where}: at most one regime per axis (listing both sides of an axis means any - leave it out)")
    return tuple(values)


def _text_problems(where: str, text: str, limit: int) -> List[str]:
    out: List[str] = []
    if not text.strip():
        out.append(f"{where}: empty")
    if "\n" in text or len(text) > limit:
        out.append(f"{where}: at most {limit} characters on one line ({len(text)} given)")
    words = advice_words(text)
    if words:
        out.append(f"{where}: advice wording is not allowed ({', '.join(words)})")
    return out


def _screens(raw: Any, methods: Dict[str, Axis], setups: Set[str], vocab: Dict[str, Tuple[Dict[str, str], ...]],
             problems: List[str], regime_vocab: Tuple[Dict[str, str], ...] = ()) -> Tuple[Screen, ...]:
    out: List[Screen] = []
    seen: Set[str] = set()
    for i, row in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(row, dict):
            problems.append(f"screens[{i}]: not an object")
            continue
        ident = str(row.get("id", ""))
        where = f"screen {ident or i!r}"
        if not _ID.match(ident):
            problems.append(f"{where}: id must be lower_snake_case with dots")
        if ident in seen:
            problems.append(f"{where}: duplicate id")
        seen.add(ident)
        source = str(row.get("source", ""))
        if not source:
            problems.append(f"{where}: needs a source (the template module it comes from)")
        elif not ident.startswith(source + "."):
            problems.append(f"{where}: id must start with its source ({source}.)")
        name, definition = str(row.get("name", "")), str(row.get("definition", ""))
        problems += _text_problems(f"{where} name", name, MAX_NAME_CHARS)
        problems += _text_problems(f"{where} definition", definition, MAX_DEFINITION_CHARS)
        method, setup = str(row.get("method", "")), str(row.get("setup", ""))
        if method not in methods:
            problems.append(f"{where}: not filed on the method axis ({method!r} is not one of {', '.join(methods)})")
        elif methods[method].user_built:
            problems.append(f"{where}: a pre-built screen cannot be filed under {methods[method].label!r}")
        if setup not in setups:
            problems.append(f"{where}: not filed on the setup axis ({setup!r} is not one of {', '.join(sorted(setups))})")
        tags: Dict[str, Any] = {}
        for key in TAG_KEYS:
            allowed = {v["id"] for v in vocab.get(key, ())}
            value = row.get(key)
            if key in LIST_TAGS:
                values = list(value) if isinstance(value, list) else []
                if not values:
                    problems.append(f"{where}: {key} must be a non-empty list")
            else:
                values = [] if isinstance(value, (list, dict)) or value in (None, "") else [value]
                if not values:
                    problems.append(f"{where}: {key} must be one value")
            bad = [v for v in values if v not in allowed]
            if bad:
                problems.append(f"{where}: {key} {', '.join(map(repr, bad))} not in the vocabulary ({', '.join(sorted(allowed))})")
            tags[key] = values if key in LIST_TAGS else (values[0] if values else None)
        for flag in ("evidence_ready", "fitness_available"):
            if not isinstance(row.get(flag, False), bool):
                problems.append(f"{where}: {flag} must be true or false")
        unknown = set(row) - {"id", "source", "name", "definition", "method", "setup", "evidence_ready", "fitness_available", "regimes",
                              *TAG_KEYS}
        if unknown:
            problems.append(f"{where}: unknown fields {', '.join(sorted(unknown))}")
        regimes = _screen_regimes(where, row.get("regimes"), regime_vocab, problems)
        out.append(Screen(ident, source, name, definition, method, setup, tags, bool(row.get("evidence_ready", False)),
                          bool(row.get("fitness_available", False)), regimes))
    return tuple(out)


def parse(data: Dict[str, Any]) -> Tuple[Taxonomy, List[str]]:
    """(taxonomy, lint problems). Problems are listed, not raised, so a test shows all of them at once."""
    problems: List[str] = []
    methods = _axis(data.get("methods"), "methods", problems)
    setups = _axis(data.get("setups"), "setups", problems)
    vocab = _vocab(data.get("tags"), problems)
    for axis in (*methods, *setups):
        problems += [p for p in _text_problems(f"axis {axis.id} label", axis.label, MAX_NAME_CHARS) if "empty" not in p]
        problems += [p for p in _text_problems(f"axis {axis.id} definition", axis.definition, MAX_DEFINITION_CHARS) if "empty" not in p]
    for key, values in vocab.items():
        for v in values:
            problems += [p for p in _text_problems(f"tag {key}.{v['id']} label", v["label"], MAX_NAME_CHARS)]
    regime_vocab = _regime_vocab(data.get("regimes"), problems)
    for r in regime_vocab:
        problems += _text_problems(f"regime {r['id']} label", r["label"], MAX_NAME_CHARS)
    screens = _screens(data.get("screens"), {m.id: m for m in methods}, {s.id for s in setups}, vocab, problems, regime_vocab)
    version = str(data.get("version", ""))
    if not version:
        problems.append("version: missing")
    return Taxonomy(version, methods, setups, vocab, screens, regime_vocab), problems


def lint(path: Path = DATA_FILE) -> List[str]:
    return parse(json.loads(path.read_text(encoding="utf-8")))[1]


@lru_cache(maxsize=4)
def _load(path: str) -> Taxonomy:
    taxonomy, problems = parse(json.loads(Path(path).read_text(encoding="utf-8")))
    if problems:
        raise TaxonomyError(problems)
    return taxonomy


def load(path: Path = DATA_FILE) -> Taxonomy:
    """The taxonomy, linted; a file that fails its lint rules is refused (CI catches it first)."""
    return _load(str(path))


def coverage_problems(taxonomy: Optional[Taxonomy] = None, registered: Optional[Dict[str, Source]] = None) -> List[str]:
    """The CI guard: every template of every registered source is filed, every filed id exists in its source, and every
    filed source is registered (or is a frontend source, which the test suite checks against its own file)."""
    tax = taxonomy or load()
    regs = sources() if registered is None else registered
    problems: List[str] = []
    filed: Dict[str, Set[str]] = {}
    for s in tax.screens:
        filed.setdefault(s.source, set()).add(s.id)
    for name, src in regs.items():
        have = list(src.ids())
        for ident in have:
            if ident not in filed.get(name, set()):
                problems.append(f"template {ident!r} from source {name!r} is not filed in the taxonomy (add it to app/screener/data/taxonomy.json)")
        for ident in sorted(filed.get(name, set()) - set(have)):
            problems.append(f"taxonomy entry {ident!r} names a template that source {name!r} does not have")
    for name in sorted(filed):
        if name not in regs and name not in FRONTEND_SOURCES:
            problems.append(f"taxonomy source {name!r} is not registered (register_source) - its screens would never show")
    return problems


def visible(taxonomy: Taxonomy, hidden_sources: Iterable[str] = ()) -> List[Screen]:
    hidden = set(hidden_sources)
    return [s for s in taxonomy.screens if s.source not in hidden]


def matrix(taxonomy: Taxonomy, screens: Sequence[Screen]) -> Dict[str, Dict[str, int]]:
    """Method -> setup -> number of screens, with every cell present (an empty cell is 0, shown as empty)."""
    out = {m.id: {s.id: 0 for s in taxonomy.setups} for m in taxonomy.methods}
    for s in screens:
        out[s.method][s.setup] += 1
    return out


def payload(taxonomy: Taxonomy, *, hidden_sources: Iterable[str] = (), method: Optional[str] = None,
            setup: Optional[str] = None) -> Dict[str, Any]:
    """The API shape: both axes, the tag vocabulary, the screens (optionally one method and / or setup) and the full
    matrix of counts (always over every visible screen, so the rail and chips can show what each cell holds)."""
    shown = visible(taxonomy, hidden_sources)
    picked = [s for s in shown if (method is None or s.method == method) and (setup is None or s.setup == setup)]
    return {"version": taxonomy.version, "methods": [m.as_dict() for m in taxonomy.methods],
            "setups": [s.as_dict() for s in taxonomy.setups],
            "tags": {k: [dict(v) for v in values] for k, values in taxonomy.tags.items()},
            "screens": [s.as_dict() for s in picked], "matrix": matrix(taxonomy, shown), "total": len(shown), "note": NOTE,
            "regimes": [dict(r) for r in taxonomy.regimes]}


__all__ = ["NOTE", "DATA_FILE", "FRONTEND_SOURCES", "TAG_KEYS", "Axis", "Screen", "Source", "Taxonomy", "TaxonomyError", "advice_words",
           "coverage_problems", "lint", "load", "matrix", "parse", "payload", "register_source", "sources", "unregister_source",
           "visible"]


def default_hidden() -> List[str]:
    """Registered sources whose flag starts OFF (U6 breakouts): what a new organisation's gallery leaves out."""
    from app.platform.controls import DEFAULT_OFF_FLAGS
    return [name for name, src in _SOURCES.items() if src.flag in DEFAULT_OFF_FLAGS]


if __name__ == "__main__":      # regenerates the frontend's sample: python -m app.screener.taxonomy > ../frontend/src/screener/taxonomySample.json
    import app.main  # noqa: F401 - registers every template source (into the imported module, not this __main__ copy)
    from app.screener import taxonomy as _tx
    print(json.dumps(_tx.payload(_tx.load(), hidden_sources=_tx.default_hidden()), indent=1, ensure_ascii=False))
