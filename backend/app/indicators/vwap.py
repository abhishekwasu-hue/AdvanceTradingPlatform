import pandas as pd
from app.indicators.prefix_cache import prefix_cached


def _ist_dates(index: pd.Index) -> pd.Series:
    """The IST trading date of each bar: a tz-aware index is converted to IST; a naive one is exchange time already."""
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_convert("Asia/Kolkata")
    return pd.Series(idx.date, index=index)


@prefix_cached
def session_vwap(df: pd.DataFrame) -> pd.Series:
    """Volume-weighted average price, restarting at the start of each IST trading date (Z10-a: the same session as the
    Strategy Builder operand, the strategies and the chart; it used the index's own date, the UTC date on a tz-aware
    index, which split a session crossing UTC midnight). No value (NaN) until the session has volume."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    price_volume = typical_price * df["volume"]
    session = _ist_dates(df.index)

    cum_price_volume = price_volume.groupby(session).cumsum()
    cum_volume = df["volume"].groupby(session).cumsum()
    # Z10-a: `.replace(0.0, pd.NA)` made an object column whose astype(float) raised TypeError on any session without
    # volume yet (an index: the S/R engine, the scanner's zone filters and signal scoring crashed on it)
    return (cum_price_volume / cum_volume.where(cum_volume > 0)).rename("vwap").astype(float)
