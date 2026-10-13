"""LD-7 (500-user load test, run 10): broker fetches side by side instead of one after another.

* A screen run fetches its symbols a few at a time - at most the broker's burst allowance (app/brokers/rate_budget.py),
  never more than FETCH_CONCURRENCY - and keeps the symbols' order and per-symbol problems as before.
* A chart's two calls (history and today's bars) are made side by side; a failure of either still fails the fetch.
* The load generator can keep itself off the server's CPUs (the CI's KVM 2 shape).
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.models import OHLCVBar

BACKEND = Path(__file__).resolve().parents[1]


def _run(coro):
    return asyncio.run(coro)


def _bars(start: datetime, closes, minutes: int = 1):
    return [OHLCVBar(timestamp=start + timedelta(minutes=minutes * i), open=c, high=c, low=c, close=c, volume=100)
            for i, c in enumerate(closes)]


def test_fetch_concurrency_follows_the_broker_burst_and_the_cap():
    from app.brokers.rate_budget import BROKER_RATE_LIMITS, DEFAULT_RATE_LIMITS
    from app.screener.routes import FETCH_CONCURRENCY, fetch_concurrency
    assert FETCH_CONCURRENCY == 3
    assert fetch_concurrency("dhan") == min(3, DEFAULT_RATE_LIMITS.burst)            # not listed: the default budget
    assert fetch_concurrency("upstox") == min(3, BROKER_RATE_LIMITS["upstox"].burst)
    assert fetch_concurrency("zerodha") == min(3, BROKER_RATE_LIMITS["zerodha"].burst)
    assert all(1 <= fetch_concurrency(name) <= 3 for name in (*BROKER_RATE_LIMITS, "anything"))


def test_a_screen_fetches_its_symbols_a_few_at_a_time_in_order(monkeypatch):
    from app.brokers import token_lifecycle
    from app.market_data import candles_routes, live_access
    from app.market_data.service import MarketDataService
    from app.screener.routes import fetch_frames

    start = datetime(2026, 10, 13, 3, 45, tzinfo=timezone.utc)                       # 09:15 IST
    in_flight, peak, order = [0], [0], []

    async def get_candles(self, symbol, exchange="NSE", interval="1min", now=None, fresh=False):
        in_flight[0] += 1
        peak[0] = max(peak[0], in_flight[0])
        order.append(symbol)
        try:
            await asyncio.sleep(0.02 if symbol != "SLOW" else 0.06)
            if symbol == "BAD":
                raise RuntimeError("no such symbol")
            return _bars(start, [1, 2, 3, 4, 5])
        finally:
            in_flight[0] -= 1

    async def pick(*a, **k):
        return SimpleNamespace(broker_name="dhan")

    async def byob_off(session, tenant_id):
        return False
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: object())
    monkeypatch.setattr(MarketDataService, "get_candles", get_candles)
    monkeypatch.setattr(live_access, "byob_on", byob_off)

    symbols = ["SLOW", "TCS", "BAD", "INFY", "SBIN", "LT", "ITC"]
    universe, problems, source = _run(fetch_frames(None, 1, symbols, "NSE", "1m", {"1m": 3}, now=start + timedelta(minutes=10)))
    assert peak[0] == 3                                                                # a few at a time, never more
    assert sorted(order) == sorted(symbols)
    assert [d.symbol for d in universe] == ["SLOW", "TCS", "INFY", "SBIN", "LT", "ITC"]  # the symbols' order, not arrival
    assert problems == {"BAD": "RuntimeError: no such symbol"}
    assert source == "broker:dhan"


class _Broker:
    name = "fake"

    def __init__(self, fail: str = ""):
        self.fail = fail
        self.in_flight = 0
        self.peak = 0

    async def _call(self, what: str, bars):
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.02)
            if what == self.fail:
                raise RuntimeError(f"{what} failed")
            return bars
        finally:
            self.in_flight -= 1

    async def get_historical_data(self, symbol, exchange, interval, start, end):
        return await self._call("history", _bars(datetime(2026, 10, 12, 3, 45, tzinfo=timezone.utc), [1, 2]))

    async def get_intraday_candles(self, symbol, exchange, interval):
        return await self._call("intraday", _bars(datetime(2026, 10, 13, 3, 45, tzinfo=timezone.utc), [3, 4]))


def test_a_chart_fetches_history_and_today_side_by_side():
    from app.market_data.service import MarketDataService
    broker = _Broker()
    bars = _run(MarketDataService(broker)._fetch("TCS", "NSE", "1min", datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc)))
    assert broker.peak == 2
    assert [b.close for b in bars] == [1, 2, 3, 4]


@pytest.mark.parametrize("fail", ["history", "intraday"])
def test_either_call_failing_still_fails_the_fetch(fail):
    from app.market_data.service import MarketDataService
    with pytest.raises(RuntimeError, match=f"{fail} failed"):
        _run(MarketDataService(_Broker(fail))._fetch("TCS", "NSE", "1min", datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc)))


def test_daily_bars_are_still_one_call():
    from app.market_data.service import MarketDataService
    broker = _Broker()
    bars = _run(MarketDataService(broker)._fetch("TCS", "NSE", "day", datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc)))
    assert broker.peak == 1 and [b.close for b in bars] == [1, 2]


def test_the_load_generator_can_pin_itself_to_cpus():
    sys.path.insert(0, str(BACKEND / "scripts"))
    try:
        import loadtest
    finally:
        sys.path.pop(0)
    assert loadtest.pin_cpus("2,3") == {2, 3}
    assert loadtest.pin_cpus("0-1,3") == {0, 1, 3}
    stack = (BACKEND.parent / "deploy/ci/stack.sh").read_text()
    assert '--pin-cpus "$client_cpus"' in stack and "cmd_pin()" in stack
