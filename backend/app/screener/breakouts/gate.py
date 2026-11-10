"""U6 BK1: the `screener_breakouts` operator flag (off by default) at the API layer.

The ScreenQL functions of the breakout family stay in the one registry (so the validator, the runtime and the parity
tests see them), but while the flag is off for a tenant the registry endpoint does not list them and a screen, a saved
screen or an alert rule that uses one is refused with the reason. Nothing in the family places an order either way.

U6 BK3: the VCP and indicator breakouts (VCPBreakout, VCPReady, DonchianBreakout, SqueezeBreakout, MABreakout,
RegimeBreakout) are in the same family (bar close only, the same pause / refusal paths) and need a second flag on top,
`screener_breakouts_bk3` (registry Spec.also_flags; off by default). U6 BK4's TrendlineBreakout, PatternBreakout,
GapBreakout, GapType and RetestEntry likewise need `screener_breakouts_bk4`.

M94 PR-1: the intraday pack's session functions (SessionHigh / SessionLow / SessionVolume / SessionCount, PreOpenHigh /
PreOpenLow / PreOpenVolume) need `intraday_pack` on top of the family flag (Spec.also_flags), and so does a PatternBreakout /
PatternBreakoutRef call that uses a named flag / pennant argument (pole=, pole_bars=, flag_min=, flag_max=, retrace=); the
same call without them needs only the flags it always needed.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.screener import nodes
from app.screener.registry import BREAKOUT_BK3_FLAG, BREAKOUT_BK4_FLAG, BREAKOUT_FLAG, FUNCTIONS, INTRADAY_FLAG, PATTERN_SHAPE_KWARGS

FLAG = BREAKOUT_FLAG
BK3_FLAG = BREAKOUT_BK3_FLAG
BK4_FLAG = BREAKOUT_BK4_FLAG
EXTRA_FLAGS = (BK3_FLAG, BK4_FLAG, INTRADAY_FLAG)    # the set flags layered on the family flag (Spec.also_flags)
PATTERN_SHAPE_CALLS = ("PatternBreakout", "PatternBreakoutRef")   # M94: their named flag / pennant arguments need INTRADAY_FLAG


def call_extra_flags(node: Any) -> list[str]:
    """The flags beyond the family's that one call needs: its function's (Spec.also_flags), plus `intraday_pack` for a
    PatternBreakout(Ref) call with a named flag / pennant argument (M94)."""
    spec = FUNCTIONS.get(node.name) if isinstance(node, nodes.Call) else None
    if spec is None or spec.flag != FLAG:
        return []
    out = list(spec.also_flags)
    if node.name in PATTERN_SHAPE_CALLS and set(node.kwargs) & set(PATTERN_SHAPE_KWARGS) and INTRADAY_FLAG not in out:
        out.append(INTRADAY_FLAG)
    return out


def _extra_by_name(ast: Any) -> dict[str, list[str]]:
    """{breakout function used: every extra flag its calls in this screen need} (in order)."""
    out: dict[str, list[str]] = {}
    for node in nodes.walk(ast) if ast is not None else ():
        flags = call_extra_flags(node)
        if flags:
            have = out.setdefault(node.name, [])
            have += [f for f in flags if f not in have]
    return out


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
SESSION_FUNCTIONS = ("ORBreakout", "DayLevelBreak", "DayExtremeBreak", "RelVolumeTOD", "SessionVWAP", "GapBreakout", "GapType",
                     # M94 PR-1: the session's running values and the pre-open levels
                     "SessionHigh", "SessionLow", "SessionVolume", "SessionCount", "PreOpenHigh", "PreOpenLow", "PreOpenVolume")


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


def required_flags(names: List[str], ast: Any = None) -> List[str]:
    """The flags the named breakout functions need, in order (the family flag first); with `ast`, also the flags their
    calls there need (M94: a PatternBreakout with a named flag / pennant argument needs `intraday_pack`)."""
    out = [FLAG]
    by_call = _extra_by_name(ast)
    for name in names:
        spec = FUNCTIONS.get(name)
        for f in (*(spec.also_flags if spec is not None else ()), *by_call.get(name, ())):
            if f not in out:
                out.append(f)
    return out


def extra_flag_calls(ast: Any) -> List[str]:
    """U6 BK3 / BK4 / M94: breakout functions in a screen that need a flag beyond the family's (each once, in order)."""
    return list(_extra_by_name(ast))


def extra_flags_needed(ast: Any) -> Dict[str, List[str]]:
    """{extra flag: the functions in the screen that need it} (U6 BK3 / BK4 / M94)."""
    out: Dict[str, List[str]] = {}
    for name, flags in _extra_by_name(ast).items():
        for f in flags:
            out.setdefault(f, []).append(name)
    return out


def refusal(names: List[str], ast: Any = None) -> str:
    flags = required_flags(names, ast)
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
    extra = _extra_by_name(ast)
    if not extra:
        return []
    from app.platform.controls import flag_enabled
    off = {f for f in {f for flags in extra.values() for f in flags} if not await flag_enabled(session, f, tenant_id)}
    return [n for n, flags in extra.items() if set(flags) & off]


def hide(entries: dict[str, Any]) -> dict[str, Any]:
    """M94: the registry endpoint's PatternBreakout / PatternBreakoutRef without their named flag / pennant arguments while
    `intraday_pack` is off (as they were before M94)."""
    from app.screener.registry import PATTERN_SHAPE_DOC, describe_spec
    out = dict(entries)
    for name in PATTERN_SHAPE_CALLS:
        if name in out:
            spec = FUNCTIONS[name]
            out[name] = describe_spec(dataclasses.replace(spec, kwargs=(), ordered_args=(), doc=spec.doc.replace(PATTERN_SHAPE_DOC, "")))
    return out


__all__ = ["FLAG", "BK3_FLAG", "BK4_FLAG", "EXTRA_FLAGS", "INTRADAY_FLAG", "PATTERN_SHAPE_CALLS", "call_extra_flags", "hide", "SESSION_FUNCTIONS", "flagged_calls", "extra_flag_calls", "extra_flags_needed", "session_calls", "alert_clock_refusal", "required_flags",
           "refusal", "enabled", "bk3_enabled", "blocked_calls"]
