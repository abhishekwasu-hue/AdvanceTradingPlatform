"""CP-UX CU6 (owner order 30 Oct): the Copilot conversation kept across reloads - per person and tenant, server-side.

* `remember` writes one turn (the question and the answer card the page showed, without the admin-only `diagnostics`)
  and deletes the person's turns older than the newest `config.COPILOT_CHAT_KEEP`.
* `recent` reads the newest turns back, oldest first (the page's order).
* `forget` deletes every turn of the person (Settings → My data, "Delete my Copilot conversation"; also on erasure), and
  what the Copilot remembers about him (M72 PR-11, `app/ai/memory.py`).

Stored for the person only: never read by a trading decision, an order path or another person.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import CopilotChatTurnRecord

NOT_STORED = ("diagnostics",)     # admin-only, never part of the person's own record


def keep() -> int:
    return int(config.COPILOT_CHAT_KEEP)


def _mine(tenant_id: int, user_id: int) -> tuple:
    return CopilotChatTurnRecord.tenant_id == tenant_id, CopilotChatTurnRecord.user_id == user_id


async def remember(session: AsyncSession, tenant_id: int, user_id: int, question: str, answer: dict[str, Any]) -> None:
    """One turn, then the bound. Does not commit."""
    card = {k: v for k, v in answer.items() if k not in NOT_STORED}
    session.add(CopilotChatTurnRecord(tenant_id=tenant_id, user_id=user_id, question=question,
                                      answer_json=json.dumps(card, default=str, ensure_ascii=False)))
    await session.flush()
    newest = select(CopilotChatTurnRecord.id).where(*_mine(tenant_id, user_id)).order_by(CopilotChatTurnRecord.id.desc()).limit(keep())
    kept = list(await session.scalars(newest))
    if len(kept) >= keep():
        await session.execute(delete(CopilotChatTurnRecord).where(*_mine(tenant_id, user_id), CopilotChatTurnRecord.id < min(kept)))


async def recent(session: AsyncSession, tenant_id: int, user_id: int) -> list[dict[str, Any]]:
    rows = list(await session.scalars(select(CopilotChatTurnRecord).where(*_mine(tenant_id, user_id))
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


async def count(session: AsyncSession, tenant_id: int, user_id: int) -> int:
    return int(await session.scalar(select(func.count()).select_from(CopilotChatTurnRecord).where(*_mine(tenant_id, user_id))) or 0)


async def forget(session: AsyncSession, tenant_id: int, user_id: int) -> int:
    """Every turn of the person - and (M72 PR-11) what the Copilot remembers about him (`ai_memory`). Returns how many turns
    were deleted. Does not commit."""
    from app.ai import memory
    result = await session.execute(delete(CopilotChatTurnRecord).where(*_mine(tenant_id, user_id)))
    await memory.forget(session, tenant_id, user_id)
    return int(result.rowcount or 0)


__all__ = ["remember", "recent", "count", "forget", "keep", "NOT_STORED"]
