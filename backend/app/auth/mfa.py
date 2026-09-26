"""TOTP multi-factor authentication (Phase C3).

Standard RFC 6238 TOTP (Google Authenticator, Authy, 1Password, ...) via pyotp. The secret is
Fernet-encrypted at rest like broker credentials; backup codes are stored as SHA-256 hashes and
shown exactly once. Login becomes two steps when MFA is on: password -> a five-minute `mfa`
challenge token -> TOTP or backup code -> session. A session that completed (or later passed) a
TOTP check is marked `mfa_verified_at`, which is what the step-up gate for LIVE trading, broker
credentials and the admin console looks at.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import jwt
import pyotp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.db.models import MfaBackupCodeRecord, User
from app.secrets_store.encryption import decrypt_text, encrypt_text

ISSUER = "Advance Trading Platform"
MFA_TOKEN_TTL_MINUTES = 5
BACKUP_CODE_COUNT = 8
TOTP_VALID_WINDOW = 1  # accept the previous/next 30s step for clock drift


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def store_secret(user: User, secret: str) -> None:
    user.mfa_secret_encrypted = encrypt_text(secret)


def load_secret(user: User) -> Optional[str]:
    if not user.mfa_secret_encrypted:
        return None
    return decrypt_text(user.mfa_secret_encrypted)


def verify_totp(secret: str, code: str) -> bool:
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=TOTP_VALID_WINDOW)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().replace("-", "").encode()).hexdigest()


async def issue_backup_codes(session: AsyncSession, user: User) -> List[str]:
    """Replaces every existing backup code with a fresh set; returns the plaintext codes once."""
    existing = await session.scalars(select(MfaBackupCodeRecord).where(MfaBackupCodeRecord.user_id == user.id))
    for record in existing:
        await session.delete(record)
    codes: List[str] = []
    for _ in range(BACKUP_CODE_COUNT):
        raw = secrets.token_hex(5)
        code = f"{raw[:5]}-{raw[5:]}"
        codes.append(code)
        session.add(MfaBackupCodeRecord(user_id=user.id, code_hash=_hash_code(code)))
    await session.flush()
    return codes


async def backup_codes_remaining(session: AsyncSession, user_id: int) -> int:
    rows = await session.scalars(
        select(MfaBackupCodeRecord.id).where(MfaBackupCodeRecord.user_id == user_id, MfaBackupCodeRecord.used_at.is_(None))
    )
    return len(list(rows))


async def consume_backup_code(session: AsyncSession, user: User, code: str) -> bool:
    record = await session.scalar(
        select(MfaBackupCodeRecord).where(
            MfaBackupCodeRecord.user_id == user.id, MfaBackupCodeRecord.code_hash == _hash_code(code),
            MfaBackupCodeRecord.used_at.is_(None),
        )
    )
    if record is None:
        return False
    record.used_at = _utcnow()
    return True


async def verify_code(session: AsyncSession, user: User, code: str) -> Tuple[bool, str]:
    """TOTP first, then a backup code. Returns (ok, method)."""
    secret = load_secret(user)
    if secret and verify_totp(secret, code):
        return True, "totp"
    if await consume_backup_code(session, user, code):
        return True, "backup_code"
    return False, "none"


# --- the login challenge token ------------------------------------------------------------------

def create_mfa_token(user_id: int) -> str:
    now = _utcnow()
    return jwt.encode(
        {"sub": str(user_id), "purpose": "mfa", "iat": now, "exp": now + timedelta(minutes=MFA_TOKEN_TTL_MINUTES)},
        JWT_SECRET_KEY, algorithm=JWT_ALGORITHM,
    )


def parse_mfa_token(token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("purpose") != "mfa":
        return None
    return int(payload["sub"])
