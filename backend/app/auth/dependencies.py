from typing import Callable, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import decode_access_token
from app.core.enums import UserRole
from app.db.models import User
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
    if user is None:
        raise unauthorized
    return user


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
        return await session.get(User, int(payload["sub"]))
    except Exception:
        return None


def require_role(*allowed: UserRole) -> Callable[[User], User]:
    """Dependency factory for RBAC-gated routes: `Depends(require_role(UserRole.SUPER_ADMIN))`.
    SUPER_ADMIN always passes, regardless of which roles are listed, since it's the platform-wide
    role above every tenant-scoped one. No route uses this yet - it's the hook future
    admin/support/strategy-management endpoints wire into.
    """

    def _check(user: User = Depends(get_current_user)) -> User:
        if user.role != UserRole.SUPER_ADMIN.value and user.role not in {r.value for r in allowed}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role for this action")
        return user

    return _check
