from dataclasses import dataclass
from typing import Optional

from app.core.models import RiskConfig, RiskDecision, Signal
from app.instruments.models import ContractSpec


@dataclass
class TradingDayState:
    """Mutable counters the risk engine checks before approving every order.

    A real deployment persists this per trading day in Redis/Postgres and updates it
    as fills and closes come back from the broker; this in-memory version is enough
    to drive paper trading and backtests.
    """

    trades_today: int = 0
    daily_pnl: float = 0.0
    consecutive_losses: int = 0
    open_positions: int = 0


class RiskManager:
    """Every order, paper or live, must pass through here before it reaches execution."""

    def __init__(self, config: RiskConfig) -> None:
        self.config = config

    def validate_and_size(
        self, signal: Signal, state: TradingDayState, contract_spec: Optional[ContractSpec] = None,
    ) -> RiskDecision:
        """`contract_spec` is only supplied for symbols the instrument registry recognizes
        (MCX commodities, crypto pairs) - see app/instruments/registry.py. Every plain NSE/BSE
        equity or index-option symbol passes None here and sizes exactly as before, off the
        tenant's own configured `RiskConfig.lot_size`, so this stays fully backward compatible.
        """
        reasons = []

        if not signal.is_tradeable:
            return RiskDecision(approved=False, reasons=["Signal is NO_TRADE"])
        if signal.entry is None or signal.stop_loss is None:
            return RiskDecision(approved=False, reasons=["Signal missing entry/stop loss"])
        if signal.risk_reward is not None and signal.risk_reward < self.config.min_risk_reward:
            reasons.append(
                f"Risk/Reward {signal.risk_reward:.2f} below configured minimum {self.config.min_risk_reward:.2f}"
            )
        if state.trades_today >= self.config.max_trades_per_day:
            reasons.append(f"Max trades per day reached ({self.config.max_trades_per_day})")
        if state.open_positions >= self.config.max_open_positions:
            reasons.append(f"Max open positions reached ({self.config.max_open_positions})")
        if state.consecutive_losses >= self.config.max_consecutive_losses:
            reasons.append(f"Max consecutive losses reached ({self.config.max_consecutive_losses}) - cool down")

        max_daily_loss_amount = -abs(self.config.capital * self.config.max_daily_loss_pct / 100)
        if state.daily_pnl <= max_daily_loss_amount:
            reasons.append(f"Daily loss limit breached (P&L {state.daily_pnl:.2f})")

        if reasons:
            return RiskDecision(approved=False, reasons=reasons)

        risk_amount = self.config.capital * self.config.risk_per_trade_pct / 100
        risk_per_unit = abs(signal.entry - signal.stop_loss)
        if risk_per_unit <= 0:
            return RiskDecision(approved=False, reasons=["Invalid risk per unit (entry == stop loss)"])

        raw_qty = risk_amount / risk_per_unit

        if contract_spec is not None and contract_spec.fractional:
            # No such thing as "one lot" of a spot crypto pair - round down to the instrument's
            # own smallest quantity increment instead of flooring to a whole multiple of it.
            increments = int(raw_qty / contract_spec.lot_size)
            quantity = round(increments * contract_spec.lot_size, 8)
        else:
            unit = contract_spec.lot_size if contract_spec is not None else self.config.lot_size
            lots = int(raw_qty // unit)
            quantity = lots * unit

        if quantity <= 0:
            return RiskDecision(
                approved=False,
                reasons=["Computed position size is below one lot for the configured risk per trade"],
            )

        return RiskDecision(approved=True, quantity=quantity, reasons=["Risk checks passed"])
