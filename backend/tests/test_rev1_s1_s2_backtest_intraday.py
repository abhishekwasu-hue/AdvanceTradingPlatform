"""REV-S1 / REV-S2 (owner 30 Oct): backtest = live for INTRADAY, and the stop before a time exit on one bar.

S1: run_backtest takes the worker's INTRADAY rules from the same venue clock (market_data.calendar.intraday_cutoffs): no new
entry once a bar closes at / after the cut-off (NSE 15:00) and the square-off at the square-off time (NSE 15:15), so a test
never holds an intraday trade overnight. A daily-only (SWING) strategy is untouched; a 24x7 / MCX venue keeps its own clock.
S2: on the bar where a time exit fires, a stop inside that bar exits at the stop; the time exit takes the close otherwise.

M68 PR-3 (D1): the cut-off and the square-off are now the USER's own (Trade settings, passed as `cutoffs`); these tests run
as a user who saved the old NSE times (SAVED); with nothing saved there is neither (the last two tests).
"""
from datetime import time, timedelta

import pandas as pd

from app.backtest.engine import run_backtest
from app.core.enums import SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.trading.exit_rules import ExitRules

CFG = RiskConfig(capital=100_000, risk_per_trade_pct=1.0)
STOP_GAP = 2.0
SAVED = (None, time(15, 0), time(15, 15))        # M68 PR-3: a user who saved "new entries until 15:00" and "square-off 15:15"


class _LongAt:
    """LONG on the bar that starts at each given IST time (stop STOP_GAP below, a far target), else no trade."""
    id = "long_at"
    params: dict = {}

    def __init__(self, *at: str, timeframe: str = "1min"):
        self.at = {pd.Timestamp(t, tz="Asia/Kolkata") for t in at}
        self.timeframes = [timeframe]

    def min_history(self):
        return {self.timeframes[0]: 2}

    def analyze(self, data, symbol):
        df = data[self.timeframes[0]]
        last = df.index[-1]
        price = float(df["close"].iloc[-1])
        common = dict(symbol=symbol, strategy_id=self.id, strategy_name="long at", timestamp=last.to_pydatetime(), reasons=[],
                      timeframe_combo=self.timeframes[0])
        if last in self.at:
            return Signal(direction=SignalDirection.LONG, entry=price, stop_loss=price - STOP_GAP, target1=price + 1000, target2=None,
                          risk_reward=500, score=90, grade=SignalGrade.HIGH_QUALITY, **common)
        return Signal(direction=SignalDirection.NO_TRADE, entry=None, stop_loss=None, target1=None, target2=None, risk_reward=None,
                      score=0, grade=SignalGrade.NO_TRADE, **common)


def _flat(start: str, periods: int, freq: str = "1min", price: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range(start, periods=periods, freq=freq, tz="Asia/Kolkata")
    return pd.DataFrame({"open": price, "high": price + 0.5, "low": price - 0.5, "close": price, "volume": 10}, index=idx)


def _days(*spans: tuple[str, int]) -> pd.DataFrame:
    return pd.concat([_flat(start, n) for start, n in spans])


# ---- S1: the entry cut-off -------------------------------------------------------------------------------------------------

def test_a_signal_on_the_bar_closing_at_1505_is_not_entered():
    df = _flat("2026-10-26 14:30", 50)                                   # 14:30 .. 15:19
    late = run_backtest(_LongAt("2026-10-26 15:04"), df, "NIFTY", "1min", CFG, cutoffs=SAVED)   # that bar closes 15:05
    assert late.total_trades == 0


def test_a_signal_before_the_cut_off_is_entered():
    df = _flat("2026-10-26 14:30", 50)
    early = run_backtest(_LongAt("2026-10-26 14:58"), df, "NIFTY", "1min", CFG, cutoffs=SAVED)  # closes 14:59
    assert early.total_trades == 1


def test_the_bar_closing_exactly_at_the_cut_off_takes_no_entry():
    df = _flat("2026-10-26 14:30", 50)
    assert run_backtest(_LongAt("2026-10-26 14:59"), df, "NIFTY", "1min", CFG, cutoffs=SAVED).total_trades == 0   # closes 15:00


# ---- S1: the square-off ----------------------------------------------------------------------------------------------------

def test_an_open_intraday_trade_is_closed_at_1515():
    df = _flat("2026-10-26 14:00", 80)                                   # 14:00 .. 15:19, nothing hits the stop
    result = run_backtest(_LongAt("2026-10-26 14:10"), df, "NIFTY", "1min", CFG, cutoffs=SAVED)
    trade = result.trades[0]
    assert trade.exit_reason == "Square-off (15:15 IST)"
    assert pd.Timestamp(trade.exit_time) == pd.Timestamp("2026-10-26 15:15", tz="Asia/Kolkata")
    assert trade.exit_price == 100.0                                       # the 15:14 bar's close


def test_a_test_never_holds_an_intraday_trade_overnight_when_the_data_skips_the_close():
    df = _days(("2026-10-26 14:00", 45), ("2026-10-27 09:15", 30))       # day 1 ends 14:44: no square-off bar
    result = run_backtest(_LongAt("2026-10-26 14:10"), df, "NIFTY", "1min", CFG, cutoffs=SAVED)
    trade = result.trades[0]
    assert trade.exit_reason == "Square-off (15:15 IST)"
    assert pd.Timestamp(trade.exit_time) == pd.Timestamp("2026-10-27 09:15", tz="Asia/Kolkata")   # the next bar's open


def test_a_daily_swing_strategy_is_not_squared_off():
    df = _flat("2026-09-01", 30, freq="1D")
    result = run_backtest(_LongAt("2026-09-05", timeframe="day"), df, "NIFTY", "day", CFG)
    assert result.total_trades == 1 and result.trades[0].exit_reason == "End of backtest"


def test_the_venue_clock_follows_the_symbol_mcx_trades_past_1500():
    df = _flat("2026-10-26 14:30", 60)
    result = run_backtest(_LongAt("2026-10-26 15:04"), df, "GOLD", "1min", CFG,
                          cutoffs=(None, time(23, 0), time(23, 15)))       # his MCX times: cut-off 23:00, square-off 23:15
    assert result.total_trades == 1 and result.trades[0].exit_reason == "End of backtest"


def test_m68_nothing_saved_no_cut_off_and_no_square_off():
    """M68 PR-3 (D1): with no time of his own the 15:04 signal is entered and nothing squares it off (live does the same)."""
    df = _flat("2026-10-26 14:30", 50)                                   # 14:30 .. 15:19
    result = run_backtest(_LongAt("2026-10-26 15:04"), df, "NIFTY", "1min", CFG)
    assert result.total_trades == 1 and result.trades[0].exit_reason == "End of backtest"


def test_m68_his_own_window_start_holds_back_an_early_entry():
    df = _flat("2026-10-26 14:30", 50)
    assert run_backtest(_LongAt("2026-10-26 14:40"), df, "NIFTY", "1min", CFG, cutoffs=(time(14, 45), None, None)).total_trades == 0
    assert run_backtest(_LongAt("2026-10-26 14:50"), df, "NIFTY", "1min", CFG, cutoffs=(time(14, 45), None, None)).total_trades == 1


# ---- S2: the stop before a time exit on the same bar ---------------------------------------------------------------------

def _stop_inside(df: pd.DataFrame, at: str) -> pd.DataFrame:
    df = df.copy()
    ts = pd.Timestamp(at, tz="Asia/Kolkata")
    df.loc[ts, "low"] = 100.0 - STOP_GAP - 1.0                             # this bar trades through the stop
    return df


def test_a_stop_inside_the_time_exit_bar_exits_at_the_stop():
    df = _stop_inside(_flat("2026-10-26 10:00", 40), "2026-10-26 10:15")   # entry 10:05; the 10m time exit fires at 10:15
    result = run_backtest(_LongAt("2026-10-26 10:05"), df, "NIFTY", "1min", CFG, exit_rules=ExitRules(time_exit_minutes=10))
    trade = result.trades[0]
    assert trade.exit_reason == "Stop Loss"
    assert trade.exit_price < 100.0 and abs(trade.exit_price - trade.stop_loss) < 0.5   # the stop (paper slippage only)


def test_without_a_stop_inside_the_bar_the_time_exit_takes_the_close():
    df = _flat("2026-10-26 10:00", 40)
    result = run_backtest(_LongAt("2026-10-26 10:05"), df, "NIFTY", "1min", CFG, exit_rules=ExitRules(time_exit_minutes=10))
    trade = result.trades[0]
    assert trade.exit_reason == "Time exit (10m in trade)"
    assert trade.exit_time - trade.entry_time == timedelta(minutes=10) and trade.exit_price == 100.0


def test_a_stop_inside_the_square_off_bar_exits_at_the_stop():
    df = _stop_inside(_flat("2026-10-26 14:00", 80), "2026-10-26 15:14")
    trade = run_backtest(_LongAt("2026-10-26 14:10"), df, "NIFTY", "1min", CFG, cutoffs=SAVED).trades[0]
    assert trade.exit_reason == "Stop Loss"
