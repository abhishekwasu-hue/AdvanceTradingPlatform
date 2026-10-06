"""CoinDCX (INR spot crypto) adapter - the last structural stub replaced by real I/O.

Built from CoinDCX's public API documentation (https://docs.coindcx.com): REST at
`https://api.coindcx.com`, public market data at `https://public.coindcx.com`, HMAC-SHA256 request
signing with the account's API key/secret. As with every other adapter here it is verified against a
mocked transport built from those docs, not against a live account: run the Settings "Read-only
check" on a real key before any LIVE routing.

Mapping onto the platform's equity-shaped `BrokerInterface`:

* **Symbols** are CoinDCX market names, which are also the platform's crypto contract symbols
  (`BTCINR`, `ETHINR`, `USDTINR`); the exchange is `CRYPTO` (aliases `COINDCX`). The public
  candle endpoint wants the market's `pair` (`I-BTC_INR`), resolved from `markets_details`.
* **Session**: the API key/secret is long-lived; `authenticate()` proves it with `users/info`.
  There is no daily token expiry (see `PERMANENT_KEY_BROKERS` in token_lifecycle).
* **Orders**: MARKET -> `market_order`, LIMIT -> `limit_order`, SL / SL-M -> `stop_limit` (CoinDCX
  has no stop-market; the protective stop is a stop-limit whose limit is `STOP_LIMIT_SLIPPAGE`
  past the trigger so it behaves like a market stop in a fast move). Quantities are fractional and
  rounded down to the market's step. `product` has no meaning on spot and is ignored.
* **Positions / holdings** are the non-INR wallet balances (spot has no position ledger);
  `average_price` is unknown to the exchange and reported as 0. **Margins** are the INR wallet:
  `balance` available, `locked_balance` used.
* **Option chain**: none - `NotImplementedError`, which the platform reads as "no chain endpoint".
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx

from app.brokers.base import BrokerCapabilities, BrokerInterface
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.models import (BrokerCredentials, BrokerHolding, BrokerOrderRequest, BrokerOrderResponse, BrokerOrderStatus,
                                BrokerPosition, BrokerProfile, BrokerTradeEntry, Instrument, MarginInfo, OptionChain, Quote)
from app.core.enums import OrderSide
from app.core.models import OHLCVBar

logger = logging.getLogger(__name__)

API_URL = "https://api.coindcx.com"
PUBLIC_URL = "https://public.coindcx.com"
EXCHANGE = "CRYPTO"
QUOTE_CURRENCY = "INR"
MARKETS_CACHE_TTL_SECONDS = 6 * 60 * 60
TICKER_CACHE_TTL_SECONDS = 2.0
STOP_LIMIT_SLIPPAGE = 0.005          # limit price 0.5% past the trigger on stop orders
ORDER_TYPE_MAP = {"MARKET": "market_order", "LIMIT": "limit_order", "SL": "stop_limit", "SL-M": "stop_limit"}
STATUS_MAP = {"init": "OPEN", "initial": "OPEN", "open": "OPEN", "partially_filled": "PARTIAL_FILL", "filled": "COMPLETE",
              "cancelled": "CANCELLED", "partially_cancelled": "CANCELLED", "rejected": "REJECTED", "expired": "EXPIRED"}
INTERVALS = {"1minute": "1m", "1min": "1m", "1m": "1m", "5minute": "5m", "5min": "5m", "5m": "5m", "15minute": "15m", "15min": "15m", "15m": "15m",
             "30minute": "30m", "30min": "30m", "30m": "30m", "60minute": "1h", "60min": "1h", "1h": "1h", "day": "1d", "1d": "1d", "1day": "1d"}
AUTH_STATUS_CODES = (401, 403)


def _ms(value) -> Optional[datetime]:
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return None
    if raw <= 0:
        return None
    seconds = raw / 1000.0 if raw > 1e11 else raw
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class CoinDCXBroker(BrokerInterface):
    name = "coindcx"
    # CoinDCX has no market stop; `place_order` itself turns an SL-M request into a stop-limit with the limit 0.5%
    # past the trigger (STOP_LIMIT_SLIPPAGE), so the platform keeps asking for SL-M and the adapter maps it. The
    # capability matrix therefore stays at the defaults (the conversion lives here, not in the router).
    capabilities = BrokerCapabilities()
    max_tag_length = 36          # client_order_id

    def __init__(self, credentials: BrokerCredentials, client: Optional[httpx.AsyncClient] = None) -> None:
        self.credentials = credentials
        self._client = client or httpx.AsyncClient(base_url=API_URL, timeout=20.0)
        self._access_token = credentials.access_token or credentials.api_key      # a key that authenticates is "the session"
        self._markets: Optional[Tuple[float, List[dict]]] = None
        self._ticker: Optional[Tuple[float, Dict[str, dict]]] = None
        self._profile: Optional[BrokerProfile] = None

    # --- transport -------------------------------------------------------------------------------
    def _signed_headers(self, body: str) -> Dict[str, str]:
        if not self.credentials.api_key or not self.credentials.api_secret:
            raise BrokerAuthenticationError("CoinDCX needs an API key and secret (Settings > Brokers)")
        signature = hmac.new(self.credentials.api_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        return {"Content-Type": "application/json", "X-AUTH-APIKEY": self.credentials.api_key, "X-AUTH-SIGNATURE": signature}

    def _url(self, path: str) -> str:
        return path if path.startswith("http") else f"{API_URL}{path}"

    async def _private(self, path: str, payload: Optional[dict] = None):
        body = dict(payload or {})
        body["timestamp"] = int(time.time() * 1000)
        raw = json.dumps(body, separators=(",", ":"))
        response = await self._client.post(self._url(path), content=raw, headers=self._signed_headers(raw))
        return self._parse(response, path)

    async def _public(self, url: str, params: Optional[dict] = None):
        response = await self._client.get(url, params=params)
        return self._parse(response, url)

    def _parse(self, response: httpx.Response, path: str):
        try:
            body = response.json() if response.content else {}
        except ValueError as exc:
            raise BrokerAPIError(f"Non-JSON response from CoinDCX for {path}: {response.text[:200]}", response.status_code) from exc
        if response.status_code in AUTH_STATUS_CODES:
            message = body.get("message") if isinstance(body, dict) else None
            raise BrokerAuthenticationError(message or "CoinDCX rejected the API key or signature")
        if response.status_code >= 400 or (isinstance(body, dict) and str(body.get("status", "")).lower() == "error"):
            message = (body.get("message") or body.get("error") or "CoinDCX API error") if isinstance(body, dict) else "CoinDCX API error"
            code = body.get("code") if isinstance(body, dict) else None
            if "signature" in str(message).lower() or "api key" in str(message).lower() or "apikey" in str(message).lower():
                raise BrokerAuthenticationError(f"{message}")
            raise BrokerAPIError(f"{message}" + (f" ({code})" if code else ""), response.status_code, response.text)
        return body

    # --- session ---------------------------------------------------------------------------------
    async def authenticate(self) -> BrokerProfile:
        return await self.get_profile()

    async def get_profile(self) -> BrokerProfile:
        data = await self._private("/exchange/v1/users/info") or {}
        name = " ".join(x for x in (data.get("first_name"), data.get("last_name")) if x) or None
        self._profile = BrokerProfile(broker=self.name, user_id=str(data.get("coindcx_id") or data.get("id") or ""), name=name, email=data.get("email"))
        return self._profile

    async def disconnect(self) -> None:
        self._access_token = None

    # --- markets ---------------------------------------------------------------------------------
    async def _market_rows(self) -> List[dict]:
        if self._markets and (time.monotonic() - self._markets[0]) < MARKETS_CACHE_TTL_SECONDS:
            return self._markets[1]
        rows = await self._public(f"{API_URL}/exchange/v1/markets_details")
        if not isinstance(rows, list):
            raise BrokerAPIError("CoinDCX markets_details did not return a list", raw=str(rows)[:200])
        self._markets = (time.monotonic(), rows)
        return rows

    async def _market(self, symbol: str) -> dict:
        key = self._plain(symbol)
        for row in await self._market_rows():
            if str(row.get("coindcx_name") or row.get("symbol") or "").upper() == key:
                return row
        raise BrokerAPIError(f"Market {key} is not listed on CoinDCX")

    @staticmethod
    def _plain(symbol: str) -> str:
        text = (symbol or "").strip().upper()
        if ":" in text:
            text = text.split(":", 1)[1]
        return text.replace("_CRYPTO", "").replace("/", "").replace("-", "")

    async def get_instruments(self, exchange: Optional[str] = None) -> List[Instrument]:
        exchange = (exchange or EXCHANGE).upper()
        if exchange not in (EXCHANGE, "COINDCX"):
            return []
        out: List[Instrument] = []
        for row in await self._market_rows():
            if str(row.get("status") or "active").lower() != "active":
                continue
            base = str(row.get("base_currency_short_name") or "").upper()          # the quote currency in CoinDCX's naming
            if base != QUOTE_CURRENCY:
                continue
            step = _f(row.get("step"), 0.0) or _f(row.get("min_quantity"), 1.0) or 1.0
            precision = int(_f(row.get("base_currency_precision"), 2))
            out.append(Instrument(instrument_token=str(row.get("pair") or row.get("coindcx_name")), exchange=EXCHANGE,
                                  tradingsymbol=str(row.get("coindcx_name") or "").upper(), name=str(row.get("target_currency_short_name") or "").upper() or None,
                                  segment="SPOT", instrument_type="CRYPTO", lot_size=step, tick_size=10 ** (-precision) if precision >= 0 else 0.01))
        return out

    # --- quotes ----------------------------------------------------------------------------------
    async def _tickers(self) -> Dict[str, dict]:
        if self._ticker and (time.monotonic() - self._ticker[0]) < TICKER_CACHE_TTL_SECONDS:
            return self._ticker[1]
        rows = await self._public(f"{API_URL}/exchange/ticker")
        table = {str(r.get("market") or "").upper(): r for r in rows} if isinstance(rows, list) else {}
        self._ticker = (time.monotonic(), table)
        return table

    def _quote_from(self, row: dict, symbol: str) -> Quote:
        return Quote(symbol=symbol, ltp=_f(row.get("last_price")), open=0.0, high=_f(row.get("high")), low=_f(row.get("low")),
                     close=_f(row.get("last_price")), volume=_f(row.get("volume")), bid=_f(row.get("bid")) or None, ask=_f(row.get("ask")) or None,
                     timestamp=_ms(row.get("timestamp")))

    async def get_quote(self, symbols: List[str]) -> Dict[str, Quote]:
        table = await self._tickers()
        out: Dict[str, Quote] = {}
        for key in symbols:
            row = table.get(self._plain(key))
            if row is not None:
                out[key] = self._quote_from(row, key)
        return out

    async def get_ltp(self, symbols: List[str]) -> Dict[str, float]:
        return {k: q.ltp for k, q in (await self.get_quote(symbols)).items()}

    async def get_quote_for_symbol(self, symbol: str, exchange: str = EXCHANGE) -> Optional[Quote]:
        quotes = await self.get_quote([symbol])
        return quotes.get(symbol)

    async def get_ltp_for_symbol(self, symbol: str, exchange: str = EXCHANGE) -> float:
        quote = await self.get_quote_for_symbol(symbol, exchange)
        if quote is None or quote.ltp <= 0:
            raise BrokerAPIError(f"No ticker for {self._plain(symbol)} on CoinDCX")
        return float(quote.ltp)

    # --- candles ---------------------------------------------------------------------------------
    async def get_historical_data(self, symbol: str, exchange: str, interval: str, from_date: datetime, to_date: datetime) -> List[OHLCVBar]:
        market = await self._market(symbol)
        pair = market.get("pair")
        if not pair:
            raise BrokerAPIError(f"CoinDCX lists no candle pair for {self._plain(symbol)}")
        params = {"pair": pair, "interval": INTERVALS.get(interval, interval), "limit": 1000,
                  "startTime": int(from_date.timestamp() * 1000), "endTime": int(to_date.timestamp() * 1000)}
        rows = await self._public(f"{PUBLIC_URL}/market_data/candles", params=params)
        bars: List[OHLCVBar] = []
        for row in rows if isinstance(rows, list) else []:
            when = _ms(row.get("time"))
            if when is None:
                continue
            bars.append(OHLCVBar(timestamp=when, open=_f(row.get("open")), high=_f(row.get("high")), low=_f(row.get("low")),
                                 close=_f(row.get("close")), volume=_f(row.get("volume"))))
        bars.sort(key=lambda b: b.timestamp)
        return bars

    async def get_intraday_candles(self, symbol: str, exchange: str, interval: str) -> List[OHLCVBar]:
        now = datetime.now(timezone.utc)
        return await self.get_historical_data(symbol, exchange, interval, now - timedelta(days=1), now)

    async def get_option_chain(self, underlying: str, expiry: Optional[date] = None) -> OptionChain:
        raise NotImplementedError("CoinDCX spot has no option chain")

    # --- orders ----------------------------------------------------------------------------------
    @staticmethod
    def _round_quantity(quantity: float, step: float) -> float:
        if step <= 0:
            return quantity
        return math.floor(quantity / step + 1e-9) * step

    async def place_order(self, order: BrokerOrderRequest) -> BrokerOrderResponse:
        order_type = ORDER_TYPE_MAP.get(order.order_type.upper())
        if order_type is None:
            raise BrokerAPIError(f"CoinDCX does not support order type {order.order_type}")
        market = await self._market(order.symbol)
        step = _f(market.get("step"), 0.0)
        quantity = self._round_quantity(float(order.quantity), step) if step else float(order.quantity)
        if quantity <= 0:
            raise BrokerAPIError(f"Quantity {order.quantity} is below the CoinDCX step {step} for {self._plain(order.symbol)}")
        payload: Dict[str, object] = {"market": str(market.get("coindcx_name")), "side": "buy" if order.transaction_type == OrderSide.BUY else "sell",
                                      "order_type": order_type, "total_quantity": quantity,
                                      "client_order_id": (order.tag or f"atp-{uuid.uuid4().hex[:24]}")[:self.max_tag_length]}
        if order_type == "limit_order":
            if order.price is None:
                raise BrokerAPIError("LIMIT order needs a price")
            payload["price_per_unit"] = float(order.price)
        elif order_type == "stop_limit":
            if order.trigger_price is None:
                raise BrokerAPIError("Stop order needs a trigger price")
            trigger = float(order.trigger_price)
            limit = float(order.price) if (order.price is not None and order.order_type.upper() == "SL") else (
                trigger * (1 - STOP_LIMIT_SLIPPAGE) if order.transaction_type == OrderSide.SELL else trigger * (1 + STOP_LIMIT_SLIPPAGE))
            payload["stop_price"] = trigger
            payload["price_per_unit"] = round(limit, int(_f(market.get("base_currency_precision"), 2)))
        body = await self._private("/exchange/v1/orders/create", payload)
        orders = body.get("orders") if isinstance(body, dict) else None
        first = (orders or [body])[0] if isinstance(orders, list) or isinstance(body, dict) else {}
        order_id = str(first.get("id") or "")
        if not order_id:
            raise BrokerAPIError("CoinDCX returned no order id", raw=str(body)[:300])
        self._remember_order(order_id)
        status = STATUS_MAP.get(str(first.get("status") or "open").lower(), "OPEN")
        return BrokerOrderResponse(order_id=order_id, status=status, message=first.get("message") if isinstance(first, dict) else None,
                                   raw=body if isinstance(body, dict) else {"orders": body})

    async def modify_order(self, order_id: str, quantity: Optional[float] = None, price: Optional[float] = None,
                           trigger_price: Optional[float] = None, order_type: Optional[str] = None) -> BrokerOrderResponse:
        if quantity is not None or order_type is not None or trigger_price is not None:
            # P0.5: `orders/edit` changes price_per_unit only. Moving a stop's limit while its trigger stays put would
            # leave a stop that triggers at the old level and then rests unmarketable - refuse, so the caller keeps
            # the software stop (and cancel + re-place is the explicit path).
            raise BrokerAPIError("CoinDCX can only edit an order's price; cancel and re-place to change quantity, type or trigger")
        body = await self._private("/exchange/v1/orders/edit", {"id": str(order_id), "price_per_unit": float(price)})
        return BrokerOrderResponse(order_id=str(order_id), status=STATUS_MAP.get(str(body.get("status") or "open").lower(), "OPEN") if isinstance(body, dict) else "OPEN",
                                   raw=body if isinstance(body, dict) else None)

    async def cancel_order(self, order_id: str) -> BrokerOrderResponse:
        body = await self._private("/exchange/v1/orders/cancel", {"id": str(order_id)})
        return BrokerOrderResponse(order_id=str(order_id), status="CANCELLED", message=body.get("message") if isinstance(body, dict) else None,
                                   raw=body if isinstance(body, dict) else None)

    def _status_from(self, o: dict) -> BrokerOrderStatus:
        total = _f(o.get("total_quantity"))
        remaining = _f(o.get("remaining_quantity"), total)
        return BrokerOrderStatus(order_id=str(o.get("id")), symbol=str(o.get("market") or "").upper(),
                                 transaction_type=OrderSide.BUY if str(o.get("side", "")).lower() == "buy" else OrderSide.SELL,
                                 quantity=total, filled_quantity=max(0.0, total - remaining),
                                 order_type={"market_order": "MARKET", "limit_order": "LIMIT", "stop_limit": "SL"}.get(str(o.get("order_type") or ""), str(o.get("order_type") or "")),
                                 status=STATUS_MAP.get(str(o.get("status") or "").lower(), str(o.get("status") or "OPEN").upper()),
                                 price=_f(o.get("price_per_unit")) or None, average_price=_f(o.get("avg_price")) or None, placed_at=_ms(o.get("created_at")))

    # P0.5 / T1: `active_orders` drops an order the moment it fills, so a fill confirmation that only reads it would
    # mistake a filled market order for a missing one. The ids this process placed are kept (most recent 50) and
    # looked up by `orders/status` when the active list does not show them.
    _RECENT_ORDERS = 50

    def _remember_order(self, order_id: str) -> None:
        recent = getattr(self, "_recent_order_ids", None)
        if recent is None:
            recent = self._recent_order_ids = []
        if order_id not in recent:
            recent.append(order_id)
            del recent[:-self._RECENT_ORDERS]

    async def get_order_book(self) -> List[BrokerOrderStatus]:
        body = await self._private("/exchange/v1/orders/active_orders", {})
        rows = body.get("orders") if isinstance(body, dict) else body
        book = [self._status_from(o) for o in (rows or []) if isinstance(o, dict)]
        listed = {o.order_id for o in book}
        for order_id in list(getattr(self, "_recent_order_ids", []) or []):
            if order_id in listed:
                continue
            try:
                status = await self._private("/exchange/v1/orders/status", {"id": order_id})
            except Exception as exc:  # noqa: BLE001 - the active list still answers; the fill check treats it as unconfirmed
                logger.debug("CoinDCX order status for %s unavailable: %s", order_id, exc)
                continue
            if isinstance(status, dict) and status.get("id"):
                book.append(self._status_from(status))
        return book

    async def get_trade_book(self) -> List[BrokerTradeEntry]:
        rows = await self._private("/exchange/v1/orders/trade_history", {"limit": 500})
        out: List[BrokerTradeEntry] = []
        for t in rows if isinstance(rows, list) else []:
            out.append(BrokerTradeEntry(trade_id=str(t.get("id")), order_id=str(t.get("order_id") or ""), symbol=str(t.get("symbol") or t.get("market") or "").upper(),
                                        transaction_type=OrderSide.BUY if str(t.get("side", "")).lower() == "buy" else OrderSide.SELL,
                                        quantity=_f(t.get("quantity")), price=_f(t.get("price")), timestamp=_ms(t.get("timestamp"))))
        return out

    # --- wallet ----------------------------------------------------------------------------------
    async def _balances(self) -> List[dict]:
        rows = await self._private("/exchange/v1/users/balances", {})
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    async def get_positions(self) -> List[BrokerPosition]:
        out: List[BrokerPosition] = []
        tickers = None
        for row in await self._balances():
            currency = str(row.get("currency") or "").upper()
            quantity = _f(row.get("balance")) + _f(row.get("locked_balance"))
            if currency == QUOTE_CURRENCY or quantity <= 0:
                continue
            if tickers is None:
                try:
                    tickers = await self._tickers()
                except Exception:  # noqa: BLE001 - a ticker outage must not hide the holdings
                    tickers = {}
            ltp = _f((tickers or {}).get(f"{currency}{QUOTE_CURRENCY}", {}).get("last_price"))
            out.append(BrokerPosition(symbol=f"{currency}{QUOTE_CURRENCY}", exchange=EXCHANGE, product="CNC", quantity=quantity, average_price=0.0, ltp=ltp, pnl=0.0))
        return out

    async def get_holdings(self) -> List[BrokerHolding]:
        return [BrokerHolding(symbol=p.symbol, exchange=p.exchange, quantity=p.quantity, average_price=p.average_price, ltp=p.ltp, pnl=p.pnl)
                for p in await self.get_positions()]

    async def get_margins(self) -> MarginInfo:
        for row in await self._balances():
            if str(row.get("currency") or "").upper() == QUOTE_CURRENCY:
                available = _f(row.get("balance"))
                locked = _f(row.get("locked_balance"))
                return MarginInfo(available_cash=available, used_margin=locked, available_margin=available, total_margin=available + locked)
        return MarginInfo(available_cash=0.0, used_margin=0.0, available_margin=0.0, total_margin=0.0)

    async def exit_position(self, symbol: str, exchange: str, quantity: float, side: OrderSide, *, product: str = "MIS", tag: Optional[str] = None) -> BrokerOrderResponse:
        exit_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
        return await self.place_order(BrokerOrderRequest(symbol=symbol, exchange=exchange or EXCHANGE, transaction_type=exit_side, quantity=quantity,
                                                         order_type="MARKET", product="CNC", tag=tag))
