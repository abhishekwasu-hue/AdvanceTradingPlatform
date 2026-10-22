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
OTHER = "https://other.example.org" + PATH     # a stored URI on a domain name: kept, only flagged


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
    assert body == {"redirect_url": "http://testserver" + PATH, "redirect_uri_mismatch": False, "accounts": [],
                    "redirect_uri_will_be_replaced": False}
    _store(headers)
    assert _start_redirect(headers) == "http://testserver" + PATH


def test_with_public_base_url_every_derivation_uses_it(public_base):
    headers = _auth("d2-base@example.com")
    body = client.get("/api/broker/upstox/redirect-url", headers=headers).json()
    assert body == {"redirect_url": public_base, "redirect_uri_mismatch": False, "accounts": [], "redirect_uri_will_be_replaced": False}
    _store(headers)
    status = {r["broker_name"]: r for r in client.get("/api/broker/token-status", headers=headers).json()}["upstox"]
    assert status["oauth_callback_url"] == public_base
    # No stored Redirect URI: the start sends the public one and remembers it, so the exchange sends the same.
    assert _start_redirect(headers) == public_base
    body = client.get("/api/broker/upstox/redirect-url", headers=headers).json()
    assert body["accounts"] == [{"account_label": "primary", "redirect_uri_set": True, "redirect_uri_mismatch": False,
                                 "redirect_uri_will_be_replaced": False}]
    assert body["redirect_uri_mismatch"] is False


def test_a_stored_redirect_uri_on_a_domain_that_differs_is_flagged_not_rewritten(public_base):
    headers = _auth("d2-mismatch@example.com")
    _store(headers, redirect_uri=OTHER)
    _store(headers, label="second", redirect_uri=public_base)
    response = client.get("/api/broker/upstox/redirect-url", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["redirect_url"] == public_base
    assert body["redirect_uri_mismatch"] is True
    assert body["accounts"] == [
        {"account_label": "primary", "redirect_uri_set": True, "redirect_uri_mismatch": True, "redirect_uri_will_be_replaced": False},
        {"account_label": "second", "redirect_uri_set": True, "redirect_uri_mismatch": False, "redirect_uri_will_be_replaced": False},
    ]
    # Booleans and the public URL only: no stored value, no other credential field.
    for secret in (OTHER, "other.example.org", "client-d2", "secret-d2"):
        assert secret not in response.text
    # The OAuth start keeps a stored URI on a domain name (it must match the Upstox app), and it stays stored.
    assert _start_redirect(headers) == OTHER
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
    _store(owner_a, redirect_uri=OTHER)
    _store(owner_a, label="old", redirect_uri=OLD)
    body_a = client.get("/api/broker/upstox/redirect-url", headers=owner_a).json()
    assert body_a["redirect_uri_mismatch"] is True and body_a["redirect_uri_will_be_replaced"] is True
    owner_b = _auth("d2-tenant-b@example.com")
    body = client.get("/api/broker/upstox/redirect-url", headers=owner_b).json()
    assert body == {"redirect_url": public_base, "redirect_uri_mismatch": False, "accounts": [], "redirect_uri_will_be_replaced": False}


def test_other_brokers_are_not_listed(public_base):
    headers = _auth("d2-other@example.com")
    client.post("/api/broker/fyers/credentials", headers=headers, json={"api_key": "k", "redirect_uri": OLD})
    assert client.get("/api/broker/upstox/redirect-url", headers=headers).json()["accounts"] == []


@pytest.mark.parametrize("value", [
    "atp.example.com",                       # no scheme
    "https://atp.example.com/app",           # a path
    "https://atp.example.com?x=1",           # a query
    "https://atp.example.com#top",           # a fragment
    "ftp://atp.example.com",                 # not http(s)
    "https://",                              # no host
    "https://user@atp.example.com",          # credentials in the address
    "https://atp.example.com:notaport",      # a malformed port
])
def test_a_public_base_url_that_is_not_a_bare_origin_is_not_used(monkeypatch, caplog, value):
    from app.brokers import routes
    monkeypatch.setattr(app_config, "PUBLIC_BASE_URL", value)
    monkeypatch.setattr(routes, "_REJECTED_PUBLIC_BASES", set())
    headers = _auth(f"d2-bad-{abs(hash(value))}@example.com")
    with caplog.at_level("WARNING", logger="app.brokers.routes"):
        body = client.get("/api/broker/upstox/redirect-url", headers=headers).json()
    assert body["redirect_url"] == "http://testserver" + PATH
    assert any("PUBLIC_BASE_URL" in r.getMessage() for r in caplog.records)
    _store(headers)
    assert _start_redirect(headers) == "http://testserver" + PATH      # never stored into the credentials


@pytest.mark.parametrize("value", ["https://atp.example.com", "https://atp.example.com/", "http://localhost:8080"])
def test_a_bare_origin_is_used(monkeypatch, value):
    monkeypatch.setattr(app_config, "PUBLIC_BASE_URL", value)
    body = client.get("/api/broker/upstox/redirect-url", headers=_auth(f"d2-good-{abs(hash(value))}@example.com")).json()
    assert body["redirect_url"] == value.rstrip("/") + PATH


def test_a_request_through_another_host_still_gets_the_public_url(public_base):
    """The API reached by its server address (a Host header that is not the domain): every derivation is the public URL."""
    headers = {**_auth("d2-host@example.com"), "Host": "203.0.113.5:8000"}
    _store(headers)
    assert client.get("/api/broker/upstox/redirect-url", headers=headers).json()["redirect_url"] == public_base
    status = {r["broker_name"]: r for r in client.get("/api/broker/token-status", headers=headers).json()}["upstox"]
    assert status["oauth_callback_url"] == public_base
    assert _start_redirect(headers) == public_base
    assert "203.0.113.5" not in client.get("/api/broker/token-status", headers=headers).text


def test_token_status_flags_the_account_whose_stored_redirect_uri_differs(public_base):
    headers = _auth("d2-status@example.com")
    _store(headers, redirect_uri=OTHER)
    _store(headers, label="second", redirect_uri=public_base)
    _store(headers, label="third")
    _store(headers, label="old", redirect_uri=OLD)
    client.post("/api/broker/zerodha/credentials", headers=headers, json={"api_key": "k", "access_token": "t"})
    response = client.get("/api/broker/token-status", headers=headers)
    rows = {(r["broker_name"], r["account_label"]): r for r in response.json()}
    flags = {key: (row["redirect_uri_mismatch"], row["redirect_uri_will_be_replaced"]) for key, row in rows.items()}
    assert flags == {
        ("upstox", "primary"): (True, False),       # a domain name: kept, the trader changes it
        ("upstox", "second"): (False, False),
        ("upstox", "third"): (False, False),
        ("upstox", "old"): (False, True),           # an IP address over http: the next login replaces it
        ("zerodha", "primary"): (None, None),
    }
    for value in (OLD, "192.0.2.10", OTHER, "other.example.org"):
        assert value not in response.text


# --- owner order: a stored Redirect URI left over on an IP address or plain http is replaced (Upstox UDAPI100068) -----

def _stored_record(headers: dict, label: str = "primary"):
    """One Upstox account's credential row, read from the database (the API never returns its values)."""
    import asyncio

    from sqlalchemy import select

    from app.db.models import BrokerCredentialRecord
    from tests.test_auth_api import _session_factory
    tenant_id = client.get("/api/auth/me", headers=headers).json()["tenant_id"]

    async def read():
        async with _session_factory() as session:
            record = await session.scalar(select(BrokerCredentialRecord).where(
                BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.broker_name == "upstox",
                BrokerCredentialRecord.account_label == label))
            return record
    return asyncio.run(read())


def _stored_credentials(headers: dict, label: str = "primary"):
    from app.brokers.token_lifecycle import load_credentials
    return load_credentials(_stored_record(headers, label))


def _stored_uri(headers: dict, label: str = "primary") -> str:
    return _stored_credentials(headers, label).redirect_uri


@pytest.mark.parametrize("stale", [
    "http://192.0.2.10:8000" + PATH,           # an IP address over http (the address the API used to be reached at)
    "https://192.0.2.10" + PATH,               # an IP address over https
    "https://[2001:db8::1]" + PATH,            # an IPv6 address
    "http://atp.example.org" + PATH,           # a domain name, but plain http
    "https://3221226122" + PATH,               # an IPv4 address as one integer
    "https://0xc0.0x0.0x2.0xa" + PATH,         # an IPv4 address in hex parts
    "https://0xc000020a" + PATH,               # an IPv4 address as one hex number
])
def test_oauth_start_replaces_a_stale_stored_redirect_uri_with_the_public_one(public_base, stale):
    headers = _auth(f"d2-stale-{abs(hash(stale))}@example.com")
    _store(headers, redirect_uri=stale, access_token="d2-access")
    before = _stored_credentials(headers)
    body = client.get("/api/broker/upstox/redirect-url", headers=headers).json()
    # Not a mismatch for the trader to fix: the next login replaces it by itself.
    assert body["redirect_uri_mismatch"] is False and body["redirect_uri_will_be_replaced"] is True
    assert body["accounts"][0]["redirect_uri_will_be_replaced"] is True
    assert _start_redirect(headers) == public_base
    after = _stored_credentials(headers)
    assert after.redirect_uri == public_base                                        # re-saved, encrypted
    # Only the Redirect URI changed.
    for field in ("api_key", "api_secret", "access_token", "request_token", "client_id"):
        assert getattr(after, field) == getattr(before, field), field
    assert before.access_token == "d2-access"
    body = client.get("/api/broker/upstox/redirect-url", headers=headers).json()
    assert body["redirect_uri_mismatch"] is False and body["redirect_uri_will_be_replaced"] is False
    logs = client.get("/api/audit-logs", headers=headers).json()
    replaced = [entry for entry in logs if entry["event"] == "broker_redirect_uri_replaced"]
    assert len(replaced) == 1 and "192.0.2" not in replaced[0]["detail"] and "2001:db8" not in replaced[0]["detail"]


def test_oauth_start_keeps_a_stored_redirect_uri_on_a_domain_name(public_base):
    other = OTHER
    headers = _auth("d2-domain@example.com")
    _store(headers, redirect_uri=other)
    assert _start_redirect(headers) == other
    assert _stored_uri(headers) == other
    assert client.get("/api/broker/upstox/redirect-url", headers=headers).json()["redirect_uri_mismatch"] is True


@pytest.mark.parametrize("base", ["", "atp.example.com", "https://atp.example.com/app"])
def test_nothing_is_replaced_without_a_valid_public_base_url(monkeypatch, base):
    monkeypatch.setattr(app_config, "PUBLIC_BASE_URL", base)
    headers = _auth(f"d2-nobase-stale-{abs(hash(base))}@example.com")
    _store(headers, redirect_uri=OLD)
    assert _start_redirect(headers) == OLD
    assert _stored_uri(headers) == OLD
    assert not any(e["event"] == "broker_redirect_uri_replaced" for e in client.get("/api/audit-logs", headers=headers).json())


def _exchanging(seen: dict):
    class _Exchanging:
        def __init__(self, creds):
            seen["redirect_uri"] = creds.redirect_uri

        async def authenticate(self):
            return BrokerProfile(broker="upstox", user_id="U1")

        @property
        def access_token(self):
            return "d2-token"
    return _Exchanging


def test_the_callback_exchange_replaces_a_stale_stored_redirect_uri(public_base, monkeypatch):
    headers = _auth("d2-cb-stale@example.com")
    _store(headers, redirect_uri=OLD)
    me = client.get("/api/auth/me", headers=headers).json()
    seen: dict = {}
    monkeypatch.setattr("app.brokers.routes.get_broker_adapter", lambda name, creds: _exchanging(seen)(creds))
    response = client.get(PATH, params={"code": "c", "state": tl.create_oauth_state(me["tenant_id"], me["id"], "upstox")},
                          follow_redirects=False)
    assert parse_qs(urlparse(response.headers["location"]).query) == {"broker": ["upstox"], "connected": ["1"]}
    assert seen["redirect_uri"] == public_base
    assert _stored_uri(headers) == public_base
    logs = client.get("/api/audit-logs", headers=headers).json()
    replaced = [entry for entry in logs if entry["event"] == "broker_redirect_uri_replaced"]
    assert len(replaced) == 1 and "192.0.2" not in replaced[0]["detail"]


def test_the_callback_exchange_keeps_a_stored_redirect_uri_on_a_domain_name(public_base, monkeypatch):
    other = OTHER
    headers = _auth("d2-cb-domain@example.com")
    _store(headers, redirect_uri=other)
    me = client.get("/api/auth/me", headers=headers).json()
    seen: dict = {}
    monkeypatch.setattr("app.brokers.routes.get_broker_adapter", lambda name, creds: _exchanging(seen)(creds))
    client.get(PATH, params={"code": "c", "state": tl.create_oauth_state(me["tenant_id"], me["id"], "upstox")}, follow_redirects=False)
    assert seen["redirect_uri"] == other
    assert _stored_uri(headers) == other


def test_oauth_start_logs_the_client_id_tail_the_redirect_uri_and_the_label_and_no_secret(public_base, caplog):
    client_id = "d2c1i3n7-0000-4000-8000-00000000ABCD"
    headers = _auth("d2-log@example.com")
    client.post("/api/broker/upstox/credentials?account_label=desk", headers=headers,
                json={"api_key": client_id, "api_secret": "d2-very-secret-value", "redirect_uri": OLD})
    with caplog.at_level("INFO", logger="app.brokers.routes"):
        assert _start_redirect(headers, "desk") == public_base
    lines = [r.getMessage() for r in caplog.records if "Upstox OAuth start" in r.getMessage()]
    assert lines == [f"Upstox OAuth start: client_id=…ABCD redirect_uri={public_base} account_label=desk"]
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert client_id not in text and "d2-very-secret-value" not in text


def test_a_failed_exchange_with_a_stale_uri_leaves_the_stored_row_byte_identical(public_base, monkeypatch):
    headers = _auth("d2-cb-fail@example.com")
    _store(headers, redirect_uri=OLD)
    before = _stored_record(headers).encrypted_payload
    me = client.get("/api/auth/me", headers=headers).json()

    class _Refusing:
        def __init__(self, creds):
            pass

        async def authenticate(self):
            raise RuntimeError("UDAPI100068")

    monkeypatch.setattr("app.brokers.routes.get_broker_adapter", lambda name, creds: _Refusing(creds))
    response = client.get(PATH, params={"code": "c", "state": tl.create_oauth_state(me["tenant_id"], me["id"], "upstox")},
                          follow_redirects=False)
    assert parse_qs(urlparse(response.headers["location"]).query)["error"] == ["exchange_failed"]
    assert _stored_record(headers).encrypted_payload == before
    assert not any(e["event"] == "broker_redirect_uri_replaced" for e in client.get("/api/audit-logs", headers=headers).json())


@pytest.mark.parametrize("stored", [None, OLD])
def test_oauth_start_refuses_when_the_row_changed_since_it_was_read(public_base, monkeypatch, caplog, stored):
    """The start's save (the replaced URI, or the first one when none is stored) is conditional on the payload it read:
    a row stored over meanwhile gets a 409 and keeps what the other request wrote."""
    from app.brokers import routes
    from app.brokers.models import BrokerCredentials
    from app.secrets_store.encryption import encrypt_text
    from app.secrets_store.envelope import PURPOSE_BROKER_CREDENTIAL
    headers = _auth(f"d2-race-{'none' if stored is None else 'old'}@example.com")
    _store(headers, **({"redirect_uri": stored} if stored else {}))
    before = _stored_record(headers).encrypted_payload
    real = routes.get_credential_record

    async def read_then_changed(session, tenant_id, name, label="primary"):
        record = await real(session, tenant_id, name, label)
        # What this request read is no longer what is stored: the row it holds carries an older payload (detached, so
        # the ORM never writes it back).
        session.expunge(record)
        record.encrypted_payload = encrypt_text(BrokerCredentials(api_key="client-d2", api_secret="secret-d2", redirect_uri=stored).model_dump_json(),
                                                record.tenant_id, PURPOSE_BROKER_CREDENTIAL)
        return record

    monkeypatch.setattr(routes, "get_credential_record", read_then_changed)
    with caplog.at_level("INFO", logger="app.brokers.routes"):
        response = client.get("/api/broker/upstox/oauth/start", headers=headers)
    assert not any("Upstox OAuth start" in r.getMessage() for r in caplog.records)
    assert response.status_code == 409
    assert response.json()["detail"] == "Your broker details changed while signing in - try again."
    monkeypatch.setattr(routes, "get_credential_record", real)
    assert _stored_record(headers).encrypted_payload == before
    events = [e["event"] for e in client.get("/api/audit-logs", headers=headers).json()]
    assert "broker_oauth_started" not in events and "broker_redirect_uri_replaced" not in events
