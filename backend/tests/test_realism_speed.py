"""Backtest realism 3: the per-run indicator cache changes nothing but the time.

- every cached indicator is causal: computed on a prefix == the prefix of the whole (bit for bit);
- a backtest with the cache gives exactly the trades it gives without it, for every inbuilt strategy;
- outside a backtest (live, any other caller) the cache is not active.
"""
import numpy as np
import pandas as pd
import pytest

from app.backtest import engine
from app.core.models import RiskConfig
from app.indicators import adx, atr, ema, rsi, session_vwap, sma, supertrend
from app.indicators.prefix_cache import _active, prefix_cache
from app.indicators.trend import macd
from app.indicators.volatility import bollinger, true_range
from app.strategy_engine.registry import registry


def _bars(n_days=3, seed=3, daily=False):
    rng = np.random.default_rng(seed)
    if daily:
        idx = pd.bdate_range("2023-01-02", periods=n_days)
    else:
        days = pd.bdate_range("2024-01-02", periods=n_days)
        idx = pd.DatetimeIndex([t for d in days for t in pd.date_range(d + pd.Timedelta(hours=9, minutes=15), periods=375, freq="1min")])
    close = 24_000 * np.exp(np.cumsum(rng.normal(0, 0.0015 if not daily else 0.012, len(idx))))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.0006, "low": np.minimum(open_, close) * 0.9994,
                         "close": close, "volume": rng.integers(500, 5000, len(idx)).astype(float)}, index=idx)


INDICATORS = [
    ("ema", lambda d: ema(d["close"], 20)), ("sma", lambda d: sma(d["close"], 20)), ("rsi", lambda d: rsi(d["close"], 14)),
    ("macd", lambda d: macd(d["close"])), ("bollinger", lambda d: bollinger(d["close"])), ("true_range", true_range),
    ("atr", lambda d: atr(d, 14)), ("adx", lambda d: adx(d, 14)), ("supertrend", lambda d: supertrend(d, 10, 3.0)),
    ("session_vwap", session_vwap),
]


@pytest.mark.parametrize("name,fn", INDICATORS, ids=[n for n, _ in INDICATORS])
def test_every_cached_indicator_is_causal(name, fn):
    df = _bars(2)
    whole = fn(df)
    for n in (1, 2, 37, 375, 376, len(df) - 1):
        part = fn(df.iloc[:n])
        if isinstance(whole, pd.DataFrame):
            pd.testing.assert_frame_equal(part, whole.iloc[:n], check_exact=True)
        else:
            pd.testing.assert_series_equal(part, whole.iloc[:n], check_exact=True)
    # and the cache returns exactly that prefix
    with prefix_cache([df]):
        for n in (5, 200, len(df)):
            cached = fn(df.iloc[:n])
            if isinstance(whole, pd.DataFrame):
                pd.testing.assert_frame_equal(cached, whole.iloc[:n], check_exact=True)
            else:
                pd.testing.assert_series_equal(cached, whole.iloc[:n], check_exact=True)


def test_only_prefixes_of_registered_frames_are_served_from_the_cache():
    df = _bars(1)
    other = df.copy()
    with prefix_cache([df]):
        ema(df["close"].iloc[:50], 10)
        hits = len(_active.get().results)
        ema(other["close"].iloc[:50], 10)                # same values, different frame: computed, not cached
        ema(df["close"].iloc[10:60], 10)                 # not a row-0 prefix
        ema(df["close"] * 2, 10)                         # a derived series
        assert len(_active.get().results) == hits == 1
    assert _active.get() is None                         # live callers never see a cache


def _trades(result):
    return [(t.entry_time, t.direction, round(t.entry_price, 6), t.exit_time, t.exit_reason, round(t.pnl or 0, 6)) for t in result.trades]


@pytest.mark.parametrize("strategy", registry.list_all(), ids=registry.ids())
def test_the_cache_does_not_change_any_trade(strategy):
    daily = strategy.timeframes[0] == "day"
    df = _bars(260 if daily else 4, daily=daily)
    tf = "day" if daily else "1min"
    risk = RiskConfig(capital=1_000_000, risk_per_trade_pct=0.5)
    cached = engine.run_backtest(strategy, df, "NIFTY 50", tf, risk)
    plain = engine.run_backtest.__wrapped__(strategy, df, "NIFTY 50", tf, risk)      # no cache active
    assert _trades(cached) == _trades(plain)
    assert cached.equity_curve == plain.equity_curve
