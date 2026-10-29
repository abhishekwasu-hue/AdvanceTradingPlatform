"""AUD-T6: `/api/setup-recall/*` - similar-setup recall on a journal row and an approval card, and "what the context said
vs what happened" on a closed journal row (app/setup_recall/service.py). Behind `ui_v2` + `setup_recall`: 404 while
either is off. Self-scoped: another user's trade or proposal answers 404, exactly like one that does not exist.
Read-only: nothing here places, changes or blocks an order."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.setup_recall import FLAG, service

router = APIRouter(prefix="/api/setup-recall", tags=["setup-recall"])


async def _flags(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    for name in ("ui_v2", FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


@router.get("/trades/{trade_id}")
async def trade_recall(trade_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The caller's own similar past closed trades for one of his journal rows, and (once it is closed) what its stored
    entry context said next to what happened."""
    await _flags(session, user)
    trade = await service.own_trade(session, user, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="No such trade")
    return await service.for_trade(session, user, trade)


@router.get("/proposals/{action_id}")
async def proposal_recall(action_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The caller's own similar past closed trades for an entry proposal he sees in Signals."""
    await _flags(session, user)
    action = await service.own_proposal(session, user, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="No such proposal")
    return await service.for_proposal(session, user, action)


__all__ = ["router"]
