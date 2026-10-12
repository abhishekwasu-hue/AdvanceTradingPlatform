"""N1 X1 (OPEN_QUESTIONS L2-5): the gate's Daily trend context - the bridge from the shared lake's Daily bars through the
ported fold (`daily_trend`) to the gate's `context_from_state`.

Computed once per instrument and IST day (the fold over ~10 years of Daily bars takes seconds, so it runs in a worker
thread and the state is kept for the day), and known at yesterday's close: today's Daily bar is never read (Trade
`level_reaction.trend_context`). Fail-closed: no bars, fewer than 30, or any error -> `ok` False (no entry, with the
reason), and the read is retried only after `RETRY_SECONDS` (Trade's `trend_retry_min`, 15 minutes).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional, Tuple

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MdCandleRecord
from app.market_data.calendar import IST
from app.price_action.daily_trend import fold as daily_fold
from app.price_action.level_gate import gate as G

DAILY_LABELS = ("1d", "day")          # the lake's Daily timeframe labels (as the zones job reads them)
LOOKBACK_DAYS = 3650                  # ~10 years, as Trade's bots fetch
MIN_DAILY_BARS = 30                   # Trade `trend_context`: fewer -> "trend unclear"
RETRY_SECONDS = 15 * 60               # Trade `trend_retry_min`
MAX_ENTRIES = 512


@dataclass
class _Entry:
    state: Any                        # the last Daily state (a `DState`), or None when the read failed
    asof: Optional[str]
    reason: str
    at: float                         # time.monotonic() of the read


_CACHE: Dict[Tuple[str, date], _Entry] = {}


def clear_cache() -> None:
    _CACHE.clear()


def _failed(reason: str) -> Dict[str, Any]:
    return {"ok": False, "trend": "UNKNOWN", "sides": set(), "reason": reason}


async def lake_daily(session: AsyncSession, key: str, today: date) -> pd.DataFrame:
    """Daily bars of `key` that ended before today's session (bar END labels <= today 09:15 IST): the highest version per
    bar, as a frame (timestamp = the bar END, UTC; prepare() turns it into the session date)."""
    end = datetime.combine(today, dtime(9, 15), IST).astimezone(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    rows = (await session.execute(select(MdCandleRecord.ts, MdCandleRecord.version, MdCandleRecord.open, MdCandleRecord.high,
                                         MdCandleRecord.low, MdCandleRecord.close)
                                  .where(MdCandleRecord.instrument_key == key, MdCandleRecord.timeframe.in_(DAILY_LABELS),
                                         MdCandleRecord.ts > start, MdCandleRecord.ts <= end)
                                  .order_by(MdCandleRecord.ts, MdCandleRecord.version))).all()
    best: Dict[Any, Tuple[float, float, float, float]] = {}
    for ts, _version, o, h, low, c in rows:
        best[ts] = (float(o), float(h), float(low), float(c))           # ordered by version: the highest wins
    if not best:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])
    stamps = sorted(best)
    return pd.DataFrame({"timestamp": pd.to_datetime([s if s.tzinfo else s.replace(tzinfo=timezone.utc) for s in stamps], utc=True),
                         "open": [best[s][0] for s in stamps], "high": [best[s][1] for s in stamps],
                         "low": [best[s][2] for s in stamps], "close": [best[s][3] for s in stamps]})


def _last_state(bars: pd.DataFrame, today: date) -> Tuple[Any, Optional[str], str]:
    """(state, asof, reason) - the fold over the Daily frame known at yesterday's close (runs in a worker thread)."""
    d = daily_fold.prepare_daily(bars, pd.Timestamp(today)) if len(bars) else bars
    if len(d) < MIN_DAILY_BARS:
        return None, None, f"{G.R_NO_TREND} (Daily data too short: {len(d)} bars)"
    states = daily_fold.fold(d)
    return states[-1], str(pd.Timestamp(d["timestamp"].iloc[-1]).date()), ""


async def daily_context(session: AsyncSession, key: Optional[str], today: date, gate_settings: Optional[Mapping[str, Any]] = None,
                        clock: Any = time.monotonic) -> Dict[str, Any]:
    """The gate's trend context for `key` on `today` (cached per key and day; failures retried after RETRY_SECONDS)."""
    if not key:
        return _failed(f"{G.R_NO_TREND} (no lake instrument for the symbol)")
    hit = _CACHE.get((key, today))
    if hit is None or (hit.state is None and clock() - hit.at >= RETRY_SECONDS):
        try:
            bars = await lake_daily(session, key, today)
            state, asof, reason = await asyncio.to_thread(_last_state, bars, today)
        except Exception as exc:  # noqa: BLE001 - fail closed, with the reason
            state, asof, reason = None, None, f"{G.R_ERROR}: trend - {type(exc).__name__}: {str(exc)[:80]}"
        if len(_CACHE) >= MAX_ENTRIES:
            for stale in [k for k in _CACHE if k[1] < today] or list(_CACHE)[: MAX_ENTRIES // 4]:
                _CACHE.pop(stale, None)
        hit = _CACHE[(key, today)] = _Entry(state, asof, reason, clock())
    if hit.state is None:
        return _failed(hit.reason)
    return G.context_from_state(hit.state, gate_settings, asof=hit.asof)
