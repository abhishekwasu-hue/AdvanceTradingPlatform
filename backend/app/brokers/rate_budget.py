"""Per-tenant broker API rate budgets.

Every Indian broker API is rate limited per API key; the published figures (and their sources) are in ONE table,
app/brokers/broker_limits.py (PUBLISHED; docs/design/M71_RATE_LIMITS.md). On a shared
worker one tenant with forty deployments can burn a broker's whole allowance - and, worse, the
429s it provokes land on *its own* exit orders. So each tenant's adapter is wrapped in a
`RateLimitedBroker` that draws from that tenant's own `RateBudget` (a per-second and a per-minute
token bucket) before every call. Tenants never share a budget: their credentials are their own
API keys, so their limits are their own too.

M71 PL-2: on top of these in-process buckets, every call also takes a slot in the broker's PUBLISHED windows for the
credential (app/brokers/broker_limits.py: per second, minute, 30 minutes, hour and day, the safety margin below each,
shared by every process in Redis), from the DATA pool or the ORDER pool. Orders, exits, stops, changes and cancels use an
in-process order bucket of their own too (`RateBudget.acquire_order`), so a queue of data calls never holds an order
back; exits and their reads may use the reserved share and are never refused (ADR-0004). A broker "too many requests"
answer is retried with exponential back-off and jitter (`broker_limits.backoff_delay`) within a bounded total wait.
"""
import asyncio
import contextlib
import contextvars
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.brokers import broker_limits
from app.brokers.base import BrokerInterface
from app.brokers.circuit_breaker import observe_call
from app.brokers.exceptions import BrokerAPIError, BrokerBudgetUsed
from app.brokers.models import BrokerOrderRequest, BrokerOrderResponse
from app.core import config
from app.core.enums import OrderSide


@dataclass(frozen=True)
class RateLimits:
    per_second: float
    burst: int
    per_minute: int


# Conservative: about a third of each broker's published allowance, leaving headroom for the
# tenant's own manual use of the same API key (their broker app, TradingView, ...).
# Upstox also caps a user per 30 minutes, which these buckets do not model: M71 PL-2's shared published windows
# (broker_limits) hold it for every process; HB2's history reads also stop at HISTORY_INTRADAY_MAX_REQUESTS (600) in any
# rolling 30 minutes (counted from the database).
BROKER_RATE_LIMITS: Dict[str, RateLimits] = {
    # M71 PL-2: Upstox's lower published set (25/s, 250/min, 1,000/30 min - broker_limits.UPSTOX_CONSERVATIVE) less the
    # 10 % margin is 22/s and 225/min: the worker's 12/s and 150/min plus the API's half (6/s, 75/min) stay inside it;
    # the 30-minute window is held by the shared published windows (broker_limits).
    "upstox": RateLimits(per_second=12.0, burst=12, per_minute=150),
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
DAILY_SHARE = config.BROKER_DAILY_CAP_SHARE
EXIT_RESERVE_SHARE = config.BROKER_DAILY_ORDER_EXIT_RESERVE_SHARE
ORDER_CALLS = frozenset({"place_order", "modify_order", "cancel_order", "place_stop_loss_order", "exit_position"})
LIVE_DATA_CALLS = frozenset({"get_ltp", "get_quote", "get_quote_for_symbol", "get_ltp_for_symbol", "get_option_chain",
                             "get_historical_data", "get_intraday_candles", "get_quotes_batch"})
AUTH_CALLS = frozenset({"authenticate"})
# Reads an exit depends on (reprice_until_filled, the position monitor's fill and position checks): never refused.
ALWAYS_READ_CALLS = frozenset({"get_order_book", "get_positions"})


# M55 S2 (ADR-0004): price reads made while this is set are for an exit (the worker sets it around its exits phase) and
# may use the price-read reserve (config.DAILY_EXIT_READ_RESERVE_SHARE); every other read stops before it.
_EXIT_READS: contextvars.ContextVar[bool] = contextvars.ContextVar("atp_exit_reads", default=False)


@contextlib.contextmanager
def exit_reads() -> Iterator[None]:
    """Mark the price reads made inside this block as reads for an exit (they may use the exit reserve)."""
    token = _EXIT_READS.set(True)
    try:
        yield
    finally:
        _EXIT_READS.reset(token)


def reading_for_exit() -> bool:
    return _EXIT_READS.get()


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
                 today=None, read_reserve: float | None = None) -> None:
        self.broker = broker
        self.caps = {k: max(0, int(v * share)) for k, v in caps.items()}
        self.entry_cap = max(0, int(self.caps.get("orders", 0) - caps.get("orders", 0) * exit_reserve))
        if read_reserve is None:
            from app.core import config
            read_reserve = config.DAILY_EXIT_READ_RESERVE_SHARE
        # M55 S2: price reads not made for an exit stop here; an exit's price read may go on to the full cap.
        self.read_cap = max(0, int(self.caps.get("live_data", 0) - caps.get("live_data", 0) * read_reserve))
        # M55 S2: caps reached since the owner was last told ({kind: True when an exit's call was refused}).
        self.cap_hits: dict[str, bool] = {}
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
                self._hit(kind, exit_call=False)
                raise DailyCapReached(self.broker, kind)
        elif method not in ALWAYS_READ_CALLS:
            for_exit = kind == "live_data" and reading_for_exit()
            cap = self.read_cap if kind == "live_data" and not for_exit else self.caps[kind]
            if used >= cap:
                self._hit(kind, exit_call=for_exit)
                raise DailyCapReached(self.broker, kind)
        self.used[kind] = used + 1

    def _hit(self, kind: str, *, exit_call: bool) -> None:
        self.cap_hits[kind] = self.cap_hits.get(kind, False) or exit_call

    def pop_cap_hits(self) -> dict[str, bool]:
        """The caps reached since the last call ({kind: an exit's call was refused}); the worker alerts on them."""
        hits, self.cap_hits = self.cap_hits, {}
        return hits

    def add(self, method: str, extra: int) -> None:
        """Count `extra` more broker requests one adapter call made (Groww's order book asks once per segment, a quote
        once per symbol), so the day's count is the broker's, not ATP's. Never refuses: the call already happened."""
        self._roll()
        kind = call_kind(method)
        if kind in self.caps and extra > 0:
            self.used[kind] = self.used.get(kind, 0) + int(extra)


def _count_rate_limited(broker: str, method: str) -> None:
    """The broker_calls metric's "rate_limited" outcome for a data call, without the circuit breaker."""
    try:
        from app.observability.metrics import BROKER_CALLS
        BROKER_CALLS.labels(broker=(broker or "unknown").lower(), method=method, outcome="rate_limited").inc()
    except Exception:  # noqa: BLE001 - a metric never fails a call
        pass


def _is_entry(method: str, args: tuple) -> bool:
    if method != "place_order" or not args:
        return False
    from app.platform.phase0_lock import is_entry_order
    try:
        return bool(is_entry_order(args[0]))
    except AttributeError:                          # not an order request (a test double): not an entry
        return False


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
    """Two buckets (per-second burst + per-minute allowance); `acquire()` waits for both.

    M71 PL-2: order calls (`acquire_order`) have two buckets and a lock of their own (`order_limits`, by default the same
    figures), so data calls waiting here never delay an order, an exit or a cancel."""

    def __init__(self, limits: RateLimits, clock=time.monotonic, sleep=asyncio.sleep, daily: DailyBudget | None = None,
                 order_limits: RateLimits | None = None) -> None:
        self.limits = limits
        self.order_limits = order_limits or limits
        self.daily = daily            # TM-27: a per-day budget by call type (Groww), None for brokers without one
        self._second = _TokenBucket(limits.per_second, limits.burst, clock)
        self._minute = _TokenBucket(limits.per_minute / 60.0, limits.per_minute, clock)
        self._order_second = _TokenBucket(self.order_limits.per_second, self.order_limits.burst, clock)
        self._order_minute = _TokenBucket(self.order_limits.per_minute / 60.0, self.order_limits.per_minute, clock)
        self._order_lock = asyncio.Lock()
        self.order_calls = 0
        self._lock = asyncio.Lock()
        self._clock = clock
        self._sleep = sleep
        self.calls = 0
        self.waited_seconds = 0.0
        self.spare_calls = 0          # HB2: tokens taken by background readers through `acquire_spare`

    async def acquire(self) -> None:
        # M71 PL-2 (review): the lock is held only to check and take, never while sleeping, so a call that must wait
        # (a backfill read) never queues another (an exit's read) behind its sleep.
        while True:
            async with self._lock:
                wait = max(self._second.time_until_token(), self._minute.time_until_token())
                if wait <= 0:
                    self._second.take()
                    self._minute.take()
                    self.calls += 1
                    return
            self.waited_seconds += wait
            await self._sleep(wait)

    async def acquire_order(self) -> None:
        """A token from the order buckets (orders, exits, stops, changes, cancels): never queued behind data calls."""
        while True:
            async with self._order_lock:
                wait = max(self._order_second.time_until_token(), self._order_minute.time_until_token())
                if wait <= 0:
                    self._order_second.take()
                    self._order_minute.take()
                    self.calls += 1
                    self.order_calls += 1
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

    def __init__(self, inner: BrokerInterface, budget: RateBudget, ops=None, tenant_id: int | None = None,
                 credential_id: int | None = None) -> None:
        self.inner = inner
        # M71 PL-2: the credential row whose published windows every call joins (broker_limits, shared in Redis); the
        # adapter carries it when token_lifecycle.build_adapter made it. None: the in-process buckets only.
        if credential_id is None:
            found = getattr(inner, "atp_credential_id", None)
            credential_id = found if isinstance(found, int) and not isinstance(found, bool) else None
        self.credential_id = credential_id
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
        entry = _is_entry(method, args)
        if daily is not None:
            daily.take(method, entry=entry)        # refuses before a token is spent or anything is sent
        kind = broker_limits.endpoint_kind(method)
        pool = broker_limits.pool_of(kind)
        # ADR-0004: an exit, stop, change or cancel, and the reads an exit depends on, may use the reserved share.
        priority = (not entry) if pool == broker_limits.ORDER_POOL else (reading_for_exit() or method in ALWAYS_READ_CALLS)
        before = getattr(self.inner, "http_calls", None)
        sleep = getattr(self.budget, "_sleep", asyncio.sleep)
        attempt, waited = 0, 0.0
        try:
            while True:
                await self._slot(kind, pool, priority)
                try:
                    result = await getattr(self.inner, method)(*args, **kwargs)
                    break
                except Exception as exc:
                    limited = broker_limits.is_rate_limited(exc)
                    if limited and pool == broker_limits.DATA_POOL:
                        _count_rate_limited(self.name, method)   # review: a data 429 never opens the order breaker
                    else:
                        observe_call(self.name, method, exc)
                    if not limited or (pool == broker_limits.ORDER_POOL and self.ops is not None):
                        raise   # Part D2: with the OPS throttle on, its own per-exchange pause handles an order's 429
                    await broker_limits.COUNTER.add(self.name, pool, kind, rate_limited=True)
                    # M71 PL-2: a broker "too many requests" is retried after an exponential, jittered pause, within a
                    # bounded total wait; then one plain message (never the broker's raw text).
                    delay = broker_limits.backoff_delay(attempt)
                    bound = (config.BROKER_429_ORDER_MAX_WAIT_SECONDS if pool == broker_limits.ORDER_POOL
                             else config.BROKER_429_DATA_MAX_WAIT_SECONDS)
                    if waited + delay > bound:
                        raise BrokerAPIError(broker_limits.BUSY_MESSAGE, 429) from exc
                    await sleep(delay)
                    waited += delay
                    attempt += 1
        finally:
            if isinstance(before, int):
                extra = int(getattr(self.inner, "http_calls", before)) - before - (attempt + 1)
                if extra > 0:
                    await broker_limits.COUNTER.add(self.name, pool, kind, extra)
                if daily is not None:
                    daily.add(method, int(getattr(self.inner, "http_calls", before)) - before - 1)
        observe_call(self.name, method, None)
        return result

    async def _slot(self, kind: str, pool: str, priority: bool) -> None:
        """One broker request's place: the credential's shared published windows (when known), then this process's
        bucket of the pool (orders apart from data), then the day's count."""
        if self.credential_id is not None:
            if not await broker_limits.LIMITER.acquire(self.credential_id, self.name, kind, priority=priority):
                raise BrokerBudgetUsed(broker_limits.BUSY_MESSAGE, 429)   # never for a priority call; nothing sent
        acquire_order = getattr(self.budget, "acquire_order", None)
        if pool == broker_limits.ORDER_POOL and acquire_order is not None:
            await acquire_order()
        else:
            await self.budget.acquire()
        await broker_limits.COUNTER.add(self.name, pool, kind)

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
    async def get_basket_margin(self, orders): return await self._call("get_basket_margin", orders)
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
