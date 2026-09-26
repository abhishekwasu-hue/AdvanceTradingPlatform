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
from app.brokers.stubs import AngelOneBroker, CoinDCXBroker, DhanBroker, FyersBroker
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
    assert set(available_brokers()) == {"zerodha", "upstox", "shoonya", "angel_one", "fyers", "dhan", "coindcx"}


def test_registry_returns_correct_adapter_type():
    zerodha = get_broker_adapter("zerodha", BrokerCredentials(api_key="k", access_token="t"))
    assert isinstance(zerodha, ZerodhaBroker)

    angel = get_broker_adapter("angel_one", BrokerCredentials(api_key="k"))
    assert isinstance(angel, AngelOneBroker)


def test_registry_raises_for_unknown_broker():
    with pytest.raises(ValueError):
        get_broker_adapter("not_a_real_broker", BrokerCredentials())


# --- Stub adapters (Angel One / Fyers / Dhan / CoinDCX) -----------------------------

@pytest.mark.parametrize("cls", [AngelOneBroker, FyersBroker, DhanBroker, CoinDCXBroker])
def test_stub_brokers_implement_interface_but_raise_until_wired(cls):
    broker = cls(BrokerCredentials(api_key="k"))
    assert isinstance(broker, BrokerInterface)
    with pytest.raises(NotImplementedError):
        run(broker.get_profile())


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
