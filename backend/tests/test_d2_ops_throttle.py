"""Part D2 (rule IN-SEBI.ops.throttle): orders per second per client and exchange, exits first.

- the rate comes from the rule-set (or OPS_PER_SECOND), never from code;
- entries beyond the rate are refused before the broker (a REJECTED response, reason ops_throttle) - never queued;
- exits, stops, modifies and cancels wait for a token and are never refused (ADR-0004), and while an exit waits no
  entry may take a token;
- each exchange has its own bucket; a broker 429 pauses that exchange for the back-off step, a success resets it;
- with OPS_THROTTLE_ENABLED off (the default) the worker's adapters carry no throttle.
"""
import asyncio
from typing import List

import pytest

from app.brokers.exceptions import BrokerAPIError
from app.brokers.models import BrokerOrderRequest, BrokerOrderResponse
from app.brokers.rate_budget import RateBudget, RateLimitedBroker, RateLimits
from app.compliance import rules
from app.core.enums import OrderSide
from app.execution.ops_throttle import OpsThrottle, is_entry
from app.execution.tagging import LEG_ENTRY, LEG_EXIT, LEG_STOP, build_order_tag


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _throttle(rate=2.0, backoff=(1.0, 2.0), critical_after=3):
    clock = _Clock()
    slept: List[float] = []

    async def sleep(seconds):
        slept.append(seconds)
        clock.now += seconds
    return OpsThrottle(rate, backoff=list(backoff), critical_after=critical_after, clock=clock, sleep=sleep), clock, slept


class _Venue:
    """Counts what reached the broker; `fail_429` makes the next order call answer 429."""
    name = "fake"
    access_token = "tok"

    def __init__(self):
        self.sent: List[tuple] = []
        self.fail_429 = 0
        self.n = 0

    async def place_order(self, order):
        if self.fail_429:
            self.fail_429 -= 1
            raise BrokerAPIError("too many requests", status_code=429)
        self.n += 1
        self.sent.append(("place", order.exchange, order.tag))
        return BrokerOrderResponse(order_id=f"o{self.n}", status="OPEN")

    async def modify_order(self, order_id, quantity=None, price=None, trigger_price=None, order_type=None):
        self.sent.append(("modify", order_id))
        return BrokerOrderResponse(order_id=order_id, status="OPEN")

    async def cancel_order(self, order_id):
        self.sent.append(("cancel", order_id))
        return BrokerOrderResponse(order_id=order_id, status="CANCELLED")


def _wrapped(ops):
    budget = RateBudget(RateLimits(1000, 1000, 100000))
    venue = _Venue()
    return RateLimitedBroker(venue, budget, ops=ops), venue  # type: ignore[arg-type]


def _order(leg, exchange="NSE"):
    return BrokerOrderRequest(symbol="RELIANCE", exchange=exchange, transaction_type=OrderSide.BUY, quantity=1,
                              tag=build_order_tag(strategy_id="ema_rsi", leg=leg, algo_id="ALGO1"))


def test_the_rate_comes_from_the_rule_set():
    rs = rules.load()
    assert OpsThrottle().rate == float(rs.param("IN-SEBI.ops.throttle", "ops_per_second"))
    assert OpsThrottle(25).rate == 25.0                                   # OPS_PER_SECOND override
    assert is_entry(_order(LEG_ENTRY)) and not is_entry(_order(LEG_EXIT)) and not is_entry(_order(LEG_STOP))
    assert not is_entry(BrokerOrderRequest(symbol="X", transaction_type=OrderSide.SELL, quantity=1))   # untagged = exit lane


def test_entries_beyond_the_rate_are_refused_before_the_broker_and_exits_are_never_refused():
    ops, clock, slept = _throttle(rate=2.0)
    broker, venue = _wrapped(ops)

    async def go():
        out = [await broker.place_order(_order(LEG_ENTRY)) for _ in range(3)]
        exit_ = await broker.place_order(_order(LEG_EXIT))
        return out, exit_
    entries, exit_ = asyncio.run(go())
    assert [r.status for r in entries] == ["OPEN", "OPEN", "REJECTED"]
    assert "ops_throttle" in entries[2].message and len([s for s in venue.sent if s[2].endswith("ENT")]) == 2
    assert exit_.status == "OPEN" and slept and sum(slept) == pytest.approx(0.5)   # the exit waited for the next token


def test_while_an_exit_waits_no_entry_takes_a_token():
    ops, clock, slept = _throttle(rate=1.0)
    broker, venue = _wrapped(ops)

    async def go():
        assert (await broker.place_order(_order(LEG_ENTRY))).status == "OPEN"     # the second's only token
        bucket = ops.bucket("NSE")
        bucket.exits_waiting += 1                                                  # an exit is queued
        clock.now += 5                                                             # tokens are back...
        refused = await broker.place_order(_order(LEG_ENTRY))                      # ...but the exit goes first
        bucket.exits_waiting -= 1
        stop = await broker.place_order(_order(LEG_STOP))
        modify = await broker.modify_order(stop.order_id, trigger_price=97.0)
        cancel = await broker.cancel_order(stop.order_id)
        return refused, stop, modify, cancel
    refused, stop, modify, cancel = asyncio.run(go())
    assert refused.status == "REJECTED" and stop.status == "OPEN"
    assert [s[0] for s in venue.sent][-2:] == ["modify", "cancel"]               # modify / cancel count in the exit lane
    assert ops.exchange_of(stop.order_id) == "NSE"


def test_each_exchange_has_its_own_bucket():
    ops, _, _ = _throttle(rate=1.0)
    broker, _ = _wrapped(ops)

    async def go():
        return [(await broker.place_order(_order(LEG_ENTRY, ex))).status for ex in ("NSE", "NFO", "NSE", "MCX")]
    assert asyncio.run(go()) == ["OPEN", "OPEN", "REJECTED", "OPEN"]


def test_a_broker_429_pauses_that_exchange_and_a_success_resets_the_back_off():
    from app.observability import metrics
    ops, clock, slept = _throttle(rate=10.0, backoff=(1.0, 2.0), critical_after=2)
    broker, venue = _wrapped(ops)
    before = metrics.OPS_429.labels(severity="critical")._value.get()

    async def go():
        venue.fail_429 = 2
        for _ in range(2):
            with pytest.raises(BrokerAPIError):
                await broker.place_order(_order(LEG_EXIT))
        refused = await broker.place_order(_order(LEG_ENTRY))           # paused: entries refused...
        exit_ = await broker.place_order(_order(LEG_EXIT))              # ...exits wait out the pause, then go
        return refused, exit_
    refused, exit_ = asyncio.run(go())
    assert refused.status == "REJECTED" and exit_.status == "OPEN"
    assert sum(slept) == pytest.approx(3.0)                              # 1 s after the first 429 + 2 s after the second
    assert metrics.OPS_429.labels(severity="critical")._value.get() == before + 1
    assert ops.bucket("NSE").backoff_step == 0 and ops.consecutive_429 == 0


def test_without_the_flag_the_worker_adds_no_throttle(monkeypatch):
    from app.core import config
    from app.workers import trading_worker as tw
    from tests.test_auth_api import _session_factory
    worker = tw.TradingWorker(_session_factory, cycle_seconds=60)
    monkeypatch.setattr(config, "OPS_THROTTLE_ENABLED", False)
    assert worker._rate_limited(1, "upstox@primary", _Venue()).ops is None
    monkeypatch.setattr(config, "OPS_THROTTLE_ENABLED", True)
    first = worker._rate_limited(1, "upstox@primary", _Venue()).ops
    assert first is not None and worker._rate_limited(1, "upstox@primary", _Venue()).ops is first   # kept across cycles
    assert worker._rate_limited(2, "upstox@primary", _Venue()).ops is not first                     # per tenant
