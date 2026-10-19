"""Phase AF: Dhan API v2 adapter.

Replaces the structural stub. Dhan is JSON REST at one host; every call carries `access-token` and
`client-id` headers. There is no exchange flow: the user generates the access token on the Dhan web
console and stores it with the client id under Settings (`access_token`, `client_id`); the platform
keeps it encrypted and re-uses it until its daily expiry.

Identifiers: every order, quote and candle needs the numeric `securityId` plus an `exchangeSegment`
(`NSE_EQ`, `NSE_FNO`, `IDX_I`, `BSE_EQ`, `MCX_COMM`). The public scrip master (CSV with header) is
parsed into platform instruments; `_resolve` maps plain symbols and index aliases to
(segment, security id, instrument kind). Quotes come from `marketfeed/quote` (batched by segment),
candles from `charts/intraday` / `charts/historical` (parallel arrays), the option chain from
`optionchain` + `optionchain/expirylist` (Dhan rate-limits the chain to one call every three seconds).

Endpoint paths, field names and enums follow the public docs (https://dhanhq.co/docs/v2/) as of
training cutoff; verify against the live docs before production use. Nothing here is reached by a LIVE
order until the tenant has logged in and the platform's risk path has approved the order.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
import weakref
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx

from app.brokers.base import BrokerInterface
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.timestamps import parse_broker_timestamp
from app.brokers.models import (BrokerCredentials, BrokerHolding, BrokerOrderRequest, BrokerOrderResponse, BrokerOrderStatus,
                                BrokerPosition, BrokerProfile, BrokerTradeEntry, Instrument, MarginInfo, OptionChain, OptionChainRow, Quote)
from app.brokers.shared_http import ORDER_POOL, shared_client
from app.core.enums import OrderSide
from app.core.models import OHLCVBar

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dhan.co/v2"
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
IST = timezone(timedelta(hours=5, minutes=30))

# platform exchange -> Dhan exchange segments it spans
SEGMENTS = {"NSE": ("NSE_EQ", "IDX_I"), "NFO": ("NSE_FNO",), "BSE": ("BSE_EQ", "IDX_I"), "BFO": ("BSE_FNO",), "MCX": ("MCX_COMM",)}
SEGMENT_OF = {("NSE", "E"): "NSE_EQ", ("NSE", "D"): "NSE_FNO", ("NSE", "I"): "IDX_I", ("BSE", "E"): "BSE_EQ", ("BSE", "D"): "BSE_FNO", ("BSE", "I"): "IDX_I",
              ("MCX", "M"): "MCX_COMM", ("MCX", "D"): "MCX_COMM", ("MCX", "C"): "MCX_COMM"}
INDEX_ALIASES = {"NIFTY": "NIFTY", "NIFTY 50": "NIFTY", "BANKNIFTY": "BANKNIFTY", "NIFTY BANK": "BANKNIFTY", "FINNIFTY": "FINNIFTY", "MIDCPNIFTY": "MIDCPNIFTY",
                 "SENSEX": "SENSEX", "BANKEX": "BANKEX"}
INDEX_SECURITY_IDS = {"NIFTY": "13", "BANKNIFTY": "25", "FINNIFTY": "27", "MIDCPNIFTY": "442", "SENSEX": "51", "BANKEX": "69"}
ORDER_TYPE_MAP = {"MARKET": "MARKET", "LIMIT": "LIMIT", "SL": "STOP_LOSS", "SL-M": "STOP_LOSS_MARKET"}
ORDER_TYPE_BACK = {v: k for k, v in ORDER_TYPE_MAP.items()}
PRODUCT_MAP = {"MIS": "INTRADAY", "CNC": "CNC", "NRML": "MARGIN", "MTF": "MTF", "BO": "BO", "CO": "CO"}
PRODUCT_BACK = {v: k for k, v in PRODUCT_MAP.items()}
# An order's `exchangeSegment` -> platform venue (re-exit guard; indices never carry orders).
_SEGMENT_VENUE = {"NSE_EQ": "NSE", "NSE_FNO": "NFO", "BSE_EQ": "BSE", "BSE_FNO": "BFO", "MCX_COMM": "MCX"}
STATUS_MAP = {"TRADED": "COMPLETE", "CANCELLED": "CANCELLED", "REJECTED": "REJECTED", "EXPIRED": "EXPIRED", "PENDING": "OPEN", "TRANSIT": "OPEN", "PART_TRADED": "OPEN", "TRIGGERED": "OPEN"}
INTERVAL_MAP = {"1min": "1", "5min": "5", "15min": "15", "25min": "25", "60min": "60"}
INSTRUMENT_KIND = {"EQUITY": "EQUITY", "INDEX": "INDEX", "FUTIDX": "FUTIDX", "OPTIDX": "OPTIDX", "FUTSTK": "FUTSTK", "OPTSTK": "OPTSTK", "FUTCOM": "FUTCOM", "OPTFUT": "OPTFUT"}
AUTH_ERROR_CODES = ("DH-901", "DH-902", "DH-903", "DH-808", "DH-809")
QUOTE_BATCH = 500
MASTER_CACHE_TTL_SECONDS = 6 * 60 * 60

# LD-7: the symbol map (platform exchange + symbol -> segment, security id, kind), built from the public scrip master
# once per process (and again after MASTER_CACHE_TTL_SECONDS) for every adapter built without a client of its own. An
# adapter is built per request; with its own map, every chart and screener run downloaded and parsed the whole master
# again (tens of MB on the real Dhan host). Only the three fields `_resolve` needs are kept, not the master's rows or
# the instrument list (instrument search still reads the master itself). Public data, the same for every user - no
# credential is sent to fetch it.
_SHARED_SYMBOLS: Dict[str, Tuple[float, Dict[Tuple[str, str], Tuple[str, str, str, dict]]]] = {}
_SHARED_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()
_NO_ROW: dict = {}
# LD-7 review B1 (ADR-0004: an exit must be able to resolve its instrument): a symbol missing from the shared map - a
# strike listed after the map was built - rebuilds it once, if the map is older than MISS_REFRESH_SECONDS; a symbol
# still missing after a rebuild is not looked for again for MISS_REMEMBER_SECONDS (an unknown symbol polled every cycle
# must not download the whole master every cycle). Together: at most one rebuild a minute from misses.
MISS_REFRESH_SECONDS = 60
MISS_REMEMBER_SECONDS = 10 * 60
_SHARED_MISSES: Dict[Tuple[str, str], float] = {}
# LD-7 review follow-up: when a rebuild's download fails (a 503, a timeout), the next attempt waits MISS_REFRESH_SECONDS;
# meanwhile a miss fails at once and a lookup that finds its symbol uses the map it has (never a queue of 15 s timeouts
# behind the build lock).
_SHARED_FAILED: Dict[str, float] = {}
# Order and account calls go through their own connection pool (LD-7 review N2, app/brokers/shared_http.py).
_ORDER_PATHS = ("/orders", "/trades", "/positions", "/holdings", "/fundlimit", "/profile")


def _parse_master(text: str) -> List[dict]:
    """The scrip master's CSV rows (run on a worker thread: the real file is tens of MB)."""
    return list(csv.DictReader(io.StringIO(text)))


def _parse_time(raw) -> Optional[datetime]:
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(int(raw), tz=timezone.utc).astimezone(IST)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(str(raw), fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


class DhanBroker(BrokerInterface):
    name = "dhan"
    BASE_URL = BASE_URL
    max_tag_length = 20

    def __init__(self, credentials: BrokerCredentials, client: Optional[httpx.AsyncClient] = None) -> None:
        if not (credentials.access_token and credentials.client_id):
            raise BrokerAuthenticationError("Dhan adapter requires credentials.access_token (from the Dhan console) and client_id")
        self.credentials = credentials
        # LD-7: built without a client (the app's own path), the adapter uses the process's shared connection to Dhan and
        # the shared scrip master (app/brokers/shared_http.py, _SHARED_*); an injected client (tests, tools) keeps its own.
        self._shared = client is None
        self._client = client or shared_client(self.BASE_URL, 15.0)
        # LD-7 review N2: orders (an exit among them) on a pool of their own, never queued behind data fetches
        self._order_client = client or shared_client(self.BASE_URL, 15.0, pool=ORDER_POOL)
        self._access_token: Optional[str] = credentials.access_token
        self._profile: Optional[BrokerProfile] = None
        self._master: Optional[Tuple[float, List[dict]]] = None
        self._instruments_cache: Dict[str, Tuple[float, List[Instrument]]] = {}
        # (platform exchange, platform symbol) -> (segment, security id, instrument kind, master row)
        self._symbol_map: Dict[Tuple[str, str], Tuple[str, str, str, dict]] = {}

    # --- transport ------------------------------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        if not self._access_token:
            raise BrokerAuthenticationError("Not authenticated - no Dhan access token")
        return {"Content-Type": "application/json", "Accept": "application/json", "access-token": self._access_token, "client-id": str(self.credentials.client_id)}

    async def _request(self, method: str, path: str, *, json_body=None):
        client = self._order_client if path.startswith(_ORDER_PATHS) else self._client
        response = await client.request(method, path, json=json_body, headers=self._headers())
        try:
            body = response.json() if response.content else {}
        except ValueError as exc:
            raise BrokerAPIError(f"Non-JSON response from Dhan: {response.text[:200]}", response.status_code) from exc
        if response.status_code in (401, 403):
            raise BrokerAuthenticationError((body.get("errorMessage") if isinstance(body, dict) else None) or "Dhan rejected the access token")
        if response.status_code >= 400 or (isinstance(body, dict) and body.get("status") == "failed") or (isinstance(body, dict) and body.get("errorCode")):
            code = str(body.get("errorCode") or "") if isinstance(body, dict) else ""
            message = (body.get("errorMessage") or body.get("message") or body.get("errorType") or "Dhan API error") if isinstance(body, dict) else "Dhan API error"
            if code in AUTH_ERROR_CODES or "token" in str(message).lower():
                raise BrokerAuthenticationError(f"{message} ({code})" if code else message)
            raise BrokerAPIError(f"{message} ({code})" if code else message, response.status_code, response.text)
        return body

    # --- session ---------------------------------------------------------------------------------
    async def authenticate(self) -> BrokerProfile:
        return await self.get_profile()

    async def get_profile(self) -> BrokerProfile:
        data = await self._request("GET", "/profile") or {}
        self._profile = BrokerProfile(broker=self.name, user_id=str(data.get("dhanClientId") or self.credentials.client_id), name=data.get("name") or None, email=None)
        return self._profile

    # --- instruments -----------------------------------------------------------------------------
    async def _scrip_master(self) -> List[dict]:
        if self._master and (time.monotonic() - self._master[0]) < MASTER_CACHE_TTL_SECONDS:
            return self._master[1]
        # LD-7 review N2: on the order pool - an exit's instrument may need it (a rebuild on a miss), and a data pool full of
        # chart and screen fetches must not hold that up; it is one download every few hours
        response = await self._order_client.get(SCRIP_MASTER_URL)
        if response.status_code >= 400:
            raise BrokerAPIError("Failed to fetch the Dhan scrip master", response.status_code, response.text)
        # LD-7 review follow-up: tens of MB of CSV parsed on a worker thread, not on the event loop
        rows = await asyncio.to_thread(_parse_master, response.text)
        self._master = (time.monotonic(), rows)
        return rows

    async def _shared_symbols(self, max_age: float = MASTER_CACHE_TTL_SECONDS, *, wait: bool = False) -> Dict[Tuple[str, str], Tuple[str, str, str, dict]]:
        """The process's symbol map (LD-7, `_SHARED_SYMBOLS`): built once from one download of the master - every
        exchange, exactly as `get_instruments` maps it - and rebuilt when older than `max_age` (MASTER_CACHE_TTL_SECONDS;
        a miss asks for MISS_REFRESH_SECONDS, `_resolve`). One build at a time per event loop. A lookup that has a map
        does not wait for a build in progress (`wait=False`) - only a miss does, as the build may bring its symbol.
        After a failed download no new attempt is made for MISS_REFRESH_SECONDS: the map there is serves (a miss then
        fails at once), or, with no map at all, the lookup fails at once."""
        entry = _SHARED_SYMBOLS.get("map")
        if entry and (time.monotonic() - entry[0]) < max_age:
            return entry[1]
        failed = _SHARED_FAILED.get("at")
        if failed is not None and (time.monotonic() - failed) < MISS_REFRESH_SECONDS:
            if entry:
                return entry[1]
            raise BrokerAPIError("The Dhan scrip master could not be downloaded; retrying within a minute")
        loop = asyncio.get_running_loop()
        lock = _SHARED_LOCKS.get(loop)
        if lock is None:
            lock = _SHARED_LOCKS[loop] = asyncio.Lock()
        if entry and lock.locked() and not wait:
            return entry[1]                           # a build is under way: the map there is, no queue behind it
        async with lock:
            entry = _SHARED_SYMBOLS.get("map")
            if entry and (time.monotonic() - entry[0]) < max_age:
                return entry[1]
            failed = _SHARED_FAILED.get("at")
            if failed is not None and (time.monotonic() - failed) < MISS_REFRESH_SECONDS:   # failed while this one waited
                if entry:
                    return entry[1]
                raise BrokerAPIError("The Dhan scrip master could not be downloaded; retrying within a minute")
            self._master = None                       # this adapter's copy of the master may be the old one: download
            self._instruments_cache.clear()
            try:
                await self._scrip_master()
            except (BrokerAPIError, httpx.HTTPError) as exc:
                _SHARED_FAILED["at"] = time.monotonic()
                logger.warning("Dhan scrip master download failed (%s); next attempt in %d s", exc, MISS_REFRESH_SECONDS)
                if entry:
                    return entry[1]
                raise BrokerAPIError(f"The Dhan scrip master could not be downloaded: {exc}") from exc
            _SHARED_FAILED.pop("at", None)
            for exchange in SEGMENTS:
                await self.get_instruments(exchange)
            table = {key: (hit[0], hit[1], hit[2], _NO_ROW) for key, hit in self._symbol_map.items()}
            _SHARED_SYMBOLS["map"] = (time.monotonic(), table)     # one assignment: readers see the old map or the new
            # this adapter's own copies (the rows, the instrument lists) go with it at the end of the request
            return table

    @staticmethod
    def _exchange_of(segment: str) -> str:
        return {"NSE_EQ": "NSE", "IDX_I": "NSE", "NSE_FNO": "NFO", "BSE_EQ": "BSE", "BSE_FNO": "BFO", "MCX_COMM": "MCX"}.get(segment, segment)

    async def get_instruments(self, exchange: Optional[str] = None) -> List[Instrument]:
        exchange = (exchange or "NSE").upper()
        cached = self._instruments_cache.get(exchange)
        if cached and (time.monotonic() - cached[0]) < MASTER_CACHE_TTL_SECONDS:
            return cached[1]
        wanted = set(SEGMENTS.get(exchange, (exchange,)))
        out: List[Instrument] = []
        for row in await self._scrip_master():
            segment = SEGMENT_OF.get((str(row.get("SEM_EXM_EXCH_ID", "")).upper(), str(row.get("SEM_SEGMENT", "")).upper()))
            if segment not in wanted:
                continue
            if segment == "IDX_I" and exchange == "BSE" and str(row.get("SEM_EXM_EXCH_ID", "")).upper() != "BSE":
                continue
            if segment == "IDX_I" and exchange == "NSE" and str(row.get("SEM_EXM_EXCH_ID", "")).upper() != "NSE":
                continue
            symbol = str(row.get("SEM_TRADING_SYMBOL") or row.get("SEM_CUSTOM_SYMBOL") or "").strip().upper()
            if not symbol:
                continue
            kind = str(row.get("SEM_INSTRUMENT_NAME") or "").upper()
            expiry = _parse_time(row.get("SEM_EXPIRY_DATE"))
            try:
                strike = float(row.get("SEM_STRIKE_PRICE") or 0)
            except (TypeError, ValueError):
                strike = 0.0
            option_type = str(row.get("SEM_OPTION_TYPE") or "").upper()
            try:
                lot = float(row.get("SEM_LOT_UNITS") or 1)
            except (TypeError, ValueError):
                lot = 1.0
            try:
                tick = float(row.get("SEM_TICK_SIZE") or 0.05)
            except (TypeError, ValueError):
                tick = 0.05
            instrument_type = option_type if option_type in ("CE", "PE") else ("FUT" if kind.startswith("FUT") else ("INDEX" if kind == "INDEX" else "EQ"))
            name = str(row.get("SM_SYMBOL_NAME") or row.get("SEM_CUSTOM_SYMBOL") or symbol).strip().upper()
            underlying = name.split(" ")[0].split("-")[0] if kind.startswith(("OPT", "FUT")) else name
            out.append(Instrument(instrument_token=str(row.get("SEM_SMST_SECURITY_ID") or "").strip(), exchange=exchange, tradingsymbol=symbol, name=underlying or None,
                                  segment=segment, instrument_type=instrument_type, lot_size=lot or 1.0, tick_size=tick or 0.05,
                                  expiry=expiry.date().isoformat() if expiry and kind != "EQUITY" and kind != "INDEX" else None, strike=strike if strike > 0 else None))
            self._symbol_map[(exchange, symbol)] = (segment, str(row.get("SEM_SMST_SECURITY_ID") or "").strip(), INSTRUMENT_KIND.get(kind, kind or "EQUITY"), row)
        self._instruments_cache[exchange] = (time.monotonic(), out)
        return out

    async def _resolve(self, symbol: str, exchange: str = "NSE", *, for_order: bool = False) -> Tuple[str, str, str]:
        """Platform symbol -> (exchangeSegment, securityId, instrument kind). `for_order`: an order (an exit among them)
        always gets a rebuild on a miss - the symbols remembered as missing are not skipped for it (ADR-0004)."""
        exchange = (exchange or "NSE").upper()
        if ":" in symbol:
            exchange, symbol = symbol.split(":", 1)
            exchange = exchange.upper()
        key = symbol.strip().upper()
        alias = INDEX_ALIASES.get(key)
        if alias and alias in INDEX_SECURITY_IDS and exchange in ("NSE", "BSE"):
            return ("IDX_I", INDEX_SECURITY_IDS[alias], "INDEX")
        if self._shared and (exchange, key) not in self._symbol_map:
            hit = (await self._shared_symbols()).get((exchange, key))
            if hit is None:
                hit = await self._resolve_missing(exchange, key, for_order=for_order)
        else:
            if (exchange, key) not in self._symbol_map:
                await self.get_instruments(exchange)
            hit = self._symbol_map.get((exchange, key))
        if hit is None:
            raise BrokerAPIError(f"Instrument {exchange}:{symbol} not found in the Dhan scrip master")
        return hit[0], hit[1], hit[2]

    async def _resolve_missing(self, exchange: str, key: str, *, for_order: bool = False) -> Optional[Tuple[str, str, str, dict]]:
        """LD-7 review B1: a symbol the shared map does not have - listed after the map was built (an intraday strike)
        - gets one rebuild of the map, unless the map is younger than MISS_REFRESH_SECONDS or this symbol was looked for
        and not found within MISS_REMEMBER_SECONDS."""
        missed = _SHARED_MISSES.get((exchange, key))
        if not for_order and missed is not None and (time.monotonic() - missed) < MISS_REMEMBER_SECONDS:
            return None
        before = _SHARED_SYMBOLS.get("map")
        hit = (await self._shared_symbols(max_age=MISS_REFRESH_SECONDS, wait=True)).get((exchange, key))
        if hit is None:
            after = _SHARED_SYMBOLS.get("map")
            if after is not None and (before is None or after[0] != before[0]):
                # a map built after this miss does not have it either: not looked for again for a while (a map that was
                # only young is not proof - the next miss after MISS_REFRESH_SECONDS rebuilds it)
                now = time.monotonic()
                for stale in [k for k, at in _SHARED_MISSES.items() if now - at >= MISS_REMEMBER_SECONDS]:
                    del _SHARED_MISSES[stale]                               # expired entries go on every write
                _SHARED_MISSES[(exchange, key)] = now
        else:
            _SHARED_MISSES.pop((exchange, key), None)
        return hit

    # --- quotes ----------------------------------------------------------------------------------
    def _quote_from(self, entry: dict, symbol: str) -> Quote:
        ohlc = entry.get("ohlc") or {}
        depth = entry.get("depth") or {}
        buy = (depth.get("buy") or [{}])[0] or {}
        sell = (depth.get("sell") or [{}])[0] or {}
        return Quote(symbol=symbol, ltp=float(entry.get("last_price") or 0), open=float(ohlc.get("open") or 0), high=float(ohlc.get("high") or 0),
                     low=float(ohlc.get("low") or 0), close=float(ohlc.get("close") or 0), volume=float(entry.get("volume") or 0),
                     oi=float(entry["oi"]) if entry.get("oi") not in (None, "") else None,
                     bid=float(buy["price"]) if buy.get("price") not in (None, "") else None, ask=float(sell["price"]) if sell.get("price") not in (None, "") else None,
                     bid_qty=float(buy["quantity"]) if buy.get("quantity") not in (None, "") else None, ask_qty=float(sell["quantity"]) if sell.get("quantity") not in (None, "") else None,
                     timestamp=_parse_time(entry.get("last_trade_time")))

    async def _quotes(self, keys: List[str]) -> Dict[str, Quote]:
        by_segment: Dict[str, List[int]] = {}
        back: Dict[Tuple[str, str], str] = {}
        unresolved: List[BrokerAPIError] = []
        for key in keys:
            exchange, plain = (key.split(":", 1) if ":" in key else ("NSE", key))
            try:
                segment, security_id, _kind = await self._resolve(plain, exchange)
            except BrokerAPIError as exc:
                # LD-7 review B1: one unknown symbol leaves it out of the answer, not the batch's other symbols (an exit
                # sweep quotes every open position together)
                logger.warning("Dhan quote: %s", exc)
                unresolved.append(exc)
                continue
            by_segment.setdefault(segment, []).append(int(security_id))
            back[(segment, str(security_id))] = key
        if unresolved and not by_segment:
            raise unresolved[0]                       # nothing resolved: the error, as before
        out: Dict[str, Quote] = {}
        segments = list(by_segment.items())
        for i in range(0, max(1, len(segments))):
            pass
        # Dhan accepts several segments per request; keep each request under the instrument cap.
        pending: Dict[str, List[int]] = {}
        count = 0
        for segment, ids in segments:
            for security_id in ids:
                pending.setdefault(segment, []).append(security_id)
                count += 1
                if count >= QUOTE_BATCH:
                    out.update(await self._quote_call(pending, back))
                    pending, count = {}, 0
        if pending:
            out.update(await self._quote_call(pending, back))
        return out

    async def _quote_call(self, payload: Dict[str, List[int]], back: Dict[Tuple[str, str], str]) -> Dict[str, Quote]:
        body = await self._request("POST", "/marketfeed/quote", json_body=payload) or {}
        out: Dict[str, Quote] = {}
        for segment, entries in (body.get("data") or {}).items():
            for security_id, entry in (entries or {}).items():
                key = back.get((segment, str(security_id)))
                if key is not None:
                    out[key] = self._quote_from(entry or {}, key)
        return out

    async def get_ltp(self, symbols: List[str]) -> Dict[str, float]:
        return {k: q.ltp for k, q in (await self._quotes(symbols)).items()}

    async def get_quote(self, symbols: List[str]) -> Dict[str, Quote]:
        return await self._quotes(symbols)

    async def get_quote_for_symbol(self, symbol: str, exchange: str = "NSE") -> Optional[Quote]:
        quote = (await self._quotes([f"{exchange}:{symbol}"])).get(f"{exchange}:{symbol}")
        if quote is None:
            raise BrokerAPIError(f"No quote returned for {exchange}:{symbol}")
        return quote

    async def get_ltp_for_symbol(self, symbol: str, exchange: str = "NSE") -> float:
        return (await self.get_quote_for_symbol(symbol, exchange)).ltp  # type: ignore[union-attr]

    # --- candles ---------------------------------------------------------------------------------
    async def get_historical_data(self, symbol: str, exchange: str, interval: str, from_date: datetime, to_date: datetime) -> List[OHLCVBar]:
        segment, security_id, kind = await self._resolve(symbol, exchange)

        def day(dt: datetime) -> str:
            dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(IST).strftime("%Y-%m-%d")
        payload = {"securityId": str(security_id), "exchangeSegment": segment, "instrument": kind, "fromDate": day(from_date), "toDate": day(to_date)}
        if interval == "day":
            payload["expiryCode"] = 0
            body = await self._request("POST", "/charts/historical", json_body=payload) or {}
        else:
            payload["interval"] = INTERVAL_MAP.get(interval, interval.replace("min", ""))
            body = await self._request("POST", "/charts/intraday", json_body=payload) or {}
        stamps = body.get("timestamp") or body.get("start_Time") or []
        opens, highs, lows, closes, volumes = (body.get(k) or [] for k in ("open", "high", "low", "close", "volume"))
        bars: List[OHLCVBar] = []
        for i, ts in enumerate(stamps):
            try:
                # LD-7: every field is converted here already, so the bar is built without a second validation pass.
                bars.append(OHLCVBar.model_construct(timestamp=datetime.fromtimestamp(int(float(ts)), tz=timezone.utc).astimezone(IST),
                                                     open=float(opens[i]), high=float(highs[i]), low=float(lows[i]), close=float(closes[i]),
                                                     volume=float(volumes[i]) if i < len(volumes) else 0.0))
            except (TypeError, ValueError, IndexError):
                continue
        return bars

    # --- option chain ----------------------------------------------------------------------------
    async def get_option_chain(self, underlying: str, expiry: Optional[date] = None) -> OptionChain:
        segment, security_id, _kind = await self._resolve(underlying, "NSE")
        scrip = {"UnderlyingScrip": int(security_id), "UnderlyingSeg": segment}
        expiries = (await self._request("POST", "/optionchain/expirylist", json_body=scrip) or {}).get("data") or []
        expiries = sorted(str(e) for e in expiries)
        if expiry is not None:
            if expiry.isoformat() not in expiries:
                raise BrokerAPIError(f"Dhan lists no {underlying} expiry on {expiry.isoformat()}")
            chosen = expiry.isoformat()
        else:
            today = date.today().isoformat()
            chosen = next((e for e in expiries if e >= today), expiries[-1] if expiries else "")
        if not chosen:
            raise BrokerAPIError(f"Dhan lists no expiries for {underlying}")
        data = (await self._request("POST", "/optionchain", json_body={**scrip, "Expiry": chosen}) or {}).get("data") or {}
        rows: List[OptionChainRow] = []
        for strike_text, legs in sorted((data.get("oc") or {}).items(), key=lambda kv: float(kv[0])):
            row = OptionChainRow(strike=float(strike_text))
            for right, leg in ((r, legs.get(r) or {}) for r in ("ce", "pe")):
                if not leg:
                    continue
                values = dict(ltp=float(leg.get("last_price") or 0), oi=float(leg["oi"]) if leg.get("oi") not in (None, "") else None,
                              chg=(float(leg["oi"]) - float(leg["previous_oi"])) if leg.get("oi") not in (None, "") and leg.get("previous_oi") not in (None, "") else None,
                              vol=float(leg["volume"]) if leg.get("volume") not in (None, "") else None, iv=float(leg["implied_volatility"]) if leg.get("implied_volatility") not in (None, "") else None,
                              bid=float(leg["top_bid_price"]) if leg.get("top_bid_price") not in (None, "") else None, ask=float(leg["top_ask_price"]) if leg.get("top_ask_price") not in (None, "") else None,
                              delta=float((leg.get("greeks") or {}).get("delta")) if (leg.get("greeks") or {}).get("delta") not in (None, "") else None)
                if right == "ce":
                    row.call_ltp, row.call_oi, row.call_change_oi, row.call_volume, row.call_iv, row.call_bid, row.call_ask, row.call_delta = (
                        values["ltp"], values["oi"], values["chg"], values["vol"], values["iv"], values["bid"], values["ask"], values["delta"])
                else:
                    row.put_ltp, row.put_oi, row.put_change_oi, row.put_volume, row.put_iv, row.put_bid, row.put_ask, row.put_delta = (
                        values["ltp"], values["oi"], values["chg"], values["vol"], values["iv"], values["bid"], values["ask"], values["delta"])
            rows.append(row)
        underlying_ltp = float(data.get("last_price") or 0) or None
        return OptionChain(underlying=underlying, expiry=chosen, underlying_ltp=underlying_ltp, rows=rows)

    # --- orders ----------------------------------------------------------------------------------
    async def place_order(self, order: BrokerOrderRequest) -> BrokerOrderResponse:
        segment, security_id, _kind = await self._resolve(order.symbol, order.exchange, for_order=True)
        order_type = ORDER_TYPE_MAP.get(order.order_type.upper())
        if order_type is None:
            raise BrokerAPIError(f"Unsupported order type {order.order_type} for Dhan")
        payload = {
            "dhanClientId": str(self.credentials.client_id), "transactionType": order.transaction_type.value, "exchangeSegment": segment,
            "productType": PRODUCT_MAP.get(order.product.upper(), order.product.upper()), "orderType": order_type, "validity": order.validity or "DAY",
            "securityId": str(security_id), "quantity": int(order.quantity), "disclosedQuantity": 0, "price": float(order.price or 0),
            "triggerPrice": float(order.trigger_price or 0), "afterMarketOrder": False,
        }
        if order.tag:
            payload["correlationId"] = order.tag[: self.max_tag_length]
        body = await self._request("POST", "/orders", json_body=payload) or {}
        order_id = str(body.get("orderId") or "")
        if not order_id:
            raise BrokerAPIError("Dhan returned no order id", 200, str(body))
        status = STATUS_MAP.get(str(body.get("orderStatus") or "").upper(), "OPEN")
        return BrokerOrderResponse(order_id=order_id, status="REJECTED" if status == "REJECTED" else "OPEN", message=body.get("orderStatus"), raw=body)

    async def _order_raw(self, order_id: str) -> dict:
        body = await self._request("GET", f"/orders/{order_id}") or {}
        if isinstance(body, list):
            body = body[0] if body else {}
        return body

    async def modify_order(self, order_id: str, quantity: Optional[float] = None, price: Optional[float] = None,
                           trigger_price: Optional[float] = None, order_type: Optional[str] = None) -> BrokerOrderResponse:
        current = await self._order_raw(order_id)
        payload = {
            "dhanClientId": str(self.credentials.client_id), "orderId": str(order_id),
            "orderType": ORDER_TYPE_MAP.get((order_type or "").upper(), current.get("orderType") or "MARKET"), "legName": current.get("legName") or "",
            "quantity": int(quantity if quantity is not None else float(current.get("quantity") or 0)),
            "price": float(price if price is not None else current.get("price") or 0), "disclosedQuantity": int(current.get("disclosedQuantity") or 0),
            "triggerPrice": float(trigger_price if trigger_price is not None else current.get("triggerPrice") or 0), "validity": current.get("validity") or "DAY",
        }
        body = await self._request("PUT", f"/orders/{order_id}", json_body=payload) or {}
        return BrokerOrderResponse(order_id=str(body.get("orderId") or order_id), status="MODIFIED", message=body.get("orderStatus"), raw=body)

    async def cancel_order(self, order_id: str) -> BrokerOrderResponse:
        body = await self._request("DELETE", f"/orders/{order_id}") or {}
        return BrokerOrderResponse(order_id=str(body.get("orderId") or order_id), status="CANCELLED", message=body.get("orderStatus"), raw=body)

    async def get_order_book(self) -> List[BrokerOrderStatus]:
        out = []
        for o in await self._request("GET", "/orders") or []:
            out.append(BrokerOrderStatus(order_id=str(o.get("orderId")), symbol=str(o.get("tradingSymbol") or "").upper(), transaction_type=OrderSide(str(o.get("transactionType", "BUY")).upper()),
                                         quantity=float(o.get("quantity") or 0), filled_quantity=float(o.get("filledQty") or 0),
                                         order_type=ORDER_TYPE_BACK.get(str(o.get("orderType") or ""), str(o.get("orderType") or "")), status=STATUS_MAP.get(str(o.get("orderStatus") or "").upper(), "OPEN"),
                                         price=float(o.get("price") or 0) or None, average_price=float(o.get("averageTradedPrice") or 0) or None,
                                         placed_at=parse_broker_timestamp(_parse_time(o.get("createTime") or o.get("updateTime"))),
                                         tag=o.get("correlationId") or None, product=PRODUCT_BACK.get(str(o.get("productType") or "")) or None,
                                         exchange=_SEGMENT_VENUE.get(str(o.get("exchangeSegment") or "").upper())))
        return out

    async def get_trade_book(self) -> List[BrokerTradeEntry]:
        out = []
        for t in await self._request("GET", "/trades") or []:
            out.append(BrokerTradeEntry(trade_id=str(t.get("exchangeTradeId") or t.get("orderId") or ""), order_id=str(t.get("orderId") or ""),
                                        symbol=str(t.get("tradingSymbol") or "").upper(), transaction_type=OrderSide(str(t.get("transactionType", "BUY")).upper()),
                                        quantity=float(t.get("tradedQuantity") or 0), price=float(t.get("tradedPrice") or 0), timestamp=_parse_time(t.get("exchangeTime") or t.get("createTime"))))
        return out

    async def get_positions(self) -> List[BrokerPosition]:
        out = []
        for p in await self._request("GET", "/positions") or []:
            qty = float(p.get("netQty") or 0)
            avg = p.get("costPrice") or (p.get("buyAvg") if qty >= 0 else p.get("sellAvg")) or 0
            out.append(BrokerPosition(symbol=str(p.get("tradingSymbol") or "").upper(), exchange=self._exchange_of(str(p.get("exchangeSegment") or "NSE_EQ")),
                                      product=PRODUCT_BACK.get(str(p.get("productType") or ""), str(p.get("productType") or "MIS")), quantity=qty,
                                      average_price=float(avg or 0), ltp=float(p.get("ltp") or p.get("lastTradedPrice") or 0), pnl=float(p.get("unrealizedProfit") or 0)))
        return out

    async def get_holdings(self) -> List[BrokerHolding]:
        out = []
        for h in await self._request("GET", "/holdings") or []:
            out.append(BrokerHolding(symbol=str(h.get("tradingSymbol") or "").upper(), exchange=str(h.get("exchange") or "NSE").upper().replace("ALL", "NSE"),
                                     quantity=float(h.get("totalQty") or h.get("availableQty") or 0), average_price=float(h.get("avgCostPrice") or 0),
                                     ltp=float(h.get("lastTradedPrice") or 0), pnl=0.0))
        return out

    async def get_margins(self) -> MarginInfo:
        data = await self._request("GET", "/fundlimit") or {}

        def f(*keys: str) -> float:
            for key in keys:
                if data.get(key) not in (None, ""):
                    try:
                        return float(data[key])
                    except (TypeError, ValueError):
                        continue
            return 0.0
        available = f("availabelBalance", "availableBalance")       # Dhan's own spelling first
        used = f("utilizedAmount")
        return MarginInfo(available_cash=available, used_margin=used, available_margin=available, total_margin=f("sodLimit") or (available + used))
