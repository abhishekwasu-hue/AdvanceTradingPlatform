"""TERM-CON C0: `GET /api/terminal/conclusion?symbol=&expiry=&tf=` - the Conclusion panel's one read (app/conclusion).

Behind `ui_v2`, `terminal` and `terminal_conclusion` (all needed; 404 with X-Feature-Disabled naming the flag that is
off). Read only, the caller's own rows. No order path: nothing here places, changes or blocks an order.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.conclusion import FLAGS, gather
from app.db.models import User
from app.db.session import get_session

router = APIRouter(prefix="/api/terminal", tags=["terminal"])

Timeframe = Literal["1m", "3m", "5m", "15m", "30m", "1h", "1d"]


async def _require_flags(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> None:
    from app.platform.controls import flag_enabled
    for flag in FLAGS:
        if not await flag_enabled(session, flag, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": flag})


@router.get("/conclusion", dependencies=[Depends(_require_flags)])
async def terminal_conclusion(symbol: str = Query(..., min_length=1, max_length=40),
                              expiry: str | None = Query(default=None, max_length=20), tf: Timeframe = "15m",
                              user: User = Depends(get_current_user),
                              session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Chart, OI, his positions, today's orders and the broker check for one symbol, each line with source and time."""
    return await gather.conclusion(session, user, symbol, expiry, tf)
