import math

import numpy as np
import pandas as pd

from app.indicators.prefix_cache import prefix_cached


@prefix_cached
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


@prefix_cached
def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


@prefix_cached
def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD line (EMA fast - EMA slow), its signal line (EMA of MACD) and the histogram."""
    line = ema(series, fast) - ema(series, slow)
    signal_line = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": signal_line, "hist": line - signal_line}, index=series.index)


@prefix_cached
def wma(series: pd.Series, period: int) -> pd.Series:
    """Linearly weighted moving average: weights 1..period, the newest bar weighs most. NaN until `period` bars, and
    wherever the window holds a NaN. Causal: bar t reads bars t - period + 1 .. t."""
    if period < 1:
        raise ValueError("WMA needs a period of at least 1")
    values = series.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    if len(values) >= period:
        weights = np.arange(1, period + 1, dtype=float)
        windows = np.lib.stride_tricks.sliding_window_view(values, period)
        out[period - 1:] = windows @ weights / weights.sum()
    return pd.Series(out, index=series.index, name=series.name)


@prefix_cached
def hma(series: pd.Series, period: int) -> pd.Series:
    """Hull moving average: WMA(2 x WMA(x, period // 2) - WMA(x, period), floor(sqrt(period))). `period` >= 2 (the half
    window must be at least one bar); the first value needs period + floor(sqrt(period)) - 1 bars."""
    if period < 2:
        raise ValueError("HMA needs a period of at least 2")
    half = period // 2
    root = int(math.floor(math.sqrt(period)))
    raw = 2.0 * wma(series, half) - wma(series, period)
    return wma(raw, root)


@prefix_cached
def parabolic_sar(df: pd.DataFrame, start: float = 0.02, step: float = 0.02, maximum: float = 0.2) -> pd.DataFrame:
    """Wilder's parabolic SAR, bar by bar on closed bars (the same steps as TradingView's `ta.sar`, so a user comparing
    with that chart sees the same line). Columns: `sar` (the level) and `dir` (+1 while the SAR is below price, -1 while
    it is above; the bar a flip happens on carries the new direction). Bar 0 has no value; the first trend is up when
    the second close is above the first. Causal: bar t reads bars <= t only."""
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    n = len(df)
    sar = np.full(n, np.nan)
    direction = np.full(n, np.nan)
    result = ep = af = 0.0
    below = False
    for i in range(1, n):
        first = False
        if i == 1:
            if close[1] > close[0]:
                below, ep, result = True, high[1], low[0]
            else:
                below, ep, result = False, low[1], high[0]
            first, af = True, start
        result = result + af * (ep - result)
        if below and result > low[i]:                 # price fell through the SAR: the trend turns down
            first, below = True, False
            result = max(high[i], ep)
            ep, af = low[i], start
        elif not below and result < high[i]:          # price rose through the SAR: the trend turns up
            first, below = True, True
            result = min(low[i], ep)
            ep, af = high[i], start
        if not first:
            if below and high[i] > ep:
                ep, af = high[i], min(af + step, maximum)
            elif not below and low[i] < ep:
                ep, af = low[i], min(af + step, maximum)
        if below:
            result = min(result, low[i - 1], low[i - 2]) if i > 1 else min(result, low[i - 1])
        else:
            result = max(result, high[i - 1], high[i - 2]) if i > 1 else max(result, high[i - 1])
        sar[i] = result
        direction[i] = 1.0 if below else -1.0
    return pd.DataFrame({"sar": sar, "dir": direction}, index=df.index)
