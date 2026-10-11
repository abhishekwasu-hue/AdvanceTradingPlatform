import numpy as np
import pandas as pd
from app.indicators.prefix_cache import prefix_cached


@prefix_cached
def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


@prefix_cached
def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = true_range(df)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


@prefix_cached
def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    atr_series = atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2.0
    basic_upper = hl2 + multiplier * atr_series
    basic_lower = hl2 - multiplier * atr_series

    n = len(df)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    st = np.full(n, np.nan)
    trend = np.zeros(n, dtype=int)

    close = df["close"].to_numpy()
    bu = basic_upper.to_numpy()
    bl = basic_lower.to_numpy()

    start = period
    if start >= n:
        return pd.DataFrame({"supertrend": st, "trend": trend}, index=df.index)

    final_upper[start] = bu[start]
    final_lower[start] = bl[start]
    trend[start] = 1 if close[start] >= final_lower[start] else -1
    st[start] = final_lower[start] if trend[start] == 1 else final_upper[start]

    for i in range(start + 1, n):
        final_upper[i] = (
            bu[i] if (bu[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]) else final_upper[i - 1]
        )
        final_lower[i] = (
            bl[i] if (bl[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]) else final_lower[i - 1]
        )

        if trend[i - 1] == 1:
            trend[i] = -1 if close[i] < final_lower[i] else 1
        else:
            trend[i] = 1 if close[i] > final_upper[i] else -1

        st[i] = final_lower[i] if trend[i] == 1 else final_upper[i]

    return pd.DataFrame({"supertrend": st, "trend": trend}, index=df.index)


@prefix_cached
def bollinger(close: pd.Series, period: int = 20, k: float = 2.0) -> pd.DataFrame:
    """Bollinger bands: SMA mid with upper/lower at k sample standard deviations."""
    mid = close.rolling(window=period, min_periods=period).mean()
    sd = close.rolling(window=period, min_periods=period).std(ddof=1)
    return pd.DataFrame({"mid": mid, "upper": mid + k * sd, "lower": mid - k * sd}, index=close.index)
