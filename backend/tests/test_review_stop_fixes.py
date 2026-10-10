"""ATP review (stop path), against mock brokers - nothing is sent anywhere:

1. a stop the guard re-arms that the broker accepts and then REJECTS is counted per trade; after
   STOP_REARM_MAX_REJECTS in a row the guard stops re-arming (LIVE_EXIT_IF_NO_STOP: closes the position as a clear
   rejection; otherwise one CRITICAL) - no endless re-arm loop. The new stop's status is read once right after placing.
2. stop triggers go out on the 0.05 tick, rounded away from the market, on placement and on a trailing modify.
8. closing a position drops the guard's per-trade counters and alert cooldowns.
9. an exit after a stop cancel goes out at once; a fill the stop made after it was read is netted in the booking and
   handed to reconciliation (broker-uncertain).
"""
import asyncio
import json
import time

import pytest

from app.brokers.base import round_stop_trigger
from app.brokers.models import BrokerOrderResponse, BrokerOrderStatus
from app.core import config
from app.core.enums import OrderSide
from app.db.models import Tenant, TradeRecord
from app.market_data.calendar import SessionStatus
from app.trading import stop_guard, stop_state
from app.trading.position_monitor import _apply_trade_exit_rules, close_position
from app.trading.stop_guard import verify_protective_stops
from tests.test_auth_api import _session_factory, client
from tests.test_phase_p_closure import _BookBroker, _seed
from tests.test_trading_worker import _FakeBroker, _get, _tenant


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    for name in ("LIVE_MARKET_PROTECTION", "LIVE_UPSTOX_OPTION_STOP_LIMIT", "LIVE_EXIT_IF_NO_STOP"):
        monkeypatch.setattr(config, name, False)
    monkeypatch.setattr(config, "STOP_LIMIT_BAND_PCT", None)
    monkeypatch.setattr(config, "STOP_REARM_MAX_REJECTS", 3)
    for table in ("exit_attempts", "last_failure_alert", "gave_up_alert", "rearm_rejects", "rearmed_order"):
        monkeypatch.setattr(stop_state, table, {})

    async def market(session, now=None, exchange="NSE"):
        return SessionStatus(True, "open", None)
    monkeypatch.setattr(stop_guard, "market_session_status", market)
    monkeypatch.setattr("app.trading.position_monitor.FILL_POLL_DELAY_SECONDS", 0)


def _stop(order_id, status, filled=0.0, avg=None, order_type="SL-M"):
    return BrokerOrderStatus(order_id=order_id, symbol="RELIANCE", transaction_type=OrderSide.SELL, quantity=10,
                             filled_quantity=filled, order_type=order_type, status=status, average_price=avg)


class _AcceptThenReject(_BookBroker):
    """Takes every stop (TRIGGER PENDING) - and rejects it `later`: at the next order-book read after placing
    (later=False) or only when the test flips it before the next guard cycle (later=True)."""

    def __init__(self, book, later=False):
        super().__init__(book)
        self.later = later
        self.fresh = []

    async def place_stop_loss_order(self, symbol, exchange, transaction_type, quantity, trigger_price, product="MIS", tag=None):
        self.stops.append((symbol, transaction_type, quantity, trigger_price, tag))
        order_id = f"SL-{len(self.stops)}"
        self.book.append(_stop(order_id, "TRIGGER PENDING"))
        self.fresh.append(order_id)
        return BrokerOrderResponse(order_id=order_id, status="TRIGGER PENDING")

    def reject_standing(self):
        self.book[:] = [_stop(o.order_id, "REJECTED") if o.status == "TRIGGER PENDING" else o for o in self.book]

    async def get_order_book(self):
        if not self.later and self.fresh:
            self.fresh.clear()
            self.reject_standing()
        return list(self.book)

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        return 97.0


def _guard(t, broker):
    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, t["tenant_id"])
            return await verify_protective_stops(session, tenant, broker, user_id=t["user_id"])
    return _run(go())


def _critical(t):
    notes = client.get("/api/notifications", headers=t["headers"]).json()
    return [n["title"] for n in (notes if isinstance(notes, list) else notes.get("items", [])) if n["severity"] == "CRITICAL"]


@pytest.mark.parametrize("later", [False, True])
def test_accept_then_reject_closes_after_n_with_the_exit_flag(monkeypatch, later):
    monkeypatch.setattr(config, "LIVE_EXIT_IF_NO_STOP", True)
    t = _tenant(f"review-acc-rej-{later}@example.com")
    trade_id = _seed(t["tenant_id"], t["user_id"], sl_order_id="SL-ENTRY")
    broker = _AcceptThenReject([_stop("SL-ENTRY", "REJECTED")], later=later)
    closed_at = None
    for cycle in range(1, 8):
        if later:
            broker.reject_standing()
        counts = _guard(t, broker)
        if counts.get("closed"):
            closed_at = cycle
            break
    # Exactly N re-armed stops were tried; the next look found the N-th rejected too and closed the position.
    assert len(broker.stops) == config.STOP_REARM_MAX_REJECTS
    assert closed_at == (config.STOP_REARM_MAX_REJECTS if not later else config.STOP_REARM_MAX_REJECTS + 1)
    trade = _get(TradeRecord, trade_id)
    assert trade.exit_time is not None and "rejected 3 re-armed stops in a row" in trade.exit_reason
    assert [(o.order_type, o.transaction_type, o.quantity) for o in broker.placed] == [("MARKET", OrderSide.SELL, 10)]
    assert trade_id not in stop_state.rearm_rejects and trade_id not in stop_state.exit_attempts   # item 8


def test_accept_then_reject_without_the_flag_stops_rearming_and_alerts_once():
    t = _tenant("review-acc-rej-noflag@example.com")
    trade_id = _seed(t["tenant_id"], t["user_id"], sl_order_id="SL-ENTRY")
    broker = _AcceptThenReject([_stop("SL-ENTRY", "REJECTED")])
    for _ in range(8):
        _guard(t, broker)
    assert len(broker.stops) == config.STOP_REARM_MAX_REJECTS       # never an endless loop
    assert broker.placed == [] and _get(TradeRecord, trade_id).exit_time is None
    assert stop_state.rearm_rejects[trade_id] == config.STOP_REARM_MAX_REJECTS
    assert _critical(t).count("Stop keeps being rejected on RELIANCE") == 1   # cooled down, not once per cycle


def test_a_stop_cancelled_by_hand_breaks_the_streak():
    t = _tenant("review-streak-break@example.com")
    trade_id = _seed(t["tenant_id"], t["user_id"], sl_order_id="SL-ENTRY")
    broker = _AcceptThenReject([_stop("SL-ENTRY", "REJECTED")])
    _guard(t, broker)
    _guard(t, broker)
    assert stop_state.rearm_rejects[trade_id] == 2
    broker.later = True
    _guard(t, broker)                                        # third stop accepted and standing
    broker.book[:] = [_stop(o.order_id, "CANCELLED") if o.status == "TRIGGER PENDING" else o for o in broker.book]
    _guard(t, broker)                                        # cancelled by hand: re-armed, streak reset
    assert trade_id not in stop_state.rearm_rejects and len(broker.stops) == 4


# --- 2. tick rounding --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("side,trigger,expected", [
    (OrderSide.SELL, 98.03, 98.00),     # a long's stop: down, farther below the market
    (OrderSide.BUY, 98.03, 98.05),      # a short's stop: up, farther above
    (OrderSide.SELL, 98.05, 98.05),     # already on the tick: unchanged
    (OrderSide.BUY, 101.2000000001, 101.20),
    (OrderSide.SELL, 0.01, 0.05),       # never below one tick
])
def test_stop_trigger_rounds_to_the_tick_away_from_the_market(side, trigger, expected):
    assert round_stop_trigger(trigger, side) == expected


def test_mcx_uses_its_own_tick():
    assert round_stop_trigger(6500.4, OrderSide.SELL, symbol="CRUDEOIL") == 6500.0
    assert round_stop_trigger(6500.4, OrderSide.BUY, symbol="CRUDEOIL") == 6501.0


class _Capture(_FakeBroker):
    def __init__(self):
        super().__init__()
        self.modified = []

    async def modify_order(self, order_id, quantity=None, price=None, trigger_price=None, order_type=None):
        self.modified.append((order_id, trigger_price, price))
        return BrokerOrderResponse(order_id=order_id, status="TRIGGER PENDING")


def test_an_off_tick_trigger_is_placed_on_the_tick():
    broker = _Capture()
    _run(broker.place_stop_loss_order("RELIANCE", "NSE", OrderSide.SELL, 10, trigger_price=98.03))
    _run(broker.place_stop_loss_order("RELIANCE", "NSE", OrderSide.BUY, 10, trigger_price=98.03))
    assert [(o.order_type, o.trigger_price) for o in broker.placed] == [("SL-M", 98.0), ("SL-M", 98.05)]


def test_a_trailing_modify_sends_an_on_tick_trigger():
    t = _tenant("review-tick-modify@example.com")
    trade_id = _seed(t["tenant_id"], t["user_id"], sl_order_id="SL-T")

    async def go():
        async with _session_factory() as session:
            trade = await session.get(TradeRecord, trade_id)
            trade.exit_rules = json.dumps({"trailing_stop_pct": 1})
            await session.commit()
            broker = _Capture()
            await _apply_trade_exit_rules(session, trade, 113.37, broker=broker)   # 1% under 113.37 = 112.24 (off-tick)
            return broker.modified
    assert _run(go()) == [("SL-T", 112.2, None)]


# --- 8. closing clears the guard's memory ----------------------------------------------------------------------
def test_close_position_clears_the_guards_per_trade_memory():
    t = _tenant("review-forget@example.com")
    trade_id = _seed(t["tenant_id"], t["user_id"], mode="PAPER")
    stop_state.exit_attempts[trade_id] = 2
    stop_state.last_failure_alert[trade_id] = time.monotonic()
    stop_state.rearm_rejects[trade_id] = 1
    stop_state.rearmed_order[trade_id] = "SL-9"
    stop_state.gave_up_alert[trade_id] = time.monotonic()

    async def go():
        async with _session_factory() as session:
            return await close_position(session, await session.get(TradeRecord, trade_id), 101.0, "Target")
    assert _run(go()).closed
    assert all(trade_id not in table for table in (stop_state.exit_attempts, stop_state.last_failure_alert, stop_state.gave_up_alert,
                                                    stop_state.rearm_rejects, stop_state.rearmed_order))


# --- 9. the exit does not wait on the cancelled stop -----------------------------------------------------------
class _LateFill(_BookBroker):
    """A standing stop-limit (nothing filled when read) that fills 3 more between our read and the cancel."""

    def __init__(self):
        super().__init__([_stop("SL-P", "OPEN", order_type="SL")])
        self.events = []
        self.cancelled = False

    async def get_order_book(self):
        self.events.append("book")
        stop = _stop("SL-P", "CANCELLED", filled=3, avg=98.0, order_type="SL") if self.cancelled else self.book[0]
        exits = [BrokerOrderStatus(order_id=f"ORD-{i + 1}", symbol=o.symbol, transaction_type=o.transaction_type, quantity=o.quantity,
                                   filled_quantity=o.quantity, order_type=o.order_type, status="COMPLETE", average_price=97.0)
                 for i, o in enumerate(self.placed)]
        return [stop, *exits]

    async def cancel_order(self, order_id):
        self.events.append("cancel")
        self.cancelled = True
        return BrokerOrderResponse(order_id=order_id, status="CANCELLED")

    async def place_order(self, order):
        self.events.append("exit")
        return await super().place_order(order)


def test_the_exit_goes_out_at_once_and_a_late_stop_fill_is_netted(monkeypatch):
    monkeypatch.setattr(config, "LIVE_UPSTOX_OPTION_STOP_LIMIT", True)
    t = _tenant("review-late-fill@example.com")
    trade_id = _seed(t["tenant_id"], t["user_id"], sl_order_id="SL-P")
    broker = _LateFill()

    async def go():
        async with _session_factory() as session:
            return await close_position(session, await session.get(TradeRecord, trade_id), 97.0, "Target", broker=broker)
    outcome = _run(go())
    assert broker.events[:3] == ["book", "cancel", "exit"]         # no read of the stop between the cancel and the exit
    assert outcome.closed and [o.quantity for o in broker.placed] == [10]
    assert outcome.broker_uncertain and "filled 3 more" in outcome.broker_uncertain
    booked = _get(TradeRecord, trade_id)
    assert booked.exit_price == pytest.approx((3 * 98.0 + 7 * 97.0) / 10, abs=0.01)
    tenant = _get(Tenant, t["tenant_id"])
    assert tenant.broker_uncertain_reason       # reconciliation decides the extra 3 the exit sent
