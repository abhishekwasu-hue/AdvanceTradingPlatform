"""D2: the Upstox Redirect URL on the production domain.

- one helper derives the callback URL: PUBLIC_BASE_URL + the callback path when set, else the request's own address;
- GET /api/broker/upstox/redirect-url shows it to a signed-in member, with a mismatch flag (booleans only) for each stored
  Upstox account whose Redirect URI differs - the stored value is never echoed and never rewritten;
- the OAuth start keeps sending a stored Redirect URI (it must match the Upstox app).
"""
from urllib.parse import parse_qs, urlparse

import pytest

from app.brokers import token_lifecycle as tl
from app.brokers.models import BrokerProfile
from app.core import config as app_config
from tests.test_auth_api import _register, client

PATH = "/api/broker/upstox/oauth/callback"
PUBLIC = "https://atp.example.com"
OLD = "http://192.0.2.10:8000" + PATH          # a documentation-range address standing in for an old IP-based one


def _auth(email: str) -> dict:
    return {"Authorization": f"Bearer {_register(email)}"}


def _store(headers: dict, label: str = "primary", **fields: str) -> None:
    body = {"api_key": "client-d2", "api_secret": "secret-d2", **fields}
    response = client.post(f"/api/broker/upstox/credentials?account_label={label}", headers=headers, json=body)
    assert response.status_code == 204, response.text


def _start_redirect(headers: dict, label: str = "primary") -> str:
    response = client.get(f"/api/broker/upstox/oauth/start?account_label={label}", headers=headers)
    assert response.status_code == 200, response.text
    return parse_qs(urlparse(response.json()["authorization_url"]).query)["redirect_uri"][0]


@pytest.fixture
def public_base(monkeypatch):
    monkeypatch.setattr(app_config, "PUBLIC_BASE_URL", PUBLIC + "/")     # a trailing slash is ignored
    return PUBLIC + PATH


def test_without_public_base_url_the_request_address_is_used(monkeypatch):
    monkeypatch.setattr(app_config, "PUBLIC_BASE_URL", "")
    headers = _auth("d2-nobase@example.com")
    body = client.get("/api/broker/upstox/redirect-url", headers=headers).json()
    assert body == {"redirect_url": "http://testserver" + PATH, "redirect_uri_mismatch": False, "accounts": []}
    _store(headers)
    assert _start_redirect(headers) == "http://testserver" + PATH


def test_with_public_base_url_every_derivation_uses_it(public_base):
    headers = _auth("d2-base@example.com")
    body = client.get("/api/broker/upstox/redirect-url", headers=headers).json()
    assert body == {"redirect_url": public_base, "redirect_uri_mismatch": False, "accounts": []}
    _store(headers)
    status = {r["broker_name"]: r for r in client.get("/api/broker/token-status", headers=headers).json()}["upstox"]
    assert status["oauth_callback_url"] == public_base
    # No stored Redirect URI: the start sends the public one and remembers it, so the exchange sends the same.
    assert _start_redirect(headers) == public_base
    body = client.get("/api/broker/upstox/redirect-url", headers=headers).json()
    assert body["accounts"] == [{"account_label": "primary", "redirect_uri_set": True, "redirect_uri_mismatch": False}]
    assert body["redirect_uri_mismatch"] is False


def test_a_stored_redirect_uri_that_differs_is_flagged_not_rewritten(public_base):
    headers = _auth("d2-mismatch@example.com")
    _store(headers, redirect_uri=OLD)
    _store(headers, label="second", redirect_uri=public_base)
    response = client.get("/api/broker/upstox/redirect-url", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["redirect_url"] == public_base
    assert body["redirect_uri_mismatch"] is True
    assert body["accounts"] == [
        {"account_label": "primary", "redirect_uri_set": True, "redirect_uri_mismatch": True},
        {"account_label": "second", "redirect_uri_set": True, "redirect_uri_mismatch": False},
    ]
    # Booleans and the public URL only: no stored value, no other credential field.
    for secret in (OLD, "192.0.2.10", "client-d2", "secret-d2"):
        assert secret not in response.text
    # The OAuth start keeps today's behaviour: the stored URI (it must match the Upstox app), and it stays stored.
    assert _start_redirect(headers) == OLD
    assert client.get("/api/broker/upstox/redirect-url", headers=headers).json()["redirect_uri_mismatch"] is True
    # Re-saving the public URL clears the flag.
    _store(headers, redirect_uri=public_base)
    assert client.get("/api/broker/upstox/redirect-url", headers=headers).json()["redirect_uri_mismatch"] is False
    assert _start_redirect(headers) == public_base


def test_the_callback_exchange_uses_the_public_url_when_nothing_is_stored(public_base, monkeypatch):
    headers = _auth("d2-callback@example.com")
    _store(headers)
    me = client.get("/api/auth/me", headers=headers).json()
    state = tl.create_oauth_state(me["tenant_id"], me["id"], "upstox")
    seen = {}

    class _Exchanging:
        def __init__(self, creds):
            seen["redirect_uri"] = creds.redirect_uri

        async def authenticate(self):
            return BrokerProfile(broker="upstox", user_id="U1")

        @property
        def access_token(self):
            return "d2-token"

    monkeypatch.setattr("app.brokers.routes.get_broker_adapter", lambda name, creds: _Exchanging(creds))
    response = client.get(PATH, params={"code": "c", "state": state}, follow_redirects=False)
    assert response.status_code == 302
    assert seen["redirect_uri"] == public_base


def test_redirect_url_needs_a_login_and_shows_only_the_callers_organisation(public_base):
    assert client.get("/api/broker/upstox/redirect-url").status_code in (401, 403)
    owner_a = _auth("d2-tenant-a@example.com")
    _store(owner_a, redirect_uri=OLD)
    assert client.get("/api/broker/upstox/redirect-url", headers=owner_a).json()["redirect_uri_mismatch"] is True
    owner_b = _auth("d2-tenant-b@example.com")
    body = client.get("/api/broker/upstox/redirect-url", headers=owner_b).json()
    assert body == {"redirect_url": public_base, "redirect_uri_mismatch": False, "accounts": []}


def test_other_brokers_are_not_listed(public_base):
    headers = _auth("d2-other@example.com")
    client.post("/api/broker/fyers/credentials", headers=headers, json={"api_key": "k", "redirect_uri": OLD})
    assert client.get("/api/broker/upstox/redirect-url", headers=headers).json()["accounts"] == []
