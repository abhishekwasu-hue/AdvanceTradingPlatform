import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from app.brokers.base import BrokerInterface
from app.brokers.models import BrokerOrderRequest, BrokerOrderResponse, BrokerProfile, MarginInfo
from app.cache import client as cache_client
from app.core.models import OHLCVBar
from app.market_data import service as md_service
from app.market_data.service import MarketDataService, build_frames, merge_bars

IST = ZoneInfo("Asia/Kolkata")
run = asyncio.run


def _bars(start: datetime, n: int, base: float = 100.0) -> List[OHLCVBar]:
    return [
        OHLCVBar(
            timestamp=start + timedelta(minutes=i), open=base + i, high=base + i + 1, low=base + i - 1,
            close=base + i + 0.5, volume=10,
        )
        for i in range(n)
    ]


class _FakeBroker(BrokerInterface):
    """Counts calls so the tests can prove caching and the history+intraday split."""

    name = "fake"

    def __init__(self, history: List[OHLCVBar], intraday: List[OHLCVBar], ltp: Dict[str, float]):
        self.history, self.intraday, self.ltp = history, intraday, ltp
        self.history_calls = 0
        self.intraday_calls = 0

    async def authenticate(self): return BrokerProfile(broker="fake", user_id="x")
    async def get_profile(self): return BrokerProfile(broker="fake", user_id="x")
    async def get_instruments(self, exchange=None): return []
    async def get_ltp(self, symbols): return {s: self.ltp[s] for s in symbols}
    async def get_quote(self, symbols): return {}
    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        self.history_calls += 1
        return self.history
    async def get_intraday_candles(self, symbol, exchange, interval):
        self.intraday_calls += 1
        return self.intraday
    async def get_option_chain(self, underlying, expiry=None): raise NotImplementedError
    async def place_order(self, order: BrokerOrderRequest): return BrokerOrderResponse(order_id="1", status="OPEN")
    async def modify_order(self, order_id, quantity=None, price=None, trigger_price=None, order_type=None):
        return BrokerOrderResponse(order_id=order_id, status="MODIFIED")
    async def cancel_order(self, order_id): return BrokerOrderResponse(order_id=order_id, status="CANCELLED")
    async def get_order_book(self): return []
    async def get_trade_book(self): return []
    async def get_positions(self): return []
    async def get_holdings(self): return []
    async def get_margins(self): return MarginInfo(available_cash=0)


class _MemoryCache:
    def __init__(self):
        self.store: Dict[str, str] = {}
        self.sets = 0

    async def get(self, key): return self.store.get(key)
    async def set(self, key, value, ex=None):
        self.sets += 1
        self.store[key] = value


def _install_cache(monkeypatch, cache: Optional[_MemoryCache]):
    if cache is None:
        class _Broken:
            async def get(self, key): raise ConnectionError
            async def set(self, key, value, ex=None): raise ConnectionError
        monkeypatch.setattr(cache_client, "_get_client", lambda: _Broken())
    else:
        monkeypatch.setattr(cache_client, "_get_client", lambda: cache)


def test_merge_bars_dedupes_on_timestamp_with_later_series_winning():
    day = datetime(2026, 9, 25, 9, 15, tzinfo=IST)
    stale = _bars(day, 3, base=100)
    fresh = _bars(day + timedelta(minutes=2), 2, base=500)
    merged = merge_bars(stale, fresh)
    assert [b.timestamp for b in merged] == [day + timedelta(minutes=i) for i in range(4)]
    assert merged[2].open == 500  # overlapping 09:17 bar came from `fresh`


def test_get_candles_combines_history_and_intraday_and_caches(monkeypatch):
    cache = _MemoryCache()
    _install_cache(monkeypatch, cache)
    yesterday = datetime(2026, 9, 24, 9, 15, tzinfo=IST)
    today = datetime(2026, 9, 25, 9, 15, tzinfo=IST)
    broker = _FakeBroker(history=_bars(yesterday, 5), intraday=_bars(today, 3), ltp={})
    svc = MarketDataService(broker, cache_ttl_seconds=60)

    first = run(svc.get_candles("RELIANCE", "NSE", "1min"))
    second = run(svc.get_candles("RELIANCE", "NSE", "1min"))

    assert len(first) == 8
    assert first[-1].timestamp == today + timedelta(minutes=2)
    assert second == first
    assert broker.history_calls == 1 and broker.intraday_calls == 1  # second call served from cache
    assert cache.sets == 1


def test_get_candles_still_works_when_redis_is_down(monkeypatch):
    _install_cache(monkeypatch, None)
    today = datetime(2026, 9, 25, 9, 15, tzinfo=IST)
    broker = _FakeBroker(history=[], intraday=_bars(today, 3), ltp={})
    svc = MarketDataService(broker)

    run(svc.get_candles("RELIANCE"))
    run(svc.get_candles("RELIANCE"))
    assert broker.history_calls == 2  # no cache -> refetch every call, but never an error


def test_build_frames_resamples_anchored_to_market_open():
    start = datetime(2026, 9, 25, 9, 15, tzinfo=IST)
    bars = _bars(start, 120)  # 09:15 .. 11:14
    frames = build_frames(bars, "1min", ["1min", "5min", "30min"])

    assert len(frames["1min"]) == 120
    assert frames["5min"].index[0] == start
    assert len(frames["5min"]) == 24
    # 30-minute bins start at the 09:15 open, not the 09:00 clock hour.
    assert frames["30min"].index[0] == start
    assert frames["30min"].index[1] == start + timedelta(minutes=30)
    first = frames["30min"].iloc[0]
    assert first["open"] == bars[0].open
    assert first["close"] == bars[29].close
    assert first["high"] == max(b.high for b in bars[:30])
    assert first["volume"] == 300


def test_get_ltp_is_never_cached(monkeypatch):
    cache = _MemoryCache()
    _install_cache(monkeypatch, cache)
    broker = _FakeBroker(history=[], intraday=[], ltp={"NSE:RELIANCE": 2500.5})
    svc = MarketDataService(broker)
    assert run(svc.get_ltp("RELIANCE", "NSE")) == 2500.5
    assert cache.sets == 0


def test_cache_key_is_shared_across_tenants_but_split_by_broker_symbol_interval():
    a = md_service._cache_key("upstox", "NSE", "RELIANCE", "1min")
    assert a == md_service._cache_key("upstox", "NSE", "RELIANCE", "1min")
    assert a != md_service._cache_key("zerodha", "NSE", "RELIANCE", "1min")
    assert a != md_service._cache_key("upstox", "NSE", "RELIANCE", "5min")
