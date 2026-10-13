"""Phase 0 deployment defaults (app/platform/deployment_phase.py, owner decision 2026-10-12).

With ATP_DEPLOYMENT_PHASE=phase0 a first boot stores `live_trading` OFF and `phase0_paper_only` ON where no entry is
stored (audited), a re-boot never overrides a later owner change, Admin -> Controls refuses `live_trading` ON while the
lock is on, the worker starts no LIVE entry path (and still exits an existing LIVE position - ADR-0004), and health says
why. Without the mode nothing changes: DEFAULT_OFF_FLAGS and the kill-flag defaults are as before."""
import asyncio
import json
from datetime import timedelta
from typing import Dict, List, Optional

import pytest
from sqlalchemy import select

from app.execution.tagging import LEG_EXIT
from app.db.models import AuditLogRecord, PlatformControlRecord, StrategyDeploymentRecord, User
from app.platform import controls, deployment_phase, phase0_lock
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import (
    BAR_TS, LATE_NOW, OPEN_NOW, _deploy, _FakeBroker, _force_signal, _get, _signal, _tenant, _trades, _worker,
)
from tests.test_z10c_phase0_lock import _StopStandingBroker

pytestmark = pytest.mark.usefixtures("no_open_positions_from_earlier_tests")   # counts are per cycle (ADR-0004 sweep)

SEEDED = (deployment_phase.LIVE_TRADING, deployment_phase.PAPER_ONLY)


def _run(coro):
    return asyncio.run(coro)


async def _row() -> Optional[str]:
    async with _session_factory() as session:
        row = await session.get(PlatformControlRecord, controls.KEY_FEATURE_FLAGS)
        return None if row is None else row.value_json


async def _write_row(value: Optional[str]) -> None:
    async with _session_factory() as session:
        row = await session.get(PlatformControlRecord, controls.KEY_FEATURE_FLAGS)
        if value is None:
            if row is not None:
                await session.delete(row)
        else:
            if row is None:
                row = PlatformControlRecord(key=controls.KEY_FEATURE_FLAGS)
                session.add(row)
            row.value_json = value
        await session.commit()


def _store(entries: Dict[str, Dict]) -> None:
    """The stored flags row with these entries (others kept), as Admin -> Controls would leave it."""
    async def go():
        current = json.loads(await _row() or "{}")
        flags = dict(current.get("flags") or {})
        flags.update(entries)
        await _write_row(json.dumps({**current, "flags": flags}))
    _run(go())


def _stored() -> Dict[str, Dict]:
    return (json.loads(_run(_row()) or "{}")).get("flags") or {}


def _flags() -> Dict[str, Dict]:
    async def go():
        async with _session_factory() as session:
            return await controls.feature_flags(session)
    return _run(go())


def _audit(event: str) -> List[str]:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(AuditLogRecord.detail).where(AuditLogRecord.event == event).order_by(AuditLogRecord.id)))
    return _run(go())


def _startup(process: str = "api") -> Dict:
    async def go():
        async with _session_factory() as session:
            return await deployment_phase.on_startup(session, process)
    return _run(go())


@pytest.fixture(autouse=True)
def _clean_flags(monkeypatch):
    """Each test starts with the two seeded flags absent from the stored row and leaves the row as it found it."""
    saved = _run(_row())
    current = json.loads(saved or "{}")
    flags = {k: v for k, v in (current.get("flags") or {}).items() if k not in SEEDED}
    _run(_write_row(json.dumps({**current, "flags": flags}) if saved is not None else None))
    monkeypatch.delenv(deployment_phase.ENV_VAR, raising=False)
    phase0_lock.reset_cache()
    yield
    _run(_write_row(saved))
    phase0_lock.reset_cache()


def _phase0(monkeypatch) -> None:
    monkeypatch.setenv(deployment_phase.ENV_VAR, "phase0")


def _owner_user() -> User:
    return User(id=None, tenant_id=1, email="owner@example.com", hashed_password="x", role="SUPER_ADMIN")


# --- the mode -----------------------------------------------------------------------------------------------------

def test_without_the_mode_nothing_is_seeded_and_the_defaults_are_unchanged(monkeypatch):
    for value in (None, "", "standard", "phase1"):
        if value is None:
            monkeypatch.delenv(deployment_phase.ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(deployment_phase.ENV_VAR, value)
        state = _startup()
        assert state["seeded"] == [] and not any(name in _stored() for name in SEEDED)
        flags = _flags()
        assert flags["live_trading"]["on"] is True and flags["phase0_paper_only"]["on"] is False   # kill flag / default off
    assert "phase0_paper_only" in controls.DEFAULT_OFF_FLAGS and "live_trading" not in controls.DEFAULT_OFF_FLAGS
    monkeypatch.setenv(deployment_phase.ENV_VAR, " PHASE0 ")                                       # normalised
    assert deployment_phase.is_phase0()


def test_phase0_defaults_hold_before_the_seed_is_written(monkeypatch):
    _phase0(monkeypatch)
    flags = _flags()
    assert flags["live_trading"]["on"] is False and flags["phase0_paper_only"]["on"] is True
    assert flags["news_feed"]["on"] is False and flags["ai_copilot"]["on"] is True                 # every other flag as before


# --- seeding --------------------------------------------------------------------------------------------------------

def test_first_boot_seeds_live_trading_off_and_the_lock_on_with_an_audit_row_each(monkeypatch):
    _phase0(monkeypatch)
    _run(_write_row(None))                                                                        # a fresh database: no row at all
    before = len(_audit(deployment_phase.SEED_AUDIT_EVENT))
    state = _startup("api")
    assert sorted(state["seeded"]) == sorted(SEEDED)
    assert _stored()["live_trading"] == {"on": False, "tenants": []} and _stored()["phase0_paper_only"] == {"on": True, "tenants": []}
    rows = _audit(deployment_phase.SEED_AUDIT_EVENT)[before:]
    assert len(rows) == 2 and any(r.startswith("live_trading: on=False") for r in rows) and any(r.startswith("phase0_paper_only: on=True") for r in rows)
    assert all("ATP_DEPLOYMENT_PHASE=phase0" in r for r in rows)
    assert state["phase0_paper_only"] is True and state["live_trading"] is False and state["live_entries"] == "refused"
    assert phase0_lock.locked(None)                                                               # the chokepoint in this process too


def test_seed_keeps_every_other_stored_flag(monkeypatch):
    _phase0(monkeypatch)
    _store({"news_feed": {"on": True, "tenants": [3]}})
    _startup("worker")
    stored = _stored()
    assert stored["news_feed"] == {"on": True, "tenants": [3]} and stored["phase0_paper_only"]["on"] is True


def test_reboot_is_idempotent_and_never_overrides_a_later_owner_change(monkeypatch):
    """The rule: seed ABSENT entries only. The owner's later choice survives every restart and redeploy."""
    _phase0(monkeypatch)
    assert sorted(_startup()["seeded"]) == sorted(SEEDED)
    before = len(_audit(deployment_phase.SEED_AUDIT_EVENT))
    assert _startup()["seeded"] == [] and _startup("worker")["seeded"] == []                     # re-boots write nothing
    assert len(_audit(deployment_phase.SEED_AUDIT_EVENT)) == before

    async def owner_turns_lock_off():
        async with _session_factory() as session:
            await controls.set_flag(session, _owner_user(), "phase0_paper_only", on=False)
    _run(owner_turns_lock_off())
    assert _startup()["seeded"] == [] and _stored()["phase0_paper_only"]["on"] is False          # not turned back on
    assert len(_audit(deployment_phase.SEED_AUDIT_EVENT)) == before


def test_a_partly_stored_row_gets_only_the_missing_entry(monkeypatch):
    """An entry the owner stored before the mode existed (even live_trading ON) is left alone; only the absent one is
    seeded - the health check then still asks the owner to act on live_trading (exit 10)."""
    _phase0(monkeypatch)
    _store({"live_trading": {"on": True, "tenants": []}})
    assert _startup()["seeded"] == ["phase0_paper_only"]
    assert _stored()["live_trading"] == {"on": True, "tenants": []} and _stored()["phase0_paper_only"]["on"] is True


@pytest.mark.parametrize("malformed", ["not json {", "[1, 2]", '{"flags": [1]}', '"text"'])
def test_a_malformed_row_is_never_overwritten_and_the_phase0_defaults_apply(monkeypatch, caplog, malformed):
    _phase0(monkeypatch)
    _run(_write_row(malformed))
    before = len(_audit(deployment_phase.SEED_AUDIT_EVENT))
    with caplog.at_level("ERROR", logger="app.platform.deployment_phase"):
        state = _startup()
    assert state["seeded"] == [] and _run(_row()) == malformed                                     # untouched
    assert len(_audit(deployment_phase.SEED_AUDIT_EVENT)) == before
    assert any("seed skipped" in r.getMessage() for r in caplog.records)
    flags = _flags()                                                                              # reads as unset: the safe defaults
    assert flags["live_trading"]["on"] is False and flags["phase0_paper_only"]["on"] is True
    assert state["phase0_paper_only"] is True and state["live_entries"] == "refused"


def test_seed_retries_after_an_integrity_error_and_warns_when_every_attempt_conflicts(monkeypatch, caplog):
    from sqlalchemy.exc import IntegrityError
    _phase0(monkeypatch)
    _run(_write_row(None))
    original = deployment_phase._seed_once
    calls: List[int] = []

    async def conflict_then(session, process):
        calls.append(1)
        if len(calls) <= conflicts:
            raise IntegrityError("INSERT INTO platform_controls", {}, Exception("duplicate key"))
        return await original(session, process)
    monkeypatch.setattr(deployment_phase, "_seed_once", conflict_then)

    conflicts = 2                                                                                 # the other process won twice
    assert sorted(_startup()["seeded"]) == sorted(SEEDED) and len(calls) == 3
    assert _stored()["phase0_paper_only"] == {"on": True, "tenants": []}

    _run(_write_row(None))
    calls.clear()
    conflicts = deployment_phase.SEED_ATTEMPTS                                                    # every attempt conflicts
    with caplog.at_level("WARNING", logger="app.platform.deployment_phase"):
        state = _startup()
    assert state["seeded"] == [] and len(calls) == deployment_phase.SEED_ATTEMPTS
    assert any("gave up after 3 attempts" in r.getMessage() for r in caplog.records)
    assert state["phase0_paper_only"] is True                                                     # still safe: Phase 0 defaults


def test_startup_never_raises_even_when_the_rollback_fails(monkeypatch):
    _phase0(monkeypatch)

    async def boom(*args, **kwargs):
        raise RuntimeError("database gone")

    class _Broken:
        async def rollback(self):
            raise RuntimeError("connection closed")
    monkeypatch.setattr(deployment_phase, "seed_flags", boom)
    monkeypatch.setattr(deployment_phase, "live_paths", boom)
    state = _run(deployment_phase.on_startup(_Broken(), "api"))                                  # type: ignore[arg-type]
    assert state["error"] == "unreadable" and state["seeded"] == []


# --- Admin -> Controls ----------------------------------------------------------------------------------------------

def test_admin_refuses_live_trading_on_while_the_lock_is_on_and_audits_it(monkeypatch):
    _phase0(monkeypatch)
    _startup()
    before = len(_audit(deployment_phase.REFUSAL_AUDIT_EVENT))

    async def turn_on(**kwargs):
        async with _session_factory() as session:
            await controls.set_flag(session, _owner_user(), "live_trading", **kwargs)

    for kwargs in ({"on": True}, {"on": False, "tenants": [5]}):
        with pytest.raises(ValueError, match="live_trading cannot be turned on while phase0_paper_only is on"):
            _run(turn_on(**kwargs))
    assert _stored()["live_trading"] == {"on": False, "tenants": []}                              # nothing changed
    refusals = _audit(deployment_phase.REFUSAL_AUDIT_EVENT)[before:]
    assert len(refusals) == 2 and all("live_trading" in r and "phase0_paper_only is on" in r for r in refusals)
    _run(turn_on(on=False))                                                                       # OFF is always allowed


def test_admin_route_answers_400_with_the_reason(monkeypatch):
    from tests.test_admin_api import _admin
    headers, _ = _admin("phase0-defaults-admin@example.com")
    _phase0(monkeypatch)
    _startup()
    res = client.put("/api/admin/controls/flags/live_trading", headers=headers, json={"on": True})
    assert res.status_code == 400 and "phase0_paper_only is on" in res.json()["detail"]
    assert _stored()["live_trading"]["on"] is False


def test_live_trading_follows_the_owner_once_the_lock_is_off_or_outside_the_mode(monkeypatch):
    async def set_flag(name, on):
        async with _session_factory() as session:
            await controls.set_flag(session, _owner_user(), name, on=on)

    _store({"phase0_paper_only": {"on": True, "tenants": []}})
    _run(set_flag("live_trading", True))                                                          # normal mode: unchanged behaviour
    _phase0(monkeypatch)
    _run(set_flag("phase0_paper_only", False))                                                    # the owner's explicit decision
    _run(set_flag("live_trading", True))
    assert _stored()["live_trading"]["on"] is True


# --- the worker: no LIVE entry path, exits still run -----------------------------------------------------------------

def test_worker_startup_seeds_and_does_not_evaluate_live_deployments(monkeypatch):
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    _phase0(monkeypatch)
    t = _tenant("phase0-defaults-entry@example.com")
    live_id = _deploy(t, mode="LIVE")
    paper_id = _deploy(t, mode="PAPER")
    broker = _FakeBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)
    evaluated: List[int] = []
    original = worker._evaluate_deployment

    async def spy(session, dep, *args, **kwargs):
        evaluated.append(dep.id)
        return await original(session, dep, *args, **kwargs)
    monkeypatch.setattr(worker, "_evaluate_deployment", spy)

    state = _run(worker.startup_check())                                                          # what run_forever does first
    assert sorted(state["seeded"]) == sorted(SEEDED) and state["phase0_paper_only"] is True
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert evaluated == [paper_id]                                                                # the LIVE entry path never started
    assert broker.placed == []                                                                    # nothing reached the broker
    assert _get(StrategyDeploymentRecord, live_id).last_error == phase0_lock.WORKER_NOT_EVALUATED
    assert report.signals_executed == 1 and [tr.mode for tr in _trades(t["tenant_id"])] == ["PAPER"]
    _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=1)))                                   # the same reason, not rewritten
    assert evaluated == [paper_id, paper_id] and _get(StrategyDeploymentRecord, live_id).last_error == phase0_lock.WORKER_NOT_EVALUATED


def test_worker_still_squares_off_an_existing_live_position(monkeypatch):
    """ADR-0004: a LIVE position opened before the Phase 0 mode was switched on is exited through the broker."""
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    monkeypatch.setattr("app.trading.position_monitor.FILL_POLL_DELAY_SECONDS", 0)
    t = _tenant("phase0-defaults-exit@example.com")
    _deploy(t, mode="LIVE")
    broker = _StopStandingBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))                                                          # normal mode: LIVE entry + stop
    trade = _trades(t["tenant_id"])[0]
    assert trade.mode == "LIVE" and trade.exit_time is None and len(broker.placed) == 2

    _phase0(monkeypatch)
    assert _run(worker.startup_check())["phase0_paper_only"] is True                              # restart in Phase 0 mode
    broker.ltp = 100.7
    _force_signal(monkeypatch, lambda: _signal(ts=BAR_TS + timedelta(hours=4, minutes=46)))
    report = _run(worker.run_cycle(now=LATE_NOW))
    closed = _trades(t["tenant_id"])
    assert report.positions_closed == 1 and closed[0].exit_reason == "Intraday square-off" and len(closed) == 1
    assert broker.placed[-1].tag is not None and broker.placed[-1].tag.endswith(LEG_EXIT)         # an exit leg reached the broker


# --- health -----------------------------------------------------------------------------------------------------------

def test_deep_health_says_why_live_entry_paths_are_not_started(monkeypatch):
    _phase0(monkeypatch)
    _startup()
    body = client.get("/api/system/health/deep").json()
    live = body["checks"]["live_paths"]
    assert live["status"] == "paper_only" and live["deployment_phase"] == "phase0" and live["exits"] == "always allowed"
    assert "phase0_paper_only is on" in live["detail"]
    monkeypatch.delenv(deployment_phase.ENV_VAR)
    _store({"phase0_paper_only": {"on": False, "tenants": []}})
    assert client.get("/api/system/health/deep").json()["checks"]["live_paths"]["status"] == "flag_controlled"


def test_flag_posture_reports_the_seeded_phase0_state(monkeypatch):
    import scripts.flag_posture as flag_posture
    _phase0(monkeypatch)
    _startup()
    monkeypatch.setattr("app.db.session._session_factory", _session_factory)
    posture = _run(flag_posture.posture())
    assert posture["deployment_phase"] == "phase0"
    assert posture["live_trading"] == {"on": False, "tenants": 0, "stored": True}
    assert posture["phase0_paper_only"] == {"known": True, "on": True, "stored": True}
