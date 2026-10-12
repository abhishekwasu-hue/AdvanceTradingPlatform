"""N1 E1 (ADR-0014): the intent queue - the `execution_intents` table, written by producers, claimed by the worker.

* `submit` writes one QUEUED row (idempotent per organisation, person and key; a second submit of the same setup for
  the same person returns the first row - another member's submit is that member's own row, review M1). It never dispatches: a producer - an API route included (U4 X1) - only ever writes a row, so no request
  can reach an order pipeline through the core.
* `claim` moves QUEUED rows to CLAIMED with a conditional UPDATE per row (`status = 'QUEUED'`), so two worker replicas
  never take the same row.
* `expire_overdue` closes rows nobody dispatched in time (EXPIRED - a setup is never proposed late). A retried row whose
  `intent:<id>` proposal was already raised is linked to it (DONE) instead (review L1).
* `requeue_stale` puts a CLAIMED row whose worker was interrupted back in the queue (every route is idempotent on
  `intent:<id>`), and FAILS it after `EXECUTION_CORE_MAX_ATTEMPTS` claims.
* `finish` records the outcome, again conditionally (only a CLAIMED row is finished).

Statuses: QUEUED -> CLAIMED -> DONE | REFUSED | FAILED; QUEUED -> EXPIRED; CLAIMED -> QUEUED (stale, retried).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config as app_config
from app.core.models import Signal
from app.db.models import AiActionRecord, ExecutionIntentRecord, User
from app.execution.core import policy

FLAG = "execution_core"
QUEUED, CLAIMED, DONE, REFUSED, FAILED, EXPIRED = "QUEUED", "CLAIMED", "DONE", "REFUSED", "FAILED", "EXPIRED"
FINAL = (DONE, REFUSED, FAILED, EXPIRED)

# Producers the core accepts, with the label a person sees. A new source is added here with its review (U4 X1: scans).
SOURCES: Dict[str, str] = {"scan": "Scan setup"}

# Which organisations the worker drains: None = every organisation (flag on for all), else these ids (the allow-list).
Scope = Optional[FrozenSet[int]]


class CoreDisabled(PermissionError):
    """The `execution_core` flag is off for this organisation: nothing is queued."""


class IntentRejected(ValueError):
    """The intent cannot be queued (unknown source or mode, or the person is not an active member)."""


@dataclass
class Submitted:
    intent: ExecutionIntentRecord
    created: bool


def _count(result: object) -> int:
    """Rows an UPDATE touched (typed loosely: `execute` is annotated as returning a plain Result)."""
    return int(getattr(result, "rowcount", 0) or 0)


def queue_key(source: str, idempotency_key: str) -> str:
    """`<source>:<key>`; a key that would not fit the column is hashed (never cut: two long keys must not collide)."""
    key = f"{source}:{idempotency_key}"
    if len(key) <= 200:
        return key
    return f"{source}:sha256:{hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()}"


def proposal_key(intent_id: int) -> str:
    return f"intent:{intent_id}"


async def existing_proposal(session: AsyncSession, tenant_id: int, intent_id: int) -> Optional[AiActionRecord]:
    """The proposal an earlier (interrupted) attempt of this intent already raised, if any."""
    return await session.scalar(select(AiActionRecord).where(AiActionRecord.tenant_id == tenant_id,
                                                             AiActionRecord.idempotency_key == proposal_key(intent_id)))


def order_key(intent_id: int) -> str:
    """N1 X2: the PAPER order an Auto PAPER intent places carries this idempotency key."""
    return f"intent:{intent_id}"


IN_FLIGHT_ORDER = frozenset({"CREATED", "VALIDATING", "RISK_CHECK", "SUBMITTED", "PENDING", "PARTIAL_FILL"})
PLACED_ORDER = frozenset({"FILLED", "POSITION_OPEN"})


async def existing_order(session: AsyncSession, tenant_id: int, intent_id: int, user_id: Optional[int] = None) -> Optional[Any]:
    """The PAPER order an earlier (interrupted) attempt of this intent already placed, if any (N1 X2). Review of X2 (L1):
    only the intent owner's own PAPER order with that key - another member's order under the same key text is not it."""
    from app.db.models import OrderRecord
    if user_id is None:
        user_id = await session.scalar(select(ExecutionIntentRecord.user_id).where(ExecutionIntentRecord.id == intent_id))
    if user_id is None:
        return None
    return await session.scalar(select(OrderRecord).where(OrderRecord.tenant_id == tenant_id, OrderRecord.user_id == user_id,
                                                          OrderRecord.mode == "PAPER", OrderRecord.idempotency_key == order_key(intent_id)))


async def relink_placed(session: AsyncSession, intent_id: int, *, route: str, reason: str, now: datetime) -> bool:
    """Review of the X2 delta (L1): an EXPIRED / FAILED row whose own attempt did place its PAPER order -> DONE. A
    conditional update on that status (False when the row is anything else); the caller commits."""
    result = await session.execute(update(ExecutionIntentRecord)
                                   .where(ExecutionIntentRecord.id == intent_id, ExecutionIntentRecord.status.in_((EXPIRED, FAILED)))
                                   .values(status=DONE, route=route, reason=(reason or "")[:300], finished_at=_utc(now))
                                   .execution_options(synchronize_session=False))
    return bool(_count(result))


def order_outcome(order: Any, now: datetime) -> Optional[Dict[str, Any]]:
    """The values that finish an intent from its PAPER order (review of X2, L3): DONE when it filled, REFUSED (route
    PAPER) when it was rejected or failed, None while it is still in flight (its own attempt finishes the row)."""
    status = str(getattr(order, "status", "") or "")
    if status in IN_FLIGHT_ORDER:
        return None
    if status in PLACED_ORDER or getattr(order, "trade_id", None) is not None:
        return {"status": DONE, "route": policy.ROUTE_PAPER, "finished_at": now,
                "reason": f"Auto PAPER: PAPER order #{order.id} was placed before an interruption"}
    return {"status": REFUSED, "route": policy.ROUTE_PAPER, "finished_at": now,
            "reason": f"Auto PAPER: PAPER order #{order.id} was not placed ({status.lower() or 'not filled'})"}


async def earlier_outcome(session: AsyncSession, tenant_id: int, intent_id: int, now: datetime) -> Optional[Dict[str, Any]]:
    """What an interrupted earlier attempt already did - a PAPER order (route PAPER) or a proposal (route PROPOSE) - as
    the values that finish the row, or None. An order wins: it is the entry itself (an order still in flight: None)."""
    order = await existing_order(session, tenant_id, intent_id)
    if order is not None:
        out = order_outcome(order, now)
        return None if out is None else {**out, "order": order}
    action = await existing_proposal(session, tenant_id, intent_id)
    if action is not None:
        return {"status": DONE, "route": policy.ROUTE_PROPOSE, "ai_action_id": action.id, "finished_at": now,
                "reason": f"Proposal #{action.id} was raised before an interruption"}
    return None


def _utc(value: Optional[datetime]) -> datetime:
    value = value or datetime.now(timezone.utc)
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


async def enabled(session: AsyncSession, tenant_id: int) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def drain_scope(session: AsyncSession) -> Tuple[bool, Scope]:
    """(anything to drain?, scope) from one read of the flags: on for all -> every organisation; else the allow-list."""
    from app.platform.controls import feature_flags
    entry = (await feature_flags(session)).get(FLAG) or {}
    if entry.get("on"):
        return True, None
    tenants = frozenset(int(t) for t in entry.get("tenants") or ())
    return bool(tenants), tenants


def _in_scope(query, scope: Scope):
    return query if scope is None else query.where(ExecutionIntentRecord.tenant_id.in_(sorted(scope)))


async def submit(session: AsyncSession, *, tenant_id: int, user_id: int, source: str, source_ref: str, idempotency_key: str,
                 mode: str, signal: Signal, exchange: str = "NSE", evidence: Optional[Dict] = None,
                 now: Optional[datetime] = None) -> Submitted:
    """Queues one intent for `user_id` (who sees the notification or decides the proposal). Flushes, never commits -
    the producer commits with its own work. Raises CoreDisabled (flag off) or IntentRejected."""
    if not await enabled(session, tenant_id):
        raise CoreDisabled(f"The '{FLAG}' feature is off for this organisation")
    if source not in SOURCES:
        raise IntentRejected(f"Unknown intent source '{source}'")
    mode = (mode or "").strip().upper()
    if mode not in policy.MODES:
        raise IntentRejected(f"Unknown mode '{mode}' - one of {', '.join(policy.MODES)}")
    # BT1b: an intent is always for a member of the same organisation - never a person elsewhere.
    user = await session.get(User, user_id)
    if user is None or user.tenant_id != tenant_id or not user.is_active:
        raise IntentRejected("The intent's person is not an active member of this organisation")
    now = _utc(now)
    key = queue_key(source, idempotency_key)
    existing = await _by_key(session, tenant_id, user_id, key)
    if existing is not None:
        return Submitted(existing, False)
    direction = signal.direction.value if hasattr(signal.direction, "value") else str(signal.direction)
    row = ExecutionIntentRecord(
        tenant_id=tenant_id, user_id=user_id, source=source, source_ref=source_ref[:120], idempotency_key=key,
        requested_mode=mode, status=QUEUED, symbol=signal.symbol[:40], exchange=(exchange or "NSE").upper()[:12],
        direction=direction[:8], strategy_id=(signal.strategy_id or source)[:80], signal_json=signal.model_dump_json(),
        evidence_json=json.dumps(evidence or {}, default=str), attempts=0,
        expires_at=now + timedelta(seconds=app_config.EXECUTION_INTENT_TTL_SECONDS), created_at=now)
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:                     # a concurrent submit of the same key won
        existing = await _by_key(session, tenant_id, user_id, key)
        if existing is None:
            raise
        return Submitted(existing, False)
    return Submitted(row, True)


async def _by_key(session: AsyncSession, tenant_id: int, user_id: int, key: str) -> Optional[ExecutionIntentRecord]:
    return await session.scalar(select(ExecutionIntentRecord).where(ExecutionIntentRecord.tenant_id == tenant_id,
                                                                     ExecutionIntentRecord.user_id == user_id,
                                                                     ExecutionIntentRecord.idempotency_key == key))


async def expire_overdue(session: AsyncSession, scope: Scope, now: datetime) -> int:
    now = _utc(now)
    # A retried row (attempts > 0) may already have its proposal: link it (DONE), never call it "nothing was raised".
    retried = (await session.execute(_in_scope(select(ExecutionIntentRecord.id, ExecutionIntentRecord.tenant_id)
                                               .where(ExecutionIntentRecord.status == QUEUED, ExecutionIntentRecord.expires_at <= now,
                                                      ExecutionIntentRecord.attempts > 0), scope))).all()
    for intent_id, tenant_id in retried:
        earlier = await earlier_outcome(session, tenant_id, intent_id, now)    # N1 X2 review M1: an order is linked too
        if earlier is not None:
            order = earlier.pop("order", None)
            result = await session.execute(update(ExecutionIntentRecord)
                                           .where(ExecutionIntentRecord.id == intent_id, ExecutionIntentRecord.status == QUEUED)
                                           .values(**earlier).execution_options(synchronize_session=False))
            if order is not None and earlier["status"] == DONE and _count(result):
                from app.execution.core import announce
                await announce.placed_once(session, intent_id, order)
    query = update(ExecutionIntentRecord).where(ExecutionIntentRecord.status == QUEUED, ExecutionIntentRecord.expires_at <= now)
    if scope is not None:
        query = query.where(ExecutionIntentRecord.tenant_id.in_(sorted(scope)))
    result = await session.execute(query.values(status=EXPIRED, finished_at=now, reason="Not dispatched in time - nothing was raised")
                                   .execution_options(synchronize_session=False))
    await session.commit()
    return _count(result)


async def requeue_stale(session: AsyncSession, scope: Scope, now: datetime, limit: int = 500) -> Tuple[int, int]:
    """(requeued, failed): CLAIMED rows older than EXECUTION_CORE_CLAIM_STALE_SECONDS were interrupted (at most
    `limit` per call; the rest next time)."""
    now = _utc(now)
    cutoff = now - timedelta(seconds=app_config.EXECUTION_CORE_CLAIM_STALE_SECONDS)
    rows = (await session.execute(_in_scope(select(ExecutionIntentRecord.id, ExecutionIntentRecord.tenant_id, ExecutionIntentRecord.attempts,
                                                   ExecutionIntentRecord.claimed_at)
                                            .where(ExecutionIntentRecord.status == CLAIMED, ExecutionIntentRecord.claimed_at <= cutoff), scope)
                                  .order_by(ExecutionIntentRecord.id).limit(max(1, int(limit))))).all()
    requeued = failed = 0
    for intent_id, tenant_id, attempts, claimed_at in rows:
        values: Dict[str, object]
        linked_order: Any = None
        if int(attempts or 0) >= app_config.EXECUTION_CORE_MAX_ATTEMPTS:
            earlier = await earlier_outcome(session, tenant_id, intent_id, now)
            linked_order = earlier.pop("order", None) if earlier is not None else None
            if earlier is not None:             # review L1 (and X2 M1): its proposal or order exists - link it, not failed
                values = dict(earlier)
            else:
                values = {"status": FAILED, "finished_at": now, "reason": f"Interrupted {int(attempts)} times - nothing more is tried"}
        else:
            values = {"status": QUEUED, "claimed_at": None}
        result = await session.execute(update(ExecutionIntentRecord)
                                       .where(ExecutionIntentRecord.id == intent_id, ExecutionIntentRecord.status == CLAIMED,
                                              ExecutionIntentRecord.claimed_at == claimed_at)
                                       .values(**values).execution_options(synchronize_session=False))
        if _count(result):
            if values["status"] == DONE and linked_order is not None:
                from app.execution.core import announce
                await announce.placed_once(session, intent_id, linked_order)
            if values["status"] == FAILED:
                failed += 1
            elif values["status"] == QUEUED:
                requeued += 1
    await session.commit()
    return requeued, failed


async def claim(session: AsyncSession, scope: Scope, now: datetime, limit: int) -> List[ExecutionIntentRecord]:
    """Takes up to `limit` QUEUED rows, oldest first; a row another replica took in between is skipped."""
    now = _utc(now)
    ids = list(await session.scalars(_in_scope(select(ExecutionIntentRecord.id).where(ExecutionIntentRecord.status == QUEUED,
                                                                                       ExecutionIntentRecord.expires_at > now), scope)
                                     .order_by(ExecutionIntentRecord.id).limit(max(1, int(limit)))))
    taken: List[int] = []
    for intent_id in ids:
        result = await session.execute(update(ExecutionIntentRecord)
                                       .where(ExecutionIntentRecord.id == intent_id, ExecutionIntentRecord.status == QUEUED)
                                       .values(status=CLAIMED, claimed_at=now, attempts=ExecutionIntentRecord.attempts + 1)
                                       .execution_options(synchronize_session=False))
        if _count(result):
            taken.append(intent_id)
    await session.commit()
    if not taken:
        return []
    return list(await session.scalars(select(ExecutionIntentRecord).where(ExecutionIntentRecord.id.in_(taken))
                                       .order_by(ExecutionIntentRecord.id).execution_options(populate_existing=True)))


async def finish(session: AsyncSession, intent_id: int, status: str, *, route: Optional[str], reason: str, now: datetime,
                 ai_action_id: Optional[int] = None, notification_id: Optional[int] = None, commit: bool = True) -> bool:
    """Records the outcome of a CLAIMED row (False when it is no longer CLAIMED - another replica or the stale sweep
    settled it). `commit=False` leaves the change in the transaction for the caller's next commit."""
    if status not in FINAL:
        raise ValueError(f"'{status}' is not a final status")
    values: Dict[str, object] = {"status": status, "route": route, "reason": (reason or "")[:300], "finished_at": _utc(now)}
    if ai_action_id is not None:
        values["ai_action_id"] = ai_action_id
    if notification_id is not None:
        values["notification_id"] = notification_id
    result = await session.execute(update(ExecutionIntentRecord)
                                   .where(ExecutionIntentRecord.id == intent_id, ExecutionIntentRecord.status == CLAIMED)
                                   .values(**values).execution_options(synchronize_session=False))
    if commit:
        await session.commit()
    return bool(_count(result))
