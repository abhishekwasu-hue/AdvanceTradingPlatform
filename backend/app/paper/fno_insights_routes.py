"""F&O workspace 8b, slice 1: the read-only ATP-only reads (app/paper/fno_insights.py). GET only; nothing here places,
proposes, blocks or changes an order, and nothing picks a strike or a trade for the user.

- `GET /api/paper/fno/risk-budget`    the caller's own capital, risk per trade and daily loss limit, for the risk line
                                      beside an order's max loss (a warning only, never a refusal). Same flags as the
                                      ticket (`ui_v2` + the PAPER account): 404 while either is off.
- `GET /api/paper/fno/setup-strikes`  the strikes the caller's OWN option deployment rules work out to for one of HIS
                                      signals (`signal=action:12` / `history:7`), at the chain's price. `ui_v2` only
                                      (the chain page's own flag): reads, never trades.
- `GET /api/paper/fno/event-risk`     slice 4: the events calendar's entries (market events + the company's calendar)
                                      from today to the contract's expiry that apply to this underlying, for the risk
                                      line (a warning only). Same flags as the ticket.
"""
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.paper import fno_insights
from app.paper.fno_routes import MAX_PRICE, UI_V2_FLAG, _enabled

router = APIRouter(prefix="/api/paper/fno", tags=["paper"])
setup_rate_limit = user_rate_limit("paper_fno_setup_strikes", limit=60, window_seconds=60)
events_rate_limit = user_rate_limit("paper_fno_event_risk", limit=60, window_seconds=60)


@router.get("/risk-budget")
async def risk_budget(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    return await fno_insights.risk_budget(session, user)


@router.get("/setup-strikes", dependencies=[Depends(setup_rate_limit)])
async def setup_strikes(signal: str = Query(min_length=3, max_length=24, pattern=r"^[a-z]+:[0-9]+$"),
                        spot: Optional[float] = Query(default=None, gt=0, lt=MAX_PRICE),
                        user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, UI_V2_FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": UI_V2_FLAG})
    return await fno_insights.setup_strikes(session, user, signal, spot=spot)


@router.get("/event-risk", dependencies=[Depends(events_rate_limit)])
async def event_risk(underlying: str = Query(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9&_ .-]+$"),
                     until: date = Query(...),
                     user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    return await fno_insights.event_risk(session, user, underlying, until)
