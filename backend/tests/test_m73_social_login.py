"""M73 SL-7: provider-generic sign-in - Google (OIDC) and Facebook (OAuth 2.0 + Graph /me) with fake providers (httpx
mock transports: the suite never reaches the network). Happy paths, state / PKCE mismatch, e-mail collision -> link,
Facebook without an e-mail, not configured -> no button, unlink of the last sign-in way refused, the same sign-up rules
as the forms, no token in a log, audit rows."""
import asyncio
import base64
import hashlib
import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlsplit

import httpx
import pyotp
import pytest
from sqlalchemy import select

from app.auth import facebook, google, mfa, social, social_routes
from app.auth import routes as auth_routes
from app.auth.security import password_usable
from app.auth.social_routes import social_start_rate_limit
from app.core import config
from app.db.models import AuditLogRecord, User, UserIdentityRecord
from app.main import app
from tests.test_auth_api import _register, _session_factory, client
from tests.test_google_signin import _KEY, KID, _jwk

PUBLIC = "https://aialgodesk.tech"
G_ID, G_SECRET = "m73-google-client.apps.googleusercontent.com", "m73-google-secret-never-shown"
FB_ID, FB_SECRET = "1234567890123456", "m73-facebook-app-secret-never-shown"
AUTH_CODE = "AQ-one-time-code-from-the-provider"
FB_ACCESS_TOKEN = "EAAG-facebook-user-access-token-never-stored"
PASSWORD = "S3cur3Pass!"

app.dependency_overrides[social_start_rate_limit] = lambda: None


def _run(coro):
    return asyncio.run(coro)


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class FakeProviders:
    """Google's token endpoint + keys and Facebook's token endpoint + /me. Each records the PKCE challenge of the last
    start and refuses a token request whose verifier does not match it (what the real providers do)."""

    def __init__(self) -> None:
        self.person: Dict[str, Any] = {"email": "nobody@example.com", "sub": "s-1", "fb_email": True}
        self.challenge: Optional[str] = None
        self.nonce: Optional[str] = None
        self.token_requests: list = []
        self.me_requests: list = []

    def id_token(self) -> str:
        import jwt
        now = int(time.time())
        claims = {"iss": "https://accounts.google.com", "aud": G_ID, "azp": G_ID, "sub": self.person["sub"],
                  "email": self.person["email"], "email_verified": True, "iat": now, "exp": now + 3600, "nonce": self.nonce}
        return jwt.encode(claims, _KEY, algorithm="RS256", headers={"kid": KID})

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == google.JWKS_URL:
            return httpx.Response(200, json={"keys": [_jwk(_KEY, KID)]})
        if url in (google.TOKEN_URL, facebook.token_endpoint()):
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            if _challenge(form.get("code_verifier", "")) != self.challenge:
                return httpx.Response(400, json={"error": "invalid_grant"})      # PKCE mismatch
            if url == google.TOKEN_URL:
                return httpx.Response(200, json={"id_token": self.id_token(), "access_token": "ya29.not-used"})
            return httpx.Response(200, json={"access_token": FB_ACCESS_TOKEN, "token_type": "bearer"})
        if url.startswith(facebook.me_endpoint()):
            self.me_requests.append({"auth": request.headers.get("authorization"), "url": url})
            if request.headers.get("authorization") != f"Bearer {FB_ACCESS_TOKEN}":
                return httpx.Response(401, json={"error": {"message": "bad token"}})
            body = {"id": self.person["sub"], "name": "Test Person"}
            if self.person.get("fb_email"):
                body["email"] = self.person["email"]
            return httpx.Response(200, json=body)
        return httpx.Response(404)


@pytest.fixture
def fake(monkeypatch):
    for name, value in (("GOOGLE_OAUTH_CLIENT_ID", G_ID), ("GOOGLE_OAUTH_CLIENT_SECRET", G_SECRET),
                        ("FACEBOOK_APP_ID", FB_ID), ("FACEBOOK_APP_SECRET", FB_SECRET),
                        ("PUBLIC_BASE_URL", PUBLIC), ("FRONTEND_URL", PUBLIC)):
        monkeypatch.setattr(config, name, value)
    providers = FakeProviders()

    def http_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(providers.handle))

    monkeypatch.setattr(google, "http_client", http_client)
    monkeypatch.setattr(facebook, "http_client", http_client)
    google.clear_jwks_cache()
    _flags(monkeypatch, self_signup=True)
    yield providers
    google.clear_jwks_cache()


def _flags(monkeypatch, **values: bool) -> None:
    async def flag_enabled(session, name, tenant_id=None):
        return values.get(name, False)

    async def legal_enabled(session, tenant_id=None):
        return values.get("legal_pages", False)
    for module in (social_routes, auth_routes):
        monkeypatch.setattr(module, "flag_enabled", flag_enabled)
        monkeypatch.setattr(module, "legal_enabled", legal_enabled)


def _start(slug: str, **params: str) -> tuple:
    response = client.get(f"/api/auth/{slug}/start", params=params, follow_redirects=False)
    assert response.status_code == 302, response.text
    cookie = response.cookies.get(f"atp_{slug}_flow")
    assert cookie
    return response, cookie, {k: v[0] for k, v in parse_qs(urlsplit(response.headers["location"]).query).items()}


def _login(fake: FakeProviders, slug: str, *, email: Optional[str], sub: str, fb_email: bool = True,
           state: Optional[str] = None, start: Optional[tuple] = None, **params: str) -> Dict[str, Any]:
    """One full round trip: start (records the PKCE challenge / nonce at the fake), then the callback."""
    _, cookie, query = start or _start(slug, **params)
    fake.challenge = query["code_challenge"] if start is None else fake.challenge
    fake.nonce = query.get("nonce")
    fake.person = {"email": email, "sub": sub, "fb_email": fb_email}
    client.cookies.clear()
    response = client.get(f"/api/auth/{slug}/callback", params={"state": state or query["state"], "code": AUTH_CODE},
                          headers={"cookie": f"atp_{slug}_flow={cookie}"}, follow_redirects=False)
    assert response.status_code == 302, response.text
    parts = urlsplit(response.headers["location"])
    return {"path": parts.path, "query": {k: v[0] for k, v in parse_qs(parts.query).items()}, "fragment": parts.fragment,
            "origin": f"{parts.scheme}://{parts.netloc}", "response": response,
            "refresh": next((c for c in response.headers.get_list("set-cookie") if c.startswith("atp_refresh=")), None)}


def _fragment(loc: Dict[str, Any], key: str) -> str:
    assert loc["fragment"].startswith(f"{key}="), loc
    return loc["fragment"].split("=", 1)[1]


def _user(email: str) -> Optional[User]:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(User).where(User.email == email))
    return _run(go())


def _identities(user_id: int) -> list:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(UserIdentityRecord).where(UserIdentityRecord.user_id == user_id)))
    return _run(go())


def _audit(user_id: int, event: str) -> list:
    async def go():
        async with _session_factory() as session:
            return [r.detail for r in await session.scalars(select(AuditLogRecord).where(
                AuditLogRecord.user_id == user_id, AuditLogRecord.event == event).order_by(AuditLogRecord.id))]
    return _run(go())


def _bearer_from(refresh_cookie: str) -> Dict[str, str]:
    token = refresh_cookie.split(";", 1)[0].split("=", 1)[1]
    refreshed = client.post("/api/auth/refresh", json={"refresh_token": token})
    assert refreshed.status_code == 200, refreshed.text
    return {"Authorization": f"Bearer {refreshed.json()['access_token']}"}


# --- SL-1: which buttons ---------------------------------------------------------------------------------------------

def test_not_configured_means_no_button_and_404(monkeypatch):
    for name in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET"):
        monkeypatch.setattr(config, name, "")
    body = client.get("/api/auth/providers").json()
    assert body["providers"] == [] and body["google"] is False and body["facebook"] is False
    for slug in ("google", "facebook"):
        assert client.get(f"/api/auth/{slug}/start", follow_redirects=False).status_code == 404
        assert client.get(f"/api/auth/{slug}/callback?state=x&code=y", follow_redirects=False).status_code == 404


def test_only_facebook_configured_lists_only_facebook(monkeypatch, fake):
    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_SECRET", "")
    body = client.get("/api/auth/providers").json()
    assert [p["id"] for p in body["providers"]] == ["facebook"] and body["google"] is False and body["facebook"] is True
    assert body["providers"][0] == {"id": "facebook", "label": "Facebook", "kind": "oauth2", "start": "/auth/facebook/start"}
    text = str(body)
    assert FB_SECRET not in text and FB_ID not in text and G_SECRET not in text


def test_both_live_in_registry_order(fake):
    assert [p["id"] for p in client.get("/api/auth/providers").json()["providers"]] == ["google", "facebook"]


def test_facebook_start_has_state_pkce_and_no_nonce(fake):
    response, cookie, q = _start("facebook")
    assert response.headers["location"].startswith(facebook.authorization_endpoint() + "?")
    assert q["client_id"] == FB_ID and q["redirect_uri"] == f"{PUBLIC}/api/auth/facebook/callback"
    assert q["scope"] == "email,public_profile" and q["code_challenge_method"] == "S256" and "nonce" not in q
    flow = social.decode_flow(social.get("facebook"), cookie)
    assert flow is not None and flow.flow.state == q["state"] and _challenge(flow.flow.verifier) == q["code_challenge"]
    assert FB_SECRET not in response.headers["location"] and flow.flow.verifier not in response.headers["location"]
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie and "path=/api/auth/facebook" in set_cookie
    # A Google cookie is not a Facebook cookie (each provider signs its own purpose).
    _, g_cookie, _ = _start("google")
    assert social.decode_flow(social.get("facebook"), g_cookie) is None


def test_registry_accepts_a_broker_kind_for_m74():
    provider = social.Provider(id="broker:test", slug="broker-test", kind=social.KIND_BROKER, label="Test broker",
                               configured=lambda: False, callback_path="/api/auth/broker-test/callback",
                               authorization_url=lambda f, r: "", identity_for_code=None)  # type: ignore[arg-type]
    try:
        social.register(provider)
        assert social.get("broker:test") is provider and provider.uses_nonce is False
        assert all(p["id"] != "broker:test" for p in social.public_providers())    # not configured -> no button
    finally:
        social.REGISTRY.pop("broker:test", None)
    with pytest.raises(ValueError):
        social.register(social.Provider(id="x", slug="x", kind="saml", label="X", configured=lambda: False,
                                        callback_path="/x", authorization_url=lambda f, r: "",
                                        identity_for_code=None))  # type: ignore[arg-type]


# --- happy paths ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("slug", ["google", "facebook"])
def test_new_person_signs_up_then_signs_in_again(fake, slug):
    email, sub = f"m73-happy-{slug}@example.com", f"{slug}-happy-1"
    first = _login(fake, slug, email=email, sub=sub)
    assert first["origin"] == PUBLIC and first["path"] == "/" and first["query"] == {"social": "new", "provider": slug}
    assert first["refresh"] and "httponly" in first["refresh"].lower() and "samesite=strict" in first["refresh"].lower()
    user = _user(email)
    assert user.role == "OWNER" and user.email_verified_at is not None and not password_usable(user.hashed_password)
    rows = _identities(user.id)
    assert [(r.provider, r.subject, r.email_at_link) for r in rows] == [(slug, sub, email)]
    assert _audit(user.id, "user_registered") == [f"{email} ({slug})"] and _audit(user.id, "social_link") == [slug]

    again = _login(fake, slug, email=email, sub=sub)
    assert again["query"] == {"social": "ok", "provider": slug} and again["refresh"]
    assert _audit(user.id, "user_login")[-1] == slug                      # SL-4: the sign-in row names the provider
    me = client.get("/api/auth/me", headers=_bearer_from(again["refresh"]))
    assert me.json()["email"] == email


def test_returning_identity_with_two_factor_goes_to_the_code_step(fake):
    email = "m73-mfa@example.com"
    _login(fake, "facebook", email=email, sub="fb-mfa")
    secret = mfa.generate_secret()

    async def enable():
        async with _session_factory() as session:
            row = await session.scalar(select(User).where(User.email == email))
            mfa.store_secret(row, secret)
            row.mfa_enabled = True
            await session.commit()
    _run(enable())
    loc = _login(fake, "facebook", email=email, sub="fb-mfa")
    assert loc["path"] == "/account" and loc["query"] == {"social": "mfa", "provider": "facebook"} and loc["refresh"] is None
    verified = client.post("/api/auth/mfa/verify", json={"mfa_token": _fragment(loc, "social_mfa"), "code": pyotp.TOTP(secret).now()})
    assert verified.status_code == 200 and verified.json()["access_token"]


# --- state / PKCE -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("slug", ["google", "facebook"])
def test_state_mismatch_is_refused_before_any_exchange(fake, slug):
    loc = _login(fake, slug, email="m73-state@example.com", sub="st-1", state="not-the-state-we-issued")
    assert loc["query"] == {"social_error": "expired", "provider": slug}
    assert fake.token_requests == [] and loc["refresh"] is None


@pytest.mark.parametrize("slug", ["google", "facebook"])
def test_pkce_mismatch_is_refused(fake, slug):
    """The provider saw the challenge of attempt A; the browser comes back with attempt B's cookie and state: the
    verifier B does not match challenge A, the provider refuses the code, nobody is signed in."""
    _, _, first = _start(slug)
    fake.challenge = first["code_challenge"]
    second = _start(slug)
    loc = _login(fake, slug, email="m73-pkce@example.com", sub="pk-1", start=second)
    assert loc["query"] == {"social_error": "failed", "provider": slug} and loc["refresh"] is None
    assert len(fake.token_requests) == 1 and _user("m73-pkce@example.com") is None


# --- SL-2: e-mail collision -> link ------------------------------------------------------------------------------------

@pytest.mark.parametrize("slug", ["google", "facebook"])
def test_email_of_an_existing_account_needs_its_password(fake, slug):
    email = f"m73-collide-{slug}@example.com"
    _register(email)
    user = _user(email)
    loc = _login(fake, slug, email=email, sub=f"{slug}-collide")
    assert loc["path"] == "/account" and loc["query"] == {"social": "link", "provider": slug}
    assert loc["refresh"] is None and _identities(user.id) == []           # never a silent sign-in
    token = _fragment(loc, "social_pending")
    info = client.post("/api/auth/social/pending", json={"token": token}).json()
    assert info == {"kind": "link", "provider": slug, "label": social.get(slug).label, "email": email, "mfa": False,
                    "has_password": True}
    wrong = client.post("/api/auth/social/link/confirm", json={"token": token, "password": "not-the-password"})
    assert wrong.status_code == 401 and _identities(user.id) == []
    ok = client.post("/api/auth/social/link/confirm", json={"token": token, "password": PASSWORD})
    assert ok.status_code == 200 and ok.json()["access_token"]
    assert [(r.provider, r.subject) for r in _identities(user.id)] == [(slug, f"{slug}-collide")]
    assert _audit(user.id, "social_link_pending") == [slug] and _audit(user.id, "social_link") == [slug]
    # From now on the provider signs this account in directly.
    assert _login(fake, slug, email=email, sub=f"{slug}-collide")["query"] == {"social": "ok", "provider": slug}


def test_link_confirmation_asks_for_the_two_factor_code(fake):
    email = "m73-collide-mfa@example.com"
    _register(email)
    secret = mfa.generate_secret()

    async def enable():
        async with _session_factory() as session:
            row = await session.scalar(select(User).where(User.email == email))
            mfa.store_secret(row, secret)
            row.mfa_enabled = True
            await session.commit()
    _run(enable())
    token = _fragment(_login(fake, "google", email=email, sub="g-collide-mfa"), "social_pending")
    assert client.post("/api/auth/social/pending", json={"token": token}).json()["mfa"] is True
    no_code = client.post("/api/auth/social/link/confirm", json={"token": token, "password": PASSWORD})
    assert no_code.status_code == 401 and _identities(_user(email).id) == []
    ok = client.post("/api/auth/social/link/confirm", json={"token": token, "password": PASSWORD, "code": pyotp.TOTP(secret).now()})
    assert ok.status_code == 200 and len(_identities(_user(email).id)) == 1


def test_pending_tokens_are_signed_and_typed(fake):
    assert client.post("/api/auth/social/pending", json={"token": "forged.token.value"}).status_code == 400
    signup = social.encode_pending(social.Pending(kind="signup", provider="google", subject="x", email=None, email_verified=False))
    confirm = client.post("/api/auth/social/link/confirm", json={"token": signup, "password": PASSWORD})
    assert confirm.status_code == 400                                         # a sign-up step is not a link step


# --- Facebook without an e-mail -----------------------------------------------------------------------------------------

def test_facebook_without_email_asks_for_one_and_sends_the_verification_link(fake, monkeypatch):
    sent: list = []

    async def send_link(session, user, request):
        sent.append(user.email)
        return {"sent": True}
    monkeypatch.setattr(auth_routes.verification, "send_link", send_link)
    loc = _login(fake, "facebook", email=None, sub="fb-no-email", fb_email=False)
    assert loc["query"] == {"social": "complete", "provider": "facebook"} and loc["refresh"] is None
    token = _fragment(loc, "social_pending")
    info = client.post("/api/auth/social/pending", json={"token": token}).json()
    assert info["needs_email"] is True and info["email"] is None and info["kind"] == "signup"
    missing = client.post("/api/auth/social/complete", json={"token": token})
    assert missing.status_code == 400 and "email_required" in missing.text
    done = client.post("/api/auth/social/complete", json={"token": token, "email": "m73-typed@example.com"})
    assert done.status_code == 201 and done.json()["access_token"]
    user = _user("m73-typed@example.com")
    assert user.email_verified_at is None and sent == ["m73-typed@example.com"]     # verified by the existing link mail
    assert [(r.provider, r.subject, r.email_at_link) for r in _identities(user.id)] == [("facebook", "fb-no-email", None)]
    # The same Facebook account now signs in directly.
    assert _login(fake, "facebook", email=None, sub="fb-no-email", fb_email=False)["query"] == {"social": "ok", "provider": "facebook"}


def test_facebook_without_email_typing_an_existing_accounts_email_links_with_its_password(fake):
    email = "m73-typed-existing@example.com"
    _register(email)
    token = _fragment(_login(fake, "facebook", email=None, sub="fb-typed-existing", fb_email=False), "social_pending")
    answer = client.post("/api/auth/social/complete", json={"token": token, "email": email})
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "link_required"
    link = answer.json()["detail"]["token"]
    assert client.post("/api/auth/social/link/confirm", json={"token": link, "password": "wrong-password"}).status_code == 401
    assert client.post("/api/auth/social/link/confirm", json={"token": link, "password": PASSWORD}).status_code == 200
    assert [r.provider for r in _identities(_user(email).id)] == ["facebook"]


# --- SL-3: the same sign-up rules -------------------------------------------------------------------------------------

def test_self_signup_off_creates_nothing(fake, monkeypatch):
    _flags(monkeypatch, self_signup=False)
    loc = _login(fake, "facebook", email="m73-closed@example.com", sub="fb-closed")
    assert loc["query"] == {"social_error": "no_account", "provider": "facebook"} and _user("m73-closed@example.com") is None


@pytest.mark.parametrize("flag,code", [("beta_cohort_gate", "beta_invite_required"), ("legal_pages", "legal_acceptance_required")])
def test_finish_step_refuses_exactly_like_the_register_form(fake, monkeypatch, flag, code):
    _flags(monkeypatch, self_signup=True, **{flag: True})
    email = f"m73-rules-{flag}@example.com".replace("_", "-")
    form = client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    token = _fragment(_login(fake, "google", email=email, sub=f"g-rules-{flag}"), "social_pending")
    info = client.post("/api/auth/social/pending", json={"token": token}).json()
    assert info["legal"] is (flag == "legal_pages") and info["beta"] is (flag == "beta_cohort_gate")
    finish = client.post("/api/auth/social/complete", json={"token": token})
    assert (finish.status_code, code in finish.text) == (form.status_code, True) and code in form.text
    assert _user(email) is None


def test_social_signup_uses_the_one_sign_up_path(fake, monkeypatch):
    calls: list = []
    original = auth_routes.self_signup_account

    async def spy(*args, **kwargs):
        calls.append(kwargs.get("source"))
        return await original(*args, **kwargs)
    monkeypatch.setattr(social_routes, "self_signup_account", spy)
    _login(fake, "facebook", email="m73-path@example.com", sub="fb-path")
    assert calls == ["facebook"]
    from app.db.models import Tenant

    async def tenant():
        async with _session_factory() as session:
            user = await session.scalar(select(User).where(User.email == "m73-path@example.com"))
            return await session.get(Tenant, user.tenant_id)
    assert _run(tenant()).name == "m73-path@example.com"                          # a new organisation, as the form


def test_join_team_with_google_uses_the_invite(fake):
    owner = _register("m73-owner@example.com")
    from app.db.models import Tenant

    async def pro_plan():           # room for one more member (the seat is counted when the invite is made)
        async with _session_factory() as session:
            user = await session.scalar(select(User).where(User.email == "m73-owner@example.com"))
            (await session.get(Tenant, user.tenant_id)).plan = "pro"
            await session.commit()
    _run(pro_plan())
    invite = client.post("/api/team/invites", headers={"Authorization": f"Bearer {owner}"},
                         json={"email": "m73-member@example.com", "role": "USER"})
    assert invite.status_code == 201, invite.text
    token = parse_qs(urlsplit(invite.json()["invite_url"]).query)["invite"][0]
    loc = _login(fake, "google", email="m73-member@example.com", sub="g-member", intent="invite", invite=token)
    assert loc["query"] == {"social": "new", "provider": "google"} and loc["refresh"]
    member, boss = _user("m73-member@example.com"), _user("m73-owner@example.com")
    assert member.tenant_id == boss.tenant_id and member.role == "USER" and [r.provider for r in _identities(member.id)] == ["google"]
    assert client.get(f"/api/auth/invite/{token}").json()["valid"] is False         # the invite is used up


# --- Settings › Account -------------------------------------------------------------------------------------------------

def test_link_from_settings_then_unlink(fake):
    email = "m73-settings@example.com"
    bearer = {"Authorization": f"Bearer {_register(email)}"}
    listing = client.get("/api/auth/identities", headers=bearer).json()
    assert listing["has_password"] is True and listing["identities"] == []
    assert [(p["id"], p["slug"]) for p in listing["available"]] == [("google", "google"), ("facebook", "facebook")]
    assert client.post("/api/auth/google/link").status_code == 401                  # signed in only
    started = client.post("/api/auth/google/link", headers=bearer)
    assert started.status_code == 200 and started.json()["url"].startswith(google.AUTHORIZATION_URL)
    cookie = started.cookies.get("atp_google_flow")
    q = {k: v[0] for k, v in parse_qs(urlsplit(started.json()["url"]).query).items()}
    fake.challenge, fake.nonce = q["code_challenge"], q["nonce"]
    fake.person = {"email": "another-address@example.com", "sub": "g-settings", "fb_email": True}
    client.cookies.clear()
    back = client.get("/api/auth/google/callback", params={"state": q["state"], "code": AUTH_CODE},
                      headers={"cookie": f"atp_google_flow={cookie}"}, follow_redirects=False)
    assert parse_qs(urlsplit(back.headers["location"]).query) == {"social": ["linked"], "provider": ["google"]}
    listing = client.get("/api/auth/identities", headers=bearer).json()
    assert [(i["provider"], i["email"]) for i in listing["identities"]] == [("google", "another-address@example.com")]
    assert client.delete("/api/auth/identities/google", headers=bearer).status_code == 204   # the password remains
    assert _audit(_user(email).id, "social_unlink") == ["google"]
    assert client.delete("/api/auth/identities/google", headers=bearer).status_code == 404


def test_a_provider_account_linked_elsewhere_is_refused(fake):
    _login(fake, "google", email="m73-first-owner@example.com", sub="g-taken")
    bearer = {"Authorization": f"Bearer {_register('m73-second@example.com')}"}
    started = client.post("/api/auth/google/link", headers=bearer)
    q = {k: v[0] for k, v in parse_qs(urlsplit(started.json()["url"]).query).items()}
    fake.challenge, fake.nonce = q["code_challenge"], q["nonce"]
    fake.person = {"email": "m73-first-owner@example.com", "sub": "g-taken", "fb_email": True}
    client.cookies.clear()
    back = client.get("/api/auth/google/callback", params={"state": q["state"], "code": AUTH_CODE},
                      headers={"cookie": f"atp_google_flow={started.cookies.get('atp_google_flow')}"}, follow_redirects=False)
    assert parse_qs(urlsplit(back.headers["location"]).query)["social_error"] == ["linked_elsewhere"]
    assert _identities(_user("m73-second@example.com").id) == []


def test_unlink_of_the_last_sign_in_way_is_refused(fake):
    email = "m73-only-way@example.com"
    loc = _login(fake, "google", email=email, sub="g-only")
    bearer = _bearer_from(loc["refresh"])
    assert client.get("/api/auth/identities", headers=bearer).json()["has_password"] is False
    refused = client.delete("/api/auth/identities/google", headers=bearer)
    assert refused.status_code == 409 and "only way to sign in" in refused.json()["detail"]
    assert len(_identities(_user(email).id)) == 1
    # With a second way linked, one of them may go.
    async def add_facebook():
        async with _session_factory() as session:
            user = await session.scalar(select(User).where(User.email == email))
            session.add(UserIdentityRecord(user_id=user.id, provider="facebook", subject="fb-only-2", email_at_link=None))
            await session.commit()
    _run(add_facebook())
    assert client.delete("/api/auth/identities/google", headers=bearer).status_code == 204
    assert client.delete("/api/auth/identities/facebook", headers=bearer).status_code == 409


def test_a_provider_made_account_has_no_password_to_guess(fake):
    email = "m73-nopass@example.com"
    _login(fake, "facebook", email=email, sub="fb-nopass")
    for guess in ("", "!", PASSWORD):
        response = client.post("/api/auth/login", json={"email": email, "password": guess})
        assert response.status_code in (401, 422)


# --- SL-4: no token in a log, none stored ------------------------------------------------------------------------------

def test_no_token_or_secret_in_logs_responses_or_rows(fake, caplog):
    caplog.set_level(logging.DEBUG)
    loc = _login(fake, "facebook", email="m73-logs@example.com", sub="fb-logs")
    g = _login(fake, "google", email="m73-logs-g@example.com", sub="g-logs")
    assert fake.me_requests and all(FB_ACCESS_TOKEN not in m["url"] for m in fake.me_requests)   # header, never a URL
    # The server's own log lines (the test client's request lines - "http://testserver/..." - are this test's, not the app's).
    server_log = "\n".join(r.getMessage() for r in caplog.records if "http://testserver" not in r.getMessage())
    texts = [server_log, loc["response"].headers["location"], g["response"].headers["location"],
             client.get("/api/auth/providers").text]
    for text in texts:
        for secret in (FB_SECRET, G_SECRET, AUTH_CODE, FB_ACCESS_TOKEN):
            assert secret not in text

    async def rows():
        async with _session_factory() as session:
            return [(r.provider, r.subject, r.email_at_link) for r in await session.scalars(select(UserIdentityRecord))]
    assert all(FB_ACCESS_TOKEN not in str(row) and AUTH_CODE not in str(row) for row in _run(rows()))
    assert {c.name for c in UserIdentityRecord.__table__.columns} == {"id", "user_id", "provider", "subject", "email_at_link", "created_at"}


def test_callback_shares_the_login_rate_limit():
    from app.auth.routes import login_rate_limit
    for slug in ("google", "facebook"):
        route = next(r for r in social_routes.router.routes if getattr(r, "path", "") == f"/api/auth/{slug}/callback")
        assert any(d.call is login_rate_limit for d in route.dependant.dependencies)
    for path in ("/api/auth/social/link/confirm", "/api/auth/social/complete", "/api/auth/social/pending"):
        route = next(r for r in social_routes.router.routes if getattr(r, "path", "") == path)
        assert any(d.call is login_rate_limit for d in route.dependant.dependencies)


def test_admin_health_lists_which_providers_are_live(fake, monkeypatch):
    from app.admin.console import sign_in_providers
    monkeypatch.setattr(config, "FACEBOOK_APP_SECRET", "")
    rows = sign_in_providers()
    # M74 adds the broker:<id> providers after these two (tests/test_m74_broker_signin.py checks their rows).
    assert [(r["id"], r["live"]) for r in rows if r["kind"] != "broker"] == [("google", True), ("facebook", False)]
    assert FB_ID not in str(rows) and G_SECRET not in str(rows) and G_ID not in str(rows)
