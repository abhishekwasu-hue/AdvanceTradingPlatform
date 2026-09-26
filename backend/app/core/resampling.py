from typing import Optional, Union

import pandas as pd


def resample_ohlc(df: pd.DataFrame, rule: str, origin: Optional[Union[str, pd.Timestamp]] = None) -> pd.DataFrame:
    """Aggregates an ascending OHLCV frame up to `rule` (e.g. "5min", "15min", "60min").

    `origin` anchors the bin grid. The pandas default ("start_day", midnight) is kept for every
    existing caller (backtests, S/R levels); the live market-data service passes the exchange
    open instead so 30/60-minute bars start at 09:15 like the broker's own charts do, not 09:00.
    """
    kwargs = {} if origin is None else {"origin": origin}
    agg = {
        "open": df["open"].resample(rule, **kwargs).first(),
        "high": df["high"].resample(rule, **kwargs).max(),
        "low": df["low"].resample(rule, **kwargs).min(),
        "close": df["close"].resample(rule, **kwargs).last(),
        "volume": df["volume"].resample(rule, **kwargs).sum(),
    }
    return pd.concat(agg, axis=1).dropna()
