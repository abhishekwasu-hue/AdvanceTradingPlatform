"""Alert outbox: queue rows when a notification is raised, send them with retries later.

`enqueue_for_notification` runs inside `notify()` and only writes DB rows - it never touches the
network, so the execution pipeline that raised a CRITICAL alert is never slowed or failed by a
dead SMTP server. `dispatch_pending` does the sending; the trading worker calls it every cycle
(market open or not), and the Settings "send test" button calls `send_via_channel` directly.

Z10-b, per organisation and off by default:
* `alerts_reliable_outbox` - a worker *claims* a row before sending it (a conditional UPDATE that takes a lease of
  `LEASE_SECONDS` from the real clock at the claim and counts the attempt), so two workers never send the same row; a
  send is cut off `LEASE_MARGIN_SECONDS` before its lease ends, and only the claim's holder can write its outcome. A
  worker killed mid-send leaves a lease that expires, and the row is claimed again (at least once - receivers that can dedupe get the row's
  `idem_key`: webhook / SMS `Idempotency-Key`, email `Message-ID`). After `MAX_ATTEMPTS` the row is DEAD (reason
  dead_letter, or worker_lost when its last attempt never came back), for the operator to replay. Each delivery has
  one idempotency key (notification x channel x address, unique), so a duplicate cycle cannot queue it twice.
* `alerts_stale_guard` - a screen / instrument alert is not sent once its bars are no longer current
  (`app/alerts/staleness.py`; a retry, a held delivery, a replayed dead letter): the row is SUPPRESSED (reason
  stale_data). Every other notification type - trades, exits, risk, broker, system - is always sent (ADR-0004).
"""
import asyncio
import hashlib
import hmac
import json
from urllib.parse import parse_qsl, quote
import html
import logging
import os
import re
import secrets
import smtplib
import socket
import ssl
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Dict, List, MutableMapping, Optional, Set, Tuple, Union

import httpx

from app.core.egress import check_url_resolved
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.metrics import ALERT_DELIVERIES
from app.alerts.channels import (DLT_FIELDS, EmailConfig, PushConfig, SmsConfig, TelegramConfig, WebhookConfig, WhatsAppConfig, decrypt_config,
                                 decrypt_raw, encrypt_config, severity_reaches)
from app.alerts import s10_gate, staleness, webpush
from app.alerts.deeplink import app_path, open_in_app_row, push_wanted
from app.core.enums import NotificationType, AlertChannelType, AlertDeliveryStatus
from app.db.models import AlertChannelRecord, AlertDeliveryRecord, NotificationRecord
from app.market_data.calendar import IST
from app.secrets_store.envelope import ensure_tenant_key

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 30
TELEGRAM_API = "https://api.telegram.org"
SEND_TIMEOUT_SECONDS = 10.0
OUTBOX_FLAG = "alerts_reliable_outbox"
# A claimed row is someone else's for this long. Above the worst single send (an SMS gateway or a push fan-out is several
# SEND_TIMEOUT_SECONDS requests), so a live worker never loses its row mid-send.
LEASE_SECONDS = 120
LEASE_MARGIN_SECONDS = 20                                  # a send is cut off this long before its lease would run out
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"[:48]     # + ":" + a token per claim fits claimed_by (64)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def delivery_key(notification_id: int, channel_id: int, address: Optional[str] = None) -> str:
    """Z10-b: the idempotency key of one delivery - the same notification, channel and address always give the same key."""
    return hashlib.sha256(f"atp-delivery|{notification_id}|{channel_id}|{address or ''}".encode()).hexdigest()


async def outbox_hardened(session: AsyncSession, tenant_id: int, memo: Optional[MutableMapping[int, bool]] = None) -> bool:
    """Flag `alerts_reliable_outbox` for this organisation (remembered in `memo` for the rest of a drain)."""
    if memo is not None and tenant_id in memo:
        return memo[tenant_id]
    from app.platform.controls import flag_enabled
    on = await flag_enabled(session, OUTBOX_FLAG, tenant_id)
    if memo is not None:
        memo[tenant_id] = on
    return on


def _push_wants(channel: AlertChannelRecord, notification: NotificationRecord) -> bool:
    try:
        categories = decrypt_raw(channel).get("categories")
    except ValueError:
        return True                                        # undecryptable: let the send report the real error
    return push_wanted(categories, notification.event_type, notification.severity)


async def enqueue_for_notification(session: AsyncSession, notification: NotificationRecord) -> List[AlertDeliveryRecord]:
    """One outbox row per enabled channel whose severity floor this notification reaches.
    Does not commit - `notify()` commits right after."""
    channels = await session.scalars(
        select(AlertChannelRecord).where(
            AlertChannelRecord.tenant_id == notification.tenant_id, AlertChannelRecord.enabled.is_(True),
        )
    )
    rows: List[AlertDeliveryRecord] = []
    keyed = await outbox_hardened(session, notification.tenant_id)
    queued: Set[str] = set()
    if keyed:                                              # Z10-b: a second enqueue of the same notification adds nothing
        queued = {k for k in await session.scalars(select(AlertDeliveryRecord.idem_key).where(
            AlertDeliveryRecord.notification_id == notification.id)) if k}
    for channel in channels:
        if not severity_reaches(notification.severity, channel.min_severity):
            continue
        if channel.channel_type == AlertChannelType.PUSH.value and not _push_wants(channel, notification):
            continue                                       # PW3: a category this organisation turned off for push
        key = delivery_key(notification.id, channel.id) if keyed else None
        if key is not None and key in queued:
            continue
        row = AlertDeliveryRecord(
            tenant_id=notification.tenant_id, notification_id=notification.id, channel_id=channel.id,
            status=AlertDeliveryStatus.PENDING.value, next_attempt_at=_utcnow(), idem_key=key,
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

async def send_telegram(config: TelegramConfig, html_text: str, client: Optional[httpx.AsyncClient] = None, chat_id: Optional[str] = None,
                        reply_markup: Optional[dict] = None) -> None:
    if not config.bot_token:                               # UX2 fix 5: a platform-bot channel while the server has no bot token
        raise RuntimeError("Telegram alerts are not set up on this server yet")
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS)
    try:
        response = await client.post(
            f"{TELEGRAM_API}/bot{config.bot_token}/sendMessage",
            json={"chat_id": chat_id or config.chat_id, "text": html_text, "parse_mode": "HTML", "disable_web_page_preview": True,
                  **({"reply_markup": reply_markup} if reply_markup else {})},
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
            # Verify the server certificate and host name: without a context starttls() accepts any certificate,
            # so an on-path attacker could read the SMTP password (gateway review LOW-2).
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        if config.username:
            smtp.login(config.username, config.password or "")
        smtp.send_message(message)


async def send_email(config: EmailConfig, subject: str, plain_text: str, to: Optional[List[str]] = None,
                     headers: Optional[Dict[str, str]] = None) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = str(config.from_address)
    message["To"] = ", ".join(to if to is not None else [str(a) for a in config.to_addresses])
    for name, value in (headers or {}).items():
        message[name] = value
    message.set_content(plain_text)
    host = config.smtp_host
    await check_url_resolved(f"https://[{host}]" if ":" in host else f"https://{host}")   # P0.2 / S5: no private SMTP host
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
        "metadata": json.loads(metadata) if (metadata := getattr(notification, "metadata_json", None)) else None,
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
                       alert: Optional[dict] = None, idempotency_key: Optional[str] = None) -> None:
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
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key                # Z10-b: the same on every retry of this delivery
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


_PLACEHOLDER = re.compile(r"\{(to|text|title|severity|event_type|dlt_entity_id|dlt_template_id|sender_id|template|language)\}")


def _fill_template(template: str, values: dict) -> str:
    """Only the documented placeholders are substituted; every other brace (the JSON body itself)
    is left exactly as typed - `str.format` would choke on it."""
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def render_sms(config: Union[SmsConfig, WhatsAppConfig], notification: NotificationRecord, to_number: str) -> Tuple[str, Optional[str]]:
    """(body, query) for one recipient: the template with placeholders filled and JSON-escaped when
    the content type is JSON, so an alert message containing quotes cannot break the request."""
    plain, _ = render_text(notification)
    text = plain[: config.max_length]
    values = {"to": to_number, "text": text, "title": notification.title[:120], "severity": notification.severity,
              "event_type": notification.event_type}
    for field in DLT_FIELDS:                                         # S10: India DLT registration (empty when not set)
        values[field] = str(getattr(config, field, None) or "")
    if isinstance(config, WhatsAppConfig):                           # S10: the approved template and its language
        values.update(template=config.template_name, language=config.language)
    else:
        values.update(template="", language="")
    if "json" in (config.content_type or "").lower():
        values = {k: json.dumps(v)[1:-1] for k, v in values.items()}  # escape for insertion inside JSON strings
    else:
        # Form bodies, query strings and anything else: every substituted value is percent-encoded, so an alert title
        # such as "x&To=+1555...&Body=spam" stays ONE value and cannot add or override a parameter.
        values = {k: quote(str(v), safe="") for k, v in values.items()}
    return _fill_template(config.body_template, values), None


async def send_sms(config: Union[SmsConfig, WhatsAppConfig], notification: NotificationRecord, client: Optional[httpx.AsyncClient] = None,
                   idempotency_key: Optional[str] = None) -> None:
    """Phase O3: one gateway request per recipient; the first failure is raised after trying all. S10: also the
    WhatsApp gateway (POST only, its approved template filled in)."""
    kind = "WhatsApp gateway" if isinstance(config, WhatsAppConfig) else "SMS gateway"
    method = "POST" if isinstance(config, WhatsAppConfig) else config.method
    await check_url_resolved(str(config.url))                       # P0.2 / S5
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    errors: List[str] = []
    try:
        for number in config.to_numbers:
            body, _ = render_sms(config, notification, number)
            headers = {"Content-Type": config.content_type, "User-Agent": "ATP-Alerts/1.0", **config.headers}
            if idempotency_key:
                headers["Idempotency-Key"] = hashlib.sha256(f"{idempotency_key}|{number}".encode()).hexdigest()  # one per recipient
            try:
                if method == "GET":
                    # decoded once here (httpx encodes again); duplicates kept as sent, so nothing is silently overridden
                    response = await client.get(str(config.url), params=httpx.QueryParams(tuple(parse_qsl(body, keep_blank_values=True))),
                                                headers=headers)
                else:
                    response = await client.post(str(config.url), content=body.encode(), headers=headers)
                if response.status_code >= 300:
                    errors.append(f"{number}: HTTP {response.status_code}")       # the gateway's body is never echoed (P0.2 / S5)
            except Exception as exc:  # noqa: BLE001 - one recipient's failure never stops the others
                errors.append(f"{number}: {type(exc).__name__}")
    finally:
        if owns_client:
            await client.aclose()
    if errors:
        raise RuntimeError(f"{kind}: " + "; ".join(errors))


async def send_push(config: PushConfig, notification: NotificationRecord, client: Optional[httpx.AsyncClient] = None) -> List[str]:
    """Phase O3: every registered device; returns the endpoints the push service reports gone so
    the caller can prune them. Raises only when no device could be reached."""
    payload = {"title": f"[{notification.severity}] {notification.title}", "body": notification.message[:300],
               "event_type": notification.event_type, "severity": notification.severity, "notification_id": notification.id,
               "url": app_path(notification)}                        # PW3: opens the exact screen (a same-origin path)
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
        raise RuntimeError("Web Push: every registered device has unsubscribed - enable push again under Settings > Alerts")
    return gone


def _message_id(idempotency_key: str, address: Optional[str] = None) -> str:
    """Z10-b: the same Message-ID on every retry of one delivery (one per recipient), so a mail server can drop a repeat."""
    token = hashlib.sha256(f"{idempotency_key}|{(address or '').strip().lower()}".encode()).hexdigest()[:40] if address else idempotency_key
    return f"<{token}@atp-alerts>"


async def _send_screen_alert_email(session: Optional[AsyncSession], channel: AlertChannelRecord, config: EmailConfig, subject: str,
                                   plain: str, idempotency_key: Optional[str] = None) -> None:
    """S3b-2: one mail per recipient, each with its own signed unsubscribe link (when PUBLIC_BASE_URL is set);
    addresses that opted out are skipped."""
    from app.alerts.links import opted_out, unsubscribe_url
    recipients = [str(a) for a in config.to_addresses]
    skip = await opted_out(session, channel.tenant_id, recipients) if session is not None else set()
    for address in recipients:
        if address.strip().lower() in skip:
            continue
        extra = {"Message-ID": _message_id(idempotency_key, address)} if idempotency_key else None
        url = unsubscribe_url(channel.tenant_id, address)
        if url is None:
            await send_email(config, subject, plain, to=[address], headers=extra)
            continue
        body = f"{plain}\n\nStop screen-alert emails to this address: {url}"
        await send_email(config, subject, body, to=[address],
                         headers={"List-Unsubscribe": f"<{url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click", **(extra or {})})


async def send_via_channel(
    channel: AlertChannelRecord, notification: NotificationRecord, client: Optional[httpx.AsyncClient] = None, alert: Optional[dict] = None,
    address: Optional[str] = None, session: Optional[AsyncSession] = None, idempotency_key: Optional[str] = None,
) -> None:
    """Sends one notification through one channel. Raises on failure with a message safe to store.
    A PUSH channel whose devices have gone gets its config rewritten in place (caller commits).
    `idempotency_key` (Z10-b) goes to receivers that can dedupe a repeated send: webhook / SMS headers, email Message-ID."""
    config = decrypt_config(channel)
    await s10_gate.enforce(session, channel.tenant_id, channel.channel_type, config)   # S10: WhatsApp / SMS DLT, every path
    plain, html_body = render_text(notification)
    mail_headers = {"Message-ID": _message_id(idempotency_key)} if idempotency_key else None
    if channel.channel_type == AlertChannelType.TELEGRAM.value:
        if address and address == config.chat_id:  # type: ignore[union-attr]
            return                                         # S3b-2: the linked chat is the channel's own chat - sent once already
        row = open_in_app_row(notification)                # PW4: every Telegram alert opens its screen in the app
        await send_telegram(config, html_body, client, chat_id=address,  # type: ignore[arg-type]
                            reply_markup={"inline_keyboard": [row]} if row else None)
        if session is not None and address and notification.event_type == NotificationType.SCREEN_ALERT.value:
            # U8 EV2: the evidence images to the rule creator's own linked chat (flag evidence_charts; best effort, never raises)
            from app.screener.evidence_store import alert_photos
            await alert_photos(session, channel, str(config.bot_token), notification, address, client)  # type: ignore[union-attr]
    elif channel.channel_type == AlertChannelType.EMAIL.value:
        subject = f"[{notification.severity}] {notification.title}"
        if notification.event_type == NotificationType.SCREEN_ALERT.value:
            await _send_screen_alert_email(session, channel, config, subject, plain, idempotency_key)  # type: ignore[arg-type]
        else:
            await send_email(config, subject, plain, headers=mail_headers)  # type: ignore[arg-type]
    elif channel.channel_type == AlertChannelType.WEBHOOK.value:
        await send_webhook(config, notification, client, alert, idempotency_key=idempotency_key)  # type: ignore[arg-type]
    elif channel.channel_type == AlertChannelType.PUSH.value:
        gone = await send_push(config, notification, client)  # type: ignore[arg-type]
        if gone:
            remaining = [s for s in config.subscriptions if str(s.endpoint) not in gone]  # type: ignore[union-attr]
            if remaining:
                channel.encrypted_config = encrypt_config(PushConfig(subscriptions=remaining, categories=config.categories),  # type: ignore[union-attr]
                                                          channel.tenant_id)
            logger.info("Pruned %d gone push subscription(s) for tenant %s", len(gone), channel.tenant_id)
    elif channel.channel_type in (AlertChannelType.SMS.value, AlertChannelType.WHATSAPP.value):
        await send_sms(config, notification, client, idempotency_key=idempotency_key)  # type: ignore[arg-type]
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
        return await telegram_inbound.send_proposal_buttons(session, channel, action, html_body, client,
                                                            extra_row=open_in_app_row(notification))
    except RuntimeError:
        raise                                         # Telegram refused the send: the ordinary retry path
    except Exception as exc:  # noqa: BLE001 - a bug in the buttons must not stop the alert itself
        logger.warning("Telegram proposal buttons failed (%s); sending the plain alert", exc)
        await session.rollback()
        return False


async def _telegram_oi_buttons(session: AsyncSession, channel: AlertChannelRecord, notification: NotificationRecord,
                               client: Optional[httpx.AsyncClient]) -> bool:
    """OI Banner O4b: an OI alert to Telegram carries read-only buttons (open chain / chart links, snooze / mute
    callbacks). False -> the plain send. A failure to build the buttons never stops the alert itself."""
    if channel.channel_type != AlertChannelType.TELEGRAM.value or notification.event_type not in (NotificationType.OI_BANNER.value, NotificationType.OI_COLLECTOR.value):
        return False
    from app.core import config as app_config
    from app.option_chain import oi_alerts
    from app.telegram_inbound import service as telegram_inbound
    try:
        cfg = decrypt_config(channel)
        meta = json.loads(notification.metadata_json) if notification.metadata_json else {}
        underlying = str(meta.get("underlying") or "")
        if not underlying:
            return False
        from app.option_chain import snapshots
        _, own = await snapshots.settings_rows(session, channel.tenant_id, underlying)
        exchange = own.exchange if own is not None else "NSE"
        keyboard = oi_alerts.telegram_keyboard(underlying, exchange, app_config.FRONTEND_URL, bool(getattr(cfg, "inbound_enabled", False)))
        if keyboard is None:
            return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegram OI buttons not built (%s); sending the plain alert", exc)
        return False
    _, html_body = render_text(notification)
    answer = await telegram_inbound.reply(cfg, cfg.chat_id, html_body, client, reply_markup=keyboard)  # type: ignore[union-attr, arg-type]
    if not answer.get("ok", False):
        raise RuntimeError(f"Telegram API: {answer.get('description') or 'send failed'}")
    return True


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

@dataclass
class Claim:
    """Z10-b: one claim of one row - "ok" (this worker holds it under `token` until `lease_until`), "taken" or "lost"."""
    state: str
    delivery_id: int
    token: Optional[str] = None
    lease_until: Optional[datetime] = None


async def _claim(session: AsyncSession, delivery: AlertDeliveryRecord) -> Claim:
    """Z10-b: take the row for this worker - "ok", "taken" (another worker holds a live lease, or it is no longer
    PENDING) or "lost" (its lease ran out on the last attempt: DEAD, reason worker_lost). One conditional UPDATE each, so
    of two workers racing for a row exactly one wins. The lease runs from the real clock at the moment of the claim (never
    from the drain's start: a slow drain must not hand out a lease that is already over), and `claimed_by` carries a
    token of this claim, so only its holder can write the outcome (`_finish`). The attempt is counted here, so a send
    that kills its worker still uses up an attempt and a poison row cannot loop for ever."""
    delivery_id = delivery.id
    clock = _utcnow()
    token = f"{WORKER_ID}:{secrets.token_hex(6)}"
    lease_until = clock + timedelta(seconds=LEASE_SECONDS)
    free = or_(AlertDeliveryRecord.lease_until.is_(None), AlertDeliveryRecord.lease_until <= clock)
    values: Dict[str, Any] = {"lease_until": lease_until, "claimed_by": token, "attempts": AlertDeliveryRecord.attempts + 1}
    if delivery.idem_key is None:                          # a row queued before the flag: keyed on its first claim
        values["idem_key"] = delivery_key(delivery.notification_id, delivery.channel_id, delivery.address)
    claim = (update(AlertDeliveryRecord)
             .where(AlertDeliveryRecord.id == delivery_id, AlertDeliveryRecord.status == AlertDeliveryStatus.PENDING.value, free,
                    AlertDeliveryRecord.attempts < MAX_ATTEMPTS)
             .values(**values).execution_options(synchronize_session=False))
    try:
        async with session.begin_nested():                 # a failed claim rolls back to here only: the drain's rows stay loaded
            mine = await session.execute(claim)
    except IntegrityError:                                 # another row already holds this delivery's key: a duplicate
        await session.execute(update(AlertDeliveryRecord).where(
            AlertDeliveryRecord.id == delivery_id, AlertDeliveryRecord.status == AlertDeliveryStatus.PENDING.value)
            .values(status=AlertDeliveryStatus.SUPPRESSED.value, reason_code="duplicate", lease_until=None,
                    last_error="The same delivery is already queued (same notification, channel and address)")
            .execution_options(synchronize_session=False))
        await session.commit()
        await session.refresh(delivery)
        return Claim("taken", delivery_id)
    state = "ok" if getattr(mine, "rowcount", 0) == 1 else "taken"
    if state == "taken":
        lost = await session.execute(
            update(AlertDeliveryRecord)
            .where(AlertDeliveryRecord.id == delivery_id, AlertDeliveryRecord.status == AlertDeliveryStatus.PENDING.value, free,
                   AlertDeliveryRecord.attempts >= MAX_ATTEMPTS)
            .values(status=AlertDeliveryStatus.DEAD.value, reason_code="worker_lost", lease_until=None,
                    last_error="The worker sending the last attempt stopped before it finished; it may or may not have arrived")
            .execution_options(synchronize_session=False))
        state = "lost" if getattr(lost, "rowcount", 0) == 1 else "taken"
    await session.commit()                                 # the claim is visible to every other worker before the send
    await session.refresh(delivery)
    return Claim(state, delivery_id, token, lease_until) if state == "ok" else Claim(state, delivery_id)


def _send_budget(claim: Claim) -> float:
    """Seconds this claim may still spend sending: from now to its lease's end, less `LEASE_MARGIN_SECONDS`. Raises
    TimeoutError when nothing is left, so the row is retried (with the attempt counted) instead of sent late."""
    left = (claim.lease_until - _utcnow()).total_seconds() - LEASE_MARGIN_SECONDS if claim.lease_until is not None else 0.0
    if left <= 0:
        raise TimeoutError("lease nearly over before the send started")
    return left


async def _finish(session: AsyncSession, delivery: AlertDeliveryRecord, claim: Optional[Claim], **values: Any) -> bool:
    """Writes a delivery's outcome. Without the flag (`claim` None): on the loaded row, as before. With it: one
    conditional UPDATE that only the claim's holder can make (`claimed_by` = this claim's token, still PENDING) - a
    worker whose lease ran out and was taken over writes nothing (False, logged). The caller commits."""
    if claim is None:
        for name, value in values.items():
            setattr(delivery, name, value)
        return True
    values["lease_until"] = None
    done = await session.execute(update(AlertDeliveryRecord).where(
        AlertDeliveryRecord.id == claim.delivery_id, AlertDeliveryRecord.claimed_by == claim.token,
        AlertDeliveryRecord.status == AlertDeliveryStatus.PENDING.value).values(**values).execution_options(synchronize_session=False))
    if getattr(done, "rowcount", 0) != 1:
        logger.warning("Alert delivery %s: this worker's claim was taken over before it finished; outcome %s not written",
                       claim.delivery_id, values.get("status"))
        return False
    return True


async def _stale_alert(session: AsyncSession, notification: NotificationRecord, now: datetime, holidays: List[Set[date]]) -> Optional[str]:
    """Z10-b: why this screen / instrument alert may no longer be sent (its newest bar is no longer current), or None."""
    from app.db.models import AlertEventRecord, AlertRuleRecord
    events = list(await session.scalars(select(AlertEventRecord).where(AlertEventRecord.notification_id == notification.id)))
    if not events:
        return "the alert's data time is unknown (no alert events behind it)"
    rule = await session.get(AlertRuleRecord, events[0].rule_id)
    if not holidays:
        from app.market_data.calendar import load_holidays
        holidays.append(await load_holidays(session, "NSE"))
    # The timeframe recorded with the events (Z10-b review: the rule may have been edited since); the rule's for older rows.
    tf = next((e.base_tf for e in events if e.base_tf), rule.base_tf if rule is not None else None)
    badge = staleness.alert_badge(max(_as_utc(e.bar_time) for e in events), tf, now, holidays[0])
    return badge["stale_reason"] if badge["alerts_suppressed"] else None


async def dispatch_pending(
    session: AsyncSession, *, limit: int = 50, client: Optional[httpx.AsyncClient] = None, now: Optional[datetime] = None,
    tenant_id: Optional[int] = None,
) -> Tuple[int, int]:
    """Sends every due PENDING delivery once, returning (sent, failed_this_round). A failure
    schedules a retry with exponential backoff (30s, 60s, 120s, ...) until MAX_ATTEMPTS, then the
    row is FAILED for good. One bad channel never delays another: each row commits on its own.
    `tenant_id` narrows the drain to one tenant (the worker passes none: platform-wide).
    Z10-b: claimed with a lease, DEAD after the last attempt (`alerts_reliable_outbox`); a stale screen alert is
    SUPPRESSED instead of sent (`alerts_stale_guard`). Both per organisation; see the module docstring."""
    now = now or _utcnow()
    query = (
        select(AlertDeliveryRecord)
        .where(AlertDeliveryRecord.status == AlertDeliveryStatus.PENDING.value, AlertDeliveryRecord.next_attempt_at <= now,
               or_(AlertDeliveryRecord.lease_until.is_(None), AlertDeliveryRecord.lease_until <= _utcnow()))   # claimed: someone else's
    )
    if tenant_id is not None:
        query = query.where(AlertDeliveryRecord.tenant_id == tenant_id)
    due = list(await session.scalars(query.order_by(AlertDeliveryRecord.id).limit(limit)))
    sent = failed = 0
    hardened: Dict[int, bool] = {}
    guard: Dict[int, bool] = {}
    holidays: List[Set[date]] = []
    for delivery in due:
        claim: Optional[Claim] = None
        if await outbox_hardened(session, delivery.tenant_id, hardened):
            claim = await _claim(session, delivery)
            if claim.state == "taken":
                continue                                   # another worker is sending it (or already did)
            if claim.state == "lost":
                ALERT_DELIVERIES.labels(channel="unknown", status=AlertDeliveryStatus.DEAD.value).inc()
                logger.error("Alert delivery %s: its last attempt's worker was lost; dead-lettered", delivery.id)
                failed += 1
                continue
        channel = await session.get(AlertChannelRecord, delivery.channel_id)
        notification = await session.get(NotificationRecord, delivery.notification_id)
        if channel is not None:
            await ensure_tenant_key(session, channel.tenant_id)  # Phase N1
        if channel is None or notification is None or not channel.enabled:
            await _finish(session, delivery, claim, status=AlertDeliveryStatus.FAILED.value, reason_code="channel_disabled",
                          last_error="Channel disabled or removed before delivery")
            await session.commit()
            failed += 1
            continue
        if notification.event_type in staleness.SUPPRESSIBLE and delivery.digest_bucket is None \
                and await staleness.guard_on(session, delivery.tenant_id, guard):
            why = await _stale_alert(session, notification, now, holidays)
            if why is not None:
                if await _finish(session, delivery, claim, status=AlertDeliveryStatus.SUPPRESSED.value, reason_code=staleness.REASON,
                                 last_error=why[:500]):
                    ALERT_DELIVERIES.labels(channel=channel.channel_type, status=AlertDeliveryStatus.SUPPRESSED.value).inc()
                    staleness.count_suppressed("alert_send")
                    logger.info("Alert delivery %s suppressed: %s", delivery.id, why)
                await session.commit()
                continue
        attempts = delivery.attempts if claim is not None else delivery.attempts + 1   # reliable: counted at the claim
        try:
            if claim is not None:
                # The send must end while the lease is still ours: a send still running at the lease's end could be
                # repeated by the worker that takes the row over. The budget is what is left of the lease now (slow DB
                # steps since the claim have used part of it), less a margin; none left = no send this round.
                budget = _send_budget(claim)
                await asyncio.wait_for(_send_one(session, channel, notification, delivery, client, delivery.idem_key), timeout=budget)
            else:
                await _send_one(session, channel, notification, delivery, client, None)
        except s10_gate.Refused as refused:
            # S10 (checked in send_via_channel, inside this try): the flag was turned off or the plan no longer pays for
            # WhatsApp - nothing is sent and the reason is kept; not retried.
            channel.last_error = refused.reason[:500]
            if await _finish(session, delivery, claim, status=AlertDeliveryStatus.SUPPRESSED.value, reason_code=s10_gate.REASON_CODE,
                             last_error=refused.reason[:500]):
                ALERT_DELIVERIES.labels(channel=channel.channel_type, status=AlertDeliveryStatus.SUPPRESSED.value).inc()
        except Exception as exc:  # noqa: BLE001 - every failure mode becomes a retry or a FAILED row (an undecryptable
            # channel included: one organisation's broken channel never stops another's deliveries)
            error = (str(exc) or type(exc).__name__)[:500]
            channel.last_error = error
            outcome: Dict[str, Any] = {"attempts": attempts, "last_error": error}
            if attempts >= MAX_ATTEMPTS:
                outcome["status"] = AlertDeliveryStatus.DEAD.value if claim is not None else AlertDeliveryStatus.FAILED.value
                outcome["reason_code"] = "dead_letter"         # S3b: kept for the operator; retry from the dead-letter list
                if await _finish(session, delivery, claim, **outcome):
                    ALERT_DELIVERIES.labels(channel=channel.channel_type, status=outcome["status"]).inc()
                    logger.error("Alert delivery %s gave up after %d attempts: %s", delivery.id, attempts, exc)
            else:
                outcome["next_attempt_at"] = now + timedelta(seconds=BASE_BACKOFF_SECONDS * (2 ** (attempts - 1)))
                if await _finish(session, delivery, claim, **outcome):
                    logger.warning("Alert delivery %s failed (attempt %d): %s", delivery.id, attempts, exc)
            failed += 1
        else:
            if await _finish(session, delivery, claim, attempts=attempts, status=AlertDeliveryStatus.SENT.value, sent_at=now, last_error=None):
                ALERT_DELIVERIES.labels(channel=channel.channel_type, status="SENT").inc()
            channel.last_delivered_at = now
            channel.last_error = None
            sent += 1
        await session.commit()
    if sent or failed:
        logger.info("Alert dispatch: %d sent, %d failed/retried", sent, failed)
    return sent, failed


async def _send_one(session: AsyncSession, channel: AlertChannelRecord, notification: NotificationRecord, delivery: AlertDeliveryRecord,
                    client: Optional[httpx.AsyncClient], idempotency_key: Optional[str]) -> None:
    if not await _telegram_proposal_buttons(session, channel, notification, client) \
            and not await _telegram_oi_buttons(session, channel, notification, client):
        await send_via_channel(channel, notification, client, await alert_context(session, notification), address=delivery.address,
                               session=session, idempotency_key=idempotency_key)
