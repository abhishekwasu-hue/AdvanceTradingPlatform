"""AUD-T1: `GET /api/today/my-day` - the caller's own "My day" card (behind `ui_v2` + `my_day_card`; 404 while either
is off). Self scoped: only the caller's own open positions (tenant AND user), PAPER and LIVE alike. Read-only: nothing
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
    from app.platform.controls import flag_enabled
    for name in ("ui_v2", FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


@router.get("/my-day")
async def my_day_card(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    me = my_day.Viewer(id=int(user.id), tenant_id=int(user.tenant_id))     # plain ids: a reader's rollback expires the ORM user
    return await my_day.build(session, me, datetime.now(timezone.utc))


__all__ = ["router"]
