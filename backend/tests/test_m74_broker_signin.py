"""M74 (BL-7): "Continue with your broker" - the broker sign-in providers, with fake brokers on an httpx mock transport.
The suite never reaches a broker. Route-level cases (sign-in, link, new user, hidden button) run on M73's routes."""
import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app.auth import broker_signin
from app.auth.broker_signin import BrokerSignInError, configured_brokers, provider_for, store_signin_session
from app.brokers.fyers import API_URL as FYERS_API
from app.brokers.models import BrokerCredentials
from app.brokers.token_lifecycle import default_token_expiry, load_credentials
from app.brokers.upstox import UpstoxBroker
from app.brokers.zerodha import ZerodhaBroker
from app.core import config
from app.core.enums import BrokerTokenStatus
from app.db.models import AuditLogRecord, BrokerCredentialRecord, User
from app.secrets_store.encryption import encrypt_text
from app.secrets_store.envelope import PURPOSE_BROKER_CREDENTIAL
from tests.test_auth_api import _register, _session_factory

PLATFORM_KEY = "platform-app-key-1234"
PLATFORM_SECRET = "platform-secret-never-shown"
CODE = "one-time-code-xyz"
TOKEN = "day-session-token-abc"
REDIRECT = "https://aialgodesk.tech/api/auth/broker/{b}/callback"
NOW = datetime(2026, 11, 2, 4, 0, tzinfo=UTC)


def _run(coro):
    return asyncio.run(coro)


def _apps(**brokers: bool) -> dict[str, dict[str, Any]]:
    """BROKER_SIGNIN_APPS with each named broker set; the bool is its `approved` switch."""
    return {b: {"api_key": PLATFORM_KEY, "api_secret": PLATFORM_SECRET, "approved": ok} for b, ok in brokers.items()}


@pytest.fixture
def apps(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://aialgodesk.tech")

    def set_apps(**brokers: bool) -> None:
        monkeypatch.setattr(config, "BROKER_SIGNIN_APPS", _apps(**brokers))
    set_apps(upstox=True, zerodha=True, fyers=True)
    return set_apps


class FakeBroker:
    """One broker's token exchange + profile. `profile` overrides the returned profile fields."""

    def __init__(self, broker: str, *, email: Any = "Trader@Example.COM", user_id: str = "AB1234") -> None:
        self.broker, self.email, self.user_id = broker, email, user_id
        self.exchange_status = 200
        self.requests: list = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if self.exchange_status != 200 and ("token" in path or "validate-authcode" in path):
            return httpx.Response(self.exchange_status, json={"status": "error", "message": f"bad code {CODE} {PLATFORM_SECRET}",
                                                              "error_description": f"bad {CODE}", "s": "error", "code": -1})
        if self.broker == "upstox":
            if path.endswith("/login/authorization/token"):
                return httpx.Response(200, json={"access_token": TOKEN})
            return httpx.Response(200, json={"status": "success", "data": {"user_id": self.user_id, "user_name": "A Trader", "email": self.email}})
        if self.broker == "zerodha":
            if path.endswith("/session/token"):
                return httpx.Response(200, json={"status": "success", "data": {"access_token": TOKEN}})
            return httpx.Response(200, json={"status": "success", "data": {"user_id": self.user_id, "user_name": "A Trader", "email": self.email}})
        if path.endswith("/validate-authcode"):
            return httpx.Response(200, json={"s": "ok", "code": 200, "access_token": TOKEN})
        return httpx.Response(200, json={"s": "ok", "code": 200, "data": {"fy_id": self.user_id, "name": "A Trader", "email_id": self.email}})

    def client(self) -> httpx.AsyncClient:
        base = {"upstox": UpstoxBroker.BASE_URL, "zerodha": ZerodhaBroker.BASE_URL, "fyers": FYERS_API}[self.broker]
        return httpx.AsyncClient(base_url=base, transport=httpx.MockTransport(self.handle))


async def _identity(broker: str, fake: FakeBroker):
    provider = provider_for(f"broker:{broker}")
    async with fake.client() as client:
        return await provider.identity_for_code(CODE, REDIRECT.format(b=broker), client)


def _user(email: str) -> User:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(User).where(User.email == email))
    return _run(go())


# --- BL-1: which brokers the sign-in page shows ---------------------------------------------------------------------

def test_only_configured_and_approved_brokers_are_listed(apps):
    apps(upstox=True, zerodha=False)                      # Zerodha set but not approved; Fyers not set at all
    listed = configured_brokers()
    assert [b["kind"] for b in listed] == ["broker:upstox"]
    assert listed[0] == {"kind": "broker:upstox", "broker": "upstox", "name": "Upstox", "initials": "UP"}
    assert PLATFORM_KEY not in repr(listed) and PLATFORM_SECRET not in repr(listed)


def test_nothing_listed_when_no_platform_app_is_set(monkeypatch):
    monkeypatch.setattr(config, "BROKER_SIGNIN_APPS", {})
    assert configured_brokers() == []
    provider = provider_for("upstox")
    assert provider is not None and not provider.configured()
    with pytest.raises(BrokerSignInError) as exc:
        provider.authorization_url("https://x/cb", "s")
    assert exc.value.code == "not_configured"


def test_a_missing_secret_hides_the_broker(monkeypatch):
    monkeypatch.setattr(config, "BROKER_SIGNIN_APPS", {"fyers": {"api_key": PLATFORM_KEY, "api_secret": "", "approved": True}})
    assert configured_brokers() == []


def test_hardened_environment_needs_a_public_origin(apps, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr(config, "ENVIRONMENT", next(iter(config.HARDENED_ENVIRONMENTS)))
    assert configured_brokers() == []


def test_own_keys_only_brokers_have_no_sign_in_provider():
    for broker in ("dhan", "angel_one", "shoonya", "kotak_neo", "groww", "coindcx", "paper", "broker:dhan"):
        assert provider_for(broker) is None


# --- the login link and the redirect ---------------------------------------------------------------------------------

def test_login_links_carry_the_state_and_the_platform_key(apps):
    for broker in ("upstox", "zerodha", "fyers"):
        provider = provider_for(broker)
        redirect = provider.redirect_uri("http://ignored")
        assert redirect == REDIRECT.format(b=broker)
        url = provider.authorization_url(redirect, "state-123")
        query = parse_qs(urlsplit(url).query)
        assert PLATFORM_SECRET not in url
        if broker == "zerodha":
            assert query["api_key"] == [PLATFORM_KEY] and parse_qs(query["redirect_params"][0]) == {"state": ["state-123"]}
        else:
            assert query["client_id"] == [PLATFORM_KEY] and query["state"] == ["state-123"]
            assert query["redirect_uri"] == [redirect]


@pytest.mark.parametrize("broker,params,code", [
    ("upstox", {"code": CODE}, CODE),
    ("zerodha", {"request_token": CODE, "status": "success"}, CODE),
    ("fyers", {"auth_code": CODE, "s": "ok"}, CODE),
])
def test_code_is_read_from_the_brokers_own_parameter(apps, broker, params, code):
    assert provider_for(broker).code_from(params) == code


@pytest.mark.parametrize("broker,params,error", [
    ("upstox", {"error": "access_denied"}, "cancelled"),
    ("zerodha", {"status": "cancelled", "request_token": CODE}, "cancelled"),
    ("fyers", {"s": "error"}, "cancelled"),
    ("upstox", {}, "failed"),
    ("zerodha", {"request_token": "has space"}, "failed"),
    ("fyers", {"auth_code": "x" * 5000}, "failed"),
])
def test_refused_or_malformed_redirects_end_in_a_fixed_code(apps, broker, params, error):
    with pytest.raises(BrokerSignInError) as exc:
        provider_for(broker).code_from(params)
    assert exc.value.code == error


# --- BL-2: the exchange gives the identity and that day's token ------------------------------------------------------

@pytest.mark.parametrize("broker", ["upstox", "zerodha", "fyers"])
def test_happy_path_identity_from_the_profile(apps, broker):
    fake = FakeBroker(broker)
    identity = _run(_identity(broker, fake))
    assert identity.kind == f"broker:{broker}" and identity.subject == "AB1234"
    assert identity.email == "trader@example.com"            # normalised for the link-by-email lookup (BL-3)
    assert identity.name == "A Trader" and identity.access_token == TOKEN
    assert TOKEN not in repr(identity)
    exchange = fake.requests[0]
    body = exchange.content.decode()
    if broker == "upstox":
        assert f"client_secret={PLATFORM_SECRET}" in body and "redirect_uri=" in body
    elif broker == "zerodha":
        assert f"api_key={PLATFORM_KEY}" in body and "checksum=" in body and PLATFORM_SECRET not in body
    else:
        assert CODE in body and PLATFORM_SECRET not in body          # Fyers sends the hash of key:secret, not the secret


def test_broker_without_an_email_gives_none(apps):
    identity = _run(_identity("fyers", FakeBroker("fyers", email="")))
    assert identity.email is None and identity.subject == "AB1234"     # the M73 no-email path asks for one


def test_failed_exchange_is_one_code_and_logs_no_secret(apps, caplog):
    fake = FakeBroker("upstox")
    fake.exchange_status = 400
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(BrokerSignInError) as exc:
            _run(_identity("upstox", fake))
    assert exc.value.code == "exchange_failed" and str(exc.value) == "exchange_failed"
    for secret in (CODE, PLATFORM_SECRET, TOKEN):
        assert secret not in caplog.text


def test_profile_without_a_user_id_is_refused(apps):
    with pytest.raises(BrokerSignInError) as exc:
        _run(_identity("zerodha", FakeBroker("zerodha", user_id="")))
    assert exc.value.code in ("failed", "exchange_failed")


# --- BL-2: the session stored as today's broker connection -----------------------------------------------------------

def _rows(tenant_id: int, broker: str):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(BrokerCredentialRecord).where(
                BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.broker_name == broker)))
    return _run(go())


def _store(email: str, identity, now=NOW):
    async def go():
        async with _session_factory() as session:
            user = await session.scalar(select(User).where(User.email == email))
            record = await store_signin_session(session, user, identity, now=now)
            await session.commit()
            return record.account_label
    return _run(go())


def test_token_is_stored_as_todays_valid_session(apps, caplog):
    email = "m74-store@example.com"
    _register(email)
    user = _user(email)
    identity = _run(_identity("zerodha", FakeBroker("zerodha")))
    with caplog.at_level(logging.DEBUG):
        assert _store(email, identity) == "primary"
    [row] = _rows(user.tenant_id, "zerodha")
    assert row.user_id == user.id and row.token_status == BrokerTokenStatus.VALID.value
    assert row.token_expires_at.replace(tzinfo=UTC) == default_token_expiry("zerodha", NOW)
    creds = load_credentials(row)
    assert creds.access_token == TOKEN and creds.api_key == PLATFORM_KEY
    assert creds.api_secret is None and creds.request_token is None          # the platform secret never lands on a tenant row
    assert (creds.model_extra or {}).get("platform_app") is True
    assert TOKEN not in row.encrypted_payload and PLATFORM_SECRET not in row.encrypted_payload
    for secret in (TOKEN, PLATFORM_SECRET, CODE):
        assert secret not in caplog.text

    async def audit():
        async with _session_factory() as session:
            return list(await session.scalars(select(AuditLogRecord).where(
                AuditLogRecord.tenant_id == user.tenant_id, AuditLogRecord.event == "broker_signin_session_stored")))
    rows = _run(audit())
    assert [r.detail for r in rows] == ["zerodha/primary"] and TOKEN not in rows[0].detail


def test_next_days_sign_in_replaces_its_own_row(apps):
    email = "m74-again@example.com"
    _register(email)
    first = _run(_identity("upstox", FakeBroker("upstox")))
    assert _store(email, first) == "primary"
    assert _store(email, first, now=NOW.replace(day=3)) == "primary"
    [row] = _rows(_user(email).tenant_id, "upstox")
    assert row.token_expires_at.replace(tzinfo=UTC) == default_token_expiry("upstox", NOW.replace(day=3))


def test_own_keys_row_is_never_overwritten(apps):
    email = "m74-own@example.com"
    _register(email)
    user = _user(email)

    async def own_keys():
        async with _session_factory() as session:
            from app.secrets_store.envelope import ensure_tenant_key
            await ensure_tenant_key(session, user.tenant_id)
            own = BrokerCredentials(api_key="own-key", api_secret="own-secret")
            session.add(BrokerCredentialRecord(tenant_id=user.tenant_id, user_id=user.id, broker_name="fyers", account_label="primary",
                                               encrypted_payload=encrypt_text(own.model_dump_json(), user.tenant_id, PURPOSE_BROKER_CREDENTIAL)))
            await session.commit()
    _run(own_keys())
    identity = _run(_identity("fyers", FakeBroker("fyers")))
    assert _store(email, identity) == broker_signin.SIGNIN_LABEL
    rows = {r.account_label: load_credentials(r) for r in _rows(user.tenant_id, "fyers")}
    assert rows["primary"].api_key == "own-key" and rows["primary"].api_secret == "own-secret"
    assert rows[broker_signin.SIGNIN_LABEL].access_token == TOKEN

    other = _run(_identity("fyers", FakeBroker("fyers", user_id="ZZ9999")))   # a second broker account: no free row left
    with pytest.raises(BrokerSignInError) as exc:
        _store(email, other)
    assert exc.value.code == "no_free_slot"


def test_store_refuses_when_the_platform_app_is_withdrawn(apps):
    email = "m74-withdrawn@example.com"
    _register(email)
    identity = _run(_identity("upstox", FakeBroker("upstox")))
    apps(upstox=False)
    with pytest.raises(BrokerSignInError) as exc:
        _store(email, identity)
    assert exc.value.code == "not_configured"
    assert _rows(_user(email).tenant_id, "upstox") == []
