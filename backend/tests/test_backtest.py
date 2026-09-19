import pandas as pd

from app.backtest.engine import run_backtest
from app.core.enums import SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.strategy_engine.indicator_strategies import EmaRsiScalper
from app.strategy_engine.mtf_strategy import build_mtf_strategies
from tests.utils import make_series, noisy_uptrend


def test_backtest_runs_and_returns_valid_metrics():
    df = make_series(noisy_uptrend(n=300))
    strategy = EmaRsiScalper(tf="1min")
    result = run_backtest(strategy, df, "TESTSYM", "1min", RiskConfig(capital=100_000, risk_per_trade_pct=1.0))

    assert result.strategy_id == strategy.id
    assert result.total_trades == result.winning_trades + result.losing_trades
    assert len(result.equity_curve) >= 1
    if result.total_trades > 0:
        assert result.win_rate >= 0
        assert all(t.pnl is not None for t in result.trades)


def test_backtest_runs_for_mtf_strategy():
    df = make_series(noisy_uptrend(n=400))
    strategy = next(s for s in build_mtf_strategies() if s.id == "mtf_1m_5m_trend_pullback")
    strategy.params["adx_min"] = 10
    result = run_backtest(strategy, df, "TESTSYM", "1min", RiskConfig(capital=100_000, risk_per_trade_pct=1.0))
    assert result.strategy_id == strategy.id
    assert len(result.equity_curve) >= 1


class _OneShotLongStrategy:
    """Fires one fixed LONG signal on the first bar, then goes flat forever - lets a test control
    exactly which bar opens/closes the trade, independent of any real indicator logic.
    """

    id = "fake_one_shot_long"
    timeframes = ["1min"]

    def __init__(self, target1: float, target2: float):
        self._target1 = target1
        self._target2 = target2
        self._fired = False

    def min_history(self):
        return {"1min": 0}

    def analyze(self, data, symbol):
        primary = data["1min"]
        ts = primary.index[-1]
        if not self._fired:
            self._fired = True
            return Signal(
                symbol=symbol, strategy_id=self.id, strategy_name="Fake One-Shot",
                direction=SignalDirection.LONG, timestamp=ts,
                entry=100.0, stop_loss=98.0, target1=self._target1, target2=self._target2,
                risk_reward=3.0, score=80, grade=SignalGrade.HIGH_QUALITY, reasons=["forced for test"],
                timeframe_combo="1min",
            )
        return Signal(
            symbol=symbol, strategy_id=self.id, strategy_name="Fake One-Shot",
            direction=SignalDirection.NO_TRADE, timestamp=ts,
            score=0, grade=SignalGrade.NO_TRADE, reasons=["already fired"], timeframe_combo="1min",
        )


def test_backtest_exits_at_target2_when_a_bar_crosses_both_targets():
    """Regression test: the backtest engine must prioritize target2 over target1 when a single
    bar's range crosses both, matching the live/paper exit logic in
    app/trading/exit_logic.py::check_exit. Before the fix, the backtest never even looked at
    target2 and always exited at target1 instead.
    """
    index = pd.date_range("2024-01-02 09:15", periods=2, freq="1min")
    df = pd.DataFrame({
        "open": [100.0, 105.0], "high": [100.5, 115.0], "low": [99.5, 100.0],
        "close": [100.0, 112.0], "volume": [1000.0, 1000.0],
    }, index=index)

    strategy = _OneShotLongStrategy(target1=104.0, target2=110.0)
    result = run_backtest(strategy, df, "TESTSYM", "1min", RiskConfig(capital=100_000, risk_per_trade_pct=0.5))

    assert result.total_trades == 1
    assert result.trades[0].exit_reason == "Target 2"
    assert result.trades[0].exit_price == 110.0
