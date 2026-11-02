"""M73 SL-1: Facebook Login (OAuth 2.0 authorization code flow with PKCE and `state`, then Graph `/me`).

Inert until the operator sets FACEBOOK_APP_ID and FACEBOOK_APP_SECRET: `configured()` is false, /api/auth/providers says
facebook: false and the routes answer 404. Facebook is not OpenID Connect here (no ID token, no nonce): the code is
exchanged at the Graph token endpoint with the app secret and the PKCE verifier, the access token reads `/me` once
(fields id, name, email - sent in the Authorization header, never in a URL), and is then dropped. Facebook returns only
a confirmed primary e-mail, and none at all when the person has none or declined it - the sign-in then asks for one
(SL-2). Nothing here logs or returns a code, a token, the verifier or the app secret; errors carry only a fixed code.
"""
import hashlib
import hmac
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import Request

from app.auth.google import Flow, GoogleSignInError, code_challenge
from app.core import config
from app.core.logging_config import install_access_log_redaction

install_access_log_redaction()        # the Graph call's appsecret_proof never reaches an httpx log line

SCOPES = "email,public_profile"
CALLBACK_PATH = "/api/auth/facebook/callback"
HTTP_TIMEOUT_SECONDS = 10.0


def _graph(path: str) -> str:
    return f"https://graph.facebook.com/{config.FACEBOOK_GRAPH_VERSION}{path}"


def authorization_endpoint() -> str:
    return f"https://www.facebook.com/{config.FACEBOOK_GRAPH_VERSION}/dialog/oauth"


def token_endpoint() -> str:
    return _graph("/oauth/access_token")


def me_endpoint() -> str:
    return _graph("/me")


def configured() -> bool:
    if not (config.FACEBOOK_APP_ID and config.FACEBOOK_APP_SECRET):
        return False
    return bool(config.PUBLIC_BASE_URL) or config.ENVIRONMENT not in config.HARDENED_ENVIRONMENTS


def redirect_uri(request: Request) -> str:
    base = config.PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    return base + CALLBACK_PATH


def authorization_url(flow: Flow, redirect: str) -> str:
    params = {
        "client_id": config.FACEBOOK_APP_ID,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPES,
        "state": flow.state,
        "code_challenge": code_challenge(flow.verifier),
        "code_challenge_method": "S256",
    }
    return f"{authorization_endpoint()}?{urlencode(params)}"


def http_client() -> httpx.AsyncClient:
    """Tests replace it with a mock transport (no network). Destinations are the fixed Graph endpoints above."""
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS)


async def exchange_code(client: httpx.AsyncClient, code: str, flow: Flow, redirect: str) -> str:
    """The access token for this code (a POST form, so neither the secret nor the code is in a URL)."""
    try:
        response = await client.post(token_endpoint(), data={
            "client_id": config.FACEBOOK_APP_ID, "client_secret": config.FACEBOOK_APP_SECRET, "redirect_uri": redirect,
            "code": code, "code_verifier": flow.verifier, "grant_type": "authorization_code",
        })
        body = response.json() if response.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        raise GoogleSignInError("failed") from None
    token = body.get("access_token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        raise GoogleSignInError("failed")
    return token


def appsecret_proof(access_token: str) -> str:
    """Review nit 5: HMAC-SHA256 of the user token keyed with the app secret - Graph refuses a token used without it when
    "Require App Secret" is on, so a token stolen elsewhere is useless. Blanked in every log line (logging_config)."""
    return hmac.new(config.FACEBOOK_APP_SECRET.encode(), access_token.encode(), hashlib.sha256).hexdigest()


async def fetch_me(client: httpx.AsyncClient, access_token: str) -> dict[str, Any]:
    try:
        response = await client.get(me_endpoint(), params={"fields": "id,name,email", "appsecret_proof": appsecret_proof(access_token)},
                                    headers={"Authorization": f"Bearer {access_token}"})
        body = response.json() if response.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        raise GoogleSignInError("failed") from None
    if not isinstance(body, dict) or not isinstance(body.get("id"), (str, int)) or not str(body.get("id")):
        raise GoogleSignInError("failed")
    return body


async def identity_for_code(code: str, flow: Flow, redirect: str) -> dict[str, Any]:
    """{"subject", "email" (lower-case, or None), "name"} - the access token does not leave this function."""
    async with http_client() as client:
        access_token = await exchange_code(client, code, flow, redirect)
        me = await fetch_me(client, access_token)
        del access_token
    email = me.get("email")
    email = email.strip().lower() if isinstance(email, str) and "@" in email else None
    name = me.get("name") if isinstance(me.get("name"), str) else None
    return {"subject": str(me["id"]), "email": email, "name": name}
