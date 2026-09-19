"""Master prompt Section 50: "backtest-vs-live parity" tests - the tests that actually enforce
Section 13's "same DSL everywhere" requirement, so it doesn't silently rot.

`determine_exit_price` (app/trading/exit_logic.py) is now the single source of truth for exit
priority (stop loss, then target2, then target1), called by both the backtest engine
(app/backtest/engine.py, which knows a bar's full low/high range) and the live/paper path
(`check_exit`, which only ever has one current price). These tests cover:
  1. `determine_exit_price` itself, directly, for every direction/priority combination.
  2. That `check_exit` (the live/paper entry point) delegates to it correctly.
  3. That the backtest engine's per-bar decisions are provably the same function call, by driving
     a real `run_backtest` through OHLCV bars engineered to hit each priority case.
"""

import pandas as pd

from app.backtest.engine import run_backtest
from app.core.enums import SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.db.models import TradeRecord
from app.trading.exit_logic import check_exit, determine_exit_price
from tests.test_backtest import _OneShotLongStrategy


def _trade_record(direction: str, stop_loss: float, target1: float, target2: float | None) -> TradeRecord:
    return TradeRecord(
        tenant_id=1, user_id=1, mode="PAPER", symbol="TESTSYM", strategy_id="fake",
        direction=direction, entry_time=pd.Timestamp.utcnow(), entry_price=100.0, quantity=1.0,
        stop_loss=stop_loss, target1=target1, target2=target2,
    )


class TestDetermineExitPriceLong:
    def test_no_exit_when_price_stays_between_sl_and_target1(self):
        assert determine_exit_price("LONG", 98.0, 104.0, 110.0, low=99.0, high=103.0) is None

    def test_stop_loss_only(self):
        assert determine_exit_price("LONG", 98.0, 104.0, 110.0, low=97.5, high=99.0) == ("Stop Loss", 98.0)

    def test_target1_only(self):
        assert determine_exit_price("LONG", 98.0, 104.0, 110.0, low=100.0, high=105.0) == ("Target 1", 104.0)

    def test_target2_only(self):
        assert determine_exit_price("LONG", 98.0, 104.0, 110.0, low=100.0, high=111.0) == ("Target 2", 110.0)

    def test_target2_wins_over_target1_in_same_bar(self):
        assert determine_exit_price("LONG", 98.0, 104.0, 110.0, low=100.0, high=115.0) == ("Target 2", 110.0)

    def test_stop_loss_wins_over_targets_in_same_bar(self):
        assert determine_exit_price("LONG", 98.0, 104.0, 110.0, low=97.0, high=115.0) == ("Stop Loss", 98.0)

    def test_no_target2_falls_through_to_target1(self):
        assert determine_exit_price("LONG", 98.0, 104.0, None, low=100.0, high=105.0) == ("Target 1", 104.0)

    def test_no_target2_falls_through_to_stop_loss(self):
        assert determine_exit_price("LONG", 98.0, 104.0, None, low=97.0, high=99.0) == ("Stop Loss", 98.0)


class TestDetermineExitPriceShort:
    def test_no_exit_when_price_stays_between_sl_and_target1(self):
        assert determine_exit_price("SHORT", 102.0, 96.0, 90.0, low=97.0, high=101.0) is None

    def test_stop_loss_only(self):
        assert determine_exit_price("SHORT", 102.0, 96.0, 90.0, low=101.0, high=102.5) == ("Stop Loss", 102.0)

    def test_target1_only(self):
        assert determine_exit_price("SHORT", 102.0, 96.0, 90.0, low=95.0, high=100.0) == ("Target 1", 96.0)

    def test_target2_only(self):
        assert determine_exit_price("SHORT", 102.0, 96.0, 90.0, low=89.0, high=100.0) == ("Target 2", 90.0)

    def test_target2_wins_over_target1_in_same_bar(self):
        assert determine_exit_price("SHORT", 102.0, 96.0, 90.0, low=85.0, high=100.0) == ("Target 2", 90.0)

    def test_stop_loss_wins_over_targets_in_same_bar(self):
        assert determine_exit_price("SHORT", 102.0, 96.0, 90.0, low=85.0, high=103.0) == ("Stop Loss", 102.0)


class TestCheckExitDelegatesToDetermineExitPrice:
    """`check_exit` is the live/paper entry point (app/trading/routes.py). It must be a pure
    delegation to `determine_exit_price` with low == high == current_price - never a separately
    maintained copy of the priority logic.
    """

    def test_check_exit_stop_loss(self):
        trade = _trade_record("LONG", stop_loss=98.0, target1=104.0, target2=110.0)
        assert check_exit(trade, 97.5) == determine_exit_price("LONG", 98.0, 104.0, 110.0, 97.5, 97.5)

    def test_check_exit_target2(self):
        trade = _trade_record("LONG", stop_loss=98.0, target1=104.0, target2=110.0)
        assert check_exit(trade, 110.0) == determine_exit_price("LONG", 98.0, 104.0, 110.0, 110.0, 110.0)

    def test_check_exit_no_exit(self):
        trade = _trade_record("SHORT", stop_loss=102.0, target1=96.0, target2=90.0)
        assert check_exit(trade, 99.0) is None

    def test_check_exit_short_target1(self):
        trade = _trade_record("SHORT", stop_loss=102.0, target1=96.0, target2=90.0)
        assert check_exit(trade, 95.0) == ("Target 1", 96.0)


class TestBacktestVsLiveParity:
    """Drives the real backtest engine through bars engineered to hit each priority case and
    checks its trade outcome against `determine_exit_price` called directly on the same bar -
    proving the backtest engine has no logic of its own left to drift from the live/paper path.
    """

    def _run(self, target1: float, target2: float, bars: list[dict]) -> str:
        index = pd.date_range("2024-01-02 09:15", periods=len(bars), freq="1min")
        df = pd.DataFrame(bars, index=index)
        strategy = _OneShotLongStrategy(target1=target1, target2=target2)
        result = run_backtest(strategy, df, "TESTSYM", "1min", RiskConfig(capital=100_000, risk_per_trade_pct=0.5))
        assert result.total_trades == 1
        return result.trades[0].exit_reason, result.trades[0].exit_price

    def test_backtest_exits_at_stop_loss(self):
        reason, price = self._run(
            target1=104.0, target2=110.0,
            bars=[
                {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1000.0},
                {"open": 99.0, "high": 99.5, "low": 97.0, "close": 97.5, "volume": 1000.0},
            ],
        )
        expected = determine_exit_price("LONG", 98.0, 104.0, 110.0, low=97.0, high=99.5)
        assert (reason, price) == expected

    def test_backtest_exits_at_target1(self):
        reason, price = self._run(
            target1=104.0, target2=110.0,
            bars=[
                {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1000.0},
                {"open": 101.0, "high": 105.0, "low": 100.5, "close": 104.5, "volume": 1000.0},
            ],
        )
        expected = determine_exit_price("LONG", 98.0, 104.0, 110.0, low=100.5, high=105.0)
        assert (reason, price) == expected

    def test_backtest_exits_at_target2_when_both_targets_cross_same_bar(self):
        reason, price = self._run(
            target1=104.0, target2=110.0,
            bars=[
                {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1000.0},
                {"open": 105.0, "high": 115.0, "low": 100.0, "close": 112.0, "volume": 1000.0},
            ],
        )
        expected = determine_exit_price("LONG", 98.0, 104.0, 110.0, low=100.0, high=115.0)
        assert (reason, price) == expected
