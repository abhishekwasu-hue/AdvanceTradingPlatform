import asyncio
import json
from typing import Callable

import httpx
import pytest

from app.brokers.base import BrokerInterface
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.models import BrokerCredentials, BrokerOrderRequest
from app.brokers.registry import available_brokers, get_broker_adapter
from app.brokers.stubs import AngelOneBroker, DhanBroker, FyersBroker
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


def test_upstox_place_order_parses_order_id():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["transaction_type"] == "SELL"
        return httpx.Response(200, json={"status": "success", "data": {"order_id": "UP-ORDER-1"}})

    creds = BrokerCredentials(api_key="clientid", access_token="tok789")
    broker = UpstoxBroker(creds, client=_mock_client(handler, UpstoxBroker.BASE_URL))
    order = BrokerOrderRequest(symbol="NSE_EQ|INE002A01018", transaction_type=OrderSide.SELL, quantity=5)

    response = run(broker.place_order(order))
    assert response.order_id == "UP-ORDER-1"


# --- Registry --------------------------------------------------------------------

def test_registry_lists_all_five_brokers():
    assert set(available_brokers()) == {"zerodha", "upstox", "angel_one", "fyers", "dhan"}


def test_registry_returns_correct_adapter_type():
    zerodha = get_broker_adapter("zerodha", BrokerCredentials(api_key="k", access_token="t"))
    assert isinstance(zerodha, ZerodhaBroker)

    angel = get_broker_adapter("angel_one", BrokerCredentials(api_key="k"))
    assert isinstance(angel, AngelOneBroker)


def test_registry_raises_for_unknown_broker():
    with pytest.raises(ValueError):
        get_broker_adapter("not_a_real_broker", BrokerCredentials())


# --- Stub adapters (Angel One / Fyers / Dhan) --------------------------------------

@pytest.mark.parametrize("cls", [AngelOneBroker, FyersBroker, DhanBroker])
def test_stub_brokers_implement_interface_but_raise_until_wired(cls):
    broker = cls(BrokerCredentials(api_key="k"))
    assert isinstance(broker, BrokerInterface)
    with pytest.raises(NotImplementedError):
        run(broker.get_profile())
