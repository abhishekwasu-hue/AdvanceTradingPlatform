"""Alert outbox: queue rows when a notification is raised, send them with retries later.

`enqueue_for_notification` runs inside `notify()` and only writes DB rows - it never touches the
network, so the execution pipeline that raised a CRITICAL alert is never slowed or failed by a
dead SMTP server. `dispatch_pending` does the sending; the trading worker calls it every cycle
(market open or not), and the Settings "send test" button calls `send_via_channel` directly.
"""
import asyncio
import hashlib
import hmac
import json
import html
import logging
import re
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import List, Optional, Tuple

import httpx

from app.core.egress import check_url_resolved
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.metrics import ALERT_DELIVERIES
from app.alerts.channels import EmailConfig, PushConfig, SmsConfig, TelegramConfig, WebhookConfig, decrypt_config, encrypt_config, severity_reaches
from app.alerts import webpush
from app.core.enums import NotificationType, AlertChannelType, AlertDeliveryStatus
from app.db.models import AlertChannelRecord, AlertDeliveryRecord, NotificationRecord
from app.market_data.calendar import IST
from app.secrets_store.envelope import ensure_tenant_key

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


WEBHOOK_SCHEMA = "atp.notification/1"
ALERT_SCHEMA = "atp.alert/1"
REPLAY_WINDOW_SECONDS = 300          # receivers should refuse an X-ATP-Timestamp older (or newer) than this


def webhook_payload(notification: NotificationRecord, alert: Optional[dict] = None) -> dict:
    """The versioned body (S3b): every notification, plus an `alert` block - rule, symbols, trigger values with their
    data timestamps, bar time - when the notification is a screen/instrument alert."""
    body = {
        "schema": WEBHOOK_SCHEMA,
        "id": notification.id, "event_type": notification.event_type, "severity": notification.severity, "title": notification.title,
        "message": notification.message, "created_at": _as_utc(notification.created_at).isoformat(), "tenant_id": notification.tenant_id,
        "metadata": json.loads(notification.metadata_json) if getattr(notification, "metadata_json", None) else None,
    }
    if alert:
        body["alert"] = {"schema": ALERT_SCHEMA, **alert}
    return body


def chartink_payload(alert: dict) -> dict:
    """The Chartink-shaped body some existing tools read (`stocks`, `trigger_prices` as comma lists)."""
    symbols = alert.get("symbols") or []
    values = alert.get("trigger_values") or {}
    prices = [str(values.get(s, {}).get("close", "")) for s in symbols]
    return {"stocks": ",".join(symbols), "trigger_prices": ",".join(prices), "triggered_at": alert.get("bar_time"),
            "scan_name": alert.get("rule_name"), "alert_name": alert.get("rule_name"), "scan_url": ""}


def verify_webhook(secret: str, body: bytes, timestamp: str, signature: str, *, now: Optional[datetime] = None,
                   window_seconds: int = REPLAY_WINDOW_SECONDS) -> bool:
    """What a receiver does: the signature matches AND the timestamp is inside the replay window."""
    try:
        sent = int(timestamp)
    except (TypeError, ValueError):
        return False
    current = int((now or _utcnow()).timestamp())
    if abs(current - sent) > window_seconds:
        return False
    return hmac.compare_digest(sign_webhook(secret, body, timestamp), signature)


def sign_webhook(secret: str, body: bytes, timestamp: str) -> str:
    """`sha256=<hex>` over `<timestamp>.<body>` - the receiver recomputes it with the shared secret."""
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def send_webhook(config: WebhookConfig, notification: NotificationRecord, client: Optional[httpx.AsyncClient] = None,
                       alert: Optional[dict] = None) -> None:
    """Phase K4: HMAC-signed JSON POST. A filtered-out event type is a silent success. S3b: versioned body, optional
    Chartink shape for screen alerts, schema header."""
    if config.event_types and notification.event_type not in config.event_types:
        return
    chartink = getattr(config, "payload_format", "atp") == "chartink" and alert is not None
    payload = chartink_payload(alert) if chartink and alert is not None else webhook_payload(notification, alert)
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    timestamp = str(int(_utcnow().timestamp()))
    headers = {"Content-Type": "application/json", "X-ATP-Timestamp": timestamp, "X-ATP-Signature": sign_webhook(config.secret, body, timestamp),
               "X-ATP-Event": notification.event_type, "X-ATP-Schema": "chartink/1" if chartink else WEBHOOK_SCHEMA,
               "User-Agent": "ATP-Webhooks/1.0"}
    await check_url_resolved(str(config.url))                       # P0.2 / S5: resolved right before the request
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await client.post(str(config.url), content=body, headers=headers)
        if response.status_code >= 300:
            raise RuntimeError(f"Webhook endpoint answered HTTP {response.status_code}")
    finally:
        if owns_client:
            await client.aclose()


_PLACEHOLDER = re.compile(r"\{(to|text|title|severity|event_type)\}")


def _fill_template(template: str, values: dict) -> str:
    """Only the documented placeholders are substituted; every other brace (the JSON body itself)
    is left exactly as typed - `str.format` would choke on it."""
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def render_sms(config: SmsConfig, notification: NotificationRecord, to_number: str) -> Tuple[str, Optional[str]]:
    """(body, query) for one recipient: the template with placeholders filled and JSON-escaped when
    the content type is JSON, so an alert message containing quotes cannot break the request."""
    plain, _ = render_text(notification)
    text = plain[: config.max_length]
    values = {"to": to_number, "text": text, "title": notification.title[:120], "severity": notification.severity,
              "event_type": notification.event_type}
    if "json" in config.content_type:
        values = {k: json.dumps(v)[1:-1] for k, v in values.items()}  # escape for insertion inside JSON strings
    return _fill_template(config.body_template, values), None


async def send_sms(config: SmsConfig, notification: NotificationRecord, client: Optional[httpx.AsyncClient] = None) -> None:
    """Phase O3: one gateway request per recipient; the first failure is raised after trying all."""
    await check_url_resolved(str(config.url))                       # P0.2 / S5
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    errors: List[str] = []
    try:
        for number in config.to_numbers:
            body, _ = render_sms(config, notification, number)
            headers = {"Content-Type": config.content_type, "User-Agent": "ATP-Alerts/1.0", **config.headers}
            try:
                if config.method == "GET":
                    response = await client.get(str(config.url), params=dict(p.split("=", 1) for p in body.split("&") if "=" in p), headers=headers)
                else:
                    response = await client.post(str(config.url), content=body.encode(), headers=headers)
                if response.status_code >= 300:
                    errors.append(f"{number}: HTTP {response.status_code}")       # the gateway's body is never echoed (P0.2 / S5)
            except httpx.HTTPError as exc:
                errors.append(f"{number}: {type(exc).__name__}")
    finally:
        if owns_client:
            await client.aclose()
    if errors:
        raise RuntimeError("SMS gateway: " + "; ".join(errors))


async def send_push(config: PushConfig, notification: NotificationRecord, client: Optional[httpx.AsyncClient] = None) -> List[str]:
    """Phase O3: every registered device; returns the endpoints the push service reports gone so
    the caller can prune them. Raises only when no device could be reached."""
    payload = {"title": f"[{notification.severity}] {notification.title}", "body": notification.message[:300],
               "event_type": notification.event_type, "severity": notification.severity, "notification_id": notification.id,
               "url": "/?page=notifications"}
    gone: List[str] = []
    errors: List[str] = []
    delivered = 0
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        for sub in config.subscriptions:
            try:
                await webpush.send_push(str(sub.endpoint), sub.p256dh, sub.auth, payload, client=client)
                delivered += 1
            except webpush.PushGone:
                gone.append(str(sub.endpoint))
            except Exception as exc:  # noqa: BLE001 - per-device failure, keep going
                errors.append(f"{sub.label}: {exc}"[:120])
    finally:
        if owns_client:
            await client.aclose()
    if delivered == 0 and (errors or not gone):
        raise RuntimeError("Web Push: " + ("; ".join(errors) if errors else "no reachable device"))
    if delivered == 0 and gone and not errors:
        raise RuntimeError("Web Push: every registered device has unsubscribed - enable push again from Settings")
    return gone


async def send_via_channel(
    channel: AlertChannelRecord, notification: NotificationRecord, client: Optional[httpx.AsyncClient] = None, alert: Optional[dict] = None,
) -> None:
    """Sends one notification through one channel. Raises on failure with a message safe to store.
    A PUSH channel whose devices have gone gets its config rewritten in place (caller commits)."""
    config = decrypt_config(channel)
    plain, html_body = render_text(notification)
    if channel.channel_type == AlertChannelType.TELEGRAM.value:
        await send_telegram(config, html_body, client)  # type: ignore[arg-type]
    elif channel.channel_type == AlertChannelType.EMAIL.value:
        subject = f"[{notification.severity}] {notification.title}"
        await send_email(config, subject, plain)  # type: ignore[arg-type]
    elif channel.channel_type == AlertChannelType.WEBHOOK.value:
        await send_webhook(config, notification, client, alert)  # type: ignore[arg-type]
    elif channel.channel_type == AlertChannelType.PUSH.value:
        gone = await send_push(config, notification, client)  # type: ignore[arg-type]
        if gone:
            remaining = [s for s in config.subscriptions if str(s.endpoint) not in gone]  # type: ignore[union-attr]
            if remaining:
                channel.encrypted_config = encrypt_config(PushConfig(subscriptions=remaining), channel.tenant_id)
            logger.info("Pruned %d gone push subscription(s) for tenant %s", len(gone), channel.tenant_id)
    elif channel.channel_type == AlertChannelType.SMS.value:
        await send_sms(config, notification, client)  # type: ignore[arg-type]
    else:
        raise RuntimeError(f"Unknown channel type {channel.channel_type}")


async def _telegram_proposal_buttons(session: AsyncSession, channel: AlertChannelRecord, notification: NotificationRecord,
                                     client: Optional[httpx.AsyncClient]) -> bool:
    """Phase BE: an AI_PROPOSAL going to a Telegram channel with inbound on is sent with one-time
    Approve / Reject buttons when Telegram may decide it (PAPER, reduce/pause). False -> plain send."""
    if channel.channel_type != AlertChannelType.TELEGRAM.value or notification.event_type != NotificationType.AI_PROPOSAL.value or not notification.ai_action_id:
        return False
    from app.db.models import AiActionRecord
    from app.telegram_inbound import service as telegram_inbound
    try:
        action = await session.get(AiActionRecord, notification.ai_action_id)
        if action is None or action.tenant_id != channel.tenant_id:
            return False
        _, html_body = render_text(notification)
        return await telegram_inbound.send_proposal_buttons(session, channel, action, html_body, client)
    except RuntimeError:
        raise                                         # Telegram refused the send: the ordinary retry path
    except Exception as exc:  # noqa: BLE001 - a bug in the buttons must not stop the alert itself
        logger.warning("Telegram proposal buttons failed (%s); sending the plain alert", exc)
        await session.rollback()
        return False


async def alert_context(session: AsyncSession, notification: NotificationRecord) -> Optional[dict]:
    """S3b: the alert block for a screen/instrument alert notification - rule, symbols, trigger values with their data
    timestamps, bar time - read from the alert events the notification grouped."""
    if notification.event_type != "SCREEN_ALERT":
        return None
    from app.db.models import AlertEventRecord, AlertRuleRecord
    events = list(await session.scalars(select(AlertEventRecord).where(AlertEventRecord.notification_id == notification.id)
                                        .order_by(AlertEventRecord.symbol)))
    if not events:
        return None
    rule = await session.get(AlertRuleRecord, events[0].rule_id)
    values = {e.symbol: json.loads(e.values_json or "{}") for e in events}
    bar_time = max(_as_utc(e.bar_time) for e in events).isoformat()
    return {"rule_id": events[0].rule_id, "rule_name": rule.name if rule else None, "screen_id": rule.screen_id if rule else None,
            "symbols": [e.symbol for e in events], "trigger_values": values, "bar_time": bar_time,
            "data_timestamps": {s: v.get("as_of") for s, v in values.items()}, "group_id": events[0].group_id}


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
        if channel is not None:
            await ensure_tenant_key(session, channel.tenant_id)  # Phase N1
        if channel is None or notification is None or not channel.enabled:
            delivery.status = AlertDeliveryStatus.FAILED.value
            delivery.last_error = "Channel disabled or removed before delivery"
            delivery.reason_code = "channel_disabled"
            await session.commit()
            failed += 1
            continue
        try:
            if not await _telegram_proposal_buttons(session, channel, notification, client):
                await send_via_channel(channel, notification, client, await alert_context(session, notification))
        except Exception as exc:  # noqa: BLE001 - every failure mode becomes a retry or a FAILED row
            delivery.attempts += 1
            delivery.last_error = str(exc)[:500]
            channel.last_error = delivery.last_error
            if delivery.attempts >= MAX_ATTEMPTS:
                delivery.status = AlertDeliveryStatus.FAILED.value
                delivery.reason_code = "dead_letter"               # S3b: kept for the operator; retry from the dead-letter list
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
