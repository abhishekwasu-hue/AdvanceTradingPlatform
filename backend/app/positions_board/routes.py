"""N2-6 (P1 spec 2.4): `GET /api/positions/board` - the caller's own open positions with P&L in ₹ and R, stop and target
status, time in trade, plan vs current stop and how each position exits. Behind the `positions_board` flag (404 while
off). Read-only: exits stay on the existing one-click "Exit now" (POST /api/positions/{id}/exit-now), which this board
never gates (ADR-0004)."""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.positions_board import service

router = APIRouter(prefix="/api/positions", tags=["positions-board"])


@router.get("/board")
async def board(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, service.FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": service.FLAG})
    return await service.board(session, user)
