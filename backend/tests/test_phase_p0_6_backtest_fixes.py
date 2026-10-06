"""P0.6: the Greeks clock runs to the 15:30 IST close (T7); the backtest engine resets its day counters per Indian
trading day (B1), charges STT on the leg that was actually sold and on exercise (B2), ranks the optimizer in-sample
and validates out-of-sample (B3), fills gapped levels at the open (B4) and sizes options with the lot of the entry
day (B5)."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from app.backtest.engine import ENGINE_VERSION, run_backtest
from app.backtest.optimizer import optimize
from app.backtest.options import LOT_SIZE_HISTORY, LOT_SIZES, lot_size_on
from app.backtest.options_engine import OptionBacktestConfig, run_option_backtest
from app.core.enums import OptionPosition, OptionStrategy, SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.execution.paper_broker import PaperBroker
from app.option_chain.greeks import time_to_expiry_years
from app.trading.exit_logic import determine_exit_price
from tests.utils import make_series, noisy_uptrend

IST = ZoneInfo("Asia/Kolkata")


class _EveryBar:
    """Fires a LONG signal on every bar it is asked (stop 2% away, target 0.5% away so the trade closes fast)."""
    id = "every_bar_p06"
    timeframes = ["1min"]

    def min_history(self):
        return {"1min": 3}

    def analyze(self, data, symbol):
        df = data["1min"]
        ts, px = df.index[-1], float(df["close"].iloc[-1])
        return Signal(symbol=symbol, strategy_id=self.id, strategy_name="x", direction=SignalDirection.LONG, timestamp=ts, entry=px,
                      stop_loss=round(px * 0.99, 2), target1=round(px * 1.013, 2), target2=None, risk_reward=1.3, score=80,
                      grade=SignalGrade.HIGH_QUALITY)


class _OnceLong:
    id = "once_p06"
    timeframes = ["1min"]

    def __init__(self, stop, target1, target2=None):
        self.stop, self.target1, self.target2, self.fired = stop, target1, target2, False

    def min_history(self):
        return {"1min": 1}

    def analyze(self, data, symbol):
        df = data["1min"]
        ts, px = df.index[-1], float(df["close"].iloc[-1])
        if self.fired:
            return Signal(symbol=symbol, strategy_id=self.id, strategy_name="x", direction=SignalDirection.NO_TRADE, timestamp=ts)
        self.fired = True
        return Signal(symbol=symbol, strategy_id=self.id, strategy_name="x", direction=SignalDirection.LONG, timestamp=ts, entry=px,
                      stop_loss=self.stop, target1=self.target1, target2=self.target2, risk_reward=2.0, score=80, grade=SignalGrade.HIGH_QUALITY)


# --- T7 ------------------------------------------------------------------------------------------------------------------
def test_time_to_expiry_runs_to_the_close_not_whole_days():
    expiry = date(2026, 10, 6)
    at_11_ist = datetime(2026, 10, 6, 11, 0, tzinfo=IST)
    assert time_to_expiry_years(expiry, at_11_ist) * 365 * 24 == pytest.approx(4.5)        # 0DTE at 11:00: 4.5 hours, not the floor
    assert time_to_expiry_years(expiry, datetime(2026, 10, 6, 15, 0, tzinfo=IST)) * 365 * 24 == pytest.approx(1.0)   # the one-hour floor
    assert time_to_expiry_years(expiry, datetime(2026, 10, 6, 16, 0, tzinfo=IST)) == pytest.approx(1 / 365 / 24)   # after the close: floor
    # A plain historical date keeps whole-day arithmetic (no clock to read); a naive datetime is UTC.
    assert time_to_expiry_years(date(2024, 10, 13), date(2024, 10, 6)) * 365 == pytest.approx(7.0)
    assert time_to_expiry_years(expiry, datetime(2026, 10, 6, 5, 30)) == time_to_expiry_years(expiry, at_11_ist)
    # Today's date means "now".
    today = datetime.now(IST).date()
    assert time_to_expiry_years(today + timedelta(days=7), today) == pytest.approx(time_to_expiry_years(today + timedelta(days=7), datetime.now(timezone.utc)), rel=1e-3)


# --- B1 ------------------------------------------------------------------------------------------------------------------
def test_day_counters_reset_per_indian_trading_day():
    # Each day: flat, then a 2% jump that closes the open trade at its target; the strategy keeps firing afterwards.
    day1 = make_series([100.0] * 5 + [102.0] * 35, start="2024-01-02 09:15")
    day2 = make_series([100.0] * 5 + [102.0] * 35, start="2024-01-03 09:15")
    df = pd.concat([day1, day2])
    result = run_backtest(_EveryBar(), df, "TESTSYM", "1min", RiskConfig(capital=1_000_000, risk_per_trade_pct=0.5, max_trades_per_day=1))
    days = {t.entry_time.date() if hasattr(t.entry_time, "date") else t.entry_time for t in result.trades}
    assert result.total_trades == 2 and len(days) == 2                  # one trade per day, both days - not one for the whole run
    assert ENGINE_VERSION == "3"


# --- B4 ------------------------------------------------------------------------------------------------------------------
def test_gapped_levels_fill_at_the_open():
    assert determine_exit_price("LONG", 98.0, 104.0, 110.0, low=95.0, high=96.0, open_price=95.5) == ("Stop Loss", 95.5)
    assert determine_exit_price("LONG", 98.0, 104.0, 110.0, low=97.0, high=99.5, open_price=99.0) == ("Stop Loss", 98.0)   # traded through: the level
    assert determine_exit_price("LONG", 98.0, 104.0, 110.0, low=105.0, high=107.0, open_price=106.0) == ("Target 1", 106.0)
    assert determine_exit_price("LONG", 98.0, 104.0, 110.0, low=111.0, high=113.0, open_price=112.0) == ("Target 2", 112.0)
    assert determine_exit_price("SHORT", 102.0, 96.0, 92.0, low=103.0, high=105.0, open_price=104.0) == ("Stop Loss", 104.0)
    assert determine_exit_price("SHORT", 102.0, 96.0, 92.0, low=94.0, high=95.0, open_price=94.5) == ("Target 1", 94.5)
    assert determine_exit_price("LONG", 98.0, 104.0, 110.0, low=95.0, high=96.0) == ("Stop Loss", 98.0)                      # no open: unchanged
    bars = [{"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1000.0},
            {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1000.0},                                 # entry bar
            {"open": 94.0, "high": 95.0, "low": 93.0, "close": 94.5, "volume": 1000.0}]                                     # gap down through the stop
    df = pd.DataFrame(bars, index=pd.date_range("2024-01-02 09:15", periods=3, freq="1min"))
    result = run_backtest(_OnceLong(stop=98.0, target1=104.0), df, "TESTSYM", "1min", RiskConfig(capital=100_000, risk_per_trade_pct=0.5))
    assert result.total_trades == 1 and result.trades[0].exit_reason == "Stop Loss" and result.trades[0].exit_price == 94.0


# --- B2 ------------------------------------------------------------------------------------------------------------------
def test_stt_lands_on_the_leg_that_was_sold_and_on_exercise():
    pb = PaperBroker()
    bought = pb.estimate_round_trip_costs(120.0, 150.0, 75, "OPTION")
    written = pb.estimate_round_trip_costs(120.0, 150.0, 75, "OPTION", sold_first=True)
    assert bought != written
    # Written: STT on the entry premium (120), stamp duty on the exit (150); bought: the other way round.
    stt_b, stt_w = 150 * 75 * 0.1 / 100, 120 * 75 * 0.1 / 100
    stamp_b, stamp_w = 120 * 75 * 0.003 / 100, 150 * 75 * 0.003 / 100
    assert bought - written == pytest.approx((stt_b + stamp_b) - (stt_w + stamp_w), abs=0.02)
    assert pb.exercise_charges(40.0, 75) == pytest.approx(40 * 75 * 0.125 / 100) and pb.exercise_charges(0.0, 75) == 0.0
    # A settled leg: one brokerage order, no stamp duty on the settlement (exit passed as 0.0).
    settled_long = pb.estimate_round_trip_costs(120.0, 0.0, 75, "OPTION", settled=True)
    traded_long = pb.estimate_round_trip_costs(120.0, 0.0, 75, "OPTION")
    assert traded_long - settled_long == pytest.approx(pb.brokerage_per_order * (1 + pb.gst_pct / 100), abs=0.02)
    settled_short = pb.estimate_round_trip_costs(120.0, 0.0, 75, "OPTION", sold_first=True, settled=True)
    traded_short = pb.estimate_round_trip_costs(120.0, 0.0, 75, "OPTION", sold_first=True)
    assert traded_short - settled_short == pytest.approx(pb.brokerage_per_order * (1 + pb.gst_pct / 100), abs=0.02)
    assert settled_short > settled_long   # the written leg owes sell-side STT on its premium; the bought leg only stamp duty
    # The option backtest: a written leg's charges use sold_first; a bought leg settled ITM carries exercise STT.
    cfg = OptionBacktestConfig(option_strategy=OptionStrategy.SINGLE, option_position=OptionPosition.BUY, implied_volatility=0.14, intraday=False, max_lots=1)
    rally = make_series(list(np.linspace(24500, 24800, 370)), start="2026-10-06 09:15")      # Tuesday expiry, bars past 15:15
    result = run_option_backtest(_OnceLong(stop=24300, target1=26000), rally, "NIFTY 50", "1min", RiskConfig(capital=5_000_000, risk_per_trade_pct=1.0), cfg)
    trade = result.trades[0]
    assert trade.exit_reason.startswith("Expiry settlement") and trade.charges > pb.exercise_charges(trade.exit_price, trade.quantity) > 0


# --- B3 ------------------------------------------------------------------------------------------------------------------
def test_optimizer_ranks_in_sample_and_reports_out_of_sample_validation():
    from app.strategy_engine.registry import registry
    strategy = registry.get("ema_rsi_scalper_1m")
    df = make_series(noisy_uptrend(900, seed=11))
    result = optimize(strategy, df, "TEST", "1min", RiskConfig(), {"rsi_period": [7, 14], "fast_ema": [9, 20]}, metric="net_pnl", split=0.7)
    rows = result["results"]
    for r in rows:
        if r["score"] is not None:
            assert r["score"] == r["in_sample"]["net_pnl"]                     # ranked on the in-sample figure
        if r["validation"] is not None:
            assert r["validation"] == r["out_of_sample"]["net_pnl"]
    scores = [r["score"] for r in rows if r["score"] is not None]
    assert scores == sorted(scores, reverse=True) and "best_confirmed_out_of_sample" in result
    assert "validation" in result["note"] and "in-sample" in result["note"]


# --- B5 ------------------------------------------------------------------------------------------------------------------
def test_option_backtests_size_with_the_lot_of_the_entry_day():
    assert lot_size_on("NIFTY", date(2024, 6, 3)) == 25 and lot_size_on("NIFTY", date(2024, 11, 20)) == LOT_SIZES["NIFTY"]
    # Before the oldest dated row the table falls back to that row's "before" lot (a stated approximation, not the
    # historical lot: BANKNIFTY traded 25 a lot in early 2023); a stock without a row keeps its current lot.
    assert lot_size_on("BANKNIFTY", date(2023, 1, 2)) == LOT_SIZE_HISTORY["BANKNIFTY"][0][1] and lot_size_on("RELIANCE", date(2024, 1, 1)) == 1
    cfg = OptionBacktestConfig(option_strategy=OptionStrategy.SINGLE, option_position=OptionPosition.BUY, implied_volatility=0.14, max_lots=1)
    old = make_series(list(np.linspace(24500, 24800, 200)), start="2024-06-04 09:15")        # before the Nov 2024 revision
    result = run_option_backtest(_OnceLong(stop=24300, target1=24700), old, "NIFTY 50", "1min", RiskConfig(capital=5_000_000, risk_per_trade_pct=1.0), cfg)
    assert result.trades and result.trades[0].quantity == 25
    new = make_series(list(np.linspace(24500, 24800, 200)), start="2026-10-05 09:15")
    result = run_option_backtest(_OnceLong(stop=24300, target1=24700), new, "NIFTY 50", "1min", RiskConfig(capital=5_000_000, risk_per_trade_pct=1.0), cfg)
    assert result.trades and result.trades[0].quantity == LOT_SIZES["NIFTY"]
    pinned = OptionBacktestConfig(option_strategy=OptionStrategy.SINGLE, option_position=OptionPosition.BUY, implied_volatility=0.14, max_lots=1, lot_size=50)
    result = run_option_backtest(_OnceLong(stop=24300, target1=24700), old, "NIFTY 50", "1min", RiskConfig(capital=5_000_000, risk_per_trade_pct=1.0), pinned)
    assert result.trades[0].quantity == 50
