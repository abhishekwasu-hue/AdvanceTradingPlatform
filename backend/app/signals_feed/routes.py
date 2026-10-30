"""UI2-3 (UI v2 proposal §7, UI-4): `GET /api/signals/feed` - the Signals inbox, read-only, behind the `ui_v2` flag
(404 while off). The caller's own rows only (tenant and user; organisation-wide proposals without an owner too, as on
the Watchtower). Approve / skip / snooze stay on `/api/ai/actions/{id}/...`; this route never writes and never places
an order (ADR-0006). See app/signals_feed/service.py for the sources and the row shape."""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.signals_feed import service

router = APIRouter(prefix="/api/signals", tags=["signals"])
FLAG = "ui_v2"


@router.get("/feed")
async def signals_feed(tab: str = Query(default="today", pattern=r"^(waiting|today|history)$"),
                       source: str = Query(default="all", pattern=r"^(all|autopilot|scan|copilot|library)$"),
                       symbol: Optional[str] = Query(default=None, max_length=40, pattern=r"^[A-Za-z0-9 &._:-]{1,40}$"),
                       cursor: Optional[str] = Query(default=None, max_length=400),
                       limit: int = Query(default=service.DEFAULT_LIMIT, ge=1, le=service.MAX_LIMIT),
                       user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Proposals waiting for the caller, today's signals or the last 30 days, newest first, one row shape for every
    source (proposals, scan setups and matches, Autopilot fills, the signal generator)."""
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": FLAG})
    try:
        return await service.feed(session, user, tab=tab, source=source, symbol=symbol, cursor=cursor, limit=limit)
    except service.BadCursor as exc:
        raise HTTPException(status_code=422, detail="This list has changed; reload it.") from exc
