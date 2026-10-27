"""DH2 / DH6: when the broker's 1-minute history stops before the last closed session, the reader's own lake bars fill it.

Production, 27 Oct 2026 03:55 IST: Upstox's 1-minute history ended at Friday 23 Oct 15:29 (close 22,600) although Monday
26 Oct had closed, and the intraday endpoint was empty before the open - the charts, the chain page's own close and the
Copilot read Friday. The owner's nightly pass had stored Monday's 375 bars in his private store.
"""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.core.models import OHLCVBar
from app.db.models import MdTenantCandleRecord
from app.market_data import lake_fill
from app.market_data.service import MarketDataService
from tests.test_auth_api import _session_factory, client  # noqa: F401 - the app and its database
from tests.test_market_data_service import _install_cache, _MemoryCache
from tests.test_phase_k_commercial import _owner

IST = ZoneInfo("Asia/Kolkata")
run = asyncio.run
TUE_PREOPEN = datetime(2026, 10, 27, 3, 55, tzinfo=IST)
FRI, MON = date(2026, 10, 23), date(2026, 10, 26)


def _session(day: date, n: int = 375, base: float = 100.0) -> list:
    start = datetime.combine(day, datetime.min.time(), tzinfo=IST).replace(hour=9, minute=15)
    return [OHLCVBar(timestamp=(start + timedelta(minutes=i)).astimezone(timezone.utc), open=base + i, high=base + i + 1,
                     low=base + i - 1, close=base + i + 0.5, volume=10) for i in range(n)]


class _LaggingBroker:
    """Upstox as seen on production: the 1-minute history holds Friday only; the intraday endpoint is empty pre-open."""
    name = "dh6-lagging"

    def __init__(self, days=(FRI,)):
        self.days = days

    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        bars = [b for d in self.days for b in _session(d)]
        return [b for b in bars if from_date <= b.timestamp <= to_date]

    async def get_intraday_candles(self, symbol, exchange, interval):
        return []


def _service(broker, calls, extra):
    service = MarketDataService(broker, lookback_days=5)

    async def fill(symbol, exchange, start, end):
        calls.append((symbol, exchange, start, end))
        return extra
    service.lake_fill = fill
    return service


def test_the_last_session_comes_from_the_own_lake_when_the_broker_history_lags(monkeypatch):
    cache = _MemoryCache()
    _install_cache(monkeypatch, cache)
    calls = []
    service = _service(_LaggingBroker(), calls, _session(MON, base=77000.0))
    bars = run(service.get_candles("NIFTY 50", "NSE", "1min", now=TUE_PREOPEN))
    assert bars[-1].timestamp.astimezone(IST).date() == MON and bars[-1].close == 77000.0 + 374 + 0.5
    assert len(bars) == 375 * 2 and [b.timestamp for b in bars] == sorted(b.timestamp for b in bars)
    [(symbol, exchange, start, end)] = calls
    assert (symbol, exchange) == ("NIFTY 50", "NSE")
    assert start == datetime(2026, 10, 23, 15, 29, tzinfo=IST) and end == datetime(2026, 10, 26, 15, 30, tzinfo=IST)
    # The cached copy stays the broker's own (shareable): no private bar is ever written to a cache.
    assert cache.store and all(bar["close"] < 1000 for v in cache.store.values() for bar in json.loads(v))


def test_a_missing_last_session_is_filled_also_once_todays_bars_arrived(monkeypatch):
    """Tuesday 10:00 IST: today's bars come from the intraday endpoint while the history still lacks Monday."""
    _install_cache(monkeypatch, _MemoryCache())
    tue = date(2026, 10, 27)

    class _Today(_LaggingBroker):
        async def get_intraday_candles(self, symbol, exchange, interval):
            return _session(tue, n=45, base=500.0)
    calls = []
    service = _service(_Today(), calls, _session(MON, base=77000.0))
    bars = run(service.get_candles("NIFTY 50", "NSE", "1min", now=datetime(2026, 10, 27, 10, 0, tzinfo=IST)))
    days = sorted({b.timestamp.astimezone(IST).date() for b in bars})
    assert days == [FRI, MON, tue] and len(calls) == 1


def test_no_fill_when_the_broker_has_the_last_session_or_for_daily_bars(monkeypatch):
    _install_cache(monkeypatch, _MemoryCache())
    calls = []
    complete = _service(_LaggingBroker(days=(FRI, MON)), calls, _session(MON, base=999.0))
    bars = run(complete.get_candles("NIFTY 50", "NSE", "1min", now=TUE_PREOPEN))
    assert calls == [] and bars[-1].close == 100.0 + 374 + 0.5
    daily = _service(_LaggingBroker(), calls, _session(MON, base=999.0))
    run(daily._fill_last_session("NIFTY 50", "NSE", "day", [], TUE_PREOPEN))
    assert calls == []


def test_without_a_lake_fill_the_broker_bars_are_shown_as_they_are(monkeypatch):
    _install_cache(monkeypatch, _MemoryCache())
    bars = run(MarketDataService(_LaggingBroker(), lookback_days=5).get_candles("NIFTY 50", "NSE", "1min", now=TUE_PREOPEN))
    assert bars[-1].timestamp.astimezone(IST).date() == FRI


def test_for_reader_reads_only_the_readers_own_private_bars():
    _, a = _owner("dh6-fill-a@example.com")
    _, b = _owner("dh6-fill-b@example.com")
    close = datetime(2026, 10, 26, 9, 16, tzinfo=IST).astimezone(timezone.utc)      # END of the 09:15 bar

    async def seed():
        async with _session_factory() as session:
            for who, price in ((a, 22700.0), (b, 11111.0)):
                session.add(MdTenantCandleRecord(tenant_id=who["tenant_id"], instrument_key="NSE_INDEX:NIFTY50", timeframe="1m",
                                                 ts=close, source=f"broker:upstox:u{who['id']}", version=1, open=price,
                                                 high=price + 5, low=price - 5, close=price + 1, volume=0))
            await session.commit()

    async def read(who):
        async with _session_factory() as session:
            fill = lake_fill.for_reader(session, SimpleNamespace(tenant_id=who["tenant_id"], id=who["id"]))
            return await fill("NIFTY 50", "NSE", datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc),
                              datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc))
    run(seed())
    [bar] = run(read(a))
    assert bar.timestamp == close - timedelta(minutes=1) and bar.close == 22701.0      # START = END - 1 minute
    [other] = run(read(b))
    assert other.close == 11112.0                                                      # never A's bar
    stranger = SimpleNamespace(tenant_id=a["tenant_id"], id=b["id"])                  # another member's id in A's tenant
    async def read_stranger():
        async with _session_factory() as session:
            return await lake_fill.for_reader(session, stranger)("NIFTY 50", "NSE", datetime(2026, 10, 23, tzinfo=timezone.utc),
                                                                 datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc))
    assert run(read_stranger()) == []
