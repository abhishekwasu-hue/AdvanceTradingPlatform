import hashlib
import json
from datetime import date, datetime
from typing import Dict, List, Optional

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

SHOONYA_INTERVAL_MINUTES = {
    "1min": "1", "3min": "3", "5min": "5", "10min": "10", "15min": "15", "30min": "30", "60min": "60",
}

PRODUCT_MAP = {"MIS": "I", "CNC": "C", "NRML": "M"}


class ShoonyaBroker(BrokerInterface):
    """Shoonya (Finvasia) adapter, built on the NorenApi convention several Indian discount
    brokers share: every authenticated call is a POST with a form body of `jData=<json>` (and
    `jKey=<session token>` once logged in), and responses come back as `{"stat": "Ok", ...}` or
    `{"stat": "Not_Ok", "emsg": "..."}`. See https://shoonya.com/api-documentation - endpoint
    paths and field names follow the public NorenApi docs as of training cutoff; verify against
    the live docs before production use, since broker APIs evolve.

    Requires `credentials.client_id` (uid), `credentials.api_secret` (login password),
    `credentials.api_key` (used to derive the appkey hash), and `credentials.totp_secret` used
    here as the current 2FA code (factor2 - a TOTP or DOB/PAN depending on account setup).
    Optional extra fields (via `BrokerCredentials`' `extra="allow"`): `vc` (vendor code, default
    `f"{uid}_API"`) and `imei` (device id, default `"abc1111"`).
    """

    name = "shoonya"
    BASE_URL = "https://api.shoonya.com/NorenWClientTP"

    def __init__(self, credentials: BrokerCredentials, client: Optional[httpx.AsyncClient] = None) -> None:
        if not (credentials.client_id and credentials.api_secret and credentials.api_key):
            raise BrokerAuthenticationError(
                "Shoonya adapter requires credentials.client_id (uid), api_secret (password) and api_key"
            )
        self.credentials = credentials
        self._client = client or httpx.AsyncClient(base_url=self.BASE_URL, timeout=15.0)
        self._session_token: Optional[str] = None
        self._profile: Optional[BrokerProfile] = None
        self._actid: Optional[str] = None

    @property
    def _uid(self) -> str:
        return self.credentials.client_id  # type: ignore[return-value]

    @property
    def _vc(self) -> str:
        return getattr(self.credentials, "vc", None) or f"{self._uid}_API"

    @property
    def _imei(self) -> str:
        return getattr(self.credentials, "imei", None) or "abc1111"

    async def _post(self, path: str, payload: dict, authenticated: bool = True) -> dict:
        if authenticated and not self._session_token:
            raise BrokerAuthenticationError("Not authenticated - call authenticate() first")

        data = {"jData": json.dumps(payload)}
        if authenticated:
            data["jKey"] = self._session_token

        response = await self._client.post(path, data=data)
        try:
            body = response.json()
        except ValueError as exc:
            raise BrokerAPIError(f"Non-JSON response from Shoonya: {response.text[:200]}", response.status_code) from exc

        if response.status_code >= 400 or body.get("stat") == "Not_Ok":
            raise BrokerAPIError(body.get("emsg", "Shoonya API error"), response.status_code, response.text)
        return body

    async def authenticate(self) -> BrokerProfile:
        pwd_hash = hashlib.sha256(self.credentials.api_secret.encode()).hexdigest()  # type: ignore[union-attr]
        appkey_hash = hashlib.sha256(f"{self._uid}|{self.credentials.api_key}".encode()).hexdigest()
        payload = {
            "apkversion": "1.0.0",
            "uid": self._uid,
            "pwd": pwd_hash,
            "factor2": self.credentials.totp_secret or "",
            "vc": self._vc,
            "appkey": appkey_hash,
            "imei": self._imei,
            "source": "API",
        }
        body = await self._post("/QuickAuth", payload, authenticated=False)
        self._session_token = body["susertoken"]
        self._actid = body.get("actid", self._uid)
        self._profile = BrokerProfile(
            broker=self.name, user_id=self._uid, name=body.get("uname"), email=body.get("email"),
        )
        return self._profile

    async def get_profile(self) -> BrokerProfile:
        if self._profile is None:
            raise BrokerAuthenticationError("Not authenticated - call authenticate() first")
        return self._profile

    async def get_instruments(self, exchange: Optional[str] = None) -> List[Instrument]:
        exchange = exchange or "NSE"
        body = await self._post("/SearchScrip", {"uid": self._uid, "exch": exchange, "stext": exchange})
        values = body.get("values", [])
        return [
            Instrument(
                instrument_token=row.get("token", ""),
                exchange=row.get("exch", exchange),
                tradingsymbol=row.get("tsym", ""),
                name=row.get("cname"),
                instrument_type=row.get("instname"),
                lot_size=int(row.get("ls", 1) or 1),
                tick_size=float(row.get("ti", 0.05) or 0.05),
                expiry=row.get("exd"),
                strike=float(row["strprc"]) if row.get("strprc") else None,
            )
            for row in values
        ]

    async def get_ltp(self, symbols: List[str]) -> Dict[str, float]:
        quotes = await self.get_quote(symbols)
        return {symbol: quote.ltp for symbol, quote in quotes.items()}

    async def get_quote(self, symbols: List[str]) -> Dict[str, Quote]:
        quotes: Dict[str, Quote] = {}
        for symbol in symbols:
            exch, token = symbol.split(":", 1) if ":" in symbol else ("NSE", symbol)
            body = await self._post("/GetQuotes", {"uid": self._uid, "exch": exch, "token": token})
            quotes[symbol] = Quote(
                symbol=symbol,
                ltp=float(body.get("lp", 0) or 0),
                open=float(body.get("o", 0) or 0), high=float(body.get("h", 0) or 0),
                low=float(body.get("l", 0) or 0), close=float(body.get("c", 0) or 0),
                volume=float(body.get("v", 0) or 0), oi=float(body["oi"]) if body.get("oi") else None,
                bid=float(body["bp1"]) if body.get("bp1") else None,
                ask=float(body["sp1"]) if body.get("sp1") else None,
            )
        return quotes

    async def get_historical_data(
        self, symbol: str, exchange: str, interval: str, from_date: datetime, to_date: datetime
    ) -> List[OHLCVBar]:
        instruments = await self.get_instruments(exchange)
        match = next((i for i in instruments if i.tradingsymbol == symbol), None)
        if match is None:
            raise BrokerAPIError(f"Instrument {exchange}:{symbol} not found")

        body = await self._post("/TPSeries", {
            "uid": self._uid, "exch": exchange, "token": match.instrument_token,
            "st": str(int(from_date.timestamp())), "et": str(int(to_date.timestamp())),
            "intrv": SHOONYA_INTERVAL_MINUTES.get(interval, "1"),
        })
        rows = body if isinstance(body, list) else body.get("values", [])
        bars = [
            OHLCVBar(
                timestamp=row["time"], open=float(row["into"]), high=float(row["inth"]),
                low=float(row["intl"]), close=float(row["intc"]), volume=float(row.get("intv", 0) or 0),
            )
            for row in rows
        ]
        return list(reversed(bars))

    async def get_option_chain(self, underlying: str, expiry: Optional[date] = None) -> OptionChain:
        body = await self._post("/GetOptionChain", {
            "uid": self._uid, "exch": "NFO", "tsym": underlying, "strprc": "0", "cnt": "20",
        })
        contracts = [v for v in body.get("values", []) if v.get("strprc") and v.get("token")]
        if not contracts:
            return OptionChain(underlying=underlying, expiry=expiry.isoformat() if expiry else "")

        # GetOptionChain lists each CE/PE contract (strike, token, option type) but not live
        # oi/ltp/volume - those come from a GetQuotes call per token, same as get_quote() already
        # does elsewhere in this adapter, and the same pattern ZerodhaBroker.get_option_chain uses.
        symbols = [f"{v.get('exch', 'NFO')}:{v['token']}" for v in contracts]
        quotes = await self.get_quote(symbols)

        rows_by_strike: Dict[float, OptionChainRow] = {}
        for v in contracts:
            strike = float(v["strprc"])
            symbol_key = f"{v.get('exch', 'NFO')}:{v['token']}"
            quote = quotes.get(symbol_key)
            if quote is None:
                continue
            row = rows_by_strike.setdefault(strike, OptionChainRow(strike=strike))
            option_type = (v.get("optt") or v.get("otype") or "").upper()
            if option_type == "CE":
                row.call_oi, row.call_ltp, row.call_volume = quote.oi, quote.ltp, quote.volume
                row.call_bid, row.call_ask = quote.bid, quote.ask
            elif option_type == "PE":
                row.put_oi, row.put_ltp, row.put_volume = quote.oi, quote.ltp, quote.volume
                row.put_bid, row.put_ask = quote.bid, quote.ask

        return OptionChain(
            underlying=underlying, expiry=expiry.isoformat() if expiry else "",
            rows=sorted(rows_by_strike.values(), key=lambda r: r.strike),
        )

    async def place_order(self, order: BrokerOrderRequest) -> BrokerOrderResponse:
        body = await self._post("/PlaceOrder", {
            "uid": self._uid, "actid": self._actid, "exch": order.exchange, "tsym": order.symbol,
            "qty": str(order.quantity), "prc": str(order.price or 0),
            "prd": PRODUCT_MAP.get(order.product, "I"),
            "trantype": "B" if order.transaction_type == OrderSide.BUY else "S",
            "prctyp": "MKT" if order.order_type == "MARKET" else "LMT",
            "ret": order.validity, "remarks": order.tag or "",
        })
        return BrokerOrderResponse(order_id=body["norenordno"], status="OPEN", raw=body)

    async def modify_order(
        self, order_id: str, quantity: Optional[float] = None, price: Optional[float] = None,
        trigger_price: Optional[float] = None, order_type: Optional[str] = None,
    ) -> BrokerOrderResponse:
        payload = {"uid": self._uid, "norenordno": order_id}
        if quantity is not None:
            payload["qty"] = str(quantity)
        if price is not None:
            payload["prc"] = str(price)
        if order_type is not None:
            payload["prctyp"] = "MKT" if order_type == "MARKET" else "LMT"
        body = await self._post("/ModifyOrder", payload)
        return BrokerOrderResponse(order_id=body["norenordno"], status="MODIFIED", raw=body)

    async def cancel_order(self, order_id: str) -> BrokerOrderResponse:
        body = await self._post("/CancelOrder", {"uid": self._uid, "norenordno": order_id})
        return BrokerOrderResponse(order_id=body["norenordno"], status="CANCELLED", raw=body)

    async def get_order_book(self) -> List[BrokerOrderStatus]:
        body = await self._post("/OrderBook", {"uid": self._uid})
        values = body if isinstance(body, list) else body.get("values", [])
        return [
            BrokerOrderStatus(
                order_id=o["norenordno"], symbol=o["tsym"],
                transaction_type=OrderSide.BUY if o.get("trantype") == "B" else OrderSide.SELL,
                quantity=int(o.get("qty", 0)), filled_quantity=int(o.get("fillshares", 0) or 0),
                order_type=o.get("prctyp", "MKT"), status=o.get("status", ""),
                price=float(o["prc"]) if o.get("prc") else None,
                average_price=float(o["avgprc"]) if o.get("avgprc") else None,
            )
            for o in values
        ]

    async def get_trade_book(self) -> List[BrokerTradeEntry]:
        body = await self._post("/TradeBook", {"uid": self._uid, "actid": self._actid})
        values = body if isinstance(body, list) else body.get("values", [])
        return [
            BrokerTradeEntry(
                trade_id=t.get("flid", t.get("norenordno", "")), order_id=t["norenordno"], symbol=t["tsym"],
                transaction_type=OrderSide.BUY if t.get("trantype") == "B" else OrderSide.SELL,
                quantity=int(t.get("qty", 0)), price=float(t.get("flprc", 0) or 0),
            )
            for t in values
        ]

    async def get_positions(self) -> List[BrokerPosition]:
        body = await self._post("/PositionBook", {"uid": self._uid, "actid": self._actid})
        values = body if isinstance(body, list) else body.get("values", [])
        return [
            BrokerPosition(
                symbol=p["tsym"], exchange=p.get("exch", "NSE"),
                product=next((k for k, v in PRODUCT_MAP.items() if v == p.get("prd")), "MIS"),
                quantity=int(p.get("netqty", 0)), average_price=float(p.get("netavgprc", 0) or 0),
                ltp=float(p.get("lp", 0) or 0), pnl=float(p.get("rpnl", 0) or 0) + float(p.get("urmtom", 0) or 0),
            )
            for p in values
        ]

    async def get_holdings(self) -> List[BrokerHolding]:
        body = await self._post("/Holdings", {"uid": self._uid, "actid": self._actid, "prd": "C"})
        values = body if isinstance(body, list) else body.get("values", [])
        holdings = []
        for h in values:
            scrips = h.get("exch_tsym", [{}])
            first = scrips[0] if scrips else {}
            holdings.append(BrokerHolding(
                symbol=first.get("tsym", ""), exchange=first.get("exch", "NSE"),
                quantity=int(h.get("holdqty", 0)), average_price=float(h.get("upldprc", 0) or 0),
            ))
        return holdings

    async def get_margins(self) -> MarginInfo:
        body = await self._post("/Limits", {"uid": self._uid, "actid": self._actid})
        cash = float(body.get("cash", 0) or 0)
        used = float(body.get("marginused", 0) or 0)
        return MarginInfo(available_cash=cash, used_margin=used, available_margin=cash - used)
