"""MO4 slice 2: `GET /api/markets/overview` and `GET /api/markets/lists/{key}` - the Sector & index view.

Behind `ui_v2` and `markets_view` (both needed; 404 with X-Feature-Disabled naming the flag that is off). Shared
reference data only (the system watchlists and the shared end-of-day history): no organisation's or user's rows are
read, no broker session is used and nothing here places an order.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.markets import service

router = APIRouter(prefix="/api/markets", tags=["markets"])


async def _require_flags(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> None:
    from app.platform.controls import flag_enabled
    for flag in (service.PAGE_FLAG, service.FLAG):
        if not await flag_enabled(session, flag, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": flag})


@router.get("/overview", dependencies=[Depends(_require_flags)])
async def overview(session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Every index and sector: its move over the day / week / month, breadth, strongest and weakest stocks, and the move
    against the NIFTY 50."""
    return await service.overview(session)


@router.get("/lists/{key}", dependencies=[Depends(_require_flags)])
async def list_detail(key: str, session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """One index's or sector's stocks with their moves."""
    out = await service.list_detail(session, key)
    if out is None:
        raise HTTPException(status_code=404, detail="This list was not found.")
    return out
