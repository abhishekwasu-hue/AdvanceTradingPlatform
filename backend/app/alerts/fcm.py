"""B8: phone notifications through Firebase Cloud Messaging (HTTP v1), with no new dependency.

FCM v1 needs an OAuth 2.0 access token minted from a Firebase service account: an RS256 JWT assertion signed with the
account's private key (`cryptography`), exchanged at Google's token endpoint (`httpx`), cached until shortly before it
expires. The message then goes to `POST https://fcm.googleapis.com/v1/projects/<project>/messages:send`.

Configuration (server `.env` only, never git or the APK): `FCM_SERVICE_ACCOUNT_JSON_BASE64` (the key file, base64 on
one line) or `FCM_SERVICE_ACCOUNT_FILE` (a path), and optionally `FCM_PROJECT_ID`. Without a key `configured()` is
false and nothing is sent: the dispatcher records the delivery as SUPPRESSED, reason `not_configured`.

Tests replace the sender with `set_sender(fake)`: a fake receives exactly the message dict that would be POSTed.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.core import config

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
FCM_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
SEND_TIMEOUT_SECONDS = 10.0
NOT_SET_UP = "Phone notifications are not set up on this server yet."


class TokenGone(Exception):
    """FCM says this registration token no longer exists (the app was removed or its data cleared): drop the phone."""


class FcmError(RuntimeError):
    """Any other refusal - the message is safe to store (no token, no key, no response body)."""


Sender = Callable[[Dict[str, Any]], Awaitable[None]]
_sender_override: Optional[Sender] = None
_access: Dict[str, Any] = {"token": None, "expires": 0.0}


def set_sender(sender: Optional[Sender]) -> None:
    """Tests (and only tests) put a fake sender here; None puts the real one back."""
    global _sender_override
    _sender_override = sender


def service_account() -> Optional[Dict[str, Any]]:
    """The service account key from the environment, or None (missing or unreadable - never raises, never logs it)."""
    raw = getattr(config, "FCM_SERVICE_ACCOUNT_JSON_BASE64", "") or ""
    path = getattr(config, "FCM_SERVICE_ACCOUNT_FILE", "") or ""
    try:
        if raw:
            data = json.loads(base64.b64decode(raw.strip(), validate=False).decode())
        elif path:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        else:
            return None
    except (OSError, ValueError, binascii.Error, UnicodeDecodeError):
        logger.warning("The FCM service account key could not be read - phone notifications stay off")
        return None
    if not isinstance(data, dict) or not data.get("client_email") or not data.get("private_key"):
        return None
    return data


def project_id(account: Optional[Dict[str, Any]] = None) -> str:
    explicit = (getattr(config, "FCM_PROJECT_ID", "") or "").strip()
    if explicit:
        return explicit
    account = account if account is not None else service_account()
    return str((account or {}).get("project_id") or "")


def configured() -> bool:
    """True when a fake sender is installed (tests) or the server has a usable key and a project id."""
    if _sender_override is not None:
        return True
    account = service_account()
    return account is not None and bool(project_id(account))


def status_line() -> str:
    if configured():
        return "Phone notifications (Android app): FCM key set - the mobile_push flag decides who gets them."
    return "Phone notifications (Android app): FCM not set (optional; FCM_SERVICE_ACCOUNT_JSON_BASE64) - nothing is sent to phones."


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def assertion(account: Dict[str, Any], now: Optional[int] = None) -> str:
    """The signed RS256 JWT the token endpoint exchanges for an access token (RFC 7523)."""
    issued = int(now or time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"iss": account["client_email"], "scope": SCOPE, "aud": TOKEN_URL, "iat": issued, "exp": issued + 3600}
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    key = serialization.load_pem_private_key(str(account["private_key"]).encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise FcmError("The FCM service account key is not an RSA key")
    signature = key.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64url(signature)}"


async def _access_token(client: httpx.AsyncClient, account: Dict[str, Any]) -> str:
    if _access["token"] and float(_access["expires"]) - 60 > time.time():
        return str(_access["token"])
    response = await client.post(TOKEN_URL, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                                   "assertion": assertion(account)})
    if response.status_code >= 300:
        raise FcmError(f"Google refused the FCM key (HTTP {response.status_code})")
    body = response.json()
    _access["token"] = body.get("access_token")
    _access["expires"] = time.time() + float(body.get("expires_in") or 3000)
    if not _access["token"]:
        raise FcmError("Google returned no access token for the FCM key")
    return str(_access["token"])


def _gone(response: httpx.Response) -> bool:
    """FCM v1: a token that no longer exists answers 404 (NOT_FOUND / UNREGISTERED), or 400 with UNREGISTERED."""
    if response.status_code == 404:
        return True
    try:
        error = (response.json() or {}).get("error") or {}
    except ValueError:
        return False
    codes = {str(error.get("status") or "")}
    for detail in error.get("details") or []:
        if isinstance(detail, dict):
            codes.add(str(detail.get("errorCode") or ""))
    return bool(codes & {"UNREGISTERED", "NOT_FOUND"})


async def send(message: Dict[str, Any], client: Optional[httpx.AsyncClient] = None) -> None:
    """Sends one v1 message (`{"message": {...}}`). Raises TokenGone for a dead token, FcmError otherwise."""
    if _sender_override is not None:
        await _sender_override(message)
        return
    account = service_account()
    project = project_id(account)
    if account is None or not project:
        raise FcmError(NOT_SET_UP)
    owns = client is None
    client = client or httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS)
    try:
        token = await _access_token(client, account)
        response = await client.post(FCM_URL.format(project=project), json=message, headers={"Authorization": f"Bearer {token}"})
    finally:
        if owns:
            await client.aclose()
    if _gone(response):
        raise TokenGone("the phone is no longer registered")
    if response.status_code == 401:
        _access["token"] = None                       # an expired or revoked access token: mint a new one on the retry
    if response.status_code >= 300:
        raise FcmError(f"FCM answered HTTP {response.status_code}")


def build_message(token: str, *, title: str, body: str, path: str, kind: str, notification_id: Optional[int],
                  quiet: bool) -> Dict[str, Any]:
    """The v1 message: a short fixed notification, a data block with only the path, the id and the kind (MA-7), and
    the Android channel of the kind. Inside quiet hours it is delivered without sound (channel `quiet`), never dropped."""
    return {"message": {
        "token": token,
        "notification": {"title": title, "body": body},
        "data": {"path": path, "kind": kind, "notification_id": str(notification_id or "")},
        "android": {
            "priority": "normal" if quiet else "high",
            "notification": {"channel_id": "quiet" if quiet else kind, "default_sound": not quiet,
                             "notification_priority": "PRIORITY_LOW" if quiet else "PRIORITY_HIGH"},
        },
    }}


__all__ = ["TokenGone", "FcmError", "set_sender", "configured", "send", "build_message", "assertion", "service_account",
           "project_id", "status_line", "NOT_SET_UP"]
