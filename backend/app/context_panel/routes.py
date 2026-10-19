"""N2-5 (P1 spec 1.5): `GET /api/context/panel` - the market context panel (regime, breadth, India VIX, FII / DII,
global cues, events). Behind the `context_panel` flag (404 while off). Shared reference data, the caller's own
organisation's events and the VIX gate's own viewer rule; no broker session is used and nothing here places an order."""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.context_panel import service
from app.db.models import User
from app.db.session import get_session

router = APIRouter(prefix="/api/context", tags=["context-panel"])


@router.get("/panel")
async def panel(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, service.FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": service.FLAG})
    return await service.panel(session, user)
