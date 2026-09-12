import pandas as pd


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """Volume-weighted average price, resetting at the start of each calendar day/session."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    price_volume = typical_price * df["volume"]
    session = pd.Series(df.index.date, index=df.index)

    cum_price_volume = price_volume.groupby(session).cumsum()
    cum_volume = df["volume"].groupby(session).cumsum()
    return (cum_price_volume / cum_volume.replace(0.0, pd.NA)).rename("vwap").astype(float)
