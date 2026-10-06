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

    # Approximate NSE charge profiles by instrument kind (percent of turnover), used only until a
    # contract note replaces the estimate (Phase D4). Options: STT on the sell-side premium,
    # exchange transaction charge on premium turnover; futures: STT sell side on notional.
    COST_PROFILES = {
        "UNDERLYING": {"stt_sell": 0.025, "stt_buy": 0.0, "exchange": 0.00345, "sebi": 0.0001, "stamp_buy": 0.003},
        "OPTION": {"stt_sell": 0.1, "stt_buy": 0.0, "exchange": 0.035, "sebi": 0.0001, "stamp_buy": 0.003},
        "FUTURE": {"stt_sell": 0.02, "stt_buy": 0.0, "exchange": 0.00173, "sebi": 0.0001, "stamp_buy": 0.002},
    }

    # STT the buyer pays when an in-the-money option is exercised/settled at expiry: 0.125% of the intrinsic value.
    EXERCISE_STT_PCT = 0.125

    def exercise_charges(self, intrinsic_per_unit: float, quantity: float) -> float:
        """P0.6 / B2: a long option settled in the money is treated as exercised - STT on the intrinsic value."""
        if intrinsic_per_unit <= 0 or quantity <= 0:
            return 0.0
        return round(intrinsic_per_unit * quantity * self.EXERCISE_STT_PCT / 100, 2)

    def estimate_round_trip_costs(self, entry_price: float, exit_price: float, quantity: float, instrument_kind: str = "UNDERLYING",
                                  *, sold_first: bool = False, settled: bool = False) -> float:
        """`settled` (P0.6 / B2): the position ended by expiry settlement, not by an exit order - one brokerage, and no
        stamp duty on a buy-back that never happened (pass the exit as 0.0; exercise STT is `exercise_charges`).
        `sold_first` (P0.6 / B2): the position was opened with a sell (a written option, a short future) - the
        sell-side STT then belongs to the *entry* premium and stamp duty to the exit, not the other way round."""
        profile = self.COST_PROFILES.get((instrument_kind or "UNDERLYING").upper())
        if profile is None or instrument_kind in (None, "UNDERLYING"):
            turnover = (entry_price + exit_price) * quantity
            stt = turnover * self.stt_pct / 100
            exchange = turnover * self.exchange_pct / 100
            brokerage = self.brokerage_per_order * 2
            gst = (brokerage + exchange) * self.gst_pct / 100
            return round(stt + exchange + brokerage + gst, 2)
        # One leg is a buy and one a sell whichever way the trade went; sell-side STT applies to
        # the sell leg's turnover, stamp duty to the buy leg's.
        legs = [entry_price * quantity, exit_price * quantity]
        turnover = sum(legs)
        # For a bought contract the exit is the sell leg; for a written/short one the entry is.
        sell_turnover, buy_turnover = (legs[0], legs[1]) if sold_first else (legs[1], legs[0])
        stt = sell_turnover * profile["stt_sell"] / 100 + buy_turnover * profile["stt_buy"] / 100
        exchange = turnover * profile["exchange"] / 100
        sebi = turnover * profile["sebi"] / 100
        stamp = buy_turnover * profile["stamp_buy"] / 100
        brokerage = self.brokerage_per_order * (1 if settled else 2)
        gst = (brokerage + exchange + sebi) * self.gst_pct / 100
        return round(stt + exchange + sebi + stamp + brokerage + gst, 2)

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
        charges = self.estimate_round_trip_costs(trade.entry_price, exit_price, trade.quantity)
        trade.exit_price = round(exit_price, 2)
        trade.exit_time = exit_time
        trade.exit_reason = reason
        trade.charges = charges
        trade.pnl = round(gross_pnl - charges, 2)
        return trade
