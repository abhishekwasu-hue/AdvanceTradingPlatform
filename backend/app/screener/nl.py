"""N3 S9 (spec §3 natural-language box): plain language -> a ScreenQL screen the trader confirms and can edit. Flag
`screener_copilot` (off by default) on top of `screener_v2` and the organisation's `ai_copilot`.

* The model is given the registry as it is for this organisation (flagged functions whose flag is off are left out)
  and answers with one JSON object `{text, explanation, assumptions}`. Our validator and flag gates check the text;
  with problems the model gets one repair turn with them; still invalid -> the problems are returned, nothing guessed.
* Nothing runs and nothing is stored: the answer is a draft the trader reads, edits and runs from the Screener.
* English only (owner directive, 13 Oct): the box asks for English and ScreenQL; a draft with any character outside
  ASCII is refused (ScreenQL is ASCII), and an explanation or assumption that is not plain English text is withheld.
* Problems with an invalid draft (MED-1, ADR-0006; S9 round-cap redesign): each is shown from a fixed template keyed
  by the problem's `kind` (app/screener/validator.py Problem), filled only with what the registry itself knows - a
  function or field that exists, one of its arguments, its allowed values or range, a timeframe - and the `pos`. No
  quoted text, no unknown name, no number from the draft. An unknown kind is "the draft could not be used".
* The explanation passes the H-C1 c output filter (no advice or guarantee words; a specific call is framed).
* No provider configured (the rule-based one): a plain refusal saying so - the builder and the query editor still work.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from app.ai.prompt_versions import version_of

FLAG = "screener_copilot"
MAX_REGISTRY_ENTRIES = 260

SYSTEM = """You turn a trader's plain-language screen request into ONE ScreenQL screen.

ScreenQL: comparisons (> < >= <= == !=), AND / OR / NOT, parentheses, numbers, "strings", fields and functions from the
list below only. x[1] is the value one bar earlier; x@1w reads another timeframe. A screen is a condition (true/false).
Examples: close > SMA(close, 50) AND RSI(14) > 55   |   PctChange(close, 20) > 10 AND volume > SMA(volume, 20) * 1.5

Fields and functions (name(args): what it returns):
{registry}

Rules:
1. Use ONLY the names above, with their arguments in order. If the request needs something not in the list, leave it
   out and say so in "assumptions".
2. Choose sensible defaults for anything the trader did not say (windows, thresholds) and list them in "assumptions".
3. A screen finds symbols that pass filters. Never recommend buying or selling, never promise returns.
4. Quoted strings are only for a function's fixed choices (e.g. Pattern("hammer")); never put other text in quotes.
5. Answer with one JSON object and nothing else:
   {"text": "<the ScreenQL screen>", "explanation": "<one or two plain sentences: what it looks for>",
    "assumptions": ["<each default you chose or thing you left out>"]}
Write everything in English with plain ASCII characters only, whatever language the request is in."""

PROMPT_VERSION = version_of("screener_nl", SYSTEM)
DISCLAIMER = "A draft screen from your words: check it, edit it, then run it. Matches are filters passed, not recommendations."
UNUSABLE = "the draft could not be used"


def registry_text(hidden_flags: Sequence[str] = ()) -> str:
    """The registry the model may use: one line per entry (name(args): returns, doc), flagged-off entries left out."""
    from app.screener.registry import FIELDS, FUNCTIONS, WAVES_FLAG
    lines: List[str] = []
    for spec in (*FIELDS.values(), *FUNCTIONS.values()):
        if spec.flag and spec.flag in hidden_flags:
            continue
        if spec.name == "RetestEntry" and WAVES_FLAG in hidden_flags:      # wave templates R7: without its wave choices
            from app.screener.waves import legacy_retest_spec
            spec = legacy_retest_spec()
        args = ", ".join(a.name if a.required else f"{a.name}={a.default!r}" for a in spec.args)
        sig = spec.name if spec.name in FIELDS else f"{spec.name}({args}{', ...' if spec.varargs else ''})"
        lines.append(f"- {sig}: {spec.returns}{f' ({spec.unit})' if spec.unit else ''} - {spec.doc[:140]}")
    return "\n".join(lines[:MAX_REGISTRY_ENTRIES])


def system_prompt(hidden_flags: Sequence[str] = ()) -> str:
    """The prompt for this organisation's registry. English only: there is no language choice."""
    return SYSTEM.replace("{registry}", registry_text(hidden_flags))


def parse(raw: str) -> Dict[str, Any]:
    """The model's JSON answer -> {text, explanation, assumptions}. Raises ValueError when there is none."""
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.S)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the answer")
    data = json.loads(body[start:end + 1])
    if not isinstance(data, dict) or not isinstance(data.get("text"), str) or not data["text"].strip():
        raise ValueError("the answer has no screen text")
    assumptions = data.get("assumptions") or []
    return {"text": data["text"].strip()[:2000], "explanation": str(data.get("explanation") or "").strip()[:600],
            "assumptions": [str(a).strip()[:200] for a in assumptions if str(a).strip()][:10] if isinstance(assumptions, list) else []}


def non_ascii_at(text: str) -> Optional[int]:
    """The position of the first character outside ASCII, or None. ScreenQL is ASCII, so such a draft is refused."""
    return next((i for i, ch in enumerate(text) if ord(ch) > 127), None)


# --- problems: fixed templates, registry names only --------------------------------------------------------------------------------

Render = Callable[[Mapping[str, str]], Optional[str]]


def _function(names: Mapping[str, str], key: str = "function") -> Any:
    from app.screener.registry import FUNCTIONS
    return FUNCTIONS.get(names.get(key, ""))


def _arg(spec: Any, names: Mapping[str, str]) -> Any:
    wanted = names.get("arg")
    return next((a for a in (*spec.args, *spec.kwargs) if a.name == wanted), None) if spec is not None else None


def _timeframe(names: Mapping[str, str], key: str) -> Optional[str]:
    from app.screener import nodes
    tf = names.get(key)
    return tf if tf in nodes.TF_MINUTES else None


def _choice_list(values: Sequence[Any]) -> str:
    return ", ".join(json.dumps(v) if isinstance(v, str) else str(v) for v in values if v != "")


def _with_function(fn: Callable[[Any], Optional[str]]) -> Render:
    def render(names: Mapping[str, str]) -> Optional[str]:
        spec = _function(names)
        return fn(spec) if spec is not None else None
    return render


def _with_arg(fn: Callable[[Any, Any], Optional[str]]) -> Render:
    def render(names: Mapping[str, str]) -> Optional[str]:
        spec = _function(names)
        arg = _arg(spec, names)
        return fn(spec, arg) if arg is not None else None
    return render


def _fixed(text: str) -> Render:
    return lambda names: text


def _arity(spec: Any) -> str:
    if spec.varargs:
        return f"{spec.name} takes at least 2 arguments"
    needed = sum(1 for a in spec.args if a.required)
    want = f"{needed}" if needed == len(spec.args) else f"{needed} to {len(spec.args)}"
    return f"{spec.name} takes {want} argument(s): {', '.join(a.name for a in spec.args) or 'none'}"


def _finer(names: Mapping[str, str]) -> Optional[str]:
    tf, base = _timeframe(names, "tf"), _timeframe(names, "base_tf")
    return f"@{tf} is finer than the timeframe of the screen (@{base})" if tf and base else None


def _last_bar(names: Mapping[str, str]) -> Optional[str]:
    inner, outer = _function(names), _function(names, "outer")
    if inner is None or outer is None:
        return None
    return f"{inner.name} is known for the last bar only, so {outer.name} cannot read its earlier values; use it outside {outer.name}"


def _field_called(names: Mapping[str, str]) -> Optional[str]:
    from app.screener.registry import FIELDS
    spec = FIELDS.get(names.get("field", ""))
    return f"{spec.name} is a field and takes no parentheses" if spec is not None else None


def _tf_multiple(names: Mapping[str, str]) -> Optional[str]:
    spec, tf = _function(names), _timeframe(names, "tf")
    arg = _arg(spec, names)
    return f"{spec.name}: {arg.name} must be a whole number of @{tf} bars" if arg is not None and tf else None


def _argument_order(names: Mapping[str, str]) -> Optional[str]:
    """TPL-1: an argument pair the registry orders (MACD fast below slow; PSAR start / step at most max)."""
    spec = _function(names)
    if spec is None:
        return None
    pair = next((p for p in spec.ordered_args if p[0] == names.get("arg") and p[1] == names.get("other")), None)
    if pair is None:
        return None
    return f"{spec.name}: {pair[0]} must be {'at most' if pair[2] else 'below'} {pair[1]}"


def _flag_off(names: Mapping[str, str]) -> Optional[str]:
    from app.screener.registry import FUNCTIONS
    known = [f for f in (names.get("functions") or "").split(",") if f in FUNCTIONS]
    return f"{', '.join(f + '()' for f in known)}: not enabled for this organisation" if known else None


def _templates() -> Dict[str, Render]:
    from app.screener import nodes
    from app.screener.registry import MAX_WINDOW
    from app.screener.validator import MAX_OFFSET, NOT_A_CONDITION
    return {
        "syntax": _fixed("syntax error: the draft is not valid ScreenQL"),
        "non_ascii": _fixed("the draft has characters outside ScreenQL (ScreenQL is plain ASCII text)"),
        "free_text": _fixed("quoted text is allowed only for the fixed choices of a function (for example Pattern(\"hammer\"))"),
        "unknown_field": _fixed("unknown field"),
        "unknown_function": _fixed("unknown function"),
        "function_without_parens": _with_function(lambda s: f"{s.name} is a function and needs parentheses"),
        "field_called": _field_called,
        "unknown_timeframe": _fixed(f"unknown timeframe; use one of {', '.join('@' + t for t in nodes.TIMEFRAMES)}"),
        "timeframe_finer": _finer,
        "look_ahead": _fixed("a negative offset reads a future bar (look-ahead)"),
        "offset_range": _fixed(f"an offset is more than {MAX_OFFSET} bars back"),
        "parameter": _fixed("a drafted screen takes no $parameters"),
        "window_not_literal": _with_function(lambda s: f"{s.name}: the window must be a whole number of bars"),
        "window_range": _with_function(lambda s: f"number out of range for {s.name}: the window must be a whole number from 1 to {MAX_WINDOW}"),
        "choice": _with_arg(lambda s, a: f"value not allowed for {s.name}: {a.name} must be one of {_choice_list(a.choices)}"
                            if a.choices else None),
        "out_of_range": _with_arg(lambda s, a: f"number out of range for {s.name}: {a.name} must be between {a.bounds[0]:g} and {a.bounds[1]:g}"
                                  if a.bounds else None),
        "literal_only": _with_arg(lambda s, a: f"{s.name}: {a.name} must be a number (one value for the whole series), not an expression"),
        "last_bar_only": _last_bar,
        "not_finite": _fixed("numbers must be finite"),
        "number_expected": _fixed("a number is needed here"),
        "condition_expected": _fixed("a condition (true/false) is needed here"),
        "unit_mismatch": _fixed("the values compared or combined here have different units"),
        "in_list": _fixed("IN takes a list of one or more literal values"),
        "compare_types": _fixed("these values cannot be compared with each other"),
        "domain": _with_function(lambda s: f"value not allowed for {s.name}: it is one of {_choice_list(s.values)}" if s.values else None),
        "no_offset_tf": _with_function(lambda s: f"{s.name} takes no [offset] or @timeframe"),
        "arity": _with_function(_arity),
        "unknown_argument": _with_function(lambda s: f"{s.name} has no such named argument"
                                           + (f"; its named arguments: {', '.join(a.name for a in s.kwargs)}" if s.kwargs else "")),
        "timeframe_unsupported": _with_function(lambda s: f"{s.name} runs on {' / '.join('@' + t for t in s.timeframes)} bars only"
                                                if s.timeframes else None),
        # strategy templates R7 (named arguments): the same argument by position and by name
        "argument_twice": _with_function(lambda s: f"{s.name}: an argument is given twice (by position and by name)"),
        "argument_type": _with_arg(lambda s, a: f"{s.name}: argument {a.name} must be {'a whole number of bars' if a.type == 'window' else a.type}"),
        "price_argument": _with_arg(lambda s, a: f"{s.name}: {a.name} must be a price"),
        "tf_multiple": _tf_multiple,
        "argument_order": _argument_order,
        "not_condition": _fixed(NOT_A_CONDITION),
        "cost": _fixed("the screen is over the cost cap of this organisation; use fewer functions or shorter windows"),
        "flag_off": _flag_off,
        "ranking_system": _fixed("RankScore names a ranking system this organisation cannot use"),
        "venue": _fixed("the screen reads data this venue does not carry"),
    }


def shown_problem(problem: Any) -> Dict[str, Any]:
    """A problem with a model's draft, as the box shows it: {kind, message, pos}. The message is the kind's fixed template
    filled from the registry (Problem.names only selects registry entries; a name the registry does not know, or a kind
    without a template, gives "the draft could not be used"). The validator's own `message` is never read: it can quote
    the draft."""
    kind = str(getattr(problem, "kind", "") or "")
    names: Mapping[str, str] = getattr(problem, "names", None) or {}
    template = _templates().get(kind)
    message = template(names) if template is not None else None
    if not message:
        kind, message = "unusable", UNUSABLE
    pos = getattr(problem, "pos", None)
    return {"kind": kind, "message": message, "pos": pos if isinstance(pos, int) and not isinstance(pos, bool) else None}


# Typographic punctuation a model writes in English, folded to ASCII before the English-only check.
_TYPOGRAPHIC = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                              "…": "...", " ": " "})


def screened(text: str) -> Optional[str]:
    """The model's own words (explanation, assumptions): English only - anything outside ASCII, after folding typographic
    punctuation, is withheld - then the H-C1 c output filter. None when they may not be shown."""
    from app.ai import output_filter
    text = (text or "").translate(_TYPOGRAPHIC)
    if non_ascii_at(text) is not None:
        return None
    ok, shown, _ = output_filter.check(text, "en", where="screener_nl")
    return shown if ok else None


__all__ = ["FLAG", "SYSTEM", "PROMPT_VERSION", "DISCLAIMER", "UNUSABLE", "registry_text", "system_prompt", "parse", "non_ascii_at",
           "shown_problem", "screened"]
