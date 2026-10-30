"""DH6 follow-up: the session-end fill from the own lake keeps ONE timezone in the merged bars.

The Upstox adapter's bars carry the broker's own +05:30 offset (parsed from its "2026-10-23T09:15:00+05:30" strings); the
lake fill returns UTC bars (lake_fill._utc). Merged as they were, pandas refused the mixed zones ("Tz-aware
datetime.datetime cannot be converted to datetime64 unless utc=True") - every 5-minute and longer chart failed after a
fill. These run the real chain: MarketDataService.get_candles -> _fill_last_session -> merge_bars -> the chart route's
_shape (build_frames / resample_ohlc), at 1m, 5m, 15m and 60m.
"""
import asyncio
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.models import OHLCVBar, bars_to_dataframe
from app.market_data import lake_fill
from app.market_data.candles_routes import _shape
from app.market_data.service import MarketDataService
from tests.test_market_data_service import _install_cache, _MemoryCache

IST = ZoneInfo("Asia/Kolkata")
run = asyncio.run
TUE_PREOPEN = datetime(2026, 10, 27, 3, 55, tzinfo=IST)
FRI, MON = date(2026, 10, 23), date(2026, 10, 26)
SESSION = 375
PER_DAY = {"1min": 375, "5min": 75, "15min": 25, "60min": 7}       # 09:15 .. 15:29; 60m: 09:15 .. 15:15 (the last is 15 min)


def _upstox_session(day: date) -> list:
    """Bars as the Upstox adapter builds them: OHLCVBar(timestamp=<the broker's +05:30 string>)."""
    start = datetime(day.year, day.month, day.day, 9, 15)
    return [OHLCVBar(timestamp=(start + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:00+05:30"), open=100.0 + i,
                     high=101.0 + i, low=99.0 + i, close=100.5 + i, volume=10) for i in range(SESSION)]


def _lake_session(day: date) -> list:
    """Bars as lake_fill.for_reader returns them: the store's naive-UTC bar END, made a UTC-aware bar start."""
    first_end = datetime(day.year, day.month, day.day, 9, 16, tzinfo=IST).astimezone(UTC).replace(tzinfo=None)
    return [OHLCVBar(timestamp=lake_fill._utc(first_end + timedelta(minutes=i)) - lake_fill.MINUTE, open=500.0 + i,
                     high=501.0 + i, low=499.0 + i, close=500.5 + i, volume=20) for i in range(SESSION)]


class _UpstoxLagging:
    """Upstox on 27 Oct 2026 pre-open: 1-minute history holds Friday only, the intraday endpoint is empty."""
    name = "tz-lagging"

    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        return [b for b in _upstox_session(FRI) if from_date <= b.timestamp <= to_date]

    async def get_intraday_candles(self, symbol, exchange, interval):
        return []


def _filled(monkeypatch) -> list:
    _install_cache(monkeypatch, _MemoryCache())
    service = MarketDataService(_UpstoxLagging(), lookback_days=5)

    async def fill(symbol, exchange, start, end):
        return [b for b in _lake_session(MON) if start < b.timestamp <= end]
    service.lake_fill = fill
    return run(service.get_candles("NIFTY 50", "NSE", "1min", now=TUE_PREOPEN))


def test_the_shapes_are_realistic():
    broker, lake = _upstox_session(FRI), _lake_session(MON)
    assert broker[0].timestamp.utcoffset() == timedelta(hours=5, minutes=30)
    assert lake[0].timestamp.tzinfo == UTC and lake[0].timestamp.astimezone(IST).strftime("%H:%M") == "09:15"


def test_the_merged_bars_have_one_timezone_and_build_a_frame(monkeypatch):
    bars = _filled(monkeypatch)
    assert len(bars) == 2 * SESSION
    assert len({b.timestamp.utcoffset() for b in bars}) == 1
    assert len({b.timestamp.tzinfo for b in bars}) == 1
    df = bars_to_dataframe(bars)                                     # raised before the fix
    assert str(df.index.tz) != "None" and df.index.is_monotonic_increasing and df.index.is_unique
    # The fill's instants are unchanged: Monday 09:15 IST opens the lake's part, Friday 15:29 IST closes the broker's.
    first_mon = next(b for b in bars if b.open >= 500)
    assert first_mon.timestamp == datetime(2026, 10, 26, 9, 15, tzinfo=IST)
    assert bars[SESSION - 1].timestamp == datetime(2026, 10, 23, 15, 29, tzinfo=IST) and bars[SESSION - 1].open < 500


@pytest.mark.parametrize("timeframe", ["1min", "5min", "15min", "60min"])
def test_every_timeframe_shapes_on_the_session_grid(monkeypatch, timeframe):
    shaped = _shape(_filled(monkeypatch), timeframe)
    by_day = {}
    for bar in shaped:
        by_day.setdefault(bar.timestamp.astimezone(IST).date(), []).append(bar)
    assert sorted(by_day) == [FRI, MON]
    for day, day_bars in by_day.items():
        assert len(day_bars) == PER_DAY[timeframe], (timeframe, day)
        assert day_bars[0].timestamp.astimezone(IST).strftime("%H:%M") == "09:15"
    assert [b.timestamp for b in shaped] == sorted({b.timestamp for b in shaped})        # ascending, no duplicates
    # No bar mixes the two sources (the broker's part < 500 <= the lake's part): no bin straddles the gap.
    assert all(b.high < 500 or b.low >= 499 for b in shaped)
    # Monday's first bar opens at the lake's 09:15 open; Friday's last closes at the broker's 15:29 close.
    assert by_day[MON][0].open == 500.0 and by_day[FRI][-1].close == 100.5 + SESSION - 1
