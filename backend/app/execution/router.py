from datetime import datetime, timezone
from typing import Optional

from app.core.enums import ExecutionMode
from app.core.models import RiskConfig, Signal, Trade
from app.execution.paper_broker import PaperBroker
from app.risk_engine.risk_manager import RiskManager, TradingDayState


class LiveTradingNotConfigured(RuntimeError):
    """Raised whenever LIVE mode is requested without a real, authenticated broker adapter wired in.

    This is intentional: the platform must never place a live order through a stub. A concrete
    BrokerInterface implementation (Zerodha/Upstox/Angel One/Fyers/Dhan/...) has to be registered
    here before ExecutionMode.LIVE can do anything.
    """


class ExecutionResult:
    def __init__(self, executed: bool, reasons: list[str], trade: Optional[Trade] = None) -> None:
        self.executed = executed
        self.reasons = reasons
        self.trade = trade


class OrderRouter:
    """Routes an approved signal to Paper or Live execution. Every signal passes the Risk Engine first."""

    def __init__(self, mode: ExecutionMode, risk_config: RiskConfig) -> None:
        self.mode = mode
        self.risk_manager = RiskManager(risk_config)
        self.paper_broker = PaperBroker()

    def execute(self, signal: Signal, state: TradingDayState) -> ExecutionResult:
        decision = self.risk_manager.validate_and_size(signal, state)
        if not decision.approved:
            return ExecutionResult(executed=False, reasons=decision.reasons)

        if self.mode == ExecutionMode.PAPER:
            trade = self.paper_broker.open_trade(signal, decision.quantity, datetime.now(timezone.utc))
            state.trades_today += 1
            state.open_positions += 1
            return ExecutionResult(executed=True, reasons=["Paper order filled"], trade=trade)

        raise LiveTradingNotConfigured(
            "Live trading is disabled until a real broker adapter (BrokerInterface) is authenticated "
            "and registered for this account. Use ExecutionMode.PAPER until then."
        )
