"""Broker-backed candles and prices for the autonomous worker.

Design constraints (learned the hard way on the previous single-user bot this platform
replaces): never hit the broker's historical API on every evaluation - one deployment on NIFTY
evaluated every 60s by ten tenants is ten identical fetches a minute otherwise - so candles are
cached in Redis for a short TTL, keyed by broker+exchange+symbol+interval (public market data,
not tenant-private, so the cache is deliberately shared across tenants). Redis is optional: the
cache layer fails open and the fetch simply happens every cycle.

LD-10: under that per-request cache, each broker call is shared by every user, tenant and bring-your-own-broker
session through `shared_candles` - the completed history (keyed by the broker's own request window) for a few minutes,
today's bars until the bar forming at fetch time closes - with one fetch at a time per key, made through the
requesting user's own adapter and budget. Candles only: prices for decisions (`get_ltp`) are never shared copies.

Only the base interval (normally 1 minute) is ever fetched; every timeframe a strategy declares
(5min, 15min, 30min, 60min, ...) is resampled up from it, anchored to the 09:15 IST open so bars
match the broker's own charts. That keeps the broker calls to two per symbol per cycle at most
(history + today's intraday) whatever the strategy's timeframe mix.
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from app.brokers.base import BrokerInterface
from app.cache.client import cache_get, cache_set
from app.core.models import OHLCVBar, bars_to_dataframe
from app.core.resampling import resample_ohlc
from app.market_data import periods, shared_candles
from app.market_data.calendar import IST, MARKET_CLOSE, MARKET_OPEN, previous_session_close
from app.market_data.freshness import StaleMarketDataError, quote_is_stale
from app.market_data.lake_fill import LakeFill
from app.observability.metrics import MARKET_DATA_STALE

logger = logging.getLogger(__name__)

DEFAULT_CANDLE_CACHE_TTL_SECONDS = 60
HISTORY_CACHE_TTL_SECONDS = 3600   # older history pages (get_history): past bars do not change
DEFAULT_LOOKBACK_DAYS = 5
DAILY_MIN_LOOKBACK_DAYS = 400   # Phase AS: about 270 sessions - enough for a 200-day average


def _cache_key(broker_name: str, exchange: str, symbol: str, interval: str, namespace: Optional[str] = None) -> str:
    if namespace:
        # BT1: candles fetched through one user's own broker session are cached for that session only.
        return f"md:byob:{namespace}:candles:{broker_name}:{exchange}:{symbol}:{interval}"
    return f"md:candles:{broker_name}:{exchange}:{symbol}:{interval}"


def merge_bars(*series: List[OHLCVBar]) -> List[OHLCVBar]:
    """Union of several bar lists, deduplicated on timestamp (later lists win, so today's
    intraday bar for a minute overrides a stale historical copy), ascending."""
    by_ts: Dict[datetime, OHLCVBar] = {}
    for bars in series:
        for bar in bars:
            by_ts[bar.timestamp] = bar
    return [by_ts[ts] for ts in sorted(by_ts)]


def build_frames(bars: List[OHLCVBar], base_timeframe: str, timeframes: List[str]) -> Dict[str, pd.DataFrame]:
    """The `data` dict a strategy's analyze() takes: base bars as-is, every other timeframe
    resampled up from them with bins anchored to the exchange open."""
    base_df = bars_to_dataframe(bars)
    frames: Dict[str, pd.DataFrame] = {}
    for tf in timeframes:
        if tf == base_timeframe:
            frames[tf] = base_df
            continue
        frames[tf] = resample_ohlc(base_df, tf, origin=_session_origin(base_df))
    return frames


def _session_origin(df: pd.DataFrame) -> pd.Timestamp:
    """09:15 on the first bar's day, in the index's own timezone, so 30/60-minute bins start at
    the open (09:15, 09:45, ...) rather than the clock hour (09:00, 09:30, ...) - and since a day
    is a whole multiple of every intraday interval, later days land on the same grid."""
    first = df.index[0]
    origin = first.normalize() + pd.Timedelta(hours=MARKET_OPEN.hour, minutes=MARKET_OPEN.minute)
    return origin


_INTRADAY_SECONDS = {"1min": 60, "3min": 180, "5min": 300, "10min": 600, "15min": 900, "30min": 1800, "60min": 3600}
_NSE_SESSION_FAMILY = frozenset({"NSE", "BSE", "NFO", "BFO"})


def forming_capped_ttl(last_bar_start: datetime, interval: str, exchange: str, now: datetime, ttl_seconds: int) -> int:
    """Z10-b fix: a cached candle list never outlives the bar that was still forming when it was fetched. Otherwise a
    copy taken at 09:29:30 is served at 09:30:10 and its 09:25 bar - cut off 30 seconds early - reads as the closed
    bar (a bar-close alert or a strategy then decides on part of a bar). The entry expires when that bar closes: its
    start plus the interval, or the 15:30 IST close for the NSE family's last bucket and daily bar."""
    start = last_bar_start if last_bar_start.tzinfo else last_bar_start.replace(tzinfo=timezone.utc)
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    session_close = None
    if exchange.upper() in _NSE_SESSION_FAMILY:
        session_close = datetime.combine(start.astimezone(IST).date(), MARKET_CLOSE, IST)
    if interval == "day":
        if session_close is None:
            return ttl_seconds
        ends = session_close
    elif interval in _INTRADAY_SECONDS:
        ends = start + timedelta(seconds=_INTRADAY_SECONDS[interval])
        if session_close is not None and start < session_close < ends:
            ends = session_close
    else:
        return ttl_seconds
    if ends <= now:
        return ttl_seconds                                  # the newest bar had already closed: the usual TTL
    return max(1, min(ttl_seconds, int((ends - now).total_seconds())))


class _SharedBars(list):
    """Bars built from shared copies, with the seconds the shortest-lived copy has left (None: not known/not shared) -
    the per-request cache above them is capped at it."""

    def __init__(self, bars: List[OHLCVBar], copy_seconds_left: Optional[float]) -> None:
        super().__init__(bars)
        self.copy_seconds_left = copy_seconds_left


def window_anchor_dates(start: datetime, end: datetime) -> str:
    return shared_candles.window_anchor(True, start, end)


DATA_STARTS_WITHIN_DAYS = 7      # a chunk whose first bar is later than this after its start: the broker has nothing older
RECENT_CHUNK_DAYS = 5            # a daily chunk ending this close to today keeps the short LD-10 history lifetime
CHUNK_LIMIT = "chunk limit"      # DailyRange.stopped: the request's share of broker fetches was used; the next one continues


@dataclass
class DailyRange:
    """UI2-4a2: completed daily bars of a date range fetched in chunks. `covered_from` is the oldest date the fetch
    reached (later than the range's start when it stopped early); `stopped` says why it stopped early (None: it did not,
    or the broker simply has nothing older)."""
    bars: List[OHLCVBar]
    covered_from: date
    stopped: Optional[str] = None
    fetched: int = 0             # broker fetches made (shared copies already there are not counted)


class MarketDataService:
    def __init__(
        self, broker: BrokerInterface, *, cache_ttl_seconds: int = DEFAULT_CANDLE_CACHE_TTL_SECONDS,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS, cache_namespace: Optional[str] = None,
    ) -> None:
        self.broker = broker
        self.cache_ttl_seconds = cache_ttl_seconds
        self.lookback_days = lookback_days
        # BT1: set for a user's own broker session (bring your own broker); None keeps the shared worker cache.
        self.cache_namespace = cache_namespace or None
        # TM Part 1 (data hub): the connection whose hub stream prices `get_ltp` (None: the cache namespace's ticks).
        self.tick_namespace: Optional[str] = None
        # C1: the exchange holidays the history window skips (set by callers holding a database session - the chart
        # route); empty = weekends only, as the market calendar does without its table.
        self.holidays: Set[date] = set()
        # DH2 / DH6: the reader's own last-session 1-minute bars when the broker's history lags (lake_fill.for_reader);
        # None = the broker's bars only (the worker, a reader without his own lake).
        self.lake_fill: Optional[LakeFill] = None

    async def get_candles(
        self, symbol: str, exchange: str = "NSE", interval: str = "1min", now: Optional[datetime] = None, fresh: bool = False,
    ) -> List[OHLCVBar]:
        """Recent history plus today's bars so far, ascending, from cache when fresh. `fresh` skips the cache read (the
        result is still cached for others): S4b-2 intrabar alerts need the bar forming now, not a copy up to a TTL old."""
        key = _cache_key(self.broker.name, exchange, symbol, interval, self.cache_namespace)
        lookback = self._lookback(interval)
        if lookback != DEFAULT_LOOKBACK_DAYS:
            # Phase AA: the research pages ask for longer windows than the worker; a 5-day
            # worker fetch must not be served back as a 30-day one (or the reverse).
            key = f"{key}:{lookback}d"
        cached = None if fresh else await cache_get(key)
        if cached:
            try:
                return [OHLCVBar.model_validate(item) for item in json.loads(cached)]
            except (ValueError, TypeError):
                logger.warning("Discarding unreadable cached candles for %s", key)

        bars = await (self._fetch(symbol, exchange, interval, now, fresh=True) if fresh else self._fetch(symbol, exchange, interval, now))
        if bars:
            ttl = forming_capped_ttl(bars[-1].timestamp, interval, exchange, now or datetime.now(timezone.utc), self.cache_ttl_seconds)
            # LD-10 review: built from shared copies, this entry never outlives them (today's bars stay <= 60 s old).
            left = getattr(bars, "copy_seconds_left", None)
            if left is not None:
                ttl = min(ttl, int(left))
            # A session's own cache over the shared copies would only repeat them (one user rarely re-opens the same
            # chart inside a minute) at the cost of a JSON dump and a Redis write per chart: not written. The shared
            # per-request cache (the worker's, no namespace) is, capped at the copies' remaining life.
            if self.cache_namespace and left is not None:
                ttl = 0
            if ttl >= 1:
                await cache_set(key, json.dumps([b.model_dump(mode="json") for b in bars]), ttl)
        return await self._fill_last_session(symbol, exchange, interval, list(bars), now)

    async def _fill_last_session(self, symbol: str, exchange: str, interval: str, bars: List[OHLCVBar],
                                 now: Optional[datetime]) -> List[OHLCVBar]:
        """DH2 / DH6: when the broker's 1-minute history stops before the last closed session (Upstox serves that session
        late, and its intraday endpoint is empty before the next open), the reader's own lake bars fill the gap. Applied
        after the cache (the cached copy stays the broker's, shareable); a filled list is never cached."""
        if self.lake_fill is None or interval != "1min":
            return bars
        history_from, history_to = self.history_window(exchange, interval, now)
        # The last closed session must have bars - also when today's bars already came (the gap is then mid-series).
        if any(b.timestamp.astimezone(IST).date() == history_to.date() for b in bars):
            return bars
        before = [b.timestamp for b in bars if b.timestamp <= history_to]
        start = max(before) if before else history_from
        extra = await self.lake_fill(symbol, exchange, start.astimezone(timezone.utc), history_to.astimezone(timezone.utc))
        return merge_bars(bars, extra) if extra else bars

    async def get_history(self, symbol: str, exchange: str, interval: str, before: date) -> List[OHLCVBar]:
        """Completed bars for the `lookback_days` calendar days ending the day before `before` (IST) -
        what a chart loads when it is scrolled back past its oldest bar. Past bars never change, so
        they are cached for HISTORY_CACHE_TTL_SECONDS under their own date range."""
        before = min(before, datetime.now(timezone.utc).astimezone(IST).date())   # never today's forming bars
        start = datetime.combine(before - timedelta(days=self.lookback_days), dtime(0, 0), tzinfo=IST)
        end = datetime.combine(before - timedelta(days=1), dtime(23, 59), tzinfo=IST)
        # LD-10: whole IST days, so the same request for every user and broker - one shared copy (shared_candles).
        key = shared_candles.key(self.broker.name, exchange, symbol, interval, "page", window_anchor_dates(start, end))

        async def fetch() -> List[OHLCVBar]:
            return merge_bars(await self.broker.get_historical_data(symbol, exchange, interval, start, end))
        return await shared_candles.get_or_fetch(key, fetch, lambda bars: HISTORY_CACHE_TTL_SECONDS)

    async def get_daily_range(self, symbol: str, exchange: str, start: date, end: date, now: Optional[datetime] = None, *,
                              max_new_chunks: Optional[int] = None, max_chunks: Optional[int] = None,
                              deadline: Optional[float] = None, newest_raises: bool = True) -> DailyRange:
        """UI2-4a2: completed daily bars for the IST dates start..end (never today), for weekly and monthly charts.
        Fetched newest first in whole-date chunks of at most the broker's `daily_days_per_request` days, each one the
        shared copy of its two dates (LD-10 `page` part, the same key as an older-history page of the same dates), so
        every user asking on the same day shares them. A chunk with no bars, or whose bars start well after its first
        day, means the broker has nothing older: no further calls.

        Stops early - the bars so far, `stopped` saying why - when a chunk gets no budget token or fails (the newest
        chunk's failure is the caller's when `newest_raises`), after `max_chunks` chunks or `max_new_chunks` broker fetches
        (CHUNK_LIMIT; shared copies already there cost nothing and do not count as fetches, so the next request
        continues), or when an older chunk
        would start past `deadline` (time.monotonic(); "time")."""
        from app.market_data import api_budget
        today = (now or datetime.now(timezone.utc)).astimezone(IST).date()
        end = min(end, today - timedelta(days=1))
        if end < start:
            return DailyRange([], start)
        cap = int(getattr(self.broker, "daily_days_per_request", 365) or 365)
        parts: List[List[OHLCVBar]] = []
        covered_from = end + timedelta(days=1)
        stopped: Optional[str] = None
        fetched = 0
        for i, (lo, hi) in enumerate(periods.chunks(start, end, cap)):
            if max_chunks is not None and i >= max_chunks:
                stopped = CHUNK_LIMIT
                break
            lo_dt = datetime.combine(lo, dtime(0, 0), tzinfo=IST)
            hi_dt = datetime.combine(hi, dtime(23, 59), tzinfo=IST)
            key = shared_candles.key(self.broker.name, exchange, symbol, "day", "page", window_anchor_dates(lo_dt, hi_dt))
            # A chunk holding the last few days lives as long as LD-10's history copies (a broker's late correction of
            # yesterday's bar shows within minutes); older chunks are final.
            ttl = shared_candles.HISTORY_TTL_SECONDS if hi >= today - timedelta(days=RECENT_CHUNK_DAYS) else HISTORY_CACHE_TTL_SECONDS
            got: Optional[List[OHLCVBar]] = await shared_candles.load(key)
            if not got:
                if max_new_chunks is not None and fetched >= max_new_chunks:
                    stopped = CHUNK_LIMIT
                    break
                if deadline is not None and (i > 0 or not newest_raises) and time.monotonic() >= deadline:
                    stopped = "time"
                    break

                async def fetch(lo_dt: datetime = lo_dt, hi_dt: datetime = hi_dt) -> List[OHLCVBar]:
                    return merge_bars(await self.broker.get_historical_data(symbol, exchange, "day", lo_dt, hi_dt))

                def ttl_for(bars: List[OHLCVBar], ttl: int = ttl) -> int:
                    return ttl
                fetched += 1
                try:
                    got = await shared_candles.get_or_fetch(key, fetch, ttl_for)
                except api_budget.BudgetUsed:
                    if i == 0 and newest_raises:
                        raise
                    stopped = "budget used"
                    break
                except Exception as exc:  # noqa: BLE001 - the newer chunks still make a chart
                    if i == 0 and newest_raises:
                        raise
                    logger.info("Older daily chunk %s..%s failed for %s:%s: %s", lo, hi, exchange, symbol, exc)
                    stopped = f"{type(exc).__name__}: {exc}"[:200]
                    break
            bars = [b for b in got if lo <= periods.ist_date(b.timestamp) <= hi]
            parts.append(bars)
            covered_from = lo
            if not bars or periods.ist_date(bars[0].timestamp) > lo + timedelta(days=DATA_STARTS_WITHIN_DAYS):
                break
        return DailyRange(merge_bars(*reversed(parts)), covered_from, stopped, fetched)

    async def get_today_minutes(self, symbol: str, exchange: str, now: Optional[datetime] = None) -> List[OHLCVBar]:
        """UI2-4a2: today's one-minute bars so far - the same shared copy (LD-10 `today` part) an intraday chart of the
        symbol reads, fetched through this service's adapter when none is fresh."""
        now_utc = now or datetime.now(timezone.utc)
        key = shared_candles.key(self.broker.name, exchange, symbol, "1min", "today", shared_candles.today_anchor(now_utc))

        async def call() -> List[OHLCVBar]:
            return await self.broker.get_intraday_candles(symbol, exchange, "1min")

        def ttl(bars: List[OHLCVBar]) -> int:
            return forming_capped_ttl(bars[-1].timestamp, "1min", exchange, now or datetime.now(timezone.utc), shared_candles.TODAY_TTL_SECONDS)
        return await shared_candles.get_or_fetch(key, call, ttl)

    async def get_today_bar(self, symbol: str, exchange: str, now: Optional[datetime] = None) -> Optional[OHLCVBar]:
        """UI2-4a2: today's daily bar (00:00 IST) built from today's one-minute bars; None before the first one. Only the
        chart route asks for it - `get_candles(..., "day")` keeps returning completed bars to strategies and workers."""
        now_utc = now or datetime.now(timezone.utc)
        return periods.today_daily_bar(await self.get_today_minutes(symbol, exchange, now_utc), now_utc.astimezone(IST).date())

    def _lookback(self, interval: str) -> int:
        """Phase AS: daily strategies need a year of bars (EMA 200, 52-week levels), not days."""
        return max(self.lookback_days, DAILY_MIN_LOOKBACK_DAYS) if interval == "day" else self.lookback_days

    def history_window(self, exchange: str, interval: str, now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
        """C1: (from, to) of the completed-history call, IST. It ends at the close of the newest trading day before today
        (the market calendar), not "now - 1 day": on a Saturday, a Sunday or a holiday the chart still ends at the last
        session's close, and a minute-precision broker no longer loses that session's bars after the clock time of
        "now". It starts `lookback` calendar days before now, and never after the start of that last session's day (a
        short lookback across a weekend still holds the last session). A 24x7 venue keeps "now - 1 day"."""
        now_ist = (now or datetime.now(timezone.utc)).astimezone(IST)
        close = previous_session_close(now_ist, self.holidays, exchange)
        history_to = close if close is not None else now_ist - timedelta(days=1)
        history_from = now_ist - timedelta(days=self._lookback(interval))
        last_day_start = datetime.combine(history_to.date(), dtime(0, 0), tzinfo=IST)
        return min(history_from, last_day_start), history_to

    async def _fetch(self, symbol: str, exchange: str, interval: str, now: Optional[datetime], *, fresh: bool = False) -> List[OHLCVBar]:
        now_utc = now or datetime.now(timezone.utc)
        history_from, history_to = self.history_window(exchange, interval, now_utc)
        # LD-10: each broker call through the shared copy (shared_candles) - the same request window, the same bars,
        # whoever asks; the fetch, when one is needed, goes through this service's own adapter (the user's budget).
        by_date = bool(getattr(self.broker, "history_by_date", False))
        history_key = shared_candles.key(self.broker.name, exchange, symbol, interval, "hist",
                                         shared_candles.window_anchor(by_date, history_from, history_to))
        history_ttl = shared_candles.HISTORY_TTL_SECONDS if by_date else shared_candles.HISTORY_MINUTE_TTL_SECONDS

        async def history_call() -> List[OHLCVBar]:
            return await self.broker.get_historical_data(symbol, exchange, interval, history_from, history_to)
        history_part = shared_candles.get_or_fetch_ttl(history_key, history_call, lambda bars: history_ttl)
        if interval == "day":
            history, left = await history_part
            # Phase AS: completed daily bars only - today's bar is still forming, and a swing
            # strategy decides on closed bars (entries go in the next session).
            logger.debug("Fetched %d daily candles for %s:%s", len(history), exchange, symbol)
            return _SharedBars(merge_bars(history), left)
        today_key = shared_candles.key(self.broker.name, exchange, symbol, interval, "today", shared_candles.today_anchor(now_utc))

        async def today_call() -> List[OHLCVBar]:
            return await self.broker.get_intraday_candles(symbol, exchange, interval)

        def today_ttl(bars: List[OHLCVBar]) -> int:
            # Never past the close of the bar still forming when it was fetched (Z10-b): a copy is not a closed bar.
            return forming_capped_ttl(bars[-1].timestamp, interval, exchange, now or datetime.now(timezone.utc),
                                      shared_candles.TODAY_TTL_SECONDS)
        # S4b-2: `fresh` needs today's bars as of now - no shared copy of them (history is final and still shared).
        today_part = shared_candles.get_or_fetch_ttl(today_key, today_call, today_ttl, read=not fresh)
        # LD-7: the two calls side by side, not one after the other (a chart waited for two broker round trips in a row).
        got_history, got_intraday = await asyncio.gather(history_part, today_part, return_exceptions=True)
        if isinstance(got_history, BaseException):
            raise got_history                     # as before: the history call's error first
        if isinstance(got_intraday, BaseException):
            raise got_intraday
        (history, history_left), (intraday, today_left) = got_history, got_intraday
        merged = merge_bars(history, intraday)
        logger.debug("Fetched %d candles for %s:%s (%d history, %d intraday)", len(merged), exchange, symbol, len(history), len(intraday))
        lefts = [x for x in (history_left, today_left) if x is not None]
        # An empty part was not stored (nothing to share) - it says nothing about how long the other part may live.
        return _SharedBars(merged, min(lefts) if lefts else None)

    async def get_frames(
        self, symbol: str, exchange: str, base_timeframe: str, timeframes: List[str],
    ) -> Dict[str, pd.DataFrame]:
        bars = await self.get_candles(symbol, exchange, base_timeframe)
        if not bars:
            return {}
        return build_frames(bars, base_timeframe, timeframes)

    async def get_ltp(self, symbol: str, exchange: str = "NSE", now: Optional[datetime] = None) -> float:
        """Current price for an exit decision. Deliberately uncached: an exit decision on a 60s-old
        price is a real stop-loss slip. When the broker can attach the exchange's own timestamp
        (get_quote_for_symbol) the quote must also be younger than QUOTE_MAX_STALE_SECONDS, or
        StaleMarketDataError is raised and the caller makes no decision this cycle (Phase G1).

        Phase S: when a streaming tick for the symbol is younger than TICK_MAX_AGE_SECONDS it is
        the price - it is the exchange's last trade, not a poll - and no REST call is made. An
        older or missing tick falls through to the REST quote exactly as before."""
        from app.market_data.stream import tick_cache
        namespace = self.tick_namespace or self.cache_namespace
        if namespace:
            # BT1 / TM Part 1: the session's own ticks - the hub stream's instance in this process, else its keys and
            # its hub snapshot.
            from app.market_data.hub import tick_cache_for
            ticks = tick_cache_for(namespace)
        else:
            ticks = tick_cache
        tick = await ticks.fresh(self.broker.name, exchange, symbol, now)
        if tick is not None:
            return float(tick.ltp)
        quote = await self.broker.get_quote_for_symbol(symbol, exchange)
        if quote is None:
            return await self.broker.get_ltp_for_symbol(symbol, exchange)
        reason = quote_is_stale(quote.timestamp, now or datetime.now(timezone.utc))
        if reason is not None:
            MARKET_DATA_STALE.labels(kind="quote").inc()
            raise StaleMarketDataError(f"{exchange}:{symbol} {reason}")
        return float(quote.ltp)
