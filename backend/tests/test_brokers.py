import asyncio
import gzip
import hashlib
import json
from typing import Callable
from urllib.parse import parse_qs

import httpx
import pytest

from app.brokers.base import BrokerInterface
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.models import BrokerCredentials, BrokerOrderRequest
from app.brokers.registry import available_brokers, get_broker_adapter
from app.brokers.shoonya import ShoonyaBroker
from app.brokers.angel_one import AngelOneBroker
from app.brokers.coindcx import CoinDCXBroker
from app.brokers.stubs import _StubBrokerAdapter
from app.brokers.upstox import UpstoxBroker
from app.brokers.zerodha import ZerodhaBroker
from app.core.enums import OrderSide

run = asyncio.run


def _mock_client(handler: Callable[[httpx.Request], httpx.Response], base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)


def test_broker_interface_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        BrokerInterface()


# --- Zerodha -----------------------------------------------------------------

def test_zerodha_get_profile_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "token key123:tok456"
        assert request.url.path == "/user/profile"
        return httpx.Response(200, json={
            "status": "success",
            "data": {"user_id": "AB1234", "user_name": "Test User", "email": "test@example.com"},
        })

    creds = BrokerCredentials(api_key="key123", access_token="tok456")
    broker = ZerodhaBroker(creds, client=_mock_client(handler, ZerodhaBroker.BASE_URL))

    profile = run(broker.get_profile())
    assert profile.broker == "zerodha"
    assert profile.user_id == "AB1234"
    assert profile.name == "Test User"


def test_zerodha_place_order_sends_expected_fields_and_parses_order_id():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"status": "success", "data": {"order_id": "151220000000123"}})

    creds = BrokerCredentials(api_key="key123", access_token="tok456")
    broker = ZerodhaBroker(creds, client=_mock_client(handler, ZerodhaBroker.BASE_URL))

    order = BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.BUY, quantity=10)
    response = run(broker.place_order(order))

    assert response.order_id == "151220000000123"
    assert captured["path"] == "/orders/regular"
    assert "tradingsymbol=RELIANCE" in captured["body"]
    assert "transaction_type=BUY" in captured["body"]


def test_zerodha_get_ltp_parses_multiple_symbols():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "status": "success",
            "data": {
                "NSE:INFY": {"instrument_token": 408065, "last_price": 1500.5},
                "NSE:TCS": {"instrument_token": 2953217, "last_price": 3800.25},
            },
        })

    creds = BrokerCredentials(api_key="key123", access_token="tok456")
    broker = ZerodhaBroker(creds, client=_mock_client(handler, ZerodhaBroker.BASE_URL))

    result = run(broker.get_ltp(["NSE:INFY", "NSE:TCS"]))
    assert result == {"NSE:INFY": 1500.5, "NSE:TCS": 3800.25}


def test_zerodha_raises_broker_api_error_on_failure_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"status": "error", "message": "Invalid session"})

    creds = BrokerCredentials(api_key="key123", access_token="expired")
    broker = ZerodhaBroker(creds, client=_mock_client(handler, ZerodhaBroker.BASE_URL))

    with pytest.raises(BrokerAPIError):
        run(broker.get_profile())


def test_zerodha_requires_api_key():
    with pytest.raises(BrokerAuthenticationError):
        ZerodhaBroker(BrokerCredentials())


def test_zerodha_requires_authentication_before_calls():
    creds = BrokerCredentials(api_key="key123")  # no access_token
    broker = ZerodhaBroker(creds, client=httpx.AsyncClient())
    with pytest.raises(BrokerAuthenticationError):
        run(broker.get_profile())


def test_zerodha_get_instruments_treats_equity_strike_zero_as_not_an_option():
    """Kite's instruments CSV puts the string "0" (non-empty, so truthy) in the strike column
    for every non-option row - regression test that this is parsed to strike=None, not 0.0,
    which would otherwise make every equity/future instrument look like an option at strike 0.
    """
    csv_text = (
        "instrument_token,exchange,tradingsymbol,name,segment,instrument_type,lot_size,tick_size,expiry,strike\n"
        "128031,NSE,RELIANCE,RELIANCE,NSE,EQ,1,0.05,,0\n"
        "9426178,NFO,NIFTY24DEC22000CE,NIFTY,NFO-OPT,CE,50,0.05,2024-12-26,22000\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=csv_text)

    creds = BrokerCredentials(api_key="key123", access_token="tok456")
    broker = ZerodhaBroker(creds, client=_mock_client(handler, ZerodhaBroker.BASE_URL))

    instruments = run(broker.get_instruments("NSE"))
    by_symbol = {i.tradingsymbol: i for i in instruments}
    assert by_symbol["RELIANCE"].strike is None
    assert by_symbol["NIFTY24DEC22000CE"].strike == 22000.0


def test_zerodha_get_instruments_is_cached_across_calls():
    fetch_count = 0
    csv_text = "instrument_token,exchange,tradingsymbol,name,segment,instrument_type,lot_size,tick_size,expiry,strike\n"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fetch_count
        fetch_count += 1
        return httpx.Response(200, text=csv_text)

    creds = BrokerCredentials(api_key="key123", access_token="tok456")
    broker = ZerodhaBroker(creds, client=_mock_client(handler, ZerodhaBroker.BASE_URL))

    run(broker.get_instruments("NSE"))
    run(broker.get_instruments("NSE"))
    assert fetch_count == 1


# --- Upstox --------------------------------------------------------------------

def test_upstox_get_profile_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tok789"
        return httpx.Response(200, json={
            "status": "success",
            "data": {"user_id": "UP123", "user_name": "Upstox User", "email": "u@example.com"},
        })

    creds = BrokerCredentials(api_key="clientid", access_token="tok789")
    broker = UpstoxBroker(creds, client=_mock_client(handler, UpstoxBroker.BASE_URL))

    profile = run(broker.get_profile())
    assert profile.broker == "upstox"
    assert profile.user_id == "UP123"


def test_upstox_place_order_resolves_tradingsymbol_to_instrument_key():
    """BrokerOrderRequest.symbol is a plain trading symbol (the same contract every adapter
    shares) - Upstox's own order API needs its "instrument_key" format instead, so place_order
    must resolve one from the other via the instrument master, not pass the plain symbol through
    as if it were already an instrument_key.
    """
    instrument_master = gzip.compress(json.dumps([
        {"instrument_key": "NSE_EQ|INE002A01018", "exchange": "NSE", "trading_symbol": "RELIANCE", "instrument_type": "EQ"},
    ]).encode())

    def handler(request: httpx.Request) -> httpx.Response:
        if "assets.upstox.com" in str(request.url):
            return httpx.Response(200, content=instrument_master)
        payload = json.loads(request.content)
        assert payload["instrument_token"] == "NSE_EQ|INE002A01018"
        assert payload["transaction_type"] == "SELL"
        return httpx.Response(200, json={"status": "success", "data": {"order_id": "UP-ORDER-1"}})

    creds = BrokerCredentials(api_key="clientid", access_token="tok789")
    broker = UpstoxBroker(creds, client=_mock_client(handler, UpstoxBroker.BASE_URL))
    order = BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.SELL, quantity=5)

    response = run(broker.place_order(order))
    assert response.order_id == "UP-ORDER-1"


def test_upstox_get_instruments_is_cached_across_calls():
    fetch_count = 0
    instrument_master = gzip.compress(json.dumps([
        {"instrument_key": "NSE_EQ|INE002A01018", "exchange": "NSE", "trading_symbol": "RELIANCE", "instrument_type": "EQ"},
    ]).encode())

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fetch_count
        fetch_count += 1
        return httpx.Response(200, content=instrument_master)

    creds = BrokerCredentials(api_key="clientid", access_token="tok789")
    broker = UpstoxBroker(creds, client=_mock_client(handler, UpstoxBroker.BASE_URL))

    run(broker.get_instruments("NSE"))
    run(broker.get_instruments("NSE"))
    assert fetch_count == 1


# --- Shoonya --------------------------------------------------------------------

def test_shoonya_authenticate_sends_hashed_credentials_and_parses_session():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/QuickAuth")
        form = parse_qs(request.content.decode())
        payload = json.loads(form["jData"][0])
        assert payload["uid"] == "FA12345"
        assert payload["pwd"] == hashlib.sha256(b"mypassword").hexdigest()
        assert payload["appkey"] == hashlib.sha256(b"FA12345|apikey123").hexdigest()
        assert payload["factor2"] == "123456"
        return httpx.Response(200, json={
            "stat": "Ok", "susertoken": "sess-token-1", "actid": "FA12345", "uname": "Test User",
        })

    creds = BrokerCredentials(client_id="FA12345", api_secret="mypassword", api_key="apikey123", totp_secret="123456")
    broker = ShoonyaBroker(creds, client=_mock_client(handler, ShoonyaBroker.BASE_URL))

    profile = run(broker.authenticate())
    assert profile.broker == "shoonya"
    assert profile.user_id == "FA12345"
    assert profile.name == "Test User"


def test_shoonya_get_profile_requires_prior_authentication():
    creds = BrokerCredentials(client_id="FA12345", api_secret="mypassword", api_key="apikey123")
    broker = ShoonyaBroker(creds, client=httpx.AsyncClient())
    with pytest.raises(BrokerAuthenticationError):
        run(broker.get_profile())


def test_shoonya_place_order_sends_jkey_and_parses_order_id():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/QuickAuth"):
            return httpx.Response(200, json={"stat": "Ok", "susertoken": "sess-token-1", "actid": "FA12345"})
        form = parse_qs(request.content.decode())
        captured["jKey"] = form["jKey"][0]
        payload = json.loads(form["jData"][0])
        captured["payload"] = payload
        return httpx.Response(200, json={"stat": "Ok", "norenordno": "23091400001234"})

    creds = BrokerCredentials(client_id="FA12345", api_secret="mypassword", api_key="apikey123", totp_secret="123456")
    broker = ShoonyaBroker(creds, client=_mock_client(handler, ShoonyaBroker.BASE_URL))
    run(broker.authenticate())

    order = BrokerOrderRequest(symbol="RELIANCE-EQ", exchange="NSE", transaction_type=OrderSide.BUY, quantity=10, product="MIS")
    response = run(broker.place_order(order))

    assert response.order_id == "23091400001234"
    assert captured["jKey"] == "sess-token-1"
    assert captured["payload"]["trantype"] == "B"
    assert captured["payload"]["prd"] == "I"


def test_shoonya_raises_broker_api_error_on_not_ok_response():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/QuickAuth"):
            return httpx.Response(200, json={"stat": "Ok", "susertoken": "sess-token-1", "actid": "FA12345"})
        return httpx.Response(200, json={"stat": "Not_Ok", "emsg": "Session Expired"})

    creds = BrokerCredentials(client_id="FA12345", api_secret="mypassword", api_key="apikey123", totp_secret="123456")
    broker = ShoonyaBroker(creds, client=_mock_client(handler, ShoonyaBroker.BASE_URL))
    run(broker.authenticate())

    with pytest.raises(BrokerAPIError):
        run(broker.get_margins())


def test_shoonya_get_option_chain_merges_ce_pe_by_strike_with_live_quotes():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/QuickAuth"):
            return httpx.Response(200, json={"stat": "Ok", "susertoken": "sess-token-1", "actid": "FA12345"})
        if request.url.path.endswith("/GetOptionChain"):
            return httpx.Response(200, json={"stat": "Ok", "values": [
                {"tsym": "BANKNIFTY24DEC50000CE", "token": "111", "strprc": "50000", "optt": "CE", "exch": "NFO"},
                {"tsym": "BANKNIFTY24DEC50000PE", "token": "112", "strprc": "50000", "optt": "PE", "exch": "NFO"},
            ]})
        if request.url.path.endswith("/GetQuotes"):
            form = parse_qs(request.content.decode())
            payload = json.loads(form["jData"][0])
            if payload["token"] == "111":
                return httpx.Response(200, json={"lp": "250.5", "oi": "123000", "v": "5000", "bp1": "249", "sp1": "251"})
            return httpx.Response(200, json={"lp": "180.0", "oi": "98000", "v": "4200", "bp1": "179", "sp1": "181"})
        raise AssertionError(f"Unexpected path {request.url.path}")

    creds = BrokerCredentials(client_id="FA12345", api_secret="mypassword", api_key="apikey123", totp_secret="123456")
    broker = ShoonyaBroker(creds, client=_mock_client(handler, ShoonyaBroker.BASE_URL))
    run(broker.authenticate())

    chain = run(broker.get_option_chain("BANKNIFTY"))

    assert len(chain.rows) == 1
    row = chain.rows[0]
    assert row.strike == 50000.0
    assert row.call_ltp == 250.5 and row.call_oi == 123000.0 and row.call_volume == 5000.0
    assert row.put_ltp == 180.0 and row.put_oi == 98000.0 and row.put_volume == 4200.0


def test_shoonya_requires_core_credentials():
    with pytest.raises(BrokerAuthenticationError):
        ShoonyaBroker(BrokerCredentials(client_id="FA12345"))


# --- Registry --------------------------------------------------------------------

def test_registry_lists_all_seven_brokers():
    # Part 1B added Kotak Neo (eight adapters; LIVE orders and live prices gated: app/brokers/gates.py).
    assert set(available_brokers()) == {"zerodha", "upstox", "shoonya", "angel_one", "fyers", "dhan", "coindcx", "kotak_neo"}


def test_registry_returns_correct_adapter_type():
    zerodha = get_broker_adapter("zerodha", BrokerCredentials(api_key="k", access_token="t"))
    assert isinstance(zerodha, ZerodhaBroker)

    angel = get_broker_adapter("angel_one", BrokerCredentials(api_key="k", client_id="A1", pin="1234", totp_secret="123456"))
    assert isinstance(angel, AngelOneBroker)          # Phase AC: a real adapter, no longer a stub


def test_registry_raises_for_unknown_broker():
    with pytest.raises(ValueError):
        get_broker_adapter("not_a_real_broker", BrokerCredentials())


# --- Stub template -------------------------------------------------------------------------

def test_stub_template_implements_interface_but_raises_until_wired():
    class _Next(_StubBrokerAdapter):
        name = "nextvenue"
    broker = _Next(BrokerCredentials(api_key="k"))
    assert isinstance(broker, BrokerInterface)
    with pytest.raises(NotImplementedError):
        run(broker.get_profile())
    assert isinstance(get_broker_adapter("coindcx", BrokerCredentials(api_key="k", api_secret="s")), CoinDCXBroker)


def test_upstox_get_ltp_for_symbol_resolves_instrument_key_and_reads_by_token():
    """Upstox wants an instrument_key in the request but keys its LTP *response* by
    "NSE_EQ:RELIANCE", so the plain-symbol helper must match on the entry's instrument_token
    rather than assume the response is keyed by what was asked for."""
    instrument_master = gzip.compress(json.dumps([
        {"instrument_key": "NSE_EQ|INE002A01018", "exchange": "NSE", "trading_symbol": "RELIANCE", "instrument_type": "EQ"},
    ]).encode())

    def handler(request: httpx.Request) -> httpx.Response:
        if "assets.upstox.com" in str(request.url):
            return httpx.Response(200, content=instrument_master)
        assert request.url.path == "/v2/market-quote/ltp"
        assert parse_qs(request.url.query.decode())["instrument_key"] == ["NSE_EQ|INE002A01018"]
        return httpx.Response(200, json={"status": "success", "data": {
            "NSE_EQ:RELIANCE": {"last_price": 2501.25, "instrument_token": "NSE_EQ|INE002A01018"},
        }})

    creds = BrokerCredentials(api_key="clientid", access_token="tok789")
    broker = UpstoxBroker(creds, client=_mock_client(handler, UpstoxBroker.BASE_URL))
    assert run(broker.get_ltp_for_symbol("RELIANCE", "NSE")) == 2501.25


def test_upstox_intraday_candles_use_intraday_endpoint_and_sort_ascending():
    instrument_master = gzip.compress(json.dumps([
        {"instrument_key": "NSE_EQ|INE002A01018", "exchange": "NSE", "trading_symbol": "RELIANCE", "instrument_type": "EQ"},
    ]).encode())

    def handler(request: httpx.Request) -> httpx.Response:
        if "assets.upstox.com" in str(request.url):
            return httpx.Response(200, content=instrument_master)
        assert request.url.path == "/v2/historical-candle/intraday/NSE_EQ|INE002A01018/1minute"
        return httpx.Response(200, json={"status": "success", "data": {"candles": [
            ["2026-09-25T09:16:00+05:30", 101, 102, 100, 101.5, 20, 0],
            ["2026-09-25T09:15:00+05:30", 100, 101, 99, 100.5, 10, 0],
        ]}})

    creds = BrokerCredentials(api_key="clientid", access_token="tok789")
    broker = UpstoxBroker(creds, client=_mock_client(handler, UpstoxBroker.BASE_URL))
    bars = run(broker.get_intraday_candles("RELIANCE", "NSE", "1min"))
    assert [b.open for b in bars] == [100, 101]
    assert bars[0].timestamp.isoformat() == "2026-09-25T09:15:00+05:30"


def test_place_stop_loss_order_default_is_slm_on_given_side():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"status": "success", "data": {"order_id": "SL-1"}})

    creds = BrokerCredentials(api_key="key123", access_token="tok456")
    broker = ZerodhaBroker(creds, client=_mock_client(handler, ZerodhaBroker.BASE_URL))
    response = run(broker.place_stop_loss_order("RELIANCE", "NSE", OrderSide.SELL, 10, trigger_price=2450.0, tag="sl"))

    assert response.order_id == "SL-1"
    assert captured["order_type"] == ["SL-M"]
    assert captured["transaction_type"] == ["SELL"]
    assert captured["trigger_price"] == ["2450.0"]
    assert captured["quantity"] == ["10.0"]


def test_shoonya_maps_slm_to_noren_sl_mkt_with_trigger():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/QuickAuth"):
            return httpx.Response(200, json={"stat": "Ok", "susertoken": "sess", "actid": "FA1"})
        captured.update(json.loads(parse_qs(request.content.decode())["jData"][0]))
        return httpx.Response(200, json={"stat": "Ok", "norenordno": "N1"})

    creds = BrokerCredentials(client_id="FA1", api_secret="pw", api_key="ak", totp_secret="123456")
    broker = ShoonyaBroker(creds, client=_mock_client(handler, ShoonyaBroker.BASE_URL))
    run(broker.authenticate())
    run(broker.place_stop_loss_order("RELIANCE-EQ", "NSE", OrderSide.SELL, 5, trigger_price=99.5))

    assert captured["prctyp"] == "SL-MKT"
    assert captured["trgprc"] == "99.5"
    assert captured["trantype"] == "S"


def test_upstox_find_instrument_resolves_index_spellings():
    """Upstox lists indices under NSE_INDEX with its own trading symbol ("NIFTY", "BANKNIFTY") and
    display name ("Nifty 50"); the platform and Kite say "NIFTY 50" / "NIFTY BANK". Live charts
    failed with 'Instrument NSE:NIFTY 50 not found' until the lookup understood both."""
    from app.brokers.models import Instrument
    from app.brokers.upstox import find_instrument

    rows = [
        Instrument(instrument_token="NSE_INDEX|Nifty 50", exchange="NSE", tradingsymbol="NIFTY", name="Nifty 50", segment="NSE_INDEX", instrument_type="INDEX"),
        Instrument(instrument_token="NSE_INDEX|Nifty Bank", exchange="NSE", tradingsymbol="BANKNIFTY", name="Nifty Bank", segment="NSE_INDEX", instrument_type="INDEX"),
        Instrument(instrument_token="NSE_FO|1", exchange="NSE", tradingsymbol="NIFTY FUT 30 OCT 26", name="NIFTY", segment="NSE_FO", instrument_type="FUT", expiry="2026-10-30"),
        Instrument(instrument_token="NSE_EQ|INE002A01018", exchange="NSE", tradingsymbol="RELIANCE", name="RELIANCE INDUSTRIES LTD", segment="NSE_EQ", instrument_type="EQ"),
    ]
    key = lambda s: (find_instrument(rows, s) or Instrument(instrument_token="-", exchange="", tradingsymbol="")).instrument_token
    assert key("NIFTY 50") == "NSE_INDEX|Nifty 50"
    assert key("Nifty 50") == "NSE_INDEX|Nifty 50"
    assert key("NIFTY BANK") == "NSE_INDEX|Nifty Bank"
    assert key("BANKNIFTY") == "NSE_INDEX|Nifty Bank"
    assert key("NIFTY") == "NSE_INDEX|Nifty 50"
    assert key("reliance") == "NSE_EQ|INE002A01018"
    assert key("RELIANCE") == "NSE_EQ|INE002A01018"
    assert find_instrument(rows, "NIFTY IT") is None

    # A master whose index rows carry Kite-style symbols keeps resolving exactly.
    kite_style = [Instrument(instrument_token="NSE_INDEX|Nifty 50", exchange="NSE", tradingsymbol="NIFTY 50", name="Nifty 50", segment="NSE_INDEX", instrument_type="INDEX")]
    assert find_instrument(kite_style, "NIFTY 50").instrument_token == "NSE_INDEX|Nifty 50"


def _upstox_chain_entry(strike, spot):
    side = {"market_data": {"ltp": 10.0, "oi": 100.0, "prev_oi": 90.0, "volume": 5.0}, "option_greeks": {"iv": 12.0, "delta": 0.5}}
    return {"strike_price": strike, "underlying_spot_price": spot, "expiry": "", "call_options": side, "put_options": side}


def test_upstox_option_chain_defaults_to_nearest_expiry():
    """Upstox rejects /option/chain without expiry_date ('Required request parameter
    expiry_date ... is not present'); with no expiry asked for, the adapter sends the nearest
    upcoming one from the master's option rows (underlying_symbol = NIFTY)."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    past, near, far = today - timedelta(days=7), today + timedelta(days=3), today + timedelta(days=10)
    # Upstox stamps an expiry at 23:59:59 IST of the expiry day.
    ms = lambda d: int(datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp() * 1000)
    master = gzip.compress(json.dumps([
        {"instrument_key": "NSE_INDEX|Nifty 50", "exchange": "NSE", "segment": "NSE_INDEX", "trading_symbol": "NIFTY", "name": "Nifty 50", "instrument_type": "INDEX"},
        *[{"instrument_key": f"NSE_FO|{i}", "exchange": "NSE", "segment": "NSE_FO", "trading_symbol": f"NIFTY {i}", "underlying_symbol": "NIFTY",
           "instrument_type": "CE", "expiry": ms(d), "strike_price": 24000} for i, d in enumerate((far, past, near))],
    ]).encode())
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "assets.upstox.com" in str(request.url):
            return httpx.Response(200, content=master)
        assert request.url.path.endswith("/option/chain")
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"status": "success", "data": [_upstox_chain_entry(24000, 24010.5)]})

    broker = UpstoxBroker(BrokerCredentials(api_key="k", access_token="t"), client=_mock_client(handler, UpstoxBroker.BASE_URL))
    chain = run(broker.get_option_chain("NIFTY"))
    assert seen == {"instrument_key": "NSE_INDEX|Nifty 50", "expiry_date": near.isoformat()}
    assert chain.expiry == near.isoformat() and chain.underlying_ltp == 24010.5 and len(chain.rows) == 1


def test_upstox_option_chain_falls_back_to_contract_listing_for_expiry():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    near = datetime.now(ZoneInfo("Asia/Kolkata")).date() + timedelta(days=2)
    master = gzip.compress(json.dumps([
        {"instrument_key": "NSE_INDEX|Nifty Bank", "exchange": "NSE", "segment": "NSE_INDEX", "trading_symbol": "BANKNIFTY", "name": "Nifty Bank", "instrument_type": "INDEX"},
    ]).encode())
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "assets.upstox.com" in str(request.url):
            return httpx.Response(200, content=master)
        if request.url.path.endswith("/option/contract"):
            assert request.url.params["instrument_key"] == "NSE_INDEX|Nifty Bank"
            return httpx.Response(200, json={"status": "success", "data": [{"expiry": near.isoformat()}, {"expiry": (near + timedelta(days=7)).isoformat()}]})
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"status": "success", "data": [_upstox_chain_entry(52000, 52010.0)]})

    broker = UpstoxBroker(BrokerCredentials(api_key="k", access_token="t"), client=_mock_client(handler, UpstoxBroker.BASE_URL))
    run(broker.get_option_chain("NIFTY BANK"))
    assert seen == {"instrument_key": "NSE_INDEX|Nifty Bank", "expiry_date": near.isoformat()}


def _shoonya_series(eod_rows, seen):
    """A Shoonya mock: login, the scrip search for RELIANCE-EQ, and /EODChartData answering `eod_rows` (a /TPSeries call fails
    the test - daily bars must never come from the minute series)."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/QuickAuth"):
            return httpx.Response(200, json={"stat": "Ok", "susertoken": "sess-token-1", "actid": "FA12345"})
        payload = json.loads(parse_qs(request.content.decode())["jData"][0])
        seen.append((path.rsplit("/", 1)[-1], payload))
        if path.endswith("/SearchScrip"):
            return httpx.Response(200, json={"stat": "Ok", "values": [{"exch": "NSE", "token": "2885", "tsym": "RELIANCE-EQ"}]})
        if path.endswith("/EODChartData"):
            return httpx.Response(200, json=eod_rows)
        raise AssertionError(f"Unexpected path {path}")
    creds = BrokerCredentials(client_id="FA12345", api_secret="mypassword", api_key="apikey123", totp_secret="123456")
    broker = ShoonyaBroker(creds, client=_mock_client(handler, ShoonyaBroker.BASE_URL))
    run(broker.authenticate())
    return broker


def test_shoonya_day_reads_daily_bars_from_eodchart_stamped_at_the_ist_date():
    """UI2-4a2-pre: "day" used to fall back to 1-minute TPSeries bars. It now calls /EODChartData (NorenRestApiPy
    get_daily_price_series; sym "EXCH:TSYM" with Noren's trading symbol, from/to in epoch seconds covering the whole IST
    dates) and returns one bar per trading date at 00:00 IST, oldest first. Rows come as JSON strings (the broker's
    form) or objects; `ssboe` alone still gives the right IST date."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    rows = [  # newest first, as the broker sends them
        json.dumps({"time": "23-SEP-2022", "into": "2400", "inth": "2420", "intl": "2390", "intc": "2410", "intv": "500", "ssboe": "1663891200"}),
        {"time": "22-Sep-2022", "into": "2380", "inth": "2405", "intl": "2370", "intc": "2400", "intv": "400"},
        # no `time`: 21 Sep 2022 00:00 IST in epoch seconds (= 20 Sep 18:30 UTC) - a UTC reading would say the 20th
        json.dumps({"into": "2350", "inth": "2385", "intl": "2340", "intc": "2380", "intv": "300",
                    "ssboe": str(int(datetime(2022, 9, 21, tzinfo=ist).timestamp()))}),
    ]
    seen = []
    broker = _shoonya_series(rows, seen)
    # The service's bounds: aware IST datetimes in the middle of the day (now - lookback .. now - 1 day).
    start = datetime(2022, 9, 21, 15, 0, tzinfo=ist)
    end = datetime(2022, 9, 23, 2, 0, tzinfo=timezone.utc)      # 07:30 IST on the 23rd
    # The platform's plain symbol: Noren names it RELIANCE-EQ, found by /SearchScrip (review: "NSE:RELIANCE" answers
    # "no data" - an empty chart).
    bars = run(broker.get_historical_data("RELIANCE", "NSE", "day", start, end))

    eod = [p for name, p in seen if name == "EODChartData"]
    assert len(eod) == 1 and not [n for n, _ in seen if n == "TPSeries"]
    assert [p["stext"] for name, p in seen if name == "SearchScrip"] == ["RELIANCE"]
    assert eod[0]["sym"] == "NSE:RELIANCE-EQ" and eod[0]["uid"] == "FA12345"
    assert int(eod[0]["from"]) == int(datetime(2022, 9, 21, tzinfo=ist).timestamp())
    assert datetime.fromtimestamp(int(eod[0]["to"]), tz=ist).date().isoformat() == "2022-09-23"
    assert [b.timestamp for b in bars] == [datetime(2022, 9, d, tzinfo=ist) for d in (21, 22, 23)]
    assert [b.timestamp.utcoffset().total_seconds() for b in bars] == [19800.0] * 3
    assert [(b.open, b.high, b.low, b.close, b.volume) for b in bars] == [
        (2350.0, 2385.0, 2340.0, 2380.0, 300.0), (2380.0, 2405.0, 2370.0, 2400.0, 400.0), (2400.0, 2420.0, 2390.0, 2410.0, 500.0)]


def test_shoonya_day_with_no_sessions_is_no_bars_and_other_errors_still_raise():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    start, end = datetime(2022, 10, 24, tzinfo=ist), datetime(2022, 10, 24, 12, tzinfo=ist)
    broker = _shoonya_series({"stat": "Not_Ok", "emsg": 'Error Occurred : 5 "no data"'}, [])
    assert run(broker.get_historical_data("RELIANCE-EQ", "NSE", "day", start, end)) == []
    broker = _shoonya_series({"stat": "Not_Ok", "emsg": "Session Expired"}, [])
    with pytest.raises(BrokerAPIError, match="Session Expired"):
        run(broker.get_historical_data("RELIANCE-EQ", "NSE", "day", start, end))


def test_shoonya_book_calls_accept_the_json_list_they_answer_on_success():
    """UI2-4a2-pre review: Noren answers OrderBook / TradeBook / PositionBook / Holdings with a JSON list on success; `_post`
    called `.get("stat")` on it and raised, so the reconciliation path failed on any non-empty book. A list now passes
    through; a Not_Ok dict still raises."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/QuickAuth"):
            return httpx.Response(200, json={"stat": "Ok", "susertoken": "sess-token-1", "actid": "FA12345"})
        if path.endswith("/OrderBook"):
            return httpx.Response(200, json=[{"stat": "Ok", "norenordno": "24101500001", "tsym": "RELIANCE-EQ", "trantype": "B",
                                              "qty": "5", "fillshares": "5", "prctyp": "MKT", "status": "COMPLETE", "avgprc": "2900.5",
                                              "norentm": "10:15:00 15-10-2024", "exch": "NSE", "prd": "I"}])
        if path.endswith("/PositionBook"):
            return httpx.Response(200, json=[{"stat": "Ok", "tsym": "RELIANCE-EQ", "exch": "NSE", "prd": "I", "netqty": "5",
                                              "netavgprc": "2900.5", "lp": "2910", "rpnl": "0", "urmtom": "47.5"}])
        return httpx.Response(200, json={"stat": "Not_Ok", "emsg": "no data"})

    creds = BrokerCredentials(client_id="FA12345", api_secret="mypassword", api_key="apikey123", totp_secret="123456")
    broker = ShoonyaBroker(creds, client=_mock_client(handler, ShoonyaBroker.BASE_URL))
    run(broker.authenticate())
    orders = run(broker.get_order_book())
    assert [(o.order_id, o.symbol, o.filled_quantity, o.average_price) for o in orders] == [("24101500001", "RELIANCE-EQ", 5, 2900.5)]
    positions = run(broker.get_positions())
    assert positions[0].quantity == 5 and positions[0].pnl == 47.5
    with pytest.raises(BrokerAPIError, match="no data"):
        run(broker.get_trade_book())


def test_shoonya_symbol_lookup_takes_a_budget_token_and_is_made_once():
    """UI2-4a2-pre review: /SearchScrip is a broker call - with a budget hook set it takes a token first; no token, no
    call (BrokerBudgetUsed). The (token, trading symbol) answer is cached, so a second use asks neither."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.brokers.exceptions import BrokerBudgetUsed
    ist = ZoneInfo("Asia/Kolkata")
    seen = []
    broker = _shoonya_series([], seen)
    asked = []

    async def refuse():
        asked.append("no")
        return False
    broker.budget_hook = refuse
    with pytest.raises(BrokerBudgetUsed):
        run(broker._lookup("RELIANCE", "NSE"))
    assert asked == ["no"] and seen == []

    async def grant():
        asked.append("yes")
        return True
    broker.budget_hook = grant
    assert run(broker._lookup("reliance", "NSE")) == ("2885", "RELIANCE-EQ")
    run(broker.get_historical_data("RELIANCE", "NSE", "day", datetime(2022, 9, 21, tzinfo=ist), datetime(2022, 9, 22, tzinfo=ist)))
    assert asked == ["no", "yes"] and [n for n, _ in seen].count("SearchScrip") == 1
