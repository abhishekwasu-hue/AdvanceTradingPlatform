"""Phase M / V4.13 endpoints: public status, admin controls, per-user trading disable."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_mfa_session, require_owner, require_role
from app.core.enums import UserRole
from app.db.models import User
from app.db.session import get_session
from app.platform import controls

status_router = APIRouter(prefix="/api/system", tags=["system"])
admin_router = APIRouter(prefix="/api/admin/controls", tags=["admin"], dependencies=[Depends(require_role()), Depends(require_mfa_session)])
users_router = APIRouter(prefix="/api/team/members", tags=["team"])


class MaintenanceBody(BaseModel):
    on: bool
    message: Optional[str] = Field(default=None, max_length=300)


class BrokersBody(BaseModel):
    names: List[str] = Field(default_factory=list, max_length=20)


class TradingDisableBody(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


@status_router.get("/status")
async def system_status(session: AsyncSession = Depends(get_session)) -> dict:
    """Unauthenticated: the frontend banner reads it before login too."""
    return await controls.status(session)


@admin_router.get("")
async def get_controls(session: AsyncSession = Depends(get_session)) -> dict:
    return await controls.status(session)


@admin_router.put("/maintenance")
async def put_maintenance(body: MaintenanceBody, user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    return await controls.set_maintenance(session, user, on=body.on, message=body.message)


@admin_router.put("/brokers")
async def put_brokers(body: BrokersBody, user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    return await controls.set_disabled_brokers(session, user, body.names)


async def _member(session: AsyncSession, member_id: int, actor: User) -> User:
    member = await session.get(User, member_id)
    if member is None or (member.tenant_id != actor.tenant_id and actor.role != UserRole.SUPER_ADMIN.value):
        raise HTTPException(status_code=404, detail="No such member")
    return member


@users_router.post("/{member_id}/trading-disable")
async def disable_trading(member_id: int, body: TradingDisableBody, actor: User = Depends(require_owner), session: AsyncSession = Depends(get_session)) -> dict:
    member = await _member(session, member_id, actor)
    member.trading_disabled_reason = body.reason.strip()
    await write_audit_log(session, member.tenant_id, actor.id, "user_trading_disabled", f"{member.email}: {body.reason}")
    await session.commit()
    return {"id": member.id, "email": member.email, "trading_disabled_reason": member.trading_disabled_reason}


@users_router.post("/{member_id}/trading-enable")
async def enable_trading(member_id: int, actor: User = Depends(require_owner), session: AsyncSession = Depends(get_session)) -> dict:
    member = await _member(session, member_id, actor)
    member.trading_disabled_reason = None
    await write_audit_log(session, member.tenant_id, actor.id, "user_trading_enabled", member.email)
    await session.commit()
    return {"id": member.id, "email": member.email, "trading_disabled_reason": None}


@users_router.get("/me/trading-status")
async def my_trading_status(user: User = Depends(get_current_user)) -> dict:
    return {"trading_disabled_reason": user.trading_disabled_reason}
