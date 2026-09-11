import numpy as np
import pandas as pd

from app.indicators.volatility import true_range


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    up_move = df["high"].diff()
    down_move = -df["low"].diff()

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    tr = true_range(df)
    smooth_tr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    smooth_plus_dm = plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    smooth_minus_dm = minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    plus_di = 100 * (smooth_plus_dm / smooth_tr.replace(0.0, np.nan))
    minus_di = 100 * (smooth_minus_dm / smooth_tr.replace(0.0, np.nan))

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx_series = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    return pd.DataFrame({"plus_di": plus_di, "minus_di": minus_di, "adx": adx_series})
