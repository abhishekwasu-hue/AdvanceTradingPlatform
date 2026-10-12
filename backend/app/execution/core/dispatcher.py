"""N1 E1 (ADR-0014): the dispatcher - the trading worker drains the intent queue every cycle, market open or not.

For each claimed intent the facts are read fresh for its organisation (approval-only mode, the Phase 0 PAPER-only lock,
the owner still an active member), `policy.decide` picks the route, and:

* NOTIFY  -> one notification to the owner (`notifications.notify`), in the same transaction as the row's DONE;
* PROPOSE -> one Z3 ENTRY proposal for the owner (`approval.propose_entry`, key `intent:<id>`: a retried intent finds
  its proposal instead of raising a second one). Vision (BYOK-V2), Telegram buttons and push ride on the proposal as
  for any other. Approve places a PAPER order through the existing approval path - never from here;
* REFUSE  -> nothing; the reason is on the row;
* PAPER   -> N1 X2 (Auto PAPER from an armed scan template, flag `scan_auto_paper` off by default; N1-3 is the owner's
  decision): `_paper` asks approval-only mode again, the owner's pause / P1-d / F2 caps, the scan limits and a
  re-quote through the owner's own price, then places one PAPER order through `execute_signal_for_user` **without a
  broker** (a GATED callee: no broker order can come from here). Any fallback is a proposal; nothing else is placed.

Fail-closed: any error on an intent marks it FAILED with the error's type and moves on; the drain never raises into
the worker cycle. Exits never come here (ADR-0004).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config as app_config
from app.core.enums import NotificationSeverity, NotificationType
from app.core.models import Signal
from app.db.models import ExecutionIntentRecord, Tenant, User
from app.execution import approval
from app.execution.core import intents, policy
from app.platform import phase0_lock

logger = logging.getLogger(__name__)

WHY_PAPER_NOT_SCAN = "Auto PAPER is placed only for an armed scan template - nothing was placed"
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
        await _dispatch_batch(session, [row.id for row in rows], now, report, started)
    return report


async def _dispatch_batch(session: AsyncSession, ids: List[int], start_now: datetime, report: DrainReport,
                          started: Optional[float] = None) -> None:
    # Ids first: a rollback after one intent's error expires every loaded row, so each is read again before it runs.
    started = time.monotonic() if started is None else started
    for intent_id in ids:
        # Review of X2 (L4): each intent's own clock - the drain's time plus what has passed since it began (the entry
        # window and the limits are asked at the moment the intent is dispatched, not when the drain started).
        now = start_now + timedelta(seconds=time.monotonic() - started)
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
        elif status != intents.CLAIMED:            # CLAIMED: left to the other attempt that is filling its order
            report.failed += 1


async def dispatch_one(session: AsyncSession, row: ExecutionIntentRecord, now: datetime) -> str:
    """Routes one CLAIMED intent and records the outcome. Returns its final status."""
    intent_id, tenant_id = row.id, row.tenant_id
    if int(row.attempts or 0) > 1:
        # Review L1 (E1) and X2 H1: a retried intent links what it already did - its PAPER order first (an entry was
        # placed: never a proposal for a second one, whatever the switches say now), else its proposal.
        earlier = await intents.earlier_outcome(session, tenant_id, intent_id, now)
        if earlier is not None:
            order = earlier.get("order")
            # Review of the X2 delta (L2): the status and the announcement land in one commit (both or neither).
            done = await intents.finish(session, intent_id, earlier["status"], route=earlier["route"], ai_action_id=earlier.get("ai_action_id"),
                                        reason=earlier["reason"], now=now, commit=False)
            if done and order is not None and earlier["status"] == intents.DONE:
                from app.execution.core import announce
                await announce.placed_once(session, intent_id, order)     # the crash came between the fill and _placed
            await session.commit()
            return str(earlier["status"])
        if await intents.existing_order(session, tenant_id, intent_id) is not None:
            return intents.CLAIMED                     # its order is still in flight in the other attempt: that one finishes it
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
    auto_paper_enabled = False
    if (row.requested_mode or "").upper() == policy.AUTO_PAPER:
        from app.screener.then import auto_paper
        try:
            async with session.begin_nested():         # reads on the worker's session: a failure rolls back this savepoint only
                auto_paper_enabled = await auto_paper.intent_places(session, row, now)
        except Exception:  # noqa: BLE001 - unknown means "not armed": a proposal
            logger.exception("Auto PAPER switches could not be read for intent %s", intent_id)
            auto_paper_enabled = False
    decision = policy.decide(policy.PolicyInputs(
        mode=row.requested_mode, approval_required=await approval.required(session, tenant_id),
        phase0_locked=await phase0_lock.is_on(session, tenant_id), auto_paper_enabled=auto_paper_enabled))
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
    if decision.route == policy.ROUTE_PAPER and auto_paper_enabled:
        return await _paper(session, row, owner, signal, now)
    await intents.finish(session, intent_id, intents.REFUSED, route=policy.ROUTE_REFUSE, reason=WHY_PAPER_NOT_SCAN, now=now)
    return intents.REFUSED


async def _fallback(session: AsyncSession, row: ExecutionIntentRecord, signal: Signal, why: str, now: datetime) -> str:
    """An Auto PAPER setup that may not be placed now becomes a proposal (with why) - never a silent skip."""
    return await _propose(session, row, signal, policy.Decision(policy.ROUTE_PROPOSE, why), now)


async def _paper_checks(session: AsyncSession, row: ExecutionIntentRecord, owner: User, signal: Signal,
                        now: datetime) -> Tuple[str, str, Optional[float]]:
    """("refuse" | "propose" | "place", why, re-quoted price). Reads only (the caller holds a savepoint)."""
    from app.ai.worker_bridge import price_lookup_for_user
    from app.paper.service import paused_reason
    from app.portfolio import caps
    from app.risk_engine.by_default import proposal_refusal
    from app.screener.then import auto_paper
    if not approval.can_trade(owner):                           # review of X2 (L1): a trading role and trading:write
        return "refuse", "The template's owner may not trade (role or scope) - nothing was placed", None
    paused = await paused_reason(session, owner.id)
    if paused:
        return "refuse", paused, None
    locked = await proposal_refusal(session, row.tenant_id, owner.id, now)
    if locked:
        return "refuse", locked, None
    refused = await caps.entry_refusals(session, owner, caps.candidate_of(row.symbol, signal.entry, signal.stop_loss), now)
    if refused:
        return "refuse", "; ".join(refused), None
    late = await auto_paper.window_reason(session, now, row.exchange or "NSE")      # review of X2 (M2): the entry window
    if late:
        return "propose", late, None
    limit = await auto_paper.limit_reason(session, row, now)
    if limit:
        return "propose", limit, None
    lookup = await price_lookup_for_user(session, owner)
    if lookup is None:
        return "propose", "No live price on your own broker session to re-quote the Auto PAPER entry - a proposal instead", None
    try:
        price = float(await lookup(row.symbol, row.exchange or "NSE"))
    except Exception as exc:  # noqa: BLE001 - no price, no placement
        return "propose", f"No live price for {row.symbol} to re-quote the Auto PAPER entry ({type(exc).__name__}) - a proposal instead", None
    verdict, why = approval.requote_check(row.direction, float(signal.entry or 0.0), price, stop_loss=signal.stop_loss, target1=signal.target1,
                                          max_drift_pct=app_config.APPROVAL_REQUOTE_MAX_DRIFT_PCT,
                                          max_drift_ticks=app_config.APPROVAL_REQUOTE_MAX_DRIFT_TICKS, tick_size=approval.tick_size_for(row.symbol))
    if verdict != "ok":
        return "propose", f"The price moved since the setup ({why}) - a proposal instead", None
    return "place", "", price


async def _paper(session: AsyncSession, row: ExecutionIntentRecord, owner: User, signal: Signal, now: datetime) -> str:
    """N1 X2: one Auto PAPER entry for an armed scan template's owner, or a proposal / refusal with why.

    Approval-only mode is asked again right before anything else (Z3 census: `approval.required(` comes before
    `execute_signal_for_user(` here). The checks read inside a savepoint; the pipeline commits on its own, so a failure
    there is rolled back here and the intent ends FAILED - the shared session is never left aborted. PAPER only: the
    pipeline is called without a broker."""
    from app.db.models import OrderRecord
    from app.execution.signal_execution import execute_signal_for_user
    # Plain values first: the pipeline commits, which expires the row (an async session must not lazy-load it later).
    facts = _Facts(row.id, row.tenant_id, row.direction, row.symbol, row.strategy_id, row.source_ref)
    intent_id, tenant_id = facts.intent_id, facts.tenant_id
    taken = await session.scalar(select(OrderRecord).where(OrderRecord.tenant_id == tenant_id,
                                                           OrderRecord.idempotency_key == intents.order_key(intent_id)))
    if taken is not None and (taken.user_id != owner.id or taken.mode != "PAPER"):
        # Review of X2 (L1): the key belongs to another order (another member's, or not PAPER): never link or replay it.
        await intents.finish(session, intent_id, intents.REFUSED, route=policy.ROUTE_REFUSE, now=now,
                             reason=f"The order key of this intent is used by another order (#{taken.id}) - nothing was placed")
        return intents.REFUSED
    earlier = await intents.existing_order(session, tenant_id, intent_id, owner.id)
    if earlier is not None and str(earlier.status) in intents.IN_FLIGHT_ORDER:
        return intents.CLAIMED
    if earlier is not None:                          # a retried intent: its PAPER order was placed before an interruption
        await intents.finish(session, intent_id, intents.DONE, route=policy.ROUTE_PAPER, now=now,
                             reason=f"Auto PAPER: PAPER order #{earlier.id} was placed before an interruption")
        return intents.DONE
    if await approval.required(session, tenant_id):
        return await _fallback(session, row, signal, policy.WHY_AUTO_APPROVAL, now)
    try:
        async with session.begin_nested():
            outcome, why, price = await _paper_checks(session, row, owner, signal, now)
    except Exception as exc:  # noqa: BLE001 - fail closed: nothing placed (the savepoint is rolled back)
        logger.exception("Auto PAPER checks failed for intent %s", intent_id)
        await intents.finish(session, intent_id, intents.FAILED, route=policy.ROUTE_PAPER, now=now,
                             reason=f"Auto PAPER checks could not run ({type(exc).__name__}) - nothing was placed")
        return intents.FAILED
    if outcome == "refuse":
        await intents.finish(session, intent_id, intents.REFUSED, route=policy.ROUTE_REFUSE, reason=why[:300], now=now)
        return intents.REFUSED
    if outcome == "propose" or price is None:
        return await _fallback(session, row, signal, why, now)
    owner_id = owner.id
    placed = signal.model_copy(update={"entry": price, "timestamp": now,
                                       "reasons": list(signal.reasons) + [f"Auto PAPER (intent #{intent_id}), re-quoted at {price:g}"]})
    try:
        result, order = await execute_signal_for_user(
            session, owner, mode="PAPER", strategy_id=facts.strategy_id, signal=placed, idempotency_key=intents.order_key(intent_id),
            origin=approval.ORIGIN_AUTOMATIC, journal_source="SCAN")
    except Exception as exc:  # noqa: BLE001 - the pipeline commits on its own: roll back what it left, record why
        logger.exception("Auto PAPER placement failed for intent %s", intent_id)
        await session.rollback()
        await intents.finish(session, intent_id, intents.FAILED, route=policy.ROUTE_PAPER, now=now,
                             reason=f"Auto PAPER placement failed ({type(exc).__name__}) - see the worker log")
        return intents.FAILED
    if not result.executed and str(order.status) in intents.IN_FLIGHT_ORDER:
        # Review of X2 (L2): the stale-requeue overlap - another attempt of this intent placed the order and is still
        # filling it; this one leaves the row to it instead of calling it refused.
        return intents.CLAIMED
    if not result.executed:
        await intents.finish(session, intent_id, intents.REFUSED, route=policy.ROUTE_PAPER, now=now,
                             reason=("PAPER order not placed: " + ("; ".join(result.reasons) or str(order.status)))[:300])
        return intents.REFUSED
    await _placed(session, facts, owner_id, placed, int(order.id), getattr(order, "trade_id", None), price, now)
    return intents.DONE


@dataclass(frozen=True)
class _Facts:
    intent_id: int
    tenant_id: int
    direction: str
    symbol: str
    strategy_id: str
    source_ref: str


async def _placed(session: AsyncSession, f: _Facts, owner_id: int, signal: Signal, order_id: int, trade_id: Optional[int], price: float,
                  now: datetime) -> None:
    """DONE with the order, an audit row and one factual notification to the owner (no advice)."""
    from app.execution.core import announce
    trade = f"; position #{trade_id}" if trade_id else ""
    if not await intents.finish(session, f.intent_id, intents.DONE, route=policy.ROUTE_PAPER, now=now, commit=False,
                                reason=f"Auto PAPER: PAPER order #{order_id} at {price:g}{trade}"):
        await session.rollback()                       # another attempt settled the row; it announces the entry
        # Review of the X2 delta (L1): or a sweep settled it EXPIRED / FAILED while this attempt's pipeline was still
        # filling the order (the sweep saw no order yet). The owner's filled PAPER order is the entry: the row becomes
        # DONE (only from that status) and the owner hears once - never an open position under an "Interrupted" intent.
        order = await intents.existing_order(session, f.tenant_id, f.intent_id, owner_id)
        if order is not None and str(order.status or "") in intents.PLACED_ORDER and await intents.relink_placed(
                session, f.intent_id, route=policy.ROUTE_PAPER, now=now,
                reason=f"Auto PAPER: PAPER order #{order_id} at {price:g}{trade} (placed while the row was being swept)"):
            await announce.placed_once(session, f.intent_id, order)
            await session.commit()
        return
    await announce.write(session, tenant_id=f.tenant_id, owner_id=owner_id, intent_id=f.intent_id, direction=f.direction, symbol=f.symbol,
                         strategy_id=f.strategy_id, source_ref=f.source_ref, order_id=order_id, price=price,
                         stop_loss=signal.stop_loss, target1=signal.target1)
    await session.commit()


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
