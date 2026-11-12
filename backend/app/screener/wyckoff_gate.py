"""M99 P4: the gate of the Wyckoff functions `Wyckoff(...)` and `WyckoffLevel(...)` (flag `wyckoff_pack`, off by default).

As for the bar classes (m99_gate), everything stays in the one registry (the validator, the runtime, the no-look-ahead
harness and the parity tests see it), but while the flag is off for an organisation the registry endpoint hides the
function, and a screen, a study or an alert rule that uses it is refused with the reason (an active rule pauses with it
and resumes when the flag is back). A bar is read once it has closed, so these alerts fire on bar close only. Signals
only: nothing here places an order.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.screener import nodes
from app.screener.registry import WYCKOFF_FLAG as FLAG


def flagged_calls(ast: Any, params: dict[str, Any] | None = None) -> list[str]:
    """The Wyckoff functions a screen uses (each name once, in order)."""
    from app.screener.registry import FUNCTIONS
    out: list[str] = []
    for node in nodes.walk(ast) if ast is not None else ():
        spec = FUNCTIONS.get(node.name) if isinstance(node, nodes.Call) else None
        if spec is not None and spec.flag == FLAG and spec.name not in out:
            out.append(spec.name)
    return out


def refusal(names: list[str]) -> str:
    return f"{', '.join(n + '()' for n in names)}: the Wyckoff pack is not enabled for this organisation (flag {FLAG})"


def intrabar_refusal(names: list[str]) -> str:
    return f"{', '.join(n + '()' for n in names)}: Wyckoff alerts fire on bar close only, not intrabar"


async def enabled(session: AsyncSession, tenant_id: Any) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def blocked_calls(session: AsyncSession, tenant_id: Any, ast: Any, params: dict[str, Any] | None = None) -> list[str]:
    """The Wyckoff functions a screen uses while the flag is off for the tenant (empty: nothing to refuse)."""
    names = flagged_calls(ast, params)
    if not names or await enabled(session, tenant_id):
        return []
    return names


__all__ = ["FLAG", "flagged_calls", "refusal", "intrabar_refusal", "enabled", "blocked_calls"]
