"""Phase S: streaming quotes - protobuf/Kite decoders, the tick cache, get_ltp preferring fresh
ticks, the stream loop's reconnect and resubscribe, the Upstox and Kite protocol frames, and the
worker keeping a stream subscribed to what its deployments and positions need."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from app.core import config
from app.market_data import stream as st
from app.market_data.service import MarketDataService
from app.market_data.stream import (
    KiteTickStream, StreamManager, Tick, TickCache, TickStream, UpstoxTickStream, decode_kite, decode_upstox_v3, encode_kite,
    encode_upstox_v3, stream_for,
)
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _tenant, _worker

NOW = datetime(2026, 9, 25, 5, 0, tzinfo=timezone.utc)   # 10:30 IST


def _run(coro):
    return asyncio.run(coro)


class _Memory:
    def __init__(self):
        self.data: Dict[str, str] = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ttl):
        self.data[key] = value


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch):
    mem = _Memory()
    monkeypatch.setattr(st, "cache_get", mem.get)
    monkeypatch.setattr(st, "cache_set", mem.set)
    st.tick_cache.clear()
    yield mem
    st.tick_cache.clear()


# --- decoders ------------------------------------------------------------------------------------

def test_upstox_v3_protobuf_roundtrip_ltpc_and_full_feed():
    feeds = {"NSE_EQ|INE002A01018": {"ltp": 2500.5, "ltt": 1790000000123, "ltq": 10, "cp": 2490.0},
             "NSE_INDEX|Nifty 50": {"ltp": 24512.35, "ltt": 1790000000456}}
    frame = encode_upstox_v3(feeds, current_ts_ms=1790000001000)
    decoded = decode_upstox_v3(frame)
    assert decoded["NSE_EQ|INE002A01018"]["ltp"] == 2500.5 and decoded["NSE_EQ|INE002A01018"]["ltt"] == 1790000000123
    assert decoded["NSE_EQ|INE002A01018"]["cp"] == 2490.0 and decoded["NSE_EQ|INE002A01018"]["ltq"] == 10
    assert decoded["NSE_INDEX|Nifty 50"]["ltp"] == 24512.35
    # The same prices wrapped in FullFeed/MarketFullFeed (the "full" subscription mode).
    full = decode_upstox_v3(encode_upstox_v3(feeds, full=True))
    assert full["NSE_EQ|INE002A01018"]["ltp"] == 2500.5 and full["NSE_INDEX|Nifty 50"]["ltt"] == 1790000000456
    # A frame without feeds (market_info / snapshot header only) is simply empty.
    assert decode_upstox_v3(encode_upstox_v3({}, current_ts_ms=5)) == {}
    with pytest.raises(ValueError):
        decode_upstox_v3(b"\x12\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff")


def test_kite_binary_packets_ltp_full_and_currency_divisor():
    frame = encode_kite([{"token": 738561, "ltp": 2500.55}, {"token": 256265, "ltp": 24512.3, "exchange_ts": 1790000000, "ltt": 1789999990}])
    packets = decode_kite(frame)
    assert packets[0] == {"token": 738561, "ltp": 2500.55}
    assert packets[1]["token"] == 256265 and packets[1]["ltp"] == 24512.3
    assert packets[1]["exchange_ts"] == 1790000000 and packets[1]["ltt"] == 1789999990
    cds_token = (12345 << 8) | 3            # segment byte 3 = CDS: prices in 1e7
    assert decode_kite(encode_kite([{"token": cds_token, "ltp": 83.4525}]))[0]["ltp"] == pytest.approx(83.4525)
    assert decode_kite(b"\x00") == [] and decode_kite(b"") == []


# --- tick cache and get_ltp ----------------------------------------------------------------------

def _tick(symbol="RELIANCE", ltp=2500.0, age=5, exchange="NSE", broker="upstox", with_exchange_ts=True) -> Tick:
    ts = NOW - timedelta(seconds=age)
    return Tick(broker=broker, exchange=exchange, symbol=symbol, ltp=ltp, exchange_ts=ts if with_exchange_ts else None, received_at=ts)


def test_tick_cache_freshness_and_redis_mirror(monkeypatch, _isolated_cache):
    cache = TickCache()
    _run(cache.put(_tick(age=5)))
    assert _run(cache.fresh("upstox", "NSE", "RELIANCE", NOW)).ltp == 2500.0
    assert _run(cache.fresh("upstox", "nse", "reliance", NOW)) is not None          # case-insensitive key
    assert _run(cache.fresh("zerodha", "NSE", "RELIANCE", NOW)) is None             # per broker
    _run(cache.put(_tick(age=config.TICK_MAX_AGE_SECONDS + 1)))
    assert _run(cache.fresh("upstox", "NSE", "RELIANCE", NOW)) is None              # too old
    assert _run(cache.get("upstox", "NSE", "RELIANCE")).ltp == 2500.0               # but still the newest known
    # Another process (the API) reads the Redis mirror.
    other = TickCache()
    assert _run(other.get("upstox", "NSE", "RELIANCE")).symbol == "RELIANCE" and len(_isolated_cache.data) == 1
    monkeypatch.setattr(config, "TICK_MAX_AGE_SECONDS", 0)                          # 0 = no age check
    assert _run(cache.fresh("upstox", "NSE", "RELIANCE", NOW)) is not None


class _RestBroker(_FakeBroker):
    def __init__(self):
        super().__init__()
        self.rest_calls = 0

    async def get_quote_for_symbol(self, symbol, exchange="NSE"):
        return None   # no exchange timestamp from this broker -> get_ltp_for_symbol is the REST path

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        self.rest_calls += 1
        return 99.0


def test_get_ltp_uses_a_fresh_tick_and_falls_back_to_rest_otherwise():
    broker = _RestBroker()
    svc = MarketDataService(broker)
    _run(st.tick_cache.put(_tick(age=3)))
    assert _run(svc.get_ltp("RELIANCE", "NSE", now=NOW)) == 2500.0 and broker.rest_calls == 0
    _run(st.tick_cache.put(_tick(age=config.TICK_MAX_AGE_SECONDS + 30)))
    assert _run(svc.get_ltp("RELIANCE", "NSE", now=NOW)) == 99.0 and broker.rest_calls == 1
    assert _run(svc.get_ltp("TCS", "NSE", now=NOW)) == 99.0 and broker.rest_calls == 2       # never streamed


# --- the stream loop ------------------------------------------------------------------------------

class _FakeConn:
    """A scripted websocket: yields `frames`, then raises `fail` (or ends), records sends."""

    def __init__(self, frames: List[Any], *, fail: Optional[Exception] = None, hold: Optional[asyncio.Event] = None):
        self.frames = list(frames)
        self.fail = fail
        self.hold = hold
        self.sent: List[Any] = []
        self.closed = False

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for f in self.frames:
            yield f
        if self.hold is not None:
            await self.hold.wait()
        if self.fail is not None:
            raise self.fail

    async def close(self):
        self.closed = True


class _JsonStream(TickStream):
    """Minimal protocol for the loop tests: keys are "EXCH:SYMBOL", frames are JSON."""
    name = "fake"

    def __init__(self, url="wss://fake", **kw):
        super().__init__(**kw)
        self.url = url

    async def connect_url(self):
        return self.url

    async def wire_key(self, symbol, exchange):
        if symbol == "BROKEN":
            raise KeyError("unknown")
        return f"{exchange}:{symbol}"

    def subscribe_frames(self, keys):
        return [json.dumps({"sub": list(keys)})]

    def unsubscribe_frames(self, keys):
        return [json.dumps({"unsub": list(keys)})]

    def decode(self, frame):
        d = json.loads(frame)
        return [(k, v, None) for k, v in d.items()]


def test_stream_reconnects_resubscribes_and_caches_ticks(monkeypatch):
    monkeypatch.setattr(st, "BACKOFF_SECONDS", (0.01, 0.01))
    conns: List[_FakeConn] = []

    async def go():
        hold = asyncio.Event()
        first = _FakeConn([json.dumps({"NSE:RELIANCE": 2501.0, "NSE:IGNORED": 1.0})], fail=ConnectionError("dropped"))
        second = _FakeConn([json.dumps({"NSE:RELIANCE": 2502.0})], hold=hold)
        conns.extend([first, second])
        it = iter(conns)

        async def connector(url):
            return next(it)
        stream = _JsonStream(cache=st.tick_cache, connector=connector)
        await stream.set_symbols([("RELIANCE", "NSE"), ("BROKEN", "NSE")])
        task = asyncio.create_task(stream.run())
        for _ in range(200):
            if stream.reconnects >= 1 and stream.ticks >= 2:
                break
            await asyncio.sleep(0.01)
        status = stream.status()
        # A subscription change while connected is sent on the live socket.
        await stream.set_symbols([("RELIANCE", "NSE"), ("TCS", "NSE")])
        await asyncio.sleep(0.02)
        stream.stop()
        hold.set()
        await asyncio.wait_for(task, timeout=2)
        return stream, status
    stream, status = _run(go())
    assert status.reconnects == 1 and status.connected and status.ticks == 2 and status.last_error is None
    assert stream.subscribed == 2 and "BROKEN" not in json.dumps([c.sent for c in conns])
    assert json.loads(conns[0].sent[0]) == {"sub": ["NSE:RELIANCE"]}          # first socket
    assert json.loads(conns[1].sent[0]) == {"sub": ["NSE:RELIANCE"]}          # resent on the new socket
    assert json.loads(conns[1].sent[1]) == {"sub": ["NSE:TCS"]}               # the later addition
    tick = _run(st.tick_cache.get("fake", "NSE", "RELIANCE"))
    assert tick.ltp == 2502.0 and all(c.closed for c in conns)
    assert _run(st.tick_cache.get("fake", "NSE", "IGNORED")) is None            # unsubscribed keys are dropped


# --- broker protocols -----------------------------------------------------------------------------

class _Instrument:
    def __init__(self, token, symbol, exchange="NSE"):
        self.instrument_token, self.tradingsymbol, self.exchange = token, symbol, exchange


class _UpstoxAdapter:
    name = "upstox"

    def __init__(self):
        self.requests = []

    async def _request(self, method, path, **kw):
        self.requests.append((method, path))
        return {"authorized_redirect_uri": "wss://api.upstox.com/v3/feed/market-data-feed?token=abc"}

    async def _resolve_instrument(self, symbol, exchange):
        return _Instrument({"RELIANCE": "NSE_EQ|INE002A01018", "NIFTY 50": "NSE_INDEX|Nifty 50"}[symbol], symbol, exchange)


def test_upstox_stream_authorises_subscribes_ltpc_and_decodes_protobuf():
    adapter = _UpstoxAdapter()
    conn_holder = {}

    async def go():
        hold = asyncio.Event()
        frame = encode_upstox_v3({"NSE_EQ|INE002A01018": {"ltp": 2500.5, "ltt": int(NOW.timestamp() * 1000)},
                                  "NSE_INDEX|Nifty 50": {"ltp": 24512.0}}, current_ts_ms=int(NOW.timestamp() * 1000))
        conn = _FakeConn([frame, "text frames are ignored"], hold=hold)
        conn_holder["conn"] = conn

        async def connector(url):
            conn_holder["url"] = url
            return conn
        stream = UpstoxTickStream(adapter, cache=st.tick_cache, connector=connector)
        await stream.set_symbols([("RELIANCE", "NSE"), ("NIFTY 50", "NSE")])
        task = asyncio.create_task(stream.run())
        for _ in range(200):
            if stream.ticks >= 2:
                break
            await asyncio.sleep(0.01)
        stream.stop()
        hold.set()
        await asyncio.wait_for(task, timeout=2)
        return stream
    stream = _run(go())
    assert conn_holder["url"].startswith("wss://api.upstox.com/v3/") and adapter.requests == [("GET", UpstoxTickStream.AUTHORIZE_URL)]
    sub = json.loads(conn_holder["conn"].sent[0])
    assert sub["method"] == "sub" and sub["data"]["mode"] == "ltpc" and set(sub["data"]["instrumentKeys"]) == {"NSE_EQ|INE002A01018", "NSE_INDEX|Nifty 50"}
    rel = _run(st.tick_cache.get("upstox", "NSE", "RELIANCE"))
    assert rel.ltp == 2500.5 and rel.exchange_ts == NOW                          # ltt (ms) -> exchange timestamp
    nifty = _run(st.tick_cache.get("upstox", "NSE", "NIFTY 50"))
    assert nifty.ltp == 24512.0 and nifty.exchange_ts is None and stream.ticks == 2


class _Creds:
    api_key = "kite-key"


class _KiteAdapter:
    name = "zerodha"
    credentials = _Creds()
    access_token = "tok123"

    async def get_instruments(self, exchange=None):
        return [_Instrument("738561", "RELIANCE", "NSE"), _Instrument("256265", "NIFTY 50", "NSE")]


def test_kite_stream_url_subscription_and_binary_ticks():
    holder = {}

    async def go():
        hold = asyncio.Event()
        conn = _FakeConn([encode_kite([{"token": 738561, "ltp": 2500.55, "exchange_ts": int(NOW.timestamp())}]), b"\x00"], hold=hold)
        holder["conn"] = conn

        async def connector(url):
            holder["url"] = url
            return conn
        stream = KiteTickStream(_KiteAdapter(), cache=st.tick_cache, connector=connector)
        await stream.set_symbols([("RELIANCE", "NSE")])
        task = asyncio.create_task(stream.run())
        for _ in range(200):
            if stream.ticks >= 1:
                break
            await asyncio.sleep(0.01)
        stream.stop()
        hold.set()
        await asyncio.wait_for(task, timeout=2)
    _run(go())
    assert holder["url"] == "wss://ws.kite.trade?api_key=kite-key&access_token=tok123"
    frames = [json.loads(f) for f in holder["conn"].sent]
    assert frames[0] == {"a": "subscribe", "v": [738561]} and frames[1] == {"a": "mode", "v": ["full", [738561]]}
    tick = _run(st.tick_cache.get("zerodha", "NSE", "RELIANCE"))
    assert tick.ltp == 2500.55 and tick.exchange_ts == NOW


def test_stream_for_picks_by_broker_name():
    assert isinstance(stream_for(_UpstoxAdapter()), UpstoxTickStream)
    assert isinstance(stream_for(_KiteAdapter()), KiteTickStream)
    assert stream_for(_FakeBroker()) is not None        # the worker's fake is named "upstox"

    class Shoonya:
        name = "shoonya"
    assert stream_for(Shoonya()) is None


# --- the worker -----------------------------------------------------------------------------------

def test_worker_keeps_a_stream_subscribed_when_enabled(monkeypatch):
    monkeypatch.setattr(config, "STREAMING_QUOTES_ENABLED", True)
    t = _tenant("stream-worker@example.com")
    _deploy(t, symbol="RELIANCE")
    broker = _FakeBroker()
    worker = _worker(monkeypatch, broker)
    made: List[_JsonStream] = []
    hold = {}

    def factory(adapter):
        async def connector(url):
            return _FakeConn([], hold=hold["event"])
        s = _JsonStream(cache=st.tick_cache, connector=connector)
        made.append(s)
        return s
    worker.streams = StreamManager(factory=factory)

    async def go():
        hold["event"] = asyncio.Event()
        report = await worker.run_cycle(OPEN_NOW)
        for _ in range(100):
            if made and made[0].connected:
                break
            await asyncio.sleep(0.01)
        status = worker.streams.status()
        active = worker.streams.active
        # A second cycle reuses the same stream (no second connection) and keeps the symbols.
        await worker.run_cycle(OPEN_NOW + timedelta(minutes=1))
        hold["event"].set()
        await worker.streams.stop_all()
        return report, status, active
    report, status, active = _run(go())
    assert len(made) == 1 and made[0].subscribed == 1 and ("NSE:RELIANCE" in made[0]._wanted)
    assert active == 1 and list(status.values())[0]["connected"] is True and list(status)[0].endswith(":upstox")
    assert worker.streams.status() == {}                       # stopped and dropped


def test_worker_leaves_streaming_off_by_default(monkeypatch):
    assert config.STREAMING_QUOTES_ENABLED is False
    t = _tenant("stream-off@example.com")
    _deploy(t, symbol="RELIANCE")
    worker = _worker(monkeypatch, _FakeBroker())
    report = _run(worker.run_cycle(OPEN_NOW))
    assert report.streams_connected == 0 and worker.streams.status() == {}
