from datetime import datetime, timezone
from typing import Optional

from app.brokers.base import BrokerInterface
from app.brokers.models import BrokerOrderRequest
from app.core.enums import ExecutionMode, SignalDirection, OrderSide
from app.core.models import RiskConfig, Signal, Trade
from app.execution.paper_broker import PaperBroker
from app.instruments.registry import get_contract_spec
from app.risk_engine.risk_manager import RiskManager, TradingDayState


class LiveTradingNotConfigured(RuntimeError):
    """Raised whenever LIVE mode is requested without a real, authenticated broker adapter wired in.

    This is intentional: the platform must never place a live order through a stub. A concrete
    BrokerInterface implementation (Zerodha/Upstox/Angel One/Fyers/Dhan/...) has to be authenticated
    and passed to OrderRouter before ExecutionMode.LIVE can do anything.
    """


class ExecutionResult:
    def __init__(
        self, executed: bool, reasons: list[str], trade: Optional[Trade] = None, broker_order_id: Optional[str] = None
    ) -> None:
        self.executed = executed
        self.reasons = reasons
        self.trade = trade
        self.broker_order_id = broker_order_id


class OrderRouter:
    """Routes an approved signal to Paper or Live execution. Every signal passes the Risk Engine first.

    LIVE mode never places an order without an authenticated `broker` (a concrete BrokerInterface
    implementation) being handed in - there is no fallback or silent no-op, per the platform's
    safety rule that live trading must never bypass risk validation or run without a real broker.
    """

    def __init__(
        self, mode: ExecutionMode, risk_config: RiskConfig, broker: Optional[BrokerInterface] = None,
        exchange: str = "NSE", product: str = "MIS",
    ) -> None:
        self.mode = mode
        self.risk_manager = RiskManager(risk_config)
        self.paper_broker = PaperBroker()
        self.broker = broker
        self.exchange = exchange
        self.product = product

    async def execute(self, signal: Signal, state: TradingDayState) -> ExecutionResult:
        contract_spec = get_contract_spec(signal.symbol)
        decision = self.risk_manager.validate_and_size(signal, state, contract_spec=contract_spec)
        if not decision.approved:
            return ExecutionResult(executed=False, reasons=decision.reasons)

        if self.mode == ExecutionMode.PAPER:
            trade = self.paper_broker.open_trade(signal, decision.quantity, datetime.now(timezone.utc))
            state.trades_today += 1
            state.open_positions += 1
            return ExecutionResult(executed=True, reasons=["Paper order filled"], trade=trade)

        if self.broker is None:
            raise LiveTradingNotConfigured(
                "Live trading is disabled until a real broker adapter (BrokerInterface) is authenticated "
                "and passed to OrderRouter. Use ExecutionMode.PAPER until then."
            )

        order_request = BrokerOrderRequest(
            symbol=signal.symbol,
            exchange=self.exchange,
            transaction_type=OrderSide.BUY if signal.direction == SignalDirection.LONG else OrderSide.SELL,
            quantity=decision.quantity,
            order_type="MARKET",
            product=self.product,
            tag=f"{signal.strategy_id}:{signal.grade.value}",
        )
        response = await self.broker.place_order(order_request)
        if response.status in ("REJECTED", "CANCELLED"):
            return ExecutionResult(executed=False, reasons=[f"Broker rejected order: {response.message or response.status}"])

        state.trades_today += 1
        state.open_positions += 1
        return ExecutionResult(
            executed=True, reasons=[f"Live order placed via {self.broker.name}: {response.order_id}"],
            broker_order_id=response.order_id,
        )
