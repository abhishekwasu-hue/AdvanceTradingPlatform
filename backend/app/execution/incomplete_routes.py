"""SE-7: the incomplete multi-leg orders on Positions - what is waiting for his answer, and his three taps.

Flags `ui_v2` + `smart_execution` (404 without them). Every route reads and answers only the caller's own orders (the
holder, or the organisation owner - as for Exit now); another organisation's order is 404, another member's 403.
"Complete the rest" is recorded here and sent by the trading worker (no route reaches an entry order in Phase 0);
"Close the filled legs" is an exit: PAPER legs close at once, LIVE legs on the worker's next cycle (ADR-0004).
"""
from typing import Any, Dict, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.execution import incomplete_structures

router = APIRouter(prefix="/api/trading/incomplete", tags=["trading"])
answer_rate_limit = user_rate_limit("incomplete_answer", 30, 60)


async def _enabled(session: AsyncSession, user: User) -> None:
    from app.execution.smart_execution import FLAG
    from app.platform.controls import flag_enabled
    for flag in ("ui_v2", FLAG):
        if not await flag_enabled(session, flag, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": flag})


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["complete_rest", "close_filled", "keep"]


@router.get("")
async def incomplete_orders(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """His own incomplete multi-leg orders: what filled, what did not, his rule and the time left to answer."""
    await _enabled(session, user)
    return await incomplete_structures.view_for_user(session, user)


@router.post("/{group_id}/answer", dependencies=[Depends(answer_rate_limit)])
async def answer_incomplete(group_id: str, body: AnswerBody, user: User = Depends(require_trader),
                            session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """His answer to one incomplete order. A second tap answers with the state it is in now (nothing is done twice)."""
    await _enabled(session, user)
    from app.ai.worker_bridge import price_lookup_for_user
    lookup = await price_lookup_for_user(session, user) if body.action == "close_filled" else None
    try:
        return await incomplete_structures.answer(session, user, group_id[:64], body.action, price_lookup=lookup)
    except incomplete_structures.AnswerError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
