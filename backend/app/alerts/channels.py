"""Per-tenant alert channel configuration: validated shapes, encryption at rest, masking for
the API, and the severity floor that decides what gets queued."""
import json
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator

from app.core.egress import EgressBlocked, check_url_literal
from app.core.enums import AlertChannelType, NotificationSeverity
from app.db.models import AlertChannelRecord
from app.secrets_store.encryption import decrypt_text, encrypt_text
from app.secrets_store.envelope import PURPOSE_ALERT_CHANNEL

SEVERITY_RANK = {NotificationSeverity.INFO.value: 0, NotificationSeverity.WARNING.value: 1, NotificationSeverity.CRITICAL.value: 2,
                 NotificationSeverity.EMERGENCY.value: 3}


def severity_reaches(severity: str, floor: str) -> bool:
    return SEVERITY_RANK.get(severity, 0) >= SEVERITY_RANK.get(floor, 1)


TELEGRAM_INBOUND_FIELDS = ("inbound_enabled", "allowed_chat_ids", "inbound_secret")   # Phase BE: set only by telegram_inbound.service.configure


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


class WebhookConfig(BaseModel):
    """Phase K4: a JSON POST to the tenant's own endpoint (their bot, Zapier, n8n, a Slack relay).
    Every delivery is signed: `X-ATP-Signature: sha256=<hex HMAC-SHA256(secret, raw body)>` plus
    `X-ATP-Timestamp`, so the receiver can reject forgeries and replays. `secret` is never
    shown again after it is stored (only whether it is set)."""

    url: HttpUrl
    secret: str = Field(min_length=16, max_length=200)
    event_types: List[str] = Field(default_factory=list, max_length=30)   # empty = every event type

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: HttpUrl) -> HttpUrl:
        # P0.2 / S5: https, no private/loopback destination in production/staging, optional allowlist.
        try:
            check_url_literal(str(value))
        except EgressBlocked as exc:
            raise ValueError(f"Webhook URL refused: {exc}") from exc
        return value


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

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: HttpUrl) -> HttpUrl:
        try:
            check_url_literal(str(value))
        except EgressBlocked as exc:
            raise ValueError(f"SMS gateway URL refused: {exc}") from exc
        return value

    @field_validator("to_numbers")
    @classmethod
    def _numbers(cls, value: List[str]) -> List[str]:
        clean = []
        for number in value:
            digits = number.strip().replace(" ", "").replace("-", "")
            if not digits.lstrip("+").isdigit() or len(digits.lstrip("+")) < 8:
                raise ValueError(f"'{number}' is not a phone number (use E.164, e.g. +919812345678)")
            clean.append(digits)
        return clean

    @field_validator("body_template")
    @classmethod
    def _has_text(cls, value: str) -> str:
        if "{text}" not in value and "{title}" not in value:
            raise ValueError("body_template must contain {text} or {title} so the alert is actually sent")
        return value


ChannelConfig = Union[TelegramConfig, EmailConfig, WebhookConfig, PushConfig, SmsConfig]

SECRET_FIELDS = {AlertChannelType.TELEGRAM.value: ("bot_token", "inbound_secret"), AlertChannelType.EMAIL.value: ("password",),
                 AlertChannelType.WEBHOOK.value: ("secret",), AlertChannelType.SMS.value: ("headers",)}


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
    raise ValueError(f"Unknown alert channel type '{channel_type}'")


def merge_push(incoming: Dict[str, Any], existing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """PUSH upserts are device operations: `subscription` adds (or refreshes, by endpoint),
    `remove_endpoint` drops; a bare `subscriptions` list replaces everything."""
    current = list((existing or {}).get("subscriptions") or [])
    if "subscriptions" in incoming:
        return {"subscriptions": incoming["subscriptions"]}
    added = incoming.get("subscription")
    if added:
        endpoint = str(added.get("endpoint", ""))
        current = [s for s in current if str(s.get("endpoint")) != endpoint] + [added]
    remove = incoming.get("remove_endpoint")
    if remove:
        current = [s for s in current if str(s.get("endpoint")) != str(remove)]
    return {"subscriptions": current}


def merge_secrets(channel_type: str, incoming: Dict[str, Any], existing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """An update that leaves a secret blank keeps the stored one, so a user can change the
    recipient list without re-typing the SMTP password the API never showed them."""
    merged = dict(incoming)
    if existing:
        for field in SECRET_FIELDS.get(channel_type, ()):
            if not merged.get(field):
                merged[field] = existing.get(field)
    return merged


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
                             "endpoint": s.get("endpoint")} for s in subs], "count": len(subs)}
    if record.channel_type == AlertChannelType.SMS.value:
        return {"url": raw.get("url"), "method": raw.get("method", "POST"), "content_type": raw.get("content_type"),
                "body_template": raw.get("body_template"), "to_numbers": raw.get("to_numbers", []),
                "headers_set": sorted((raw.get("headers") or {}).keys()), "max_length": raw.get("max_length", 300)}
    return {
        "smtp_host": raw.get("smtp_host"), "smtp_port": raw.get("smtp_port"), "username": raw.get("username"),
        "use_tls": raw.get("use_tls", True), "from_address": raw.get("from_address"), "to_addresses": raw.get("to_addresses", []),
        "password_set": bool(raw.get("password")),
    }
