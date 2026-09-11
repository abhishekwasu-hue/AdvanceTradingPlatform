from datetime import datetime

from app.core.enums import SignalDirection
from app.core.models import Signal, Trade


class PaperBroker:
    """Simulates fills for paper trading using real-time/last-traded prices with a basic cost model.

    Costs approximate NSE intraday equity charges; brokers/segments vary, and this is only
    used to keep paper-trading P&L realistic, never for actual order routing.
    """

    def __init__(
        self,
        brokerage_per_order: float = 20.0,
        stt_pct: float = 0.025,
        exchange_pct: float = 0.00345,
        gst_pct: float = 18.0,
        slippage_pct: float = 0.02,
    ) -> None:
        self.brokerage_per_order = brokerage_per_order
        self.stt_pct = stt_pct
        self.exchange_pct = exchange_pct
        self.gst_pct = gst_pct
        self.slippage_pct = slippage_pct

    def _slip(self, price: float, direction: SignalDirection) -> float:
        slip = price * self.slippage_pct / 100
        return price + slip if direction == SignalDirection.LONG else price - slip

    def estimate_round_trip_costs(self, entry_price: float, exit_price: float, quantity: int) -> float:
        turnover = (entry_price + exit_price) * quantity
        stt = turnover * self.stt_pct / 100
        exchange = turnover * self.exchange_pct / 100
        brokerage = self.brokerage_per_order * 2
        gst = (brokerage + exchange) * self.gst_pct / 100
        return round(stt + exchange + brokerage + gst, 2)

    def open_trade(self, signal: Signal, quantity: int, timestamp: datetime) -> Trade:
        fill_price = self._slip(signal.entry, signal.direction)
        return Trade(
            symbol=signal.symbol,
            strategy_id=signal.strategy_id,
            direction=signal.direction,
            entry_time=timestamp,
            entry_price=round(fill_price, 2),
            quantity=quantity,
            stop_loss=signal.stop_loss,
            target1=signal.target1,
            target2=signal.target2,
        )

    def close_trade(self, trade: Trade, exit_price: float, exit_time: datetime, reason: str) -> Trade:
        direction_sign = 1 if trade.direction == SignalDirection.LONG else -1
        gross_pnl = direction_sign * (exit_price - trade.entry_price) * trade.quantity
        charges = self.estimate_round_trip_costs(trade.entry_price, exit_price, trade.quantity)
        trade.exit_price = round(exit_price, 2)
        trade.exit_time = exit_time
        trade.exit_reason = reason
        trade.charges = charges
        trade.pnl = round(gross_pnl - charges, 2)
        return trade
