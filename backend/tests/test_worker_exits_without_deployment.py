"""ADR-0004: the worker's exits sweep covers every open position, not only those of organisations with an ACTIVE or
PAUSED deployment. A PAPER position opened from a ticket, a manual /paper or a webhook proposal has no deployment, and a
deployment can be stopped while its position stays open: the stop must still close it."""
from datetime import timedelta, timezone

import pytest

from app.db.models import TradeRecord
from tests.test_auth_api import _session_factory
from tests.test_trading_worker import OPEN_NOW, _deploy, _FakeBroker, _run, _tenant, _trades, _worker

pytestmark = pytest.mark.usefixtures("no_open_positions_from_earlier_tests")   # counts are per cycle (ADR-0004 sweep)


def _open_trade(t, *, deployment_id=None, stop=98.0, target=120.0) -> int:
    async def go():
        async with _session_factory() as session:
            row = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], deployment_id=deployment_id, symbol="RELIANCE",
                              strategy_id="plan_other", direction="LONG", mode="PAPER", entry_time=OPEN_NOW - timedelta(minutes=30),
                              entry_price=100.0, quantity=10, stop_loss=stop, target1=target)
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _cycle(monkeypatch, ltp: float):
    broker = _FakeBroker(ltp=ltp)
    return _run(_worker(monkeypatch, broker).run_cycle(now=OPEN_NOW.astimezone(timezone.utc))), broker


def test_stop_closes_a_paper_position_with_no_deployment(monkeypatch):
    t = _tenant("exits-nodep@example.com")
    trade_id = _open_trade(t)
    report, broker = _cycle(monkeypatch, ltp=90.0)            # through the stop at 98
    assert report.market_open and report.positions_closed >= 1
    trade = next(x for x in _trades(t["tenant_id"]) if x.id == trade_id)
    assert trade.exit_time is not None and "stop" in (trade.exit_reason or "").lower()
    assert broker.placed == []                                # PAPER: nothing reaches the broker's order API


def test_position_of_a_stopped_deployment_is_still_watched(monkeypatch):
    t = _tenant("exits-stopped@example.com")
    dep_id = _deploy(t, status="STOPPED")
    trade_id = _open_trade(t, deployment_id=dep_id)
    _cycle(monkeypatch, ltp=90.0)
    trade = next(x for x in _trades(t["tenant_id"]) if x.id == trade_id)
    assert trade.exit_time is not None and "stop" in (trade.exit_reason or "").lower()


def test_position_inside_its_levels_is_marked_and_stays_open(monkeypatch):
    t = _tenant("exits-hold@example.com")
    trade_id = _open_trade(t)
    _cycle(monkeypatch, ltp=101.0)
    trade = next(x for x in _trades(t["tenant_id"]) if x.id == trade_id)
    assert trade.exit_time is None and trade.mark_price == 101.0   # watched (marked), not closed
