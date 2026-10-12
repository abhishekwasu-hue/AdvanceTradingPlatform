"""Per-tenant alert channel configuration: validated shapes, encryption at rest, masking for
the API, and the severity floor that decides what gets queued."""
import json
import re
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, EmailStr, Field, HttpUrl, TypeAdapter, ValidationError, field_validator, model_validator

from app.core.egress import EgressBlocked, check_url_literal
from app.core.enums import AlertChannelType, NotificationSeverity
from app.db.models import AlertChannelRecord
from app.secrets_store.encryption import decrypt_text, encrypt_text
from app.secrets_store.envelope import PURPOSE_ALERT_CHANNEL

SEVERITY_RANK = {NotificationSeverity.INFO.value: 0, NotificationSeverity.WARNING.value: 1, NotificationSeverity.CRITICAL.value: 2,
                 NotificationSeverity.EMERGENCY.value: 3}


def severity_reaches(severity: str, floor: str) -> bool:
    return SEVERITY_RANK.get(severity, 0) >= SEVERITY_RANK.get(floor, 1)


TELEGRAM_INBOUND_FIELDS = ("inbound_enabled", "allowed_chat_ids", "inbound_secret", "approvers")   # Phase BE / P0.8: set only by telegram_inbound.service.configure


class TelegramApprover(BaseModel):
    """P0.8 / A6: one Telegram account allowed to decide (and ask) for one platform user. `telegram_user_id` is the
    sender's `from.id`, not a chat id - in a group every member shares the chat id."""

    telegram_user_id: str = Field(min_length=1, max_length=32)
    user_id: int
    email: str = Field(default="", max_length=200)


class TelegramConfig(BaseModel):
    """A bot created with @BotFather and the chat (a user or a group the bot is in) to post to.
    Find the chat id by messaging the bot and reading /getUpdates, or via @userinfobot."""

    bot_token: str = Field(min_length=10, max_length=200)
    chat_id: str = Field(min_length=1, max_length=64)
    # Phase BE: inbound commands and approve/reject buttons from whitelisted chats. Off by default;
    # `inbound_secret` is the value Telegram echoes in X-Telegram-Bot-Api-Secret-Token (set by the
    # platform when inbound is enabled, never typed by a person, never returned by the API).
    inbound_enabled: bool = False
    allowed_chat_ids: List[str] = Field(default_factory=list, max_length=10)
    inbound_secret: Optional[str] = Field(default=None, max_length=128)
    # P0.8 / A6: who may press Approve/Reject and ask account questions. Empty = legacy private-chat mode: only the
    # sender whose Telegram user id *is* the configured chat id (a private chat) acts, as the organisation's owner.
    approvers: List[TelegramApprover] = Field(default_factory=list, max_length=10)

    @field_validator("bot_token")
    @classmethod
    def _token_shape(cls, value: str) -> str:
        if ":" not in value:
            raise ValueError("Telegram bot token looks wrong - expected the '123456:ABC-...' form from @BotFather")
        return value.strip()


class EmailConfig(BaseModel):
    smtp_host: str = Field(min_length=1, max_length=255)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    username: Optional[str] = Field(default=None, max_length=255)
    password: Optional[str] = Field(default=None, max_length=255)
    use_tls: bool = True
    from_address: EmailStr
    to_addresses: List[EmailStr] = Field(min_length=1, max_length=10)

    @field_validator("smtp_host")
    @classmethod
    def _public_host(cls, value: str) -> str:
        # The same egress policy as the gateway URLs (P0.2 / S5): a host name or a public address, never a private,
        # loopback or link-local one in production / staging; the resolved address is checked again on send.
        host = value.strip().lower().rstrip(".")
        if not _SMTP_HOST.fullmatch(host):
            raise ValueError("smtp_host must be a host name or an IP address")
        try:
            check_url_literal(f"https://[{host}]" if ":" in host else f"https://{host}")
        except EgressBlocked as exc:
            raise ValueError(f"SMTP host refused: {exc}") from exc
        return host


class WebhookConfig(BaseModel):
    """Phase K4: a JSON POST to the tenant's own endpoint (their bot, Zapier, n8n, a Slack relay).
    Every delivery is signed: `X-ATP-Signature: sha256=<hex HMAC-SHA256(secret, raw body)>` plus
    `X-ATP-Timestamp`, so the receiver can reject forgeries and replays. `secret` is never
    shown again after it is stored (only whether it is set)."""

    url: HttpUrl
    secret: str = Field(min_length=16, max_length=200)
    event_types: List[str] = Field(default_factory=list, max_length=30)   # empty = every event type
    # S3b (ADR-0022): "atp" = the versioned atp.notification/1 body (with an `alert` block for screen alerts);
    # "chartink" = the Chartink-shaped body for tools that already read Chartink webhooks (screen alerts only).
    payload_format: Literal["atp", "chartink"] = "atp"

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: HttpUrl) -> HttpUrl:
        # P0.2 / S5: https, no private/loopback destination in production/staging, optional allowlist.
        try:
            check_url_literal(str(value))
        except EgressBlocked as exc:
            raise ValueError(f"Webhook URL refused: {exc}") from exc
        return value

    @field_validator("url", mode="before")
    @classmethod
    def _plain_url(cls, value: Any) -> Any:
        return plain_gateway_url(value)


class PushSubscription(BaseModel):
    """What `PushManager.subscribe()` returns in the browser, plus a label so the user recognises
    the device. The keys are per-device secrets (they decrypt the messages) - stored encrypted."""

    endpoint: HttpUrl
    p256dh: str = Field(min_length=80, max_length=120)
    auth: str = Field(min_length=20, max_length=30)
    label: str = Field(default="browser", max_length=60)


class PushConfig(BaseModel):
    """Phase O3: one channel, many devices. Saving with `subscription` appends a device, saving
    with `remove_endpoint` drops one; stale endpoints (404/410 from the push service) are pruned
    on delivery."""

    subscriptions: List[PushSubscription] = Field(min_length=1, max_length=20)
    # PW3: which kinds of notification this organisation's devices get (app/alerts/deeplink.py PUSH_CATEGORIES);
    # None = all. EMERGENCY always goes through.
    categories: Optional[List[str]] = Field(default=None, max_length=10)

    @field_validator("categories")
    @classmethod
    def _known_categories(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        from app.alerts.deeplink import PUSH_CATEGORIES
        if value is None:
            return None
        unknown = [c for c in value if c not in PUSH_CATEGORIES]
        if unknown:
            raise ValueError(f"Unknown push categories: {', '.join(unknown)}")
        return sorted(set(value))


def e164_numbers(value: List[str]) -> List[str]:
    clean = []
    for number in value:
        digits = number.strip().replace(" ", "").replace("-", "")
        if not digits.lstrip("+").isdigit() or len(digits.lstrip("+")) < 8:
            raise ValueError(f"'{number}' is not a phone number (use E.164, e.g. +919812345678)")
        clean.append(digits)
    return clean


# RFC 9110 field-name token; a value holds printable ASCII, spaces and tabs only - never CR / LF / NUL (header injection)
# and never a non-ASCII character (httpx cannot encode it, which would stop the send for the remaining recipients).
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,100}$")
_HEADER_VALUE = re.compile(r"^[\t\x20-\x7e]{0,2000}$")
# Framing / routing headers the HTTP client owns: a user value would smuggle a second request or retarget this one.
RESERVED_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "te", "trailer", "upgrade", "keep-alive",
                    "proxy-connection", "proxy-authorization"}
MAX_HEADERS = 20
_SMTP_HOST = re.compile(r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$|^[0-9a-f:.]{2,45}$")


def valid_headers(value: Dict[str, str]) -> Dict[str, str]:
    """Gateway headers as typed, refused when a name is not a token or a value carries a line break / control char."""
    if len(value) > MAX_HEADERS:
        raise ValueError(f"at most {MAX_HEADERS} headers")
    for name, val in value.items():
        if not _HEADER_NAME.fullmatch(str(name)):
            raise ValueError(f"header name {str(name)[:40]!r} is not a valid HTTP header name")
        if str(name).lower() in RESERVED_HEADERS:
            raise ValueError(f"header {name!r} is set by the platform and cannot be configured")
        if not isinstance(val, str) or not _HEADER_VALUE.fullmatch(val):
            raise ValueError(f"header {name!r} must be printable ASCII without line breaks or control characters")
    return value


def valid_content_type(value: str) -> str:
    """The Content-Type header the gateway gets: printable ASCII, no line break (header injection)."""
    if not re.fullmatch(r"[\x21-\x7e][\x20-\x7e]{0,79}", value):
        raise ValueError("content_type must be printable ASCII without line breaks")
    return value


def plain_gateway_url(value: Any) -> Any:
    """Refuse a backslash or a control character in a gateway URL before it is parsed: WHATWG parsing (pydantic, httpx)
    reads `\\` as `/`, so `https://evil.example\\@api.example.com/` means evil.example to them and api.example.com to
    other parsers - the stored-secret check must never disagree with the client that sends."""
    if isinstance(value, str) and ("\\" in value or any(ord(c) < 0x21 or ord(c) == 0x7f for c in value)):
        raise ValueError("the URL must not contain a backslash, a space or a control character")
    return value


_URL = TypeAdapter(HttpUrl)


def url_origin(url: Any) -> Optional[Tuple[str, str, int]]:
    """Scheme, host and port as the sending client sees them: parsed by the same WHATWG parser that validated the URL
    on save (pydantic), not by urllib (which reads `\\` differently). None when it is not a valid gateway URL."""
    try:
        plain_gateway_url(str(url or ""))
        parsed = _URL.validate_python(str(url or ""))
    except (ValueError, ValidationError):
        return None
    return parsed.scheme.lower(), (parsed.host or "").lower().rstrip("."), int(parsed.port or 0)


def same_origin(a: Any, b: Any) -> bool:
    """Scheme, host and port of two URLs match (the scheme's default port when none is given)."""
    origin = url_origin(a)
    return origin is not None and origin == url_origin(b)


class SmsConfig(BaseModel):
    """Phase O3: a request template for any HTTP SMS gateway, so no vendor SDK is baked in.
    `body_template` is JSON (or form text) with `{to}`, `{text}`, `{title}`, `{severity}`
    placeholders; `headers` carry the gateway's auth (write-only). Presets for MSG91 and Twilio are
    in docs/OPERATIONS.md 1.6e."""

    url: HttpUrl
    method: str = Field(default="POST", pattern="^(POST|GET)$")
    headers: Dict[str, str] = Field(default_factory=dict)
    content_type: str = Field(default="application/json", max_length=80)
    body_template: str = Field(min_length=2, max_length=2000)
    to_numbers: List[str] = Field(min_length=1, max_length=10)
    max_length: int = Field(default=300, ge=60, le=1000)
    # S10 (flag alerts_whatsapp_dlt): India's DLT registration (TRAI TCCCPR 2018) - the principal entity id, the
    # registered content template id and the sender header, filled into `{dlt_entity_id}` / `{dlt_template_id}` /
    # `{sender_id}` in the body template. Optional; a template that uses one must set it.
    dlt_entity_id: Optional[str] = Field(default=None, max_length=40, pattern=r"^[0-9A-Za-z]+$")
    dlt_template_id: Optional[str] = Field(default=None, max_length=40, pattern=r"^[0-9A-Za-z]+$")
    sender_id: Optional[str] = Field(default=None, max_length=11, pattern=r"^[A-Za-z0-9]+$")

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: HttpUrl) -> HttpUrl:
        try:
            check_url_literal(str(value))
        except EgressBlocked as exc:
            raise ValueError(f"SMS gateway URL refused: {exc}") from exc
        return value

    @field_validator("url", mode="before")
    @classmethod
    def _plain_url(cls, value: Any) -> Any:
        return plain_gateway_url(value)

    @field_validator("headers")
    @classmethod
    def _headers(cls, value: Dict[str, str]) -> Dict[str, str]:
        return valid_headers(value)

    @model_validator(mode="after")
    def _dlt_fields_set(self) -> "SmsConfig":
        for field in DLT_FIELDS:
            if "{" + field + "}" in self.body_template and not getattr(self, field):
                raise ValueError(f"body_template uses {{{field}}} but {field} is not set")
        return self

    def uses_dlt(self) -> bool:
        return any(getattr(self, f) for f in DLT_FIELDS)

    @field_validator("content_type")
    @classmethod
    def _content_type(cls, value: str) -> str:
        return valid_content_type(value)

    @field_validator("to_numbers")
    @classmethod
    def _numbers(cls, value: List[str]) -> List[str]:
        return e164_numbers(value)

    @field_validator("body_template")
    @classmethod
    def _has_text(cls, value: str) -> str:
        if "{text}" not in value and "{title}" not in value:
            raise ValueError("body_template must contain {text} or {title} so the alert is actually sent")
        return value


class WhatsAppConfig(BaseModel):
    """S10 seam (flag `alerts_whatsapp_dlt`): a WhatsApp Business gateway (Meta Cloud API, Gupshup, Twilio, ...) through a
    request template like SMS - no vendor SDK. WhatsApp allows a business-initiated message only as a PRE-APPROVED
    template to a number that OPTED IN: `template_name` (and `language`) name it, `{template}` / `{language}` fill it in,
    `{title}` / `{text}` are its parameters, and `opt_in_confirmed` must be true (the organisation confirms it holds the
    recipients' opt-in). Plan-gated to paid plans (each message costs money)."""

    url: HttpUrl
    headers: Dict[str, str] = Field(default_factory=dict)
    content_type: str = Field(default="application/json", max_length=80)
    body_template: str = Field(min_length=2, max_length=3000)
    template_name: str = Field(min_length=1, max_length=512, pattern=r"^[a-z0-9_]+$")
    language: str = Field(default="en", pattern=r"^[a-z]{2}(_[A-Z]{2})?$")
    to_numbers: List[str] = Field(min_length=1, max_length=10)
    opt_in_confirmed: bool                              # required: the organisation confirms it holds every recipient's opt-in
    max_length: int = Field(default=300, ge=60, le=1000)

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: HttpUrl) -> HttpUrl:
        try:
            check_url_literal(str(value))
        except EgressBlocked as exc:
            raise ValueError(f"WhatsApp gateway URL refused: {exc}") from exc
        return value

    @field_validator("url", mode="before")
    @classmethod
    def _plain_url(cls, value: Any) -> Any:
        return plain_gateway_url(value)

    @field_validator("content_type")
    @classmethod
    def _content_type(cls, value: str) -> str:
        return valid_content_type(value)

    @field_validator("to_numbers")
    @classmethod
    def _numbers(cls, value: List[str]) -> List[str]:
        return e164_numbers(value)

    @field_validator("headers")
    @classmethod
    def _headers(cls, value: Dict[str, str]) -> Dict[str, str]:
        return valid_headers(value)

    @field_validator("body_template")
    @classmethod
    def _template(cls, value: str) -> str:
        if "{template}" not in value:
            raise ValueError("body_template must contain {template}: WhatsApp sends business messages as approved templates only")
        return value

    @field_validator("opt_in_confirmed")
    @classmethod
    def _opt_in(cls, value: bool) -> bool:
        if not value:
            raise ValueError("confirm that every recipient opted in to WhatsApp messages from your organisation (opt_in_confirmed)")
        return value


DLT_FIELDS: Tuple[str, ...] = ("dlt_entity_id", "dlt_template_id", "sender_id")
ChannelConfig = Union[TelegramConfig, EmailConfig, WebhookConfig, PushConfig, SmsConfig, WhatsAppConfig]

SECRET_FIELDS = {AlertChannelType.TELEGRAM.value: ("bot_token", "inbound_secret"), AlertChannelType.EMAIL.value: ("password",),
                 AlertChannelType.WEBHOOK.value: ("secret",), AlertChannelType.SMS.value: ("headers",),
                 AlertChannelType.WHATSAPP.value: ("headers",)}


def parse_config(channel_type: str, raw: Dict[str, Any]) -> ChannelConfig:
    if channel_type == AlertChannelType.TELEGRAM.value:
        return TelegramConfig.model_validate(raw)
    if channel_type == AlertChannelType.EMAIL.value:
        return EmailConfig.model_validate(raw)
    if channel_type == AlertChannelType.WEBHOOK.value:
        return WebhookConfig.model_validate(raw)
    if channel_type == AlertChannelType.PUSH.value:
        return PushConfig.model_validate(raw)
    if channel_type == AlertChannelType.SMS.value:
        return SmsConfig.model_validate(raw)
    if channel_type == AlertChannelType.WHATSAPP.value:
        return WhatsAppConfig.model_validate(raw)
    raise ValueError(f"Unknown alert channel type '{channel_type}'")


def merge_push(incoming: Dict[str, Any], existing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """PUSH upserts are device operations: `subscription` adds (or refreshes, by endpoint),
    `remove_endpoint` drops; a bare `subscriptions` list replaces everything."""
    current = list((existing or {}).get("subscriptions") or [])
    # PW3: the category preference is kept unless this request sets it.
    categories = incoming["categories"] if "categories" in incoming else (existing or {}).get("categories")
    keep = {} if categories is None else {"categories": categories}
    if "subscriptions" in incoming:
        return {"subscriptions": incoming["subscriptions"], **keep}
    added = incoming.get("subscription")
    if added:
        endpoint = str(added.get("endpoint", ""))
        current = [s for s in current if str(s.get("endpoint")) != endpoint] + [added]
    remove = incoming.get("remove_endpoint")
    if remove:
        current = [s for s in current if str(s.get("endpoint")) != str(remove)]
    return {"subscriptions": current, **keep}


def merge_secrets(channel_type: str, incoming: Dict[str, Any], existing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """An update that leaves a secret blank keeps the stored one, so a user can change the
    recipient list without re-typing the SMTP password the API never showed them."""
    merged = dict(incoming)
    if existing:
        moved = secret_destination_changed(channel_type, merged, existing)
        for field in SECRET_FIELDS.get(channel_type, ()):
            if not merged.get(field):
                if moved and field in ("headers", "password"):
                    # The destination moved (another gateway scheme / host / port, another SMTP server, port or login, or
                    # TLS turned off): the stored write-only secret - an API key, a bearer token, the SMTP password - is
                    # never sent there; it must be typed again.
                    merged[field] = {} if field == "headers" else None
                    continue
                merged[field] = existing.get(field)
    return merged


def secret_destination_changed(channel_type: str, incoming: Dict[str, Any], existing: Dict[str, Any]) -> bool:
    """True when an update would send a stored secret somewhere (or in a way) it was not entered for."""
    if channel_type == AlertChannelType.EMAIL.value:
        def server(raw: Dict[str, Any]) -> Tuple[str, int, str]:
            return (str(raw.get("smtp_host") or "").strip().lower().rstrip("."), int(raw.get("smtp_port") or 587),
                    str(raw.get("username") or "").strip())
        tls_dropped = existing.get("use_tls", True) is not False and incoming.get("use_tls", True) is False
        return server(incoming) != server(existing) or tls_dropped
    return "url" in incoming and not same_origin(incoming.get("url"), existing.get("url"))


def encrypt_config(config: ChannelConfig, tenant_id: Optional[int] = None) -> str:
    return encrypt_text(config.model_dump_json(), tenant_id, PURPOSE_ALERT_CHANNEL)


def decrypt_config(record: AlertChannelRecord) -> ChannelConfig:
    return parse_config(record.channel_type, json.loads(decrypt_text(record.encrypted_config, PURPOSE_ALERT_CHANNEL, record.tenant_id)))


def decrypt_raw(record: AlertChannelRecord) -> Dict[str, Any]:
    return json.loads(decrypt_text(record.encrypted_config, PURPOSE_ALERT_CHANNEL, record.tenant_id))


def masked_summary(record: AlertChannelRecord) -> Dict[str, Any]:
    """What the API returns about a channel: enough to recognise it, never a secret."""
    try:
        raw = decrypt_raw(record)
    except ValueError:
        return {"error": "stored config cannot be decrypted (encryption key rotated?)"}
    if record.channel_type == AlertChannelType.TELEGRAM.value:
        token = raw.get("bot_token", "")
        return {"chat_id": raw.get("chat_id"), "bot_token_hint": f"{token[:4]}…{token[-3:]}" if len(token) > 8 else "set"}
    if record.channel_type == AlertChannelType.WEBHOOK.value:
        return {"url": raw.get("url"), "event_types": raw.get("event_types", []), "secret_set": bool(raw.get("secret"))}
    if record.channel_type == AlertChannelType.PUSH.value:
        subs = raw.get("subscriptions", [])
        return {"devices": [{"label": s.get("label", "browser"), "endpoint_hint": str(s.get("endpoint", ""))[:40] + "…",
                             "endpoint": s.get("endpoint")} for s in subs], "count": len(subs),
                "categories": raw.get("categories")}
    if record.channel_type == AlertChannelType.SMS.value:
        return {"url": raw.get("url"), "method": raw.get("method", "POST"), "content_type": raw.get("content_type"),
                "body_template": raw.get("body_template"), "to_numbers": raw.get("to_numbers", []),
                "headers_set": sorted((raw.get("headers") or {}).keys()), "max_length": raw.get("max_length", 300),
                **{f: raw.get(f) for f in DLT_FIELDS if raw.get(f)}}
    if record.channel_type == AlertChannelType.WHATSAPP.value:
        return {"url": raw.get("url"), "content_type": raw.get("content_type"), "body_template": raw.get("body_template"),
                "template_name": raw.get("template_name"), "language": raw.get("language", "en"), "to_numbers": raw.get("to_numbers", []),
                "opt_in_confirmed": bool(raw.get("opt_in_confirmed")), "headers_set": sorted((raw.get("headers") or {}).keys()),
                "max_length": raw.get("max_length", 300)}
    return {
        "smtp_host": raw.get("smtp_host"), "smtp_port": raw.get("smtp_port"), "username": raw.get("username"),
        "use_tls": raw.get("use_tls", True), "from_address": raw.get("from_address"), "to_addresses": raw.get("to_addresses", []),
        "password_set": bool(raw.get("password")),
    }
