"""M55 S6 (A11) + A18: two guards before a NEW automated entry - never before an exit (ADR-0004).

- S6: one direction per symbol across an organisation's bots. The organisation's `opposite_positions` risk setting
  ("deny" by default, owner order M55; "allow" turns it off): a bot's entry is skipped while another of its bots holds
  an open position on the same underlying in the other direction. Same direction: unchanged.
- A18: Redis down means no replica lock - no new automated entry, PAPER and LIVE alike; exits still run.
"""
import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.core.enums import SignalDirection
from app.db.models import RiskSettingsRecord, StrategyDeploymentRecord
from app.deployments import why
from app.strategy_engine.registry import registry
from app.workers import trading_worker as tw
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import BAR_TS, OPEN_NOW, STRATEGY, _deploy, _FakeBroker, _signal, _tenant, _trades, _worker

LATER = OPEN_NOW + timedelta(minutes=5)
NEW_BAR = BAR_TS + timedelta(minutes=5)
OTHER = "supertrend_adx_scalper_1m"


def _run(coro):
    return asyncio.run(coro)


def _force(monkeypatch, signal_factory):
    """Both bots' strategies read the same forced signal."""
    for strategy_id in (STRATEGY, OTHER):
        monkeypatch.setattr(registry.get(strategy_id), "analyze", lambda data, symbol: signal_factory())


def _dep(dep_id):
    async def go():
        async with _session_factory() as session:
            return await session.get(StrategyDeploymentRecord, dep_id)
    return _run(go())


def _first_bot_long(monkeypatch, email, ltp=101.0):
    """Bot A on RELIANCE goes LONG (a real PAPER entry through the worker); then bot B is set up on the same symbol."""
    t = _tenant(email)
    a = _deploy(t)
    broker = _FakeBroker(ltp=ltp)
    worker = _worker(monkeypatch, broker)
    _force(monkeypatch, _signal)
    assert _run(worker.run_cycle(now=OPEN_NOW)).signals_executed == 1
    b = _deploy(t, strategy_id=OTHER)          # a second bot (another strategy) on the same symbol
    return t, a, b, broker, worker


def _set(t, value):
    response = client.put("/api/risk-settings", headers=t["headers"], json={"opposite_positions": value})
    assert response.status_code == 200 and response.json()["opposite_positions"] == value


def test_the_setting_defaults_to_deny_and_a_save_without_it_keeps_the_choice():
    t = _tenant("m55s6-setting@example.com")
    assert client.get("/api/risk-settings", headers=t["headers"]).json()["opposite_positions"] == "deny"
    _set(t, "allow")
    # An older page saves the other settings without the field: the stored choice stays.
    assert client.put("/api/risk-settings", headers=t["headers"], json={"max_trades_per_day": 5}).json()["opposite_positions"] == "allow"
    assert client.get("/api/risk-settings", headers=t["headers"]).json()["opposite_positions"] == "allow"
    assert client.put("/api/risk-settings", headers=t["headers"], json={"opposite_positions": "maybe"}).status_code == 422

    async def stored():
        async with _session_factory() as session:
            return await session.scalar(select(RiskSettingsRecord.opposite_positions).where(RiskSettingsRecord.tenant_id == t["tenant_id"]))
    assert _run(stored()) == "allow"
    assert "opposite_position" in why.CODES


def test_an_opposite_entry_on_a_symbol_another_bot_holds_is_skipped_under_deny(monkeypatch):
    t, a, b, _, worker = _first_bot_long(monkeypatch, "m55s6-deny@example.com")
    _force(monkeypatch, lambda: _signal(SignalDirection.SHORT, ts=NEW_BAR))
    report = _run(worker.run_cycle(now=LATER))
    assert report.signals_executed == 0
    trades = _trades(t["tenant_id"])
    assert [(x.deployment_id, x.direction) for x in trades] == [(a, "LONG")] and trades[0].exit_time is None
    error = _dep(b).last_error or ""
    assert "another of your bots already holds a long position on RELIANCE" in error
    assert not any(word in error.lower() for word in ("buy", "sell", "target", "stop-loss"))   # signal words only


def test_the_same_opposite_entry_is_taken_under_allow(monkeypatch):
    t, a, b, _, worker = _first_bot_long(monkeypatch, "m55s6-allow@example.com")
    _set(t, "allow")
    _force(monkeypatch, lambda: _signal(SignalDirection.SHORT, ts=NEW_BAR))
    report = _run(worker.run_cycle(now=LATER))
    assert report.signals_executed == 1
    assert sorted((x.deployment_id, x.direction) for x in _trades(t["tenant_id"])) == [(a, "LONG"), (b, "SHORT")]


def test_a_same_direction_entry_from_another_bot_is_unchanged_under_deny(monkeypatch):
    t, a, b, _, worker = _first_bot_long(monkeypatch, "m55s6-same@example.com")
    _force(monkeypatch, lambda: _signal(SignalDirection.LONG, ts=NEW_BAR))
    report = _run(worker.run_cycle(now=LATER))
    assert report.signals_executed == 1
    assert sorted((x.deployment_id, x.direction) for x in _trades(t["tenant_id"])) == [(a, "LONG"), (b, "LONG")]


def test_an_opposite_position_on_another_symbol_never_counts(monkeypatch):
    t = _tenant("m55s6-other@example.com")
    _deploy(t)
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    _force(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))
    _deploy(t, symbol="INFY", strategy_id=OTHER)
    _force(monkeypatch, lambda: _signal(SignalDirection.SHORT, ts=NEW_BAR))
    assert _run(worker.run_cycle(now=LATER)).signals_executed == 1


def test_exits_are_unchanged_under_deny(monkeypatch):
    t, a, b, broker, worker = _first_bot_long(monkeypatch, "m55s6-exit@example.com")
    broker.ltp = 97.0                                              # bot A's position reaches its own stop level
    _force(monkeypatch, lambda: _signal(SignalDirection.SHORT, ts=NEW_BAR))
    report = _run(worker.run_cycle(now=LATER))
    assert report.positions_closed >= 1                            # (older tests' open positions may close too)
    first = [x for x in _trades(t["tenant_id"]) if x.deployment_id == a][0]
    assert first.exit_time is not None and first.exit_reason


def test_redis_down_skips_a_paper_entry_and_the_exit_still_runs(monkeypatch):
    t, a, b, broker, worker = _first_bot_long(monkeypatch, "m55s6-redis@example.com")

    async def down(key, holder, ttl):
        return True, False                                         # Redis unreachable: no replica lock this cycle
    monkeypatch.setattr(tw, "cache_try_lock", down)
    broker.ltp = 97.0
    _force(monkeypatch, lambda: _signal(SignalDirection.LONG, ts=NEW_BAR))   # same direction: only A18 can stop it
    report = _run(worker.run_cycle(now=LATER))
    assert report.lock_degraded and not report.skipped_lock
    assert report.positions_closed >= 1 and report.signals_executed == 0
    trades = _trades(t["tenant_id"])
    assert len(trades) == 1 and trades[0].deployment_id == a and trades[0].exit_time is not None
    assert "Redis replica lock unavailable - new automated entries paused" in (_dep(b).last_error or "")


def test_migration_is_additive_rollback_safe_reversible_and_the_only_head():
    import importlib.util
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from scripts.migration_rollback_safety import lint, risky_operations

    root = Path(__file__).resolve().parents[1]
    path = root / "alembic" / "versions" / "c7e9a1b3d5f7_m55_s6_opposite_positions.py"
    spec = importlib.util.spec_from_file_location("m55_s6_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.ROLLBACK_SAFE is True and module.revision == "c7e9a1b3d5f7" and module.down_revision == "f4b6d8a0c2e3"
    source = path.read_text(encoding="utf-8")
    assert risky_operations(source) == [] and not [p for p in lint() if p.startswith(path.name)]
    assert "server_default='deny'" in source and "drop_column('risk_settings', 'opposite_positions')" in source
    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    heads = script.get_heads()
    assert len(heads) == 1 and "c7e9a1b3d5f7" in {r.revision for r in script.walk_revisions("base", heads[0])}
