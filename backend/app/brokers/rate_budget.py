"""Per-tenant broker API rate budgets.

Every Indian broker API is rate limited per API key (Upstox: roughly 25 req/s and 250 req/min on
most endpoints; Kite: 1 req/s on quotes, 3 req/s on historical candles, 10 req/s on orders - Kite Connect's
published rate-limit table; LD-10 review: quotes were recorded here as 3/s). On a shared
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
from app.brokers.exceptions import BrokerAPIError
from app.brokers.models import BrokerOrderRequest, BrokerOrderResponse
from app.core.enums import OrderSide


@dataclass(frozen=True)
class RateLimits:
    per_second: float
    burst: int
    per_minute: int


# Conservative: about a third of each broker's published allowance, leaving headroom for the
# tenant's own manual use of the same API key (their broker app, TradingView, ...).
# Upstox also caps a user at 2,000 standard API requests per 30 minutes, which these buckets do not model; HB2's history
# reads therefore stop at HISTORY_INTRADAY_MAX_REQUESTS (600) in any rolling 30 minutes (counted from the database).
BROKER_RATE_LIMITS: Dict[str, RateLimits] = {
    "upstox": RateLimits(per_second=8.0, burst=10, per_minute=200),
    "zerodha": RateLimits(per_second=2.0, burst=3, per_minute=100),
    "shoonya": RateLimits(per_second=3.0, burst=5, per_minute=120),
    # Part 1B: Kotak Neo states 25 requests / s for quotes (SDK docs, quotes.md) and, in a search extract of its FAQ,
    # up to 10 orders / s; no per-minute or per-day figure is published (docs/design/BROKER_KOTAK_NEO.md). A third of
    # the order figure, as for the others; the SDK's own optional client limiter defaults to 10 / s and 300 / min.
    "kotak_neo": RateLimits(per_second=3.0, burst=5, per_minute=120),
    # Part 1B: Groww limits by type, each type sharing one allowance: Orders 10 / s and 250 / min, Live data 10 / s and
    # 300 / min, Non-trading 20 / s and 500 / min (Groww's API docs as quoted in search extracts and a third-party
    # integration that cites them, read 23 Oct 2026; an older extract said Orders 15 / s - docs/design/BROKER_GROWW.md).
    # One budget for every call of the session: a third of the tightest type (orders: 250 / min).
    "groww": RateLimits(per_second=3.0, burst=5, per_minute=80),
}
DEFAULT_RATE_LIMITS = RateLimits(per_second=2.0, burst=3, per_minute=100)

# TM-27: brokers that also cap calls PER DAY, by call type. Groww (as the terminal-master review recorded from a search
# extract of Groww's rate-limit table; not found on the pages readable on 23 Oct 2026, so kept as the conservative
# figure - GW-3): orders 3,000, live data 5,000, non-trading 3,000 a day; the token endpoint 150 a day (search extract).
# ATP spends at most DAILY_SHARE of each (the rest is headroom for the user's own use of the same key) and, of the
# orders, keeps EXIT_RESERVE_SHARE for exits: a new entry stops before it, an exit, stop, change or cancel is never
# refused by this budget (ADR-0004; the broker's own cap still applies at the broker).
DAILY_CAPS: Dict[str, Dict[str, int]] = {
    "groww": {"orders": 3000, "live_data": 5000, "non_trading": 3000, "auth": 150},
}
DAILY_SHARE = 0.9
EXIT_RESERVE_SHARE = 0.1
ORDER_CALLS = frozenset({"place_order", "modify_order", "cancel_order", "place_stop_loss_order", "exit_position"})
LIVE_DATA_CALLS = frozenset({"get_ltp", "get_quote", "get_quote_for_symbol", "get_ltp_for_symbol", "get_option_chain",
                             "get_historical_data", "get_intraday_candles", "get_quotes_batch"})
AUTH_CALLS = frozenset({"authenticate"})
# Reads an exit depends on (reprice_until_filled, the position monitor's fill and position checks): never refused.
ALWAYS_READ_CALLS = frozenset({"get_order_book", "get_positions"})


def call_kind(method: str) -> str:
    if method in ORDER_CALLS:
        return "orders"
    if method in LIVE_DATA_CALLS:
        return "live_data"
    if method in AUTH_CALLS:
        return "auth"
    return "non_trading"


class DailyCapReached(BrokerAPIError):
    """Today's share of a broker's per-day allowance for this kind of call is used. Nothing was sent."""

    def __init__(self, broker: str, kind: str) -> None:
        words = {"orders": "new orders", "live_data": "price reads", "non_trading": "account reads", "auth": "logins"}
        super().__init__(f"Today's {words.get(kind, kind)} allowance at {broker} is used up; it starts again tomorrow.", 429)
        self.kind = kind


class DailyBudget:
    """Per-day counters by call type for one session (TM-27), reset at midnight IST. In-process, like RateBudget: the
    trading worker holds one per (tenant, broker account)."""

    def __init__(self, broker: str, caps: Dict[str, int], *, share: float = DAILY_SHARE, exit_reserve: float = EXIT_RESERVE_SHARE,
                 today=None) -> None:
        self.broker = broker
        self.caps = {k: max(0, int(v * share)) for k, v in caps.items()}
        self.entry_cap = max(0, int(self.caps.get("orders", 0) - caps.get("orders", 0) * exit_reserve))
        self._today = today or _ist_today
        self._day = self._today()
        self.used: Dict[str, int] = {k: 0 for k in caps}

    def _roll(self) -> None:
        day = self._today()
        if day != self._day:
            self._day = day
            self.used = {k: 0 for k in self.used}

    def remaining(self, kind: str) -> Optional[int]:
        self._roll()
        if kind not in self.caps:
            return None
        return max(0, self.caps[kind] - self.used.get(kind, 0))

    def take(self, method: str, *, entry: bool = False) -> None:
        """Count one call of `method`; refuse (DailyCapReached, nothing sent) a call this budget may refuse: a new entry
        past the entry cap, a price read or an account read past its cap, a login past its cap. Exits, stops, changes,
        cancels and the order-book read are counted and always pass."""
        self._roll()
        kind = call_kind(method)
        if kind not in self.caps:
            return
        used = self.used.get(kind, 0)
        if kind == "orders":
            if entry and used >= self.entry_cap:
                raise DailyCapReached(self.broker, kind)
        elif method not in ALWAYS_READ_CALLS and used >= self.caps[kind]:
            raise DailyCapReached(self.broker, kind)
        self.used[kind] = used + 1

    def add(self, method: str, extra: int) -> None:
        """Count `extra` more broker requests one adapter call made (Groww's order book asks once per segment, a quote
        once per symbol), so the day's count is the broker's, not ATP's. Never refuses: the call already happened."""
        self._roll()
        kind = call_kind(method)
        if kind in self.caps and extra > 0:
            self.used[kind] = self.used.get(kind, 0) + int(extra)


def _ist_today():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).date()


def daily_budget_for(broker_name: str) -> Optional[DailyBudget]:
    caps = DAILY_CAPS.get((broker_name or "").lower())
    return DailyBudget(broker_name, caps) if caps else None


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
        return self.time_until(1.0)

    def time_until(self, tokens: float) -> float:
        """Seconds until the bucket holds `tokens` (capped at its capacity)."""
        self._refill()
        tokens = min(tokens, self.capacity)
        if self.tokens >= tokens:
            return 0.0
        return (tokens - self.tokens) / self.rate

    def take(self) -> None:
        self.tokens -= 1


class RateBudget:
    """Two buckets (per-second burst + per-minute allowance); `acquire()` waits for both."""

    def __init__(self, limits: RateLimits, clock=time.monotonic, sleep=asyncio.sleep, daily: Optional[DailyBudget] = None) -> None:
        self.limits = limits
        self.daily = daily            # TM-27: a per-day budget by call type (Groww), None for brokers without one
        self._second = _TokenBucket(limits.per_second, limits.burst, clock)
        self._minute = _TokenBucket(limits.per_minute / 60.0, limits.per_minute, clock)
        self._lock = asyncio.Lock()
        self._clock = clock
        self._sleep = sleep
        self.calls = 0
        self.waited_seconds = 0.0
        self.spare_calls = 0          # HB2: tokens taken by background readers through `acquire_spare`

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

    def now(self) -> float:
        """This budget's clock (monotonic seconds) - what `acquire_spare`'s deadline is measured in."""
        return self._clock()

    async def acquire_spare(self, reserve: float, *, deadline: Optional[float] = None) -> bool:
        """A token for a BACKGROUND reader (HB2 intraday history) that shares this budget with the trading worker.

        It is taken only while both buckets still hold `reserve` (0..0.95) of their capacity on top of it, so the
        worker's exits always find tokens left; and the lock is never held while waiting, so an exit's `acquire()` is
        never queued behind a background read. Returns False, without a token, when `deadline` (in this budget's clock
        seconds) would pass first."""
        reserve = min(max(float(reserve), 0.0), 0.95)
        while True:
            async with self._lock:
                wait = max(self._second.time_until(1 + reserve * self._second.capacity),
                           self._minute.time_until(1 + reserve * self._minute.capacity))
                if wait <= 0:
                    self._second.take()
                    self._minute.take()
                    self.calls += 1
                    self.spare_calls += 1
                    return True
            if deadline is not None and self._clock() + wait > deadline:
                return False
            await self._sleep(wait)                  # outside the lock: the worker's acquire() goes first


class RateLimitedBroker(BrokerInterface):
    """Transparent wrapper: same interface, every broker call first draws a token from the
    tenant's budget. Order placement/cancellation is *not* exempt on purpose - an exit order
    that provokes a 429 is worse than one delayed by 200ms."""

    def __init__(self, inner: BrokerInterface, budget: RateBudget, ops=None, tenant_id: Optional[int] = None) -> None:
        self.inner = inner
        self.budget = budget
        self.name = inner.name
        self.history_by_date = bool(getattr(inner, "history_by_date", False))   # LD-10 (shared_candles)
        self.daily_days_per_request = int(getattr(inner, "daily_days_per_request", 365))   # UI2-4a2 (daily chunks)
        # Z10-c: whose orders these are, for the Phase 0 PAPER-only lock (app/platform/phase0_lock.py).
        self.tenant_id = tenant_id
        # Part D2: the SEBI orders-per-second throttle (app/execution/ops_throttle.py), when OPS_THROTTLE_ENABLED.
        self.ops = ops

    @property
    def access_token(self) -> Optional[str]:
        return self.inner.access_token

    # Phase G2: this wrapper reports every call's outcome to the broker's circuit breaker, so a
    # caller holding it must not record the same call again (OrderRouter checks this flag).
    records_circuit = True

    async def _call(self, method: str, *args, **kwargs):
        daily = getattr(self.budget, "daily", None)
        if daily is not None:
            from app.platform.phase0_lock import is_entry_order
            entry = method == "place_order" and bool(args) and is_entry_order(args[0])
            daily.take(method, entry=entry)        # refuses before a token is spent or anything is sent
        await self.budget.acquire()
        before = getattr(self.inner, "http_calls", None) if daily is not None else None
        try:
            result = await getattr(self.inner, method)(*args, **kwargs)
        except Exception as exc:
            observe_call(self.name, method, exc)
            raise
        finally:
            if daily is not None and isinstance(before, int):
                daily.add(method, int(getattr(self.inner, "http_calls", before)) - before - 1)
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
    async def place_order(self, order: BrokerOrderRequest) -> BrokerOrderResponse:
        # Z10-c: the broker-order chokepoint of the Phase 0 PAPER-only lock. A new entry is refused before it costs a
        # token or reaches the broker; exits, stops, modifies and cancels always pass (ADR-0004).
        from app.platform import phase0_lock
        if phase0_lock.locked(self.tenant_id) and phase0_lock.is_entry_order(order):
            return phase0_lock.refused_response()
        if self.ops is None:
            return await self._call("place_order", order)
        from app.execution.ops_throttle import is_entry, refused_response
        if is_entry(order):
            if not self.ops.try_entry(order.exchange):
                return refused_response()          # never sent: the router records a refusal, not a failure
        else:
            await self.ops.exit(order.exchange)    # exits wait for a token, never refused (ADR-0004)
        response = await self._ops_call(order.exchange, "place_order", order)
        self.ops.remember(response, order.exchange)
        return response

    async def modify_order(self, order_id, quantity=None, price=None, trigger_price=None, order_type=None):
        if self.ops is None:
            return await self._call("modify_order", order_id, quantity, price, trigger_price, order_type)
        exchange = self.ops.exchange_of(order_id)
        await self.ops.exit(exchange)
        return await self._ops_call(exchange, "modify_order", order_id, quantity, price, trigger_price, order_type)

    async def cancel_order(self, order_id):
        if self.ops is None:
            return await self._call("cancel_order", order_id)
        exchange = self.ops.exchange_of(order_id)
        await self.ops.exit(exchange)
        return await self._ops_call(exchange, "cancel_order", order_id)

    async def _ops_call(self, exchange, method: str, *args):
        try:
            result = await self._call(method, *args)
        except BrokerAPIError as exc:
            if exc.status_code == 429:
                self.ops.rate_limited(exchange)
            raise
        self.ops.accepted(exchange)
        return result
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
        product: str = "MIS", tag: Optional[str] = None, is_option: Optional[bool] = None,
    ) -> BrokerOrderResponse:
        extra = {"is_option": is_option} if is_option is not None else {}
        return await self._call("place_stop_loss_order", symbol, exchange, transaction_type, quantity, trigger_price, product, tag, **extra)

    @property
    def capabilities(self):          # P0.5 / T2: the wrapped broker's matrix, not the interface default
        return self.inner.capabilities

    def stop_order_params(self, symbol: str, transaction_type: OrderSide, trigger_price: float, *, is_option: Optional[bool] = None):
        return self.inner.stop_order_params(symbol, transaction_type, trigger_price, is_option=is_option)
