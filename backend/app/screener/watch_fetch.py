"""Owner order (15 Nov, watchlist screens): fewer broker calls for a watchlist's Screen check (`watch_screen_routes.check`).

Only while `lean()` is active (the check route sets it); every other caller of `fetch_frames` is unchanged.

- Tail cache (`TailCachedAdapter`): completed intraday history fetched through the reader's own session is kept per
  symbol / interval for HISTORY_CACHE_TTL_SECONDS, under that session's own scope (M86 D4: never another member's
  copy). A later check reads it back and asks the broker only for the bars after the cached tail (or before its head).
  Only history that ends before today is cached: today's bars (still forming) always come from the broker.
- Coarse timeframes from 30-minute bars (`coarse_plan`, `coarse_frame`): a screen on 1-minute bars (e.g. 15m) that also
  reads 1h / 30m / 2h... got its whole long window as 1-minute bars (up to 60 days). With the broker serving 30-minute
  bars natively, the 1-minute window is cut to what the finer timeframes need, and the coarser timeframe's older
  sessions come from 30-minute history (closed bars only); the sessions the 1-minute window holds are built from it
  exactly as before, so the newest bars are the same bars.
- Daily context from ATP's own NSE daily store (`stored_daily`) instead of the broker, where the store has the symbol.

Nothing here decides anything about a trade; it only chooses where closed bars come from.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Dict, Iterator, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_LEAN: ContextVar[bool] = ContextVar("watch_screen_lean", default=False)
COARSE_INTERVAL = "30min"              # the broker interval the coarser timeframes' older sessions are read at
COARSE_TF = "30m"
_SESSION_OPEN = (9, 15)                # IST: a session's first bar (the 09:15-anchored grid of every intraday frame)


@contextmanager
def lean() -> Iterator[None]:
    token = _LEAN.set(True)
    try:
        yield
    finally:
        _LEAN.reset(token)


def active() -> bool:
    return _LEAN.get()


def tail_ttl() -> int:
    from app.market_data.service import HISTORY_CACHE_TTL_SECONDS
    return HISTORY_CACHE_TTL_SECONDS


# ------------------------------------------------------------------------------------------------------- the tail cache
async def _get(key: str) -> tuple[Optional[str], Optional[float]]:
    from app.cache.client import cache_get_with_ttl
    return await cache_get_with_ttl(key)


async def _set(key: str, value: str, ttl: int) -> None:
    from app.cache.client import cache_set
    await cache_set(key, value, ttl)


def _ist(t: datetime) -> datetime:
    from app.market_data.calendar import IST
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone(IST)


class TailCachedAdapter:
    """The adapter with its completed intraday history kept per symbol / interval (see the module docstring). Every other
    attribute (the budget's `taken` / `refused`, the broker's name, its intervals) passes through to `_inner`."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.served_from_cache = 0
        self.broker_requests = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _by_date(self) -> bool:
        return bool(getattr(self._inner, "history_by_date", False))

    def _key(self, symbol: str, exchange: str, interval: str) -> str:
        from app.market_data.feed_scope import fetch_scope
        return f"md:byob:{fetch_scope(self._inner)}:wstail:{getattr(self._inner, 'name', '?')}:{exchange}:{symbol}:{interval}"

    def _pos(self, t: datetime) -> Any:
        """Where `t` falls on the broker's own grid: its IST date for a date-only history API, else the instant."""
        return _ist(t).date() if self._by_date() else _ist(t)

    def _next(self, t: datetime) -> datetime:
        from app.market_data.calendar import IST
        if self._by_date():
            return datetime.combine(_ist(t).date() + timedelta(days=1), datetime.min.time(), IST)
        return t + timedelta(minutes=1)

    def _prev(self, t: datetime) -> datetime:
        from app.market_data.calendar import IST
        if self._by_date():
            return datetime.combine(_ist(t).date() - timedelta(days=1), datetime.min.time(), IST)
        return t - timedelta(minutes=1)

    def _slice(self, bars: List[Any], start: datetime, end: datetime) -> List[Any]:
        lo, hi = self._pos(start), self._pos(end)
        return [b for b in bars if lo <= self._pos(b.timestamp) <= hi]

    async def _fetch(self, symbol: str, exchange: str, interval: str, start: datetime, end: datetime) -> List[Any]:
        self.broker_requests += 1
        return list(await self._inner.get_historical_data(symbol, exchange, interval, start, end))

    async def get_historical_data(self, symbol: str, exchange: str, interval: str, start: datetime, end: datetime) -> List[Any]:
        from app.core.models import OHLCVBar
        from app.market_data.service import merge_bars
        today = _ist(datetime.now(UTC)).date()
        if interval == "day" or start is None or end is None or _ist(end).date() >= today:
            return await self._fetch(symbol, exchange, interval, start, end)       # today's bars are never cached
        key = self._key(symbol, exchange, interval)
        raw, left = await _get(key)
        entry: Optional[Dict[str, Any]] = None
        if raw:
            try:
                got = json.loads(raw)
                entry = {"lo": datetime.fromisoformat(got["lo"]), "hi": datetime.fromisoformat(got["hi"]),
                         "bars": [OHLCVBar.model_validate(b) for b in got["bars"]]}
            except (ValueError, TypeError, KeyError):
                entry = None
        lo, hi = start, end
        if entry is not None and self._pos(entry["lo"]) <= self._pos(start) and self._pos(end) <= self._pos(entry["hi"]):
            self.served_from_cache += 1
            if left is not None and left < tail_ttl() / 2:                   # in use: kept, never longer than its TTL at once
                await self._store(key, entry["lo"], entry["hi"], entry["bars"])
            return self._slice(entry["bars"], start, end)
        if entry is not None and self._pos(entry["lo"]) <= self._pos(start) <= self._pos(self._next(entry["hi"])) \
                and self._pos(end) > self._pos(entry["hi"]):
            newer = await self._fetch(symbol, exchange, interval, self._next(entry["hi"]), end)     # only after the tail
            bars, lo, hi = merge_bars(entry["bars"], newer), entry["lo"], end
        elif entry is not None and self._pos(start) < self._pos(entry["lo"]) and \
                self._pos(self._prev(entry["lo"])) <= self._pos(end) <= self._pos(entry["hi"]):
            older = await self._fetch(symbol, exchange, interval, start, self._prev(entry["lo"]))   # only before the head
            bars, lo, hi = merge_bars(older, entry["bars"]), start, entry["hi"]
        else:
            bars = await self._fetch(symbol, exchange, interval, start, end)
        await self._store(key, lo, hi, bars)
        return self._slice(bars, start, end)

    async def _store(self, key: str, lo: datetime, hi: datetime, bars: List[Any]) -> None:
        value = json.dumps({"lo": lo.isoformat(), "hi": hi.isoformat(), "bars": [b.model_dump(mode="json") for b in bars]})
        await _set(key, value, tail_ttl())


# ----------------------------------------------------------------------------------------- coarse timeframes from 30m
@dataclass(frozen=True)
class CoarsePlan:
    fine: Dict[str, int]                # the lookback the 1-minute window is sized by
    coarse: Dict[str, int]              # timeframe -> bars read from 30-minute history
    days: int                           # calendar days of 30-minute history


def coarse_plan(window: Dict[str, int], base_tf: str, interval: str, adapter: Any, fetch_days: Any) -> Optional[CoarsePlan]:
    """The split, or None (nothing to change): only for a screen fetched at 1-minute bars whose broker serves 30-minute
    bars natively, when an intraday timeframe that 30-minute bars build (30m, 1h, 2h...) needs a longer window than the
    finer ones."""
    from app.market_data.intervals import native_intervals
    from app.screener import nodes
    if interval != "1min" or COARSE_INTERVAL not in native_intervals(adapter):
        return None
    daily = nodes.TF_MINUTES["1d"]
    coarse = {tf: b for tf, b in window.items() if nodes.TF_MINUTES.get(tf, 0) < daily and nodes.TF_MINUTES.get(tf, 0) % 30 == 0
              and nodes.TF_MINUTES.get(tf, 0) > nodes.TF_MINUTES[base_tf]}
    if not coarse:
        return None
    fine = {tf: b for tf, b in window.items() if tf not in coarse} or {base_tf: 1}
    fine_days, all_days = fetch_days(fine, base_tf), fetch_days(window, base_tf)
    if fine_days >= all_days:
        return None
    return CoarsePlan(fine=fine, coarse=coarse, days=fetch_days(coarse, base_tf))


def _first_full_session(index: pd.DatetimeIndex) -> Optional[date]:
    """The IST date of the first session the frame holds from its open (09:15), None when it holds none."""
    from app.market_data.calendar import IST
    ist = index.tz_convert(IST) if index.tz is not None else index.tz_localize("UTC").tz_convert(IST)
    seen: set[date] = set()
    for t in ist:
        d = t.date()
        if d in seen:
            continue
        seen.add(d)
        if (t.hour, t.minute) == _SESSION_OPEN:
            return d
    return None


def coarse_frame(thirty: pd.DataFrame, base: pd.DataFrame, base_tf: str, tf: str, now: datetime, exchange: Optional[str]) -> pd.DataFrame:
    """`tf` bars: the sessions before the 1-minute window from 30-minute history (closed bars only, `closed_only` at `now`
    and the resampler's own cut of a bucket still forming), then the sessions the window holds built from the base
    frame exactly as the runtime builds them. No bar closes after `now` and none after the base frame's last bar."""
    from app.market_data.calendar import IST
    from app.screener.runtime import closed_only, resample
    recent = resample(base, base_tf, tf, exchange=exchange) if len(base) else base
    boundary = _first_full_session(base.index) if len(base) else None
    if thirty is None or thirty.empty or boundary is None:
        return recent
    older = closed_only(thirty, COARSE_TF, now, exchange)
    older = older[[t.date() < boundary for t in older.index.tz_convert(IST)]]    # whole sessions before the window
    if older.empty:
        return recent
    older = older if tf == COARSE_TF else resample(older, COARSE_TF, tf, exchange=exchange)
    keep = recent[[t.date() >= boundary for t in recent.index.tz_convert(IST)]] if len(recent) else recent
    if older.empty:
        return recent
    older = older.copy()
    if len(keep):
        older.index = older.index.tz_convert(keep.index.tz)
        cols = [c for c in keep.columns if c in older.columns]
        return pd.concat([older[cols], keep[cols]])
    return older


async def attach_coarse(universe: List[Any], service_of: Any, plan: CoarsePlan, base_tf: str, exchange: str, now: datetime,
                        skip: set[str]) -> int:
    """Each symbol's coarser frames (see `coarse_frame`) from its own service's 30-minute history (`service_of(data)`).
    A symbol whose 30-minute history fails keeps the runtime's own build from the base frame (shorter). Returns how many
    symbols got them."""
    import asyncio

    from app.core.models import bars_to_dataframe
    done = 0

    async def one(data: Any) -> None:
        nonlocal done
        svc = service_of(data)
        base = data.frames.get(base_tf)
        if svc is None or base is None or base.empty or data.symbol in skip:
            return
        try:
            lo, hi = svc.history_window(exchange, COARSE_INTERVAL, now)
            bars = await svc._history_chunks(data.symbol, exchange, COARSE_INTERVAL, lo, hi)
        except Exception as exc:  # noqa: BLE001 - the runtime builds the frame from the base bars instead
            logger.info("30-minute history not loaded for %s:%s: %s", exchange, data.symbol, type(exc).__name__)
            return
        df = bars_to_dataframe(bars) if bars else pd.DataFrame()
        if df.empty:
            return
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        for tf in plan.coarse:
            data.frames[tf] = coarse_frame(df, base, base_tf, tf, now, exchange)
        done += 1
    await asyncio.gather(*(one(d) for d in universe))
    return done


async def stored_daily(session: Any, symbols: List[str], exchange: str, days: int, now: datetime) -> Dict[str, pd.DataFrame]:
    """The NSE daily store's closed sessions for the symbols it holds (no broker call); {} without a database."""
    if session is None or not symbols:
        return {}
    from app.market_store import reader as ms
    if not ms.eod_served(exchange):
        return {}
    try:
        got = await ms.read_many(session, symbols, "1d", now - timedelta(days=days), now, purpose="screener-context",
                                 exchange=exchange, now=now)
    except Exception as exc:  # noqa: BLE001 - the broker's daily history stays the fallback
        logger.info("stored daily context not read: %s", type(exc).__name__)
        return {}
    return {s: r.frame for s, r in got.items() if r.served and r.frame is not None and not r.frame.empty}
