"""Part 1B: the Groww adapter against recorded fixtures (tests/fixtures/groww/recorded.json, the shapes of Groww's
published API reference with made-up values) - the TOTP and approval logins and the stored token, the gates (no LIVE
order and no market data leaves ATP while they are closed), the order wire format with the gates opened in a test,
TM-3's marketable limit, TM-23 (no native GTT / OCO call), the books and account reads, symbol mapping to ATP's
instrument master, the per-day budget (TM-27), the charges table, the PAPER parity and the feed (NATS + protobuf).
No network, no real account, no secret: fake tokens are built at run time."""
import asyncio
import base64
import hashlib
import json
import struct
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pyotp
import pytest

from app.brokers import broker_charges, gates
from app.brokers import groww as gw
from app.brokers import rate_budget as rb
from app.brokers.catalog import entry as catalog_entry
from app.brokers.contract_symbols import wrap_contract_symbols
from app.brokers.exceptions import BrokerAuthenticationError, BrokerOrderRejected
from app.brokers.models import ORDER_INTENT_ENTRY, BrokerCredentials, BrokerOrderRequest
from app.brokers.registry import get_broker_adapter
from app.brokers.token_lifecycle import TOKEN_DAILY_EXPIRY_IST, default_token_expiry
from app.compliance.order_policy import policy_for
from app.core.enums import OrderSide
from app.execution import marketable_limit as ml
from app.execution.paper_adapter import PaperExecutionAdapter
from app.execution.router import DEFAULT_MARKET_PROTECTION_PCT
from app.market_data import groww_feed as gf
from app.market_data.data_terms import terms_for
from app.market_data.stream import stream_for
from tests.broker_fakes import GROWW_API_KEY, GROWW_API_SECRET, TOTP_SECRET

run = asyncio.run
FIX = json.loads((Path(__file__).parent / "fixtures" / "groww" / "recorded.json").read_text())
ACCESS = ".".join(["fake" + "access", "fake" + "body", "fake" + "sig"])
SOCKET_JWT = ".".join(["fake" + "socket", "fake" + "body", "fake" + "sig"])


def _creds(**over) -> BrokerCredentials:
    body = {"api_key": GROWW_API_KEY, "totp_secret": TOTP_SECRET}
    body.update(over)
    return BrokerCredentials(**body)


class _Server:
    """A fake Groww: answers each SDK path with its recorded fixture and records every request."""

    def __init__(self):
        self.requests = []

    def paths(self):
        return [r.url.path for r in self.requests]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url, path = str(request.url), request.url.path
        assert not path.startswith("/v1" + gw.SMART_ORDER_PREFIX), "TM-23: ATP never calls Groww's smart orders"
        if url == gw.INSTRUMENT_CSV_URL:
            return httpx.Response(200, text=FIX["instruments_csv"])
        assert request.url.host == "api.groww.in", url
        assert request.headers["x-api-version"] == "1.0"
        bearer = request.headers["Authorization"].removeprefix("Bearer ")
        if path == "/v1/token/api/access":
            assert bearer == GROWW_API_KEY
            return httpx.Response(200, json=dict(FIX["token"], token=ACCESS))
        assert bearer == ACCESS, path
        if url == gw.SOCKET_TOKEN_URL:
            return httpx.Response(200, json={"status": "SUCCESS", "payload": {"token": SOCKET_JWT, "subscriptionId": "s1"}})
        p = path.removeprefix("/v1")
        if p == gw.ORDER_LIST_PATH:
            segment, page = request.url.params["segment"], int(request.url.params["page"])
            key = {"CASH": "order_list_cash", "FNO": "order_list_fno"}.get(segment, "order_list_empty")
            return httpx.Response(200, json=FIX[key] if page == 0 else FIX["order_list_empty"])
        if p.startswith("/order/trades/"):
            return httpx.Response(200, json=FIX["trades"])
        if p.startswith("/option-chain/"):
            return httpx.Response(200, json=FIX["option_chain"])
        if p.startswith("/live-data/greeks/"):
            return httpx.Response(200, json=FIX["greeks"])
        table = {gw.PROFILE_PATH: "profile", gw.POSITIONS_PATH: "positions", gw.HOLDINGS_PATH: "holdings", gw.MARGINS_PATH: "margins",
                 gw.ORDER_MARGIN_PATH: "order_margin", gw.PLACE_ORDER_PATH: "place_order", gw.MODIFY_ORDER_PATH: "modify_order",
                 gw.CANCEL_ORDER_PATH: "cancel_order", gw.LTP_PATH: "ltp", gw.QUOTE_PATH: "quote", gw.HISTORY_PATH: "history"}
        if p in table:
            return httpx.Response(200, json=FIX[table[p]])
        return httpx.Response(404, json={"status": "FAILURE", "error": {"message": "unknown path"}})


def _adapter(server=None, **creds):
    server = server or _Server()
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return gw.GrowwBroker(_creds(**creds), client), server


def _sent(server, path):
    return json.loads([r for r in server.requests if r.url.path == "/v1" + path][-1].content)


@pytest.fixture
def gates_open(monkeypatch):
    """Both Groww gates opened for one test only (the wire format behind them); the real GATES stay closed."""
    monkeypatch.setitem(gates.GATES, "groww", gates.BrokerGates())
    monkeypatch.setattr(gates, "market_data_open", lambda broker: True)


# --- session -----------------------------------------------------------------------------------------------------

def test_the_approval_checksum_is_the_sdks_sha256_of_secret_and_timestamp():
    assert gw.approval_checksum("abc", 1700000000) == hashlib.sha256(b"abc1700000000").hexdigest()
    adapter = gw.GrowwBroker(BrokerCredentials(api_key=GROWW_API_KEY, api_secret=GROWW_API_SECRET))
    adapter.clock = lambda: 1792737000.7
    assert adapter.token_request() == {"key_type": "approval", "timestamp": 1792737000,
                                       "checksum": hashlib.sha256((GROWW_API_SECRET + "1792737000").encode()).hexdigest()}
    totp = gw.GrowwBroker(_creds())
    assert totp.token_request() == {"key_type": "totp", "totp": pyotp.TOTP(TOTP_SECRET).now()}


def test_totp_login_gets_the_days_token_and_reads_the_profile():
    adapter, server = _adapter()
    profile = run(adapter.authenticate())
    assert profile.broker == "groww" and profile.user_id == "FAKEUCC9" and adapter.access_token == ACCESS
    assert server.paths() == ["/v1/token/api/access", "/v1/user/detail"]
    assert json.loads(server.requests[0].content) == {"key_type": "totp", "totp": pyotp.TOTP(TOTP_SECRET).now()}
    assert run(adapter.active_segments()) == ["CASH", "FNO"]


def test_approval_login_sends_the_checksum():
    adapter, server = _adapter(totp_secret=None, api_secret=GROWW_API_SECRET)
    run(adapter.authenticate())
    sent = json.loads(server.requests[0].content)
    assert sent["key_type"] == "approval" and sent["checksum"] == gw.approval_checksum(GROWW_API_SECRET, sent["timestamp"])


def test_a_refused_login_and_a_refused_session_are_authentication_errors():
    def refuse(request):
        return httpx.Response(400, json=FIX["failure"])
    adapter = gw.GrowwBroker(_creds(), httpx.AsyncClient(transport=httpx.MockTransport(refuse)))
    with pytest.raises(Exception) as exc:
        run(adapter.authenticate())
    assert "Invalid TOTP" in str(exc.value)

    def inactive(request):
        return httpx.Response(200, json={"token": ACCESS, "isActive": False})
    with pytest.raises(BrokerAuthenticationError):
        run(gw.GrowwBroker(_creds(), httpx.AsyncClient(transport=httpx.MockTransport(inactive))).authenticate())

    def expired(request):
        return httpx.Response(401, json={"status": "FAILURE", "error": {"message": "Token expired"}})
    stored = gw.GrowwBroker(BrokerCredentials(api_key=GROWW_API_KEY, access_token=ACCESS),
                            httpx.AsyncClient(transport=httpx.MockTransport(expired)))
    with pytest.raises(BrokerAuthenticationError):
        run(stored.get_profile())


def test_a_stored_token_needs_no_login_fields_and_the_registry_builds_the_adapter():
    adapter = get_broker_adapter("groww", BrokerCredentials(api_key=GROWW_API_KEY, access_token=ACCESS))
    assert isinstance(adapter, gw.GrowwBroker) and adapter.access_token == ACCESS
    with pytest.raises(BrokerAuthenticationError):
        gw.GrowwBroker(BrokerCredentials(api_key=GROWW_API_KEY))
    with pytest.raises(BrokerAuthenticationError):
        gw.GrowwBroker(BrokerCredentials(totp_secret=TOTP_SECRET))


def test_token_lifecycle_ends_the_session_at_six_in_the_morning():
    assert "groww" in TOKEN_DAILY_EXPIRY_IST
    now = datetime(2026, 10, 23, 4, 0, tzinfo=timezone.utc)            # 09:30 IST
    assert default_token_expiry("groww", now) == datetime(2026, 10, 24, 0, 30, tzinfo=timezone.utc)


# --- the gates (closed: the real state) -----------------------------------------------------------------------------

def test_the_gates_are_closed_for_groww():
    assert not gates.live_orders_open("groww") and not gates.market_data_open("groww")
    assert terms_for("groww").status.value == "pending"
    assert "Groww" in gates.GATES["groww"].note and gates.GATES["groww"].opens_when


def test_no_live_order_leaves_atp_while_the_gate_is_closed():
    adapter, server = _adapter(access_token=ACCESS)
    order = BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.BUY, quantity=1, order_type="LIMIT",
                               price=3000.0, product="MIS")
    calls = [adapter.place_order(order), adapter.modify_order("GFAKE0001", price=2990.0), adapter.cancel_order("GFAKE0001"),
             adapter.exit_position("RELIANCE", "NSE", 1, OrderSide.BUY),
             adapter.place_stop_loss_order("RELIANCE", "NSE", OrderSide.SELL, 1, 2950.0)]
    for call in calls:
        with pytest.raises(gates.LiveOrdersClosed) as exc:
            run(call)
        assert "Nothing was sent to Groww" in str(exc.value)
    with pytest.raises(gates.LiveOrdersClosed):
        run(wrap_contract_symbols(adapter).place_order(order))
    assert server.requests == []


def test_no_market_data_is_asked_while_the_gate_is_closed():
    adapter, server = _adapter(access_token=ACCESS)
    for call in (adapter.get_ltp(["NSE:RELIANCE"]), adapter.get_quote(["NSE:RELIANCE"]), adapter.get_quote_for_symbol("RELIANCE"),
                 adapter.get_ltp_for_symbol("RELIANCE"), adapter.get_quotes_batch([("NSE", "RELIANCE")]),
                 adapter.get_historical_data("RELIANCE", "NSE", "5min", datetime(2026, 10, 22), datetime(2026, 10, 22)),
                 adapter.get_intraday_candles("RELIANCE", "NSE", "5min"), adapter.get_option_chain("NIFTY", date(2026, 10, 27)),
                 adapter.option_greeks("NIFTY", "NIFTY26O2726000CE", date(2026, 10, 27))):
        with pytest.raises(gates.MarketDataClosed):
            run(call)
    assert server.requests == []
    assert stream_for(adapter) is None                                   # no socket token, no socket


def test_account_reads_work_with_the_gates_closed_and_carry_no_broker_price():
    adapter, server = _adapter(access_token=ACCESS)
    positions = {p.symbol: p for p in run(adapter.get_positions())}
    fut = positions["NIFTY26NOVFUT"]
    assert (fut.quantity, fut.average_price, fut.ltp, fut.pnl, fut.exchange, fut.product) == (65, 25000.0, 0.0, 0.0, "NFO", "NRML")
    flat = positions["TCS"]
    assert flat.quantity == 0 and flat.pnl == 250.0 and flat.ltp == 0.0
    holding = run(adapter.get_holdings())[0]
    assert (holding.symbol, holding.quantity, holding.average_price, holding.ltp, holding.pnl) == ("RELIANCE", 4, 2800.5, 0.0, 0.0)
    margins = run(adapter.get_margins())
    assert margins.available_cash == 150000.25 and margins.used_margin == 25000.0 and margins.available_margin == 125000.25
    book = run(adapter.get_order_book())
    assert [(o.order_id, o.symbol, o.status, o.exchange, o.tag) for o in book] == [
        ("GFAKE0001", "RELIANCE", "OPEN", "NSE", "ATPFAKE1"), ("GFAKE0002", "NIFTY26O2726000CE", "COMPLETE", "NFO", None)]
    assert book[1].transaction_type == OrderSide.SELL and book[1].average_price == 121.1 and book[1].placed_at.tzinfo is not None
    trades = run(adapter.get_trade_book())
    assert [(t.trade_id, t.order_id, t.quantity, t.price) for t in trades] == [("fake-fill-1", "GFAKE0002", 65, 121.1)]
    margin = run(adapter.get_order_margin(BrokerOrderRequest(symbol="RELIANCE", transaction_type=OrderSide.BUY, quantity=1,
                                                             order_type="LIMIT", price=3000.0)))
    assert margin == 15500.0
    assert "/v1" + gw.PLACE_ORDER_PATH not in server.paths()


def test_with_prices_open_positions_read_the_broker_price(gates_open):
    adapter, _server = _adapter(access_token=ACCESS)
    fut = next(p for p in run(adapter.get_positions()) if p.symbol == "NIFTY26NOVFUT")
    assert fut.ltp == 25100.0 and fut.pnl == round(65 * (25100.0 - 25000.0), 2)


# --- the wire format behind the gates (opened only in these tests) ----------------------------------------------------

def test_a_limit_order_goes_as_the_sdk_sends_it(gates_open):
    adapter, server = _adapter(access_token=ACCESS)
    order = BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.BUY, quantity=10, order_type="LIMIT",
                               price=2999.5, product="MIS", tag="ATP-ALGO-1-TOO-LONG-FOR-THE-FIELD")
    response = run(adapter.place_order(order))
    assert response.order_id == "GFAKE0201" and response.status == "OPEN"
    assert _sent(server, gw.PLACE_ORDER_PATH) == {
        "trading_symbol": "RELIANCE", "quantity": 10, "price": 2999.5, "trigger_price": None, "validity": "DAY", "exchange": "NSE",
        "segment": "CASH", "product": "MIS", "order_type": "LIMIT", "transaction_type": "BUY", "order_reference_id": "ATP-ALGO-1-TOO-LONG-"}


def test_tm3_a_market_order_goes_as_a_marketable_limit_from_the_users_band(gates_open):
    adapter, server = _adapter(access_token=ACCESS)
    order = BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.SELL, quantity=4, order_type="MARKET",
                               product="CNC", protection_pct=1.0)
    response = run(adapter.place_order(order))
    body = _sent(server, gw.PLACE_ORDER_PATH)
    # Groww's own last price 3001.00 (LTP fixture), 1% below for a sell, rounded down to the tick: 2970.95.
    assert body["order_type"] == "LIMIT" and body["price"] == 2970.95 and body["transaction_type"] == "SELL"
    assert "limit order at 2970.95" in (response.message or "")
    run(adapter.place_order(order.model_copy(update={"protection_pct": None, "transaction_type": OrderSide.BUY})))
    assert _sent(server, gw.PLACE_ORDER_PATH)["price"] == ml.marketable_limit_price(3001.0, OrderSide.BUY, DEFAULT_MARKET_PROTECTION_PCT) == 3016.05


def test_a_stop_goes_as_sl_with_a_band_and_atp_refuses_what_it_does_not_send(gates_open):
    adapter, server = _adapter(access_token=ACCESS)
    run(adapter.place_stop_loss_order("RELIANCE", "NSE", OrderSide.SELL, 4, 2950.0, product="MIS"))
    body = _sent(server, gw.PLACE_ORDER_PATH)
    assert body["order_type"] == "SL" and body["trigger_price"] == 2950.0 and body["price"] < 2950.0
    for bad in ({"product": "BO"}, {"product": "CO"}, {"validity": "GTC"}, {"validity": "GTD"}):
        with pytest.raises(BrokerOrderRejected):
            run(adapter.place_order(BrokerOrderRequest(symbol="RELIANCE", transaction_type=OrderSide.BUY, quantity=1, order_type="LIMIT",
                                                       price=3000.0, **bad)))
    with pytest.raises(BrokerOrderRejected):
        run(adapter.modify_order("GFAKE0001", order_type="MARKET"))


def test_modify_and_cancel_carry_the_orders_segment(gates_open):
    adapter, server = _adapter(access_token=ACCESS)
    run(adapter.modify_order("GFAKE0001", price=2990.0))
    assert _sent(server, gw.MODIFY_ORDER_PATH) == {"quantity": 10, "price": 2990.0, "trigger_price": None, "groww_order_id": "GFAKE0001",
                                                   "order_type": "LIMIT", "segment": "CASH"}
    run(adapter.cancel_order("GFAKE0002"))
    assert _sent(server, gw.CANCEL_ORDER_PATH) == {"segment": "FNO", "groww_order_id": "GFAKE0002"}


def test_tm23_the_adapter_has_no_native_gtt_or_oco_call():
    names = {n.lower() for n in dir(gw.GrowwBroker)}
    assert not any(word in n for n in names for word in ("smart", "gtt", "oco"))


def test_market_data_behind_the_open_gate(gates_open):
    adapter, server = _adapter(access_token=ACCESS)
    quote = run(adapter.get_quote(["NSE:RELIANCE"]))["NSE:RELIANCE"]
    assert quote.ltp == 3001.0 and quote.prev_close == 2995.0 and quote.bid == 3000.9 and quote.ask == 3001.1 and quote.timestamp.year == 2026
    params = [r for r in server.requests if r.url.path == "/v1" + gw.QUOTE_PATH][-1].url.params
    assert dict(params) == {"exchange": "NSE", "segment": "CASH", "trading_symbol": "RELIANCE"}
    ltp = run(adapter.get_ltp(["NSE:RELIANCE", "NFO:NIFTY26O2726000CE"]))
    assert ltp == {"NSE:RELIANCE": 3001.0, "NFO:NIFTY26O2726000CE": 120.5}
    segments = sorted(r.url.params["segment"] for r in server.requests if r.url.path == "/v1" + gw.LTP_PATH)
    assert segments == ["CASH", "FNO"]
    bars = run(adapter.get_historical_data("RELIANCE", "NSE", "5min", datetime(2026, 10, 22, 3, 45, tzinfo=timezone.utc),
                                           datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)))
    assert [b.close for b in bars] == [2999.5, 3002.0] and bars[0].timestamp.utcoffset().total_seconds() == 19800
    params = [r for r in server.requests if r.url.path == "/v1" + gw.HISTORY_PATH][-1].url.params
    assert dict(params) == {"exchange": "NSE", "segment": "CASH", "groww_symbol": "NSE-RELIANCE", "start_time": "2026-10-22 09:15:00",
                            "end_time": "2026-10-22 15:30:00", "candle_interval": "5minute"}
    chain = run(adapter.get_option_chain("NIFTY", date(2026, 10, 27)))
    assert chain.underlying_ltp == 26010.5 and [r.strike for r in chain.rows] == [25900.0, 26000.0]
    atm = chain.rows[1]
    assert (atm.call_ltp, atm.call_oi, atm.call_delta, atm.call_iv, atm.put_delta, atm.put_iv) == (120.5, 900000, 0.52, 13.4, -0.48, 13.9)
    assert chain.rows[0].put_ltp is None
    chain_req = [r for r in server.requests if r.url.path.startswith("/v1/option-chain/")][-1]
    assert chain_req.url.path == "/v1/option-chain/exchange/NSE/underlying/NIFTY" and chain_req.url.params["expiry_date"] == "2026-10-27"
    greeks = run(adapter.option_greeks("NIFTY", "NIFTY26O2726000CE", date(2026, 10, 27)))
    assert greeks == {"delta": 0.52, "gamma": 0.0011, "theta": -12.1, "vega": 9.8, "rho": 1.2, "iv": 13.4}
    with pytest.raises(Exception):
        run(adapter.get_option_chain("NIFTY"))                          # Groww needs the expiry: never guessed
    assert stream_for(adapter) is not None


# --- symbols ---------------------------------------------------------------------------------------------------

def test_instrument_file_parsing_and_symbol_mapping_to_the_instrument_master():
    adapter, server = _adapter(access_token=ACCESS)
    equities = run(adapter.get_instruments("NSE"))
    reliance = next(i for i in equities if i.tradingsymbol == "RELIANCE")
    assert (reliance.instrument_token, reliance.instrument_type, reliance.tick_size, reliance.name) == ("2885", "EQ", 0.05, "Reliance Industries")
    assert run(adapter.resolve("RELIANCE", "NSE")) == ("NSE", "CASH", "RELIANCE", "2885")
    assert run(adapter.resolve("NSE-RELIANCE", "NSE")) == ("NSE", "CASH", "RELIANCE", "2885")       # the Groww symbol too
    assert run(adapter.resolve("RELIANCE", "BSE")) == ("BSE", "CASH", "RELIANCE", "500325")
    assert run(adapter.resolve("NIFTY 50", "NSE"))[3] == "NIFTY"
    options = run(adapter.get_instruments("NFO"))
    call = next(i for i in options if i.instrument_token == "71472")
    assert (call.name, call.expiry, call.strike, call.instrument_type, call.lot_size) == ("NIFTY", "2026-10-27", 26000.0, "CE", 65)
    fut = next(i for i in options if i.instrument_token == "53001")
    assert fut.instrument_type == "FUT" and fut.expiry == "2026-11-24" and fut.strike is None and fut.tick_size == 0.1
    assert run(adapter.get_instruments("CDS")) == []
    assert [r.url for r in server.requests].count(httpx.URL(gw.INSTRUMENT_CSV_URL)) == 1     # one file for every exchange
    wrapped = wrap_contract_symbols(adapter)
    assert run(wrapped._out("NIFTY 26000 CE 27 OCT 26", "NFO")) == "NIFTY26O2726000CE"
    assert run(wrapped._out("NIFTY FUT 24 NOV 26", "NFO")) == "NIFTY26NOVFUT"
    assert run(wrapped._back("NIFTY26O2726000PE", "NFO")) == "NIFTY 26000 PE 27 OCT 26"
    with pytest.raises(Exception):
        run(adapter.resolve("NOSUCHSTOCK", "NSE"))


def test_rows_without_a_token_or_on_an_unknown_venue_are_left_out():
    assert gw.instrument_from_row({"exchange": "NSE", "trading_symbol": "X", "segment": "CASH"}) is None
    assert gw.instrument_from_row({"exchange": "NCDEX", "exchange_token": "1", "trading_symbol": "X", "segment": "COMMODITY"}) is None
    odd = gw.instrument_from_row({"exchange": "NSE", "exchange_token": "1", "trading_symbol": "x", "segment": "CASH", "tick_size": "abc"})
    assert odd.tradingsymbol == "X" and odd.tick_size == 0.05


# --- TM-27: the per-day budget ----------------------------------------------------------------------------------------

def test_daily_budget_counts_by_type_keeps_exits_and_resets_each_day():
    day = [date(2026, 10, 23)]
    budget = rb.DailyBudget("Groww", {"orders": 100, "live_data": 50, "non_trading": 20, "auth": 10}, today=lambda: day[0])
    assert budget.caps == {"orders": 90, "live_data": 45, "non_trading": 18, "auth": 9} and budget.entry_cap == 80
    for _ in range(80):
        budget.take("place_order", entry=True)
    with pytest.raises(rb.DailyCapReached) as exc:
        budget.take("place_order", entry=True)                          # a new entry stops at the exit reserve
    assert exc.value.status_code == 429 and "starts again tomorrow" in str(exc.value)
    for _ in range(30):                                                 # exits, stops, changes, cancels: never refused
        budget.take("exit_position")
        budget.take("cancel_order")
    assert budget.used["orders"] == 140
    for _ in range(40):
        budget.take("get_ltp")
    with pytest.raises(rb.DailyCapReached):
        budget.take("get_option_chain")                                 # live data shares one allowance; M55 S2: a read
    with rb.exit_reads():                                               # not for an exit stops before the exit reserve
        for _ in range(5):
            budget.take("get_ltp")
        with pytest.raises(rb.DailyCapReached):
            budget.take("get_ltp")
    for _ in range(18):
        budget.take("get_positions")
    with pytest.raises(rb.DailyCapReached):
        budget.take("get_holdings")
    budget.take("get_order_book")                                       # an exit's follow-up reads always pass
    budget.take("get_positions")
    budget.add("get_order_book", 2)                                     # extra requests one adapter call made
    assert budget.remaining("non_trading") == 0 and budget.remaining("nothing") is None
    day[0] = date(2026, 10, 24)
    assert budget.remaining("orders") == 90 and budget.remaining("live_data") == 45
    budget.take("place_order", entry=True)


def test_groww_has_the_reviews_daily_caps_and_a_third_of_the_tightest_rate():
    assert rb.DAILY_CAPS["groww"] == {"orders": 3000, "live_data": 5000, "non_trading": 3000, "auth": 150}
    assert rb.daily_budget_for("upstox") is None and rb.daily_budget_for("kotak_neo") is None
    budget = rb.daily_budget_for("groww")
    assert budget.caps["orders"] == 2700 and budget.entry_cap == 2400 and budget.caps["live_data"] == 4500
    assert rb.limits_for("groww").per_minute <= 250 / 3 and rb.limits_for("groww").per_second <= 10 / 3 + 0.01
    assert rb.call_kind("get_quote") == "live_data" and rb.call_kind("modify_order") == "orders"
    assert rb.call_kind("authenticate") == "auth" and rb.call_kind("get_margins") == "non_trading"


class _Counting:
    name = "groww"
    history_by_date = False
    daily_days_per_request = 90

    def __init__(self):
        self.calls = []

    async def get_ltp(self, symbols):
        self.calls.append("get_ltp")
        return {}

    async def place_order(self, order):
        self.calls.append("place_order")
        from app.brokers.models import BrokerOrderResponse
        return BrokerOrderResponse(order_id="1", status="OPEN")


def test_the_rate_limited_wrapper_refuses_before_sending_and_never_refuses_an_exit():
    inner = _Counting()
    daily = rb.DailyBudget("Groww", {"orders": 2, "live_data": 1}, share=1.0, exit_reserve=0.5, read_reserve=0.0)
    wrapped = rb.RateLimitedBroker(inner, rb.RateBudget(rb.limits_for("groww"), daily=daily))
    run(wrapped.get_ltp(["NSE:RELIANCE"]))
    with pytest.raises(rb.DailyCapReached):
        run(wrapped.get_ltp(["NSE:RELIANCE"]))
    entry = BrokerOrderRequest(symbol="X", transaction_type=OrderSide.BUY, quantity=1, order_type="LIMIT", price=1.0,
                               intent=ORDER_INTENT_ENTRY)
    run(wrapped.place_order(entry))
    with pytest.raises(rb.DailyCapReached):
        run(wrapped.place_order(entry))
    exit_order = entry.model_copy(update={"intent": None, "transaction_type": OrderSide.SELL})
    for _ in range(3):
        assert run(wrapped.place_order(exit_order)).order_id == "1"
    assert inner.calls == ["get_ltp", "place_order", "place_order", "place_order", "place_order"]


def test_the_wrapper_counts_every_request_the_adapter_sent():
    adapter, _server = _adapter(access_token=ACCESS)
    daily = rb.DailyBudget("Groww", {"non_trading": 100}, share=1.0)
    wrapped = rb.RateLimitedBroker(adapter, rb.RateBudget(rb.limits_for("groww"), daily=daily))
    run(wrapped.get_order_book())                                       # one page per segment: three requests
    assert adapter.http_calls == 3 and daily.used["non_trading"] == 3
    run(wrapped.get_margins())
    assert daily.used["non_trading"] == 4


def test_the_worker_gives_a_groww_session_its_daily_budget():
    from app.workers.trading_worker import TradingWorker
    worker = TradingWorker.__new__(TradingWorker)
    worker._budgets = {}
    assert worker._budget_for(1, "groww@primary").daily is not None
    assert worker._budget_for(1, "upstox@primary").daily is None


# --- charges, catalogue, policy, PAPER parity --------------------------------------------------------------------------

def test_groww_brokerage_is_the_stated_rule_with_its_source():
    day = date(2026, 10, 23)
    charge = broker_charges.brokerage_for_order
    assert charge("groww", "standard", day, "EQUITY_INTRADAY", 100000.0) == 20.0           # 0.1% = 100, capped at 20
    assert charge("groww", "standard", day, "EQUITY_DELIVERY", 10000.0) == 10.0            # 0.1% of 10,000
    assert charge("groww", "standard", day, "EQUITY_INTRADAY", 1000.0) == 5.0              # 1.0 < 5: the lower of 5 and 2.5%
    assert charge("groww", "standard", day, "EQUITY_INTRADAY", 100.0) == 2.5               # 2.5% of 100
    assert charge("groww", "standard", day, "OPTION", 500.0) == 20.0 == charge("groww", "standard", day, "FUTURE", 9e6)
    assert charge("groww", "standard", date(2025, 6, 30), "OPTION", 500.0) is None         # nothing before the rows apply
    assert charge("groww", "other", day, "OPTION", 500.0) is None
    assert broker_charges.brokerage_per_order("groww", "standard", day) is None             # not a flat rate
    assert charge("kotak_neo", "trade_free", day, "OPTION", 500.0) == 0.0                   # flat rows still answer
    fee = broker_charges.platform_fee("groww")
    assert fee.per_month == 499.0 and fee.tax == "+ GST" and fee.source.startswith("https://groww.in/") and not fee.verified
    for row in broker_charges.ROWS:
        assert row.source.startswith("https://") and row.read_on and row.note


def test_paper_practising_for_groww_takes_the_same_path_as_live():
    order = BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.BUY, quantity=2, order_type="MARKET",
                               protection_pct=0.5, product="CNC")
    paper = PaperExecutionAdapter(venue="groww", venue_plan="standard", clock=lambda: datetime(2026, 10, 23, 5, 0, tzinfo=timezone.utc))
    paper.mark("RELIANCE", 3001.0)
    response = run(paper.place_order(order))
    live_order, _note = ml.to_venue_order(order, gw.GrowwBroker.capabilities, 3001.0)
    placed = run(paper.get_order_book())[0]
    assert placed.order_type == live_order.order_type == "LIMIT" and placed.price == live_order.price == 3016.05
    assert response.status == "COMPLETE"
    assert paper.capabilities.market_orders_allowed is False and paper.capabilities.stop_market is False
    plain = PaperExecutionAdapter(clock=lambda: datetime(2026, 10, 23, 5, 0, tzinfo=timezone.utc))
    plain.mark("RELIANCE", 3001.0)
    plain_response = run(plain.place_order(order))
    # Groww's stated stock brokerage on ~6,000 of value is 0.1% = ~6 (+ GST) against the default 20 (+ GST).
    assert 10 < plain_response.raw["charges"] - response.raw["charges"] < 20


def test_catalog_entry_gates_guide_and_ui_v2():
    row = catalog_entry("groww").as_dict()
    assert row["method"] == "totp_auto" and row["requires_ui_v2"] is True and row["daily_renewal"] is True
    assert [f["key"] for f in row["fields"]] == ["api_key", "totp_secret"]
    assert row["gates"] == {"live_orders": "closed", "live_prices": "closed", "note": gates.GATES["groww"].note}
    assert all(link["url"].startswith("https://groww.in/") for link in row["guide"]["links"]) and len(row["guide"]["points"]) >= 3
    for text in row["guide"]["points"] + row["how_to"] + [row["gates"]["note"]]:
        lowered = text.lower()
        assert not any(word in lowered for word in ("api_", "token_", "gate", "tm-", "flag", "nats", "jwt")), text
    assert policy_for("groww")["market_for_algo"] == "map_to_limit"


# --- the feed (built, OFF behind the gate) ---------------------------------------------------------------------------

def _price_body(ltp: float, ts_millis: float, *, index: bool = False) -> bytes:
    def double(field: int, value: float) -> bytes:
        return bytes([(field << 3) | 1]) + struct.pack("<d", value)
    inner = double(1, ts_millis) + (double(2, ltp) if index else double(13, ltp))
    return bytes([((6 if index else 4) << 3) | 2, len(inner)]) + inner


def test_the_nkey_is_a_valid_nats_user_key_and_signs_the_nonce():
    keys = gf.NKeyPair(seed=b"\x01" * 32)
    assert keys.public_key.startswith("U") and len(keys.public_key) == 56
    raw = base64.b32decode(keys.public_key + "=" * (-len(keys.public_key) % 8))
    assert raw[0] == gf.PREFIX_USER and raw[1:33] == keys.public_raw
    assert struct.unpack("<H", raw[33:])[0] == gf._crc16(raw[:33])
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    sig = keys.sign(b"nonce-1")
    Ed25519PublicKey.from_public_bytes(keys.public_raw).verify(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)), b"nonce-1")
    assert gf.NKeyPair().public_key != keys.public_key                  # a fresh key per connection


def test_the_price_decoder_reads_the_sdks_protobuf_fields():
    assert gf.decode_price(_price_body(3001.5, 1792737000000.0)) == (3001.5, datetime.fromtimestamp(1792737000, tz=timezone.utc))
    assert gf.decode_price(_price_body(26050.25, 0.0, index=True)) == (26050.25, None)
    assert gf.decode_price(b"\x0a\x03abc") is None                      # a symbol only, no price
    with pytest.raises(ValueError):
        gf.decode_price(b"\x21\x00")                                    # a truncated double
    assert gf.topic_for("CASH", "NSE", "2885") == "/ld/eq/nse/price.2885"
    assert gf.topic_for("FNO", "BSE", "1") == "/ld/fo/bse/price.1"
    assert gf.topic_for("CASH", "NSE", "NIFTY", index=True) == "/ld/indices/nse/price.NIFTY"


class _FakeSocket:
    def __init__(self, frames):
        self.frames, self.sent, self.closed = list(frames), [], False

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.frames:
            raise StopAsyncIteration
        return self.frames.pop(0)

    async def close(self):
        self.closed = True


def test_feed_handshake_subscribe_ping_and_message(gates_open):
    adapter, server = _adapter(access_token=ACCESS)
    body = _price_body(3001.0, 1792737000000.0)
    sock = _FakeSocket([b'INFO {"server_id":"x","nonce":"abc123"}\r\n', b"PONG\r\n", b"PING\r\n",
                        b"MSG /ld/eq/nse/price.2885 1 " + str(len(body)).encode() + b"\r\n" + body[:5], body[5:] + b"\r\n"])

    async def connect(url):
        assert url == gf.FEED_URL
        return sock
    stream = gf.GrowwTickStream(adapter, connector=connect)
    conn = run(stream._connect_and_auth(run(stream.connect_url())))
    token_req = next(r for r in server.requests if str(r.url) == gw.SOCKET_TOKEN_URL)
    socket_key = json.loads(token_req.content)["socketKey"]
    assert socket_key.startswith("U")
    connect_line = sock.sent[0].split(b"\r\n")[0]
    assert connect_line.startswith(b"CONNECT ")
    sent = json.loads(connect_line[8:])
    assert sent["jwt"] == SOCKET_JWT and sent["sig"] and sent["verbose"] is False
    key = run(stream.wire_key("RELIANCE", "NSE"))
    assert key == "/ld/eq/nse/price.2885"
    assert stream.subscribe_frames([key]) == [b"SUB /ld/eq/nse/price.2885 1\r\n"]
    assert stream.unsubscribe_frames([key]) == [b"UNSUB 1\r\n"]

    async def first():
        return await conn.__anext__()
    frame = run(first())
    assert sock.sent[-1] == b"PONG\r\n"                                 # the server's PING was answered
    assert stream.decode(frame) == [("/ld/eq/nse/price.2885", 3001.0, datetime.fromtimestamp(1792737000, tz=timezone.utc))]
    assert stream.decode(b"raw") == []
    assert run(stream.wire_key("NIFTY", "NSE")) == "/ld/indices/nse/price.NIFTY"


def test_feed_refusals_close_the_socket(gates_open):
    adapter, _server = _adapter(access_token=ACCESS)
    refused = _FakeSocket([b'INFO {"nonce":"n"}\r\n', b"-ERR 'Authorization Violation'\r\n"])

    async def connect(url):
        return refused
    with pytest.raises(BrokerAuthenticationError):
        run(gf.GrowwTickStream(adapter, connector=connect)._connect_and_auth(gf.FEED_URL))
    assert refused.closed
    with pytest.raises(BrokerAuthenticationError):
        run(gf.GrowwTickStream(gw.GrowwBroker(_creds())).connect_url())   # no session yet


def test_feed_keeps_at_most_the_subscription_limit(gates_open):
    adapter, _server = _adapter(access_token=ACCESS)
    stream = gf.GrowwTickStream(adapter)
    run(stream.set_symbols([("RELIANCE", "NSE")] * 3))
    assert stream.subscribed == 1 and gf.MAX_SUBSCRIPTIONS == 1000
