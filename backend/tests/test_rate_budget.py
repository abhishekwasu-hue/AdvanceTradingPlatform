"""Phase B4: per-tenant broker API rate budgets and per-tenant cycle-time fairness."""
import asyncio
from datetime import timedelta
from typing import List

from app.brokers.rate_budget import RateBudget, RateLimitedBroker, RateLimits, limits_for
from app.db.models import StrategyDeploymentRecord


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _budget(per_second=2.0, burst=2, per_minute=100):
    clock = _Clock()
    slept: List[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        clock.now += seconds

    return RateBudget(RateLimits(per_second, burst, per_minute), clock=clock, sleep=fake_sleep), clock, slept


def test_burst_then_throttle_at_the_per_second_rate():
    budget, clock, slept = _budget(per_second=2.0, burst=2)

    async def go():
        await budget.acquire()
        await budget.acquire()   # burst of 2 is free
        await budget.acquire()   # third must wait 0.5s for a token
        await budget.acquire()   # and the fourth another 0.5s
    asyncio.run(go())
    assert budget.calls == 4
    assert [round(s, 3) for s in slept] == [0.5, 0.5]
    assert round(budget.waited_seconds, 3) == 1.0


def test_per_minute_allowance_caps_a_sustained_burst():
    budget, clock, slept = _budget(per_second=100.0, burst=100, per_minute=3)

    async def go():
        for _ in range(4):
            await budget.acquire()
    asyncio.run(go())
    # 3 free within the minute; the 4th waits for the minute bucket to refill one token (20s).
    assert len(slept) == 1 and round(slept[0], 1) == 20.0


def test_tokens_refill_with_time():
    budget, clock, slept = _budget(per_second=1.0, burst=1)

    async def go():
        await budget.acquire()
        clock.now += 5  # plenty of time passes
        await budget.acquire()
    asyncio.run(go())
    assert slept == []


def test_limits_per_broker_with_conservative_default():
    assert limits_for("upstox").per_second > limits_for("zerodha").per_second
    assert limits_for("unknown_broker") == limits_for("dhan")


class _Counting:
    name = "counting"
    _access_token = "tok"

    def __init__(self):
        self.calls = []

    @property
    def access_token(self):
        return self._access_token

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        self.calls.append(("ltp", symbol, exchange))
        return 101.5

    async def get_intraday_candles(self, symbol, exchange, interval):
        self.calls.append(("intraday", symbol))
        return []

    async def place_stop_loss_order(self, symbol, exchange, transaction_type, quantity, trigger_price, product="MIS", tag=None):
        self.calls.append(("sl", symbol, trigger_price, tag))
        from app.brokers.models import BrokerOrderResponse
        return BrokerOrderResponse(order_id="SL", status="OPEN")

    async def get_order_book(self):
        self.calls.append(("book",))
        return []


def test_rate_limited_broker_delegates_every_call_through_the_budget():
    from app.core.enums import OrderSide
    budget, clock, slept = _budget(per_second=100, burst=100)
    inner = _Counting()
    wrapped = RateLimitedBroker(inner, budget)  # type: ignore[arg-type]

    async def go():
        assert await wrapped.get_ltp_for_symbol("RELIANCE") == 101.5
        await wrapped.get_intraday_candles("RELIANCE", "NSE", "1min")
        await wrapped.place_stop_loss_order("RELIANCE", "NSE", OrderSide.SELL, 10, 98.0, tag="t")
        await wrapped.get_order_book()
    asyncio.run(go())
    assert [c[0] for c in inner.calls] == ["ltp", "intraday", "sl", "book"]
    assert inner.calls[2] == ("sl", "RELIANCE", 98.0, "t")
    assert budget.calls == 4
    assert wrapped.name == "counting" and wrapped.access_token == "tok"


def test_worker_wraps_each_tenant_adapter_in_its_own_budget(monkeypatch):
    from tests import test_trading_worker as w

    seen = []

    class _Recorder(w.TradingWorker):
        pass

    t1 = w._tenant("rb-tenant-1@example.com")
    t2 = w._tenant("rb-tenant-2@example.com")
    w._stop_all_deployments()  # _tenant stops earlier ones; keep both of ours active:
    d1 = w._deploy(t1)
    d2 = w._deploy(t2)
    broker = w._FakeBroker(ltp=101.0)
    worker = w._worker(monkeypatch, broker)

    original_factory = worker.market_data_factory
    def factory(adapter):
        seen.append(adapter)
        return original_factory(adapter)
    worker.market_data_factory = factory
    w._force_signal(monkeypatch, w._signal)

    report = w._run(worker.run_cycle(now=w.OPEN_NOW))
    assert report.tenants_processed == 2 and report.signals_executed == 2
    assert all(isinstance(a, RateLimitedBroker) for a in seen)
    budgets = {id(a.budget) for a in seen}
    assert len(budgets) == 2  # one budget per tenant, never shared
    assert sum(a.budget.calls for a in seen) > 0
    assert {w._get(StrategyDeploymentRecord, d).last_error for d in (d1, d2)} == {None}


def test_tenant_time_budget_round_robins_deployments_across_cycles(monkeypatch):
    from tests import test_trading_worker as w

    t = w._tenant("rb-fair@example.com")
    ids = [w._deploy(t, symbol=s) for s in ("A", "B", "C")]
    broker = w._FakeBroker(ltp=101.0)
    worker = w._worker(monkeypatch, broker)
    worker.max_seconds_per_tenant = 0  # at least one per cycle, then the budget is "spent"
    w._force_signal(monkeypatch, w._signal)

    first = w._run(worker.run_cycle(now=w.OPEN_NOW))
    second = w._run(worker.run_cycle(now=w.OPEN_NOW + timedelta(minutes=1)))
    third = w._run(worker.run_cycle(now=w.OPEN_NOW + timedelta(minutes=2)))

    assert [r.deployments_evaluated for r in (first, second, third)] == [1, 1, 1]
    evaluated = [w._get(StrategyDeploymentRecord, i).last_evaluated_at for i in ids]
    assert all(e is not None for e in evaluated)  # every deployment got its turn over three cycles
    assert len(w._trades(t["tenant_id"])) == 3
