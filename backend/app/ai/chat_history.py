"""CP-UX CU6 (owner order 30 Oct): the Copilot conversation kept across reloads - per person and tenant, server-side.
CH-2 (owner 5 Nov, "Ho finish all job"): named conversations like chat sessions - "New chat", a list of earlier
conversations (titled by their first question), open / rename / delete.

* `remember` writes one turn (the question and the answer card the page showed, without the admin-only `diagnostics`)
  into a conversation (a given one of the person's, a new one, or else his most recently used one) and keeps the newest
  `config.COPILOT_CHAT_KEEP` turns of that conversation and the newest `config.COPILOT_CHAT_SESSIONS_KEEP` conversations.
* `recent` reads one conversation's turns back, oldest first (the page's order); no conversation named = the most
  recently used one.
* `sessions` / `rename` / `drop` - the person's own conversations only.
* `forget` deletes every turn and conversation of the person (Settings → My data, "Delete my Copilot conversation"; also
  on erasure), and what the Copilot remembers about him (M72 PR-11, `app/ai/memory.py`).

Turns without a conversation (written before CH-2, or by an older release after a rollback) are adopted into one
"Earlier conversation" the next time the person's conversations are read or written.

Stored for the person only: never read by a trading decision, an order path or another person.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import CopilotChatSessionRecord, CopilotChatTurnRecord

NOT_STORED = ("diagnostics",)     # admin-only, never part of the person's own record
EARLIER = "Earlier conversation"
TITLE_MAX = 60


class NotYours(LookupError):
    """A conversation id that is not one of the caller's own (the route answers 404, never another person's data)."""


def keep() -> int:
    return int(config.COPILOT_CHAT_KEEP)


def sessions_keep() -> int:
    return int(config.COPILOT_CHAT_SESSIONS_KEEP)


def title_of(question: str) -> str:
    """The first question as a one-line title (at most TITLE_MAX characters, cut at a word)."""
    line = " ".join((question or "").split())
    if len(line) <= TITLE_MAX:
        return line or "New chat"
    cut = line[:TITLE_MAX].rsplit(" ", 1)[0] or line[:TITLE_MAX]
    return cut.rstrip(" ,.;:-") + "…"


def _mine(tenant_id: int, user_id: int) -> tuple:
    return CopilotChatTurnRecord.tenant_id == tenant_id, CopilotChatTurnRecord.user_id == user_id


def _own_sessions(tenant_id: int, user_id: int) -> tuple:
    return CopilotChatSessionRecord.tenant_id == tenant_id, CopilotChatSessionRecord.user_id == user_id


async def _adopt(session: AsyncSession, tenant_id: int, user_id: int) -> None:
    """Turns without a conversation go into one "Earlier conversation" (created when needed). Does not commit."""
    loose = select(func.count(), func.min(CopilotChatTurnRecord.created_at), func.max(CopilotChatTurnRecord.created_at)).where(
        *_mine(tenant_id, user_id), CopilotChatTurnRecord.session_id.is_(None))
    n, first, last = (await session.execute(loose)).one()
    if not n:
        return
    home = CopilotChatSessionRecord(tenant_id=tenant_id, user_id=user_id, title=EARLIER, created_at=first or _now(), updated_at=last or _now())
    session.add(home)
    await session.flush()
    await session.execute(update(CopilotChatTurnRecord).where(*_mine(tenant_id, user_id), CopilotChatTurnRecord.session_id.is_(None))
                          .values(session_id=home.id))


def _now() -> datetime:
    return datetime.now(UTC)


async def _own(session: AsyncSession, tenant_id: int, user_id: int, session_id: int) -> CopilotChatSessionRecord:
    row = await session.get(CopilotChatSessionRecord, session_id)
    if row is None or row.tenant_id != tenant_id or row.user_id != user_id:
        raise NotYours(session_id)
    return row


async def _latest(session: AsyncSession, tenant_id: int, user_id: int) -> CopilotChatSessionRecord | None:
    return (await session.scalars(select(CopilotChatSessionRecord).where(*_own_sessions(tenant_id, user_id))
                                  .order_by(CopilotChatSessionRecord.updated_at.desc(), CopilotChatSessionRecord.id.desc()).limit(1))).first()


async def _target(session: AsyncSession, tenant_id: int, user_id: int, question: str, session_id: int | None,
                  new: bool) -> CopilotChatSessionRecord:
    await _adopt(session, tenant_id, user_id)
    if session_id is not None:
        return await _own(session, tenant_id, user_id, session_id)
    if not new:
        latest = await _latest(session, tenant_id, user_id)
        if latest is not None:
            return latest
    row = CopilotChatSessionRecord(tenant_id=tenant_id, user_id=user_id, title=title_of(question))
    session.add(row)
    await session.flush()
    return row


async def remember(session: AsyncSession, tenant_id: int, user_id: int, question: str, answer: dict[str, Any], *,
                   session_id: int | None = None, new: bool = False) -> dict[str, Any]:
    """One turn into its conversation, then the bounds. Returns the conversation ({"id", "title"}). Raises NotYours for a
    conversation that is not the person's. Does not commit."""
    convo = await _target(session, tenant_id, user_id, question, session_id, new)
    card = {k: v for k, v in answer.items() if k not in NOT_STORED}
    session.add(CopilotChatTurnRecord(tenant_id=tenant_id, user_id=user_id, session_id=convo.id, question=question,
                                      answer_json=json.dumps(card, default=str, ensure_ascii=False)))
    convo.updated_at = _now()
    await session.flush()
    in_convo = (*_mine(tenant_id, user_id), CopilotChatTurnRecord.session_id == convo.id)
    newest = select(CopilotChatTurnRecord.id).where(*in_convo).order_by(CopilotChatTurnRecord.id.desc()).limit(keep())
    kept = list(await session.scalars(newest))
    if len(kept) >= keep():
        await session.execute(delete(CopilotChatTurnRecord).where(*in_convo, CopilotChatTurnRecord.id < min(kept)))
    old = list(await session.scalars(select(CopilotChatSessionRecord.id).where(*_own_sessions(tenant_id, user_id))
                                     .order_by(CopilotChatSessionRecord.updated_at.desc(), CopilotChatSessionRecord.id.desc())
                                     .offset(sessions_keep())))
    if old:
        await session.execute(delete(CopilotChatTurnRecord).where(*_mine(tenant_id, user_id), CopilotChatTurnRecord.session_id.in_(old)))
        await session.execute(delete(CopilotChatSessionRecord).where(*_own_sessions(tenant_id, user_id), CopilotChatSessionRecord.id.in_(old)))
    return {"id": convo.id, "title": convo.title}


async def recent(session: AsyncSession, tenant_id: int, user_id: int, session_id: int | None = None) -> list[dict[str, Any]]:
    """One conversation's turns, oldest first (no id: the most recently used conversation). Raises NotYours."""
    convo = await current(session, tenant_id, user_id, session_id)
    if convo is None:
        return []
    rows = list(await session.scalars(select(CopilotChatTurnRecord).where(*_mine(tenant_id, user_id), CopilotChatTurnRecord.session_id == convo["id"])
                                      .order_by(CopilotChatTurnRecord.id.desc()).limit(keep())))
    out = []
    for row in reversed(rows):
        try:
            answer = json.loads(row.answer_json or "{}")
        except ValueError:
            answer = None
        out.append({"id": row.id, "question": row.question, "answer": answer if isinstance(answer, dict) and answer else None,
                    "at": row.created_at.isoformat() if row.created_at else None})
    return out


async def current(session: AsyncSession, tenant_id: int, user_id: int, session_id: int | None = None) -> dict[str, Any] | None:
    """The named conversation (NotYours when not his) or his most recently used one, as {"id", "title"}; None when none.
    Adopts loose turns first. Does not commit."""
    await _adopt(session, tenant_id, user_id)
    row = await _own(session, tenant_id, user_id, session_id) if session_id is not None else await _latest(session, tenant_id, user_id)
    return {"id": row.id, "title": row.title} if row is not None else None


async def sessions(session: AsyncSession, tenant_id: int, user_id: int) -> list[dict[str, Any]]:
    """The person's conversations, most recently used first: id, title, turns, updated_at. Does not commit."""
    await _adopt(session, tenant_id, user_id)
    turns = (select(CopilotChatTurnRecord.session_id, func.count().label("n")).where(*_mine(tenant_id, user_id))
             .group_by(CopilotChatTurnRecord.session_id).subquery())
    rows = await session.execute(select(CopilotChatSessionRecord, turns.c.n).outerjoin(turns, turns.c.session_id == CopilotChatSessionRecord.id)
                                 .where(*_own_sessions(tenant_id, user_id))
                                 .order_by(CopilotChatSessionRecord.updated_at.desc(), CopilotChatSessionRecord.id.desc()))
    return [{"id": s.id, "title": s.title, "turns": int(n or 0), "updated_at": s.updated_at.isoformat() if s.updated_at else None}
            for s, n in rows.all()]


async def rename(session: AsyncSession, tenant_id: int, user_id: int, session_id: int, title: str) -> dict[str, Any]:
    """A new title (one line, trimmed). Raises NotYours. Does not commit; does not change the order of the list."""
    row = await _own(session, tenant_id, user_id, session_id)
    row.title = " ".join(title.split())[:120] or row.title
    await session.flush()
    return {"id": row.id, "title": row.title}


async def drop(session: AsyncSession, tenant_id: int, user_id: int, session_id: int) -> int:
    """One conversation and its turns. Returns how many turns went. Raises NotYours. Does not commit."""
    row = await _own(session, tenant_id, user_id, session_id)
    result = await session.execute(delete(CopilotChatTurnRecord).where(*_mine(tenant_id, user_id), CopilotChatTurnRecord.session_id == row.id))
    await session.execute(delete(CopilotChatSessionRecord).where(CopilotChatSessionRecord.id == row.id))
    return int(result.rowcount or 0)


async def count(session: AsyncSession, tenant_id: int, user_id: int) -> int:
    return int(await session.scalar(select(func.count()).select_from(CopilotChatTurnRecord).where(*_mine(tenant_id, user_id))) or 0)


async def forget(session: AsyncSession, tenant_id: int, user_id: int) -> int:
    """Every turn and conversation of the person - and (M72 PR-11) what the Copilot remembers about him (`ai_memory`).
    Returns how many turns were deleted. Does not commit."""
    from app.ai import memory
    result = await session.execute(delete(CopilotChatTurnRecord).where(*_mine(tenant_id, user_id)))
    await session.execute(delete(CopilotChatSessionRecord).where(*_own_sessions(tenant_id, user_id)))
    await memory.forget(session, tenant_id, user_id)
    return int(result.rowcount or 0)


__all__ = ["remember", "recent", "current", "sessions", "rename", "drop", "count", "forget", "keep", "sessions_keep",
           "title_of", "NotYours", "NOT_STORED", "EARLIER", "TITLE_MAX"]
