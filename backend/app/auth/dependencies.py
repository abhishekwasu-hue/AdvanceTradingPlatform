from typing import Callable, Optional, Sequence

from fastapi import Depends, HTTPException, status
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
        refused = await role_refusal(session, user, allowed, active_tenant=active_tenant, scope=scope)
        if refused is not None:
            raise refused
        return user

    return _check


async def role_refusal(session: AsyncSession, user: User, allowed: Sequence[UserRole], *, active_tenant: bool = False,
                       scope: Optional[str] = None) -> Optional[HTTPException]:
    """The refusal `require_role(*allowed, ...)` would raise for this user, or None when it lets them through - the one
    check behind the dependency and behind the screens that only show a control the user may use (N2-6 Exit now)."""
    if user.role != UserRole.SUPER_ADMIN.value and user.role not in {r.value for r in allowed}:
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role for this action")
    if scope is not None:
        # Phase N2: a per-member scope denial (users.scope_overrides) bites even when the role allows.
        from app.auth.scopes import has_scope, scope_denied
        if not has_scope(user, scope):
            return scope_denied(user, scope)
    if active_tenant and user.role != UserRole.SUPER_ADMIN.value:
        # A suspended organisation keeps read access (its people can still see their positions
        # and history) but every trading/configuration write is refused (app/plans/limits.py).
        from app.plans.limits import ensure_tenant_active, load_tenant
        try:
            ensure_tenant_active(await load_tenant(session, user.tenant_id))
        except HTTPException as exc:
            return exc
    return None


# The two tenant-side gates every write endpoint uses. Reads stay on get_current_user, so a
# VIEWER (or platform SUPPORT staff) sees everything and changes nothing.
TRADING_ROLES = (UserRole.OWNER, UserRole.USER, UserRole.STRATEGY_CREATOR)

# Anything that places, configures or stops trading, or touches broker/alert credentials.
TRADER_SCOPE = "trading:write"
require_trader = require_role(*TRADING_ROLES, active_tenant=True, scope=TRADER_SCOPE)


async def may_trade(session: AsyncSession, user: User) -> bool:
    """Whether `require_trader` lets this user through (same check, no exception)."""
    return await role_refusal(session, user, TRADING_ROLES, active_tenant=True, scope=TRADER_SCOPE) is None
# Team management (invites, roles, removing members): the tenant's owner(s) only.
require_owner = require_role(UserRole.OWNER, active_tenant=True, scope="team:manage")
# Z10-c: shared reference data every organisation sees (fundamentals, cited news events). Writes are attributed
# (`created_by`) and need a trading role of an active organisation; VIEWER and platform SUPPORT read only.
require_contributor = require_role(*TRADING_ROLES, active_tenant=True)


class MfaRequired(HTTPException):
    """403 with a machine-readable code so the UI can open the TOTP prompt and retry."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail, headers={"X-Step-Up": "mfa"})


async def ensure_mfa_session(session: AsyncSession, user: User, session_id: Optional[int], why: str) -> None:
    """Raises unless the user has MFA enabled *and* this session passed a TOTP check."""
    if not user.mfa_enabled:
        raise MfaRequired(f"{why} requires two-factor authentication - enable it from the Account tab first")
    record = await session.get(UserSessionRecord, session_id) if session_id is not None else None
    if record is None or record.mfa_verified_at is None:
        raise MfaRequired(f"{why} requires a fresh two-factor check on this session - enter your authenticator code")


async def require_mfa_session(
    user: User = Depends(get_current_user), session_id: Optional[int] = Depends(current_session_id),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Unconditional step-up: the admin console and platform-wide switches."""
    await ensure_mfa_session(session, user, session_id, "This action")
    return user


async def ensure_live_step_up(session: AsyncSession, user: User, session_id: Optional[int], why: str) -> None:
    """Conditional step-up: only when the tenant's owner turned on `require_mfa_for_live`
    (SUPER_ADMIN is always held to it)."""
    from app.db.models import Tenant
    tenant = await session.get(Tenant, user.tenant_id)
    if user.role == UserRole.SUPER_ADMIN.value or (tenant is not None and tenant.require_mfa_for_live):
        await ensure_mfa_session(session, user, session_id, why)
