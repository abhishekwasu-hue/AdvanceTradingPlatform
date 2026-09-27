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
from app.auth import scopes

status_router = APIRouter(prefix="/api/system", tags=["system"])
admin_router = APIRouter(prefix="/api/admin/controls", tags=["admin"], dependencies=[Depends(require_role()), Depends(require_mfa_session)])
users_router = APIRouter(prefix="/api/team/members", tags=["team"])


class MaintenanceBody(BaseModel):
    on: bool
    message: Optional[str] = Field(default=None, max_length=300)


class BrokersBody(BaseModel):
    names: List[str] = Field(default_factory=list, max_length=20)


class FlagBody(BaseModel):
    on: bool
    tenants: List[int] = Field(default_factory=list, max_length=500)


class ScopeOverridesBody(BaseModel):
    deny: List[str] = Field(default_factory=list, max_length=20)
    grant: List[str] = Field(default_factory=list, max_length=20)


class TradingDisableBody(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


@status_router.get("/status")
async def system_status(session: AsyncSession = Depends(get_session)) -> dict:
    """Unauthenticated: the frontend banner reads it before login too."""
    from app.market_data.calendar import all_session_statuses
    result = await controls.status(session)
    result["sessions"] = {name: {"is_open": st.is_open, "reason": st.reason, "next_open": st.next_open.isoformat() if st.next_open else None}
                          for name, st in (await all_session_statuses(session)).items()}
    return result


@admin_router.get("")
async def get_controls(session: AsyncSession = Depends(get_session)) -> dict:
    return await controls.status(session)


@admin_router.put("/maintenance")
async def put_maintenance(body: MaintenanceBody, user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    return await controls.set_maintenance(session, user, on=body.on, message=body.message)


@admin_router.put("/brokers")
async def put_brokers(body: BrokersBody, user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    return await controls.set_disabled_brokers(session, user, body.names)


# --- Phase N4: feature flags -----------------------------------------------------------------------

@admin_router.get("/flags")
async def get_flags(session: AsyncSession = Depends(get_session)) -> dict:
    return await controls.feature_flags(session)


@admin_router.put("/flags/{name}")
async def put_flag(name: str, body: FlagBody, user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    try:
        return await controls.set_flag(session, user, name, on=body.on, tenants=body.tenants)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@status_router.get("/features")
async def my_features(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Which optional features are on for the caller's tenant (the UI hides what is off)."""
    return await controls.features_for_tenant(session, user.tenant_id)


@status_router.get("/encryption")
async def encryption_status(user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    """Phase N1 (admin): how many secrets still sit under the master key vs tenant data keys."""
    from app.secrets_store import envelope
    return await envelope.status(session)


# --- Phase N2: per-member scope overrides ---------------------------------------------------------

@users_router.get("/{member_id}/scopes")
async def get_member_scopes(member_id: int, actor: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    member = await _member(session, member_id, actor)
    return {"id": member.id, "role": member.role, "scopes": scopes.scopes_for(member), "overrides": scopes.parse_overrides(member.scope_overrides)}


@users_router.put("/{member_id}/scopes")
async def put_member_scopes(member_id: int, body: ScopeOverridesBody, actor: User = Depends(require_owner),
                            session: AsyncSession = Depends(get_session)) -> dict:
    member = await _member(session, member_id, actor)
    if member.role == UserRole.SUPER_ADMIN.value:
        raise HTTPException(status_code=403, detail="Platform administrators are managed outside the tenant")
    if member.id == actor.id and "team:manage" in body.deny:
        raise HTTPException(status_code=409, detail="You cannot remove your own team:manage permission")
    try:
        overrides = scopes.set_overrides(member, actor, deny=body.deny, grant=body.grant)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit_log(session, member.tenant_id, actor.id, "member_scopes_changed", f"{member.email}: {overrides}")
    await session.commit()
    return {"id": member.id, "role": member.role, "scopes": scopes.scopes_for(member), "overrides": overrides}


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


@users_router.post("/{member_id}/verify-email")
async def admin_verify_email(member_id: int, actor: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    """Phase N3 (platform admin): stamp an address verified when no mailer is configured yet."""
    from app.auth.verification import mark_verified
    member = await _member(session, member_id, actor)
    mark_verified(member)
    await write_audit_log(session, member.tenant_id, actor.id, "email_verified_by_admin", member.email)
    await session.commit()
    return {"id": member.id, "email": member.email, "email_verified": True}


@users_router.get("/me/trading-status")
async def my_trading_status(user: User = Depends(get_current_user)) -> dict:
    return {"trading_disabled_reason": user.trading_disabled_reason}
