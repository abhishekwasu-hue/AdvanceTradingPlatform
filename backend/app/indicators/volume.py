"""FN2-a: the volume indicators - on-balance volume and the money flow index - causal (bar t reads bars <= t only), on
closed bars.

Each function takes a frame of bars (high / low / close / volume). The cores (`_obv`, `_mfi`) take the columns
themselves, either one symbol's series or a frame with one column per symbol: the vectorised screen
(app/screener/panel.py) calls the same cores on its stacked columns, so both paths give the same numbers bit for bit.
"""
from typing import Any

import numpy as np
import pandas as pd

from app.indicators.prefix_cache import prefix_cached


def _obv(close: Any, volume: Any) -> Any:
    step = np.sign(close.diff()) * volume
    return step.fillna(0.0).cumsum()


@prefix_cached
def obv(df: pd.DataFrame) -> pd.Series:
    """On-balance volume (Joseph Granville, "Granville's New Key to Stock Market Profits", 1963; StockCharts ChartSchool
    "On Balance Volume (OBV)"; TradingView's ta.obv = ta.cum(math.sign(ta.change(close)) x volume)): a running total
    that adds the bar's volume when the close rises, subtracts it when the close falls and keeps it on an equal close.
    The first bar, and a bar whose close, previous close or volume is missing, adds nothing. Its level depends on where
    the bars start, so read it against itself (its average, its value some bars ago), not as a number."""
    return _obv(df["close"].astype(float), df["volume"].astype(float))


def _mfi(high: Any, low: Any, close: Any, volume: Any, period: int) -> Any:
    tp = (high + low + close) / 3.0
    prev = tp.shift(1)
    valid = tp.notna() & prev.notna() & volume.notna()
    up, down = (tp > prev) & valid, (tp < prev) & valid
    raw = tp * volume
    pos = raw.where(up, 0.0).where(valid)
    neg = raw.where(down, 0.0).where(valid)
    pos_mean = pos.rolling(window=period, min_periods=period).mean()
    neg_mean = neg.rolling(window=period, min_periods=period).mean()
    # whether the window had any rising / falling bar: a count (exact), not the float sums (a rolling sum can keep a
    # tiny remainder after a value leaves the window)
    ups = up.astype(float).rolling(window=period, min_periods=period).sum()
    downs = down.astype(float).rolling(window=period, min_periods=period).sum()
    ratio = pos_mean / neg_mean.where(downs > 0)
    out = 100.0 - 100.0 / (1.0 + ratio)
    out = out.where(downs > 0, 100.0)                     # only rising money flow: 100
    out = out.where((ups + downs) > 0, 50.0)              # no rise and no fall in the window: the neutral 50 (as RSI)
    return out.where(pos_mean.notna() & neg_mean.notna())


@prefix_cached
def mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Money flow index (Gene Quong and Avrum Soudack, "Volume-Weighted RSI: Money Flow", Technical Analysis of Stocks &
    Commodities, 1989; StockCharts ChartSchool "Money Flow Index (MFI)"): typical price TP = (high + low + close) / 3,
    raw money flow = TP x volume, positive on a bar whose TP is above the previous bar's, negative when below (an equal
    TP is neither); money ratio = positive / negative flow over the last n bars; MFI = 100 - 100 / (1 + ratio), 0-100.
    Only positive flow gives 100; no rise and no fall gives the neutral 50 (the RSI convention of app.indicators.momentum).
    A window with a missing bar has no value."""
    return _mfi(df["high"].astype(float), df["low"].astype(float), df["close"].astype(float), df["volume"].astype(float), period)


__all__ = ["obv", "mfi"]
