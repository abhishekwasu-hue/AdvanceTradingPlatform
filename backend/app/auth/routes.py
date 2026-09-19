import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user
from app.auth.security import create_access_token, hash_password, verify_password
from app.core.enums import UserRole
from app.core.rate_limit import rate_limit
from app.db.models import Tenant, User
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


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    email: str
    tenant_id: int
    role: str


@router.post(
    "/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(register_rate_limit)],
)
async def register(request: RegisterRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    """Every registration gets its own new tenant (spec section 5-6's isolation boundary) - there
    is no invite-onto-an-existing-tenant flow yet, so V1 is one tenant per signup, and every
    tenant-scoped resource this user creates is isolated from every other tenant from day one.
    """
    existing = await session.scalar(select(User).where(User.email == request.email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    tenant = Tenant(name=request.email, webhook_token=secrets.token_urlsafe(24))
    session.add(tenant)
    await session.flush()

    user = User(
        tenant_id=tenant.id, email=request.email, hashed_password=hash_password(request.password),
        role=UserRole.USER.value,
    )
    session.add(user)
    await session.flush()
    await write_audit_log(session, tenant.id, user.id, "user_registered", request.email)
    await session.commit()

    return TokenResponse(access_token=create_access_token(user.id, user.email))


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(login_rate_limit)])
async def login(request: LoginRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    user = await session.scalar(select(User).where(User.email == request.email))
    # Always run a bcrypt comparison, even for an unknown email, so this endpoint's response
    # time doesn't leak whether an email is registered (see _DUMMY_PASSWORD_HASH above).
    password_ok = verify_password(request.password, user.hashed_password if user else _DUMMY_PASSWORD_HASH)
    if user is None or not password_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    await write_audit_log(session, user.tenant_id, user.id, "user_login")
    await session.commit()
    return TokenResponse(access_token=create_access_token(user.id, user.email))


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(id=user.id, email=user.email, tenant_id=user.tenant_id, role=user.role)
