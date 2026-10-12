"""N1 E1 (ADR-0014): the dispatcher - the trading worker drains the intent queue every cycle, market open or not.

For each claimed intent the facts are read fresh for its organisation (approval-only mode, the Phase 0 PAPER-only lock,
the owner still an active member), `policy.decide` picks the route, and:

* NOTIFY  -> one notification to the owner (`notifications.notify`), in the same transaction as the row's DONE;
* PROPOSE -> one Z3 ENTRY proposal for the owner (`approval.propose_entry`, key `intent:<id>`: a retried intent finds
  its proposal instead of raising a second one). Vision (BYOK-V2), Telegram buttons and push ride on the proposal as
  for any other. Approve places a PAPER order through the existing approval path - never from here;
* REFUSE  -> nothing; the reason is on the row;
* PAPER   -> refused until U4 X2 (Auto PAPER) - this module never calls an order pipeline.

Fail-closed: any error on an intent marks it FAILED with the error's type and moves on; the drain never raises into
the worker cycle. Exits never come here (ADR-0004).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config as app_config
from app.core.enums import NotificationSeverity, NotificationType
from app.core.models import Signal
from app.db.models import ExecutionIntentRecord, Tenant, User
from app.execution import approval
from app.execution.core import intents, policy
from app.platform import phase0_lock

logger = logging.getLogger(__name__)

WHY_PAPER_LATER = "Auto PAPER placement arrives with U4 X2 - nothing was placed"
WHY_OWNER_GONE = "The person this intent is for is no longer an active member - nothing was raised"
CLAIM_BATCH = 10            # intents claimed at a time, so the drain's deadline is checked between small batches


@dataclass
class DrainReport:
    claimed: int = 0
    done: int = 0
    refused: int = 0
    failed: int = 0
    expired: int = 0
    requeued: int = 0


def _money(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:,.2f}"


async def drain(session: AsyncSession, now: Optional[datetime] = None, limit: Optional[int] = None,
                deadline_seconds: Optional[float] = None) -> DrainReport:
    """One pass over the queue for the organisations with `execution_core` on. Never raises for one intent. Claims in
    small batches and stops claiming once `deadline_seconds` (EXECUTION_CORE_DRAIN_SECONDS) have passed (review L4)."""
    report = DrainReport()
    started = time.monotonic()
    budget = float(app_config.EXECUTION_CORE_DRAIN_SECONDS if deadline_seconds is None else deadline_seconds)
    now = intents._utc(now)
    anything, scope = await intents.drain_scope(session)
    if not anything:
        return report
    cap = int(limit or app_config.EXECUTION_CORE_DRAIN_LIMIT)
    report.expired = await intents.expire_overdue(session, scope, now)
    report.requeued, stale_failed = await intents.requeue_stale(session, scope, now, limit=cap)
    report.failed += stale_failed
    while report.claimed < cap and time.monotonic() - started < budget:
        rows = await intents.claim(session, scope, now, min(CLAIM_BATCH, cap - report.claimed))
        if not rows:
            break
        report.claimed += len(rows)
        await _dispatch_batch(session, [row.id for row in rows], now, report)
    return report


async def _dispatch_batch(session: AsyncSession, ids: List[int], now: datetime, report: DrainReport) -> None:
    # Ids first: a rollback after one intent's error expires every loaded row, so each is read again before it runs.
    for intent_id in ids:
        try:
            row = await session.get(ExecutionIntentRecord, intent_id, populate_existing=True)
            if row is None or row.status != intents.CLAIMED:
                continue
            status = await dispatch_one(session, row, now)
        except Exception as exc:  # noqa: BLE001 - fail closed: this intent fails, the next one runs
            logger.exception("Execution intent %s failed", intent_id)
            await session.rollback()
            await intents.finish(session, intent_id, intents.FAILED, route=None, now=now,
                                 reason=f"Dispatch error ({type(exc).__name__}); the worker log has the details")
            status = intents.FAILED
        if status == intents.DONE:
            report.done += 1
        elif status == intents.REFUSED:
            report.refused += 1
        else:
            report.failed += 1


async def dispatch_one(session: AsyncSession, row: ExecutionIntentRecord, now: datetime) -> str:
    """Routes one CLAIMED intent and records the outcome. Returns its final status."""
    intent_id, tenant_id = row.id, row.tenant_id
    if int(row.attempts or 0) > 1:                  # review L1: a retried intent links the proposal it already raised
        action = await intents.existing_proposal(session, tenant_id, intent_id)
        if action is not None:
            await intents.finish(session, intent_id, intents.DONE, route=policy.ROUTE_PROPOSE, ai_action_id=action.id, now=now,
                                 reason=f"Proposal #{action.id} was raised before an interruption")
            return intents.DONE
    from app.plans.limits import tenant_is_active
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or not tenant_is_active(tenant):          # review L3
        await intents.finish(session, intent_id, intents.REFUSED, route=policy.ROUTE_REFUSE, now=now,
                             reason=f"The organisation is {getattr(tenant, 'status', 'gone')}: nothing was raised")
        return intents.REFUSED
    owner = await session.get(User, row.user_id) if row.user_id is not None else None
    if owner is None or owner.tenant_id != tenant_id or not owner.is_active:
        await intents.finish(session, intent_id, intents.REFUSED, route=policy.ROUTE_REFUSE, reason=WHY_OWNER_GONE, now=now)
        return intents.REFUSED
    decision = policy.decide(policy.PolicyInputs(
        mode=row.requested_mode, approval_required=await approval.required(session, tenant_id),
        phase0_locked=await phase0_lock.is_on(session, tenant_id), auto_paper_enabled=False))
    if decision.route == policy.ROUTE_REFUSE:
        await intents.finish(session, intent_id, intents.REFUSED, route=decision.route, reason=decision.reason, now=now)
        return intents.REFUSED
    signal = Signal.model_validate_json(row.signal_json)
    problems = policy.level_problems(row.direction, signal.entry, signal.stop_loss, signal.target1,
                                     need_stop=decision.route != policy.ROUTE_NOTIFY)
    if problems:
        await intents.finish(session, intent_id, intents.REFUSED, route=policy.ROUTE_REFUSE, reason="; ".join(problems), now=now)
        return intents.REFUSED
    if decision.route == policy.ROUTE_NOTIFY:
        return await _notify(session, row, signal, decision, now)
    if decision.route == policy.ROUTE_PROPOSE:
        return await _propose(session, row, signal, decision, now)
    await intents.finish(session, intent_id, intents.REFUSED, route=policy.ROUTE_REFUSE, reason=WHY_PAPER_LATER, now=now)
    return intents.REFUSED


async def _notify(session: AsyncSession, row: ExecutionIntentRecord, signal: Signal, decision: policy.Decision,
                  now: datetime) -> str:
    from app.notifications.service import notify
    intent_id, tenant_id, user_id = row.id, row.tenant_id, row.user_id
    label = intents.SOURCES.get(row.source, row.source)
    # DONE first, in the transaction notify() commits: the row and its notification are written together or not at all.
    if not await intents.finish(session, intent_id, intents.DONE, route=decision.route, reason=decision.reason, now=now, commit=False):
        await session.rollback()
        return intents.FAILED
    record = await notify(
        session, tenant_id, NotificationType.SCREEN_ALERT, title=f"{label}: {row.direction} {row.symbol}",
        message=(f"{label}: {row.direction} {row.symbol} at {_money(signal.entry)} (stop {_money(signal.stop_loss)}, "
                 f"target {_money(signal.target1)}) - {row.strategy_id}. Notify only: nothing is proposed or placed."),
        severity=NotificationSeverity.INFO, user_id=user_id,
        metadata_json=json.dumps({"execution_intent_id": intent_id, "source_ref": row.source_ref}))
    await session.execute(update(ExecutionIntentRecord).where(ExecutionIntentRecord.id == intent_id)
                          .values(notification_id=record.id).execution_options(synchronize_session=False))
    await session.commit()
    return intents.DONE


async def _propose(session: AsyncSession, row: ExecutionIntentRecord, signal: Signal, decision: policy.Decision,
                   now: datetime) -> str:
    intent_id, tenant_id = row.id, row.tenant_id
    key = intents.proposal_key(intent_id)
    try:
        action = await approval.propose_entry(session, tenant_id, signal=signal, strategy_id=row.strategy_id, source=row.source,
                                              idempotency_key=key, user_id=row.user_id, now=now, exchange=row.exchange)
    except approval.EntriesPaused as exc:           # Z3-b pause / Z5 lockout of the owner: not raised, with why
        await session.rollback()
        await intents.finish(session, intent_id, intents.REFUSED, route=policy.ROUTE_REFUSE, reason=str(exc), now=now)
        return intents.REFUSED
    if action is None:                               # a retried intent: its proposal is already there
        action = await intents.existing_proposal(session, tenant_id, intent_id)
        if action is None:
            await intents.finish(session, intent_id, intents.FAILED, route=decision.route, now=now,
                                 reason="The proposal could not be raised - nothing was placed")
            return intents.FAILED
    await intents.finish(session, intent_id, intents.DONE, route=decision.route, reason=f"{decision.reason} (proposal #{action.id})",
                         ai_action_id=action.id, now=now)
    return intents.DONE
