"""M55 S7: worker liveness - every pass (also an early skip) writes last_evaluated_at with why the bot waits; the API
raises ONE CRITICAL when the worker's heartbeat is silent beyond N cycles; a market-data failure streak makes a bot
"waiting for data" (never PAUSED) and it resumes by itself; code errors still pause; a person's pause is never undone;
exits run unchanged."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core import config
from app.db.models import NotificationRecord, RuntimeCounterRecord, StrategyDeploymentRecord, WorkerHeartbeatRecord
from app.strategy_engine.registry import registry
from app.workers import liveness
from app.workers import trading_worker as tw
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import (
    CLOSED_NOW,
    IST,
    OPEN_NOW,
    STRATEGY,
    _deploy,
    _FakeBroker,
    _force_signal,
    _get,
    _run,
    _signal,
    _tenant,
    _trades,
    _worker,
)

pytestmark = pytest.mark.usefixtures("no_open_positions_from_earlier_tests")


def _notes(tenant_id: int):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(NotificationRecord.tenant_id == tenant_id)
                                              .order_by(NotificationRecord.id)))
    return _run(go())


def _set(dep_id: int, **values) -> None:
    async def go():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            for k, v in values.items():
                setattr(dep, k, v)
            await session.commit()
    _run(go())


def _break_data(broker: _FakeBroker) -> None:
    async def down(*args, **kwargs):
        raise ConnectionError("feed unreachable")
    broker.get_historical_data = down
    broker.get_intraday_candles = down


def _mend_data(broker: _FakeBroker) -> None:
    for name in ("get_historical_data", "get_intraday_candles"):
        broker.__dict__.pop(name, None)


def _utc(value):
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# --- 1. every pass writes the time and the reason ----------------------------------------------------------------------

def test_market_closed_pass_writes_last_evaluated_and_reason(monkeypatch):
    t = _tenant("s7-closed@example.com")
    dep_id = _deploy(t)
    worker = _worker(monkeypatch, _FakeBroker())
    _run(worker.run_cycle(now=CLOSED_NOW))
    dep = _get(StrategyDeploymentRecord, dep_id)
    assert dep.wait_reason == tw.WAIT_MARKET_CLOSED
    assert _utc(dep.last_evaluated_at) == CLOSED_NOW.astimezone(UTC)


def test_no_broker_session_early_skip_writes_last_evaluated_and_reason(monkeypatch):
    t = _tenant("s7-nologin@example.com", token_status="EXPIRED")
    dep_id = _deploy(t)
    broker = _FakeBroker()

    async def rejecting_profile():
        from app.brokers.exceptions import BrokerAuthenticationError
        raise BrokerAuthenticationError("Invalid token")
    broker.get_profile = rejecting_profile
    worker = _worker(monkeypatch, broker)
    _run(worker.run_cycle(now=OPEN_NOW))
    dep = _get(StrategyDeploymentRecord, dep_id)
    assert dep.wait_reason == "Waiting: broker login needed"
    assert _utc(dep.last_evaluated_at) == OPEN_NOW.astimezone(UTC)


def test_after_the_cutoff_the_reason_says_so_and_a_normal_pass_clears_it(monkeypatch):
    t = _tenant("s7-cutoff@example.com")
    dep_id = _deploy(t)
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0, intraday_bars=350))
    _force_signal(monkeypatch, lambda: _signal(ts=datetime(2026, 9, 25, 15, 4, tzinfo=IST)))
    _run(worker.run_cycle(now=datetime(2026, 9, 25, 15, 5, tzinfo=IST)))
    assert _get(StrategyDeploymentRecord, dep_id).wait_reason == "Waiting: no new entries after the cut-off"

    t2 = _tenant("s7-normal@example.com")
    dep2 = _deploy(t2)
    _set(dep2, wait_reason=tw.WAIT_MARKET_CLOSED)
    worker2 = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    monkeypatch.setattr(registry.get(STRATEGY), "analyze", lambda data, symbol: _signal(ts=datetime(2026, 9, 25, 10, 29, tzinfo=IST)))
    _run(worker2.run_cycle(now=OPEN_NOW))
    assert _get(StrategyDeploymentRecord, dep2).wait_reason is None


def test_deployment_response_carries_the_wait_reason(monkeypatch):
    t = _tenant("s7-api@example.com")
    dep_id = _deploy(t)
    _set(dep_id, wait_reason=tw.WAIT_MARKET_CLOSED)
    rows = client.get("/api/deployments", headers=t["headers"]).json()
    rows = rows if isinstance(rows, list) else rows.get("deployments", rows)
    assert any(r["id"] == dep_id and r["wait_reason"] == tw.WAIT_MARKET_CLOSED for r in rows)


# --- 2. the heartbeat goes stale: the API tells each organisation once ------------------------------------------------

def _heartbeat(seen: datetime, cycle: int) -> None:
    async def go():
        async with _session_factory() as session:
            row = await session.scalar(select(WorkerHeartbeatRecord).where(WorkerHeartbeatRecord.worker_name == "trading_worker"))
            if row is None:
                row = WorkerHeartbeatRecord(worker_name="trading_worker")
                session.add(row)
            row.last_seen_at = seen
            row.cycle_count = cycle
            row.last_error = None
            await session.commit()
    _run(go())


def _forget_alerts() -> None:
    async def go():
        async with _session_factory() as session:
            for row in await session.scalars(select(RuntimeCounterRecord).where(RuntimeCounterRecord.name == liveness.ALERT_COUNTER)):
                await session.delete(row)
            await session.commit()
    _run(go())


async def _alert(now, tenant_id=None):
    async with _session_factory() as session:
        return await liveness.alert_if_silent(session, now, tenant_id=tenant_id)


def test_stale_heartbeat_raises_one_critical_with_cooldown():
    t = _tenant("s7-silent@example.com")
    _deploy(t)
    _forget_alerts()
    now = datetime.now(UTC)
    window = config.WORKER_CYCLE_SECONDS * config.WORKER_SILENT_CYCLES_ALERT

    _heartbeat(now - timedelta(seconds=window - 5), cycle=100)           # within N cycles: not stale
    assert _run(_alert(now, t["tenant_id"])) == 0

    _heartbeat(now - timedelta(seconds=window + 30), cycle=100)          # silent beyond N cycles
    assert _run(_alert(now, t["tenant_id"])) == 1
    assert _run(_alert(now + timedelta(minutes=1), t["tenant_id"])) == 0      # the same silence: never twice
    status = client.get("/api/system/worker-status", headers=t["headers"]).json()
    assert status["bots_checked"] is False and status["running"] is False
    critical = [n for n in _notes(t["tenant_id"]) if n.title == liveness.SILENT_TITLE]
    assert len(critical) == 1 and critical[0].severity == "CRITICAL"

    # A new silence inside the cool-down: still quiet. After the cool-down: told again.
    _heartbeat(now - timedelta(seconds=window + 30), cycle=101)
    assert _run(_alert(now + timedelta(minutes=2), t["tenant_id"])) == 0
    later = now + timedelta(seconds=config.WORKER_SILENCE_ALERT_COOLDOWN_SECONDS + 60)
    _heartbeat(later - timedelta(seconds=window + 30), cycle=102)
    assert _run(_alert(later, t["tenant_id"])) == 1

    # A fresh heartbeat: bots are checked again.
    _heartbeat(datetime.now(UTC), cycle=103)
    assert client.get("/api/system/worker-status", headers=t["headers"]).json()["bots_checked"] is True


def test_silence_is_not_told_to_an_organisation_without_running_bots():
    t = _tenant("s7-nobots@example.com")
    _forget_alerts()
    now = datetime.now(UTC)
    _heartbeat(now - timedelta(hours=1), cycle=200)
    assert _run(_alert(now, t["tenant_id"])) == 0
    _heartbeat(datetime.now(UTC), cycle=201)


# --- 3. data failures wait and resume by themselves; code errors still pause --------------------------------------------

def test_data_failure_streak_waits_for_data_then_resumes_by_itself(monkeypatch):
    t = _tenant("s7-data@example.com")
    dep_id = _deploy(t)
    broker = _FakeBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    monkeypatch.setattr(registry.get(STRATEGY), "analyze", lambda data, symbol: _signal())
    _break_data(broker)

    n = config.DEPLOYMENT_DATA_FAILURES_BEFORE_WAIT

    async def cycles(first: int, count: int):      # one event loop for a run of cycles, as in the worker process
        for i in range(first, first + count):
            await worker.run_cycle(now=OPEN_NOW + timedelta(minutes=i))
    _run(cycles(0, n + 2))
    dep = _get(StrategyDeploymentRecord, dep_id)
    assert dep.status == "ACTIVE" and dep.pause_reason is None        # never PAUSED for data
    assert dep.data_failures == n + 2 and dep.waiting_for_data_since is not None
    assert dep.consecutive_failures == 0 and dep.wait_reason == tw.WAIT_DATA
    notes = _notes(t["tenant_id"])
    waiting = [x for x in notes if x.title.startswith("Bot waiting for market data")]
    assert len(waiting) == 1 and waiting[0].severity == "WARNING"     # once, not every cycle
    assert not any(x.title.startswith("Deployment paused") for x in notes)

    _mend_data(broker)
    from app.brokers.circuit_breaker import reset_all
    reset_all()                     # the broker's circuit (opened by the failed reads) has closed again by then
    worker = _worker(monkeypatch, broker)   # a fresh event loop here: the worker's per-broker rate budgets belong to one loop
    _run(cycles(n + 2, 1))
    dep = _get(StrategyDeploymentRecord, dep_id)
    assert dep.status == "ACTIVE" and dep.data_failures == 0 and dep.waiting_for_data_since is None
    assert any(x.title.startswith("Bot resumed") for x in _notes(t["tenant_id"]))


def test_code_error_streak_still_pauses(monkeypatch):
    t = _tenant("s7-code@example.com")
    dep_id = _deploy(t)
    worker = _worker(monkeypatch, _FakeBroker())

    def boom(data, symbol):
        raise RuntimeError("indicator blew up")
    monkeypatch.setattr(registry.get(STRATEGY), "analyze", boom)
    for i in range(tw.MAX_CONSECUTIVE_FAILURES):
        _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=i)))
    dep = _get(StrategyDeploymentRecord, dep_id)
    assert dep.status == "PAUSED" and "Auto-paused" in dep.pause_reason and dep.data_failures == 0


def test_a_code_error_inside_the_data_read_still_pauses(monkeypatch):
    t = _tenant("s7-code-read@example.com")
    dep_id = _deploy(t)
    worker = _worker(monkeypatch, _FakeBroker())

    class _Buggy:
        async def get_frames(self, *args, **kwargs):
            raise TypeError("bad argument")
    monkeypatch.setattr(tw, "_history_source", lambda market_data, strategy: _Buggy())
    for i in range(tw.MAX_CONSECUTIVE_FAILURES):
        _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=i)))
    dep = _get(StrategyDeploymentRecord, dep_id)
    assert dep.status == "PAUSED" and "Auto-paused" in dep.pause_reason
    assert dep.data_failures == 0 and dep.waiting_for_data_since is None


def test_a_manual_pause_is_never_resumed(monkeypatch):
    t = _tenant("s7-manual@example.com")
    dep_id = _deploy(t)
    _set(dep_id, status="PAUSED", pause_reason="paused from the Trading bot page", data_failures=7,
         waiting_for_data_since=datetime.now(UTC))
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    monkeypatch.setattr(registry.get(STRATEGY), "analyze", lambda data, symbol: _signal())
    for i in range(2):
        _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=i)))
    dep = _get(StrategyDeploymentRecord, dep_id)
    assert dep.status == "PAUSED" and dep.pause_reason == "paused from the Trading bot page"
    assert _trades(t["tenant_id"]) == []


def test_exits_run_unchanged_while_the_bot_waits_for_data(monkeypatch):
    t = _tenant("s7-exits@example.com")
    _deploy(t)
    broker = _FakeBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))
    assert _trades(t["tenant_id"])[0].exit_time is None

    _break_data(broker)
    for i in range(1, config.DEPLOYMENT_DATA_FAILURES_BEFORE_WAIT + 1):
        _run(worker.run_cycle(now=OPEN_NOW + timedelta(seconds=10 * i)))
    broker.ltp = 97.0                                                  # below the stop (98)
    report = _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=2)))
    trade = _trades(t["tenant_id"])[0]
    assert report.positions_closed == 1 and trade.exit_time is not None


# --- the migration ------------------------------------------------------------------------------------------------------

def test_migration_a7d3e5c9f214_is_additive_and_round_trips(tmp_path):
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "a7d3e5c9f214_deployment_wait_reason.py"
    spec = importlib.util.spec_from_file_location("m55_s7_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "f9c2a4e6b801" and module.ROLLBACK_SAFE is True
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    added = {"wait_reason", "data_failures", "waiting_for_data_since"}
    with eng.begin() as conn:
        conn.execute(sa.text("CREATE TABLE strategy_deployments (id INTEGER PRIMARY KEY, symbol VARCHAR(20))"))
        conn.execute(sa.text("INSERT INTO strategy_deployments (id, symbol) VALUES (1, 'KEPT')"))
        ctx = MigrationContext.configure(conn)
        for _ in range(2):
            with Operations.context(ctx):
                module.upgrade()
            assert added <= {c["name"] for c in sa.inspect(conn).get_columns("strategy_deployments")}
            assert conn.execute(sa.text("SELECT data_failures FROM strategy_deployments")).scalar() == 0   # server default
            with Operations.context(ctx):
                module.downgrade()
            assert not added & {c["name"] for c in sa.inspect(conn).get_columns("strategy_deployments")}
            assert conn.execute(sa.text("SELECT symbol FROM strategy_deployments")).scalar() == "KEPT"
