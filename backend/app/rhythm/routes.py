"""`/api/rhythm/*` - the daily rhythm for the logged-in user (behind `ui_v2` + `daily_rhythm`; 404 while either is off).

* `GET /api/rhythm/today[?kind=]` - the digest of the moment on the IST market calendar (`phase`), or the one asked for:
  `{phase, brief}`. Built on demand from the user's own data and the market data ATP holds; cached for a minute per
  user and digest so the Dashboard's refresh does not rebuild it.
* `GET /api/rhythm/settings` / `PUT /api/rhythm/settings` - the user's Telegram choice for the digests (the
  `daily_rhythm` preferences namespace) and whether Telegram reaches the user's own chat.

Per user: every read is the caller's own; nothing names another user. Read-only towards trading: nothing here places,
changes or blocks an order.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.market_data.calendar import family_holidays
from app.me import prefs as prefs_module
from app.rhythm import FLAG, KINDS, build, deliver, schedule

router = APIRouter(prefix="/api/rhythm", tags=["daily-rhythm"])

CACHE_SECONDS = 60
_cache: Dict[Tuple[int, int, str], Tuple[float, Dict[str, Any]]] = {}


def reset_cache() -> None:
    _cache.clear()


async def _flags(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    for name in ("ui_v2", FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


@router.get("/today")
async def today(kind: Optional[str] = Query(default=None, pattern="^(pre_market|mid_session|closing|weekly)$"),
                user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    tenant_id, user_id = int(user.tenant_id), int(user.id)
    now = datetime.now(timezone.utc)
    phase = schedule.phase(now, await family_holidays(session, "NSE"))
    which = kind or phase
    key = (tenant_id, user_id, which)
    hit = _cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < CACHE_SECONDS:
        return {"phase": phase, "kinds": list(KINDS), "brief": hit[1]}
    brief = await build.build(session, user, which, now)
    _cache[key] = (time.monotonic(), brief)
    if len(_cache) > 5000:                                 # bounded: the oldest entries go first
        for old in sorted(_cache, key=lambda k: _cache[k][0])[:1000]:
            _cache.pop(old, None)
    return {"phase": phase, "kinds": list(KINDS), "brief": brief}


async def _settings(session: AsyncSession, user: User) -> Dict[str, Any]:
    from app.alerts import platform_bot
    value, version = await prefs_module.read_one(deliver.PREFS, user, session)
    channel = await platform_bot.telegram_channel(session, user.tenant_id)
    reaches = await deliver.destination(session, user) is not None
    return {"prefs": value, "version": version, "telegram": {"connected": bool(channel is not None and channel.enabled),
                                                              "reaches_you": reaches}}


@router.get("/settings")
async def get_settings(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    return await _settings(session, user)


@router.put("/settings")
async def put_settings(request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Replaces the user's choice (the `daily_rhythm` namespace's schema: unknown keys and wrong types are refused)."""
    await _flags(session, user)
    raw = await prefs_module.read_body(request)
    try:
        json.loads(raw or b"null")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Body must be JSON") from exc
    await prefs_module.save(deliver.PREFS, raw, None, user, session)
    return await _settings(session, user)


__all__ = ["router", "reset_cache"]
