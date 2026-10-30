"""AUD-T1: `GET /api/today/my-day` - the caller's own "My day" card (behind `ui_v2` + `my_day_card`; 404 while either
is off; TERM-2 R3 also reads it with `ui_v2` + `terminal` + `term_strike_area`). Self scoped: only the caller's own open positions (tenant AND user), PAPER and LIVE alike. Read-only: nothing
is written, nothing places, changes or blocks an order.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.today import FLAG, my_day

router = APIRouter(prefix="/api/today", tags=["today"])


async def _flags(session: AsyncSession, user: User) -> None:
    """ui_v2 + my_day_card, or (TERM-2 R3) the Terminal's strike-to-area mark: ui_v2 + terminal + term_strike_area."""
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, "ui_v2", user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": "ui_v2"})
    if await flag_enabled(session, FLAG, user.tenant_id):
        return
    from app.paper import fno_term
    if await fno_term.enabled(session, user.tenant_id, fno_term.STRIKE_AREA_FLAG):
        return
    raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": FLAG})


@router.get("/my-day")
async def my_day_card(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    me = my_day.Viewer(id=int(user.id), tenant_id=int(user.tenant_id))     # plain ids: a reader's rollback expires the ORM user
    return await my_day.build(session, me, datetime.now(timezone.utc))


__all__ = ["router"]
