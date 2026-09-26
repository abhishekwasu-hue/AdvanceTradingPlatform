"""Master prompt Section 50: disaster-simulation tests ("kill DB mid-order, kill broker
mid-fill"). Covers the "kill broker mid-fill" case directly: a real bug this session found by
reading `OrderRouter.execute` (app/execution/router.py) - `await self.broker.place_order(...)`
had no exception handling at all, so a broker call that errors mid-flight (network failure,
timeout, the broker's own service being down) would propagate all the way up through
`execute_signal_for_user` as an unhandled exception, leaving the order stuck at RISK_CHECK
forever: never REJECTED, never FAILED, invisible to any "list my pending orders" query, and the
caller's HTTP request ending in an unhandled 500 instead of a clean error response.

Fixed by catching the exception in `OrderRouter.execute` and returning
`ExecutionResult(executed=False, system_failure=True, ...)` - a new `system_failure` flag that
distinguishes "the broker was reachable and explicitly declined this order" (REJECTED - a business
decision) from "the broker call itself failed, so this platform never got a real answer" (FAILED -
a system failure, notified as one). `RISK_CHECK -> FAILED` was added to the order state machine's
allowed transitions (app/execution/order_state_machine.py) specifically to make this reachable -
it was not a legal transition before this fix.

LIVE mode (`OrderRouter(mode=ExecutionMode.LIVE, broker=...)`) is not yet wired into the shared
`execute_signal_for_user` pipeline (see that module's own scoping) - it is only reachable by
constructing `OrderRouter` directly, so these tests exercise it at that level, the same way the
existing `test_risk_and_execution.py` broker tests already do.
"""
import asyncio

import pytest

from app.brokers.base import BrokerInterface
from app.core.enums import ExecutionMode, OrderStatus, SignalDirection
from app.core.models import RiskConfig, Signal
from app.execution.order_state_machine import assert_valid_transition
from app.execution.router import OrderRouter
from app.risk_engine.risk_manager import TradingDayState


def _sample_signal() -> Signal:
    return Signal(
        symbol="TESTSYM", strategy_id="test", strategy_name="Test",
        direction=SignalDirection.LONG, timestamp="2024-01-02T09:20:00",
        entry=100.0, stop_loss=98.0, target1=104.0, target2=108.0,
        risk_reward=2.0, score=85,
    )


class _BrokerDownMidFill(BrokerInterface):
    """A broker whose place_order raises, simulating exactly the "kill broker mid-fill"
    scenario: a network failure, a timeout, or the broker's own service erroring while this
    platform is waiting for a response to an order it already committed to sending.
    """

    name = "broker-down"

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def place_order(self, order):
        raise self._exc

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


def test_order_router_survives_a_broker_exception_mid_placement():
    """The core regression: this used to propagate the raw exception (confirmed by reverting the
    fix locally before writing this test) - it must now come back as a clean, non-executed result.
    """
    broker = _BrokerDownMidFill(ConnectionError("connection reset by peer"))
    router = OrderRouter(mode=ExecutionMode.LIVE, risk_config=RiskConfig(risk_per_trade_pct=1.0), broker=broker)
    state = TradingDayState()

    result = asyncio.run(router.execute(_sample_signal(), state))

    assert result.executed is False
    assert result.system_failure is True
    assert "connection reset by peer" in result.reasons[0]
    # Never counted as a real fill - a system failure must not silently inflate today's trade
    # count or open-position count the way a genuine fill/rejection legitimately would.
    assert state.trades_today == 0
    assert state.open_positions == 0


def test_order_router_survives_a_broker_timeout_mid_placement():
    """A timeout is the single most likely real-world trigger for this - tested as its own case
    rather than assuming any Exception subclass is handled identically by coincidence.
    """
    broker = _BrokerDownMidFill(asyncio.TimeoutError("broker did not respond in time"))
    router = OrderRouter(mode=ExecutionMode.LIVE, risk_config=RiskConfig(risk_per_trade_pct=1.0), broker=broker)
    state = TradingDayState()

    result = asyncio.run(router.execute(_sample_signal(), state))

    assert result.executed is False
    assert result.system_failure is True


def test_broker_rejection_is_not_flagged_as_a_system_failure():
    """A broker explicitly declining an order (REJECTED/CANCELLED status, no exception) is a
    business outcome, not a system failure - system_failure must stay False here so it's routed
    to REJECTED, not FAILED, by the caller.
    """
    from app.brokers.models import BrokerOrderResponse

    class _RejectingBroker(_BrokerDownMidFill):
        async def place_order(self, order):
            return BrokerOrderResponse(order_id="X", status="REJECTED", message="insufficient margin")

    broker = _RejectingBroker(RuntimeError("unused"))
    router = OrderRouter(mode=ExecutionMode.LIVE, risk_config=RiskConfig(risk_per_trade_pct=1.0), broker=broker)
    result = asyncio.run(router.execute(_sample_signal(), TradingDayState()))

    assert result.executed is False
    assert result.system_failure is False


def test_risk_check_can_now_transition_to_failed():
    """The state-machine change this fix depended on: RISK_CHECK -> FAILED must be legal so a
    broker-call system failure discovered during that stage can actually be recorded, instead of
    the order being stuck at RISK_CHECK forever (which is exactly what happened before this fix,
    since transition_order would have raised InvalidOrderTransition trying to reach FAILED from
    there).
    """
    assert_valid_transition(OrderStatus.RISK_CHECK, OrderStatus.FAILED)  # must not raise


def test_risk_check_still_cannot_transition_directly_to_position_open():
    """A sanity check that the new transition was added narrowly, not by accidentally loosening
    the state machine wholesale.
    """
    from app.execution.order_state_machine import InvalidOrderTransition

    with pytest.raises(InvalidOrderTransition):
        assert_valid_transition(OrderStatus.RISK_CHECK, OrderStatus.POSITION_OPEN)
