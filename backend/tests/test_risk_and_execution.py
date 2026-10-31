import asyncio

import pytest

from app.brokers.base import BrokerInterface
from app.brokers.models import BrokerOrderResponse
from app.core.enums import ExecutionMode, SignalDirection
from app.core.models import RiskConfig, Signal
from app.execution.router import LiveTradingNotConfigured, OrderRouter
from app.instruments.registry import get_contract_spec
from app.risk_engine.risk_manager import RiskManager, TradingDayState
from tests.def0_legacy import legacy


def _sample_signal(entry=100.0, sl=98.0, rr=2.0) -> Signal:
    return Signal(
        symbol="TESTSYM",
        strategy_id="test",
        strategy_name="Test",
        direction=SignalDirection.LONG,
        timestamp="2024-01-02T09:20:00",
        entry=entry,
        stop_loss=sl,
        target1=entry + rr * (entry - sl),
        target2=entry + (rr + 1) * (entry - sl),
        risk_reward=rr,
        score=85,
    )


def test_risk_manager_approves_valid_signal_and_sizes_position():
    config = RiskConfig(capital=100_000, risk_per_trade_pct=1.0, lot_size=1)
    manager = RiskManager(config)
    state = TradingDayState()
    decision = manager.validate_and_size(_sample_signal(), state)
    assert decision.approved
    # risk amount = 1000, risk per unit = 2.0 -> 500 shares
    assert decision.quantity == 500


@pytest.mark.parametrize("field,value", [("entry", float("nan")), ("stop_loss", float("nan")), ("entry", float("inf")),
                                         ("stop_loss", float("-inf")), ("target1", float("inf")), ("target2", float("nan"))])
def test_risk_manager_refuses_a_level_that_is_not_a_finite_number(field, value):
    """#308 review: a NaN level used to crash the sizing (int(NaN)) and leave the order at RISK_CHECK; an infinite target
    filled and then could not be stored. The reason names the level and its value."""
    signal = _sample_signal().model_copy(update={field: value})
    decision = RiskManager(legacy()).validate_and_size(signal, TradingDayState())
    name = {"stop_loss": "stop loss"}.get(field, field)
    assert decision.approved is False and decision.reasons == [f"Signal {name} {value!r} is not a finite number"]


def test_contract_plan_refuses_an_underlying_level_that_is_not_a_finite_number():
    """#309 review: an option's order levels come from its premium, so only the plan sees the underlying ones - which
    are stored on the trade and drive its exits."""
    from datetime import date

    from app.core.enums import InstrumentKind, OptionPosition, OrderSide
    from app.execution.contract_execution import ContractExecutionError, build_order_plan
    from app.instruments.contracts import ContractRules, ResolvedContract

    contract = ResolvedContract(kind=InstrumentKind.OPTION, underlying="NIFTY", underlying_symbol="NIFTY 50",
                                tradingsymbol="NIFTY24JAN21500CE", exchange="NFO", instrument_key="NSE_FO|1", lot_size=50,
                                tick_size=0.05, expiry=date(2024, 1, 25), strike=21500.0, right="CE", entry_side=OrderSide.BUY,
                                trade_direction=SignalDirection.LONG, position=OptionPosition.BUY)
    rules = ContractRules(kind=InstrumentKind.OPTION, position=OptionPosition.BUY)
    with pytest.raises(ContractExecutionError, match="stop loss nan is not a finite number"):
        build_order_plan(_sample_signal().model_copy(update={"stop_loss": float("nan")}), contract, rules, ltp=120.0)
    assert build_order_plan(_sample_signal(), contract, rules, ltp=120.0).order_signal.entry == 120.0


def test_risk_manager_rejects_when_daily_loss_limit_breached():
    config = RiskConfig(capital=100_000, max_daily_loss_pct=2.0)
    manager = RiskManager(config)
    state = TradingDayState(daily_pnl=-2500.0)
    decision = manager.validate_and_size(_sample_signal(), state)
    assert not decision.approved
    assert any("has reached your daily loss limit" in r for r in decision.reasons)


def test_risk_manager_rejects_when_max_trades_reached():
    config = RiskConfig(max_trades_per_day=3)
    manager = RiskManager(config)
    state = TradingDayState(trades_today=3)
    decision = manager.validate_and_size(_sample_signal(), state)
    assert not decision.approved


def test_risk_manager_sizes_mcx_commodity_off_its_own_lot_size():
    # risk_per_unit = 3.0 -> raw_qty = 333.33, which is NOT a clean multiple of 100 - this is
    # what actually distinguishes MCX lot-size flooring from the tenant's default lot_size=1.
    signal = _sample_signal(entry=100.0, sl=97.0, rr=2.0)
    config = RiskConfig(capital=100_000, risk_per_trade_pct=1.0)
    manager = RiskManager(config)
    state = TradingDayState()

    without_spec = manager.validate_and_size(signal, state)
    assert without_spec.quantity == 333  # tenant default lot_size=1, floors to a whole share

    crude_oil = get_contract_spec("CRUDEOIL")
    assert crude_oil is not None and crude_oil.lot_size == 100
    with_spec = manager.validate_and_size(signal, TradingDayState(), contract_spec=crude_oil)
    assert with_spec.approved
    assert with_spec.quantity == 300  # floors to 3 whole lots of 100, not 333 individual units


def test_risk_manager_sizes_crypto_fractionally():
    # A BTCINR-scale signal: entry/stop ~5,000,000/4,900,000 INR, so even a modest risk budget
    # only affords a fraction of one BTC - the entire point of `fractional=True` sizing.
    signal = _sample_signal(entry=5_000_000.0, sl=4_900_000.0, rr=2.0)
    config = RiskConfig(capital=100_000, risk_per_trade_pct=1.0)
    manager = RiskManager(config)
    state = TradingDayState()

    btc = get_contract_spec("BTCINR")
    assert btc is not None and btc.fractional
    decision = manager.validate_and_size(signal, state, contract_spec=btc)
    assert decision.approved
    assert 0 < decision.quantity < 1
    assert decision.quantity == 0.01


def test_risk_manager_rejects_no_trade_signal():
    config = legacy()
    manager = RiskManager(config)
    state = TradingDayState()
    no_trade = Signal(
        symbol="X", strategy_id="s", strategy_name="s",
        direction=SignalDirection.NO_TRADE, timestamp="2024-01-02T09:20:00",
    )
    decision = manager.validate_and_size(no_trade, state)
    assert not decision.approved


def test_order_router_fills_paper_trade():
    router = OrderRouter(mode=ExecutionMode.PAPER, risk_config=legacy(risk_per_trade_pct=1.0), product="MIS", order_style="MARKET")
    state = TradingDayState()
    result = asyncio.run(router.execute(_sample_signal(), state))
    assert result.executed
    assert result.trade is not None
    assert result.trade.quantity > 0
    assert state.trades_today == 1
    assert state.open_positions == 1


def test_order_router_blocks_live_mode_without_broker_adapter():
    router = OrderRouter(mode=ExecutionMode.LIVE, risk_config=legacy(), product="MIS", order_style="MARKET")
    state = TradingDayState()
    with pytest.raises(LiveTradingNotConfigured):
        asyncio.run(router.execute(_sample_signal(), state))


class _FakeBroker(BrokerInterface):
    """Minimal BrokerInterface double for testing the live execution path without real network calls."""

    name = "fake"

    def __init__(self, order_status: str = "OPEN") -> None:
        self.order_status = order_status
        self.placed_orders = []

    async def place_order(self, order):
        self.placed_orders.append(order)
        return BrokerOrderResponse(order_id="FAKE-1", status=self.order_status)

    async def authenticate(self): raise NotImplementedError
    async def get_profile(self): raise NotImplementedError
    async def get_instruments(self, exchange=None): raise NotImplementedError
    async def get_ltp(self, symbols): raise NotImplementedError
    async def get_quote(self, symbols): raise NotImplementedError
    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date): raise NotImplementedError
    async def get_option_chain(self, underlying, expiry=None): raise NotImplementedError
    async def modify_order(self, order_id, quantity=None, price=None, trigger_price=None, order_type=None):
        raise NotImplementedError
    async def cancel_order(self, order_id): raise NotImplementedError
    async def get_order_book(self): raise NotImplementedError
    async def get_trade_book(self): raise NotImplementedError
    async def get_positions(self): raise NotImplementedError
    async def get_holdings(self): raise NotImplementedError
    async def get_margins(self): raise NotImplementedError


def test_order_router_places_live_order_through_broker():
    broker = _FakeBroker()
    router = OrderRouter(
        mode=ExecutionMode.LIVE, risk_config=legacy(risk_per_trade_pct=1.0), broker=broker, product="MIS", order_style="MARKET")
    state = TradingDayState()
    result = asyncio.run(router.execute(_sample_signal(), state))

    assert result.executed
    assert result.broker_order_id == "FAKE-1"
    # Entry market order, then the protective SL-M on the opposite side at the signal's stop.
    assert len(broker.placed_orders) == 2
    assert broker.placed_orders[0].quantity == 500
    assert broker.placed_orders[0].order_type == "MARKET"
    assert broker.placed_orders[1].order_type == "SL-M"
    assert broker.placed_orders[1].transaction_type != broker.placed_orders[0].transaction_type
    assert broker.placed_orders[1].trigger_price == _sample_signal().stop_loss
    assert result.sl_order_id == "FAKE-1"
    assert result.trade is not None and result.trade.quantity == 500
    assert state.trades_today == 1


def test_order_router_reports_broker_rejection():
    broker = _FakeBroker(order_status="REJECTED")
    router = OrderRouter(mode=ExecutionMode.LIVE, risk_config=legacy(risk_per_trade_pct=1.0), broker=broker, product="MIS", order_style="MARKET")
    state = TradingDayState()
    result = asyncio.run(router.execute(_sample_signal(), state))

    assert not result.executed
    assert state.trades_today == 0
    assert any("rejected" in r.lower() for r in result.reasons)
