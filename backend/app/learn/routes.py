"""Readiness C13: the Learn section, the public marketing pages and the operator's video links.

* `GET /api/learn` - the lessons and their video links (any signed-in member; `ui_v2` + `learn`, 404 naming the flag while
  either is off).
* `GET /api/site/pages` - the public marketing pages (Features, How it works, About) for the front page; no sign-in. The
  flag `marketing_site` is read globally (a visitor has no organisation); 404 while it is off.
* `GET` / `PUT /api/admin/learn/settings` - the video links and the course link (SUPER_ADMIN + two-factor; audited
  without the addresses).

Education only (the content guard runs over every line in the tests). Nothing here reads positions or places an order.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_mfa_session, require_role
from app.db.models import User
from app.db.session import get_session
from app.learn import FLAG, PAGE_FLAG, SITE_FLAG, content, settings

router = APIRouter(tags=["learn"])


async def _require_flags(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> None:
    from app.platform.controls import flag_enabled
    for name in (PAGE_FLAG, FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


@router.get("/api/learn", dependencies=[Depends(_require_flags)])
async def learn(session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    stored = await settings.read(session)
    return {**content.lessons_payload(content.load(), stored["videos"]), "course_url": stored["course_url"] or None}


@router.get("/api/site/pages")
async def site_pages(session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, SITE_FLAG, None):
        raise HTTPException(status_code=404, detail="Not found")
    return content.site_payload(content.load_site())


@router.get("/api/admin/learn/settings", dependencies=[Depends(require_role()), Depends(require_mfa_session)])
async def admin_settings(session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    stored = await settings.read(session)
    lessons = [{"id": lesson.id, "title": lesson.title} for lesson in content.load().lessons]
    return {**stored, "lessons": lessons}


@router.put("/api/admin/learn/settings", dependencies=[Depends(require_mfa_session)])
async def admin_save_settings(body: settings.LearnSettingsIn, admin: User = Depends(require_role()),
                              session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    return await settings.write(session, admin, body)


__all__ = ["router"]
