"""Part 1B: the Groww market-data feed for the data hub - built, and OFF behind the TM-6 gate.

`stream_for(adapter)` (app/market_data/stream.py) hands out this stream only while the Groww market-data gate is open
(app/brokers/gates.py: closed until Groww confirms its data terms in writing). With the gate closed no socket token is
asked for and no socket is opened; the adapter's REST price reads refuse as well. TM-27: Groww's per-day caps make REST
polling of prices impossible, so the hub streams them through this feed once the gate opens.

The wire format is the one the official SDK `growwapi` 1.5.0 speaks (groww/feed.py, groww/nats_client.py,
groww/constants.py, groww/proto/*.py, read 23 Oct 2026):

* the feed is a NATS server behind a websocket, `wss://socket-api.groww.in`;
* the client makes an ed25519 key pair (a NATS "user" nkey), sends its public key to
  `POST https://api.groww.in/v1/api/apex/v1/socket/token/create/` (`{"socketKey": "<public nkey>"}`, the day's access
  token as Bearer) and receives a socket JWT (`token`) and a `subscriptionId`;
* NATS protocol: the server's `INFO` carries a nonce; the client answers `CONNECT` with the JWT and the nonce signed by
  its key (`sig`, base64url), then `PING` / `PONG`; `SUB <subject> <sid>` per instrument; prices arrive as
  `MSG <subject> <sid> <bytes>` with a protobuf body; the server's `PING` is answered with `PONG`;
* subjects: `/ld/eq/nse/price.<exchange token>` (cash), `/ld/fo/nse/price.<token>` (F&O), `/ld/indices/nse/price.<token>`
  (indices), the same with `bse`;
* body: `StocksSocketResponseProtoDto` - field 4 `stockLivePrice` (`tsInMillis` = 1, `ltp` = 13, doubles), field 6
  `stocksLiveIndices` (`tsInMillis` = 1, `value` = 2, doubles). ATP decodes these few fields itself (no protobuf or
  NATS library is added).

At most MAX_SUBSCRIPTIONS instruments per connection (1,000: Groww's docs as quoted in search extracts, unverified -
GW-5). Only (key, last price, exchange time) leaves `decode`; the tick goes into the connection's own namespaced cache
(the hub), never a platform-wide one.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import struct
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Tuple

from app.brokers.exceptions import BrokerAuthenticationError
from app.market_data.stream import Connector, StreamConnection, TickStream, _websockets_connect

logger = logging.getLogger(__name__)

FEED_URL = "wss://socket-api.groww.in"
MAX_SUBSCRIPTIONS = 1000
CRLF = b"\r\n"

# --- NATS nkeys (the user key the socket token is issued for) ----------------------------------------------------------
PREFIX_USER = 20 << 3          # "U"
PREFIX_SEED = 18 << 3          # "S"


def _crc16(data: bytes) -> int:
    """CRC-16/XMODEM, as the nkeys encoding uses (little-endian at the end of the key)."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def _b32(raw: bytes) -> str:
    return base64.b32encode(raw).decode().rstrip("=")


def encode_public_key(public: bytes) -> str:
    body = bytes([PREFIX_USER]) + public
    return _b32(body + struct.pack("<H", _crc16(body)))


class NKeyPair:
    """An ed25519 user key: the public part (sent to Groww for the socket token) and the signer of the server nonce.
    Made fresh for every connection; the private part never leaves this process."""

    def __init__(self, seed: Optional[bytes] = None) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        self._key = Ed25519PrivateKey.from_private_bytes(seed if seed is not None else os.urandom(32))
        self.public_raw = self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.public_key = encode_public_key(self.public_raw)

    def sign(self, nonce: bytes) -> str:
        return base64.urlsafe_b64encode(self._key.sign(nonce)).decode().rstrip("=")


# --- the protobuf body (just the fields ATP reads) -------------------------------------------------------------------

def _varint(buf: bytes, i: int) -> Tuple[int, int]:
    shift = result = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def proto_fields(buf: bytes) -> Dict[int, Any]:
    """field number -> last value: doubles (wire type 1) as float, length-delimited as bytes, varints as int."""
    out: Dict[int, Any] = {}
    i = 0
    while i < len(buf):
        tag, i = _varint(buf, i)
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            out[number], i = _varint(buf, i)
        elif wire == 1:
            if i + 8 > len(buf):
                raise ValueError("truncated double")
            out[number] = struct.unpack("<d", buf[i:i + 8])[0]
            i += 8
        elif wire == 2:
            size, i = _varint(buf, i)
            if i + size > len(buf):
                raise ValueError("truncated field")
            out[number] = buf[i:i + size]
            i += size
        elif wire == 5:
            out[number] = struct.unpack("<f", buf[i:i + 4])[0]
            i += 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
    return out


def decode_price(body: bytes) -> Optional[Tuple[float, Optional[datetime]]]:
    """(last price, exchange time) from a `StocksSocketResponseProtoDto` body; None when it carries neither."""
    top = proto_fields(body)
    inner, value_field = None, 0
    if isinstance(top.get(4), bytes):
        inner, value_field = proto_fields(top[4]), 13          # stockLivePrice.ltp
    elif isinstance(top.get(6), bytes):
        inner, value_field = proto_fields(top[6]), 2           # stocksLiveIndices.value
    if inner is None or not isinstance(inner.get(value_field), float):
        return None
    millis = inner.get(1)
    when = datetime.fromtimestamp(float(millis) / 1000.0, tz=timezone.utc) if isinstance(millis, float) and millis > 0 else None
    return float(inner[value_field]), when


def topic_for(segment: str, exchange: str, token: str, *, index: bool = False) -> str:
    venue = (exchange or "NSE").lower()
    if index:
        return f"/ld/indices/{venue}/price.{token}"
    kind = "fo" if (segment or "").upper() == "FNO" else "eq"
    return f"/ld/{kind}/{venue}/price.{token}"


# --- the NATS connection ----------------------------------------------------------------------------------------------

class NatsConnection:
    """NATS text protocol over a websocket: yields (subject, body) per MSG, answers the server's PING, raises on -ERR."""

    def __init__(self, conn: StreamConnection) -> None:
        self._conn = conn
        self._buf = b""
        self._iter: Optional[AsyncIterator[Any]] = None
        self._sids: Dict[str, int] = {}
        self._next_sid = 1

    async def _more(self) -> None:
        if self._iter is None:
            self._iter = self._conn.__aiter__()
        frame = await self._iter.__anext__()
        self._buf += frame if isinstance(frame, (bytes, bytearray)) else str(frame).encode()

    async def _line(self) -> bytes:
        while CRLF not in self._buf:
            await self._more()
        line, self._buf = self._buf.split(CRLF, 1)
        return line

    async def _exact(self, size: int) -> bytes:
        while len(self._buf) < size + 2:
            await self._more()
        body, self._buf = self._buf[:size], self._buf[size + 2:]
        return body

    async def handshake(self, jwt: str, keys: NKeyPair) -> None:
        line = await self._line()
        if not line.startswith(b"INFO "):
            raise BrokerAuthenticationError("Groww feed did not greet as expected")
        info = json.loads(line[5:].decode() or "{}")
        nonce = str(info.get("nonce") or "").encode()
        connect = {"verbose": False, "pedantic": False, "tls_required": False, "jwt": jwt, "sig": keys.sign(nonce),
                   "lang": "python3", "version": "atp", "protocol": 1, "headers": False}
        await self._conn.send(b"CONNECT " + json.dumps(connect, separators=(",", ":")).encode() + CRLF + b"PING" + CRLF)
        while True:
            reply = await self._line()
            if reply == b"PONG":
                return
            if reply.startswith(b"-ERR"):
                raise BrokerAuthenticationError("Groww feed refused the session")
            if reply == b"PING":
                await self._conn.send(b"PONG" + CRLF)

    def sid_for(self, subject: str) -> int:
        sid = self._sids.get(subject)
        if sid is None:
            sid = self._sids[subject] = self._next_sid
            self._next_sid += 1
        return sid

    async def send(self, data: Any) -> None:
        await self._conn.send(data)

    def __aiter__(self) -> "NatsConnection":
        return self

    async def __anext__(self) -> Tuple[str, bytes]:
        while True:
            try:
                line = await self._line()
            except StopAsyncIteration:
                raise
            if line.startswith(b"MSG "):
                parts = line.decode().split(" ")
                size = int(parts[-1])
                return parts[1], await self._exact(size)
            if line == b"PING":
                await self._conn.send(b"PONG" + CRLF)
            elif line.startswith(b"-ERR"):
                text = line.decode(errors="replace")
                if "authorization" in text.lower() or "expired" in text.lower():
                    raise BrokerAuthenticationError("Groww feed refused the session (token)")
                raise RuntimeError(f"Groww feed error: {text[:120]}")

    async def close(self) -> None:
        await self._conn.close()


class GrowwTickStream(TickStream):
    name = "groww"

    def __init__(self, adapter: Any, *, connector: Optional[Connector] = None, http_client: Any = None, **kw: Any) -> None:
        self._raw_connector: Connector = connector or _websockets_connect
        super().__init__(connector=self._connect_and_auth, **kw)
        self.adapter = adapter
        self._http = http_client
        self._nats: Optional[NatsConnection] = None

    async def connect_url(self) -> str:
        if not getattr(self.adapter, "access_token", None):
            raise BrokerAuthenticationError("Groww feed needs a logged-in session (token)")
        return FEED_URL

    async def socket_token(self, keys: NKeyPair) -> str:
        """The day's socket JWT for this key pair (the SDK's `generate_socket_token`)."""
        from app.brokers.groww import SOCKET_TOKEN_URL, GrowwBroker
        client = self._http or getattr(self.adapter, "_client", None)
        if client is None:
            raise BrokerAuthenticationError("Groww feed has no connection to ask for its token")
        headers = GrowwBroker._headers(str(self.adapter.access_token))
        response = await client.post(SOCKET_TOKEN_URL, headers=headers, json={"socketKey": keys.public_key})
        if response.status_code in (401, 403):
            raise BrokerAuthenticationError("Groww refused the session (token) for the feed")
        body = response.json()
        payload = body.get("payload", body) if isinstance(body, dict) else {}
        token = payload.get("token") if isinstance(payload, dict) else None
        if not token:
            raise BrokerAuthenticationError("Groww gave no feed token")
        return str(token)

    async def _connect_and_auth(self, url: str) -> StreamConnection:
        keys = NKeyPair()
        jwt = await self.socket_token(keys)
        conn = await self._raw_connector(url)
        nats = NatsConnection(conn)
        try:
            await nats.handshake(jwt, keys)
        except Exception:
            await conn.close()
            raise
        self._nats = nats
        return nats  # type: ignore[return-value]

    async def wire_key(self, symbol: str, exchange: str) -> Optional[str]:
        g_exchange, segment, _symbol, token = await self.adapter.resolve(symbol, exchange)
        from app.brokers.groww import INDEX_NAMES
        index = (symbol or "").strip().upper() in INDEX_NAMES or (symbol or "").strip().upper() in INDEX_NAMES.values()
        return topic_for(segment, g_exchange, token, index=index)

    async def set_symbols(self, symbols: Iterable[Tuple[str, str]]) -> None:
        wanted = list(symbols)
        if len(wanted) > MAX_SUBSCRIPTIONS:
            logger.warning("groww stream: %d symbols asked, the first %d kept (feed limit)", len(wanted), MAX_SUBSCRIPTIONS)
            wanted = wanted[:MAX_SUBSCRIPTIONS]
        await super().set_symbols(wanted)

    def _sid(self, subject: str) -> int:
        if self._nats is not None:
            return self._nats.sid_for(subject)
        return abs(hash(subject)) % 1_000_000 + 1

    def subscribe_frames(self, keys: Iterable[str]) -> List[Any]:
        return [f"SUB {k} {self._sid(k)}\r\n".encode() for k in keys]

    def unsubscribe_frames(self, keys: Iterable[str]) -> List[Any]:
        return [f"UNSUB {self._sid(k)}\r\n".encode() for k in keys]

    def decode(self, frame: Any) -> List[Tuple[str, float, Optional[datetime]]]:
        if not (isinstance(frame, tuple) and len(frame) == 2 and isinstance(frame[1], (bytes, bytearray))):
            return []
        subject, body = frame
        found = decode_price(bytes(body))
        if found is None:
            return []
        return [(str(subject), found[0], found[1])]
