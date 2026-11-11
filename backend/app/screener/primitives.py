"""M99 B1-b: the flag gate of the M99 shared primitives in ScreenQL (FibLevel, FibDistancePct, ClusterDistancePct).

Flag `m99_primitives` (off by default), the U6 BK1 / Z7a-3 pattern: the functions stay in the one registry; while the
flag is off the registry endpoint hides them (Spec.flag) and a screen or a chart study using one is refused with the
reason. Alert rules do not take them yet (refused with the reason; a saved screen edited to use one pauses its rule):
the primitives are unfinished. Information only - places no orders.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.screener import nodes
from app.screener.registry import M99_FLAG as FLAG


def flagged_calls(ast: Any) -> list[str]:
    """The M99 primitive functions a screen uses (each name once, in order)."""
    from app.screener.registry import FUNCTIONS
    out: list[str] = []
    for node in nodes.walk(ast) if ast is not None else ():
        spec = FUNCTIONS.get(node.name) if isinstance(node, nodes.Call) else None
        if spec is not None and spec.flag == FLAG and spec.name not in out:
            out.append(spec.name)
    return out


def refusal(names: list[str]) -> str:
    return f"{', '.join(n + '()' for n in names)}: the shared primitives are not enabled for this organisation (flag {FLAG})"


def alert_refusal(names: list[str]) -> str:
    return f"{', '.join(n + '()' for n in names)}: alert rules do not read the shared primitives yet - run the screen instead"


async def enabled(session: AsyncSession, tenant_id: Any) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def blocked_calls(session: AsyncSession, tenant_id: Any, ast: Any) -> list[str]:
    names = flagged_calls(ast)
    if not names or await enabled(session, tenant_id):
        return []
    return names


__all__ = ["FLAG", "flagged_calls", "refusal", "alert_refusal", "enabled", "blocked_calls"]
