"""M71 PL-2 / PL-6: published broker limits, every window, data / order pools, the exit reserve, 429 back-off, the
shared Redis windows across processes and the per-day call counters (app/brokers/broker_limits.py)."""
import asyncio
import os
import random
from urllib.parse import urlparse

import httpx
import pytest

from app.brokers import broker_limits as bl
from app.brokers.exceptions import BrokerAPIError, BrokerBudgetUsed
from app.brokers.rate_budget import RateBudget, RateLimitedBroker, RateLimits, exit_reads
from app.core import config

SUPPORTED = ("upstox", "zerodha", "angel_one", "dhan", "fyers", "groww", "kotak_neo", "shoonya", "coindcx")


def run(coro):
    return asyncio.run(coro)


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds


def local_limiter(clock: Clock, margin: float | None = None) -> bl.BrokerLimiter:
    return bl.BrokerLimiter(client_factory=lambda: None, clock=clock, sleep=clock.sleep, margin=margin)


async def burst(limiter, cred, broker, kind, n, priority=False):
    got = 0
    for _ in range(n):
        got += (await limiter.try_acquire(cred, broker, kind, priority=priority)) == 0
    return got


# --- the table ------------------------------------------------------------------------------------------------------------
def test_every_supported_broker_has_published_or_documented_limits_for_both_pools():
    for broker in SUPPORTED:
        spec = bl.spec_for(broker)
        assert spec.broker == broker and spec.source and spec.status, broker
        assert {b.pool for b in spec.buckets} == {bl.DATA_POOL, bl.ORDER_POOL}, broker
        for kind in bl.KINDS:
            buckets = spec.buckets_for(kind)
            assert buckets and all(b.pool == bl.pool_of(kind) for b in buckets), (broker, kind)
    assert bl.spec_for("some-new-broker") is bl.DEFAULT_SPEC


def test_upstox_defaults_to_the_lower_set_until_confirmed_and_r33_is_a_setting(monkeypatch):
    data = {b.name: dict(b.windows) for b in bl.spec_for("upstox").buckets}
    assert data["standard"] == {1: 25, 60: 250, 1800: 1000}
    assert data["orders"] == {1: 10, 60: 250, 1800: 1000}
    monkeypatch.setattr(config, "BROKER_UPSTOX_LIMIT_SET", "r33")
    r33 = {b.name: dict(b.windows) for b in bl.spec_for("upstox").buckets}
    assert r33["standard"] == {1: 50, 60: 500, 1800: 2000} and r33["orders"] == {1: 10, 60: 500, 1800: 2000}


def test_kite_quote_history_other_and_orders_are_separate_buckets():
    kite = {b.name: (b.pool, dict(b.windows)) for b in bl.spec_for("zerodha").buckets}
    assert kite == {"quote": ("data", {1: 1}), "history": ("data", {1: 3}), "other": ("data", {1: 10}),
                    "orders": ("orders", {1: 10, 60: 200, 86400: 3000})}


def test_the_safety_margin_is_a_setting(monkeypatch):
    assert bl.effective(25) == 22 and bl.effective(1000) == 900 and bl.effective(1) == 1
    monkeypatch.setattr(config, "BROKER_BUDGET_SAFETY_MARGIN", 0.2)
    assert bl.effective(25) == 20 and bl.effective(250) == 200
    clock = Clock()
    assert run(burst(local_limiter(clock), 1, "upstox", "history", 40, priority=True)) == 20


# --- every window ---------------------------------------------------------------------------------------------------------
def test_every_window_is_honoured_with_a_fake_clock():
    """Upstox data (lower set, 10 % margin): 22 a second, 225 a minute, 900 in 30 minutes - for priority calls; an
    ordinary call leaves the 10 % reserve (19 / 203 / 810)."""
    clock = Clock()
    lim = local_limiter(clock)

    async def go():
        assert await burst(lim, 7, "upstox", "history", 30, priority=True) == 22          # per second
        clock.t += 1.0
        assert await burst(lim, 7, "upstox", "history", 30, priority=True) == 22
        total = 44
        while total < 300 and clock.t < 1_800_000_000.0 + 59:                              # per minute
            clock.t += 1.0
            total += await burst(lim, 7, "upstox", "history", 30, priority=True)
        assert total == 225
        wait = await lim.try_acquire(7, "upstox", "history", priority=True)
        assert wait is not None and 0 < wait <= 60
        n = 225
        for _ in range(10):                                                                # per 30 minutes
            clock.t += 50.0
            for _ in range(12):                                     # 12 busy seconds a minute: 264 tries > 225
                n += await burst(lim, 7, "upstox", "history", 22, priority=True)
                clock.t += 1.0
        assert n == 900                                             # 225 + 3 x 225, then the 30 minutes are used
        clock.t += 60.0
        assert await burst(lim, 7, "upstox", "history", 5, priority=True) == 0
    run(go())


def test_the_hour_and_day_windows(monkeypatch):
    """Dhan orders (10/s, 250/min, 1,000/h, 7,000/day), margin and reserve 0 here: the minute, then the hour, then the
    day stop the count."""
    monkeypatch.setattr(config, "BROKER_ORDER_EXIT_RESERVE_SHARE", 0.0)
    clock = Clock()
    lim = local_limiter(clock, margin=0.0)

    async def hour():
        per_minute = []
        for _ in range(5):
            n = 0
            for _ in range(30):                                  # 30 seconds of 20 tries a second
                n += await burst(lim, 9, "dhan", "orders", 20)
                clock.t += 1.0
            per_minute.append(n)
            clock.t += 31.0
        return per_minute
    assert run(hour()) == [250, 250, 250, 250, 0]

    clock2 = Clock()
    lim2 = local_limiter(clock2, margin=0.0)

    async def day():
        n = 0
        for _ in range(10):                                      # ten hours, 1,000 each at most
            for _ in range(5):
                for _ in range(30):
                    n += await burst(lim2, 9, "dhan", "orders", 20)
                    clock2.t += 1.0
                clock2.t += 31.0
            clock2.t += 3600.0
        return n
    assert run(day()) == 7000


def test_an_ordinary_call_waits_for_a_free_slot_within_its_bound():
    clock = Clock()
    lim = local_limiter(clock)

    async def go():
        assert await burst(lim, 3, "zerodha", "history", 5) == 2                             # Kite history 3/s -> 2
        before = clock.t
        assert await lim.acquire(3, "zerodha", "history", max_wait=5) is True                # waits for the next slot
        assert 0 < clock.t - before <= 1.0
        for _ in range(300):
            await lim.acquire(3, "zerodha", "history", max_wait=5)
        await burst(lim, 3, "zerodha", "history", 5)
        assert await lim.acquire(3, "zerodha", "history", max_wait=0) is False               # no wait allowed: refused
    run(go())


# --- pools and the exit reserve -------------------------------------------------------------------------------------------
def test_data_saturation_never_touches_the_order_pool():
    clock = Clock()
    lim = local_limiter(clock)

    async def go():
        for kind in ("history", "quote", "account"):
            await burst(lim, 5, "upstox", kind, 500, priority=True)
        assert await lim.try_acquire(5, "upstox", "history", priority=True) > 0              # data pool is full
        assert await lim.try_acquire(5, "upstox", "orders") == 0                             # an entry goes at once
        assert await lim.try_acquire(5, "upstox", "orders", priority=True) == 0              # an exit too
    run(go())


def test_entries_stop_before_the_exit_reserve_and_exits_are_never_refused():
    clock = Clock()
    lim = local_limiter(clock)

    async def go():
        entries = await burst(lim, 6, "upstox", "orders", 50)                 # 10/s -> 9, 20 % kept: 8 entries
        assert entries == 8
        assert await lim.try_acquire(6, "upstox", "orders", priority=True) == 0              # the reserved slot: an exit
        assert await lim.try_acquire(6, "upstox", "orders", priority=True) > 0               # the second is full now
        started = clock.t
        assert await lim.acquire(6, "upstox", "orders", priority=True) is True                # waits for the next slot
        assert clock.t - started <= config.BROKER_EXIT_MAX_WAIT_SECONDS
        # every window full for a long time (the day): an exit is still sent, after at most the exit wait
        for _ in range(5000):
            await lim.try_acquire(6, "zerodha", "orders", priority=True)
            clock.t += 0.2
        started = clock.t
        assert await lim.acquire(6, "zerodha", "orders", priority=True) is True
        assert clock.t - started <= config.BROKER_EXIT_MAX_WAIT_SECONDS + 1e-6 and lim.forced >= 1
        assert await lim.acquire(6, "zerodha", "orders", max_wait=5) is False                 # an entry is refused
    run(go())


def test_an_exits_reads_use_the_data_reserve_under_saturation():
    clock = Clock()
    lim = local_limiter(clock)

    async def go():
        assert await burst(lim, 8, "upstox", "quote", 100) == 20                 # 22 less the 10 % reserve (2)
        assert await lim.try_acquire(8, "upstox", "quote") > 0                   # a chart's read waits
        assert await burst(lim, 8, "upstox", "quote", 5, priority=True) == 2     # an exit's reads: the reserve
    run(go())


# --- the wrapper: pools, priority, 429 back-off, counters ------------------------------------------------------------------
class _Order:
    def __init__(self, intent):
        self.intent = intent
        self.exchange = "NSE"


class _Broker:
    name = "upstox"
    access_token = "t"

    def __init__(self, fail_429: int = 0, credential: int | None = 77):
        self.fail_429 = fail_429
        self.calls: list[str] = []
        if credential is not None:
            self.atp_credential_id = credential

    async def _do(self, what):
        self.calls.append(what)
        if self.fail_429 > 0:
            self.fail_429 -= 1
            raise BrokerAPIError("Too Many Requests Sent", 429)
        return what

    async def get_historical_data(self, *a):
        return await self._do("history")

    async def get_ltp(self, *a):
        return await self._do("ltp")

    async def place_order(self, order):
        return await self._do("order")

    async def get_positions(self):
        return await self._do("positions")


@pytest.fixture
def fresh(monkeypatch):
    clock = Clock()
    lim = local_limiter(clock)
    counter = bl.CallCounter("t", client_factory=lambda: None, clock=clock)
    monkeypatch.setattr(bl, "LIMITER", lim)
    monkeypatch.setattr(bl, "COUNTER", counter)
    return clock, lim, counter


def _wrapped(inner, clock):
    budget = RateBudget(RateLimits(1000.0, 1000, 100000), clock=clock, sleep=clock.sleep)
    return RateLimitedBroker(inner, budget), budget  # type: ignore[arg-type]


def test_the_wrapper_finds_the_credential_and_draws_the_right_pool(fresh):
    clock, lim, counter = fresh
    inner = _Broker()
    wrapped, budget = _wrapped(inner, clock)
    assert wrapped.credential_id == 77 and RateLimitedBroker(_Broker(credential=None), budget).credential_id is None

    async def go():
        for _ in range(20):
            await wrapped.get_historical_data("X", "NSE", "1d", None, None)
        with pytest.raises(BrokerBudgetUsed):
            config_wait = config.BROKER_DATA_MAX_WAIT_SECONDS
            try:
                config.BROKER_DATA_MAX_WAIT_SECONDS = 0.0
                await wrapped.get_historical_data("X", "NSE", "1d", None, None)      # the data pool is used up
            finally:
                config.BROKER_DATA_MAX_WAIT_SECONDS = config_wait
        assert await wrapped.place_order(_Order("ENTRY")) == "order"                    # the order pool is apart
        assert await wrapped.get_positions() == "positions"                              # an exit's read: the reserve
        with exit_reads():
            assert await wrapped.get_ltp(["X"]) == "ltp"
        today = await counter.today()
        assert today["brokers"]["upstox"] == {"data": {"history": 20, "account": 1, "quote": 1}, "orders": {"orders": 1}}
        assert budget.order_calls == 1
    run(go())


def test_a_429_is_retried_with_back_off_and_never_reaches_the_order_breaker(fresh, monkeypatch):
    from app.brokers import circuit_breaker
    clock, lim, counter = fresh
    circuit_breaker.reset_all()
    inner = _Broker(fail_429=3)
    wrapped, _ = _wrapped(inner, clock)
    start = clock.t
    assert run(wrapped.get_historical_data("X", "NSE", "1d", None, None)) == "history"
    assert inner.calls == ["history"] * 4
    waited = clock.t - start
    base = config.BROKER_429_BASE_SECONDS
    assert base * (1 + 2 + 4) / 2 <= waited <= base * (1 + 2 + 4) + 1e-9
    assert circuit_breaker.breaker_for("upstox").snapshot()["failures_in_window"] == 0  # data 429s kept out
    today = run(counter.today())
    assert today["rate_limited"] == 3 and today["brokers"]["upstox"]["data"]["history"] == 4
    circuit_breaker.reset_all()


def test_after_the_bounded_wait_the_caller_gets_one_plain_message(fresh, monkeypatch):
    clock, _, _ = fresh
    monkeypatch.setattr(config, "BROKER_429_DATA_MAX_WAIT_SECONDS", 3.0)
    wrapped, _ = _wrapped(_Broker(fail_429=100), clock)
    with pytest.raises(BrokerAPIError) as err:
        run(wrapped.get_ltp(["X"]))
    assert str(err.value) == bl.BUSY_MESSAGE and err.value.status_code == 429


def test_an_exit_order_is_retried_after_a_429(fresh):
    clock, _, _ = fresh
    inner = _Broker(fail_429=2)
    wrapped, _ = _wrapped(inner, clock)
    assert run(wrapped.place_order(_Order("EXIT"))) == "order" and inner.calls == ["order"] * 3


def test_backoff_is_exponential_with_bounded_jitter():
    rng = random.Random(7)
    for attempt in range(12):
        step = min(8.0, 0.5 * 2 ** attempt)
        delays = [bl.backoff_delay(attempt, rng=rng, base=0.5, cap=8.0) for _ in range(200)]
        assert all(step / 2 <= d <= step for d in delays)
        assert max(delays) - min(delays) > step / 4                                   # really jittered
    assert bl.is_rate_limited(BrokerAPIError("x", 429))
    assert bl.is_rate_limited(BrokerAPIError("Too many requests", 400))
    assert not bl.is_rate_limited(BrokerBudgetUsed("ours", 429))
    assert not bl.is_rate_limited(BrokerAPIError("bad symbol", 400))


def test_the_order_bucket_is_never_held_behind_a_waiting_data_call():
    clock = Clock()
    gate = asyncio.Event()

    async def slow_sleep(seconds):
        await gate.wait()
        clock.t += seconds

    budget = RateBudget(RateLimits(1.0, 1, 60), clock=clock, sleep=slow_sleep)

    async def go():
        await budget.acquire()
        waiting = asyncio.create_task(budget.acquire())                 # a data call that must sleep
        await asyncio.sleep(0)
        await asyncio.wait_for(budget.acquire_order(), 0.5)              # an order does not wait for it
        assert not waiting.done()
        gate.set()
        await waiting
    run(go())


# --- counters -------------------------------------------------------------------------------------------------------------
def test_http_requests_are_counted_per_broker_pool_and_kind(monkeypatch):
    from app.brokers.shared_http import count_requests, http_kind
    counter = bl.CallCounter("h", client_factory=lambda: None)
    monkeypatch.setattr(bl, "HTTP_COUNTER", counter)
    assert http_kind("GET", "/v2/historical-candle/x") == "history" and http_kind("POST", "/v2/order/place") == "orders"
    assert http_kind("GET", "/v2/order/retrieve-all") == "account" and http_kind("GET", "/v2/market-quote/ltp") == "quote"

    def handler(request):
        return httpx.Response(429 if "ltp" in request.url.path else 200, json={})

    async def go():
        client = count_requests(httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://b.test"), "Upstox")
        count_requests(client, "upstox")                                    # hooked once only
        await client.get("/v2/historical-candle/a")
        await client.get("/v2/market-quote/ltp")
        await client.post("/v2/order/place")
        await client.aclose()
        return await counter.today()
    today = run(go())
    assert today["brokers"] == {"upstox": {"data": {"history": 1, "quote": 1}, "orders": {"orders": 1}}}
    assert today["total"] == 3 and today["rate_limited"] == 1


def test_purpose_is_counted_apart_from_the_totals():
    counter = bl.CallCounter("p", client_factory=lambda: None)

    async def go():
        await counter.add("dhan", "data", "history", 2, purpose="screener")
        await counter.add("dhan", "data", "quote", purpose="chart")
        return await counter.today()
    today = run(go())
    assert today["total"] == 3 and today["purposes"] == {"dhan": {"screener": 2, "chart": 1}}


# --- one Redis, many processes ----------------------------------------------------------------------------------------------
def _redis_url() -> str:
    url = os.environ.get("REDIS_URL") or "redis://localhost:6379/0"
    parsed = urlparse(url)
    return f"redis://{parsed.hostname or 'localhost'}:{parsed.port or 6379}/15"


async def _redis():
    import redis.asyncio as aioredis
    client = aioredis.from_url(_redis_url(), decode_responses=True, socket_connect_timeout=0.5)
    try:
        await client.ping()
    except Exception:  # noqa: BLE001
        await client.aclose()
        return None
    return client


def _need_redis():
    async def probe():
        client = await _redis()
        if client is not None:
            await client.aclose()
        return client is not None
    if not run(probe()):
        pytest.skip("Redis not reachable")


def test_two_limiter_processes_share_one_redis_window():
    _need_redis()
    clock = Clock()

    async def go():
        client = await _redis()
        await client.delete(*[k for k, _, _ in bl.shared_windows(4242, "zerodha", "history")] or ["x"])
        a = bl.BrokerLimiter(client_factory=lambda: client, clock=clock, sleep=clock.sleep)
        b = bl.BrokerLimiter(client_factory=lambda: client, clock=clock, sleep=clock.sleep)
        got = [await a.try_acquire(4242, "zerodha", "history"), await b.try_acquire(4242, "zerodha", "history"),
               await a.try_acquire(4242, "zerodha", "history")]
        other = await b.try_acquire(4243, "zerodha", "history")                       # another credential
        clock.t += 1.0
        later = await b.try_acquire(4242, "zerodha", "history")
        priority = await a.try_acquire(4242, "zerodha", "history", priority=True)
        await client.delete(*[k for c in (4242, 4243) for k, _, _ in bl.shared_windows(c, "zerodha", "history")])
        await client.aclose()
        return got, other, later, priority
    got, other, later, priority = run(go())
    assert got[:2] == [0.0, 0.0] and got[2] > 0                                         # 3/s -> 2, shared by both
    assert other == 0.0 and later == 0.0 and priority == 0.0


def test_the_api_budget_counts_in_the_workers_published_windows(monkeypatch):
    """One credential: the API's chart reads and the worker's reads fill the SAME published window in Redis."""
    _need_redis()
    import app.cache.client as cache_client
    from app.core import rate_limit
    from app.market_data import api_budget

    async def go():
        client = await _redis()
        keys = [k for k, _, _ in bl.shared_windows(5151, "zerodha", "history")]
        await client.delete(*keys, "ab:{5151}:tb", "ab:{5151}:60")
        monkeypatch.setattr(rate_limit, "redis_backed", lambda: True)
        monkeypatch.setattr(cache_client, "_get_client", lambda: client)
        api_budget.reset()
        api = await api_budget.try_take(5151, "zerodha", "history")                       # the API: one call
        worker = bl.BrokerLimiter(client_factory=lambda: client)                         # the worker, Redis clock
        first = await worker.try_acquire(5151, "zerodha", "history")
        second = await worker.try_acquire(5151, "zerodha", "history")                    # 2 a second in all: full
        size = await client.zcard(keys[0])
        await client.delete(*keys, "ab:{5151}:tb", "ab:{5151}:60")
        await client.aclose()
        return api, first, second, size
    api, first, second, size = run(go())
    assert api is True and first == 0.0 and second > 0 and size == 2
    api_budget.reset()


def test_redis_down_keeps_orders_and_exits_flowing():
    clock = Clock()

    def down():
        raise ConnectionError("redis down")
    lim = bl.BrokerLimiter(client_factory=down, clock=clock, sleep=clock.sleep)

    async def go():
        assert await lim.acquire(1, "upstox", "orders", priority=True) is True
        assert await lim.acquire(1, "upstox", "orders") is True
        assert await lim.try_acquire(1, "upstox", "history", local_fallback=False) is None    # the API: not counted
    run(go())


def test_calls_today_reports_both_counters_and_the_eviction_policy(monkeypatch):
    monkeypatch.setattr(bl, "HTTP_COUNTER", bl.CallCounter("x1", client_factory=lambda: None))
    monkeypatch.setattr(bl, "COUNTER", bl.CallCounter("x2", client_factory=lambda: None))
    out = run(bl.calls_today())
    assert set(out) == {"day", "http", "budgeted", "redis_eviction_policy"} and out["http"]["total"] == 0
    assert all(row["windows"] for row in bl.limits_table())


def test_the_redis_script_keeps_the_exit_reserve_and_reports_the_wait():
    _need_redis()
    clock = Clock()

    async def go():
        client = await _redis()
        keys = [k for k, _, _ in bl.shared_windows(6262, "upstox", "orders")]
        await client.delete(*keys)
        lim = bl.BrokerLimiter(client_factory=lambda: client, clock=clock, sleep=clock.sleep)
        entries = await burst(lim, 6262, "upstox", "orders", 20)
        exit_now = await lim.try_acquire(6262, "upstox", "orders", priority=True)
        full = await lim.try_acquire(6262, "upstox", "orders", priority=True)
        clock.t += 0.5
        sent = await lim.acquire(6262, "upstox", "orders", priority=True)               # waits ~0.5 s, then goes
        size = await client.zcard(keys[0])
        await client.delete(*keys)
        await client.aclose()
        return entries, exit_now, full, sent, size
    entries, exit_now, full, sent, size = run(go())
    assert entries == 8 and exit_now == 0.0 and 0.9 <= full <= 1.0 and sent is True and size >= 1


def test_the_budgeted_data_adapter_factory_retries_a_429_and_counts_by_purpose(monkeypatch):
    import types

    from app.market_data import api_budget
    counter = bl.CallCounter("f", client_factory=lambda: None)
    monkeypatch.setattr(bl, "COUNTER", counter)
    monkeypatch.setattr(bl, "backoff_delay", lambda attempt, **k: 0.01)
    grants = []

    async def attempt(record_id, broker, kind="quote"):
        grants.append(kind)
        await counter.add(broker, "data", kind, purpose=api_budget._PURPOSE.get())
        return 1
    monkeypatch.setattr(api_budget, "_attempt", attempt)
    inner = _Broker(fail_429=2)
    record = types.SimpleNamespace(id=31, broker_name="upstox")
    adapter = api_budget.budgeted_data_adapter(record, "screener", adapter=inner)
    assert run(adapter.get_historical_data("X", "NSE", "1d", None, None)) == "history"
    assert inner.calls == ["history"] * 3 and grants == ["history"] * 3                 # each retry took a token again
    today = run(counter.today())
    assert today["purposes"] == {"upstox": {"screener": 3}} and today["rate_limited"] == 2

    always = api_budget.budgeted_data_adapter(record, "chart", adapter=_Broker(fail_429=100), wait_seconds=0.05)
    with pytest.raises(api_budget.BudgetUsed):
        run(always.get_ltp(["X"]))


def test_a_data_read_waits_for_a_full_window_within_its_bound_then_gets_the_plain_gap(monkeypatch):
    from app.market_data import api_budget
    verdicts = [-2, -2, 1]

    async def attempt(record_id, broker, kind="quote"):
        return verdicts.pop(0) if verdicts else -2
    monkeypatch.setattr(api_budget, "_attempt", attempt)
    monkeypatch.setattr(api_budget, "WINDOW_RETRY_SECONDS", 0.01)
    inner = _Broker()
    adapter = api_budget.BudgetedAdapter(inner, 1, "upstox", wait_seconds=1.0)
    assert run(adapter.get_historical_data("X", "NSE", "1d", None, None)) == "history"     # waited for the window
    monkeypatch.setattr(config, "BROKER_DATA_CALL_WAIT_SECONDS", 0.05)
    with pytest.raises(api_budget.BudgetUsed) as err:
        run(adapter.get_historical_data("X", "NSE", "1d", None, None))
    assert str(err.value) == api_budget.BUSY_GAP == "Data is busy; try again in a minute."
    assert api_budget._default_run_wait() >= api_budget.CANDLE_WAIT_SECONDS
