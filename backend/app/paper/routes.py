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
    """Z3-b ladder card. N2-1: with `ladder_evidence` on for the organisation, also the discipline criterion from the
    journal and the user's own figures (information only)."""
    await _enabled(session, user)
    from app.platform.controls import flag_enabled
    return await service.ladder(session, user, evidence=await flag_enabled(session, service.EVIDENCE_FLAG, user.tenant_id))


# --- N2-2: experience levels (flag `experience_levels`) ------------------------------------------------------------------

class OptOutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gate: str = Field(max_length=32)
    reason: str = Field(max_length=300)


async def _experience_on(session: AsyncSession, user: User) -> None:
    from app.paper import experience
    if not await experience.enabled(session, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": experience.FLAG})


@router.get("/experience")
async def experience_status(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """The caller's experience level from their own PAPER record, each level's criteria, the gates and the opt-out counts."""
    from app.paper import experience
    await _experience_on(session, user)
    return await experience.status(session, user)


@router.post("/experience/opt-out")
async def experience_opt_out(body: OptOutBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    """The caller's explicit opt-out of one gate for the rest of the IST day, with a reason: logged and counted. It only
    ever affects the caller's own new entries; exits are never gated."""
    from app.paper import experience
    await _experience_on(session, user)
    try:
        return await experience.opt_out(session, user, body.gate, body.reason)
    except experience.NothingToOptOut as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except experience.OptOutError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
