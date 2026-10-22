"""The daily rhythm on Telegram: opt-in per user (default OFF), on the alert channel the user already connected.

* The choice: the user's `daily_rhythm` preferences (`telegram` on, and which digests). Nothing is sent while
  `telegram` is off, and nothing at all while `ui_v2` or `daily_rhythm` is off for the organisation.
* Where it goes (`destination`): the user's own chat only, never another member's - the chat the user linked to the
  organisation's own bot (S3b-2), else the organisation's Telegram chat when this user is the one who connected it
  (the platform bot's "Connect Telegram", or the organisation's own bot set up by this user). Otherwise nothing is sent
  and Settings → Alerts says "Connect Telegram first".
* When: once per digest in its window on trading days (`schedule.due_kinds`), the weekly review on the weekend; the
  "sent" marks (`daily_rhythm_sent`, an internal `user_prefs` row) keep a restarted worker from sending twice. A send
  that fails is tried again on the next run inside the window.
* Nothing when there is nothing: a digest whose sections are all empty is not sent.

The message is short HTML (titles, bullet lines, the note) with an "Open in app" button when the app address is set.
It never carries an order button: the rhythm informs; Telegram approvals stay with the organisation's own bot.
"""
from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AlertChannelRecord, NotificationLinkRecord, User, UserPrefsRecord
from app.rhythm import FLAG, KINDS, schedule

logger = logging.getLogger(__name__)

PREFS = "daily_rhythm"
SENT = "daily_rhythm_sent"           # internal: not in app/me/prefs.py NAMESPACES, so no route reads or writes it
MAX_LINES_PER_SECTION = 4
MAX_CHARS = 3500                     # Telegram's limit is 4096


def render_html(brief: Dict[str, Any], app_base: Optional[str] = None) -> str:
    out: List[str] = [f"<b>{html.escape(brief['title'])}</b>", f"<i>{html.escape(brief['subtitle'])}</i>"]
    for sec in brief["sections"]:
        if not sec["lines"]:
            continue
        out.append("")
        out.append(f"<b>{html.escape(sec['title'])}</b>")
        out.extend(f"• {html.escape(line)}" for line in sec["lines"][:MAX_LINES_PER_SECTION])
    out.append("")
    out.append(f"<i>{html.escape(brief['note'])}</i>")
    text = "\n".join(out)
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS - 1] + "…"


def open_button(app_base: Optional[str]) -> Optional[dict]:
    return {"inline_keyboard": [[{"text": "Open in app", "url": f"{app_base}/"}]]} if app_base else None


async def destination(session: AsyncSession, user: Any) -> Optional[Tuple[AlertChannelRecord, str]]:
    """(the organisation's Telegram channel, the chat id that is this user's own) or None."""
    from app.alerts import links, platform_bot
    from app.alerts.channels import decrypt_raw
    from app.secrets_store.envelope import ensure_tenant_key
    channel = await platform_bot.telegram_channel(session, user.tenant_id)
    if channel is None or not channel.enabled:
        return None
    await ensure_tenant_key(session, user.tenant_id)
    try:
        raw = decrypt_raw(channel)
    except ValueError:
        return None
    link = await links.linked_chat(session, user.tenant_id, user.id)
    if link is not None and link.chat_id and not raw.get("platform_bot"):
        return channel, str(link.chat_id)
    chat = str(raw.get("chat_id") or "")
    if not chat or chat == "0":
        return None
    if raw.get("platform_bot"):
        connected_by = await session.scalar(select(NotificationLinkRecord.user_id).where(
            NotificationLinkRecord.tenant_id == user.tenant_id, NotificationLinkRecord.channel == platform_bot.CHANNEL,
            NotificationLinkRecord.status == "used", NotificationLinkRecord.chat_id == chat).order_by(NotificationLinkRecord.id.desc()).limit(1))
        owner = connected_by if connected_by is not None else channel.created_by
        return (channel, chat) if owner == user.id else None
    return (channel, chat) if channel.created_by == user.id else None


async def read_marks(session: AsyncSession, user: Any) -> Tuple[Optional[UserPrefsRecord], Dict[str, str]]:
    row = await session.scalar(select(UserPrefsRecord).where(UserPrefsRecord.tenant_id == user.tenant_id, UserPrefsRecord.user_id == user.id,
                                                             UserPrefsRecord.namespace == SENT))
    try:
        marks = json.loads(row.value_json) if row else {}
    except ValueError:
        marks = {}
    return row, {k: str(v) for k, v in marks.items() if k in KINDS} if isinstance(marks, dict) else {}


async def write_mark(session: AsyncSession, user: Any, row: Optional[UserPrefsRecord], marks: Dict[str, str], now: datetime) -> UserPrefsRecord:
    value = json.dumps(marks, sort_keys=True)
    if row is None:
        row = UserPrefsRecord(tenant_id=user.tenant_id, user_id=user.id, namespace=SENT, value_json=value, version=1, updated_at=now)
        session.add(row)
    else:
        row.value_json, row.version, row.updated_at = value, row.version + 1, now
    await session.commit()
    return row


async def flags_on(session: AsyncSession, tenant_id: int, memo: Dict[int, bool]) -> bool:
    if tenant_id not in memo:
        from app.platform.controls import flag_enabled
        memo[tenant_id] = await flag_enabled(session, "ui_v2", tenant_id) and await flag_enabled(session, FLAG, tenant_id)
    return memo[tenant_id]


async def send_due(session: AsyncSession, now: Optional[datetime] = None, client: Optional[httpx.AsyncClient] = None) -> int:
    """Sends each opted-in user's digests that are due now and not sent yet. Returns how many were sent."""
    from app.alerts.deeplink import app_base_url
    from app.market_data.calendar import family_holidays
    from app.me.prefs import stored_value
    now = now or datetime.now(timezone.utc)
    kinds = schedule.due_kinds(now, await family_holidays(session, "NSE"))
    if not kinds:
        return 0
    # Plain values, read once: a rollback below expires every ORM row of this session.
    rows = [(r.tenant_id, r.user_id, r.value_json) for r in await session.scalars(
        select(UserPrefsRecord).where(UserPrefsRecord.namespace == PREFS).order_by(UserPrefsRecord.id))]
    memo: Dict[int, bool] = {}
    sent = 0
    base = app_base_url()
    for tenant_id, user_id, value_json in rows:
        choice = stored_value(PREFS, value_json)
        wanted = [k for k in kinds if choice.get("telegram") and choice.get(k)]
        if not wanted or not await flags_on(session, tenant_id, memo):
            continue
        user = await session.get(User, user_id)
        if user is None or not user.is_active or user.tenant_id != tenant_id:
            continue
        try:
            sent += await _send_user(session, user, wanted, now, base, client)
        except Exception as exc:  # noqa: BLE001 - one user's send never stops the others; retried on the next run
            logger.warning("daily rhythm for user %s not sent: %s", user_id, type(exc).__name__)
            await session.rollback()
    return sent


async def _send_user(session: AsyncSession, user: User, wanted: List[str], now: datetime, base: Optional[str],
                     client: Optional[httpx.AsyncClient]) -> int:
    from app.alerts.channels import decrypt_config
    from app.alerts.dispatcher import send_telegram
    from app.rhythm import build
    from app.rhythm.build import Me
    me = Me(id=int(user.id), tenant_id=int(user.tenant_id), role=str(user.role or "USER"))
    mark_row, marks = await read_marks(session, me)
    sent = 0
    for kind in wanted:
        key = schedule.period_key(kind, now)
        if marks.get(kind) == key:
            continue
        target = await destination(session, me)
        if target is None:
            return sent
        channel, chat = target
        config = decrypt_config(channel)
        brief = await build.build(session, me, kind, now)
        if brief["has_content"]:
            await send_telegram(config, render_html(brief, base), client, chat_id=chat,  # type: ignore[arg-type]
                                reply_markup=open_button(base))
            sent += 1
        mark_row, marks = await read_marks(session, me)     # read again: a reader's rollback expires the row read above
        marks[kind] = key
        mark_row = await write_mark(session, me, mark_row, marks, now)
    return sent


__all__ = ["send_due", "destination", "render_html", "PREFS", "SENT"]
