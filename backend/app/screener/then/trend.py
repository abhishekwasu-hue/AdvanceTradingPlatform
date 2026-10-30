"""N1 X1 (OPEN_QUESTIONS L2-5): the gate's Daily trend context - the bridge from the shared lake's Daily bars through the
ported fold (`daily_trend`) to the gate's `context_from_state`.

Computed once per instrument and IST day (the fold over ~10 years of Daily bars takes seconds and holds the GIL, so it
runs in a worker thread and the state is kept for the day), and known at yesterday's close: today's Daily bar is never
read (Trade `level_reaction.trend_context`). Fail-closed: no bars, fewer than 30, or any error -> `ok` False (no entry,
with the reason), and the read is retried only after `RETRY_SECONDS` (Trade's `trend_retry_min`, 15 minutes).

Review of #214 (H1): the trading cycle never folds. `warm` computes the day's contexts for the symbols templates may
watch in a background task outside the cycle (the worker spawns it, like the Vision drain); inside the cycle
`daily_context(..., allow_cold=False)` answers "trend pending" for a symbol not computed yet or being computed (no
entry, retried). Re-review (W1): `warm` reads each key with its own short session, selecting only the five columns, and
the session is closed before the fold, so no transaction stays open through the folds. (W2): at most `WARM_PER_RUN` keys
per run (today's matches first; the rest are reported and wait for the next run) and an LRU cache sized well above that,
so today's entries are never evicted by the warm-up itself.

Daily bars come only from the end-of-day lake sources (`EOD_SOURCES`, NSE bhavcopies through `candles_as_of`) - never a
broker's or a stream's bars (review L1).
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

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
MAX_ENTRIES = 512                     # LRU: the least recently used entry goes first (another day's before today's)
WARM_PER_RUN = MAX_ENTRIES // 2       # keys one warm-up run may fold


@dataclass
class _Entry:
    state: Any                        # the last Daily state (a `DState`), or None when the read failed
    asof: Optional[str]
    reason: str
    at: float                         # time.monotonic() of the read


_CACHE: "OrderedDict[Tuple[str, date], _Entry]" = OrderedDict()
_INFLIGHT: Set[Tuple[str, date]] = set()   # being folded now (by the warm-up): the cycle answers "pending", never folds it again


def clear_cache() -> None:
    _CACHE.clear()
    _INFLIGHT.clear()


def _store(key: Tuple[str, date], entry: _Entry) -> None:
    """LRU insert: when full, entries of earlier days go first, then the least recently used."""
    _CACHE[key] = entry
    _CACHE.move_to_end(key)
    while len(_CACHE) > MAX_ENTRIES:
        stale = next((k for k in _CACHE if k[1] < key[1]), None)
        _CACHE.pop(stale if stale is not None else next(iter(_CACHE)))


def _get(key: Tuple[str, date]) -> Optional[_Entry]:
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
    return hit


def _failed(reason: str) -> Dict[str, Any]:
    return {"ok": False, "trend": "UNKNOWN", "sides": set(), "reason": reason}


async def lake_daily(session: AsyncSession, key: str, today: date) -> pd.DataFrame:
    """Daily bars of `key` that ended before today's session (bar END <= today 09:15 IST), from the end-of-day sources only
    (`candle_values_as_of`: the preferred source, then its highest version; five columns, no ORM rows). timestamp = the
    bar END (UTC); prepare() makes it the session date."""
    from app.market_lake.asof import candle_values_as_of
    end = datetime.combine(today, dtime(9, 15), IST).astimezone(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    rows: List[Tuple[datetime, float, float, float, float]] = []
    for label in DAILY_LABELS:
        rows = await candle_values_as_of(session, key, label, start, end, sources=list(EOD_SOURCES))
        if rows:
            break
    if not rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])
    stamps = [ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc) for ts, *_ in rows]
    return pd.DataFrame({"timestamp": pd.to_datetime(stamps, utc=True), "open": [r[1] for r in rows], "high": [r[2] for r in rows],
                         "low": [r[3] for r in rows], "close": [r[4] for r in rows]})


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


def _fold_failed(exc: BaseException) -> Tuple[Any, Optional[str], str]:
    return None, None, f"{G.R_ERROR}: trend - {type(exc).__name__}: {str(exc)[:80]}"


async def _fold(bars: pd.DataFrame, today: date) -> Tuple[Any, Optional[str], str]:
    try:
        return await asyncio.to_thread(_last_state, bars, today)
    except Exception as exc:  # noqa: BLE001 - fail closed, with the reason
        return _fold_failed(exc)


async def daily_context(session: AsyncSession, key: Optional[str], today: date, gate_settings: Optional[Mapping[str, Any]] = None,
                        clock: Any = time.monotonic, allow_cold: bool = True) -> Dict[str, Any]:
    """The gate's trend context for `key` on `today` (cached per key and day; failures retried after RETRY_SECONDS).
    `allow_cold=False`: a symbol not computed yet answers "trend pending" (`pending` True) instead of folding now."""
    if not key:
        return _failed(f"{G.R_NO_TREND} (no lake instrument for the symbol)")
    if (key, today) in _INFLIGHT or (is_cold(key, today, clock) and not allow_cold):
        return {**_failed(R_PENDING), "pending": True}
    hit = _get((key, today))
    if hit is None or (hit.state is None and clock() - hit.at >= RETRY_SECONDS):
        _INFLIGHT.add((key, today))
        try:
            try:
                async with session.begin_nested():       # the caller's session: a failed read rolls back this savepoint only
                    bars = await lake_daily(session, key, today)
            except Exception as exc:  # noqa: BLE001 - fail closed, with the reason
                state, asof, reason = _fold_failed(exc)
            else:
                state, asof, reason = await _fold(bars, today)
            hit = _Entry(state, asof, reason, clock())
            _store((key, today), hit)
        finally:
            _INFLIGHT.discard((key, today))
    if hit.state is None:
        return _failed(hit.reason)
    return G.context_from_state(hit.state, gate_settings, asof=hit.asof)


@dataclass
class WarmReport:
    folded: int = 0
    skipped: List[str] = field(default_factory=list)     # cold keys left for the next run (over WARM_PER_RUN)


async def warm(session_factory: Any, keys: Iterable[str], today: date, limit: int = WARM_PER_RUN,
               clock: Any = time.monotonic) -> WarmReport:
    """Computes the day's contexts for the cold `keys`, in order, at most `limit` (a background task outside the trading
    cycle). Each key is read with its own session, which is closed before the fold (review W1)."""
    report = WarmReport()
    for key in keys:
        if not is_cold(key, today, clock) or (key, today) in _INFLIGHT:
            continue
        if report.folded >= limit:
            report.skipped.append(key)
            continue
        _INFLIGHT.add((key, today))
        try:
            try:
                async with session_factory() as session:
                    bars = await lake_daily(session, key, today)
            except Exception as exc:  # noqa: BLE001 - fail closed, retried after RETRY_SECONDS
                state, asof, reason = _fold_failed(exc)
            else:
                state, asof, reason = await _fold(bars, today)
            _store((key, today), _Entry(state, asof, reason, clock()))
            report.folded += 1
        finally:
            _INFLIGHT.discard((key, today))
    return report
