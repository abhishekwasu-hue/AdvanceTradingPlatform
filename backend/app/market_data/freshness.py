"""Phase G1: the market-data staleness gate (master prompt safety rule 7, sections 10 and 17).

A strategy evaluated on candles that stopped arriving twenty minutes ago produces a signal
about a market that no longer exists; a stop checked against a quote the broker last updated
before a feed outage is not a stop. Both decisions are refused here rather than made on stale
data:

* candles - `candle_staleness(last_bar_ts, timeframe, now)` says how far behind the *expected*
  latest closed bar the newest bar is. A `timeframe` bar is expected every `timeframe`; the gate
  allows `MARKET_DATA_MAX_STALE_BARS` missed bars (3 by default - a one-minute feed may lag a
  bar or two behind the exchange clock without anything being wrong) before refusing.
* quotes - `quote_is_stale(quote_ts, now)` compares a quote's exchange timestamp with the
  clock; older than `QUOTE_MAX_STALE_SECONDS` is stale. Brokers whose LTP endpoint carries no
  timestamp cannot be judged and are accepted (the endpoint is real-time by contract); those
  that do (Upstox/Zerodha full quotes) are.

Both return a human-readable reason, so the deployment's `last_error` / the monitor's warning
tells the operator exactly what was refused and why, instead of a silent skip.
"""
import re
from datetime import datetime, timezone
from typing import Optional

from app.core import config

_TF = re.compile(r"^\s*(\d+)\s*(min|m|h|hr|hour|d|day)\s*$", re.IGNORECASE)


def timeframe_seconds(timeframe: str) -> int:
    """'1min' -> 60, '15min' -> 900, '1h'/'60min' -> 3600, '1d' -> 86400. Unknown -> 60."""
    match = _TF.match(str(timeframe or ""))
    if not match:
        return 60
    n, unit = int(match.group(1)), match.group(2).lower()
    if unit in ("min", "m"):
        return n * 60
    if unit in ("h", "hr", "hour"):
        return n * 3600
    return n * 86400


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def bar_age_seconds(last_bar_ts: datetime, now: datetime) -> float:
    """Seconds since the newest bar *opened*."""
    return (_aware(now) - _aware(last_bar_ts)).total_seconds()


def candle_staleness(last_bar_ts: Optional[datetime], timeframe: str, now: datetime,
                     max_stale_bars: Optional[int] = None) -> Optional[str]:
    """None when the candle feed is fresh enough to act on; otherwise why it is not.

    The newest bar's open is at most `timeframe` old while it is still forming plus one more
    `timeframe` until the next one appears; allowing `max_stale_bars` missed bars on top means
    the gate trips at age > (max_stale_bars + 1) * timeframe. With the default 3 on 1-minute
    candles that is a feed more than four minutes behind the clock.
    """
    if last_bar_ts is None:
        return "no candles received"
    limit_bars = config.MARKET_DATA_MAX_STALE_BARS if max_stale_bars is None else max_stale_bars
    if limit_bars <= 0:
        return None
    step = timeframe_seconds(timeframe)
    age = bar_age_seconds(last_bar_ts, now)
    allowed = (limit_bars + 1) * step
    if age <= allowed:
        return None
    missed = int(age // step) - 1
    return (f"market data stale: newest {timeframe} bar is {int(age // 60)} min old "
            f"({missed} bars missing, limit {limit_bars})")


def quote_is_stale(quote_ts: Optional[datetime], now: datetime, max_seconds: Optional[int] = None) -> Optional[str]:
    """None when a quote is fresh (or carries no timestamp to judge by); otherwise why not."""
    if quote_ts is None:
        return None
    limit = config.QUOTE_MAX_STALE_SECONDS if max_seconds is None else max_seconds
    if limit <= 0:
        return None
    age = (_aware(now) - _aware(quote_ts)).total_seconds()
    if age <= limit:
        return None
    return f"quote stale: last trade {int(age)}s ago (limit {limit}s)"


class StaleMarketDataError(RuntimeError):
    """Raised by MarketDataService.get_ltp when the broker's quote is older than the gate allows.
    Callers treat it exactly like an unavailable price: no exit decision this cycle."""
