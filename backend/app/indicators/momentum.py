import numpy as np
import pandas as pd

from app.indicators.prefix_cache import prefix_cached


@prefix_cached
def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    result = 100 - (100 / (1 + rs))
    result = result.where(avg_loss != 0, 100.0)
    return result


@prefix_cached
def stochastic(df: pd.DataFrame, period: int = 14, smooth: int = 3, d: int = 3) -> pd.DataFrame:
    """Slow stochastic oscillator (0-100). The raw %K is 100 x (close - lowest low) / (highest high - lowest low) over
    `period` bars; `k` is its `smooth`-bar simple average (smooth 1 = the fast stochastic) and `d` the `d`-bar simple
    average of `k`. A window without range (highest high = lowest low) has no value (NaN), so it never matches.
    Causal: bar t reads bars <= t only."""
    lowest = df["low"].astype(float).rolling(window=period, min_periods=period).min()
    highest = df["high"].astype(float).rolling(window=period, min_periods=period).max()
    span = (highest - lowest).where(lambda s: s > 0)
    raw = 100.0 * (df["close"].astype(float) - lowest) / span
    k = raw.rolling(window=smooth, min_periods=smooth).mean()
    d_line = k.rolling(window=d, min_periods=d).mean()
    return pd.DataFrame({"k": k, "d": d_line}, index=df.index)
