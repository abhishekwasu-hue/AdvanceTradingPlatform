import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.models import BrokerProfile
from app.brokers import token_lifecycle as tl
from app.core.enums import BrokerTokenStatus
from app.db.models import BrokerCredentialRecord, NotificationRecord
from app.secrets_store.encryption import decrypt_text, encrypt_text
from tests.test_auth_api import _register, _session_factory, client

IST = ZoneInfo("Asia/Kolkata")


# --- pure helpers -------------------------------------------------------------------------------

def test_upstox_token_expires_at_next_0330_ist():
    issued = datetime(2026, 9, 25, 9, 0, tzinfo=IST)  # trading morning
    expiry = tl.default_token_expiry("upstox", issued)
    assert expiry.astimezone(IST) == datetime(2026, 9, 26, 3, 30, tzinfo=IST)

    small_hours = datetime(2026, 9, 25, 2, 0, tzinfo=IST)  # before today's cut-off -> today's 03:30
    assert tl.default_token_expiry("upstox", small_hours).astimezone(IST) == datetime(2026, 9, 25, 3, 30, tzinfo=IST)


def test_unknown_broker_gets_conservative_0600_default():
    issued = datetime(2026, 9, 25, 9, 0, tzinfo=IST)
    assert tl.default_token_expiry("some_new_broker", issued).astimezone(IST) == datetime(2026, 9, 26, 6, 0, tzinfo=IST)


def _record(status: str, expires_at=None) -> BrokerCredentialRecord:
    return BrokerCredentialRecord(
        tenant_id=1, user_id=1, broker_name="upstox", encrypted_payload=encrypt_text('{"api_key": "k"}'),
        token_status=status, token_expires_at=expires_at,
    )


def test_token_is_usable_requires_valid_status_and_unexpired():
    now = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    assert tl.token_is_usable(_record("VALID", now + timedelta(hours=1)), now)
    assert tl.token_is_usable(_record("VALID", None), now)  # no expiry recorded -> trust the status
    assert not tl.token_is_usable(_record("VALID", now - timedelta(minutes=1)), now)
    assert not tl.token_is_usable(_record("EXPIRED", now + timedelta(hours=1)), now)
    assert not tl.token_is_usable(_record("UNKNOWN", None), now)
    assert not tl.token_is_usable(None, now)


def test_token_is_usable_tolerates_naive_expiry_from_sqlite():
    now = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    assert tl.token_is_usable(_record("VALID", datetime(2026, 9, 25, 11, 0)), now)
    assert not tl.token_is_usable(_record("VALID", datetime(2026, 9, 25, 9, 0)), now)


def test_store_access_token_reencrypts_and_drops_one_time_code():
    record = _record("UNKNOWN")
    record.encrypted_payload = encrypt_text('{"api_key": "k", "api_secret": "s", "request_token": "one-time"}')
    now = datetime(2026, 9, 25, 9, 0, tzinfo=IST)

    tl.store_access_token(record, "fresh-token", now)

    stored = tl.load_credentials(record)
    assert stored.access_token == "fresh-token"
    assert stored.request_token is None
    assert stored.api_secret == "s"
    assert record.token_status == "VALID"
    assert record.token_expires_at.astimezone(IST) == datetime(2026, 9, 26, 3, 30, tzinfo=IST)
    assert record.last_verified_at == now


def test_oauth_state_round_trips_and_rejects_tampering():
    state = tl.create_oauth_state(tenant_id=7, user_id=42, broker_name="upstox")
    claims = tl.parse_oauth_state(state)
    assert (claims["tenant_id"], claims["user_id"], claims["broker"]) == (7, 42, "upstox")
    with pytest.raises(ValueError):
        tl.parse_oauth_state(state[:-3] + "xyz")
    with pytest.raises(ValueError):
        tl.parse_oauth_state("not-a-token")


def test_oauth_state_expires():
    long_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    with pytest.raises(ValueError):
        tl.parse_oauth_state(tl.create_oauth_state(1, 1, "upstox", now=long_ago))


def test_upstox_authorization_url_carries_client_redirect_and_state():
    url = tl.build_upstox_authorization_url("client-1", "https://app.example.com/api/broker/upstox/oauth/callback", "st")
    parsed = urlparse(url)
    assert parsed.scheme == "https" and parsed.netloc == "api.upstox.com"
    q = parse_qs(parsed.query)
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["client-1"]
    assert q["redirect_uri"] == ["https://app.example.com/api/broker/upstox/oauth/callback"]
    assert q["state"] == ["st"]


# --- verify_token against a fake adapter -------------------------------------------------------

class _Adapter:
    def __init__(self, exc=None):
        self.exc = exc

    async def get_profile(self):
        if self.exc:
            raise self.exc
        return BrokerProfile(broker="upstox", user_id="U1")


def _run_verify(monkeypatch, tenant_id: int, record_status: str, exc):
    monkeypatch.setattr(tl, "build_adapter", lambda record, client=None: _Adapter(exc))

    async def go():
        async with _session_factory() as session:
            record = BrokerCredentialRecord(
                tenant_id=tenant_id, user_id=1, broker_name="upstox",
                encrypted_payload=encrypt_text('{"api_key": "k", "access_token": "t"}'), token_status=record_status,
            )
            # user_id must reference a real user for FK-enforcing DBs; SQLite in tests doesn't enforce.
            session.add(record)
            await session.flush()
            ok, message = await tl.verify_token(session, record)
            await session.commit()
            notes = list(await session.scalars(
                select(NotificationRecord).where(NotificationRecord.tenant_id == tenant_id)
            ))
            return ok, message, record.token_status, notes

    return asyncio.run(go())


def _tenant_of(token: str) -> int:
    return client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["tenant_id"]


def test_verify_token_marks_valid_on_success(monkeypatch):
    tenant_id = _tenant_of(_register("tl-ok@example.com"))
    ok, _, status, notes = _run_verify(monkeypatch, tenant_id, "UNKNOWN", None)
    assert ok and status == "VALID"
    assert notes == []


def test_verify_token_marks_expired_and_notifies_on_auth_failure(monkeypatch):
    tenant_id = _tenant_of(_register("tl-exp@example.com"))
    ok, message, status, notes = _run_verify(monkeypatch, tenant_id, "VALID", BrokerAuthenticationError("Invalid token"))
    assert not ok and status == "EXPIRED"
    assert [n.event_type for n in notes] == ["TOKEN_EXPIRED"]
    assert notes[0].severity == "CRITICAL"


def test_verify_token_treats_401_as_expiry(monkeypatch):
    tenant_id = _tenant_of(_register("tl-401@example.com"))
    ok, _, status, notes = _run_verify(monkeypatch, tenant_id, "VALID", BrokerAPIError("Unauthorized", 401))
    assert not ok and status == "EXPIRED"
    assert len(notes) == 1


def test_verify_token_leaves_status_alone_on_network_failure(monkeypatch):
    tenant_id = _tenant_of(_register("tl-net@example.com"))
    ok, message, status, notes = _run_verify(monkeypatch, tenant_id, "VALID", ConnectionError("dns failed"))
    assert not ok and status == "VALID"
    assert "Could not reach" in message
    assert notes == []


# --- API: token status + OAuth endpoints ---------------------------------------------------------

def _auth(email: str):
    token = _register(email)
    return {"Authorization": f"Bearer {token}"}


def test_token_status_lists_each_stored_broker_with_login_hint():
    headers = _auth("status@example.com")
    client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": "k", "api_secret": "s"})
    client.post("/api/broker/zerodha/credentials", headers=headers, json={"api_key": "k", "access_token": "t"})

    response = client.get("/api/broker/token-status", headers=headers)
    assert response.status_code == 200
    by_name = {row["broker_name"]: row for row in response.json()}
    assert by_name["upstox"]["token_status"] == "UNKNOWN"
    assert by_name["upstox"]["needs_login"] is True
    assert by_name["upstox"]["oauth_supported"] is True
    assert by_name["upstox"]["oauth_callback_url"].endswith("/api/broker/upstox/oauth/callback")
    assert by_name["zerodha"]["oauth_supported"] is False
    assert by_name["zerodha"]["oauth_callback_url"] is None


def test_token_status_requires_auth():
    assert client.get("/api/broker/token-status").status_code in (401, 403)


def test_authenticate_success_marks_token_valid_and_persists_new_token(monkeypatch):
    headers = _auth("auth-valid@example.com")
    client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": "k", "api_secret": "s", "request_token": "code1", "redirect_uri": "https://x/cb"})

    class _Exchanging:
        _access_token = None

        async def authenticate(self):
            self._access_token = "exchanged-token"
            return BrokerProfile(broker="upstox", user_id="U1")

        @property
        def access_token(self):
            return self._access_token

    monkeypatch.setattr("app.brokers.routes.get_broker_adapter", lambda name, creds: _Exchanging())
    assert client.post("/api/broker/upstox/authenticate", headers=headers).status_code == 200

    status = {r["broker_name"]: r for r in client.get("/api/broker/token-status", headers=headers).json()}["upstox"]
    assert status["token_status"] == "VALID"
    assert status["needs_login"] is False
    assert status["token_expires_at"] is not None

    async def stored_token():
        async with _session_factory() as session:
            record = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.broker_name == "upstox").order_by(BrokerCredentialRecord.id.desc()))
            return tl.load_credentials(record)

    creds = asyncio.run(stored_token())
    assert creds.access_token == "exchanged-token"
    assert creds.request_token is None


def test_authenticate_token_failure_marks_expired(monkeypatch):
    headers = _auth("auth-expired@example.com")
    client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": "k", "access_token": "old"})

    class _Rejecting:
        async def authenticate(self):
            raise BrokerAuthenticationError("Invalid token")

    monkeypatch.setattr("app.brokers.routes.get_broker_adapter", lambda name, creds: _Rejecting())
    assert client.post("/api/broker/upstox/authenticate", headers=headers).status_code == 502
    status = {r["broker_name"]: r for r in client.get("/api/broker/token-status", headers=headers).json()}["upstox"]
    assert status["token_status"] == "EXPIRED"


def test_restoring_credentials_resets_token_status_to_unknown(monkeypatch):
    headers = _auth("restore@example.com")
    client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": "k", "access_token": "t"})

    class _Ok:
        async def authenticate(self):
            return BrokerProfile(broker="upstox", user_id="U1")

    monkeypatch.setattr("app.brokers.routes.get_broker_adapter", lambda name, creds: _Ok())
    client.post("/api/broker/upstox/authenticate", headers=headers)
    assert {r["broker_name"]: r for r in client.get("/api/broker/token-status", headers=headers).json()}["upstox"]["token_status"] == "VALID"

    client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": "k2", "access_token": "t2"})
    assert {r["broker_name"]: r for r in client.get("/api/broker/token-status", headers=headers).json()}["upstox"]["token_status"] == "UNKNOWN"


def test_oauth_start_requires_stored_upstox_credentials():
    headers = _auth("oauth-none@example.com")
    assert client.get("/api/broker/upstox/oauth/start", headers=headers).status_code == 404


def test_oauth_start_requires_api_secret():
    headers = _auth("oauth-nosecret@example.com")
    client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": "k"})
    assert client.get("/api/broker/upstox/oauth/start", headers=headers).status_code == 400


def test_oauth_start_returns_upstox_dialog_url_bound_to_tenant():
    headers = _auth("oauth-start@example.com")
    client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": "client-9", "api_secret": "s"})

    response = client.get("/api/broker/upstox/oauth/start", headers=headers)
    assert response.status_code == 200
    q = parse_qs(urlparse(response.json()["authorization_url"]).query)
    assert q["client_id"] == ["client-9"]
    assert q["redirect_uri"][0].endswith("/api/broker/upstox/oauth/callback")
    claims = tl.parse_oauth_state(q["state"][0])
    me = client.get("/api/auth/me", headers=headers).json()
    assert claims["tenant_id"] == me["tenant_id"]
    assert claims["user_id"] == me["id"]


def test_oauth_callback_exchanges_code_stores_token_and_redirects(monkeypatch):
    headers = _auth("oauth-cb@example.com")
    client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": "client-9", "api_secret": "s"})
    state = parse_qs(urlparse(client.get("/api/broker/upstox/oauth/start", headers=headers).json()["authorization_url"]).query)["state"][0]

    seen = {}

    class _Exchanging:
        def __init__(self, creds):
            seen["creds"] = creds
            self._access_token = None

        async def authenticate(self):
            assert seen["creds"].request_token == "the-code"
            self._access_token = "oauth-token"
            return BrokerProfile(broker="upstox", user_id="U1")

        @property
        def access_token(self):
            return self._access_token

    monkeypatch.setattr("app.brokers.routes.get_broker_adapter", lambda name, creds: _Exchanging(creds))

    response = client.get(
        "/api/broker/upstox/oauth/callback", params={"code": "the-code", "state": state}, follow_redirects=False,
    )
    assert response.status_code == 302
    assert parse_qs(urlparse(response.headers["location"]).query) == {"broker": ["upstox"], "connected": ["1"]}

    status = {r["broker_name"]: r for r in client.get("/api/broker/token-status", headers=headers).json()}["upstox"]
    assert status["token_status"] == "VALID" and status["needs_login"] is False

    logs = client.get("/api/audit-logs", headers=headers).json()
    assert any(l["event"] == "broker_authenticated" and "oauth" in l["detail"] for l in logs)


def test_oauth_callback_rejects_forged_state_without_touching_credentials(monkeypatch):
    called = {"n": 0}

    def _never(name, creds):
        called["n"] += 1
        raise AssertionError("adapter must not be built for a bad state")

    monkeypatch.setattr("app.brokers.routes.get_broker_adapter", _never)
    response = client.get(
        "/api/broker/upstox/oauth/callback", params={"code": "c", "state": "forged"}, follow_redirects=False,
    )
    assert response.status_code == 302
    assert parse_qs(urlparse(response.headers["location"]).query)["error"] == ["invalid_state"]
    assert called["n"] == 0


def test_oauth_callback_relays_broker_side_denial():
    response = client.get("/api/broker/upstox/oauth/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert response.status_code == 302
    assert parse_qs(urlparse(response.headers["location"]).query)["error"] == ["access_denied"]


def test_oauth_callback_exchange_failure_redirects_with_error(monkeypatch):
    headers = _auth("oauth-fail@example.com")
    client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": "client-9", "api_secret": "s"})
    state = parse_qs(urlparse(client.get("/api/broker/upstox/oauth/start", headers=headers).json()["authorization_url"]).query)["state"][0]

    class _Failing:
        async def authenticate(self):
            raise BrokerAuthenticationError("invalid_grant")

    monkeypatch.setattr("app.brokers.routes.get_broker_adapter", lambda name, creds: _Failing())
    response = client.get("/api/broker/upstox/oauth/callback", params={"code": "bad", "state": state}, follow_redirects=False)
    assert parse_qs(urlparse(response.headers["location"]).query)["error"] == ["exchange_failed"]
    status = {r["broker_name"]: r for r in client.get("/api/broker/token-status", headers=headers).json()}["upstox"]
    assert status["token_status"] == "UNKNOWN"
