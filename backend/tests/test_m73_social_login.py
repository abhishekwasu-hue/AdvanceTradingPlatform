"""M73 SL-7: provider-generic sign-in - Google (OIDC) and Facebook (OAuth 2.0 + Graph /me) with fake providers (httpx
mock transports: the suite never reaches the network). Happy paths, state / PKCE mismatch, e-mail collision -> link,
Facebook without an e-mail, not configured -> no button, unlink of the last sign-in way refused, the same sign-up rules
as the forms, no token in a log, audit rows."""
import asyncio
import base64
import hashlib
import logging
import time
from typing import Any
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
PASSWORD = "".join(["S3cur", "3Pass!"])            # the suite's register password, built at runtime (secret scan)
NEW_PASSWORD = "".join(["An0ther", "-Str0ng", "-Pass"])
WRONG_PASSWORD = "".join(["not-the", "-password"])

app.dependency_overrides[social_start_rate_limit] = lambda: None


def _run(coro):
    return asyncio.run(coro)


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class FakeProviders:
    """Google's token endpoint + keys and Facebook's token endpoint + /me. Each records the PKCE challenge of the last
    start and refuses a token request whose verifier does not match it (what the real providers do)."""

    def __init__(self) -> None:
        self.person: dict[str, Any] = {"email": "nobody@example.com", "sub": "s-1", "fb_email": True}
        self.challenge: str | None = None
        self.nonce: str | None = None
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


def _login(fake: FakeProviders, slug: str, *, email: str | None, sub: str, fb_email: bool = True,
           state: str | None = None, start: tuple | None = None, **params: str) -> dict[str, Any]:
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


def _fragment(loc: dict[str, Any], key: str) -> str:
    assert loc["fragment"].startswith(f"{key}="), loc
    return loc["fragment"].split("=", 1)[1]


def _user(email: str) -> User | None:
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


def _security_titles(user_id: int, everything: bool = False) -> list:
    from app.db.models import NotificationRecord

    async def go():
        async with _session_factory() as session:
            return [r.title for r in await session.scalars(select(NotificationRecord).where(
                NotificationRecord.user_id == user_id, NotificationRecord.event_type == "SECURITY").order_by(NotificationRecord.id))
                    if everything or "linked" in r.title]
    return _run(go())


def _bearer_from(refresh_cookie: str) -> dict[str, str]:
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
    wrong = client.post("/api/auth/social/link/confirm", json={"token": token, "password": WRONG_PASSWORD})
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

def _mailbox(monkeypatch) -> list:
    sent: list = []

    async def send(to, subject, body, headers=None):
        sent.append({"to": to, "subject": subject, "body": body})
        return True
    from app.notifications import mailer
    monkeypatch.setattr(mailer, "send", send)
    return sent


def _verify_token(mail: dict) -> str:
    link = next(w for w in mail["body"].split() if "social_verify=" in w)
    return parse_qs(urlsplit(link).query)["social_verify"][0]


def test_facebook_without_email_creates_nothing_until_the_mailed_link_is_opened(fake, monkeypatch):
    """Review blocker 1: a typed address is not trusted - no account, no session, no link until its inbox confirms."""
    mails = _mailbox(monkeypatch)
    loc = _login(fake, "facebook", email=None, sub="fb-no-email", fb_email=False)
    assert loc["query"] == {"social": "complete", "provider": "facebook"} and loc["refresh"] is None
    token = _fragment(loc, "social_pending")
    info = client.post("/api/auth/social/pending", json={"token": token}).json()
    assert info["needs_email"] is True and info["email"] is None and info["kind"] == "signup"
    missing = client.post("/api/auth/social/complete", json={"token": token})
    assert missing.status_code == 400 and "email_required" in missing.text
    answer = client.post("/api/auth/social/complete", json={"token": token, "email": "m73-typed@example.com"})
    assert answer.status_code == 202 and answer.json()["status"] == "check_email"
    assert "access_token" not in answer.text and "atp_refresh" not in answer.headers.get("set-cookie", "")
    assert _user("m73-typed@example.com") is None                                    # nothing exists yet
    assert [m["to"] for m in mails] == ["m73-typed@example.com"]
    # The finish step is spent: it cannot mail again or be replayed.
    assert client.post("/api/auth/social/complete", json={"token": token, "email": "m73-other@example.com"}).status_code == 400
    # Before the link is opened the Facebook account signs nobody in.
    assert _login(fake, "facebook", email=None, sub="fb-no-email", fb_email=False)["query"]["social"] == "complete"
    verify = _verify_token(mails[0])
    done = client.post("/api/auth/social/verify", json={"token": verify})
    assert done.status_code == 201 and done.json()["access_token"]
    user = _user("m73-typed@example.com")
    assert user.email_verified_at is not None                                         # the link proved the inbox
    assert [(r.provider, r.subject, r.email_at_link) for r in _identities(user.id)] == [("facebook", "fb-no-email", None)]
    assert client.post("/api/auth/social/verify", json={"token": verify}).status_code == 400   # single use
    assert _login(fake, "facebook", email=None, sub="fb-no-email", fb_email=False)["query"] == {"social": "ok", "provider": "facebook"}


def test_a_confirmation_link_for_an_address_taken_meanwhile_makes_nothing(fake, monkeypatch):
    mails = _mailbox(monkeypatch)
    token = _fragment(_login(fake, "facebook", email=None, sub="fb-raced", fb_email=False), "social_pending")
    assert client.post("/api/auth/social/complete", json={"token": token, "email": "m73-raced@example.com"}).status_code == 202
    _register("m73-raced@example.com")                                                # the real owner signs up first
    answer = client.post("/api/auth/social/verify", json={"token": _verify_token(mails[0])})
    assert answer.status_code == 409 and _identities(_user("m73-raced@example.com").id) == []


def test_password_reset_removes_a_provider_linked_while_the_email_was_unverified(fake, monkeypatch):
    """Review blocker 1, the old shape of the attack: an account on someone else's address with an attacker's provider
    linked before the address was verified. The real owner's reset removes that link."""
    email = "m73-victim@example.com"
    bearer = {"Authorization": f"Bearer {_register(email)}"}                          # unverified address
    user = _user(email)

    async def plant():
        async with _session_factory() as session:
            session.add(UserIdentityRecord(user_id=user.id, provider="facebook", subject="fb-attacker", email_at_link=None))
            await session.commit()
    _run(plant())
    assert [i["provider"] for i in client.get("/api/auth/identities", headers=bearer).json()["identities"]] == ["facebook"]
    mails = _mailbox(monkeypatch)
    assert client.post("/api/auth/password/forgot", json={"email": email}).status_code == 202
    link = next(w for w in mails[-1]["body"].split() if "reset=" in w)
    token = parse_qs(urlsplit(link).query)["reset"][0]
    assert "This account can also sign in with: Facebook." in mails[-1]["body"]
    info = client.get(f"/api/auth/password/reset/{token}").json()
    assert info["linked"] == ["Facebook"]                                             # the reset page names it
    assert client.post(f"/api/auth/password/reset/{token}", json={"password": NEW_PASSWORD}).status_code == 200
    assert _identities(user.id) == []
    assert _audit(user.id, "social_unlink") == ["facebook (password reset)"]
    loc = _login(fake, "facebook", email=None, sub="fb-attacker", fb_email=False)
    assert loc["refresh"] is None and loc["query"]["social"] == "complete"            # it signs nobody in any more


def test_email_verification_removes_a_provider_linked_before_it(fake):
    email = "m73-verify-drop@example.com"
    _register(email)
    user = _user(email)

    async def plant_and_issue():
        async with _session_factory() as session:
            session.add(UserIdentityRecord(user_id=user.id, provider="facebook", subject="fb-early", email_at_link=None))
            session.add(UserIdentityRecord(user_id=user.id, provider="google", subject="g-same", email_at_link=email))
            row = await session.get(User, user.id)
            from app.auth import verification
            token, _ = await verification.issue(session, row)
            await session.commit()
            return token
    token = _run(plant_and_issue())
    assert client.post(f"/api/auth/verify-email/{token}").status_code == 200
    # Facebook (no proven address) goes; Google, which verified this very address itself, stays.
    assert [r.provider for r in _identities(user.id)] == ["google"]
    assert _audit(user.id, "social_unlink") == ["facebook (email verified)"]


def test_the_reset_mail_names_the_linked_providers(fake, monkeypatch):
    mails = _mailbox(monkeypatch)
    email = "m73-reset-mail@example.com"
    _login(fake, "google", email=email, sub="g-reset-mail")
    assert client.post("/api/auth/password/forgot", json={"email": email}).status_code == 202
    assert mails and "This account can also sign in with: Google." in mails[-1]["body"]


def test_facebook_without_email_typing_an_existing_accounts_email_links_with_its_password(fake):
    email = "m73-typed-existing@example.com"
    _register(email)
    token = _fragment(_login(fake, "facebook", email=None, sub="fb-typed-existing", fb_email=False), "social_pending")
    answer = client.post("/api/auth/social/complete", json={"token": token, "email": email})
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "link_required"
    link = answer.json()["detail"]["token"]
    assert client.post("/api/auth/social/link/confirm", json={"token": link, "password": WRONG_PASSWORD}).status_code == 401
    assert client.post("/api/auth/social/link/confirm", json={"token": link, "password": PASSWORD}).status_code == 200
    assert client.post("/api/auth/social/link/confirm", json={"token": link, "password": PASSWORD}).status_code == 400  # spent
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


def test_join_team_needs_the_invited_email_at_the_provider(fake):
    owner = _register("m73-owner-2@example.com")
    from app.db.models import Tenant

    async def pro_plan():
        async with _session_factory() as session:
            user = await session.scalar(select(User).where(User.email == "m73-owner-2@example.com"))
            (await session.get(Tenant, user.tenant_id)).plan = "pro"
            await session.commit()
    _run(pro_plan())
    invite = client.post("/api/team/invites", headers={"Authorization": f"Bearer {owner}"},
                         json={"email": "m73-invited@example.com", "role": "USER"})
    token = parse_qs(urlsplit(invite.json()["invite_url"]).query)["invite"][0]
    other = _login(fake, "google", email="m73-someone-else@example.com", sub="g-else", intent="invite", invite=token)
    assert other["query"] == {"social_error": "invite_email_mismatch", "provider": "google"} and other["refresh"] is None
    no_email = _login(fake, "facebook", email=None, sub="fb-invite", fb_email=False, intent="invite", invite=token)
    assert no_email["query"] == {"social_error": "invite_email_mismatch", "provider": "facebook"}
    assert _user("m73-someone-else@example.com") is None and _user("m73-invited@example.com") is None
    assert client.get(f"/api/auth/invite/{token}").json()["valid"] is True               # the invite is still open


# --- Settings › Account -------------------------------------------------------------------------------------------------

def test_link_from_settings_then_unlink(fake):
    email = "m73-settings@example.com"
    bearer = {"Authorization": f"Bearer {_register(email)}"}
    listing = client.get("/api/auth/identities", headers=bearer).json()
    assert listing["has_password"] is True and listing["identities"] == []
    assert [(p["id"], p["slug"]) for p in listing["available"]] == [("google", "google"), ("facebook", "facebook")]
    assert client.post("/api/auth/google/link", json={"password": PASSWORD}).status_code == 401   # signed in only
    wrong = client.post("/api/auth/google/link", headers=bearer, json={"password": WRONG_PASSWORD})
    assert wrong.status_code == 401 and "atp_google_flow" not in wrong.headers.get("set-cookie", "")   # review 3
    started = client.post("/api/auth/google/link", headers=bearer, json={"password": PASSWORD})
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
    assert _security_titles(_user(email).id) == ["Google was linked to sign in to your account",
                                                 "Google was unlinked from your account"]
    assert client.delete("/api/auth/identities/google", headers=bearer).status_code == 404


def test_a_provider_account_linked_elsewhere_is_refused(fake):
    _login(fake, "google", email="m73-first-owner@example.com", sub="g-taken")
    bearer = {"Authorization": f"Bearer {_register('m73-second@example.com')}"}
    started = client.post("/api/auth/google/link", headers=bearer, json={"password": PASSWORD})
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


def test_a_state_is_spent_once_server_side(fake):
    """Review nit 4: replaying the callback with the same cookie and state is refused before any exchange."""
    _, cookie, q = _start("facebook")
    fake.challenge, fake.nonce = q["code_challenge"], None
    fake.person = {"email": "m73-once@example.com", "sub": "fb-once", "fb_email": True}
    query = {"state": q["state"], "code": AUTH_CODE}
    first = client.get("/api/auth/facebook/callback", params=query, headers={"cookie": f"atp_facebook_flow={cookie}"}, follow_redirects=False)
    assert parse_qs(urlsplit(first.headers["location"]).query)["social"] == ["new"]
    exchanges = len(fake.token_requests)
    client.cookies.clear()
    again = client.get("/api/auth/facebook/callback", params=query, headers={"cookie": f"atp_facebook_flow={cookie}"}, follow_redirects=False)
    assert parse_qs(urlsplit(again.headers["location"]).query)["social_error"] == ["expired"]
    assert len(fake.token_requests) == exchanges


def test_graph_me_carries_the_appsecret_proof_and_no_log_line_shows_it(fake, caplog):
    import hmac as _hmac
    caplog.set_level(logging.DEBUG)
    _login(fake, "facebook", email="m73-proof@example.com", sub="fb-proof")
    proof = _hmac.new(FB_SECRET.encode(), FB_ACCESS_TOKEN.encode(), hashlib.sha256).hexdigest()
    assert parse_qs(urlsplit(fake.me_requests[-1]["url"]).query)["appsecret_proof"] == [proof]
    assert proof not in caplog.text


def test_a_relative_frontend_url_keeps_providers_off_in_production(fake, monkeypatch):
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    monkeypatch.setattr(config, "FRONTEND_URL", "/")
    assert client.get("/api/auth/providers").json()["providers"] == []
    assert client.get("/api/auth/google/start", follow_redirects=False).status_code == 404
    monkeypatch.setattr(config, "FRONTEND_URL", PUBLIC)
    assert [p["id"] for p in client.get("/api/auth/providers").json()["providers"]] == ["google", "facebook"]


def test_poc_typed_address_link_does_not_survive_the_owners_password_reset(fake, monkeypatch):
    """Re-review blocker (the exact PoC): the attacker pre-registers a password account on the victim's address (never
    verified), signs in with a Facebook account that gives no e-mail, types that address and confirms the link with
    his own password. The real owner then resets the password: the Facebook link must be gone and sign nobody in."""
    victim = "m73-poc-victim@example.com"
    _register(victim)                                                    # the attacker's password, the victim's address
    token = _fragment(_login(fake, "facebook", email=None, sub="fb-poc-attacker", fb_email=False), "social_pending")
    answer = client.post("/api/auth/social/complete", json={"token": token, "email": victim})
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "link_required"
    assert client.post("/api/auth/social/complete", json={"token": token, "email": victim}).status_code == 400  # spent
    link = answer.json()["detail"]["token"]
    info = client.post("/api/auth/social/pending", json={"token": link}).json()
    assert info["mfa"] is None and info["has_password"] is None          # nothing said about a typed address's account
    assert client.post("/api/auth/social/link/confirm", json={"token": link, "password": PASSWORD}).status_code == 200
    user = _user(victim)
    assert [(r.provider, r.email_at_link) for r in _identities(user.id)] == [("facebook", None)]   # unproven: not stored
    assert _login(fake, "facebook", email=None, sub="fb-poc-attacker", fb_email=False)["query"]["social"] == "ok"
    # The real owner resets the password from his inbox.
    mails = _mailbox(monkeypatch)
    assert client.post("/api/auth/password/forgot", json={"email": victim}).status_code == 202
    reset = parse_qs(urlsplit(next(w for w in mails[-1]["body"].split() if "reset=" in w)).query)["reset"][0]
    assert client.post(f"/api/auth/password/reset/{reset}", json={"password": NEW_PASSWORD}).status_code == 200
    assert _identities(user.id) == []
    after = _login(fake, "facebook", email=None, sub="fb-poc-attacker", fb_email=False)
    assert after["refresh"] is None and after["query"]["social"] == "complete"
    assert any("Facebook unlinked" in t for t in _security_titles(user.id))   # re-review c: the owner is told


def test_a_link_after_verification_without_a_proven_address_goes_on_reset(fake, monkeypatch):
    """Re-review: every link without a proven address is dropped by the owner's reset, even one made after verifying."""
    email = "m73-late-link@example.com"
    _register(email)
    user = _user(email)
    _set_verified(user.id)

    async def plant():
        async with _session_factory() as session:
            session.add(UserIdentityRecord(user_id=user.id, provider="facebook", subject="fb-late", email_at_link=None))
            await session.commit()
    _run(plant())
    mails = _mailbox(monkeypatch)
    client.post("/api/auth/password/forgot", json={"email": email})
    reset = parse_qs(urlsplit(next(w for w in mails[-1]["body"].split() if "reset=" in w)).query)["reset"][0]
    assert client.post(f"/api/auth/password/reset/{reset}", json={"password": NEW_PASSWORD}).status_code == 200
    assert _identities(user.id) == []


def test_the_confirmation_mail_says_the_provider_is_linked_and_the_new_account_is_told(fake, monkeypatch):
    mails = _mailbox(monkeypatch)
    token = _fragment(_login(fake, "facebook", email=None, sub="fb-told", fb_email=False), "social_pending")
    assert client.post("/api/auth/social/complete", json={"token": token, "email": "m73-told@example.com"}).status_code == 202
    assert "links that Facebook account to it" in mails[0]["body"]
    assert client.post("/api/auth/social/verify", json={"token": _verify_token(mails[0])}).status_code == 201
    assert "Facebook is linked to your new account" in _security_titles(_user("m73-told@example.com").id, everything=True)


def _set_verified(user_id: int) -> None:
    from datetime import UTC, datetime

    async def go():
        async with _session_factory() as session:
            row = await session.get(User, user_id)
            row.email_verified_at = datetime.now(UTC)
            await session.commit()
    _run(go())


def _reset_via_mail(monkeypatch, email: str) -> None:
    mails = _mailbox(monkeypatch)
    assert client.post("/api/auth/password/forgot", json={"email": email}).status_code == 202
    reset = parse_qs(urlsplit(next(w for w in mails[-1]["body"].split() if "reset=" in w)).query)["reset"][0]
    assert client.post(f"/api/auth/password/reset/{reset}", json={"password": NEW_PASSWORD}).status_code == 200


def test_a_settings_link_started_before_a_reset_cannot_finish_after_it(fake, monkeypatch):
    """Re-review 3 (high): the attacker starts Settings › Link with the password he knows; the owner resets; the
    callback that follows links nothing."""
    email = "m73-link-race@example.com"
    bearer = {"Authorization": f"Bearer {_register(email)}"}
    started = client.post("/api/auth/facebook/link", headers=bearer, json={"password": PASSWORD})
    assert started.status_code == 200
    cookie = started.cookies.get("atp_facebook_flow")
    q = {k: v[0] for k, v in parse_qs(urlsplit(started.json()["url"]).query).items()}
    _reset_via_mail(monkeypatch, email)                                    # the owner takes the account back
    fake.challenge, fake.nonce = q["code_challenge"], None
    fake.person = {"email": None, "sub": "fb-race", "fb_email": False}
    client.cookies.clear()
    back = client.get("/api/auth/facebook/callback", params={"state": q["state"], "code": AUTH_CODE},
                      headers={"cookie": f"atp_facebook_flow={cookie}"}, follow_redirects=False)
    assert parse_qs(urlsplit(back.headers["location"]).query) == {"social_error": ["expired"], "provider": ["facebook"]}
    assert _identities(_user(email).id) == []


def test_a_pending_link_minted_before_a_reset_is_dead_after_it(fake, monkeypatch):
    email = "m73-pending-race@example.com"
    _register(email)
    token = _fragment(_login(fake, "google", email=email, sub="g-pending-race"), "social_pending")
    _reset_via_mail(monkeypatch, email)
    assert client.post("/api/auth/social/pending", json={"token": token}).status_code == 400
    assert client.post("/api/auth/social/link/confirm", json={"token": token, "password": NEW_PASSWORD}).status_code == 400
    assert _identities(_user(email).id) == []


def test_a_typed_step_never_says_the_account_has_no_password(fake):
    """Re-review 3 (low): an account made by a provider (no password) answers a typed link step exactly like a wrong
    password - same bcrypt work, 401 "Wrong password." - never 409 "no password yet"."""
    email = "m73-typed-nopass@example.com"
    _login(fake, "google", email=email, sub="g-typed-nopass")              # a provider-made account, no password
    token = _fragment(_login(fake, "facebook", email=None, sub="fb-typed-nopass", fb_email=False), "social_pending")
    link = client.post("/api/auth/social/complete", json={"token": token, "email": email}).json()["detail"]["token"]
    answer = client.post("/api/auth/social/link/confirm", json={"token": link, "password": WRONG_PASSWORD})
    assert answer.status_code == 401 and answer.json()["detail"] == "Wrong password."
    assert [r.provider for r in _identities(_user(email).id)] == ["google"]


def test_the_typed_link_token_names_no_account(fake):
    """Review 4 (low): the token is readable by anyone who holds it - it carries the typed address only."""
    import jwt as _jwt
    email = "m73-typed-noid@example.com"
    _register(email)
    token = _fragment(_login(fake, "facebook", email=None, sub="fb-noid", fb_email=False), "social_pending")
    link = client.post("/api/auth/social/complete", json={"token": token, "email": email}).json()["detail"]["token"]
    claims = _jwt.decode(link, options={"verify_signature": False})
    assert claims.get("user_id") is None and "sub" not in claims
    assert client.post("/api/auth/social/pending", json={"token": link}).json()["kind"] == "link"
    assert client.post("/api/auth/social/link/confirm", json={"token": link, "password": PASSWORD}).status_code == 200
    assert [r.provider for r in _identities(_user(email).id)] == ["facebook"]


def test_a_link_made_by_the_confirmation_mail_does_not_survive_the_owners_reset(fake, monkeypatch):
    """Review 4 (medium): the victim (or a mail scanner) opens the confirmation link the attacker caused; the account
    exists with the attacker's Facebook. The owner's password reset removes that link."""
    mails = _mailbox(monkeypatch)
    victim = "m73-preaccount@example.com"
    token = _fragment(_login(fake, "facebook", email=None, sub="fb-preaccount", fb_email=False), "social_pending")
    assert client.post("/api/auth/social/complete", json={"token": token, "email": victim}).status_code == 202
    verify = _verify_token(mails[0])
    info = client.post("/api/auth/social/pending", json={"token": verify}).json()
    assert info == {"kind": "verify", "provider": "facebook", "label": "Facebook", "email": victim}   # what the button does
    assert _user(victim) is None                                                  # showing the page creates nothing
    assert client.post("/api/auth/social/verify", json={"token": verify}).status_code == 201
    user = _user(victim)
    assert [(r.provider, r.email_at_link) for r in _identities(user.id)] == [("facebook", None)]
    _reset_via_mail(monkeypatch, victim)
    assert _identities(user.id) == []
    assert _login(fake, "facebook", email=None, sub="fb-preaccount", fb_email=False)["refresh"] is None
