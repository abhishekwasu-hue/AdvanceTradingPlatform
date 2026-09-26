"""Phase A4: LIVE mode through the shared execution pipeline - real broker fill, protective
stop-loss, persisted broker order ids."""
import asyncio
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select

from app.brokers.base import BrokerInterface
from app.brokers.models import BrokerOrderResponse, BrokerOrderStatus
from app.core.enums import ExecutionMode, OrderSide, SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.db.models import NotificationRecord, OrderRecord, TradeRecord, User
from app.execution.router import OrderRouter
from app.execution.signal_execution import execute_signal_for_user
from app.risk_engine.risk_manager import TradingDayState
from tests.test_auth_api import _register, _session_factory, client


def _signal(direction=SignalDirection.LONG) -> Signal:
    long = direction == SignalDirection.LONG
    return Signal(
        symbol="RELIANCE", strategy_id="ema_rsi_scalper_1m", strategy_name="EMA + RSI", direction=direction,
        timestamp=datetime.now(timezone.utc), entry=100.0, stop_loss=98.0 if long else 102.0,
        target1=104.0 if long else 96.0, target2=108.0 if long else 92.0, risk_reward=2.0, score=85,
        grade=SignalGrade.HIGH_QUALITY, reasons=["test"], timeframe_combo="1min",
    )


class _LiveBroker(BrokerInterface):
    name = "fakelive"

    def __init__(self, *, fill_price: Optional[float] = 100.35, sl_fails: bool = False, book_supported: bool = True):
        self.fill_price = fill_price
        self.sl_fails = sl_fails
        self.book_supported = book_supported
        self.placed = []
        self.book_calls = 0

    async def place_order(self, order):
        if order.order_type == "SL-M" and self.sl_fails:
            raise ConnectionError("SL rejected: margin")
        self.placed.append(order)
        return BrokerOrderResponse(order_id=f"ORD-{len(self.placed)}", status="OPEN")

    async def get_order_book(self) -> List[BrokerOrderStatus]:
        self.book_calls += 1
        if not self.book_supported:
            raise NotImplementedError
        # First look: still pending; second look: filled - exercises the retry.
        filled = self.book_calls >= 2 and self.fill_price is not None
        return [BrokerOrderStatus(
            order_id="ORD-1", symbol="RELIANCE", transaction_type=OrderSide.BUY, quantity=500,
            filled_quantity=500 if filled else 0, order_type="MARKET", status="COMPLETE" if filled else "OPEN",
            average_price=self.fill_price if filled else None,
        )]

    async def authenticate(self): raise NotImplementedError
    async def get_profile(self): raise NotImplementedError
    async def get_instruments(self, exchange=None): raise NotImplementedError
    async def get_ltp(self, symbols): raise NotImplementedError
    async def get_quote(self, symbols): raise NotImplementedError
    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date): raise NotImplementedError
    async def get_option_chain(self, underlying, expiry=None): raise NotImplementedError
    async def modify_order(self, order_id, quantity=None, price=None, trigger_price=None, order_type=None): raise NotImplementedError
    async def cancel_order(self, order_id): return BrokerOrderResponse(order_id=order_id, status="CANCELLED")
    async def get_trade_book(self): raise NotImplementedError
    async def get_positions(self): raise NotImplementedError
    async def get_holdings(self): raise NotImplementedError
    async def get_margins(self): raise NotImplementedError


def _router(broker, **kwargs) -> OrderRouter:
    router = OrderRouter(mode=ExecutionMode.LIVE, risk_config=RiskConfig(risk_per_trade_pct=1.0), broker=broker, **kwargs)
    router.fill_poll_delay_seconds = 0  # keep the retry path fast in tests
    return router


def _upgrade_plan(tenant_id: int, plan: str = "business") -> None:
    """Free tenants are paper-only with one member (Phase B2); these tests exercise LIVE, teams
    and multiple channels, which are Pro/Business features."""
    from app.db.models import Tenant

    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            tenant.plan = plan
            await session.commit()
    asyncio.run(go())


def test_live_fill_uses_broker_average_price_and_places_opposite_side_stop():
    broker = _LiveBroker(fill_price=100.35)
    result = asyncio.run(_router(broker).execute(_signal(), TradingDayState()))

    assert result.executed and result.trade is not None
    assert result.trade.entry_price == 100.35
    assert result.broker_order_id == "ORD-1"
    assert result.sl_order_id == "ORD-2" and not result.sl_failed
    sl = broker.placed[1]
    assert sl.order_type == "SL-M" and sl.transaction_type == OrderSide.SELL and sl.trigger_price == 98.0
    assert sl.quantity == result.trade.quantity
    assert broker.book_calls == 2  # pending on first look, filled on the retry


def test_short_entry_places_buy_side_stop():
    broker = _LiveBroker()
    result = asyncio.run(_router(broker).execute(_signal(SignalDirection.SHORT), TradingDayState()))
    assert result.executed
    assert broker.placed[0].transaction_type == OrderSide.SELL
    assert broker.placed[1].transaction_type == OrderSide.BUY and broker.placed[1].trigger_price == 102.0


def test_fill_price_falls_back_to_signal_entry_when_order_book_unsupported():
    broker = _LiveBroker(book_supported=False)
    result = asyncio.run(_router(broker).execute(_signal(), TradingDayState()))
    assert result.executed and result.trade.entry_price == 100.0
    assert broker.book_calls == 1  # gave up immediately rather than retrying an unsupported call


def test_fill_price_falls_back_when_order_never_shows_filled():
    broker = _LiveBroker(fill_price=None)
    router = _router(broker)
    result = asyncio.run(router.execute(_signal(), TradingDayState()))
    assert result.trade.entry_price == 100.0
    assert broker.book_calls == router.fill_poll_attempts


def test_failed_protective_stop_keeps_the_fill_but_flags_it():
    broker = _LiveBroker(sl_fails=True)
    result = asyncio.run(_router(broker).execute(_signal(), TradingDayState()))
    assert result.executed  # the entry really filled - it must never be reported as not executed
    assert result.sl_failed and result.sl_order_id is None
    assert any("protective stop-loss order failed" in r for r in result.reasons)
    assert len(broker.placed) == 1


def test_protective_stop_can_be_disabled():
    broker = _LiveBroker()
    result = asyncio.run(_router(broker, place_protective_stop=False).execute(_signal(), TradingDayState()))
    assert result.executed and result.sl_order_id is None and not result.sl_failed
    assert len(broker.placed) == 1


# --- through execute_signal_for_user ------------------------------------------------------------

def _user(email: str) -> User:
    _register(email)

    async def load():
        async with _session_factory() as session:
            return await session.scalar(select(User).where(User.email == email))

    user = asyncio.run(load())
    _upgrade_plan(user.tenant_id, "pro")
    return user


def _execute(user: User, broker, **kwargs):
    async def go():
        async with _session_factory() as session:
            user_in_session = await session.get(User, user.id)
            result, order = await execute_signal_for_user(
                session, user_in_session, mode="LIVE", strategy_id="ema_rsi_scalper_1m", signal=_signal(),
                broker=broker, **kwargs,
            )
            trade = await session.get(TradeRecord, order.trade_id) if order.trade_id else None
            notes = list(await session.scalars(
                select(NotificationRecord).where(NotificationRecord.tenant_id == user.tenant_id)
                .order_by(NotificationRecord.id)
            ))
            return result, order, trade, notes

    return asyncio.run(go())


def test_live_execution_persists_live_trade_with_broker_ids(monkeypatch):
    monkeypatch.setattr(OrderRouter, "fill_poll_delay_seconds", 0)
    user = _user("live-ok@example.com")
    result, order, trade, notes = _execute(user, _LiveBroker(), deployment_id=None)

    assert result.executed
    assert order.status == "POSITION_OPEN"
    assert order.mode == "LIVE" and order.broker_order_id == "ORD-1"
    assert trade is not None
    assert trade.mode == "LIVE"
    assert trade.entry_price == 100.35
    assert trade.broker_order_id == "ORD-1" and trade.sl_order_id == "ORD-2"
    assert [n.event_type for n in notes] == ["ENTRY"]

    events = client.get(f"/api/orders/{order.id}/events", headers={"Authorization": f"Bearer {_login(user)}"}).json()
    assert any("fakelive" in e["detail"] for e in events)


def _login(user: User) -> str:
    return client.post("/api/auth/login", json={"email": user.email, "password": "S3cur3Pass!"}).json()["access_token"]


def test_live_execution_raises_critical_alert_when_stop_fails(monkeypatch):
    monkeypatch.setattr(OrderRouter, "fill_poll_delay_seconds", 0)
    user = _user("live-nosl@example.com")
    result, order, trade, notes = _execute(user, _LiveBroker(sl_fails=True))

    assert result.executed and order.status == "POSITION_OPEN"
    assert trade.sl_order_id is None
    assert [n.event_type for n in notes] == ["ENTRY", "SYSTEM_FAILURE"]
    assert notes[1].severity == "CRITICAL"


def test_live_without_broker_is_rejected_on_the_order_trail():
    user = _user("live-nobroker@example.com")
    result, order, trade, _ = _execute(user, None)

    assert not result.executed
    assert order.status == "REJECTED"
    assert "not configured" in result.reasons[0]
    assert trade is None


def test_paper_mode_is_unchanged_by_broker_argument():
    user = _user("paper-still@example.com")

    async def go():
        async with _session_factory() as session:
            u = await session.get(User, user.id)
            result, order = await execute_signal_for_user(
                session, u, mode="PAPER", strategy_id="ema_rsi_scalper_1m", signal=_signal(), broker=_LiveBroker(),
            )
            return result, order, await session.get(TradeRecord, order.trade_id)

    result, order, trade = asyncio.run(go())
    assert result.executed and trade.mode == "PAPER" and trade.broker_order_id is None
