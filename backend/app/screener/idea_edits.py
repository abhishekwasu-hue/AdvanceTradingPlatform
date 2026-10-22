"""Handover 11j step 6 "edits by chat" on a scan from an idea: "drop the volume condition", "also RSI above 50",
"weekly instead of daily", "change RSI to above 60". Deterministic, English only, no AI key - the same reader as
`app/screener/idea.py`:

* an edit only removes parts of the draft or adds parts the idea reader builds from REGISTRY functions; it never
  writes a condition of its own, so every part stays validated and flag-gated by the route like a typed screen;
* each edit comes back with one plain line of feedback (what was removed, added or changed, or why nothing changed);
* an added part replaces the part it contradicts on the same chart (an uptrend replaces a downtrend, RSI above 60
  replaces RSI near 40), and says so;
* trade wording ("tighter SL", "add a target") changes nothing: a scan finds matches, the trade decision stays the
  user's;
* the idea and its edits are replayed from the start on every call (`replay`), so a version is just "the idea plus its
  first n edits": stateless, reproducible, nothing stored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from app.screener import idea as reader

MAX_EDITS = 20
MAX_EDIT_CHARS = 300

NOT_UNDERSTOOD = ("ATP could not tell what to change. Try “drop the volume condition”, “also RSI above 50” "
                  "or “weekly instead of daily”.")

# Words that name a part of the scan -> the idea reader's rules they name.
_TOPICS: Tuple[Tuple[str, FrozenSet[str]], ...] = tuple((k, frozenset(v.split())) for k, v in (
    (r"volume", "volume_low volume_high"),
    (r"rsi", "rsi_level rsi_oversold rsi_overbought rsi_divergence"),
    (r"divergences?", "rsi_divergence"),
    (r"oversold", "rsi_oversold"), (r"overbought", "rsi_overbought"),
    (r"up[- ]?trend|trending up", "trend_up"), (r"down[- ]?trend|trending down", "trend_down"),
    (r"sideways|range|ranging", "range"),
    (r"trend", "trend_up trend_down range adx"),
    (r"strong trend|adx", "adx"),
    (r"zones?|areas?", "demand_zone supply_zone"),
    (r"demand|buyer|support", "demand_zone"), (r"supply|seller|resistance", "supply_zone"),
    (r"f\s?&\s?o|fno|f and o|futures and options|segment", "fno"),
    (r"moving averages?|averages?|ema|sma|dma|ma", "moving_average"),
    (r"change of character|choch", "choch"), (r"break of structure|bos", "bos"), (r"structure", "choch bos"),
    (r"patterns?|candles?|candlesticks?", "pattern candle"),
    (r"hammer|doji|engulfing|morning star|evening star|shooting star|pin bar|inside bar", "pattern"),
    (r"52[- ]week|new highs?|highs?", "new_high"), (r"new lows?|lows?", "new_low"),
    (r"retests?", "retest retest_down"),
    (r"break[- ]?outs?", "breakout"), (r"break[- ]?downs?", "breakdown"),
    (r"higher lows?", "higher_low"), (r"lower highs?", "lower_high"), (r"swings?", "higher_low lower_high"),
    (r"vwap", "vwap"), (r"supertrend", "supertrend"),
))
_TOPIC_RE = [(re.compile(rf"(?<![\w&])(?:{p})(?![\w&])", re.I), rules) for p, rules in _TOPICS]

# Parts that cannot hold together on one chart: an added one replaces the one already there.
_EXCLUSIVE: Tuple[FrozenSet[str], ...] = tuple(frozenset(g.split()) for g in (
    "trend_up trend_down range", "volume_low volume_high", "rsi_level rsi_oversold rsi_overbought", "new_high new_low",
    "rsi_divergence", "candle", "vwap", "supertrend", "adx",
))

_LEAD = re.compile(r"^(?:please|ok|okay|now|then|and|also|plus|add|include|with|only|but|just|make it|i want|show me)\b[\s,]*", re.I)
_REMOVE = re.compile(r"^(?:drop|remove|delete|take out|take away|without|no|skip|ignore|forget|leave out|exclude|get rid of)\s+"
                     r"(?:the\s+|a\s+|an\s+|my\s+|any\s+)?(?P<what>.+)$", re.I)
_REPLACE = re.compile(r"^(?:replace|change|swap|turn)\s+(?:the\s+)?(?P<old>.+?)\s+(?:with|to|for|into|by)\s+(?P<new>.+)$", re.I)
_INSTEAD = re.compile(r"^(?P<new>.+?)\s+instead\s+of\s+(?:the\s+|a\s+)?(?P<old>.+)$", re.I)
_TF_ONLY = re.compile(r"^(?:(?:make it|use|switch to|change(?: it| the timeframe| the chart| the scan)? to|scan on|scan|on|read)\s+)?"
                      r"(?:the\s+|a\s+)?(?P<tf>.+?)(?:\s+(?:chart|charts|timeframe|bars?|candles?))?$", re.I)
_TAIL = re.compile(r"\s+(?:condition|conditions|part|parts|filter|filters|check|checks|block|blocks|rule|rules)$", re.I)


@dataclass
class Op:
    """One edit, read: `remove` (rules named, optional chart), `add` (a phrase for the idea reader), `replace` (both),
    `tf` (the scan's timeframe), or `none` (with the reason)."""
    kind: str
    edit: str
    rules: FrozenSet[str] = frozenset()
    chart: Optional[str] = None
    phrase: str = ""
    tf: str = ""
    tf_from: str = ""
    scan: bool = True                  # a `tf` edit moves the scan's own timeframe (set by `replay`)
    why: str = ""


@dataclass
class Feedback:
    edit: str
    kind: str          # removed / added / replaced / timeframe / unchanged
    message: str
    changed: bool
    removed: List[str] = field(default_factory=list)
    added: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {"edit": self.edit, "kind": self.kind, "message": self.message, "changed": self.changed,
                "removed": list(self.removed), "added": list(self.added)}


def _tf_of(words: str) -> Optional[str]:
    """The timeframe the words are, when they are nothing else ("weekly", "1h", "the 15 minute")."""
    w = re.sub(r"^(?:the|a)\s+", "", words.strip(" .,!"), flags=re.I)
    for pattern, tf in reader._TF_RE:
        m = pattern.match(w)
        if m and m.end() == len(w):
            return tf
    return None


def _chart_in(words: str) -> Optional[str]:
    for pattern, tf in reader._TF_RE:
        if pattern.search(words):
            return tf
    return None


def _topics(words: str) -> FrozenSet[str]:
    """The rules the words name. A narrower word wins over a wider one it sits in: "demand zone" names the demand
    zone only (not every zone), "up trend" the up trend only, "RSI divergence" the divergence only."""
    hits = [rules for pattern, rules in _TOPIC_RE if pattern.search(words)]
    kept = [h for h in hits if not any(o < h for o in hits)]
    return frozenset().union(*kept) if kept else frozenset()


def parse(edit: str) -> Op:
    """One edit -> what it asks for. Pure."""
    raw = re.sub(r"\s+", " ", (edit or "")[:MAX_EDIT_CHARS]).strip()
    if any(ord(ch) > 127 for ch in raw.replace("’", "'")):
        return Op("none", raw, why="only English is supported yet")
    s = raw.strip(" .!?")
    while True:
        stripped = _LEAD.sub("", s, count=1).strip()
        if stripped == s:
            break
        s = stripped
    if not s:
        return Op("none", raw, why=NOT_UNDERSTOOD)
    m = _TF_ONLY.match(s)
    tf = _tf_of(m.group("tf")) if m else None
    if tf:
        return Op("tf", raw, tf=tf)
    m = _INSTEAD.match(s)
    if m:
        new_tf = _tf_of(m.group("new"))
        old_tf = _tf_of(re.sub(r"\s+(?:chart|charts|timeframe)$", "", m.group("old").strip(" .")))
        if new_tf and old_tf:
            return Op("tf", raw, tf=new_tf, tf_from=old_tf)
        return _replace(raw, m.group("old"), m.group("new"))
    m = _REPLACE.match(s)
    if m:
        return _replace(raw, m.group("old"), m.group("new"))
    m = _REMOVE.match(s)
    if m:
        what = _TAIL.sub("", m.group("what").strip(" ."))
        return Op("remove", raw, rules=_topics(what), chart=_chart_in(what), phrase=what)
    return Op("add", raw, phrase=s)


def _replace(raw: str, old: str, new: str) -> Op:
    old = _TAIL.sub("", old.strip(" ."))
    new_tf, old_tf = _tf_of(new), _tf_of(re.sub(r"\s+(?:chart|charts|timeframe)$", "", old))
    if new_tf and old_tf:
        return Op("tf", raw, tf=new_tf, tf_from=old_tf)
    return Op("replace", raw, rules=_topics(old), chart=_chart_in(old), phrase=new.strip(" ."), why=old)


def _quoted(reads: Sequence[str]) -> str:
    return "; ".join(reads)


def _tf_words(tf: str) -> str:
    return reader._tf_name(tf) or tf


def _matches(part: reader.Part, op: Op, base_tf: str) -> bool:
    if part.rule not in op.rules:
        return False
    return op.chart is None or (part.tf or base_tf) == op.chart


def _conflicts(old: reader.Part, new: reader.Part) -> bool:
    return old.tf == new.tf and any(old.rule in g and new.rule in g for g in _EXCLUSIVE)


def _unsupported_line(phrase: str, found: List[Dict[str, str]]) -> str:
    first = found[0] if found else {"phrase": phrase, "why": reader.NOT_SUPPORTED}
    if first["why"] == reader.NOT_SUPPORTED:
        return f"“{first['phrase']}” is not supported yet, so nothing changed."
    return f"“{first['phrase']}”: {first['why']}. Nothing changed."


def _add(parts: List[reader.Part], phrase: str, base_tf: str) -> Tuple[List[reader.Part], List[Tuple[Optional[reader.Part], reader.Part]], List[Dict[str, str]], bool]:
    """The parts with the phrase's parts added: [(the part it replaced or None, the new part)], the phrase's words no
    rule knows, and whether every new part was already there."""
    draft = reader.read_idea(phrase, base_tf)
    out = list(parts)
    changes: List[Tuple[Optional[reader.Part], reader.Part]] = []
    already = bool(draft.parts)
    for new in draft.parts:
        if any(p.condition == new.condition for p in out):
            continue
        already = False
        gone = next((p for p in out if _conflicts(p, new)), None)
        if gone is not None:
            out[out.index(gone)] = new
        else:
            out.append(new)
        changes.append((gone, new))
    return out, changes, draft.unsupported, already


def apply(parts: List[reader.Part], op: Op, base_tf: str, tf_before: str) -> Tuple[List[reader.Part], Feedback]:
    """One edit on the parts (read at the scan's final timeframe `base_tf`) -> the new parts and the plain feedback."""
    if op.kind == "none":
        return parts, Feedback(op.edit, "unchanged", op.why if op.why == NOT_UNDERSTOOD else f"{op.why[:1].upper()}{op.why[1:]}, so nothing changed.", False)
    if op.kind == "tf":
        if op.tf == (op.tf_from or tf_before):
            return parts, Feedback(op.edit, "unchanged", f"The scan already reads the {_tf_words(op.tf)} chart, so nothing changed."
                                   if not op.tf_from else "Both are the same chart, so nothing changed.", False)
        if op.tf_from and op.scan:
            return parts, Feedback(op.edit, "timeframe", f"The {_tf_words(op.tf_from)} chart is now the {_tf_words(op.tf)} chart, in the scan "
                                   "and in the parts that named it.", True)
        if op.tf_from:
            return parts, Feedback(op.edit, "timeframe", f"The parts that named the {_tf_words(op.tf_from)} chart now read the "
                                   f"{_tf_words(op.tf)} chart. The scan's own chart stays the same.", True)
        return parts, Feedback(op.edit, "timeframe", f"The scan now reads the {_tf_words(op.tf)} chart. Parts that named their own chart keep it.", True)
    if op.kind == "remove":
        if not op.rules:
            return parts, Feedback(op.edit, "unchanged", f"ATP could not tell which part “{op.phrase}” means. Name it, for example "
                                   "“drop the volume condition”.", False)
        gone = [p for p in parts if _matches(p, op, base_tf)]
        if not gone:
            return parts, Feedback(op.edit, "unchanged", f"Your scan has no “{op.phrase}” part, so nothing changed.", False)
        left = [p for p in parts if p not in gone]
        message = f"Removed: {_quoted([p.reads for p in gone])}."
        if not left:
            message += " No condition is left; add one, or go back to an earlier version."
        return left, Feedback(op.edit, "removed", message, True, removed=[p.reads for p in gone])
    if op.kind == "replace":
        if not op.rules:
            return parts, Feedback(op.edit, "unchanged", f"ATP could not tell which part “{op.why}” means, so nothing changed.", False)
        gone = [p for p in parts if _matches(p, op, base_tf)]
        if not gone:
            return parts, Feedback(op.edit, "unchanged", f"Your scan has no “{op.why}” part, so nothing changed.", False)
        kept = [p for p in parts if p not in gone]
        added, changes, unsupported, _ = _add(kept, op.phrase, base_tf)
        if not changes:                       # "change RSI to above 60": the new words read together with the old ones
            added, changes, unsupported, _ = _add(kept, f"{op.why} {op.phrase}", base_tf)
        if not changes:
            return parts, Feedback(op.edit, "unchanged", _unsupported_line(op.phrase, unsupported), False)
        at = parts.index(gone[0])
        new_parts = [c[1] for c in changes]
        rest = [p for p in added if p not in new_parts]
        merged = rest[:at] + new_parts + rest[at:]
        before = [p.reads for p in gone] + [c[0].reads for c in changes if c[0] is not None]
        after = [c[1].reads for c in changes]
        return merged, Feedback(op.edit, "replaced", f"Changed: {_quoted(before)} → {_quoted(after)}.", True, removed=before, added=after)
    new_parts, changes, unsupported, already = _add(parts, op.phrase, base_tf)
    if already:
        return parts, Feedback(op.edit, "unchanged", "That is already in your scan, so nothing changed.", False)
    if not changes:
        return parts, Feedback(op.edit, "unchanged", _unsupported_line(op.phrase, unsupported), False)
    lines: List[str] = []
    swapped = [(a, b) for a, b in changes if a is not None]
    plain = [b for a, b in changes if a is None]
    if plain:
        lines.append(f"Added: {_quoted([b.reads for b in plain])}.")
    if swapped:
        lines.append(f"Changed: {_quoted([a.reads for a, _ in swapped])} → {_quoted([b.reads for _, b in swapped])}.")
    if unsupported:
        lines.append(f"Not supported yet: {', '.join(chr(0x201c) + u['phrase'] + chr(0x201d) for u in unsupported)}.")
    return new_parts, Feedback(op.edit, "replaced" if swapped and not plain else "added", " ".join(lines), True,
                               removed=[a.reads for a, _ in swapped], added=[b.reads for _, b in changes])


# Timeframe words the idea reader reads back (for "X instead of Y").
_TF_SAY = {"1M": "monthly", "1w": "weekly", "1d": "daily", "1h": "hourly", "30m": "30 minute", "15m": "15 minute", "5m": "5 minute"}


def _swap_tf(text: str, old: str, new: str) -> str:
    word = _TF_SAY.get(new)
    if not word:
        return text
    for pattern, tf in reader._TF_RE:
        if tf == old:
            text = pattern.sub(word, text)
    return text


def replay(text: str, base_tf: str, edits: Sequence[str]) -> Tuple[reader.Draft, str, List[Feedback]]:
    """The idea with its edits applied in order -> the draft, the scan's timeframe after the edits, and one feedback per
    edit. Every part is read at the final timeframe, so a part that named the chart the scan now reads carries no
    `@tf`, and one that named another chart keeps it."""
    ops = [parse(e) for e in list(edits)[:MAX_EDITS]]
    before: List[str] = []
    final_tf = base_tf
    for op in ops:
        before.append(final_tf)
        if op.kind != "tf" or op.tf == (op.tf_from or final_tf):
            continue
        if op.tf_from:                            # "weekly instead of daily": the idea's own Daily words read Weekly
            text = _swap_tf(text, op.tf_from, op.tf)
        op.scan = not op.tf_from or op.tf_from == final_tf
        if op.scan:
            final_tf = op.tf
    draft = reader.read_idea(text, final_tf)
    parts = list(draft.parts)
    feedback: List[Feedback] = []
    for op, tf_before in zip(ops, before):
        parts, fb = apply(parts, op, final_tf, tf_before)
        feedback.append(fb)
    return reader.Draft(parts, draft.unsupported), final_tf, feedback


__all__ = ["MAX_EDITS", "MAX_EDIT_CHARS", "NOT_UNDERSTOOD", "Op", "Feedback", "parse", "apply", "replay"]
