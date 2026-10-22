"""UX2 fix 5 ("Telegram in one tap", docs/design/UX_AUDIT_FULL.md problem 7): the platform's own Telegram bot.

One bot for every organisation, created once by the operator with @BotFather (`ATP_TELEGRAM_BOT_TOKEN`). A trader taps
"Connect Telegram": the app makes a one-time code and opens `t.me/<bot>?start=<code>`; the trader presses Start in
Telegram, Telegram sends `/start <code>` to the bot, and the code links that private chat as the organisation's
Telegram alert channel (`TelegramConfig.platform_bot`). No bot token or chat id is typed by anybody.

* The code: a `NotificationLinkRecord` with `channel = CHANNEL` (the table of S3b-2's per-user links, so no migration).
  Only its SHA-256 is stored, it lasts `links.LINK_CODE_TTL` and works once; a new code revokes the user's older one.
* The bot hears `/start` either by webhook (`ATP_TELEGRAM_WEBHOOK_SECRET` set; Telegram posts to
  `POST /api/telegram/platform/webhook`, registered from the admin route) or, without the secret, by asking Telegram for
  new messages (`getUpdates`) while a trader waits on the Connect screen (`poll_once`).
* The channel stores the chat id only: the platform token is filled in from the server's setting when the channel is
  read (`channels.decrypt_config`), so it is never stored per organisation.
* The chat gets alerts only. It gains no command or approval rights: two-way Telegram stays with an organisation's own
  bot (Settings → Advanced), and `telegram_inbound.service.configure` refuses a platform-bot channel.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.channels import TelegramConfig, decrypt_raw, encrypt_config
from app.alerts.links import LINK_CODE_TTL, START_CODE
from app.audit.log import write_audit_log
from app.core import config as app_config
from app.core.enums import AlertChannelType, NotificationSeverity
from app.db.models import AlertChannelRecord, NotificationLinkRecord

logger = logging.getLogger(__name__)

CHANNEL = "tg_platform"                                   # NotificationLinkRecord.channel (String(16))
WEBHOOK_PATH = "/api/telegram/platform/webhook"
USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
POLL_LIMIT = 50

WELCOME = ("Connected. Your AiAlgoDesk alerts will come to this chat: a lost broker login, a daily loss limit, "
           "a paper trade waiting for you. Turn them off any time in the app, under Settings, Alerts.")
NOT_PRIVATE = "Open this bot in a private chat to connect it, not in a group."
BAD_CODE = "That link did not work (used or expired). In the app, tap Connect Telegram again."
OWN_BOT = "Your organisation already sends Telegram alerts with its own bot, so nothing was changed."
HELLO = "This bot sends your AiAlgoDesk alerts. To connect it, open the app, go to Settings, Alerts, and tap Connect Telegram."

_username_cache: Dict[str, str] = {}
_offset: Dict[str, int] = {}
_poll_lock = asyncio.Lock()


# --- the server's setting ------------------------------------------------------------------------------
def bot_token() -> str:
    return str(getattr(app_config, "ATP_TELEGRAM_BOT_TOKEN", "") or "").strip()


def configured() -> bool:
    token = bot_token()
    return ":" in token and len(token) >= 10


def webhook_mode() -> bool:
    return bool(str(getattr(app_config, "ATP_TELEGRAM_WEBHOOK_SECRET", "") or "").strip())


def webhook_secret_ok(header_value: Optional[str]) -> bool:
    secret = str(getattr(app_config, "ATP_TELEGRAM_WEBHOOK_SECRET", "") or "").strip()
    return bool(secret) and bool(header_value) and hmac.compare_digest(str(header_value), secret)


def webhook_url() -> str:
    base = (getattr(app_config, "PUBLIC_BASE_URL", "") or getattr(app_config, "FRONTEND_URL", "") or "").rstrip("/")
    return f"{base}{WEBHOOK_PATH}"


def _cfg(chat_id: str = "0") -> TelegramConfig:
    return TelegramConfig(bot_token=bot_token(), chat_id=chat_id or "0", platform_bot=True)


async def _call(method: str, payload: dict, client: Optional[httpx.AsyncClient] = None) -> dict:
    from app.telegram_inbound import service             # the one Telegram API helper (never raises, never logs the token)
    return await service._call(_cfg(), method, payload, client)


async def bot_username(client: Optional[httpx.AsyncClient] = None) -> Optional[str]:
    """`ATP_TELEGRAM_BOT_USERNAME`, else asked once from Telegram (getMe) and kept for this process."""
    given = str(getattr(app_config, "ATP_TELEGRAM_BOT_USERNAME", "") or "").strip().lstrip("@")
    if given:
        return given if USERNAME.fullmatch(given) else None
    token = bot_token()
    if token in _username_cache:
        return _username_cache[token]
    answer = await _call("getMe", {}, client)
    name = str((answer.get("result") or {}).get("username") or "") if answer.get("ok") else ""
    if not USERNAME.fullmatch(name):
        return None
    _username_cache[token] = name
    return name


def connect_url(username: str, code: str) -> str:
    return f"https://t.me/{username}?start={code}"


# --- the organisation's channel ---------------------------------------------------------------------------
async def telegram_channel(session: AsyncSession, tenant_id: int) -> Optional[AlertChannelRecord]:
    return await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant_id,
                                                                  AlertChannelRecord.channel_type == AlertChannelType.TELEGRAM.value))


def is_platform(record: Optional[AlertChannelRecord]) -> bool:
    if record is None:
        return False
    try:
        return bool(decrypt_raw(record).get("platform_bot"))
    except ValueError:
        return False


# --- the one-time code ----------------------------------------------------------------------------------
def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def new_code(session: AsyncSession, tenant_id: int, user_id: int, now: Optional[datetime] = None) -> Tuple[str, datetime]:
    """A fresh code for this user (an earlier unused one stops working). Telegram's start parameter allows
    [A-Za-z0-9_-] up to 64 characters; this is 16."""
    now = _utc(now or datetime.now(timezone.utc))
    await session.execute(update(NotificationLinkRecord).where(
        NotificationLinkRecord.tenant_id == tenant_id, NotificationLinkRecord.user_id == user_id,
        NotificationLinkRecord.channel == CHANNEL, NotificationLinkRecord.status == "pending",
    ).values(status="revoked", code_hash=None).execution_options(synchronize_session=False))
    code = secrets.token_urlsafe(12)
    expires = now + LINK_CODE_TTL
    session.add(NotificationLinkRecord(tenant_id=tenant_id, user_id=user_id, channel=CHANNEL, status="pending", code_hash=_hash(code),
                                       code_expires_at=expires, created_at=now))
    await session.flush()
    return code, expires


async def pending_code(session: AsyncSession, tenant_id: int, user_id: int, now: Optional[datetime] = None) -> bool:
    now = _utc(now or datetime.now(timezone.utc))
    row = await session.scalar(select(NotificationLinkRecord).where(
        NotificationLinkRecord.tenant_id == tenant_id, NotificationLinkRecord.user_id == user_id,
        NotificationLinkRecord.channel == CHANNEL, NotificationLinkRecord.status == "pending",
    ).order_by(NotificationLinkRecord.id.desc()).limit(1))
    return row is not None and row.code_expires_at is not None and _utc(row.code_expires_at) > now


async def consume(session: AsyncSession, code: str, chat_id: str, from_id: str,
                  now: Optional[datetime] = None) -> Tuple[Optional[AlertChannelRecord], str]:
    """`/start <code>`: links this private chat as the code's organisation's Telegram channel. Returns (the channel,
    "linked") or (None, why it was refused). The caller commits."""
    now = _utc(now or datetime.now(timezone.utc))
    if not START_CODE.fullmatch(code or ""):
        return None, "bad_code"
    if not chat_id or chat_id != from_id:                 # a group would get one organisation's alerts
        return None, "not_private"
    row = await session.scalar(select(NotificationLinkRecord).where(NotificationLinkRecord.code_hash == _hash(code),
                                                                    NotificationLinkRecord.channel == CHANNEL))
    if row is None or row.status != "pending":
        return None, "unknown_code"
    if row.code_expires_at is None or _utc(row.code_expires_at) <= now:
        row.status, row.code_hash = "revoked", None
        return None, "expired"
    from app.secrets_store.envelope import ensure_tenant_key
    await ensure_tenant_key(session, row.tenant_id)       # the tenant's data key, loaded on this worker before decrypt / encrypt
    record = await telegram_channel(session, row.tenant_id)
    if record is not None and not is_platform(record):
        row.status, row.code_hash = "revoked", None
        return None, "own_bot"
    config = TelegramConfig(chat_id=chat_id, platform_bot=True)
    if record is None:
        record = AlertChannelRecord(tenant_id=row.tenant_id, channel_type=AlertChannelType.TELEGRAM.value, created_by=row.user_id,
                                    encrypted_config=encrypt_config(config, row.tenant_id), enabled=True,
                                    min_severity=NotificationSeverity.WARNING.value)
        session.add(record)
    else:
        record.encrypted_config = encrypt_config(config, row.tenant_id)
        record.enabled = True
    record.last_delivered_at, record.last_error = None, None
    row.status, row.code_hash, row.chat_id, row.linked_at = "used", None, chat_id, now
    await session.flush()
    await write_audit_log(session, row.tenant_id, row.user_id, "telegram_connected", f"chat {chat_id} connected through the platform bot")
    return record, "linked"


# --- what the bot hears ---------------------------------------------------------------------------------
async def handle_update(session: AsyncSession, update: Dict[str, Any], *, client: Optional[httpx.AsyncClient] = None,
                        now: Optional[datetime] = None) -> Dict[str, Any]:
    """One update. Only private `/start <code>` messages change anything; everything else gets a short hint."""
    from app.telegram_inbound import service
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    from_id = str((message.get("from") or {}).get("id") or "")
    text = str(message.get("text") or "").strip()
    if not chat_id or not text:
        return {"handled": "ignored"}
    if await service.rate_limited(0, chat_id):
        return {"handled": "rate_limited"}
    if not text.startswith("/start"):
        await service.reply(_cfg(), chat_id, HELLO, client, parse_mode=None)
        return {"handled": "hello"}
    code = text[len("/start"):].strip()
    if not code:
        await service.reply(_cfg(), chat_id, HELLO, client, parse_mode=None)
        return {"handled": "hello"}
    record, why = await consume(session, code, chat_id, from_id, now=now)
    await session.commit()
    if record is None:
        text_back = NOT_PRIVATE if why == "not_private" else OWN_BOT if why == "own_bot" else BAD_CODE
        await service.reply(_cfg(), chat_id, text_back, client, parse_mode=None)
        return {"handled": "link", "result": why}
    answer = await service.reply(_cfg(), chat_id, WELCOME, client, parse_mode=None)
    if answer.get("ok"):
        record.last_delivered_at = _utc(now or datetime.now(timezone.utc))   # the welcome went through: the channel works
    else:
        record.last_error = "The welcome message did not reach Telegram. Send a test to check."
    await session.commit()
    return {"handled": "link", "result": why}


async def poll_once(session: AsyncSession, *, client: Optional[httpx.AsyncClient] = None) -> int:
    """Without a webhook: read the bot's new messages once (getUpdates, no waiting) and handle them. Returns how many
    were handled. One poll at a time per process; another process reading the same messages is harmless (a code works
    once, and Telegram drops what an offset confirms)."""
    if not configured() or webhook_mode() or _poll_lock.locked():
        return 0
    async with _poll_lock:
        token = bot_token()
        payload: Dict[str, Any] = {"timeout": 0, "limit": POLL_LIMIT, "allowed_updates": ["message"]}
        if token in _offset:
            payload["offset"] = _offset[token] + 1
        answer = await _call("getUpdates", payload, client)
        if not answer.get("ok"):
            logger.warning("Telegram getUpdates for the platform bot failed: %s", str(answer.get("description", ""))[:200])
            return 0
        handled = 0
        for item in answer.get("result") or []:
            if not isinstance(item, dict):
                continue
            update_id = int(item.get("update_id") or 0)
            _offset[token] = max(_offset.get(token, 0), update_id)
            try:
                await handle_update(session, item, client=client)
                handled += 1
            except Exception:  # noqa: BLE001 - one bad message never stops the others
                logger.exception("Platform bot update %s failed", update_id)
                await session.rollback()
        return handled


async def register_webhook(client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    """Tells Telegram to post the bot's messages to this server (setWebhook with the secret). The token never leaves."""
    if not configured():
        raise LookupError("ATP_TELEGRAM_BOT_TOKEN is not set on this server")
    if not webhook_mode():
        raise ValueError("Set ATP_TELEGRAM_WEBHOOK_SECRET first (without it the app reads new messages itself)")
    answer = await _call("setWebhook", {"url": webhook_url(), "secret_token": str(app_config.ATP_TELEGRAM_WEBHOOK_SECRET).strip(),
                                        "allowed_updates": ["message"], "drop_pending_updates": True}, client)
    return {"ok": bool(answer.get("ok")), "description": answer.get("description"), "webhook_url": webhook_url()}


__all__ = ["CHANNEL", "WEBHOOK_PATH", "bot_token", "configured", "webhook_mode", "webhook_secret_ok", "webhook_url", "bot_username",
           "connect_url", "telegram_channel", "is_platform", "new_code", "pending_code", "consume", "handle_update", "poll_once",
           "register_webhook"]
