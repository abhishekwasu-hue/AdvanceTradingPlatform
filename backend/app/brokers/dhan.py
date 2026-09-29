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

import csv
import io
import logging
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
STATUS_MAP = {"TRADED": "COMPLETE", "CANCELLED": "CANCELLED", "REJECTED": "REJECTED", "EXPIRED": "EXPIRED", "PENDING": "OPEN", "TRANSIT": "OPEN", "PART_TRADED": "OPEN", "TRIGGERED": "OPEN"}
INTERVAL_MAP = {"1min": "1", "5min": "5", "15min": "15", "25min": "25", "60min": "60"}
INSTRUMENT_KIND = {"EQUITY": "EQUITY", "INDEX": "INDEX", "FUTIDX": "FUTIDX", "OPTIDX": "OPTIDX", "FUTSTK": "FUTSTK", "OPTSTK": "OPTSTK", "FUTCOM": "FUTCOM", "OPTFUT": "OPTFUT"}
AUTH_ERROR_CODES = ("DH-901", "DH-902", "DH-903", "DH-808", "DH-809")
QUOTE_BATCH = 500
MASTER_CACHE_TTL_SECONDS = 6 * 60 * 60


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
        self._client = client or httpx.AsyncClient(base_url=self.BASE_URL, timeout=15.0)
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
        response = await self._client.request(method, path, json=json_body, headers=self._headers())
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
        response = await self._client.get(SCRIP_MASTER_URL)
        if response.status_code >= 400:
            raise BrokerAPIError("Failed to fetch the Dhan scrip master", response.status_code, response.text)
        rows = list(csv.DictReader(io.StringIO(response.text)))
        self._master = (time.monotonic(), rows)
        return rows

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

    async def _resolve(self, symbol: str, exchange: str = "NSE") -> Tuple[str, str, str]:
        """Platform symbol -> (exchangeSegment, securityId, instrument kind)."""
        exchange = (exchange or "NSE").upper()
        if ":" in symbol:
            exchange, symbol = symbol.split(":", 1)
            exchange = exchange.upper()
        key = symbol.strip().upper()
        alias = INDEX_ALIASES.get(key)
        if alias and alias in INDEX_SECURITY_IDS and exchange in ("NSE", "BSE"):
            return ("IDX_I", INDEX_SECURITY_IDS[alias], "INDEX")
        if (exchange, key) not in self._symbol_map:
            await self.get_instruments(exchange)
        hit = self._symbol_map.get((exchange, key))
        if hit is None:
            raise BrokerAPIError(f"Instrument {exchange}:{symbol} not found in the Dhan scrip master")
        return hit[0], hit[1], hit[2]

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
        for key in keys:
            exchange, plain = (key.split(":", 1) if ":" in key else ("NSE", key))
            segment, security_id, _kind = await self._resolve(plain, exchange)
            by_segment.setdefault(segment, []).append(int(security_id))
            back[(segment, str(security_id))] = key
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
                bars.append(OHLCVBar(timestamp=datetime.fromtimestamp(int(float(ts)), tz=timezone.utc).astimezone(IST), open=float(opens[i]), high=float(highs[i]),
                                     low=float(lows[i]), close=float(closes[i]), volume=float(volumes[i]) if i < len(volumes) else 0.0))
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
        segment, security_id, _kind = await self._resolve(order.symbol, order.exchange)
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
                                         placed_at=_parse_time(o.get("createTime") or o.get("updateTime"))))
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
