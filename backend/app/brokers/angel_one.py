"""Phase AC: Angel One SmartAPI adapter.

Replaces the structural stub. SmartAPI is a plain JSON REST API: every call carries the app's
`X-PrivateKey` (the API key) plus client-identification headers, and every authenticated call a
`Authorization: Bearer <jwtToken>` obtained from `loginByPassword` with the client code, the
trading PIN and a TOTP. Responses are `{"status": true|false, "message": ..., "errorcode": ...,
"data": ...}`.

Endpoint paths, field names and enums follow the public SmartAPI documentation
(https://smartapi.angelbroking.com/docs) as of training cutoff; verify against the live docs before
production use, since broker APIs evolve. Nothing here is reached by a LIVE order until the tenant
has logged in through Settings and the platform's own risk path has approved the order.

Credentials (`BrokerCredentials`): `api_key` (SmartAPI app key), `client_id` (client code),
`pin` (trading PIN), `totp_secret` (the base32 secret from the SmartAPI TOTP enrolment - the
adapter generates the current code; a six-digit code is accepted as-is for a one-off login) and,
after a login, `access_token` (the jwtToken the platform stores encrypted and re-uses until the
broker's daily expiry). Optional extras: `local_ip`, `public_ip`, `mac` for the identification
headers.

Symbols: the platform speaks plain trading symbols (RELIANCE, NIFTY, BANKNIFTY). Angel One names
cash equities "RELIANCE-EQ" and indices "Nifty 50"/"Nifty Bank", and every order and quote needs
the numeric `symboltoken` from the scrip master. `_resolve` maps between the two through the cached
master and `INDEX_ALIASES`.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx

from app.brokers.base import BrokerInterface
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.models import (BrokerCredentials, BrokerHolding, BrokerOrderRequest, BrokerOrderResponse, BrokerOrderStatus,
                                BrokerPosition, BrokerProfile, BrokerTradeEntry, Instrument, MarginInfo, OptionChain, OptionChainRow, Quote)
from app.core.enums import OrderSide
from app.core.models import OHLCVBar

logger = logging.getLogger(__name__)

BASE_URL = "https://apiconnect.angelone.in"
SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
IST = timezone(timedelta(hours=5, minutes=30))

LOGIN_PATH = "/rest/auth/angelbroking/user/v1/loginByPassword"
LOGOUT_PATH = "/rest/secure/angelbroking/user/v1/logout"
PROFILE_PATH = "/rest/secure/angelbroking/user/v1/getProfile"
RMS_PATH = "/rest/secure/angelbroking/user/v1/getRMS"
PLACE_ORDER_PATH = "/rest/secure/angelbroking/order/v1/placeOrder"
MODIFY_ORDER_PATH = "/rest/secure/angelbroking/order/v1/modifyOrder"
CANCEL_ORDER_PATH = "/rest/secure/angelbroking/order/v1/cancelOrder"
ORDER_BOOK_PATH = "/rest/secure/angelbroking/order/v1/getOrderBook"
TRADE_BOOK_PATH = "/rest/secure/angelbroking/order/v1/getTradeBook"
POSITIONS_PATH = "/rest/secure/angelbroking/order/v1/getPosition"
HOLDINGS_PATH = "/rest/secure/angelbroking/portfolio/v1/getAllHolding"
QUOTE_PATH = "/rest/secure/angelbroking/market/v1/quote/"
CANDLES_PATH = "/rest/secure/angelbroking/historical/v1/getCandleData"

INTERVAL_MAP = {"1min": "ONE_MINUTE", "3min": "THREE_MINUTE", "5min": "FIVE_MINUTE", "10min": "TEN_MINUTE", "15min": "FIFTEEN_MINUTE",
                "30min": "THIRTY_MINUTE", "60min": "ONE_HOUR", "day": "ONE_DAY"}
# Platform order type -> (variety, ordertype). SL-M is the protective stop the worker places.
ORDER_TYPE_MAP = {"MARKET": ("NORMAL", "MARKET"), "LIMIT": ("NORMAL", "LIMIT"), "SL": ("STOPLOSS", "STOPLOSS_LIMIT"), "SL-M": ("STOPLOSS", "STOPLOSS_MARKET")}
PRODUCT_MAP = {"MIS": "INTRADAY", "CNC": "DELIVERY", "NRML": "CARRYFORWARD", "MTF": "MARGIN", "BO": "BO"}
PRODUCT_BACK = {v: k for k, v in PRODUCT_MAP.items()}
INDEX_ALIASES = {"NIFTY": "Nifty 50", "BANKNIFTY": "Nifty Bank", "FINNIFTY": "Nifty Fin Service", "MIDCPNIFTY": "NIFTY MID SELECT",
                 "SENSEX": "SENSEX", "BANKEX": "BANKEX", "NIFTY 50": "Nifty 50", "NIFTY BANK": "Nifty Bank"}
QUOTE_BATCH = 50
INSTRUMENT_CACHE_TTL_SECONDS = 6 * 60 * 60
AUTH_ERROR_CODES = ("AG8001", "AG8002", "AG8003", "AB8050", "AB8051", "AB1010")
_BASE32 = re.compile(r"^[A-Z2-7]{16,}=*$")


def _totp_code(secret_or_code: Optional[str]) -> str:
    value = (secret_or_code or "").strip().replace(" ", "")
    if value.isdigit() and len(value) in (6, 8):
        return value
    if _BASE32.match(value.upper()):
        import pyotp
        return pyotp.TOTP(value.upper()).now()
    return value


def _parse_expiry(raw: Optional[str]) -> Optional[str]:
    """Angel writes expiries as 26SEP2026 in the scrip master."""
    if not raw:
        return None
    for fmt in ("%d%b%Y", "%Y-%m-%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(raw.strip().upper() if fmt == "%d%b%Y" else raw.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_time(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%H:%M:%S"):
        try:
            parsed = datetime.strptime(raw, fmt)
            if fmt == "%H:%M:%S":
                today = datetime.now(IST)
                parsed = parsed.replace(year=today.year, month=today.month, day=today.day)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


class AngelOneBroker(BrokerInterface):
    name = "angel_one"
    BASE_URL = BASE_URL
    max_tag_length = 20

    def __init__(self, credentials: BrokerCredentials, client: Optional[httpx.AsyncClient] = None) -> None:
        if not credentials.api_key:
            raise BrokerAuthenticationError("Angel One adapter requires credentials.api_key (SmartAPI app key)")
        if not (credentials.access_token or (credentials.client_id and credentials.pin)):
            raise BrokerAuthenticationError("Angel One adapter requires client_id + pin (+ totp_secret) to log in, or a stored access_token")
        self.credentials = credentials
        self._client = client or httpx.AsyncClient(base_url=self.BASE_URL, timeout=15.0)
        self._access_token: Optional[str] = credentials.access_token
        self._refresh_token: Optional[str] = None
        self._feed_token: Optional[str] = None
        self._profile: Optional[BrokerProfile] = None
        self._master: Optional[Tuple[float, List[dict]]] = None
        self._instruments_cache: Dict[str, Tuple[float, List[Instrument]]] = {}
        # (exchange, platform symbol) -> (symboltoken, Angel trading symbol)
        self._symbol_map: Dict[Tuple[str, str], Tuple[str, str]] = {}

    # --- transport ------------------------------------------------------------------------------
    def _headers(self, authenticated: bool) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json", "Accept": "application/json", "X-UserType": "USER", "X-SourceID": "WEB",
            "X-ClientLocalIP": getattr(self.credentials, "local_ip", None) or "127.0.0.1",
            "X-ClientPublicIP": getattr(self.credentials, "public_ip", None) or "127.0.0.1",
            "X-MACAddress": getattr(self.credentials, "mac", None) or "00:00:00:00:00:00",
            "X-PrivateKey": self.credentials.api_key or "",
        }
        if authenticated:
            if not self._access_token:
                raise BrokerAuthenticationError("Not authenticated - call authenticate() first")
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    async def _request(self, method: str, path: str, *, json_body: Optional[dict] = None, authenticated: bool = True):
        response = await self._client.request(method, path, json=json_body, headers=self._headers(authenticated))
        try:
            body = response.json()
        except ValueError as exc:
            raise BrokerAPIError(f"Non-JSON response from Angel One: {response.text[:200]}", response.status_code) from exc
        if response.status_code in (401, 403):
            raise BrokerAuthenticationError(body.get("message") or "Angel One rejected the session token")
        if response.status_code >= 400 or body.get("status") is False or str(body.get("status", "true")).lower() == "false":
            code = str(body.get("errorcode") or "")
            message = body.get("message") or "Angel One API error"
            if code in AUTH_ERROR_CODES:
                raise BrokerAuthenticationError(f"{message} ({code})")
            raise BrokerAPIError(f"{message} ({code})" if code else message, response.status_code, response.text)
        return body.get("data")

    # --- session ---------------------------------------------------------------------------------
    async def authenticate(self) -> BrokerProfile:
        if self.credentials.client_id and self.credentials.pin:
            payload = {"clientcode": self.credentials.client_id, "password": self.credentials.pin, "totp": _totp_code(self.credentials.totp_secret), "state": "atp"}
            data = await self._request("POST", LOGIN_PATH, json_body=payload, authenticated=False) or {}
            token = data.get("jwtToken")
            if not token:
                raise BrokerAuthenticationError("Angel One login returned no jwtToken")
            self._access_token = token
            self._refresh_token = data.get("refreshToken")
            self._feed_token = data.get("feedToken")
        elif not self._access_token:
            raise BrokerAuthenticationError("No Angel One session: provide client_id, pin and totp_secret")
        return await self.get_profile()

    async def get_profile(self) -> BrokerProfile:
        data = await self._request("GET", PROFILE_PATH) or {}
        self._profile = BrokerProfile(broker=self.name, user_id=str(data.get("clientcode") or self.credentials.client_id or ""), name=data.get("name"), email=data.get("email") or None)
        return self._profile

    async def disconnect(self) -> None:
        try:
            if self._access_token and self.credentials.client_id:
                await self._request("POST", LOGOUT_PATH, json_body={"clientcode": self.credentials.client_id})
        except (BrokerAPIError, BrokerAuthenticationError, httpx.HTTPError) as exc:
            logger.info("Angel One logout call failed: %s", exc)
        finally:
            self._access_token = None

    # --- instruments -----------------------------------------------------------------------------
    async def _scrip_master(self) -> List[dict]:
        if self._master and (time.monotonic() - self._master[0]) < INSTRUMENT_CACHE_TTL_SECONDS:
            return self._master[1]
        response = await self._client.get(SCRIP_MASTER_URL)
        if response.status_code >= 400:
            raise BrokerAPIError("Failed to fetch the Angel One scrip master", response.status_code, response.text)
        rows = json.loads(response.content)
        self._master = (time.monotonic(), rows)
        return rows

    @staticmethod
    def _platform_symbol(row: dict) -> str:
        symbol = str(row.get("symbol") or "").strip()
        if row.get("exch_seg") in ("NSE", "BSE") and symbol.upper().endswith("-EQ"):
            return symbol[:-3].upper()
        return symbol.upper()

    async def get_instruments(self, exchange: Optional[str] = None) -> List[Instrument]:
        exchange = (exchange or "NSE").upper()
        cached = self._instruments_cache.get(exchange)
        if cached and (time.monotonic() - cached[0]) < INSTRUMENT_CACHE_TTL_SECONDS:
            return cached[1]
        out: List[Instrument] = []
        for row in await self._scrip_master():
            if str(row.get("exch_seg", "")).upper() != exchange:
                continue
            symbol = self._platform_symbol(row)
            token = str(row.get("token"))
            try:
                strike = float(row.get("strike") or 0) / 100.0
            except (TypeError, ValueError):
                strike = 0.0
            try:
                tick = float(row.get("tick_size") or 5) / 100.0
            except (TypeError, ValueError):
                tick = 0.05
            out.append(Instrument(instrument_token=token, exchange=exchange, tradingsymbol=symbol, name=(row.get("name") or None),
                                  segment=row.get("exch_seg"), instrument_type=(row.get("instrumenttype") or None) or ("EQ" if exchange in ("NSE", "BSE") else None),
                                  lot_size=float(row.get("lotsize") or 1), tick_size=tick or 0.05, expiry=_parse_expiry(row.get("expiry")),
                                  strike=strike if strike > 0 else None))
            self._symbol_map[(exchange, symbol)] = (token, str(row.get("symbol") or symbol))
            name = str(row.get("name") or "").upper()
            if name and (exchange, name) not in self._symbol_map and not row.get("instrumenttype"):
                self._symbol_map[(exchange, name)] = (token, str(row.get("symbol") or symbol))
        self._instruments_cache[exchange] = (time.monotonic(), out)
        return out

    async def _resolve(self, symbol: str, exchange: str) -> Tuple[str, str]:
        """Platform symbol -> (symboltoken, Angel trading symbol)."""
        exchange = (exchange or "NSE").upper()
        symbol = symbol.strip()
        if ":" in symbol:
            exchange, symbol = symbol.split(":", 1)
            exchange = exchange.upper()
        if (exchange, symbol.upper()) not in self._symbol_map:
            await self.get_instruments(exchange)
        for candidate in (symbol.upper(), INDEX_ALIASES.get(symbol.upper(), "").upper()):
            if candidate and (exchange, candidate) in self._symbol_map:
                return self._symbol_map[(exchange, candidate)]
        alias = INDEX_ALIASES.get(symbol.upper())
        if alias:
            for (exch, sym), value in self._symbol_map.items():
                if exch == exchange and (value[1].upper() == alias.upper() or sym == alias.upper()):
                    return value
        raise BrokerAPIError(f"Instrument {exchange}:{symbol} not found in the Angel One scrip master")

    # --- quotes ----------------------------------------------------------------------------------
    async def _quotes(self, tokens_by_exchange: Dict[str, List[str]], mode: str = "FULL") -> List[dict]:
        fetched: List[dict] = []
        for exchange, tokens in tokens_by_exchange.items():
            for i in range(0, len(tokens), QUOTE_BATCH):
                data = await self._request("POST", QUOTE_PATH, json_body={"mode": mode, "exchangeTokens": {exchange: tokens[i:i + QUOTE_BATCH]}}) or {}
                fetched.extend(data.get("fetched") or [])
        return fetched

    def _quote_from(self, entry: dict, symbol: str) -> Quote:
        depth = entry.get("depth") or {}
        buy = (depth.get("buy") or [{}])[0] or {}
        sell = (depth.get("sell") or [{}])[0] or {}
        return Quote(symbol=symbol, ltp=float(entry.get("ltp") or 0), open=float(entry.get("open") or 0), high=float(entry.get("high") or 0),
                     low=float(entry.get("low") or 0), close=float(entry.get("close") or 0), volume=float(entry.get("tradeVolume") or 0),
                     oi=float(entry["opnInterest"]) if entry.get("opnInterest") not in (None, "") else None,
                     bid=float(buy["price"]) if buy.get("price") is not None else None, ask=float(sell["price"]) if sell.get("price") is not None else None,
                     bid_qty=float(buy["quantity"]) if buy.get("quantity") is not None else None, ask_qty=float(sell["quantity"]) if sell.get("quantity") is not None else None,
                     timestamp=_parse_time(entry.get("exchTradeTime") or entry.get("exchFeedTime")))

    async def _resolve_many(self, symbols: List[str]) -> Tuple[Dict[str, List[str]], Dict[Tuple[str, str], str]]:
        by_exchange: Dict[str, List[str]] = {}
        back: Dict[Tuple[str, str], str] = {}
        for key in symbols:
            exchange, plain = (key.split(":", 1) if ":" in key else ("NSE", key))
            token, _raw = await self._resolve(plain, exchange)
            by_exchange.setdefault(exchange.upper(), []).append(token)
            back[(exchange.upper(), token)] = key
        return by_exchange, back

    async def get_ltp(self, symbols: List[str]) -> Dict[str, float]:
        by_exchange, back = await self._resolve_many(symbols)
        out: Dict[str, float] = {}
        for entry in await self._quotes(by_exchange, mode="LTP"):
            key = back.get((str(entry.get("exchange", "")).upper(), str(entry.get("symbolToken"))))
            if key is not None:
                out[key] = float(entry.get("ltp") or 0)
        return out

    async def get_quote(self, symbols: List[str]) -> Dict[str, Quote]:
        by_exchange, back = await self._resolve_many(symbols)
        out: Dict[str, Quote] = {}
        for entry in await self._quotes(by_exchange, mode="FULL"):
            key = back.get((str(entry.get("exchange", "")).upper(), str(entry.get("symbolToken"))))
            if key is not None:
                out[key] = self._quote_from(entry, key)
        return out

    async def get_quote_for_symbol(self, symbol: str, exchange: str = "NSE") -> Optional[Quote]:
        quotes = await self.get_quote([f"{exchange}:{symbol}"])
        quote = quotes.get(f"{exchange}:{symbol}")
        if quote is None:
            raise BrokerAPIError(f"No quote returned for {exchange}:{symbol}")
        return quote

    async def get_ltp_for_symbol(self, symbol: str, exchange: str = "NSE") -> float:
        prices = await self.get_ltp([f"{exchange}:{symbol}"])
        if not prices:
            raise BrokerAPIError(f"No LTP returned for {exchange}:{symbol}")
        return float(next(iter(prices.values())))

    # --- candles ---------------------------------------------------------------------------------
    async def get_historical_data(self, symbol: str, exchange: str, interval: str, from_date: datetime, to_date: datetime) -> List[OHLCVBar]:
        token, _raw = await self._resolve(symbol, exchange)
        api_interval = INTERVAL_MAP.get(interval, interval)

        def fmt(dt: datetime) -> str:
            dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M")
        payload = {"exchange": exchange.upper(), "symboltoken": token, "interval": api_interval, "fromdate": fmt(from_date), "todate": fmt(to_date)}
        data = await self._request("POST", CANDLES_PATH, json_body=payload) or []
        bars: List[OHLCVBar] = []
        for row in data:
            try:
                ts = datetime.fromisoformat(str(row[0]))
            except ValueError:
                continue
            bars.append(OHLCVBar(timestamp=ts, open=float(row[1]), high=float(row[2]), low=float(row[3]), close=float(row[4]), volume=float(row[5]) if len(row) > 5 else 0.0))
        return bars

    # --- option chain ----------------------------------------------------------------------------
    async def get_option_chain(self, underlying: str, expiry: Optional[date] = None) -> OptionChain:
        """SmartAPI has no chain endpoint; the chain is the NFO scrip master rows for the underlying
        at the chosen expiry, priced with the quote API (FULL mode, batches of 50) around the money."""
        name = underlying.upper()
        contracts = [i for i in await self.get_instruments("NFO") if (i.name or "").upper() == name and (i.instrument_type or "").startswith("OPT") and i.expiry]
        if not contracts:
            raise BrokerAPIError(f"No option contracts for {underlying} in the Angel One scrip master")
        expiries = sorted({c.expiry for c in contracts})
        today = date.today().isoformat()
        chosen = expiry.isoformat() if expiry else next((e for e in expiries if e >= today), expiries[-1])
        legs = [c for c in contracts if c.expiry == chosen]
        try:
            underlying_ltp: Optional[float] = await self.get_ltp_for_symbol(underlying, "NSE")
        except (BrokerAPIError, BrokerAuthenticationError):
            underlying_ltp = None
        strikes = sorted({c.strike for c in legs if c.strike})
        if underlying_ltp and len(strikes) > 40:
            centre = min(range(len(strikes)), key=lambda i: abs(strikes[i] - underlying_ltp))
            window = set(strikes[max(0, centre - 20):centre + 21])
            legs = [c for c in legs if c.strike in window]
        tokens = [c.instrument_token for c in legs]
        by_token = {c.instrument_token: c for c in legs}
        rows: Dict[float, OptionChainRow] = {}
        for entry in await self._quotes({"NFO": tokens}, mode="FULL"):
            contract = by_token.get(str(entry.get("symbolToken")))
            if contract is None or not contract.strike:
                continue
            row = rows.setdefault(contract.strike, OptionChainRow(strike=contract.strike))
            quote = self._quote_from(entry, contract.tradingsymbol)
            right = "CE" if contract.tradingsymbol.endswith("CE") else "PE"
            if right == "CE":
                row.call_ltp, row.call_oi, row.call_volume, row.call_bid, row.call_ask = quote.ltp, quote.oi, quote.volume, quote.bid, quote.ask
            else:
                row.put_ltp, row.put_oi, row.put_volume, row.put_bid, row.put_ask = quote.ltp, quote.oi, quote.volume, quote.bid, quote.ask
        return OptionChain(underlying=underlying, expiry=chosen, underlying_ltp=underlying_ltp, rows=[rows[k] for k in sorted(rows)])

    # --- orders ----------------------------------------------------------------------------------
    async def place_order(self, order: BrokerOrderRequest) -> BrokerOrderResponse:
        token, angel_symbol = await self._resolve(order.symbol, order.exchange)
        variety, ordertype = ORDER_TYPE_MAP.get(order.order_type.upper(), ("NORMAL", order.order_type.upper()))
        payload = {
            "variety": variety, "tradingsymbol": angel_symbol, "symboltoken": token, "transactiontype": order.transaction_type.value,
            "exchange": order.exchange.upper(), "ordertype": ordertype, "producttype": PRODUCT_MAP.get(order.product.upper(), order.product.upper()),
            "duration": order.validity or "DAY", "price": str(order.price or 0), "squareoff": "0", "stoploss": "0",
            "quantity": str(int(order.quantity)), "triggerprice": str(order.trigger_price or 0),
        }
        if order.tag:
            payload["ordertag"] = order.tag[: self.max_tag_length]
        data = await self._request("POST", PLACE_ORDER_PATH, json_body=payload) or {}
        order_id = str(data.get("orderid") or data.get("uniqueorderid") or "")
        if not order_id:
            raise BrokerAPIError("Angel One returned no order id", 200, json.dumps(data))
        return BrokerOrderResponse(order_id=order_id, status="OPEN", raw=data)

    async def modify_order(self, order_id: str, quantity: Optional[float] = None, price: Optional[float] = None,
                           trigger_price: Optional[float] = None, order_type: Optional[str] = None) -> BrokerOrderResponse:
        current = next((o for o in await self._order_book_raw() if str(o.get("orderid")) == str(order_id)), None)
        if current is None:
            raise BrokerAPIError(f"Order {order_id} not found in the Angel One order book")
        variety, ordertype = ORDER_TYPE_MAP.get((order_type or "").upper(), (current.get("variety") or "NORMAL", current.get("ordertype") or "MARKET"))
        payload = {
            "variety": variety, "orderid": str(order_id), "ordertype": ordertype, "producttype": current.get("producttype"), "duration": current.get("duration") or "DAY",
            "price": str(price if price is not None else current.get("price") or 0), "quantity": str(int(quantity if quantity is not None else float(current.get("quantity") or 0))),
            "tradingsymbol": current.get("tradingsymbol"), "symboltoken": current.get("symboltoken"), "exchange": current.get("exchange"),
            "triggerprice": str(trigger_price if trigger_price is not None else current.get("triggerprice") or 0),
        }
        data = await self._request("POST", MODIFY_ORDER_PATH, json_body=payload) or {}
        return BrokerOrderResponse(order_id=str(data.get("orderid") or order_id), status="MODIFIED", raw=data)

    async def cancel_order(self, order_id: str) -> BrokerOrderResponse:
        current = next((o for o in await self._order_book_raw() if str(o.get("orderid")) == str(order_id)), None)
        variety = (current or {}).get("variety") or "NORMAL"
        data = await self._request("POST", CANCEL_ORDER_PATH, json_body={"variety": variety, "orderid": str(order_id)}) or {}
        return BrokerOrderResponse(order_id=str(data.get("orderid") or order_id), status="CANCELLED", raw=data)

    async def _order_book_raw(self) -> List[dict]:
        return list(await self._request("GET", ORDER_BOOK_PATH) or [])

    @staticmethod
    def _status(raw: str) -> str:
        value = (raw or "").lower()
        if value in ("complete", "completed", "filled"):
            return "COMPLETE"
        if value in ("cancelled", "canceled"):
            return "CANCELLED"
        if value == "rejected":
            return "REJECTED"
        if "trigger" in value:
            return "TRIGGER_PENDING"
        return "OPEN"

    async def get_order_book(self) -> List[BrokerOrderStatus]:
        out = []
        for o in await self._order_book_raw():
            out.append(BrokerOrderStatus(order_id=str(o.get("orderid")), symbol=self._platform_symbol({"symbol": o.get("tradingsymbol"), "exch_seg": o.get("exchange")}),
                                         transaction_type=OrderSide(str(o.get("transactiontype", "BUY")).upper()), quantity=float(o.get("quantity") or 0),
                                         filled_quantity=float(o.get("filledshares") or 0), order_type=str(o.get("ordertype") or ""), status=self._status(str(o.get("status") or o.get("orderstatus") or "")),
                                         price=float(o.get("price") or 0) or None, average_price=float(o.get("averageprice") or 0) or None, placed_at=_parse_time(o.get("updatetime"))))
        return out

    async def get_trade_book(self) -> List[BrokerTradeEntry]:
        out = []
        for t in await self._request("GET", TRADE_BOOK_PATH) or []:
            out.append(BrokerTradeEntry(trade_id=str(t.get("fillid") or t.get("tradeid") or ""), order_id=str(t.get("orderid") or ""),
                                        symbol=self._platform_symbol({"symbol": t.get("tradingsymbol"), "exch_seg": t.get("exchange")}),
                                        transaction_type=OrderSide(str(t.get("transactiontype", "BUY")).upper()), quantity=float(t.get("fillsize") or 0),
                                        price=float(t.get("fillprice") or 0), timestamp=_parse_time(t.get("filltime"))))
        return out

    async def get_positions(self) -> List[BrokerPosition]:
        out = []
        for p in await self._request("GET", POSITIONS_PATH) or []:
            qty = float(p.get("netqty") or 0)
            avg = p.get("avgnetprice") or p.get("netprice") or (p.get("buyavgprice") if qty >= 0 else p.get("sellavgprice")) or 0
            out.append(BrokerPosition(symbol=self._platform_symbol({"symbol": p.get("tradingsymbol"), "exch_seg": p.get("exchange")}), exchange=str(p.get("exchange") or "NSE"),
                                      product=PRODUCT_BACK.get(str(p.get("producttype") or ""), str(p.get("producttype") or "MIS")), quantity=qty,
                                      average_price=float(avg or 0), ltp=float(p.get("ltp") or 0), pnl=float(p.get("unrealised") or p.get("pnl") or 0)))
        return out

    async def get_holdings(self) -> List[BrokerHolding]:
        data = await self._request("GET", HOLDINGS_PATH) or []
        rows = data.get("holdings", []) if isinstance(data, dict) else data
        return [BrokerHolding(symbol=self._platform_symbol({"symbol": h.get("tradingsymbol"), "exch_seg": h.get("exchange")}), exchange=str(h.get("exchange") or "NSE"),
                              quantity=float(h.get("quantity") or 0), average_price=float(h.get("averageprice") or 0), ltp=float(h.get("ltp") or 0),
                              pnl=float(h.get("profitandloss") or 0)) for h in rows]

    async def get_margins(self) -> MarginInfo:
        data = await self._request("GET", RMS_PATH) or {}

        def f(key: str) -> float:
            try:
                return float(data.get(key) or 0)
            except (TypeError, ValueError):
                return 0.0
        net, used = f("net"), f("utiliseddebits")
        return MarginInfo(available_cash=f("availablecash"), used_margin=used, available_margin=net, total_margin=net + used)
