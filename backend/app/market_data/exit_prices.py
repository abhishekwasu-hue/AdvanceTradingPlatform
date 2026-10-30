"""M55 S2 (ADR-0004: exits are never blocked): prices for the exits of an organisation with no usable broker session.

An exit needs a price, and until now that price came only through the organisation's own broker session - so a login
that ended left its open positions without a stop, a target or the square-off until he logged in again. A PAPER exit
needs nothing but a price, so the exits now run on the newest price ATP already holds without a broker call:

1. the organisation's own data-hub connection (`hub.org_namespace`: its stream in this process, else its snapshot), and
2. when the organisation does not bring its own broker data (flag byob_data_tier off), the shared stream keys
   (`md:tick:<broker>:...`) - the same keys its MarketDataService reads first while a session is usable.

A price older than config.EXIT_FALLBACK_TICK_MAX_AGE_SECONDS (default: the broker-quote staleness limit) is never used:
the caller then makes no exit decision this cycle, exactly as with a stale broker quote (StaleMarketDataError). Nothing
here places an order or calls a broker; a LIVE exit still goes out only through a broker session (close_position).
"""
from collections.abc import Iterable
from datetime import UTC, datetime

from app.core import config
from app.core.models import OHLCVBar
from app.market_data.freshness import StaleMarketDataError


class StoredTickPrices:
    """The price-lookup half of MarketDataService (`get_ltp`, `get_today_minutes`) from stored ticks only."""

    def __init__(self, tenant_id: int, brokers: Iterable[str], *, shared: bool) -> None:
        from app.brokers.registry import available_brokers
        self.tenant_id = tenant_id
        names = [b for b in brokers if b]
        self.brokers = list(dict.fromkeys([*names, *available_brokers()]))
        self.shared = shared
        self.used = 0                         # prices served this cycle (the worker logs it)

    def _caches(self) -> list:
        from app.market_data.hub import org_namespace, tick_cache_for
        from app.market_data.stream import tick_cache
        caches = [tick_cache_for(org_namespace(self.tenant_id))]
        if self.shared:
            caches.append(tick_cache)
        return caches

    async def get_ltp(self, symbol: str, exchange: str = "NSE", now: datetime | None = None) -> float:
        now = now or datetime.now(UTC)
        limit = max(1, int(config.EXIT_FALLBACK_TICK_MAX_AGE_SECONDS))
        newest = None
        for cache in self._caches():
            for broker in self.brokers:
                tick = await cache.get(broker, exchange, symbol)
                if tick is not None and (newest is None or tick.best_ts > newest.best_ts):
                    newest = tick
        if newest is None:
            raise StaleMarketDataError(f"{exchange}:{symbol} no stored price (no broker session)")
        if newest.age_seconds(now) > limit:
            raise StaleMarketDataError(f"{exchange}:{symbol} stored price is {newest.age_seconds(now):.0f}s old (no broker session)")
        self.used += 1
        return float(newest.ltp)

    async def get_today_minutes(self, symbol: str, exchange: str, now: datetime | None = None) -> list[OHLCVBar]:
        """No candles without a broker session: a candle-close trigger makes no decision this pass (it waits); a price
        trigger still fires on the stored price."""
        del now
        raise StaleMarketDataError(f"{exchange}:{symbol} no candles without a broker session")
