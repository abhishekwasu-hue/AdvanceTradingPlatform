from app.backtest.engine import run_backtest
from app.core.models import RiskConfig
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
