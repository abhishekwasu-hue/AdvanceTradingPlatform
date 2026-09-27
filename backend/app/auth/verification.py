"""Phase N3: email verification.

* Registration (and an OWNER's invite acceptance) leaves `users.email_verified_at` empty and
  issues a 24-hour single-use token whose SHA-256 is stored; the raw token travels in the link.
* `POST /api/auth/verify-email/{token}` stamps the user verified; `POST /api/auth/verify-email/resend`
  issues a fresh link (rate limited); a platform admin can stamp a user directly.
* `ensure_verified(user, why)` is the gate LIVE deployments and broker credential storage call
  when EMAIL_VERIFICATION_REQUIRED is on. Exits are never gated.

Links go out through the platform mailer (app/notifications/mailer.py); without one they are
logged, and the resend endpoint says so, so the operator knows to configure PLATFORM_SMTP_*.
"""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import EmailVerificationRecord, User
from app.notifications import mailer

logger = logging.getLogger(__name__)

VERIFY_TTL_HOURS = 24


class EmailVerificationRequired(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail, headers={"X-Step-Up": "email"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def required() -> bool:
    return config.EMAIL_VERIFICATION_REQUIRED


def ensure_verified(user: User, why: str) -> None:
    if required() and user.email_verified_at is None:
        raise EmailVerificationRequired(f"{why} requires a verified email address - check your inbox or resend the link from the Account tab")


def verify_link(request: Optional[Request], token: str) -> str:
    base = config.FRONTEND_URL if config.FRONTEND_URL.startswith("http") else (str(request.base_url).rstrip("/") + "/" if request else "/")
    return f"{base}?verify={token}"


async def issue(session: AsyncSession, user: User) -> Tuple[str, EmailVerificationRecord]:
    token = secrets.token_urlsafe(32)
    record = EmailVerificationRecord(user_id=user.id, token_hash=_hash(token), expires_at=_utcnow() + timedelta(hours=VERIFY_TTL_HOURS))
    session.add(record)
    await session.flush()
    return token, record


async def send_link(session: AsyncSession, user: User, request: Optional[Request]) -> dict:
    """Issues a token and mails the link. Returns what the UI needs to say."""
    token, _ = await issue(session, user)
    link = verify_link(request, token)
    sent = await mailer.send(
        user.email, "Verify your Advance Trading Platform email",
        f"Welcome to Advance Trading Platform.\n\nConfirm that {user.email} is yours by opening this link within "
        f"{VERIFY_TTL_HOURS} hours:\n{link}\n\nIf you did not create this account, ignore this email.",
    )
    if not sent:
        logger.info("Email verification link for %s (no platform mailer configured): %s", user.email, link)
    return {"sent": sent, "mailer_configured": mailer.configured(), "expires_in_hours": VERIFY_TTL_HOURS}


async def verify(session: AsyncSession, token: str) -> User:
    record = await session.scalar(select(EmailVerificationRecord).where(EmailVerificationRecord.token_hash == _hash(token)))
    if record is None:
        raise HTTPException(status_code=400, detail="This verification link is invalid")
    if record.used_at is not None:
        raise HTTPException(status_code=400, detail="This verification link was already used")
    if _as_utc(record.expires_at) < _utcnow():
        raise HTTPException(status_code=400, detail="This verification link has expired - request a new one")
    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail="This account is no longer active")
    record.used_at = _utcnow()
    if user.email_verified_at is None:
        user.email_verified_at = _utcnow()
    return user


def mark_verified(user: User) -> None:
    if user.email_verified_at is None:
        user.email_verified_at = _utcnow()
