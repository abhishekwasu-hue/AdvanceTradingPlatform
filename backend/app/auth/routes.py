import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.bootstrap import is_configured_super_admin
from app.audit.log import write_audit_log
from app.auth.dependencies import current_session_id, get_current_user
from app.auth.sessions import IssuedTokens, revoke_all_sessions, revoke_session, rotate_refresh_token, start_session
from app.auth.security import hash_password, verify_password
from app.core.enums import UserRole
from app.core.rate_limit import rate_limit
from app.db.models import Tenant, TenantInviteRecord, User, UserSessionRecord
from app.db.session import get_session

router = APIRouter(prefix="/api/auth", tags=["auth"])

# A bcrypt hash of an unguessable, unused password - compared against on login when the email
# doesn't exist, purely to make that branch take roughly as long as a real password check.
# Otherwise a missing-user response returns near-instantly while a wrong-password response
# takes a full bcrypt round, letting an attacker enumerate registered emails by response timing.
_DUMMY_PASSWORD_HASH = hash_password("not-a-real-password-used-only-for-timing-parity")

# Module-level (not inline in the route decorator) so tests can target this exact dependency via
# `app.dependency_overrides` - the whole point of a per-IP limiter is that a real client's IP
# doesn't change between requests, but every request in this test suite shares the TestClient's
# one fake IP, so leaving these active would rate-limit the test suite itself, not just an
# attacker. See tests/test_auth_api.py, which disables both by default, and
# tests/test_rate_limiting.py, which re-enables them to test the 429 behavior itself.
register_rate_limit = rate_limit("auth_register", limit=10, window_seconds=60)
login_rate_limit = rate_limit("auth_login", limit=10, window_seconds=60)
refresh_rate_limit = rate_limit("auth_refresh", limit=30, window_seconds=60)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: Optional[str] = None
    expires_in: Optional[int] = None


def _token_response(issued: IssuedTokens) -> TokenResponse:
    return TokenResponse(access_token=issued.access_token, refresh_token=issued.refresh_token, expires_in=issued.expires_in)


class RefreshRequest(BaseModel):
    refresh_token: str


class SessionResponse(BaseModel):
    id: int
    current: bool
    ip_address: Optional[str]
    user_agent: Optional[str]
    created_at: str
    last_used_at: str
    expires_at: str


class UserResponse(BaseModel):
    id: int
    email: str
    tenant_id: int
    role: str


@router.post(
    "/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(register_rate_limit)],
)
async def register(request: RegisterRequest, http_request: Request, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    """Every registration creates a new tenant (spec section 5-6's isolation boundary) with this
    user as its OWNER. Teammates join an existing tenant through an owner's invite instead
    (app/team/routes.py + /api/auth/invite/{token}/accept), never through this endpoint.
    """
    existing = await session.scalar(select(User).where(User.email == request.email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    tenant = Tenant(name=request.email, webhook_token=secrets.token_urlsafe(24))
    session.add(tenant)
    await session.flush()

    user = User(
        tenant_id=tenant.id, email=request.email, hashed_password=hash_password(request.password),
        role=UserRole.SUPER_ADMIN.value if is_configured_super_admin(request.email) else UserRole.OWNER.value,
    )
    session.add(user)
    await session.flush()
    await write_audit_log(session, tenant.id, user.id, "user_registered", request.email)
    issued = await start_session(session, user, http_request)
    await session.commit()
    return _token_response(issued)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(login_rate_limit)])
async def login(request: LoginRequest, http_request: Request, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    user = await session.scalar(select(User).where(User.email == request.email))
    # Always run a bcrypt comparison, even for an unknown email, so this endpoint's response
    # time doesn't leak whether an email is registered (see _DUMMY_PASSWORD_HASH above).
    password_ok = verify_password(request.password, user.hashed_password if user else _DUMMY_PASSWORD_HASH)
    if user is None or not password_ok or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    await write_audit_log(session, user.tenant_id, user.id, "user_login")
    issued = await start_session(session, user, http_request)
    await session.commit()
    return _token_response(issued)


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(id=user.id, email=user.email, tenant_id=user.tenant_id, role=user.role)


# --- Invitations (public: the invitee is not logged in yet) ------------------------------------

class InviteInfoResponse(BaseModel):
    email: str
    role: str
    tenant_name: str
    expires_at: str
    valid: bool
    reason: Optional[str] = None


class AcceptInviteRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def _invite_by_token(session: AsyncSession, token: str) -> Optional[TenantInviteRecord]:
    from app.team.routes import hash_invite_token
    return await session.scalar(select(TenantInviteRecord).where(TenantInviteRecord.token_hash == hash_invite_token(token)))


def _invite_problem(record: TenantInviteRecord) -> Optional[str]:
    if record.accepted_at is not None:
        return "This invitation has already been used"
    if _as_utc(record.expires_at) <= datetime.now(timezone.utc):
        return "This invitation has expired - ask the owner for a new one"
    return None


@router.get("/invite/{token}", response_model=InviteInfoResponse)
async def invite_info(token: str, session: AsyncSession = Depends(get_session)) -> InviteInfoResponse:
    """What the accept page shows before asking for a password. Unknown tokens are 404 so a link
    can't be probed for tenant names."""
    record = await _invite_by_token(session, token)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown invitation")
    tenant = await session.get(Tenant, record.tenant_id)
    problem = _invite_problem(record)
    return InviteInfoResponse(
        email=record.email, role=record.role, tenant_name=tenant.name if tenant else "",
        expires_at=_as_utc(record.expires_at).isoformat(), valid=problem is None, reason=problem,
    )


@router.post(
    "/invite/{token}/accept", response_model=TokenResponse, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(register_rate_limit)],
)
async def accept_invite(token: str, request: AcceptInviteRequest, http_request: Request, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    """Creates the invitee's account inside the inviting tenant with the invited role and logs
    them in. The email is fixed by the invite - the invitee only chooses a password."""
    record = await _invite_by_token(session, token)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown invitation")
    problem = _invite_problem(record)
    if problem:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=problem)
    if await session.scalar(select(User).where(User.email == record.email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        tenant_id=record.tenant_id, email=record.email, hashed_password=hash_password(request.password), role=record.role,
    )
    session.add(user)
    await session.flush()
    record.accepted_at = datetime.now(timezone.utc)
    record.accepted_user_id = user.id
    await write_audit_log(session, record.tenant_id, user.id, "invite_accepted", f"{record.email} as {record.role}")
    issued = await start_session(session, user, http_request)
    await session.commit()
    return _token_response(issued)


# --- Sessions (Phase C1) -------------------------------------------------------------------------

@router.post("/refresh", response_model=TokenResponse, dependencies=[Depends(refresh_rate_limit)])
async def refresh(request: RefreshRequest, http_request: Request, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    """Exchanges a refresh token for a new access + refresh pair (the old refresh token stops
    working). Reusing an already-rotated token revokes the session: log in again."""
    issued = await rotate_refresh_token(session, request.refresh_token, http_request)
    if issued is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token is invalid, expired or revoked")
    return _token_response(issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    user: User = Depends(get_current_user), session_id: Optional[int] = Depends(current_session_id),
    session: AsyncSession = Depends(get_session),
) -> None:
    if session_id is not None:
        record = await session.get(UserSessionRecord, session_id)
        if record is not None and record.user_id == user.id:
            await revoke_session(session, record, "logout")
    await write_audit_log(session, user.tenant_id, user.id, "user_logout")
    await session.commit()


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_everywhere(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> None:
    """Revokes every session of this user, this one included."""
    count = await revoke_all_sessions(session, user.id, "logout everywhere")
    await write_audit_log(session, user.tenant_id, user.id, "user_logout_all", f"{count} session(s)")
    await session.commit()


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    user: User = Depends(get_current_user), session_id: Optional[int] = Depends(current_session_id),
    session: AsyncSession = Depends(get_session),
) -> list[SessionResponse]:
    """This user's live sessions (devices), newest first."""
    rows = await session.scalars(
        select(UserSessionRecord).where(UserSessionRecord.user_id == user.id, UserSessionRecord.revoked_at.is_(None))
        .order_by(UserSessionRecord.last_used_at.desc())
    )
    return [
        SessionResponse(
            id=r.id, current=r.id == session_id, ip_address=r.ip_address, user_agent=r.user_agent,
            created_at=_as_utc(r.created_at).isoformat(), last_used_at=_as_utc(r.last_used_at).isoformat(),
            expires_at=_as_utc(r.expires_at).isoformat(),
        )
        for r in rows if _as_utc(r.expires_at) > datetime.now(timezone.utc)
    ]


@router.delete("/sessions/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_one_session(
    target_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> None:
    record = await session.get(UserSessionRecord, target_id)
    if record is None or record.user_id != user.id:
        raise HTTPException(status_code=404, detail="Unknown session")
    await revoke_session(session, record, "revoked by user")
    await write_audit_log(session, user.tenant_id, user.id, "session_revoked", f"#{target_id}")
    await session.commit()
