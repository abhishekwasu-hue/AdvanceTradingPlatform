"""M55 S2 (ADR-0004: exits are never blocked).

* A3: an organisation with no usable broker session still gets its exits - a PAPER stop closes on the newest stored
  price; a LIVE position that cannot be exited raises a CRITICAL notification; no entry is evaluated.
* A9: when the replica lock is lost to another holder mid-cycle, the organisations not reached get an exits-only pass;
  no entry. A lock held by a holder with no finished cycle is logged as possibly stale.
* A13: a per-day price-read allowance keeps a reserve for exits; an entry's read stops before it; a cap reached is
  alerted, never skipped silently.
* A7: an exit order the broker refuses is logged at ERROR, raises a CRITICAL notification and is retried by the policy
  in core/config (never when part of it may have filled).
* A8: under approval-only, a target exit is automatic (tests/test_z3_approval_entries.py covers the worker path)."""
import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.brokers.models import BrokerOrderResponse, BrokerOrderStatus
from app.brokers.rate_budget import DailyBudget, DailyCapReached, RateBudget, RateLimitedBroker, RateLimits, exit_reads
from app.core import config
from app.db.models import NotificationRecord, TradeRecord
from app.market_data.stream import Tick, _tick_key, tick_cache
from app.trading import position_monitor as pm
from app.workers import trading_worker as tw
from app.workers.trading_worker import TradingWorker
from tests.test_auth_api import _session_factory
from tests.test_reexit_guard import _Book, _close, _live_trade
from tests.test_trading_worker import OPEN_NOW, _deploy, _FakeBroker, _force_signal, _signal, _tenant, _trades, _worker

pytestmark = pytest.mark.usefixtures("no_open_positions_from_earlier_tests")


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"m55s2-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _open_trade(t, *, symbol: str, mode: str = "PAPER", dep_id=None, stop: float = 98.0) -> int:
    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode=mode, symbol=symbol, strategy_id="ema_rsi_scalper_1m",
                                direction="LONG", entry_time=datetime.now(UTC) - timedelta(minutes=20), entry_price=100.0, quantity=1.0,
                                stop_loss=stop, target1=104.0, exchange="NSE", deployment_id=dep_id)
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


def _notes(tenant_id: int):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(NotificationRecord.tenant_id == tenant_id)
                                              .order_by(NotificationRecord.id)))
    return _run(go())


def _row(trade_id: int) -> TradeRecord:
    async def go():
        async with _session_factory() as session:
            return await session.get(TradeRecord, trade_id)
    return _run(go())


def _expired_broker() -> _FakeBroker:
    broker = _FakeBroker(ltp=97.0)

    async def rejecting_profile():
        from app.brokers.exceptions import BrokerAuthenticationError
        raise BrokerAuthenticationError("Invalid token")
    broker.get_profile = rejecting_profile
    return broker


@pytest.fixture
def stored_tick():
    """A stored stream tick (process-local only, never Redis) for one symbol, cleared afterwards."""
    keys = []

    def put(symbol: str, ltp: float, *, age_seconds: float = 1.0):
        when = datetime.now(UTC) - timedelta(seconds=age_seconds)
        key = _tick_key("upstox", "NSE", symbol)
        tick_cache._local[key] = Tick(broker="upstox", exchange="NSE", symbol=symbol, ltp=ltp, exchange_ts=when, received_at=when)
        keys.append(key)
    yield put
    for key in keys:
        tick_cache._local.pop(key, None)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(pm, "FILL_POLL_DELAY_SECONDS", 0)
    pm._rejected_exit_alert.clear()


# --- A3: no broker session ---------------------------------------------------------------------------------------------

def test_without_a_broker_session_a_paper_stop_still_exits_on_the_stored_price_and_nothing_enters(monkeypatch, stored_tick):
    t = _tenant(_email("paper"), token_status="EXPIRED")
    dep_id = _deploy(t)
    trade_id = _open_trade(t, symbol="M55PA")
    stored_tick("M55PA", 97.0)
    broker = _expired_broker()
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)

    report = _run(worker.run_cycle(now=OPEN_NOW))

    row = _row(trade_id)
    assert row.exit_time is not None and row.exit_reason == "Stop Loss"
    assert report.positions_closed >= 1 and report.signals_executed == 0
    assert [tr.id for tr in _trades(t["tenant_id"])] == [trade_id]                    # entries unchanged: none
    assert broker.placed == []
    from app.db.models import StrategyDeploymentRecord
    from tests.test_trading_worker import _get
    assert "log in again" in _get(StrategyDeploymentRecord, dep_id).last_error.lower()


def test_without_a_broker_session_an_old_stored_price_makes_no_exit_decision(monkeypatch, stored_tick):
    t = _tenant(_email("old"), token_status="EXPIRED")
    trade_id = _open_trade(t, symbol="M55PB")
    stored_tick("M55PB", 97.0, age_seconds=config.EXIT_FALLBACK_TICK_MAX_AGE_SECONDS + 60)
    worker = _worker(monkeypatch, _expired_broker())
    _run(worker.run_cycle(now=OPEN_NOW))
    assert _row(trade_id).exit_time is None


def test_without_a_broker_session_a_live_exit_that_cannot_be_sent_raises_a_critical_alert(monkeypatch, stored_tick):
    t = _tenant(_email("live"), token_status="EXPIRED")
    trade_id = _open_trade(t, symbol="M55LA", mode="LIVE")
    stored_tick("M55LA", 97.0)
    broker = _expired_broker()
    worker = _worker(monkeypatch, broker)

    _run(worker.run_cycle(now=OPEN_NOW))
    _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=1)))

    assert _row(trade_id).exit_time is None and broker.placed == []
    alerts = [n for n in _notes(t["tenant_id"]) if n.title == "LIVE positions cannot be exited"]
    assert len(alerts) == 1 and alerts[0].severity == "CRITICAL"                       # once per cooldown
    assert "log in again" in alerts[0].message.lower()


# --- A9: lock lost mid-cycle -------------------------------------------------------------------------------------------

def test_after_the_lock_is_lost_the_remaining_tenants_get_exits_and_no_entries(monkeypatch):
    first = _tenant(_email("lock-a"))
    _deploy(first)
    second = _tenant(_email("lock-b"))
    _deploy(second)                     # _tenant stops earlier deployments; re-activate the first one's
    from app.db.models import StrategyDeploymentRecord

    async def activate():
        async with _session_factory() as session:
            for dep in await session.scalars(select(StrategyDeploymentRecord).where(StrategyDeploymentRecord.tenant_id == first["tenant_id"])):
                dep.status = "ACTIVE"
            await session.commit()
    _run(activate())
    trade_id = _open_trade(second, symbol="RELIANCE")
    worker = _worker(monkeypatch, _FakeBroker(ltp=97.0))
    _force_signal(monkeypatch, _signal)

    async def keep_lock(session, report, where):
        if where.startswith("before tenant ") and int(where.rsplit(" ", 1)[1]) < second["tenant_id"]:
            return True
        worker._lock_lost = report.lock_lost = True
        report.lock_aborted = True
        return False
    monkeypatch.setattr(worker, "_keep_lock", keep_lock)

    report = _run(worker.run_cycle(now=OPEN_NOW))

    assert report.lock_aborted and report.exits_only_tenants >= 1
    row = _row(trade_id)
    assert row.exit_time is not None and row.exit_reason == "Stop Loss"               # the exit ran without the lock
    assert [tr.id for tr in _trades(second["tenant_id"])] == [trade_id]              # and no entry
    assert len(_trades(first["tenant_id"])) == 1                                      # the tenant reached first traded as before


def test_a_lock_held_by_a_holder_with_no_finished_cycle_is_logged_as_stale(monkeypatch, caplog):
    async def held(key, holder, ttl):
        return False, True

    async def holder(key):
        return "other-host:1", True
    monkeypatch.setattr(tw, "cache_try_lock", held)
    monkeypatch.setattr(tw, "cache_lock_holder", holder)
    worker = TradingWorker(_session_factory, cycle_seconds=60, worker_name=f"m55-stale-{uuid.uuid4().hex[:6]}")
    with caplog.at_level(logging.WARNING, logger="app.workers.trading_worker"):
        report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.skipped_lock
    assert any("may be stale" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


# --- A13: the per-day price-read allowance -----------------------------------------------------------------------------

def test_the_daily_read_allowance_keeps_a_reserve_for_exits():
    daily = DailyBudget("groww", {"live_data": 100}, share=1.0, read_reserve=0.1)
    for _ in range(90):
        daily.take("get_ltp_for_symbol")
    with pytest.raises(DailyCapReached):
        daily.take("get_ltp_for_symbol")                     # an entry's read stops before the reserve
    assert daily.pop_cap_hits() == {"live_data": False}
    with exit_reads():
        for _ in range(10):
            daily.take("get_ltp_for_symbol")                 # an exit's read uses it
        with pytest.raises(DailyCapReached):
            daily.take("get_ltp_for_symbol")                 # the broker's own share ends it
    assert daily.pop_cap_hits() == {"live_data": True} and daily.pop_cap_hits() == {}
    daily.take("get_order_book")                             # the reads an exit depends on still pass


class _Quotes(_FakeBroker):
    name = "groww"

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        return 97.0


def test_an_exit_price_read_comes_from_the_reserve_and_an_entry_read_is_refused():
    daily = DailyBudget("groww", {"live_data": 10}, share=1.0, read_reserve=0.2)
    broker = RateLimitedBroker(_Quotes(), RateBudget(RateLimits(per_second=1000.0, burst=1000, per_minute=100000), daily=daily))

    async def go():
        for _ in range(8):
            await broker.get_ltp_for_symbol("M55Q")
        with pytest.raises(DailyCapReached):
            await broker.get_ltp_for_symbol("M55Q")
        with exit_reads():
            return await broker.get_ltp_for_symbol("M55Q")
    assert _run(go()) == 97.0


def test_a_reached_daily_cap_is_alerted_once_and_an_exit_refusal_is_critical():
    t = _tenant(_email("cap"), with_credentials=False)
    worker = TradingWorker(_session_factory, cycle_seconds=60)
    budget = worker._budget_for(t["tenant_id"], "groww@primary")
    budget.daily.cap_hits = {"live_data": False}

    async def alert():
        async with _session_factory() as session:
            await worker._alert_daily_caps(session, t["tenant_id"])
    _run(alert())
    _run(alert())                                            # nothing new reached: nothing new sent
    budget.daily.cap_hits = {"live_data": True}
    _run(alert())
    budget.daily.cap_hits = {"live_data": True}
    _run(alert())                                            # once a day per kind
    notes = [(n.title, n.severity) for n in _notes(t["tenant_id"])]
    assert notes == [("Daily broker allowance reached", "WARNING"), ("Exit price reads stopped for today", "CRITICAL")]


# --- A7: a refused exit order --------------------------------------------------------------------------------------------

class _Refusing(_Book):
    """Refuses the first `refusals` exit orders with `status`; `filled` = what the book shows filled on a refused one."""

    def __init__(self, refusals: int, status: str = "REJECTED", filled: float = 0.0):
        super().__init__()
        self.refusals = refusals
        self.status = status
        self.filled = filled

    async def place_order(self, order):
        if self.refusals <= 0:
            return await super().place_order(order)
        self.refusals -= 1
        self.placed.append(order)
        oid = f"NEW-{len(self.placed)}"
        self.orders[oid] = BrokerOrderStatus(order_id=oid, symbol=order.symbol, transaction_type=order.transaction_type, quantity=order.quantity,
                                             filled_quantity=self.filled, order_type=order.order_type, status=self.status,
                                             average_price=self.ltp if self.filled else None)
        return BrokerOrderResponse(order_id=oid, status=self.status, message="RMS: blocked")


def test_a_rejected_exit_is_logged_at_error_notified_and_retried_by_the_policy(monkeypatch, caplog):
    monkeypatch.setattr(config, "EXIT_REJECT_RETRIES", 2)
    monkeypatch.setattr(config, "EXIT_REJECT_RETRY_SPACING_SECONDS", 0.0)
    t = _tenant(_email("rej-retry"))
    trade_id = _live_trade(t, symbol="M55R", exit_order_id=None)
    broker = _Refusing(2)
    with caplog.at_level(logging.ERROR, logger="app.trading.position_monitor"):
        outcome = _close(trade_id, broker, t["user_id"])
    assert outcome.closed and len(broker.placed) == 3                                  # two refusals, then the exit filled
    assert sum(1 for r in caplog.records if r.levelno == logging.ERROR and "rejected by" in r.getMessage()) == 2
    assert not [n for n in _notes(t["tenant_id"]) if n.title.startswith("Exit refused")]   # it went out in the end


def test_a_rejected_exit_with_no_retry_left_raises_one_critical_and_stays_open(monkeypatch, caplog):
    monkeypatch.setattr(config, "EXIT_REJECT_RETRIES", 0)                 # the default: one send per check
    t = _tenant(_email("rej-alert"))
    trade_id = _live_trade(t, symbol="M55S", exit_order_id=None)
    with caplog.at_level(logging.ERROR, logger="app.trading.position_monitor"):
        first = _close(trade_id, _Refusing(5), t["user_id"])
        second = _close(trade_id, _Refusing(5), t["user_id"])                       # the next check tries again
    assert not first.closed and not second.closed and _row(trade_id).exit_time is None
    assert any(r.levelno == logging.ERROR and "refused by the broker" in r.getMessage() for r in caplog.records)
    alerts = [n for n in _notes(t["tenant_id"]) if n.title == "Exit refused by the broker: M55S"]
    assert len(alerts) == 1 and alerts[0].severity == "CRITICAL" and alerts[0].related_trade_id == trade_id


def test_a_cancelled_exit_that_part_filled_is_never_sent_again_in_the_same_check(monkeypatch):
    monkeypatch.setattr(config, "EXIT_REJECT_RETRIES", 3)
    monkeypatch.setattr(config, "EXIT_REJECT_RETRY_SPACING_SECONDS", 0.0)
    t = _tenant(_email("rej-part"))
    trade_id = _live_trade(t, symbol="M55T", exit_order_id=None, quantity=2)
    broker = _Refusing(5, status="CANCELLED", filled=1.0)
    outcome = _close(trade_id, broker, t["user_id"])
    assert not outcome.closed and len(broker.placed) == 1                              # no second exit on a part fill
    assert [n for n in _notes(t["tenant_id"]) if n.title == "Exit refused by the broker: M55T"]


def test_entries_are_untouched_by_the_exit_reserve(monkeypatch):
    """An entry order is still refused past the entry cap inside or outside the exits phase (only reads use the read
    reserve); the reserve never lets an entry through."""
    daily = DailyBudget("groww", {"orders": 10}, share=1.0, exit_reserve=0.2, read_reserve=0.5)
    for _ in range(8):
        daily.take("place_order", entry=True)
    with exit_reads():
        with pytest.raises(DailyCapReached):
            daily.take("place_order", entry=True)
    daily.take("place_order", entry=False)                   # an exit order always passes
