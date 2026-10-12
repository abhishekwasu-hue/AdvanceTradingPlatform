"""S10 J: `GET /api/screener/etf` - the ETF screen (app/screener/etf.py) on the shared end-of-day history. Needs
`screener_v2` and `screener_etf` (off by default: 503). Member: shared reference data only, nothing of the organisation."""
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.platform.controls import require_flag
from app.screener import etf

router = APIRouter(prefix="/api/screener/etf", tags=["screener"])
etf_rate_limit = user_rate_limit("screener_etf", limit=30, window_seconds=60)
CACHE_SECONDS = 120.0
# Shared reference data (the same for every organisation): one computation per window size per CACHE_SECONDS.
_CACHE: Dict[int, Tuple[float, List[etf.EtfRow]]] = {}


async def _rows(session: AsyncSession, sessions: int) -> List[etf.EtfRow]:
    hit = _CACHE.get(sessions)
    if hit is not None and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    rows = await etf.etf_rows(session, sessions=sessions)
    _CACHE[sessions] = (time.monotonic(), rows)
    return rows


@router.get("", dependencies=[Depends(etf_rate_limit)])
async def etf_screen(sessions: int = Query(default=etf.DEFAULT_SESSIONS, ge=1, le=etf.MAX_SESSIONS),
                     min_traded_value: float = Query(default=0.0, ge=0.0), q: str = Query(default="", max_length=60),
                     max_abs_tracking: Optional[float] = Query(default=None, ge=0.0, le=100.0), sort: str = Query(default="traded_value"),
                     user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, "screener_v2", user.tenant_id)
    await require_flag(session, etf.FLAG, user.tenant_id)
    if sort not in etf.SORTS:
        raise HTTPException(status_code=422, detail=f"sort must be one of {', '.join(etf.SORTS)}")
    rows = await _rows(session, sessions)
    shown = etf.screen(rows, min_traded_value=min_traded_value, query=q, max_abs_tracking=max_abs_tracking, sort=sort)
    return {"sessions": sessions, "total": len(rows), "shown": len(shown), "rows": [r.as_dict() for r in shown], "note": etf.NOTE,
            "source": "lake:nse_bhav (closed sessions)"}


__all__ = ["router"]
