"""SCR-FIX A1: one way for every caller to get bars at an interval its broker may not serve as it is.

Each adapter declares the ATP intervals its APIs accept (`BrokerInterface.native_intervals`; Upstox's v2 API, for one,
serves only 1minute / 30minute / day / week / month). A caller asks for any interval here:

* `fetch_interval(broker, interval)` - the interval actually sent to the broker: the interval itself when native, else
  the largest native intraday interval that divides it (30min for 1h / 2h / 4h where 30min is native, 1min for 5m / 15m
  on Upstox), "day" for a week or a month (built from daily bars in IST, as the Charts page does).
* `reshape(bars, base, interval)` - the base bars grouped up to the interval with the existing anchored resampler
  (`core.resampling.resample_ohlc`, bins from the 09:15 IST open - the same grid as Charts' `build_frames`), or
  `periods.aggregate` for weeks / months. A bucket still forming stays in, exactly as a native fetch returns it.
* `get_bars(service, ...)` - both: `MarketDataService.get_candles` at the fetch interval, reshaped.

Nothing here decides anything about a trade; it only shapes bars.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, FrozenSet, Iterable, List, Optional

import pandas as pd

from app.core.models import OHLCVBar, bars_to_dataframe
from app.core.resampling import resample_ohlc
from app.market_data import periods
from app.market_data.calendar import IST, MARKET_OPEN

# What an adapter that declares nothing (a test double, an older adapter) is assumed to accept: the intervals every
# adapter accepted before SCR-FIX.
DEFAULT_NATIVE: FrozenSet[str] = frozenset({"1min", "3min", "5min", "10min", "15min", "30min", "60min", "day"})
_MINUTES = re.compile(r"^(\d+)min$")


def minutes_of(interval: str) -> Optional[int]:
    """Minutes of an intraday interval ("15min" -> 15), None for day / week / month or an unknown name."""
    m = _MINUTES.match(interval or "")
    return int(m.group(1)) if m else None


def native_intervals(broker: Any) -> FrozenSet[str]:
    """The intervals `broker` (an adapter, or a wrapper passing attributes through) accepts as they are."""
    declared = getattr(broker, "native_intervals", None)
    return frozenset(declared) if declared else DEFAULT_NATIVE


def fetch_interval(broker: Any, interval: str, among: Optional[Iterable[str]] = None) -> str:
    """The interval to send to the broker for bars at `interval` (see the module docstring). `among`: the base intervals
    the caller can resample from (the screener's own timeframes); None = any native one."""
    if interval in periods.PERIODS:
        return "day"
    native = native_intervals(broker)
    if among is not None:
        native = native & frozenset(among) | ({interval} & native)
    if interval in native:
        return interval
    wanted = minutes_of(interval)
    if wanted is None:
        return interval                     # day (always native) or an unknown name: the broker's own answer, as before
    divisors = [(m, name) for name in native if (m := minutes_of(name)) and wanted % m == 0]
    if not divisors:
        return "1min"
    return max(divisors)[1]


def _frame_to_bars(df: pd.DataFrame) -> List[OHLCVBar]:
    df = df.dropna(subset=["open", "close"])
    return [OHLCVBar(timestamp=ts.to_pydatetime(), open=float(o), high=float(h), low=float(lo), close=float(c),
                     volume=float(v) if v == v else 0.0)
            for ts, o, h, lo, c, v in zip(df.index, df["open"], df["high"], df["low"], df["close"], df["volume"])]


def reshape(bars: List[OHLCVBar], base: str, interval: str) -> List[OHLCVBar]:
    """`bars` at `base` grouped up to `interval` (bar start labels, 09:15 IST grid). Unchanged when they already are."""
    if not bars or base == interval:
        return list(bars)
    if interval in periods.PERIODS:
        return periods.aggregate(list(bars), interval)
    df = bars_to_dataframe(bars)
    tz = df.index.tz
    ist = (df.index.tz_localize("UTC") if tz is None else df.index).tz_convert(IST)
    df = df.set_axis(ist)
    # The Charts origin (`service._session_origin`) on an IST index: 09:15 IST, whatever zone the broker stamped bars in.
    origin = ist[0].normalize() + pd.Timedelta(hours=MARKET_OPEN.hour, minutes=MARKET_OPEN.minute)
    out = resample_ohlc(df, interval, origin=origin)
    out.index = out.index.tz_convert("UTC" if tz is None else tz)
    if tz is None:
        out.index = out.index.tz_localize(None)
    return _frame_to_bars(out)


async def get_bars(service: Any, symbol: str, exchange: str, interval: str, *, now: Optional[datetime] = None,
                   fresh: bool = False) -> List[OHLCVBar]:
    """Bars at `interval` through `service` (a MarketDataService): fetched at the broker's native interval, reshaped."""
    base = fetch_interval(service.broker, interval)
    if fresh:
        bars = await service.get_candles(symbol, exchange, base, now=now, fresh=True)
    else:
        bars = await service.get_candles(symbol, exchange, base, now=now)
    return reshape(bars, base, interval)


__all__ = ["DEFAULT_NATIVE", "fetch_interval", "get_bars", "minutes_of", "native_intervals", "reshape"]
