"""U9 TX2 (docs/specs/ATP_SCREENER_TAXONOMY_ADDENDUM.md §3, §4 "TX2"): the library's confluence view, mirror screens
and watch-then-trigger lists - behind the `screener_tx2` flag (off by default), on top of `screener_v2` and
`screener_gallery`.

* **Pre-built screens as ScreenQL.** `resolve()` turns a filed screen id (taxonomy.json) into its text, timeframe and
  parameters: the U5 starters (`STARTERS`, the same text the funnel builds - frontend/src/screener/starters.json carries
  it too and both sides test it), the U7 Legends cards (their source with the default parameters) and the U6 breakout
  templates (`templates.screenql`).
* **Mirror screens.** Every long pre-built screen gets its short / breakdown mirror (`<id>.short`), generated, never
  maintained by hand: the breakout templates' mirror is the template's own down side (the breakout engine defines it);
  every other screen goes through `mirror.mirror` (the same rules on reflected prices), or says why it has none.
* **Confluence.** `confluence()` ranks symbols by how many different methods (taxonomy axis 1) flag them, then by how
  many screens, then by symbol - a count of agreement with each screen's result as its evidence, never a score, a pick
  or a recommendation.
* **Watch-then-trigger lists.** A list is a "ready" condition and a "trigger" condition. On the last closed bar, a
  symbol is *triggered* when the trigger holds and the ready condition held on the bar before (`transition_ast`:
  `Count(1, ready)[1] >= 1 AND trigger`), and *ready* when the ready condition holds and it has not triggered. The
  transition is itself a screen, so the existing screen alert rules give the alert on the transition (save it, then
  alert on it) - no second alert engine. Both read closed bars only (no look-ahead; Z10-a case in causal_cases).

Research only (ADR-0006): nothing here places, changes or blocks an order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.screener import mirror as mr
from app.screener import nodes as n
from app.screener import taxonomy as tx
from app.screener.parser import parse

FLAG = "screener_tx2"
MIRROR_SUFFIX = ".short"
MAX_CONFLUENCE_SCREENS = 6
MIN_CONFLUENCE_SCREENS = 2
SAVED_PREFIX = "saved:"
CONFLUENCE_NOTE = ("Agreement is a count of the screens and methods whose filters held on the data shown. It is not a score or "
                   "a ranking of quality, and it does not say what to trade.")
WATCH_NOTE = ("Ready: the ready condition holds on the last closed bar. Triggered: the trigger holds on the last closed bar "
              "and the ready condition held on the bar before. Save the trigger screen to get an alert on the transition.")
MIRROR_NOTE = ("A mirror is the same rules on reflected prices (highs and lows swap, a rise becomes a fall). It is generated "
               "from the long screen, never edited by hand.")
# The U5 starters (frontend/src/screener/starters.json "text": what FunnelCanvas STARTER_BUILDS print; tested both sides).
STARTERS: Dict[str, Tuple[str, str]] = {
    "starter.momentum": ("RSI(14) > 60 AND close > EMA(close, 20)", "1d"),
    "starter.oversold": ("RSI(14) <= 30", "1d"),
    "starter.daily_trend": ("close@1d > EMA(close, 50)@1d", "1d"),
}


@dataclass(frozen=True)
class Resolved:
    """A runnable screen of the library: a filed pre-built screen, its mirror or a saved screen of the organisation."""
    ref: str
    name: str
    method: str
    setup: str
    direction: str
    source: str
    text: str
    base_tf: str
    params: Dict[str, Any] = field(default_factory=dict)
    mirror_of: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"ref": self.ref, "name": self.name, "method": self.method, "setup": self.setup, "direction": self.direction,
                "source": self.source, "text": self.text, "base_tf": self.base_tf, "params": dict(self.params),
                "mirror_of": self.mirror_of}


def _breakout_text(screen_id: str, direction: Optional[str] = None) -> Tuple[Optional[str], str, Optional[str]]:
    from app.screener.breakouts.templates import BY_ID, screenql, template_params
    template = BY_ID.get(screen_id.split(".", 1)[1])
    if template is None:
        return None, "1d", "no such breakout template"
    overrides = {"direction": direction} if direction else None
    text, why = screenql(template.screen, template.signal_tf, template_params(template, overrides))
    return text, template.signal_tf, why


def resolve(screen: tx.Screen) -> Tuple[Optional[Resolved], Optional[str]]:
    """(the screen as ScreenQL, None) or (None, why it cannot run as text)."""
    text: Optional[str]
    params: Dict[str, Any] = {}
    if screen.source == "starter":
        if screen.id not in STARTERS:
            return None, "no text for this starter"
        text, base_tf = STARTERS[screen.id]
    elif screen.source == "legends":
        from app.screener import legends as lg
        card = lg.load().card(screen.id)
        if card is None:
            return None, "no such Legends card"
        text, base_tf, params = card.source, card.base_tf, card.defaults()
    elif screen.source == "breakouts":
        text, base_tf, why = _breakout_text(screen.id)
        if text is None:
            return None, why
    else:
        return None, f"the {screen.source!r} source has no ScreenQL text"
    return Resolved(screen.id, screen.name, screen.method, screen.setup, str(screen.tags.get("direction", "long")), screen.source,
                    text, base_tf, params), None


def inline_params(ast: n.Node, params: Dict[str, Any]) -> n.Node:
    """The screen with its $parameters replaced by their values (a self-contained text to save or run)."""
    data = n.to_json(ast)

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if node.get("k") == "Param" and node.get("name") in params:
                value = params[node["name"]]
                if isinstance(value, bool):
                    return {"k": "Bool", "v": value}
                if isinstance(value, str):
                    return {"k": "Str", "v": value}
                return {"k": "Num", "v": float(value)}
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node
    return n.from_json(walk(data))


def mirror_of(resolved: Resolved) -> Dict[str, Any]:
    """The `<id>.short` entry of a long screen: its text (parameters inlined) or the reason it has none."""
    entry = {"id": resolved.ref + MIRROR_SUFFIX, "mirror_of": resolved.ref, "name": f"{resolved.name} (mirror, down side)",
             "method": resolved.method, "setup": resolved.setup, "direction": "short", "base_tf": resolved.base_tf,
             "source": resolved.source, "text": None, "reason": None, "how": "reflected"}
    if resolved.source == "breakouts":
        text, _, why = _breakout_text(resolved.ref, "down")
        entry.update(text=text, reason=None if text else f"No mirror: {why}.", how="template_down_side")
        return entry
    m = mr.mirror(inline_params(parse(resolved.text), resolved.params))
    entry.update(text=m.text, reason=m.reason)
    return entry


def mirrors(taxonomy: tx.Taxonomy, hidden_sources: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """One entry per visible long pre-built screen (screens that read both sides need none)."""
    out: List[Dict[str, Any]] = []
    for screen in tx.visible(taxonomy, hidden_sources):
        if screen.tags.get("direction") != "long":
            continue
        resolved, why = resolve(screen)
        if resolved is None:
            out.append({"id": screen.id + MIRROR_SUFFIX, "mirror_of": screen.id, "name": f"{screen.name} (mirror, down side)",
                        "method": screen.method, "setup": screen.setup, "direction": "short", "base_tf": None,
                        "source": screen.source, "text": None, "reason": f"No mirror: {why}.", "how": None})
            continue
        out.append(mirror_of(resolved))
    return out


def resolve_ref(ref: str, taxonomy: tx.Taxonomy, hidden_sources: Iterable[str] = ()) -> Tuple[Optional[Resolved], Optional[str]]:
    """A pre-built id or its `.short` mirror -> runnable screen (saved screens are resolved by the route, per tenant)."""
    hidden = set(hidden_sources)
    base = ref[:-len(MIRROR_SUFFIX)] if ref.endswith(MIRROR_SUFFIX) else ref
    screen = next((s for s in taxonomy.screens if s.id == base and s.source not in hidden), None)
    if screen is None:
        return None, f"{ref}: no such screen in the library (or its feature is off)"
    resolved, why = resolve(screen)
    if resolved is None:
        return None, f"{ref}: {why}"
    if base == ref:
        return resolved, None
    if resolved.direction != "long":
        return None, f"{ref}: only long screens have a mirror"
    entry = mirror_of(resolved)
    if entry["text"] is None:
        return None, f"{ref}: {entry['reason']}"
    return Resolved(ref, entry["name"], resolved.method, resolved.setup, "short", resolved.source, entry["text"], resolved.base_tf,
                    {}, mirror_of=resolved.ref), None


# --- confluence -------------------------------------------------------------------------------------------------------

def confluence(screens: Sequence[Resolved], results: Dict[str, Dict[str, Tuple[bool, Optional[str]]]],
               symbols: Sequence[str]) -> Dict[str, Any]:
    """`results[ref][symbol] = (matched, reason)`. Rows rank symbols by distinct methods, then screens, then symbol; a
    symbol no screen flags is listed apart. Each row carries the screens that flagged it (their result is the evidence)."""
    rows: List[Dict[str, Any]] = []
    quiet: List[str] = []
    not_evaluated: Dict[str, List[Dict[str, str]]] = {}
    for symbol in symbols:
        flagged = [s for s in screens if results.get(s.ref, {}).get(symbol, (False, None))[0]]
        for s in screens:
            matched, reason = results.get(s.ref, {}).get(symbol, (False, "not run"))
            if not matched and reason:
                not_evaluated.setdefault(symbol, []).append({"ref": s.ref, "reason": reason})
        if not flagged:
            quiet.append(symbol)
            continue
        methods = sorted({s.method for s in flagged})
        rows.append({"symbol": symbol, "methods": methods, "method_count": len(methods), "screen_count": len(flagged),
                     "screens": [{"ref": s.ref, "name": s.name, "method": s.method, "setup": s.setup, "text": s.text} for s in flagged]})
    rows.sort(key=lambda r: (-r["method_count"], -r["screen_count"], r["symbol"]))
    return {"rows": rows, "unflagged": quiet, "not_evaluated": not_evaluated, "methods_run": sorted({s.method for s in screens}),
            "note": CONFLUENCE_NOTE}


# --- watch-then-trigger lists --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class WatchList:
    id: str
    name: str
    definition: str
    method: str
    setup: str
    direction: str
    base_tf: str
    ready: str
    trigger: str
    params: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        ast = transition_ast(parse(self.ready), parse(self.trigger))
        return {"id": self.id, "name": self.name, "definition": self.definition, "method": self.method, "setup": self.setup,
                "direction": self.direction, "base_tf": self.base_tf, "ready": self.ready, "trigger": self.trigger,
                "params": dict(self.params), "transition": n.to_text(inline_params(ast, self.params))}


WATCH_LISTS: Tuple[WatchList, ...] = (
    WatchList("watch.pullback_ema20", "Pullback to the 20 EMA in an uptrend",
              "Ready: above a rising 50 EMA with the low near the 20 EMA. Trigger: a close above the previous bar's high.",
              "indicator", "pullback", "long", "1d",
              "close > EMA(close, 50) AND EMA(close, 20) > EMA(close, 50) AND low <= EMA(close, 20) + $near_atr * ATR(14)",
              "close > high[1]", {"near_atr": 0.25}),
    WatchList("watch.near_20_bar_high", "Close near the 20-bar high",
              "Ready: the close within a fraction of an ATR below the prior 20-bar high. Trigger: a close above that high.",
              "price_action", "breakout", "long", "1d",
              "close >= Max(high, 20)[1] - $near_atr * ATR(14) AND close <= Max(high, 20)[1]",
              "close > Max(high, 20)[1]", {"near_atr": 0.5}),
    WatchList("watch.rsi_oversold_turn", "RSI oversold, then back above 30",
              "Ready: RSI(14) at or below 30. Trigger: RSI(14) crosses back above 30.",
              "indicator", "reversal", "long", "1d",
              "RSI(14) <= 30", "CrossAbove(RSI(14), 30)"),
)
WATCH_BY_ID: Dict[str, WatchList] = {w.id: w for w in WATCH_LISTS}


def transition_ast(ready: n.Node, trigger: n.Node) -> n.Node:
    """The trigger now, the ready condition on the bar before: `Count(1, ready)[1] >= 1 AND trigger` (closed bars only)."""
    was_ready = n.Compare(">=", n.Call("Count", [n.Num(1.0), ready], {}, 1, None), n.Num(1.0))
    return n.Logic("ALL", [was_ready, trigger])


def watch_split(ready: Dict[str, Tuple[bool, Optional[str]]], triggered: Dict[str, Tuple[bool, Optional[str]]],
                symbols: Sequence[str]) -> Dict[str, Any]:
    """Triggered first (a transition this bar); ready = ready now and not triggered; the rest are neither."""
    hit = [s for s in symbols if triggered.get(s, (False, None))[0]]
    waiting = [s for s in symbols if ready.get(s, (False, None))[0] and s not in hit]
    reasons = {s: why for s in symbols for ok, why in (ready.get(s, (False, None)),) if not ok and why}
    return {"triggered": hit, "ready": waiting, "neither": [s for s in symbols if s not in hit and s not in waiting],
            "not_evaluated": reasons, "note": WATCH_NOTE}


def lint() -> List[str]:
    """The library's own lint (spec §3): every watch list filed on both axes, a definition of at most two lines, no
    advice wording, and its conditions parse."""
    taxonomy = tx.load()
    methods, setups = {m.id for m in taxonomy.methods}, {s.id for s in taxonomy.setups}
    problems: List[str] = []
    for w in WATCH_LISTS:
        if w.method not in methods or w.setup not in setups or w.method == "my_screens":
            problems.append(f"{w.id}: not filed on both axes")
        if len(w.definition) > tx.MAX_DEFINITION_CHARS or "\n" in w.definition or len(w.name) > tx.MAX_NAME_CHARS:
            problems.append(f"{w.id}: name or definition too long")
        for text in (w.name, w.definition):
            if tx.advice_words(text):
                problems.append(f"{w.id}: advice wording {tx.advice_words(text)}")
        for text in (w.ready, w.trigger):
            try:
                parse(text)
            except ValueError as exc:
                problems.append(f"{w.id}: {exc}")
    for text in (CONFLUENCE_NOTE, WATCH_NOTE, MIRROR_NOTE):
        if tx.advice_words(text):
            problems.append(f"note: advice wording {tx.advice_words(text)}")
    return problems


__all__ = ["FLAG", "MIRROR_SUFFIX", "SAVED_PREFIX", "STARTERS", "WATCH_LISTS", "WATCH_BY_ID", "Resolved", "WatchList", "confluence",
           "inline_params", "lint", "mirror_of", "mirrors", "resolve", "resolve_ref", "transition_ast", "watch_split",
           "CONFLUENCE_NOTE", "WATCH_NOTE", "MIRROR_NOTE", "MAX_CONFLUENCE_SCREENS", "MIN_CONFLUENCE_SCREENS"]
