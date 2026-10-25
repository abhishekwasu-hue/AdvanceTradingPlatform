"""Exit safety before any LIVE work (ADR-0004).

* Re-exit guard: a LIVE position that already sent an exit (its id was committed when the broker took it, the booking did
  not happen) never gets a second exit on top of it. The earlier order decides: filled -> booked at its fill; still
  working -> wait; rejected / cancelled with nothing filled -> a new exit (both ids on the audit trail); ended after a part
  fill -> an exit for exactly the rest, both fills blended (HIGH-2); not in the book -> a fresh exit once it is over (sent on
  an earlier trading day, or a clean reconciliation since shows the position open), else the tenant is flagged so
  reconciliation runs (HIGH-1). The stop guard does not re-arm a stop on a position whose exit is pending.
* Claim (MED-2): an exit is claimed before it is sent and its broker id committed before the fill is polled; a second
  closer finds the claim and sends nothing; a clear refusal releases it; an unclear error keeps it and flags the tenant.
* An unreadable order book is "wait this cycle", never "the order is gone" (MED-3).
* Naked-short guard: when a structure's SHORT leg does not close, its wings stay open (recorded + CRITICAL notification,
  with a cooldown); the next pass tries the short first again - in the monitor, the intraday square-off and the
  emergency exit (MED-1)."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.brokers.exceptions import BrokerAPIError, BrokerOrderRejected
from app.brokers.models import BrokerOrderResponse, BrokerOrderStatus, BrokerPosition
from app.core.enums import OrderSide
from app.db.models import AuditLogRecord, NotificationRecord, Tenant, TradeRecord
from app.market_data.calendar import to_ist
from app.reconciliation.service import broker_uncertain_reason, run_reconciliation
from app.trading import position_monitor as pm
from app.trading import stop_guard, stop_state
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import OPEN_NOW, STRATEGY, _FakeBroker, _tenant


def _run(coro):
    return asyncio.run(coro)


class _Book(_FakeBroker):
    """An order book the test controls: `orders` by id; a placed MARKET order fills at the LTP unless `reject` names its
    symbol; a placed stop stays TRIGGER PENDING."""

    def __init__(self, ltp=97.0, reject=(), positions=(), book_error=None):
        super().__init__(ltp=ltp)
        self.orders = {}
        self.reject = set(reject)
        self.positions = list(positions)
        self.book_error = book_error

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
        if self.book_error is not None:
            raise self.book_error
        return list(self.orders.values())

    async def get_positions(self):
        return list(self.positions)


def _old(status, filled=0.0, price=None, quantity=1):
    return BrokerOrderStatus(order_id="OLD-1", symbol="RXG", transaction_type=OrderSide.SELL, quantity=quantity, filled_quantity=filled,
                             order_type="MARKET", status=status, average_price=price)


def _lookalike(order_id, status, *, tag=None, placed_at=None, product=None, exchange=None, quantity=1, filled=None, price=None):
    """A SELL MARKET on RXG that looks like the position's exit: whether it is one is down to its tag / product /
    exchange and when the broker took it."""
    filled = (quantity if status == "COMPLETE" else 0) if filled is None else filled
    return BrokerOrderStatus(order_id=order_id, symbol="RXG", transaction_type=OrderSide.SELL, quantity=quantity, filled_quantity=filled,
                             order_type="MARKET", status=status, average_price=price if price is not None else (96.0 if filled else None),
                             tag=tag, placed_at=placed_at, product=product, exchange=exchange)


def _exit_tag(broker):
    """The tag this platform's exits of a STRATEGY position carry at `broker` (no algo id registered)."""
    from types import SimpleNamespace
    from app.compliance.algo_id import order_algo_id
    return pm.expected_exit_tag(SimpleNamespace(strategy_id=STRATEGY), broker, order_algo_id(None, broker.name))


# A fixed clock early in today's IST trading day: "sent" at T0, judged a few minutes later on the same day (a real
# reconciliation, stamped with the wall clock, comes after T0).
T0 = to_ist(datetime.now(timezone.utc)).replace(hour=0, minute=1, second=0, microsecond=0).astimezone(timezone.utc)


def _live_trade(t, *, symbol="RXG", exit_order_id="OLD-1", sl_order_id=None, direction="LONG", role=None, group=None,
                exit_sent_at=None, quantity=1) -> int:
    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="LIVE", strategy_id=STRATEGY, symbol=symbol,
                                exchange="NFO", direction=direction, entry_price=100.0, quantity=quantity, stop_loss=98.0, target1=104.0,
                                target2=108.0, exit_order_id=exit_order_id, sl_order_id=sl_order_id, leg_role=role, leg_group_id=group,
                                instrument_kind="OPTION" if group else "UNDERLYING", entry_time=OPEN_NOW - timedelta(minutes=30),
                                exit_sent_at=exit_sent_at)
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


def _close(trade_id, broker, user_id, *, now=None, resume_only=False):
    async def go():
        async with _session_factory() as session:
            trade = await session.get(TradeRecord, trade_id)
            return await pm.close_position(session, trade, 97.0, "Stop Loss", broker=broker, user_id=user_id, now=now,
                                           resume_only=resume_only)
    return _run(go())


def _guard(t, broker):
    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, t["tenant_id"])
            return await stop_guard.verify_protective_stops(session, tenant, broker, user_id=t["user_id"])
    return _run(go())


def _reconcile(t, broker):
    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, t["tenant_id"])
            return await run_reconciliation(session, tenant, "upstox", broker, user_id=t["user_id"], source="test")
    return _run(go())


def _notifications(tenant_id, trade_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(NotificationRecord.tenant_id == tenant_id,
                                                                              NotificationRecord.related_trade_id == trade_id)))
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
    stop_state.wings_held_alert.clear()


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


def test_an_earlier_exit_not_in_the_book_flags_the_tenant_and_sends_nothing_while_it_may_be_alive():
    t = _tenant(f"rxg-unknown-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_sent_at=T0)
    broker = _Book()
    outcome = _close(trade_id, broker, t["user_id"], now=T0 + timedelta(minutes=5))
    assert not outcome.closed and broker.placed == []
    assert _get(TradeRecord, trade_id).exit_time is None
    assert broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is not None   # reconciliation decides; entries pause


def test_the_stop_guard_does_not_rearm_while_an_exit_is_pending_and_does_once_it_died():
    t = _tenant(f"rxg-guard-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, sl_order_id="SL-GONE")                        # the stop is gone from the book
    working = _Book()
    working.orders["OLD-1"] = _old("OPEN")
    counts = _guard(t, working)
    assert counts.get("exit_pending") == 1 and counts["rearmed"] == 0 and working.placed == []
    part = _Book()
    part.orders["OLD-1"] = _old("CANCELLED", filled=0.5, price=96.0)     # the monitor sends its rest - no stop for the whole
    counts = _guard(t, part)
    assert counts.get("exit_pending") == 1 and part.placed == []
    dead = _Book()
    dead.orders["OLD-1"] = _old("REJECTED")
    counts = _guard(t, dead)
    assert counts["rearmed"] == 1 and [o.order_type for o in dead.placed] in (["SL-M"], ["SL"])
    assert _get(TradeRecord, trade_id).sl_order_id == "NEW-1"


# --- HIGH-1: an exit order gone from the book is not a block forever ---------------------------------------------------

def _open_at_broker(quantity=1, symbol="RXG"):
    return [BrokerPosition(symbol=symbol, exchange="NFO", quantity=quantity, average_price=100.0)]


def test_a_swing_exit_cancelled_at_the_close_is_replaced_the_next_trading_day_after_reconciliation():
    """A SWING / NRML exit sent yesterday, not booked, cancelled by the exchange at the close: today's book does not list
    it. "Sent yesterday" alone proves nothing (a filled day order is just as absent): the tenant is flagged, reconciliation
    finds the position still open with nothing working on it, and the next check sends a fresh exit (the old id is
    cleared on the audit trail)."""
    t = _tenant(f"rxg-swing-{uuid.uuid4().hex[:6]}@example.com")
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    trade_id = _live_trade(t, exit_sent_at=yesterday, sl_order_id="SL-OLD")
    broker = _Book(positions=_open_at_broker())                              # nothing of yesterday's is in today's book
    first = _close(trade_id, broker, t["user_id"])
    assert not first.closed and broker.placed == [] and first.exit_waiting
    assert broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is not None
    assert _reconcile(t, broker).mismatched_count == 0
    outcome = _close(trade_id, broker, t["user_id"])
    assert outcome.closed and [o.order_type for o in broker.placed] == ["MARKET"]
    row = _get(TradeRecord, trade_id)
    assert row.exit_order_id == "NEW-1" and row.exit_price == 97.0
    cleared = _audit(t["tenant_id"], "exit_order_cleared")
    assert len(cleared) == 1 and "OLD-1" in cleared[0] and "still open at the broker" in cleared[0]
    replaced = _audit(t["tenant_id"], "exit_order_replaced")
    assert len(replaced) == 1 and "OLD-1" in replaced[0] and "NEW-1" in replaced[0]


def test_a_day_exit_that_filled_yesterday_never_gets_a_second_exit_or_a_stop():
    """Review P1: the exit filled near the close, the booking failed after its id was recorded; this morning it is absent
    from the book and the account is flat. No fresh exit (it would open a naked short the other way), no stop."""
    t = _tenant(f"rxg-filled-yday-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_sent_at=datetime.now(timezone.utc) - timedelta(days=1), sl_order_id="SL-GONE")
    broker = _Book(positions=[])                                             # flat at the broker
    assert not _close(trade_id, broker, t["user_id"]).closed
    assert _guard(t, broker).get("exit_pending") == 1
    assert _reconcile(t, broker).mismatched_count == 1                       # the reconciliation does not prove it open
    for _ in range(3):
        assert not _close(trade_id, broker, t["user_id"]).closed
    assert _guard(t, broker).get("exit_pending") == 1
    assert broker.placed == [] and _get(TradeRecord, trade_id).exit_time is None
    assert broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is not None


def test_the_stop_guard_rearms_a_stop_once_a_vanished_exit_is_known_to_be_over():
    t = _tenant(f"rxg-swing-guard-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_sent_at=datetime.now(timezone.utc) - timedelta(days=1), sl_order_id="SL-GONE")
    broker = _Book(positions=_open_at_broker())
    counts = _guard(t, broker)
    assert counts["rearmed"] == 0 and counts.get("exit_pending") == 1 and broker.placed == []
    _reconcile(t, broker)
    counts = _guard(t, broker)
    assert counts["rearmed"] == 1 and counts.get("exit_pending", 0) == 0
    assert _get(TradeRecord, trade_id).sl_order_id == "NEW-1"


def test_a_mismatch_on_another_symbol_does_not_park_this_exit_and_the_wait_is_alerted():
    """Review item 4: the tenant stays broker-uncertain for an unrelated symbol. This position's own reconciliation
    result decides; while it waits the "exit not completed" alert counts the checks."""
    t = _tenant(f"rxg-other-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_sent_at=T0)
    broker = _Book(positions=_open_at_broker() + _open_at_broker(5, symbol="STRAY"))   # STRAY: a mismatch, for good
    for i in range(pm.EXIT_WAIT_ALERT_CHECKS):
        outcome = _close(trade_id, broker, t["user_id"], now=T0 + timedelta(minutes=5 + i))
        assert not outcome.closed and outcome.exit_waiting
    assert any("Exit not completed" in n.title for n in _notifications(t["tenant_id"], trade_id))
    assert _reconcile(t, broker).mismatched_count == 1
    assert broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is not None   # still flagged for STRAY
    outcome = _close(trade_id, broker, t["user_id"], now=T0 + timedelta(minutes=20))
    assert outcome.closed and [o.order_type for o in broker.placed] == ["MARKET"]


def test_a_vanished_exit_flags_then_a_reconciliation_lets_a_fresh_exit_out():
    """The loop the review found: flag -> reconciliation clean (clears the flag) -> flag again, forever. Now the clean
    reconciliation after the exit was sent is the proof the position is still open: the next check sends a fresh exit."""
    t = _tenant(f"rxg-recon-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_sent_at=T0, sl_order_id="SL-GONE")
    broker = _Book(positions=[BrokerPosition(symbol="RXG", exchange="NFO", quantity=1, average_price=100.0)])
    first = _close(trade_id, broker, t["user_id"], now=T0 + timedelta(minutes=5))
    assert not first.closed and broker.placed == [] and broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is not None
    assert _guard(t, broker).get("exit_pending") == 1 and broker.placed == []   # no stop next to an exit that may live
    report = _reconcile(t, broker)
    assert report.mismatched_count == 0 and broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is None
    second = _close(trade_id, broker, t["user_id"], now=T0 + timedelta(minutes=10))
    assert second.closed and [o.order_type for o in broker.placed] == ["MARKET"]
    assert broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is None
    assert any("still open at the broker" in d for d in _audit(t["tenant_id"], "exit_order_cleared"))


def test_a_vanished_exit_is_never_replaced_while_reconciliation_disagrees():
    """Flat at the broker (the exit filled, the booking was lost, the book rolled over): reconciliation finds the
    mismatch and the flag stays - a second exit would open a position the other way."""
    t = _tenant(f"rxg-flat-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_sent_at=datetime.now(timezone.utc) - timedelta(days=1))
    broker = _Book(positions=[])
    report = _reconcile(t, broker)
    assert report.mismatched_count == 1
    outcome = _close(trade_id, broker, t["user_id"])
    assert not outcome.closed and broker.placed == []


# --- HIGH-2: the rest of a part-filled exit goes out ---------------------------------------------------------------------

def test_an_earlier_exit_cancelled_after_a_part_fill_sends_exactly_the_rest_and_blends_the_fills():
    t = _tenant(f"rxg-part-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, quantity=4)
    broker = _Book(ltp=95.0)
    broker.orders["OLD-1"] = _old("CANCELLED", filled=1, price=96.0, quantity=4)
    outcome = _close(trade_id, broker, t["user_id"])
    assert outcome.closed and [(o.order_type, float(o.quantity)) for o in broker.placed] == [("MARKET", 3.0)]
    row = _get(TradeRecord, trade_id)
    assert row.exit_price == pytest.approx((1 * 96.0 + 3 * 95.0) / 4) and row.exit_order_id == "NEW-1"
    replaced = _audit(t["tenant_id"], "exit_order_replaced")
    assert len(replaced) == 1 and "after filling 1" in replaced[0]
    assert broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is None
    assert row.exit_filled_quantity == 1 and row.exit_filled_price == 96.0     # what filled before the recorded exit


class _PartFills(_Book):
    """Each MARKET exit fills `fills` units at the LTP and is then cancelled (an illiquid strike), one by one."""

    def __init__(self, fills, **kw):
        super().__init__(**kw)
        self.fills = list(fills)

    async def place_order(self, order):
        self.placed.append(order)
        oid = f"NEW-{len(self.placed)}"
        filled = self.fills.pop(0) if self.fills else order.quantity
        self.orders[oid] = BrokerOrderStatus(order_id=oid, symbol=order.symbol, transaction_type=order.transaction_type,
                                             quantity=order.quantity, filled_quantity=filled, order_type=order.order_type,
                                             status="COMPLETE" if filled >= order.quantity else "CANCELLED",
                                             average_price=self.ltp if filled else None)
        return BrokerOrderResponse(order_id=oid, status="OPEN")


def test_a_third_attempt_blends_every_earlier_fill_at_its_own_price():
    """Review item 6: 4 to exit; the first exit fills 1 @96 and dies, the second (3) fills 1 @95 and dies, the third (2)
    fills @94. The booking is (96 + 95 + 2 x 94) / 4 - not the second attempt's gap at the stop price."""
    t = _tenant(f"rxg-third-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, quantity=4)
    broker = _PartFills(fills=[1], ltp=95.0)
    broker.orders["OLD-1"] = _old("CANCELLED", filled=1, price=96.0, quantity=4)
    assert not _close(trade_id, broker, t["user_id"]).closed                  # NEW-1: 3 sent, 1 filled @95, cancelled
    assert [float(o.quantity) for o in broker.placed] == [3.0]
    broker.ltp = 94.0
    outcome = _close(trade_id, broker, t["user_id"])                          # NEW-2: the last 2
    assert outcome.closed and [float(o.quantity) for o in broker.placed] == [3.0, 2.0]
    assert _get(TradeRecord, trade_id).exit_price == pytest.approx((96.0 + 95.0 + 2 * 94.0) / 4)


def test_the_monitor_finishes_a_part_filled_exit_even_when_its_rule_no_longer_fires():
    """The price came back inside the stop / target: the rest still goes out (the exit was decided and part done)."""
    t = _tenant(f"rxg-part-monitor-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, quantity=2)
    broker = _Book(ltp=101.0)
    broker.orders["OLD-1"] = _old("REJECTED", filled=1, price=97.0, quantity=2)

    async def price(symbol, exchange):
        return 101.0                                                         # between the stop (98) and the target (104)

    async def go():
        async with _session_factory() as session:
            return await pm.monitor_open_positions(session, t["tenant_id"], price, broker=broker, user_id=t["user_id"])
    outcomes = _run(go())
    assert any(o.trade_id == trade_id and o.closed for o in outcomes)
    assert [float(o.quantity) for o in broker.placed] == [1.0]
    row = _get(TradeRecord, trade_id)
    assert row.exit_price == pytest.approx(99.0) and row.exit_reason == pm.RESUMED_EXIT_REASON


def test_the_monitor_books_an_earlier_exit_that_filled_without_its_rule_firing_again():
    t = _tenant(f"rxg-filled-monitor-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t)
    broker = _Book()
    broker.orders["OLD-1"] = _old("COMPLETE", filled=1, price=97.5)

    async def price(symbol, exchange):
        return 101.0

    async def go():
        async with _session_factory() as session:
            return await pm.monitor_open_positions(session, t["tenant_id"], price, broker=broker, user_id=t["user_id"])
    _run(go())
    row = _get(TradeRecord, trade_id)
    assert row.exit_time is not None and row.exit_price == 97.5 and broker.placed == []


def test_resuming_never_sends_a_whole_new_exit():
    t = _tenant(f"rxg-resume-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t)
    broker = _Book()
    broker.orders["OLD-1"] = _old("REJECTED")
    outcome = _close(trade_id, broker, t["user_id"], resume_only=True)
    assert not outcome.closed and broker.placed == [] and _get(TradeRecord, trade_id).exit_order_id == "OLD-1"


# --- MED-2: claim before sending -----------------------------------------------------------------------------------------

class _Gated(_Book):
    """place_order waits for the test to let it through (an exit being placed right now)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()

    async def place_order(self, order):
        self.entered.set()
        await self.gate.wait()
        return await super().place_order(order)


def test_a_second_closer_finds_the_claim_and_sends_nothing():
    t = _tenant(f"rxg-claim-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_order_id=None)
    broker = _Gated()

    async def go():
        async with _session_factory() as first_session:
            first_trade = await first_session.get(TradeRecord, trade_id)
            async with _session_factory() as second_session:
                second_trade = await second_session.get(TradeRecord, trade_id)     # read before the claim
                first = asyncio.create_task(pm.close_position(first_session, first_trade, 97.0, "Stop Loss", broker=broker,
                                                              user_id=t["user_id"]))
                await broker.entered.wait()                                          # the first exit is being placed
                second = await pm.close_position(second_session, second_trade, 97.0, "Target", broker=broker,
                                                 user_id=t["user_id"])
                await second_session.rollback()
            broker.gate.set()
            return await first, second
    first, second = _run(go())
    assert first.closed and not second.closed and any("claimed it first" in w for w in second.warnings)
    assert len(broker.placed) == 1
    assert broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is None


def test_a_claim_being_placed_right_now_is_waited_for_without_a_flag():
    t = _tenant(f"rxg-inflight-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_order_id=pm.EXIT_PENDING, exit_sent_at=T0)
    broker = _Book()
    outcome = _close(trade_id, broker, t["user_id"], now=T0 + timedelta(seconds=30))
    assert not outcome.closed and broker.placed == [] and outcome.exit_waiting
    assert broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is None


def test_a_stale_claim_flags_and_a_clean_reconciliation_releases_a_fresh_exit():
    t = _tenant(f"rxg-stale-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_order_id=pm.EXIT_PENDING, exit_sent_at=T0)
    broker = _Book(positions=[BrokerPosition(symbol="RXG", exchange="NFO", quantity=1, average_price=100.0)])
    first = _close(trade_id, broker, t["user_id"], now=T0 + timedelta(minutes=10))
    assert not first.closed and broker.placed == [] and broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is not None
    _reconcile(t, broker)
    second = _close(trade_id, broker, t["user_id"], now=T0 + timedelta(minutes=15))
    assert second.closed and len(broker.placed) == 1 and _get(TradeRecord, trade_id).exit_order_id == "NEW-1"


def test_the_broker_id_is_committed_before_the_fill_is_polled(monkeypatch):
    t = _tenant(f"rxg-commit-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_order_id=None)

    async def crash(*args, **kwargs):
        raise RuntimeError("worker died while polling the fill")
    monkeypatch.setattr(pm, "_exit_fill", crash)
    with pytest.raises(RuntimeError):
        _close(trade_id, _Book(), t["user_id"])
    row = _get(TradeRecord, trade_id)
    assert row.exit_order_id == "NEW-1" and row.exit_sent_at is not None and row.exit_time is None
    assert any("NEW-1" in d for d in _audit(t["tenant_id"], "algo_order"))


def test_an_unclear_send_that_reached_the_broker_is_adopted_never_sent_twice():
    """Review P2: the send times out but the order reached the broker and stays OPEN (circuit lock). Reconciliation
    compares positions only - with that order working it proves nothing - and once the claim is stale the order in
    the book is adopted as the exit: one order, ever."""
    t = _tenant(f"rxg-unclear-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_order_id=None)

    class _Lost(_Book):
        async def place_order(self, order):
            self.placed.append(order)
            self.orders["LOST-1"] = BrokerOrderStatus(order_id="LOST-1", symbol=order.symbol, transaction_type=order.transaction_type,
                                                      quantity=order.quantity, order_type="MARKET", status="OPEN", tag=order.tag,
                                                      placed_at=datetime.now(timezone.utc))
            raise TimeoutError("read timed out")
    broker = _Lost(positions=_open_at_broker())
    first = _close(trade_id, broker, t["user_id"])
    assert not first.closed and _get(TradeRecord, trade_id).exit_order_id == pm.EXIT_PENDING
    _reconcile(t, broker)                                                     # position still open, but LOST-1 is working
    assert _get(TradeRecord, trade_id).reconciled_open_at is None
    later = datetime.now(timezone.utc) + timedelta(seconds=pm.CLAIM_INFLIGHT_SECONDS + 60)
    second = _close(trade_id, broker, t["user_id"], now=later)
    assert not second.closed and second.exit_waiting and len(broker.placed) == 1
    assert _get(TradeRecord, trade_id).exit_order_id == "LOST-1"
    assert any("LOST-1" in d for d in _audit(t["tenant_id"], "exit_order_adopted"))
    broker.orders["LOST-1"] = broker.orders["LOST-1"].model_copy(update={"status": "COMPLETE", "filled_quantity": 1,
                                                                         "average_price": 96.5})
    third = _close(trade_id, broker, t["user_id"], now=later)
    assert third.closed and len(broker.placed) == 1 and _get(TradeRecord, trade_id).exit_price == 96.5


def test_the_stop_guard_does_not_rearm_next_to_an_adoptable_exit():
    t = _tenant(f"rxg-unclear-guard-{uuid.uuid4().hex[:6]}@example.com")
    sent = datetime.now(timezone.utc) - timedelta(minutes=10)
    trade_id = _live_trade(t, exit_order_id=pm.EXIT_PENDING, exit_sent_at=sent, sl_order_id="SL-GONE")
    broker = _Book(positions=_open_at_broker())
    broker.orders["LOST-1"] = _lookalike("LOST-1", "OPEN", tag=_exit_tag(broker), placed_at=sent + timedelta(seconds=2))

    async def stamp():                                                        # even with a (stale) proof on record
        async with _session_factory() as session:
            row = await session.get(TradeRecord, trade_id)
            row.reconciled_open_at = datetime.now(timezone.utc)
            await session.commit()
    _run(stamp())
    counts = _guard(t, broker)
    assert counts.get("exit_pending") == 1 and broker.placed == []


def test_a_failing_audit_write_after_the_send_never_releases_the_claim(monkeypatch):
    """Review P3: the broker took the exit, then writing its audit row raised. The id is already committed: the claim is
    not released and the next pass books that order instead of sending a second one."""
    t = _tenant(f"rxg-audit-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_order_id=None)
    broker = _Book()

    async def broken(*args, **kwargs):
        raise RuntimeError("audit chain unavailable")
    monkeypatch.setattr(pm, "audit_order", broken)
    with pytest.raises(RuntimeError):
        _close(trade_id, broker, t["user_id"])
    assert _get(TradeRecord, trade_id).exit_order_id == "NEW-1"
    monkeypatch.undo()
    monkeypatch.setattr(pm, "FILL_POLL_DELAY_SECONDS", 0)
    outcome = _close(trade_id, broker, t["user_id"])
    assert outcome.closed and len(broker.placed) == 1


@pytest.mark.parametrize("error, clear", [(BrokerOrderRejected("RMS: margin"), True),
                                          (BrokerAPIError("gateway timeout", status_code=504), False),
                                          (TimeoutError("read timed out"), False)])
def test_a_failed_send_releases_the_claim_only_when_the_broker_clearly_refused(monkeypatch, error, clear):
    from app.core import config
    monkeypatch.setattr(config, "LIVE_UPSTOX_OPTION_STOP_LIMIT", False)
    monkeypatch.setattr(config, "LIVE_EXIT_IF_NO_STOP", False)                 # whatever the stop switches
    t = _tenant(f"rxg-fail-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_order_id=None)

    class _Failing(_Book):
        async def place_order(self, order):
            self.placed.append(order)
            raise error
    outcome = _close(trade_id, _Failing(), t["user_id"])
    assert not outcome.closed
    row = _get(TradeRecord, trade_id)
    if clear:
        assert row.exit_order_id is None and row.exit_sent_at is None                 # released: the next exit is not blocked
        assert broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is None
    else:
        assert row.exit_order_id == pm.EXIT_PENDING                                   # it may stand at the broker
        assert broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is not None


def test_a_synchronous_rejection_releases_the_claim():
    t = _tenant(f"rxg-rej-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_order_id=None)
    outcome = _close(trade_id, _Book(reject={"RXG"}), t["user_id"])
    assert not outcome.closed and _get(TradeRecord, trade_id).exit_order_id is None


# --- MED-3: an unreadable book is not a missing order ----------------------------------------------------------------------

def test_an_unreadable_order_book_waits_this_cycle_without_flagging_the_tenant():
    t = _tenant(f"rxg-book-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_sent_at=T0)
    broker = _Book(book_error=ConnectionError("order book: 503"))
    outcome = _close(trade_id, broker, t["user_id"], now=T0 + timedelta(minutes=5))
    assert not outcome.closed and broker.placed == [] and outcome.exit_waiting
    assert any("could not be read" in w for w in outcome.warnings)
    assert broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is None      # other positions' no-stop exits stay possible
    assert _get(TradeRecord, trade_id).exit_order_id == "OLD-1"


# --- LOWs -----------------------------------------------------------------------------------------------------------------

def test_an_exit_still_working_check_after_check_is_raised_once():
    t = _tenant(f"rxg-wait-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t)
    broker = _Book()
    broker.orders["OLD-1"] = _old("OPEN")
    for _ in range(pm.EXIT_WAIT_ALERT_CHECKS + 2):
        assert not _close(trade_id, broker, t["user_id"]).closed
    alerts = [n for n in _notifications(t["tenant_id"], trade_id) if "Exit not completed" in n.title]
    assert len(alerts) == 1 and alerts[0].severity == "CRITICAL" and broker.placed == []
    broker.orders["OLD-1"] = _old("COMPLETE", filled=1, price=96.0)
    assert _close(trade_id, broker, t["user_id"]).closed and trade_id not in stop_state.exit_waits


def test_an_earlier_exit_and_its_stop_both_filled_is_booked_and_flagged():
    t = _tenant(f"rxg-double-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, sl_order_id="SL-1")
    broker = _Book()
    broker.orders["OLD-1"] = _old("COMPLETE", filled=1, price=96.4)
    broker.orders["SL-1"] = BrokerOrderStatus(order_id="SL-1", symbol="RXG", transaction_type=OrderSide.SELL, quantity=1,
                                              filled_quantity=1, order_type="SL-M", status="COMPLETE", average_price=97.9)
    outcome = _close(trade_id, broker, t["user_id"])
    assert outcome.closed and broker.placed == [] and _get(TradeRecord, trade_id).exit_price == 96.4
    assert "both filled" in (broker_uncertain_reason(_get(Tenant, t["tenant_id"])) or "")


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


def test_wings_held_is_recorded_and_raised_once_per_cooldown_not_every_cycle():
    t = _tenant(f"rxg-cool-{uuid.uuid4().hex[:6]}@example.com")
    group = f"g-{uuid.uuid4().hex[:8]}"
    short = _live_trade(t, symbol="RXCSHORT", exit_order_id=None, direction="SHORT", role="SHORT", group=group)
    wing = _live_trade(t, symbol="RXCWING", exit_order_id=None, direction="LONG", role="LONG", group=group)

    async def close_group(broker):
        async with _session_factory() as session:
            legs = [await session.get(TradeRecord, short), await session.get(TradeRecord, wing)]
            return await pm.close_group(session, legs, {short: 50.0, wing: 10.0}, "Group stop", broker=broker, user_id=t["user_id"])
    for _ in range(3):
        _run(close_group(_Book(reject={"RXCSHORT"})))
    assert len(_audit(t["tenant_id"], "group_wings_held")) == 1
    assert len([n for n in _notifications(t["tenant_id"], short) if "Short leg not closed" in n.title]) == 1


# --- MED-1: every path that closes a structure holds the wings ------------------------------------------------------------

def _structure(t, prefix):
    group = f"g-{uuid.uuid4().hex[:8]}"
    wing = _live_trade(t, symbol=f"{prefix}WING", exit_order_id=None, direction="LONG", role="LONG", group=group)   # first in DB order
    short = _live_trade(t, symbol=f"{prefix}SHORT", exit_order_id=None, direction="SHORT", role="SHORT", group=group)
    return short, wing


def test_the_intraday_square_off_never_sells_the_wings_of_a_short_that_did_not_close():
    from app.workers.trading_worker import TradingWorker
    t = _tenant(f"rxg-sq-{uuid.uuid4().hex[:6]}@example.com")
    short, wing = _structure(t, "RXS")
    refusing = _Book(reject={"RXSSHORT"})

    class MD:
        async def get_ltp(self, symbol, exchange="NSE", now=None):
            return 50.0

    async def square_off(broker):
        async with _session_factory() as session:
            worker = TradingWorker(_session_factory, cycle_seconds=60)
            return await worker._square_off_all(session, t["tenant_id"], MD(), broker, t["user_id"])
    assert _run(square_off(refusing)) == 0
    assert [o.symbol for o in refusing.placed] == ["RXSSHORT"]              # the wing (first in DB order) was never sold
    assert _get(TradeRecord, wing).exit_time is None
    accepting = _Book()
    assert _run(square_off(accepting)) == 2
    assert [o.symbol for o in accepting.placed] == ["RXSSHORT", "RXSWING"]


def test_the_emergency_exit_never_sells_the_wings_of_a_short_that_did_not_close(monkeypatch):
    t = _tenant(f"rxg-kill-{uuid.uuid4().hex[:6]}@example.com")
    short, wing = _structure(t, "RXK")
    refusing = _Book(reject={"RXKSHORT"})

    async def the_broker(session, trade):
        return refusing
    monkeypatch.setattr(pm, "broker_for_trade", the_broker)
    response = client.post("/api/kill-switch/emergency-exit", headers=t["headers"],
                           json={"reason": "test", "prices": {"RXKSHORT": 50.0, "RXKWING": 10.0}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["closed_trade_ids"] == []
    assert [o.symbol for o in refusing.placed] == ["RXKSHORT"]
    assert any("RXKWING" in s and "naked short" in s for s in body["skipped_symbols"])
    assert _get(TradeRecord, wing).exit_time is None and _get(TradeRecord, short).exit_time is None


def test_the_emergency_exit_holds_the_wing_of_a_paper_short_that_did_not_close_as_live_does():
    """CX review F1 (owner principle 23 Oct: PAPER and LIVE behave the same): a PAPER short without a supplied price keeps
    its wing open too - never a naked short in paper; both are tried again, the short first. (Before: the hold was for
    LIVE shorts only and the PAPER wing closed.)"""
    t = _tenant(f"rxg-kill-paper-{uuid.uuid4().hex[:6]}@example.com")
    group = f"g-{uuid.uuid4().hex[:8]}"

    async def seed():
        async with _session_factory() as session:
            rows = [TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", strategy_id=STRATEGY, symbol=sym,
                                exchange="NFO", direction=d, entry_price=10.0, quantity=1, stop_loss=5.0, leg_role=role,
                                leg_group_id=group, instrument_kind="OPTION", entry_time=OPEN_NOW)
                    for sym, d, role in (("RXPSHORT", "SHORT", "SHORT"), ("RXPWING", "LONG", "LONG"))]
            session.add_all(rows)
            await session.commit()
    _run(seed())
    body = client.post("/api/kill-switch/emergency-exit", headers=t["headers"],
                       json={"reason": "test", "prices": {"RXPWING": 4.0}}).json()
    assert body["closed_trade_ids"] == [] and body["skipped_symbols"][0] == "RXPSHORT"
    assert body["skipped_symbols"][1].startswith("RXPWING (Held: short leg") and "naked short" in body["skipped_symbols"][1]


def test_an_exit_the_broker_took_and_then_rejected_is_never_booked_as_closed():
    """Found while reproducing item 6: an exit accepted and then rejected by the broker's RMS (nothing filled) was booked
    closed at the reference price while the position stayed open. It stays open, its id recorded, and a new exit goes
    out when the stop fires again."""
    t = _tenant(f"rxg-async-rej-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_order_id=None)
    broker = _PartFills(fills=[0])
    outcome = _close(trade_id, broker, t["user_id"])
    row = _get(TradeRecord, trade_id)
    assert not outcome.closed and row.exit_time is None and row.exit_order_id == "NEW-1"
    assert any("not booked" in w for w in outcome.warnings)
    again = _close(trade_id, broker, t["user_id"])                           # the stop fires again: a new full exit
    assert again.closed and [float(o.quantity) for o in broker.placed] == [1.0, 1.0]


# --- Round 4, finding 1: only this platform's exit, placed inside the claim window, is ever adopted ----------------------

def _stale_claim(t, minutes=10, sl_order_id=None):
    """A claim that never recorded a broker order, `minutes` old (stale), and when it was sent."""
    sent = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=minutes)
    return _live_trade(t, exit_order_id=pm.EXIT_PENDING, exit_sent_at=sent, sl_order_id=sl_order_id), sent


def _naive_ist(moment):
    """How Kite / Upstox spell a time: the IST wall clock, no offset."""
    return to_ist(moment).replace(tzinfo=None)


def _assert_not_adopted(t, trade_id, broker, outcome):
    row = _get(TradeRecord, trade_id)
    assert not outcome.closed and row.exit_time is None and broker.placed == []
    assert row.exit_order_id == pm.EXIT_PENDING and _audit(t["tenant_id"], "exit_order_adopted") == []
    assert outcome.exit_waiting and broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is not None


def test_a_manual_sell_placed_hours_before_with_a_naive_ist_time_is_never_adopted():
    """Review round 3, HIGH: a manual SELL MARKET on the symbol, COMPLETE, placed 2 h before the exit was claimed, its time
    naive IST as Kite / Upstox send it. Read as UTC it looked 3.5 h after the claim and was adopted - the position booked
    closed on someone else's order. Read as IST it is 2 h before the claim window: not this exit."""
    t = _tenant(f"rxg-manual-ist-{uuid.uuid4().hex[:6]}@example.com")
    trade_id, sent = _stale_claim(t)
    broker = _Book(positions=_open_at_broker())
    broker.orders["MAN-1"] = _lookalike("MAN-1", "COMPLETE", placed_at=_naive_ist(sent - timedelta(hours=2)),
                                        product=product_of(trade_id), exchange="NFO")
    outcome = _close(trade_id, broker, t["user_id"])
    _assert_not_adopted(t, trade_id, broker, outcome)


def test_a_lookalike_with_no_time_is_never_adopted():
    """Shoonya sent no order time: the window could not be checked and the order was adopted. Now it never is."""
    t = _tenant(f"rxg-manual-notime-{uuid.uuid4().hex[:6]}@example.com")
    trade_id, sent = _stale_claim(t)
    broker = _Book(positions=_open_at_broker())
    broker.orders["MAN-1"] = _lookalike("MAN-1", "COMPLETE", tag=_exit_tag(broker), placed_at=None)
    outcome = _close(trade_id, broker, t["user_id"])
    _assert_not_adopted(t, trade_id, broker, outcome)


def test_an_untimed_lookalike_still_working_holds_the_exit_until_it_ends_then_the_exit_goes_out():
    """The safe path for an order that may be the exit but cannot be shown to be: never adopted, never sent over while it
    works - even with a reconciliation proof on record - and raised CRITICAL while it waits; once it ends (or is cancelled
    at the broker) the proof lets the exit out (ADR-0004)."""
    t = _tenant(f"rxg-untimed-working-{uuid.uuid4().hex[:6]}@example.com")
    trade_id, sent = _stale_claim(t, sl_order_id="SL-GONE")
    broker = _Book(positions=_open_at_broker())
    broker.orders["MAN-1"] = _lookalike("MAN-1", "OPEN", tag=_exit_tag(broker), placed_at=None)

    async def stamp():                                                        # a reconciliation found the position open
        async with _session_factory() as session:
            row = await session.get(TradeRecord, trade_id)
            row.reconciled_open_at = sent + timedelta(minutes=5)
            await session.commit()
    _run(stamp())
    for _ in range(pm.EXIT_WAIT_ALERT_CHECKS):
        outcome = _close(trade_id, broker, t["user_id"])
        assert not outcome.closed and outcome.exit_waiting and broker.placed == []
        assert any("MAN-1" in w for w in outcome.warnings)
    assert any(n.severity == "CRITICAL" for n in _notifications(t["tenant_id"], trade_id))
    assert _guard(t, broker).get("exit_pending") == 1 and broker.placed == []      # no stop next to it either
    broker.orders["MAN-1"] = broker.orders["MAN-1"].model_copy(update={"status": "CANCELLED"})
    outcome = _close(trade_id, broker, t["user_id"])
    assert outcome.closed and len(broker.placed) == 1 and _get(TradeRecord, trade_id).exit_order_id == "NEW-1"


def test_a_lookalike_on_another_product_is_never_adopted():
    """A manual CNC SELL (no tag) on the symbol of an MIS / NRML position, inside the window: not this exit."""
    t = _tenant(f"rxg-manual-cnc-{uuid.uuid4().hex[:6]}@example.com")
    trade_id, sent = _stale_claim(t)
    broker = _Book(positions=_open_at_broker())
    assert product_of(trade_id) != "CNC"
    broker.orders["MAN-1"] = _lookalike("MAN-1", "COMPLETE", placed_at=sent + timedelta(seconds=2), product="CNC", exchange="NFO")
    outcome = _close(trade_id, broker, t["user_id"])
    _assert_not_adopted(t, trade_id, broker, outcome)


def test_a_lookalike_with_another_tag_is_never_adopted():
    t = _tenant(f"rxg-manual-tag-{uuid.uuid4().hex[:6]}@example.com")
    trade_id, sent = _stale_claim(t)
    broker = _Book(positions=_open_at_broker())
    broker.orders["MAN-1"] = _lookalike("MAN-1", "COMPLETE", tag="MANUAL", placed_at=sent + timedelta(seconds=2))
    outcome = _close(trade_id, broker, t["user_id"])
    _assert_not_adopted(t, trade_id, broker, outcome)


def test_our_exit_placed_after_the_claim_window_is_never_adopted():
    """The window is closed at both ends: an order placed after `exit_sent_at + CLAIM_INFLIGHT_SECONDS` was not this send."""
    t = _tenant(f"rxg-late-{uuid.uuid4().hex[:6]}@example.com")
    trade_id, sent = _stale_claim(t)
    broker = _Book(positions=_open_at_broker())
    late = sent + timedelta(seconds=pm.CLAIM_INFLIGHT_SECONDS + 30)
    broker.orders["MAN-1"] = _lookalike("MAN-1", "COMPLETE", tag=_exit_tag(broker), placed_at=late)
    outcome = _close(trade_id, broker, t["user_id"])
    _assert_not_adopted(t, trade_id, broker, outcome)


def test_our_tagged_exit_inside_the_window_is_adopted_and_booked():
    t = _tenant(f"rxg-ours-{uuid.uuid4().hex[:6]}@example.com")
    trade_id, sent = _stale_claim(t)
    broker = _Book(positions=_open_at_broker())
    broker.orders["LOST-1"] = _lookalike("LOST-1", "COMPLETE", tag=_exit_tag(broker), price=96.5,
                                         placed_at=_naive_ist(sent + timedelta(seconds=1)))      # naive IST, inside the window
    outcome = _close(trade_id, broker, t["user_id"])
    row = _get(TradeRecord, trade_id)
    assert outcome.closed and broker.placed == [] and row.exit_order_id == "LOST-1" and row.exit_price == 96.5
    assert any("LOST-1" in d for d in _audit(t["tenant_id"], "exit_order_adopted"))


def test_an_untagged_order_on_the_positions_product_and_exchange_inside_the_window_is_adopted():
    t = _tenant(f"rxg-ours-untagged-{uuid.uuid4().hex[:6]}@example.com")
    trade_id, sent = _stale_claim(t)
    broker = _Book(positions=_open_at_broker())
    broker.orders["LOST-1"] = _lookalike("LOST-1", "COMPLETE", placed_at=sent - timedelta(seconds=2), product=product_of(trade_id),
                                         exchange="NFO", price=96.5)
    outcome = _close(trade_id, broker, t["user_id"])
    assert outcome.closed and broker.placed == [] and _get(TradeRecord, trade_id).exit_order_id == "LOST-1"


def test_the_stop_guard_never_counts_an_order_another_position_records_as_this_exit():
    """Round 3, LOW: the stop guard used no exclusion set - another position's own exit, on the same symbol and inside the
    window, held this position's stop off. It is excluded as the monitor excludes it; with the position proven open and
    no exit of its own in the book, the stop is re-armed."""
    t = _tenant(f"rxg-guard-elsewhere-{uuid.uuid4().hex[:6]}@example.com")
    trade_id, sent = _stale_claim(t, sl_order_id="SL-GONE")
    _live_trade(t, exit_order_id="OTHER-EXIT")                               # another position on RXG records it
    broker = _Book(positions=_open_at_broker())
    broker.orders["OTHER-EXIT"] = _lookalike("OTHER-EXIT", "OPEN", tag=_exit_tag(broker), placed_at=sent + timedelta(seconds=1))

    async def stamp():
        async with _session_factory() as session:
            row = await session.get(TradeRecord, trade_id)
            row.reconciled_open_at = sent + timedelta(minutes=5)
            await session.commit()
    _run(stamp())
    counts = _guard(t, broker)
    assert counts["rearmed"] == 1 and counts.get("exit_pending") == 1             # the other position's exit is pending
    assert _get(TradeRecord, trade_id).sl_order_id == "NEW-1"                     # this one got its stop back


def test_adapters_read_a_naive_order_time_as_ist():
    """Kite and Upstox send `order_timestamp` as a naive IST wall time; Noren sends `norentm` as "HH:MM:SS DD-MM-YYYY"."""
    import httpx
    from app.brokers.models import BrokerCredentials
    from app.brokers.shoonya import ShoonyaBroker
    from app.brokers.upstox import UpstoxBroker
    from app.brokers.zerodha import ZerodhaBroker
    utc = datetime(2026, 10, 13, 4, 0, 0, tzinfo=timezone.utc)                # 09:30 IST
    kite = {"order_id": "K1", "tradingsymbol": "RXG", "transaction_type": "SELL", "quantity": 1, "order_type": "MARKET",
            "status": "COMPLETE", "order_timestamp": "2026-10-13 09:30:00", "tag": "T-EXIT", "product": "MIS", "exchange": "NFO"}
    upstox = {"order_id": "U1", "trading_symbol": "RXG", "transaction_type": "SELL", "quantity": 1, "order_type": "MARKET",
              "status": "complete", "order_timestamp": "2026-10-13 09:30:00", "tag": "T-EXIT", "product": "I", "exchange": "NFO"}
    noren = {"norenordno": "S1", "tsym": "RXG", "trantype": "S", "qty": "1", "prctyp": "MKT", "status": "COMPLETE",
             "norentm": "09:30:00 13-10-2026", "remarks": "T-EXIT", "prd": "I", "exch": "NFO"}

    def handler(request):
        if "OrderBook" in request.url.path:
            return httpx.Response(200, json={"stat": "Ok", "values": [noren]})
        if "retrieve-all" in request.url.path:
            return httpx.Response(200, json={"status": "success", "data": [upstox]})
        return httpx.Response(200, json={"status": "success", "data": [kite]})

    def client(base):
        return httpx.AsyncClient(base_url=base, transport=httpx.MockTransport(handler))
    creds = BrokerCredentials(api_key="k", access_token="t", client_id="u", api_secret="s")
    shoonya = ShoonyaBroker(creds, client=client(ShoonyaBroker.BASE_URL))
    shoonya._session_token = "x"
    books = [_run(ZerodhaBroker(creds, client=client(ZerodhaBroker.BASE_URL)).get_order_book()),
             _run(UpstoxBroker(creds, client=client(UpstoxBroker.BASE_URL)).get_order_book()),
             _run(shoonya.get_order_book())]
    for (order,) in books:
        assert order.placed_at == utc and order.placed_at.tzinfo is not None
        assert (order.tag, order.product, order.exchange) == ("T-EXIT", "MIS", "NFO")


def product_of(trade_id):
    from app.execution.products import product_for_trade
    return product_for_trade(_get(TradeRecord, trade_id))


# --- Round 4, finding 2: a part fill while the exit still works is never booked as closed --------------------------------

class _Working(_Book):
    """Each MARKET exit is taken and part-fills `first_fill` units, still OPEN; the test moves it on."""

    def __init__(self, first_fill, **kw):
        super().__init__(**kw)
        self.first_fill = first_fill

    async def place_order(self, order):
        self.placed.append(order)
        oid = f"NEW-{len(self.placed)}"
        first = len(self.placed) == 1
        filled = self.first_fill if first else order.quantity
        self.orders[oid] = BrokerOrderStatus(order_id=oid, symbol=order.symbol, transaction_type=order.transaction_type,
                                             quantity=order.quantity, filled_quantity=filled, order_type=order.order_type,
                                             status="OPEN" if first else "COMPLETE", average_price=self.ltp)
        return BrokerOrderResponse(order_id=oid, status="OPEN")


def test_an_exit_part_filled_and_still_open_is_not_booked_until_it_completes():
    t = _tenant(f"rxg-open-part-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_order_id=None, quantity=10)
    broker = _Working(first_fill=3, ltp=97.0)
    first = _close(trade_id, broker, t["user_id"])
    row = _get(TradeRecord, trade_id)
    assert not first.closed and first.exit_waiting and row.exit_time is None and row.exit_order_id == "NEW-1"
    assert any("still working" in w for w in first.warnings)
    again = _close(trade_id, broker, t["user_id"])                            # still OPEN with 3: wait, nothing new
    assert not again.closed and again.exit_waiting and len(broker.placed) == 1
    broker.orders["NEW-1"] = broker.orders["NEW-1"].model_copy(update={"status": "COMPLETE", "filled_quantity": 10,
                                                                       "average_price": 96.8})
    done = _close(trade_id, broker, t["user_id"])
    row = _get(TradeRecord, trade_id)
    assert done.closed and len(broker.placed) == 1 and [float(o.quantity) for o in broker.placed] == [10.0]
    assert row.exit_price == 96.8 and row.exit_order_id == "NEW-1"
    assert row.pnl is not None and row.pnl < (96.8 - 100.0) * 10 + 1e-9       # booked for all 10 (less charges)


def test_an_exit_part_filled_while_open_then_cancelled_sends_exactly_the_rest():
    t = _tenant(f"rxg-open-cancel-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_order_id=None, quantity=10)
    broker = _Working(first_fill=3, ltp=97.0)
    assert not _close(trade_id, broker, t["user_id"]).closed
    broker.orders["NEW-1"] = broker.orders["NEW-1"].model_copy(update={"status": "CANCELLED"})
    broker.ltp = 95.0
    outcome = _close(trade_id, broker, t["user_id"])
    row = _get(TradeRecord, trade_id)
    assert outcome.closed and [float(o.quantity) for o in broker.placed] == [10.0, 7.0]
    assert row.exit_price == pytest.approx((3 * 97.0 + 7 * 95.0) / 10) and row.exit_order_id == "NEW-2"


# --- Round 5: an old "still open" stamp never lets a replacement exit out on a position closed since ----------------------

class _Unclear(_Book):
    """The exit send times out and the order never reaches the broker; later sends fill. `positions_error` makes the
    positions read fail."""

    def __init__(self, positions_error=None, **kw):
        super().__init__(**kw)
        self.positions_error = positions_error
        self.lost_once = False
        self.position_reads = 0

    async def place_order(self, order):
        if not self.lost_once:
            self.lost_once = True
            self.placed.append(order)
            raise TimeoutError("read timed out")
        return await super().place_order(order)

    async def get_positions(self):
        self.position_reads += 1
        if self.positions_error is not None:
            raise self.positions_error
        return list(self.positions)


def _stamped_lost_exit(t, broker):
    """An unclear send that never reached the broker, then a reconciliation that stamps the position open (nothing working)."""
    trade_id = _live_trade(t, exit_order_id=None)
    first = _close(trade_id, broker, t["user_id"])
    row = _get(TradeRecord, trade_id)
    assert not first.closed and row.exit_order_id == pm.EXIT_PENDING and len(broker.placed) == 1
    _reconcile(t, broker)
    row = _get(TradeRecord, trade_id)
    assert row.reconciled_open_at is not None and row.reconciled_open_at > row.exit_sent_at
    return trade_id, row.exit_sent_at


def _after_claim_window():
    return datetime.now(timezone.utc) + timedelta(seconds=pm.CLAIM_INFLIGHT_SECONDS + 60)


def _assert_no_replacement(t, trade_id, broker, outcome):
    row = _get(TradeRecord, trade_id)
    assert not outcome.closed and outcome.exit_waiting and row.exit_time is None
    assert len(broker.placed) == 1                                            # only the lost send - nothing new
    assert broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is not None
    assert row.reconciled_open_at is None


@pytest.mark.parametrize("reconcile_after_close", [True, False])
def test_r2_a_manual_cnc_close_after_the_stamp_never_gets_a_replacement_exit(reconcile_after_close):
    """Review round 4, R2: the send is unclear and never reaches the broker; a reconciliation stamps the position open; the
    user then closes it in the broker app with an untagged CNC order (someone else's order, not adopted). A later
    reconciliation finds it flat and clears the stamp (a); without that run the fresh positions read before sending
    catches it (b). Either way no SELL goes out on a flat position (a reverse position)."""
    t = _tenant(f"rxg-r2-{uuid.uuid4().hex[:6]}@example.com")
    broker = _Unclear(positions=_open_at_broker())
    trade_id, sent = _stamped_lost_exit(t, broker)
    broker.orders["MAN-1"] = _lookalike("MAN-1", "COMPLETE", placed_at=sent + timedelta(seconds=90), product="CNC", exchange="NFO")
    broker.positions = []                                                     # flat at the broker
    if reconcile_after_close:
        _reconcile(t, broker)
        assert _get(TradeRecord, trade_id).reconciled_open_at is None         # (a) the stamp is gone
    outcome = _close(trade_id, broker, t["user_id"], now=_after_claim_window())
    _assert_no_replacement(t, trade_id, broker, outcome)


@pytest.mark.parametrize("reconcile_after_fill", [True, False])
def test_r1_an_order_that_cannot_be_adopted_filling_after_the_stamp_never_gets_a_replacement_exit(reconcile_after_fill):
    """Review round 4, R1: after the stamp, an order that may be the exit but cannot be adopted (Shoonya-style, no order
    time) fills and the broker is flat. The old stamp must not let a replacement out."""
    t = _tenant(f"rxg-r1-{uuid.uuid4().hex[:6]}@example.com")
    broker = _Unclear(positions=_open_at_broker())
    trade_id, sent = _stamped_lost_exit(t, broker)
    broker.orders["UNTIMED-1"] = _lookalike("UNTIMED-1", "COMPLETE", tag=_exit_tag(broker), placed_at=None)
    broker.positions = []
    if reconcile_after_fill:
        _reconcile(t, broker)
        assert _get(TradeRecord, trade_id).reconciled_open_at is None
    outcome = _close(trade_id, broker, t["user_id"], now=_after_claim_window())
    _assert_no_replacement(t, trade_id, broker, outcome)


def test_a_reconciliation_clears_the_stamp_while_an_order_works_on_the_symbol():
    t = _tenant(f"rxg-r5-working-{uuid.uuid4().hex[:6]}@example.com")
    broker = _Unclear(positions=_open_at_broker())
    trade_id, sent = _stamped_lost_exit(t, broker)
    broker.orders["MAN-1"] = _lookalike("MAN-1", "OPEN", product="CNC", exchange="NFO", placed_at=sent + timedelta(seconds=90))
    _reconcile(t, broker)
    assert _get(TradeRecord, trade_id).reconciled_open_at is None


def test_a_position_still_open_gets_its_replacement_after_the_fresh_positions_check():
    t = _tenant(f"rxg-r5-open-{uuid.uuid4().hex[:6]}@example.com")
    broker = _Unclear(positions=_open_at_broker())
    trade_id, sent = _stamped_lost_exit(t, broker)
    reads = broker.position_reads
    outcome = _close(trade_id, broker, t["user_id"], now=_after_claim_window())
    row = _get(TradeRecord, trade_id)
    assert outcome.closed and broker.position_reads == reads + 1             # read again right before sending
    assert len(broker.placed) == 2 and row.exit_order_id == "NEW-2"
    assert any("gone:" in d for d in _audit(t["tenant_id"], "exit_order_replaced"))


def test_unreadable_positions_wait_and_a_later_reconciliation_lets_the_exit_out():
    """(b) never sends blind, and never blocks for good (ADR-0004): once the positions can be read again and a
    reconciliation shows the position open, the replacement goes out."""
    t = _tenant(f"rxg-r5-unreadable-{uuid.uuid4().hex[:6]}@example.com")
    broker = _Unclear(positions=_open_at_broker())
    trade_id, sent = _stamped_lost_exit(t, broker)
    broker.positions_error = BrokerAPIError("positions endpoint down")
    outcome = _close(trade_id, broker, t["user_id"], now=_after_claim_window())
    _assert_no_replacement(t, trade_id, broker, outcome)
    assert any("could not be read" in w for w in outcome.warnings)
    broker.positions_error = None
    _reconcile(t, broker)
    assert _get(TradeRecord, trade_id).reconciled_open_at is not None
    again = _close(trade_id, broker, t["user_id"], now=_after_claim_window())
    assert again.closed and len(broker.placed) == 2


def test_another_quantity_at_the_broker_waits_and_the_exit_goes_out_once_the_books_agree():
    """A momentary difference (another fill on the symbol landing) holds the replacement; it goes out once a
    reconciliation shows the position open again."""
    t = _tenant(f"rxg-r5-qty-{uuid.uuid4().hex[:6]}@example.com")
    broker = _Unclear(positions=_open_at_broker())
    trade_id, sent = _stamped_lost_exit(t, broker)
    broker.positions = _open_at_broker(quantity=2)
    outcome = _close(trade_id, broker, t["user_id"], now=_after_claim_window())
    _assert_no_replacement(t, trade_id, broker, outcome)
    broker.positions = _open_at_broker()
    _reconcile(t, broker)
    again = _close(trade_id, broker, t["user_id"], now=_after_claim_window())
    assert again.closed and len(broker.placed) == 2
