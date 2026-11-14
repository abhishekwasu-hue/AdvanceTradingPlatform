"""Runner S2: where the runner's bars and prices come from - the user's OWN broker session on his own machine.

ATP never streams market data to the runner (design section 4). `BrokerFeed` reads candles and LTPs from his broker
adapter (Angel One first, RUN-Q3; any `BrokerInterface` works). `FakeFeed` serves fixed bars and prices for tests and
dry runs; it is PAPER-only - the runner refuses every LIVE entry while it is the feed.

Frames are UTC-indexed OHLCV DataFrames keyed by timeframe label, as the strategies read them (strategy_engine.base).
The bar still forming is NOT removed here: the loop drops it with `market_data.freshness.closed_frames`, as the worker
and the backtest do (decisions on closed bars only - no look-ahead).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Protocol

import pandas as pd

from app.brokers.base import BrokerInterface
from app.brokers.rate_budget import RateBudget, RateLimitedBroker, daily_budget_for, limits_for
from app.execution.ops_throttle import OpsThrottle
from app.market_data.freshness import timeframe_seconds

COLUMNS = ["open", "high", "low", "close", "volume"]


class BarFeed(Protocol):
    paper_only: bool
    broker: BrokerInterface | None      # his broker session (PAPER fills are priced from it too); None for a fake

    async def frames(self, symbol: str, exchange: str, timeframes: Iterable[str], now: datetime) -> dict[str, pd.DataFrame]: ...

    async def ltp(self, symbol: str, exchange: str) -> float: ...


def bars_to_frame(bars: Iterable) -> pd.DataFrame:
    """OHLCVBar rows -> a UTC-indexed DataFrame (ascending, one row per bar start)."""
    rows = [(b.timestamp, b.open, b.high, b.low, b.close, b.volume) for b in bars]
    if not rows:
        return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], tz="UTC"))
    frame = pd.DataFrame([r[1:] for r in rows], columns=COLUMNS,
                         index=pd.DatetimeIndex([pd.Timestamp(r[0]) for r in rows]))
    index = frame.index
    frame.index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    return frame.astype(float)


def protected(broker: BrokerInterface, *, ops_throttle: bool = True) -> BrokerInterface:
    """His adapter behind the wrapper the server worker uses for every session (`RateLimitedBroker`): the broker's rate
    budget (in-process buckets - no Redis on the runner), the Phase 0 PAPER-only chokepoint on new entry orders, and the
    SEBI orders-per-second throttle (rule R-9: 10/s from the compliance rules, default ON; exits wait, never refused)."""
    budget = RateBudget(limits_for(broker.name), daily=daily_budget_for(broker.name))
    return RateLimitedBroker(broker, budget, ops=OpsThrottle() if ops_throttle else None)


class BrokerFeed:
    """Candles and LTPs from the user's own broker adapter."""

    paper_only = False

    def __init__(self, broker: BrokerInterface, *, history_days_intraday: int = 20, history_days_daily: int = 400) -> None:
        self.broker: BrokerInterface | None = broker
        self._days_intraday = history_days_intraday
        self._days_daily = history_days_daily

    def _start(self, timeframe: str, now: datetime) -> datetime:
        days = self._days_daily if timeframe_seconds(timeframe) >= 86400 else self._days_intraday
        return now - timedelta(days=days)

    async def frames(self, symbol: str, exchange: str, timeframes: Iterable[str], now: datetime) -> dict[str, pd.DataFrame]:
        assert self.broker is not None
        out: dict[str, pd.DataFrame] = {}
        for timeframe in dict.fromkeys(timeframes):
            bars = await self.broker.get_historical_data(symbol, exchange, timeframe, self._start(timeframe, now), now)
            out[timeframe] = bars_to_frame(bars)
        return out

    async def ltp(self, symbol: str, exchange: str) -> float:
        assert self.broker is not None
        return float(await self.broker.get_ltp_for_symbol(symbol, exchange))


PriceSource = float | Callable[[], float]


class FakeQuotes:
    """S4b: the fake feed's prices as the quote source the derivative pipeline reads a contract's premium from (PAPER
    only: it has no order methods, and the runner never hands it to a LIVE path - `FakeFeed.broker` stays None)."""

    name = "test_feed"

    def __init__(self, feed: FakeFeed) -> None:
        self._feed = feed

    async def get_ltp_for_symbol(self, symbol: str, exchange: str = "NSE") -> float:
        return await self._feed.ltp(symbol, exchange)

    async def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        return {s: await self._feed.ltp(s, "") for s in symbols}


class FakeFeed:
    """Fixed frames and prices (tests, dry runs). PAPER only: no broker, so nothing can be sent anywhere."""

    paper_only = True
    broker: BrokerInterface | None = None

    def __init__(self) -> None:
        self._frames: dict[str, dict[str, pd.DataFrame]] = {}
        self._prices: dict[str, PriceSource] = {}
        self.quotes = FakeQuotes(self)

    def set_frames(self, symbol: str, frames: dict[str, pd.DataFrame]) -> None:
        self._frames[symbol.upper()] = frames

    def set_price(self, symbol: str, price: PriceSource) -> None:
        self._prices[symbol.upper()] = price

    async def frames(self, symbol: str, exchange: str, timeframes: Iterable[str], now: datetime) -> dict[str, pd.DataFrame]:
        held = self._frames.get(symbol.upper(), {})
        stamp = pd.Timestamp(now if now.tzinfo else now.replace(tzinfo=UTC)).tz_convert("UTC")
        # a real feed never returns a bar that starts after `now`
        return {tf: held[tf][held[tf].index <= stamp] for tf in dict.fromkeys(timeframes) if tf in held}

    async def ltp(self, symbol: str, exchange: str) -> float:
        source = self._prices.get(symbol.upper())
        if source is None:
            raise LookupError(f"no price for {symbol}")
        return float(source() if callable(source) else source)


__all__ = ["BarFeed", "BrokerFeed", "FakeFeed", "FakeQuotes", "bars_to_frame", "protected"]
