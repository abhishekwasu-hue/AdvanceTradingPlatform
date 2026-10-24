"""Part 1B: the Kotak Neo market-data feed (SFeed `native_batch`) for the data hub - built, and OFF behind the TM-6 gate.

`stream_for(adapter)` (app/market_data/stream.py) hands out this stream only while the Kotak Neo market-data gate is
open (app/brokers/gates.py: closed until Kotak Neo confirms its data terms in writing). With the gate closed no socket is
ever opened; the hub reports the connection as "polling" and the adapter's REST reads refuse as well.

The wire format is the one the official SDK `kotakneoapi` 3.0.9 speaks (websocket/feed/client.py and protocol.py, read
23 Oct 2026):

* connect to the feed URL (the SDK's production default `wss://sfeed.kotaksecurities.com/apifeed`; the SDK may also
  learn a data-centre URL from Kotak's config service, which ATP does not call yet - docs/design/BROKER_KOTAK_NEO.md);
* send one JSON auth frame (`user` = the trade session id, `auth` = the trade token, `format` = `native_batch`); the
  answer is a JSON text frame with `message_code` 1117 or 1119 and a price `divider` per exchange;
* subscribe with JSON control frames `{"event": "subscribeScripsLite" | "subscribeIndices", "inputtoken":
  "nse_cm|11536,nse_cm|1594"}` (index subscriptions use the index name in place of a token);
* data frames are binary, little-endian and batched: each packet starts with a `uint16` length and a 9-byte header
  (length, message code, exchange id, level, auction flag, sequence, bitmask length). Level 1 is the "mini touch line"
  (token, last trade time, last price, ...); levels 2/4/8/16 carry the market picture; message code 7207 is an index.

Prices are integers divided by the exchange's divider. Only (key, last price, last trade time) leaves `decode`; the
tick goes into the connection's own namespaced cache (the hub), never a platform-wide one.
"""
from __future__ import annotations

import json
import struct
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Tuple

from app.brokers.exceptions import BrokerAuthenticationError
from app.market_data.stream import Connector, StreamConnection, TickStream, _websockets_connect

FEED_URL = "wss://sfeed.kotaksecurities.com/apifeed"
AUTH_RESPONSE_CODES = (1117, 1119)
MSG_INDEX = 7207
LEVEL_MINI = 1
LEVEL_PICTURE = (2, 4, 8, 16)
HEADER = struct.Struct("<HHbBBBB")
HEADER_SIZE = 9
MINI_BODY = struct.Struct("<IqIqIiiIBI")
PICTURE_BODY = struct.Struct("<IqqqqqIIIIIqIIIIhiIdiIIIIIBI")
INDEX_BODY = struct.Struct("<IiiiiiQiiidBi21s")
DEFAULT_DIVIDER = 100
EXCHANGE_IDS = {1: "nse_cm", 2: "nse_fo", 3: "cde_fo", 4: "nse_com", 5: "bse_cm", 6: "bse_fo", 7: "bse_cd", 8: "bse_co",
                9: "mcx_fo", 10: "ncd_co"}
INDEX_PREFIX = "idx:"
SOURCE = "NEOTRADEAPI"


def auth_frame(sid: str, token: str, now_ms: Optional[int] = None) -> str:
    """The native_batch auth frame (SDK 3.0.9 `_build_auth_frame`; `platform` / `version` / `sdk_*` as the SDK sends)."""
    return json.dumps({"user": sid, "auth": token, "format": "native_batch", "source": SOURCE, "platform": "Web",
                       "version": "1.2.3", "sdk_version": 2, "sdk_date": "2026-08-07T09:41:17.667Z",
                       "conn_req_time": int(now_ms if now_ms is not None else time.time() * 1000), "sessionValidation": False})


def dividers_from(answer: Dict[str, Any]) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for name, info in (answer.get("exchanges") or {}).items():
        if not isinstance(info, dict):
            continue
        exch_id = info.get("value")
        if exch_id is None:
            exch_id = next((k for k, v in EXCHANGE_IDS.items() if v == name), None)
        if exch_id is not None:
            try:
                out[int(exch_id)] = int(info.get("divider") or DEFAULT_DIVIDER)
            except (TypeError, ValueError):
                continue
    return out


def split_batch(frame: bytes) -> List[bytes]:
    packets: List[bytes] = []
    offset = 0
    while offset + 2 <= len(frame):
        size = struct.unpack_from("<H", frame, offset)[0]
        if size < HEADER_SIZE or offset + size > len(frame):
            break
        packets.append(frame[offset:offset + size])
        offset += size
    return packets


def _when(value: int) -> Optional[datetime]:
    if not value or value <= 0:
        return None
    seconds = value / 1000.0 if value > 100_000_000_000 else float(value)
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def decode_packet(packet: bytes, dividers: Dict[int, int]) -> Optional[Tuple[str, float, Optional[datetime]]]:
    """(wire key, last price, last trade time) for a price packet; None for anything else."""
    if len(packet) < HEADER_SIZE:
        return None
    _length, code, exchange_id, level, _auction, _seq, _bits = HEADER.unpack_from(packet, 0)
    segment = EXCHANGE_IDS.get(exchange_id, str(exchange_id))
    divider = dividers.get(exchange_id) or DEFAULT_DIVIDER
    if code == MSG_INDEX:
        if len(packet) < HEADER_SIZE + INDEX_BODY.size:
            return None
        fields = INDEX_BODY.unpack_from(packet, HEADER_SIZE)
        name = fields[13].split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
        return f"{INDEX_PREFIX}{segment}|{name}", fields[5] / divider, _when(int(fields[6]))
    if level == LEVEL_MINI:
        if len(packet) < HEADER_SIZE + MINI_BODY.size:
            return None
        token, ltt, ltp = MINI_BODY.unpack_from(packet, HEADER_SIZE)[:3]
        return f"{segment}|{token}", ltp / divider, _when(int(ltt))
    if level in LEVEL_PICTURE:
        if len(packet) < HEADER_SIZE + PICTURE_BODY.size:
            return None
        fields = PICTURE_BODY.unpack_from(packet, HEADER_SIZE)
        return f"{segment}|{fields[0]}", fields[10] / divider, _when(int(fields[4]))
    return None


class _AuthedConnection:
    """A feed connection after a successful auth: frames are read from the same iterator the auth answer came from."""

    def __init__(self, conn: StreamConnection, iterator: AsyncIterator[Any]) -> None:
        self._conn = conn
        self._iterator = iterator

    async def send(self, data: Any) -> None:
        await self._conn.send(data)

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iterator

    async def close(self) -> None:
        await self._conn.close()


class KotakNeoTickStream(TickStream):
    name = "kotak_neo"

    def __init__(self, adapter: Any, *, connector: Optional[Connector] = None, **kw: Any) -> None:
        self._raw_connector: Connector = connector or _websockets_connect
        super().__init__(connector=self._connect_and_auth, **kw)
        self.adapter = adapter
        self.dividers: Dict[int, int] = {}

    def _session(self) -> Dict[str, str]:
        from app.brokers.kotak_neo import decode_session
        found = decode_session(getattr(self.adapter, "access_token", None))
        if found is None:
            raise BrokerAuthenticationError("Kotak Neo feed needs a logged-in session")
        return found

    async def connect_url(self) -> str:
        self._session()
        return FEED_URL

    async def _connect_and_auth(self, url: str) -> StreamConnection:
        session = self._session()
        conn = await self._raw_connector(url)
        iterator = conn.__aiter__()
        await conn.send(auth_frame(session["s"], session["t"]))
        answer: Optional[Dict[str, Any]] = None
        for _ in range(10):
            try:
                frame = await iterator.__anext__()
            except StopAsyncIteration:
                break
            if isinstance(frame, str):
                try:
                    answer = json.loads(frame)
                except ValueError:
                    answer = None
                break
        if not isinstance(answer, dict) or answer.get("message_code") not in AUTH_RESPONSE_CODES or answer.get("format") == "native_fallback":
            await conn.close()
            raise BrokerAuthenticationError("Kotak Neo feed refused the session")
        self.dividers = dividers_from(answer)
        return _AuthedConnection(conn, iterator)  # type: ignore[return-value]

    async def wire_key(self, symbol: str, exchange: str) -> Optional[str]:
        from app.brokers.kotak_neo import INDEX_ALIASES
        segment, token, _sym = await self.adapter.resolve(symbol, exchange)
        if (symbol or "").strip().upper() in INDEX_ALIASES:
            return f"{INDEX_PREFIX}{segment}|{token}"
        return f"{segment}|{token}"

    @staticmethod
    def _frames(event_scrips: str, event_index: str, keys: Iterable[str]) -> List[Any]:
        keys = list(keys)
        scrips = [k for k in keys if not k.startswith(INDEX_PREFIX)]
        indices = [k[len(INDEX_PREFIX):] for k in keys if k.startswith(INDEX_PREFIX)]
        frames: List[Any] = []
        if scrips:
            frames.append(json.dumps({"event": event_scrips, "inputtoken": ",".join(scrips)}))
        if indices:
            frames.append(json.dumps({"event": event_index, "inputtoken": ",".join(indices)}))
        return frames

    def subscribe_frames(self, keys: Iterable[str]) -> List[Any]:
        return self._frames("subscribeScripsLite", "subscribeIndices", keys)

    def unsubscribe_frames(self, keys: Iterable[str]) -> List[Any]:
        return self._frames("unsubscribeScripsLite", "unsubscribeIndices", keys)

    def decode(self, frame: Any) -> List[Tuple[str, float, Optional[datetime]]]:
        if not isinstance(frame, (bytes, bytearray)):
            return []
        out: List[Tuple[str, float, Optional[datetime]]] = []
        for packet in split_batch(bytes(frame)):
            found = decode_packet(packet, self.dividers)
            if found is not None:
                out.append(found)
        return out
