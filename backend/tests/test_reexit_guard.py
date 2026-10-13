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

def test_a_swing_exit_cancelled_at_the_close_is_replaced_the_next_trading_day():
    """A SWING / NRML exit sent yesterday, not booked, cancelled by the exchange at the close: today's book does not list
    it. When the stop fires a fresh exit goes out (the old id is cleared on the audit trail) - no flag, no block."""
    t = _tenant(f"rxg-swing-{uuid.uuid4().hex[:6]}@example.com")
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    trade_id = _live_trade(t, exit_sent_at=yesterday, sl_order_id="SL-OLD")
    broker = _Book()                                                         # nothing of yesterday's is in today's book
    outcome = _close(trade_id, broker, t["user_id"])
    assert outcome.closed and [o.order_type for o in broker.placed] == ["MARKET"]
    assert broker_uncertain_reason(_get(Tenant, t["tenant_id"])) is None
    row = _get(TradeRecord, trade_id)
    assert row.exit_order_id == "NEW-1" and row.exit_price == 97.0
    cleared = _audit(t["tenant_id"], "exit_order_cleared")
    assert len(cleared) == 1 and "OLD-1" in cleared[0] and "earlier trading day" in cleared[0]
    replaced = _audit(t["tenant_id"], "exit_order_replaced")
    assert len(replaced) == 1 and "OLD-1" in replaced[0] and "NEW-1" in replaced[0]


def test_the_stop_guard_rearms_a_stop_once_a_vanished_exit_is_known_to_be_over():
    t = _tenant(f"rxg-swing-guard-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_sent_at=datetime.now(timezone.utc) - timedelta(days=1), sl_order_id="SL-GONE")
    broker = _Book()
    counts = _guard(t, broker)
    assert counts["rearmed"] == 1 and counts.get("exit_pending", 0) == 0
    assert _get(TradeRecord, trade_id).sl_order_id == "NEW-1"


def test_a_vanished_exit_flags_then_a_clean_reconciliation_lets_a_fresh_exit_out():
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
    assert any("clean reconciliation" in d for d in _audit(t["tenant_id"], "exit_order_cleared"))


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
    monkeypatch.setattr(pm, "_fill_price", crash)
    with pytest.raises(RuntimeError):
        _close(trade_id, _Book(), t["user_id"])
    row = _get(TradeRecord, trade_id)
    assert row.exit_order_id == "NEW-1" and row.exit_sent_at is not None and row.exit_time is None
    assert any("NEW-1" in d for d in _audit(t["tenant_id"], "algo_order"))


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


def test_the_emergency_exit_still_closes_a_paper_structure_as_before():
    """PAPER is unchanged: the hold is for LIVE shorts only (a PAPER short without a supplied price leaves its wing to
    close as it always did)."""
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
    assert len(body["closed_trade_ids"]) == 1 and body["skipped_symbols"] == ["RXPSHORT"]
