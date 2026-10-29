"""AUD-T4: `GET /api/paper/report-card` - the caller's own paper report card (closed PAPER trades per bot and per strategy;
app/paper/report_card.py). Behind `ui_v2` + `paper_report_card`: 404 while either is off. Read-only."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.paper import report_card

router = APIRouter(prefix="/api/paper", tags=["paper-report-card"])


async def _flags(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    for name in ("ui_v2", report_card.FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


@router.get("/report-card")
async def get_report_card(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    return await report_card.build(session, user)


__all__ = ["router"]
