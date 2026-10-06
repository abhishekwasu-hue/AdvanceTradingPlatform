"""Phase AE: Fyers API v3 adapter against a mocked transport - auth-code exchange with appIdHash,
the app_id:token Authorization header, symbol-master parsing and ticker resolution (SBIN-EQ,
NIFTY50-INDEX, NFO options), batched quotes, candles, orders (SL-M -> type 3), books, positions,
holdings, funds, the option chain endpoint and error mapping."""
import asyncio
import hashlib
import json
from datetime import date, datetime, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.fyers import FyersBroker
from app.brokers.models import BrokerCredentials, BrokerOrderRequest
from app.brokers.registry import get_broker_adapter
from app.core.enums import OrderSide

run = asyncio.run
EXPIRY_EPOCH = 1793167200            # 2026-10-28 in IST
CM_CSV = "\n".join([
    "101000000003045,STATE BANK OF INDIA,0,1,0.05,INE062A01020,0915-1530,2026-09-29,0,NSE:SBIN-EQ,10,10,3045,,,,,",
    "101000000026000,NIFTY 50,0,1,0.05,,0915-1530,2026-09-29,0,NSE:NIFTY50-INDEX,10,10,26000,,,,,",
])
FO_CSV = "\n".join([
    f"101100000012345,NIFTY 28 OCT 26 26000 CE,14,75,0.05,,0915-1530,2026-09-29,{EXPIRY_EPOCH},NSE:NIFTY26OCT26000CE,10,11,12345,NIFTY,26000,CE,101000000026000,",
    f"101100000012346,NIFTY 28 OCT 26 26000 PE,14,75,0.05,,0915-1530,2026-09-29,{EXPIRY_EPOCH},NSE:NIFTY26OCT26000PE,10,11,12346,NIFTY,26000,PE,101000000026000,",
])


def _ok(**data):
    return httpx.Response(200, json={"s": "ok", "code": 200, "message": "", **data})


class _Server:
    def __init__(self):
        self.requests = []
        self.last = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        path = request.url.path
        if "public.fyers.in" in url:
            return httpx.Response(200, text=CM_CSV if "NSE_CM" in url else FO_CSV)
        if path.endswith("/validate-authcode"):
            body = json.loads(request.content)
            assert body["grant_type"] == "authorization_code" and body["code"] == "auth-code-1"
            assert body["appIdHash"] == hashlib.sha256(b"APP-100:secret-1").hexdigest()
            return _ok(access_token="tok-1", refresh_token="ref-1")
        assert request.headers["Authorization"] == "APP-100:tok-1", path
        if path.endswith("/profile"):
            return _ok(data={"fy_id": "XA12345", "name": "Test Trader", "email_id": "t@example.com"})
        if path.endswith("/quotes"):
            symbols = parse_qs(request.url.query.decode())["symbols"][0].split(",")
            assert len(symbols) <= 50
            price = {"NSE:SBIN-EQ": 812.5, "NSE:NIFTY50-INDEX": 26050.0}
            return _ok(d=[{"n": s, "s": "ok", "v": {"lp": price.get(s, 100.0), "open_price": 800, "high_price": 820, "low_price": 795, "prev_close_price": 805,
                                                     "volume": 1000, "bid": price.get(s, 100.0) - 0.05, "ask": price.get(s, 100.0) + 0.05, "tt": 1790673300}} for s in symbols])
        if path.endswith("/history"):
            q = parse_qs(request.url.query.decode())
            assert q["symbol"][0] == "NSE:SBIN-EQ" and q["resolution"][0] == "5" and q["range_from"][0] == "2026-09-29"
            return _ok(candles=[[1790653500, 800, 805, 798, 803, 12000], [1790653800, 803, 806, 801, 804.5, 9000]])
        if path.endswith("/options-chain-v3"):
            q = parse_qs(request.url.query.decode())
            assert q["symbol"][0] == "NSE:NIFTY50-INDEX"
            ts = q.get("timestamp", [None])[0]
            strike = 26000 if ts in (None, str(EXPIRY_EPOCH)) else 26100
            return _ok(data={"expiryData": [{"date": "28-10-2026", "expiry": str(EXPIRY_EPOCH)}, {"date": "04-11-2026", "expiry": "1793772000"}],
                             "optionsChain": [{"symbol": "NSE:NIFTY50-INDEX", "option_type": "", "strike_price": -1, "ltp": 26050.0},
                                              {"symbol": "NSE:NIFTY26OCT26000CE", "option_type": "CE", "strike_price": strike, "ltp": 210.5, "oi": 5000, "oich": 120, "volume": 900, "bid": 210, "ask": 211},
                                              {"symbol": "NSE:NIFTY26OCT26000PE", "option_type": "PE", "strike_price": strike, "ltp": 180.0, "oi": 7000, "oich": -40, "volume": 700, "bid": 179.5, "ask": 180.5}]})
        if path.endswith("/orders/sync"):
            body = json.loads(request.content)
            self.last[request.method] = body
            if request.method == "POST" and body["symbol"] == "NSE:REJECT-EQ":
                return httpx.Response(200, json={"s": "error", "code": -99, "message": "Insufficient funds"})
            return _ok(id="24092900001", message="Order submitted successfully")
        if path.endswith("/orders"):
            return _ok(orderBook=[{"id": "24092900001", "symbol": "NSE:SBIN-EQ", "qty": 10, "filledQty": 0, "limitPrice": 0, "stopPrice": 790, "tradedPrice": 0, "type": 3, "side": -1,
                                   "productType": "INTRADAY", "status": 6, "orderDateTime": "29-Sep-2026 10:15:02"}])
        if path.endswith("/tradebook"):
            return _ok(tradeBook=[{"id": "t1", "orderNumber": "24092900002", "symbol": "NSE:SBIN-EQ", "tradePrice": 812.5, "tradedQty": 10, "side": 1, "orderDateTime": "29-Sep-2026 10:16:00"}])
        if path.endswith("/positions"):
            return _ok(netPositions=[{"symbol": "NSE:SBIN-EQ", "netQty": 10, "netAvg": 812.5, "ltp": 815, "unrealized_profit": 25, "pl": 25, "productType": "INTRADAY", "side": 1}])
        if path.endswith("/holdings"):
            return _ok(holdings=[{"symbol": "NSE:SBIN-EQ", "quantity": 5, "costPrice": 700, "ltp": 815, "pl": 575}])
        if path.endswith("/funds"):
            return _ok(fund_limit=[{"id": 1, "title": "Total Balance", "equityAmount": 150000.5}, {"id": 2, "title": "Utilized Amount", "equityAmount": 30000}, {"id": 10, "title": "Available Balance", "equityAmount": 120000}])
        return httpx.Response(404, json={"s": "error", "code": -404, "message": "no route"})


def _broker(server, **overrides):
    creds = BrokerCredentials(api_key="APP-100", api_secret="secret-1", request_token="auth-code-1", **overrides)
    return FyersBroker(creds, client=httpx.AsyncClient(transport=httpx.MockTransport(server)))


def test_credentials_login_symbols_quotes_and_candles():
    with pytest.raises(BrokerAuthenticationError):
        FyersBroker(BrokerCredentials(api_secret="s", request_token="c"))
    with pytest.raises(BrokerAuthenticationError):
        FyersBroker(BrokerCredentials(api_key="APP-100"))
    assert isinstance(get_broker_adapter("fyers", BrokerCredentials(api_key="APP-100", access_token="t")), FyersBroker)

    server = _Server()
    broker = _broker(server)
    profile = run(broker.authenticate())
    assert profile.user_id == "XA12345" and profile.name == "Test Trader" and broker.access_token == "tok-1"

    cm = run(broker.get_instruments("NSE"))
    sbin = next(i for i in cm if i.tradingsymbol == "SBIN")
    assert sbin.instrument_type == "EQ" and sbin.tick_size == 0.05 and sbin.instrument_token == "101000000003045"
    assert next(i for i in cm if i.tradingsymbol == "NIFTY").instrument_type == "INDEX"
    fo = run(broker.get_instruments("NFO"))
    ce = next(i for i in fo if i.tradingsymbol == "NIFTY26OCT26000CE")
    assert ce.strike == 26000.0 and ce.expiry == "2026-10-28" and ce.lot_size == 75 and ce.name == "NIFTY" and ce.instrument_type == "CE"
    assert run(broker._ticker("SBIN", "NSE")) == "NSE:SBIN-EQ" and run(broker._ticker("NIFTY")) == "NSE:NIFTY50-INDEX"
    assert run(broker._ticker("NIFTY26OCT26000CE", "NFO")) == "NSE:NIFTY26OCT26000CE"
    with pytest.raises(BrokerAPIError):
        run(broker._ticker("NOSUCH", "NSE"))

    assert run(broker.get_ltp_for_symbol("SBIN")) == 812.5
    assert run(broker.get_ltp(["NSE:SBIN", "NSE:NIFTY"])) == {"NSE:SBIN": 812.5, "NSE:NIFTY": 26050.0}
    quote = run(broker.get_quote_for_symbol("SBIN"))
    assert quote.bid == 812.45 and quote.ask == 812.55 and quote.close == 805 and quote.timestamp.year == 2026
    bars = run(broker.get_historical_data("SBIN", "NSE", "5min", datetime(2026, 9, 29, 3, 45, tzinfo=timezone.utc), datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)))
    assert len(bars) == 2 and bars[1].close == 804.5 and bars[0].timestamp.utcoffset().total_seconds() == 19800


def test_orders_books_funds_chain_and_errors():
    server = _Server()
    broker = _broker(server)
    run(broker.authenticate())
    placed = run(broker.place_stop_loss_order("SBIN", "NSE", OrderSide.SELL, 10, 790.0, tag="ALGO123-xyz-toolongtag-cut"))
    o = server.last["POST"]
    assert placed.order_id == "24092900001" and o["symbol"] == "NSE:SBIN-EQ" and o["type"] == 3 and o["side"] == -1 and o["stopPrice"] == 790.0
    assert o["productType"] == "INTRADAY" and o["qty"] == 10 and len(o["orderTag"]) == 20
    run(broker.place_order(BrokerOrderRequest(symbol="SBIN", transaction_type=OrderSide.BUY, quantity=5, order_type="LIMIT", product="CNC", price=810)))
    assert server.last["POST"]["type"] == 1 and server.last["POST"]["side"] == 1 and server.last["POST"]["productType"] == "CNC" and server.last["POST"]["limitPrice"] == 810.0
    assert run(broker.modify_order("24092900001", trigger_price=785.0)).status == "MODIFIED" and server.last["PATCH"] == {"id": "24092900001", "stopPrice": 785.0}
    assert run(broker.cancel_order("24092900001")).status == "CANCELLED" and server.last["DELETE"] == {"id": "24092900001"}

    book = run(broker.get_order_book())
    assert book[0].symbol == "SBIN" and book[0].order_type == "SL-M" and book[0].status == "OPEN" and book[0].transaction_type == OrderSide.SELL and book[0].placed_at.day == 29
    trades = run(broker.get_trade_book())
    assert trades[0].order_id == "24092900002" and trades[0].quantity == 10 and trades[0].price == 812.5
    positions = run(broker.get_positions())
    assert positions[0].symbol == "SBIN" and positions[0].product == "MIS" and positions[0].pnl == 25 and positions[0].exchange == "NSE"
    holdings = run(broker.get_holdings())
    assert holdings[0].quantity == 5 and holdings[0].average_price == 700 and holdings[0].pnl == 575
    margins = run(broker.get_margins())
    assert margins.available_cash == 120000 and margins.used_margin == 30000 and margins.total_margin == 150000.5

    chain = run(broker.get_option_chain("NIFTY"))
    assert chain.expiry == "2026-10-28" and chain.underlying_ltp == 26050.0 and [r.strike for r in chain.rows] == [26000.0]
    assert chain.rows[0].call_ltp == 210.5 and chain.rows[0].put_oi == 7000 and chain.rows[0].call_change_oi == 120 and chain.rows[0].put_bid == 179.5
    later = run(broker.get_option_chain("NIFTY", expiry=date(2026, 11, 4)))
    assert later.expiry == "2026-11-04" and later.rows[0].strike == 26100.0
    with pytest.raises(BrokerAPIError):
        run(broker.get_option_chain("NIFTY", expiry=date(2026, 12, 25)))

    broker._tickers[("NSE", "REJECT")] = "NSE:REJECT-EQ"
    with pytest.raises(BrokerAPIError) as exc:
        run(broker.place_order(BrokerOrderRequest(symbol="REJECT", transaction_type=OrderSide.BUY, quantity=1)))
    assert "Insufficient funds" in str(exc.value) and "-99" in str(exc.value)

    def expired(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"s": "error", "code": -16, "message": "Could not authenticate the user"})
    stale = FyersBroker(BrokerCredentials(api_key="APP-100", access_token="old"), client=httpx.AsyncClient(transport=httpx.MockTransport(expired)))
    with pytest.raises(BrokerAuthenticationError):
        run(stale.get_profile())
