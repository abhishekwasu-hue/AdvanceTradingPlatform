"""GS-1: Google sign-in (OpenID Connect, authorization code flow with PKCE).

Inert until the operator sets GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET (docs/OPEN_QUESTIONS.md GS-1):
`configured()` is false, /api/auth/providers says google: false and the routes answer 404.

The flow (routes in app/auth/google_routes.py):
1. `/api/auth/google/start` makes a fresh `state`, `nonce` and PKCE verifier, keeps them in a signed, short-lived,
   HttpOnly cookie scoped to /api/auth/google (SameSite=Lax: the callback is a top-level navigation from Google), and
   redirects to Google's authorization endpoint.
2. `/api/auth/google/callback` checks that the `state` in the query matches the cookie (login CSRF), exchanges the code
   at Google's token endpoint with the client secret and the PKCE verifier, and verifies the ID token: RS256 signature
   against Google's published keys (cached), issuer, audience = our client id, expiry, the nonce from the cookie, and
   `email_verified`. Only then is the e-mail trusted.

Nothing here logs or returns a code, a token, the verifier or the client secret; errors carry only a fixed short code.
"""
import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import Request

from app.core import config
from app.core.config import JWT_ALGORITHM

AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPES = "openid email profile"
CALLBACK_PATH = "/api/auth/google/callback"

FLOW_COOKIE = "atp_google_flow"
FLOW_COOKIE_PATH = "/api/auth/google"
FLOW_TTL_SECONDS = 600
FLOW_PURPOSE = "google_signin_flow"
HTTP_TIMEOUT_SECONDS = 10.0
JWKS_TTL_SECONDS = 3600
CLOCK_LEEWAY_SECONDS = 60


class GoogleSignInError(Exception):
    """A refusal with a fixed, short code (the sign-in page turns it into plain words). Never carries a secret."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Flow:
    state: str
    nonce: str
    verifier: str


@dataclass(frozen=True)
class GoogleIdentity:
    email: str
    subject: str


def configured() -> bool:
    """Both credentials set, and a public origin to build the redirect URI from (hardened environments need
    PUBLIC_BASE_URL; a dev box may use the address the request arrived on)."""
    if not (config.GOOGLE_OAUTH_CLIENT_ID and config.GOOGLE_OAUTH_CLIENT_SECRET):
        return False
    return bool(config.PUBLIC_BASE_URL) or config.ENVIRONMENT not in config.HARDENED_ENVIRONMENTS


def redirect_uri(request: Request) -> str:
    base = config.PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    return base + CALLBACK_PATH


# --- the flow cookie --------------------------------------------------------------------------------------------

def new_flow() -> Flow:
    return Flow(state=secrets.token_urlsafe(32), nonce=secrets.token_urlsafe(32), verifier=secrets.token_urlsafe(64))


def encode_flow(flow: Flow) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"purpose": FLOW_PURPOSE, "state": flow.state, "nonce": flow.nonce, "verifier": flow.verifier,
         "iat": now, "exp": now + timedelta(seconds=FLOW_TTL_SECONDS)},
        config.JWT_SECRET_KEY, algorithm=JWT_ALGORITHM,
    )


def decode_flow(value: Optional[str]) -> Optional[Flow]:
    if not value:
        return None
    try:
        payload = jwt.decode(value, config.JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM], options={"require": ["exp"]})
    except jwt.PyJWTError:
        return None
    if payload.get("purpose") != FLOW_PURPOSE:
        return None
    try:
        return Flow(state=str(payload["state"]), nonce=str(payload["nonce"]), verifier=str(payload["verifier"]))
    except KeyError:
        return None


def state_matches(flow: Optional[Flow], presented: Optional[str]) -> bool:
    return bool(flow and presented and hmac.compare_digest(flow.state.encode(), presented.encode()))


def code_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def authorization_url(flow: Flow, redirect: str) -> str:
    params = {
        "client_id": config.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPES,
        "state": flow.state,
        "nonce": flow.nonce,
        "code_challenge": code_challenge(flow.verifier),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return f"{AUTHORIZATION_URL}?{urlencode(params)}"


# --- talking to Google ------------------------------------------------------------------------------------------

def http_client() -> httpx.AsyncClient:
    """One place to build the outbound client (tests replace it with a mock transport; no network in tests). The
    destinations are Google's fixed endpoints above, never a URL from a request."""
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS)


_jwks_cache: Dict[str, Any] = {"keys": {}, "fetched_at": 0.0}


def clear_jwks_cache() -> None:
    _jwks_cache["keys"] = {}
    _jwks_cache["fetched_at"] = 0.0


async def _fetch_jwks(client: httpx.AsyncClient) -> Dict[str, Dict[str, Any]]:
    response = await client.get(JWKS_URL)
    if response.status_code != 200:
        raise GoogleSignInError("failed")
    keys = {k["kid"]: k for k in (response.json().get("keys") or []) if isinstance(k, dict) and k.get("kid")}
    _jwks_cache["keys"] = keys
    _jwks_cache["fetched_at"] = time.monotonic()
    return keys


async def _signing_key(client: httpx.AsyncClient, kid: str) -> Dict[str, Any]:
    keys: Dict[str, Dict[str, Any]] = _jwks_cache["keys"]
    fresh = keys and time.monotonic() - float(_jwks_cache["fetched_at"]) < JWKS_TTL_SECONDS
    if not fresh or kid not in keys:              # stale, or Google rotated its keys: fetch once more
        keys = await _fetch_jwks(client)
    if kid not in keys:
        raise GoogleSignInError("failed")
    return keys[kid]


async def exchange_code(client: httpx.AsyncClient, code: str, flow: Flow, redirect: str) -> str:
    """The ID token for this code. Any failure is a plain "failed" (the response body is not logged)."""
    try:
        response = await client.post(TOKEN_URL, data={
            "code": code, "client_id": config.GOOGLE_OAUTH_CLIENT_ID, "client_secret": config.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": flow.verifier,
        })
        body = response.json() if response.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        raise GoogleSignInError("failed") from None
    id_token = body.get("id_token") if isinstance(body, dict) else None
    if not isinstance(id_token, str) or not id_token:
        raise GoogleSignInError("failed")
    return id_token


async def verify_id_token(client: httpx.AsyncClient, id_token: str, nonce: str) -> GoogleIdentity:
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError:
        raise GoogleSignInError("failed") from None
    if header.get("alg") != "RS256" or not header.get("kid"):
        raise GoogleSignInError("failed")
    try:
        jwk = await _signing_key(client, str(header["kid"]))
        key = jwt.PyJWK(jwk, algorithm="RS256")
        claims = jwt.decode(
            id_token, key, algorithms=["RS256"], audience=config.GOOGLE_OAUTH_CLIENT_ID, leeway=CLOCK_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except GoogleSignInError:
        raise
    except (jwt.PyJWTError, httpx.HTTPError, ValueError, KeyError, TypeError):
        raise GoogleSignInError("failed") from None
    if claims.get("iss") not in ISSUERS:
        raise GoogleSignInError("failed")
    azp = claims.get("azp")
    if azp is not None and azp != config.GOOGLE_OAUTH_CLIENT_ID:
        raise GoogleSignInError("failed")
    token_nonce = claims.get("nonce")
    if not isinstance(token_nonce, str) or not hmac.compare_digest(token_nonce.encode(), nonce.encode()):
        raise GoogleSignInError("failed")          # a token minted for another flow (replay)
    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email:
        raise GoogleSignInError("unverified")
    if claims.get("email_verified") not in (True, "true"):
        raise GoogleSignInError("unverified")      # an unverified address never matches an account (takeover)
    return GoogleIdentity(email=email.strip().lower(), subject=str(claims["sub"]))


async def identity_for_code(code: str, flow: Flow, redirect: str) -> GoogleIdentity:
    async with http_client() as client:
        id_token = await exchange_code(client, code, flow, redirect)
        return await verify_id_token(client, id_token, flow.nonce)
