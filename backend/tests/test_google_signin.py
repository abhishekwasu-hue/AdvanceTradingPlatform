"""GS-1: Google sign-in. Google's endpoints are served by an httpx mock transport - the suite never reaches the network."""
import asyncio
import base64
import hashlib
import json
import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from app.auth import google, google_routes
from app.auth.google_routes import google_rate_limit
from app.core import config
from app.core.logging_config import QueryRedactionFilter, redact_query
from app.db.models import AuditLogRecord, LoginEventRecord, User, UserSessionRecord
from app.main import app
from tests.test_auth_api import _register as _register_new
from tests.test_auth_api import _session_factory, client

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
CLIENT_SECRET = "test-client-secret-never-shown"
AUTH_CODE = "4/one-time-auth-code-from-google"
PUBLIC = "https://aialgodesk.tech"

app.dependency_overrides[google_rate_limit] = lambda: None

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "test-kid-1"


def _jwk(key: rsa.RSAPrivateKey, kid: str) -> Dict[str, Any]:
    data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**data, "kid": kid, "alg": "RS256", "use": "sig"}


def _run(coro):
    return asyncio.run(coro)


def _register(email: str) -> None:
    """An account with a password (the register form), once per address."""
    if _user(email) is None:
        _register_new(email)


def _subject(email: str) -> str:
    """The fake Google's stable account id for an address (one Google account per e-mail)."""
    return "sub-" + email.strip().lower()


def _link_google(email: str, subject: str = "") -> None:
    """M73 SL-2: the account has linked this Google account (Settings › Account, or a confirmed link) - only then does
    a Google sign-in sign it in directly."""
    from app.db.models import UserIdentityRecord
    subject = subject or _subject(email)

    async def go():
        async with _session_factory() as session:
            user = await session.scalar(select(User).where(User.email == email))
            old = await session.scalar(select(UserIdentityRecord).where(UserIdentityRecord.provider == "google",
                                                                        UserIdentityRecord.subject == subject))
            if old is not None:
                await session.delete(old)
                await session.flush()
            session.add(UserIdentityRecord(user_id=user.id, provider="google", subject=subject, email_at_link=email))
            await session.commit()
    _run(go())


class FakeGoogle:
    """Google's token endpoint and key set. `claims` overrides the ID token's claims; `sign_with` its key."""

    def __init__(self) -> None:
        self.claims: Dict[str, Any] = {}
        self.sign_with = _KEY
        self.token_status = 200
        self.token_requests: list = []
        self.jwks_requests = 0
        self.nonce: Optional[str] = None
        self.id_tokens: list = []

    def id_token(self, email: str) -> str:
        now = int(time.time())
        claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "azp": CLIENT_ID, "sub": _subject(email),
                  "email": email, "email_verified": True, "iat": now, "exp": now + 3600, "nonce": self.nonce}
        claims.update(self.claims)
        claims = {k: v for k, v in claims.items() if v is not None}
        token = jwt.encode(claims, self.sign_with, algorithm="RS256", headers={"kid": KID})
        self.id_tokens.append(token)
        return token

    def handler(self, email: str):
        def handle(request: httpx.Request) -> httpx.Response:
            if str(request.url) == google.JWKS_URL:
                self.jwks_requests += 1
                return httpx.Response(200, json={"keys": [_jwk(_KEY, KID)]})
            if str(request.url) == google.TOKEN_URL:
                form = parse_qs(request.content.decode())
                self.token_requests.append(form)
                if self.token_status != 200:
                    return httpx.Response(self.token_status, json={"error": "invalid_grant"})
                return httpx.Response(200, json={"id_token": self.id_token(email), "access_token": "ya29.not-used"})
            return httpx.Response(404)
        return handle


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", PUBLIC)
    monkeypatch.setattr(config, "FRONTEND_URL", PUBLIC)
    google.clear_jwks_cache()
    yield
    google.clear_jwks_cache()


@pytest.fixture
def fake(monkeypatch):
    state: Dict[str, Any] = {"email": "nobody@example.com"}
    fake_google = FakeGoogle()

    def http_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(fake_google.handler(state["email"])))

    monkeypatch.setattr(google, "http_client", http_client)
    fake_google.state = state  # type: ignore[attr-defined]
    return fake_google


def _flags(monkeypatch, **values: bool) -> None:
    async def flag_enabled(session, name, tenant_id=None):
        return values.get(name, False)

    async def legal_enabled(session, tenant_id=None):
        return values.get("legal_pages", False)

    from app.auth import routes as auth_routes
    from app.auth import social_routes
    for module in (social_routes, auth_routes):
        monkeypatch.setattr(module, "flag_enabled", flag_enabled)
        monkeypatch.setattr(module, "legal_enabled", legal_enabled)


def _start() -> tuple:
    response = client.get("/api/auth/google/start", follow_redirects=False)
    assert response.status_code == 302, response.text
    cookie = response.cookies.get(google.FLOW_COOKIE)
    assert cookie
    params = parse_qs(urlsplit(response.headers["location"]).query)
    return response, cookie, params


def _callback(fake: FakeGoogle, email: str, *, state: Optional[str] = None, cookie: Optional[str] = "auto", **extra: str) -> httpx.Response:
    _, flow_cookie, params = _start()
    fake.state["email"] = email  # type: ignore[attr-defined]
    fake.nonce = params["nonce"][0]
    query = {"state": state if state is not None else params["state"][0], "code": AUTH_CODE, **extra}
    headers = {}
    value = flow_cookie if cookie == "auto" else cookie
    client.cookies.clear()
    if value:
        headers["cookie"] = f"{google.FLOW_COOKIE}={value}"
    return client.get("/api/auth/google/callback", params=query, headers=headers, follow_redirects=False)


def _location(response: httpx.Response) -> Dict[str, Any]:
    location = response.headers["location"]
    parts = urlsplit(location)
    return {"url": location, "query": {k: v[0] for k, v in parse_qs(parts.query).items()}, "fragment": parts.fragment,
            "origin": f"{parts.scheme}://{parts.netloc}", "path": parts.path}


def _user(email: str) -> User:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(User).where(User.email == email))
    return _run(go())


def _set_user(email: str, **fields: Any) -> None:
    async def go():
        async with _session_factory() as session:
            user = await session.scalar(select(User).where(User.email == email))
            for k, v in fields.items():
                setattr(user, k, v)
            await session.commit()
    _run(go())


def _events(email: str) -> list:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(LoginEventRecord).where(LoginEventRecord.email == email).order_by(LoginEventRecord.id)))
    return _run(go())


def _sessions(user_id: int) -> list:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(UserSessionRecord).where(UserSessionRecord.user_id == user_id)))
    return _run(go())


# --- configuration ---------------------------------------------------------------------------------------------

def test_providers_false_and_routes_404_without_credentials(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_ID", "")
    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_SECRET", "")
    assert client.get("/api/auth/providers").json()["google"] is False
    assert client.get("/api/v1/auth/providers").json()["google"] is False
    assert client.get("/api/auth/google/start", follow_redirects=False).status_code == 404
    assert client.get("/api/auth/google/callback?state=x&code=y", follow_redirects=False).status_code == 404


def test_providers_false_with_only_one_credential(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_SECRET", "")
    assert client.get("/api/auth/providers").json()["google"] is False
    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_ID", "")
    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_SECRET", CLIENT_SECRET)
    assert client.get("/api/auth/providers").json()["google"] is False


def test_providers_false_in_production_without_public_base_url(monkeypatch, configured):
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    assert client.get("/api/auth/providers").json()["google"] is False


def test_providers_true_with_both_credentials(configured):
    body = client.get("/api/auth/providers").json()
    assert body["google"] is True and [p["id"] for p in body["providers"]] == ["google"]
    assert CLIENT_SECRET not in json.dumps(body) and CLIENT_ID not in json.dumps(body)


# --- start -----------------------------------------------------------------------------------------------------

def test_start_redirects_to_google_with_state_nonce_and_pkce(configured):
    response, cookie, params = _start()
    location = response.headers["location"]
    assert location.startswith(google.AUTHORIZATION_URL + "?")
    assert params["client_id"] == [CLIENT_ID]
    assert params["redirect_uri"] == [f"{PUBLIC}/api/auth/google/callback"]
    assert params["response_type"] == ["code"]
    assert params["scope"] == ["openid email profile"]
    assert params["code_challenge_method"] == ["S256"]
    assert len(params["state"][0]) >= 32 and len(params["nonce"][0]) >= 32
    assert CLIENT_SECRET not in location
    flow = google.decode_flow(cookie)
    assert flow is not None and flow.state == params["state"][0] and flow.nonce == params["nonce"][0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(flow.verifier.encode()).digest()).rstrip(b"=").decode()
    assert params["code_challenge"] == [challenge]
    assert flow.verifier not in location                       # only the challenge leaves the server
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie and "path=/api/auth/google" in set_cookie
    assert "max-age=600" in set_cookie
    # Each start is a new attempt.
    _, cookie2, params2 = _start()
    assert params2["state"] != params["state"] and cookie2 != cookie


def test_start_through_the_v1_alias(configured):
    response = client.get("/api/v1/auth/google/start", follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"].startswith(google.AUTHORIZATION_URL)


def test_flow_cookie_is_signed_and_expires(configured, monkeypatch):
    flow = google.new_flow()
    assert google.decode_flow(google.encode_flow(flow)) == flow
    forged = jwt.encode({"purpose": google.FLOW_PURPOSE, "state": "s", "nonce": "n", "verifier": "v", "exp": int(time.time()) + 60},
                        "another-secret-that-is-long-enough-for-hs256", algorithm="HS256")
    assert google.decode_flow(forged) is None
    expired = jwt.encode({"purpose": google.FLOW_PURPOSE, "state": "s", "nonce": "n", "verifier": "v", "exp": int(time.time()) - 5},
                         config.JWT_SECRET_KEY, algorithm="HS256")
    assert google.decode_flow(expired) is None
    other_purpose = jwt.encode({"purpose": "mfa", "state": "s", "nonce": "n", "verifier": "v", "exp": int(time.time()) + 60},
                               config.JWT_SECRET_KEY, algorithm="HS256")
    assert google.decode_flow(other_purpose) is None


# --- callback refusals -----------------------------------------------------------------------------------------

def test_callback_state_mismatch_is_refused(configured, fake):
    _register("gs-state@example.com")
    response = _callback(fake, "gs-state@example.com", state="not-the-state-we-issued")
    assert response.status_code == 302
    loc = _location(response)
    assert loc["origin"] == PUBLIC and loc["path"] == "/account" and loc["query"] == {"social_error": "expired", "provider": "google"}
    assert fake.token_requests == []                           # the code is never exchanged
    assert "atp_refresh" not in response.headers.get("set-cookie", "")


def test_callback_without_the_flow_cookie_is_refused(configured, fake):
    response = _callback(fake, "gs-state@example.com", cookie=None)
    assert _location(response)["query"] == {"social_error": "expired", "provider": "google"}
    assert fake.token_requests == []


def test_callback_cancelled_at_google(configured, fake):
    _, cookie, params = _start()
    response = client.get("/api/auth/google/callback", params={"error": "access_denied", "state": params["state"][0]},
                          headers={"cookie": f"{google.FLOW_COOKIE}={cookie}"}, follow_redirects=False)
    assert _location(response)["query"] == {"social_error": "cancelled", "provider": "google"}


def test_callback_token_exchange_failure(configured, fake):
    _register("gs-exchange@example.com")
    fake.token_status = 400
    response = _callback(fake, "gs-exchange@example.com")
    assert _location(response)["query"] == {"social_error": "failed", "provider": "google"}


def test_token_exchange_sends_secret_verifier_and_redirect(configured, fake):
    _register("gs-form@example.com")
    _callback(fake, "gs-form@example.com")
    form = fake.token_requests[-1]
    assert form["client_id"] == [CLIENT_ID] and form["client_secret"] == [CLIENT_SECRET]
    assert form["code"] == [AUTH_CODE] and form["grant_type"] == ["authorization_code"]
    assert form["redirect_uri"] == [f"{PUBLIC}/api/auth/google/callback"]
    assert len(form["code_verifier"][0]) >= 43


@pytest.mark.parametrize("claims", [
    {"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200},          # expired
    {"aud": "someone-elses-client.apps.googleusercontent.com"},                  # wrong audience
    {"iss": "https://evil.example.com"},                                         # wrong issuer
    {"nonce": "a-nonce-from-another-flow"},                                      # replayed token
    {"azp": "someone-elses-client"},                                             # issued to another party
])
def test_callback_bad_id_token_is_refused(configured, fake, claims):
    _register("gs-bad-token@example.com")
    before = len(_events("gs-bad-token@example.com"))
    fake.claims = claims
    response = _callback(fake, "gs-bad-token@example.com")
    assert _location(response)["query"] == {"social_error": "failed", "provider": "google"}
    assert "atp_refresh" not in response.headers.get("set-cookie", "")
    assert len(_events("gs-bad-token@example.com")) == before


def test_callback_id_token_signed_by_another_key_is_refused(configured, fake):
    _register("gs-forged@example.com")
    fake.sign_with = _OTHER_KEY
    response = _callback(fake, "gs-forged@example.com")
    assert _location(response)["query"] == {"social_error": "failed", "provider": "google"}


def test_callback_unverified_email_is_refused(configured, fake):
    _register("gs-unverified@example.com")
    fake.claims = {"email_verified": False}
    response = _callback(fake, "gs-unverified@example.com")
    assert _location(response)["query"] == {"social_error": "unverified", "provider": "google"}
    assert not any(e.success for e in _events("gs-unverified@example.com") if e.reason == "google")


def test_callback_inactive_user_is_refused(configured, fake):
    _register("gs-inactive@example.com")
    _set_user("gs-inactive@example.com", is_active=False)
    response = _callback(fake, "gs-inactive@example.com")
    assert _location(response)["query"] == {"social_error": "inactive", "provider": "google"}
    assert _events("gs-inactive@example.com")[-1].reason == "inactive"
    assert "atp_refresh" not in response.headers.get("set-cookie", "")


def test_unknown_email_with_self_signup_off_is_refused_with_the_message(configured, fake, monkeypatch):
    _flags(monkeypatch, self_signup=False)
    response = _callback(fake, "gs-stranger@example.com")
    assert _location(response)["query"] == {"social_error": "no_account", "provider": "google"}
    assert _user("gs-stranger@example.com") is None                     # no account was created
    event = _events("gs-stranger@example.com")[-1]
    assert event.success is False and event.reason == "google_no_account"
    assert google_routes.NO_ACCOUNT_MESSAGE == "No account uses this Google e-mail. Ask your organisation owner for an invite."


# --- callback success ------------------------------------------------------------------------------------------

def test_existing_user_is_signed_in_like_the_password_login(configured, fake):
    _register("gs-ok@example.com")
    _link_google("gs-ok@example.com")
    user = _user("gs-ok@example.com")
    before = len(_sessions(user.id))
    response = _callback(fake, "GS-OK@example.com")                     # Google's casing does not matter
    assert response.status_code == 302
    loc = _location(response)
    assert loc["origin"] == PUBLIC and loc["path"] == "/" and loc["query"] == {"social": "ok", "provider": "google"} and loc["fragment"] == ""
    cookies = response.headers.get_list("set-cookie")
    refresh = next(c for c in cookies if c.startswith("atp_refresh="))
    assert "httponly" in refresh.lower() and "samesite=strict" in refresh.lower() and "path=/api" in refresh.lower()
    assert any(c.startswith(f"{google.FLOW_COOKIE}=") and "max-age=0" in c.lower() for c in cookies)   # single use
    assert len(_sessions(user.id)) == before + 1
    event = _events("gs-ok@example.com")[-1]
    assert event.success is True and event.reason == "google" and event.user_id == user.id

    async def audit():
        async with _session_factory() as session:
            return await session.scalar(select(AuditLogRecord).where(AuditLogRecord.user_id == user.id, AuditLogRecord.event == "user_login")
                                        .order_by(AuditLogRecord.id.desc()))
    assert _run(audit()).detail == "google"

    # The cookie is a working session: /auth/refresh hands out an access token for this user.
    token = refresh.split(";", 1)[0].split("=", 1)[1]
    refreshed = client.post("/api/auth/refresh", json={"refresh_token": token})
    assert refreshed.status_code == 200
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {refreshed.json()['access_token']}"})
    assert me.json()["email"] == "gs-ok@example.com"


def test_the_same_flow_cannot_be_used_twice(configured, fake):
    _register("gs-replay@example.com")
    _link_google("gs-replay@example.com")
    _, cookie, params = _start()
    fake.state["email"] = "gs-replay@example.com"  # type: ignore[attr-defined]
    fake.nonce = params["nonce"][0]
    query = {"state": params["state"][0], "code": AUTH_CODE}
    first = client.get("/api/auth/google/callback", params=query, headers={"cookie": f"{google.FLOW_COOKIE}={cookie}"},
                       follow_redirects=False)
    assert _location(first)["query"] == {"social": "ok", "provider": "google"}
    # The browser drops the cookie; a replay of the callback URL without it is refused before any exchange.
    client.cookies.clear()
    exchanges = len(fake.token_requests)
    second = client.get("/api/auth/google/callback", params=query, follow_redirects=False)
    assert _location(second)["query"] == {"social_error": "expired", "provider": "google"}
    assert len(fake.token_requests) == exchanges


def test_mfa_user_goes_to_the_mfa_step(configured, fake):
    from app.auth import mfa
    _register("gs-mfa@example.com")
    _link_google("gs-mfa@example.com")
    user = _user("gs-mfa@example.com")
    secret = mfa.generate_secret()

    async def enable():
        async with _session_factory() as session:
            row = await session.get(User, user.id)
            mfa.store_secret(row, secret)
            row.mfa_enabled = True
            await session.commit()
    _run(enable())
    before = len(_sessions(user.id))
    response = _callback(fake, "gs-mfa@example.com")
    loc = _location(response)
    assert loc["path"] == "/account" and loc["query"] == {"social": "mfa", "provider": "google"}
    assert loc["fragment"].startswith("social_mfa=")
    assert not any(c.startswith("atp_refresh=") for c in response.headers.get_list("set-cookie"))
    assert len(_sessions(user.id)) == before                         # no session before the code
    challenge = loc["fragment"].split("=", 1)[1]
    assert mfa.parse_mfa_token(challenge) == user.id
    import pyotp
    verified = client.post("/api/auth/mfa/verify", json={"mfa_token": challenge, "code": pyotp.TOTP(secret).now()})
    assert verified.status_code == 200 and verified.json()["access_token"]
    assert len(_sessions(user.id)) == before + 1


def test_self_signup_on_creates_an_organisation_for_a_new_email(configured, fake, monkeypatch):
    _flags(monkeypatch, self_signup=True)
    response = _callback(fake, "gs-new@example.com")
    assert _location(response)["query"] == {"social": "new", "provider": "google"}
    user = _user("gs-new@example.com")
    assert user is not None and user.role == "OWNER" and user.email_verified_at is not None
    assert any(c.startswith("atp_refresh=") for c in response.headers.get_list("set-cookie"))


@pytest.mark.parametrize("flag", ["beta_cohort_gate", "legal_pages"])
def test_self_signup_that_needs_the_form_gets_the_finish_step(configured, fake, monkeypatch, flag):
    """M73 SL-3 (was: sent to the form): what only a form collects is asked on the pending "finish" step."""
    _flags(monkeypatch, self_signup=True, **{flag: True})
    email = f"gs-form-{flag}@example.com".replace("_", "-")
    response = _callback(fake, email)
    loc = _location(response)
    assert loc["query"] == {"social": "complete", "provider": "google"} and loc["fragment"].startswith("social_pending=")
    assert _user(email) is None


# --- secrets ---------------------------------------------------------------------------------------------------

def test_secrets_never_in_logs_or_responses(configured, fake, caplog):
    _register("gs-logs@example.com")
    caplog.set_level(logging.DEBUG)
    start = client.get("/api/auth/google/start", follow_redirects=False)
    response = _callback(fake, "gs-logs@example.com")
    providers = client.get("/api/auth/providers")
    id_token = fake.id_tokens[-1]
    for text in (caplog.text, start.text, response.text, response.headers.get("location", ""), providers.text):
        assert CLIENT_SECRET not in text
        assert AUTH_CODE not in text
        assert id_token not in text


def test_access_log_redacts_oauth_query_values():
    line = "GET /api/auth/google/callback?state=abc123&code=4/secret-code&scope=email HTTP/1.1"
    redacted = redact_query(line)
    assert "abc123" not in redacted and "4/secret-code" not in redacted
    assert "state=[redacted]" in redacted and "code=[redacted]" in redacted and "scope=email" in redacted
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4:5", "GET", "/api/auth/google/callback?code=4/secret-code&state=s1", "1.1", 302), None)
    QueryRedactionFilter().filter(record)
    assert "4/secret-code" not in record.getMessage() and "s1" not in record.getMessage().split("?", 1)[1]


def test_jwks_is_cached_between_sign_ins(configured, fake):
    _register("gs-cache@example.com")
    _callback(fake, "gs-cache@example.com")
    _callback(fake, "gs-cache@example.com")
    assert fake.jwks_requests == 1
