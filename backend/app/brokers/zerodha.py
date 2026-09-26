import csv
import logging
import hashlib
import io
import time
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import httpx

from app.brokers.base import BrokerInterface
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.timestamps import parse_broker_timestamp
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

KITE_INTERVAL_MAP = {
    "1min": "minute",
    "3min": "3minute",
    "5min": "5minute",
    "10min": "10minute",
    "15min": "15minute",
    "30min": "30minute",
    "60min": "60minute",
    "day": "day",
}

# Kite's instruments dump is a full exchange CSV that changes at most once a day; re-downloading
# and re-parsing it on every historical-data/option-chain call would add needless latency per
# request, so each adapter instance caches it for a while.
_INSTRUMENT_CACHE_TTL_SECONDS = 6 * 3600


logger = logging.getLogger(__name__)

class ZerodhaBroker(BrokerInterface):
    """Kite Connect (Zerodha) adapter. Reference implementation for the BrokerInterface.

    Requires `credentials.api_key`; either `access_token` (an already-established session)
    or both `request_token` and `api_secret` so `authenticate()` can complete the login
    handshake itself. See https://kite.trade/docs/connect/v3/ for the underlying API.
    """

    name = "zerodha"
    BASE_URL = "https://api.kite.trade"

    def __init__(self, credentials: BrokerCredentials, client: Optional[httpx.AsyncClient] = None) -> None:
        if not credentials.api_key:
            raise BrokerAuthenticationError("Zerodha adapter requires credentials.api_key")
        self.credentials = credentials
        self._access_token = credentials.access_token
        self._client = client or httpx.AsyncClient(base_url=self.BASE_URL, timeout=15.0)
        self._instruments_cache: Dict[str, Tuple[float, List[Instrument]]] = {}

    def _headers(self) -> Dict[str, str]:
        if not self._access_token:
            raise BrokerAuthenticationError("Not authenticated - call authenticate() first")
        return {
            "X-Kite-Version": "3",
            "Authorization": f"token {self.credentials.api_key}:{self._access_token}",
        }

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        response = await self._client.request(method, path, headers=self._headers(), **kwargs)
        try:
            payload = response.json()
        except ValueError as exc:
            raise BrokerAPIError(f"Non-JSON response from Kite: {response.text[:200]}", response.status_code) from exc

        if response.status_code >= 400 or payload.get("status") == "error":
            raise BrokerAPIError(payload.get("message", "Kite API error"), response.status_code, response.text)
        return payload.get("data", {})

    async def authenticate(self) -> BrokerProfile:
        if not self._access_token:
            if not (self.credentials.request_token and self.credentials.api_secret):
                raise BrokerAuthenticationError(
                    "No access_token on file and no request_token/api_secret to exchange for one"
                )
            checksum = hashlib.sha256(
                f"{self.credentials.api_key}{self.credentials.request_token}{self.credentials.api_secret}".encode()
            ).hexdigest()
            response = await self._client.post(
                "/session/token",
                data={
                    "api_key": self.credentials.api_key,
                    "request_token": self.credentials.request_token,
                    "checksum": checksum,
                },
            )
            payload = response.json()
            if response.status_code >= 400 or payload.get("status") == "error":
                raise BrokerAuthenticationError(payload.get("message", "Zerodha login failed"))
            self._access_token = payload["data"]["access_token"]
        return await self.get_profile()

    async def get_profile(self) -> BrokerProfile:
        data = await self._request("GET", "/user/profile")
        return BrokerProfile(broker=self.name, user_id=data["user_id"], name=data.get("user_name"), email=data.get("email"))

    async def get_instruments(self, exchange: Optional[str] = None) -> List[Instrument]:
        cache_key = exchange or "__all__"
        cached = self._instruments_cache.get(cache_key)
        if cached is not None and (time.monotonic() - cached[0]) < _INSTRUMENT_CACHE_TTL_SECONDS:
            return cached[1]

        path = f"/instruments/{exchange}" if exchange else "/instruments"
        response = await self._client.get(path, headers=self._headers())
        if response.status_code >= 400:
            raise BrokerAPIError("Failed to fetch instruments dump", response.status_code, response.text)

        reader = csv.DictReader(io.StringIO(response.text))
        instruments = []
        for row in reader:
            # Kite's CSV puts "0" (a non-empty, truthy string) in the strike column for every
            # non-option instrument, not an empty value - `if row.get("strike")` alone treats
            # that "0" as present and would set strike=0.0 on every equity/future row instead of
            # None. Parse first, then collapse a genuine zero to None.
            strike_raw = float(row["strike"]) if row.get("strike") else None
            instruments.append(
                Instrument(
                    instrument_token=row["instrument_token"],
                    exchange=row["exchange"],
                    tradingsymbol=row["tradingsymbol"],
                    name=row.get("name") or None,
                    segment=row.get("segment") or None,
                    instrument_type=row.get("instrument_type") or None,
                    lot_size=int(row["lot_size"]) if row.get("lot_size") else 1,
                    tick_size=float(row["tick_size"]) if row.get("tick_size") else 0.05,
                    expiry=row.get("expiry") or None,
                    strike=strike_raw or None,
                )
            )
        self._instruments_cache[cache_key] = (time.monotonic(), instruments)
        return instruments

    async def get_ltp(self, symbols: List[str]) -> Dict[str, float]:
        data = await self._request("GET", "/quote/ltp", params=[("i", s) for s in symbols])
        return {symbol: entry["last_price"] for symbol, entry in data.items()}

    async def get_quote_for_symbol(self, symbol: str, exchange: str = "NSE") -> Optional[Quote]:
        """Kite `/quote` for one "EXCHANGE:SYMBOL", with its `last_trade_time`/`timestamp`
        (IST wall time, no offset) parsed into Quote.timestamp for the staleness gate."""
        key = f"{exchange}:{symbol}"
        data = await self._request("GET", "/quote", params=[("i", key)])
        entry = data.get(key) or (next(iter(data.values())) if len(data) == 1 else None)
        if entry is None:
            raise BrokerAPIError(f"No quote returned for {key}")
        ts = parse_broker_timestamp(entry.get("last_trade_time") or entry.get("timestamp"))
        return Quote(symbol=symbol, ltp=float(entry["last_price"]), volume=entry.get("volume", 0.0) or 0.0,
                     oi=entry.get("oi"), timestamp=ts)

    async def disconnect(self) -> None:
        """`DELETE /session/token` invalidates the Kite access token; the local copy is dropped
        regardless of the broker's answer."""
        try:
            if self._access_token:
                await self._request(
                    "DELETE", "/session/token",
                    params={"api_key": self.credentials.api_key, "access_token": self._access_token},
                )
        finally:
            self._access_token = None

    async def get_quote(self, symbols: List[str]) -> Dict[str, Quote]:
        data = await self._request("GET", "/quote", params=[("i", s) for s in symbols])
        quotes: Dict[str, Quote] = {}
        for symbol, entry in data.items():
            ohlc = entry.get("ohlc", {})
            depth = entry.get("depth", {})
            best_buy = (depth.get("buy") or [{}])[0]
            best_sell = (depth.get("sell") or [{}])[0]
            quotes[symbol] = Quote(
                symbol=symbol,
                ltp=entry["last_price"],
                open=ohlc.get("open", 0.0),
                high=ohlc.get("high", 0.0),
                low=ohlc.get("low", 0.0),
                close=ohlc.get("close", 0.0),
                volume=entry.get("volume", 0.0),
                oi=entry.get("oi"),
                bid=best_buy.get("price"),
                ask=best_sell.get("price"),
                bid_qty=best_buy.get("quantity"),
                ask_qty=best_sell.get("quantity"),
            )
        return quotes

    async def get_historical_data(
        self, symbol: str, exchange: str, interval: str, from_date: datetime, to_date: datetime
    ) -> List[OHLCVBar]:
        kite_interval = KITE_INTERVAL_MAP.get(interval, interval)
        instruments = await self.get_instruments(exchange)
        match = next((i for i in instruments if i.tradingsymbol == symbol), None)
        if match is None:
            raise BrokerAPIError(f"Instrument {exchange}:{symbol} not found")

        data = await self._request(
            "GET",
            f"/instruments/historical/{match.instrument_token}/{kite_interval}",
            params={"from": from_date.strftime("%Y-%m-%d %H:%M:%S"), "to": to_date.strftime("%Y-%m-%d %H:%M:%S")},
        )
        return [
            OHLCVBar(timestamp=c[0], open=c[1], high=c[2], low=c[3], close=c[4], volume=c[5] if len(c) > 5 else 0.0)
            for c in data.get("candles", [])
        ]

    async def get_option_chain(self, underlying: str, expiry: Optional[date] = None) -> OptionChain:
        instruments = await self.get_instruments("NFO")
        matches = [i for i in instruments if i.name == underlying and i.instrument_type in ("CE", "PE")]
        if expiry is not None:
            expiry_str = expiry.isoformat()
            matches = [i for i in matches if i.expiry == expiry_str]
        elif matches:
            nearest = min(i.expiry for i in matches if i.expiry)
            matches = [i for i in matches if i.expiry == nearest]

        if not matches:
            return OptionChain(underlying=underlying, expiry=expiry.isoformat() if expiry else "")

        symbols = [f"{i.exchange}:{i.tradingsymbol}" for i in matches]
        quotes = await self.get_quote(symbols)

        rows_by_strike: Dict[float, OptionChainRow] = {}
        for instrument in matches:
            symbol_key = f"{instrument.exchange}:{instrument.tradingsymbol}"
            quote = quotes.get(symbol_key)
            if quote is None or instrument.strike is None:
                continue
            row = rows_by_strike.setdefault(instrument.strike, OptionChainRow(strike=instrument.strike))
            if instrument.instrument_type == "CE":
                row.call_oi, row.call_ltp, row.call_volume = quote.oi, quote.ltp, quote.volume
                row.call_bid, row.call_ask = quote.bid, quote.ask
            else:
                row.put_oi, row.put_ltp, row.put_volume = quote.oi, quote.ltp, quote.volume
                row.put_bid, row.put_ask = quote.bid, quote.ask

        return OptionChain(
            underlying=underlying,
            expiry=matches[0].expiry or "",
            rows=sorted(rows_by_strike.values(), key=lambda r: r.strike),
        )

    async def place_order(self, order: BrokerOrderRequest) -> BrokerOrderResponse:
        data = await self._request(
            "POST",
            "/orders/regular",
            data={
                "tradingsymbol": order.symbol,
                "exchange": order.exchange,
                "transaction_type": order.transaction_type.value,
                "order_type": order.order_type,
                "quantity": order.quantity,
                "product": order.product,
                "price": order.price or 0,
                "trigger_price": order.trigger_price or 0,
                "validity": order.validity,
                "tag": order.tag or "",
            },
        )
        return BrokerOrderResponse(order_id=data["order_id"], status="OPEN", raw=data)

    async def modify_order(
        self, order_id: str, quantity: Optional[float] = None, price: Optional[float] = None,
        trigger_price: Optional[float] = None, order_type: Optional[str] = None,
    ) -> BrokerOrderResponse:
        payload = {k: v for k, v in {
            "quantity": quantity, "price": price, "trigger_price": trigger_price, "order_type": order_type,
        }.items() if v is not None}
        data = await self._request("PUT", f"/orders/regular/{order_id}", data=payload)
        return BrokerOrderResponse(order_id=data["order_id"], status="MODIFIED", raw=data)

    async def cancel_order(self, order_id: str) -> BrokerOrderResponse:
        data = await self._request("DELETE", f"/orders/regular/{order_id}")
        return BrokerOrderResponse(order_id=data["order_id"], status="CANCELLED", raw=data)

    async def get_order_book(self) -> List[BrokerOrderStatus]:
        data = await self._request("GET", "/orders")
        return [
            BrokerOrderStatus(
                order_id=o["order_id"],
                symbol=o["tradingsymbol"],
                transaction_type=OrderSide(o["transaction_type"]),
                quantity=o["quantity"],
                filled_quantity=o.get("filled_quantity", 0),
                order_type=o["order_type"],
                status=o["status"],
                price=o.get("price"),
                average_price=o.get("average_price"),
                placed_at=o.get("order_timestamp"),
            )
            for o in data
        ]

    async def get_trade_book(self) -> List[BrokerTradeEntry]:
        data = await self._request("GET", "/trades")
        return [
            BrokerTradeEntry(
                trade_id=t["trade_id"],
                order_id=t["order_id"],
                symbol=t["tradingsymbol"],
                transaction_type=OrderSide(t["transaction_type"]),
                quantity=t["quantity"],
                price=t["average_price"],
                timestamp=t.get("fill_timestamp"),
            )
            for t in data
        ]

    async def get_positions(self) -> List[BrokerPosition]:
        data = await self._request("GET", "/portfolio/positions")
        return [
            BrokerPosition(
                symbol=p["tradingsymbol"], exchange=p["exchange"], product=p["product"],
                quantity=p["quantity"], average_price=p["average_price"], ltp=p["last_price"], pnl=p["pnl"],
            )
            for p in data.get("net", [])
        ]

    async def get_holdings(self) -> List[BrokerHolding]:
        data = await self._request("GET", "/portfolio/holdings")
        return [
            BrokerHolding(
                symbol=h["tradingsymbol"], exchange=h["exchange"],
                quantity=h["quantity"], average_price=h["average_price"], ltp=h["last_price"], pnl=h["pnl"],
            )
            for h in data
        ]

    async def get_order_margin(self, order: BrokerOrderRequest) -> Optional[float]:
        """Kite's order-margin calculator (`POST /margins/orders`): one entry per leg with `total`."""
        payload = [{
            "exchange": order.exchange, "tradingsymbol": order.symbol, "transaction_type": order.transaction_type.value,
            "variety": "regular", "product": order.product, "order_type": order.order_type, "quantity": int(order.quantity),
            "price": order.price or 0, "trigger_price": order.trigger_price or 0,
        }]
        try:
            data = await self._request("POST", "/margins/orders", json=payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Kite margin calculator failed for %s: %s", order.symbol, exc)
            return None
        legs = data if isinstance(data, list) else data.get("data", data)
        if isinstance(legs, list) and legs and isinstance(legs[0], dict) and legs[0].get("total") is not None:
            return float(legs[0]["total"])
        return None

    async def get_margins(self) -> MarginInfo:
        data = await self._request("GET", "/user/margins")
        equity = data.get("equity", {})
        available = equity.get("available", {})
        return MarginInfo(
            available_cash=available.get("cash", 0.0),
            used_margin=equity.get("utilised", {}).get("debits", 0.0),
            available_margin=available.get("live_balance", available.get("cash", 0.0)),
            total_margin=equity.get("net"),
        )
