"""Phase AC: Angel One SmartAPI adapter against a mocked transport - login headers and TOTP,
scrip-master parsing and symbol resolution (RELIANCE-EQ, Nifty 50, NFO options), quotes in
batches, candles, order mapping (SL-M -> STOPLOSS_MARKET), order book / positions / holdings /
margins parsing, option chain assembly, error mapping and logout."""
import asyncio
import json
from datetime import datetime, timezone

import httpx
import pyotp
import pytest

from app.brokers.angel_one import AngelOneBroker, SCRIP_MASTER_URL, _parse_expiry, _totp_code
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.models import BrokerCredentials, BrokerOrderRequest
from app.brokers.registry import get_broker_adapter
from app.brokers.token_lifecycle import TOKEN_DAILY_EXPIRY_IST
from app.core.enums import OrderSide

run = asyncio.run
SECRET = "JBSWY3DPEHPK3PXP"
MASTER = [
    {"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE", "expiry": "", "strike": "-1.000000", "lotsize": "1", "instrumenttype": "", "exch_seg": "NSE", "tick_size": "5.000000"},
    {"token": "99926000", "symbol": "Nifty 50", "name": "NIFTY", "expiry": "", "strike": "-1.000000", "lotsize": "1", "instrumenttype": "AMXIDX", "exch_seg": "NSE", "tick_size": "0.000000"},
    {"token": "43001", "symbol": "NIFTY30OCT2626000CE", "name": "NIFTY", "expiry": "30OCT2026", "strike": "2600000.000000", "lotsize": "75", "instrumenttype": "OPTIDX", "exch_seg": "NFO", "tick_size": "5.000000"},
    {"token": "43002", "symbol": "NIFTY30OCT2626000PE", "name": "NIFTY", "expiry": "30OCT2026", "strike": "2600000.000000", "lotsize": "75", "instrumenttype": "OPTIDX", "exch_seg": "NFO", "tick_size": "5.000000"},
    {"token": "43003", "symbol": "NIFTY30OCT2626100CE", "name": "NIFTY", "expiry": "30OCT2026", "strike": "2610000.000000", "lotsize": "75", "instrumenttype": "OPTIDX", "exch_seg": "NFO", "tick_size": "5.000000"},
    {"token": "43004", "symbol": "NIFTY30OCT2626100PE", "name": "NIFTY", "expiry": "30OCT2026", "strike": "2610000.000000", "lotsize": "75", "instrumenttype": "OPTIDX", "exch_seg": "NFO", "tick_size": "5.000000"},
    {"token": "43005", "symbol": "NIFTY27NOV2626000CE", "name": "NIFTY", "expiry": "27NOV2026", "strike": "2600000.000000", "lotsize": "75", "instrumenttype": "OPTIDX", "exch_seg": "NFO", "tick_size": "5.000000"},
]


def _ok(data):
    return httpx.Response(200, json={"status": True, "message": "SUCCESS", "errorcode": "", "data": data})


class _Server:
    """A tiny fake SmartAPI: records requests, answers each path."""

    def __init__(self):
        self.requests = []
        self.quote_calls = 0
        self.order_book = [{"orderid": "2409290001", "variety": "STOPLOSS", "ordertype": "STOPLOSS_MARKET", "producttype": "INTRADAY", "duration": "DAY", "price": "0", "triggerprice": "2950",
                            "quantity": "10", "filledshares": "0", "tradingsymbol": "RELIANCE-EQ", "symboltoken": "2885", "exchange": "NSE", "transactiontype": "SELL",
                            "status": "trigger pending", "averageprice": "0", "updatetime": "29-Sep-2026 10:15:30"}]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if str(request.url) == SCRIP_MASTER_URL:
            return httpx.Response(200, content=json.dumps(MASTER).encode())
        assert request.headers["X-PrivateKey"] == "app-key" and request.headers["X-SourceID"] == "WEB"
        if path.endswith("/loginByPassword"):
            body = json.loads(request.content)
            assert body["clientcode"] == "A123456" and body["password"] == "1234" and body["totp"] == pyotp.TOTP(SECRET).now()
            return _ok({"jwtToken": "jwt-1", "refreshToken": "r-1", "feedToken": "f-1"})
        assert request.headers["Authorization"] == "Bearer jwt-1", path
        if path.endswith("/getProfile"):
            return _ok({"clientcode": "A123456", "name": "Test Trader", "email": "t@example.com", "exchanges": ["NSE", "NFO"]})
        if path.endswith("/logout"):
            return _ok({})
        if path.endswith("/quote/"):
            self.quote_calls += 1
            body = json.loads(request.content)
            fetched = []
            for exchange, tokens in body["exchangeTokens"].items():
                assert len(tokens) <= 50
                for token in tokens:
                    price = {"2885": 2999.5, "99926000": 26050.0}.get(token, 100.0 + int(token) % 50)
                    fetched.append({"exchange": exchange, "tradingSymbol": next(r["symbol"] for r in MASTER if r["token"] == token), "symbolToken": token, "ltp": price,
                                    "open": price - 5, "high": price + 5, "low": price - 10, "close": price - 2, "tradeVolume": 1000, "opnInterest": 5000 if exchange == "NFO" else 0,
                                    "exchTradeTime": "29-Sep-2026 10:15:00", "depth": {"buy": [{"price": price - 0.5, "quantity": 100, "orders": 2}], "sell": [{"price": price + 0.5, "quantity": 120, "orders": 3}]}})
            return _ok({"fetched": fetched, "unfetched": []})
        if path.endswith("/getCandleData"):
            body = json.loads(request.content)
            assert body["interval"] == "FIVE_MINUTE" and body["symboltoken"] == "2885" and body["fromdate"] == "2026-09-29 09:15"
            return _ok([["2026-09-29T09:15:00+05:30", 2990, 3001, 2988, 2999.5, 12000], ["2026-09-29T09:20:00+05:30", 2999.5, 3005, 2995, 3002, 9000]])
        if path.endswith("/placeOrder"):
            body = json.loads(request.content)
            self.last_order = body
            if body["tradingsymbol"] == "REJECT-EQ":
                return httpx.Response(200, json={"status": False, "message": "Insufficient funds", "errorcode": "AB1004", "data": None})
            return _ok({"script": body["tradingsymbol"], "orderid": "2409290002", "uniqueorderid": "u-2"})
        if path.endswith("/modifyOrder"):
            self.last_modify = json.loads(request.content)
            return _ok({"orderid": "2409290001"})
        if path.endswith("/cancelOrder"):
            self.last_cancel = json.loads(request.content)
            return _ok({"orderid": "2409290001"})
        if path.endswith("/getOrderBook"):
            return _ok(self.order_book)
        if path.endswith("/getTradeBook"):
            return _ok([{"fillid": "f1", "orderid": "2409290002", "tradingsymbol": "RELIANCE-EQ", "exchange": "NSE", "transactiontype": "BUY", "fillsize": "10", "fillprice": "2999.5", "filltime": "10:16:02"}])
        if path.endswith("/getPosition"):
            return _ok([{"tradingsymbol": "RELIANCE-EQ", "exchange": "NSE", "producttype": "INTRADAY", "netqty": "10", "avgnetprice": "2999.5", "ltp": "3002", "unrealised": "25", "buyavgprice": "2999.5"}])
        if path.endswith("/getAllHolding"):
            return _ok({"holdings": [{"tradingsymbol": "RELIANCE-EQ", "exchange": "NSE", "quantity": "5", "averageprice": "2500", "ltp": "3002", "profitandloss": "2510"}], "totalholding": {}})
        if path.endswith("/getRMS"):
            return _ok({"net": "150000.5", "availablecash": "120000", "utiliseddebits": "30000"})
        return httpx.Response(404, json={"status": False, "message": "no route", "errorcode": "AB9999"})


def _broker(server, **overrides):
    creds = BrokerCredentials(api_key="app-key", client_id="A123456", pin="1234", totp_secret=SECRET, **overrides)
    return AngelOneBroker(creds, client=httpx.AsyncClient(transport=httpx.MockTransport(server), base_url=AngelOneBroker.BASE_URL))


def test_helpers_totp_expiry_and_credential_requirements():
    assert _totp_code("123456") == "123456" and _totp_code(SECRET) == pyotp.TOTP(SECRET).now()
    assert _parse_expiry("30OCT2026") == "2026-10-30" and _parse_expiry("") is None
    assert TOKEN_DAILY_EXPIRY_IST["angel_one"].hour == 5
    with pytest.raises(BrokerAuthenticationError):
        AngelOneBroker(BrokerCredentials(client_id="A1", pin="1"))
    with pytest.raises(BrokerAuthenticationError):
        AngelOneBroker(BrokerCredentials(api_key="k"))
    assert isinstance(get_broker_adapter("angel_one", BrokerCredentials(api_key="k", access_token="jwt")), AngelOneBroker)


def test_login_profile_symbols_quotes_and_candles():
    server = _Server()
    broker = _broker(server)
    profile = run(broker.authenticate())
    assert profile.broker == "angel_one" and profile.user_id == "A123456" and profile.name == "Test Trader" and broker.access_token == "jwt-1"

    instruments = run(broker.get_instruments("NSE"))
    reliance = next(i for i in instruments if i.tradingsymbol == "RELIANCE")
    assert reliance.instrument_token == "2885" and reliance.tick_size == 0.05 and reliance.instrument_type == "EQ"
    nfo = run(broker.get_instruments("NFO"))
    ce = next(i for i in nfo if i.tradingsymbol == "NIFTY30OCT2626000CE")
    assert ce.strike == 26000.0 and ce.expiry == "2026-10-30" and ce.lot_size == 75 and ce.name == "NIFTY"
    assert run(broker._resolve("RELIANCE", "NSE")) == ("2885", "RELIANCE-EQ")
    assert run(broker._resolve("NIFTY", "NSE"))[0] == "99926000"                       # index alias
    with pytest.raises(BrokerAPIError):
        run(broker._resolve("NOSUCH", "NSE"))

    assert run(broker.get_ltp_for_symbol("RELIANCE")) == 2999.5
    ltps = run(broker.get_ltp(["NSE:RELIANCE", "NSE:NIFTY"]))
    assert ltps == {"NSE:RELIANCE": 2999.5, "NSE:NIFTY": 26050.0}
    quote = run(broker.get_quote_for_symbol("RELIANCE"))
    assert quote.bid == 2999.0 and quote.ask == 3000.0 and quote.volume == 1000 and quote.timestamp.year == 2026

    bars = run(broker.get_historical_data("RELIANCE", "NSE", "5min", datetime(2026, 9, 29, 3, 45, tzinfo=timezone.utc), datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)))
    assert len(bars) == 2 and bars[0].close == 2999.5 and bars[1].volume == 9000 and bars[0].timestamp.utcoffset().total_seconds() == 19800


def test_orders_books_positions_margins_and_logout():
    server = _Server()
    broker = _broker(server)
    run(broker.authenticate())
    placed = run(broker.place_stop_loss_order("RELIANCE", "NSE", OrderSide.SELL, 10, 2950.0, product="MIS", tag="ALGO123-xyz-toolongtag-cut"))
    assert placed.order_id == "2409290002" and placed.status == "OPEN"
    o = server.last_order
    assert o["variety"] == "STOPLOSS" and o["ordertype"] == "STOPLOSS_MARKET" and o["producttype"] == "INTRADAY" and o["tradingsymbol"] == "RELIANCE-EQ"
    assert o["symboltoken"] == "2885" and o["triggerprice"] == "2950.0" and o["quantity"] == "10" and len(o["ordertag"]) == 20
    limit = run(broker.place_order(BrokerOrderRequest(symbol="RELIANCE", transaction_type=OrderSide.BUY, quantity=5, order_type="LIMIT", product="CNC", price=2990)))
    assert limit.order_id and server.last_order["variety"] == "NORMAL" and server.last_order["ordertype"] == "LIMIT" and server.last_order["producttype"] == "DELIVERY"

    book = run(broker.get_order_book())
    assert book[0].symbol == "RELIANCE" and book[0].status == "TRIGGER_PENDING" and book[0].transaction_type == OrderSide.SELL and book[0].placed_at.day == 29
    modified = run(broker.modify_order("2409290001", trigger_price=2940.0))
    assert modified.status == "MODIFIED" and server.last_modify["variety"] == "STOPLOSS" and server.last_modify["triggerprice"] == "2940.0" and server.last_modify["symboltoken"] == "2885"
    cancelled = run(broker.cancel_order("2409290001"))
    assert cancelled.status == "CANCELLED" and server.last_cancel == {"variety": "STOPLOSS", "orderid": "2409290001"}

    trades = run(broker.get_trade_book())
    assert trades[0].symbol == "RELIANCE" and trades[0].quantity == 10 and trades[0].price == 2999.5 and trades[0].timestamp is not None
    positions = run(broker.get_positions())
    assert positions[0].symbol == "RELIANCE" and positions[0].product == "MIS" and positions[0].quantity == 10 and positions[0].pnl == 25
    holdings = run(broker.get_holdings())
    assert holdings[0].symbol == "RELIANCE" and holdings[0].quantity == 5 and holdings[0].pnl == 2510
    margins = run(broker.get_margins())
    assert margins.available_cash == 120000 and margins.used_margin == 30000 and margins.available_margin == 150000.5 and margins.total_margin == 180000.5

    run(broker.disconnect())
    assert broker.access_token is None and any(r.url.path.endswith("/logout") for r in server.requests)


def test_option_chain_from_master_and_quotes_and_error_mapping():
    server = _Server()
    broker = _broker(server)
    run(broker.authenticate())
    chain = run(broker.get_option_chain("NIFTY"))
    assert chain.expiry == "2026-10-30" and chain.underlying_ltp == 26050.0 and [r.strike for r in chain.rows] == [26000.0, 26100.0]
    row = chain.rows[0]
    assert row.call_ltp and row.put_ltp and row.call_oi == 5000 and row.put_bid is not None
    from datetime import date
    later = run(broker.get_option_chain("NIFTY", expiry=date(2026, 11, 27)))
    assert later.expiry == "2026-11-27" and len(later.rows) == 1 and later.rows[0].put_ltp is None

    # A rejected order is a BrokerAPIError carrying the broker's message and code; an auth code is an auth error.
    MASTER.append({"token": "1", "symbol": "REJECT-EQ", "name": "REJECT", "expiry": "", "strike": "-1", "lotsize": "1", "instrumenttype": "", "exch_seg": "NSE", "tick_size": "5"})
    try:
        broker._instruments_cache.clear(); broker._master = None
        with pytest.raises(BrokerAPIError) as exc:
            run(broker.place_order(BrokerOrderRequest(symbol="REJECT", transaction_type=OrderSide.BUY, quantity=1)))
        assert "Insufficient funds" in str(exc.value) and "AB1004" in str(exc.value)
    finally:
        MASTER.pop()

    def auth_fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": False, "message": "Invalid Token", "errorcode": "AG8001", "data": None})
    stale = AngelOneBroker(BrokerCredentials(api_key="app-key", access_token="expired"), client=httpx.AsyncClient(transport=httpx.MockTransport(auth_fail), base_url=AngelOneBroker.BASE_URL))
    with pytest.raises(BrokerAuthenticationError):
        run(stale.get_profile())
    with pytest.raises(BrokerAuthenticationError):
        run(AngelOneBroker(BrokerCredentials(api_key="k", client_id="A1", pin="1"), client=httpx.AsyncClient(transport=httpx.MockTransport(server), base_url=AngelOneBroker.BASE_URL)).get_margins())
