"""`GET /api/fno/expiry-checklist` - AUD-T2: the caller's own open F&O positions expiring today or next session, with
in / out of the money now, how each settles, his broker's rule (linked) and the next expiry (behind `ui_v2` +
`expiry_checklist`; 404 while either is off). Read-only: nothing here places, changes or blocks an order."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.expiry_checklist import FLAG, build

router = APIRouter(prefix="/api/fno", tags=["expiry-checklist"])


def _now() -> datetime:
    """The clock (a test freezes it here)."""
    return datetime.now(timezone.utc)


async def _flags(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    for name in ("ui_v2", FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


@router.get("/expiry-checklist")
async def expiry_checklist(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    return await build.for_user(session, user, _now())
