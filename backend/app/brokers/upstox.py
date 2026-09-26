import gzip
import json
import time
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import httpx

from app.brokers.base import BrokerInterface
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
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
from app.core.enums import OrderSide
from app.core.models import OHLCVBar

UPSTOX_INTERVAL_MAP = {
    "1min": "1minute", "30min": "30minute", "day": "day", "week": "week", "month": "month",
}

# Static, unauthenticated per-exchange instrument master published by Upstox.
INSTRUMENTS_URL_TEMPLATE = "https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.json.gz"

# The instrument master is a multi-MB file that changes at most once a day; re-downloading and
# re-parsing it on every historical-data/option-chain/place-order call would add seconds of
# needless latency per request, so each adapter instance caches it per exchange for a while.
_INSTRUMENT_CACHE_TTL_SECONDS = 6 * 3600


class UpstoxBroker(BrokerInterface):
    """Upstox API v2 adapter.

    Requires `credentials.api_key` (client_id) and `credentials.api_secret` (client_secret) for
    the OAuth2 code exchange, or an existing `credentials.access_token`. Endpoint paths follow the
    public Upstox v2 docs (https://upstox.com/developer/api-documentation/) - verify against the
    live docs before production use, since broker APIs evolve.
    """

    name = "upstox"
    BASE_URL = "https://api.upstox.com/v2"

    def __init__(self, credentials: BrokerCredentials, client: Optional[httpx.AsyncClient] = None) -> None:
        if not credentials.api_key:
            raise BrokerAuthenticationError("Upstox adapter requires credentials.api_key (client_id)")
        self.credentials = credentials
        self._access_token = credentials.access_token
        self._client = client or httpx.AsyncClient(base_url=self.BASE_URL, timeout=15.0)
        self._instruments_cache: Dict[str, Tuple[float, List[Instrument]]] = {}

    def _headers(self) -> Dict[str, str]:
        if not self._access_token:
            raise BrokerAuthenticationError("Not authenticated - call authenticate() first")
        return {"Authorization": f"Bearer {self._access_token}", "Accept": "application/json"}

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        response = await self._client.request(method, path, headers=self._headers(), **kwargs)
        try:
            payload = response.json()
        except ValueError as exc:
            raise BrokerAPIError(f"Non-JSON response from Upstox: {response.text[:200]}", response.status_code) from exc

        if response.status_code >= 400 or payload.get("status") == "error":
            message = payload.get("errors", [{}])[0].get("message", "Upstox API error") if "errors" in payload else "Upstox API error"
            raise BrokerAPIError(message, response.status_code, response.text)
        return payload.get("data", {})

    async def authenticate(self) -> BrokerProfile:
        if not self._access_token:
            if not (self.credentials.request_token and self.credentials.api_secret and self.credentials.redirect_uri):
                raise BrokerAuthenticationError(
                    "No access_token on file and no request_token/api_secret/redirect_uri to exchange for one"
                )
            response = await self._client.post(
                "/login/authorization/token",
                headers={"Accept": "application/json"},
                data={
                    "code": self.credentials.request_token,
                    "client_id": self.credentials.api_key,
                    "client_secret": self.credentials.api_secret,
                    "redirect_uri": self.credentials.redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            payload = response.json()
            if response.status_code >= 400:
                raise BrokerAuthenticationError(payload.get("error_description", "Upstox login failed"))
            self._access_token = payload["access_token"]
        return await self.get_profile()

    async def get_profile(self) -> BrokerProfile:
        data = await self._request("GET", "/user/profile")
        return BrokerProfile(broker=self.name, user_id=data["user_id"], name=data.get("user_name"), email=data.get("email"))

    async def get_instruments(self, exchange: Optional[str] = None) -> List[Instrument]:
        exchange = exchange or "NSE"
        cached = self._instruments_cache.get(exchange)
        if cached is not None and (time.monotonic() - cached[0]) < _INSTRUMENT_CACHE_TTL_SECONDS:
            return cached[1]

        response = await self._client.get(INSTRUMENTS_URL_TEMPLATE.format(exchange=exchange))
        if response.status_code >= 400:
            raise BrokerAPIError("Failed to fetch instrument master", response.status_code, response.text)
        raw = json.loads(gzip.decompress(response.content))
        instruments = [
            Instrument(
                instrument_token=row["instrument_key"],
                exchange=row.get("exchange", exchange),
                tradingsymbol=row.get("trading_symbol", row.get("tradingsymbol", "")),
                name=row.get("name"),
                segment=row.get("segment"),
                instrument_type=row.get("instrument_type"),
                lot_size=int(row.get("lot_size", 1) or 1),
                tick_size=float(row.get("tick_size", 0.05) or 0.05),
                expiry=row.get("expiry"),
                strike=float(row["strike_price"]) if row.get("strike_price") else None,
            )
            for row in raw
        ]
        self._instruments_cache[exchange] = (time.monotonic(), instruments)
        return instruments

    async def _resolve_instrument(self, symbol: str, exchange: str) -> Instrument:
        """Plain trading symbol -> the Upstox instrument (with its "NSE_EQ|INE002A01018"-style
        instrument_key) via the cached instrument master. A symbol that already looks like an
        instrument key is passed through untouched."""
        if "|" in symbol:
            return Instrument(instrument_token=symbol, exchange=exchange, tradingsymbol=symbol)
        instruments = await self.get_instruments(exchange)
        match = next((i for i in instruments if i.tradingsymbol == symbol), None)
        if match is None:
            raise BrokerAPIError(f"Instrument {exchange}:{symbol} not found in Upstox instrument master")
        return match

    async def get_ltp(self, symbols: List[str]) -> Dict[str, float]:
        """`symbols` are Upstox instrument keys. Upstox keys its LTP response by
        "EXCHANGE_SEGMENT:TRADINGSYMBOL" (not by the instrument key that was asked for), so the
        result is keyed as the response keys it."""
        data = await self._request("GET", "/market-quote/ltp", params={"instrument_key": ",".join(symbols)})
        return {symbol: entry["last_price"] for symbol, entry in data.items()}

    async def get_ltp_for_symbol(self, symbol: str, exchange: str = "NSE") -> float:
        instrument = await self._resolve_instrument(symbol, exchange)
        data = await self._request(
            "GET", "/market-quote/ltp", params={"instrument_key": instrument.instrument_token}
        )
        for entry in data.values():
            if entry.get("instrument_token") == instrument.instrument_token:
                return float(entry["last_price"])
        if len(data) == 1:
            return float(next(iter(data.values()))["last_price"])
        raise BrokerAPIError(f"No LTP returned for {exchange}:{symbol}")

    async def get_intraday_candles(self, symbol: str, exchange: str, interval: str) -> List[OHLCVBar]:
        """Upstox serves the current trading day only from its separate intraday endpoint - the
        historical endpoint (get_historical_data) returns nothing for today. Only 1minute and
        30minute intervals exist there; everything else is resampled up by the market-data
        service from 1-minute bars."""
        upstox_interval = UPSTOX_INTERVAL_MAP.get(interval, interval)
        instrument = await self._resolve_instrument(symbol, exchange)
        data = await self._request(
            "GET", f"/historical-candle/intraday/{instrument.instrument_token}/{upstox_interval}"
        )
        bars = [
            OHLCVBar(timestamp=c[0], open=c[1], high=c[2], low=c[3], close=c[4], volume=c[5] if len(c) > 5 else 0.0)
            for c in data.get("candles", [])
        ]
        # Upstox returns newest-first; every consumer here expects ascending time.
        return sorted(bars, key=lambda b: b.timestamp)

    async def get_quote(self, symbols: List[str]) -> Dict[str, Quote]:
        data = await self._request("GET", "/market-quote/quotes", params={"instrument_key": ",".join(symbols)})
        quotes: Dict[str, Quote] = {}
        for symbol, entry in data.items():
            ohlc = entry.get("ohlc", {})
            depth = entry.get("depth", {})
            best_buy = (depth.get("buy") or [{}])[0]
            best_sell = (depth.get("sell") or [{}])[0]
            quotes[symbol] = Quote(
                symbol=symbol,
                ltp=entry.get("last_price", 0.0),
                open=ohlc.get("open", 0.0), high=ohlc.get("high", 0.0),
                low=ohlc.get("low", 0.0), close=ohlc.get("close", 0.0),
                volume=entry.get("volume", 0.0), oi=entry.get("oi"),
                bid=best_buy.get("price"), ask=best_sell.get("price"),
                bid_qty=best_buy.get("quantity"), ask_qty=best_sell.get("quantity"),
            )
        return quotes

    async def get_historical_data(
        self, symbol: str, exchange: str, interval: str, from_date: datetime, to_date: datetime
    ) -> List[OHLCVBar]:
        upstox_interval = UPSTOX_INTERVAL_MAP.get(interval, interval)
        instruments = await self.get_instruments(exchange)
        match = next((i for i in instruments if i.tradingsymbol == symbol), None)
        if match is None:
            raise BrokerAPIError(f"Instrument {exchange}:{symbol} not found")

        data = await self._request(
            "GET",
            f"/historical-candle/{match.instrument_token}/{upstox_interval}/"
            f"{to_date.strftime('%Y-%m-%d')}/{from_date.strftime('%Y-%m-%d')}",
        )
        return [
            OHLCVBar(timestamp=c[0], open=c[1], high=c[2], low=c[3], close=c[4], volume=c[5] if len(c) > 5 else 0.0)
            for c in data.get("candles", [])
        ]

    async def get_option_chain(self, underlying: str, expiry: Optional[date] = None) -> OptionChain:
        instruments = await self.get_instruments("NSE")
        underlying_match = next((i for i in instruments if i.tradingsymbol == underlying), None)
        instrument_key = underlying_match.instrument_token if underlying_match else underlying

        params = {"instrument_key": instrument_key}
        if expiry is not None:
            params["expiry_date"] = expiry.isoformat()
        data = await self._request("GET", "/option/chain", params=params)

        rows = []
        for entry in data:
            call = entry.get("call_options", {}).get("market_data", {})
            put = entry.get("put_options", {}).get("market_data", {})
            rows.append(
                OptionChainRow(
                    strike=entry["strike_price"],
                    call_oi=call.get("oi"), call_ltp=call.get("ltp"), call_volume=call.get("volume"),
                    put_oi=put.get("oi"), put_ltp=put.get("ltp"), put_volume=put.get("volume"),
                )
            )
        return OptionChain(
            underlying=underlying,
            expiry=expiry.isoformat() if expiry else (data[0].get("expiry", "") if data else ""),
            rows=sorted(rows, key=lambda r: r.strike),
        )

    async def place_order(self, order: BrokerOrderRequest) -> BrokerOrderResponse:
        # BrokerOrderRequest.symbol is a plain trading symbol (the same contract every other
        # adapter uses) - Upstox's own API needs its "instrument_key" format instead
        # (e.g. "NSE_EQ|INE002A01018"), so resolve it here rather than making every caller know
        # Upstox-specific identifiers.
        instruments = await self.get_instruments(order.exchange)
        match = next((i for i in instruments if i.tradingsymbol == order.symbol), None)
        if match is None:
            raise BrokerAPIError(f"Instrument {order.exchange}:{order.symbol} not found in Upstox instrument master")

        data = await self._request(
            "POST",
            "/order/place",
            json={
                "instrument_token": match.instrument_token,
                "transaction_type": order.transaction_type.value,
                "order_type": order.order_type,
                "quantity": order.quantity,
                "product": order.product,
                "price": order.price or 0,
                "trigger_price": order.trigger_price or 0,
                "validity": order.validity,
                "disclosed_quantity": 0,
                "is_amo": False,
                "tag": order.tag or "",
            },
        )
        return BrokerOrderResponse(order_id=data["order_id"], status="OPEN", raw=data)

    async def modify_order(
        self, order_id: str, quantity: Optional[float] = None, price: Optional[float] = None,
        trigger_price: Optional[float] = None, order_type: Optional[str] = None,
    ) -> BrokerOrderResponse:
        payload = {"order_id": order_id}
        payload.update({k: v for k, v in {
            "quantity": quantity, "price": price, "trigger_price": trigger_price, "order_type": order_type,
        }.items() if v is not None})
        data = await self._request("PUT", "/order/modify", json=payload)
        return BrokerOrderResponse(order_id=data["order_id"], status="MODIFIED", raw=data)

    async def cancel_order(self, order_id: str) -> BrokerOrderResponse:
        data = await self._request("DELETE", "/order/cancel", params={"order_id": order_id})
        return BrokerOrderResponse(order_id=data["order_id"], status="CANCELLED", raw=data)

    async def get_order_book(self) -> List[BrokerOrderStatus]:
        data = await self._request("GET", "/order/retrieve-all")
        return [
            BrokerOrderStatus(
                order_id=o["order_id"], symbol=o["trading_symbol"],
                transaction_type=OrderSide(o["transaction_type"]), quantity=o["quantity"],
                filled_quantity=o.get("filled_quantity", 0), order_type=o["order_type"], status=o["status"],
                price=o.get("price"), average_price=o.get("average_price"), placed_at=o.get("order_timestamp"),
            )
            for o in data
        ]

    async def get_trade_book(self) -> List[BrokerTradeEntry]:
        data = await self._request("GET", "/order/trades/get-trades-for-day")
        return [
            BrokerTradeEntry(
                trade_id=t["trade_id"], order_id=t["order_id"], symbol=t["trading_symbol"],
                transaction_type=OrderSide(t["transaction_type"]), quantity=t["quantity"],
                price=t["average_price"], timestamp=t.get("order_timestamp"),
            )
            for t in data
        ]

    async def get_positions(self) -> List[BrokerPosition]:
        data = await self._request("GET", "/portfolio/short-term-positions")
        return [
            BrokerPosition(
                symbol=p["trading_symbol"], exchange=p["exchange"], product=p["product"],
                quantity=p["quantity"], average_price=p["average_price"], ltp=p["last_price"], pnl=p["pnl"],
            )
            for p in data
        ]

    async def get_holdings(self) -> List[BrokerHolding]:
        data = await self._request("GET", "/portfolio/long-term-holdings")
        return [
            BrokerHolding(
                symbol=h["trading_symbol"], exchange=h["exchange"],
                quantity=h["quantity"], average_price=h["average_price"], ltp=h["last_price"], pnl=h["pnl"],
            )
            for h in data
        ]

    async def get_margins(self) -> MarginInfo:
        data = await self._request("GET", "/user/get-funds-and-margin")
        equity = data.get("equity", {})
        return MarginInfo(
            available_cash=equity.get("available_margin", 0.0),
            used_margin=equity.get("used_margin", 0.0),
            available_margin=equity.get("available_margin", 0.0),
            total_margin=equity.get("total_margin"),
        )
