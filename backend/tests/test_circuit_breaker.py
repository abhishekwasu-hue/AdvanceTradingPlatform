"""Phase G2: the per-broker circuit breaker - error-rate window, open/half-open/closed, wired
into the rate-limited wrapper and the LIVE entry path, exits never refused."""
import asyncio

import pytest

from app.brokers.circuit_breaker import CircuitBreaker, CircuitState, all_breakers, breaker_for, is_health_failure, reset_all
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError, BrokerOrderRejected
from app.brokers.rate_budget import RateBudget, RateLimitedBroker, RateLimits
from app.core.enums import ExecutionMode
from app.risk_engine.risk_manager import TradingDayState
from tests.test_live_execution import _LiveBroker, _router, _signal


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _breaker(clock, **kw) -> CircuitBreaker:
    return CircuitBreaker("test", window_seconds=60, min_calls=4, failure_ratio=0.5, open_seconds=30, clock=clock, **kw)


@pytest.fixture(autouse=True)
def _fresh_breakers():
    reset_all()
    yield
    reset_all()


def test_health_failures_are_outages_not_business_answers():
    assert is_health_failure(TimeoutError()) and is_health_failure(ConnectionError())
    assert is_health_failure(BrokerAPIError("boom", 503)) and is_health_failure(BrokerAPIError("slow down", 429))
    assert is_health_failure(BrokerAPIError("non-JSON", None))
    assert not is_health_failure(BrokerAPIError("insufficient funds", 400))
    assert not is_health_failure(BrokerAuthenticationError("token expired"))
    assert not is_health_failure(BrokerOrderRejected("margin"))


def test_breaker_opens_on_error_rate_after_min_calls_then_probes_and_recovers():
    clock = _Clock()
    b = _breaker(clock)
    b.record_success(); b.record_failure("a"); b.record_failure("b")
    assert b.state == CircuitState.CLOSED  # only 3 calls: below min_calls
    b.record_failure("c")
    assert b.state == CircuitState.OPEN and b.opened_count == 1  # 3/4 > 0.5, tripped by the failing call
    assert not b.allow_submission() and "paused for another 30s" in b.refusal_reason()

    clock.t += 31
    assert b.state == CircuitState.HALF_OPEN
    assert b.allow_submission() and not b.allow_submission()  # exactly one probe
    b.record_failure("probe died")
    assert b.state == CircuitState.OPEN and b.opened_count == 2

    clock.t += 31
    assert b.allow_submission()
    b.record_success()
    assert b.state == CircuitState.CLOSED and b.counts() == (0, 0)
    snap = b.snapshot()
    assert snap["state"] == "CLOSED" and snap["opened_count"] == 2


def test_old_failures_fall_out_of_the_window():
    clock = _Clock()
    b = _breaker(clock)
    b.record_failure("x"); b.record_failure("y")
    clock.t += 61
    b.record_failure("z"); b.record_success(); b.record_success(); b.record_success()
    assert b.counts() == (1, 4) and b.state == CircuitState.CLOSED


def test_business_rejections_do_not_move_the_breaker():
    b = _breaker(_Clock())
    for _ in range(10):
        b.record(BrokerAPIError("insufficient margin", 400))
    assert b.counts() == (0, 0) and b.state == CircuitState.CLOSED


class _FlakyBroker(_LiveBroker):
    name = "flaky"

    def __init__(self, failures: int):
        super().__init__()
        self.failures = failures

    async def get_ltp(self, symbols):
        if self.failures > 0:
            self.failures -= 1
            raise TimeoutError("broker timeout")
        return {s: 100.0 for s in symbols}


def test_rate_limited_wrapper_feeds_the_breaker_and_the_router_refuses_entries_while_open():
    inner = _FlakyBroker(failures=5)
    wrapped = RateLimitedBroker(inner, RateBudget(RateLimits(per_second=1000, burst=1000, per_minute=100000)))
    breaker = breaker_for("flaky")
    breaker.min_calls = 5
    for _ in range(5):
        with pytest.raises(TimeoutError):
            asyncio.run(wrapped.get_ltp(["X"]))
    assert breaker.state == CircuitState.OPEN and "flaky" in all_breakers()

    router = _router(wrapped)
    result = asyncio.run(router.execute(_signal(), TradingDayState()))
    assert not result.executed and not result.system_failure
    assert any("circuit open" in r.lower() for r in result.reasons) and inner.placed == []

    # Exits are not entries: a cancel (or an exit order) still reaches the broker.
    assert asyncio.run(wrapped.cancel_order("ORD-1")).status == "CANCELLED"

    breaker.reset()
    result = asyncio.run(router.execute(_signal(), TradingDayState()))
    assert result.executed and len(inner.placed) == 2


def test_unwrapped_broker_failures_are_recorded_by_the_router_itself():
    class _Dying(_LiveBroker):
        name = "dying"

        async def place_order(self, order):
            raise ConnectionError("socket closed")

    router = _router(_Dying())
    breaker = breaker_for("dying")
    breaker.min_calls = 2
    for _ in range(2):
        result = asyncio.run(router.execute(_signal(), TradingDayState()))
        assert result.system_failure
    assert breaker.state == CircuitState.OPEN
    result = asyncio.run(router.execute(_signal(), TradingDayState()))
    assert not result.system_failure and any("circuit open" in r.lower() for r in result.reasons)


def test_metrics_expose_broker_calls_and_circuit_state():
    from tests.test_auth_api import client
    breaker_for("upstox")
    body = client.get("/metrics").text
    assert 'atp_broker_circuit_state{broker="upstox"}' in body
    assert "atp_broker_calls_total" in body or "atp_broker_calls_created" in body or "atp_broker_circuit_rejections" in body
