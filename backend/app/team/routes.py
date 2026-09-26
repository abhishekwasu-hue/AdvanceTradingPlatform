"""Team management for a tenant: who is in it, with which role, and inviting more people.

Invites are links, not accounts: the owner creates one for an email + role, gets a URL to share
(shown once - only a hash of its token is stored), and the invitee sets a password on accepting.
Accepting is in app/auth/routes.py (public endpoints), everything here needs an OWNER.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_owner
from app.auth.sessions import revoke_all_sessions
from app.core.config import FRONTEND_URL
from app.core.enums import UserRole
from app.db.models import Tenant, TenantInviteRecord, User
from app.db.session import get_session
from app.plans.limits import check_can_add_member, limits as plan_limits, usage as plan_usage
from app.plans.registry import get_plan

router = APIRouter(prefix="/api/team", tags=["team"])

INVITE_TTL_HOURS = 48
# Roles an owner can hand out. OWNER itself is granted by promoting an existing member, never
# by invite, so a leaked invite link can never mint an owner.
INVITABLE_ROLES = (UserRole.USER, UserRole.STRATEGY_CREATOR, UserRole.VIEWER)
ASSIGNABLE_ROLES = (UserRole.OWNER,) + INVITABLE_ROLES


def hash_invite_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def invite_url(request: Request, token: str) -> str:
    base = FRONTEND_URL if FRONTEND_URL.startswith("http") else str(request.base_url).rstrip("/") + "/"
    return f"{base}?invite={token}"


class MemberResponse(BaseModel):
    id: int
    email: str
    role: str
    is_active: bool
    created_at: str

    @classmethod
    def from_user(cls, user: User) -> "MemberResponse":
        return cls(id=user.id, email=user.email, role=user.role, is_active=user.is_active, created_at=user.created_at.isoformat())


class InviteCreateRequest(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.USER


class InviteResponse(BaseModel):
    id: int
    email: str
    role: str
    expires_at: str
    accepted_at: Optional[str]
    invited_by: Optional[int]
    # Only present on the create response - the raw token is never stored.
    invite_url: Optional[str] = None

    @classmethod
    def from_record(cls, record: TenantInviteRecord, url: Optional[str] = None) -> "InviteResponse":
        return cls(
            id=record.id, email=record.email, role=record.role, expires_at=_as_utc(record.expires_at).isoformat(),
            accepted_at=_as_utc(record.accepted_at).isoformat() if record.accepted_at else None,
            invited_by=record.invited_by, invite_url=url,
        )


class RoleChangeRequest(BaseModel):
    role: UserRole


class TenantResponse(BaseModel):
    id: int
    name: str
    plan: str
    plan_name: str
    plan_description: str
    status: str
    members: int
    limits: dict
    usage: dict


@router.get("/tenant", response_model=TenantResponse)
async def get_tenant(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> TenantResponse:
    """The organisation, its plan and how much of each plan limit is in use - the numbers the
    402 errors elsewhere refer to."""
    tenant = await session.get(Tenant, user.tenant_id)
    plan = get_plan(tenant.plan)
    members = await session.scalar(select(func.count()).select_from(User).where(User.tenant_id == user.tenant_id, User.is_active.is_(True)))
    return TenantResponse(
        id=tenant.id, name=tenant.name, plan=plan.id, plan_name=plan.name, plan_description=plan.description,
        status=tenant.status, members=members or 0, limits=plan_limits(plan), usage=await plan_usage(session, tenant.id),
    )


class TenantRenameRequest(BaseModel):
    name: str


@router.patch("/tenant", response_model=TenantResponse)
async def rename_tenant(
    request: TenantRenameRequest, user: User = Depends(require_owner), session: AsyncSession = Depends(get_session),
) -> TenantResponse:
    tenant = await session.get(Tenant, user.tenant_id)
    name = request.name.strip()
    if not 1 <= len(name) <= 255:
        raise HTTPException(status_code=400, detail="Name must be 1-255 characters")
    tenant.name = name
    await write_audit_log(session, user.tenant_id, user.id, "tenant_renamed", name)
    await session.commit()
    return await get_tenant(user, session)


@router.get("/members", response_model=List[MemberResponse])
async def list_members(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[MemberResponse]:
    rows = await session.scalars(select(User).where(User.tenant_id == user.tenant_id).order_by(User.id))
    return [MemberResponse.from_user(u) for u in rows]


async def _active_owner_count(session: AsyncSession, tenant_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(User).where(
            User.tenant_id == tenant_id, User.is_active.is_(True), User.role == UserRole.OWNER.value,
        )
    ) or 0


async def _member_or_404(session: AsyncSession, tenant_id: int, member_id: int) -> User:
    member = await session.get(User, member_id)
    if member is None or member.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Unknown team member")
    return member


@router.patch("/members/{member_id}", response_model=MemberResponse)
async def change_member_role(
    member_id: int, request: RoleChangeRequest,
    user: User = Depends(require_owner), session: AsyncSession = Depends(get_session),
) -> MemberResponse:
    if request.role not in ASSIGNABLE_ROLES:
        raise HTTPException(status_code=400, detail=f"Role must be one of {[r.value for r in ASSIGNABLE_ROLES]}")
    member = await _member_or_404(session, user.tenant_id, member_id)
    if member.role == UserRole.SUPER_ADMIN.value:
        raise HTTPException(status_code=403, detail="Platform administrators are managed outside the tenant")
    if member.id == user.id and request.role != UserRole.OWNER:
        if await _active_owner_count(session, user.tenant_id) <= 1:
            raise HTTPException(status_code=409, detail="You are the only owner - promote someone else first")
    if member.role == UserRole.OWNER.value and request.role != UserRole.OWNER and member.id != user.id:
        if await _active_owner_count(session, user.tenant_id) <= 1:
            raise HTTPException(status_code=409, detail="A tenant must keep at least one owner")
    previous = member.role
    member.role = request.role.value
    await write_audit_log(session, user.tenant_id, user.id, "member_role_changed", f"{member.email}: {previous} -> {member.role}")
    await session.commit()
    await session.refresh(member)
    return MemberResponse.from_user(member)


@router.delete("/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    member_id: int, user: User = Depends(require_owner), session: AsyncSession = Depends(get_session),
) -> None:
    """Deactivates (never deletes) - every trade, order and audit row stays attributed to them,
    but their token stops working immediately and they can no longer log in."""
    member = await _member_or_404(session, user.tenant_id, member_id)
    if member.id == user.id:
        raise HTTPException(status_code=409, detail="You cannot remove yourself - hand over ownership first")
    if member.role == UserRole.SUPER_ADMIN.value:
        raise HTTPException(status_code=403, detail="Platform administrators are managed outside the tenant")
    if member.role == UserRole.OWNER.value and await _active_owner_count(session, user.tenant_id) <= 1:
        raise HTTPException(status_code=409, detail="A tenant must keep at least one owner")
    if not member.is_active:
        return
    member.is_active = False
    await revoke_all_sessions(session, member.id, "removed from team")
    await write_audit_log(session, user.tenant_id, user.id, "member_removed", member.email)
    await session.commit()


class ResetLinkResponse(BaseModel):
    reset_url: str
    expires_at: str
    delivered_by_email: bool


@router.post("/members/{member_id}/reset-link", response_model=ResetLinkResponse)
async def issue_member_reset_link(
    member_id: int, http_request: Request, user: User = Depends(require_owner), session: AsyncSession = Depends(get_session),
) -> ResetLinkResponse:
    """The owner's way to help a locked-out teammate when the organisation has no email channel:
    a one-hour single-use reset link to hand over out of band (shown once). Also emailed when an
    email channel exists."""
    from app.auth.routes import _email_reset_link, _issue_reset, app_link
    member = await _member_or_404(session, user.tenant_id, member_id)
    if not member.is_active:
        raise HTTPException(status_code=409, detail="Member is deactivated - reactivate them first")
    token, record = await _issue_reset(session, member, http_request, user.id)
    link = app_link(http_request, reset=token)
    delivered = await _email_reset_link(session, member, link)
    await write_audit_log(session, user.tenant_id, user.id, "password_reset_issued_by_owner", f"{member.email} ({'emailed' if delivered else 'link handed over'})")
    await session.commit()
    return ResetLinkResponse(reset_url=link, expires_at=_as_utc(record.expires_at).isoformat(), delivered_by_email=delivered)


@router.post("/members/{member_id}/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_member_everywhere(
    member_id: int, user: User = Depends(require_owner), session: AsyncSession = Depends(get_session),
) -> None:
    """Owner's "that laptop was stolen" button: ends every session of a teammate without
    removing them - they simply log in again."""
    member = await _member_or_404(session, user.tenant_id, member_id)
    count = await revoke_all_sessions(session, member.id, f"revoked by owner {user.email}")
    await write_audit_log(session, user.tenant_id, user.id, "member_sessions_revoked", f"{member.email}: {count}")
    await session.commit()


@router.post("/members/{member_id}/reactivate", response_model=MemberResponse)
async def reactivate_member(
    member_id: int, user: User = Depends(require_owner), session: AsyncSession = Depends(get_session),
) -> MemberResponse:
    member = await _member_or_404(session, user.tenant_id, member_id)
    member.is_active = True
    await write_audit_log(session, user.tenant_id, user.id, "member_reactivated", member.email)
    await session.commit()
    await session.refresh(member)
    return MemberResponse.from_user(member)


@router.get("/invites", response_model=List[InviteResponse])
async def list_invites(user: User = Depends(require_owner), session: AsyncSession = Depends(get_session)) -> List[InviteResponse]:
    """Open (not accepted, not expired) invitations."""
    rows = await session.scalars(
        select(TenantInviteRecord).where(
            TenantInviteRecord.tenant_id == user.tenant_id, TenantInviteRecord.accepted_at.is_(None),
            TenantInviteRecord.expires_at > _utcnow(),
        ).order_by(TenantInviteRecord.id.desc())
    )
    return [InviteResponse.from_record(r) for r in rows]


@router.post("/invites", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
async def create_invite(
    request: InviteCreateRequest, http_request: Request,
    user: User = Depends(require_owner), session: AsyncSession = Depends(get_session),
) -> InviteResponse:
    if request.role not in INVITABLE_ROLES:
        raise HTTPException(status_code=400, detail=f"Invite role must be one of {[r.value for r in INVITABLE_ROLES]}")
    email = request.email.lower()
    await check_can_add_member(session, await session.get(Tenant, user.tenant_id))
    existing_user = await session.scalar(select(User).where(User.email == email))
    if existing_user is not None:
        detail = "That email already belongs to your team" if existing_user.tenant_id == user.tenant_id else "That email is already registered on the platform"
        raise HTTPException(status_code=409, detail=detail)
    # One open invite per email per tenant: re-inviting replaces (revokes) the earlier link.
    open_invites = await session.scalars(
        select(TenantInviteRecord).where(
            TenantInviteRecord.tenant_id == user.tenant_id, TenantInviteRecord.email == email,
            TenantInviteRecord.accepted_at.is_(None),
        )
    )
    for stale in open_invites:
        await session.delete(stale)

    token = secrets.token_urlsafe(32)
    record = TenantInviteRecord(
        tenant_id=user.tenant_id, email=email, role=request.role.value, token_hash=hash_invite_token(token),
        invited_by=user.id, expires_at=_utcnow() + timedelta(hours=INVITE_TTL_HOURS),
    )
    session.add(record)
    await session.flush()
    await write_audit_log(session, user.tenant_id, user.id, "member_invited", f"{email} as {record.role}")
    await session.commit()
    await session.refresh(record)
    return InviteResponse.from_record(record, invite_url(http_request, token))


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    invite_id: int, user: User = Depends(require_owner), session: AsyncSession = Depends(get_session),
) -> None:
    record = await session.get(TenantInviteRecord, invite_id)
    if record is None or record.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Unknown invite")
    await session.delete(record)
    await write_audit_log(session, user.tenant_id, user.id, "invite_revoked", record.email)
    await session.commit()
