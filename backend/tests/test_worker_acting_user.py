"""TX-3 (OPEN_QUESTIONS): the trading worker files a deployment's entries under the member who set the deployment up -
its creator, when that user is an active member of the same organisation - instead of one acting user for the whole
organisation, so a member's automated trades never carry another member's user id (the own-trades tax report, BT1b).
A deployment with no recorded creator keeps the organisation's acting user; one whose creator was removed, belongs to
another organisation or lost the trading role opens no new entries (TX-4). Exits are unchanged (ADR-0004): the monitor and the
square-off close every open position of the organisation, whoever's it is."""
from datetime import timedelta

from sqlalchemy import select

from app.db.models import AiActionRecord, StrategyDeploymentRecord, User
from app.strategy_engine.registry import registry
from app.workers import trading_worker as tw
from tests.test_auth_api import _session_factory
from tests.test_trading_worker import LATE_NOW, OPEN_NOW, STRATEGY, _deploy, _FakeBroker, _run, _signal, _tenant, _trades, _worker
from tests.test_z3_members import member


def _deactivate(user_id: int) -> None:
    async def go():
        async with _session_factory() as session:
            (await session.get(User, user_id)).is_active = False
            await session.commit()
    _run(go())


def _set_creator(dep_id: int, user_id) -> None:
    async def go():
        async with _session_factory() as session:
            (await session.get(StrategyDeploymentRecord, dep_id)).created_by = user_id
            await session.commit()
    _run(go())


def _signals(monkeypatch, ts=None):
    """A forced signal on whatever symbol a deployment trades (the deployments below trade different symbols)."""
    def analyze(data, symbol):
        sig = _signal(ts=ts) if ts is not None else _signal()
        return sig.model_copy(update={"symbol": symbol})
    monkeypatch.setattr(registry.get(STRATEGY), "analyze", analyze)


def _by_deployment(tenant_id: int) -> dict:
    return {t.deployment_id: t for t in _trades(tenant_id)}


def test_each_deployments_entries_carry_its_creator_and_exits_still_close_them(monkeypatch):
    owner = _tenant("w-acting-owner@example.com")
    mate = member(owner)
    mine = _deploy(owner)
    theirs = _deploy(mate, symbol="TCS")
    _signals(monkeypatch)
    broker = _FakeBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)

    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 2
    trades = _by_deployment(owner["tenant_id"])
    assert trades[mine].user_id == owner["user_id"]
    assert trades[theirs].user_id == mate["user_id"]                # not the organisation's acting user any more

    # ADR-0004: the square-off still closes every open position of the organisation, the member's included.
    broker.ltp = 100.7
    _signals(monkeypatch, ts=LATE_NOW)
    closed = _run(worker.run_cycle(now=LATE_NOW))
    assert closed.positions_closed == 2
    assert all(t.exit_reason == "Intraday square-off" for t in _trades(owner["tenant_id"]))


def _set_role(user_id: int, role: str) -> None:
    async def go():
        async with _session_factory() as session:
            (await session.get(User, user_id)).role = role
            await session.commit()
    _run(go())


def _dep(dep_id: int) -> StrategyDeploymentRecord:
    async def go():
        async with _session_factory() as session:
            return await session.get(StrategyDeploymentRecord, dep_id)
    return _run(go())


def test_a_removed_or_foreign_creator_opens_no_entries_and_their_open_position_still_closes(monkeypatch):
    owner = _tenant("w-acting-removed@example.com")
    gone = member(owner)
    stranger = _tenant("w-acting-stranger@example.com")
    _deploy(owner)
    left = _deploy(gone, symbol="TCS")
    _signals(monkeypatch)
    broker = _FakeBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _run(worker.run_cycle(now=OPEN_NOW))
    assert _by_deployment(owner["tenant_id"])[left].user_id == gone["user_id"]          # opened while still a member

    _deactivate(gone["user_id"])                                                       # removed from the team (team/routes.py)
    forged = _deploy(owner, symbol="INFY")
    _set_creator(forged, stranger["user_id"])                                          # another organisation's user: never used
    _signals(monkeypatch, ts=OPEN_NOW + timedelta(minutes=5))
    _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=5)))
    trades = _trades(owner["tenant_id"])
    assert [t.deployment_id for t in trades].count(left) == 1 and forged not in {t.deployment_id for t in trades}
    assert _dep(left).last_error.startswith("No new entries: the member who set up this deployment is no longer an active member")
    assert _dep(forged).last_error.startswith("No new entries")
    assert not _trades(stranger["tenant_id"])

    # ADR-0004: the removed member's open position is still squared off.
    broker.ltp = 100.7
    _signals(monkeypatch, ts=LATE_NOW)
    _run(worker.run_cycle(now=LATE_NOW))
    assert _by_deployment(owner["tenant_id"])[left].exit_reason == "Intraday square-off"


def test_a_creator_without_a_trading_role_opens_no_entries(monkeypatch):
    owner = _tenant("w-acting-viewer@example.com")
    mate = member(owner)
    dep = _deploy(mate, symbol="TCS")
    _set_role(mate["user_id"], "VIEWER")
    _signals(monkeypatch)
    _run(_worker(monkeypatch, _FakeBroker(ltp=101.0)).run_cycle(now=OPEN_NOW))
    assert dep not in {t.deployment_id for t in _trades(owner["tenant_id"])}
    assert "no longer has a trading role or the trading:write scope" in _dep(dep).last_error


def test_no_recorded_creator_keeps_the_acting_user_and_a_failed_lookup_skips_entries(monkeypatch):
    owner = _tenant("w-acting-legacy@example.com")
    mate = member(owner)
    legacy = _deploy(owner, symbol="TCS")
    _set_creator(legacy, None)
    theirs = _deploy(mate, symbol="INFY")
    calls = []
    original = tw.TradingWorker._deployment_user

    async def flaky(self, session, tenant_id, dep, fallback, cache):
        calls.append(dep.id)
        if dep.id == theirs:
            raise RuntimeError("database hiccup")
        return await original(self, session, tenant_id, dep, fallback, cache)
    monkeypatch.setattr(tw.TradingWorker, "_deployment_user", flaky)
    _signals(monkeypatch)
    report = _run(_worker(monkeypatch, _FakeBroker(ltp=101.0)).run_cycle(now=OPEN_NOW))
    trades = _by_deployment(owner["tenant_id"])
    assert set(calls) == {legacy, theirs} and report.signals_executed == 1           # the loop went on past the failure
    # the organisation's acting user (`_acting_user`: the first recorded deployment creator, here the member) - as before TX-3
    assert trades[legacy].user_id == mate["user_id"]
    # a failed lookup is never re-attributed to the acting user: no entry this cycle (TX-4)
    assert theirs not in trades


def test_an_approval_only_entry_is_proposed_for_the_resolved_deployment_user(monkeypatch):
    from tests.test_z3_approval_entries import _flag_for
    owner = _tenant("w-acting-approval@example.com")
    _flag_for(owner["tenant_id"])
    legacy = _deploy(owner, symbol="TCS")
    _set_creator(legacy, None)                                                         # older row: no creator recorded
    _signals(monkeypatch)
    _run(_worker(monkeypatch, _FakeBroker(ltp=101.0)).run_cycle(now=OPEN_NOW))

    async def proposals():
        async with _session_factory() as session:
            return list(await session.scalars(select(AiActionRecord).where(AiActionRecord.tenant_id == owner["tenant_id"],
                                                                            AiActionRecord.action == "ENTRY")))
    rows = _run(proposals())
    assert len(rows) == 1 and rows[0].owner_user_id == owner["user_id"]                   # not the raw (empty) created_by
