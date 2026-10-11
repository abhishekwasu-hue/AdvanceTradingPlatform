"""Phase S: streaming quotes (master prompt sections 8 and 10).

Until this phase every price the worker used came from the broker's REST endpoints, once per
symbol per cycle, through the rate budget. Brokers also publish a websocket feed, and for a
one-minute strategy watching a stop that is where the price should come from: the exchange's
last trade arrives within a second instead of being polled once a minute, and each tick costs
nothing against the REST budget.

Design:

* `TickCache` is the single hand-off point. Streams write ticks into it; `MarketDataService.
  get_ltp` reads a symbol's newest tick and uses it when it is younger than
  `TICK_MAX_AGE_SECONDS` (default = the Phase G1 quote limit), otherwise falls back to the REST
  quote exactly as before. In-process first (the worker is one process), mirrored to Redis with
  a short TTL so the API process can show the same last price. Redis is optional here as
  everywhere else: the cache fails open to REST.
* `TickStream` owns one websocket: connect, subscribe, decode frames into ticks, reconnect with
  backoff on any error, honour subscription changes between cycles. The wire protocol lives in
  the broker subclass; the connection object is injectable so the loop is tested against a fake
  socket. The decoders are pure functions on bytes and are tested against hand-built frames.
* `UpstoxTickStream` speaks Upstox Market Data Feed V3: an authorised wss URL from the REST API,
  a JSON subscription frame, protobuf-encoded `FeedResponse` frames. The few fields we need
  (LTPC: ltp, last trade time, close) are read with a minimal protobuf wire-format reader, so no
  generated `_pb2` module or `protobuf` dependency is required.
* `KiteTickStream` speaks Kite Connect's binary ticker: big-endian packets, LTP in paise (or
  1e7 for currency derivatives), the exchange timestamp in the full packet.

Nothing here changes behaviour until `STREAMING_QUOTES_ENABLED=true`: the worker then keeps one
stream per (tenant, broker session) subscribed to the symbols its deployments and open positions
need. The staleness gate is unchanged - a tick, like a REST quote, is refused when its exchange
time is older than the limit - so a silent feed degrades to REST polling, never to a stale price.
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Iterable, List, Optional, Set, Tuple

from app.cache.client import cache_get, cache_set
from app.core import config
from app.observability.metrics import STREAM_RECONNECTS, TICKS_RECEIVED

logger = logging.getLogger(__name__)

TICK_REDIS_TTL_SECONDS = 180
BACKOFF_SECONDS = (1, 2, 5, 10, 30)


# --- ticks and the cache -------------------------------------------------------------------------

@dataclass(frozen=True)
class Tick:
    broker: str
    exchange: str
    symbol: str
    ltp: float
    exchange_ts: Optional[datetime]   # the exchange's last-trade time when the feed carries one
    received_at: datetime

    @property
    def best_ts(self) -> datetime:
        return self.exchange_ts or self.received_at

    def age_seconds(self, now: Optional[datetime] = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now - self.best_ts).total_seconds()

    def to_json(self) -> str:
        return json.dumps({"broker": self.broker, "exchange": self.exchange, "symbol": self.symbol, "ltp": self.ltp,
                           "exchange_ts": self.exchange_ts.isoformat() if self.exchange_ts else None,
                           "received_at": self.received_at.isoformat()})

    @classmethod
    def from_json(cls, raw: str) -> "Tick":
        d = json.loads(raw)
        return cls(broker=d["broker"], exchange=d["exchange"], symbol=d["symbol"], ltp=float(d["ltp"]),
                   exchange_ts=datetime.fromisoformat(d["exchange_ts"]) if d.get("exchange_ts") else None,
                   received_at=datetime.fromisoformat(d["received_at"]))


def _tick_key(broker: str, exchange: str, symbol: str) -> str:
    return f"md:tick:{broker}:{exchange.upper()}:{symbol.upper()}"


class TickCache:
    """Newest tick per (broker, exchange, symbol). Process-local dict plus a Redis mirror."""

    def __init__(self) -> None:
        self._local: Dict[str, Tick] = {}
        self.received = 0

    async def put(self, tick: Tick) -> None:
        key = _tick_key(tick.broker, tick.exchange, tick.symbol)
        self._local[key] = tick
        self.received += 1
        await cache_set(key, tick.to_json(), TICK_REDIS_TTL_SECONDS)

    async def get(self, broker: str, exchange: str, symbol: str) -> Optional[Tick]:
        key = _tick_key(broker, exchange, symbol)
        tick = self._local.get(key)
        if tick is not None:
            return tick
        raw = await cache_get(key)
        if raw:
            try:
                return Tick.from_json(raw)
            except (ValueError, KeyError, TypeError):
                return None
        return None

    async def fresh(self, broker: str, exchange: str, symbol: str, now: Optional[datetime] = None,
                    max_age: Optional[int] = None) -> Optional[Tick]:
        """The newest tick when it is young enough to act on, else None (caller polls REST)."""
        tick = await self.get(broker, exchange, symbol)
        if tick is None:
            return None
        limit = config.TICK_MAX_AGE_SECONDS if max_age is None else max_age
        if limit <= 0 or tick.age_seconds(now) <= limit:
            return tick
        return None

    def clear(self) -> None:
        self._local.clear()


tick_cache = TickCache()

# Part B2: synchronous listeners told about every cached tick (the lake recorder when LAKE_TICK_WRITER_ENABLED).
tick_listeners: List[Callable[["Tick"], None]] = []


# --- protobuf wire-format reader (Upstox Market Data Feed V3) -------------------------------------

def _read_varint(buf: bytes, pos: int) -> Tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")


def protobuf_fields(buf: bytes) -> List[Tuple[int, int, Any]]:
    """Every (field number, wire type, value) in one message. Length-delimited values are
    returned as bytes; varints as int; fixed64 as the raw 8 bytes; fixed32 as the raw 4."""
    out: List[Tuple[int, int, Any]] = []
    pos = 0
    while pos < len(buf):
        tag, pos = _read_varint(buf, pos)
        number, wtype = tag >> 3, tag & 0x7
        if wtype == 0:
            value, pos = _read_varint(buf, pos)
        elif wtype == 1:
            value, pos = buf[pos:pos + 8], pos + 8
        elif wtype == 2:
            length, pos = _read_varint(buf, pos)
            value, pos = buf[pos:pos + length], pos + length
        elif wtype == 5:
            value, pos = buf[pos:pos + 4], pos + 4
        else:
            raise ValueError(f"unsupported wire type {wtype}")
        out.append((number, wtype, value))
    return out


def _double(raw: bytes) -> float:
    return struct.unpack("<d", raw)[0]


def _signed64(v: int) -> int:
    return v - (1 << 64) if v >= (1 << 63) else v


def _ltpc(buf: bytes) -> Dict[str, Any]:
    """LTPC { double ltp = 1; int64 ltt = 2; int64 ltq = 3; double cp = 4; }"""
    out: Dict[str, Any] = {}
    for number, wtype, value in protobuf_fields(buf):
        if number == 1 and wtype == 1:
            out["ltp"] = _double(value)
        elif number == 2 and wtype == 0:
            out["ltt"] = _signed64(value)
        elif number == 3 and wtype == 0:
            out["ltq"] = _signed64(value)
        elif number == 4 and wtype == 1:
            out["cp"] = _double(value)
    return out


def _feed_ltpc(buf: bytes) -> Optional[Dict[str, Any]]:
    """Feed { oneof: LTPC ltpc = 1; FullFeed fullFeed = 2; FirstLevelWithGreeks firstLevelWithGreeks = 3; }
    FullFeed { oneof: MarketFullFeed marketFF = 1; IndexFullFeed indexFF = 2; } - both start with LTPC ltpc = 1.
    FirstLevelWithGreeks { LTPC ltpc = 1; ... }"""
    for number, wtype, value in protobuf_fields(buf):
        if wtype != 2:
            continue
        if number == 1:
            return _ltpc(value)
        if number == 2:
            for n2, w2, v2 in protobuf_fields(value):
                if w2 == 2 and n2 in (1, 2):
                    for n3, w3, v3 in protobuf_fields(v2):
                        if n3 == 1 and w3 == 2:
                            return _ltpc(v3)
        if number == 3:
            for n2, w2, v2 in protobuf_fields(value):
                if n2 == 1 and w2 == 2:
                    return _ltpc(v2)
    return None


def decode_upstox_v3(payload: bytes) -> Dict[str, Dict[str, Any]]:
    """`FeedResponse { Type type = 1; map<string, Feed> feeds = 2; int64 currentTs = 3; ... }`
    -> {instrument_key: {"ltp", "ltt", "ltq", "cp"}} for every feed carrying an LTPC. Frames
    without price data (market_info, the initial snapshot type) decode to an empty dict."""
    result: Dict[str, Dict[str, Any]] = {}
    for number, wtype, value in protobuf_fields(payload):
        if number != 2 or wtype != 2:
            continue
        key: Optional[str] = None
        feed: Optional[bytes] = None
        for n2, w2, v2 in protobuf_fields(value):
            if n2 == 1 and w2 == 2:
                key = v2.decode("utf-8", "replace")
            elif n2 == 2 and w2 == 2:
                feed = v2
        if key is None or feed is None:
            continue
        ltpc = _feed_ltpc(feed)
        if ltpc and ltpc.get("ltp"):
            result[key] = ltpc
    return result


def _varint(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _pb_field(number: int, wtype: int, payload: bytes) -> bytes:
    head = _varint((number << 3) | wtype)
    if wtype == 2:
        return head + _varint(len(payload)) + payload
    return head + payload


def encode_upstox_v3(feeds: Dict[str, Dict[str, Any]], *, current_ts_ms: Optional[int] = None, full: bool = False) -> bytes:
    """The inverse of `decode_upstox_v3` for tests and fixtures: builds a FeedResponse with an
    LTPC per instrument key (wrapped in a FullFeed/MarketFullFeed when `full`)."""
    body = bytearray(_pb_field(1, 0, _varint(1)))   # type = live_feed
    for key, ltpc in feeds.items():
        inner = bytearray()
        if "ltp" in ltpc:
            inner += _pb_field(1, 1, struct.pack("<d", float(ltpc["ltp"])))
        if "ltt" in ltpc:
            inner += _pb_field(2, 0, _varint(int(ltpc["ltt"]) & ((1 << 64) - 1)))
        if "ltq" in ltpc:
            inner += _pb_field(3, 0, _varint(int(ltpc["ltq"])))
        if "cp" in ltpc:
            inner += _pb_field(4, 1, struct.pack("<d", float(ltpc["cp"])))
        if full:
            feed = _pb_field(2, 2, _pb_field(1, 2, _pb_field(1, 2, bytes(inner))))
        else:
            feed = _pb_field(1, 2, bytes(inner))
        entry = _pb_field(1, 2, key.encode()) + _pb_field(2, 2, feed)
        body += _pb_field(2, 2, entry)
    if current_ts_ms is not None:
        body += _pb_field(3, 0, _varint(int(current_ts_ms)))
    return bytes(body)


# --- Kite Connect binary ticker ------------------------------------------------------------------

def _kite_divisor(token: int) -> float:
    segment = token & 0xFF
    if segment == 3:      # CDS
        return 10_000_000.0
    if segment == 6:      # BCD
        return 10_000.0
    return 100.0


def decode_kite(payload: bytes) -> List[Dict[str, Any]]:
    """Kite's binary frame: int16 packet count, then per packet int16 length + packet. Every mode
    starts with int32 instrument token and int32 last price (paise). The 184-byte full packet
    carries the last-trade time at [44:48] and the exchange timestamp at [60:64] (epoch seconds).
    A text/heartbeat frame (length < 2, or a single byte) decodes to []."""
    if len(payload) < 4:
        return []
    count = struct.unpack_from(">H", payload, 0)[0]
    pos = 2
    out: List[Dict[str, Any]] = []
    for _ in range(count):
        if pos + 2 > len(payload):
            break
        length = struct.unpack_from(">H", payload, pos)[0]
        pos += 2
        packet = payload[pos:pos + length]
        pos += length
        if len(packet) < 8:
            continue
        token, ltp_raw = struct.unpack_from(">ii", packet, 0)
        entry: Dict[str, Any] = {"token": token, "ltp": ltp_raw / _kite_divisor(token)}
        if len(packet) >= 184:
            ltt, ets = struct.unpack_from(">i", packet, 44)[0], struct.unpack_from(">i", packet, 60)[0]
            entry["ltt"] = ltt or None
            entry["exchange_ts"] = ets or None
        out.append(entry)
    return out


def encode_kite(packets: List[Dict[str, Any]]) -> bytes:
    """Test helper: builds LTP (8-byte) or full (184-byte) packets."""
    body = bytearray(struct.pack(">H", len(packets)))
    for p in packets:
        token = int(p["token"])
        raw = int(round(float(p["ltp"]) * _kite_divisor(token)))
        if "exchange_ts" in p or "ltt" in p:
            packet = bytearray(184)
            struct.pack_into(">ii", packet, 0, token, raw)
            struct.pack_into(">i", packet, 44, int(p.get("ltt") or 0))
            struct.pack_into(">i", packet, 60, int(p.get("exchange_ts") or 0))
        else:
            packet = bytearray(struct.pack(">ii", token, raw))
        body += struct.pack(">H", len(packet)) + packet
    return bytes(body)


# --- the stream loop ------------------------------------------------------------------------------

class StreamConnection:
    """What a stream needs from a websocket: send bytes/text, iterate incoming frames, close.
    `websockets` connections satisfy this; tests pass a fake."""

    async def send(self, data: Any) -> None: ...  # pragma: no cover - protocol

    def __aiter__(self) -> AsyncIterator[Any]: ...  # pragma: no cover - protocol

    async def close(self) -> None: ...  # pragma: no cover - protocol


Connector = Callable[[str], Awaitable[StreamConnection]]


async def _websockets_connect(url: str) -> StreamConnection:
    from websockets.asyncio.client import connect
    return await connect(url, max_size=2 ** 22, ping_interval=20, ping_timeout=20)


@dataclass
class StreamStatus:
    broker: str
    connected: bool
    subscribed: int
    ticks: int
    reconnects: int
    last_tick_at: Optional[datetime]
    last_error: Optional[str]

    def as_dict(self) -> dict:
        return {"broker": self.broker, "connected": self.connected, "subscribed": self.subscribed, "ticks": self.ticks,
                "reconnects": self.reconnects, "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
                "last_error": self.last_error}


class TickStream:
    """One websocket to one broker session. Subclasses implement the protocol; this class runs it."""

    name = "generic"

    def __init__(self, *, cache: Optional[TickCache] = None, connector: Optional[Connector] = None) -> None:
        self.cache = cache or tick_cache
        self.connector = connector or _websockets_connect
        self._wanted: Dict[str, Tuple[str, str]] = {}     # wire key -> (symbol, exchange)
        self._pending: Set[str] = set()                     # wire keys not yet sent to this connection
        self._pending_unsub: Set[str] = set()
        self._connection: Optional[StreamConnection] = None
        self._stop = asyncio.Event()
        self.connected = False
        self.ticks = 0
        self.reconnects = 0
        self.last_tick_at: Optional[datetime] = None
        self.last_error: Optional[str] = None

    # --- protocol hooks (subclass) --
    async def connect_url(self) -> str:
        raise NotImplementedError

    async def wire_key(self, symbol: str, exchange: str) -> Optional[str]:
        """The identifier this feed subscribes by (Upstox instrument key, Kite token)."""
        raise NotImplementedError

    def subscribe_frames(self, keys: Iterable[str]) -> List[Any]:
        raise NotImplementedError

    def unsubscribe_frames(self, keys: Iterable[str]) -> List[Any]:
        return []

    def decode(self, frame: Any) -> List[Tuple[str, float, Optional[datetime]]]:
        """(wire key, ltp, exchange timestamp) per price update in one frame."""
        raise NotImplementedError

    # --- subscriptions --
    async def set_symbols(self, symbols: Iterable[Tuple[str, str]]) -> None:
        """Make the subscription exactly `symbols` ((symbol, exchange) pairs). Additions and
        removals are sent on the live connection, or on the next one."""
        wanted: Dict[str, Tuple[str, str]] = {}
        for symbol, exchange in symbols:
            try:
                key = await self.wire_key(symbol, exchange)
            except Exception as exc:  # noqa: BLE001 - an unresolvable symbol is polled, not fatal
                logger.warning("%s stream: cannot resolve %s:%s (%s) - REST fallback", self.name, exchange, symbol, exc)
                continue
            if key:
                wanted[key] = (symbol, exchange)
        added = set(wanted) - set(self._wanted)
        removed = set(self._wanted) - set(wanted)
        self._wanted = wanted
        self._pending |= added
        self._pending -= removed
        self._pending_unsub |= removed
        self._pending_unsub -= added
        await self._flush()

    async def _flush(self) -> None:
        if self._connection is None or not self.connected:
            return
        try:
            if self._pending_unsub:
                for frame in self.unsubscribe_frames(sorted(self._pending_unsub)):
                    await self._connection.send(frame)
                self._pending_unsub.clear()
            if self._pending:
                for frame in self.subscribe_frames(sorted(self._pending)):
                    await self._connection.send(frame)
                self._pending.clear()
        except Exception as exc:  # noqa: BLE001 - the run loop reconnects and resubscribes
            self.last_error = f"send failed: {exc}"
            self.connected = False

    @property
    def subscribed(self) -> int:
        return len(self._wanted)

    # --- lifecycle --
    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Connect, subscribe, read until the socket drops, back off, repeat, until `stop()`."""
        attempt = 0
        while not self._stop.is_set():
            try:
                url = await self.connect_url()
                self._connection = await self.connector(url)
                self.connected = True
                self.last_error = None
                attempt = 0
                self._pending = set(self._wanted)      # a fresh socket knows nothing: resend everything
                self._pending_unsub.clear()
                await self._flush()
                async for frame in self._connection:
                    if self._stop.is_set():
                        break
                    await self._handle(frame)
                    if self._pending or self._pending_unsub:
                        await self._flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - every failure is a reconnect
                self.last_error = str(exc) or exc.__class__.__name__
                logger.warning("%s stream error: %s", self.name, self.last_error)
            finally:
                self.connected = False
                if self._connection is not None:
                    try:
                        await self._connection.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._connection = None
            if self._stop.is_set():
                break
            self.reconnects += 1
            STREAM_RECONNECTS.labels(broker=self.name).inc()
            delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
            attempt += 1
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _handle(self, frame: Any) -> None:
        try:
            updates = self.decode(frame)
        except Exception as exc:  # noqa: BLE001 - one bad frame is logged, the stream continues
            logger.debug("%s stream: undecodable frame (%s)", self.name, exc)
            return
        now = datetime.now(timezone.utc)
        for key, ltp, exchange_ts in updates:
            target = self._wanted.get(key)
            if target is None or ltp is None or ltp <= 0:
                continue
            symbol, exchange = target
            tick = Tick(broker=self.name, exchange=exchange, symbol=symbol, ltp=float(ltp), exchange_ts=exchange_ts, received_at=now)
            await self.cache.put(tick)
            for listener in tick_listeners:
                listener(tick)
            self.ticks += 1
            self.last_tick_at = now
            TICKS_RECEIVED.labels(broker=self.name).inc()

    def status(self) -> StreamStatus:
        return StreamStatus(broker=self.name, connected=self.connected, subscribed=self.subscribed, ticks=self.ticks,
                            reconnects=self.reconnects, last_tick_at=self.last_tick_at, last_error=self.last_error)


def _epoch_to_dt(value: Optional[int], *, millis: bool) -> Optional[datetime]:
    if not value:
        return None
    seconds = value / 1000.0 if millis else float(value)
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


class UpstoxTickStream(TickStream):
    """Upstox Market Data Feed V3 (wss, protobuf). The authorised URL comes from
    `GET /v3/feed/market-data-feed/authorize` with the session's bearer token."""

    name = "upstox"
    AUTHORIZE_URL = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
    MODE = "ltpc"

    def __init__(self, adapter, **kw) -> None:
        super().__init__(**kw)
        self.adapter = adapter

    async def connect_url(self) -> str:
        data = await self.adapter._request("GET", self.AUTHORIZE_URL)
        url = data.get("authorized_redirect_uri") or data.get("authorizedRedirectUri")
        if not url:
            raise RuntimeError("Upstox did not return an authorized feed URL")
        return url

    async def wire_key(self, symbol: str, exchange: str) -> Optional[str]:
        instrument = await self.adapter._resolve_instrument(symbol, exchange)
        return instrument.instrument_token

    def subscribe_frames(self, keys: Iterable[str]) -> List[Any]:
        return [json.dumps({"guid": uuid.uuid4().hex[:16], "method": "sub",
                            "data": {"mode": self.MODE, "instrumentKeys": list(keys)}}).encode()]

    def unsubscribe_frames(self, keys: Iterable[str]) -> List[Any]:
        return [json.dumps({"guid": uuid.uuid4().hex[:16], "method": "unsub",
                            "data": {"instrumentKeys": list(keys)}}).encode()]

    def decode(self, frame: Any) -> List[Tuple[str, float, Optional[datetime]]]:
        if not isinstance(frame, (bytes, bytearray)):
            return []
        out = []
        for key, ltpc in decode_upstox_v3(bytes(frame)).items():
            out.append((key, float(ltpc["ltp"]), _epoch_to_dt(ltpc.get("ltt"), millis=True)))
        return out


class KiteTickStream(TickStream):
    """Kite Connect ticker: `wss://ws.kite.trade?api_key=..&access_token=..`, JSON control
    frames, binary quote frames. Subscribes in `full` mode so the exchange timestamp is present
    for the staleness gate."""

    name = "zerodha"
    URL = "wss://ws.kite.trade"
    MODE = "full"

    def __init__(self, adapter, **kw) -> None:
        super().__init__(**kw)
        self.adapter = adapter
        self._tokens: Dict[str, int] = {}

    async def connect_url(self) -> str:
        token = getattr(self.adapter, "access_token", None)
        api_key = getattr(getattr(self.adapter, "credentials", None), "api_key", None)
        if not token or not api_key:
            raise RuntimeError("Kite ticker needs api_key and an access token")
        return f"{self.URL}?api_key={api_key}&access_token={token}"

    async def wire_key(self, symbol: str, exchange: str) -> Optional[str]:
        instruments = await self.adapter.get_instruments(exchange)
        match = next((i for i in instruments if i.tradingsymbol == symbol and i.exchange == exchange), None)
        if match is None:
            match = next((i for i in instruments if i.tradingsymbol == symbol), None)
        if match is None:
            raise RuntimeError(f"{exchange}:{symbol} not in the Kite instrument dump")
        return str(int(match.instrument_token))

    def subscribe_frames(self, keys: Iterable[str]) -> List[Any]:
        tokens = [int(k) for k in keys]
        return [json.dumps({"a": "subscribe", "v": tokens}), json.dumps({"a": "mode", "v": [self.MODE, tokens]})]

    def unsubscribe_frames(self, keys: Iterable[str]) -> List[Any]:
        return [json.dumps({"a": "unsubscribe", "v": [int(k) for k in keys]})]

    def decode(self, frame: Any) -> List[Tuple[str, float, Optional[datetime]]]:
        if not isinstance(frame, (bytes, bytearray)):
            return []
        out = []
        for p in decode_kite(bytes(frame)):
            ts = _epoch_to_dt(p.get("exchange_ts") or p.get("ltt"), millis=False)
            out.append((str(p["token"]), float(p["ltp"]), ts))
        return out


def stream_for(adapter) -> Optional[TickStream]:
    """The streaming implementation for a broker session, or None when the broker has none
    (the worker then polls REST as before)."""
    name = getattr(adapter, "name", "")
    if name == "upstox":
        return UpstoxTickStream(adapter)
    if name == "zerodha":
        return KiteTickStream(adapter)
    return None


class StreamManager:
    """The worker's streams: one task per broker session key, resubscribed every cycle."""

    def __init__(self, factory: Callable[[Any], Optional[TickStream]] = stream_for) -> None:
        self.factory = factory
        self._streams: Dict[str, TickStream] = {}
        self._tasks: Dict[str, asyncio.Task] = {}

    async def ensure(self, key: str, adapter, symbols: Iterable[Tuple[str, str]]) -> Optional[TickStream]:
        stream = self._streams.get(key)
        task = self._tasks.get(key)
        if stream is None or task is None or task.done():
            stream = self.factory(adapter)
            if stream is None:
                return None
            self._streams[key] = stream
            self._tasks[key] = asyncio.create_task(stream.run(), name=f"tick-stream:{key}")
        await stream.set_symbols(symbols)
        return stream

    async def drop(self, key: str) -> None:
        stream = self._streams.pop(key, None)
        task = self._tasks.pop(key, None)
        if stream is not None:
            stream.stop()
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def stop_all(self) -> None:
        for key in list(self._streams):
            await self.drop(key)

    def status(self) -> Dict[str, dict]:
        return {key: stream.status().as_dict() for key, stream in self._streams.items()}

    @property
    def active(self) -> int:
        return sum(1 for s in self._streams.values() if s.connected)
