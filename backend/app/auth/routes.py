import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.bootstrap import is_configured_super_admin
from app.audit.log import write_audit_log
from app.auth.dependencies import current_session_id, get_current_user
from app.auth.passwords import MAX_LENGTH, MIN_LENGTH, password_problem
from app.auth.sessions import IssuedTokens, revoke_all_sessions, revoke_session, rotate_refresh_token, start_session
from app.auth.security import hash_password, verify_password
from app.core.enums import UserRole
from app.core.rate_limit import rate_limit
from app.db.models import AlertChannelRecord, PasswordResetRecord, Tenant, TenantInviteRecord, User, UserSessionRecord
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
forgot_rate_limit = rate_limit("auth_forgot", limit=5, window_seconds=900)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_LENGTH, max_length=MAX_LENGTH)


def _enforce_password_policy(password: str, email: str) -> None:
    problem = password_problem(password, email)
    if problem:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=problem)


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
    _enforce_password_policy(request.password, request.email)

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
    password: str = Field(min_length=MIN_LENGTH, max_length=MAX_LENGTH)


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
    _enforce_password_policy(request.password, record.email)

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


# --- Passwords (Phase C2) --------------------------------------------------------------------------

RESET_TTL_MINUTES = 60


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetInfoResponse(BaseModel):
    email_hint: str
    valid: bool
    reason: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=MIN_LENGTH, max_length=MAX_LENGTH)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=MIN_LENGTH, max_length=MAX_LENGTH)


class ResetLinkResponse(BaseModel):
    reset_url: str
    expires_at: str
    delivered_by_email: bool


def _hash_token(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:2]}{'*' * max(1, len(local) - 2)}@{domain}"


def app_link(request: Request, **params: str) -> str:
    from urllib.parse import urlencode
    from app.core.config import FRONTEND_URL
    base = FRONTEND_URL if FRONTEND_URL.startswith("http") else str(request.base_url).rstrip("/") + "/"
    return f"{base}?{urlencode(params)}"


async def _issue_reset(session: AsyncSession, user: User, request: Request, requested_by: Optional[int]) -> tuple[str, PasswordResetRecord]:
    token = secrets.token_urlsafe(32)
    record = PasswordResetRecord(
        user_id=user.id, token_hash=_hash_token(token), requested_ip=request.client.host if request.client else None,
        requested_by=requested_by, expires_at=datetime.now(timezone.utc) + timedelta(minutes=RESET_TTL_MINUTES),
    )
    session.add(record)
    await session.flush()
    return token, record


async def _email_reset_link(session: AsyncSession, user: User, link: str) -> bool:
    """Best effort: send through the tenant's own SMTP alert channel, to the user's address.
    Returns False when there is no such channel or sending fails - never raises."""
    from app.alerts.channels import decrypt_config
    from app.alerts.dispatcher import send_email
    channel = await session.scalar(select(AlertChannelRecord).where(
        AlertChannelRecord.tenant_id == user.tenant_id, AlertChannelRecord.channel_type == "EMAIL", AlertChannelRecord.enabled.is_(True),
    ))
    if channel is None:
        return False
    try:
        config = decrypt_config(channel).model_copy(update={"to_addresses": [user.email]})
        await send_email(
            config, "Reset your Advance Trading Platform password",
            f"Someone (hopefully you) asked to reset the password for {user.email}.\n\n"
            f"Open this link within {RESET_TTL_MINUTES} minutes to choose a new password:\n{link}\n\n"
            "If you did not ask for this, ignore this email - the link works only once and your password is unchanged.",
        )
        return True
    except Exception:  # noqa: BLE001 - delivery is best effort; the owner path still exists
        return False


@router.post("/password/forgot", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(forgot_rate_limit)])
async def forgot_password(request: ForgotPasswordRequest, http_request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    """Always 202 with the same message, whether or not the email exists (no account enumeration).
    When the user's organisation has an email alert channel the link is sent through it;
    otherwise the organisation owner can generate one from the Team tab."""
    user = await session.scalar(select(User).where(User.email == request.email.lower()))
    if user is not None and user.is_active:
        token, _ = await _issue_reset(session, user, http_request, None)
        link = app_link(http_request, reset=token)
        delivered = await _email_reset_link(session, user, link)
        await write_audit_log(session, user.tenant_id, user.id, "password_reset_requested", "emailed" if delivered else "no email channel - owner must issue link")
        await session.commit()
    return {"detail": "If that email belongs to an account, a reset link has been sent (or ask your organisation owner for one)."}


async def _reset_by_token(session: AsyncSession, token: str) -> Optional[PasswordResetRecord]:
    return await session.scalar(select(PasswordResetRecord).where(PasswordResetRecord.token_hash == _hash_token(token)))


def _reset_problem(record: PasswordResetRecord) -> Optional[str]:
    if record.used_at is not None:
        return "This reset link has already been used"
    if _as_utc(record.expires_at) <= datetime.now(timezone.utc):
        return "This reset link has expired - request a new one"
    return None


@router.get("/password/reset/{token}", response_model=ResetInfoResponse)
async def reset_info(token: str, session: AsyncSession = Depends(get_session)) -> ResetInfoResponse:
    record = await _reset_by_token(session, token)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown reset link")
    user = await session.get(User, record.user_id)
    problem = _reset_problem(record)
    return ResetInfoResponse(email_hint=_mask_email(user.email) if user else "", valid=problem is None, reason=problem)


@router.post("/password/reset/{token}", response_model=TokenResponse, dependencies=[Depends(login_rate_limit)])
async def reset_password(token: str, request: ResetPasswordRequest, http_request: Request, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    """Sets the new password, burns the link, ends every existing session and logs the user in."""
    record = await _reset_by_token(session, token)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown reset link")
    problem = _reset_problem(record)
    if problem:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=problem)
    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This account is no longer active")
    _enforce_password_policy(request.password, user.email)
    user.hashed_password = hash_password(request.password)
    record.used_at = datetime.now(timezone.utc)
    await revoke_all_sessions(session, user.id, "password reset")
    await write_audit_log(session, user.tenant_id, user.id, "password_reset_completed")
    issued = await start_session(session, user, http_request)
    await session.commit()
    return _token_response(issued)


@router.post("/password/change", response_model=TokenResponse)
async def change_password(
    request: ChangePasswordRequest, http_request: Request,
    user: User = Depends(get_current_user), session_id: Optional[int] = Depends(current_session_id),
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    """Requires the current password. Every *other* session is ended; this one continues with
    a fresh token pair."""
    if not verify_password(request.current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")
    if request.current_password == request.new_password:
        raise HTTPException(status_code=400, detail="New password must differ from the current one")
    _enforce_password_policy(request.new_password, user.email)
    user.hashed_password = hash_password(request.new_password)
    count = await revoke_all_sessions(session, user.id, "password changed", except_session_id=session_id)
    await write_audit_log(session, user.tenant_id, user.id, "password_changed", f"{count} other session(s) ended")
    if session_id is not None:
        current = await session.get(UserSessionRecord, session_id)
        if current is not None:
            await revoke_session(session, current, "password changed (rotated)")
    issued = await start_session(session, user, http_request)
    await session.commit()
    return _token_response(issued)
