"""M55 A18 (follow-up to #749): "Complete the rest" (SE-7) waits while Redis / the worker's replica lock is unavailable.

- `_complete_rests` only ever finishes a structure that already has a filled leg (a group exists only then), and no leg
  left to place is the hedge of a sold leg already open (the engine fills every hedge in full before any sold leg);
- while the lock is degraded nothing is sent: no new leg, the order stays "Complete the rest" (COMPLETING); one
  WARNING per outage; closing the filled legs (his rule at the deadline) is an exit and still runs (ADR-0004);
- with the lock back, the next cycle completes it.
"""
import logging
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import OrderRecord
from app.execution import incomplete_structures as inc
from app.workers import trading_worker as tw
from tests.test_auth_api import _session_factory, client
from tests.test_multileg import _OptionBroker
from tests.test_se7_live_multileg import _incomplete_paper, _no_poll_wait, _run, _shift_deadline, _smart  # noqa: F401
from tests.test_trading_worker import OPEN_NOW, _tenant, _trades, _worker

pytestmark = pytest.mark.usefixtures("no_open_positions_from_earlier_tests")
WARNING_TEXT = '"Complete the rest" orders wait'


def _redis_down(monkeypatch):
    async def try_lock(key, holder, ttl):
        return True, False

    async def renew(key, holder, ttl):
        return False

    async def holder(key):
        return None, False
    monkeypatch.setattr(tw, "cache_try_lock", try_lock)
    monkeypatch.setattr(tw, "cache_renew_lock", renew)
    monkeypatch.setattr(tw, "cache_lock_holder", holder)


def _orders(tenant_id):
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(OrderRecord).where(OrderRecord.tenant_id == tenant_id))
    return _run(go())


def _state(tenant_id):
    return {inc.block_of(tr)["state"] for tr in _trades(tenant_id)}


def test_complete_the_rest_waits_while_the_lock_is_degraded_and_runs_after_recovery(monkeypatch, caplog):
    t = _smart(_tenant("m55a18-rest@example.com"))
    result = _incomplete_paper(t, monkeypatch, key="a18r1")
    assert len(_trades(t["tenant_id"])) == 1                                  # the filled hedge only
    client.post(f"/api/trading/incomplete/{result.leg_group_id}/answer", headers=t["headers"], json={"action": "complete_rest"})
    orders_before = _orders(t["tenant_id"])
    worker = _worker(monkeypatch, _OptionBroker())
    caplog.set_level(logging.WARNING, logger=tw.logger.name)
    with monkeypatch.context() as down:
        _redis_down(down)
        for minute in (1, 2):
            report = _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=minute)))
            assert report.lock_degraded
        assert len(_trades(t["tenant_id"])) == 1 and _orders(t["tenant_id"]) == orders_before   # no leg sent
        assert _state(t["tenant_id"]) == {inc.COMPLETING}                    # still "Complete the rest", not cancelled
        assert sum(WARNING_TEXT in r.getMessage() for r in caplog.records) == 1   # one warning per outage
    _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=3)))              # Redis back
    trades = _trades(t["tenant_id"])
    assert len(trades) == 2 and _state(t["tenant_id"]) == {inc.COMPLETED} and {tr.leg_role for tr in trades} == {"LONG", "SHORT"}


def test_closing_the_filled_legs_still_runs_while_the_lock_is_degraded(monkeypatch):
    t = _smart(_tenant("m55a18-close@example.com"))
    _incomplete_paper(t, monkeypatch, key="a18c1")
    _shift_deadline(t, OPEN_NOW - timedelta(minutes=1))                      # his rule (close the filled legs) is due
    worker = _worker(monkeypatch, _OptionBroker())
    with monkeypatch.context() as down:
        _redis_down(down)
        report = _run(worker.run_cycle(now=OPEN_NOW))
    leg = _trades(t["tenant_id"])[0]
    assert report.lock_degraded and leg.exit_time is not None and inc.block_of(leg)["state"] == inc.CLOSED
