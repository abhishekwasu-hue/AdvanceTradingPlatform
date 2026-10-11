"""Z3-b / BT3: the caller's own PAPER account - balance, P&L after charges, positions, journal, would-have records, the
PAPER-scope pause and the progression ladder. Behind the `paper_account` flag (404 while off). Every answer is the
caller's own data (user scope, app/paper/service.py); nothing here places an order."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.db.models import User
from app.db.session import get_session
from app.paper import service

router = APIRouter(prefix="/api/paper", tags=["paper"])


async def _enabled(session: AsyncSession, user: User) -> None:
    if not await service.enabled(session, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": service.FLAG})


class CapitalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    starting_capital: float = Field(gt=0)


class PauseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Optional[str] = Field(default=None, max_length=150)


@router.get("/account")
async def account(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    await _enabled(session, user)
    return await service.summary(session, user)


@router.put("/account")
async def set_capital(body: CapitalBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    await _enabled(session, user)
    try:
        await service.set_starting_capital(session, user, body.starting_capital)
    except service.CapitalLocked as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await service.summary(session, user)


@router.post("/pause")
async def pause(body: PauseBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    """Stops the caller's new PAPER entries and entry proposals. Exits keep running (ADR-0004); LIVE is not touched."""
    await _enabled(session, user)
    row = await service.set_paused(session, user, True, via="web", reason=body.reason)
    return {"paused": row.paused, "paused_reason": row.paused_reason}


@router.post("/resume")
async def resume(user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    await _enabled(session, user)
    row = await service.set_paused(session, user, False, via="web")
    return {"paused": row.paused, "paused_reason": row.paused_reason}


@router.get("/ladder")
async def ladder(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    await _enabled(session, user)
    return await service.ladder(session, user)
