"""Handover 11c-1: translation parity - the ScreenQL a Pine script translates to gives the same signal bars as a
hand-written ScreenQL screen (and, where ATP has a second implementation, as that one) on synthetic fixtures.

The fixtures are seeded random walks (no market data), long enough for every warm-up. Every case also checks that the
signal is not trivially all-false or all-true, so a broken translation cannot pass by matching nothing."""
import numpy as np
import pandas as pd
import pytest

from app.lab.pine.templates import TEMPLATES
from app.lab.pine.translate import translate
from app.screener import parse
from app.screener.runtime import SymbolData, evaluate


def _bars(seed: int, count: int = 700) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1.2, count))
    close = np.maximum(close, 5.0)
    open_ = close + rng.normal(0, 0.6, count)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.8, count))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.8, count))
    volume = rng.integers(1_000, 50_000, count).astype(float)
    idx = pd.date_range("2024-01-01", periods=count, freq="1D", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


FIXTURES = [SymbolData(f"S{seed}", {"1d": _bars(seed)}) for seed in (1, 7, 42)]


def _series(text: str, data: SymbolData, params=None) -> pd.Series:
    v = evaluate(parse(text), data, base_tf="1d", params=params or {})
    assert isinstance(v, pd.Series)
    return v.fillna(False).astype(bool)


def _pine(source: str, signal: int = 0):
    result = translate(source)
    assert result.ok, [d.as_dict() for d in result.diagnostics]
    s = result.signals[signal]
    assert s.ok, s.problems
    return s


def _head(body: str) -> str:
    return '//@version=5\nindicator("parity")\n' + body


CASES = [
    # (Pine body, alertcondition argument, hand-written ScreenQL)
    ("fast = ta.ema(close, 9)\nslow = ta.ema(close, 21)\n", "ta.crossover(fast, slow)", "CrossAbove(EMA(close, 9), EMA(close, 21))"),
    ("", "(close - open)[1] > 0 and hl2 > hlc3", "Lag(close - open, 1) > 0 AND (high + low) / 2 > (high + low + close) / 3"),
    ("", "ta.change(close, 3) > 0", "close - Lag(close, 3) > 0"),
    ("k = ta.sma(ta.stoch(close, high, low, 14), 3)\n", "k < 20",
     "SMA(100 * (close - Min(low, 14)) / (Max(high, 14) - Min(low, 14)), 3) < 20"),
    ("[m, s, h] = ta.macd(close, 12, 26, 9)\n", "ta.crossover(m, s)",
     "CrossAbove(EMA(close, 12) - EMA(close, 26), EMA(EMA(close, 12) - EMA(close, 26), 9))"),
    ("[m, s, h] = ta.macd(close, 12, 26, 9)\n", "h > 0 and h[1] <= 0",
     "EMA(close, 12) - EMA(close, 26) - EMA(EMA(close, 12) - EMA(close, 26), 9) > 0 AND "
     "Lag(EMA(close, 12) - EMA(close, 26) - EMA(EMA(close, 12) - EMA(close, 26), 9), 1) <= 0"),
    ("", "ta.rsi(close[2], 14) > 50", "Lag(RSI(14), 2) > 50"),
    ("", "close > ta.highest(20)[1]", "close > Max(high, 20)[1]"),
    ("", "ta.rising(close, 3)", "close > Lag(Max(close, 3), 1)"),
    ("", "ta.falling(close, 3)", "close < Lag(Min(close, 3), 1)"),
    ("", "close > open ? volume > volume[1] : false", "close > open AND volume > volume[1]"),
    ("", "not (close > open) ? close < close[1] : close > close[2]",
     "(close <= open AND close < close[1]) OR (close > open AND close > close[2])"),
    ("[mid, up, lo] = ta.bb(close, 20, 2)\n", "close[1] < lo[1] and close > lo", "Lag(close, 1) < Lag(BBLower(20, 2), 1) AND close > BBLower(20, 2)"),
    ("", "ta.cross(close, ta.sma(close, 10))", "CrossAbove(close, SMA(close, 10)) OR CrossBelow(close, SMA(close, 10))"),
    ("", "math.max(open, close) > ta.ema(high, 5)", "Greatest(open, close) > EMA(high, 5)"),
    ("", "ta.roc(close, 5) > 2", "PctChange(close, 5) > 2"),
    ("", "ta.mom(close, 4) < 0", "close - Lag(close, 4) < 0"),
    ("[p, m, a] = ta.dmi(14, 14)\n", "p > m and a > 20", "PlusDI(14) > MinusDI(14) AND ADX(14) > 20"),
    ("len = input.int(10, 'Len', minval=2)\nx = close - ta.sma(close, len)\n", "x[len] > 0", "Lag(close - SMA(close, 10), 10) > 0"),
]


@pytest.mark.parametrize("body,cond,hand", CASES)
def test_translated_screen_matches_the_hand_written_one(body, cond, hand):
    s = _pine(_head(body + f"alertcondition({cond}, 'x')\n"))
    for data in FIXTURES:
        got = _series(s.text, data, s.params)
        want = _series(hand, data)
        assert got.equals(want), (cond, s.text, int((got != want).sum()))
    anyone = pd.concat([_series(s.text, d, s.params) for d in FIXTURES])
    assert 0 < anyone.sum() < len(anyone), f"{cond} never or always matches on the fixtures"


def test_ema_cross_matches_an_independent_pandas_cross():
    s = _pine(TEMPLATES[0]["source"])
    for data in FIXTURES:
        c = data.frames["1d"]["close"]
        from app.indicators.trend import ema
        fast, slow = ema(c, 9), ema(c, 21)
        want = (fast > slow) & (fast.shift(1) <= slow.shift(1)) & fast.shift(1).notna() & slow.shift(1).notna()
        assert _series(s.text, data, s.params).equals(want.astype(bool))


def test_supertrend_direction_matches_the_indicator_trend_column():
    """Pine's dir < 0 (up) is ATP's supertrend trend == 1; the flip template is the bar where the trend turns."""
    from app.indicators.volatility import supertrend
    src = _head("[st, dir] = ta.supertrend(3, 10)\nalertcondition(dir < 0, 'up')\nalertcondition(dir > 0, 'down')\n")
    result = translate(src)
    up, down = result.signals
    flip_up, flip_down = translate(TEMPLATES[2]["source"]).signals
    for data in FIXTURES:
        trend = pd.Series(supertrend(data.frames["1d"], 10, 3.0)["trend"], index=data.frames["1d"].index)
        assert _series(up.text, data).equals(trend == 1)
        assert _series(down.text, data).equals(trend == -1)
        turned_up = (trend == 1) & (trend.shift(1) == -1)
        turned_down = (trend == -1) & (trend.shift(1) == 1)
        assert _series(flip_up.text, data, flip_up.params).equals(turned_up)
        assert _series(flip_down.text, data, flip_down.params).equals(turned_down)
        assert turned_up.any() and turned_down.any()


def test_stochastic_matches_an_independent_formula():
    s = _pine(_head("k = ta.stoch(close, high, low, 14)\nalertcondition(k > 80, 'x')\n"))
    for data in FIXTURES:
        f = data.frames["1d"]
        lo, hi = f["low"].rolling(14).min(), f["high"].rolling(14).max()
        k = 100 * (f["close"] - lo) / (hi - lo)
        assert _series(s.text, data).equals((k > 80).fillna(False))


def test_every_template_translates_cleanly_and_runs_on_the_fixtures():
    for t in TEMPLATES:
        result = translate(t["source"])
        assert result.ok and result.complete, (t["id"], [d.as_dict() for d in result.diagnostics if d.severity == "error"])
        assert not [d for d in result.diagnostics if d.severity == "error"]
        for s in result.signals:
            hits = pd.concat([_series(s.text, d, s.params) for d in FIXTURES])
            assert 0 < hits.sum() < len(hits), (t["id"], s.label)
