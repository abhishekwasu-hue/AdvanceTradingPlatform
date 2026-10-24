"""TT12-1 (owner, DECISIONS 24 Oct): one-time notices for the signed-in user, shown once in the v2 shell.

* `GET /api/me/notices` - the notices due for the caller now (an empty list when none).
* `POST /api/me/notices/{notice_id}/seen` - the caller dismissed it; it never shows again on any device.

The one notice today: "limits_not_set". TT-12 made every platform-default risk limit "Not set" (an old stored value equal
to the former default counts as unset, app/risk_engine/by_default.py). An existing user whose limits became "Not set" that
way is told once. It is due only when all of these hold:
- the organisation has `ui_v2` on (the notice lives in the v2 shell) and `risk_by_default` on (the page it points to);
- his stored PAPER risk settings are a row from before TT-12 whose limits were the old defaults
  (`by_default.reset_by_tt12`): a new user (no row) or a user who had his own values never gets it;
- it was not seen yet: the mark `limits_not_set_seen_at` in the prefs namespace `notices` (app/me/prefs.py; no table, no
  migration). Dismissing sets the mark; so does saving any risk limit (app/paper/ticket_routes.py), and that save also
  writes `_set`, so the row stops qualifying.
Per user and tenant (the prefs and the account rows are filtered on both). Reads and one mark; places nothing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.me import prefs
from app.risk_engine import by_default

NAMESPACE = "notices"
LIMITS_NOT_SET = "limits_not_set"
TEXT = {LIMITS_NOT_SET: "Your risk limits are now OFF by default — set your own in Settings → Risk"}
LINK = {LIMITS_NOT_SET: "/risk-management"}
MARK = {LIMITS_NOT_SET: "limits_not_set_seen_at"}
PAGE_FLAG = "ui_v2"

router = APIRouter(prefix="/api/me", tags=["me"])


async def _seen(session: AsyncSession, user: User, notice_id: str) -> bool:
    value, _version = await prefs.read_one(NAMESPACE, user, session)
    return bool(value.get(MARK[notice_id]))


async def due(session: AsyncSession, user: User) -> List[Dict[str, Any]]:
    """The notices due for `user` now (see the module docstring)."""
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, PAGE_FLAG, user.tenant_id) or not await by_default.enabled(session, user.tenant_id):
        return []
    account = await by_default._account(session, user)
    if account is None or not by_default.reset_by_tt12(account.risk_settings_json):
        return []
    if await _seen(session, user, LIMITS_NOT_SET):
        return []
    return [{"id": LIMITS_NOT_SET, "text": TEXT[LIMITS_NOT_SET], "link": LINK[LIMITS_NOT_SET]}]


async def mark_seen(session: AsyncSession, user: User, notice_id: str) -> None:
    """Stores that `user` has seen the notice (once: an existing mark is kept, so the prefs version does not move)."""
    value, _version = await prefs.read_one(NAMESPACE, user, session)
    if value.get(MARK[notice_id]):
        return
    value[MARK[notice_id]] = datetime.now(timezone.utc).isoformat()
    await prefs.save(NAMESPACE, json.dumps(value).encode(), None, user, session)


@router.get("/notices")
async def get_notices(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    return {"notices": await due(session, user)}


@router.post("/notices/{notice_id}/seen")
async def notice_seen(notice_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    if notice_id not in MARK:
        raise HTTPException(status_code=404, detail="Unknown notice")
    await mark_seen(session, user, notice_id)
    return {"notices": await due(session, user)}


__all__ = ["router", "due", "mark_seen", "LIMITS_NOT_SET", "TEXT"]
