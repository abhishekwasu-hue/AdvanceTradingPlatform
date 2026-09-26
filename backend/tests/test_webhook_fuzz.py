"""Master prompt Section 50: property-based/fuzz tests on the webhook schema. Section 48 says
webhook input must be treated as untrusted; `TradingViewAlertPayload`
(app/webhooks/routes.py) is the schema boundary that's supposed to enforce that. The property
that matters: **no malformed payload can ever reach an unhandled exception (a 500)** - every
input is either accepted (200, for genuinely valid alerts) or cleanly rejected by Pydantic
validation (422/401 for an unknown token), regardless of what garbage a hostile or buggy sender
puts in the body.
"""
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.test_auth_api import _register, client

_ARBITRARY_JSON_SCALAR = st.one_of(
    # NaN/Infinity excluded: httpx's own JSON encoder (the test client's, not the app's) refuses
    # to serialize them at all - a limitation of this test's HTTP client, not something that
    # exercises the app's own JSON parsing.
    st.none(), st.booleans(), st.integers(), st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=50), st.lists(st.text(max_size=10), max_size=5),
    st.dictionaries(st.text(max_size=10), st.text(max_size=10), max_size=3),
)


def _webhook_token() -> str:
    token = _register("webhook-fuzz@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/webhooks/tradingview/token", headers=headers)
    return response.json()["webhook_token"]


_TOKEN = None


def _get_or_create_token() -> str:
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = _webhook_token()
    return _TOKEN


@given(
    strategy_id=_ARBITRARY_JSON_SCALAR, symbol=_ARBITRARY_JSON_SCALAR, direction=_ARBITRARY_JSON_SCALAR,
    entry=_ARBITRARY_JSON_SCALAR, stop_loss=_ARBITRARY_JSON_SCALAR, target1=_ARBITRARY_JSON_SCALAR,
    target2=_ARBITRARY_JSON_SCALAR, alert_id=_ARBITRARY_JSON_SCALAR,
)
@settings(
    max_examples=150, deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_webhook_never_500s_on_arbitrary_field_types(
    strategy_id, symbol, direction, entry, stop_loss, target1, target2, alert_id,
):
    token = _get_or_create_token()
    payload = {
        "strategy_id": strategy_id, "symbol": symbol, "direction": direction, "entry": entry,
        "stop_loss": stop_loss, "target1": target1, "target2": target2, "alert_id": alert_id,
    }
    response = client.post(f"/api/webhooks/tradingview/{token}", json=payload)
    assert response.status_code in (200, 422), (
        f"Expected a clean 200 or 422, got {response.status_code}: {response.text}"
    )


@given(garbage=st.one_of(
    st.none(), st.integers(), st.text(max_size=200), st.lists(st.integers(), max_size=10),
    st.dictionaries(st.text(max_size=20), _ARBITRARY_JSON_SCALAR, max_size=8),
))
@settings(
    max_examples=100, deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_webhook_never_500s_on_a_completely_malformed_body(garbage):
    """Not even a dict at all, or a dict with none of the expected keys - the body a hostile or
    just-plain-broken alert integration might send.
    """
    token = _get_or_create_token()
    response = client.post(f"/api/webhooks/tradingview/{token}", json=garbage)
    assert response.status_code == 422, f"Expected 422, got {response.status_code}: {response.text}"


def test_webhook_rejects_missing_required_fields():
    token = _get_or_create_token()
    response = client.post(f"/api/webhooks/tradingview/{token}", json={})
    assert response.status_code == 422


def test_webhook_handles_entry_equal_to_stop_loss_without_dividing_by_zero():
    """The risk_reward calculation divides by (entry - stop_loss) - this is the one field
    combination fuzzing is unlikely to hit by chance but is exactly the kind of edge case a
    division guard needs an explicit test for.
    """
    token = _get_or_create_token()
    payload = {
        "strategy_id": "s", "symbol": "SYM", "direction": "LONG",
        "entry": 100.0, "stop_loss": 100.0, "target1": 110.0, "target2": None,
    }
    response = client.post(f"/api/webhooks/tradingview/{token}", json=payload)
    assert response.status_code == 200
