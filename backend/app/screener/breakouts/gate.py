"""U6 BK1: the `screener_breakouts` operator flag (off by default) at the API layer.

The ScreenQL functions of the breakout family stay in the one registry (so the validator, the runtime and the parity
tests see them), but while the flag is off for a tenant the registry endpoint does not list them and a screen, a saved
screen or an alert rule that uses one is refused with the reason. Nothing in the family places an order either way.

U6 BK3: the VCP and indicator breakouts (VCPBreakout, VCPReady, DonchianBreakout, SqueezeBreakout, MABreakout,
RegimeBreakout) are in the same family (bar close only, the same pause / refusal paths) and need a second flag on top,
`screener_breakouts_bk3` (registry Spec.also_flags; off by default).
"""
from __future__ import annotations

from typing import Any, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.screener import nodes
from app.screener.registry import BREAKOUT_BK3_FLAG, BREAKOUT_FLAG, FUNCTIONS

FLAG = BREAKOUT_FLAG
BK3_FLAG = BREAKOUT_BK3_FLAG


def flagged_calls(ast: Any) -> List[str]:
    """Breakout-family functions used in a screen (each once, in order)."""
    out: List[str] = []
    if ast is None:
        return out
    for node in nodes.walk(ast):
        spec = FUNCTIONS.get(node.name) if isinstance(node, nodes.Call) else None
        if spec is not None and spec.flag == FLAG and spec.name not in out:      # Z7a3-7: not the market-zone family
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
        family = calendar_for(exchange).name                          # CDS has its own 09:00-17:00 clock
    except ValueError:
        family = "CRYPTO"
    if not names or family == "NSE":
        return None
    return (f"{', '.join(n + '()' for n in names)}: bar-close alerts follow the NSE session clock; on {family} these session "
            f"functions run in screens and the breakout run, not in alert rules yet (open question BK2-5)")


def required_flags(names: List[str]) -> List[str]:
    """The flags the named breakout functions need, in order (the family flag first)."""
    out = [FLAG]
    for name in names:
        spec = FUNCTIONS.get(name)
        out += [f for f in (spec.also_flags if spec is not None else ()) if f not in out]
    return out


def extra_flag_calls(ast: Any) -> List[str]:
    """U6 BK3: breakout functions in a screen that need a flag beyond the family's (each once, in order)."""
    return [n for n in flagged_calls(ast) if FUNCTIONS[n].also_flags]


def refusal(names: List[str]) -> str:
    flags = required_flags(names)
    return (f"{', '.join(n + '()' for n in names)}: the breakout screens are not enabled for this organisation "
            f"(flag{'s' if len(flags) > 1 else ''} {', '.join(flags)})")


async def enabled(session: AsyncSession, tenant_id: Any) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def bk3_enabled(session: AsyncSession, tenant_id: Any) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, BK3_FLAG, tenant_id)


async def blocked_calls(session: AsyncSession, tenant_id: Any, ast: Any) -> List[str]:
    """The breakout functions a screen uses while a flag they need is off for the tenant (empty: nothing to refuse).
    The family flag off blocks every one; with it on, the BK3 functions still need `screener_breakouts_bk3`."""
    names = flagged_calls(ast)
    if not names:
        return []
    if not await enabled(session, tenant_id):
        return names
    extra = [n for n in names if FUNCTIONS[n].also_flags]
    if not extra:
        return []
    from app.platform.controls import flag_enabled
    off = {f for f in {f for n in extra for f in FUNCTIONS[n].also_flags} if not await flag_enabled(session, f, tenant_id)}
    return [n for n in extra if set(FUNCTIONS[n].also_flags) & off]


__all__ = ["FLAG", "BK3_FLAG", "SESSION_FUNCTIONS", "flagged_calls", "extra_flag_calls", "session_calls", "alert_clock_refusal", "required_flags",
           "refusal", "enabled", "bk3_enabled", "blocked_calls"]
