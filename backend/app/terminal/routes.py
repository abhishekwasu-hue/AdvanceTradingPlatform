"""Trading Terminal T1: `GET /api/terminal/status` - the status bar's figures for the signed-in user (app/terminal/status.py).

Behind `ui_v2` and `terminal` (both needed; 404 with X-Feature-Disabled naming the flag that is off). Read only, the
caller's own rows (tenant AND user, TT-15). No order path: nothing here places, changes or blocks an order.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.terminal import status

router = APIRouter(prefix="/api/terminal", tags=["terminal"])


async def _require_flags(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> None:
    from app.platform.controls import flag_enabled
    for flag in (status.PAGE_FLAG, status.FLAG):
        if not await flag_enabled(session, flag, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": flag})


@router.get("/status", dependencies=[Depends(_require_flags)])
async def terminal_status(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Today's MTM, open MTM, funds, the margin estimate, the mode, the price feed and the kill switch - the caller's own."""
    return await status.terminal_status(session, user)
