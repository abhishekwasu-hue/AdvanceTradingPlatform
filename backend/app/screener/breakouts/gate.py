"""U6 BK1: the `screener_breakouts` operator flag (off by default) at the API layer.

The ScreenQL functions of the breakout family stay in the one registry (so the validator, the runtime and the parity
tests see them), but while the flag is off for a tenant the registry endpoint does not list them and a screen, a saved
screen or an alert rule that uses one is refused with the reason. Nothing in the family places an order either way.
"""
from __future__ import annotations

from typing import Any, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.screener import nodes
from app.screener.registry import BREAKOUT_FLAG, FUNCTIONS

FLAG = BREAKOUT_FLAG


def flagged_calls(ast: Any) -> List[str]:
    """Breakout-family functions used in a screen (each once, in order)."""
    out: List[str] = []
    if ast is None:
        return out
    for node in nodes.walk(ast):
        spec = FUNCTIONS.get(node.name) if isinstance(node, nodes.Call) else None
        if spec is not None and spec.flag and spec.name not in out:
            out.append(spec.name)
    return out


# U6 BK2: functions that read the exchange session (open, VWAP from the open, time-of-day slots)
SESSION_FUNCTIONS = ("ORBreakout", "DayLevelBreak", "DayExtremeBreak", "RelVolumeTOD", "SessionVWAP")


def session_calls(ast: Any) -> List[str]:
    """Session functions used in a screen (each once, in order)."""
    return [n for n in flagged_calls(ast) if n in SESSION_FUNCTIONS]


def alert_clock_refusal(ast: Any, exchange: Optional[str]) -> Optional[str]:
    """U6 BK2 (review M1): the bar-close alert engine keeps the NSE clock (expected bars 09:15-15:30), so an alert rule
    on another exchange's session (MCX, currency) cannot use the session functions yet - refused with the reason."""
    from app.screener.breakouts.session import calendar_for
    names = session_calls(ast)
    try:
        family = calendar_for(exchange).name                          # CDS has its own clock in the screener only
    except ValueError:
        family = "CRYPTO"
    if not names or family == "NSE":
        return None
    return (f"{', '.join(n + '()' for n in names)}: bar-close alerts follow the NSE session clock; on {family} these session "
            f"functions run in screens and the breakout run, not in alert rules yet (open question BK2-5)")


def refusal(names: List[str]) -> str:
    return f"{', '.join(n + '()' for n in names)}: the breakout screens are not enabled for this organisation (flag {FLAG})"


async def enabled(session: AsyncSession, tenant_id: Any) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def blocked_calls(session: AsyncSession, tenant_id: Any, ast: Any) -> List[str]:
    """The breakout functions a screen uses while the flag is off for the tenant (empty: nothing to refuse)."""
    names = flagged_calls(ast)
    if not names or await enabled(session, tenant_id):
        return []
    return names


__all__ = ["FLAG", "SESSION_FUNCTIONS", "flagged_calls", "session_calls", "alert_clock_refusal", "refusal", "enabled", "blocked_calls"]
