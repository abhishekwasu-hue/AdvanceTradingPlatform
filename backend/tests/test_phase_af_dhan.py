"""Phase AF: Dhan API v2 adapter against a mocked transport - headers, scrip-master parsing and
resolution (equity, index ids, options), segment-batched quotes, parallel-array candles, order
mapping (SL-M -> STOP_LOSS_MARKET), modify/cancel, books, positions, holdings, funds (Dhan's own
spelling), the option chain (expiry list + chain with Greeks) and error mapping."""
import asyncio
import json
from datetime import date, datetime, timezone

import httpx
import pytest

from app.brokers.dhan import DhanBroker, SCRIP_MASTER_URL
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.models import BrokerCredentials, BrokerOrderRequest
from app.brokers.registry import get_broker_adapter
from app.core.enums import OrderSide

run = asyncio.run
MASTER = "\n".join([
    "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_EXPIRY_CODE,SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_TICK_SIZE,SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME",
    "NSE,E,2885,EQUITY,0,RELIANCE,1,RELIANCE,,0.000000,,0.050000,,ES,EQ,RELIANCE",
    "NSE,I,13,INDEX,0,NIFTY,1,NIFTY,,0.000000,,0.050000,,,,NIFTY",
    "NSE,D,43001,OPTIDX,0,NIFTY-Oct2026-26000-CE,75,NIFTY 28 OCT 26000 CALL,2026-10-28 14:30:00,26000.000000,CE,0.050000,M,OP,,NIFTY",
    "NSE,D,43002,OPTIDX,0,NIFTY-Oct2026-26000-PE,75,NIFTY 28 OCT 26000 PUT,2026-10-28 14:30:00,26000.000000,PE,0.050000,M,OP,,NIFTY",
])


class _Server:
    def __init__(self):
        self.requests = []
        self.last = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url, path = str(request.url), request.url.path
        if url == SCRIP_MASTER_URL:
            return httpx.Response(200, text=MASTER)
        assert request.headers["access-token"] == "tok-1" and request.headers["client-id"] == "1000001", path
        body = json.loads(request.content) if request.content else {}
        if path.endswith("/profile"):
            return httpx.Response(200, json={"dhanClientId": "1000001", "tokenValidity": "30/09/2026 06:00", "activeSegment": "Equity, Derivative"})
        if path.endswith("/marketfeed/quote"):
            self.last["quote"] = body
            data = {}
            for segment, ids in body.items():
                data[segment] = {str(i): {"last_price": {2885: 2999.5, 13: 26050.0}.get(i, 100.0), "ohlc": {"open": 2990, "high": 3005, "low": 2985, "close": 2980}, "volume": 1000,
                                          "oi": 0, "last_trade_time": "2026-09-29 10:15:00", "depth": {"buy": [{"price": 2999.0, "quantity": 100, "orders": 2}], "sell": [{"price": 3000.0, "quantity": 120, "orders": 3}]}} for i in ids}
            return httpx.Response(200, json={"data": data, "status": "success"})
        if path.endswith("/charts/intraday"):
            assert body["securityId"] == "2885" and body["exchangeSegment"] == "NSE_EQ" and body["instrument"] == "EQUITY" and body["interval"] == "5" and body["fromDate"] == "2026-09-29"
            return httpx.Response(200, json={"open": [2990, 2999.5], "high": [3001, 3005], "low": [2988, 2995], "close": [2999.5, 3002], "volume": [12000, 9000], "timestamp": [1790653500, 1790653800]})
        if path.endswith("/charts/historical"):
            assert body["expiryCode"] == 0 and body["instrument"] == "INDEX"
            return httpx.Response(200, json={"open": [26000], "high": [26100], "low": [25900], "close": [26050], "volume": [0], "timestamp": [1790620200]})
        if path.endswith("/optionchain/expirylist"):
            assert body == {"UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I"}
            return httpx.Response(200, json={"data": ["2026-10-28", "2026-11-04"], "status": "success"})
        if path.endswith("/optionchain"):
            self.last["chain"] = body
            strike = "26000.000000" if body["Expiry"] == "2026-10-28" else "26100.000000"
            return httpx.Response(200, json={"data": {"last_price": 26050.0, "oc": {strike: {
                "ce": {"greeks": {"delta": 0.52}, "implied_volatility": 12.5, "last_price": 210.5, "oi": 5000, "previous_oi": 4880, "volume": 900, "top_bid_price": 210, "top_ask_price": 211},
                "pe": {"greeks": {"delta": -0.48}, "implied_volatility": 13.1, "last_price": 180.0, "oi": 7000, "previous_oi": 7040, "volume": 700, "top_bid_price": 179.5, "top_ask_price": 180.5}}}}, "status": "success"})
        if path.endswith("/orders") and request.method == "POST":
            self.last["place"] = body
            if body["securityId"] == "999":
                return httpx.Response(400, json={"errorType": "Order_Error", "errorCode": "DH-906", "errorMessage": "Insufficient funds"})
            return httpx.Response(200, json={"orderId": "112409290001", "orderStatus": "TRANSIT"})
        if path.endswith("/orders/112409290001") and request.method == "GET":
            return httpx.Response(200, json={"orderId": "112409290001", "orderType": "STOP_LOSS_MARKET", "legName": "", "quantity": 10, "price": 0, "disclosedQuantity": 0, "triggerPrice": 2950, "validity": "DAY"})
        if path.endswith("/orders/112409290001") and request.method == "PUT":
            self.last["modify"] = body
            return httpx.Response(200, json={"orderId": "112409290001", "orderStatus": "TRANSIT"})
        if path.endswith("/orders/112409290001") and request.method == "DELETE":
            return httpx.Response(200, json={"orderId": "112409290001", "orderStatus": "CANCELLED"})
        if path.endswith("/orders"):
            return httpx.Response(200, json=[{"orderId": "112409290001", "tradingSymbol": "RELIANCE", "transactionType": "SELL", "orderStatus": "PENDING", "orderType": "STOP_LOSS_MARKET",
                                              "quantity": 10, "filledQty": 0, "price": 0, "triggerPrice": 2950, "averageTradedPrice": 0, "createTime": "2026-09-29 10:15:02"}])
        if path.endswith("/trades"):
            return httpx.Response(200, json=[{"orderId": "112409290002", "exchangeTradeId": "T1", "tradingSymbol": "RELIANCE", "transactionType": "BUY", "tradedQuantity": 10, "tradedPrice": 2999.5, "exchangeTime": "2026-09-29 10:16:00"}])
        if path.endswith("/positions"):
            return httpx.Response(200, json=[{"tradingSymbol": "RELIANCE", "exchangeSegment": "NSE_EQ", "productType": "INTRADAY", "netQty": 10, "costPrice": 2999.5, "buyAvg": 2999.5, "unrealizedProfit": 25, "realizedProfit": 0}])
        if path.endswith("/holdings"):
            return httpx.Response(200, json=[{"exchange": "NSE", "tradingSymbol": "RELIANCE", "totalQty": 5, "availableQty": 5, "avgCostPrice": 2500, "lastTradedPrice": 3002}])
        if path.endswith("/fundlimit"):
            return httpx.Response(200, json={"dhanClientId": "1000001", "availabelBalance": 120000, "sodLimit": 150000.5, "utilizedAmount": 30000, "withdrawableBalance": 100000})
        return httpx.Response(404, json={"errorType": "NotFound", "errorCode": "DH-999", "errorMessage": "no route"})


def _broker(server):
    return DhanBroker(BrokerCredentials(access_token="tok-1", client_id="1000001"), client=httpx.AsyncClient(transport=httpx.MockTransport(server), base_url=DhanBroker.BASE_URL))


def test_profile_symbols_quotes_and_candles():
    with pytest.raises(BrokerAuthenticationError):
        DhanBroker(BrokerCredentials(access_token="t"))
    assert isinstance(get_broker_adapter("dhan", BrokerCredentials(access_token="t", client_id="1")), DhanBroker)
    server = _Server()
    broker = _broker(server)
    assert run(broker.authenticate()).user_id == "1000001"

    nse = run(broker.get_instruments("NSE"))
    rel = next(i for i in nse if i.tradingsymbol == "RELIANCE")
    assert rel.instrument_token == "2885" and rel.segment == "NSE_EQ" and rel.instrument_type == "EQ"
    assert next(i for i in nse if i.tradingsymbol == "NIFTY").instrument_type == "INDEX"
    nfo = run(broker.get_instruments("NFO"))
    ce = next(i for i in nfo if i.tradingsymbol == "NIFTY-OCT2026-26000-CE")
    assert ce.strike == 26000.0 and ce.expiry == "2026-10-28" and ce.lot_size == 75 and ce.name == "NIFTY" and ce.instrument_type == "CE"
    assert run(broker._resolve("RELIANCE")) == ("NSE_EQ", "2885", "EQUITY")
    assert run(broker._resolve("NIFTY")) == ("IDX_I", "13", "INDEX") and run(broker._resolve("BANKNIFTY")) == ("IDX_I", "25", "INDEX")
    assert run(broker._resolve("NIFTY-Oct2026-26000-CE", "NFO")) == ("NSE_FNO", "43001", "OPTIDX")
    with pytest.raises(BrokerAPIError):
        run(broker._resolve("NOSUCH"))

    assert run(broker.get_ltp(["NSE:RELIANCE", "NSE:NIFTY"])) == {"NSE:RELIANCE": 2999.5, "NSE:NIFTY": 26050.0}
    assert server.last["quote"] == {"NSE_EQ": [2885], "IDX_I": [13]}
    quote = run(broker.get_quote_for_symbol("RELIANCE"))
    assert quote.bid == 2999.0 and quote.ask == 3000.0 and quote.close == 2980 and quote.timestamp.hour == 10
    bars = run(broker.get_historical_data("RELIANCE", "NSE", "5min", datetime(2026, 9, 29, 3, 45, tzinfo=timezone.utc), datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)))
    assert len(bars) == 2 and bars[1].close == 3002 and bars[0].volume == 12000 and bars[0].timestamp.utcoffset().total_seconds() == 19800
    daily = run(broker.get_historical_data("NIFTY", "NSE", "day", datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 29, tzinfo=timezone.utc)))
    assert len(daily) == 1 and daily[0].close == 26050


def test_orders_books_funds_chain_and_errors():
    server = _Server()
    broker = _broker(server)
    placed = run(broker.place_stop_loss_order("RELIANCE", "NSE", OrderSide.SELL, 10, 2950.0, tag="ALGO123-xyz-toolongtag-cut"))
    o = server.last["place"]
    assert placed.order_id == "112409290001" and placed.status == "OPEN"
    assert o["orderType"] == "STOP_LOSS_MARKET" and o["transactionType"] == "SELL" and o["exchangeSegment"] == "NSE_EQ" and o["securityId"] == "2885"
    assert o["productType"] == "INTRADAY" and o["triggerPrice"] == 2950.0 and o["quantity"] == 10 and len(o["correlationId"]) == 20 and o["dhanClientId"] == "1000001"
    run(broker.place_order(BrokerOrderRequest(symbol="RELIANCE", transaction_type=OrderSide.BUY, quantity=5, order_type="LIMIT", product="CNC", price=2990)))
    assert server.last["place"]["orderType"] == "LIMIT" and server.last["place"]["productType"] == "CNC" and server.last["place"]["price"] == 2990.0
    assert run(broker.modify_order("112409290001", trigger_price=2940.0)).status == "MODIFIED"
    assert server.last["modify"]["triggerPrice"] == 2940.0 and server.last["modify"]["orderType"] == "STOP_LOSS_MARKET" and server.last["modify"]["quantity"] == 10
    assert run(broker.cancel_order("112409290001")).status == "CANCELLED"

    book = run(broker.get_order_book())
    assert book[0].symbol == "RELIANCE" and book[0].order_type == "SL-M" and book[0].status == "OPEN" and book[0].transaction_type == OrderSide.SELL and book[0].placed_at.day == 29
    trades = run(broker.get_trade_book())
    assert trades[0].trade_id == "T1" and trades[0].quantity == 10 and trades[0].price == 2999.5
    positions = run(broker.get_positions())
    assert positions[0].symbol == "RELIANCE" and positions[0].exchange == "NSE" and positions[0].product == "MIS" and positions[0].pnl == 25
    holdings = run(broker.get_holdings())
    assert holdings[0].quantity == 5 and holdings[0].average_price == 2500 and holdings[0].ltp == 3002
    margins = run(broker.get_margins())
    assert margins.available_cash == 120000 and margins.used_margin == 30000 and margins.total_margin == 150000.5

    chain = run(broker.get_option_chain("NIFTY"))
    assert chain.expiry == "2026-10-28" and chain.underlying_ltp == 26050.0 and [r.strike for r in chain.rows] == [26000.0]
    row = chain.rows[0]
    assert row.call_ltp == 210.5 and row.call_change_oi == 120 and row.put_change_oi == -40 and row.call_iv == 12.5 and row.call_delta == 0.52 and row.put_bid == 179.5
    later = run(broker.get_option_chain("NIFTY", expiry=date(2026, 11, 4)))
    assert later.expiry == "2026-11-04" and later.rows[0].strike == 26100.0
    with pytest.raises(BrokerAPIError):
        run(broker.get_option_chain("NIFTY", expiry=date(2026, 12, 25)))

    broker._symbol_map[("NSE", "REJECT")] = ("NSE_EQ", "999", "EQUITY", {})
    with pytest.raises(BrokerAPIError) as exc:
        run(broker.place_order(BrokerOrderRequest(symbol="REJECT", transaction_type=OrderSide.BUY, quantity=1)))
    assert "Insufficient funds" in str(exc.value) and "DH-906" in str(exc.value)

    def expired(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errorType": "Invalid_Authentication", "errorCode": "DH-901", "errorMessage": "Client ID or user generated access token is invalid or expired."})
    stale = DhanBroker(BrokerCredentials(access_token="old", client_id="1000001"), client=httpx.AsyncClient(transport=httpx.MockTransport(expired), base_url=DhanBroker.BASE_URL))
    with pytest.raises(BrokerAuthenticationError):
        run(stale.get_profile())
