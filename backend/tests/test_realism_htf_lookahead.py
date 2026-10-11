"""Backtest realism 1: a higher-timeframe bar is visible only once it has closed (end <= decision time).

The engines used to slice every timeframe with `index <= bar start`; resampled frames are labelled by bar start, so a
60-minute bar appeared from its first minute carrying the close of minutes not yet traded. The truncation test runs
every multi-timeframe strategy and, at sampled decisions, rebuilds the inputs from data cut at the decision time:
the strategy must see the same bars and give the same signal (it could not have known more).
"""
import copy

import numpy as np
import pandas as pd
import pytest

from app.backtest.engine import run_backtest
from app.backtest.options_engine import OptionBacktestConfig, run_option_backtest
from app.backtest.windows import WindowCursor, bar_length, decision_time
from app.core.enums import OptionStrategy
from app.core.models import RiskConfig
from app.core.resampling import resample_ohlc
from app.strategy_engine.registry import registry

MULTI_TF = [s for s in registry.list_all() if len(s.timeframes) > 1]


def _walk(days=3, seed=7, start="2024-01-02 09:15"):
    rng = np.random.default_rng(seed)
    frames = []
    day0 = pd.Timestamp(start)
    d = 0
    while len(frames) < days:
        day = day0 + pd.Timedelta(days=d)
        d += 1
        if day.weekday() >= 5:
            continue
        idx = pd.date_range(day, periods=375, freq="1min")
        close = 24_000 * np.exp(np.cumsum(rng.normal(0, 0.0012, len(idx))))
        open_ = np.r_[close[0], close[:-1]]
        frames.append(pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.0004, "low": np.minimum(open_, close) * 0.9996,
                                    "close": close, "volume": 1000.0}, index=idx))
    return pd.concat(frames)


def test_cursor_shows_a_bar_only_after_it_closes():
    base = _walk(days=1)
    frames = {"1min": base, "15min": resample_ohlc(base, "15min")}
    cur = WindowCursor(frames, ["1min", "15min"])
    at = lambda hhmm: cur.at(decision_time(pd.Timestamp(f"2024-01-02 {hhmm}"), "1min"))   # noqa: E731 - the 1-min bar starting then
    assert len(at("09:15")["1min"]) == 1 and len(at("09:15")["15min"]) == 0           # the 09:15-09:30 bar is still forming
    assert len(at("09:28")["15min"]) == 0
    assert len(at("09:29")["15min"]) == 1                                              # decided at 09:30: the bar has closed
    assert at("09:29")["15min"].index[-1] == pd.Timestamp("2024-01-02 09:15")
    assert bar_length("day") == pd.Timedelta(days=1) and bar_length("60min") == pd.Timedelta(hours=1)
    with pytest.raises(ValueError):
        bar_length("2h")


class _Recorder:
    """Wraps a strategy: every window it is shown, and the signal it gave."""

    def __init__(self, strategy):
        self.inner = copy.deepcopy(strategy)
        self.original = self.inner.analyze
        self.seen = []
        self.inner.analyze = self.analyze

    def analyze(self, data, symbol):
        signal = self.original(data, symbol)
        self.seen.append(({tf: df.index[-1] if len(df) else None for tf, df in data.items()}, len(data[self.inner.timeframes[0]]), signal))
        return signal


def _check(rec, base, base_tf="1min", samples=25):
    primary = rec.inner.timeframes[0]
    assert rec.seen, rec.inner.id
    for last, _, _ in rec.seen:
        decided = last[primary] + bar_length(primary)
        for tf, start in last.items():
            if start is not None:
                assert start + bar_length(tf) <= decided, (rec.inner.id, tf, start, decided)    # never a forming bar
    # truncation: rebuilt from data cut at the decision time, the strategy sees the same bars and says the same thing
    step = max(1, len(rec.seen) // samples)
    for last, _, signal in rec.seen[::step]:
        decided = last[primary] + bar_length(primary)
        cut = base[base.index + bar_length(base_tf) <= decided]
        frames = {}
        for tf in rec.inner.timeframes:
            f = cut if tf == base_tf else resample_ohlc(cut, tf)
            frames[tf] = f[f.index + bar_length(tf) <= decided]
        again = rec.original(frames, "NIFTY 50")
        assert {tf: (df.index[-1] if len(df) else None) for tf, df in frames.items()} == last, rec.inner.id
        assert (again.direction, again.entry, again.stop_loss) == (signal.direction, signal.entry, signal.stop_loss), rec.inner.id


@pytest.mark.parametrize("strategy", MULTI_TF, ids=[s.id for s in MULTI_TF])
def test_no_multi_timeframe_strategy_sees_an_unclosed_bar(strategy):
    base = _walk()
    base_tf = "1min"
    if strategy.timeframes[0] != base_tf and base_tf not in strategy.timeframes:
        base = base  # resampled primaries are covered the same way (every timeframe is checked against its own end)
    rec = _Recorder(strategy)
    run_backtest(rec.inner, base, "NIFTY 50", base_tf, RiskConfig(capital=1_000_000, risk_per_trade_pct=0.5))
    _check(rec, base, base_tf)


def test_the_option_engine_uses_the_same_rule():
    strategy = next(s for s in MULTI_TF if s.timeframes[0] == "1min")
    rec = _Recorder(strategy)
    base = _walk(days=2, start="2026-10-05 09:15")
    run_option_backtest(rec.inner, base, "NIFTY 50", "1min", RiskConfig(capital=1_000_000, risk_per_trade_pct=0.5),
                        OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, spread_width=2))
    _check(rec, base, "1min", samples=10)
