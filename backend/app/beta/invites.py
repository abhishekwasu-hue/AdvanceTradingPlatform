"""PW6: closed-beta invite codes and the cohort cap.

With the `beta_cohort_gate` flag on (it is off by default), `POST /api/auth/register` calls `redeem()`: the sign-up
needs a valid invite code, spends one of its uses, and is refused once the cohort is full. With the flag off nothing
here runs and registration is exactly as before.

* A code is 16 characters from an alphabet without look-alikes (no 0/O, 1/I/L), shown as `XXXX-XXXX-XXXX-XXXX`
  (80 bits). Dashes, spaces and case do not matter when it is typed in. Only the SHA-256 of the normalised code is
  stored; the plain code is returned once, by the create call.
* The cohort size is the sum of `used_count` over every code, revoked or expired ones included: the number of
  accounts created through the gate. Accounts that existed before the gate went on, and teammates who join an
  organisation through an owner's invite (a different endpoint), are not counted.
* Z10-e staged rollout: the cohort grows in stages (`BETA_COHORT_STAGES`, 50 -> 200 -> 500). The gate stops at the
  effective cap, min(stage, `BETA_COHORT_CAP`). The SUPER_ADMIN moves the stage; it is stored in `platform_controls`
  (key `beta_cohort_stage`), and every move is written to the audit log. Moving up goes one stage at a time and needs a
  reason; moving down may skip stages and never touches existing accounts (it only closes sign-up sooner). Until a
  stage is stored, the `BETA_COHORT_STAGE` setting (default 50) applies.
* On Postgres the redemption takes a transaction-scoped advisory lock, so two sign-ups at the same moment cannot both
  take the last place (SQLite serialises writers by itself). The per-code use is also a conditional UPDATE, so a code
  is never spent past its `max_uses`.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.core import config as app_config
from app.db.models import BetaInviteCodeRecord, PlatformControlRecord, User

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
    """The hard ceiling from the environment (`BETA_COHORT_CAP`); the gate enforces `effective_cap`."""
    return int(app_config.BETA_COHORT_CAP)


async def cohort_size(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.coalesce(func.sum(BetaInviteCodeRecord.used_count), 0))) or 0)


# --- Z10-e: staged rollout --------------------------------------------------------------------------------------------

KEY_STAGE = "beta_cohort_stage"


def stages() -> Tuple[int, ...]:
    return tuple(int(s) for s in app_config.BETA_COHORT_STAGES)


async def _stage_row(session: AsyncSession) -> Optional[PlatformControlRecord]:
    return await session.get(PlatformControlRecord, KEY_STAGE)


def _stored_stage(row: Optional[PlatformControlRecord]) -> Optional[int]:
    if row is None:
        return None
    try:
        value = json.loads(row.value_json or "{}").get("stage")
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, int) and value in stages() else None


async def current_stage(session: AsyncSession) -> int:
    """The stage the admin set, or the `BETA_COHORT_STAGE` setting while none is stored (or a stored one is unreadable)."""
    stored = _stored_stage(await _stage_row(session))
    return stored if stored is not None else int(app_config.BETA_COHORT_STAGE)


async def effective_cap(session: AsyncSession) -> int:
    """What the gate enforces: min(stage, BETA_COHORT_CAP)."""
    return min(await current_stage(session), cohort_cap())


async def cohort_view(session: AsyncSession, *, gate_on: bool) -> Dict[str, Any]:
    """The admin's view: the stage, the caps, the cohort size and the headroom left before sign-up stops."""
    row = await _stage_row(session)
    stored = _stored_stage(row)
    stage = stored if stored is not None else int(app_config.BETA_COHORT_STAGE)
    env_cap = cohort_cap()
    cap = min(stage, env_cap)
    used = await cohort_size(session)
    higher = [s for s in stages() if s > stage]
    return {"gate_on": gate_on, "cap": cap, "used": used, "remaining": max(0, cap - used),
            "stage": stage, "stages": list(stages()), "next_stage": higher[0] if higher else None, "env_cap": env_cap,
            "capped_by_env": env_cap < stage, "headroom": max(0, cap - used),
            "stage_source": "admin" if stored is not None else "default",
            "stage_set_by": row.updated_by if row is not None and stored is not None else None,
            "stage_set_at": _utc(row.updated_at).isoformat() if row is not None and stored is not None and row.updated_at else None}


class StageChangeError(ValueError):
    """A stage move the rules refuse (unknown stage, skipping a stage on the way up, no reason)."""


async def set_stage(session: AsyncSession, admin: User, stage: int, reason: Optional[str]) -> Tuple[int, int]:
    """Moves the cohort to `stage` and writes the audit row. Returns (previous stage, new stage). Does not commit.

    Up: exactly one stage at a time (50 -> 200 -> 500), with a reason. Down: any lower stage; accounts already created
    are never touched - sign-up just stops sooner. Setting the current stage again changes nothing and writes nothing.
    On Postgres the move takes the cohort advisory lock; two first-ever moves racing on SQLite (or any database without
    it) can still collide on the new row, which raises IntegrityError - the route answers 409."""
    allowed = stages()
    if stage not in allowed:
        raise StageChangeError(f"stage must be one of {list(allowed)}")
    await _serialise(session)                       # the same lock as redemptions: no sign-up between read and move
    previous = await current_stage(session)
    if stage == previous:
        return previous, stage
    note = (reason or "").strip()[:200]
    if stage > previous:
        higher = [s for s in allowed if s > previous]
        if not higher or stage != higher[0]:
            raise StageChangeError(f"move up one stage at a time: the next stage after {previous} is {higher[0] if higher else 'none'}")
        if len(note) < 3:
            raise StageChangeError("a reason (at least 3 characters) is required to move up a stage")
    row = await _stage_row(session)
    if row is None:
        row = PlatformControlRecord(key=KEY_STAGE)
        session.add(row)
    row.value_json = json.dumps({"stage": stage})
    row.updated_by = admin.id
    used, env_cap = await cohort_size(session), cohort_cap()
    event = "beta_cohort_stage_raised" if stage > previous else "beta_cohort_stage_lowered"
    await write_audit_log(session, None, admin.id, event,
                          f"{previous} -> {stage} (effective cap {min(stage, env_cap)}, BETA_COHORT_CAP {env_cap}, used {used})"
                          + (f": {note}" if note else ""))
    await session.flush()
    return previous, stage


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
    if await cohort_size(session) >= await effective_cap(session):
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
