"""Tamper-evident audit trail (master prompt Section 48: "tamper-evident hash-chained audit
logs"). `AuditLogRecord` rows form a single hash chain across the whole table, in `id` order:
each row's `hash` covers its own fields plus the previous row's `hash`, so altering, deleting, or
inserting a row out of band breaks every hash from that point forward - a check anyone with the
data (not just this running process) can independently redo.

This is the only place `AuditLogRecord` should be constructed; every audit-log write in the
codebase must go through `write_audit_log` so the chain has no gaps.

Known v1 limitation, honestly noted rather than hidden: this reads the current last row and
computes the next hash without a DB-level lock, so two audit-log writes committing at the exact
same instant on a real concurrent-writer Postgres deployment could both chain off the same
`prev_hash` (a fork, not corruption - each is still individually valid, but `verify_audit_chain`
would need to tolerate it or a production deployment would need a serializing lock, e.g. a
Postgres advisory lock on the audit_logs table, around this function). No test in this repo has
found this to actually occur, since every write here happens inside the same request's existing
transaction and tests run single-threaded, but it is called out here as future work rather than
implicitly assumed safe.
"""
import hashlib
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLogRecord

GENESIS_HASH = "GENESIS"


def _normalize_timestamp(created_at: datetime) -> str:
    """SQLite's `DateTime(timezone=True)` does not reliably round-trip tzinfo - the exact same
    row can come back tz-aware or naive depending on whether it's read within the same
    transaction it was inserted in or after a fresh commit (observed directly: two audit-log
    writes followed by a chain verification computed a different hash for the first row purely
    because its `created_at` lost its "+00:00" suffix on the intermediate read, even though the
    wall-clock value was unchanged). Always writing (and hashing) datetimes as UTC and stripping
    tzinfo before formatting makes the hash stable regardless of which way a given read happens
    to come back - every `created_at` in this codebase is already UTC (`_utcnow`/`datetime.now
    (timezone.utc)`), so this never changes the wall-clock value, only its string form.
    """
    if created_at.tzinfo is not None:
        created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
    return created_at.isoformat()


def _compute_hash(
    prev_hash: str, tenant_id: Optional[int], user_id: Optional[int], event: str, detail: str, created_at: datetime,
) -> str:
    payload = "|".join([
        prev_hash, str(tenant_id), str(user_id), event, detail, _normalize_timestamp(created_at),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def write_audit_log(
    session: AsyncSession, tenant_id: Optional[int], user_id: Optional[int], event: str, detail: str = "",
) -> AuditLogRecord:
    """Appends one hash-chained row. Does not commit - callers already commit as part of their
    own request's transaction, same as every previous direct `session.add(AuditLogRecord(...))`
    call site this replaces.
    """
    last = await session.scalar(select(AuditLogRecord).order_by(AuditLogRecord.id.desc()).limit(1))
    prev_hash = last.hash if last is not None else GENESIS_HASH
    created_at = datetime.now(timezone.utc)

    record = AuditLogRecord(
        tenant_id=tenant_id, user_id=user_id, event=event, detail=detail, created_at=created_at,
        prev_hash=prev_hash, hash=_compute_hash(prev_hash, tenant_id, user_id, event, detail, created_at),
    )
    session.add(record)
    return record


async def verify_audit_chain(session: AsyncSession) -> Tuple[bool, Optional[int]]:
    """Recomputes every row's hash from its own fields and expected `prev_hash`, in `id` order.
    Returns (True, None) if the whole chain is intact, or (False, <first broken row's id>) at the
    first mismatch - either this row's stored hash doesn't match its own fields, or its
    `prev_hash` doesn't match the previous row's actual hash.
    """
    rows = list(await session.scalars(select(AuditLogRecord).order_by(AuditLogRecord.id.asc())))
    expected_prev = GENESIS_HASH
    for row in rows:
        if row.prev_hash != expected_prev:
            return False, row.id
        recomputed = _compute_hash(row.prev_hash, row.tenant_id, row.user_id, row.event, row.detail, row.created_at)
        if recomputed != row.hash:
            return False, row.id
        expected_prev = row.hash
    return True, None
