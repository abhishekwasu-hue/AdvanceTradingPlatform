"""Login protection (Phase C4): a durable record of every login attempt, lockout after
repeated failures, and "new device" detection.

Lockout is computed from the `login_events` table, not from an in-process counter, so it holds
across API instances and restarts - unlike the per-IP request limiter (app/core/rate_limit.py),
which only slows a single client down. Both are per-email *and* per-IP: per-email stops a
targeted brute force whatever IPs it comes from, per-IP stops one machine spraying many emails.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.metrics import LOGIN_ATTEMPTS
from app.db.models import LoginEventRecord, User

LOCKOUT_WINDOW_MINUTES = 15
MAX_FAILURES_PER_EMAIL = 10
MAX_FAILURES_PER_IP = 50
HISTORY_LIMIT = 50


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _client(request: Optional[Request]) -> Tuple[Optional[str], Optional[str]]:
    if request is None:
        return None, None
    ip = request.client.host if request.client else None
    agent = request.headers.get("user-agent")
    return ip, (agent[:300] if agent else None)


async def record_login_event(
    session: AsyncSession, *, email: str, user: Optional[User], success: bool, reason: str, request: Optional[Request],
) -> LoginEventRecord:
    ip, agent = _client(request)
    record = LoginEventRecord(
        user_id=user.id if user else None, tenant_id=user.tenant_id if user else None, email=email.lower(),
        success=success, reason=reason, ip_address=ip, user_agent=agent,
    )
    session.add(record)
    await session.flush()
    LOGIN_ATTEMPTS.labels(success=str(success).lower(), reason=reason[:40]).inc()
    return record


async def _failures_since(session: AsyncSession, since: datetime, *, email: Optional[str] = None, ip: Optional[str] = None) -> int:
    query = select(func.count()).select_from(LoginEventRecord).where(
        LoginEventRecord.success.is_(False), LoginEventRecord.created_at >= since,
    )
    if email is not None:
        query = query.where(LoginEventRecord.email == email.lower())
    if ip is not None:
        query = query.where(LoginEventRecord.ip_address == ip)
    return await session.scalar(query) or 0


async def lock_reason(session: AsyncSession, email: str, request: Optional[Request]) -> Optional[str]:
    """Why this login must be refused before even checking the password, or None."""
    since = _utcnow() - timedelta(minutes=LOCKOUT_WINDOW_MINUTES)
    if await _failures_since(session, since, email=email) >= MAX_FAILURES_PER_EMAIL:
        return f"Too many failed attempts for this account - try again in {LOCKOUT_WINDOW_MINUTES} minutes"
    ip, _ = _client(request)
    if ip and await _failures_since(session, since, ip=ip) >= MAX_FAILURES_PER_IP:
        return f"Too many failed attempts from this network - try again in {LOCKOUT_WINDOW_MINUTES} minutes"
    return None


async def is_new_device(session: AsyncSession, user: User, request: Optional[Request]) -> bool:
    """True when this user has never successfully logged in from this IP + user agent before.
    (Evaluated *before* the current success is recorded.)"""
    ip, agent = _client(request)
    if ip is None and agent is None:
        return False
    previous = await session.scalar(
        select(LoginEventRecord.id).where(
            LoginEventRecord.user_id == user.id, LoginEventRecord.success.is_(True),
            LoginEventRecord.ip_address == ip, LoginEventRecord.user_agent == agent,
        ).limit(1)
    )
    return previous is None
