"""Phase G1: the market-data staleness gate (safety rule 7), the broker-uncertain tenant flag
(rule 8) and reconciliation on worker start (rule 18)."""
import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.brokers.models import BrokerPosition, Quote
from app.brokers.timestamps import parse_broker_timestamp
from app.core import config
from app.db.models import NotificationRecord, OrderRecord, StrategyDeploymentRecord, Tenant, TradeRecord, User
from app.market_data.freshness import StaleMarketDataError, candle_staleness, quote_is_stale, timeframe_seconds
from app.market_data.service import MarketDataService
from app.trading.position_monitor import monitor_open_positions
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import (
    OPEN_NOW, _FakeBroker, _deploy, _force_signal, _get, _run, _signal, _tenant, _trades, _worker,
)

IST = ZoneInfo("Asia/Kolkata")


# --- freshness rules -----------------------------------------------------------------------

def test_timeframe_seconds_parses_the_platform_timeframes():
    assert timeframe_seconds("1min") == 60 and timeframe_seconds("5min") == 300 and timeframe_seconds("15min") == 900
    assert timeframe_seconds("1h") == 3600 and timeframe_seconds("60min") == 3600 and timeframe_seconds("1d") == 86400
    assert timeframe_seconds("garbage") == 60


def test_candle_staleness_allows_the_configured_missed_bars_then_refuses():
    now = datetime(2026, 9, 25, 10, 30, tzinfo=IST)
    assert candle_staleness(now - timedelta(minutes=1), "1min", now, max_stale_bars=3) is None
    assert candle_staleness(now - timedelta(minutes=4), "1min", now, max_stale_bars=3) is None   # (3+1) bars: still allowed
    reason = candle_staleness(now - timedelta(minutes=9), "1min", now, max_stale_bars=3)
    assert reason and "stale" in reason and "9 min old" in reason and "limit 3" in reason
    # Scaled by timeframe: a 5-minute feed 9 minutes behind is fine.
    assert candle_staleness(now - timedelta(minutes=9), "5min", now, max_stale_bars=3) is None
    assert candle_staleness(None, "1min", now) == "no candles received"
    assert candle_staleness(now - timedelta(hours=5), "1min", now, max_stale_bars=0) is None  # disabled


def test_quote_staleness_judges_only_timestamped_quotes():
    now = datetime(2026, 9, 25, 5, 0, tzinfo=timezone.utc)
    assert quote_is_stale(None, now) is None
    assert quote_is_stale(now - timedelta(seconds=30), now, max_seconds=120) is None
    assert "quote stale" in quote_is_stale(now - timedelta(seconds=300), now, max_seconds=120)
    assert quote_is_stale(now - timedelta(hours=1), now, max_seconds=0) is None


def test_broker_timestamp_parsing_handles_upstox_and_kite_formats():
    upstox = parse_broker_timestamp("2026-09-25T10:29:58+05:30")
    kite = parse_broker_timestamp("2026-09-25 10:29:58")
    assert upstox == kite == datetime(2026, 9, 25, 4, 59, 58, tzinfo=timezone.utc)
    assert parse_broker_timestamp(1790312398000) == datetime(2026, 9, 25, 4, 59, 58, tzinfo=timezone.utc)
    assert parse_broker_timestamp("") is None and parse_broker_timestamp("not a time") is None


# --- staleness gate in the worker and the monitor ------------------------------------------

def test_worker_refuses_to_evaluate_a_stalled_candle_feed(monkeypatch):
    t = _tenant("g-stale@example.com")
    dep_id = _deploy(t)
    broker = _FakeBroker(ltp=101.0, intraday_bars=40)  # newest bar 09:54, cycle at 10:30
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)

    report = _run(worker.run_cycle(now=OPEN_NOW))

    assert report.signals_executed == 0 and report.stale_skips == 1 and _trades(t["tenant_id"]) == []
    err = _get(StrategyDeploymentRecord, dep_id).last_error
    assert "market data stale" in err and "1min bar is 36 min old" in err

    # The feed catches up: the same deployment trades on the next cycle without intervention.
    broker.intraday_bars = 75
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 1 and report.stale_skips == 0


class _QuoteBroker(_FakeBroker):
    def __init__(self, quote_age_seconds: int):
        super().__init__(ltp=97.0)  # below the 98 stop: would close the position if acted on
        self.quote_age_seconds = quote_age_seconds

    async def get_quote_for_symbol(self, symbol, exchange="NSE"):
        return Quote(symbol=symbol, ltp=self.ltp, timestamp=datetime.now(timezone.utc) - timedelta(seconds=self.quote_age_seconds))


def test_market_data_service_raises_on_a_stale_quote_and_accepts_a_fresh_one():
    svc = MarketDataService(_QuoteBroker(quote_age_seconds=600))
    with pytest.raises(StaleMarketDataError, match="quote stale"):
        _run(svc.get_ltp("RELIANCE"))
    assert _run(MarketDataService(_QuoteBroker(quote_age_seconds=5)).get_ltp("RELIANCE")) == 97.0
    # A broker without timestamps (plain LTP endpoint) is accepted as-is.
    assert _run(MarketDataService(_FakeBroker(ltp=97.0)).get_ltp("RELIANCE")) == 97.0


def test_monitor_takes_no_exit_decision_on_a_stale_quote(monkeypatch):
    t = _tenant("g-stale-exit@example.com")
    _deploy(t)
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))
    trade = _trades(t["tenant_id"])[0]
    assert trade.exit_time is None

    async def sweep(broker):
        async with _session_factory() as session:
            return await monitor_open_positions(session, t["tenant_id"], MarketDataService(broker).get_ltp)

    outcomes = _run(sweep(_QuoteBroker(quote_age_seconds=600)))
    assert len(outcomes) == 1 and not outcomes[0].closed and "quote stale" in outcomes[0].warnings[0]
    assert _trades(t["tenant_id"])[0].exit_time is None

    outcomes = _run(sweep(_QuoteBroker(quote_age_seconds=5)))
    assert outcomes[0].closed and outcomes[0].exit_reason == "Stop Loss" and _trades(t["tenant_id"])[0].exit_time is not None


# --- broker-uncertain flag ------------------------------------------------------------------

class _PositionBroker(_FakeBroker):
    def __init__(self, positions):
        super().__init__()
        self.positions = positions

    async def get_positions(self):
        return self.positions


class _FailingBroker(_FakeBroker):
    async def place_order(self, order):
        raise TimeoutError("broker did not answer")


def _tenant_row(tenant_id: int) -> Tenant:
    return _get(Tenant, tenant_id)


def _orders(tenant_id: int):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == tenant_id).order_by(OrderRecord.id)))
    return _run(go())


def test_failed_live_order_flags_the_tenant_and_blocks_new_live_entries_until_reconciled(monkeypatch):
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    t = _tenant("g-uncertain@example.com")
    dep_id = _deploy(t, mode="LIVE")
    failing = _FailingBroker(ltp=101.0)
    worker = _worker(monkeypatch, failing)
    _force_signal(monkeypatch, _signal)

    _run(worker.run_cycle(now=OPEN_NOW))
    orders = _orders(t["tenant_id"])
    assert orders[-1].status == "FAILED"
    tenant = _tenant_row(t["tenant_id"])
    assert tenant.broker_uncertain_since is not None and "FAILED" in tenant.broker_uncertain_reason

    # Broker is healthy again and a new bar signals, but reconciliation (run by the worker every
    # cycle while flagged) finds a position at the broker the platform never recorded: the tenant
    # stays uncertain and the entry is skipped with the reason on the deployment.
    untracked = _PositionBroker([BrokerPosition(symbol="RELIANCE", quantity=10, average_price=100.0)])
    untracked.ltp = 101.0
    worker = _worker(monkeypatch, untracked)
    later = datetime(2026, 9, 25, 10, 31, tzinfo=IST)
    _force_signal(monkeypatch, lambda: _signal(ts=datetime(2026, 9, 25, 10, 30, tzinfo=IST)))
    report = _run(worker.run_cycle(now=later))
    assert report.reconciled == 1 and report.signals_executed == 0 and untracked.placed == []
    assert "broker state uncertain" in _get(StrategyDeploymentRecord, dep_id).last_error.lower()
    tenant = _tenant_row(t["tenant_id"])
    assert tenant.broker_uncertain_since is not None and tenant.last_reconciled_at is not None

    # The operator squares the stray position off at the broker; the next cycle's reconciliation
    # is clean, the flag clears, and the same signal bar is traded - no manual unblocking.
    clean = _PositionBroker([])
    clean.ltp = 101.0
    worker = _worker(monkeypatch, clean)
    report = _run(worker.run_cycle(now=later))
    assert report.reconciled == 1 and report.signals_executed == 1
    assert [o.order_type for o in clean.placed] == ["MARKET", "SL-M"]
    assert _tenant_row(t["tenant_id"]).broker_uncertain_since is None


def test_uncertain_tenant_rejects_live_entries_from_every_entry_point_but_paper_still_runs(monkeypatch):
    from app.execution.signal_execution import execute_signal_for_user

    t = _tenant("g-uncertain-api@example.com")

    async def flag():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, t["tenant_id"])
            tenant.broker_uncertain_since = datetime.now(timezone.utc)
            tenant.broker_uncertain_reason = "order 7 FAILED"
            await session.commit()
    _run(flag())

    async def go(mode):
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            return await execute_signal_for_user(session, user, mode=mode, strategy_id="ema_rsi_scalper_1m", signal=_signal(),
                                                 broker=_FakeBroker() if mode == "LIVE" else None)

    result, order = _run(go("LIVE"))
    assert not result.executed and order.status == "REJECTED"
    assert any("reconciliation passes" in r for r in result.reasons)
    result, order = _run(go("PAPER"))
    assert result.executed

    status = client.get("/api/reconciliation/status", headers=t["headers"]).json()
    assert status["broker_uncertain"] is True and status["broker_uncertain_reason"] == "order 7 FAILED"


def test_reconcile_on_start_flags_a_mismatch_and_clears_when_books_agree(monkeypatch):
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    t = _tenant("g-startup@example.com")
    _deploy(t, mode="LIVE")
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))
    trade = _trades(t["tenant_id"])[0]
    assert trade.mode == "LIVE" and trade.exit_time is None

    # Process restarts; the broker says it holds nothing -> MISSING_AT_BROKER -> flagged + CRITICAL.
    worker = _worker(monkeypatch, _PositionBroker([]))
    assert _run(worker.reconcile_on_start()) >= 1  # other tests' tenants may hold LIVE trades too
    tenant = _tenant_row(t["tenant_id"])
    assert tenant.broker_uncertain_since is not None and "MISSING_AT_BROKER RELIANCE" in tenant.broker_uncertain_reason

    async def critical():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(
                NotificationRecord.tenant_id == t["tenant_id"], NotificationRecord.severity == "CRITICAL")))
    assert any("do not match" in n.title for n in _run(critical()))

    # Restart again with the broker reporting the same net quantity -> clean -> cleared.
    matching = [BrokerPosition(symbol="RELIANCE", exchange="NSE", product="MIS", quantity=trade.quantity,
                               average_price=trade.entry_price, pnl=0.0)]
    worker = _worker(monkeypatch, _PositionBroker(matching))
    assert _run(worker.reconcile_on_start()) >= 1
    tenant = _tenant_row(t["tenant_id"])
    assert tenant.broker_uncertain_since is None and tenant.last_reconciled_at is not None

    # The API route runs the same service (broker positions via the stored credentials).
    monkeypatch.setattr("app.reconciliation.routes.get_broker_adapter", lambda name, creds: _PositionBroker([]))
    resp = client.post("/api/reconciliation/upstox", headers=t["headers"])
    assert resp.status_code == 200 and resp.json()["mismatched_count"] == 1
    assert client.get("/api/reconciliation/status", headers=t["headers"]).json()["broker_uncertain"] is True


def test_reconciliation_ignores_paper_positions():
    """PAPER positions never exist at the broker - counting them would flag every paper tenant."""
    from app.reconciliation.service import run_reconciliation

    t = _tenant("g-paper-recon@example.com")

    async def go():
        async with _session_factory() as session:
            session.add(TradeRecord(
                tenant_id=t["tenant_id"], user_id=t["user_id"], symbol="RELIANCE", strategy_id="ema_rsi_scalper_1m",
                direction="LONG", entry_time=datetime.now(timezone.utc), entry_price=100.0, quantity=10, stop_loss=98.0,
                target1=104.0, target2=108.0, mode="PAPER",
            ))
            await session.commit()
            tenant = await session.get(Tenant, t["tenant_id"])
            return await run_reconciliation(session, tenant, "upstox", _PositionBroker([]), user_id=t["user_id"], source="test")
    report = _run(go())
    assert report.mismatched_count == 0 and report.items == []
    assert _tenant_row(t["tenant_id"]).broker_uncertain_since is None
