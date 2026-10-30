"""Part 1B: Groww Trade API adapter (REST, the wire format of Groww's official Python SDK).

Source: the official SDK `growwapi` 1.5.0 (PyPI, released 6 Dec 2025, the current release when read on 23 Oct 2026;
package downloaded and read). Every path, header and request field below is the one that SDK sends. The SDK does not
show the response shapes: the fields read here follow Groww's published API reference as quoted in search extracts
(groww.in could not be opened from the build machine) and every reader is defensive. docs/design/BROKER_GROWW.md lists
each fact with its source and what could not be verified.

Login (once a day, at 06:00 IST the token ends - search extract of the official docs). Two official ways, both with the
API key from Groww's Trade API page (API Keys) in `Authorization: Bearer <key>`:
* TOTP (automatic): `POST https://api.groww.in/v1/token/api/access` `{"key_type": "totp", "totp": "<six digits>"}` - the
  code is made from the TOTP secret the user stored, so the daily renew is one click.
* Approval: the user approves the key on Groww's API Keys page each day; then `{"key_type": "approval", "checksum":
  sha256(secret + timestamp), "timestamp": <epoch seconds>}`.
The answer's `token` is the day's access token; every later call sends it as `Authorization: Bearer <token>`. ATP keeps
it in the encrypted credential row, never logged, never returned.

Gates (TM-5 / TM-6, app/brokers/gates.py): this adapter refuses every LIVE order action (place, modify, cancel, exit,
stop) before anything is sent, and every market-data read (quotes, LTP, candles, the option chain and its Greeks, the
streaming feed) before anything is asked, while the Groww gates are closed - the connect flow, the daily login, the
profile check, positions, holdings, margins, the order and trade books and the instrument list work. Positions and
holdings carry no broker price while the market-data gate is closed (0): only the user's own quantities, average prices
and booked P&L are read.

TM-3 (market orders): SEBI's retail-algo framework and the exchanges ask that API orders be protected against an open
market price; Groww does not state in a page ATP could read whether it takes a plain MARKET order from an API client
(GW-4). Until it is confirmed the adapter declares `market_orders_allowed=False` and `stop_market=False`: a MARKET order
goes as a marketable LIMIT a protection band past the reference (app/execution/marketable_limit.py), a stop as SL with
its limit a band past the trigger. Bracket, cover and arbitrage products are not offered.

TM-23: Groww's own "smart orders" (GTT, OCO: `/order-advance/...` in the SDK) are deliberately NOT used. ATP's own
trigger engine is the default (the kill switch and the order chokepoint apply when a trigger fires); a native GTT / OCO
would be a later per-user opt-in.

TM-27: Groww limits calls by type (orders, live data, non-trading) per second, per minute and - in the figures the
review recorded - per day. app/brokers/rate_budget.py keeps a per-day budget per type for Groww (`DAILY_CAPS`), and the
data hub streams prices (app/market_data/groww_feed.py) instead of polling.

Symbols: the platform speaks plain trading symbols. Groww's instrument file (one CSV for every exchange,
`INSTRUMENT_CSV_URL`) gives each instrument its exchange, segment, trading symbol, Groww symbol and exchange token; F&O
contracts map to ATP's instrument master by attributes (app/brokers/contract_symbols.py): the adapter reports each
contract's underlying, expiry, strike and right.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from app.brokers import gates, master_cache
from app.brokers.base import BrokerCapabilities, BrokerInterface, stop_order_params
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError, BrokerOrderRejected
from app.brokers.models import (
    BrokerCredentials,
    BrokerHolding,
    BrokerOrderRequest,
    BrokerOrderResponse,
    BrokerOrderStatus,
    BrokerPosition,
    BrokerProfile,
    BrokerTradeEntry,
    Instrument,
    MarginInfo,
    OptionChain,
    OptionChainRow,
    Quote,
)
from app.brokers.timestamps import parse_broker_timestamp
from app.brokers.totp import totp_code
from app.core.enums import OrderSide
from app.core.models import OHLCVBar

logger = logging.getLogger(__name__)

NAME = "groww"
DISPLAY_NAME = "Groww"
IST = timezone(timedelta(hours=5, minutes=30))

# Hosts and paths (growwapi 1.5.0 groww/client.py).
API_BASE = "https://api.groww.in/v1"
TOKEN_URL = "https://api.groww.in/v1/token/api/access"
INSTRUMENT_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"
PROFILE_PATH = "/user/detail"
PLACE_ORDER_PATH = "/order/create"
MODIFY_ORDER_PATH = "/order/modify"
CANCEL_ORDER_PATH = "/order/cancel"
ORDER_LIST_PATH = "/order/list"
ORDER_TRADES_PATH = "/order/trades/{order_id}"
POSITIONS_PATH = "/positions/user"
HOLDINGS_PATH = "/holdings/user"
MARGINS_PATH = "/margins/detail/user"
ORDER_MARGIN_PATH = "/margins/detail/orders"
QUOTE_PATH = "/live-data/quote"
LTP_PATH = "/live-data/ltp"
OPTION_CHAIN_PATH = "/option-chain/exchange/{exchange}/underlying/{underlying}"
GREEKS_PATH = "/live-data/greeks/exchange/{exchange}/underlying/{underlying}/trading_symbol/{symbol}/expiry/{expiry}"
HISTORY_PATH = "/historical/candles"
SOCKET_TOKEN_URL = "https://api.groww.in/v1/api/apex/v1/socket/token/create/"
# The SDK's own smart-order paths, named here only so a test can prove ATP never calls them (TM-23).
SMART_ORDER_PREFIX = "/order-advance/"

API_VERSION = "1.0"                  # the SDK's `x-api-version`
LTP_BATCH = 50                       # symbols per LTP call: the documented maximum is not in the SDK (GW-8); 50 is conservative
ORDER_PAGE_SIZE = 25                 # the SDK's default page size for the order list
MAX_ORDER_PAGES = 20
INSTRUMENT_CACHE_TTL_SECONDS = 6 * 60 * 60

# ATP exchange -> Groww (exchange, segment). Groww names the exchange (NSE / BSE / MCX) and the segment (CASH / FNO /
# COMMODITY) separately (SDK constants).
EXCHANGE_SEGMENT: Dict[str, Tuple[str, str]] = {"NSE": ("NSE", "CASH"), "BSE": ("BSE", "CASH"), "NFO": ("NSE", "FNO"),
                                                "BFO": ("BSE", "FNO"), "MCX": ("MCX", "COMMODITY")}
SEGMENT_EXCHANGE: Dict[Tuple[str, str], str] = {v: k for k, v in EXCHANGE_SEGMENT.items()}
SEGMENTS = ("CASH", "FNO", "COMMODITY")
ORDER_TYPE_MAP = {"MARKET": "MARKET", "LIMIT": "LIMIT", "SL": "SL", "SL-M": "SL_M"}
ORDER_TYPE_BACK = {v: k for k, v in ORDER_TYPE_MAP.items()}
PRODUCTS = ("CNC", "MIS", "NRML", "MTF")          # the SDK also lists BO, CO and ARB: not offered by ATP
VALIDITIES = ("DAY", "IOC")
INTERVAL_MAP = {"1min": "1minute", "2min": "2minute", "3min": "3minute", "5min": "5minute", "10min": "10minute",
                "15min": "15minute", "30min": "30minute", "60min": "1hour", "1hour": "1hour", "4hour": "4hour",
                "day": "1day", "week": "1week", "month": "1month"}
INDEX_NAMES = {"NIFTY 50": "NIFTY", "NIFTY BANK": "BANKNIFTY", "NIFTY FIN SERVICE": "FINNIFTY"}


# --- the approval login's checksum ---------------------------------------------------------------------------------

def approval_checksum(secret: str, timestamp: int) -> str:
    """sha256(secret + str(timestamp)) as hex (SDK `_generate_checksum(secret, str(timestamp))`)."""
    return hashlib.sha256((secret + str(int(timestamp))).encode("utf-8")).hexdigest()


# --- helpers ----------------------------------------------------------------------------------------------------------

def _num(value: Any, default: float = 0.0) -> float:
    try:
        text = str(value).strip()
        return float(text) if text not in ("", "-", "NA", "None", "nan", "null") else default
    except (TypeError, ValueError):
        return default


def _col(row: Dict[str, Any], *names: str) -> str:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return str(row[name]).strip()
    return ""


def _status(raw: Any) -> str:
    value = str(raw or "").strip().upper()
    if value in ("EXECUTED", "COMPLETED", "COMPLETE", "FILLED", "DELIVERY_AWAITED"):
        return "COMPLETE"
    if value in ("CANCELLED", "CANCELED", "CANCELLATION_REQUESTED"):
        return "CANCELLED"
    if value in ("REJECTED", "FAILED"):
        return "REJECTED"
    if "TRIGGER" in value:
        return "TRIGGER_PENDING"
    return "OPEN"


def _side(raw: Any) -> OrderSide:
    return OrderSide.SELL if str(raw or "").strip().upper() in ("SELL", "S") else OrderSide.BUY


def _parse_time(raw: Any) -> Optional[datetime]:
    if raw in (None, "", "NA"):
        return None
    if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.strip().isdigit()):
        value = float(raw)
        if value > 1e11:                                  # epoch milliseconds
            value /= 1000.0
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(raw).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def instrument_from_row(row: Dict[str, Any]) -> Optional[Instrument]:
    """One row of Groww's instrument CSV -> an Instrument (None without an exchange token or a trading symbol). The SDK
    reads `exchange`, `trading_symbol`, `groww_symbol` and `exchange_token`; the other columns (`segment`,
    `instrument_type`, `underlying_symbol`, `expiry_date`, `strike_price`, `lot_size`, `tick_size`, `name`) follow the
    published reference (unverified, read defensively)."""
    token = _col(row, "exchange_token")
    symbol = _col(row, "trading_symbol")
    exchange = _col(row, "exchange").upper()
    segment = _col(row, "segment").upper() or "CASH"
    if not token or not symbol or not exchange:
        return None
    atp_exchange = SEGMENT_EXCHANGE.get((exchange, segment))
    if atp_exchange is None:
        return None
    kind = _col(row, "instrument_type").upper()
    derivative = segment in ("FNO", "COMMODITY")
    if derivative and kind in ("CE", "PE"):
        itype = kind
    elif derivative:
        itype = "FUT"
    elif kind == "IDX":
        itype = "INDEX"
    else:
        itype = "EQ"
    tick = _num(_col(row, "tick_size"), 0.05)
    if not (0 < tick <= 100):
        tick = 0.05
    strike = _num(_col(row, "strike_price"), 0.0)
    expiry = _col(row, "expiry_date")[:10] or None
    return Instrument(instrument_token=token, exchange=atp_exchange, tradingsymbol=symbol.upper(),
                      name=(_col(row, "underlying_symbol") if derivative else _col(row, "name")) or None,
                      segment=segment, instrument_type=itype, lot_size=_num(_col(row, "lot_size"), 1.0) or 1.0, tick_size=tick,
                      expiry=expiry if derivative else None, strike=strike if derivative and itype in ("CE", "PE") and strike > 0 else None)


def parse_instruments(text: str) -> Tuple[Dict[str, List[Instrument]], Dict[Tuple[str, str], Tuple[str, str, str, str]]]:
    """The CSV -> ({ATP exchange: instruments}, {(ATP exchange, symbol): (groww exchange, segment, trading symbol,
    exchange token)}), the symbol keyed by the trading symbol and by the Groww symbol."""
    reader = csv.DictReader(io.StringIO(text))
    by_exchange: Dict[str, List[Instrument]] = {}
    symbols: Dict[Tuple[str, str], Tuple[str, str, str, str]] = {}
    for raw in reader:
        row = {(k or "").strip(): v for k, v in raw.items()}
        instrument = instrument_from_row(row)
        if instrument is None:
            continue
        by_exchange.setdefault(instrument.exchange, []).append(instrument)
        groww_exchange, segment = EXCHANGE_SEGMENT[instrument.exchange]
        entry = (groww_exchange, segment, instrument.tradingsymbol, instrument.instrument_token)
        symbols.setdefault((instrument.exchange, instrument.tradingsymbol), entry)
        groww_symbol = _col(row, "groww_symbol").upper()
        if groww_symbol:
            symbols.setdefault((instrument.exchange, groww_symbol), entry)
    return by_exchange, symbols


class GrowwBroker(BrokerInterface):
    name = NAME
    display_name = DISPLAY_NAME
    # TM-3: until Groww states that it takes plain MARKET / SL-M orders from an API client (GW-4), a marketable LIMIT
    # and SL (with a band) - the same transform in PAPER and LIVE.
    capabilities = BrokerCapabilities(stop_market=False, stop_market_on_options=False, market_orders_allowed=False)
    max_tag_length = 20                  # `order_reference_id`: the published limit is not in the SDK (GW-8); 20 is safe
    history_by_date = False
    daily_days_per_request = 90          # history per call is not published in figures (GW-6): conservative
    native_intervals = frozenset(INTERVAL_MAP) - {"1hour", "4hour"}   # SCR-FIX A1: ATP names only (4 hours: from 60min)
    multi_quote = True

    def __init__(self, credentials: BrokerCredentials, client: Optional[httpx.AsyncClient] = None) -> None:
        if not credentials.api_key:
            raise BrokerAuthenticationError("Groww needs the API key from Groww's Trade API page (API Keys)")
        if not (credentials.access_token or credentials.totp_secret or credentials.api_secret):
            raise BrokerAuthenticationError("Groww needs the TOTP secret (or the API secret) to log in each day")
        self.credentials = credentials
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._access_token: Optional[str] = credentials.access_token or None
        self._profile: Optional[BrokerProfile] = None
        self._instruments: Dict[str, Tuple[float, List[Instrument]]] = {}
        self._symbols: Dict[Tuple[str, str], Tuple[str, str, str, str]] = {}
        self._groww_symbols: Dict[Tuple[str, str], str] = {}         # (exchange, trading symbol) -> Groww symbol
        self._shared_master = client is None
        self.clock = time.time
        self.http_calls = 0              # every request sent to Groww (the per-day budget counts them: TM-27)

    # --- transport ------------------------------------------------------------------------------------------------

    @staticmethod
    def _headers(bearer: str) -> Dict[str, str]:
        return {"Authorization": "Bearer " + bearer, "Accept": "application/json", "Content-Type": "application/json",
                "x-api-version": API_VERSION, "x-request-id": str(uuid.uuid4())}

    def _session_headers(self) -> Dict[str, str]:
        if not self._access_token:
            raise BrokerAuthenticationError("Not logged in to Groww - log in on the Broker page")
        return self._headers(self._access_token)

    async def _send(self, method: str, url: str, *, headers: Dict[str, str], params: Optional[Any] = None,
                    json_body: Optional[Any] = None) -> Any:
        kwargs: Dict[str, Any] = {"headers": headers}
        if params:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["json"] = json_body
        self.http_calls += 1
        response = await self._client.request(method, url, **kwargs)
        try:
            body = response.json()
        except ValueError as exc:
            raise BrokerAPIError(f"Groww answered with something that is not JSON (HTTP {response.status_code})",
                                 response.status_code) from exc
        if response.status_code in (401, 403):
            raise BrokerAuthenticationError("Groww refused the session - log in again")
        if response.status_code == 429:
            raise BrokerAPIError("Groww: too many requests", 429)
        if isinstance(body, dict) and str(body.get("status") or "").upper() == "FAILURE":
            error: Dict[str, Any] = body["error"] if isinstance(body.get("error"), dict) else {}
            message = str(error.get("displayMessage") or error.get("message") or "Groww refused the request")[:200]
            if response.status_code in (401, 403):
                raise BrokerAuthenticationError(f"Groww: {message}")
            raise BrokerAPIError(f"Groww: {message}", response.status_code if response.status_code >= 400 else 400)
        if response.status_code >= 400:
            raise BrokerAPIError(f"Groww error (HTTP {response.status_code})", response.status_code)
        if isinstance(body, dict) and "payload" in body:
            return body["payload"]
        return body

    def _url(self, path: str) -> str:
        return API_BASE + path

    # --- session --------------------------------------------------------------------------------------------------

    def token_request(self) -> Dict[str, Any]:
        """The body of today's token request: TOTP when a TOTP secret is stored (one click), else the approval checksum
        (the user approves the key on Groww's API Keys page first)."""
        if self.credentials.totp_secret:
            return {"key_type": "totp", "totp": totp_code(self.credentials.totp_secret)}
        if self.credentials.api_secret:
            stamp = int(self.clock())
            return {"key_type": "approval", "checksum": approval_checksum(self.credentials.api_secret, stamp), "timestamp": stamp}
        raise BrokerAuthenticationError("Groww needs the TOTP secret (or the API secret) to log in")

    async def authenticate(self) -> BrokerProfile:
        if self.credentials.totp_secret or self.credentials.api_secret:
            body = await self._send("POST", TOKEN_URL, headers=self._headers(self.credentials.api_key or ""), json_body=self.token_request())
            token = body.get("token") if isinstance(body, dict) else None
            if not token or (isinstance(body, dict) and body.get("isActive") is False):
                raise BrokerAuthenticationError("Groww did not accept the API key, the TOTP or today's approval")
            self._access_token = str(token)
            return await self.get_profile()
        if not self._access_token:
            raise BrokerAuthenticationError("No Groww session: store the API key and the TOTP secret")
        return await self.get_profile()

    async def get_profile(self) -> BrokerProfile:
        """A cheap authenticated read (the user's details), so a dead session shows."""
        body = await self._send("GET", self._url(PROFILE_PATH), headers=self._session_headers())
        data = body if isinstance(body, dict) else {}
        user_id = str(data.get("ucc") or data.get("vendor_user_id") or "")
        self._profile = BrokerProfile(broker=self.name, user_id=user_id, name=(data.get("name") or None))
        return self._profile

    async def active_segments(self) -> List[str]:
        """The segments enabled on the account (the profile's `active_segments`), for the connect guide."""
        body = await self._send("GET", self._url(PROFILE_PATH), headers=self._session_headers())
        found = body.get("active_segments") if isinstance(body, dict) else None
        return [str(s).upper() for s in found or []]

    async def disconnect(self) -> None:
        self._access_token = None

    # --- instruments ------------------------------------------------------------------------------------------------

    async def _build_master(self) -> Tuple[Dict[str, List[Instrument]], Dict[Tuple[str, str], Tuple[str, str, str, str]]]:
        response = await self._client.get(INSTRUMENT_CSV_URL)
        if response.status_code >= 400:
            raise BrokerAPIError("Groww's instrument list could not be read", response.status_code)
        return parse_instruments(response.text)

    async def get_instruments(self, exchange: Optional[str] = None) -> List[Instrument]:
        exchange = (exchange or "NSE").upper()
        cached = self._instruments.get(exchange)
        if cached and (time.monotonic() - cached[0]) < INSTRUMENT_CACHE_TTL_SECONDS:
            return cached[1]
        if exchange not in EXCHANGE_SEGMENT:
            return []
        if self._shared_master:
            by_exchange, symbols = await master_cache.shared(self.name, "ALL", self._build_master, ttl=INSTRUMENT_CACHE_TTL_SECONDS)
        else:
            by_exchange, symbols = await self._build_master()
        self._symbols.update(symbols)
        for (ex, key), entry in symbols.items():
            if key != entry[2]:
                self._groww_symbols.setdefault((ex, entry[2]), key)
        now = time.monotonic()
        for name, rows in by_exchange.items():
            self._instruments[name] = (now, rows)
        return by_exchange.get(exchange, [])

    async def resolve(self, symbol: str, exchange: str) -> Tuple[str, str, str, str]:
        """Platform symbol -> (Groww exchange, segment, trading symbol, exchange token)."""
        exchange = (exchange or "NSE").upper()
        text = (symbol or "").strip()
        if ":" in text:
            exchange, text = text.split(":", 1)
            exchange = exchange.upper()
        upper = INDEX_NAMES.get(text.upper(), text.upper())
        if (exchange, upper) not in self._symbols:
            await self.get_instruments(exchange)
        found = self._symbols.get((exchange, upper))
        if found is None:
            raise BrokerAPIError(f"{exchange}:{text} is not in today's Groww instrument list")
        return found

    # --- market data (gated: TM-6) -----------------------------------------------------------------------------------

    def _data_gate(self) -> None:
        gates.require_market_data(self.name, DISPLAY_NAME)

    @staticmethod
    def quote_from(data: Dict[str, Any], symbol: str) -> Quote:
        ohlc: Dict[str, Any] = data["ohlc"] if isinstance(data.get("ohlc"), dict) else {}
        depth: Dict[str, Any] = data["depth"] if isinstance(data.get("depth"), dict) else {}
        buy = ((depth.get("buy") or [{}])[0] or {}) if depth else {}
        sell = ((depth.get("sell") or [{}])[0] or {}) if depth else {}
        stamp = _parse_time(data.get("last_trade_time"))
        close = _num(ohlc.get("close"), 0.0)
        oi = _num(data.get("open_interest"), 0.0)
        return Quote(symbol=symbol, ltp=_num(data.get("last_price")), open=_num(ohlc.get("open")), high=_num(ohlc.get("high")),
                     low=_num(ohlc.get("low")), close=close, volume=_num(data.get("volume")), oi=oi or None,
                     bid=_num(data.get("bid_price") if data.get("bid_price") is not None else buy.get("price")) or None,
                     ask=_num(data.get("offer_price") if data.get("offer_price") is not None else sell.get("price")) or None,
                     bid_qty=_num(data.get("bid_quantity") if data.get("bid_quantity") is not None else buy.get("quantity")) or None,
                     ask_qty=_num(data.get("offer_quantity") if data.get("offer_quantity") is not None else sell.get("quantity")) or None,
                     timestamp=stamp, prev_close=close or None)

    async def _quote_one(self, key: str) -> Quote:
        exchange, plain = key.split(":", 1) if ":" in key else ("NSE", key)
        g_exchange, segment, trading_symbol, _token = await self.resolve(plain, exchange)
        body = await self._send("GET", self._url(QUOTE_PATH), headers=self._session_headers(),
                                params={"exchange": g_exchange, "segment": segment, "trading_symbol": trading_symbol})
        return self.quote_from(body if isinstance(body, dict) else {}, key)

    async def get_quote(self, symbols: List[str]) -> Dict[str, Quote]:
        """One call per symbol (Groww's quote endpoint takes one instrument; LTP takes many)."""
        self._data_gate()
        return {key: await self._quote_one(key) for key in symbols}

    async def get_ltp(self, symbols: List[str]) -> Dict[str, float]:
        self._data_gate()
        wanted: Dict[str, Dict[str, List[str]]] = {}
        for key in symbols:
            exchange, plain = key.split(":", 1) if ":" in key else ("NSE", key)
            g_exchange, segment, trading_symbol, _token = await self.resolve(plain, exchange)
            wanted.setdefault(segment, {}).setdefault(f"{g_exchange}_{trading_symbol}", []).append(key)
        out: Dict[str, float] = {}
        for segment, by_wire in wanted.items():
            wires = list(by_wire)
            for i in range(0, len(wires), LTP_BATCH):
                chunk = wires[i:i + LTP_BATCH]
                body = await self._send("GET", self._url(LTP_PATH), headers=self._session_headers(),
                                        params={"segment": segment, "exchange_symbols": ",".join(chunk)})
                for wire, value in (body.items() if isinstance(body, dict) else []):
                    for key in by_wire.get(str(wire), []):
                        out[key] = _num(value)
        return out

    async def get_quote_for_symbol(self, symbol: str, exchange: str = "NSE") -> Optional[Quote]:
        self._data_gate()
        return await self._quote_one(f"{exchange}:{symbol}")

    async def get_ltp_for_symbol(self, symbol: str, exchange: str = "NSE") -> float:
        quote_ = await self.get_quote_for_symbol(symbol, exchange)
        return float(quote_.ltp) if quote_ is not None else 0.0

    async def get_quotes_batch(self, items: Sequence[Tuple[str, str]]) -> Optional[Dict[Tuple[str, str], Quote]]:
        """The watchlist: one LTP call per segment (a quote without depth), keyed by the pair asked for."""
        self._data_gate()
        keys: Dict[str, Tuple[str, str]] = {}
        for exchange, symbol in items:
            try:
                await self.resolve(symbol, exchange)
            except BrokerAPIError:
                continue
            keys[f"{exchange}:{symbol}"] = (exchange, symbol)
        prices = await self.get_ltp(list(keys))
        return {keys[k]: Quote(symbol=k, ltp=v) for k, v in prices.items() if k in keys}

    async def get_historical_data(self, symbol: str, exchange: str, interval: str, from_date: datetime, to_date: datetime) -> List[OHLCVBar]:
        self._data_gate()
        g_exchange, segment, trading_symbol, _token = await self.resolve(symbol, exchange)
        groww_symbol = await self._groww_symbol(exchange, trading_symbol)

        def stamp(value: datetime) -> str:
            value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return value.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
        params = {"exchange": g_exchange, "segment": segment, "groww_symbol": groww_symbol, "start_time": stamp(from_date),
                  "end_time": stamp(to_date), "candle_interval": INTERVAL_MAP.get(interval, interval)}
        body = await self._send("GET", self._url(HISTORY_PATH), headers=self._session_headers(), params=params)
        bars: List[OHLCVBar] = []
        for row in (body.get("candles") if isinstance(body, dict) else None) or []:
            if not isinstance(row, (list, tuple)) or len(row) < 5:
                continue
            when = _parse_time(row[0])
            if when is None:
                continue
            bars.append(OHLCVBar(timestamp=when.astimezone(IST), open=float(row[1]), high=float(row[2]), low=float(row[3]),
                                 close=float(row[4]), volume=float(row[5]) if len(row) > 5 and row[5] is not None else 0.0))
        return bars

    async def _groww_symbol(self, exchange: str, trading_symbol: str) -> str:
        """The Groww symbol of an instrument (history asks by it); falls back to the trading symbol."""
        return self._groww_symbols.get(((exchange or "NSE").upper(), trading_symbol), trading_symbol)

    async def get_intraday_candles(self, symbol: str, exchange: str, interval: str) -> List[OHLCVBar]:
        self._data_gate()
        return await super().get_intraday_candles(symbol, exchange, interval)

    async def get_option_chain(self, underlying: str, expiry: Optional[date] = None) -> OptionChain:
        """The chain with Groww's Greeks (delta, IV per strike; the rest in `option_greeks`). Groww needs the expiry."""
        self._data_gate()
        if expiry is None:
            raise BrokerAPIError("Groww's option chain needs an expiry date")
        name = INDEX_NAMES.get(underlying.upper(), underlying.upper())
        exchange = "BSE" if name in ("SENSEX", "BANKEX") else "NSE"
        body = await self._send("GET", self._url(OPTION_CHAIN_PATH.format(exchange=exchange, underlying=name)),
                                headers=self._session_headers(), params={"expiry_date": expiry.isoformat()})
        data = body if isinstance(body, dict) else {}
        rows: List[OptionChainRow] = []
        strikes: Dict[str, Any] = data["strikes"] if isinstance(data.get("strikes"), dict) else {}
        for strike_text, legs in strikes.items():
            strike = _num(strike_text, 0.0)
            if strike <= 0 or not isinstance(legs, dict):
                continue
            row = OptionChainRow(strike=strike)
            for right in ("CE", "PE"):
                leg = legs.get(right) if isinstance(legs.get(right), dict) else None
                if leg is None:
                    continue
                greeks = leg.get("greeks") if isinstance(leg.get("greeks"), dict) else {}
                prefix = "call" if right == "CE" else "put"
                setattr(row, f"{prefix}_ltp", _num(leg.get("ltp")))
                setattr(row, f"{prefix}_oi", _num(leg.get("open_interest")))
                setattr(row, f"{prefix}_volume", _num(leg.get("volume")))
                if greeks.get("iv") is not None:
                    setattr(row, f"{prefix}_iv", _num(greeks.get("iv")))
                if greeks.get("delta") is not None:
                    setattr(row, f"{prefix}_delta", _num(greeks.get("delta")))
            rows.append(row)
        rows.sort(key=lambda r: r.strike)
        ltp = data.get("underlying_ltp")
        return OptionChain(underlying=underlying, expiry=expiry.isoformat(), underlying_ltp=_num(ltp) if ltp is not None else None, rows=rows)

    async def option_greeks(self, underlying: str, trading_symbol: str, expiry: date, exchange: str = "NSE") -> Dict[str, float]:
        """Groww's Greeks for one option contract: {delta, gamma, theta, vega, rho, iv} as Groww sends them."""
        self._data_gate()
        url = self._url(GREEKS_PATH.format(exchange=exchange.upper(), underlying=underlying.upper(), symbol=trading_symbol,
                                           expiry=expiry.isoformat()))
        body = await self._send("GET", url, headers=self._session_headers())
        greeks = body.get("greeks") if isinstance(body, dict) and isinstance(body.get("greeks"), dict) else body
        return {k: _num(v) for k, v in (greeks or {}).items() if k in ("delta", "gamma", "theta", "vega", "rho", "iv")}

    # --- orders (gated: TM-5) ----------------------------------------------------------------------------------------

    def _order_gate(self) -> None:
        gates.require_live_orders(self.name, DISPLAY_NAME)

    async def _order_reference(self, g_exchange: str, segment: str, trading_symbol: str) -> Optional[float]:
        """The reference price for a marketable limit (TM-3): Groww's own last price, read only to price this one order
        and never shown. Reached only with the LIVE-order gate open."""
        body = await self._send("GET", self._url(LTP_PATH), headers=self._session_headers(),
                                params={"segment": segment, "exchange_symbols": f"{g_exchange}_{trading_symbol}"})
        value = body.get(f"{g_exchange}_{trading_symbol}") if isinstance(body, dict) else None
        return _num(value, 0.0) or None

    async def build_order_payload(self, order: BrokerOrderRequest) -> Tuple[Dict[str, Any], str]:
        """(the `/order/create` body, a plain note). Raises BrokerOrderRejected for what ATP does not send to Groww."""
        from app.execution.marketable_limit import to_venue_order
        product = (order.product or "MIS").upper()
        if product not in PRODUCTS:
            raise BrokerOrderRejected(f"ATP does not send {product} orders to Groww (only CNC, MIS, NRML, MTF)")
        validity = (order.validity or "DAY").upper()
        if validity not in VALIDITIES:
            raise BrokerOrderRejected(f"ATP does not send validity {validity} to Groww (only DAY or IOC)")
        g_exchange, segment, trading_symbol, _token = await self.resolve(order.symbol, order.exchange)
        note = ""
        order_type = (order.order_type or "MARKET").upper()
        if order_type == "MARKET":
            reference = order.price if order.price else await self._order_reference(g_exchange, segment, trading_symbol)
            try:
                order, note = to_venue_order(order, self.capabilities, reference)
            except ValueError as exc:
                raise BrokerOrderRejected("Groww needs a price for this order and none could be read; nothing was sent") from exc
            order_type = (order.order_type or "LIMIT").upper()
        elif order_type == "SL-M" and not self.capabilities.stop_market:
            kind, limit = stop_order_params(self.capabilities, order.symbol, order.transaction_type, float(order.trigger_price or 0))
            order = order.model_copy(update={"order_type": kind, "price": limit})
            order_type = kind
        if order_type not in ORDER_TYPE_MAP:
            raise BrokerOrderRejected(f"ATP does not send {order_type} orders to Groww")
        payload: Dict[str, Any] = {
            "trading_symbol": trading_symbol, "quantity": int(order.quantity),
            "price": round(float(order.price or 0), 2), "trigger_price": round(float(order.trigger_price), 2) if order.trigger_price else None,
            "validity": validity, "exchange": g_exchange, "segment": segment, "product": product,
            "order_type": ORDER_TYPE_MAP[order_type], "transaction_type": "BUY" if order.transaction_type == OrderSide.BUY else "SELL",
            "order_reference_id": (order.client_ref or order.tag or uuid.uuid4().hex)[: self.max_tag_length],
        }
        return payload, note

    async def place_order(self, order: BrokerOrderRequest) -> BrokerOrderResponse:
        self._order_gate()
        payload, note = await self.build_order_payload(order)
        body = await self._send("POST", self._url(PLACE_ORDER_PATH), headers=self._session_headers(), json_body=payload)
        order_id = str(body.get("groww_order_id") or "") if isinstance(body, dict) else ""
        if not order_id:
            raise BrokerAPIError("Groww returned no order id")
        return BrokerOrderResponse(order_id=order_id, status=_status(body.get("order_status")), message=note or None, raw=body)

    async def place_stop_loss_order(self, symbol: str, exchange: str, transaction_type: OrderSide, quantity: float, trigger_price: float,
                                    product: str = "MIS", tag: Optional[str] = None, is_option: Optional[bool] = None) -> BrokerOrderResponse:
        self._order_gate()
        return await super().place_stop_loss_order(symbol, exchange, transaction_type, quantity, trigger_price, product, tag, is_option)

    async def exit_position(self, symbol: str, exchange: str, quantity: float, side: OrderSide, *, product: str = "MIS",
                            tag: Optional[str] = None) -> BrokerOrderResponse:
        self._order_gate()
        return await super().exit_position(symbol, exchange, quantity, side, product=product, tag=tag)

    async def modify_order(self, order_id: str, quantity: Optional[float] = None, price: Optional[float] = None,
                           trigger_price: Optional[float] = None, order_type: Optional[str] = None) -> BrokerOrderResponse:
        self._order_gate()
        current = next((o for o in await self._order_rows() if str(o.get("groww_order_id")) == str(order_id)), None)
        if current is None:
            raise BrokerAPIError(f"Order {order_id} is not in the Groww order list")
        wanted = (order_type or ORDER_TYPE_BACK.get(str(current.get("order_type") or "LIMIT"), "LIMIT")).upper()
        if wanted in ("MARKET", "SL-M"):
            raise BrokerOrderRejected("ATP changes a Groww order by its limit price; a market order is not sent")
        payload = {"quantity": int(quantity if quantity is not None else _num(current.get("quantity"))),
                   "price": round(float(price if price is not None else _num(current.get("price"))), 2),
                   "trigger_price": round(float(trigger_price), 2) if trigger_price is not None else (current.get("trigger_price") or None),
                   "groww_order_id": str(order_id), "order_type": ORDER_TYPE_MAP[wanted], "segment": str(current.get("segment") or "CASH")}
        body = await self._send("POST", self._url(MODIFY_ORDER_PATH), headers=self._session_headers(), json_body=payload)
        return BrokerOrderResponse(order_id=str(order_id), status="MODIFIED", raw=body if isinstance(body, dict) else None)

    async def cancel_order(self, order_id: str) -> BrokerOrderResponse:
        self._order_gate()
        current = next((o for o in await self._order_rows() if str(o.get("groww_order_id")) == str(order_id)), None)
        segment = str((current or {}).get("segment") or "CASH")
        body = await self._send("POST", self._url(CANCEL_ORDER_PATH), headers=self._session_headers(),
                                json_body={"segment": segment, "groww_order_id": str(order_id)})
        return BrokerOrderResponse(order_id=str(order_id), status="CANCELLED", raw=body if isinstance(body, dict) else None)

    async def get_order_margin(self, order: BrokerOrderRequest) -> Optional[float]:
        """The margin Groww would block for `order` (an account read; no order is placed)."""
        g_exchange, segment, trading_symbol, _token = await self.resolve(order.symbol, order.exchange)
        order_type = (order.order_type or "MARKET").upper()
        body = await self._send("POST", self._url(ORDER_MARGIN_PATH), headers=self._session_headers(), params={"segment": segment},
                                json_body=[{"trading_symbol": trading_symbol,
                                            "transaction_type": "BUY" if order.transaction_type == OrderSide.BUY else "SELL",
                                            "quantity": int(order.quantity), "price": round(float(order.price or 0), 2),
                                            "order_type": ORDER_TYPE_MAP.get(order_type, "LIMIT"), "product": (order.product or "MIS").upper(),
                                            "exchange": g_exchange}])
        value = _num((body or {}).get("total_requirement"), -1.0) if isinstance(body, dict) else -1.0
        return value if value >= 0 else None

    # --- books and account reads (work with the gates closed) ---------------------------------------------------------

    async def _order_rows(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for segment in SEGMENTS:
            for page in range(MAX_ORDER_PAGES):
                body = await self._send("GET", self._url(ORDER_LIST_PATH), headers=self._session_headers(),
                                        params={"segment": segment, "page": page, "page_size": ORDER_PAGE_SIZE})
                rows = [r for r in ((body.get("order_list") if isinstance(body, dict) else None) or []) if isinstance(r, dict)]
                for row in rows:
                    row.setdefault("segment", segment)
                out.extend(rows)
                if len(rows) < ORDER_PAGE_SIZE:
                    break
        return out

    async def get_order_book(self) -> List[BrokerOrderStatus]:
        out: List[BrokerOrderStatus] = []
        for o in await self._order_rows():
            exchange = SEGMENT_EXCHANGE.get((str(o.get("exchange") or "NSE").upper(), str(o.get("segment") or "CASH").upper()))
            avg = _num(o.get("average_fill_price"))
            out.append(BrokerOrderStatus(
                order_id=str(o.get("groww_order_id") or ""), symbol=str(o.get("trading_symbol") or "").upper(),
                transaction_type=_side(o.get("transaction_type")), quantity=_num(o.get("quantity")),
                filled_quantity=_num(o.get("filled_quantity")),
                order_type=ORDER_TYPE_BACK.get(str(o.get("order_type") or ""), str(o.get("order_type") or "")),
                status=_status(o.get("order_status")), price=_num(o.get("price")) or None, average_price=avg or None,
                placed_at=parse_broker_timestamp(_parse_time(o.get("created_at") or o.get("exchange_time"))),
                tag=(o.get("order_reference_id") or None), product=str(o.get("product") or "") or None, exchange=exchange))
        return out

    async def get_trade_book(self) -> List[BrokerTradeEntry]:
        """Today's fills: Groww lists trades per order, so one call per order with a fill (non-trading calls)."""
        out: List[BrokerTradeEntry] = []
        for o in await self._order_rows():
            if _num(o.get("filled_quantity")) <= 0:
                continue
            order_id = str(o.get("groww_order_id") or "")
            body = await self._send("GET", self._url(ORDER_TRADES_PATH.format(order_id=order_id)), headers=self._session_headers(),
                                    params={"segment": str(o.get("segment") or "CASH"), "page": 0, "page_size": 50})
            for t in (body.get("trade_list") if isinstance(body, dict) else None) or []:
                out.append(BrokerTradeEntry(trade_id=str(t.get("groww_trade_id") or t.get("exchange_trade_id") or ""), order_id=order_id,
                                            symbol=str(t.get("trading_symbol") or o.get("trading_symbol") or "").upper(),
                                            transaction_type=_side(t.get("transaction_type") or o.get("transaction_type")),
                                            quantity=_num(t.get("quantity")), price=_num(t.get("price")),
                                            timestamp=_parse_time(t.get("trade_date_time") or t.get("created_at"))))
        return out

    async def get_positions(self) -> List[BrokerPosition]:
        """Net positions (units). While the market-data gate is closed no broker price is read into ATP: `ltp` is 0 and
        `pnl` is the booked part only (Groww's `realised_pnl`)."""
        body = await self._send("GET", self._url(POSITIONS_PATH), headers=self._session_headers())
        show_prices = gates.market_data_open(self.name)
        out: List[BrokerPosition] = []
        for p in (body.get("positions") if isinstance(body, dict) else None) or []:
            exchange = SEGMENT_EXCHANGE.get((str(p.get("exchange") or "NSE").upper(), str(p.get("segment") or "CASH").upper()), "NSE")
            qty = _num(p.get("quantity"))
            avg = _num(p.get("net_price")) or _num(p.get("net_carry_forward_price"))
            booked = _num(p.get("realised_pnl"))
            ltp = _num(p.get("ltp")) if show_prices else 0.0
            pnl = booked + (qty * (ltp - avg) if show_prices and ltp else 0.0)
            out.append(BrokerPosition(symbol=str(p.get("trading_symbol") or "").upper(), exchange=exchange,
                                      product=str(p.get("product") or "MIS"), quantity=qty, average_price=round(avg, 2),
                                      ltp=ltp, pnl=round(pnl, 2)))
        return out

    async def get_holdings(self) -> List[BrokerHolding]:
        body = await self._send("GET", self._url(HOLDINGS_PATH), headers=self._session_headers())
        out: List[BrokerHolding] = []
        for h in (body.get("holdings") if isinstance(body, dict) else None) or []:
            qty, avg = _num(h.get("quantity")), _num(h.get("average_price"))
            # Groww's holdings carry no price (published reference): the mark comes from ATP's own data source.
            out.append(BrokerHolding(symbol=str(h.get("trading_symbol") or "").upper(), exchange="NSE", quantity=qty,
                                     average_price=avg, ltp=0.0, pnl=0.0))
        return out

    async def get_margins(self) -> MarginInfo:
        body = await self._send("GET", self._url(MARGINS_PATH), headers=self._session_headers())
        data = body if isinstance(body, dict) else {}
        cash = _num(data.get("clear_cash"))
        used = _num(data.get("net_margin_used"))
        equity = data.get("equity_margin_details") if isinstance(data.get("equity_margin_details"), dict) else {}
        available = _num(equity.get("cnc_balance_available"), cash) if equity else cash
        return MarginInfo(available_cash=cash, used_margin=used, available_margin=available, total_margin=cash + used)
