"""N1 X1 (OPEN_QUESTIONS L2-5): the gate's Daily trend context - the bridge from the shared lake's Daily bars through the
ported fold (`daily_trend`) to the gate's `context_from_state`.

Computed once per instrument and IST day (the fold over ~10 years of Daily bars takes seconds and holds the GIL, so it
runs in a worker thread and the state is kept for the day), and known at yesterday's close: today's Daily bar is never
read (Trade `level_reaction.trend_context`). Fail-closed: no bars, fewer than 30, or any error -> `ok` False (no entry,
with the reason), and the read is retried only after `RETRY_SECONDS` (Trade's `trend_retry_min`, 15 minutes).

Review of #214 (H1): the trading cycle never pays for many cold folds. `warm` computes the day's contexts for every
symbol a template may watch in a background task outside the cycle (the worker spawns it, like the Vision drain);
inside the cycle `daily_context(..., allow_cold=False)` answers "trend pending" for a symbol not computed yet (no entry,
retried), and the runner allows at most `COLD_FOLDS_PER_CYCLE` cold folds itself.

Daily bars come only from the end-of-day lake sources (`EOD_SOURCES`, NSE bhavcopies through `candles_as_of`) - never a
broker's or a stream's bars (review L1).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.market_data.calendar import IST
from app.price_action.daily_trend import fold as daily_fold
from app.price_action.level_gate import gate as G

DAILY_LABELS = ("1d", "day")          # the lake's Daily timeframe labels (as the zones job reads them)
EOD_SOURCES = ("nse_bhav:cm", "nse_bhav:index")    # HB1 / B2 end-of-day sources, in preference order
R_PENDING = "trend pending (the Daily trend is being computed)"
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
    """Daily bars of `key` that ended before today's session (bar END <= today 09:15 IST), from the end-of-day sources only
    (`candles_as_of`: the preferred source, then its highest version). timestamp = the bar END (UTC); prepare() makes it
    the session date."""
    from app.market_lake.asof import candles_as_of
    end = datetime.combine(today, dtime(9, 15), IST).astimezone(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    rows: List[Any] = []
    for label in DAILY_LABELS:
        rows = await candles_as_of(session, key, label, start, end, sources=list(EOD_SOURCES))
        if rows:
            break
    if not rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])
    stamps = [r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc) for r in rows]
    return pd.DataFrame({"timestamp": pd.to_datetime(stamps, utc=True), "open": [float(r.open) for r in rows],
                         "high": [float(r.high) for r in rows], "low": [float(r.low) for r in rows],
                         "close": [float(r.close) for r in rows]})


def _last_state(bars: pd.DataFrame, today: date) -> Tuple[Any, Optional[str], str]:
    """(state, asof, reason) - the fold over the Daily frame known at yesterday's close (runs in a worker thread)."""
    d = daily_fold.prepare_daily(bars, pd.Timestamp(today)) if len(bars) else bars
    if len(d) < MIN_DAILY_BARS:
        return None, None, f"{G.R_NO_TREND} (Daily data too short: {len(d)} bars)"
    states = daily_fold.fold(d)
    return states[-1], str(pd.Timestamp(d["timestamp"].iloc[-1]).date()), ""


def is_cold(key: Optional[str], today: date, clock: Any = time.monotonic) -> bool:
    """Would `daily_context` have to fold now (no entry for the day, or a failed one due for its retry)?"""
    if not key:
        return False
    hit = _CACHE.get((key, today))
    return hit is None or (hit.state is None and clock() - hit.at >= RETRY_SECONDS)


async def daily_context(session: AsyncSession, key: Optional[str], today: date, gate_settings: Optional[Mapping[str, Any]] = None,
                        clock: Any = time.monotonic, allow_cold: bool = True) -> Dict[str, Any]:
    """The gate's trend context for `key` on `today` (cached per key and day; failures retried after RETRY_SECONDS).
    `allow_cold=False`: a symbol not computed yet answers "trend pending" (`pending` True) instead of folding now."""
    if not key:
        return _failed(f"{G.R_NO_TREND} (no lake instrument for the symbol)")
    if is_cold(key, today, clock) and not allow_cold:
        return {**_failed(R_PENDING), "pending": True}
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


async def warm(session: AsyncSession, keys: Iterable[str], today: date) -> int:
    """Computes the day's contexts for `keys` not cached yet (a background task outside the trading cycle). Returns the
    number of folds run."""
    done = 0
    for key in keys:
        if is_cold(key, today):
            await daily_context(session, key, today)
            done += 1
    return done
