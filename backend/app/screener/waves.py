"""Wave templates W1 (docs/design/WAVE_TEMPLATES_REVIEW.md, finding WB13): the gate of the wave functions.

WaveState / WaveType / WaveAlt / WaveClarity (app/price_action/waves) carry Spec.flag `screener_waves`, off by default.
As for the U6 BK1 / Z7a-3 families, everything stays in the one registry (the validator, the runtime, the no-look-ahead
harness and the parity tests see it), but while the flag is off for an organisation the registry endpoint hides the
functions, and a screen, a study or an alert rule that uses one is refused with the reason (an active rule pauses with
it and resumes when the flag is back). The count reads swings a forming bar can confirm and then take back, so wave
alerts fire on bar close only. Information only: nothing here places an order.

Build steps 3 / 4 (W2-W4, R7): the level, ratio and line functions (WaveOrigin ... WaveLineBreak) carry the same flag, and
so do the new choices of an existing function - RetestEntry with `of` "wave_line" / "wave_end". While the flag is off the
registry endpoint (and the Copilot's registry) shows RetestEntry exactly as before (`hide`, `legacy_retest_spec`), and a
screen, study or alert rule that uses a wave choice is refused with the flag's reason; a `$parameter` that cannot be
resolved counts as a wave choice (as for the AMW-3 gate). RetestEntry's older choices are never gated here.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.screener import nodes
from app.screener.registry import RETEST_WAVE_OF, Spec
from app.screener.registry import WAVES_FLAG as FLAG

RETEST = "RetestEntry"
_UNKNOWN = object()


def _value(arg: Any, params: Optional[Dict[str, Any]]) -> Any:
    """A literal's value; a parameter's value when the parameters are known (else unknown: treated as gated)."""
    if isinstance(arg, (nodes.Str, nodes.Num, nodes.Bool)):
        return arg.value
    if isinstance(arg, nodes.Param):
        return params.get(arg.name, _UNKNOWN) if params is not None else _UNKNOWN
    return _UNKNOWN


def wave_retest(node: Any, params: Optional[Dict[str, Any]] = None) -> bool:
    """A RetestEntry call whose `of` is a wave choice (or a parameter that cannot be resolved)."""
    if not (isinstance(node, nodes.Call) and node.name == RETEST and len(node.args) > 1):
        return False
    value = _value(node.args[1], params)
    return value is _UNKNOWN or value in RETEST_WAVE_OF


def flagged_calls(ast: Any, params: Optional[Dict[str, Any]] = None) -> List[str]:
    """The wave functions a screen uses (each name once, in order), RetestEntry when it follows a wave line / wave end.
    `params` resolves a `$parameter` RetestEntry choice; one that cannot be resolved counts as a wave choice."""
    from app.screener.registry import FUNCTIONS
    out: List[str] = []
    for node in nodes.walk(ast) if ast is not None else ():
        spec = FUNCTIONS.get(node.name) if isinstance(node, nodes.Call) else None
        if spec is None:
            continue
        if (spec.flag == FLAG or wave_retest(node, params)) and spec.name not in out:
            out.append(spec.name)
    return out


def legacy_retest_spec() -> Spec:
    """RetestEntry as it was before the wave choices (R7): the four older arguments and choices, the older doc."""
    from app.screener.registry import FUNCTIONS, RETEST_DOC
    spec = FUNCTIONS[RETEST]
    of = dataclasses.replace(spec.args[1], choices=tuple(c for c in spec.args[1].choices if c not in RETEST_WAVE_OF))
    return dataclasses.replace(spec, args=(spec.args[0], of, spec.args[2], spec.args[3]), degree_min_bars=(), doc=RETEST_DOC)


def hide(entries: Dict[str, Any]) -> Dict[str, Any]:
    """The registry endpoint's entries with RetestEntry shown as before the wave choices (the flag is off)."""
    from app.screener.registry import describe_spec
    if RETEST not in entries:
        return entries
    return {**entries, RETEST: describe_spec(legacy_retest_spec())}


def refusal(names: List[str]) -> str:
    return f"{', '.join(n + '()' for n in names)}: the wave count functions are not enabled for this organisation (flag {FLAG})"


def intrabar_refusal(names: List[str]) -> str:
    return f"{', '.join(n + '()' for n in names)}: wave count alerts fire on bar close only, not intrabar"


async def enabled(session: AsyncSession, tenant_id: Any) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def blocked_calls(session: AsyncSession, tenant_id: Any, ast: Any, params: Optional[Dict[str, Any]] = None) -> List[str]:
    """The wave functions a screen uses while the flag is off for the tenant (empty: nothing to refuse)."""
    names = flagged_calls(ast, params)
    if not names or await enabled(session, tenant_id):
        return []
    return names


__all__ = ["FLAG", "flagged_calls", "refusal", "intrabar_refusal", "enabled", "blocked_calls", "wave_retest", "legacy_retest_spec", "hide"]
