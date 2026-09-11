from typing import List

import numpy as np

from app.brokers.models import OptionChain, OptionChainRow
from app.core.enums import SignalDirection
from app.core.models import Signal
from app.signal_scoring.engine import WEIGHTS, enrich_signal
from tests.utils import make_series, noisy_uptrend


def _zigzag(turning_points: List[float], bars_per_leg: int = 8) -> List[float]:
    prices: List[float] = []
    for a, b in zip(turning_points[:-1], turning_points[1:]):
        prices.extend(np.linspace(a, b, bars_per_leg, endpoint=False))
    prices.append(turning_points[-1])
    return prices


def _long_signal(entry: float) -> Signal:
    return Signal(
        symbol="TESTSYM", strategy_id="test", strategy_name="Test",
        direction=SignalDirection.LONG, timestamp="2024-01-02T09:20:00",
        entry=entry, stop_loss=entry - 2.0, target1=entry + 3.0, target2=entry + 5.0,
        risk_reward=1.5, score=80, timeframe_combo="1min",
    )


def test_weights_sum_to_one_hundred():
    assert sum(WEIGHTS.values()) == 100


def test_enrich_signal_no_trade_scores_zero():
    no_trade = Signal(
        symbol="X", strategy_id="s", strategy_name="s",
        direction=SignalDirection.NO_TRADE, timestamp="2024-01-02T09:20:00",
    )
    df = make_series([100.0] * 30)
    result = enrich_signal(no_trade, df)
    assert result.composite_score == 0
    assert result.breakdown == {}


def test_enrich_signal_on_uptrend_scores_trend_component_highly():
    prices = _zigzag([100, 110, 105, 118, 112, 126, 120, 135])  # ends on an up-leg: HH/HL
    df = make_series(prices)
    signal = _long_signal(entry=float(df["close"].iloc[-1]))

    result = enrich_signal(signal, df)

    assert set(result.breakdown.keys()) == set(WEIGHTS.keys())
    assert 0 <= result.composite_score <= 100
    assert result.breakdown["trend"].pct == 100.0
    assert len(result.confirmations) == 7


def test_enrich_signal_downtrend_conflicts_with_long_signal():
    prices = _zigzag([135, 120, 126, 112, 118, 105, 110, 100])  # ends on a down-leg: LH/LL
    df = make_series(prices)
    signal = _long_signal(entry=float(df["close"].iloc[-1]))

    result = enrich_signal(signal, df)
    assert result.breakdown["trend"].pct == 0.0


def test_enrich_signal_uses_option_chain_bias_when_supplied():
    df = make_series(noisy_uptrend(n=100))
    signal = _long_signal(entry=float(df["close"].iloc[-1]))

    bullish_chain = OptionChain(
        underlying="TESTSYM", expiry="2024-01-25", underlying_ltp=signal.entry,
        rows=[
            OptionChainRow(strike=signal.entry - 10, call_oi=20, call_change_oi=-5, put_oi=40, put_change_oi=15),
            OptionChainRow(strike=signal.entry, call_oi=15, call_change_oi=-2, put_oi=35, put_change_oi=10),
            OptionChainRow(strike=signal.entry + 10, call_oi=10, call_change_oi=-1, put_oi=30, put_change_oi=8),
        ],
    )

    result = enrich_signal(signal, df, option_chain=bullish_chain)
    assert result.breakdown["option_chain"].pct == 100.0


def test_enrich_signal_without_option_chain_is_neutral():
    df = make_series(noisy_uptrend(n=100))
    signal = _long_signal(entry=float(df["close"].iloc[-1]))
    result = enrich_signal(signal, df, option_chain=None)
    assert result.breakdown["option_chain"].pct == 50.0
