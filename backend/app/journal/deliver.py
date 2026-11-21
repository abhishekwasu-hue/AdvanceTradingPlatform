"""P2-8 J6: the journal reviews on his own Telegram chat and / or e-mail, when he turned them on (default OFF).

* daily   - the day's card, on trading days after his `daily_hour` (IST);
* weekly  - the week's lines, on his `weekly_day` from 09:00 IST;
* monthly - the month just ended, from the 1st to the 3rd of the next month, 09:00 IST;
* check-in prompts - one short line when a trade of his opens / closes and its check-in is waiting.

Choices: user_prefs `journal_delivery` (app/me/prefs.py). "Already sent" marks: the internal row `journal_delivery_sent`
(no route reads it). The messages carry counts and results from his own trades and the one fact done well; never his
feelings, notes or lines. Telegram goes only to his OWN chat (`rhythm.deliver.destination`); e-mail to his own address.
Flags `ui_v2` + `discipline_engine` per organisation. Read-only towards trading; the worker's cycle never waits on it.
"""
from __future__ import annotations

import html
import json
import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserPrefsRecord
from app.journal import checkin, review_data, reviews, service
from app.journal import settings as journal_settings
from app.market_data.calendar import IST, is_trading_day

logger = logging.getLogger(__name__)

PREFS = "journal_delivery"
SENT = "journal_delivery_sent"
REVIEW_FROM = time(9, 0)
PROMPT_MARKS = 60
DAYS = {"SAT": 5, "SUN": 6}


def due(prefs: dict[str, Any], now: datetime, holidays: set[date]) -> dict[str, str]:
    """The reviews due now -> their period label (pure)."""
    ist = now.astimezone(IST)
    out: dict[str, str] = {}
    if prefs.get("daily") and is_trading_day(ist.date(), holidays) and ist.hour >= int(prefs.get("daily_hour") or 17):
        out["daily"] = ist.date().isoformat()
    if prefs.get("weekly") and ist.weekday() == DAYS.get(str(prefs.get("weekly_day") or "SAT"), 5) and ist.time() >= REVIEW_FROM:
        iso = ist.date().isocalendar()
        out["weekly"] = f"{iso[0]}-W{iso[1]:02d}"
    if prefs.get("monthly") and ist.day <= 3 and ist.time() >= REVIEW_FROM:
        last = ist.date().replace(day=1) - timedelta(days=1)
        out["monthly"] = last.strftime("%Y-%m")
    return out


def message(kind: str, label: str, review: dict[str, Any]) -> tuple[str, list[str]]:
    """(title, lines) of one review as sent: its counts and results and the fact done well - no feelings (pure)."""
    title = {"daily": f"Your day ({label})", "weekly": f"Your week ({label})", "monthly": f"Your month ({label})"}[kind]
    lines = list(review.get("lines") or [])
    gate = review.get("gate") or {}
    if gate.get("note"):
        lines.insert(0, str(gate["note"]))
    if review.get("done_well"):
        lines.append(f"Done well: {review['done_well']}")
    return title, lines


def prompt_text(item: dict[str, Any]) -> str:
    return f"{item['symbol']} {'opened' if item['moment'] == 'open' else 'closed'}: your 20-second check-in is waiting in the Journal."


async def _review(session: AsyncSession, user: User, kind: str, label: str) -> dict[str, Any]:
    s = await journal_settings.load(session, user)
    g = await review_data.gate(session, user, s)
    if kind == "daily":
        start, end = review_data.day_bounds(date.fromisoformat(label))
        return reviews.daily_card(label, await review_data.units(session, user, start, end), g)
    if kind == "weekly":
        _, start, end = service.week_bounds(label)
        return reviews.weekly_extras(label, await review_data.units(session, user, start, end), g)
    start, end = review_data.month_bounds(label)
    return reviews.monthly(label, await review_data.units(session, user, start, end), g, [])


async def _marks(session: AsyncSession, user: User) -> tuple[UserPrefsRecord | None, dict[str, Any]]:
    row = await session.scalar(select(UserPrefsRecord).where(UserPrefsRecord.user_id == user.id, UserPrefsRecord.tenant_id == user.tenant_id,
                                                             UserPrefsRecord.namespace == SENT))
    try:
        value = json.loads(row.value_json) if row else {}
    except ValueError:
        value = {}
    return row, value if isinstance(value, dict) else {}


async def _save_marks(session: AsyncSession, user: User, row: UserPrefsRecord | None, marks: dict[str, Any], now: datetime) -> None:
    value = json.dumps(marks, sort_keys=True)
    if row is None:
        session.add(UserPrefsRecord(tenant_id=user.tenant_id, user_id=user.id, namespace=SENT, value_json=value, version=1, updated_at=now))
    else:
        row.value_json, row.version, row.updated_at = value, row.version + 1, now
    await session.commit()


async def _send(session: AsyncSession, user: User, prefs: dict[str, Any], title: str, lines: list[str],
                client: httpx.AsyncClient | None) -> bool:
    sent = False
    if prefs.get("telegram"):
        from app.alerts.channels import decrypt_config
        from app.alerts.dispatcher import send_telegram
        from app.rhythm.build import Me
        from app.rhythm.deliver import destination
        target = await destination(session, Me(id=int(user.id), tenant_id=int(user.tenant_id), role=str(user.role or "USER")))
        if target is not None:
            channel, chat = target
            body = f"<b>{html.escape(title)}</b>\n" + "\n".join(f"• {html.escape(line)}" for line in lines)
            await send_telegram(decrypt_config(channel), body, client, chat_id=chat)  # type: ignore[arg-type]
            sent = True
    if prefs.get("email") and user.email:
        from app.notifications import mailer
        sent = await mailer.send(user.email, title, "\n".join([title, "", *lines])) or sent
    return sent


async def send_user(session: AsyncSession, user: User, prefs: dict[str, Any], now: datetime, holidays: set[date],
                    client: httpx.AsyncClient | None = None) -> int:
    """Sends what is due for one user (each once). Returns how many messages went out."""
    count = 0
    row, marks = await _marks(session, user)
    for kind, label in due(prefs, now, holidays).items():
        if marks.get(kind) == label:
            continue
        title, lines = message(kind, label, await _review(session, user, kind, label))
        if await _send(session, user, prefs, title, lines, client):
            count += 1
        row, marks = await _marks(session, user)         # read again: a review's reads may have expired the row
        marks[kind] = label
        await _save_marks(session, user, row, marks, now)
    if prefs.get("checkin_prompts"):
        done: list[str] = list(marks.get("prompts") or [])
        for item in await checkin.waiting(session, user, now=now):
            key = f"{item['kind']}:{item['id']}:{item['moment']}"
            if key in done:
                continue
            if await _send(session, user, prefs, "Journal", [prompt_text(item)], client):
                count += 1
            done = (done + [key])[-PROMPT_MARKS:]
            row, marks = await _marks(session, user)
            marks["prompts"] = done
            await _save_marks(session, user, row, marks, now)
    return count


async def send_due(session: AsyncSession, now: datetime | None = None, client: httpx.AsyncClient | None = None) -> int:
    """Every opted-in user's due journal messages. One user's failure never stops the others (retried next run)."""
    from app.market_data.calendar import family_holidays
    from app.me.prefs import stored_value
    from app.platform.controls import flag_enabled
    now = now or datetime.now(UTC)
    rows = [(r.tenant_id, r.user_id, r.value_json) for r in await session.scalars(
        select(UserPrefsRecord).where(UserPrefsRecord.namespace == PREFS).order_by(UserPrefsRecord.id))]
    if not rows:
        return 0
    holidays = set(await family_holidays(session, "NSE"))
    memo: dict[int, bool] = {}
    sent = 0
    for tenant_id, user_id, raw in rows:
        prefs = stored_value(PREFS, raw)
        if not (prefs.get("telegram") or prefs.get("email")):
            continue
        if tenant_id not in memo:
            from app.journal import FLAG
            memo[tenant_id] = await flag_enabled(session, "ui_v2", tenant_id) and await flag_enabled(session, FLAG, tenant_id)
        if not memo[tenant_id]:
            continue
        user = await session.get(User, user_id)
        if user is None or not user.is_active or user.tenant_id != tenant_id:
            continue
        try:
            sent += await send_user(session, user, prefs, now, holidays, client)
        except Exception as exc:  # noqa: BLE001 - one user's send never stops the others
            logger.warning("journal delivery for user %s not sent: %s", user_id, type(exc).__name__)
            await session.rollback()
    return sent


__all__ = ["PREFS", "SENT", "due", "message", "prompt_text", "send_user", "send_due"]
