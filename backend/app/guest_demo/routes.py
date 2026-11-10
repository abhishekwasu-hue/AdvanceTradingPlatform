"""M95 PR-5: `GET /api/guest-demo` - the stored guest snapshot, for a visitor without an account.

No sign-in. The flag `guest_demo` is read globally (a visitor has no organisation, like `marketing_site`); 404 while it
is off or while no snapshot is stored. Per-IP limit (core/rate_limit). Serves only the one stored row: no broker call,
no AI call, no per-user data, nothing computed per request.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import rate_limit
from app.db.session import get_session
from app.guest_demo import FLAG
from app.guest_demo.build import stored

router = APIRouter(tags=["guest-demo"])

RATE_LIMIT = 30                 # requests per IP per window (the sign-in screen asks once, the page once)
RATE_WINDOW_SECONDS = 60
CACHE_SECONDS = 300             # the snapshot is frozen; a browser may keep it a few minutes

guest_demo_rate_limit = rate_limit("guest_demo", limit=RATE_LIMIT, window_seconds=RATE_WINDOW_SECONDS)


@router.get("/api/guest-demo", dependencies=[Depends(guest_demo_rate_limit)])
async def guest_demo(response: Response, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, FLAG, None):
        raise HTTPException(status_code=404, detail="Not found")
    payload = await stored(session)
    if payload is None:
        raise HTTPException(status_code=404, detail="Not found")
    response.headers["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    return {key: payload[key] for key in ("version", "as_of", "indices", "screens")}


__all__ = ["router"]
