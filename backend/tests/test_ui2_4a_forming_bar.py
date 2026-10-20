"""UI2-4a2: the forming daily bar on the chart route.

* With `forming: true` and no `before`, a day / week / month request gets today's daily bar, built from today's
  one-minute bars (the LD-10 `today` copy an intraday chart shares), marked `"forming": true` while the session is open;
  the newest week / month includes it.
* After the close on a trading day the bar is the last completed one (no mark); before the open, on a weekend or holiday,
  and on a 24x7 venue there is none.
* Without `forming` (every page today), with `before`, and for strategies and workers (`get_candles(..., "day")`): the
  completed bars only, as before - no call for today's bars."""
import asyncio
from datetime import date, datetime, time, timedelta, timezone
from typing import List

import pytest

from app.core.models import OHLCVBar
from app.market_data import candles_routes, periods, shared_candles
from app.market_data.calendar import IST
from app.market_data.service import MarketDataService
from tests.test_auth_api import _session_factory
from tests.test_ui2_4a_week_month import DailyBroker, _post, _setup, _sym, daily_series, hand

run = asyncio.run
UTC = timezone.utc


def _today() -> date:
    return datetime.now(UTC).astimezone(IST).date()


def _minutes(day: date, n: int = 30, base: float = 500.0) -> List[OHLCVBar]:
    start = datetime.combine(day, time(9, 15), tzinfo=IST)
    return [OHLCVBar(timestamp=start + timedelta(minutes=i), open=base + i, high=base + i + 2 + i % 3, low=base + i - 1 - i % 2,
                     close=base + i + 0.5, volume=10 + i) for i in range(n)]


def _state(monkeypatch, value):
    async def fake(session, body, exchange, now):
        if not body.forming or body.before is not None or body.timeframe not in candles_routes.DAILY_TIMEFRAMES:
            return None
        return value
    monkeypatch.setattr(candles_routes, "_today_state", fake)


def _broker(sym: str) -> DailyBroker:
    today = _today()
    yesterday_minutes = _minutes(today - timedelta(days=1), 5, base=1.0)   # an adapter's "today" may carry older bars: dropped
    return DailyBroker({sym: daily_series(today - timedelta(days=800), today - timedelta(days=1))},
                       today={sym: yesterday_minutes + _minutes(today)})


def test_forming_day_bar_is_the_aggregate_of_todays_one_minute_bars(monkeypatch):
    sym = _sym("FB")
    broker = _broker(sym)
    headers, _ = _setup(monkeypatch, broker, "forming")
    _state(monkeypatch, True)
    body = _post(headers, symbols=[sym], timeframe="day", lookback_days=30, forming=True)
    bars = body["symbols"][sym]["bars"]
    today = _today()
    mins = _minutes(today)
    last = bars[-1]
    assert datetime.fromisoformat(last["timestamp"]) == datetime.combine(today, time(0, 0), tzinfo=IST)
    assert last["forming"] is True and not any("forming" in b for b in bars[:-1])
    assert (last["open"], last["high"], last["low"], last["close"], last["volume"]) == (
        mins[0].open, max(b.high for b in mins), min(b.low for b in mins), mins[-1].close, sum(b.volume for b in mins))
    assert broker.intraday == [(sym, "NSE", "1min")]
    # The copy is the LD-10 `today` part of the one-minute series: an intraday chart of the symbol reads it, no new call.
    intraday = _post(headers, symbols=[sym], timeframe="5min", lookback_days=2)
    assert intraday["symbols"][sym]["error"] is None and broker.intraday == [(sym, "NSE", "1min")]


@pytest.mark.parametrize("period", ["week", "month"])
def test_the_newest_week_and_month_include_todays_bar(monkeypatch, period):
    sym = _sym("FW")
    broker = _broker(sym)
    headers, _ = _setup(monkeypatch, broker, f"forming-{period}")
    _state(monkeypatch, True)
    body = _post(headers, symbols=[sym], timeframe=period, lookback_days=400, forming=True)
    bars = body["symbols"][sym]["bars"]
    today = _today()
    today_bar = periods.today_daily_bar(_minutes(today), today)
    end = today - timedelta(days=1)
    start = periods.period_start(end - timedelta(days=399), period)
    daily = [b for b in broker.daily[sym] if start <= b.timestamp.date() <= end] + [today_bar]
    expected = hand(daily, period)
    assert bars[-1]["forming"] is True and not any("forming" in b for b in bars[:-1])
    assert (bars[-1]["close"], bars[-1]["volume"]) == (today_bar.close, expected[-1]["volume"])
    assert datetime.fromisoformat(bars[-1]["timestamp"]).astimezone(IST).date() == expected[-1]["day"]
    assert len(bars) == len(expected)


def test_after_the_close_todays_bar_is_the_last_completed_one(monkeypatch):
    sym = _sym("FC")
    broker = _broker(sym)
    headers, _ = _setup(monkeypatch, broker, "closed")
    _state(monkeypatch, False)
    bars = _post(headers, symbols=[sym], timeframe="day", lookback_days=30, forming=True)["symbols"][sym]["bars"]
    assert datetime.fromisoformat(bars[-1]["timestamp"]).astimezone(IST).date() == _today() and "forming" not in bars[-1]


def test_no_forming_bar_without_the_field_with_before_or_outside_trading_days(monkeypatch):
    sym = _sym("FN")
    broker = _broker(sym)
    headers, _ = _setup(monkeypatch, broker, "absent")
    _state(monkeypatch, True)
    today = _today()
    old = _post(headers, symbols=[sym], timeframe="day", lookback_days=30)                       # an old client
    assert datetime.fromisoformat(old["symbols"][sym]["bars"][-1]["timestamp"]).astimezone(IST).date() < today
    assert "forming" not in str(old["symbols"][sym]["bars"]) and broker.intraday == []
    paged = _post(headers, symbols=[sym], timeframe="day", lookback_days=30, forming=True, before=today.isoformat())
    assert "forming" not in str(paged["symbols"][sym]["bars"]) and broker.intraday == []
    intraday = _post(headers, symbols=[sym], timeframe="15min", lookback_days=2, forming=True)    # intraday: as before
    assert "forming" not in str(intraday["symbols"][sym]["bars"])
    _state(monkeypatch, None)                                                                     # weekend / before the open
    calls = len(broker.intraday)
    none = _post(headers, symbols=[sym], timeframe="day", lookback_days=30, forming=True)
    assert "forming" not in str(none["symbols"][sym]["bars"]) and len(broker.intraday) == calls


def test_todays_bar_failing_keeps_the_completed_bars(monkeypatch):
    sym = _sym("FF")
    broker = _broker(sym)

    async def broken(symbol, exchange, interval):
        raise RuntimeError("intraday down")
    broker.get_intraday_candles = broken
    headers, _ = _setup(monkeypatch, broker, "broken")
    _state(monkeypatch, True)
    body = _post(headers, symbols=[sym], timeframe="day", lookback_days=30, forming=True)
    assert body["symbols"][sym]["error"] is None and body["symbols"][sym]["count"] > 0
    assert any("today's bar not loaded" in w for w in body["warnings"]) and "forming" not in str(body["symbols"][sym]["bars"])


def test_the_worker_path_is_unchanged():
    async def go():
        shared_candles.reset()
        sym = _sym("FK")
        broker = _broker(sym)
        now = datetime.combine(_today(), time(11, 0), tzinfo=IST)
        bars = await MarketDataService(broker).get_candles(sym, "NSE", "day", now=now)
        return broker, bars
    broker, bars = run(go())
    assert broker.intraday == [] and bars and bars[-1].timestamp.astimezone(IST).date() < _today()


@pytest.mark.parametrize("when, holidays, exchange, expected", [
    (datetime(2026, 10, 20, 11, 0, tzinfo=IST), set(), "NSE", True),            # a Tuesday, in session
    (datetime(2026, 10, 20, 16, 5, tzinfo=IST), set(), "NSE", False),           # after the close: complete
    (datetime(2026, 10, 20, 8, 50, tzinfo=IST), set(), "NSE", None),            # before the open: no bar yet
    (datetime(2026, 10, 18, 11, 0, tzinfo=IST), set(), "NSE", None),            # Sunday
    (datetime(2026, 10, 20, 11, 0, tzinfo=IST), {date(2026, 10, 20)}, "NSE", None),   # a holiday
    (datetime(2026, 10, 20, 22, 0, tzinfo=IST), set(), "MCX", True),            # MCX trades until 23:30
    (datetime(2026, 10, 18, 11, 0, tzinfo=IST), set(), "BINANCE", None),        # a 24x7 venue: none
])
def test_today_state_follows_the_exchange_session(monkeypatch, when, holidays, expected, exchange):
    async def family(session, name):
        return set(holidays)
    monkeypatch.setattr(candles_routes, "family_holidays", family)

    async def go(body):
        async with _session_factory() as session:
            return await candles_routes._today_state(session, body, exchange, when.astimezone(UTC))
    body = candles_routes.CandlesBody(symbols=["X"], timeframe="day", forming=True)
    assert run(go(body)) is expected
    for other in (candles_routes.CandlesBody(symbols=["X"], timeframe="day"),
                  candles_routes.CandlesBody(symbols=["X"], timeframe="day", forming=True, before=date(2026, 10, 1)),
                  candles_routes.CandlesBody(symbols=["X"], timeframe="5min", forming=True)):
        assert run(go(other)) is None
