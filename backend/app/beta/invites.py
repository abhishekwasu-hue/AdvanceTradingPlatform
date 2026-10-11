"""PW6: closed-beta invite codes and the cohort cap.

With the `beta_cohort_gate` flag on (it is off by default), `POST /api/auth/register` calls `redeem()`: the sign-up
needs a valid invite code, spends one of its uses, and is refused once the cohort is full. With the flag off nothing
here runs and registration is exactly as before.

* A code is 16 characters from an alphabet without look-alikes (no 0/O, 1/I/L), shown as `XXXX-XXXX-XXXX-XXXX`
  (80 bits). Dashes, spaces and case do not matter when it is typed in. Only the SHA-256 of the normalised code is
  stored; the plain code is returned once, by the create call.
* The cohort size is the sum of `used_count` over every code, revoked or expired ones included: the number of
  accounts created through the gate. Accounts that existed before the gate went on, and teammates who join an
  organisation through an owner's invite (a different endpoint), are not counted. The cap is `BETA_COHORT_CAP`.
* On Postgres the redemption takes a transaction-scoped advisory lock, so two sign-ups at the same moment cannot both
  take the last place (SQLite serialises writers by itself). The per-code use is also a conditional UPDATE, so a code
  is never spent past its `max_uses`.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config as app_config
from app.db.models import BetaInviteCodeRecord

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"            # 31 symbols, no 0/O, 1/I/L
CODE_LENGTH = 16
_STRIP = re.compile(r"[\s-]+")
_VALID = re.compile(rf"^[{ALPHABET}]{{{CODE_LENGTH}}}$")
# One lock key for every redemption: sign-ups are rare, so serialising them costs nothing.
COHORT_LOCK_KEY = 7349262


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalise(code: Optional[str]) -> str:
    return _STRIP.sub("", code or "").upper()


def hash_code(code: str) -> str:
    return hashlib.sha256(normalise(code).encode()).hexdigest()


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    return "-".join(raw[i:i + 4] for i in range(0, CODE_LENGTH, 4))


def cohort_cap() -> int:
    return int(app_config.BETA_COHORT_CAP)


async def cohort_size(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.coalesce(func.sum(BetaInviteCodeRecord.used_count), 0))) or 0)


def _refuse(code: str, message: str) -> HTTPException:
    """403 with a machine-readable code (the sign-up form shows the invite field on `beta_invite_*`)."""
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": code, "message": message})


async def create_code(session: AsyncSession, *, created_by: Optional[int], max_uses: int, expires_at: Optional[datetime],
                      note: Optional[str]) -> Tuple[BetaInviteCodeRecord, str]:
    """A new code. Returns (the row, the plain code) - the plain code is never stored and cannot be shown again."""
    for _ in range(5):                                                      # a hash collision is astronomically unlikely
        code = new_code()
        if await session.scalar(select(BetaInviteCodeRecord.id).where(BetaInviteCodeRecord.code_hash == hash_code(code))) is None:
            break
    row = BetaInviteCodeRecord(code_hash=hash_code(code), note=(note or "").strip()[:200] or None, max_uses=max_uses, used_count=0,
                               expires_at=_utc(expires_at) if expires_at else None, created_by=created_by)
    session.add(row)
    await session.flush()
    return row, code


def code_state(row: BetaInviteCodeRecord, now: Optional[datetime] = None) -> str:
    now = _utc(now or datetime.now(timezone.utc))
    if row.revoked_at is not None:
        return "revoked"
    if row.expires_at is not None and _utc(row.expires_at) <= now:
        return "expired"
    if row.used_count >= row.max_uses:
        return "exhausted"
    return "active"


def describe(row: BetaInviteCodeRecord, now: Optional[datetime] = None) -> Dict[str, object]:
    """The admin view of a code: never the code itself, nor its hash."""
    return {"id": row.id, "note": row.note, "max_uses": row.max_uses, "used_count": row.used_count,
            "expires_at": _utc(row.expires_at).isoformat() if row.expires_at else None,
            "revoked_at": _utc(row.revoked_at).isoformat() if row.revoked_at else None,
            "created_by": row.created_by, "created_at": _utc(row.created_at).isoformat() if row.created_at else None,
            "state": code_state(row, now)}


async def _serialise(session: AsyncSession) -> None:
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": COHORT_LOCK_KEY})


async def redeem(session: AsyncSession, code: Optional[str], now: Optional[datetime] = None) -> BetaInviteCodeRecord:
    """Spends one use of `code` for a sign-up, or raises 403 saying why not. Does not commit: the caller commits it
    together with the new account, so a sign-up that fails later (in the same transaction) does not spend the code."""
    now = _utc(now or datetime.now(timezone.utc))
    clean = normalise(code)
    if not clean:
        raise _refuse("beta_invite_required", "The beta is invite-only: enter your invite code to sign up.")
    if not _VALID.fullmatch(clean):
        raise _refuse("beta_invite_invalid", "This invite code is not valid. Check it and try again.")
    await _serialise(session)
    row = await session.scalar(select(BetaInviteCodeRecord).where(BetaInviteCodeRecord.code_hash == hash_code(clean)))
    if row is None:
        raise _refuse("beta_invite_invalid", "This invite code is not valid. Check it and try again.")
    state = code_state(row, now)
    if state == "revoked":
        raise _refuse("beta_invite_revoked", "This invite code has been withdrawn.")
    if state == "expired":
        raise _refuse("beta_invite_expired", "This invite code has expired.")
    if state == "exhausted":
        raise _refuse("beta_invite_used", "This invite code has already been used.")
    if await cohort_size(session) >= cohort_cap():
        raise _refuse("beta_cohort_full", "The beta is full right now. We will let you know when places open up.")
    spent = await session.execute(update(BetaInviteCodeRecord).where(
        BetaInviteCodeRecord.id == row.id, BetaInviteCodeRecord.revoked_at.is_(None),
        BetaInviteCodeRecord.used_count < BetaInviteCodeRecord.max_uses,
    ).values(used_count=BetaInviteCodeRecord.used_count + 1).execution_options(synchronize_session=False))
    if getattr(spent, "rowcount", 0) != 1:
        raise _refuse("beta_invite_used", "This invite code has already been used.")
    await session.refresh(row)
    return row


async def revoke(session: AsyncSession, code_id: int, now: Optional[datetime] = None) -> Optional[BetaInviteCodeRecord]:
    row = await session.get(BetaInviteCodeRecord, code_id)
    if row is None:
        return None
    if row.revoked_at is None:
        row.revoked_at = _utc(now or datetime.now(timezone.utc))
    await session.flush()
    return row
