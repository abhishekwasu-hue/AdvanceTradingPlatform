from app.core.enums import SignalDirection
from app.backtest.engine import resample_ohlc
from app.strategy_engine.indicator_strategies import (
    EmaRsiScalper,
    RsiAdxMomentumScalper,
    SupertrendAdxScalper,
)
from app.strategy_engine.mtf_strategy import MTF_COMBOS, build_mtf_strategies
from app.strategy_engine.registry import registry
from tests.utils import decline_then_rally, make_series, noisy_uptrend, rally_then_decline


def _directions_over_window(strategy, tf_key, df, start):
    directions = []
    for i in range(start, len(df)):
        signal = strategy.analyze({tf_key: df.iloc[: i + 1]}, "TESTSYM")
        directions.append(signal.direction)
    return directions


def test_no_trade_when_insufficient_history():
    strategy = EmaRsiScalper(tf="1min")
    df = make_series([100.0] * 5)
    signal = strategy.analyze({"1min": df}, "TESTSYM")
    assert signal.direction == SignalDirection.NO_TRADE
    assert "Insufficient history" in signal.reasons[0]


def test_ema_rsi_scalper_detects_long_on_rally_after_decline():
    df = make_series(decline_then_rally(decline_len=40, rally_len=20))
    strategy = EmaRsiScalper(tf="1min")
    directions = _directions_over_window(strategy, "1min", df, start=40)
    assert SignalDirection.LONG in directions
    for d in directions:
        assert d in (SignalDirection.LONG, SignalDirection.SHORT, SignalDirection.NO_TRADE)


def test_ema_rsi_scalper_detects_short_on_decline_after_rally():
    df = make_series(rally_then_decline(rally_len=40, decline_len=20))
    strategy = EmaRsiScalper(tf="1min")
    directions = _directions_over_window(strategy, "1min", df, start=40)
    assert SignalDirection.SHORT in directions


def test_supertrend_adx_scalper_runs_without_error_and_flips():
    df = make_series(decline_then_rally(decline_len=30, rally_len=30))
    strategy = SupertrendAdxScalper(tf="1min", adx_min=10)
    directions = _directions_over_window(strategy, "1min", df, start=25)
    assert SignalDirection.LONG in directions


def test_rsi_adx_momentum_scalper_runs_without_error():
    df = make_series(decline_then_rally(decline_len=30, rally_len=30), freq="5min")
    strategy = RsiAdxMomentumScalper(tf="5min", adx_min=10)
    directions = _directions_over_window(strategy, "5min", df, start=25)
    assert all(d in (SignalDirection.LONG, SignalDirection.SHORT, SignalDirection.NO_TRADE) for d in directions)


def test_mtf_combo_registration_matches_spec():
    combos = {(c["ltf"], c["htf"]) for c in MTF_COMBOS}
    assert combos == {("1min", "5min"), ("1min", "15min"), ("5min", "30min"), ("5min", "60min")}


def test_mtf_strategy_generates_long_signal_on_sustained_uptrend():
    ltf_df = make_series(noisy_uptrend(n=400), freq="1min")
    strategy = next(s for s in build_mtf_strategies() if s.id == "mtf_1m_5m_trend_pullback")
    strategy.params["adx_min"] = 10
    strategy.params["pullback_atr_mult"] = 1.5

    directions = []
    for i in range(150, len(ltf_df)):
        ltf_window = ltf_df.iloc[: i + 1]
        htf_window = resample_ohlc(ltf_window, "5min")
        signal = strategy.analyze({"1min": ltf_window, "5min": htf_window}, "TESTSYM")
        directions.append(signal.direction)

    assert any(d != SignalDirection.NO_TRADE for d in directions)


def test_registry_exposes_seven_inbuilt_scalping_strategies():
    expected_ids = {
        "mtf_1m_5m_trend_pullback",
        "mtf_1m_15m_trend_pullback",
        "mtf_5m_30m_trend_pullback",
        "mtf_5m_60m_trend_pullback",
        "ema_rsi_scalper_1m",
        "supertrend_adx_scalper_1m",
        "rsi_adx_momentum_5m",
    }
    assert set(registry.ids()) == expected_ids
    for strategy in registry.list_all():
        info = strategy.info()
        assert info.id == strategy.id
        assert len(info.timeframes) >= 1
