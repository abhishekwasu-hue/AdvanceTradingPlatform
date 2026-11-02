"""`/api/copilot-watch/*` (M72 PR-7, CP-7) - the caller's own Copilot watch choices (flags `ui_v2` + `copilot_watch`; 404
while either is off).

* `GET /api/copilot-watch/settings` - his choices (the `copilot_watch` prefs: every type OFF and every number Not set until
  he saves one), whether Telegram reaches his own chat, and whether his own risk-free rate is saved (the delta part needs it).
* `PUT /api/copilot-watch/settings` - replaces his choices (the namespace's schema: a type turned on without its number is
  refused with 422).

Per user: nothing names another user. Information only: nothing here places, changes or blocks an order.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import watch
from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.me import prefs as prefs_module

router = APIRouter(prefix="/api/copilot-watch", tags=["copilot-watch"])


async def _flags(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    for name in ("ui_v2", watch.FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


async def _settings(session: AsyncSession, user: User) -> dict[str, Any]:
    from app.alerts import platform_bot
    from app.rhythm.deliver import destination
    value, version = await prefs_module.read_one(watch.PREFS, user, session)
    channel = await platform_bot.telegram_channel(session, user.tenant_id)
    return {"prefs": value, "version": version,
            "telegram": {"connected": bool(channel is not None and channel.enabled), "reaches_you": await destination(session, user) is not None},
            "risk_free_rate_set": await watch._rate_pct(session, user) is not None}


@router.get("/settings")
async def get_settings(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _flags(session, user)
    return await _settings(session, user)


@router.put("/settings")
async def put_settings(request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _flags(session, user)
    raw = await prefs_module.read_body(request)
    try:
        json.loads(raw or b"null")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Body must be JSON") from exc
    await prefs_module.save(watch.PREFS, raw, None, user, session)
    return await _settings(session, user)


__all__ = ["router"]
