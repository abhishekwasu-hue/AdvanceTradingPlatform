import pytest

from app.core.enums import SignalDirection
from app.strategy_engine.declarative import Condition, CustomStrategyConfig, DeclarativeStrategy, Operand
from tests.utils import decline_then_rally, make_series, rally_then_decline


def _rsi_cross_above_50_config() -> CustomStrategyConfig:
    return CustomStrategyConfig(
        name="RSI cross 50",
        timeframe="1min",
        long_conditions=[
            Condition(
                left=Operand(type="indicator", indicator="RSI", period=14),
                operator="CROSSES_ABOVE",
                right=Operand(type="value", value=50),
            )
        ],
        short_conditions=[
            Condition(
                left=Operand(type="indicator", indicator="RSI", period=14),
                operator="CROSSES_BELOW",
                right=Operand(type="value", value=50),
            )
        ],
    )


def test_declarative_strategy_fires_long_on_rsi_cross_above():
    # RSI(14) crosses above 50 between bar 41 and 42 of this series (verified against the
    # actual indicator output); truncating there makes the cross land exactly on the last bar.
    df = make_series(decline_then_rally()).iloc[:43]
    strategy = DeclarativeStrategy("custom:test1", _rsi_cross_above_50_config())
    signal = strategy.analyze({"1min": df}, "TESTSYM")

    assert signal.direction == SignalDirection.LONG
    assert signal.entry is not None
    assert any("crosses above" in r for r in signal.reasons)


def test_declarative_strategy_fires_short_on_rsi_cross_below():
    # RSI(14) crosses below 50 between bar 41 and 42 of this series (verified against the
    # actual indicator output); truncating there makes the cross land exactly on the last bar.
    df = make_series(rally_then_decline()).iloc[:43]
    strategy = DeclarativeStrategy("custom:test2", _rsi_cross_above_50_config())
    signal = strategy.analyze({"1min": df}, "TESTSYM")

    assert signal.direction == SignalDirection.SHORT
    assert any("crosses below" in r for r in signal.reasons)


def test_declarative_strategy_no_trade_when_flat():
    df = make_series([100.0] * 60)
    strategy = DeclarativeStrategy("custom:test3", _rsi_cross_above_50_config())
    signal = strategy.analyze({"1min": df}, "TESTSYM")

    assert signal.direction == SignalDirection.NO_TRADE


def test_declarative_strategy_ema_vs_ema_condition():
    config = CustomStrategyConfig(
        name="EMA cross",
        timeframe="1min",
        long_conditions=[
            Condition(
                left=Operand(type="indicator", indicator="EMA", period=5),
                operator="CROSSES_ABOVE",
                right=Operand(type="indicator", indicator="EMA", period=20),
            )
        ],
    )
    df = make_series(decline_then_rally())
    strategy = DeclarativeStrategy("custom:test4", config)
    signal = strategy.analyze({"1min": df}, "TESTSYM")
    assert signal.direction in (SignalDirection.LONG, SignalDirection.NO_TRADE)


def test_custom_strategy_config_requires_at_least_one_condition():
    with pytest.raises(ValueError):
        CustomStrategyConfig(name="Empty", timeframe="1min")


def test_declarative_strategy_insufficient_history_returns_no_trade():
    config = _rsi_cross_above_50_config()
    df = make_series([100.0, 101.0, 99.0])
    strategy = DeclarativeStrategy("custom:test5", config)
    signal = strategy.analyze({"1min": df}, "TESTSYM")
    assert signal.direction == SignalDirection.NO_TRADE
    assert signal.reasons == ["Insufficient history"]
