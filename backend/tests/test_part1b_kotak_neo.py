"""Part 1B: the Kotak Neo adapter against recorded fixtures (tests/fixtures/kotak_neo/recorded.json, the SDK 3.0.9
documented shapes with made-up values) - the TOTP login and the stored session, the gates (no LIVE order and no market
data leaves ATP while they are closed), the order wire format with the gates opened in a test, TM-3's marketable limit,
the books and account reads, symbol mapping to ATP's instrument master, the feed decoder, the charges table and the
PAPER parity. No network, no real account, no secret: fake tokens are built at run time."""
import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pyotp
import pytest

from app.brokers import broker_charges, gates
from app.brokers import kotak_neo as kn
from app.brokers.catalog import entry as catalog_entry
from app.brokers.contract_symbols import wrap_contract_symbols
from app.brokers.exceptions import BrokerAuthenticationError, BrokerOrderRejected
from app.brokers.models import BrokerCredentials, BrokerOrderRequest, BrokerOrderStatus
from app.brokers.rate_budget import limits_for
from app.brokers.registry import get_broker_adapter
from app.brokers.token_lifecycle import TOKEN_DAILY_EXPIRY_IST, cap_expiry_at_token_exp, default_token_expiry
from app.compliance.order_policy import policy_for
from app.core.enums import OrderSide
from app.execution import marketable_limit as ml
from app.execution.paper_adapter import PaperExecutionAdapter
from app.execution.router import DEFAULT_MARKET_PROTECTION_PCT
from app.market_data import kotak_feed
from app.market_data.data_terms import terms_for
from app.market_data.stream import stream_for
from tests.broker_fakes import KOTAK_CONSUMER_KEY, KOTAK_MOBILE, KOTAK_MPIN, KOTAK_UCC, TOTP_SECRET

run = asyncio.run
FIX = json.loads((Path(__file__).parent / "fixtures" / "kotak_neo" / "recorded.json").read_text())
VIEW_TOKEN = ".".join(["fake" + "view", "fake" + "body", "fake" + "sig"])
VIEW_SID = "fake" + "-view-sid"
TRADE_TOKEN = ".".join(["fake" + "trade", "fake" + "body", "fake" + "sig"])
TRADE_SID = "fake" + "-trade-sid"
BASE = "https://e21.kotaksecurities.com"


def _creds(**over) -> BrokerCredentials:
    body = {"api_key": KOTAK_CONSUMER_KEY, "mobile_number": KOTAK_MOBILE, "client_id": KOTAK_UCC, "pin": KOTAK_MPIN,
            "totp_secret": TOTP_SECRET}
    body.update(over)
    return BrokerCredentials(**body)


def _session() -> str:
    return kn.encode_session(TRADE_TOKEN, TRADE_SID, BASE, "FAKEUCC1", "gdc")


class _Server:
    """A fake Kotak Neo: answers each SDK path with its recorded fixture and records every request."""

    def __init__(self):
        self.requests = []

    def paths(self):
        return [r.url.path for r in self.requests]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url, path = str(request.url), request.url.path
        if url.endswith("nse_cm-v1.csv"):
            return httpx.Response(200, text=FIX["scrip_nse_cm"])
        if url.endswith("nse_fo.csv"):
            return httpx.Response(200, text=FIX["scrip_nse_fo"])
        assert request.headers.get("Authorization") == KOTAK_CONSUMER_KEY or path in (kn.POSITIONS_PATH, kn.HOLDINGS_PATH,
                                                                                    kn.ORDER_BOOK_PATH, kn.TRADE_BOOK_PATH,
                                                                                    kn.LIMITS_PATH, kn.MARGIN_PATH), path
        if path == kn.TOTP_LOGIN_PATH:
            assert request.url.host == "mis.kotaksecurities.com" and request.headers["neo-fin-key"] == "neotradeapi"
            body = json.loads(request.content)
            assert body == {"mobileNumber": KOTAK_MOBILE, "ucc": KOTAK_UCC, "totp": pyotp.TOTP(TOTP_SECRET).now()}
            data = dict(FIX["totp_login"]["data"], token=VIEW_TOKEN, sid=VIEW_SID)
            return httpx.Response(200, json={"data": data})
        if path == kn.TOTP_VALIDATE_PATH:
            assert request.headers["sid"] == VIEW_SID and request.headers["Auth"] == VIEW_TOKEN
            assert json.loads(request.content) == {"mpin": KOTAK_MPIN}
            data = dict(FIX["totp_validate"]["data"], token=TRADE_TOKEN, sid=TRADE_SID)
            return httpx.Response(200, json={"data": data})
        if path == kn.CLIENT_IP_PATH:
            assert request.headers["Auth"] == TRADE_TOKEN and request.headers["Sid"] == TRADE_SID
            return httpx.Response(200, json=FIX["client_ip"])
        assert request.url.host == "e21.kotaksecurities.com", url       # every later call goes to the session's base URL
        if path == kn.SCRIP_MASTER_PATH:
            return httpx.Response(200, json=FIX["file_paths"])
        if path.startswith("/script-details/1.0/quotes/neosymbol/"):
            return httpx.Response(200, json=FIX["quotes"])
        if path == kn.OPTION_CHAIN_PATH:
            return httpx.Response(200, json=FIX["option_chain"])
        if path == kn.HISTORY_PATH:
            return httpx.Response(200, json=FIX["history"])
        assert request.headers["Auth"] == TRADE_TOKEN and request.headers["Sid"] == TRADE_SID, path
        table = {kn.ORDER_BOOK_PATH: "order_book", kn.TRADE_BOOK_PATH: "trade_book", kn.POSITIONS_PATH: "positions",
                 kn.HOLDINGS_PATH: "holdings", kn.LIMITS_PATH: "limits", kn.MARGIN_PATH: "margin", kn.PLACE_ORDER_PATH: "place_order",
                 kn.MODIFY_ORDER_PATH: "modify_order", kn.CANCEL_ORDER_PATH: "cancel_order"}
        if path in table:
            return httpx.Response(200, json=FIX[table[path]])
        return httpx.Response(404, json={"errMsg": "unknown path"})


def _adapter(server=None, **creds):
    server = server or _Server()
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return kn.KotakNeoBroker(_creds(**creds), client), server


def _jdata(request: httpx.Request) -> dict:
    return json.loads(parse_qs(request.content.decode())["jData"][0])


@pytest.fixture
def gates_open(monkeypatch):
    """Both Kotak Neo gates opened for one test only (the wire format behind them); the real GATES stay closed."""
    monkeypatch.setitem(gates.GATES, "kotak_neo", gates.BrokerGates())
    monkeypatch.setattr(gates, "market_data_open", lambda broker: True)


# --- session -----------------------------------------------------------------------------------------------------

def test_session_string_round_trip_and_refuses_anything_else():
    value = _session()
    assert value.startswith("kn1.") and TRADE_TOKEN not in value and " " not in value
    assert kn.decode_session(value) == {"t": TRADE_TOKEN, "s": TRADE_SID, "b": BASE, "u": "FAKEUCC1", "dc": "gdc"}
    for bad in (None, "", "kn1.", "kn1.!!!", TRADE_TOKEN, "kn1." + "e30"):
        assert kn.decode_session(bad) is None


def test_totp_login_makes_the_code_validates_the_mpin_and_keeps_one_session_string():
    adapter, server = _adapter()
    profile = run(adapter.authenticate())
    assert profile.user_id == "FAKEUCC1" and profile.name == "Test Trader" and profile.broker == "kotak_neo"
    assert server.paths() == [kn.TOTP_LOGIN_PATH, kn.TOTP_VALIDATE_PATH]
    assert kn.decode_session(adapter.access_token)["b"] == BASE and adapter.base_url == BASE
    assert run(adapter.client_ip()) == "198.51.100.7"


def test_a_refused_login_and_a_refused_session_are_authentication_errors():
    def refuse(request):
        return httpx.Response(200, json={"stat": "Not_Ok", "stCode": 1001, "errMsg": "Invalid TOTP"})
    adapter = kn.KotakNeoBroker(_creds(), httpx.AsyncClient(transport=httpx.MockTransport(refuse)))
    with pytest.raises(Exception) as exc:
        run(adapter.authenticate())
    assert "Invalid TOTP" in str(exc.value)

    def expired(request):
        return httpx.Response(403, json={"message": "Invalid session, please re-login to continue"})
    stored = kn.KotakNeoBroker(BrokerCredentials(api_key=KOTAK_CONSUMER_KEY, access_token=_session()),
                               httpx.AsyncClient(transport=httpx.MockTransport(expired)))
    with pytest.raises(BrokerAuthenticationError):
        run(stored.get_profile())


def test_a_stored_session_needs_no_login_fields_and_the_registry_builds_the_adapter():
    adapter = get_broker_adapter("kotak_neo", BrokerCredentials(api_key=KOTAK_CONSUMER_KEY, access_token=_session()))
    assert isinstance(adapter, kn.KotakNeoBroker) and adapter.base_url == BASE
    with pytest.raises(BrokerAuthenticationError):
        kn.KotakNeoBroker(BrokerCredentials(api_key=KOTAK_CONSUMER_KEY))
    with pytest.raises(BrokerAuthenticationError):
        kn.KotakNeoBroker(BrokerCredentials(mobile_number=KOTAK_MOBILE, client_id=KOTAK_UCC, pin=KOTAK_MPIN))


def test_token_lifecycle_daily_expiry_and_the_session_string_never_lengthens_it():
    assert "kotak_neo" in TOKEN_DAILY_EXPIRY_IST
    now = datetime(2026, 10, 23, 4, 0, tzinfo=timezone.utc)            # 09:30 IST
    assert default_token_expiry("kotak_neo", now) == datetime(2026, 10, 24, 0, 30, tzinfo=timezone.utc)

    class Row:
        token_status = "VALID"
        token_expires_at = datetime(2026, 10, 24, 0, 30, tzinfo=timezone.utc)
    row = Row()
    cap_expiry_at_token_exp(row, _session())                            # not a JWT: the daily schedule stands
    assert row.token_expires_at == datetime(2026, 10, 24, 0, 30, tzinfo=timezone.utc)


# --- the gates (closed: the real state) -----------------------------------------------------------------------------

def test_the_gates_are_closed_for_kotak_neo_and_open_for_everyone_else():
    assert not gates.live_orders_open("kotak_neo") and not gates.market_data_open("kotak_neo")
    assert terms_for("kotak_neo").status.value == "pending"
    for other in ("upstox", "zerodha", "dhan", "angel_one", "fyers", "shoonya", "coindcx"):
        assert gates.live_orders_open(other) and gates.market_data_open(other), other


def test_no_live_order_leaves_atp_while_the_gate_is_closed():
    adapter, server = _adapter(access_token=_session())
    order = BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.BUY, quantity=1, order_type="LIMIT",
                               price=3000.0, product="MIS")
    calls = [adapter.place_order(order), adapter.modify_order("261023000000101", price=2990.0), adapter.cancel_order("261023000000101"),
             adapter.exit_position("RELIANCE", "NSE", 1, OrderSide.BUY),
             adapter.place_stop_loss_order("RELIANCE", "NSE", OrderSide.SELL, 1, 2950.0)]
    for call in calls:
        with pytest.raises(gates.LiveOrdersClosed) as exc:
            run(call)
        assert "not offered yet" in str(exc.value) and "Nothing was sent" in str(exc.value)
    # Through the contract-symbol wrapper (the platform's path) as well.
    wrapped = wrap_contract_symbols(adapter)
    with pytest.raises(gates.LiveOrdersClosed):
        run(wrapped.place_order(order))
    assert server.requests == []


def test_no_market_data_is_asked_while_the_gate_is_closed():
    adapter, server = _adapter(access_token=_session())
    for call in (adapter.get_ltp(["NSE:RELIANCE"]), adapter.get_quote(["NSE:RELIANCE"]), adapter.get_quote_for_symbol("RELIANCE"),
                 adapter.get_ltp_for_symbol("RELIANCE"), adapter.get_quotes_batch([("NSE", "RELIANCE")]),
                 adapter.get_historical_data("RELIANCE", "NSE", "5min", datetime(2026, 10, 22), datetime(2026, 10, 22)),
                 adapter.get_intraday_candles("RELIANCE", "NSE", "5min"), adapter.get_option_chain("NIFTY")):
        with pytest.raises(gates.MarketDataClosed):
            run(call)
    assert server.requests == []
    assert stream_for(adapter) is None                                   # no feed socket is ever opened


def test_account_reads_work_with_the_gates_closed_and_carry_no_broker_price():
    adapter, server = _adapter(access_token=_session())
    positions = {p.symbol: p for p in run(adapter.get_positions())}
    fut = positions["NIFTY26NOVFUT"]
    assert fut.quantity == 65 and fut.average_price == 25000.0 and fut.ltp == 0.0 and fut.pnl == 0.0 and fut.exchange == "NFO"
    flat = positions["TCS"]
    assert flat.quantity == 0 and flat.pnl == 250.0 and flat.ltp == 0.0        # booked P&L needs no price
    holding = run(adapter.get_holdings())[0]
    assert (holding.symbol, holding.quantity, holding.average_price, holding.ltp, holding.pnl) == ("RELIANCE", 4, 2800.5, 0.0, 0.0)
    margins = run(adapter.get_margins())
    assert margins.available_cash == 150000.25 and margins.used_margin == 25000.0
    book = run(adapter.get_order_book())
    assert [(o.order_id, o.symbol, o.status, o.exchange, o.tag) for o in book] == [
        ("261023000000101", "RELIANCE", "OPEN", "NSE", "ATPFAKE1"), ("261023000000102", "NIFTY26O2726000CE", "COMPLETE", "NFO", None)]
    assert book[1].transaction_type == OrderSide.SELL and book[1].average_price == 121.1 and book[1].placed_at.tzinfo is not None
    trade = run(adapter.get_trade_book())[0]
    assert trade.trade_id == "fake-fill-1" and trade.quantity == 65 and trade.timestamp.hour == 10
    margin = run(adapter.get_order_margin(BrokerOrderRequest(symbol="RELIANCE", transaction_type=OrderSide.BUY, quantity=1,
                                                             order_type="LIMIT", price=3000.0)))
    assert margin == 15500.0
    assert "/quick/order/rule/ms/place" not in server.paths()


def test_with_prices_open_positions_and_holdings_read_the_broker_price(gates_open):
    adapter, _server = _adapter(access_token=_session())
    fut = next(p for p in run(adapter.get_positions()) if p.symbol == "NIFTY26NOVFUT")
    assert fut.ltp == 25100.0 and fut.pnl == round(-1625000 + 65 * 25100.0, 2)
    holding = run(adapter.get_holdings())[0]
    assert holding.ltp == 3000.0 and holding.pnl == round((3000.0 - 2800.5) * 4, 2)


# --- the wire format behind the gates (opened only in these tests) ----------------------------------------------------

def test_a_limit_order_goes_as_the_sdk_sends_it(gates_open):
    adapter, server = _adapter(access_token=_session())
    order = BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.BUY, quantity=10, order_type="LIMIT",
                               price=2999.5, product="MIS", tag="ATP-ALGO-1-TOO-LONG-FOR-THE-FIELD")
    response = run(adapter.place_order(order))
    assert response.order_id == "261023000000201" and response.status == "OPEN"
    sent = next(r for r in server.requests if r.url.path == kn.PLACE_ORDER_PATH)
    assert sent.headers["Content-Type"] == "application/x-www-form-urlencoded" and sent.headers["Authorization"] == KOTAK_CONSUMER_KEY
    assert _jdata(sent) == {"am": "NO", "dq": "0", "es": "nse_cm", "mp": "0", "pc": "MIS", "pr": "2999.50", "pt": "L", "qt": "10",
                            "rt": "DAY", "tp": "0.00", "ts": "RELIANCE-EQ", "tt": "B", "os": "NEOTRADEAPI", "ig": "ATP-ALGO-1-TOO-LONG-"}


def test_tm3_a_market_order_goes_as_a_marketable_limit_from_the_users_band(gates_open):
    adapter, server = _adapter(access_token=_session())
    order = BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.SELL, quantity=4, order_type="MARKET",
                               product="CNC", protection_pct=1.0)
    response = run(adapter.place_order(order))
    body = _jdata(next(r for r in server.requests if r.url.path == kn.PLACE_ORDER_PATH))
    # The broker's own last price 3001.00 (quotes fixture), 1% below for a sell, rounded down to the tick: 2970.95.
    assert body["pt"] == "L" and body["pr"] == "2970.95" and body["pc"] == "CNC" and body["tt"] == "S"
    assert "limit order at 2970.95" in (response.message or "")
    # No band set: the platform's stated default for a protected limit.
    run(adapter.place_order(order.model_copy(update={"protection_pct": None, "transaction_type": OrderSide.BUY})))
    body = _jdata([r for r in server.requests if r.url.path == kn.PLACE_ORDER_PATH][-1])
    assert body["pr"] == f"{ml.marketable_limit_price(3001.0, OrderSide.BUY, DEFAULT_MARKET_PROTECTION_PCT):.2f}" == "3016.05"


def test_a_stop_goes_as_sl_with_a_band_and_kotak_refuses_what_it_does_not_take(gates_open):
    adapter, server = _adapter(access_token=_session())
    run(adapter.place_stop_loss_order("RELIANCE", "NSE", OrderSide.SELL, 4, 2950.0, product="MIS"))
    body = _jdata([r for r in server.requests if r.url.path == kn.PLACE_ORDER_PATH][-1])
    assert body["pt"] == "SL" and body["tp"] == "2950.00" and float(body["pr"]) < 2950.0
    with pytest.raises(BrokerOrderRejected):
        run(adapter.place_order(BrokerOrderRequest(symbol="RELIANCE", transaction_type=OrderSide.BUY, quantity=1, order_type="LIMIT",
                                                   price=3000.0, product="BO")))
    with pytest.raises(BrokerOrderRejected):
        run(adapter.place_order(BrokerOrderRequest(symbol="RELIANCE", transaction_type=OrderSide.BUY, quantity=1, order_type="LIMIT",
                                                   price=3000.0, validity="GTC")))
    with pytest.raises(BrokerOrderRejected):
        run(adapter.modify_order("261023000000101", order_type="MARKET"))


def test_amo_modify_cancel_and_an_already_complete_order(gates_open):
    adapter, server = _adapter(access_token=_session())
    run(adapter.place_order(BrokerOrderRequest(symbol="TCS", transaction_type=OrderSide.BUY, quantity=1, order_type="LIMIT",
                                               price=3000.0, product="CNC", after_market=True)))
    assert _jdata([r for r in server.requests if r.url.path == kn.PLACE_ORDER_PATH][-1])["am"] == "YES"
    run(adapter.modify_order("261023000000101", price=2990.0))
    body = _jdata([r for r in server.requests if r.url.path == kn.MODIFY_ORDER_PATH][-1])
    assert body["no"] == "261023000000101" and body["pr"] == "2990.00" and body["pt"] == "L" and body["qt"] == "10" and body["mp"] == "0"
    run(adapter.cancel_order("261023000000101"))
    assert _jdata([r for r in server.requests if r.url.path == kn.CANCEL_ORDER_PATH][-1]) == {"on": "261023000000101", "am": "NO"}

    def complete(request):
        return httpx.Response(200, json=FIX["order_complete"])
    done = kn.KotakNeoBroker(BrokerCredentials(api_key=KOTAK_CONSUMER_KEY, access_token=_session()),
                             httpx.AsyncClient(transport=httpx.MockTransport(complete)))
    with pytest.raises(BrokerOrderRejected):
        run(done.cancel_order("261023000000102"))


def test_market_data_behind_the_open_gate(gates_open):
    adapter, server = _adapter(access_token=_session())
    quote = run(adapter.get_quote(["NSE:RELIANCE"]))["NSE:RELIANCE"]
    assert quote.ltp == 3001.0 and quote.prev_close == 2995.0 and quote.bid == 3000.9 and quote.timestamp is not None
    url = next(r for r in server.requests if "/quotes/neosymbol/" in r.url.path).url
    assert "nse_cm|2885" in url.path and url.path.endswith("/all")
    bars = run(adapter.get_historical_data("RELIANCE", "NSE", "5min", datetime(2026, 10, 22, 3, 45, tzinfo=timezone.utc),
                                           datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)))
    assert [b.close for b in bars] == [2999.5, 3002.0] and bars[0].timestamp.utcoffset().total_seconds() == 19800
    params = next(r for r in server.requests if r.url.path == kn.HISTORY_PATH).url.params
    assert dict(params) == {"neosymbol": "nse_cm|2885", "interval": "5min", "fromdate": "2026-10-22", "todate": "2026-10-22"}
    chain = run(adapter.get_option_chain("NIFTY", date(2026, 10, 27)))
    row = chain.rows[0]
    assert chain.expiry == "2026-10-27" and row.strike == 26000 and row.call_ltp == 120.5 and row.put_oi == 800000 and row.put_change_oi == -20000
    assert stream_for(adapter) is not None


# --- symbols ---------------------------------------------------------------------------------------------------

def test_scrip_master_parsing_and_symbol_mapping_to_the_instrument_master():
    adapter, _server = _adapter(access_token=_session())
    equities = run(adapter.get_instruments("NSE"))
    reliance = next(i for i in equities if i.tradingsymbol == "RELIANCE")
    assert reliance.instrument_token == "2885" and reliance.tick_size == 0.05 and reliance.instrument_type == "EQ"
    assert run(adapter.resolve("RELIANCE", "NSE")) == ("nse_cm", "2885", "RELIANCE-EQ")
    assert run(adapter.resolve("RELIANCE-EQ", "NSE")) == ("nse_cm", "2885", "RELIANCE-EQ")
    assert run(adapter.resolve("NIFTY", "NSE")) == ("nse_cm", "Nifty 50", "Nifty 50")
    assert run(adapter.resolve("SENSEX", "BSE"))[0] == "bse_cm"
    options = run(adapter.get_instruments("NFO"))
    call = next(i for i in options if i.instrument_token == "71472")
    assert (call.name, call.expiry, call.strike, call.instrument_type, call.lot_size) == ("NIFTY", "2026-10-27", 26000.0, "CE", 65)
    fut = next(i for i in options if i.instrument_token == "53001")
    assert fut.instrument_type == "FUT" and fut.expiry == "2026-11-24" and fut.strike is None
    # The platform's spelling (Upstox master) goes out in Kotak's and comes back in the platform's.
    wrapped = wrap_contract_symbols(adapter)
    assert run(wrapped._out("NIFTY 26000 CE 27 OCT 26", "NFO")) == "NIFTY26O2726000CE"
    assert run(wrapped._out("NIFTY FUT 24 NOV 26", "NFO")) == "NIFTY26NOVFUT"
    assert run(wrapped._back("NIFTY26O2726000PE", "NFO")) == "NIFTY 26000 PE 27 OCT 26"
    with pytest.raises(Exception):
        run(adapter.resolve("NOSUCHSTOCK", "NSE"))


def test_expiry_epochs_and_symbol_spellings():
    assert kn.parse_expiry("1477584000", "nse_fo") == "2026-10-27"          # NSE F&O counts from 1980
    assert kn.parse_expiry("1793095200", "bse_fo") == "2026-10-27"          # BSE F&O / MCX: Unix time
    assert kn.parse_expiry("", "nse_fo") is None and kn.parse_expiry("-1", "nse_fo") is None
    assert kn.platform_symbol("IDEA-EQ", "nse_cm") == "IDEA" and kn.platform_symbol("BAJAJ-AUTO", "nse_cm") == "BAJAJ-AUTO"
    assert kn.platform_symbol("NIFTY26NOVFUT", "nse_fo") == "NIFTY26NOVFUT"
    assert kn.KotakNeoBroker._pick_file(FIX["file_paths"]["data"]["filesPaths"], "nse_cm").endswith("nse_cm-v1.csv")


# --- TM-3: the marketable limit ---------------------------------------------------------------------------------------

def test_marketable_limit_band_rounds_outward_and_keeps_the_platform_default():
    assert ml.DEFAULT_PROTECTION_PCT == DEFAULT_MARKET_PROTECTION_PCT
    assert ml.marketable_limit_price(100.0, OrderSide.BUY, 0.5) == 100.5
    assert ml.marketable_limit_price(100.02, OrderSide.BUY, 0.5) == 100.55      # 100.5201 up to the tick
    assert ml.marketable_limit_price(100.02, OrderSide.SELL, 0.5) == 99.5       # 99.5199 down to the tick
    assert ml.marketable_limit_price(0.06, OrderSide.SELL, 50) == 0.05          # never below one tick
    for bad in (0, -1, float("nan"), None):
        with pytest.raises(ValueError):
            ml.marketable_limit_price(bad, OrderSide.BUY, 0.5)
    market = BrokerOrderRequest(symbol="X", transaction_type=OrderSide.BUY, quantity=1, order_type="MARKET", protection_pct=2.0)
    same, note = ml.to_venue_order(market, kn.KotakNeoBroker.capabilities.__class__(), 100.0)   # a venue that takes market orders
    assert same is market and note == ""
    limit, note = ml.to_venue_order(market, kn.KotakNeoBroker.capabilities, 100.0)
    assert (limit.order_type, limit.price) == ("LIMIT", 102.0) and "protection band" in note
    with pytest.raises(ValueError):
        ml.to_venue_order(market, kn.KotakNeoBroker.capabilities, None)
    assert ml.band_pct(market.model_copy(update={"protection_pct": -3})) == ml.DEFAULT_PROTECTION_PCT


class _Book:
    """An order book that fills after N re-prices; records every modify."""

    def __init__(self, fill_after: int, status: str = "OPEN"):
        self.fill_after, self.status, self.modifies, self.cancelled = fill_after, status, [], False

    async def get_order_book(self):
        filled = len(self.modifies) >= self.fill_after
        return [BrokerOrderStatus(order_id="1", symbol="X", transaction_type=OrderSide.SELL, quantity=5, filled_quantity=5 if filled else 0,
                                  order_type="LIMIT", status="COMPLETE" if filled else self.status)]

    async def modify_order(self, order_id, price=None, **_):
        self.modifies.append(price)

    async def cancel_order(self, order_id):
        self.cancelled = True


def test_reprice_until_filled_follows_the_price_and_never_cancels_an_exit():
    async def no_sleep(_):
        return None
    prices = iter([99.0, 98.0, 97.0, 96.0])

    async def reference():
        return next(prices)
    exit_order = BrokerOrderRequest(symbol="X", transaction_type=OrderSide.SELL, quantity=5, order_type="MARKET", protection_pct=1.0)
    book = _Book(fill_after=2)
    result = run(ml.reprice_until_filled(book, "1", exit_order, 99.0, reference, attempts=5, wait_seconds=0, sleep=no_sleep))
    assert result.filled and result.limits == [99.0, 98.0, 97.0] and book.modifies == [98.0, 97.0] and not book.cancelled
    stuck = _Book(fill_after=99)
    prices = iter([90.0, 89.0])
    result = run(ml.reprice_until_filled(stuck, "1", exit_order, 99.0, reference, attempts=2, wait_seconds=0, sleep=no_sleep))
    assert not result.filled and result.status == "OPEN" and "not cancelled" in result.note and not stuck.cancelled

    async def broken():
        raise RuntimeError("no price")
    quiet = _Book(fill_after=99)
    result = run(ml.reprice_until_filled(quiet, "1", exit_order, 99.0, broken, attempts=3, wait_seconds=0, sleep=no_sleep))
    assert quiet.modifies == [] and not quiet.cancelled and result.limits == [99.0]
    ended = _Book(fill_after=99, status="REJECTED")
    result = run(ml.reprice_until_filled(ended, "1", exit_order, 99.0, reference, attempts=3, wait_seconds=0, sleep=no_sleep))
    assert result.status == "REJECTED" and ended.modifies == []


def test_paper_practising_for_kotak_neo_takes_the_same_path_as_live():
    order = BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.BUY, quantity=2, order_type="MARKET",
                               protection_pct=0.5, product="CNC")
    paper = PaperExecutionAdapter(venue="kotak_neo", venue_plan="trade_free",
                                  clock=lambda: datetime(2026, 10, 23, 5, 0, tzinfo=timezone.utc))
    paper.mark("RELIANCE", 3001.0)
    response = run(paper.place_order(order))
    live_order, _note = ml.to_venue_order(order, kn.KotakNeoBroker.capabilities, 3001.0)
    placed = run(paper.get_order_book())[0]
    assert placed.order_type == live_order.order_type == "LIMIT" and placed.price == live_order.price == 3016.05
    assert response.status == "COMPLETE" and response.raw["average_price"] <= 3016.05
    assert paper.capabilities.market_orders_allowed is False and paper.capabilities.stop_market is False
    # The broker's stated API brokerage (Rs 0 on Trade Free) - only the statutory charges remain.
    plain = PaperExecutionAdapter(clock=lambda: datetime(2026, 10, 23, 5, 0, tzinfo=timezone.utc))
    plain.mark("RELIANCE", 3001.0)
    plain_response = run(plain.place_order(order))
    assert plain_response.raw["charges"] - response.raw["charges"] > 15                     # the default Rs 20 + GST is gone
    assert run(plain.get_order_book())[0].order_type == "MARKET"                            # no venue: as before


# --- charges, catalogue, budget, policy, data sources ---------------------------------------------------------------

def test_charges_table_states_only_what_the_broker_states_with_its_source():
    assert broker_charges.brokerage_per_order("kotak_neo", "trade_free", date(2026, 10, 23)) == 0.0
    assert broker_charges.brokerage_per_order("kotak_neo", "trade_free", date(2025, 10, 31)) is None   # before it applied
    assert broker_charges.brokerage_per_order("kotak_neo", "other_plan", date(2026, 10, 23)) is None
    assert broker_charges.brokerage_per_order("kotak_neo", None, date(2026, 10, 23)) is None
    for row in broker_charges.ROWS:
        assert row.source.startswith("https://") and row.read_on and row.note
    assert broker_charges.unverified()                                                     # re-read on the broker's page before LIVE


def test_catalog_entry_gates_guide_and_ui_v2():
    row = catalog_entry("kotak_neo").as_dict()
    assert row["method"] == "totp_auto" and row["requires_ui_v2"] is True and row["daily_renewal"] is True
    assert row["gates"] == {"live_orders": "closed", "live_prices": "closed", "note": gates.GATES["kotak_neo"].note}
    assert all(link["url"].startswith("https://") for link in row["guide"]["links"]) and len(row["guide"]["points"]) >= 3
    for text in row["guide"]["points"] + row["how_to"] + [row["gates"]["note"]]:
        lowered = text.lower()
        assert not any(word in lowered for word in ("api_", "token_", "gate", "tm-", "flag")), text
    assert catalog_entry("upstox").as_dict()["gates"]["live_orders"] == "open"


def test_rate_budget_policy_and_data_sources():
    assert limits_for("kotak_neo").per_second <= 10 / 3 + 0.01
    assert policy_for("kotak_neo")["market_for_algo"] == "map_to_limit"
    assert policy_for("upstox")["market_for_algo"] == "allow"

    class Row:
        def __init__(self, name):
            self.broker_name = name
    rows = [Row("kotak_neo"), Row("upstox"), Row("zerodha")]
    assert [r.broker_name for r in gates.data_sources(rows)] == ["upstox", "zerodha"]


# --- the feed decoder (built, OFF behind the gate) --------------------------------------------------------------------

def _header(code: int, exchange_id: int, level: int, body: bytes) -> bytes:
    return kotak_feed.HEADER.pack(kotak_feed.HEADER_SIZE + len(body), code, exchange_id, level, 0, 1, 0) + body


def test_feed_decoder_reads_the_batched_little_endian_packets():
    mini = kotak_feed.MINI_BODY.pack(2885, 1792737000, 300100, 5, 299500, 20, 600, 1, 2, 1)
    index_body = kotak_feed.INDEX_BODY.pack(0, 2600000, 2590000, 2610000, 2580000, 2605025, 1792737000, 0, 0, 58, 0.0, 2, 1,
                                            b"Nifty 50".ljust(21, b"\x00"))
    frame = _header(1, 1, 1, mini) + _header(kotak_feed.MSG_INDEX, 1, 0, index_body) + b"\x05"      # a truncated tail is ignored
    stream = kotak_feed.KotakNeoTickStream(object())
    stream.dividers = {1: 100}
    ticks = stream.decode(frame)
    assert ticks[0][0] == "nse_cm|2885" and ticks[0][1] == 3001.0 and ticks[0][2].year == 2026
    assert ticks[1][0] == "idx:nse_cm|Nifty 50" and ticks[1][1] == 26050.25
    assert stream.decode("not binary") == []
    frames = stream.subscribe_frames(["nse_cm|2885", "idx:nse_cm|Nifty 50"])
    assert [json.loads(f) for f in frames] == [{"event": "subscribeScripsLite", "inputtoken": "nse_cm|2885"},
                                               {"event": "subscribeIndices", "inputtoken": "nse_cm|Nifty 50"}]
    assert json.loads(stream.unsubscribe_frames(["nse_cm|2885"])[0])["event"] == "unsubscribeScripsLite"


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


def test_feed_auth_frame_and_a_refused_session(gates_open):
    adapter, _server = _adapter(access_token=_session())
    answer = json.dumps({"message_code": 1119, "format": "native_batch", "exchanges": {"nse_cm": {"value": 1, "divider": 100},
                                                                                       "mcx_fo": {"value": 9, "divider": 10000}}})
    sock = _FakeSocket([answer])

    async def connect(url):
        assert url == kotak_feed.FEED_URL
        return sock
    stream = kotak_feed.KotakNeoTickStream(adapter, connector=connect)
    conn = run(stream._connect_and_auth(run(stream.connect_url())))
    auth = json.loads(sock.sent[0])
    assert auth["user"] == TRADE_SID and auth["auth"] == TRADE_TOKEN and auth["format"] == "native_batch"
    assert stream.dividers == {1: 100, 9: 10000} and conn is not sock
    refused = _FakeSocket([json.dumps({"message_code": 9999})])

    async def connect_refused(url):
        return refused
    with pytest.raises(BrokerAuthenticationError):
        run(kotak_feed.KotakNeoTickStream(adapter, connector=connect_refused)._connect_and_auth(kotak_feed.FEED_URL))
    assert refused.closed
    assert run(kotak_feed.KotakNeoTickStream(adapter).wire_key("RELIANCE", "NSE")) == "nse_cm|2885"
    assert run(kotak_feed.KotakNeoTickStream(adapter).wire_key("NIFTY", "NSE")) == "idx:nse_cm|Nifty 50"
