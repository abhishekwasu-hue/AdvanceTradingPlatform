"""`/api/copilot-memory` (M72 PR-11, CP-11) - what the Copilot remembers about the caller (flags `ui_v2` + `copilot_memory`
for reading and saving; 404 while either is off; Delete always works - deleting one's own data is never switched off).

* `GET`    - his saved memory (instruments, timeframes, style) and ATP's suggestions from his own watchlists, trades and
             Copilot questions (each with its source; nothing is stored until he saves).
* `PUT`    - replaces his memory (the `ai_memory` namespace's schema; a bad value is refused with 422).
* `DELETE` - deletes it (also done by "Delete my Copilot conversation").

Per user: nothing names another user. Nothing here places, changes or blocks an order.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import memory
from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.me import prefs as prefs_module

router = APIRouter(prefix="/api/copilot-memory", tags=["copilot-memory"])


async def _flags(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    for name in ("ui_v2", memory.FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


async def _view(session: AsyncSession, user: User) -> dict[str, Any]:
    return {"memory": await memory.saved(session, user), "suggested": await memory.suggest(session, user), "styles": memory.STYLES}


@router.get("")
async def get_memory(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _flags(session, user)
    return await _view(session, user)


@router.put("")
async def put_memory(request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _flags(session, user)
    raw = await prefs_module.read_body(request)
    try:
        json.loads(raw or b"null")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Body must be JSON") from exc
    await prefs_module.save(memory.PREFS, raw, None, user, session)
    return await _view(session, user)


@router.delete("")
async def delete_memory(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    deleted = await memory.forget(session, int(user.tenant_id), int(user.id))
    await session.commit()
    return {"deleted": deleted}


__all__ = ["router"]
