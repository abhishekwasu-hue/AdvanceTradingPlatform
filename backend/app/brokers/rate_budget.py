"""Per-tenant broker API rate budgets.

Every Indian broker API is rate limited per API key (Upstox: roughly 25 req/s and 250 req/min on
most endpoints; Kite: 3 req/s on quotes and historical data, 10 req/s on orders). On a shared
worker one tenant with forty deployments can burn a broker's whole allowance - and, worse, the
429s it provokes land on *its own* exit orders. So each tenant's adapter is wrapped in a
`RateLimitedBroker` that draws from that tenant's own `RateBudget` (a per-second and a per-minute
token bucket) before every call. Tenants never share a budget: their credentials are their own
API keys, so their limits are their own too.
"""
import asyncio
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.brokers.base import BrokerInterface
from app.brokers.circuit_breaker import observe_call
from app.brokers.models import BrokerOrderRequest, BrokerOrderResponse
from app.core.enums import OrderSide


@dataclass(frozen=True)
class RateLimits:
    per_second: float
    burst: int
    per_minute: int


# Conservative: about a third of each broker's published allowance, leaving headroom for the
# tenant's own manual use of the same API key (their broker app, TradingView, ...).
BROKER_RATE_LIMITS: Dict[str, RateLimits] = {
    "upstox": RateLimits(per_second=8.0, burst=10, per_minute=200),
    "zerodha": RateLimits(per_second=2.0, burst=3, per_minute=100),
    "shoonya": RateLimits(per_second=3.0, burst=5, per_minute=120),
}
DEFAULT_RATE_LIMITS = RateLimits(per_second=2.0, burst=3, per_minute=100)


def limits_for(broker_name: str) -> RateLimits:
    return BROKER_RATE_LIMITS.get(broker_name, DEFAULT_RATE_LIMITS)


class _TokenBucket:
    def __init__(self, rate_per_second: float, capacity: float, clock=time.monotonic) -> None:
        self.rate = rate_per_second
        self.capacity = capacity
        self.tokens = capacity
        self.clock = clock
        self.updated = clock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now

    def time_until_token(self) -> float:
        self._refill()
        if self.tokens >= 1:
            return 0.0
        return (1 - self.tokens) / self.rate

    def take(self) -> None:
        self.tokens -= 1


class RateBudget:
    """Two buckets (per-second burst + per-minute allowance); `acquire()` waits for both."""

    def __init__(self, limits: RateLimits, clock=time.monotonic, sleep=asyncio.sleep) -> None:
        self.limits = limits
        self._second = _TokenBucket(limits.per_second, limits.burst, clock)
        self._minute = _TokenBucket(limits.per_minute / 60.0, limits.per_minute, clock)
        self._lock = asyncio.Lock()
        self._sleep = sleep
        self.calls = 0
        self.waited_seconds = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                wait = max(self._second.time_until_token(), self._minute.time_until_token())
                if wait <= 0:
                    self._second.take()
                    self._minute.take()
                    self.calls += 1
                    return
                self.waited_seconds += wait
                await self._sleep(wait)


class RateLimitedBroker(BrokerInterface):
    """Transparent wrapper: same interface, every broker call first draws a token from the
    tenant's budget. Order placement/cancellation is *not* exempt on purpose - an exit order
    that provokes a 429 is worse than one delayed by 200ms."""

    def __init__(self, inner: BrokerInterface, budget: RateBudget) -> None:
        self.inner = inner
        self.budget = budget
        self.name = inner.name

    @property
    def access_token(self) -> Optional[str]:
        return self.inner.access_token

    # Phase G2: this wrapper reports every call's outcome to the broker's circuit breaker, so a
    # caller holding it must not record the same call again (OrderRouter checks this flag).
    records_circuit = True

    async def _call(self, method: str, *args, **kwargs):
        await self.budget.acquire()
        try:
            result = await getattr(self.inner, method)(*args, **kwargs)
        except Exception as exc:
            observe_call(self.name, method, exc)
            raise
        observe_call(self.name, method, None)
        return result

    async def authenticate(self): return await self._call("authenticate")
    async def get_profile(self): return await self._call("get_profile")
    async def get_instruments(self, exchange=None): return await self._call("get_instruments", exchange)
    async def get_ltp(self, symbols): return await self._call("get_ltp", symbols)
    async def get_quote(self, symbols): return await self._call("get_quote", symbols)
    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        return await self._call("get_historical_data", symbol, exchange, interval, from_date, to_date)
    async def get_option_chain(self, underlying, expiry=None): return await self._call("get_option_chain", underlying, expiry)
    async def place_order(self, order: BrokerOrderRequest) -> BrokerOrderResponse: return await self._call("place_order", order)
    async def modify_order(self, order_id, quantity=None, price=None, trigger_price=None, order_type=None):
        return await self._call("modify_order", order_id, quantity, price, trigger_price, order_type)
    async def cancel_order(self, order_id): return await self._call("cancel_order", order_id)
    async def get_order_book(self): return await self._call("get_order_book")
    async def get_trade_book(self): return await self._call("get_trade_book")
    async def get_positions(self): return await self._call("get_positions")
    async def get_holdings(self): return await self._call("get_holdings")
    async def get_margins(self): return await self._call("get_margins")
    async def get_order_margin(self, order): return await self._call("get_order_margin", order)
    async def get_quote_for_symbol(self, symbol, exchange="NSE"): return await self._call("get_quote_for_symbol", symbol, exchange)
    async def get_balance(self): return await self._call("get_balance")
    async def disconnect(self): return await self._call("disconnect")

    # The non-abstract conveniences must go through the inner adapter's own overrides (Upstox
    # resolves instrument keys in them), each still costing one token.
    async def get_ltp_for_symbol(self, symbol: str, exchange: str = "NSE") -> float:
        return await self._call("get_ltp_for_symbol", symbol, exchange)

    async def get_intraday_candles(self, symbol: str, exchange: str, interval: str) -> List:
        return await self._call("get_intraday_candles", symbol, exchange, interval)

    async def place_stop_loss_order(
        self, symbol: str, exchange: str, transaction_type: OrderSide, quantity: float, trigger_price: float,
        product: str = "MIS", tag: Optional[str] = None,
    ) -> BrokerOrderResponse:
        return await self._call("place_stop_loss_order", symbol, exchange, transaction_type, quantity, trigger_price, product, tag)
