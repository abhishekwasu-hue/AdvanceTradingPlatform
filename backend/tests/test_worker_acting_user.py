"""TX-3 (OPEN_QUESTIONS): the trading worker files a deployment's entries under the member who set the deployment up -
its creator, when that user is an active member of the same organisation - instead of one acting user for the whole
organisation, so a member's automated trades never carry another member's user id (the own-trades tax report, BT1b).
Otherwise it falls back to the organisation's acting user, as before. Exits are unchanged (ADR-0004): the monitor and the
square-off close every open position of the organisation, whoever's it is."""
from datetime import timedelta

from app.db.models import StrategyDeploymentRecord, User
from app.strategy_engine.registry import registry
from tests.test_auth_api import _session_factory
from tests.test_trading_worker import LATE_NOW, OPEN_NOW, STRATEGY, _deploy, _FakeBroker, _run, _signal, _tenant, _trades, _worker
from tests.test_z3_members import member


def _deactivate(user_id: int) -> None:
    async def go():
        async with _session_factory() as session:
            (await session.get(User, user_id)).is_active = False
            await session.commit()
    _run(go())


def _set_creator(dep_id: int, user_id: int) -> None:
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


def test_inactive_or_foreign_creator_falls_back_to_the_acting_user(monkeypatch):
    owner = _tenant("w-acting-fallback@example.com")
    gone = member(owner)
    stranger = _tenant("w-acting-stranger@example.com")
    _stop_others = _deploy(owner)                                   # the owner's deployment: the organisation's acting user
    left = _deploy(gone, symbol="TCS")
    forged = _deploy(owner, symbol="INFY")
    _set_creator(forged, stranger["user_id"])                       # a creator from another organisation is never used
    _deactivate(gone["user_id"])
    _signals(monkeypatch)
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))

    _run(worker.run_cycle(now=OPEN_NOW + timedelta(seconds=1)))
    trades = _by_deployment(owner["tenant_id"])
    assert {trades[d].user_id for d in (_stop_others, left, forged)} == {owner["user_id"]}
    assert not _trades(stranger["tenant_id"])
