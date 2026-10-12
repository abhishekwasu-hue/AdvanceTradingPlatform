"""S10 J: `GET /api/screener/etf` - the ETF screen (app/screener/etf.py) on the shared end-of-day history. Needs
`screener_v2` and `screener_etf` (off by default: 503). Member: shared reference data only, nothing of the organisation."""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.platform.controls import require_flag
from app.screener import etf

router = APIRouter(prefix="/api/screener/etf", tags=["screener"])


@router.get("")
async def etf_screen(sessions: int = Query(default=etf.DEFAULT_SESSIONS, ge=1, le=etf.MAX_SESSIONS),
                     min_traded_value: float = Query(default=0.0, ge=0.0), q: str = Query(default="", max_length=60),
                     max_abs_tracking: Optional[float] = Query(default=None, ge=0.0, le=100.0), sort: str = Query(default="traded_value"),
                     user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, "screener_v2", user.tenant_id)
    await require_flag(session, etf.FLAG, user.tenant_id)
    if sort not in etf.SORTS:
        raise HTTPException(status_code=422, detail=f"sort must be one of {', '.join(etf.SORTS)}")
    rows = await etf.etf_rows(session, sessions=sessions)
    shown = etf.screen(rows, min_traded_value=min_traded_value, query=q, max_abs_tracking=max_abs_tracking, sort=sort)
    return {"sessions": sessions, "total": len(rows), "shown": len(shown), "rows": [r.as_dict() for r in shown], "note": etf.NOTE,
            "source": "lake:nse_bhav (closed sessions)"}


__all__ = ["router"]
