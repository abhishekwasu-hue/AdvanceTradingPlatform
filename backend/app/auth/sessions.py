"""Login sessions and rotating refresh tokens (Phase C1).

A login creates a `UserSessionRecord` and hands the client two things: a short-lived access
JWT carrying the session id, and an opaque refresh token whose SHA-256 is the only thing stored.
Refreshing rotates the token (the old hash is kept as `previous_token_hash` for one step);
presenting a token that was already rotated means two parties hold the same credential, so the
session is revoked outright rather than guessing which one is the real user.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import create_access_token
from app.core.config import JWT_EXPIRE_MINUTES, REFRESH_TOKEN_DAYS
from app.db.models import User, UserSessionRecord


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_info(request: Optional[Request]) -> Tuple[Optional[str], Optional[str]]:
    if request is None:
        return None, None
    ip = request.client.host if request.client else None
    agent = request.headers.get("user-agent")
    return ip, (agent[:300] if agent else None)


class IssuedTokens:
    def __init__(self, access_token: str, refresh_token: str, session: UserSessionRecord) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.session = session
        self.expires_in = JWT_EXPIRE_MINUTES * 60


async def start_session(session: AsyncSession, user: User, request: Optional[Request] = None) -> IssuedTokens:
    """Creates the session row and issues both tokens. Does not commit."""
    refresh_token = secrets.token_urlsafe(48)
    ip, agent = client_info(request)
    record = UserSessionRecord(
        user_id=user.id, tenant_id=user.tenant_id, refresh_token_hash=hash_refresh_token(refresh_token),
        ip_address=ip, user_agent=agent, expires_at=_utcnow() + timedelta(days=REFRESH_TOKEN_DAYS),
    )
    session.add(record)
    await session.flush()
    return IssuedTokens(create_access_token(user.id, user.email, record.id), refresh_token, record)


def session_is_live(record: Optional[UserSessionRecord], now: Optional[datetime] = None) -> bool:
    if record is None or record.revoked_at is not None:
        return False
    return _as_utc(record.expires_at) > (now or _utcnow())


async def rotate_refresh_token(
    session: AsyncSession, refresh_token: str, request: Optional[Request] = None,
) -> Optional[IssuedTokens]:
    """Exchanges a valid refresh token for a new pair. Returns None when the token is unknown,
    expired or revoked. A token that was already rotated (reuse) revokes the session and returns
    None - the legitimate client will be asked to log in again, which is the safe outcome."""
    token_hash = hash_refresh_token(refresh_token)
    record = await session.scalar(select(UserSessionRecord).where(UserSessionRecord.refresh_token_hash == token_hash))
    if record is None:
        reused = await session.scalar(select(UserSessionRecord).where(UserSessionRecord.previous_token_hash == token_hash))
        if reused is not None and reused.revoked_at is None:
            reused.revoked_at = _utcnow()
            reused.revoke_reason = "refresh token reuse detected"
            await session.commit()
        return None
    if not session_is_live(record):
        return None
    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        return None

    new_token = secrets.token_urlsafe(48)
    record.previous_token_hash = record.refresh_token_hash
    record.refresh_token_hash = hash_refresh_token(new_token)
    record.last_used_at = _utcnow()
    record.expires_at = _utcnow() + timedelta(days=REFRESH_TOKEN_DAYS)
    ip, agent = client_info(request)
    if ip:
        record.ip_address = ip
    if agent:
        record.user_agent = agent
    await session.commit()
    return IssuedTokens(create_access_token(user.id, user.email, record.id), new_token, record)


async def revoke_session(session: AsyncSession, record: UserSessionRecord, reason: str) -> None:
    if record.revoked_at is None:
        record.revoked_at = _utcnow()
        record.revoke_reason = reason


async def revoke_all_sessions(
    session: AsyncSession, user_id: int, reason: str, except_session_id: Optional[int] = None,
) -> int:
    """Revokes every live session of a user (optionally keeping the current one). Returns count."""
    query = update(UserSessionRecord).where(
        UserSessionRecord.user_id == user_id, UserSessionRecord.revoked_at.is_(None),
    )
    if except_session_id is not None:
        query = query.where(UserSessionRecord.id != except_session_id)
    result = await session.execute(query.values(revoked_at=_utcnow(), revoke_reason=reason))
    return result.rowcount or 0
