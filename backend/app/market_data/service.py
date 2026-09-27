"""Broker-backed candles and prices for the autonomous worker.

Design constraints (learned the hard way on the previous single-user bot this platform
replaces): never hit the broker's historical API on every evaluation - one deployment on NIFTY
evaluated every 60s by ten tenants is ten identical fetches a minute otherwise - so candles are
cached in Redis for a short TTL, keyed by broker+exchange+symbol+interval (public market data,
not tenant-private, so the cache is deliberately shared across tenants). Redis is optional: the
cache layer fails open and the fetch simply happens every cycle.

Only the base interval (normally 1 minute) is ever fetched; every timeframe a strategy declares
(5min, 15min, 30min, 60min, ...) is resampled up from it, anchored to the 09:15 IST open so bars
match the broker's own charts. That keeps the broker calls to two per symbol per cycle at most
(history + today's intraday) whatever the strategy's timeframe mix.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd

from app.brokers.base import BrokerInterface
from app.cache.client import cache_get, cache_set
from app.core.models import OHLCVBar, bars_to_dataframe
from app.core.resampling import resample_ohlc
from app.market_data.calendar import IST, MARKET_OPEN
from app.market_data.freshness import StaleMarketDataError, quote_is_stale
from app.observability.metrics import MARKET_DATA_STALE

logger = logging.getLogger(__name__)

DEFAULT_CANDLE_CACHE_TTL_SECONDS = 60
DEFAULT_LOOKBACK_DAYS = 5


def _cache_key(broker_name: str, exchange: str, symbol: str, interval: str) -> str:
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


class MarketDataService:
    def __init__(
        self, broker: BrokerInterface, *, cache_ttl_seconds: int = DEFAULT_CANDLE_CACHE_TTL_SECONDS,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> None:
        self.broker = broker
        self.cache_ttl_seconds = cache_ttl_seconds
        self.lookback_days = lookback_days

    async def get_candles(
        self, symbol: str, exchange: str = "NSE", interval: str = "1min", now: Optional[datetime] = None,
    ) -> List[OHLCVBar]:
        """Recent history plus today's bars so far, ascending, from cache when fresh."""
        key = _cache_key(self.broker.name, exchange, symbol, interval)
        cached = await cache_get(key)
        if cached:
            try:
                return [OHLCVBar.model_validate(item) for item in json.loads(cached)]
            except (ValueError, TypeError):
                logger.warning("Discarding unreadable cached candles for %s", key)

        bars = await self._fetch(symbol, exchange, interval, now)
        if bars:
            await cache_set(key, json.dumps([b.model_dump(mode="json") for b in bars]), self.cache_ttl_seconds)
        return bars

    async def _fetch(self, symbol: str, exchange: str, interval: str, now: Optional[datetime]) -> List[OHLCVBar]:
        now_ist = (now or datetime.now(timezone.utc)).astimezone(IST)
        history_to = now_ist - timedelta(days=1)
        history_from = now_ist - timedelta(days=self.lookback_days)
        history = await self.broker.get_historical_data(symbol, exchange, interval, history_from, history_to)
        intraday = await self.broker.get_intraday_candles(symbol, exchange, interval)
        merged = merge_bars(history, intraday)
        logger.debug("Fetched %d candles for %s:%s (%d history, %d intraday)", len(merged), exchange, symbol, len(history), len(intraday))
        return merged

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
        StaleMarketDataError is raised and the caller makes no decision this cycle (Phase G1)."""
        quote = await self.broker.get_quote_for_symbol(symbol, exchange)
        if quote is None:
            return await self.broker.get_ltp_for_symbol(symbol, exchange)
        reason = quote_is_stale(quote.timestamp, now or datetime.now(timezone.utc))
        if reason is not None:
            MARKET_DATA_STALE.labels(kind="quote").inc()
            raise StaleMarketDataError(f"{exchange}:{symbol} {reason}")
        return float(quote.ltp)
