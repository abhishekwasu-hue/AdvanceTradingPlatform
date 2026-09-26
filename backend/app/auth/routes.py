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
from app.auth import mfa
from app.auth.lockout import HISTORY_LIMIT, is_new_device, lock_reason, record_login_event
from app.auth.passwords import MAX_LENGTH, MIN_LENGTH, password_problem
from app.auth.sessions import IssuedTokens, revoke_all_sessions, revoke_session, rotate_refresh_token, start_session
from app.auth.security import hash_password, verify_password
from app.core.enums import UserRole
from app.core.rate_limit import rate_limit
from app.db.models import (
    AlertChannelRecord, LoginEventRecord, PasswordResetRecord, Tenant, TenantInviteRecord, User, UserSessionRecord,
)
from app.notifications.service import notify
from app.core.enums import NotificationSeverity, NotificationType
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
    # Either a full token pair, or - when the account has MFA on - `mfa_required` with a short
    # challenge token to present to /auth/mfa/verify together with the authenticator code.
    access_token: str = ""
    token_type: str = "bearer"
    refresh_token: Optional[str] = None
    expires_in: Optional[int] = None
    mfa_required: bool = False
    mfa_token: Optional[str] = None


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
    mfa_enabled: bool = False


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
    email = request.email.lower()
    locked = await lock_reason(session, email, http_request)
    if locked:
        await record_login_event(session, email=email, user=None, success=False, reason="locked", request=http_request)
        await session.commit()
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=locked)

    user = await session.scalar(select(User).where(User.email == email))
    # Always run a bcrypt comparison, even for an unknown email, so this endpoint's response
    # time doesn't leak whether an email is registered (see _DUMMY_PASSWORD_HASH above).
    password_ok = verify_password(request.password, user.hashed_password if user else _DUMMY_PASSWORD_HASH)
    if user is None or not password_ok or not user.is_active:
        reason = "unknown_email" if user is None else "inactive" if not user.is_active else "bad_password"
        await record_login_event(session, email=email, user=user, success=False, reason=reason, request=http_request)
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if user.mfa_enabled:
        # Password is right; the session only starts once the authenticator code checks out.
        await write_audit_log(session, user.tenant_id, user.id, "user_login_mfa_challenge")
        await session.commit()
        return TokenResponse(mfa_required=True, mfa_token=mfa.create_mfa_token(user.id))

    return await _complete_login(session, user, http_request, "password")


async def _complete_login(session: AsyncSession, user: User, http_request: Request, method: str) -> TokenResponse:
    """Shared tail of a successful login (password-only, or after the MFA step): new-device
    alert, login event, audit, session."""
    new_device = await is_new_device(session, user, http_request)
    await record_login_event(session, email=user.email, user=user, success=True, reason=method, request=http_request)
    await write_audit_log(session, user.tenant_id, user.id, "user_login", method)
    issued = await start_session(session, user, http_request, mfa_verified=method.startswith("mfa"))
    await session.commit()
    if new_device:
        ip = http_request.client.host if http_request.client else "unknown"
        agent = (http_request.headers.get("user-agent") or "unknown browser")[:120]
        await notify(
            session, user.tenant_id, NotificationType.SECURITY, title=f"New device login: {user.email}",
            message=f"Logged in from {ip} ({agent}). If this was not you, log out everywhere and change your password "
                    "from the Account tab.", severity=NotificationSeverity.WARNING, user_id=user.id,
        )
    return _token_response(issued)


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(id=user.id, email=user.email, tenant_id=user.tenant_id, role=user.role, mfa_enabled=user.mfa_enabled)


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


# --- Two-factor authentication (Phase C3) --------------------------------------------------------

mfa_rate_limit = rate_limit("auth_mfa", limit=10, window_seconds=60)


class MfaVerifyRequest(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=16)


class MfaCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=16)


class MfaDisableRequest(BaseModel):
    password: str
    code: str = Field(min_length=6, max_length=16)


class MfaEnrolResponse(BaseModel):
    secret: str
    otpauth_uri: str


class MfaBackupCodesResponse(BaseModel):
    backup_codes: list[str]


class MfaStatusResponse(BaseModel):
    enabled: bool
    enabled_at: Optional[str]
    pending_enrolment: bool
    backup_codes_remaining: int
    session_verified: bool
    required_for_live: bool


@router.post("/mfa/verify", response_model=TokenResponse, dependencies=[Depends(mfa_rate_limit)])
async def mfa_verify_login(request: MfaVerifyRequest, http_request: Request, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    """Second login step: the challenge token from /login plus a TOTP or backup code."""
    user_id = mfa.parse_mfa_token(request.mfa_token)
    user = await session.get(User, user_id) if user_id is not None else None
    if user is None or not user.is_active or not user.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login challenge is invalid or expired - start again")
    locked = await lock_reason(session, user.email, http_request)
    if locked:
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=locked)
    ok, method = await mfa.verify_code(session, user, request.code)
    if not ok:
        await record_login_event(session, email=user.email, user=user, success=False, reason="mfa_failed", request=http_request)
        await write_audit_log(session, user.tenant_id, user.id, "mfa_login_failed")
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authenticator code")
    return await _complete_login(session, user, http_request, f"mfa:{method}")


@router.get("/mfa/status", response_model=MfaStatusResponse)
async def mfa_status(
    user: User = Depends(get_current_user), session_id: Optional[int] = Depends(current_session_id),
    session: AsyncSession = Depends(get_session),
) -> MfaStatusResponse:
    current = await session.get(UserSessionRecord, session_id) if session_id is not None else None
    tenant = await session.get(Tenant, user.tenant_id)
    return MfaStatusResponse(
        enabled=user.mfa_enabled, enabled_at=_as_utc(user.mfa_enabled_at).isoformat() if user.mfa_enabled_at else None,
        pending_enrolment=bool(user.mfa_secret_encrypted) and not user.mfa_enabled,
        backup_codes_remaining=await mfa.backup_codes_remaining(session, user.id) if user.mfa_enabled else 0,
        session_verified=bool(current and current.mfa_verified_at),
        required_for_live=bool(tenant and tenant.require_mfa_for_live) or user.role == UserRole.SUPER_ADMIN.value,
    )


@router.post("/mfa/enrol", response_model=MfaEnrolResponse)
async def mfa_enrol(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> MfaEnrolResponse:
    """Starts (or restarts) enrolment: a new secret to add to an authenticator app. Nothing is
    enforced until /mfa/confirm proves the app produces the right codes."""
    if user.mfa_enabled:
        raise HTTPException(status_code=409, detail="Two-factor authentication is already enabled - disable it first to re-enrol")
    secret = mfa.generate_secret()
    mfa.store_secret(user, secret)
    await session.commit()
    return MfaEnrolResponse(secret=secret, otpauth_uri=mfa.provisioning_uri(secret, user.email))


@router.post("/mfa/confirm", response_model=MfaBackupCodesResponse)
async def mfa_confirm(
    request: MfaCodeRequest, user: User = Depends(get_current_user), session_id: Optional[int] = Depends(current_session_id),
    session: AsyncSession = Depends(get_session),
) -> MfaBackupCodesResponse:
    """Turns MFA on once a code from the freshly enrolled app verifies. Returns the backup codes
    - the only time they are ever shown."""
    if user.mfa_enabled:
        raise HTTPException(status_code=409, detail="Two-factor authentication is already enabled")
    secret = mfa.load_secret(user)
    if not secret:
        raise HTTPException(status_code=400, detail="Start enrolment first")
    if not mfa.verify_totp(secret, request.code):
        raise HTTPException(status_code=400, detail="That code did not match - check the app's clock and try the next code")
    user.mfa_enabled = True
    user.mfa_enabled_at = datetime.now(timezone.utc)
    codes = await mfa.issue_backup_codes(session, user)
    if session_id is not None:
        current = await session.get(UserSessionRecord, session_id)
        if current is not None:
            current.mfa_verified_at = datetime.now(timezone.utc)
    await write_audit_log(session, user.tenant_id, user.id, "mfa_enabled")
    await session.commit()
    return MfaBackupCodesResponse(backup_codes=codes)


@router.post("/mfa/step-up", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(mfa_rate_limit)])
async def mfa_step_up(
    request: MfaCodeRequest, user: User = Depends(get_current_user), session_id: Optional[int] = Depends(current_session_id),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Marks the current session as two-factor verified (for a session that logged in before
    MFA was enabled, or after a refresh on a device that never entered a code)."""
    if not user.mfa_enabled:
        raise HTTPException(status_code=400, detail="Two-factor authentication is not enabled")
    ok, method = await mfa.verify_code(session, user, request.code)
    if not ok:
        await write_audit_log(session, user.tenant_id, user.id, "mfa_step_up_failed")
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authenticator code")
    current = await session.get(UserSessionRecord, session_id) if session_id is not None else None
    if current is None:
        raise HTTPException(status_code=400, detail="No login session to verify")
    current.mfa_verified_at = datetime.now(timezone.utc)
    await write_audit_log(session, user.tenant_id, user.id, "mfa_step_up", method)
    await session.commit()


@router.post("/mfa/backup-codes", response_model=MfaBackupCodesResponse, dependencies=[Depends(mfa_rate_limit)])
async def mfa_regenerate_backup_codes(
    request: MfaCodeRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> MfaBackupCodesResponse:
    if not user.mfa_enabled:
        raise HTTPException(status_code=400, detail="Two-factor authentication is not enabled")
    secret = mfa.load_secret(user)
    if not secret or not mfa.verify_totp(secret, request.code):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authenticator code")
    codes = await mfa.issue_backup_codes(session, user)
    await write_audit_log(session, user.tenant_id, user.id, "mfa_backup_codes_regenerated")
    await session.commit()
    return MfaBackupCodesResponse(backup_codes=codes)


@router.post("/mfa/disable", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(mfa_rate_limit)])
async def mfa_disable(
    request: MfaDisableRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> None:
    """Needs the password *and* a current code, so neither a stolen session nor a stolen
    password alone can switch the second factor off."""
    if not user.mfa_enabled:
        raise HTTPException(status_code=400, detail="Two-factor authentication is not enabled")
    if not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Password is incorrect")
    ok, _ = await mfa.verify_code(session, user, request.code)
    if not ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authenticator code")
    if user.role == UserRole.SUPER_ADMIN.value:
        raise HTTPException(status_code=409, detail="Platform administrators must keep two-factor authentication on")
    user.mfa_enabled = False
    user.mfa_secret_encrypted = None
    user.mfa_enabled_at = None
    await mfa.issue_backup_codes(session, user)  # replaces the set...
    from app.db.models import MfaBackupCodeRecord
    for record in await session.scalars(select(MfaBackupCodeRecord).where(MfaBackupCodeRecord.user_id == user.id)):
        await session.delete(record)  # ...then removes it entirely
    await write_audit_log(session, user.tenant_id, user.id, "mfa_disabled")
    await session.commit()


# --- Login history (Phase C4) ------------------------------------------------------------------

class LoginEventResponse(BaseModel):
    id: int
    success: bool
    reason: str
    ip_address: Optional[str]
    user_agent: Optional[str]
    created_at: str


@router.get("/login-history", response_model=list[LoginEventResponse])
async def login_history(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[LoginEventResponse]:
    """The last attempts to log in as *you* - successes and failures alike, with where from."""
    rows = await session.scalars(
        select(LoginEventRecord).where(LoginEventRecord.email == user.email.lower())
        .order_by(LoginEventRecord.id.desc()).limit(HISTORY_LIMIT)
    )
    return [
        LoginEventResponse(id=r.id, success=r.success, reason=r.reason, ip_address=r.ip_address, user_agent=r.user_agent,
                           created_at=_as_utc(r.created_at).isoformat())
        for r in rows
    ]
