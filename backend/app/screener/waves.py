"""Wave templates W1 (docs/design/WAVE_TEMPLATES_REVIEW.md, finding WB13): the gate of the wave functions.

WaveState / WaveType / WaveAlt / WaveClarity (app/price_action/waves) carry Spec.flag `screener_waves`, off by default.
As for the U6 BK1 / Z7a-3 families, everything stays in the one registry (the validator, the runtime, the no-look-ahead
harness and the parity tests see it), but while the flag is off for an organisation the registry endpoint hides the
functions, and a screen, a study or an alert rule that uses one is refused with the reason (an active rule pauses with
it and resumes when the flag is back). The count reads swings a forming bar can confirm and then take back, so wave
alerts fire on bar close only. Information only: nothing here places an order.
"""
from __future__ import annotations

from typing import Any, List

from sqlalchemy.ext.asyncio import AsyncSession

from app.screener import nodes
from app.screener.registry import WAVES_FLAG as FLAG


def flagged_calls(ast: Any) -> List[str]:
    """The wave functions a screen uses (each name once, in order)."""
    from app.screener.registry import FUNCTIONS
    out: List[str] = []
    for node in nodes.walk(ast) if ast is not None else ():
        spec = FUNCTIONS.get(node.name) if isinstance(node, nodes.Call) else None
        if spec is not None and spec.flag == FLAG and spec.name not in out:
            out.append(spec.name)
    return out


def refusal(names: List[str]) -> str:
    return f"{', '.join(n + '()' for n in names)}: the wave count functions are not enabled for this organisation (flag {FLAG})"


def intrabar_refusal(names: List[str]) -> str:
    return f"{', '.join(n + '()' for n in names)}: wave count alerts fire on bar close only, not intrabar"


async def enabled(session: AsyncSession, tenant_id: Any) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def blocked_calls(session: AsyncSession, tenant_id: Any, ast: Any) -> List[str]:
    """The wave functions a screen uses while the flag is off for the tenant (empty: nothing to refuse)."""
    names = flagged_calls(ast)
    if not names or await enabled(session, tenant_id):
        return []
    return names


__all__ = ["FLAG", "flagged_calls", "refusal", "intrabar_refusal", "enabled", "blocked_calls"]
