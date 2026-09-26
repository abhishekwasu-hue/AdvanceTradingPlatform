"""Per-tenant alert channel configuration: validated shapes, encryption at rest, masking for
the API, and the severity floor that decides what gets queued."""
import json
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator

from app.core.enums import AlertChannelType, NotificationSeverity
from app.db.models import AlertChannelRecord
from app.secrets_store.encryption import decrypt_text, encrypt_text

SEVERITY_RANK = {NotificationSeverity.INFO.value: 0, NotificationSeverity.WARNING.value: 1, NotificationSeverity.CRITICAL.value: 2}


def severity_reaches(severity: str, floor: str) -> bool:
    return SEVERITY_RANK.get(severity, 0) >= SEVERITY_RANK.get(floor, 1)


class TelegramConfig(BaseModel):
    """A bot created with @BotFather and the chat (a user or a group the bot is in) to post to.
    Find the chat id by messaging the bot and reading /getUpdates, or via @userinfobot."""

    bot_token: str = Field(min_length=10, max_length=200)
    chat_id: str = Field(min_length=1, max_length=64)

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
        if value.scheme != "https" and value.host not in ("localhost", "127.0.0.1"):
            raise ValueError("Webhook URL must use https (plain http is allowed only for localhost while testing)")
        return value


ChannelConfig = Union[TelegramConfig, EmailConfig, WebhookConfig]

SECRET_FIELDS = {AlertChannelType.TELEGRAM.value: ("bot_token",), AlertChannelType.EMAIL.value: ("password",),
                 AlertChannelType.WEBHOOK.value: ("secret",)}


def parse_config(channel_type: str, raw: Dict[str, Any]) -> ChannelConfig:
    if channel_type == AlertChannelType.TELEGRAM.value:
        return TelegramConfig.model_validate(raw)
    if channel_type == AlertChannelType.EMAIL.value:
        return EmailConfig.model_validate(raw)
    if channel_type == AlertChannelType.WEBHOOK.value:
        return WebhookConfig.model_validate(raw)
    raise ValueError(f"Unknown alert channel type '{channel_type}'")


def merge_secrets(channel_type: str, incoming: Dict[str, Any], existing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """An update that leaves a secret blank keeps the stored one, so a user can change the
    recipient list without re-typing the SMTP password the API never showed them."""
    merged = dict(incoming)
    if existing:
        for field in SECRET_FIELDS.get(channel_type, ()):
            if not merged.get(field):
                merged[field] = existing.get(field)
    return merged


def encrypt_config(config: ChannelConfig) -> str:
    return encrypt_text(config.model_dump_json())


def decrypt_config(record: AlertChannelRecord) -> ChannelConfig:
    return parse_config(record.channel_type, json.loads(decrypt_text(record.encrypted_config)))


def decrypt_raw(record: AlertChannelRecord) -> Dict[str, Any]:
    return json.loads(decrypt_text(record.encrypted_config))


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
    return {
        "smtp_host": raw.get("smtp_host"), "smtp_port": raw.get("smtp_port"), "username": raw.get("username"),
        "use_tls": raw.get("use_tls", True), "from_address": raw.get("from_address"), "to_addresses": raw.get("to_addresses", []),
        "password_set": bool(raw.get("password")),
    }
