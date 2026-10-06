from tests.test_auth_api import client

from tests.test_auth_api import _register

_HEADERS = None


def _h():
    """A logged-in caller: P0.1 / S3 closed the CPU-heavy endpoints to anonymous callers."""
    global _HEADERS
    if _HEADERS is None:
        _HEADERS = {"Authorization": f"Bearer {_register('p0-greeks@example.com')}"}
    return _HEADERS


def _leg(strike=100.0, option_type="CALL", quantity=50, iv=0.2):
    return {
        "strike": strike, "option_type": option_type, "quantity": quantity, "underlying_ltp": 100.0,
        "expiry": "2099-01-01", "implied_volatility": iv, "as_of": "2098-12-01",
    }


def test_greeks_endpoint_computes_single_leg():
    response = client.post("/api/option-chain/greeks", headers=_h(), json={"legs": [_leg()]})
    assert response.status_code == 200
    body = response.json()
    assert len(body["legs"]) == 1
    assert body["legs"][0]["greeks"]["implied_volatility"] == 0.2
    assert body["net_delta"] == body["legs"][0]["position_delta"]


def test_greeks_endpoint_nets_a_long_and_short_leg():
    response = client.post("/api/option-chain/greeks", headers=_h(), json={"legs": [_leg(quantity=50), _leg(quantity=-50)]})
    assert response.status_code == 200
    body = response.json()
    assert abs(body["net_delta"]) < 1e-6
    assert abs(body["net_gamma"]) < 1e-6


def test_greeks_endpoint_rejects_leg_with_neither_price_nor_iv():
    leg = _leg()
    del leg["implied_volatility"]
    response = client.post("/api/option-chain/greeks", headers=_h(), json={"legs": [leg]})
    assert response.status_code == 422


def test_greeks_endpoint_rejects_unsolvable_option_ltp():
    leg = _leg()
    del leg["implied_volatility"]
    leg["strike"] = 50.0
    leg["option_ltp"] = 10.0  # below intrinsic value (100 - 50 = 50) for this call
    response = client.post("/api/option-chain/greeks", headers=_h(), json={"legs": [leg]})
    assert response.status_code == 422
