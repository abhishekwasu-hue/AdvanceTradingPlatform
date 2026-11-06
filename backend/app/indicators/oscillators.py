"""FN2-a: the commodity channel index, Williams %R and the Aroon lines - causal (bar t reads bars <= t only), on
closed bars.

Each function takes a frame of bars (open / high / low / close). The cores (`_cci`, `_williams_r`, `_aroon`) take the
columns themselves, either one symbol's series or a frame with one column per symbol: the vectorised screen
(app/screener/panel.py) calls the same cores on its stacked columns, so both paths give the same numbers bit for bit.
A window with a missing value has no value (NaN), so it never matches.
"""
from typing import Any, Dict

import numpy as np
import pandas as pd

from app.indicators.prefix_cache import prefix_cached

CCI_CONSTANT = 0.015       # Lambert's scaling constant: about 70-80 % of the values fall between -100 and +100


def _shifted(x: np.ndarray, k: int) -> np.ndarray:
    """x moved k bars later along the first axis (the first k rows missing)."""
    if k == 0:
        return x
    out = np.full(x.shape, np.nan)
    if k < len(x):
        out[k:] = x[:len(x) - k]
    return out


def _mean_deviation(tp: np.ndarray, mean: np.ndarray, period: int) -> np.ndarray:
    """The mean absolute deviation of the last `period` typical prices from their mean at each bar (the same additions
    in the same order for one column or many)."""
    acc = np.zeros(tp.shape)
    for k in range(period):
        acc = acc + np.abs(_shifted(tp, k) - mean)
    return acc / period


def _cci(high: Any, low: Any, close: Any, period: int) -> Any:
    tp = (high + low + close) / 3.0
    mean = tp.rolling(window=period, min_periods=period).mean()
    md = _mean_deviation(tp.to_numpy(dtype=float), mean.to_numpy(dtype=float), period)
    md = np.where(md > 0, md, np.nan)                     # every typical price equal: no deviation, no value
    with np.errstate(invalid="ignore", divide="ignore"):
        values = (tp.to_numpy(dtype=float) - mean.to_numpy(dtype=float)) / (CCI_CONSTANT * md)
    if isinstance(tp, pd.DataFrame):
        return pd.DataFrame(values, index=tp.index, columns=tp.columns)
    return pd.Series(values, index=tp.index)


@prefix_cached
def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Commodity channel index (Donald Lambert, "Commodity Channel Index: Tools for Trading Cyclic Trends", Commodities
    magazine, 1980; the formula as StockCharts ChartSchool "Commodity Channel Index (CCI)" gives it):
    typical price TP = (high + low + close) / 3; CCI = (TP - SMA(TP, n)) / (0.015 x mean deviation), the mean deviation
    being the average of |TP - SMA(TP, n)| over the same n bars (TradingView's ta.cci, ta.dev). Unbounded; no value
    while the n bars have no deviation."""
    return _cci(df["high"].astype(float), df["low"].astype(float), df["close"].astype(float), period)


def _williams_r(high: Any, low: Any, close: Any, period: int) -> Any:
    highest = high.rolling(window=period, min_periods=period).max()
    lowest = low.rolling(window=period, min_periods=period).min()
    span = (highest - lowest).where(lambda s: s > 0)
    return -100.0 * ((highest - close) / span) + 0.0     # the ratio first (-100 at the lowest low exactly); + 0.0: 0, not -0


@prefix_cached
def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Williams %R (Larry Williams, "How I Made One Million Dollars Last Year Trading Commodities", 1973; StockCharts
    ChartSchool "Williams %R"): %R = (highest high - close) / (highest high - lowest low) x -100 over the last n bars,
    from -100 (the close at the lowest low) to 0 (at the highest high). No value when the n bars have no range."""
    return _williams_r(df["high"].astype(float), df["low"].astype(float), df["close"].astype(float), period)


def _bars_since_extreme(x: np.ndarray, period: int, newer_is_better: Any) -> np.ndarray:
    """Bars since the extreme of the last period + 1 values of x (0 = this bar); on a tie the most recent one counts
    (TA-Lib's `>=` / `<=` scan). Missing when a value in the window is missing."""
    best = x.copy()
    age = np.zeros(x.shape)
    valid = ~np.isnan(x)
    for k in range(1, period + 1):
        older = _shifted(x, k)
        valid &= ~np.isnan(older)
        with np.errstate(invalid="ignore"):
            take = newer_is_better(older, best)          # strictly beyond: the newer of two equal extremes stays
        best = np.where(take, older, best)
        age = np.where(take, float(k), age)
    return np.where(valid, age, np.nan)


def _aroon(high: np.ndarray, low: np.ndarray, period: int) -> Dict[str, np.ndarray]:
    since_high = _bars_since_extreme(high, period, np.greater)
    since_low = _bars_since_extreme(low, period, np.less)
    return {"up": 100.0 * (period - since_high) / period, "down": 100.0 * (period - since_low) / period}


@prefix_cached
def aroon(df: pd.DataFrame, period: int = 25) -> pd.DataFrame:
    """Aroon up / down (Tushar Chande, 1995; TA-Lib AROON, TradingView's built-in Aroon): over the last n + 1 bars,
    Aroon up = 100 x (n - bars since the highest high) / n and Aroon down = 100 x (n - bars since the lowest low) / n,
    each 0-100 (100 = the extreme is on this bar). On equal extremes the most recent one counts (TA-Lib)."""
    lines = _aroon(df["high"].astype(float).to_numpy(), df["low"].astype(float).to_numpy(), period)
    return pd.DataFrame(lines, index=df.index)


__all__ = ["cci", "williams_r", "aroon", "CCI_CONSTANT"]
