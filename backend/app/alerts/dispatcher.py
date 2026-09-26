"""Alert outbox: queue rows when a notification is raised, send them with retries later.

`enqueue_for_notification` runs inside `notify()` and only writes DB rows - it never touches the
network, so the execution pipeline that raised a CRITICAL alert is never slowed or failed by a
dead SMTP server. `dispatch_pending` does the sending; the trading worker calls it every cycle
(market open or not), and the Settings "send test" button calls `send_via_channel` directly.
"""
import asyncio
import html
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import List, Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.metrics import ALERT_DELIVERIES
from app.alerts.channels import EmailConfig, TelegramConfig, decrypt_config, severity_reaches
from app.core.enums import AlertChannelType, AlertDeliveryStatus
from app.db.models import AlertChannelRecord, AlertDeliveryRecord, NotificationRecord
from app.market_data.calendar import IST

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 30
TELEGRAM_API = "https://api.telegram.org"
SEND_TIMEOUT_SECONDS = 10.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def enqueue_for_notification(session: AsyncSession, notification: NotificationRecord) -> List[AlertDeliveryRecord]:
    """One outbox row per enabled channel whose severity floor this notification reaches.
    Does not commit - `notify()` commits right after."""
    channels = await session.scalars(
        select(AlertChannelRecord).where(
            AlertChannelRecord.tenant_id == notification.tenant_id, AlertChannelRecord.enabled.is_(True),
        )
    )
    rows: List[AlertDeliveryRecord] = []
    for channel in channels:
        if not severity_reaches(notification.severity, channel.min_severity):
            continue
        row = AlertDeliveryRecord(
            tenant_id=notification.tenant_id, notification_id=notification.id, channel_id=channel.id,
            status=AlertDeliveryStatus.PENDING.value, next_attempt_at=_utcnow(),
        )
        session.add(row)
        rows.append(row)
    return rows


# --- rendering ---------------------------------------------------------------------------------

def render_text(notification: NotificationRecord) -> Tuple[str, str]:
    """(plain, html) bodies. Kept short: these land on a phone."""
    created = _as_utc(notification.created_at).astimezone(IST).strftime("%d %b %H:%M IST")
    plain = f"[{notification.severity}] {notification.title}\n{notification.message}\n{notification.event_type} · {created}"
    html_body = (
        f"<b>[{html.escape(notification.severity)}] {html.escape(notification.title)}</b>\n"
        f"{html.escape(notification.message)}\n"
        f"<i>{html.escape(notification.event_type)} · {created}</i>"
    )
    return plain, html_body


# --- senders -----------------------------------------------------------------------------------

async def send_telegram(config: TelegramConfig, html_text: str, client: Optional[httpx.AsyncClient] = None) -> None:
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS)
    try:
        response = await client.post(
            f"{TELEGRAM_API}/bot{config.bot_token}/sendMessage",
            json={"chat_id": config.chat_id, "text": html_text, "parse_mode": "HTML", "disable_web_page_preview": True},
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400 or not payload.get("ok", False):
            description = payload.get("description") or response.text[:200]
            # Never echo the token back in an error message.
            raise RuntimeError(f"Telegram API {response.status_code}: {description}")
    finally:
        if owns_client:
            await client.aclose()


def _smtp_send(config: EmailConfig, message: EmailMessage) -> None:
    """Blocking SMTP round-trip, run in a thread by send_email. Split out so tests can stub it."""
    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=SEND_TIMEOUT_SECONDS) as smtp:
        smtp.ehlo()
        if config.use_tls:
            smtp.starttls()
            smtp.ehlo()
        if config.username:
            smtp.login(config.username, config.password or "")
        smtp.send_message(message)


async def send_email(config: EmailConfig, subject: str, plain_text: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = str(config.from_address)
    message["To"] = ", ".join(str(a) for a in config.to_addresses)
    message.set_content(plain_text)
    await asyncio.to_thread(_smtp_send, config, message)


async def send_via_channel(
    channel: AlertChannelRecord, notification: NotificationRecord, client: Optional[httpx.AsyncClient] = None,
) -> None:
    """Sends one notification through one channel. Raises on failure with a message safe to store."""
    config = decrypt_config(channel)
    plain, html_body = render_text(notification)
    if channel.channel_type == AlertChannelType.TELEGRAM.value:
        await send_telegram(config, html_body, client)  # type: ignore[arg-type]
    elif channel.channel_type == AlertChannelType.EMAIL.value:
        subject = f"[{notification.severity}] {notification.title}"
        await send_email(config, subject, plain)  # type: ignore[arg-type]
    else:
        raise RuntimeError(f"Unknown channel type {channel.channel_type}")


# --- the drain -----------------------------------------------------------------------------------

async def dispatch_pending(
    session: AsyncSession, *, limit: int = 50, client: Optional[httpx.AsyncClient] = None, now: Optional[datetime] = None,
    tenant_id: Optional[int] = None,
) -> Tuple[int, int]:
    """Sends every due PENDING delivery once, returning (sent, failed_this_round). A failure
    schedules a retry with exponential backoff (30s, 60s, 120s, ...) until MAX_ATTEMPTS, then the
    row is FAILED for good. One bad channel never delays another: each row commits on its own.
    `tenant_id` narrows the drain to one tenant (the worker passes none: platform-wide)."""
    now = now or _utcnow()
    query = (
        select(AlertDeliveryRecord)
        .where(AlertDeliveryRecord.status == AlertDeliveryStatus.PENDING.value, AlertDeliveryRecord.next_attempt_at <= now)
    )
    if tenant_id is not None:
        query = query.where(AlertDeliveryRecord.tenant_id == tenant_id)
    due = list(await session.scalars(query.order_by(AlertDeliveryRecord.id).limit(limit)))
    sent = failed = 0
    for delivery in due:
        channel = await session.get(AlertChannelRecord, delivery.channel_id)
        notification = await session.get(NotificationRecord, delivery.notification_id)
        if channel is None or notification is None or not channel.enabled:
            delivery.status = AlertDeliveryStatus.FAILED.value
            delivery.last_error = "Channel disabled or removed before delivery"
            await session.commit()
            failed += 1
            continue
        try:
            await send_via_channel(channel, notification, client)
        except Exception as exc:  # noqa: BLE001 - every failure mode becomes a retry or a FAILED row
            delivery.attempts += 1
            delivery.last_error = str(exc)[:500]
            channel.last_error = delivery.last_error
            if delivery.attempts >= MAX_ATTEMPTS:
                delivery.status = AlertDeliveryStatus.FAILED.value
                ALERT_DELIVERIES.labels(channel=channel.channel_type, status="FAILED").inc()
                logger.error("Alert delivery %s gave up after %d attempts: %s", delivery.id, delivery.attempts, exc)
            else:
                delivery.next_attempt_at = now + timedelta(seconds=BASE_BACKOFF_SECONDS * (2 ** (delivery.attempts - 1)))
                logger.warning("Alert delivery %s failed (attempt %d): %s", delivery.id, delivery.attempts, exc)
            failed += 1
        else:
            delivery.attempts += 1
            delivery.status = AlertDeliveryStatus.SENT.value
            ALERT_DELIVERIES.labels(channel=channel.channel_type, status="SENT").inc()
            delivery.sent_at = now
            delivery.last_error = None
            channel.last_delivered_at = now
            channel.last_error = None
            sent += 1
        await session.commit()
    if sent or failed:
        logger.info("Alert dispatch: %d sent, %d failed/retried", sent, failed)
    return sent, failed
