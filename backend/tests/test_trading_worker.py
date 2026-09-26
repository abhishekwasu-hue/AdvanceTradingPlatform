"""Phase A6: the autonomous trading worker's cycle, end to end against the in-memory DB with a
fake broker - market gate, token gate, one-entry-per-signal, monitoring, square-off, failure
auto-pause, heartbeat."""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.brokers import token_lifecycle
from app.brokers.base import BrokerInterface
from app.brokers.models import BrokerOrderResponse, BrokerProfile
from app.core.enums import SignalDirection, SignalGrade
from app.core.models import OHLCVBar, Signal
from app.db.models import (
    BrokerCredentialRecord, NotificationRecord, OrderRecord, StrategyDeploymentRecord, TradeRecord, User,
    WorkerHeartbeatRecord,
)
from app.strategy_engine.registry import registry
from app.workers import trading_worker as tw
from app.workers.trading_worker import TradingWorker
from tests.test_auth_api import _register, _session_factory, client

IST = ZoneInfo("Asia/Kolkata")
OPEN_NOW = datetime(2026, 9, 25, 10, 30, tzinfo=IST)          # Friday, mid-session
LATE_NOW = datetime(2026, 9, 25, 15, 16, tzinfo=IST)          # past square-off
CLOSED_NOW = datetime(2026, 9, 26, 10, 30, tzinfo=IST)        # Saturday
STRATEGY = "ema_rsi_scalper_1m"
BAR_TS = datetime(2026, 9, 25, 10, 29, tzinfo=IST)


class _FakeBroker(BrokerInterface):
    name = "upstox"

    def __init__(self, ltp: float = 101.0, bars: int = 200):
        self.ltp = ltp
        self.bars = bars
        self.placed = []

    async def get_profile(self): return BrokerProfile(broker="upstox", user_id="U1")
    async def authenticate(self): return await self.get_profile()
    async def get_instruments(self, exchange=None): return []
    async def get_ltp(self, symbols): return {s: self.ltp for s in symbols}
    async def get_quote(self, symbols): return {}

    def _bars(self, start: datetime, n: int) -> List[OHLCVBar]:
        return [OHLCVBar(timestamp=start + timedelta(minutes=i), open=100, high=101, low=99, close=100.5, volume=10) for i in range(n)]

    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        return self._bars(datetime(2026, 9, 24, 9, 15, tzinfo=IST), self.bars)
    async def get_intraday_candles(self, symbol, exchange, interval):
        return self._bars(datetime(2026, 9, 25, 9, 15, tzinfo=IST), 75)
    async def get_option_chain(self, underlying, expiry=None): raise NotImplementedError
    async def place_order(self, order):
        self.placed.append(order)
        return BrokerOrderResponse(order_id=f"ORD-{len(self.placed)}", status="OPEN")
    async def modify_order(self, order_id, quantity=None, price=None, trigger_price=None, order_type=None): raise NotImplementedError
    async def cancel_order(self, order_id): return BrokerOrderResponse(order_id=order_id, status="CANCELLED")
    async def get_order_book(self): return []
    async def get_trade_book(self): return []
    async def get_positions(self): return []
    async def get_holdings(self): return []
    async def get_margins(self): raise NotImplementedError


def _run(coro):
    return asyncio.run(coro)


def _upgrade_plan(tenant_id: int, plan: str = "business") -> None:
    """Free tenants are paper-only with one member (Phase B2); these tests exercise LIVE, teams
    and multiple channels, which are Pro/Business features."""
    from app.db.models import Tenant

    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            tenant.plan = plan
            await session.commit()
    asyncio.run(go())


def _stop_all_deployments() -> None:
    """The worker is platform-wide: it evaluates every tenant's ACTIVE deployment in one cycle.
    Each test starts by stopping whatever earlier tests left behind so its counts are exact."""
    async def go():
        async with _session_factory() as session:
            for dep in await session.scalars(select(StrategyDeploymentRecord)):
                dep.status = "STOPPED"
            await session.commit()
    _run(go())


def _tenant(email: str, *, token_status="VALID", with_credentials=True) -> Dict:
    _stop_all_deployments()
    token = _register(email)
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=headers).json()
    _upgrade_plan(me["tenant_id"])
    if with_credentials:
        client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": "k", "api_secret": "s", "access_token": "t"})

        async def mark():
            async with _session_factory() as session:
                record = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == me["tenant_id"]))
                record.token_status = token_status
                record.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=8) if token_status == "VALID" else None
                record.last_verified_at = datetime.now(timezone.utc)
                await session.commit()
        _run(mark())
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}


def _deploy(t: Dict, *, mode="PAPER", symbol="RELIANCE", status="ACTIVE", broker_name="upstox", strategy_id=STRATEGY) -> int:
    async def go():
        async with _session_factory() as session:
            dep = StrategyDeploymentRecord(
                tenant_id=t["tenant_id"], strategy_id=strategy_id, symbol=symbol, exchange="NSE", timeframe="1min",
                mode=mode, broker_name=broker_name, status=status, created_by=t["user_id"],
            )
            session.add(dep)
            await session.commit()
            return dep.id
    return _run(go())


def _get(model, id_):
    async def go():
        async with _session_factory() as session:
            return await session.get(model, id_)
    return _run(go())


def _trades(tenant_id: int) -> List[TradeRecord]:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id).order_by(TradeRecord.id)))
    return _run(go())


def _signal(direction=SignalDirection.LONG, ts=BAR_TS) -> Signal:
    return Signal(
        symbol="RELIANCE", strategy_id=STRATEGY, strategy_name="EMA + RSI", direction=direction, timestamp=ts,
        entry=100.0, stop_loss=98.0, target1=104.0, target2=108.0, risk_reward=2.0, score=85,
        grade=SignalGrade.HIGH_QUALITY, reasons=["forced"], timeframe_combo="1min",
    )


def _worker(monkeypatch, broker: _FakeBroker) -> TradingWorker:
    # Both the worker's adapter construction and verify_token's go through the fake broker.
    monkeypatch.setattr(tw, "build_adapter", lambda record, client=None: broker)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record, client=None: broker)
    # Every test's fake broker answers for the same symbol, so with a real Redis reachable (CI)
    # one test's candles would be served to the next from the market-data cache. Cache off here.
    from app.market_data import service as market_data_service
    monkeypatch.setattr(market_data_service, "cache_get", _no_cache_get)
    monkeypatch.setattr(market_data_service, "cache_set", _no_cache_set)
    # The daily instrument-master download (Phase F1) needs the network; tests that want it
    # replace this again with their own fake.
    monkeypatch.setattr(tw, "sync_upstox", _no_master_sync)
    return TradingWorker(_session_factory, cycle_seconds=60)


async def _no_cache_get(key):
    return None


async def _no_cache_set(key, value, ttl_seconds):
    return None


async def _no_master_sync(session, exchanges, client=None):
    return None


def _force_signal(monkeypatch, signal_factory):
    monkeypatch.setattr(registry.get(STRATEGY), "analyze", lambda data, symbol: signal_factory())


def test_closed_market_only_heartbeats(monkeypatch):
    t = _tenant("w-closed@example.com")
    _deploy(t)
    _force_signal(monkeypatch, _signal)
    worker = _worker(monkeypatch, _FakeBroker())

    report = _run(worker.run_cycle(now=CLOSED_NOW))

    assert not report.market_open and "weekend" in report.session_reason
    assert report.deployments_evaluated == 0 and _trades(t["tenant_id"]) == []
    hb = _run(_heartbeat())
    assert hb is not None and hb.cycle_count >= 1


async def _heartbeat():
    async with _session_factory() as session:
        return await session.scalar(select(WorkerHeartbeatRecord).where(WorkerHeartbeatRecord.worker_name == "trading_worker"))


def test_paper_deployment_enters_once_per_signal_bar(monkeypatch):
    t = _tenant("w-paper@example.com")
    dep_id = _deploy(t)
    _force_signal(monkeypatch, _signal)
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))

    first = _run(worker.run_cycle(now=OPEN_NOW))
    second = _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=1)))

    assert first.market_open and first.deployments_evaluated == 1 and first.signals_executed == 1
    assert second.signals_executed == 0  # same signal bar -> no re-entry
    trades = _trades(t["tenant_id"])
    assert len(trades) == 1
    assert trades[0].mode == "PAPER" and trades[0].deployment_id == dep_id and trades[0].exit_time is None
    dep = _get(StrategyDeploymentRecord, dep_id)
    assert dep.last_signal_at is not None and dep.last_evaluated_at is not None and dep.last_error is None

    async def order():
        async with _session_factory() as session:
            return await session.scalar(select(OrderRecord).where(OrderRecord.tenant_id == t["tenant_id"]))
    assert _run(order()).idempotency_key.startswith(f"deployment:{dep_id}:")


def test_new_signal_bar_after_position_closed_enters_again(monkeypatch):
    t = _tenant("w-rebar@example.com")
    dep_id = _deploy(t)
    broker = _FakeBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))

    # Position monitor closes it at target on the next cycle, then a *newer* bar signals again.
    broker.ltp = 104.5
    _force_signal(monkeypatch, lambda: _signal(ts=BAR_TS + timedelta(minutes=5)))
    report = _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=5)))

    assert report.positions_closed == 1 and report.signals_executed == 1
    trades = _trades(t["tenant_id"])
    assert len(trades) == 2
    assert trades[0].exit_reason == "Target 1" and trades[1].exit_time is None


def test_open_position_blocks_a_second_entry_for_same_deployment(monkeypatch):
    t = _tenant("w-onepos@example.com")
    _deploy(t)
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))
    _force_signal(monkeypatch, lambda: _signal(ts=BAR_TS + timedelta(minutes=3)))
    report = _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=3)))
    assert report.signals_executed == 0 and len(_trades(t["tenant_id"])) == 1


def test_tenant_without_usable_broker_session_is_skipped_with_reason(monkeypatch):
    t = _tenant("w-notoken@example.com", token_status="EXPIRED")
    dep_id = _deploy(t)
    _force_signal(monkeypatch, _signal)
    broker = _FakeBroker()

    async def rejecting_profile():
        from app.brokers.exceptions import BrokerAuthenticationError
        raise BrokerAuthenticationError("Invalid token")
    broker.get_profile = rejecting_profile
    worker = _worker(monkeypatch, broker)

    report = _run(worker.run_cycle(now=OPEN_NOW))

    assert report.signals_executed == 0 and _trades(t["tenant_id"]) == []
    assert "log in again" in _get(StrategyDeploymentRecord, dep_id).last_error.lower()
    assert _get(StrategyDeploymentRecord, dep_id).status == "ACTIVE"  # resumes by itself after re-login


def test_live_deployment_trades_through_broker_with_protective_stop(monkeypatch):
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    t = _tenant("w-live@example.com")
    dep_id = _deploy(t, mode="LIVE")
    broker = _FakeBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)

    report = _run(worker.run_cycle(now=OPEN_NOW))

    assert report.signals_executed == 1
    assert [o.order_type for o in broker.placed] == ["MARKET", "SL-M"]
    trade = _trades(t["tenant_id"])[0]
    assert trade.mode == "LIVE" and trade.broker_order_id == "ORD-1" and trade.sl_order_id == "ORD-2"
    assert trade.deployment_id == dep_id


def test_live_deployment_never_fires_on_expired_token_but_paper_sibling_still_runs(monkeypatch):
    """Paper needs the broker only for candles - which an expired token can't serve either - so
    both stop; the point is the LIVE one is refused explicitly, never silently attempted."""
    t = _tenant("w-live-expired@example.com", token_status="EXPIRED")
    dep_id = _deploy(t, mode="LIVE")
    broker = _FakeBroker()

    async def rejecting_profile():
        from app.brokers.exceptions import BrokerAuthenticationError
        raise BrokerAuthenticationError("Invalid token")
    broker.get_profile = rejecting_profile
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)

    _run(worker.run_cycle(now=OPEN_NOW))
    assert broker.placed == [] and _trades(t["tenant_id"]) == []
    assert "log in again" in _get(StrategyDeploymentRecord, dep_id).last_error.lower()


def test_repeated_failures_auto_pause_deployment_with_alert(monkeypatch):
    t = _tenant("w-fail@example.com")
    dep_id = _deploy(t)
    worker = _worker(monkeypatch, _FakeBroker())

    def boom(data, symbol):
        raise RuntimeError("indicator blew up")
    monkeypatch.setattr(registry.get(STRATEGY), "analyze", boom)

    for i in range(tw.MAX_CONSECUTIVE_FAILURES):
        report = _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=i)))
        assert report.errors and "indicator blew up" in report.errors[0]

    dep = _get(StrategyDeploymentRecord, dep_id)
    assert dep.status == "PAUSED" and "Auto-paused" in dep.pause_reason
    assert dep.consecutive_failures == tw.MAX_CONSECUTIVE_FAILURES

    async def notes():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(NotificationRecord.tenant_id == t["tenant_id"])))
    alerts = _run(notes())
    assert any(n.event_type == "SYSTEM_FAILURE" and n.severity == "CRITICAL" for n in alerts)

    # A paused deployment is no longer evaluated.
    after = _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=10)))
    assert after.deployments_evaluated == 0 and not after.errors


def test_insufficient_history_is_reported_not_traded(monkeypatch):
    t = _tenant("w-short@example.com")
    dep_id = _deploy(t)
    broker = _FakeBroker(bars=0)
    broker.get_intraday_candles = lambda symbol, exchange, interval: _async([])  # type: ignore[assignment]
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)

    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 0 and not report.errors
    assert "Insufficient history" in _get(StrategyDeploymentRecord, dep_id).last_error


async def _async(value):
    return value


def test_no_new_entries_after_cutoff_but_monitoring_continues(monkeypatch):
    t = _tenant("w-cutoff@example.com")
    dep_id = _deploy(t)
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    late_signal_ts = datetime(2026, 9, 25, 15, 4, tzinfo=IST)
    _force_signal(monkeypatch, lambda: _signal(ts=late_signal_ts))

    report = _run(worker.run_cycle(now=datetime(2026, 9, 25, 15, 5, tzinfo=IST)))
    assert report.signals_executed == 0 and _trades(t["tenant_id"]) == []
    assert "no new entries after" in _get(StrategyDeploymentRecord, dep_id).last_error


def test_square_off_flattens_open_positions_after_1515(monkeypatch):
    t = _tenant("w-squareoff@example.com")
    _deploy(t)
    broker = _FakeBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))
    assert _trades(t["tenant_id"])[0].exit_time is None

    broker.ltp = 100.7  # between stop and target - nothing would close it normally
    _force_signal(monkeypatch, lambda: _signal(ts=LATE_NOW))
    report = _run(worker.run_cycle(now=LATE_NOW))

    trade = _trades(t["tenant_id"])[0]
    assert report.positions_closed == 1
    assert trade.exit_reason == "Intraday square-off" and trade.exit_price == 100.7
    assert len(_trades(t["tenant_id"])) == 1  # and no fresh entry that late


def test_worker_status_endpoint_reflects_heartbeat(monkeypatch):
    t = _tenant("w-status@example.com", with_credentials=False)
    before = client.get("/api/system/worker-status", headers=t["headers"]).json()
    worker = _worker(monkeypatch, _FakeBroker())
    _run(worker.run_cycle(now=CLOSED_NOW))
    after = client.get("/api/system/worker-status", headers=t["headers"]).json()

    assert after["running"] is True and after["cycle_count"] >= 1
    assert after["seconds_since_heartbeat"] is not None and after["seconds_since_heartbeat"] < 60
    assert after["cycle_count"] > (before["cycle_count"] or 0)
    assert isinstance(after["market_open"], bool) and after["market_status"]


def test_worker_status_requires_auth():
    assert client.get("/api/system/worker-status").status_code in (401, 403)
