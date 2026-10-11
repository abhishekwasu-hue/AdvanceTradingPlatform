"""S3b-2 (ADR-0022): per-user Telegram linking and email unsubscribe.

Telegram: a user asks for a one-time code; sending `/start <code>` to the organisation's bot from a PRIVATE chat links
that chat to the user. Only the code's SHA-256 is stored, it expires after `LINK_CODE_TTL` and works once. A linked
chat then receives the screen alerts of rules that user created, as its own outbox row (its own retries). It gains no
command rights: those stay on the channel's approver list.

Email: each screen-alert mail goes to one recipient at a time with a signed unsubscribe link (and the RFC 8058
List-Unsubscribe headers). Opting out stops screen-alert mails to that address for that organisation only; risk and
system mails still go. The link is a page with a button - the POST unsubscribes, so a mail scanner that follows
links does not.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Set, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config as app_config
from app.db.models import AlertChannelRecord, AlertDeliveryRecord, EmailOptOutRecord, NotificationLinkRecord

LINK_CODE_TTL = timedelta(minutes=15)
START_CODE = re.compile(r"^[A-Za-z0-9_-]{16,32}$")
SCOPE = "screen_alerts"


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


async def new_link_code(session: AsyncSession, tenant_id: int, user_id: int, now: Optional[datetime] = None) -> Tuple[str, datetime]:
    """A fresh one-time code (any earlier unused code of this user stops working)."""
    now = _utc(now or datetime.now(timezone.utc))
    await session.execute(update(NotificationLinkRecord).where(
        NotificationLinkRecord.tenant_id == tenant_id, NotificationLinkRecord.user_id == user_id, NotificationLinkRecord.status == "pending",
    ).values(status="revoked", code_hash=None).execution_options(synchronize_session=False))
    code = secrets.token_urlsafe(12)                                           # 16 characters, 96 bits
    expires = now + LINK_CODE_TTL
    session.add(NotificationLinkRecord(tenant_id=tenant_id, user_id=user_id, channel="telegram", status="pending", code_hash=_hash(code),
                                       code_expires_at=expires, created_at=now))
    await session.flush()
    return code, expires


async def consume_start(session: AsyncSession, tenant_id: int, code: str, chat_id: str, from_id: str,
                        now: Optional[datetime] = None) -> Tuple[Optional[NotificationLinkRecord], str]:
    """`/start <code>` from Telegram. Returns (the linked row, "linked") or (None, why it was refused)."""
    now = _utc(now or datetime.now(timezone.utc))
    if not START_CODE.fullmatch(code or ""):
        return None, "bad_code"
    if not chat_id or chat_id != from_id:                                      # a group would see one user's alerts
        return None, "not_private"
    row = await session.scalar(select(NotificationLinkRecord).where(NotificationLinkRecord.code_hash == _hash(code)))
    if row is None or row.tenant_id != tenant_id or row.status != "pending":
        return None, "unknown_code"
    if row.code_expires_at is None or _utc(row.code_expires_at) <= now:
        row.status, row.code_hash = "revoked", None
        return None, "expired"
    await session.execute(update(NotificationLinkRecord).where(           # one linked chat per user
        NotificationLinkRecord.tenant_id == tenant_id, NotificationLinkRecord.user_id == row.user_id, NotificationLinkRecord.status == "linked",
    ).values(status="revoked").execution_options(synchronize_session=False))
    row.status, row.code_hash, row.chat_id, row.linked_at = "linked", None, chat_id, now
    return row, "linked"


async def linked_chat(session: AsyncSession, tenant_id: int, user_id: int) -> Optional[NotificationLinkRecord]:
    return await session.scalar(select(NotificationLinkRecord).where(
        NotificationLinkRecord.tenant_id == tenant_id, NotificationLinkRecord.user_id == user_id, NotificationLinkRecord.status == "linked",
    ).order_by(NotificationLinkRecord.id.desc()).limit(1))


async def unlink(session: AsyncSession, tenant_id: int, user_id: int) -> int:
    result = await session.execute(update(NotificationLinkRecord).where(
        NotificationLinkRecord.tenant_id == tenant_id, NotificationLinkRecord.user_id == user_id,
        NotificationLinkRecord.status.in_(("linked", "pending")),
    ).values(status="revoked", code_hash=None).execution_options(synchronize_session=False))
    return int(getattr(result, "rowcount", 0) or 0)


async def add_linked_delivery(session: AsyncSession, tenant_id: int, notification_id: int, user_id: Optional[int], *,
                              priority: Optional[str] = None, group_id: Optional[str] = None,
                              digest_bucket: Optional[str] = None) -> Optional[AlertDeliveryRecord]:
    """After a screen-alert notification is queued: one more Telegram row for the rule creator's linked chat - only
    when the organisation's Telegram channel took the notification (its severity floor and enabled state apply)."""
    if user_id is None:
        return None
    link = await linked_chat(session, tenant_id, user_id)
    if link is None or not link.chat_id:
        return None
    base = await session.scalar(select(AlertDeliveryRecord).join(AlertChannelRecord, AlertChannelRecord.id == AlertDeliveryRecord.channel_id).where(
        AlertDeliveryRecord.notification_id == notification_id, AlertDeliveryRecord.address.is_(None),
        AlertChannelRecord.channel_type == "TELEGRAM", AlertChannelRecord.enabled.is_(True)).limit(1))
    if base is None:
        return None
    row = AlertDeliveryRecord(tenant_id=tenant_id, notification_id=notification_id, channel_id=base.channel_id, status="PENDING", attempts=0,
                              next_attempt_at=base.next_attempt_at, priority=priority, group_id=group_id,
                              digest_bucket=digest_bucket, address=link.chat_id)
    session.add(row)
    return row


# --- email unsubscribe -------------------------------------------------------------------------------

def _key() -> bytes:
    return hashlib.sha256(b"atp-email-unsubscribe|" + (app_config.JWT_SECRET_KEY or "atp").encode()).digest()


def unsubscribe_token(tenant_id: int, address: str) -> str:
    payload = base64.urlsafe_b64encode(f"{tenant_id}:{address.strip().lower()}".encode()).decode().rstrip("=")
    mac = hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}.{mac}"


def read_unsubscribe_token(token: str) -> Optional[Tuple[int, str]]:
    try:
        payload, mac = token.split(".", 1)
        if not hmac.compare_digest(hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()[:32], mac):
            return None
        tenant, address = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode().split(":", 1)
        return int(tenant), address
    except (ValueError, UnicodeDecodeError):
        return None


def unsubscribe_url(tenant_id: int, address: str) -> Optional[str]:
    base = getattr(app_config, "PUBLIC_BASE_URL", "")
    return f"{base}/api/alerts/unsubscribe?t={unsubscribe_token(tenant_id, address)}" if base else None


async def opt_out(session: AsyncSession, tenant_id: int, address: str, now: Optional[datetime] = None) -> bool:
    """Records the opt-out; True when it is new."""
    address = address.strip().lower()
    if await session.scalar(select(EmailOptOutRecord.id).where(EmailOptOutRecord.tenant_id == tenant_id, EmailOptOutRecord.address == address,
                                                              EmailOptOutRecord.scope == SCOPE)) is not None:
        return False
    session.add(EmailOptOutRecord(tenant_id=tenant_id, address=address, scope=SCOPE, created_at=_utc(now or datetime.now(timezone.utc))))
    return True


async def opted_out(session: AsyncSession, tenant_id: int, addresses: Iterable[str]) -> Set[str]:
    wanted = {a.strip().lower() for a in addresses}
    if not wanted:
        return set()
    rows = await session.scalars(select(EmailOptOutRecord.address).where(EmailOptOutRecord.tenant_id == tenant_id, EmailOptOutRecord.scope == SCOPE,
                                                                        func.lower(EmailOptOutRecord.address).in_(wanted)))
    return set(rows)


__all__ = ["new_link_code", "consume_start", "linked_chat", "unlink", "add_linked_delivery", "unsubscribe_token", "read_unsubscribe_token",
           "unsubscribe_url", "opt_out", "opted_out", "LINK_CODE_TTL", "START_CODE", "SCOPE"]
