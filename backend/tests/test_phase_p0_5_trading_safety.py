"""P0.5 trading safety (T1-T6): unfilled LIVE entries are cancelled rather than booked; the protective stop takes the
order type the broker accepts; shorts of a structure wait for confirmed wings; the emergency exit cancels at the
broker and buys shorts back first; PROTECTED_LIMIT entries; the daily loss counts open marks on the IST day."""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select

from app.brokers.base import BrokerCapabilities, BrokerInterface, looks_like_option, stop_order_params
from app.brokers.models import BrokerOrderResponse, BrokerOrderStatus
from app.core.enums import OrderSide, SignalDirection
from app.core.models import RiskConfig
from app.db.models import OrderRecord, Tenant, TradeRecord, User
from app.execution.router import protected_limit_price
from app.market_data.calendar import IST, trading_day_start
from app.risk_engine.risk_manager import TradingDayState
from app.trading.persistence import build_trading_day_state, open_unrealised_pnl
from app.trading.position_monitor import monitor_open_positions
from tests.test_auth_api import _register, _session_factory, client
from tests.test_live_execution import _LiveBroker, _router, _signal


def _run(coro):
    return asyncio.run(coro)


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


class _BookBroker(_LiveBroker):
    """A LIVE fake whose book can be scripted: `fills` = filled quantity reported for ORD-1, `status` its status,
    `cancel_raises` makes cancel fail, `cancel_fills` makes the order fill between the last look and the cancel."""

    def __init__(self, *, fills: float = 0, status: str = "OPEN", cancel_raises: bool = False, cancel_fills: bool = False,
                 capabilities: Optional[BrokerCapabilities] = None):
        super().__init__(fill_price=100.2)
        self.fills, self.status, self.cancel_raises, self.cancel_fills = fills, status, cancel_raises, cancel_fills
        self.cancelled: List[str] = []
        if capabilities is not None:
            self.capabilities = capabilities

    async def get_order_book(self) -> List[BrokerOrderStatus]:
        self.book_calls += 1
        return [BrokerOrderStatus(order_id="ORD-1", symbol="RELIANCE", transaction_type=OrderSide.BUY, quantity=500,
                                  filled_quantity=self.fills, order_type="MARKET", status=self.status,
                                  average_price=100.2 if self.fills else None)]

    async def cancel_order(self, order_id):
        if self.cancel_raises:
            raise ConnectionError("cancel timed out")
        self.cancelled.append(order_id)
        if self.cancel_fills:
            self.fills, self.status = 500, "COMPLETE"
            return BrokerOrderResponse(order_id=order_id, status="REJECTED", message="already executed")
        self.status = "CANCELLED"
        return BrokerOrderResponse(order_id=order_id, status="CANCELLED")


# --- T1 ------------------------------------------------------------------------------------------------------------------
def test_partial_fill_cancels_the_working_remainder_and_sizes_the_stop_to_the_fill():
    broker = _BookBroker(fills=200, status="OPEN")
    result = _run(_router(broker).execute(_signal(), TradingDayState()))
    assert result.executed and result.partial_fill and result.trade.quantity == 200 and result.requested_quantity == 500
    assert broker.cancelled == ["ORD-1"] and any("remainder" in r for r in result.reasons)
    assert broker.placed[1].quantity == 200 and not result.broker_uncertain


def test_partial_fill_whose_remainder_cannot_be_cancelled_marks_the_broker_uncertain():
    broker = _BookBroker(fills=200, status="OPEN", cancel_raises=True)
    result = _run(_router(broker).execute(_signal(), TradingDayState()))
    assert result.executed and result.partial_fill and result.broker_uncertain
    assert any("reconciliation" in r for r in result.reasons)


def test_fill_that_lands_during_the_cancel_is_honoured():
    broker = _BookBroker(fills=0, status="OPEN", cancel_fills=True)
    result = _run(_router(broker).execute(_signal(), TradingDayState()))
    assert result.executed and result.trade.quantity == 500 and result.trade.entry_price == 100.2
    assert any("during the cancel" in r for r in result.reasons)


def test_rejected_order_is_a_business_rejection_not_a_phantom_position():
    broker = _BookBroker(fills=0, status="REJECTED")
    state = TradingDayState()
    result = _run(_router(broker).execute(_signal(), state))
    assert not result.executed and not result.system_failure and any("REJECTED" in r for r in result.reasons)
    assert broker.cancelled == [] and state.open_positions == 0


def test_unreadable_book_records_as_requested_and_flags_uncertainty_instead_of_cancelling_blind():
    broker = _LiveBroker(book_supported=False)
    result = _run(_router(broker).execute(_signal(), TradingDayState()))
    assert result.executed and result.trade.entry_price == 100.0 and result.broker_uncertain
    assert any("Order book unavailable" in r for r in result.reasons)


def test_unconfirmed_live_fill_pauses_live_entries_through_signal_execution(monkeypatch):
    from app.execution.signal_execution import execute_signal_for_user
    from tests.test_live_execution import _upgrade_plan
    headers = _headers(_register("p05-uncertain@example.com"))
    me = client.get("/api/auth/me", headers=headers).json()
    _upgrade_plan(me["tenant_id"])
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            tenant = await session.get(Tenant, me["tenant_id"])
            tenant.algo_id = "ALGO1"
            await session.commit()
            result, order = await execute_signal_for_user(session, user, mode="LIVE", strategy_id="ema_rsi_scalper_1m", signal=_signal(),
                                                          broker=_BookBroker(fills=200, status="OPEN", cancel_raises=True),
                                                          risk_config=RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0))
            await session.refresh(tenant)
            return result, order, tenant.broker_uncertain_since
    result, order, since = _run(go())
    assert result.executed and result.broker_uncertain and since is not None and order.status == "POSITION_OPEN"


# --- T2 ------------------------------------------------------------------------------------------------------------------
def test_stop_order_type_follows_the_broker_capability_matrix():
    kite = BrokerCapabilities(stop_market_on_options=False)
    assert stop_order_params(kite, "RELIANCE", OrderSide.SELL, 98.0) == ("SL-M", None)
    assert stop_order_params(kite, "NIFTY26OCT26000CE", OrderSide.SELL, 120.0) == ("SL", 118.8)      # limit 1% under the trigger
    assert stop_order_params(kite, "NIFTY 26000 PE 30 OCT 26", OrderSide.BUY, 120.0) == ("SL", 121.2)
    assert stop_order_params(BrokerCapabilities(), "NIFTY26OCT26000CE", OrderSide.SELL, 120.0) == ("SL-M", None)
    assert stop_order_params(BrokerCapabilities(stop_market=False), "BTCINR", OrderSide.SELL, 100.0) == ("SL", 99.0)
    assert stop_order_params(kite, "RELIANCE", OrderSide.SELL, 98.0, is_option=True)[0] == "SL"         # caller knows better
    assert [looks_like_option(s) for s in ("RELIANCE", "ICICIPRULI", "NIFTY FUT 30 OCT 26", "NIFTY-OCT2026-26000-CE", "NIFTY30OCT26C26000")] == [False, False, False, True, True]
    from app.brokers.zerodha import ZerodhaBroker
    from app.brokers.coindcx import CoinDCXBroker
    from app.brokers.fyers import FyersBroker
    assert ZerodhaBroker.capabilities.stop_market_on_options is False and FyersBroker.capabilities.stop_market_on_options is True
    assert CoinDCXBroker.capabilities.stop_market is True            # the adapter converts SL-M to its stop-limit itself


def test_router_places_a_stop_limit_where_the_broker_refuses_sl_m_on_options():
    broker = _BookBroker(fills=500, status="COMPLETE", capabilities=BrokerCapabilities(stop_market_on_options=False))
    result = _run(_router(broker, is_option=True).execute(_signal(), TradingDayState()))
    assert result.executed and result.sl_order_id == "ORD-2"
    stop = broker.placed[1]
    assert stop.order_type == "SL" and stop.trigger_price == 98.0 and stop.price == 97.0
    assert any("(SL, limit 97.0)" in r for r in result.reasons)
    # The same broker on an equity keeps SL-M.
    broker = _BookBroker(fills=500, status="COMPLETE", capabilities=BrokerCapabilities(stop_market_on_options=False))
    result = _run(_router(broker).execute(_signal(), TradingDayState()))
    assert broker.placed[1].order_type == "SL-M" and broker.placed[1].price is None


# --- T5 ------------------------------------------------------------------------------------------------------------------
def test_protected_limit_entries_send_a_marketable_limit_and_default_stays_market():
    assert protected_limit_price(100.0, OrderSide.BUY, 0.5) == 100.5 and protected_limit_price(100.0, OrderSide.SELL, 0.5) == 99.5
    assert protected_limit_price(22512.37, OrderSide.BUY, 0.1) == 22534.9                       # on the 0.05 tick
    broker = _BookBroker(fills=500, status="COMPLETE")
    result = _run(_router(broker, order_style="PROTECTED_LIMIT", market_protection_pct=0.3).execute(_signal(), TradingDayState()))
    assert result.executed and broker.placed[0].order_type == "LIMIT" and broker.placed[0].price == 100.3
    assert any("Protected limit entry at 100.3" in r for r in result.reasons)
    broker = _BookBroker(fills=500, status="COMPLETE")
    _run(_router(broker).execute(_signal(), TradingDayState()))
    assert broker.placed[0].order_type == "MARKET" and broker.placed[0].price is None
    # Deployment API: accepted, stored, returned; defaults to MARKET.
    from tests.test_deployments_api import _auth, _create, _store_broker
    headers = _auth("p05-style@example.com")
    _store_broker(headers)
    created = _create(headers, symbol="tcs", order_style="PROTECTED_LIMIT", market_protection_pct=0.25).json()
    assert created["order_style"] == "PROTECTED_LIMIT" and created["market_protection_pct"] == 0.25
    assert _create(headers, symbol="infy").json()["order_style"] == "MARKET"
    assert _create(headers, symbol="sbin", order_style="PROTECTED_LIMIT", market_protection_pct=9).status_code == 422


# --- T3 ------------------------------------------------------------------------------------------------------------------
def test_structure_shorts_wait_for_confirmed_wing_fills(monkeypatch):
    from app.execution.multileg import _place_live_legs
    from app.instruments.spreads import resolve_structure
    from app.core.enums import OptionStrategy
    from tests.test_contract_rules import TODAY, _load_master
    from tests.test_multileg import SPOT, _OptionBroker, _rules
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    _load_master()

    class _NoWingFill(_OptionBroker):
        def __init__(self):
            super().__init__()
            self.cancelled = []

        async def get_order_book(self):
            book = await super().get_order_book()
            return [o.model_copy(update={"filled_quantity": 0, "average_price": None, "status": "OPEN"}) if "24400 PE" in o.symbol else o for o in book]

        async def cancel_order(self, order_id):
            self.cancelled.append(order_id)
            return BrokerOrderResponse(order_id=order_id, status="CANCELLED")

    async def go(broker):
        async with _session_factory() as session:
            structure = await resolve_structure(session, "NIFTY 50", _rules(), OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG,
                                                spread_width=2, spot=SPOT, today=TODAY)
            fills, notes = {}, []
            ok, failure = await _place_live_legs(broker, structure, structure.lot_size, "s", None, fills, notes)
            return ok, failure, fills, notes
    broker = _NoWingFill()
    ok, failure, fills, notes = _run(go(broker))
    assert not ok and "not filled within the confirmation window" in failure and "(wing)" in failure
    assert [o.transaction_type.value for o in broker.placed] == ["BUY"]              # the short was never sent
    assert broker.cancelled == ["ORD-1"] and fills == {} and "unwound" not in failure   # nothing confirmed, nothing to unwind
    good = _OptionBroker()
    ok, failure, fills, notes = _run(go(good))
    assert ok and [o.transaction_type.value for o in good.placed] == ["BUY", "SELL"] and all(p > 0 for p, _ in fills.values())


# --- T4 ------------------------------------------------------------------------------------------------------------------
def test_emergency_exit_cancels_live_orders_at_the_broker_and_closes_shorts_first(monkeypatch):
    from app.kill_switch import routes as ks
    headers = _headers(_register("p05-emergency@example.com"))
    me = client.get("/api/auth/me", headers=headers).json()
    client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": "k", "api_secret": "s", "access_token": "t"})

    class _Cancelling(BrokerInterface):
        name = "upstox"
        cancelled: List[str] = []

        async def cancel_order(self, order_id):
            _Cancelling.cancelled.append(order_id)
            if order_id == "GONE":
                raise RuntimeError("unknown order")
            return BrokerOrderResponse(order_id=order_id, status="CANCELLED")
        async def authenticate(self): raise NotImplementedError
        async def get_profile(self): raise NotImplementedError
        async def get_instruments(self, exchange=None): raise NotImplementedError
        async def get_ltp(self, symbols): raise NotImplementedError
        async def get_quote(self, symbols): raise NotImplementedError
        async def get_historical_data(self, *a): raise NotImplementedError
        async def get_option_chain(self, underlying, expiry=None): raise NotImplementedError
        async def place_order(self, order): raise NotImplementedError
        async def modify_order(self, *a, **k): raise NotImplementedError
        async def get_order_book(self): return []
        async def get_trade_book(self): raise NotImplementedError
        async def get_positions(self): raise NotImplementedError
        async def get_holdings(self): raise NotImplementedError
        async def get_margins(self): raise NotImplementedError

    async def seed():
        async with _session_factory() as session:
            from app.db.models import BrokerCredentialRecord
            record = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == me["tenant_id"]))
            record.token_status, record.token_expires_at, record.last_verified_at = "VALID", datetime.now(timezone.utc) + timedelta(hours=8), datetime.now(timezone.utc)
            now = datetime.now(timezone.utc)
            session.add_all([
                OrderRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="LIVE", strategy_id="s", symbol="RELIANCE", direction="LONG",
                            quantity=10, status="PENDING", broker_order_id="BRK-1"),
                OrderRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="LIVE", strategy_id="s", symbol="TCS", direction="LONG",
                            quantity=10, status="SUBMITTED", broker_order_id="GONE"),
                OrderRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", strategy_id="s", symbol="INFY", direction="LONG",
                            quantity=10, status="PENDING"),
                TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol="LONGLEG", strategy_id="s", direction="LONG",
                            entry_time=now, entry_price=100.0, quantity=10, stop_loss=98.0),
                TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol="SHORTLEG", strategy_id="s", direction="SHORT",
                            entry_time=now, entry_price=50.0, quantity=10, stop_loss=55.0),
            ])
            await session.commit()
    _run(seed())
    monkeypatch.setattr(ks, "build_adapter", lambda record: _Cancelling())
    response = client.post("/api/kill-switch/emergency-exit", headers=headers, json={"reason": "p0.5", "prices": {"LONGLEG": 101.0, "SHORTLEG": 49.0}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["broker_cancelled"] == ["BRK-1@upstox"] and body["broker_cancel_failures"] == ["GONE"]
    assert len(body["cancelled_order_ids"]) == 3 and _Cancelling.cancelled == ["BRK-1", "GONE"]   # the PAPER order never reaches a broker

    async def closed_order():
        async with _session_factory() as session:
            rows = list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == me["tenant_id"]).order_by(TradeRecord.exit_time)))
            return [r.symbol for r in rows], [r.id for r in rows]
    symbols, ids = _run(closed_order())
    assert body["closed_trade_ids"][0] == ids[symbols.index("SHORTLEG")]           # the short was bought back first


# --- T6 ------------------------------------------------------------------------------------------------------------------
def test_trading_day_starts_at_ist_midnight():
    start = trading_day_start(datetime(2026, 10, 6, 2, 0, tzinfo=timezone.utc))        # 07:30 IST, 6 Oct
    assert start == datetime(2026, 10, 5, 18, 30, tzinfo=timezone.utc) and start.astimezone(IST).hour == 0
    late = trading_day_start(datetime(2026, 10, 6, 20, 0, tzinfo=timezone.utc))         # 01:30 IST, 7 Oct
    assert late == datetime(2026, 10, 6, 18, 30, tzinfo=timezone.utc)


def test_daily_loss_counts_marked_open_positions_on_the_ist_day():
    from app.risk_engine.hierarchy import RiskContext, RiskLimitType, _measure
    from app.db.models import RiskLimitRecord
    headers = _headers(_register("p05-marks@example.com"))
    me = client.get("/api/auth/me", headers=headers).json()
    now = datetime.now(timezone.utc)
    day_start = trading_day_start(now)

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            # A loss realised just after IST midnight (UTC "yesterday" when it is before 05:30 IST) still counts today.
            realised_at = max(day_start + timedelta(minutes=5), now - timedelta(minutes=1))
            session.add(TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol="A", strategy_id="s1", direction="LONG",
                                    entry_time=realised_at - timedelta(hours=1), entry_price=100.0, quantity=10, stop_loss=98.0,
                                    exit_time=realised_at, exit_price=90.0, pnl=-100.0))
            # Open, marked 20 under entry -> -200 unrealised; another open with no mark -> ignored.
            session.add(TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol="B", strategy_id="s1", direction="LONG",
                                    entry_time=now, entry_price=100.0, quantity=10, stop_loss=70.0, target1=130.0, mark_price=80.0, mark_time=now))
            session.add(TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol="C", strategy_id="s2", direction="SHORT",
                                    entry_time=now, entry_price=100.0, quantity=10, stop_loss=130.0, target1=70.0))
            await session.commit()
            assert await open_unrealised_pnl(session, me["tenant_id"]) == -200.0
            assert await open_unrealised_pnl(session, me["tenant_id"], strategy_id="s2") == 0.0
            state = await build_trading_day_state(session, user)
            assert state.daily_pnl == -300.0 and state.open_positions == 2
            rule = RiskLimitRecord(scope="TENANT", scope_id="", limit_type="MAX_DAILY_LOSS", limit_value=250)
            ctx = RiskContext(tenant_id=me["tenant_id"], user_id=me["id"], strategy_id="s1", symbol="A", quantity=1, entry=100.0, stop_loss=99.0, capital=1e6)
            value, label = await _measure(session, ctx, RiskLimitType.MAX_DAILY_LOSS, rule)
            assert value == 300.0 and "marked-to-market" in label
            value, _ = await _measure(session, ctx, RiskLimitType.MAX_STRATEGY_LOSS, rule)
            assert value == 300.0
            # The monitor writes the marks it sees for positions that stay open.
            async def price_lookup(symbol, exchange):
                return {"B": 95.0, "C": 101.0}[symbol] if symbol in ("B", "C") else 100.0
            await monitor_open_positions(session, me["tenant_id"], price_lookup)
            rows = {t.symbol: t for t in await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == me["tenant_id"], TradeRecord.exit_time.is_(None)))}
            assert rows["B"].mark_price == 95.0 and rows["C"].mark_price == 101.0 and rows["C"].mark_time is not None
            assert await open_unrealised_pnl(session, me["tenant_id"]) == -60.0           # B: -50, C (short): -10
    _run(go())


# --- review follow-ups -----------------------------------------------------------------------------------------------------
def test_stop_type_survives_the_symbol_and_rate_limit_wrappers(monkeypatch):
    """The worker hands the router RateLimitedBroker(ContractSymbolBroker(adapter)); the option flag and the
    capability matrix must pass through both, or an F&O entry silently loses its exchange-side stop."""
    from app.brokers.contract_symbols import ContractSymbolBroker
    from app.brokers.rate_budget import RateBudget, RateLimitedBroker, RateLimits
    inner = _BookBroker(fills=500, status="COMPLETE", capabilities=BrokerCapabilities(stop_market_on_options=False))
    stacked = RateLimitedBroker(ContractSymbolBroker(inner), RateBudget(RateLimits(per_second=100, per_minute=6000, burst=100)))
    assert stacked.capabilities.stop_market_on_options is False
    assert stacked.stop_order_params("NIFTY26OCT26000CE", OrderSide.SELL, 120.0) == ("SL", 118.8)
    result = _run(_router(stacked, is_option=True).execute(_signal(), TradingDayState()))
    assert result.executed and result.sl_order_id == "ORD-2" and not result.sl_failed
    assert inner.placed[1].order_type == "SL" and inner.placed[1].price == 97.0


def test_cancel_counts_only_when_the_book_shows_the_order_terminal():
    class _CancelSaysYesBookSaysOpen(_BookBroker):
        async def cancel_order(self, order_id):
            self.cancelled.append(order_id)
            return BrokerOrderResponse(order_id=order_id, status="CANCELLED")      # accepted, but the book keeps it OPEN
    broker = _CancelSaysYesBookSaysOpen(fills=0, status="OPEN")
    result = _run(_router(broker).execute(_signal(), TradingDayState()))
    assert result.executed and result.broker_uncertain and result.trade.quantity == 500
    assert any("could not be cancelled" in r for r in result.reasons) and broker.cancelled == ["ORD-1"]
    # A cancel that raises because the order already executed: the book's fill is honoured.
    class _AlreadyExecuted(_BookBroker):
        async def cancel_order(self, order_id):
            self.fills, self.status = 500, "COMPLETE"
            raise RuntimeError("Order already executed")
    broker = _AlreadyExecuted(fills=0, status="OPEN")
    result = _run(_router(broker).execute(_signal(), TradingDayState()))
    assert result.executed and not result.broker_uncertain and result.trade.entry_price == 100.2


def test_a_remainder_already_terminal_at_the_broker_is_not_cancelled_again():
    broker = _BookBroker(fills=200, status="CANCELLED", cancel_raises=True)     # exchange cancelled the rest itself
    result = _run(_router(broker).execute(_signal(), TradingDayState()))
    assert result.executed and result.partial_fill and result.trade.quantity == 200 and not result.broker_uncertain


def test_structure_leg_filled_during_the_cancel_is_unwound_not_orphaned(monkeypatch):
    from app.execution.multileg import _place_live_legs
    from app.instruments.spreads import resolve_structure
    from app.core.enums import OptionStrategy
    from tests.test_contract_rules import TODAY, _load_master
    from tests.test_multileg import SPOT, _OptionBroker, _rules
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    _load_master()

    class _ShortFillsOnCancel(_OptionBroker):
        def __init__(self):
            super().__init__()
            self.cancelled = []
            self.short_filled = False

        async def get_order_book(self):
            book = await super().get_order_book()
            out = []
            for o in book:
                if "24500 PE" in o.symbol and not self.short_filled:
                    out.append(o.model_copy(update={"filled_quantity": 0, "average_price": None, "status": "OPEN"}))
                else:
                    out.append(o)
            return out

        async def cancel_order(self, order_id):
            self.cancelled.append(order_id)
            self.short_filled = True                                             # the exchange filled it first
            raise RuntimeError("Order already executed")

    async def go(broker):
        async with _session_factory() as session:
            structure = await resolve_structure(session, "NIFTY 50", _rules(), OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG,
                                                spread_width=2, spot=SPOT, today=TODAY)
            fills, notes = {}, []
            return await _place_live_legs(broker, structure, structure.lot_size, "s", None, fills, notes)
    broker = _ShortFillsOnCancel()
    ok, failure = _run(go(broker))
    assert not ok and "filled" in failure and "unwound 2 filled leg(s)" in failure
    sides = [(o.transaction_type.value, "24500 PE" in o.symbol) for o in broker.placed]
    # wing BUY, short SELL, then the unwind: short bought back first, wing sold.
    assert sides == [("BUY", False), ("SELL", True), ("BUY", True), ("SELL", False)]


def test_unrealised_counts_only_positions_opened_and_marked_today():
    headers = _headers(_register("p05-swing@example.com"))
    me = client.get("/api/auth/me", headers=headers).json()
    now = datetime.now(timezone.utc)
    yesterday = trading_day_start(now) - timedelta(hours=3)

    async def go():
        async with _session_factory() as session:
            session.add(TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol="SWING", strategy_id="s", direction="LONG",
                                    entry_time=yesterday, entry_price=100.0, quantity=10, stop_loss=90.0, target1=130.0, mark_price=150.0, mark_time=now))
            session.add(TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol="STALE", strategy_id="s", direction="LONG",
                                    entry_time=now, entry_price=100.0, quantity=10, stop_loss=90.0, target1=130.0, mark_price=50.0, mark_time=yesterday))
            session.add(TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol="TODAY", strategy_id="s", direction="LONG",
                                    entry_time=now, entry_price=100.0, quantity=10, stop_loss=90.0, target1=130.0, mark_price=96.0, mark_time=now))
            await session.commit()
            assert await open_unrealised_pnl(session, me["tenant_id"]) == -40.0        # the carried gain and the stale mark do not count
    _run(go())


def test_coindcx_book_includes_filled_orders_it_placed_and_refuses_trigger_edits():
    import json as _json
    import httpx
    from app.brokers.coindcx import CoinDCXBroker
    from app.brokers.exceptions import BrokerAPIError
    from app.brokers.models import BrokerCredentials

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET":
            return httpx.Response(200, json=[{"coindcx_name": "BTCINR", "base_currency_short_name": "INR", "target_currency_short_name": "BTC", "symbol": "BTCINR",
                                              "min_quantity": 0.0001, "max_quantity": 10, "step": 0.0001, "base_currency_precision": 2, "target_currency_precision": 4,
                                              "order_types": ["limit_order", "market_order", "stop_limit"]}])
        payload = _json.loads(request.content or b"{}")
        if path == "/exchange/v1/orders/create":
            return httpx.Response(200, json={"orders": [{"id": "ord-9", "market": "BTCINR", "side": "buy", "order_type": "market_order", "status": "open",
                                                         "total_quantity": 0.001, "remaining_quantity": 0.001, "created_at": 1}]})
        if path == "/exchange/v1/orders/active_orders":
            return httpx.Response(200, json={"orders": []})                               # filled: gone from the active list
        if path == "/exchange/v1/orders/status":
            assert payload["id"] == "ord-9"
            return httpx.Response(200, json={"id": "ord-9", "market": "BTCINR", "side": "buy", "order_type": "market_order", "status": "filled",
                                             "total_quantity": 0.001, "remaining_quantity": 0, "avg_price": 4900000, "created_at": 1})
        raise AssertionError(f"unexpected {request.method} {path}")
    broker = CoinDCXBroker(BrokerCredentials(api_key="k", api_secret="s"), client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.coindcx.com"))
    placed = _run(broker.place_order(__import__("app.brokers.models", fromlist=["BrokerOrderRequest"]).BrokerOrderRequest(
        symbol="BTCINR", exchange="CRYPTO", transaction_type=OrderSide.BUY, quantity=0.001, order_type="MARKET", product="MIS")))
    book = _run(broker.get_order_book())
    assert placed.order_id == "ord-9" and [o.order_id for o in book] == ["ord-9"] and book[0].filled_quantity == 0.001 and book[0].average_price == 4900000
    import pytest
    with pytest.raises(BrokerAPIError):
        _run(broker.modify_order("ord-9", price=4800000.0, trigger_price=4850000.0))    # trigger is not editable: cancel + re-place
