"""Part 1B: Kotak Neo Trade API adapter (REST, the current official SDK's wire format).

Source: the official Python SDK `kotakneoapi` 3.0.9 (PyPI, released 15 Oct 2026; repository
https://github.com/Kotak-Neo/kotak-neo-python, docs read 23 Oct 2026). Every path, header and field below is the one
that SDK sends; docs/design/BROKER_KOTAK_NEO.md lists each fact with its source and what could not be verified.

Login (TOTP, once a day): the consumer key from the NEO app (More -> Trade API -> Generate application) goes in the
`Authorization` header of every call. `POST <login host>/login/1.0/tradeApiLogin` with the registered mobile number, the
UCC and the current six-digit TOTP gives a view token and a session id; `POST /login/1.0/tradeApiValidate` with the MPIN
(headers `sid` + `Auth` = the view token) gives the trade token, the trade session id and the account's own base URL.
Every later call goes to that base URL with `Auth` (trade token) + `Sid` (trade session id). ATP keeps those three in
the encrypted credential row as one session string (`encode_session`), never logged, never returned.

Gates (TM-5 / TM-6, app/brokers/gates.py): this adapter refuses every LIVE order action (place, modify, cancel, exit,
stop) before anything is sent, and every market-data read (quotes, candles, the option chain, the streaming feed) before
anything is asked, while the Kotak Neo gates are closed - the connect flow, the daily login, the profile check, positions,
holdings, funds and the instrument list work. Prices inside positions and holdings are left out (0) while the
market-data gate is closed: only the user's own quantities, average prices and booked P&L are read.

TM-3 (market orders): Kotak Neo's Trade API page says market orders are not allowed for retail algo trading. The adapter
declares `market_orders_allowed=False` and `stop_market=False`; a MARKET order goes as a marketable LIMIT a protection
band past the reference price (app/execution/marketable_limit.py) and a stop as SL with its limit a band past the
trigger (app/brokers/base.py stop_order_params). Bracket and Cover orders are not offered (the SDK 3.0.9 accepts only
CNC / MIS / NRML / MTF).

Symbols: the platform speaks plain trading symbols. Kotak names cash equities `RELIANCE-EQ`, and every quote needs the
instrument token (`pSymbol`) from the day's scrip master (CSV files listed by `script-details/1.0/masterscrip/file-paths`).
F&O contracts map to ATP's instrument master by attributes (app/brokers/contract_symbols.py): the adapter reports each
contract's underlying (`pSymbolName`), expiry, strike and right.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

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
    lot_or_none,
)
from app.brokers.timestamps import parse_broker_timestamp
from app.brokers.totp import totp_code
from app.core.enums import OrderSide
from app.core.models import OHLCVBar

logger = logging.getLogger(__name__)

NAME = "kotak_neo"
DISPLAY_NAME = "Kotak Neo"
IST = timezone(timedelta(hours=5, minutes=30))

# Login host and paths (SDK 3.0.9 utils/urls.py SESSION_PROD_BASE_URL, settings.py PROD_URL).
LOGIN_BASE_URL = "https://mis.kotaksecurities.com"
DEFAULT_BASE_URL = "https://mis.kotaksecurities.com"        # PROD_BASE_URL: used only when the login returns no baseUrl
TOTP_LOGIN_PATH = "/login/1.0/tradeApiLogin"
TOTP_VALIDATE_PATH = "/login/1.0/tradeApiValidate"
CLIENT_IP_PATH = "/login/1.0/get-client-ip"
LOGOUT_PATH = "/apim/login/2.0/logout"
PLACE_ORDER_PATH = "/quick/order/rule/ms/place"
MODIFY_ORDER_PATH = "/quick/order/vr/modify"
CANCEL_ORDER_PATH = "/quick/order/cancel"
ORDER_BOOK_PATH = "/quick/user/orders"
TRADE_BOOK_PATH = "/quick/user/trades"
POSITIONS_PATH = "/portfolio/v2/positions"
HOLDINGS_PATH = "/portfolio/v1/holdings"
LIMITS_PATH = "/quick/user/limits"
MARGIN_PATH = "/quick/user/check-margin"
SCRIP_MASTER_PATH = "/script-details/1.0/masterscrip/file-paths"
QUOTES_PATH = "/script-details/1.0/quotes/neosymbol/{symbols}/{kind}"
OPTION_CHAIN_PATH = "/market-data/1.0/watchlist/option-chain"
HISTORY_PATH = "/market-data/1.0/historical/details"

NEO_FIN_KEY = "neotradeapi"          # the SDK's production default for the `neo-fin-key` header
ORDER_SOURCE = "NEOTRADEAPI"         # the SDK's `os` field on every order request
SESSION_PREFIX = "kn1."
QUOTE_BATCH = 50                     # "Limit: 50 instruments per call" (SDK docs, quotes.md)
INSTRUMENT_CACHE_TTL_SECONDS = 6 * 60 * 60
# NSE F&O scrip master expiries count seconds from 1 Jan 1980 (the SDK adds 315511200 s); BSE F&O and MCX use Unix time.
NSE_FO_EPOCH_OFFSET = 315511200

EXCHANGE_SEGMENT = {"NSE": "nse_cm", "BSE": "bse_cm", "NFO": "nse_fo", "BFO": "bse_fo", "MCX": "mcx_fo"}
SEGMENT_EXCHANGE = {v: k for k, v in EXCHANGE_SEGMENT.items()}
ORDER_TYPE_MAP = {"MARKET": "MKT", "LIMIT": "L", "SL": "SL", "SL-M": "SL-M"}
ORDER_TYPE_BACK = {v: k for k, v in ORDER_TYPE_MAP.items()}
PRODUCTS = ("CNC", "MIS", "NRML", "MTF")          # place_order_product_allowed_values (SDK 3.0.9)
VALIDITY_BY_SEGMENT = {"nse_cm": ("DAY", "IOC"), "bse_cm": ("DAY", "IOC"), "nse_fo": ("DAY", "IOC"), "bse_fo": ("DAY", "IOC"),
                       "mcx_fo": ("DAY",)}
INTERVAL_MAP = {"1min": "1min", "3min": "3min", "5min": "5min", "10min": "10min", "15min": "15min", "30min": "30min",
                "60min": "60min", "day": "D", "week": "W"}
# Index names the quote API and the feed take in place of a token (SDK docs, websocket: "Exchange Identifier is not a
# number in case of Indexes").
INDEX_ALIASES = {"NIFTY": "Nifty 50", "NIFTY 50": "Nifty 50", "BANKNIFTY": "Nifty Bank", "NIFTY BANK": "Nifty Bank",
                 "FINNIFTY": "Nifty Fin Service", "SENSEX": "SENSEX", "INDIA VIX": "INDIA VIX", "INDIAVIX": "INDIA VIX"}
INDEX_SEGMENT = {"SENSEX": "bse_cm"}
ALREADY_COMPLETE_ST_CODE = 1021


# --- the session string (one encrypted field) ----------------------------------------------------------------------

def encode_session(token: str, sid: str, base_url: Optional[str], ucc: Optional[str] = None, data_center: Optional[str] = None) -> str:
    """The trade session as one string for `BrokerCredentials.access_token` (stored encrypted, never logged)."""
    payload = {"t": token, "s": sid, "b": base_url or "", "u": ucc or "", "dc": data_center or ""}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return SESSION_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_session(value: Optional[str]) -> Optional[Dict[str, str]]:
    """The stored session string back to its parts; None for anything that is not one (a missing or foreign value)."""
    if not value or not value.startswith(SESSION_PREFIX):
        return None
    body = value[len(SESSION_PREFIX):]
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not data.get("t") or not data.get("s"):
        return None
    return {k: str(data.get(k) or "") for k in ("t", "s", "b", "u", "dc")}


# --- scrip master ---------------------------------------------------------------------------------------------------

def _num(value: Any, default: float = 0.0) -> float:
    try:
        text = str(value).strip()
        return float(text) if text not in ("", "-", "NA", "None") else default
    except (TypeError, ValueError):
        return default


def parse_expiry(raw: Any, segment: str) -> Optional[str]:
    """A scrip-master expiry (seconds; NSE F&O counted from 1980) as an ISO date; None for none."""
    seconds = _num(raw, 0.0)
    if seconds <= 0:
        return None
    if segment == "nse_fo":
        seconds += NSE_FO_EPOCH_OFFSET
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(IST).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _col(row: Dict[str, Any], *names: str) -> str:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return str(row[name]).strip()
    return ""


def platform_symbol(trading_symbol: str, segment: str) -> str:
    """Kotak's trading symbol -> the platform's: a cash equity loses its `-EQ` (`-BE`, ... series) suffix."""
    text = (trading_symbol or "").strip().upper()
    if segment in ("nse_cm", "bse_cm") and "-" in text:
        base, series = text.rsplit("-", 1)
        if series.isalpha() and len(series) <= 2:
            return base
    return text


def instrument_from_row(row: Dict[str, Any], segment: str) -> Optional[Instrument]:
    """One scrip-master CSV row -> an Instrument (None for a row without a token or a trading symbol). Columns as the SDK
    reads them: pSymbol (token), pTrdSymbol, pSymbolName (underlying), pExpiryDate, pOptionType (CE / PE / XX),
    dStrikePrice (in paise; the CSV header carries a trailing ';'), lLotSize, dTickSize, pInstType."""
    token = _col(row, "pSymbol")
    trading_symbol = _col(row, "pTrdSymbol")
    if not token or not trading_symbol:
        return None
    option_type = _col(row, "pOptionType").upper()
    strike_raw = _num(_col(row, "dStrikePrice;", "dStrikePrice"), 0.0)
    strike = strike_raw / 100.0 if strike_raw > 0 else None
    tick_raw = _num(_col(row, "dTickSize"), 0.0)
    tick = tick_raw / 100.0 if tick_raw >= 1 else tick_raw
    if not (0 < tick <= 100):
        tick = 0.05
    derivative = segment.endswith("_fo")
    if derivative and option_type in ("CE", "PE"):
        itype = option_type
    elif derivative:
        itype = "FUT"
    else:
        itype = "EQ"
    lot = lot_or_none(_col(row, "lLotSize", "iLotSize"))      # M68 TS1-088: none given = None, never 1
    return Instrument(instrument_token=token, exchange=SEGMENT_EXCHANGE.get(segment, segment.upper()),
                      tradingsymbol=platform_symbol(trading_symbol, segment), name=(_col(row, "pSymbolName") or None),
                      segment=segment, instrument_type=itype, lot_size=lot, tick_size=tick,
                      expiry=parse_expiry(_col(row, "pExpiryDate"), segment) if derivative else None,
                      strike=strike if derivative and itype != "FUT" else None)


def parse_scrip_master(text: str, segment: str) -> Tuple[List[Instrument], Dict[Tuple[str, str], Tuple[str, str, str]]]:
    """The CSV -> (instruments, (exchange, platform symbol) -> (segment, token, Kotak trading symbol))."""
    reader = csv.DictReader(io.StringIO(text))
    out: List[Instrument] = []
    symbols: Dict[Tuple[str, str], Tuple[str, str, str]] = {}
    for raw in reader:
        row = {(k or "").strip(): v for k, v in raw.items()}
        instrument = instrument_from_row(row, segment)
        if instrument is None:
            continue
        out.append(instrument)
        kotak_symbol = _col(row, "pTrdSymbol")
        symbols.setdefault((instrument.exchange, instrument.tradingsymbol.upper()), (segment, instrument.instrument_token, kotak_symbol))
        symbols.setdefault((instrument.exchange, kotak_symbol.upper()), (segment, instrument.instrument_token, kotak_symbol))
    return out, symbols


def _status(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in ("complete", "completed", "traded", "filled"):
        return "COMPLETE"
    if value in ("cancelled", "canceled"):
        return "CANCELLED"
    if value == "rejected":
        return "REJECTED"
    if "trigger" in value:
        return "TRIGGER_PENDING"
    return "OPEN"


def _side(raw: Any) -> OrderSide:
    return OrderSide.SELL if str(raw or "").strip().upper() in ("S", "SELL") else OrderSide.BUY


def _parse_time(raw: Any) -> Optional[datetime]:
    if raw in (None, "", "NA", "--"):
        return None
    text = str(raw).strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d-%b-%Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)
        except ValueError:
            continue
    return None


class KotakNeoBroker(BrokerInterface):
    name = NAME
    display_name = DISPLAY_NAME
    # TM-3: no MARKET or SL-M orders from an API client (Kotak Neo's retail-algo rule); everything else as usual.
    capabilities = BrokerCapabilities(stop_market=False, stop_market_on_options=False, market_orders_allowed=False)
    max_tag_length = 20                  # the SDK checks only "non-blank"; 20 keeps ATP's algo tag as on Kite
    history_by_date = True               # fromdate / todate are dates (YYYY-MM-DD)
    daily_days_per_request = 365         # the per-interval range limits are not published in figures: conservative
    native_intervals = frozenset(INTERVAL_MAP)           # SCR-FIX A1: 1 / 3 / 5 / 10 / 15 / 30 / 60 min, day, week
    multi_quote = True

    def __init__(self, credentials: BrokerCredentials, client: Optional[httpx.AsyncClient] = None) -> None:
        if not credentials.api_key:
            raise BrokerAuthenticationError("Kotak Neo needs the consumer key from the NEO app (More, Trade API)")
        session = decode_session(credentials.access_token)
        if session is None and not (credentials.mobile_number and credentials.client_id and credentials.pin):
            raise BrokerAuthenticationError("Kotak Neo needs the mobile number, UCC, MPIN and TOTP secret to log in")
        self.credentials = credentials
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._session: Optional[Dict[str, str]] = session
        self._access_token: Optional[str] = credentials.access_token if session else None
        self._profile: Optional[BrokerProfile] = None
        self._instruments: Dict[str, Tuple[float, List[Instrument]]] = {}
        self._symbols: Dict[Tuple[str, str], Tuple[str, str, str]] = {}
        self._shared_master = client is None

    # --- transport ------------------------------------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        found = (self._session or {}).get("b") or DEFAULT_BASE_URL
        return found.rstrip("/")

    def _consumer_headers(self) -> Dict[str, str]:
        return {"Authorization": self.credentials.api_key or "", "accept": "application/json"}

    def _session_headers(self, *, with_consumer: bool = False) -> Dict[str, str]:
        if not self._session:
            raise BrokerAuthenticationError("Not logged in to Kotak Neo - log in on the Broker page")
        headers = {"Sid": self._session["s"], "Auth": self._session["t"], "accept": "application/json"}
        if with_consumer:
            headers["Authorization"] = self.credentials.api_key or ""
        return headers

    async def _send(self, method: str, url: str, *, headers: Dict[str, str], params: Optional[Dict[str, Any]] = None,
                    json_body: Optional[Dict[str, Any]] = None, form: Optional[Dict[str, Any]] = None) -> Any:
        kwargs: Dict[str, Any] = {"headers": headers}
        if params:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["json"] = json_body
        if form is not None:
            # The order endpoints take one form field `jData` holding the JSON body (SDK rest.py).
            kwargs["data"] = {"jData": json.dumps(form, separators=(",", ":"))}
            kwargs["headers"] = {**headers, "Content-Type": "application/x-www-form-urlencoded"}
        response = await self._client.request(method, url, **kwargs)
        try:
            body = response.json()
        except ValueError as exc:
            raise BrokerAPIError(f"Kotak Neo answered with something that is not JSON (HTTP {response.status_code})",
                                 response.status_code) from exc
        if response.status_code in (401, 403):
            raise BrokerAuthenticationError("Kotak Neo refused the session - log in again")
        if response.status_code == 429:
            raise BrokerAPIError("Kotak Neo: too many requests", 429)
        if response.status_code >= 400:
            raise BrokerAPIError(self._message(body) or f"Kotak Neo error (HTTP {response.status_code})", response.status_code)
        if isinstance(body, dict):
            code = body.get("stCode")
            stat = str(body.get("stat") or "").lower()
            if (code not in (None, 200, "200")) or stat in ("not_ok", "notok"):
                message = self._message(body) or "Kotak Neo refused the request"
                if code in (ALREADY_COMPLETE_ST_CODE, str(ALREADY_COMPLETE_ST_CODE)):
                    raise BrokerOrderRejected(f"Kotak Neo: {message}")
                raise BrokerAPIError(f"Kotak Neo: {message}", 400 if code not in (None, "") else None)
        return body

    @staticmethod
    def _message(body: Any) -> str:
        if isinstance(body, dict):
            for key in ("errMsg", "emsg", "message", "msg"):
                if body.get(key):
                    return str(body[key])[:200]
            err = body.get("error")
            if isinstance(err, list) and err and isinstance(err[0], dict):
                return str(err[0].get("message") or "")[:200]
        return ""

    # --- session --------------------------------------------------------------------------------------------------

    async def authenticate(self) -> BrokerProfile:
        if self.credentials.mobile_number and self.credentials.client_id and self.credentials.pin and self.credentials.totp_secret:
            login_headers = {"Authorization": self.credentials.api_key or "", "neo-fin-key": NEO_FIN_KEY, "Content-Type": "application/json"}
            view = await self._send("POST", LOGIN_BASE_URL + TOTP_LOGIN_PATH, headers=login_headers,
                                    json_body={"mobileNumber": self.credentials.mobile_number, "ucc": self.credentials.client_id,
                                               "totp": totp_code(self.credentials.totp_secret)})
            data = view.get("data") if isinstance(view, dict) else None
            if not isinstance(data, dict) or not data.get("token") or not data.get("sid"):
                raise BrokerAuthenticationError("Kotak Neo did not accept the mobile number, UCC or TOTP")
            validate_headers = {"Authorization": self.credentials.api_key or "", "sid": str(data["sid"]), "Auth": str(data["token"]),
                                "neo-fin-key": NEO_FIN_KEY, "Content-Type": "application/json"}
            trade = await self._send("POST", LOGIN_BASE_URL + TOTP_VALIDATE_PATH, headers=validate_headers, json_body={"mpin": self.credentials.pin})
            tdata = trade.get("data") if isinstance(trade, dict) else None
            if not isinstance(tdata, dict) or not tdata.get("token") or not tdata.get("sid"):
                raise BrokerAuthenticationError("Kotak Neo did not accept the MPIN")
            ucc = str(tdata.get("ucc") or data.get("ucc") or self.credentials.client_id or "")
            self._access_token = encode_session(str(tdata["token"]), str(tdata["sid"]), tdata.get("baseUrl"), ucc, tdata.get("dataCenter"))
            self._session = decode_session(self._access_token)
            self._profile = BrokerProfile(broker=self.name, user_id=ucc, name=(tdata.get("greetingName") or None))
            return self._profile
        if not self._session:
            raise BrokerAuthenticationError("No Kotak Neo session: store the mobile number, UCC, MPIN and TOTP secret")
        return await self.get_profile()

    async def get_profile(self) -> BrokerProfile:
        """A cheap authenticated round-trip (the login host's "what is my IP" call), so a dead session shows."""
        await self._send("GET", LOGIN_BASE_URL + CLIENT_IP_PATH, headers=self._session_headers(with_consumer=True))
        ucc = (self._session or {}).get("u") or self.credentials.client_id or ""
        self._profile = BrokerProfile(broker=self.name, user_id=ucc, name=self._profile.name if self._profile else None)
        return self._profile

    async def client_ip(self) -> Optional[str]:
        """The outbound IP Kotak Neo sees for this session (for the static-IP check; read-only)."""
        body = await self._send("GET", LOGIN_BASE_URL + CLIENT_IP_PATH, headers=self._session_headers(with_consumer=True))
        rows = body.get("data") if isinstance(body, dict) else None
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return str(rows[0].get("ip") or "") or None
        return None

    async def disconnect(self) -> None:
        try:
            if self._session:
                await self._send("POST", self.base_url + LOGOUT_PATH, headers=self._session_headers(with_consumer=True))
        except (BrokerAPIError, BrokerAuthenticationError, httpx.HTTPError) as exc:
            logger.info("Kotak Neo logout call failed: %s", type(exc).__name__)
        finally:
            self._access_token = None
            self._session = None

    # --- instruments ------------------------------------------------------------------------------------------------

    async def _file_paths(self) -> List[str]:
        body = await self._send("GET", self.base_url + SCRIP_MASTER_PATH, headers=self._consumer_headers())
        data = body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else body
        paths = (data or {}).get("filesPaths") if isinstance(data, dict) else None
        return [str(p) for p in paths or []]

    @staticmethod
    def _pick_file(paths: Sequence[str], segment: str) -> Optional[str]:
        """The segment's file: `<segment>-v1.csv` first (the current cash files), then `<segment>.csv`; never
        `nse_com.csv` for `nse_cm` (the SDK docs warn the older-format file is named so)."""
        named = [p for p in paths if p.rsplit("/", 1)[-1].lower() in (f"{segment}-v1.csv", f"{segment}.csv")]
        named.sort(key=lambda p: 0 if p.lower().endswith("-v1.csv") else 1)
        return named[0] if named else None

    async def _build_exchange(self, exchange: str) -> Tuple[List[Instrument], Dict[Tuple[str, str], Tuple[str, str, str]]]:
        segment = EXCHANGE_SEGMENT.get(exchange)
        if segment is None:
            return [], {}
        url = self._pick_file(await self._file_paths(), segment)
        if url is None:
            raise BrokerAPIError(f"Kotak Neo lists no instrument file for {exchange} today")
        response = await self._client.get(url)
        if response.status_code >= 400:
            raise BrokerAPIError(f"Kotak Neo instrument file for {exchange} could not be read", response.status_code)
        return parse_scrip_master(response.text, segment)

    async def get_instruments(self, exchange: Optional[str] = None) -> List[Instrument]:
        exchange = (exchange or "NSE").upper()
        cached = self._instruments.get(exchange)
        if cached and (time.monotonic() - cached[0]) < INSTRUMENT_CACHE_TTL_SECONDS:
            return cached[1]
        if self._shared_master:
            out, symbols = await master_cache.shared(self.name, exchange, lambda: self._build_exchange(exchange),
                                                     ttl=INSTRUMENT_CACHE_TTL_SECONDS)
        else:
            out, symbols = await self._build_exchange(exchange)
        self._symbols.update(symbols)
        self._instruments[exchange] = (time.monotonic(), out)
        return out

    async def resolve(self, symbol: str, exchange: str) -> Tuple[str, str, str]:
        """Platform symbol -> (segment, instrument token or index name, Kotak trading symbol)."""
        exchange = (exchange or "NSE").upper()
        text = (symbol or "").strip()
        if ":" in text:
            exchange, text = text.split(":", 1)
            exchange = exchange.upper()
        upper = text.upper()
        if upper in INDEX_ALIASES:
            name = INDEX_ALIASES[upper]
            return INDEX_SEGMENT.get(upper, EXCHANGE_SEGMENT.get(exchange, "nse_cm")), name, name
        if (exchange, upper) not in self._symbols:
            await self.get_instruments(exchange)
        found = self._symbols.get((exchange, upper))
        if found is None:
            raise BrokerAPIError(f"{exchange}:{text} is not in today's Kotak Neo instrument list")
        return found

    # --- market data (gated: TM-6) -----------------------------------------------------------------------------------

    def _data_gate(self) -> None:
        gates.require_market_data(self.name, DISPLAY_NAME)

    async def _quotes_raw(self, pairs: Sequence[Tuple[str, str]], kind: str = "all") -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for i in range(0, len(pairs), QUOTE_BATCH):
            chunk = ",".join(f"{seg}|{tok}" for seg, tok in pairs[i:i + QUOTE_BATCH])
            url = self.base_url + QUOTES_PATH.format(symbols=quote(chunk, safe="|,"), kind=kind)
            body = await self._send("GET", url, headers=self._consumer_headers())
            rows = body if isinstance(body, list) else (body.get("data") if isinstance(body, dict) else None)
            out.extend(r for r in rows or [] if isinstance(r, dict))
        return out

    @staticmethod
    def quote_from(row: Dict[str, Any], symbol: str) -> Quote:
        ohlc = row.get("ohlc") or {}
        depth = row.get("depth") or {}
        buy = (depth.get("buy") or [{}])[0] or {}
        sell = (depth.get("sell") or [{}])[0] or {}
        stamp = _num(row.get("lstup_time"), 0.0)
        oi = _num(row.get("open_int"), 0.0)
        close = _num(ohlc.get("close"), 0.0)
        return Quote(symbol=symbol, ltp=_num(row.get("ltp")), open=_num(ohlc.get("open")), high=_num(ohlc.get("high")),
                     low=_num(ohlc.get("low")), close=close, volume=_num(row.get("last_volume")), oi=oi or None,
                     bid=_num(buy.get("price")) if buy.get("price") not in (None, "") else None,
                     ask=_num(sell.get("price")) if sell.get("price") not in (None, "") else None,
                     bid_qty=_num(buy.get("quantity")) if buy.get("quantity") not in (None, "") else None,
                     ask_qty=_num(sell.get("quantity")) if sell.get("quantity") not in (None, "") else None,
                     timestamp=datetime.fromtimestamp(stamp, tz=timezone.utc) if stamp > 0 else None,
                     prev_close=close or None)

    async def _keyed_quotes(self, keys: List[str], kind: str = "all") -> Dict[str, Quote]:
        wanted: Dict[Tuple[str, str], List[str]] = {}
        for key in keys:
            exchange, plain = key.split(":", 1) if ":" in key else ("NSE", key)
            segment, token, _sym = await self.resolve(plain, exchange)
            wanted.setdefault((segment, str(token)), []).append(key)
        out: Dict[str, Quote] = {}
        for row in await self._quotes_raw(list(wanted), kind):
            pair = (str(row.get("exchange") or ""), str(row.get("exchange_token") or ""))
            for key in wanted.get(pair, []):
                out[key] = self.quote_from(row, key)
        return out

    async def get_quote(self, symbols: List[str]) -> Dict[str, Quote]:
        self._data_gate()
        return await self._keyed_quotes(symbols)

    async def get_ltp(self, symbols: List[str]) -> Dict[str, float]:
        self._data_gate()
        return {k: q.ltp for k, q in (await self._keyed_quotes(symbols, "ltp")).items()}

    async def get_quote_for_symbol(self, symbol: str, exchange: str = "NSE") -> Optional[Quote]:
        self._data_gate()
        key = f"{exchange}:{symbol}"
        found = (await self._keyed_quotes([key])).get(key)
        if found is None:
            raise BrokerAPIError(f"No quote returned for {key}")
        return found

    async def get_ltp_for_symbol(self, symbol: str, exchange: str = "NSE") -> float:
        quote_ = await self.get_quote_for_symbol(symbol, exchange)
        return float(quote_.ltp) if quote_ is not None else 0.0

    async def get_quotes_batch(self, items):
        self._data_gate()
        keys = [f"{exchange}:{symbol}" for exchange, symbol in items]
        found = {}
        for key, (exchange, symbol) in zip(keys, items):
            try:
                await self.resolve(symbol, exchange)
                found[key] = (exchange, symbol)
            except BrokerAPIError:
                continue
        quotes = await self._keyed_quotes(list(found))
        return {found[k]: q for k, q in quotes.items()}

    async def get_historical_data(self, symbol: str, exchange: str, interval: str, from_date: datetime, to_date: datetime) -> List[OHLCVBar]:
        self._data_gate()
        segment, token, _sym = await self.resolve(symbol, exchange)

        def day(value: datetime) -> str:
            value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return value.astimezone(IST).date().isoformat()
        params = {"neosymbol": f"{segment}|{token}", "interval": INTERVAL_MAP.get(interval, interval),
                  "fromdate": day(from_date), "todate": day(to_date)}
        body = await self._send("GET", self.base_url + HISTORY_PATH, headers=self._consumer_headers(), params=params)
        data = body.get("data") if isinstance(body, dict) else None
        bars: List[OHLCVBar] = []
        for row in (data or {}).get("candles") or []:
            try:
                stamp = datetime.strptime(str(row[0]), "%Y-%m-%dT%H:%M:%S%z")
            except (ValueError, IndexError, TypeError):
                continue
            bars.append(OHLCVBar(timestamp=stamp, open=float(row[1]), high=float(row[2]), low=float(row[3]), close=float(row[4]),
                                 volume=float(row[5]) if len(row) > 5 and row[5] is not None else 0.0))
        return bars

    async def get_intraday_candles(self, symbol: str, exchange: str, interval: str) -> List[OHLCVBar]:
        self._data_gate()
        return await super().get_intraday_candles(symbol, exchange, interval)

    async def get_option_chain(self, underlying: str, expiry: Optional[date] = None) -> OptionChain:
        self._data_gate()
        exchange = "bse_fo" if underlying.upper() in ("SENSEX", "BANKEX") else "nse_fo"
        params: Dict[str, Any] = {"exchange": exchange, "underlying": underlying.upper()}
        if expiry is not None:
            params["expiry"] = expiry.isoformat()
        body = await self._send("GET", self.base_url + OPTION_CHAIN_PATH, headers=self._consumer_headers(), params=params)
        data = (body.get("data") if isinstance(body, dict) else None) or {}
        common = data.get("common_data") or {}
        rows: Dict[float, OptionChainRow] = {}
        for right, items in (("CE", data.get("call") or []), ("PE", data.get("put") or [])):
            for item in items:
                inst, q, oi = item.get("instrument") or {}, item.get("quote") or {}, item.get("openInterest") or {}
                strike = _num(inst.get("strikePrice"), 0.0)
                if strike <= 0:
                    continue
                row = rows.setdefault(strike, OptionChainRow(strike=strike))
                ltp, cur, chg, vol = _num(q.get("ltp")), _num(oi.get("current")), _num(oi.get("change")), _num(q.get("volume"))
                if right == "CE":
                    row.call_ltp, row.call_oi, row.call_change_oi, row.call_volume = ltp, cur, chg, vol
                else:
                    row.put_ltp, row.put_oi, row.put_change_oi, row.put_volume = ltp, cur, chg, vol
        return OptionChain(underlying=underlying, expiry=str(common.get("expiryDt") or (expiry.isoformat() if expiry else "")),
                           underlying_ltp=None, rows=[rows[k] for k in sorted(rows)])

    # --- orders (gated: TM-5) ----------------------------------------------------------------------------------------

    def _order_gate(self) -> None:
        gates.require_live_orders(self.name, DISPLAY_NAME)

    async def _order_reference(self, segment: str, token: str) -> Optional[float]:
        """The reference price for a marketable limit (TM-3): the broker's own last price, read only to price this one
        order and never shown. Reached only with the LIVE-order gate open."""
        rows = await self._quotes_raw([(segment, token)], "ltp")
        if not rows:
            return None
        return _num(rows[0].get("ltp"), 0.0) or None

    async def build_order_payload(self, order: BrokerOrderRequest) -> Tuple[Dict[str, Any], str]:
        """(the `jData` body for place-order, a plain note). Pure apart from the symbol lookup and, for a MARKET order,
        the reference price. Raises BrokerOrderRejected for what Kotak Neo does not take (BO / CO, an unknown product, a
        validity the segment refuses)."""
        from app.execution.marketable_limit import to_venue_order
        product = (order.product or "MIS").upper()
        if product not in PRODUCTS:
            raise BrokerOrderRejected(f"Kotak Neo does not take {product} orders through its Trade API (only CNC, MIS, NRML, MTF)")
        segment, token, kotak_symbol = await self.resolve(order.symbol, order.exchange)
        validity = (order.validity or "DAY").upper()
        if validity not in VALIDITY_BY_SEGMENT.get(segment, ("DAY", "IOC")):
            raise BrokerOrderRejected(f"Kotak Neo does not take validity {validity} on {SEGMENT_EXCHANGE.get(segment, segment)}")
        note = ""
        order_type = (order.order_type or "MARKET").upper()
        if order_type == "MARKET":
            reference = order.price if order.price else await self._order_reference(segment, str(token))
            try:
                order, note = to_venue_order(order, self.capabilities, reference)
            except ValueError as exc:
                raise BrokerOrderRejected("Kotak Neo needs a price for this order and none could be read; nothing was sent") from exc
            order_type = "LIMIT"
        elif order_type == "SL-M":
            trigger = float(order.trigger_price or 0)
            kind, limit = stop_order_params(self.capabilities, order.symbol, order.transaction_type, trigger)
            order = order.model_copy(update={"order_type": kind, "price": limit})
            order_type = kind
        if order_type not in ORDER_TYPE_MAP:
            raise BrokerOrderRejected(f"Kotak Neo does not take {order_type} orders")
        payload: Dict[str, Any] = {
            "am": "YES" if order.after_market else "NO", "dq": "0", "es": segment, "mp": "0", "pc": product,
            "pr": f"{float(order.price or 0):.2f}", "pt": ORDER_TYPE_MAP[order_type], "qt": str(int(order.quantity)), "rt": validity,
            "tp": f"{float(order.trigger_price or 0):.2f}", "ts": kotak_symbol, "tt": "B" if order.transaction_type == OrderSide.BUY else "S",
            "os": ORDER_SOURCE,
        }
        if order.tag:
            payload["ig"] = order.tag[: self.max_tag_length]
        return payload, note

    async def place_order(self, order: BrokerOrderRequest) -> BrokerOrderResponse:
        self._order_gate()
        payload, note = await self.build_order_payload(order)
        body = await self._send("POST", self.base_url + PLACE_ORDER_PATH, headers=self._session_headers(with_consumer=True), form=payload)
        order_id = str(body.get("nOrdNo") or "") if isinstance(body, dict) else ""
        if not order_id:
            raise BrokerAPIError("Kotak Neo returned no order number")
        return BrokerOrderResponse(order_id=order_id, status="OPEN", message=note or None, raw=body)

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
        current = next((o for o in await self._order_book_raw() if str(o.get("nOrdNo")) == str(order_id)), None)
        if current is None:
            raise BrokerAPIError(f"Order {order_id} is not in the Kotak Neo order book")
        wanted_type = (order_type or ORDER_TYPE_BACK.get(str(current.get("prcTp") or "L"), "LIMIT")).upper()
        if wanted_type in ("MARKET", "SL-M"):
            raise BrokerOrderRejected("Kotak Neo does not take market orders from an API; change the limit price instead")
        payload = {
            "mp": "0", "dq": "0", "vd": str(current.get("vldt") or "DAY"),
            "pr": f"{float(price if price is not None else _num(current.get('prc'))):.2f}", "pt": ORDER_TYPE_MAP[wanted_type],
            "am": "NO", "tp": f"{float(trigger_price if trigger_price is not None else _num(current.get('trgPrc'))):.2f}",
            "qt": str(int(quantity if quantity is not None else _num(current.get("qty")))), "no": str(order_id), "os": ORDER_SOURCE,
        }
        body = await self._send("POST", self.base_url + MODIFY_ORDER_PATH, headers=self._session_headers(with_consumer=True), form=payload)
        return BrokerOrderResponse(order_id=str((body or {}).get("nOrdNo") or order_id), status="MODIFIED", raw=body)

    async def cancel_order(self, order_id: str) -> BrokerOrderResponse:
        self._order_gate()
        body = await self._send("POST", self.base_url + CANCEL_ORDER_PATH, headers=self._session_headers(with_consumer=True),
                                form={"on": str(order_id), "am": "NO"})
        return BrokerOrderResponse(order_id=str((body or {}).get("nOrdNo") or order_id), status="CANCELLED", raw=body)

    async def get_order_margin(self, order: BrokerOrderRequest) -> Optional[float]:
        """The margin Kotak Neo would block for `order` (check-margin; an account read, no order is placed)."""
        segment, token, _sym = await self.resolve(order.symbol, order.exchange)
        order_type = (order.order_type or "MARKET").upper()
        payload = {"exSeg": segment, "prc": f"{float(order.price or 0):.2f}", "prcTp": ORDER_TYPE_MAP.get(order_type, "L"),
                   "prod": (order.product or "MIS").upper(), "qty": str(int(order.quantity)), "tok": str(token),
                   "trnsTp": "B" if order.transaction_type == OrderSide.BUY else "S", "trgPrc": f"{float(order.trigger_price or 0):.2f}"}
        body = await self._send("POST", self.base_url + MARGIN_PATH, headers=self._session_headers(), form=payload)
        data = body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else body
        value = _num((data or {}).get("ordMrgn"), -1.0) if isinstance(data, dict) else -1.0
        return value if value >= 0 else None

    # --- books and account reads (work with the gates closed) ---------------------------------------------------------

    async def _order_book_raw(self) -> List[Dict[str, Any]]:
        body = await self._send("GET", self.base_url + ORDER_BOOK_PATH, headers=self._session_headers())
        rows = body.get("data") if isinstance(body, dict) else None
        return [r for r in rows or [] if isinstance(r, dict)]

    async def get_order_book(self) -> List[BrokerOrderStatus]:
        out: List[BrokerOrderStatus] = []
        for o in await self._order_book_raw():
            segment = str(o.get("exSeg") or "")
            out.append(BrokerOrderStatus(
                order_id=str(o.get("nOrdNo") or ""), symbol=platform_symbol(str(o.get("trdSym") or o.get("sym") or ""), segment),
                transaction_type=_side(o.get("trnsTp")), quantity=_num(o.get("qty")), filled_quantity=_num(o.get("fldQty")),
                order_type=ORDER_TYPE_BACK.get(str(o.get("prcTp") or ""), str(o.get("prcTp") or "")), status=_status(o.get("ordSt") or o.get("stat")),
                price=_num(o.get("prc")) or None, average_price=_num(o.get("avgPrc")) or None,
                placed_at=parse_broker_timestamp(_parse_time(o.get("ordDtTm") or o.get("hsUpTm"))), tag=(o.get("GuiOrdId") or None),
                product=str(o.get("prod") or "") or None, exchange=SEGMENT_EXCHANGE.get(segment)))
        return out

    async def get_trade_book(self) -> List[BrokerTradeEntry]:
        body = await self._send("GET", self.base_url + TRADE_BOOK_PATH, headers=self._session_headers())
        out: List[BrokerTradeEntry] = []
        for t in (body.get("data") if isinstance(body, dict) else None) or []:
            segment = str(t.get("exSeg") or "")
            when = _parse_time(f"{t.get('flDt')} {t.get('flTm')}") if t.get("flDt") and t.get("flTm") else _parse_time(t.get("exTm"))
            out.append(BrokerTradeEntry(trade_id=str(t.get("flId") or ""), order_id=str(t.get("nOrdNo") or ""),
                                        symbol=platform_symbol(str(t.get("trdSym") or ""), segment), transaction_type=_side(t.get("trnsTp")),
                                        quantity=_num(t.get("fldQty")), price=_num(t.get("avgPrc")), timestamp=when))
        return out

    async def get_positions(self) -> List[BrokerPosition]:
        """Net positions per the SDK's documented formula (units, not lots). While the market-data gate is closed the
        broker's last price is not read into ATP: `ltp` is 0 and `pnl` is the booked part only (a flat position)."""
        body = await self._send("GET", self.base_url + POSITIONS_PATH, headers=self._session_headers())
        show_prices = gates.market_data_open(self.name)
        out: List[BrokerPosition] = []
        for p in (body.get("data") if isinstance(body, dict) else None) or []:
            segment = str(p.get("exSeg") or "")
            buy_qty = _num(p.get("cfBuyQty")) + _num(p.get("flBuyQty"))
            sell_qty = _num(p.get("cfSellQty")) + _num(p.get("flSellQty"))
            buy_amt = _num(p.get("cfBuyAmt")) + _num(p.get("buyAmt"))
            sell_amt = _num(p.get("cfSellAmt")) + _num(p.get("sellAmt"))
            factor = (_num(p.get("multiplier"), 1.0) or 1.0) * ((_num(p.get("genNum"), 1.0) or 1.0) / (_num(p.get("genDen"), 1.0) or 1.0)) \
                * ((_num(p.get("prcNum"), 1.0) or 1.0) / (_num(p.get("prcDen"), 1.0) or 1.0))
            net = buy_qty - sell_qty
            if buy_qty > sell_qty:
                avg = buy_amt / (buy_qty * factor) if buy_qty else 0.0
            elif sell_qty > buy_qty:
                avg = sell_amt / (sell_qty * factor) if sell_qty else 0.0
            else:
                avg = 0.0
            ltp = _num(p.get("ltp")) if show_prices else 0.0
            booked = sell_amt - buy_amt
            if show_prices:
                pnl = booked + net * ltp * factor
            else:
                pnl = booked if net == 0 else 0.0
            precision = int(_num(p.get("precision"), 2.0))
            out.append(BrokerPosition(symbol=platform_symbol(str(p.get("trdSym") or p.get("sym") or ""), segment),
                                      exchange=SEGMENT_EXCHANGE.get(segment, "NSE"), product=str(p.get("prod") or "MIS"),
                                      quantity=net, average_price=round(avg, max(0, precision)), ltp=ltp, pnl=round(pnl, 2)))
        return out

    async def get_holdings(self) -> List[BrokerHolding]:
        body = await self._send("GET", self.base_url + HOLDINGS_PATH, headers=self._session_headers())
        show_prices = gates.market_data_open(self.name)
        out: List[BrokerHolding] = []
        for h in (body.get("data") if isinstance(body, dict) else None) or []:
            segment = str(h.get("exchangeSegment") or "nse_cm")
            qty, avg = _num(h.get("quantity")), _num(h.get("averagePrice"))
            close = _num(h.get("closingPrice")) if show_prices else 0.0
            out.append(BrokerHolding(symbol=platform_symbol(str(h.get("symbol") or h.get("displaySymbol") or ""), segment),
                                     exchange=SEGMENT_EXCHANGE.get(segment, "NSE"), quantity=qty, average_price=avg, ltp=close,
                                     pnl=round((close - avg) * qty, 2) if show_prices and close else 0.0))
        return out

    async def get_margins(self) -> MarginInfo:
        body = await self._send("POST", self.base_url + LIMITS_PATH, headers=self._session_headers(),
                                form={"seg": "ALL", "exch": "ALL", "prod": "ALL"})
        data = body if isinstance(body, dict) else {}
        net, used = _num(data.get("Net")), _num(data.get("MarginUsed"))
        return MarginInfo(available_cash=net, used_margin=used, available_margin=net, total_margin=net + used)
