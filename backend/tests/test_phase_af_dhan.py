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


class _Before28Oct(date):
    """The fixture's nearest expiry is 28 Oct 2026: the chain's default expiry is read as of a day before it (the test
    used the real clock and broke once that date passed)."""
    @classmethod
    def today(cls):
        return cls(2026, 10, 27)


def test_orders_books_funds_chain_and_errors(monkeypatch):
    import app.brokers.dhan as dhan_module
    monkeypatch.setattr(dhan_module, "date", _Before28Oct)
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


# --- LD-7: shared connection and scrip master for adapters built without a client -----------------------------------------

def _mock(client: httpx.AsyncClient, handler) -> None:
    """Points a shared client at a mock transport (and past any proxy the environment configures)."""
    client._transport = httpx.MockTransport(handler)
    client._mounts = {}


def _shared_state_reset():
    from app.brokers import dhan
    dhan._SHARED_SYMBOLS.clear()
    dhan._SHARED_MISSES.clear()
    dhan._SHARED_FAILED.clear()


def test_adapters_without_a_client_share_one_connection_and_one_scrip_master():
    from app.brokers import dhan
    _shared_state_reset()
    server = _Server()
    creds = BrokerCredentials(access_token="tok-1", client_id="1000001")

    async def go():
        first = DhanBroker(creds)
        _mock(first._client, server)                                  # the shared clients of this loop, mocked
        _mock(first._order_client, server)
        second = DhanBroker(creds)
        assert second._client is first._client                        # one connection pool per host and loop
        assert await first._resolve("RELIANCE") == ("NSE_EQ", "2885", "EQUITY")
        assert await second._resolve("RELIANCE") == ("NSE_EQ", "2885", "EQUITY")
        await second.get_historical_data("RELIANCE", "NSE", "5min", datetime(2026, 9, 29, tzinfo=timezone.utc), datetime(2026, 9, 29, tzinfo=timezone.utc))
        assert await second._resolve("NIFTY-Oct2026-26000-CE", "NFO") == ("NSE_FNO", "43001", "OPTIDX")
        assert await second._resolve("NIFTY") == ("IDX_I", "13", "INDEX")
        with pytest.raises(BrokerAPIError):
            await second._resolve("NOSUCH")
        table = dhan._SHARED_SYMBOLS["map"][1]
        assert all(entry[3] == {} for entry in table.values())         # the three fields only, not the master's rows
        injected = DhanBroker(creds, client=httpx.AsyncClient(transport=httpx.MockTransport(server), base_url=DhanBroker.BASE_URL))
        assert await injected._resolve("RELIANCE") == ("NSE_EQ", "2885", "EQUITY")   # a tool's own client: its own master
        return [str(r.url) for r in server.requests]
    urls = run(go())
    assert urls.count(SCRIP_MASTER_URL) == 2                          # once for the shared map, once for the tool's own
    assert any(u.endswith("/charts/intraday") for u in urls)
    _shared_state_reset()


def test_the_shared_client_keeps_no_cookie_between_requests():
    """BT1b: a cookie a broker sets on one user's response must never ride along on another user's request."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("cookie"))
        return httpx.Response(200, json={}, headers={"set-cookie": "session=user-a; Path=/; Domain=api.dhan.co"})

    async def go():
        from app.brokers.shared_http import shared_client
        client = shared_client("https://api.dhan.co/v2", 15.0)
        _mock(client, handler)
        await client.get("/profile", headers={"access-token": "user-a"})
        await client.get("/profile", headers={"access-token": "user-b"})
        assert shared_client("https://api.dhan.co/v2", 15.0) is client and len(client.cookies) == 0
    run(go())
    assert seen == [None, None]


# --- LD-7 review: the shared scrip map and exits (ADR-0004) -------------------------------------------------------------

NEW_STRIKE = ("NSE,D,43999,OPTIDX,0,NIFTY-Oct2026-26500-CE,75,NIFTY 28 OCT 26500 CALL,2026-10-28 14:30:00,26500.000000,CE,"
              "0.050000,M,OP,,NIFTY")


class _ListingServer(_Server):
    """`_Server` whose scrip master can gain a row (a strike listed during the day); counts the master downloads."""

    def __init__(self):
        super().__init__()
        self.master = MASTER
        self.downloads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == SCRIP_MASTER_URL:
            self.downloads += 1
            self.requests.append(request)
            return httpx.Response(200, text=self.master)
        return super().__call__(request)


def _age_shared_map(seconds: float) -> None:
    import time
    from app.brokers import dhan
    built, table = dhan._SHARED_SYMBOLS["map"]
    dhan._SHARED_SYMBOLS["map"] = (time.monotonic() - seconds, table)


def test_a_symbol_listed_after_the_shared_map_was_built_resolves():
    """B1: the worker's map was built before a strike was listed; an exit on that strike must still resolve it (one
    rebuild of the map, not a wait until it expires)."""
    _shared_state_reset()
    server = _ListingServer()
    creds = BrokerCredentials(access_token="tok-1", client_id="1000001")

    async def go():
        first = DhanBroker(creds)
        _mock(first._client, server)
        _mock(first._order_client, server)
        assert await first._resolve("RELIANCE") == ("NSE_EQ", "2885", "EQUITY")      # the map, built at the open
        server.master = MASTER + "\n" + NEW_STRIKE                                    # listed during the day
        _age_shared_map(120)                                                           # two minutes later
        later = DhanBroker(creds)
        assert await later._resolve("NIFTY-Oct2026-26500-CE", "NFO") == ("NSE_FNO", "43999", "OPTIDX")
        assert server.downloads == 2
        assert await DhanBroker(creds)._resolve("NIFTY-Oct2026-26500-CE", "NFO") == ("NSE_FNO", "43999", "OPTIDX")
        assert server.downloads == 2                                                   # now in the shared map
    run(go())
    _shared_state_reset()


def test_an_unknown_symbol_does_not_download_the_master_every_time():
    _shared_state_reset()
    server = _ListingServer()
    creds = BrokerCredentials(access_token="tok-1", client_id="1000001")

    async def go():
        adapter = DhanBroker(creds)
        _mock(adapter._client, server)
        _mock(adapter._order_client, server)
        await adapter._resolve("RELIANCE")
        assert server.downloads == 1
        with pytest.raises(BrokerAPIError):
            await DhanBroker(creds)._resolve("NOSUCH")             # the map is fresh: no rebuild for a miss
        assert server.downloads == 1
        _age_shared_map(120)
        with pytest.raises(BrokerAPIError):
            await DhanBroker(creds)._resolve("NOSUCH")             # an old map: one rebuild, still not there
        assert server.downloads == 2
        for _ in range(5):                                          # every worker cycle asks again
            _age_shared_map(120)
            with pytest.raises(BrokerAPIError):
                await DhanBroker(creds)._resolve("NOSUCH")
        assert server.downloads == 2                                # remembered as missing: no download each time
    run(go())
    _shared_state_reset()


def test_one_unknown_symbol_does_not_fail_the_other_quotes():
    server = _Server()
    broker = _broker(server)
    assert run(broker.get_ltp(["NSE:RELIANCE", "NSE:NOSUCH", "NSE:NIFTY"])) == {"NSE:RELIANCE": 2999.5, "NSE:NIFTY": 26050.0}
    with pytest.raises(BrokerAPIError, match="not found in the Dhan scrip master"):
        run(broker.get_quote_for_symbol("NOSUCH"))                  # alone: the error, as before


def test_an_order_does_not_wait_behind_a_saturated_data_pool():
    """N2: data fetches that hold every connection of the data client (here: never answered) do not delay an order -
    orders and account calls go through a pool of their own."""
    _shared_state_reset()
    server = _Server()
    creds = BrokerCredentials(access_token="tok-1", client_id="1000001")

    async def go():
        stuck = asyncio.Event()

        async def data_handler(request: httpx.Request) -> httpx.Response:
            await stuck.wait()                                      # a data pool with nothing left
            return httpx.Response(503)

        async def order_handler(request: httpx.Request) -> httpx.Response:
            return server(request)

        broker = DhanBroker(creds)
        broker._client._transport = httpx.MockTransport(data_handler)
        broker._client._mounts = {}
        assert broker._order_client is not broker._client
        broker._order_client._transport = httpx.MockTransport(order_handler)
        broker._order_client._mounts = {}
        fetches = [asyncio.ensure_future(broker._client.get("/charts/intraday")) for _ in range(150)]
        await asyncio.sleep(0)
        try:
            # RELIANCE: the symbol map is not built yet, so the order also downloads the scrip master - on the order pool
            response = await asyncio.wait_for(broker.place_order(BrokerOrderRequest(symbol="RELIANCE", transaction_type=OrderSide.SELL, quantity=10)),
                                              timeout=2)
            assert response.order_id == "112409290001"
            await asyncio.wait_for(broker.cancel_order("112409290001"), timeout=2)
            assert all(not f.done() for f in fetches)               # the data calls are still waiting
        finally:
            stuck.set()
            await asyncio.gather(*fetches, return_exceptions=True)
        assert [r.url.path for r in server.requests if r.method in ("POST", "DELETE")] == ["/v2/orders", "/v2/orders/112409290001"]
        assert any(str(r.url) == SCRIP_MASTER_URL for r in server.requests)
    run(go())
    _shared_state_reset()


class _FailingMasterServer(_ListingServer):
    """The master download fails (a 503) once `failing` is set; counts the attempts."""

    def __init__(self):
        super().__init__()
        self.failing = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == SCRIP_MASTER_URL and self.failing:
            self.downloads += 1
            return httpx.Response(503, text="unavailable")
        return super().__call__(request)


def _shared_adapter(creds, server):
    adapter = DhanBroker(creds)
    _mock(adapter._client, server)
    _mock(adapter._order_client, server)
    return adapter


def test_a_failing_master_download_is_tried_at_most_once_a_minute():
    """Follow-up 1: with the master failing, misses do not each download again behind the build lock - one attempt,
    the misses fail at once, and a symbol the map has still resolves."""
    import time as _time
    _shared_state_reset()
    server = _FailingMasterServer()
    creds = BrokerCredentials(access_token="tok-1", client_id="1000001")

    async def go():
        await _shared_adapter(creds, server)._resolve("RELIANCE")                    # the map, built
        assert server.downloads == 1
        server.failing = True
        _age_shared_map(120)
        started = _time.monotonic()
        for i in range(5):
            with pytest.raises(BrokerAPIError):
                await DhanBroker(creds)._resolve(f"NEWSTRIKE{i}", "NFO")
        assert server.downloads == 2                                                  # one attempt, not five
        assert _time.monotonic() - started < 1.0
        assert await DhanBroker(creds)._resolve("RELIANCE") == ("NSE_EQ", "2885", "EQUITY")   # the map it has
        assert server.downloads == 2
    run(go())
    _shared_state_reset()

    async def no_map_at_all():
        server.failing, server.downloads = True, 0
        for _ in range(5):
            with pytest.raises(BrokerAPIError, match="could not be downloaded"):
                await _shared_adapter(creds, server)._resolve("RELIANCE")
        assert server.downloads == 1
    run(no_map_at_all())
    _shared_state_reset()


def test_an_order_is_not_refused_by_the_remembered_misses():
    """Follow-up 2: a symbol remembered as missing (a data lookup found it in no map) is still looked for when an order
    - an exit - needs it: one rebuild, which now has it."""
    _shared_state_reset()
    server = _ListingServer()
    creds = BrokerCredentials(access_token="tok-1", client_id="1000001")

    async def go():
        await _shared_adapter(creds, server)._resolve("RELIANCE")
        _age_shared_map(120)
        with pytest.raises(BrokerAPIError):
            await DhanBroker(creds)._resolve("NIFTY-Oct2026-26500-CE", "NFO")      # not listed yet: remembered
        assert server.downloads == 2
        server.master = MASTER + "\n" + NEW_STRIKE                                   # listed now
        _age_shared_map(120)
        with pytest.raises(BrokerAPIError):
            await DhanBroker(creds)._resolve("NIFTY-Oct2026-26500-CE", "NFO")      # a data lookup: skipped for now
        assert server.downloads == 2
        response = await DhanBroker(creds).place_order(BrokerOrderRequest(symbol="NIFTY-Oct2026-26500-CE", exchange="NFO",
                                                                          transaction_type=OrderSide.SELL, quantity=75))
        assert response.order_id == "112409290001" and server.downloads == 3
        assert server.last["place"]["securityId"] == "43999"
    run(go())
    _shared_state_reset()


def test_expired_misses_are_pruned_when_a_miss_is_written():
    import time as _time
    from app.brokers import dhan
    _shared_state_reset()
    server = _ListingServer()
    creds = BrokerCredentials(access_token="tok-1", client_id="1000001")
    dhan._SHARED_MISSES[("NSE", "OLD1")] = _time.monotonic() - dhan.MISS_REMEMBER_SECONDS - 1
    dhan._SHARED_MISSES[("NSE", "OLD2")] = _time.monotonic() - dhan.MISS_REMEMBER_SECONDS - 60

    async def go():
        await _shared_adapter(creds, server)._resolve("RELIANCE")
        _age_shared_map(120)
        with pytest.raises(BrokerAPIError):
            await DhanBroker(creds)._resolve("NOSUCH")
    run(go())
    assert set(dhan._SHARED_MISSES) == {("NSE", "NOSUCH")}
    _shared_state_reset()
