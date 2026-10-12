"""ADR-0004: the worker's exits sweep covers every open position, not only those of organisations with an ACTIVE or
PAUSED deployment. A PAPER position opened from a ticket, a manual /paper or a webhook proposal has no deployment, and a
deployment can be stopped while its position stays open: the stop must still close it."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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


# --- #221 review: every venue, a LIVE square-off and an organisation with no active user ------------------------------

IST = ZoneInfo("Asia/Kolkata")


def _open_at(t, symbol: str, exchange: str, when: datetime, mode: str = "PAPER") -> int:
    async def go():
        async with _session_factory() as session:
            row = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], symbol=symbol, exchange=exchange, strategy_id="plan_other",
                              direction="LONG", mode=mode, entry_time=when - timedelta(minutes=30), entry_price=100.0, quantity=1,
                              stop_loss=98.0, target1=120.0)
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


@pytest.mark.parametrize("symbol,exchange,now", [
    ("CRUDEOIL", "MCX", datetime(2026, 9, 25, 20, 0, tzinfo=IST)),        # NSE closed, MCX open
    ("CRUDEOIL", "MCX", datetime(2026, 9, 25, 10, 30, tzinfo=IST)),       # both open
    ("BTCUSDT", "BINANCE", datetime(2026, 9, 26, 12, 0, tzinfo=IST)),     # a Saturday: only crypto trades
    ("USDINR", "CDS", datetime(2026, 9, 25, 16, 0, tzinfo=IST)),          # CDS open, NSE closed
])
def test_stop_without_deployment_on_its_own_venue_clock(monkeypatch, symbol, exchange, now):
    """No ACTIVE / PAUSED deployment anywhere: the open position's own venue decides the clock the worker keeps."""
    t = _tenant(f"exits-nodep-{exchange.lower()}-{now.hour}@example.com")
    trade_id = _open_at(t, symbol, exchange, now)
    report = _run(_worker(monkeypatch, _FakeBroker(ltp=90.0)).run_cycle(now=now.astimezone(timezone.utc)))
    assert report.market_open, report.session_reason
    trade = next(x for x in _trades(t["tenant_id"]) if x.id == trade_id)
    assert trade.exit_time is not None and "stop" in (trade.exit_reason or "").lower(), (report.open_exchanges, report.session_reason)


def test_live_position_without_deployment_is_squared_off(monkeypatch):
    """MED-2: at 15:20 IST a LIVE position with no deployment is squared off through its own broker session with a MARKET
    exit (an exit: the Phase 0 PAPER-only chokepoint lets it through)."""
    now = datetime(2026, 9, 25, 15, 20, tzinfo=IST)
    t = _tenant("exits-nodep-live@example.com")
    trade_id = _open_at(t, "RELIANCE", "NSE", now, mode="LIVE")
    broker = _FakeBroker(ltp=101.0)
    from app.trading import position_monitor as pm
    monkeypatch.setattr(pm, "build_adapter", lambda record, client=None: broker)
    _run(_worker(monkeypatch, broker).run_cycle(now=now.astimezone(timezone.utc)))
    trade = next(x for x in _trades(t["tenant_id"]) if x.id == trade_id)
    assert trade.exit_time is not None and trade.exit_reason == "Intraday square-off"
    assert len(broker.placed) == 1
    order = broker.placed[0]
    assert str(getattr(order.order_type, "value", order.order_type)) == "MARKET"
    assert str(getattr(order.transaction_type, "value", order.transaction_type)) == "SELL" and order.symbol == "RELIANCE"


def test_exits_run_for_an_organisation_without_an_active_user(monkeypatch):
    """LOW-4: no active member left - the stop still closes the position (no entries are evaluated)."""
    from app.db.models import User
    t = _tenant("exits-nouser@example.com")
    trade_id = _open_trade(t)

    async def deactivate():
        async with _session_factory() as session:
            (await session.get(User, t["user_id"])).is_active = False
            await session.commit()
    _run(deactivate())
    _cycle(monkeypatch, ltp=90.0)
    trade = next(x for x in _trades(t["tenant_id"]) if x.id == trade_id)
    assert trade.exit_time is not None and "stop" in (trade.exit_reason or "").lower()
