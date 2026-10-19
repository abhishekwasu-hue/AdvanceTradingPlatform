"""AMW-3 (docs/design/AMW_PORT.md section 4): the ScreenQL functions ported from the AMW screener's predicates, and their
flag gate.

* New registry functions (Spec.flag `screenql_amw_functions`): StDev, RsiDivergence, SwingLabel, TimeOfDay.
* New arguments and values of existing functions: the v2 candles in `Pattern(name)` (bullish / bearish strong body and
  gap reversal) and the `confirm` argument of Pattern / PatternBullish / PatternBearish (confirmed / failed /
  unconfirmed on the bar after the pattern). `confirm "any"` is the old behaviour and is never gated.

The flag works like the U6 BK1 / Z7a-3 families: everything stays in the one registry (so the validator, the runtime,
the Z10-a harness and the parity tests see it), but while the flag is off for an organisation the registry endpoint
hides it (the functions, the v2 names and the `confirm` argument), and a screen, a saved screen or an alert rule that
uses it is refused with the reason; an active alert rule using it pauses with the reason and resumes when the flag is
back. Everything here reads closed bars only and places no orders.
"""
from __future__ import annotations

import copy
import dataclasses
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.screener import nodes
from app.screener.registry import AMW_FLAG, CONFIRM_STATES, FUNCTIONS, PATTERN_NAMES, PATTERN_NAMES_V2, Spec

FLAG = AMW_FLAG
PATTERN_CALLS = ("Pattern", "PatternBullish", "PatternBearish")
INTRABAR_FUNCTIONS = ("RsiDivergence", "SwingLabel")    # read swings a forming bar can confirm and then take back
_UNKNOWN = object()


def _legacy_spec(spec: Spec) -> Spec:
    """A Pattern* entry as it was before AMW-3: no `confirm` argument, no v2 candle, the old doc."""
    name_args = tuple(dataclasses.replace(a, choices=PATTERN_NAMES) for a in spec.args if a.name == "name")
    doc = spec.doc.split(". confirm:")[0].split(" (confirm:")[0]
    return dataclasses.replace(spec, args=name_args, choice_extra_bars=(), doc=doc)


# The registry the validator reads while the flag is off (review of AMW-3): Pattern / PatternBullish / PatternBearish
# exactly as before AMW-3, so a mistake in them gets main's message, never one that names a hidden value or argument.
# The four AMW-3 functions stay known, so using one is refused with the flag's reason (the U6 BK1 / Z7a-3 pattern).
FLAG_OFF_FUNCTIONS: Dict[str, Spec] = {name: (_legacy_spec(spec) if name in PATTERN_CALLS else spec) for name, spec in FUNCTIONS.items()}


def _value(arg: Any, params: Optional[Dict[str, Any]]) -> Any:
    """A literal's value; a parameter's value when the parameters are known (else unknown: treated as gated)."""
    if isinstance(arg, (nodes.Str, nodes.Num, nodes.Bool)):
        return arg.value
    if isinstance(arg, nodes.Param):
        return params.get(arg.name, _UNKNOWN) if params is not None else _UNKNOWN
    return _UNKNOWN


def _gated_pattern(call: Any, params: Optional[Dict[str, Any]]) -> bool:
    """A Pattern* call that uses a v2 candle or a confirmation status other than "any"."""
    confirm_pos = 1 if call.name == "Pattern" else 0
    if len(call.args) > confirm_pos and _value(call.args[confirm_pos], params) != "any":
        return True
    if call.name == "Pattern" and call.args:
        name = _value(call.args[0], params)
        return name is _UNKNOWN or name in PATTERN_NAMES_V2
    return False


def flagged_calls(ast: Any, params: Optional[Dict[str, Any]] = None) -> List[str]:
    """What a screen uses from AMW-3 (each once, in order): a function's name with `()`, or the Pattern call's text for a
    v2 candle / a confirmation status. `params` resolves `$parameters`; one that cannot be resolved counts as gated."""
    out: List[str] = []
    if ast is None:
        return out
    for node in nodes.walk(ast):
        if not isinstance(node, nodes.Call):
            continue
        spec = FUNCTIONS.get(node.name)
        label = None
        if spec is not None and spec.flag == FLAG:
            label = f"{node.name}()"
        elif node.name in PATTERN_CALLS and _gated_pattern(node, params):
            label = nodes.to_text(node)
        if label is not None and label not in out:
            out.append(label)
    return out


def refusal(names: List[str]) -> str:
    return f"{', '.join(names)}: the AMW screener functions are not enabled for this organisation (flag {FLAG})"


def intrabar_calls(ast: Any, params: Optional[Dict[str, Any]] = None) -> List[str]:
    """The AMW-3 parts that read closed bars only: a confirmation status (decided by the bar's close) and the swing
    functions (a forming bar can confirm a swing and then take it back). A v2 candle, StDev and TimeOfDay read a forming
    bar like the indicators do."""
    out: List[str] = []
    for node in nodes.walk(ast) if ast is not None else ():
        if not isinstance(node, nodes.Call):
            continue
        label = None
        if node.name in INTRABAR_FUNCTIONS:
            label = f"{node.name}()"
        elif node.name in PATTERN_CALLS:
            pos = 1 if node.name == "Pattern" else 0
            if len(node.args) > pos and _value(node.args[pos], params) != "any":
                label = nodes.to_text(node)
        if label is not None and label not in out:
            out.append(label)
    return out


def intrabar_refusal(names: List[str]) -> str:
    return f"{', '.join(names)}: these AMW screener functions fire on bar close only, not intrabar"


def uses_patterns(ast: Any) -> bool:
    return ast is not None and any(isinstance(node, nodes.Call) and node.name in PATTERN_CALLS for node in nodes.walk(ast))


def flag_off_validation(ast: Any, validated: Any, params: Optional[Dict[str, Any]] = None) -> None:
    """With the flag off: a screen whose Pattern* calls do not validate against the registry as it was before AMW-3 gets
    THAT validation's problems (main's messages - no hidden candle name, `confirm` argument or arity in them). A screen
    that validates there validates the same way here, so nothing else changes. A valid screen that uses nothing gated
    (e.g. `Pattern("doji", "any")`, saved while the flag was on) stays valid: `confirm "any"` is never gated."""
    from app.screener.validator import validate
    if not uses_patterns(ast) or getattr(validated, "base_tf", None) is None:
        return
    if validated.ok and not flagged_calls(ast, params):
        return
    legacy = validate(ast, base_tf=validated.base_tf, params=params, cost_cap=validated.cost_cap, strict_units=validated.strict_units,
                      functions=FLAG_OFF_FUNCTIONS)
    if not legacy.ok:
        validated.ok = False
        validated.problems = list(legacy.problems)


async def enabled(session: AsyncSession, tenant_id: Any) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def blocked_calls(session: AsyncSession, tenant_id: Any, ast: Any, params: Optional[Dict[str, Any]] = None) -> List[str]:
    """The AMW-3 parts a screen uses while the flag is off for the tenant (empty: nothing to refuse)."""
    names = flagged_calls(ast, params)
    if not names or await enabled(session, tenant_id):
        return []
    return names


def hide(entries: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """The registry description with the flag off: the AMW-3 functions are already left out by their Spec.flag; here the
    v2 candle names and the `confirm` argument are taken off Pattern / PatternBullish / PatternBearish, so the builder
    shows and writes them exactly as before AMW-3."""
    out = dict(entries)
    for name in PATTERN_CALLS:
        if name not in out:
            continue
        e = copy.deepcopy(out[name])
        e["args"] = [a for a in e["args"] if a != "confirm"]
        for key in ("choices", "defaults", "types"):
            e[key] = {k: v for k, v in e.get(key, {}).items() if k != "confirm"}
        if "name" in e["choices"]:
            e["choices"]["name"] = [v for v in e["choices"]["name"] if v not in PATTERN_NAMES_V2]
        for cut in (". confirm:", " (confirm:"):                     # the doc as it was before AMW-3
            e["doc"] = e["doc"].split(cut)[0]
        out[name] = e
    return out


__all__ = ["FLAG", "PATTERN_CALLS", "CONFIRM_STATES", "FLAG_OFF_FUNCTIONS", "INTRABAR_FUNCTIONS", "flagged_calls", "refusal", "enabled",
           "blocked_calls", "hide", "intrabar_calls", "intrabar_refusal", "uses_patterns", "flag_off_validation"]
