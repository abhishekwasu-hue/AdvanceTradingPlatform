"""TERM-CON C0: `GET /api/terminal/conclusion?symbol=&expiry=&tf=` - the Conclusion panel's one read (app/conclusion).

Behind `ui_v2`, `terminal` and `terminal_conclusion` (all needed; 404 with X-Feature-Disabled naming the flag that is
off). Read only, the caller's own rows. No order path: nothing here places, changes or blocks an order.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.compliance_terms import require_ai_acknowledged
from app.ai.rate_limit import ai_rate_limit
from app.auth.dependencies import get_current_user
from app.conclusion import FLAGS, gather
from app.db.models import User
from app.db.session import get_session

router = APIRouter(prefix="/api/terminal", tags=["terminal"])

Timeframe = Literal["1m", "3m", "5m", "15m", "30m", "1h", "1d"]


async def _require_flags(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> None:
    from app.platform.controls import flag_enabled
    for flag in FLAGS:
        if not await flag_enabled(session, flag, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": flag})


@router.get("/conclusion", dependencies=[Depends(_require_flags)])
async def terminal_conclusion(symbol: str = Query(..., min_length=1, max_length=40),
                              expiry: str | None = Query(default=None, max_length=20), tf: Timeframe = "15m",
                              also: list[Timeframe] = Query(default=[]),
                              v: str | None = Query(default=None, max_length=200),
                              user: User = Depends(get_current_user),
                              session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Chart, OI, his positions, today's orders and the broker check for one symbol, each line with source and time."""
    import time
    key = (user.id, symbol.strip().upper(), expiry, tf, tuple(also), v)
    now = time.monotonic()
    if (hit := gather.cached(key, now)) is not None:
        return hit
    body = await gather.conclusion(session, user, symbol, expiry, tf, also=tuple(also))
    gather.remember(key, body, now)
    return body


class SummaryBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    expiry: str | None = Field(default=None, max_length=20)
    tf: Timeframe = "15m"
    also: list[Timeframe] = Field(default_factory=list, max_length=2)
    v: str | None = Field(default=None, max_length=200)


NO_KEY = "Connect your own AI key in Settings to get a summary. The readings above need no key."


@router.post("/conclusion/summary", dependencies=[Depends(_require_flags), Depends(ai_rate_limit)])
async def terminal_conclusion_summary(body: SummaryBody, user: User = Depends(require_ai_acknowledged),
                                      session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """TERM-CON C4: on tap only - an AI summary of the same read, on his own key (no fallback to the organisation's key
    at his limit), with the cost of this call. Refused by the checks: no summary, the rule lines stay."""
    import time

    from app.ai import metering
    from app.ai import resolve as ai_resolve
    from app.conclusion import summary
    from app.db.models import Tenant
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Organisation not found")
    try:
        provider = await ai_resolve.provider_for_user(session, user, tenant, "copilot")
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("code") == ai_resolve.LIMIT_CODE:
            return {"ready": False, "text": None, "reason": str(exc.detail.get("message") or "Your AI limit is reached."), "usage": None}
        raise
    if getattr(provider, "name", "rule_based") == "rule_based":
        return {"ready": False, "text": None, "reason": NO_KEY, "usage": None}
    key = (user.id, body.symbol.strip().upper(), body.expiry, body.tf, tuple(body.also), body.v)
    now = time.monotonic()
    read = gather.cached(key, now)
    if read is None:
        read = await gather.conclusion(session, user, body.symbol, body.expiry, body.tf, also=tuple(body.also))
        gather.remember(key, read, now)
    text, why = await summary.summarize(provider, read)
    return {"ready": True, "text": text, "reason": None if text else "The summary was withheld: it did not pass ATP's checks. The readings above are unchanged.",
            "usage": metering.spent_by(provider), "fingerprint": read.get("fingerprint")}
