"""P2-8 J2: the 20-second check-in - the part of the journal only the trader can add.

Every field is optional and one tap (or one line): his feeling before the entry and his confidence (1-5), quick notes
while the trade is open, his feeling after the close, "Did I follow my plan?" (Yes / Partly / No), the mistake category
(the 8 behaviour tags, "other" or his own labels) and one line "what I would do the same / differently", and the setup
name (from the strategy, editable). Nothing is required, nothing blocks a trade, nothing is inferred: no emotional-state
score, no label he did not pick. A detector never writes here; his answer sits beside the auto tags.

Private: only the owner's own routes read it (another member of the organisation, an admin included, gets 404 like for
a row that does not exist); route bodies are never logged; no admin page or analytics export reads this table; it is
in his account export (`me/account.py`) and erased with his account (`retention.service._erase_journal`).

The prompt: an entry opened or closed in the last PROMPT_DAYS days without the matching half answered is "waiting"
(GET /api/journal/checkins/waiting) until he answers or taps "Not now" - the Journal shows it as a card; J6 adds Telegram.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JournalCheckinRecord, JournalManualTradeRecord, TradeRecord, User
from app.journal.entries import EntryError, Ref
from app.journal.settings import TAGS, DisciplineSettings

PLAN_ANSWERS = ("YES", "PARTLY", "NO")
OTHER = "other"
NOTE_MAX = 300
NOTES_MAX = 20
LESSON_MAX = 300
SETUP_MAX = 100
PROMPT_DAYS = 3
FIELDS = ("feeling_before", "confidence", "feeling_after", "plan_followed", "mistake", "lesson", "setup_name")


def mistake_choices(s: DisciplineSettings) -> list[str]:
    """The 8 behaviour tags, "other", then his own labels (in his order, no repeats)."""
    out = list(TAGS) + [OTHER]
    return out + [t for t in s.custom_tags if t not in out]


def _text(name: str, value: Any, longest: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EntryError(f"{name} must be text")
    text = " ".join(value.split())
    if len(text) > longest:
        raise EntryError(f"{name} may have at most {longest} characters")
    return text or None


def clean(body: dict[str, Any], s: DisciplineSettings) -> dict[str, Any]:
    """The fields he sent, checked (pure). A key left out is not touched; null clears it."""
    out: dict[str, Any] = {}
    unknown = sorted(set(body) - set(FIELDS))
    if unknown:
        raise EntryError(f"Unknown check-in field '{unknown[0]}'")
    feelings = {f.lower(): f for f in s.feelings}
    for key in ("feeling_before", "feeling_after"):
        if key in body:
            value = _text(key, body[key], 20)
            if value is not None and value.lower() not in feelings:
                raise EntryError(f"{key} must be one of your feelings: {', '.join(s.feelings) or 'none set'}")
            out[key] = feelings[value.lower()] if value is not None else None
    if "confidence" in body:
        value = body["confidence"]
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5):
            raise EntryError("confidence must be a whole number from 1 to 5")
        out["confidence"] = value
    if "plan_followed" in body:
        value = body["plan_followed"]
        if value is not None and (not isinstance(value, str) or value.upper() not in PLAN_ANSWERS):
            raise EntryError("plan_followed must be YES, PARTLY or NO")
        out["plan_followed"] = value.upper() if isinstance(value, str) else None
    if "mistake" in body:
        value = _text("mistake", body["mistake"], 30)
        if value is not None and value.lower() not in mistake_choices(s):
            raise EntryError("mistake must be a behaviour tag, other, or one of your own labels")
        out["mistake"] = value.lower() if value is not None else None
    if "lesson" in body:
        out["lesson"] = _text("lesson", body["lesson"], LESSON_MAX)
    if "setup_name" in body:
        out["setup_name"] = _text("setup_name", body["setup_name"], SETUP_MAX)
    return out


def _notes(row: JournalCheckinRecord | None) -> list[dict[str, Any]]:
    if row is None or not row.during_json:
        return []
    try:
        value = json.loads(row.during_json)
    except ValueError:
        return []
    return [n for n in value if isinstance(n, dict) and n.get("text")] if isinstance(value, list) else []


def view(row: JournalCheckinRecord | None, s: DisciplineSettings, *, default_setup: str | None = None) -> dict[str, Any]:
    """His check-in as the entry sheet shows it, with the choices to tap (an empty check-in when he has none)."""
    out: dict[str, Any] = {k: (getattr(row, k) if row is not None else None) for k in FIELDS}
    if out["setup_name"] is None:
        out["setup_name_suggested"] = default_setup
    out.update(notes=_notes(row), feelings=list(s.feelings), mistakes=mistake_choices(s), plan_answers=list(PLAN_ANSWERS),
               answered=row is not None and any(getattr(row, k) is not None for k in FIELDS))
    return out


def _filter(user: User, ref: Ref) -> list[Any]:
    where = [JournalCheckinRecord.user_id == user.id, JournalCheckinRecord.tenant_id == user.tenant_id]
    if ref.trade is not None:
        where.append(JournalCheckinRecord.trade_id == ref.id)
    else:
        where.append(JournalCheckinRecord.manual_trade_id == ref.id)
    return where


async def load(session: AsyncSession, user: User, ref: Ref) -> JournalCheckinRecord | None:
    return await session.scalar(select(JournalCheckinRecord).where(*_filter(user, ref)))


async def _row(session: AsyncSession, user: User, ref: Ref) -> JournalCheckinRecord:
    row = await load(session, user, ref)
    if row is not None:
        return row
    row = JournalCheckinRecord(tenant_id=user.tenant_id, user_id=user.id, trade_id=ref.id if ref.trade is not None else None,
                               manual_trade_id=ref.id if ref.manual is not None else None)
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:                               # created a moment ago by another tap
        await session.rollback()
        found = await load(session, user, ref)
        if found is None:
            raise
        return found
    return row


async def save(session: AsyncSession, user: User, ref: Ref, body: dict[str, Any], s: DisciplineSettings) -> JournalCheckinRecord:
    values = clean(body, s)
    row = await _row(session, user, ref)
    for key, value in values.items():
        setattr(row, key, value)
    await session.commit()
    await session.refresh(row)
    return row


async def add_note(session: AsyncSession, user: User, ref: Ref, text: Any, *, now: datetime | None = None) -> JournalCheckinRecord:
    note = _text("text", text, NOTE_MAX)
    if not note:
        raise EntryError("Write a note first")
    row = await _row(session, user, ref)
    notes = _notes(row)
    if len(notes) >= NOTES_MAX:
        raise EntryError(f"This entry already holds {NOTES_MAX} notes", 409)
    notes.append({"at": (now or datetime.now(UTC)).isoformat(), "text": note})
    row.during_json = json.dumps(notes)
    await session.commit()
    await session.refresh(row)
    return row


async def dismiss(session: AsyncSession, user: User, ref: Ref, moment: str) -> None:
    if moment not in ("open", "close"):
        raise EntryError("moment must be open or close")
    row = await _row(session, user, ref)
    setattr(row, f"dismissed_{moment}", True)
    await session.commit()


def waiting_moment(*, opened: datetime | None, closed: datetime | None, row: JournalCheckinRecord | None, now: datetime) -> str | None:
    """Which prompt an entry is waiting for (pure): "close" once closed and the after-half is not answered or dismissed,
    "open" while the before-half is not, else None. Only entries of the last PROMPT_DAYS days are asked about."""
    since = now - timedelta(days=PROMPT_DAYS)
    if closed is not None:
        if closed < since:
            return None
        done = row is not None and (row.plan_followed is not None or row.feeling_after is not None or row.dismissed_close)
        return None if done else "close"
    if opened is None or opened < since:
        return None
    done = row is not None and (row.feeling_before is not None or row.confidence is not None or row.dismissed_open)
    return None if done else "open"


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def waiting(session: AsyncSession, user: User, *, now: datetime | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """His entries waiting for a check-in, newest first (one row per structure: its first leg)."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=PROMPT_DAYS)
    trades = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.user_id == user.id, TradeRecord.tenant_id == user.tenant_id,
        (TradeRecord.entry_time >= since) | (TradeRecord.exit_time >= since)).order_by(TradeRecord.id)))
    manual = list(await session.scalars(select(JournalManualTradeRecord).where(
        JournalManualTradeRecord.user_id == user.id, JournalManualTradeRecord.tenant_id == user.tenant_id,
        (JournalManualTradeRecord.entry_time >= since) | (JournalManualTradeRecord.exit_time >= since))))
    rows = {(r.trade_id, r.manual_trade_id): r for r in await session.scalars(select(JournalCheckinRecord).where(
        JournalCheckinRecord.user_id == user.id, JournalCheckinRecord.tenant_id == user.tenant_id))}
    out: list[dict[str, Any]] = []
    seen_groups: set[str] = set()
    for t in trades:
        if t.leg_group_id:
            if t.leg_group_id in seen_groups:
                continue
            seen_groups.add(t.leg_group_id)
        moment = waiting_moment(opened=_utc(t.entry_time), closed=_utc(t.exit_time), row=rows.get((t.id, None)), now=now)
        if moment:
            out.append({"kind": "trade", "id": t.id, "symbol": t.symbol, "moment": moment, "mode": t.mode,
                        "at": (_utc(t.exit_time) if moment == "close" else _utc(t.entry_time)).isoformat()})  # type: ignore[union-attr]
    for m in manual:
        moment = waiting_moment(opened=_utc(m.entry_time), closed=_utc(m.exit_time), row=rows.get((None, m.id)), now=now)
        if moment:
            out.append({"kind": "manual", "id": m.id, "symbol": m.symbol, "moment": moment, "mode": None,
                        "at": (_utc(m.exit_time) if moment == "close" else _utc(m.entry_time)).isoformat()})  # type: ignore[union-attr]
    out.sort(key=lambda r: r["at"], reverse=True)
    return out[:limit]


__all__ = ["PLAN_ANSWERS", "FIELDS", "clean", "view", "load", "save", "add_note", "dismiss", "waiting", "waiting_moment",
           "mistake_choices"]
