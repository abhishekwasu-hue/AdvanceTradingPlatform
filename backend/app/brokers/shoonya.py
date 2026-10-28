import hashlib
import json
from datetime import date, datetime, time
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from app.brokers import master_cache
from app.brokers.base import BrokerInterface
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError, BrokerBudgetUsed
from app.brokers.timestamps import parse_broker_timestamp
from app.brokers.totp import totp_code
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
# UI2-4a2-pre: daily bars come from NorenApi's EOD call (`/EODChartData`, `get_daily_price_series` in the broker's own
# client library NorenRestApiPy), never from the minute series (`/TPSeries` has no daily `intrv`, so "day" used to fall
# back to 1-minute bars).
DAILY_INTERVALS = ("day", "1day", "1d", "D")
IST = ZoneInfo("Asia/Kolkata")

PRODUCT_MAP = {"MIS": "I", "CNC": "C", "NRML": "M"}
# Platform order types -> NorenApi `prctyp` codes. SL-M (stop-loss market) is what the autonomous
# worker places as the protective stop after a live fill (BrokerInterface.place_stop_loss_order).
ORDER_TYPE_MAP = {"MARKET": "MKT", "LIMIT": "LMT", "SL": "SL-LMT", "SL-M": "SL-MKT"}


class ShoonyaBroker(BrokerInterface):
    """Shoonya (Finvasia) adapter, built on the NorenApi convention several Indian discount
    brokers share: every authenticated call is a POST with a form body of `jData=<json>` (and
    `jKey=<session token>` once logged in), and responses come back as `{"stat": "Ok", ...}` or
    `{"stat": "Not_Ok", "emsg": "..."}`. See https://shoonya.com/api-documentation - endpoint
    paths and field names follow the public NorenApi docs as of training cutoff; verify against
    the live docs before production use, since broker APIs evolve.

    Requires `credentials.client_id` (uid), `credentials.api_secret` (login password),
    `credentials.api_key` (used to derive the appkey hash), and `credentials.totp_secret` for the
    second factor (factor2): the base32 TOTP secret, turned into the current code at login (see
    `app.brokers.totp`); a 6- or 8-digit code, or a DOB/PAN on accounts set up that way, is sent as-is.
    Optional extra fields (via `BrokerCredentials`' `extra="allow"`): `vc` (vendor code, default
    `f"{uid}_API"`) and `imei` (device id, default `"abc1111"`).
    """

    name = "shoonya"
    native_intervals = frozenset(SHOONYA_INTERVAL_MINUTES) | {"day"}   # SCR-FIX A1 (day: the EOD call)
    BASE_URL = "https://api.shoonya.com/NorenWClientTP"

    def __init__(self, credentials: BrokerCredentials, client: Optional[httpx.AsyncClient] = None) -> None:
        if not (credentials.client_id and credentials.api_secret and credentials.api_key):
            raise BrokerAuthenticationError(
                "Shoonya adapter requires credentials.client_id (uid), api_secret (password) and api_key"
            )
        self.credentials = credentials
        self._client = client or httpx.AsyncClient(base_url=self.BASE_URL, timeout=15.0)
        # The session (`susertoken`) lives in `_access_token`, the BrokerInterface convention, so a login's token is read
        # back by token_lifecycle, stored encrypted and handed to the next adapter as `credentials.access_token` (P2a).
        self._access_token: Optional[str] = credentials.access_token or None
        self._profile: Optional[BrokerProfile] = None
        # Shoonya's account id is the user id for a retail account; a login replaces it with the `actid` it returns.
        self._actid: Optional[str] = self._uid if self._access_token else None
        # UI2-4a2-pre review: symbol -> token lookups shared by the process when built without a client (master_cache)
        self._shared_master = client is None
        self._tokens: Dict[str, Tuple[str, str]] = {}

    @property
    def _session_token(self) -> Optional[str]:
        """The NorenApi `jKey` (kept as `_access_token`); the old attribute name still reads and writes it."""
        return self._access_token

    @_session_token.setter
    def _session_token(self, value: Optional[str]) -> None:
        self._access_token = value

    @property
    def _uid(self) -> str:
        return self.credentials.client_id  # type: ignore[return-value]

    @property
    def _vc(self) -> str:
        return getattr(self.credentials, "vc", None) or f"{self._uid}_API"

    @property
    def _imei(self) -> str:
        return getattr(self.credentials, "imei", None) or "abc1111"

    async def _post(self, path: str, payload: dict, authenticated: bool = True) -> Any:
        if authenticated and not self._session_token:
            raise BrokerAuthenticationError("Not authenticated - call authenticate() first")

        data = {"jData": json.dumps(payload)}
        if authenticated:
            data["jKey"] = self._access_token or ""      # set: checked just above

        response = await self._client.post(path, data=data)
        try:
            body = response.json()
        except ValueError as exc:
            raise BrokerAPIError(f"Non-JSON response from Shoonya: {response.text[:200]}", response.status_code) from exc

        # The series and book calls (/TPSeries, /EODChartData, /OrderBook, /TradeBook, /PositionBook, /Holdings) answer a JSON list on success; only a dict carries `stat`.
        failed = isinstance(body, dict) and body.get("stat") == "Not_Ok"
        if response.status_code >= 400 or failed:
            emsg = body.get("emsg", "Shoonya API error") if isinstance(body, dict) else "Shoonya API error"
            raise BrokerAPIError(emsg, response.status_code, response.text)
        return body

    async def authenticate(self) -> BrokerProfile:
        pwd_hash = hashlib.sha256(self.credentials.api_secret.encode()).hexdigest()  # type: ignore[union-attr]
        appkey_hash = hashlib.sha256(f"{self._uid}|{self.credentials.api_key}".encode()).hexdigest()
        payload = {
            "apkversion": "1.0.0",
            "uid": self._uid,
            "pwd": pwd_hash,
            "factor2": totp_code(self.credentials.totp_secret),
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
            if not self._access_token:
                raise BrokerAuthenticationError("Not authenticated - call authenticate() first")
            # A stored session: one authenticated read (`/Limits`) proves it is still alive before it is reported.
            await self._post("/Limits", {"uid": self._uid, "actid": self._actid or self._uid})
            self._profile = BrokerProfile(broker=self.name, user_id=self._uid)
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

    async def _lookup(self, symbol: str, exchange: str) -> Tuple[str, str]:
        """Platform symbol -> (Shoonya token, Noren trading symbol) via `/SearchScrip` for that symbol: the exact `tsym`,
        else `<symbol>-EQ` (an NSE/BSE equity is "RELIANCE-EQ" at Noren), else the only hit. Cached per process
        (master_cache) - neither changes within a day. The lookup is a broker call: with a `budget_hook` set it takes
        a token first (none -> BrokerBudgetUsed, no call)."""
        exchange = (exchange or "NSE").upper()
        wanted = symbol.strip().upper()

        async def look_up() -> Tuple[str, str]:
            if self.budget_hook is not None and not await self.budget_hook():
                raise BrokerBudgetUsed(f"broker rate budget used - {exchange}:{symbol} not looked up this time")
            body = await self._post("/SearchScrip", {"uid": self._uid, "exch": exchange, "stext": wanted})
            values = [v for v in (body.get("values") or []) if v.get("token")] if isinstance(body, dict) else []
            by_tsym = {str(v.get("tsym", "")).upper(): (str(v["token"]), str(v.get("tsym"))) for v in values}
            hit = by_tsym.get(wanted) or by_tsym.get(f"{wanted}-EQ")
            if hit is None and len(values) == 1:
                hit = (str(values[0]["token"]), str(values[0].get("tsym") or wanted))
            if hit is None:
                raise BrokerAPIError(f"Instrument {exchange}:{symbol} not found")
            return hit
        key = f"{exchange}:{wanted}"
        if self._shared_master:
            return await master_cache.shared(self.name, key, look_up)
        if key not in self._tokens:
            self._tokens[key] = await look_up()
        return self._tokens[key]

    async def _token(self, symbol: str, exchange: str) -> str:
        return (await self._lookup(symbol, exchange))[0]

    async def get_quote_for_symbol(self, symbol: str, exchange: str = "NSE") -> Optional[Quote]:
        """UI2-4a2-pre review: one `/GetQuotes` call for one trading symbol (its token looked up first, cached), with
        `c` - the previous session's close - as `prev_close` and `lut` (epoch seconds) as the quote's time."""
        token = await self._token(symbol, exchange)
        body = await self._post("/GetQuotes", {"uid": self._uid, "exch": exchange.upper(), "token": token})
        return Quote(symbol=symbol, ltp=float(body.get("lp", 0) or 0), open=float(body.get("o", 0) or 0),
                     high=float(body.get("h", 0) or 0), low=float(body.get("l", 0) or 0), close=float(body.get("c", 0) or 0),
                     volume=float(body.get("v", 0) or 0), oi=float(body["oi"]) if body.get("oi") else None,
                     bid=float(body["bp1"]) if body.get("bp1") else None, ask=float(body["sp1"]) if body.get("sp1") else None,
                     prev_close=float(body["c"]) if body.get("c") not in (None, "", "0", 0) else None,
                     timestamp=parse_broker_timestamp(body.get("lut")))

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
        if interval in DAILY_INTERVALS:
            # /EODChartData names the symbol by Noren's trading symbol ("NSE:RELIANCE-EQ", not the platform's
            # "RELIANCE" - that answers "no data", an empty chart), from the same cached lookup
            _token, tradingsymbol = await self._lookup(symbol, exchange)
            return await self._daily_bars(exchange.upper(), tradingsymbol, from_date, to_date)
        # The symbol's own token (a /SearchScrip for it, cached) - get_instruments searches the exchange's name, which
        # does not list every symbol.
        token = await self._token(symbol, exchange)

        body = await self._post("/TPSeries", {
            "uid": self._uid, "exch": exchange, "token": token,
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

    async def _daily_bars(self, exchange: str, tradingsymbol: str, from_date: datetime, to_date: datetime) -> List[OHLCVBar]:
        """Daily bars from `/EODChartData` for the IST dates `from_date`..`to_date` (both whole days), oldest first, each
        stamped at 00:00 IST of its trading date - like every other broker's daily bars here. The request names the
        symbol as "EXCH:TSYM" and the range in epoch seconds; each row comes back as a JSON string (or an object) with
        `time` "DD-MON-YYYY", `ssboe` (that date's midnight in epoch seconds), `into/inth/intl/intc` and `intv`."""
        start = datetime.combine(_ist_date(from_date), time.min, tzinfo=IST)
        end = datetime.combine(_ist_date(to_date), time.max, tzinfo=IST)
        try:
            body = await self._post("/EODChartData", {"uid": self._uid, "sym": f"{exchange}:{tradingsymbol}",
                                                  "from": str(int(start.timestamp())), "to": str(int(end.timestamp()))})
        except BrokerAPIError as exc:
            if "no data" in str(exc).lower():         # a range with no sessions (a holiday stretch) is no bars, not an error
                return []
            raise
        rows = body if isinstance(body, list) else (body.get("values") or [])
        by_day: Dict[date, OHLCVBar] = {}
        for raw in rows:
            row = json.loads(raw) if isinstance(raw, str) else raw
            day = _eod_date(row)
            if day is None or not start.date() <= day <= end.date():
                continue
            by_day[day] = OHLCVBar(timestamp=datetime.combine(day, time.min, tzinfo=IST), open=float(row["into"]),
                                   high=float(row["inth"]), low=float(row["intl"]), close=float(row["intc"]),
                                   volume=float(row.get("intv", 0) or 0))
        return [by_day[d] for d in sorted(by_day)]

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
            "prctyp": ORDER_TYPE_MAP.get(order.order_type, "LMT"),
            "trgprc": str(order.trigger_price or 0),
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
            payload["prctyp"] = ORDER_TYPE_MAP.get(order_type, "LMT")
        if trigger_price is not None:
            payload["trgprc"] = str(trigger_price)
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
                placed_at=parse_broker_timestamp(o.get("norentm")),   # "HH:MM:SS DD-MM-YYYY" IST; None when absent
                tag=o.get("remarks") or None, exchange=o.get("exch") or None,
                product=next((k for k, v in PRODUCT_MAP.items() if v == o.get("prd")), None),
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


def _ist_date(value: datetime) -> date:
    """The IST trading date of a request bound (a naive datetime is taken as IST wall time, as the service builds them)."""
    if isinstance(value, datetime):
        return value.astimezone(IST).date() if value.tzinfo else value.date()
    return value


def _eod_date(row: dict) -> Optional[date]:
    """The trading date of one /EODChartData row: its `time` ("21-SEP-2022"), else its `ssboe` (that date's midnight) read in
    IST - right whether the broker counts the midnight in UTC (05:30 IST, same date) or in IST (00:00 IST); a UTC reading
    of an IST midnight would slip to the day before."""
    text = str(row.get("time") or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.split(" ")[0].title(), fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromtimestamp(int(float(row["ssboe"])), tz=IST).date()
    except (KeyError, TypeError, ValueError):
        return None
