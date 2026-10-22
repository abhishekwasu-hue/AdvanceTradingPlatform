"""`/api/growth/*` - the user's plan page and his discipline share card (behind `ui_v2` + `growth_v2`; 404 while either
is off, X-Feature-Disabled names the flag).

* `GET /api/growth/plan` - what his plan includes and his usage meter (app/growth/plan.py).
* `GET /api/growth/share/discipline-week` - the "my discipline this week" card from his own PAPER record, guarded for a
  public surface: no security names, no prices, no money, no call words (app/growth/share.py).

Per user: every read is the caller's own (the organisation's paper bots and backtests are counted for the team, and the
meter says so). Read-only towards billing and trading: nothing here changes a plan, starts a trial or touches an order.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.growth import FLAG, PAGE_FLAG, plan, share

router = APIRouter(prefix="/api/growth", tags=["growth"])


async def require_flags(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> None:
    from app.platform.controls import flag_enabled
    for name in (PAGE_FLAG, FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


@router.get("/plan", dependencies=[Depends(require_flags)])
async def plan_page(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """His plan in plain words and how much of it he used (this month / today / now)."""
    return await plan.plan_page(session, user)


@router.get("/share/discipline-week", dependencies=[Depends(require_flags)])
async def discipline_week(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The share card's facts: discipline only, never a name, a price or a call."""
    return await share.discipline_week(session, user)
