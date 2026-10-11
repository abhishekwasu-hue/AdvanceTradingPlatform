from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from app.core.enums import SignalDirection
from app.execution import india_costs
from app.core.models import Signal, Trade

IST = ZoneInfo("Asia/Kolkata")


class PaperBroker:
    """Simulates fills for paper trading using real-time/last-traded prices with a dated cost model.

    Charges come from `app.execution.india_costs` (rates of the trade's own date); only brokerage per order and slippage
    are this broker's settings. `stt_pct`, `exchange_pct` and `gst_pct` are kept for callers that pass them and are no
    longer used. Only for paper P&L and backtests - never for order routing; a contract note replaces the estimate.
    """

    def __init__(
        self,
        brokerage_per_order: float = 20.0,
        stt_pct: float = 0.025,
        exchange_pct: float = 0.00345,
        gst_pct: float = 18.0,
        slippage_pct: float = 0.02,
        slippage_model=None,
    ) -> None:
        self.brokerage_per_order = brokerage_per_order
        self.stt_pct = stt_pct
        self.exchange_pct = exchange_pct
        self.gst_pct = gst_pct
        self.slippage_pct = slippage_pct
        self.slippage_model = slippage_model    # realism C2: a backtest SlippageModel; None keeps the fixed percentage

    def _slip(self, price: float, direction: SignalDirection) -> float:
        if self.slippage_model is not None:
            return self.slippage_model.apply(price, direction)
        slip = price * self.slippage_pct / 100
        return price + slip if direction == SignalDirection.LONG else price - slip

    # Trade port (Trade elliott/costs.py): every estimate uses `app.execution.india_costs` - the statutory rates of the
    # trade's own date, STT attached to the executed SELL leg (both legs for delivery), stamp duty to the buy leg. The
    # old fixed profiles charged equity STT on both sides ("STT on the wrong side") and kept options at 0.1% after the
    # 1 Apr 2026 change. Brokerage per order stays this broker's setting.

    @staticmethod
    def _day(when: Optional[object]) -> date:
        if when is None:
            return datetime.now(IST).date()
        if isinstance(when, datetime):
            return (when.astimezone(IST) if when.tzinfo else when).date()
        return when if isinstance(when, date) else datetime.fromisoformat(str(when)).date()

    def exercise_charges(self, intrinsic_per_unit: float, quantity: float, trade_date: Optional[object] = None) -> float:
        """P0.6 / B2: a long option settled in the money is treated as exercised - STT on the intrinsic value."""
        return round(india_costs.exercise_cost(self._day(trade_date), intrinsic_per_unit, intrinsic_per_unit, quantity), 2)

    def estimate_round_trip_costs(self, entry_price: float, exit_price: float, quantity: float, instrument_kind: str = "UNDERLYING",
                                  *, sold_first: bool = False, settled: bool = False, entry_date: Optional[object] = None,
                                  exit_date: Optional[object] = None, delivery: bool = False) -> float:
        """`settled` (P0.6 / B2): the position ended by expiry settlement, not by an exit order - one brokerage, and no
        closing charges (pass the exit as 0.0; exercise STT is `exercise_charges`). `sold_first`: the position was opened
        with a sell (a written option, a short future / stock) - sell-side STT then belongs to the entry. Dates default to
        today (IST); the exit date defaults to the entry date."""
        segment = india_costs.segment_for(instrument_kind, delivery=delivery)
        d_in = self._day(entry_date)
        d_out = self._day(exit_date) if exit_date is not None else d_in
        cost = india_costs.round_trip(d_in, d_out, entry_price, exit_price, quantity, segment, sold_first=sold_first, settled=settled,
                                      brokerage_per_order=self.brokerage_per_order)
        return round(cost["total"], 2)

    def open_trade(self, signal: Signal, quantity: float, timestamp: datetime) -> Trade:
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
        charges = self.estimate_round_trip_costs(trade.entry_price, exit_price, trade.quantity,
                                                 sold_first=trade.direction == SignalDirection.SHORT, entry_date=trade.entry_time, exit_date=exit_time)
        trade.exit_price = round(exit_price, 2)
        trade.exit_time = exit_time
        trade.exit_reason = reason
        trade.charges = charges
        trade.pnl = round(gross_pnl - charges, 2)
        return trade
