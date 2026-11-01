"""Phase L4: the monitoring agent (V4.1) - observe, propose, wait for a human, then execute.

Deterministic rules over the tenant's own records (never a model call in the decision path):

- LOSING_STREAK: a deployment's last N trades today all lost           -> PAUSE_DEPLOYMENT
- DAY_DRAWDOWN:  a deployment's realised P&L today below -X% of capital -> PAUSE_DEPLOYMENT
- ERROR_STREAK:  consecutive evaluation failures                        -> PAUSE_DEPLOYMENT
- STALE_POSITION: an open position older than the time budget and
                  moving against us with an adverse regime              -> EXIT_POSITION
- WIN_RATE_DRIFT: rolling win rate far below the strategy's backtest     -> REVIEW_STRATEGY

Each firing writes one `ai_actions` row (PROPOSED) and one notification; duplicates for the same
(deployment, rule) are suppressed while a proposal is open or was decided today. `decide()` is
the human step; `execute()` runs only on APPROVED rows and reuses the ordinary services (pause =
the deployments API's own status change; exit = the position monitor's `close_position`). Open
proposals EXPIRE after `TTL_HOURS`. No model is called anywhere in this module: the reason the human
reads is the rule's own sentence (H-C1 f corrects an earlier note that said an LLM phrased it).
"""
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.core.enums import NotificationSeverity, NotificationType
from app.core.json_finite import dumps as finite_dumps
from app.db.models import AiActionRecord, BacktestRunRecord, StrategyDeploymentRecord, TradeRecord, User
from app.market_data.calendar import IST
from app.notifications.service import notify
from app.observability.metrics import AI_DECISIONS, AI_PROPOSALS
from app.risk_engine.routes import get_tenant_risk_config

TTL_HOURS = 24
LOSING_STREAK = 3
DAY_DRAWDOWN_PCT = 2.0
ERROR_STREAK = 3       # below the worker's own auto-pause (5) so the human hears first
STALE_POSITION_MINUTES = 120
WIN_RATE_DRIFT = 0.25      # absolute drop vs the backtest's win rate, with >= 10 trades
# ENTRY: Z3 approval-only entries; CREATE_ALERT: N3 S9 - an alert rule the Copilot proposed, created only on approval
ACTIONS = ("PAUSE_DEPLOYMENT", "EXIT_POSITION", "REDUCE_RISK", "REVIEW_STRATEGY", "ENTRY", "CREATE_ALERT")
OPEN_STATES = ("PROPOSED", "APPROVED")


@dataclass
class Proposal:
    deployment_id: Optional[int]
    trade_id: Optional[int]
    action: str
    rule: str
    reason: str
    evidence: Dict
    owner_user_id: Optional[int] = None     # N3 S9: the person the proposal is for (only they or the OWNER decide it)


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _already_open(session: AsyncSession, tenant_id: int, deployment_id: Optional[int], rule: str, now: datetime) -> bool:
    day_start = now.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    rows = await session.scalars(select(AiActionRecord).where(
        AiActionRecord.tenant_id == tenant_id, AiActionRecord.deployment_id == deployment_id, AiActionRecord.rule == rule))
    for row in rows:
        if row.status in OPEN_STATES:
            return True
        if row.status == "FAILED":
            continue            # P0.8 / A1: an approved action the system could not carry out leaves the rule free to fire again
        if row.decided_at is not None and _utc(row.decided_at) >= day_start:
            return True
        if row.status == "EXECUTED" and row.executed_at is not None and _utc(row.executed_at) >= day_start:
            return True
    return False


async def observe(session: AsyncSession, tenant_id: int, deployments: List[StrategyDeploymentRecord], now: datetime, *,
                  regime_lookup: Optional[Callable[[StrategyDeploymentRecord], Optional[str]]] = None) -> List[Proposal]:
    """Pure observation over persisted state; returns the proposals worth raising."""
    risk = await get_tenant_risk_config(tenant_id, session)
    # DEF-0 / M68 PR-7 (TS1-065): the organisation's own capital - never a built-in figure; not set: no day-drawdown %
    # proposal (a share of a capital nobody chose is not measured).
    capital = float(risk.capital) if risk is not None and risk.capital is not None else 0.0
    day_start = now.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    proposals: List[Proposal] = []
    for dep in deployments:
        if dep.status != "ACTIVE":
            continue
        trades = list(await session.scalars(select(TradeRecord).where(TradeRecord.deployment_id == dep.id).order_by(TradeRecord.id)))
        closed_today = [t for t in trades if t.exit_time is not None and _utc(t.exit_time) >= day_start]
        if len(closed_today) >= LOSING_STREAK and all((t.pnl or 0.0) < 0 for t in closed_today[-LOSING_STREAK:]):
            proposals.append(Proposal(dep.id, None, "PAUSE_DEPLOYMENT", "LOSING_STREAK",
                                      f"Deployment #{dep.id} ({dep.strategy_id} on {dep.symbol}) lost its last {LOSING_STREAK} trades today.",
                                      {"losses": [round(t.pnl or 0.0, 2) for t in closed_today[-LOSING_STREAK:]]}))
        day_pnl = sum((t.pnl or 0.0) for t in closed_today)
        if capital > 0 and day_pnl < -capital * DAY_DRAWDOWN_PCT / 100.0:
            proposals.append(Proposal(dep.id, None, "PAUSE_DEPLOYMENT", "DAY_DRAWDOWN",
                                      f"Deployment #{dep.id} is down {day_pnl:,.0f} today ({day_pnl / capital * 100:.1f}% of capital {capital:,.0f}).",
                                      {"day_pnl": round(day_pnl, 2), "capital": capital, "threshold_pct": DAY_DRAWDOWN_PCT}))
        if (dep.consecutive_failures or 0) >= ERROR_STREAK:
            proposals.append(Proposal(dep.id, None, "PAUSE_DEPLOYMENT", "ERROR_STREAK",
                                      f"Deployment #{dep.id} failed {dep.consecutive_failures} evaluations in a row: {dep.last_error or 'see logs'}",
                                      {"consecutive_failures": dep.consecutive_failures, "last_error": dep.last_error}))
        for trade in (t for t in trades if t.exit_time is None):
            age_min = (now - _utc(trade.entry_time)).total_seconds() / 60.0
            regime = regime_lookup(dep) if regime_lookup else None
            adverse = regime is not None and ((trade.direction == "LONG" and regime == "TRENDING_DOWN") or (trade.direction == "SHORT" and regime == "TRENDING_UP") or regime == "VOLATILE")
            if age_min >= STALE_POSITION_MINUTES and adverse:
                proposals.append(Proposal(dep.id, trade.id, "EXIT_POSITION", "STALE_POSITION",
                                          f"Position #{trade.id} ({trade.direction} {trade.symbol}) is {age_min:.0f} min old and the regime turned {regime}.",
                                          {"age_minutes": round(age_min), "regime": regime}))
        closed_all = [t for t in trades if t.exit_time is not None]
        if len(closed_all) >= 10:
            live_wr = sum(1 for t in closed_all[-20:] if (t.pnl or 0.0) > 0) / len(closed_all[-20:])
            run = await session.scalar(select(BacktestRunRecord).where(BacktestRunRecord.tenant_id == tenant_id, BacktestRunRecord.strategy_id == dep.strategy_id)
                                       .order_by(BacktestRunRecord.id.desc()))
            bt_wr = None
            if run is not None:
                metrics = json.loads(run.metrics_json or "{}")
                bt_wr = metrics.get("win_rate")
                if bt_wr is not None and bt_wr > 1:
                    bt_wr = bt_wr / 100.0
            if bt_wr is not None and bt_wr - live_wr >= WIN_RATE_DRIFT:
                proposals.append(Proposal(dep.id, None, "REVIEW_STRATEGY", "WIN_RATE_DRIFT",
                                          f"Deployment #{dep.id}: live win rate {live_wr:.0%} over the last {len(closed_all[-20:])} trades vs {bt_wr:.0%} in the latest backtest.",
                                          {"live_win_rate": round(live_wr, 3), "backtest_win_rate": round(bt_wr, 3), "backtest_run_id": run.id}))
            # Phase P4 / section 50 model-drift gate: when the degradation engine (win rate,
            # expectancy, profit factor vs the saved backtest) says DEGRADED, propose a pause -
            # the human decides, exactly like every other proposal.
            if run is not None:
                from app.trading.degradation import compare, live_metrics
                verdict = compare(live_metrics(closed_all), json.loads(run.metrics_json or "{}"))
                if verdict["status"] == "DEGRADED":
                    proposals.append(Proposal(dep.id, None, "PAUSE_DEPLOYMENT", "DEGRADATION",
                                              f"Deployment #{dep.id} ({dep.strategy_id}) has degraded versus its backtest: {'; '.join(verdict['reasons'])}.",
                                              {"reasons": verdict["reasons"], "backtest_run_id": run.id, "closed_trades": len(closed_all)}))
    return proposals


async def raise_proposals(session: AsyncSession, tenant_id: int, proposals: List[Proposal], now: datetime, *,
                          phrase: Optional[Callable[[Proposal], "str | None"]] = None) -> List[AiActionRecord]:
    created: List[AiActionRecord] = []
    for p in proposals:
        if await _already_open(session, tenant_id, p.deployment_id, p.rule, now):
            continue
        reason = p.reason
        if phrase is not None:
            try:
                extra = phrase(p)
                if extra:
                    reason = f"{p.reason}\n\n{extra}"
            except Exception:  # noqa: BLE001 - phrasing is optional
                pass
        row = AiActionRecord(tenant_id=tenant_id, deployment_id=p.deployment_id, trade_id=p.trade_id, action=p.action, rule=p.rule, reason=reason,
                             evidence_json=finite_dumps(p.evidence), status="PROPOSED", expires_at=now + timedelta(hours=TTL_HOURS),
                             owner_user_id=p.owner_user_id)
        try:
            # P0.8 / A5: the insert runs in a savepoint, so when the partial unique index on open proposals (tenant,
            # deployment, rule) catches a concurrent raise only this row is dropped - the proposals and notifications
            # already flushed in this call, and the caller's loaded objects, stay intact.
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            continue
        AI_PROPOSALS.labels(action=p.action).inc()
        await notify(session, tenant_id, NotificationType.AI_PROPOSAL, title=f"AI proposes {p.action.replace('_', ' ').lower()}",
                     message=f"{p.reason} Approve or reject it under AI Copilot - nothing happens until you do.",
                     severity=NotificationSeverity.WARNING, related_trade_id=p.trade_id, ai_action_id=row.id,
                     # review of S9 (LOW): addressed to the person it is for (only they or the OWNER decide it, and the
                     # AI_PROPOSAL kind stays in the organisation feed the OWNER reads); None = organisation-wide, as before
                     user_id=p.owner_user_id)
        # BYOK-V2: queue the deployment creator's Vision audit (flag vision_byok; a DB row only, in a savepoint, never
        # raises). The audit itself runs later in the worker and only comments - it never touches this proposal.
        from app.ai import vision_audit
        await vision_audit.enqueue_for_proposal(session, row, now=now)
        created.append(row)
    await session.commit()
    return created


ORPHAN_MINUTES = 10      # Z3 (review M6): an APPROVED row not carried out by then was interrupted (decide -> execute)


async def expire_stale(session: AsyncSession, now: datetime, *, tenant_id: Optional[int] = None) -> int:
    """PROPOSED rows past their expiry -> EXPIRED, each by a conditional UPDATE (a decision taken at the same instant
    wins). Z3: the worker runs this for every organisation every cycle (entry proposals live minutes). An APPROVED row
    older than ORPHAN_MINUTES was interrupted between the decision and its execution: an entry whose PAPER order did go
    through is recorded EXECUTED with its trade, anything else FAILED - so it never holds a deployment's open slot."""
    now = _utc(now)
    query = select(AiActionRecord).where(AiActionRecord.status.in_(("PROPOSED", "APPROVED")))
    if tenant_id is not None:
        query = query.where(AiActionRecord.tenant_id == tenant_id)
    expired: List[AiActionRecord] = []
    for row in list(await session.scalars(query.order_by(AiActionRecord.id).limit(2000))):
        if row.status == "PROPOSED":
            if _utc(row.expires_at) > now:
                continue
            values = {"status": "EXPIRED", "result": "No decision within the time window"}
        else:
            if row.decided_at is None or (now - _utc(row.decided_at)).total_seconds() < ORPHAN_MINUTES * 60:
                continue
            values = await _orphan_outcome(session, row, now)
        claimed = await session.execute(update(AiActionRecord).where(AiActionRecord.id == row.id, AiActionRecord.status == row.status)
                                        .values(**values).execution_options(synchronize_session=False))
        if claimed.rowcount == 1:
            expired.append(row)
    if expired:
        await session.commit()
        # PW4: an expired proposal's Telegram buttons come off too (best effort, after the commit).
        from app.telegram_inbound.service import sync_proposal_message
        for row in expired:
            await session.refresh(row)
            await sync_proposal_message(session, row, now=now)
    return len(expired)


async def _orphan_outcome(session: AsyncSession, row: AiActionRecord, now: datetime) -> dict:
    if row.action == "ENTRY":
        from app.db.models import OrderRecord
        order = await session.scalar(select(OrderRecord).where(OrderRecord.tenant_id == row.tenant_id, OrderRecord.idempotency_key == f"approval:{row.id}"))
        if order is not None and order.trade_id is not None:
            return {"status": "EXECUTED", "executed_at": now, "trade_id": order.trade_id,
                    "result": f"PAPER order #{order.id} was placed (recorded after an interrupted approval); position #{order.trade_id}"}
    if row.action == "EXIT_POSITION" and row.trade_id is not None:
        # Review V5: say what is true of the position - its close may have committed before the interruption.
        trade = await session.get(TradeRecord, row.trade_id)
        if trade is not None and trade.exit_time is not None:
            return {"status": "FAILED", "executed_at": now,
                    "result": f"Approved; its execution was interrupted - position #{trade.id} is closed ({trade.exit_reason or 'see the journal'})"[:300]}
        return {"status": "FAILED", "executed_at": now, "result": "Approved but not carried out (interrupted) - the position is still open; it may be proposed again"}
    if row.action == "ENTRY":
        return {"status": "FAILED", "executed_at": now, "result": "Approved but not carried out (interrupted) - no order was placed; it may be proposed again"}
    return {"status": "FAILED", "executed_at": now, "result": "Approved but not carried out (interrupted) - it may be proposed again"}


async def decide(session: AsyncSession, action: AiActionRecord, user: User, *, approve: bool, note: Optional[str], now: Optional[datetime] = None) -> AiActionRecord:
    now = now or datetime.now(timezone.utc)
    if action.status != "PROPOSED":
        raise ValueError(f"Action is {action.status}; only PROPOSED actions can be decided")
    if _utc(action.expires_at) <= now:
        action.status, action.result = "EXPIRED", "Expired before the decision"
        await session.commit()
        raise ValueError("This proposal has expired")
    status = "APPROVED" if approve else "REJECTED"
    # P0.8 / A5: the decision is a conditional UPDATE - the web and Telegram (or two tabs) may decide the same proposal at
    # the same instant, and only the one whose row was still PROPOSED wins; the other learns it was already decided.
    claimed = await session.execute(update(AiActionRecord).where(AiActionRecord.id == action.id, AiActionRecord.status == "PROPOSED")
                                    .values(status=status, decided_by=user.id, decided_at=now, decision_note=(note or "")[:300] or None)
                                    .execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        # The UPDATE matched nothing, so there is nothing to roll back: reload the row and tell the loser. (A rollback
        # here would expire every object the caller still holds - the Telegram handler's tenant among them.)
        await session.refresh(action)
        raise ValueError(f"Action is {action.status}; it was decided concurrently")
    AI_DECISIONS.labels(decision=status).inc()
    await write_audit_log(session, action.tenant_id, user.id, "ai_action_decided", f"#{action.id} {action.action} {status} {note or ''}".strip())
    await session.commit()
    await session.refresh(action)
    return action


def is_snoozed(action: AiActionRecord, now: Optional[datetime] = None) -> bool:
    """PW5: a PROPOSED row whose snooze has not ended yet (hidden from the pending list and the badge)."""
    now = now or datetime.now(timezone.utc)
    return action.status == "PROPOSED" and action.snoozed_until is not None and _utc(action.snoozed_until) > now


async def snooze(session: AsyncSession, action: AiActionRecord, user: User, *, until: datetime, via: str = "web",
                 now: Optional[datetime] = None) -> AiActionRecord:
    """PW5: hide a PROPOSED proposal until `until`; the worker announces it once more when it wakes (`wake_snoozed`).
    A snooze never extends the expiry: it is cut at `expires_at`, and the proposal expires on time as before. It is
    not a decision (status stays PROPOSED, nobody is recorded as the decider) and no exit ever looks at it."""
    now = now or datetime.now(timezone.utc)
    if action.status != "PROPOSED":
        raise ValueError(f"Action is {action.status}; only PROPOSED actions can be snoozed")
    expires = _utc(action.expires_at)
    if expires <= now:
        action.status, action.result = "EXPIRED", "Expired before the decision"
        await session.commit()
        raise ValueError("This proposal has expired")
    until = _utc(until)
    if until <= now:
        raise ValueError("A snooze must end in the future")
    wake = min(until, expires)
    # Conditional, like decide(): a decision taken at the same instant on another surface wins, the snooze is refused.
    claimed = await session.execute(update(AiActionRecord).where(AiActionRecord.id == action.id, AiActionRecord.tenant_id == action.tenant_id,
                                                                 AiActionRecord.status == "PROPOSED")
                                    .values(snoozed_until=wake).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        await session.refresh(action)
        raise ValueError(f"Action is {action.status}; it was decided concurrently")
    await write_audit_log(session, action.tenant_id, user.id, "ai_action_snoozed",
                          f"#{action.id} {action.action} until {wake.isoformat()} via {via}" + (" (cut at the expiry)" if wake < until else ""))
    await session.commit()
    await session.refresh(action)
    return action


async def wake_snoozed(session: AsyncSession, now: datetime, *, tenant_id: Optional[int] = None) -> int:
    """PW5: snoozes that ended. Each still-open proposal is announced once more through the ordinary notification
    service (in-app, push, Telegram with fresh buttons); one that reached its expiry meanwhile is EXPIRED instead.
    The conditional UPDATE that clears `snoozed_until` is the "once": two workers never both re-announce a row."""
    query = select(AiActionRecord).where(AiActionRecord.status == "PROPOSED", AiActionRecord.snoozed_until.is_not(None),
                                         AiActionRecord.snoozed_until <= now)
    if tenant_id is not None:
        query = query.where(AiActionRecord.tenant_id == tenant_id)
    woken = 0
    for row in list(await session.scalars(query.order_by(AiActionRecord.id).limit(500))):
        snoozed_until, expired = row.snoozed_until, _utc(row.expires_at) <= now
        values = {"snoozed_until": None} if not expired else {"status": "EXPIRED", "result": "No decision within the time window (expired while snoozed)"}
        claimed = await session.execute(update(AiActionRecord).where(AiActionRecord.id == row.id, AiActionRecord.status == "PROPOSED",
                                                                     AiActionRecord.snoozed_until == snoozed_until)
                                        .values(**values).execution_options(synchronize_session=False))
        await session.commit()
        if claimed.rowcount != 1:
            continue
        if expired:
            # As in expire_stale: the expired proposal's Telegram buttons come off (best effort).
            from app.telegram_inbound.service import sync_proposal_message
            await session.refresh(row)
            await sync_proposal_message(session, row, now=now)
            continue
        await notify(session, row.tenant_id, NotificationType.AI_PROPOSAL, title=f"Reminder: AI proposes {row.action.replace('_', ' ').lower()}",
                     message=f"{row.reason} You snoozed this proposal; it is waiting again. Approve, reject or snooze it under AI Copilot - nothing happens until you do.",
                     severity=NotificationSeverity.WARNING, related_trade_id=row.trade_id, ai_action_id=row.id)
        woken += 1
    return woken


async def execute(session: AsyncSession, action: AiActionRecord, user: User, *, price_lookup=None, broker=None, now: Optional[datetime] = None) -> AiActionRecord:
    """Runs an APPROVED action through the ordinary services. Called right after approval by the
    API (so the human sees the result) - never by the agent on its own."""
    now = now or datetime.now(timezone.utc)
    if action.status != "APPROVED":
        raise ValueError(f"Action is {action.status}; only APPROVED actions execute")
    if action.action == "ENTRY":
        # Z3 / BT2: an approved entry proposal - re-quoted, then one PAPER order through the ordinary pipeline.
        from app.execution.approval import execute_entry
        return await execute_entry(session, action, user, price_lookup=price_lookup, now=now)
    try:
        if action.action == "PAUSE_DEPLOYMENT":
            dep = await session.get(StrategyDeploymentRecord, action.deployment_id)
            if dep is None:
                raise ValueError("Deployment no longer exists")
            if dep.status == "ACTIVE":
                dep.status = "PAUSED"
                dep.pause_reason = f"Paused on AI proposal #{action.id} ({action.rule}), approved by user {user.id}"
                await write_audit_log(session, action.tenant_id, user.id, "deployment_paused", f"#{dep.id} AI action #{action.id} {action.rule}")
                action.result = f"Deployment #{dep.id} paused"
            else:
                action.result = f"Deployment #{dep.id} already {dep.status}"
        elif action.action == "EXIT_POSITION":
            from app.trading.position_monitor import close_position
            trade = await session.get(TradeRecord, action.trade_id)
            if trade is None or trade.exit_time is not None:
                action.result = "Position already closed"
            else:
                if price_lookup is None:
                    raise ValueError("No price source available to exit the position - use the Positions page")
                from app.trading.position_monitor import venue_for
                exchange = await venue_for(session, trade)     # Z3 (review L3) / BK2-8: the position's own venue, NSE by default
                price = (await (price_lookup(trade.symbol) if exchange == "NSE" else price_lookup(trade.symbol, exchange))
                         if callable(price_lookup) else None)
                if price is None:
                    raise ValueError("No live price for the position")
                if trade.mode == "LIVE" and broker is None:
                    raise ValueError("No broker session for this LIVE position - close it from the Positions page")
                outcome = await close_position(session, trade, float(price), f"AI action #{action.id} ({action.rule}) approved", broker=broker, user_id=user.id, now=now)
                if not outcome.closed:
                    # P0.8 / A1: an exit the broker did not complete is a FAILED action, never EXECUTED - the position is still
                    # open, the row says why, and the rule may propose again (FAILED is not an open state).
                    raise ValueError(f"Exit not completed: {outcome.exit_reason or '; '.join(outcome.warnings) or 'unknown'}")
                action.result = f"Position #{trade.id} closed at {price}"
        elif action.action == "CREATE_ALERT":
            action.result = await _create_alert(session, action, user)
        elif action.action in ("REDUCE_RISK", "REVIEW_STRATEGY"):
            action.result = "Acknowledged - review the strategy/risk settings; no automatic change is made for this action type"
        else:
            raise ValueError(f"Unknown action {action.action}")
        action.status, action.executed_at = "EXECUTED", now
    except Exception as exc:  # noqa: BLE001 - recorded on the row, surfaced to the human
        action.status, action.result, action.executed_at = "FAILED", str(exc)[:300], now
    await session.commit()
    await session.refresh(action)
    return action


async def _create_alert(session: AsyncSession, action: AiActionRecord, user: User) -> str:
    """N3 S9: an approved Copilot alert proposal -> the alert rule, for the person who asked (checked again now: flags,
    validation). An alert rule notifies; it never places an order."""
    from fastapi import HTTPException

    from app.ai.tools.screener import FLAG as COPILOT_FLAG
    from app.ai.tools.screener import final_name
    from app.alerts.rules_routes import RuleBody, build_rule
    from app.auth.dependencies import ensure_trader
    from app.platform.controls import flag_enabled
    owner = await session.get(User, action.owner_user_id) if action.owner_user_id else user
    if owner is None or owner.tenant_id != action.tenant_id or not owner.is_active:
        raise ValueError("The person who asked for this alert is no longer an active member")
    if not await flag_enabled(session, COPILOT_FLAG, action.tenant_id):          # review LOW-2: checked again at approval
        raise ValueError(f"Copilot alert proposals are no longer enabled for this organisation (flag {COPILOT_FLAG})")
    try:                                                                          # review LOW-1: the owner may still write alerts
        await ensure_trader(session, owner)
        await ensure_trader(session, owner, scope="alerts:write")
    except HTTPException as exc:
        raise ValueError("The person who asked for this alert may not create alerts now") from exc
    raw = (json.loads(action.evidence_json or "{}") or {}).get("rule") or {}
    if isinstance(raw, dict) and raw.get("name"):                                 # review MED-2: filtered again, as stored
        raw = {**raw, "name": final_name(str(raw["name"]))}
    body = RuleBody.model_validate(raw)
    try:
        async with session.begin_nested():                 # a refusal leaves nothing behind in the action's commit
            rule = await build_rule(session, owner, body)
            await session.flush()
    except HTTPException as exc:
        detail = exc.detail.get("message") if isinstance(exc.detail, dict) else exc.detail
        raise ValueError(f"The alert could not be created: {detail}") from exc
    await write_audit_log(session, action.tenant_id, user.id, "alert_rule_created", f"rule #{rule.id} from AI action #{action.id} (approved)")
    return f"Alert rule #{rule.id} created ({rule.kind}, {rule.base_tf}); it notifies only"


def as_dict(row: AiActionRecord) -> dict:
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {"id": row.id, "deployment_id": row.deployment_id, "trade_id": row.trade_id, "action": row.action, "rule": row.rule, "reason": row.reason,
            "evidence": json.loads(row.evidence_json or "{}"), "status": row.status, "decided_by": row.decided_by, "decided_at": iso(row.decided_at),
            "decision_note": row.decision_note, "executed_at": iso(row.executed_at), "result": row.result, "expires_at": iso(row.expires_at),
            "created_at": iso(row.created_at), "snoozed_until": iso(row.snoozed_until)}
