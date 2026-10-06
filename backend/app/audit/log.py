"""Tamper-evident audit trail (master prompt Section 48: "tamper-evident hash-chained audit
logs"). `AuditLogRecord` rows form a single hash chain across the whole table, in `id` order:
each row's `hash` covers its own fields plus the previous row's `hash`, so altering, deleting, or
inserting a row out of band breaks every hash from that point forward - a check anyone with the
data (not just this running process) can independently redo.

This is the only place `AuditLogRecord` should be constructed; every audit-log write in the
codebase must go through `write_audit_log` so the chain has no gaps.

P0.3 / S7: on Postgres the append takes a transaction-scoped advisory lock first, so two writers can never
chain off the same `prev_hash` (SQLite serialises writers by itself). `record_anchor` stores the chain head
once a day in `audit_anchors`; `verify_audit_chain(since_anchor=True)` then re-checks only the rows after the
last anchor against the head it recorded, which keeps the monthly and backup checks cheap and lets a
tampered-then-rewritten prefix be caught by comparing anchors with an off-site copy. The foreign keys on
`audit_logs` are RESTRICT (migration a2b4c6d8e0f2): a user or tenant with audit rows cannot be deleted, because
SET NULL would rewrite hashed fields and break the chain.
"""
import hashlib
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditAnchorRecord, AuditLogRecord

GENESIS_HASH = "GENESIS"
# One lock key for the whole table: appends are rare and short, so a global serialisation costs nothing.
AUDIT_LOCK_KEY = 7349261


async def _serialise_appends(session: AsyncSession) -> None:
    """Postgres: hold the audit append lock until this transaction ends. Other dialects: no-op."""
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": AUDIT_LOCK_KEY})


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
    await _serialise_appends(session)
    last = await session.scalar(select(AuditLogRecord).order_by(AuditLogRecord.id.desc()).limit(1))
    prev_hash = last.hash if last is not None else GENESIS_HASH
    created_at = datetime.now(timezone.utc)

    record = AuditLogRecord(
        tenant_id=tenant_id, user_id=user_id, event=event, detail=detail, created_at=created_at,
        prev_hash=prev_hash, hash=_compute_hash(prev_hash, tenant_id, user_id, event, detail, created_at),
    )
    session.add(record)
    return record


async def latest_anchor(session: AsyncSession) -> Optional[AuditAnchorRecord]:
    return await session.scalar(select(AuditAnchorRecord).order_by(AuditAnchorRecord.id.desc()).limit(1))


async def record_anchor(session: AsyncSession, now: Optional[datetime] = None) -> Optional[AuditAnchorRecord]:
    """Stores the current chain head (last row id + hash). Returns None when the chain has not grown since the
    last anchor. Does not commit."""
    last = await session.scalar(select(AuditLogRecord).order_by(AuditLogRecord.id.desc()).limit(1))
    if last is None:
        return None
    previous = await latest_anchor(session)
    if previous is not None and previous.last_id == last.id and previous.head_hash == last.hash:
        return None
    anchor = AuditAnchorRecord(last_id=last.id, head_hash=last.hash, anchored_at=now or datetime.now(timezone.utc))
    session.add(anchor)
    await session.flush()
    return anchor


async def verify_audit_chain(session: AsyncSession, *, since_anchor: bool = False) -> Tuple[bool, Optional[int]]:
    """Recomputes every row's hash from its own fields and expected `prev_hash`, in `id` order.
    Returns (True, None) if the whole chain is intact, or (False, <first broken row's id>) at the
    first mismatch - either this row's stored hash doesn't match its own fields, or its
    `prev_hash` doesn't match the previous row's actual hash.

    `since_anchor=True` starts at the latest anchor: the anchored row must still carry the anchored hash and
    recompute to it from its own fields (else that row id is reported), and the rows from it onwards are checked.
    """
    expected_prev = GENESIS_HASH
    query = select(AuditLogRecord).order_by(AuditLogRecord.id.asc())
    if since_anchor:
        anchor = await latest_anchor(session)
        if anchor is not None:
            anchored = await session.get(AuditLogRecord, anchor.last_id)
            if anchored is None or anchored.hash != anchor.head_hash:
                return False, anchor.last_id
            expected_prev = anchored.prev_hash
            query = query.where(AuditLogRecord.id >= anchor.last_id)
    rows = list(await session.scalars(query))
    for row in rows:
        if row.prev_hash != expected_prev:
            return False, row.id
        recomputed = _compute_hash(row.prev_hash, row.tenant_id, row.user_id, row.event, row.detail, row.created_at)
        if recomputed != row.hash:
            return False, row.id
        expected_prev = row.hash
    return True, None
