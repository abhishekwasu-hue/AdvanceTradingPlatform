"""Phase A5: the single close path (paper + live) and the per-cycle open-position sweep."""
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import select

from app.brokers.base import BrokerInterface
from app.brokers.models import BrokerOrderResponse, BrokerOrderStatus
from app.core.enums import OrderSide
from app.db.models import NotificationRecord, TradeRecord, User
from app.trading import position_monitor as pm
from app.trading.position_monitor import close_position, monitor_open_positions
from tests.test_auth_api import _register, _session_factory


def _user(email: str) -> User:
    _register(email)

    async def load():
        async with _session_factory() as session:
            return await session.scalar(select(User).where(User.email == email))

    return asyncio.run(load())


def _trade(user: User, *, mode="PAPER", direction="LONG", symbol="RELIANCE", sl_order_id=None, **overrides) -> int:
    long = direction == "LONG"
    fields = dict(
        tenant_id=user.tenant_id, user_id=user.id, mode=mode, symbol=symbol, strategy_id="ema_rsi_scalper_1m",
        direction=direction, entry_time=datetime.now(timezone.utc), entry_price=100.0, quantity=100,
        stop_loss=98.0 if long else 102.0, target1=104.0 if long else 96.0, target2=108.0 if long else 92.0,
        broker_order_id="ORD-ENTRY" if mode == "LIVE" else None, sl_order_id=sl_order_id,
    )
    fields.update(overrides)

    async def create():
        async with _session_factory() as session:
            record = TradeRecord(**fields)
            session.add(record)
            await session.commit()
            return record.id

    return asyncio.run(create())


def _load(trade_id: int) -> TradeRecord:
    async def go():
        async with _session_factory() as session:
            return await session.get(TradeRecord, trade_id)
    return asyncio.run(go())


def _notifications(tenant_id: int) -> List[NotificationRecord]:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(
                select(NotificationRecord).where(NotificationRecord.tenant_id == tenant_id).order_by(NotificationRecord.id)
            ))
    return asyncio.run(go())


class _Broker(BrokerInterface):
    name = "fakelive"

    def __init__(self, *, sl_status="OPEN", exit_fill=103.9, cancel_fails=False, exit_fails=False):
        self.sl_status = sl_status
        self.exit_fill = exit_fill
        self.cancel_fails = cancel_fails
        self.exit_fails = exit_fails
        self.placed = []
        self.cancelled = []

    async def get_order_book(self) -> List[BrokerOrderStatus]:
        book = [BrokerOrderStatus(
            order_id="SL-1", symbol="RELIANCE", transaction_type=OrderSide.SELL, quantity=10,
            filled_quantity=10 if self.sl_status == "COMPLETE" else 0, order_type="SL-M", status=self.sl_status,
            average_price=97.9 if self.sl_status == "COMPLETE" else None,
        )]
        for i, _ in enumerate(self.placed, start=1):
            book.append(BrokerOrderStatus(
                order_id=f"EXIT-{i}", symbol="RELIANCE", transaction_type=OrderSide.SELL, quantity=10,
                filled_quantity=10, order_type="MARKET", status="COMPLETE", average_price=self.exit_fill,
            ))
        return book

    async def cancel_order(self, order_id):
        if self.cancel_fails:
            raise RuntimeError("Order already executed")
        self.cancelled.append(order_id)
        return BrokerOrderResponse(order_id=order_id, status="CANCELLED")

    async def place_order(self, order):
        if self.exit_fails:
            raise ConnectionError("broker down")
        self.placed.append(order)
        return BrokerOrderResponse(order_id=f"EXIT-{len(self.placed)}", status="OPEN")

    async def authenticate(self): raise NotImplementedError
    async def get_profile(self): raise NotImplementedError
    async def get_instruments(self, exchange=None): raise NotImplementedError
    async def get_ltp(self, symbols): raise NotImplementedError
    async def get_quote(self, symbols): raise NotImplementedError
    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date): raise NotImplementedError
    async def get_option_chain(self, underlying, expiry=None): raise NotImplementedError
    async def modify_order(self, order_id, quantity=None, price=None, trigger_price=None, order_type=None): raise NotImplementedError
    async def get_trade_book(self): raise NotImplementedError
    async def get_positions(self): raise NotImplementedError
    async def get_holdings(self): raise NotImplementedError
    async def get_margins(self): raise NotImplementedError


def _close(trade_id: int, price: float, reason: str, broker=None):
    async def go():
        async with _session_factory() as session:
            trade = await session.get(TradeRecord, trade_id)
            return await close_position(session, trade, price, reason, broker=broker)
    return asyncio.run(go())


def test_paper_close_books_pnl_and_notifies():
    user = _user("pm-paper@example.com")
    trade_id = _trade(user)
    outcome = _close(trade_id, 104.0, "Target 1")

    assert outcome.closed and outcome.exit_reason == "Target 1" and outcome.exit_price == 104.0
    trade = _load(trade_id)
    assert trade.exit_time is not None and trade.exit_reason == "Target 1"
    assert trade.charges > 0
    assert trade.pnl == round(400.0 - trade.charges, 2)
    notes = _notifications(user.tenant_id)
    assert [n.event_type for n in notes] == ["EXIT"] and notes[0].severity == "INFO"


def test_losing_close_is_a_warning_notification():
    user = _user("pm-loss@example.com")
    trade_id = _trade(user)
    _close(trade_id, 98.0, "Stop Loss")
    assert _notifications(user.tenant_id)[0].severity == "WARNING"


def test_already_closed_position_is_not_closed_twice():
    user = _user("pm-twice@example.com")
    trade_id = _trade(user)
    _close(trade_id, 104.0, "Target 1")
    second = _close(trade_id, 108.0, "Target 2")
    assert not second.closed and "already closed" in second.warnings[0]
    assert _load(trade_id).exit_price == 104.0


def test_live_target_exit_cancels_stop_and_places_market_exit_at_real_fill(monkeypatch):
    monkeypatch.setattr(pm, "FILL_POLL_DELAY_SECONDS", 0)
    user = _user("pm-live-target@example.com")
    trade_id = _trade(user, mode="LIVE", sl_order_id="SL-1")
    broker = _Broker(exit_fill=103.9)

    outcome = _close(trade_id, 104.0, "Target 1", broker=broker)

    assert outcome.closed
    assert broker.cancelled == ["SL-1"]
    assert len(broker.placed) == 1
    exit_order = broker.placed[0]
    assert exit_order.transaction_type == OrderSide.SELL and exit_order.order_type == "MARKET" and exit_order.quantity == 100
    assert outcome.exit_price == 103.9  # realised fill, not the target level
    assert outcome.broker_exit_order_id == "EXIT-1"
    assert _load(trade_id).exit_price == 103.9


def test_live_short_exit_buys_back():
    user = _user("pm-live-short@example.com")
    trade_id = _trade(user, mode="LIVE", direction="SHORT", sl_order_id="SL-1")
    broker = _Broker(exit_fill=96.1)
    outcome = _close(trade_id, 96.0, "Target 1", broker=broker)
    assert outcome.closed and broker.placed[0].transaction_type == OrderSide.BUY


def test_live_stop_already_filled_at_broker_places_no_new_order():
    user = _user("pm-live-slhit@example.com")
    trade_id = _trade(user, mode="LIVE", sl_order_id="SL-1")
    broker = _Broker(sl_status="COMPLETE")

    outcome = _close(trade_id, 98.0, "Stop Loss", broker=broker)

    assert outcome.closed
    assert broker.placed == [] and broker.cancelled == []
    assert outcome.exit_price == 97.9  # the stop's own average fill
    assert outcome.broker_exit_order_id == "SL-1"


def test_live_stop_still_open_is_cancelled_then_market_exited(monkeypatch):
    monkeypatch.setattr(pm, "FILL_POLL_DELAY_SECONDS", 0)
    user = _user("pm-live-slopen@example.com")
    trade_id = _trade(user, mode="LIVE", sl_order_id="SL-1")
    broker = _Broker(sl_status="OPEN", exit_fill=97.8)
    outcome = _close(trade_id, 98.0, "Stop Loss", broker=broker)
    assert outcome.closed and broker.cancelled == ["SL-1"] and len(broker.placed) == 1
    assert outcome.exit_price == 97.8


def test_live_without_broker_session_stays_open():
    user = _user("pm-live-nobroker@example.com")
    trade_id = _trade(user, mode="LIVE", sl_order_id="SL-1")
    outcome = _close(trade_id, 104.0, "Target 1", broker=None)
    assert not outcome.closed and "broker session" in outcome.warnings[0]
    assert _load(trade_id).exit_time is None
    assert _notifications(user.tenant_id) == []


def test_live_exit_order_failure_leaves_position_open():
    user = _user("pm-live-exitfail@example.com")
    trade_id = _trade(user, mode="LIVE", sl_order_id="SL-1")
    broker = _Broker(exit_fails=True)
    outcome = _close(trade_id, 104.0, "Target 1", broker=broker)
    assert not outcome.closed and "Exit order failed" in outcome.warnings[0]
    assert _load(trade_id).exit_time is None


def test_cancel_failure_because_stop_just_filled_is_treated_as_stop_exit():
    user = _user("pm-live-race@example.com")
    trade_id = _trade(user, mode="LIVE", sl_order_id="SL-1")

    class _Racy(_Broker):
        """Book says OPEN on first read, cancel fails, book then says COMPLETE - the classic race."""
        reads = 0

        async def get_order_book(self):
            self.reads += 1
            self.sl_status = "OPEN" if self.reads == 1 else "COMPLETE"
            return await super().get_order_book()

    broker = _Racy(cancel_fails=True)
    outcome = _close(trade_id, 104.0, "Target 1", broker=broker)
    assert outcome.closed and broker.placed == []
    assert outcome.exit_price == 97.9 and outcome.broker_exit_order_id == "SL-1"
    assert outcome.exit_reason == "Stop Loss"  # not the Target 1 the software check saw
    assert any("already filled" in w for w in outcome.warnings)


# --- the sweep ------------------------------------------------------------------------------------

def test_monitor_closes_only_positions_whose_levels_are_crossed():
    user = _user("pm-sweep@example.com")
    hit = _trade(user, symbol="RELIANCE")
    untouched = _trade(user, symbol="TCS")
    prices: Dict[str, float] = {"RELIANCE": 104.5, "TCS": 101.0}

    async def lookup(symbol, exchange):
        assert exchange == "NSE"
        return prices[symbol]

    async def go():
        async with _session_factory() as session:
            return await monitor_open_positions(session, user.tenant_id, lookup)

    outcomes = asyncio.run(go())
    assert [(o.trade_id, o.closed, o.exit_reason) for o in outcomes] == [(hit, True, "Target 1")]
    assert _load(untouched).exit_time is None


def test_monitor_survives_a_missing_quote_and_keeps_sweeping():
    user = _user("pm-sweep-fail@example.com")
    bad = _trade(user, symbol="NOQUOTE")
    good = _trade(user, symbol="INFY")

    async def lookup(symbol, exchange):
        if symbol == "NOQUOTE":
            raise ConnectionError("quote service down")
        return 97.0  # stop-loss hit for the LONG at 98

    async def go():
        async with _session_factory() as session:
            return await monitor_open_positions(session, user.tenant_id, lookup)

    outcomes = asyncio.run(go())
    by_id = {o.trade_id: o for o in outcomes}
    assert not by_id[bad].closed and "Price unavailable" in by_id[bad].warnings[0]
    assert by_id[good].closed and by_id[good].exit_reason == "Stop Loss"


def test_monitor_is_tenant_scoped():
    a = _user("pm-tenant-a@example.com")
    b = _user("pm-tenant-b@example.com")
    _trade(a, symbol="RELIANCE")
    b_trade = _trade(b, symbol="RELIANCE")

    async def lookup(symbol, exchange):
        return 104.5

    async def go():
        async with _session_factory() as session:
            return await monitor_open_positions(session, a.tenant_id, lookup)

    outcomes = asyncio.run(go())
    assert all(o.trade_id != b_trade for o in outcomes)
    assert _load(b_trade).exit_time is None
