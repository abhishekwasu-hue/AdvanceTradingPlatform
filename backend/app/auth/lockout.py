"""Login protection (Phase C4, reshaped in P0.2 / S1): a durable record of every login attempt, a
progressive delay per email, a lockout per IP, an optional CAPTCHA demand, and "new device" detection.

Everything is computed from the `login_events` table, not from an in-process counter, so it holds across
API instances and restarts. Per email the protection is a *delay*, never a hard lock: after
LOGIN_DELAY_AFTER_FAILURES failures the next attempt must wait 2^(n-3) seconds (capped at
LOGIN_DELAY_MAX_SECONDS) since the last failure, so a brute force slows to a few attempts a minute while the
real owner - who types the right password after the pause - is never locked out by someone spamming their
address. Per IP there is still a hard cap: one machine spraying many emails is refused for the window.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.metrics import LOGIN_ATTEMPTS
from app.db.models import LoginEventRecord, User

from app.core.config import LOGIN_DELAY_AFTER_FAILURES, LOGIN_DELAY_MAX_SECONDS

LOCKOUT_WINDOW_MINUTES = 15
MAX_FAILURES_PER_IP = 50
HISTORY_LIMIT = 50


@dataclass
class LoginRefusal:
    reason: str
    status_code: int                 # 423 (network lock) or 429 (wait)
    retry_after: int = 0


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


async def _failures_since(session: AsyncSession, since: datetime, *, email: Optional[str] = None, ip: Optional[str] = None) -> Tuple[int, Optional[datetime]]:
    """(count, most recent) of failed attempts since `since` for the email and/or IP."""
    query = select(func.count(), func.max(LoginEventRecord.created_at)).select_from(LoginEventRecord).where(
        LoginEventRecord.success.is_(False), LoginEventRecord.created_at >= since,
    )
    if email is not None:
        query = query.where(LoginEventRecord.email == email.lower())
    if ip is not None:
        query = query.where(LoginEventRecord.ip_address == ip)
    count, latest = (await session.execute(query)).one()
    if latest is not None and latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return int(count or 0), latest


def required_delay_seconds(failures: int) -> int:
    """Seconds the next attempt must wait after `failures` recent failures on one email: 0 below the threshold,
    then 1, 2, 4, ... capped."""
    if failures < LOGIN_DELAY_AFTER_FAILURES:
        return 0
    return min(2 ** (failures - LOGIN_DELAY_AFTER_FAILURES), LOGIN_DELAY_MAX_SECONDS)


async def recent_failures(session: AsyncSession, email: str) -> int:
    count, _ = await _failures_since(session, _utcnow() - timedelta(minutes=LOCKOUT_WINDOW_MINUTES), email=email)
    return count


async def refusal(session: AsyncSession, email: str, request: Optional[Request], now: Optional[datetime] = None) -> Optional[LoginRefusal]:
    """Why this attempt must be refused before even checking the password, or None."""
    now = now or _utcnow()
    since = now - timedelta(minutes=LOCKOUT_WINDOW_MINUTES)
    ip, _ = _client(request)
    if ip:
        ip_failures, _ = await _failures_since(session, since, ip=ip)
        if ip_failures >= MAX_FAILURES_PER_IP:
            return LoginRefusal(f"Too many failed attempts from this network - try again in {LOCKOUT_WINDOW_MINUTES} minutes", 423)
    failures, latest = await _failures_since(session, since, email=email)
    wait = required_delay_seconds(failures)
    if wait and latest is not None:
        elapsed = (now - latest).total_seconds()
        if elapsed < wait:
            remaining = max(1, int(wait - elapsed + 0.999))
            return LoginRefusal(f"Too many failed attempts for this account - wait {remaining} second(s) before trying again", 429, remaining)
    return None


async def lock_reason(session: AsyncSession, email: str, request: Optional[Request]) -> Optional[str]:
    """Backwards-compatible view of `refusal`: the reason text, or None."""
    verdict = await refusal(session, email, request)
    return verdict.reason if verdict else None


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
