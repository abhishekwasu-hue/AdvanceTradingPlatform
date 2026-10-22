"""Idea library slice 1: the read-only API, behind the `idea_library` flag (404 while off, so nothing is there).

* `GET /api/ideas` - the library: the compliance frame, the groups, the beginner questions and every idea card.
* `GET /api/ideas/{idea_id}` - one idea card (the Screener reads it to open "Try as a scan").

Each scan carries `available`: every flag it needs (the Screener and its builder, and the flags of the functions it
uses) is on for the caller's organisation. Flag names are never sent. Slice 2: `adopt.available` is true only for an idea
mapped onto a strategy template (IDEA-1) while `strategy_templates` and every function that template reads are on; the
card then links to the paper bot form's pre-fill (the user saves it himself, PAPER only). Nothing is stored and nothing runs here; a scan
runs only on the Screener page, through the Screener's own validation, gates and limits. Places no orders.
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, Set

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.idea_library import content

router = APIRouter(prefix="/api/ideas", tags=["idea-library"])


async def _enabled(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, content.FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": content.FLAG})


async def _flags_on(session: AsyncSession, user: User) -> Set[str]:
    from app.platform.controls import flag_enabled
    return {f for f in content.needed_flags(content.load()) if await flag_enabled(session, f, user.tenant_id)}


async def _adoptable(session: AsyncSession, user: User) -> FrozenSet[str]:
    """The mapped templates the organisation can adopt now (empty while `strategy_templates` is off)."""
    names = content.adopt_templates(content.load())
    if not names:
        return frozenset()
    from app.platform.controls import flag_enabled
    from app.strategy_templates import library
    from app.strategy_templates.routes import FLAG as TEMPLATES_FLAG
    from app.strategy_templates.routes import _available
    if not await flag_enabled(session, TEMPLATES_FLAG, user.tenant_id):
        return frozenset()
    out: Set[str] = set()
    for name in names:
        try:
            template = library.get(name)
        except KeyError:
            continue
        if await _available(session, user, template):
            out.add(name)
    return frozenset(out)


@router.get("")
async def library(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    return content.payload(content.load(), await _flags_on(session, user), await _adoptable(session, user))


@router.get("/{idea_id}")
async def idea(idea_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    found = content.load().idea(idea_id)
    if found is None:
        raise HTTPException(status_code=404, detail="no such idea")
    return content.idea_payload(found, await _flags_on(session, user), await _adoptable(session, user))


__all__ = ["router"]
