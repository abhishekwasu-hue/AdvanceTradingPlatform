"""Phase AE: Fyers API v3 adapter.

Replaces the structural stub. Fyers is plain JSON REST split across a trading host
(`/api/v3`: profile, funds, orders, positions, holdings) and a data host (`/data`: quotes,
history, option chain). Every authenticated call carries `Authorization: <app_id>:<access_token>`.
Responses are `{"s": "ok"|"error", "code": ..., "message": ..., ...}`.

Login is an auth-code exchange like Zerodha's: the user completes the Fyers login page once a day
and pastes the returned code into Settings as `request_token`; `authenticate()` exchanges it with
`validate-authcode` (`appIdHash = sha256("<app_id>:<secret>")`) for the day's access token, which the
platform stores encrypted and re-uses. Credentials: `api_key` (App ID, e.g. `ABCD1234-100`),
`api_secret` (secret key), `request_token` (auth code) or `access_token`.

Symbols: the platform speaks plain trading symbols; Fyers writes `NSE:SBIN-EQ`, indices
`NSE:NIFTY50-INDEX` / `NSE:NIFTYBANK-INDEX`, derivatives `NSE:NIFTY26OCT26000CE`. `_ticker` maps
between the two with `INDEX_ALIASES` and the public symbol master (CSV, no header).

Endpoint paths, field names and enums follow the public docs (https://myapi.fyers.in/docsv3) as
of training cutoff; verify against the live docs before production use. Nothing here is reached by a
LIVE order until the tenant has logged in and the platform's risk path has approved the order.
"""
from __future__ import annotations

import csv
import hashlib
import io
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

API_URL = "https://api-t1.fyers.in/api/v3"
DATA_URL = "https://api-t1.fyers.in/data"
SYMBOL_MASTER_URL = "https://public.fyers.in/sym_details/{segment}.csv"     # NSE_CM, NSE_FO, BSE_CM, MCX_COM
IST = timezone(timedelta(hours=5, minutes=30))

RESOLUTION_MAP = {"1min": "1", "3min": "3", "5min": "5", "10min": "10", "15min": "15", "30min": "30", "60min": "60", "day": "D"}
ORDER_TYPE_MAP = {"LIMIT": 1, "MARKET": 2, "SL-M": 3, "SL": 4}
ORDER_TYPE_BACK = {1: "LIMIT", 2: "MARKET", 3: "SL-M", 4: "SL"}
PRODUCT_MAP = {"MIS": "INTRADAY", "CNC": "CNC", "NRML": "MARGIN", "BO": "BO", "CO": "CO"}
PRODUCT_BACK = {v: k for k, v in PRODUCT_MAP.items()}
STATUS_MAP = {1: "CANCELLED", 2: "COMPLETE", 4: "OPEN", 5: "REJECTED", 6: "OPEN", 7: "EXPIRED"}
INDEX_ALIASES = {"NIFTY": "NSE:NIFTY50-INDEX", "NIFTY 50": "NSE:NIFTY50-INDEX", "BANKNIFTY": "NSE:NIFTYBANK-INDEX", "NIFTY BANK": "NSE:NIFTYBANK-INDEX",
                 "FINNIFTY": "NSE:FINNIFTY-INDEX", "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX", "SENSEX": "BSE:SENSEX-INDEX", "BANKEX": "BSE:BANKEX-INDEX"}
SEGMENTS = {"NSE": "NSE_CM", "NFO": "NSE_FO", "BSE": "BSE_CM", "BFO": "BSE_FO", "MCX": "MCX_COM"}
AUTH_ERROR_CODES = {-8, -15, -16, -17, -50, -300}
QUOTE_BATCH = 50
MASTER_CACHE_TTL_SECONDS = 6 * 60 * 60
FUND_AVAILABLE, FUND_TOTAL, FUND_UTILISED = 10, 1, 2


def _parse_time(raw) -> Optional[datetime]:
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.isdigit()):
        return datetime.fromtimestamp(int(raw), tz=timezone.utc).astimezone(IST)
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(raw), fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


_TICKER_OPTION_RE = re.compile(r"^(?P<u>[A-Z0-9][A-Z0-9&-]*?)(?P<when>\d{2}(?:[A-Z]{3}|[1-9OND]\d{2}))(?P<strike>\d+(?:\.\d+)?)(?P<right>CE|PE)$")
_TICKER_FUTURE_RE = re.compile(r"^(?P<u>[A-Z0-9][A-Z0-9&-]*?)(?P<when>\d{2}(?:[A-Z]{3}|[1-9OND]\d{2}))FUT$")


def _number(value: str) -> Optional[float]:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _contract_fields(row: List[str], ticker: str) -> Tuple[Optional[float], str, Optional[str]]:
    """(strike, option type, underlying) of one symbol-master row.

    The public docs list the F&O master's columns, but their exact order around the underlying
    (`Underlying symbol`, `Underlying scrip code`, `Strike price`, `Option type`) has moved between
    revisions, and an off-by-one there is silent and expensive: with `Underlying scrip code` taken
    for the strike every NIFTY option would carry strike 26000 (the index's scrip code) and no
    `CE`/`PE` at all, so no option could ever be resolved. The ticker itself (`NSE:NIFTY26OCT26000CE`,
    weekly `NSE:NIFTY25O0726000CE`, `NSE:NIFTY26OCTFUT`) is the documented, stable spelling, so it is
    read first and the columns only confirm or fill in what it cannot say.
    """
    body = (ticker.split(":", 1)[1] if ":" in ticker else ticker).strip().upper()
    cells = [c.strip() for c in row]
    option_type = ""
    strike: Optional[float] = None
    underlying: Optional[str] = None
    m = _TICKER_OPTION_RE.match(body)
    if m:
        option_type, strike, underlying = m.group("right"), _number(m.group("strike")), m.group("u").rstrip("-")
    else:
        m = _TICKER_FUTURE_RE.match(body)
        if m:
            underlying = m.group("u").rstrip("-")
    # Columns: the option type is whichever of the documented positions reads CE/PE; the strike is the
    # positive number right before it. Used when the ticker did not parse, and to catch a decimal strike
    # the ticker rounds.
    for i in (16, 15, 14):
        if i < len(cells) and cells[i].upper() in ("CE", "PE"):
            column_type = cells[i].upper()
            column_strike = _number(cells[i - 1]) if i - 1 < len(cells) else None
            option_type = option_type or column_type
            if column_strike is not None and (strike is None or abs(column_strike - strike) < 1.0):
                strike = column_strike
            break
    # Underlying: the first alphabetic (non-numeric) cell in the documented positions beats the ticker prefix.
    for i in (13, 14):
        if i < len(cells) and cells[i] and not cells[i].replace(".", "").replace("-", "").isdigit() and cells[i].upper() not in ("CE", "PE"):
            underlying = cells[i].upper()
            break
    if option_type not in ("CE", "PE"):
        strike = None
    return strike, option_type, underlying


# Fyers `segment` codes on positions / orders: 10 capital market, 11 F&O, 12 currency, 20 commodity.
_SEGMENT_EXCHANGE = {10: {"NSE": "NSE", "BSE": "BSE"}, 11: {"NSE": "NFO", "BSE": "BFO"}, 12: {"NSE": "CDS", "BSE": "BCD"}, 20: {"MCX": "MCX", "NSE": "NSE"}}


def _exchange_of(ticker: str, segment=None) -> str:
    """Fyers spells NSE equities and NSE derivatives with the same `NSE:` prefix; the platform (and the
    contract-symbol translator that restores positions to the platform spelling) tells them apart by
    exchange, NSE vs NFO. The segment code decides when present; the ticker's contract shape otherwise."""
    prefix = ticker.split(":", 1)[0].upper() if ":" in ticker else "NSE"
    body = (ticker.split(":", 1)[1] if ":" in ticker else ticker).strip().upper()
    try:
        code = int(segment) if segment not in (None, "") else None
    except (TypeError, ValueError):
        code = None
    if code in _SEGMENT_EXCHANGE:
        return _SEGMENT_EXCHANGE[code].get(prefix, prefix)
    if _TICKER_OPTION_RE.match(body) or _TICKER_FUTURE_RE.match(body):
        return {"NSE": "NFO", "BSE": "BFO"}.get(prefix, prefix)
    return prefix


class FyersBroker(BrokerInterface):
    name = "fyers"
    BASE_URL = API_URL
    max_tag_length = 20

    def __init__(self, credentials: BrokerCredentials, client: Optional[httpx.AsyncClient] = None) -> None:
        if not credentials.api_key:
            raise BrokerAuthenticationError("Fyers adapter requires credentials.api_key (the App ID)")
        if not (credentials.access_token or (credentials.request_token and credentials.api_secret)):
            raise BrokerAuthenticationError("Fyers adapter requires a stored access_token, or request_token (auth code) + api_secret to exchange for one")
        self.credentials = credentials
        self._client = client or httpx.AsyncClient(timeout=15.0)
        self._access_token: Optional[str] = credentials.access_token
        self._profile: Optional[BrokerProfile] = None
        self._masters: Dict[str, Tuple[float, List[Instrument]]] = {}
        # (exchange, platform symbol) -> Fyers ticker such as "NSE:SBIN-EQ"
        self._tickers: Dict[Tuple[str, str], str] = {}

    # --- transport ------------------------------------------------------------------------------
    def _headers(self, authenticated: bool) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if authenticated:
            if not self._access_token:
                raise BrokerAuthenticationError("Not authenticated - call authenticate() first")
            headers["Authorization"] = f"{self.credentials.api_key}:{self._access_token}"
        return headers

    async def _request(self, method: str, url: str, *, params: Optional[dict] = None, json_body: Optional[dict] = None, authenticated: bool = True) -> dict:
        response = await self._client.request(method, url, params=params, json=json_body, headers=self._headers(authenticated))
        try:
            body = response.json()
        except ValueError as exc:
            raise BrokerAPIError(f"Non-JSON response from Fyers: {response.text[:200]}", response.status_code) from exc
        if response.status_code in (401, 403):
            raise BrokerAuthenticationError(body.get("message") or "Fyers rejected the access token")
        if response.status_code >= 400 or body.get("s") == "error":
            code = body.get("code")
            message = body.get("message") or "Fyers API error"
            try:
                numeric = int(code)
            except (TypeError, ValueError):
                numeric = None
            if numeric in AUTH_ERROR_CODES or "token" in str(message).lower():
                raise BrokerAuthenticationError(f"{message} ({code})")
            raise BrokerAPIError(f"{message} ({code})" if code is not None else message, response.status_code, response.text)
        return body

    # --- session ---------------------------------------------------------------------------------
    async def authenticate(self) -> BrokerProfile:
        if not self._access_token:
            app_hash = hashlib.sha256(f"{self.credentials.api_key}:{self.credentials.api_secret}".encode()).hexdigest()
            body = await self._request("POST", f"{API_URL}/validate-authcode",
                                       json_body={"grant_type": "authorization_code", "appIdHash": app_hash, "code": self.credentials.request_token}, authenticated=False)
            token = body.get("access_token")
            if not token:
                raise BrokerAuthenticationError("Fyers auth-code exchange returned no access_token")
            self._access_token = token
        return await self.get_profile()

    async def get_profile(self) -> BrokerProfile:
        data = (await self._request("GET", f"{API_URL}/profile")).get("data") or {}
        self._profile = BrokerProfile(broker=self.name, user_id=str(data.get("fy_id") or ""), name=data.get("name") or data.get("display_name"), email=data.get("email_id") or None)
        return self._profile

    # --- instruments -----------------------------------------------------------------------------
    @staticmethod
    def _platform_symbol(ticker: str) -> str:
        """`NSE:SBIN-EQ` -> `SBIN`; `NSE:NIFTY50-INDEX` -> `NIFTY`; `NSE:NIFTY26OCT26000CE` -> `NIFTY26OCT26000CE`."""
        body = ticker.split(":", 1)[1] if ":" in ticker else ticker
        for alias, full in INDEX_ALIASES.items():
            if full == ticker.upper() and " " not in alias:
                return alias
        if body.upper().endswith("-EQ"):
            return body[:-3].upper()
        if body.upper().endswith("-INDEX"):
            return body[:-6].upper()
        return body.upper()

    async def get_instruments(self, exchange: Optional[str] = None) -> List[Instrument]:
        exchange = (exchange or "NSE").upper()
        cached = self._masters.get(exchange)
        if cached and (time.monotonic() - cached[0]) < MASTER_CACHE_TTL_SECONDS:
            return cached[1]
        segment = SEGMENTS.get(exchange, exchange)
        response = await self._client.get(SYMBOL_MASTER_URL.format(segment=segment))
        if response.status_code >= 400:
            raise BrokerAPIError(f"Failed to fetch the Fyers symbol master for {exchange}", response.status_code, response.text)
        out: List[Instrument] = []
        for row in csv.reader(io.StringIO(response.text)):
            if len(row) < 10 or not row[9]:
                continue
            ticker = row[9].strip()
            symbol = self._platform_symbol(ticker)
            expiry = None
            try:
                if row[8] and int(float(row[8])) > 0:
                    expiry = datetime.fromtimestamp(int(float(row[8])), tz=IST).date().isoformat()
            except (TypeError, ValueError):
                expiry = None
            strike, option_type, underlying = _contract_fields(row, ticker)
            instrument_type = option_type if option_type in ("CE", "PE") else ("FUT" if expiry and exchange in ("NFO", "BFO", "MCX") else ("INDEX" if ticker.upper().endswith("-INDEX") else "EQ"))
            try:
                lot = float(row[3] or 1)
            except (TypeError, ValueError):
                lot = 1.0
            try:
                tick = float(row[4] or 0.05)
            except (TypeError, ValueError):
                tick = 0.05
            out.append(Instrument(instrument_token=str(row[0]).strip(), exchange=exchange, tradingsymbol=symbol, name=underlying or (row[1].strip() or None),
                                  segment=segment, instrument_type=instrument_type, lot_size=lot or 1.0, tick_size=tick or 0.05, expiry=expiry, strike=strike))
            self._tickers[(exchange, symbol)] = ticker
        self._masters[exchange] = (time.monotonic(), out)
        return out

    async def _ticker(self, symbol: str, exchange: str = "NSE") -> str:
        """Platform symbol (or EXCHANGE:SYMBOL key) -> Fyers ticker. Indices resolve without the master."""
        exchange = (exchange or "NSE").upper()
        if ":" in symbol and not symbol.upper().startswith(tuple(f"{e}:" for e in SEGMENTS)):
            exchange, symbol = symbol.split(":", 1)
            exchange = exchange.upper()
        elif ":" in symbol:
            exchange, symbol = symbol.split(":", 1)
            exchange = exchange.upper()
            if symbol.upper().endswith(("-EQ", "-INDEX")):
                return f"{exchange}:{symbol.upper()}"
        key = symbol.strip().upper()
        if key in INDEX_ALIASES:
            return INDEX_ALIASES[key]
        if (exchange, key) not in self._tickers:
            await self.get_instruments(exchange)
        ticker = self._tickers.get((exchange, key))
        if ticker is None:
            raise BrokerAPIError(f"Instrument {exchange}:{symbol} not found in the Fyers symbol master")
        return ticker

    # --- quotes ----------------------------------------------------------------------------------
    def _quote_from(self, entry: dict, symbol: str) -> Quote:
        v = entry.get("v") or {}
        return Quote(symbol=symbol, ltp=float(v.get("lp") or 0), open=float(v.get("open_price") or 0), high=float(v.get("high_price") or 0),
                     low=float(v.get("low_price") or 0), close=float(v.get("prev_close_price") or 0), volume=float(v.get("volume") or 0),
                     oi=float(v["oi"]) if v.get("oi") not in (None, "") else None, bid=float(v["bid"]) if v.get("bid") not in (None, "") else None,
                     ask=float(v["ask"]) if v.get("ask") not in (None, "") else None, timestamp=_parse_time(v.get("tt")))

    async def _quotes(self, keys: List[str]) -> Dict[str, Quote]:
        tickers: Dict[str, str] = {}
        for key in keys:
            exchange, plain = (key.split(":", 1) if ":" in key and not key.upper().endswith(("-EQ", "-INDEX")) else ("NSE", key))
            tickers[await self._ticker(plain, exchange)] = key
        out: Dict[str, Quote] = {}
        ordered = list(tickers)
        for i in range(0, len(ordered), QUOTE_BATCH):
            body = await self._request("GET", f"{DATA_URL}/quotes", params={"symbols": ",".join(ordered[i:i + QUOTE_BATCH])})
            for entry in body.get("d") or []:
                key = tickers.get(str(entry.get("n")))
                if key is not None and entry.get("s", "ok") == "ok":
                    out[key] = self._quote_from(entry, key)
        return out

    async def get_ltp(self, symbols: List[str]) -> Dict[str, float]:
        return {k: q.ltp for k, q in (await self._quotes(symbols)).items()}

    async def get_quote(self, symbols: List[str]) -> Dict[str, Quote]:
        return await self._quotes(symbols)

    async def get_quote_for_symbol(self, symbol: str, exchange: str = "NSE") -> Optional[Quote]:
        quotes = await self._quotes([f"{exchange}:{symbol}"])
        quote = quotes.get(f"{exchange}:{symbol}")
        if quote is None:
            raise BrokerAPIError(f"No quote returned for {exchange}:{symbol}")
        return quote

    async def get_ltp_for_symbol(self, symbol: str, exchange: str = "NSE") -> float:
        return (await self.get_quote_for_symbol(symbol, exchange)).ltp  # type: ignore[union-attr]

    # --- candles ---------------------------------------------------------------------------------
    async def get_historical_data(self, symbol: str, exchange: str, interval: str, from_date: datetime, to_date: datetime) -> List[OHLCVBar]:
        ticker = await self._ticker(symbol, exchange)

        def day(dt: datetime) -> str:
            dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(IST).strftime("%Y-%m-%d")
        params = {"symbol": ticker, "resolution": RESOLUTION_MAP.get(interval, interval), "date_format": "1", "range_from": day(from_date), "range_to": day(to_date), "cont_flag": "1"}
        body = await self._request("GET", f"{DATA_URL}/history", params=params)
        bars: List[OHLCVBar] = []
        for row in body.get("candles") or []:
            try:
                ts = datetime.fromtimestamp(int(row[0]), tz=timezone.utc).astimezone(IST)
            except (TypeError, ValueError, IndexError):
                continue
            bars.append(OHLCVBar(timestamp=ts, open=float(row[1]), high=float(row[2]), low=float(row[3]), close=float(row[4]), volume=float(row[5]) if len(row) > 5 else 0.0))
        return bars

    # --- option chain ----------------------------------------------------------------------------
    async def get_option_chain(self, underlying: str, expiry: Optional[date] = None) -> OptionChain:
        """Fyers has a chain endpoint (`options-chain-v3`): the first call lists expiries, a second
        with the expiry's timestamp returns the strikes around the money (`strikecount` each side)."""
        ticker = await self._ticker(underlying, "NSE")
        params = {"symbol": ticker, "strikecount": "20"}
        body = await self._request("GET", f"{DATA_URL}/options-chain-v3", params=params)
        data = body.get("data") or {}
        expiries = data.get("expiryData") or []
        chosen_label, chosen_ts = None, None
        for item in expiries:
            label = _parse_time(item.get("date"))
            if label is None:
                continue
            if expiry is None or label.date() == expiry:
                chosen_label, chosen_ts = label.date().isoformat(), item.get("expiry")
                break
        if expiry is not None and chosen_ts is None:
            raise BrokerAPIError(f"Fyers lists no {underlying} expiry on {expiry.isoformat()}")
        if chosen_ts is not None and (expiry is not None or expiries and str(expiries[0].get("expiry")) != str(chosen_ts)):
            body = await self._request("GET", f"{DATA_URL}/options-chain-v3", params={**params, "timestamp": str(chosen_ts)})
            data = body.get("data") or {}
        rows: Dict[float, OptionChainRow] = {}
        underlying_ltp: Optional[float] = None
        for leg in data.get("optionsChain") or []:
            right = str(leg.get("option_type") or "").upper()
            try:
                strike = float(leg.get("strike_price") or 0)
            except (TypeError, ValueError):
                strike = 0.0
            if right not in ("CE", "PE") or strike <= 0:
                if leg.get("ltp") is not None and right not in ("CE", "PE"):
                    underlying_ltp = float(leg.get("ltp") or 0) or underlying_ltp
                continue
            row = rows.setdefault(strike, OptionChainRow(strike=strike))
            ltp = float(leg.get("ltp") or 0)
            oi = float(leg["oi"]) if leg.get("oi") not in (None, "") else None
            chg = float(leg["oich"]) if leg.get("oich") not in (None, "") else None
            vol = float(leg["volume"]) if leg.get("volume") not in (None, "") else None
            bid = float(leg["bid"]) if leg.get("bid") not in (None, "") else None
            ask = float(leg["ask"]) if leg.get("ask") not in (None, "") else None
            if right == "CE":
                row.call_ltp, row.call_oi, row.call_change_oi, row.call_volume, row.call_bid, row.call_ask = ltp, oi, chg, vol, bid, ask
            else:
                row.put_ltp, row.put_oi, row.put_change_oi, row.put_volume, row.put_bid, row.put_ask = ltp, oi, chg, vol, bid, ask
        if underlying_ltp is None:
            try:
                underlying_ltp = await self.get_ltp_for_symbol(underlying, "NSE")
            except (BrokerAPIError, BrokerAuthenticationError):
                underlying_ltp = None
        return OptionChain(underlying=underlying, expiry=chosen_label or (expiry.isoformat() if expiry else ""), underlying_ltp=underlying_ltp, rows=[rows[k] for k in sorted(rows)])

    # --- orders ----------------------------------------------------------------------------------
    async def place_order(self, order: BrokerOrderRequest) -> BrokerOrderResponse:
        ticker = await self._ticker(order.symbol, order.exchange)
        order_type = ORDER_TYPE_MAP.get(order.order_type.upper())
        if order_type is None:
            raise BrokerAPIError(f"Unsupported order type {order.order_type} for Fyers")
        payload = {
            "symbol": ticker, "qty": int(order.quantity), "type": order_type, "side": 1 if order.transaction_type == OrderSide.BUY else -1,
            "productType": PRODUCT_MAP.get(order.product.upper(), order.product.upper()), "limitPrice": float(order.price or 0), "stopPrice": float(order.trigger_price or 0),
            "validity": order.validity or "DAY", "disclosedQty": 0, "offlineOrder": False,
        }
        if order.tag:
            payload["orderTag"] = order.tag[: self.max_tag_length]
        body = await self._request("POST", f"{API_URL}/orders/sync", json_body=payload)
        order_id = str(body.get("id") or "")
        if not order_id:
            raise BrokerAPIError("Fyers returned no order id", 200, str(body))
        return BrokerOrderResponse(order_id=order_id, status="OPEN", message=body.get("message"), raw=body)

    async def modify_order(self, order_id: str, quantity: Optional[float] = None, price: Optional[float] = None,
                           trigger_price: Optional[float] = None, order_type: Optional[str] = None) -> BrokerOrderResponse:
        payload: Dict[str, object] = {"id": str(order_id)}
        if order_type:
            payload["type"] = ORDER_TYPE_MAP.get(order_type.upper(), 2)
        if quantity is not None:
            payload["qty"] = int(quantity)
        if price is not None:
            payload["limitPrice"] = float(price)
        if trigger_price is not None:
            payload["stopPrice"] = float(trigger_price)
        body = await self._request("PATCH", f"{API_URL}/orders/sync", json_body=payload)
        return BrokerOrderResponse(order_id=str(body.get("id") or order_id), status="MODIFIED", message=body.get("message"), raw=body)

    async def cancel_order(self, order_id: str) -> BrokerOrderResponse:
        body = await self._request("DELETE", f"{API_URL}/orders/sync", json_body={"id": str(order_id)})
        return BrokerOrderResponse(order_id=str(body.get("id") or order_id), status="CANCELLED", message=body.get("message"), raw=body)

    async def get_order_book(self) -> List[BrokerOrderStatus]:
        out = []
        for o in (await self._request("GET", f"{API_URL}/orders")).get("orderBook") or []:
            out.append(BrokerOrderStatus(order_id=str(o.get("id")), symbol=self._platform_symbol(str(o.get("symbol") or "")),
                                         transaction_type=OrderSide.BUY if int(o.get("side") or 1) > 0 else OrderSide.SELL, quantity=float(o.get("qty") or 0),
                                         filled_quantity=float(o.get("filledQty") or 0), order_type=ORDER_TYPE_BACK.get(int(o.get("type") or 2), str(o.get("type"))),
                                         status=STATUS_MAP.get(int(o.get("status") or 6), "OPEN"), price=float(o.get("limitPrice") or 0) or None,
                                         average_price=float(o.get("tradedPrice") or 0) or None, placed_at=_parse_time(o.get("orderDateTime"))))
        return out

    async def get_trade_book(self) -> List[BrokerTradeEntry]:
        out = []
        for t in (await self._request("GET", f"{API_URL}/tradebook")).get("tradeBook") or []:
            out.append(BrokerTradeEntry(trade_id=str(t.get("id") or ""), order_id=str(t.get("orderNumber") or ""), symbol=self._platform_symbol(str(t.get("symbol") or "")),
                                        transaction_type=OrderSide.BUY if int(t.get("side") or 1) > 0 else OrderSide.SELL, quantity=float(t.get("tradedQty") or 0),
                                        price=float(t.get("tradePrice") or 0), timestamp=_parse_time(t.get("orderDateTime"))))
        return out

    async def get_positions(self) -> List[BrokerPosition]:
        out = []
        for p in (await self._request("GET", f"{API_URL}/positions")).get("netPositions") or []:
            ticker = str(p.get("symbol") or "")
            out.append(BrokerPosition(symbol=self._platform_symbol(ticker), exchange=_exchange_of(ticker, p.get("segment")),
                                      product=PRODUCT_BACK.get(str(p.get("productType") or ""), str(p.get("productType") or "MIS")), quantity=float(p.get("netQty") or 0),
                                      average_price=float(p.get("netAvg") or p.get("avgPrice") or 0), ltp=float(p.get("ltp") or 0),
                                      pnl=float(p.get("unrealized_profit") if p.get("unrealized_profit") is not None else p.get("pl") or 0)))
        return out

    async def get_holdings(self) -> List[BrokerHolding]:
        out = []
        for h in (await self._request("GET", f"{API_URL}/holdings")).get("holdings") or []:
            ticker = str(h.get("symbol") or "")
            out.append(BrokerHolding(symbol=self._platform_symbol(ticker), exchange=_exchange_of(ticker, h.get("segment")),
                                     quantity=float(h.get("quantity") or h.get("qty") or 0), average_price=float(h.get("costPrice") or 0), ltp=float(h.get("ltp") or 0),
                                     pnl=float(h.get("pl") or 0)))
        return out

    async def get_margins(self) -> MarginInfo:
        rows = (await self._request("GET", f"{API_URL}/funds")).get("fund_limit") or []
        by_id = {int(r.get("id")): float(r.get("equityAmount") or 0) for r in rows if str(r.get("id", "")).lstrip("-").isdigit()}
        available, total, used = by_id.get(FUND_AVAILABLE, 0.0), by_id.get(FUND_TOTAL, 0.0), by_id.get(FUND_UTILISED, 0.0)
        return MarginInfo(available_cash=available, used_margin=used, available_margin=available, total_margin=total or (available + used))
