"""Day-one fix 2 (README row 19, DAY_ONE_TRADER_TEST finding 2): Find setups before the day's broker login.

A screen on Daily (or coarser) closed bars needs no live data, so when no broker session is usable today (the 409 the
broker path raises) and the flag `backtest_stored_history` is on, the run reads ATP's stored end-of-day history - the
same licensed shared Daily bars and window the stored-history backtest reads (`app/backtest/bar_source.lake_frame`) -
instead of stopping at "Log in". Intraday screens still need the broker (the 409 stands); so does a non-NSE exchange.
Weekly / monthly screens are built from the stored Daily bars exactly as from a broker's Daily candles (`resample`).

No look-ahead: the stored window ends yesterday (today's bar is never read) and every bar is a closed one.
With a usable broker session nothing changes: this module is reached only from the broker path's 409.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.screener import nodes, reasons
from app.screener.runtime import SymbolData, resample

DAILY_OR_COARSER = ("1d", "1w", "1M")
MAX_DAYS = 3650
PAD_DAYS = 10


def stored_days(lookback: Dict[str, int], base_tf: str) -> int:
    """Calendar days of Daily bars a screen's lookback needs (sessions -> calendar days with room for holidays)."""
    daily = nodes.TF_MINUTES["1d"]
    sessions = max([math.ceil(bars * nodes.TF_MINUTES.get(tf, nodes.TF_MINUTES[base_tf]) / daily) for tf, bars in lookback.items()] or [1])
    return min(MAX_DAYS, math.ceil(sessions * 7 / 5) + PAD_DAYS)


def applies(exc: HTTPException, base_tf: str, lookback: Dict[str, int], exchange: str) -> bool:
    """True when the broker path refused for want of a session and the screen reads only Daily-or-coarser NSE bars."""
    if exc.status_code != 409 or base_tf not in DAILY_OR_COARSER:
        return False
    if any(tf not in DAILY_OR_COARSER for tf in lookback):
        return False
    return (exchange or "NSE").strip().upper() == "NSE"


async def stored_universe(session: AsyncSession, tenant_id: int, exc: HTTPException, symbols: List[str], exchange: str, base_tf: str,
                          lookback: Dict[str, int]) -> Optional[Tuple[List[SymbolData], Dict[str, str], str]]:
    """(universe, per-symbol problems, "lake:daily") from stored Daily history, or None when it does not apply (the
    caller then raises the broker's own 409, exactly as before)."""
    from app.backtest import bar_source
    from app.platform.controls import flag_enabled
    if not applies(exc, base_tf, lookback, exchange):
        return None
    if not await flag_enabled(session, bar_source.FLAG, tenant_id):
        return None
    days = stored_days(lookback, base_tf)
    universe: List[SymbolData] = []
    problems: Dict[str, str] = {}
    for symbol in symbols:
        df = await bar_source.lake_frame(session, symbol, "day", days)
        if df.empty:
            problems[symbol] = reasons.Reason(bar_source.not_stored(symbol), reasons.NOT_STORED)   # SCR-FIX B1: log in to read it
            continue
        frame = df if base_tf == "1d" else resample(df, "1d", base_tf, keep_forming=False)
        universe.append(SymbolData(symbol, {base_tf: frame}, exchange=exchange))
    return universe, problems, bar_source.LAKE_SOURCE


__all__ = ["stored_universe", "stored_days", "applies", "DAILY_OR_COARSER"]
