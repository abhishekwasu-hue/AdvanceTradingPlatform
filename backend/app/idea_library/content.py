"""Idea library (owner order ATP_SESSION_IDEA_LIBRARY.md, T1-T8), slice 1: the ideas as white-box method cards.

The content is data (`ideas.json`), never code: per idea the plain-words description, the structure words, the logic
(context, area, trigger, invalidation, evidence), the settings the user owns, the decisions that stay the user's, an
honest provenance and its "Try as a scan" screens. Education, not advice: the content guard (`guard`) keeps security
names, prices, buy / sell / target / stop wording and performance claims out of every human-readable line, and the tests
run it over the whole file.

A scan is ScreenQL built ONLY from the existing registry (`app.screener.registry`); every number in it is a `$setting`
declared on the idea (type, default, bounds), so the user sees and changes it. Where an idea needs something the registry
does not have yet, the idea says so (`scan_gaps`) instead of inventing a rule. Nothing here places, sizes or exits an
order.

Slice 2 (IDEA-1): "Adopt as a paper strategy". An idea carries `adopt` only when it maps HONESTLY onto a built-in strategy
template (`app.strategy_templates`): the same structure (context, area, trigger, invalidation), never a stand-in that
reads differently. The card then links to the paper bot form with the template picked and the mapped settings as the
pre-fill; the user reviews and saves it himself, PAPER only. `ADOPT_REVIEW` records, for every idea without a mapping,
why no template matches today (the tests keep both in step). The first review (D1, D2, B1, B3) found none; the second
(slice 3, the templates of families A-G) maps T2 onto A2 and T5 onto A5, whose signals are exactly those ideas' own scans
(the tests compare them bar by bar); each mapping carries a `note` saying where the paper strategy still reads less than
the card (OPEN_QUESTIONS IDEA-10, IDEA-13).

Slice 3: T7 gets its scans (the second top / bottom at an area, from the existing swing, reversal and zone functions);
T6 still has none - no registry function reads "price at a channel line" (IDEA-3, IDEA-12).

Slice 4 (IDEA-12, IDEA-14): two causal registry functions close those gaps, and the scans are still built from the
registry only. `ChannelLine(side, touches, edge_touches)` is the B5 channel's trend-side line on every bar (the channel
B5 reads from the bars before the bar, projected to it), so T6 is a rejection at that line. `PriorSwingHigh` /
`PriorSwingLow(degree, back)` are the confirmed swings before the latest one, so T7 reads a head and shoulders: the right
shoulder rejected at the left shoulder's height below the head, and the retest of the broken neckline (the swing that
broke, through the B9 swing retest). The re-check of the template matches (T1, T3, T4, T6, T7, T8) found none new.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Literal, Optional, Set, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

CONTENT_PATH = Path(__file__).parent / "ideas.json"
FLAG = "idea_library"
IDEA_IDS: Tuple[str, ...] = ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8")
# The Screener's own flags a scan needs before it can open in the builder (on top of the flags of the functions it uses).
SCREENER_FLAGS: Tuple[str, ...] = ("screener_v2", "screener_builder_u2")
_KEY = r"^[a-z][a-z0-9_]{0,31}$"

Value = Union[StrictBool, StrictInt, StrictFloat, StrictStr]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Choice(_Strict):
    value: Union[StrictInt, StrictStr]
    label: str = Field(min_length=1, max_length=60)


class Setting(_Strict):
    """One user setting a scan reads as `$key`: what the card shows (label, help) and what it accepts."""
    key: str = Field(pattern=_KEY)
    label: str = Field(min_length=1, max_length=80)
    help: str = Field(min_length=1, max_length=300)
    type: Literal["number", "choice"]
    default: Union[StrictInt, StrictFloat, StrictStr]
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    choices: Tuple[Choice, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> "Setting":
        if self.type == "number":
            if self.min is None or self.max is None or self.step is None or self.min > self.max or self.choices:
                raise ValueError(f"{self.key}: a number setting needs min <= max, a step and no choices")
            if isinstance(self.default, str) or not self.min <= float(self.default) <= self.max:
                raise ValueError(f"{self.key}: the default must be a number within the bounds")
        else:
            if not self.choices or self.min is not None or self.max is not None:
                raise ValueError(f"{self.key}: a choice setting needs its choices and no bounds")
            if self.default not in [c.value for c in self.choices]:
                raise ValueError(f"{self.key}: the default must be one of the choices")
        return self

    def values_to_check(self) -> List[Any]:
        """The default and every edge a scan must validate at: the bounds of a number, every choice."""
        if self.type == "number":
            assert self.min is not None and self.max is not None
            whole = all(float(v) == int(v) for v in (self.min, self.max, self.default, self.step or 1))
            edges = [self.min, self.max]
            return [self.default] + [int(v) if whole else float(v) for v in edges]
        return [c.value for c in self.choices]


class Scan(_Strict):
    id: str = Field(pattern=_KEY)
    label: str = Field(min_length=1, max_length=60)
    base_tf: Literal["5m", "15m", "30m", "1h", "1d"]
    text: str = Field(min_length=1, max_length=2000)


class Logic(_Strict):
    context: str = Field(min_length=1, max_length=400)
    area: str = Field(min_length=1, max_length=400)
    trigger: str = Field(min_length=1, max_length=400)
    invalidation: str = Field(min_length=1, max_length=400)
    evidence: Tuple[str, ...] = Field(min_length=1)


class Adopt(_Strict):
    """An honest mapping onto a built-in strategy template: the template, its chart timeframe and the settings it starts
    with (a pre-fill the user changes; every other setting keeps the template's default)."""
    template: str = Field(pattern=r"^[A-G][0-9]{1,2}$")
    timeframe: Optional[str] = Field(default=None, max_length=4)
    params: Dict[str, Value] = Field(default_factory=dict, max_length=40)
    # slice 3: what the paper strategy reads differently from the card, in plain words (shown next to the button)
    note: Optional[str] = Field(default=None, min_length=1, max_length=300)


class Idea(_Strict):
    id: str = Field(pattern=r"^T[1-8]$")
    name: str = Field(min_length=1, max_length=80)
    group: str = Field(pattern=_KEY)
    difficulty: Literal["Beginner", "Intermediate"]
    one_line: str = Field(min_length=1, max_length=200)
    works_best_when: str = Field(min_length=1, max_length=200)
    tends_to_fail_when: str = Field(min_length=1, max_length=200)
    plain: Tuple[str, ...] = Field(min_length=1)
    logic: Logic
    structure_words: Tuple[str, ...] = Field(min_length=1)
    settings: Tuple[Setting, ...] = ()
    your_decisions: Tuple[str, ...] = Field(min_length=1)
    provenance: str = Field(min_length=1, max_length=400)
    scans: Tuple[Scan, ...] = ()
    scan_gaps: Optional[str] = Field(default=None, max_length=300)
    adopt: Optional[Adopt] = None

    @model_validator(mode="after")
    def _consistent(self) -> "Idea":
        keys = [s.key for s in self.settings]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{self.id}: setting keys must be unique")
        if len({s.id for s in self.scans}) != len(self.scans):
            raise ValueError(f"{self.id}: scan ids must be unique")
        used: Set[str] = set()
        for scan in self.scans:
            refs = set(re.findall(r"\$([a-z][a-z0-9_]*)", scan.text))
            unknown = refs - set(keys)
            if unknown:
                raise ValueError(f"{self.id}.{scan.id}: unknown setting(s) {', '.join(sorted(unknown))}")
            used |= refs
        if set(keys) - used:
            raise ValueError(f"{self.id}: settings no scan reads: {', '.join(sorted(set(keys) - used))}")
        if not self.scans and not self.scan_gaps:
            raise ValueError(f"{self.id}: an idea without a scan must say why (scan_gaps)")
        return self

    def defaults(self) -> Dict[str, Any]:
        return {s.key: s.default for s in self.settings}


class Group(_Strict):
    id: str = Field(pattern=_KEY)
    name: str = Field(min_length=1, max_length=40)


class Option(_Strict):
    id: str = Field(pattern=_KEY)
    label: str = Field(min_length=1, max_length=60)
    ideas: Tuple[str, ...] = ()


class Question(_Strict):
    id: str = Field(pattern=_KEY)
    text: str = Field(min_length=1, max_length=120)
    options: Tuple[Option, ...] = Field(min_length=2)


class Beginner(_Strict):
    title: str = Field(min_length=1, max_length=60)
    intro: str = Field(min_length=1, max_length=200)
    questions: Tuple[Question, ...] = Field(min_length=1)


class Header(_Strict):
    title: str = Field(min_length=1, max_length=60)
    subtitle: str = Field(min_length=1, max_length=200)
    frame: Tuple[str, ...] = Field(min_length=1)
    adopt_later: str = Field(min_length=1, max_length=300)


class Library(_Strict):
    schema_: Literal["atp.ideas/1"] = Field(alias="schema")
    header: Header
    groups: Tuple[Group, ...] = Field(min_length=1)
    beginner: Beginner
    ideas: Tuple[Idea, ...] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, populate_by_name=True)

    @model_validator(mode="after")
    def _consistent(self) -> "Library":
        ids = [i.id for i in self.ideas]
        if tuple(ids) != IDEA_IDS:
            raise ValueError(f"the library holds exactly {', '.join(IDEA_IDS)} in order, got {', '.join(ids)}")
        groups = {g.id for g in self.groups}
        for idea in self.ideas:
            if idea.group not in groups:
                raise ValueError(f"{idea.id}: unknown group {idea.group!r}")
        for q in self.beginner.questions:
            for o in q.options:
                bad = [i for i in o.ideas if i not in ids]
                if bad:
                    raise ValueError(f"beginner {q.id}.{o.id}: unknown idea(s) {', '.join(bad)}")
        return self

    def idea(self, idea_id: str) -> Optional[Idea]:
        return next((i for i in self.ideas if i.id == idea_id), None)


@lru_cache(maxsize=1)
def load() -> Library:
    return Library.model_validate_json(CONTENT_PATH.read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------------------------- the guard
# Public / education surface (CLAUDE.md "Signal language"): no security names, no prices, no buy / sell / target /
# stop-loss wording and no performance claims. Whole words, case-insensitive (also inside hyphenated words).
FORBIDDEN_WORDS: Tuple[str, ...] = (
    "buy", "buying", "bought", "sell", "selling", "sold", "target", "targets", "stop", "stops", "stoploss", "sl", "tp",
    "profit", "profits", "profitable", "guarantee", "guaranteed", "accuracy", "accurate", "winning", "jackpot",
    "recommend", "recommended", "recommendation", "tip", "tips", "multibagger",
)
FORBIDDEN_PHRASES: Tuple[str, ...] = (
    "stop-loss", "stop loss", "take profit", "win rate", "success rate", "hit rate", "sure shot", "risk-free", "risk free",
    "best strategy", "high probability", "returns of",
)
# Index and security names a card must never carry (the common Indian indices and the largest names; any other
# capitalised name is caught by the all-capitals rule below).
SECURITY_NAMES: Tuple[str, ...] = (
    "nifty", "banknifty", "finnifty", "midcpnifty", "sensex", "bankex", "reliance", "tcs", "infosys", "infy", "hdfc",
    "hdfcbank", "icici", "icicibank", "sbi", "sbin", "kotak", "axis", "itc", "wipro", "airtel", "bharti", "adani",
    "tata", "maruti", "bajaj", "larsen", "hindalco", "ongc", "ntpc", "sunpharma", "titan", "asianpaint", "nestle",
)
ALLOWED_CAPITALS: Tuple[str, ...] = ("ATP", "RSI")
# Digits may only appear in a timeframe word ("15-minute") or an idea id ("T5"): never a price or a value.
_ALLOWED_DIGITS = re.compile(r"\b(?:\d{1,2}-(?:minute|hour)|T[1-8])\b")
_WORD = re.compile(r"[A-Za-z]+")
_CURRENCY = re.compile(r"(?:₹|\bRs\b\.?|\bINR\b|\$)")


def guard(text: str) -> List[str]:
    """The problems of one human-readable line (empty = clean)."""
    problems: List[str] = []
    lower = text.lower()
    for word in _WORD.findall(text):
        w = word.lower()
        if w in FORBIDDEN_WORDS:
            problems.append(f"forbidden word {word!r}")
        if w in SECURITY_NAMES:
            problems.append(f"a security name {word!r}")
        if len(word) >= 2 and word.isupper() and word not in ALLOWED_CAPITALS:
            problems.append(f"an all-capitals name {word!r}")
    for phrase in FORBIDDEN_PHRASES:
        if phrase in lower:
            problems.append(f"forbidden phrase {phrase!r}")
    if re.search(r"[0-9]", _ALLOWED_DIGITS.sub("", text)):
        problems.append("a number (no prices or values on an education surface)")
    if _CURRENCY.search(text):
        problems.append("a currency sign")
    if re.search(r"[^A-Za-z0-9 ,;:.\-'()%?]", text):
        problems.append("a character outside plain English text")
    return problems


def readable_lines(library: Library) -> List[Tuple[str, str]]:
    """Every human-readable line of the library, with where it is - what the guard checks."""
    out: List[Tuple[str, str]] = []
    h = library.header
    out += [("header.title", h.title), ("header.subtitle", h.subtitle), ("header.adopt_later", h.adopt_later)]
    out += [(f"header.frame[{i}]", t) for i, t in enumerate(h.frame)]
    out += [(f"groups.{g.id}", g.name) for g in library.groups]
    b = library.beginner
    out += [("beginner.title", b.title), ("beginner.intro", b.intro)]
    for q in b.questions:
        out.append((f"beginner.{q.id}", q.text))
        out += [(f"beginner.{q.id}.{o.id}", o.label) for o in q.options]
    for idea in library.ideas:
        p = idea.id
        out += [(f"{p}.name", idea.name), (f"{p}.one_line", idea.one_line), (f"{p}.works_best_when", idea.works_best_when),
                (f"{p}.tends_to_fail_when", idea.tends_to_fail_when), (f"{p}.provenance", idea.provenance)]
        out += [(f"{p}.plain[{i}]", t) for i, t in enumerate(idea.plain)]
        lg = idea.logic
        out += [(f"{p}.logic.{k}", getattr(lg, k)) for k in ("context", "area", "trigger", "invalidation")]
        out += [(f"{p}.logic.evidence[{i}]", t) for i, t in enumerate(lg.evidence)]
        out += [(f"{p}.structure_words[{i}]", t) for i, t in enumerate(idea.structure_words)]
        out += [(f"{p}.your_decisions[{i}]", t) for i, t in enumerate(idea.your_decisions)]
        for s in idea.settings:
            out += [(f"{p}.settings.{s.key}.label", s.label), (f"{p}.settings.{s.key}.help", s.help)]
            out += [(f"{p}.settings.{s.key}.choice", c.label) for c in s.choices]
        out += [(f"{p}.scans.{s.id}.label", s.label) for s in idea.scans]
        if idea.scan_gaps:
            out.append((f"{p}.scan_gaps", idea.scan_gaps))
        if idea.adopt is not None and idea.adopt.note:
            out.append((f"{p}.adopt.note", idea.adopt.note))
    return out


def problems(library: Library) -> List[str]:
    """Every guard problem in the library ("where: problem"), plus any security name inside a scan's text."""
    out = [f"{where}: {p}" for where, text in readable_lines(library) for p in guard(text)]
    for idea in library.ideas:
        for scan in idea.scans:
            for literal in re.findall(r'"([^"]*)"', scan.text):
                if any(w.lower() in SECURITY_NAMES for w in _WORD.findall(literal)):
                    out.append(f"{idea.id}.scans.{scan.id}: a security name in {literal!r}")
    return out


# ----------------------------------------------------------------------------------------------- scans and the registry
def scan_params(idea: Idea, scan: Scan) -> Dict[str, Any]:
    """The defaults of the settings this scan reads (the validator refuses a value for a setting a screen does not use)."""
    used = set(re.findall(r"\$([a-z][a-z0-9_]*)", scan.text))
    return {k: v for k, v in idea.defaults().items() if k in used}


def check_scan(idea: Idea, scan: Scan, params: Optional[Dict[str, Any]] = None) -> List[str]:
    """Validation problems of one scan against the ScreenQL registry at the given settings (default: the defaults)."""
    from app.screener.parser import ScreenQLSyntaxError, parse
    from app.screener.validator import validate
    values = {**scan_params(idea, scan), **(params or {})}
    try:
        ast = parse(scan.text)
    except ScreenQLSyntaxError as e:
        return [f"syntax: {e}"]
    result = validate(ast, base_tf=scan.base_tf, params=values)
    return [p.message for p in result.problems]


def scan_flags(scan: Scan) -> List[str]:
    """The operator flags a scan needs: the Screener's own (it opens in the builder) and those of every function it uses."""
    from app.screener import nodes
    from app.screener.parser import parse
    from app.screener.registry import FUNCTIONS
    out: List[str] = list(SCREENER_FLAGS)
    for node in nodes.walk(parse(scan.text)):
        spec = FUNCTIONS.get(node.name) if isinstance(node, nodes.Call) else None
        if spec is None:
            continue
        for flag in (spec.flag, *spec.also_flags):
            if flag and flag not in out:
                out.append(flag)
    return out


def scan_payload(idea: Idea, scan: Scan, flags_on: Set[str]) -> Dict[str, Any]:
    """A scan as the card shows it: the ScreenQL with its `$settings`, the defaults, and whether it can open now
    (`available`: every flag it needs is on for the organisation; flag names are never sent to a user)."""
    return {"id": scan.id, "label": scan.label, "base_tf": scan.base_tf, "text": scan.text,
            "params": scan_params(idea, scan),
            "available": all(f in flags_on for f in scan_flags(scan))}


# --------------------------------------------------------------------------------------- adopt as a paper strategy
# Third round (slice 4, 23 Oct): the same library plus C4 (#416); still no template reads a market zone, a channel line
# or a swing pattern, so T1, T3, T4, T6, T7 and T8 keep their reasons (tests/test_idea_library_channel_swings.py pins that no template
# reads NearZone, ChannelLine or PriorSwingHigh / PriorSwingLow, so a new one triggers this review again).
# IDEA-1 review, second round (slice 3, 22 Oct: the templates A2, A5, B1-B11, C1-C5, D1-D7, E1-E5, F1, F2, G1-G11 on
# main). An idea is adoptable only through a template with the SAME structure (context, area, trigger, invalidation), never
# one with a similar name; otherwise its reason is here. Mapped: T2 -> A2 and T5 -> A5 (their signals are the ideas' own
# scans; tests/test_idea_library.py compares them bar by bar). The family A templates that read areas, channel lines and
# patterns (A1, A3, A4, A6, A7, A8) are not built (TPL-Q23).
ADOPT_REVIEW: Dict[str, str] = {
    "T1": "A pullback into a demand / supply area with a reaction there; no template reads an area (A1 waits for zone data "
          "in the template engine, TPL-Q23). D3 and E1 read a pullback to a moving average or an RSI zone, F1's sources a "
          "Supertrend flip or that average pullback: a different structure.",
    "T3": "RSI divergence INTO an area; E2 reads the divergence without an area, so it is not the same idea (A3 waits for "
          "areas, TPL-Q23 / TPL-Q57).",
    "T4": "A sweep beyond an obvious high / low and a close back inside an area; no template reads it. G7 is the sweep "
          "beyond the start of wave A inside a wave count, and B1's 'close back inside' is its exit, not a signal.",
    "T6": "A rejection at the channel line in the trend's favour; B3 signals the opposite (a close beyond the far edge) "
          "and C3 the retest of that broken edge. The scan reads the line now (ChannelLine, IDEA-12), but no template does: "
          "A6 is not built (TPL-Q23).",
    "T7": "Double top / bottom or head and shoulders at an area; no template reads swing patterns at an area (A7, TPL-Q23). "
          "B6 is a flag / pennant / triangle breakout and G5 / G10 are wave-count lines; the head and shoulders neckline "
          "retest is not C2 (C2 retests any broken swing, with no pattern before it).",
    "T8": "T1 and T2 reduced to direction. F2's range edges source is T2's reading, but F1's sources (a Supertrend flip, "
          "a moving-average pullback) are not T1, and one card cannot adopt half of the pack; adoptable once A1 joins F1 "
          "(TPL-Q50).",
}


def check_adopt(idea: Idea) -> List[str]:
    """Problems of an idea's mapping against the real template library (empty = it adopts as written)."""
    if idea.adopt is None:
        return []
    from app.strategy_templates import ids, library
    from app.strategy_templates.adapter import ENGINE_LABEL, TemplateStrategy
    from app.strategy_templates.compile import TemplateError
    a = idea.adopt
    try:
        template = library.get(a.template)
    except KeyError:
        return [f"{idea.id}: unknown template {a.template!r}"]
    tf = a.timeframe or next((t for t in template.allowed_timeframes if t in ENGINE_LABEL), None)
    if tf not in ENGINE_LABEL:
        return [f"{idea.id}: the timeframe {tf!r} cannot run as a paper strategy"]
    try:
        _, settings = ids.adopt(template, tf, None, dict(a.params))
        TemplateStrategy(template, settings)
    except TemplateError as exc:
        return [f"{idea.id}: {p}" for p in exc.problems]
    return []


def adopt_templates(library: Library) -> List[str]:
    """The template ids the library's mappings name (the route checks only these)."""
    out: List[str] = []
    for idea in library.ideas:
        if idea.adopt is not None and idea.adopt.template not in out:
            out.append(idea.adopt.template)
    return out


def adopt_payload(idea: Idea, adoptable: FrozenSet[str] = frozenset()) -> Dict[str, Any]:
    """What the card's "Adopt as a paper strategy" needs: available only for a mapped idea whose template the
    organisation can adopt now (`strategy_templates` on and every function it reads switched on)."""
    a = idea.adopt
    if a is None or a.template not in adoptable:
        return {"available": False}
    return {"available": True, "template": a.template, "timeframe": a.timeframe, "params": dict(a.params), "note": a.note}


def idea_payload(idea: Idea, flags_on: Set[str], adoptable: FrozenSet[str] = frozenset()) -> Dict[str, Any]:
    data = json.loads(idea.model_dump_json(exclude={"adopt"}))
    data["scans"] = [scan_payload(idea, s, flags_on) for s in idea.scans]
    data["adopt"] = adopt_payload(idea, adoptable)
    return data


def payload(library: Library, flags_on: Set[str], adoptable: FrozenSet[str] = frozenset()) -> Dict[str, Any]:
    return {"header": json.loads(library.header.model_dump_json()), "groups": [json.loads(g.model_dump_json()) for g in library.groups],
            "beginner": json.loads(library.beginner.model_dump_json()),
            "ideas": [idea_payload(i, flags_on, adoptable) for i in library.ideas]}


def needed_flags(library: Library) -> List[str]:
    """Every flag any scan in the library needs (the route reads only these)."""
    out: List[str] = []
    for idea in library.ideas:
        for scan in idea.scans:
            out += [f for f in scan_flags(scan) if f not in out]
    return out


__all__ = ["CONTENT_PATH", "FLAG", "IDEA_IDS", "SCREENER_FLAGS", "ADOPT_REVIEW", "Library", "Idea", "Adopt", "Scan", "Setting", "load",
           "guard", "scan_params", "readable_lines", "problems", "check_scan", "scan_flags", "scan_payload", "check_adopt",
           "adopt_templates", "adopt_payload", "idea_payload", "payload", "needed_flags"]
