"""M71 W2-D (flag `market_store_reads`): a deployment's strategy bars store-first (owner order M71 PL-1 / PL-3).

OFF: `market_data.get_frames(...)` exactly as before (the worker's own session, its RateBudget).
ON, read order (`app.market_store`, docs/design/M71_READ_PATHS.md row 8):
1. The own store: Daily / Weekly from the NSE files (zero broker calls) over the window the broker path asks for (the
   completed sessions before today - the same bars, so equal inputs give equal signals); intraday from the reader's own
   stored 1-minute bars (M71Q-2: the member's own broker session's rows; the tenant's others for the OWNER only).
2. Only the newest missing bars by REST, through the member's OWN session (`market_store.sources.own_rest`), paced by
   the credential's shared DATA budget (`market_store.reader.paced_adapter`) - never the worker's order / exit budget
   (ADR-0004). Kept per credential until the next bar closes: a second cycle on the same bar makes zero history calls.
3. Nothing stored for the reader (cold), another exchange or an interval the store does not serve: the old read
   (`get_frames`), but on the member's own session through the same DATA budget.
Every budgeted call here is taken only if a token is there now (`api_budget.no_wait`): a data read never waits, so the
worker's cycle (and the next cycle's exits) is never held up; a refused read is a plain reason, retried next cycle.

Closed bars only (no look-ahead; the worker drops the forming bar of the higher timeframes as before). A missing bar is
never zero-filled: no bars, the newest bars not read or a hole inside the window -> `BarsNotRead` with a plain line, and
the worker skips the deployment's entry for this cycle ("waiting for data", never paused) exactly as on a failed read.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import OHLCVBar
from app.market_data.calendar import IST

logger = logging.getLogger(__name__)

PURPOSE = "strategy"
WAIT_SECONDS = 0.0                  # a worker data read never waits for a token (the cycle's exits come first)
NOT_STORED_DAILY = "Daily history for this symbol is not stored yet."
HOLE = "Part of the recent history is not stored yet."


class BarsNotRead(RuntimeError):
    """The strategy's bars could not be read store-first; the message is the plain reason shown on the deployment."""


async def store_reads_on(session: AsyncSession, tenant_id: int) -> bool:
    from app.market_store.models import FLAG
    from app.platform.controls import flag_enabled
    try:
        return await flag_enabled(session, FLAG, tenant_id)
    except Exception:  # noqa: BLE001 - a flag that cannot be read is off: the read exactly as before
        logger.warning("flag %s not read for tenant %s - reading strategy bars as before", FLAG, tenant_id)
        return False


def _lookback_days(market_data: Any, interval: str) -> int:
    from app.market_data.service import DAILY_MIN_LOOKBACK_DAYS, DEFAULT_LOOKBACK_DAYS
    days = int(getattr(market_data, "lookback_days", DEFAULT_LOOKBACK_DAYS) or DEFAULT_LOOKBACK_DAYS)
    return max(days, DAILY_MIN_LOOKBACK_DAYS) if interval == "day" else days


def _window(market_data: Any, exchange: str, interval: str, now: datetime) -> tuple[datetime, datetime]:
    """(start, end of the completed history) of the broker path's own window, so both read the same sessions."""
    window = getattr(market_data, "history_window", None)
    if callable(window):
        start, history_to = window(exchange, interval, now)
        return start.astimezone(UTC), history_to.astimezone(UTC)
    return now - timedelta(days=_lookback_days(market_data, interval)), now - timedelta(days=1)


def _frames(result: Any, base_timeframe: str, timeframes: list[str]) -> dict[str, pd.DataFrame]:
    """The store's closed bars -> the strategy's frames, exactly as `get_frames` builds them from broker bars (IST
    stamps: the higher timeframes are binned from the session open)."""
    from app.market_data.service import build_frames
    bars = [OHLCVBar(timestamp=b.timestamp.astimezone(IST), open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume)
            for b in result.bars()]
    return build_frames(bars, base_timeframe, timeframes) if bars else {}


def _refusal(result: Any) -> str | None:
    """The plain reason a served read is not decided on: the newest bars were not read, or a hole inside the window."""
    from app.market_store.models import NEWEST, OLDER
    for gap in result.gaps:
        if gap.kind == NEWEST:
            return gap.reason or "The newest bars could not be read."
    first = result.frame.index[0].to_pydatetime() if len(result.frame) else None
    for gap in result.gaps:
        if gap.kind == OLDER and first is not None and gap.end > first:
            return gap.reason or HOLE
    return None


async def _own_rest(session: AsyncSession, user: Any, symbol: str, exchange: str) -> Any:
    from app.market_store.sources import own_rest
    rest = await own_rest(session, user, symbol, exchange)
    return None if rest is None else replace(rest, wait_seconds=WAIT_SECONDS)


async def _paced_frames(session: AsyncSession, user: Any, market_data: Any, symbol: str, exchange: str, base_timeframe: str,
                        timeframes: list[str], reason: str) -> dict[str, pd.DataFrame]:
    """The old read (`get_frames`) on the member's OWN session through the credential's DATA budget, or `reason`."""
    from app.market_data import api_budget
    from app.market_store.models import NEWEST_BUDGET
    from app.market_store.reader import paced_adapter
    rest = await _own_rest(session, user, symbol, exchange)
    if rest is None:
        raise BarsNotRead(reason)
    service = copy.copy(market_data)
    service.broker = paced_adapter(rest)
    try:
        return await service.get_frames(symbol, exchange, base_timeframe, timeframes)
    except api_budget.BudgetUsed as exc:
        raise BarsNotRead(NEWEST_BUDGET) from exc


async def frames(session: AsyncSession, user: Any, market_data: Any, symbol: str, exchange: str, base_timeframe: str,
                 timeframes: list[str], *, now: datetime | None = None, store: bool | None = None) -> dict[str, pd.DataFrame]:
    """A deployment's strategy frames: flag OFF -> `market_data.get_frames(...)` unchanged; ON -> store-first (above).
    `store` None: read the flag for the member's organisation."""
    if store is None:
        store = await store_reads_on(session, int(getattr(user, "tenant_id", 0) or 0))
    if not store:
        return await market_data.get_frames(symbol, exchange, base_timeframe, timeframes)
    from app.market_data import api_budget
    with api_budget.no_wait():
        return await _store_frames(session, user, market_data, symbol, exchange, base_timeframe, timeframes,
                                   now=now or datetime.now(UTC))


async def _store_frames(session: AsyncSession, user: Any, market_data: Any, symbol: str, exchange: str, base_timeframe: str,
                        timeframes: list[str], *, now: datetime) -> dict[str, pd.DataFrame]:
    from app.market_store import reader as ms
    from app.market_store.models import CONNECT_FOR_INTRADAY, INTRADAY_MINUTES, UnknownInterval, canonical, is_eod
    ex = (exchange or "NSE").strip().upper()
    sym = symbol.strip().upper()
    try:
        name = canonical(base_timeframe)
    except UnknownInterval:
        return await _paced_frames(session, user, market_data, symbol, exchange, base_timeframe, timeframes, CONNECT_FOR_INTRADAY)
    start, history_to = _window(market_data, ex, base_timeframe, now)
    if is_eod(name):
        if not ms.eod_served(ex):
            return await _paced_frames(session, user, market_data, symbol, exchange, base_timeframe, timeframes, NOT_STORED_DAILY)
        # the broker path's Daily history ends at the last completed session before today: the same bars here
        got = await ms.read_bars(session, sym, name, start, history_to, user=user, purpose=PURPOSE, exchange=ex, now=now)
        if got.cold:
            return await _paced_frames(session, user, market_data, symbol, exchange, base_timeframe, timeframes, NOT_STORED_DAILY)
    else:
        stored = await ms.store_intraday(session, sym, ex, name, start, now, user=user, now=now)
        if not stored.minutes:
            return await _paced_frames(session, user, market_data, symbol, exchange, base_timeframe, timeframes, CONNECT_FOR_INTRADAY)
        holidays = await ms.holidays_for(session, ex)
        last_end = stored.minutes[-1].timestamp.astimezone(UTC) + ms.MINUTE
        behind = last_end < min(ms.last_closed_end(now, INTRADAY_MINUTES[name], holidays, ex), stored.end)
        rest = await _own_rest(session, user, sym, ex) if behind else None     # a store hit builds no broker session
        got = await ms.top_up(stored, rest=rest, now=now, holidays=holidays)
    why = _refusal(got) if got.served else (" ".join(got.gap_lines()) or None)
    if why is not None:
        raise BarsNotRead(why)
    logger.debug("strategy bars %s:%s %s from %s (%d bars, %d REST)", ex, sym, name, got.source, len(got.frame), got.rest_calls)
    return _frames(got, base_timeframe, timeframes)


__all__ = ["BarsNotRead", "frames", "store_reads_on", "WAIT_SECONDS"]
