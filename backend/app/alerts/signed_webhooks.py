"""AUD-G2.5 (docs/owner-orders/ATP_ORDER_AUDIT_28OCT.md, flag `alert_webhooks`, needs `ui_v2`): outbound, user-defined,
SIGNED webhooks - one more alert channel (`AlertChannelType.SIGNED_WEBHOOK`), added in Settings › Alerts.

One channel row per organisation holds every member's webhooks (`endpoints`, encrypted at rest with the organisation's
envelope key like every channel config). Each webhook belongs to the member who added it (`user_id`): only he lists,
tests and removes it, and it gets only notifications addressed to him or to the whole organisation. The outbox queues
one delivery per webhook (`address` = the webhook id), so one dead receiver never re-sends to the others, and reuses the
dispatcher's retries with backoff and its dead letter.

Security:
* https only; no user name / password in the URL; a literal private, loopback, link-local, metadata or other
  non-public address is refused when the webhook is added, and the name is resolved and checked then too;
* at send time the name is resolved again, EVERY address must be public, and the request is pinned to the checked
  address (the URL is rebuilt with the IP, the name goes in Host and as the TLS name), so a DNS answer that changes
  after the check (DNS rebinding) cannot reach an internal address; redirects are never followed;
* the secret is generated here, shown once (the create answer), stored only inside the encrypted config;
* the body carries its own `timestamp` and `event_id`, and `X-ATP-Signature: sha256=<hex HMAC-SHA256(secret, raw body)>`
  covers both, so a receiver that checks the signature, the timestamp window and the event id is safe from forgery
  and replay (docs/PUBLIC_API.md "Signed webhooks").

The body is facts only: the notification's own title and message (structure words, written by the producers under the
signals-not-advice rules), its type, severity and ids that are the receiver's own. Nothing here places, blocks or
changes an order.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import socket
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, Field, field_validator

FLAG = "alert_webhooks"
PAGE_FLAG = "ui_v2"
SCHEMA = "atp.webhook/1"
SECRET_PREFIX = "whsec_"
MAX_URL_LENGTH = 2000
LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa", ".lan")
LOCAL_NAMES = {"localhost", "ip6-localhost", "ip6-loopback", "metadata", "metadata.google.internal"}

Resolver = Callable[[str], Awaitable[List[str]]]


class WebhookRefused(ValueError):
    """A destination that may not be called. The message is plain and safe to show (never the secret)."""


async def system_resolver(host: str) -> List[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return []
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


# Tests replace these (a DNS answer that changes between the add and the send; a fake receiver for Send test).
RESOLVER: Resolver = system_resolver
TRANSPORT: Optional[httpx.AsyncBaseTransport] = None


def _ip_or_none(host: str) -> Optional[str]:
    import ipaddress
    try:
        return str(ipaddress.ip_address(host.strip("[]").split("%", 1)[0]))
    except ValueError:
        return None


def address_public(address: str) -> bool:
    from app.chart_analyze.linkfetch import address_allowed  # public unicast only (+ CGNAT, NAT64, 6to4 refused)
    return address_allowed(address)


def checked_url(raw: Any) -> str:
    """The URL as stored, or WebhookRefused. No DNS here (see `check_resolved`)."""
    from app.core.egress import host_allowed
    url = str(raw or "").strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise WebhookRefused("Enter the full https:// address of your receiver")
    try:
        parts = urlsplit(url)
        parts.port                                              # noqa: B018 - raises on a bad port
    except ValueError:
        raise WebhookRefused("This is not a valid web address") from None
    if parts.scheme.lower() != "https":
        raise WebhookRefused("The address must start with https://")
    if parts.username is not None or parts.password is not None:
        raise WebhookRefused("Leave the user name and password out of the address")
    if parts.fragment:
        raise WebhookRefused("Leave the #part out of the address")
    host = (parts.hostname or "").strip().rstrip(".").lower()
    if not host:
        raise WebhookRefused("The address has no host")
    try:
        ascii_host = host.encode("idna").decode("ascii") if _ip_or_none(host) is None else host
    except (UnicodeError, ValueError):
        raise WebhookRefused("The host name is not valid") from None
    literal = _ip_or_none(ascii_host)
    if literal is not None and not address_public(literal):
        raise WebhookRefused("A private, loopback, link-local or metadata address is not allowed")
    if literal is None and (ascii_host in LOCAL_NAMES or ascii_host.endswith(LOCAL_SUFFIXES) or "." not in ascii_host):
        raise WebhookRefused("A local or internal host name is not allowed")
    if not host_allowed(ascii_host):
        raise WebhookRefused("This host is not on the platform's allowed list")
    return url


async def check_resolved(url: str, resolver: Optional[Resolver] = None) -> str:
    """Resolves the URL's host and returns the ONE checked address to connect to; WebhookRefused when it does not
    resolve or when any address it resolves to is not public."""
    checked_url(url)
    host = (urlsplit(url).hostname or "").rstrip(".").lower()
    literal = _ip_or_none(host)
    if literal is not None:
        return literal
    ascii_host = host.encode("idna").decode("ascii")
    addresses = await (resolver or RESOLVER)(ascii_host)
    if not addresses:
        raise WebhookRefused("The receiver's host name could not be found")
    if not all(address_public(a) for a in addresses):
        raise WebhookRefused("The receiver's host name points to a private or internal address")
    return addresses[0]


def new_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def new_id() -> str:
    return secrets.token_hex(8)


class SignedWebhook(BaseModel):
    id: str = Field(min_length=8, max_length=32)
    user_id: int
    label: str = Field(default="", max_length=60)
    url: str
    secret: str = Field(min_length=24, max_length=200)
    event_types: List[str] = Field(default_factory=list, max_length=30)     # empty = every type
    min_severity: Literal["INFO", "WARNING", "CRITICAL", "EMERGENCY"] = "INFO"
    created_at: str = ""

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        try:
            return checked_url(value)
        except WebhookRefused as exc:
            raise ValueError(str(exc)) from exc


class SignedWebhookConfig(BaseModel):
    endpoints: List[SignedWebhook] = Field(default_factory=list, max_length=500)


def wants(endpoint: SignedWebhook, notification: Any) -> bool:
    from app.alerts.channels import severity_reaches
    from app.alerts.push_kinds import addressed_to
    if endpoint.event_types and notification.event_type not in endpoint.event_types:
        return False
    return severity_reaches(notification.severity, endpoint.min_severity) and addressed_to(endpoint.user_id, notification)


def event_id_for(notification_id: Optional[int], channel_id: Optional[int], webhook_id: str) -> str:
    """The same id on every retry of one delivery (the receiver dedupes on it); a test send gets a fresh one."""
    if notification_id is None or channel_id is None:
        return "evt_test_" + uuid.uuid4().hex
    digest = hashlib.sha256(f"{notification_id}:{channel_id}:{webhook_id}".encode()).hexdigest()[:32]
    return "evt_" + digest


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def build_body(notification: Any, *, event_id: str, webhook_id: str, now: datetime, test: bool = False) -> bytes:
    timestamp = int(now.timestamp())
    payload = {
        "schema": SCHEMA, "event_id": event_id, "timestamp": timestamp, "test": test,
        "event_type": notification.event_type, "severity": notification.severity,
        "title": notification.title, "text": notification.message,
        "notification_id": notification.id, "webhook_id": webhook_id, "created_at": _iso(notification.created_at),
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8")


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, signature_header: str, timestamp_header: str, *, now: Optional[datetime] = None,
           window_seconds: int = 300) -> bool:
    """What a receiver does (the reference for docs/PUBLIC_API.md): constant-time compare of the HMAC of the raw body,
    the header timestamp equal to the signed one and inside the window."""
    if not hmac.compare_digest(sign(secret, body), str(signature_header or "")):
        return False
    try:
        signed_ts = int(json.loads(body)["timestamp"])
        header_ts = int(timestamp_header)
    except (ValueError, KeyError, TypeError):
        return False
    current = int((now or datetime.now(timezone.utc)).timestamp())
    return signed_ts == header_ts and abs(current - signed_ts) <= window_seconds


def _timeout() -> httpx.Timeout:
    from app.core.config import ALERT_WEBHOOKS_TIMEOUT_SECONDS
    seconds = float(ALERT_WEBHOOKS_TIMEOUT_SECONDS)
    return httpx.Timeout(connect=seconds, read=seconds, write=seconds, pool=seconds)


async def post(endpoint: SignedWebhook, notification: Any, *, event_id: str, client: Optional[httpx.AsyncClient] = None,
               resolver: Optional[Resolver] = None, now: Optional[datetime] = None, test: bool = False) -> int:
    """One signed POST to the checked, pinned address. Returns the HTTP status; raises on anything but 2xx (the
    message is safe to store: no secret, no response body)."""
    address = await check_resolved(endpoint.url, resolver)
    parts = urlsplit(endpoint.url)
    host = (parts.hostname or "").rstrip(".").lower()
    tls_name = host if _ip_or_none(host) is not None else host.encode("idna").decode("ascii")
    body = build_body(notification, event_id=event_id, webhook_id=endpoint.id, now=now or datetime.now(timezone.utc), test=test)
    timestamp = str(json.loads(body)["timestamp"])
    host_header = tls_name if parts.port in (None, 443) else f"{tls_name}:{parts.port}"
    headers = {"Host": host_header, "Content-Type": "application/json", "User-Agent": "ATP-Webhooks/2.0",
               "X-ATP-Signature": sign(endpoint.secret, body), "X-ATP-Timestamp": timestamp, "X-ATP-Event-Id": event_id,
               "X-ATP-Event": notification.event_type, "X-ATP-Schema": SCHEMA, "Idempotency-Key": event_id}
    pinned = httpx.URL(scheme="https", host=address, port=parts.port, path=parts.path or "/",
                       query=quote(parts.query, safe="=&;%+/?:@!$'()*,~-._").encode("ascii") if parts.query else None)
    owns = client is None
    client = client or httpx.AsyncClient(transport=TRANSPORT, trust_env=False, follow_redirects=False, timeout=_timeout())
    try:
        request = client.build_request("POST", pinned, content=body, headers=headers, extensions={"sni_hostname": tls_name},
                                       timeout=_timeout())
        response = await client.send(request, stream=True, follow_redirects=False)
        await response.aclose()                                  # the receiver's body is never read
    except httpx.TimeoutException:
        raise RuntimeError("The receiver did not answer in time") from None
    except httpx.HTTPError as exc:
        raise RuntimeError(f"The receiver could not be reached ({type(exc).__name__})") from None
    finally:
        if owns:
            await client.aclose()
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"The receiver answered HTTP {response.status_code}")
    return response.status_code


def summary(endpoint: SignedWebhook) -> Dict[str, Any]:
    """What the API shows about one webhook: never its secret."""
    return {"id": endpoint.id, "label": endpoint.label, "url": endpoint.url, "event_types": endpoint.event_types,
            "min_severity": endpoint.min_severity, "created_at": endpoint.created_at}
