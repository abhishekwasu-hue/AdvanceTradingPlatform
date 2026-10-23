from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Tuple

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import decode_access_token
from app.core.enums import UserRole
from app.db.models import User, UserSessionRecord
from app.db.session import get_session

_bearer_scheme = HTTPBearer(auto_error=True)
_optional_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = int(payload["sub"])
    except Exception as exc:
        raise unauthorized from exc

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    # Every access token belongs to a login session; a revoked session (logout, log-out-
    # everywhere, member removed, password changed) invalidates the token immediately.
    if not await _session_alive(session, payload.get("sid")):
        raise unauthorized
    await _warm_tenant_key(session, user.tenant_id)
    return user


async def _warm_tenant_key(session: AsyncSession, tenant_id: int) -> None:
    """Phase N1: make the tenant's data key available to the sync encrypt/decrypt helpers for
    the rest of this request (a dict lookup once cached)."""
    from app.secrets_store.envelope import ensure_tenant_key
    await ensure_tenant_key(session, tenant_id)


async def _session_alive(session: AsyncSession, session_id) -> bool:
    from app.auth.sessions import session_is_live
    if session_id is None:
        return False
    return session_is_live(await session.get(UserSessionRecord, int(session_id)))


def current_session_id(credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme)) -> Optional[int]:
    """The login session behind this request's token (for "this device" markers and logout)."""
    try:
        sid = decode_access_token(credentials.credentials).get("sid")
        return int(sid) if sid is not None else None
    except Exception:
        return None


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> Optional[User]:
    """Same as `get_current_user`, but returns None instead of raising when no (or an invalid)
    token is supplied - for endpoints that work anonymously but do more when a user is logged in.
    """
    if credentials is None:
        return None
    try:
        payload = decode_access_token(credentials.credentials)
        user = await session.get(User, int(payload["sub"]))
        if user is None or not user.is_active or not await _session_alive(session, payload.get("sid")):
            return None
        await _warm_tenant_key(session, user.tenant_id)
        return user
    except Exception:
        return None


def require_role(*allowed: UserRole, active_tenant: bool = False, scope: Optional[str] = None) -> Callable[..., User]:
    """Dependency factory for RBAC-gated routes: `Depends(require_role(UserRole.SUPER_ADMIN))`.
    SUPER_ADMIN always passes, regardless of which roles are listed, since it's the platform-wide
    role above every tenant-scoped one. `require_role()` with no roles is therefore
    "SUPER_ADMIN only".
    """

    async def _check(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> User:
        await ensure_role(session, user, allowed, active_tenant=active_tenant, scope=scope)
        return user

    return _check


async def ensure_role(session: AsyncSession, user: User, allowed: Tuple[UserRole, ...], *, active_tenant: bool = False,
                      scope: Optional[str] = None) -> None:
    """`require_role`'s checks for code that is not a route (N3 S9: the Copilot's screener tools act for the caller and
    must pass the same gate the route would). Raises the same HTTPException."""
    if user.role != UserRole.SUPER_ADMIN.value and user.role not in {r.value for r in allowed}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role for this action")
    if scope is not None:
        # Phase N2: a per-member scope denial (users.scope_overrides) bites even when the role allows.
        from app.auth.scopes import has_scope, scope_denied
        if not has_scope(user, scope):
            raise scope_denied(user, scope)
    if active_tenant and user.role != UserRole.SUPER_ADMIN.value:
        # A suspended organisation keeps read access (its people can still see their positions
        # and history) but every trading/configuration write is refused (app/plans/limits.py).
        from app.plans.limits import ensure_tenant_active, load_tenant
        ensure_tenant_active(await load_tenant(session, user.tenant_id))


async def ensure_trader(session: AsyncSession, user: User, scope: str = "trading:write") -> None:
    """`require_trader` for non-route code."""
    await ensure_role(session, user, TRADING_ROLES, active_tenant=True, scope=scope)


# The two tenant-side gates every write endpoint uses. Reads stay on get_current_user, so a
# VIEWER (or platform SUPPORT staff) sees everything and changes nothing.
TRADING_ROLES = (UserRole.OWNER, UserRole.USER, UserRole.STRATEGY_CREATOR)

# Anything that places, configures or stops trading, or touches broker/alert credentials.
require_trader = require_role(*TRADING_ROLES, active_tenant=True, scope="trading:write")
# Team management (invites, roles, removing members): the tenant's owner(s) only.
require_owner = require_role(UserRole.OWNER, active_tenant=True, scope="team:manage")
# Z10-c: shared reference data every organisation sees (fundamentals, cited news events). Writes are attributed
# (`created_by`) and need a trading role of an active organisation; VIEWER and platform SUPPORT read only.
require_contributor = require_role(*TRADING_ROLES, active_tenant=True)


class MfaRequired(HTTPException):
    """403 with a machine-readable code so the UI can open the TOTP prompt and retry."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail, headers={"X-Step-Up": "mfa"})


async def ensure_mfa_session(session: AsyncSession, user: User, session_id: Optional[int], why: str, *,
                             within_minutes: Optional[int] = None) -> None:
    """Raises unless the user has MFA enabled *and* this session passed a TOTP check - and, with `within_minutes`, passed
    it no longer ago than that (A4: the step-up freshness window for sensitive writes)."""
    if not user.mfa_enabled:
        raise MfaRequired(f"{why} requires two-factor authentication - enable it from the Account tab first")
    record = await session.get(UserSessionRecord, session_id) if session_id is not None else None
    if record is None or record.mfa_verified_at is None:
        raise MfaRequired(f"{why} requires a fresh two-factor check on this session - enter your authenticator code")
    if within_minutes is not None:
        verified_at = record.mfa_verified_at
        verified_at = verified_at.replace(tzinfo=timezone.utc) if verified_at.tzinfo is None else verified_at
        if datetime.now(timezone.utc) - verified_at > timedelta(minutes=within_minutes):
            raise MfaRequired(f"{why} needs a fresh two-factor check (the last one was more than {within_minutes} minutes "
                              "ago). Enter the code from your authenticator app.")


_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def require_mfa_session(
    request: Request, user: User = Depends(get_current_user), session_id: Optional[int] = Depends(current_session_id),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Unconditional step-up: the admin console and platform-wide switches. A read needs a two-factor-checked session; a
    write also needs the check to be recent (`MFA_STEP_UP_MINUTES`, A4 / review R5 c)."""
    from app.core.config import MFA_STEP_UP_MINUTES
    fresh = None if request.method.upper() in _READ_METHODS else MFA_STEP_UP_MINUTES
    await ensure_mfa_session(session, user, session_id, "This action", within_minutes=fresh)
    return user


async def require_checked_mfa_session(
    user: User = Depends(get_current_user), session_id: Optional[int] = Depends(current_session_id),
    session: AsyncSession = Depends(get_session),
) -> User:
    """A two-factor-checked session, however long ago the check was: for the one admin write that only makes things
    safer - engaging the global kill switch - so an emergency stop is never held up by a code prompt (A4, RDY-28)."""
    await ensure_mfa_session(session, user, session_id, "This action")
    return user


async def ensure_admin_write_step_up(session: AsyncSession, user: User, session_id: Optional[int], why: str) -> None:
    """A4 / review R5 b: a SUPER_ADMIN write that lives inside a route every member uses (a GLOBAL risk limit, a platform-wide
    market event, a shared company profile) gets the same check as the admin routers' writes: two-factor, recent."""
    from app.core.config import MFA_STEP_UP_MINUTES
    await ensure_mfa_session(session, user, session_id, why, within_minutes=MFA_STEP_UP_MINUTES)


async def ensure_live_step_up(session: AsyncSession, user: User, session_id: Optional[int], why: str) -> None:
    """Conditional step-up: only when the tenant's owner turned on `require_mfa_for_live`
    (SUPER_ADMIN is always held to it)."""
    from app.db.models import Tenant
    tenant = await session.get(Tenant, user.tenant_id)
    if user.role == UserRole.SUPER_ADMIN.value or (tenant is not None and tenant.require_mfa_for_live):
        await ensure_mfa_session(session, user, session_id, why)
