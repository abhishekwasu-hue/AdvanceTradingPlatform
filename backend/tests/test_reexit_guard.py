"""Exit safety before any LIVE work (ADR-0004).

* Re-exit guard: a LIVE position that already sent an exit (its id was committed when the broker took it, the booking did
  not happen) never gets a second exit on top of it. The earlier order decides: filled -> booked at its fill; still
  working -> wait; rejected / cancelled with nothing filled -> a new exit (both ids on the audit trail); not in the book,
  or ended after a part fill -> the tenant is flagged broker-uncertain and nothing is sent. The stop guard does not re-arm a
  stop on a position whose exit is pending.
* Naked-short guard: when a structure's SHORT leg does not close, its wings stay open (recorded + CRITICAL notification);
  the next pass tries the short first again."""
import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.brokers.models import BrokerOrderResponse, BrokerOrderStatus
from app.core.enums import OrderSide
from app.db.models import AuditLogRecord, NotificationRecord, Tenant, TradeRecord
from app.reconciliation.service import broker_uncertain_reason
from app.trading import position_monitor as pm
from app.trading import stop_guard
from tests.test_auth_api import _session_factory
from tests.test_trading_worker import OPEN_NOW, STRATEGY, _FakeBroker, _tenant


def _run(coro):
    return asyncio.run(coro)


class _Book(_FakeBroker):
    """An order book the test controls: `orders` by id; a placed MARKET order fills at the LTP unless `reject` names its
    symbol; a placed stop stays TRIGGER PENDING."""

    def __init__(self, ltp=97.0, reject=()):
        super().__init__(ltp=ltp)
        self.orders = {}
        self.reject = set(reject)

    async def place_order(self, order):
        self.placed.append(order)
        oid = f"NEW-{len(self.placed)}"
        if order.symbol in self.reject:
            return BrokerOrderResponse(order_id=oid, status="REJECTED", message="RMS: margin")
        filled = order.order_type == "MARKET"
        self.orders[oid] = BrokerOrderStatus(order_id=oid, symbol=order.symbol, transaction_type=order.transaction_type,
                                             quantity=order.quantity, filled_quantity=order.quantity if filled else 0,
                                             order_type=order.order_type, status="COMPLETE" if filled else "TRIGGER PENDING",
                                             average_price=self.ltp if filled else None)
        return BrokerOrderResponse(order_id=oid, status="OPEN")

    async def get_order_book(self):
        return list(self.orders.values())


def _old(status, filled=0.0, price=None):
    return BrokerOrderStatus(order_id="OLD-1", symbol="RXG", transaction_type=OrderSide.SELL, quantity=1, filled_quantity=filled,
                             order_type="MARKET", status=status, average_price=price)


def _live_trade(t, *, symbol="RXG", exit_order_id="OLD-1", sl_order_id=None, direction="LONG", role=None, group=None) -> int:
    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="LIVE", strategy_id=STRATEGY, symbol=symbol,
                                exchange="NFO", direction=direction, entry_price=100.0, quantity=1, stop_loss=98.0, target1=104.0,
                                target2=108.0, exit_order_id=exit_order_id, sl_order_id=sl_order_id, leg_role=role, leg_group_id=group,
                                instrument_kind="OPTION" if group else "UNDERLYING", entry_time=OPEN_NOW - timedelta(minutes=30))
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


def _close(trade_id, broker, user_id):
    async def go():
        async with _session_factory() as session:
            trade = await session.get(TradeRecord, trade_id)
            return await pm.close_position(session, trade, 97.0, "Stop Loss", broker=broker, user_id=user_id)
    return _run(go())


def _get(model, id_):
    async def go():
        async with _session_factory() as session:
            return await session.get(model, id_)
    return _run(go())


def _audit(tenant_id, event):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(AuditLogRecord.detail).where(AuditLogRecord.tenant_id == tenant_id,
                                                                                  AuditLogRecord.event == event)))
    return _run(go())


@pytest.fixture(autouse=True)
def _no_poll_wait(monkeypatch):
    monkeypatch.setattr(pm, "FILL_POLL_DELAY_SECONDS", 0)


def test_an_earlier_exit_that_filled_is_booked_at_its_fill_and_nothing_new_is_sent():
    t = _tenant(f"rxg-filled-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t)
    broker = _Book()
    broker.orders["OLD-1"] = _old("COMPLETE", filled=1, price=96.4)
    outcome = _close(trade_id, broker, t["user_id"])
    row = _get(TradeRecord, trade_id)
    assert outcome.closed and broker.placed == []                           # no second exit
    assert row.exit_price == 96.4 and row.exit_order_id == "OLD-1"
    assert _audit(t["tenant_id"], "exit_order_replaced") == []


def test_an_earlier_exit_still_working_is_waited_for():
    t = _tenant(f"rxg-working-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t)
    broker = _Book()
    broker.orders["OLD-1"] = _old("OPEN")
    outcome = _close(trade_id, broker, t["user_id"])
    assert not outcome.closed and broker.placed == [] and any("still working" in w for w in outcome.warnings)
    row = _get(TradeRecord, trade_id)
    assert row.exit_time is None and row.exit_order_id == "OLD-1"
    assert broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is None


@pytest.mark.parametrize("status", ["REJECTED", "CANCELLED"])
def test_an_earlier_exit_that_died_unfilled_is_replaced_and_both_ids_are_kept(status):
    t = _tenant(f"rxg-dead-{status.lower()}-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, sl_order_id="SL-CANCELLED")                  # its stop was cancelled by that earlier attempt
    broker = _Book()
    broker.orders["OLD-1"] = _old(status)
    broker.orders["SL-CANCELLED"] = BrokerOrderStatus(order_id="SL-CANCELLED", symbol="RXG", transaction_type=OrderSide.SELL,
                                                      quantity=1, order_type="SL-M", status="CANCELLED")
    outcome = _close(trade_id, broker, t["user_id"])
    assert outcome.closed and [o.order_type for o in broker.placed] == ["MARKET"]   # one new exit; the dead stop is not re-cancelled
    row = _get(TradeRecord, trade_id)
    assert row.exit_order_id == "NEW-1"
    replaced = _audit(t["tenant_id"], "exit_order_replaced")
    assert len(replaced) == 1 and "OLD-1" in replaced[0] and "NEW-1" in replaced[0] and status in replaced[0]
    assert any("NEW-1" in d for d in _audit(t["tenant_id"], "algo_order"))


@pytest.mark.parametrize("case", ["not_in_the_book", "part_filled_then_cancelled"])
def test_an_unknown_earlier_exit_flags_the_tenant_and_sends_nothing(case):
    t = _tenant(f"rxg-unknown-{case[:8]}-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t)
    broker = _Book()
    if case == "part_filled_then_cancelled":
        broker.orders["OLD-1"] = _old("CANCELLED", filled=0.5, price=96.0)
    outcome = _close(trade_id, broker, t["user_id"])
    assert not outcome.closed and broker.placed == []
    assert _get(TradeRecord, trade_id).exit_time is None
    assert broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is not None   # reconciliation decides; entries pause


def test_the_stop_guard_does_not_rearm_while_an_exit_is_pending_and_does_once_it_died():
    t = _tenant(f"rxg-guard-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, sl_order_id="SL-GONE")                        # the stop is gone from the book

    async def guard(broker):
        async with _session_factory() as session:
            tenant = await session.get(Tenant, t["tenant_id"])
            return await stop_guard.verify_protective_stops(session, tenant, broker, user_id=t["user_id"])
    working = _Book()
    working.orders["OLD-1"] = _old("OPEN")
    counts = _run(guard(working))
    assert counts.get("exit_pending") == 1 and counts["rearmed"] == 0 and working.placed == []
    dead = _Book()
    dead.orders["OLD-1"] = _old("REJECTED")
    counts = _run(guard(dead))
    assert counts["rearmed"] == 1 and [o.order_type for o in dead.placed] in (["SL-M"], ["SL"])
    assert _get(TradeRecord, trade_id).sl_order_id == "NEW-1"


# --- naked-short guard ---------------------------------------------------------------------------------------------------

def test_a_short_leg_that_does_not_close_keeps_the_wings_open_and_is_retried_first():
    t = _tenant(f"rxg-group-{uuid.uuid4().hex[:6]}@example.com")
    group = f"g-{uuid.uuid4().hex[:8]}"
    short = _live_trade(t, symbol="RXGSHORT", exit_order_id=None, direction="SHORT", role="SHORT", group=group)
    wing = _live_trade(t, symbol="RXGWING", exit_order_id=None, direction="LONG", role="LONG", group=group)

    async def close_group(broker):
        async with _session_factory() as session:
            legs = [await session.get(TradeRecord, short), await session.get(TradeRecord, wing)]
            return await pm.close_group(session, legs, {short: 50.0, wing: 10.0}, "Group stop", broker=broker, user_id=t["user_id"])
    refusing = _Book(reject={"RXGSHORT"})
    outcomes = {o.trade_id: o for o in _run(close_group(refusing))}
    assert not outcomes[short].closed and not outcomes[wing].closed
    assert [o.symbol for o in refusing.placed] == ["RXGSHORT"]              # the wing exit was never sent
    assert "naked short" in outcomes[wing].warnings[0]
    assert _get(TradeRecord, wing).exit_time is None and _get(TradeRecord, short).exit_time is None
    held = _audit(t["tenant_id"], "group_wings_held")
    assert len(held) == 1 and f"#{short}" in held[0]

    async def critical():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(NotificationRecord.tenant_id == t["tenant_id"],
                                                                              NotificationRecord.related_trade_id == short)))
    assert any(n.severity == "CRITICAL" and "Short leg not closed" in n.title for n in _run(critical()))

    accepting = _Book()                                                      # the next pass: short first, then the wing
    outcomes = {o.trade_id: o for o in _run(close_group(accepting))}
    assert outcomes[short].closed and outcomes[wing].closed
    assert [o.symbol for o in accepting.placed] == ["RXGSHORT", "RXGWING"]
