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
from app.db.models import AiActionRecord, BacktestRunRecord, StrategyDeploymentRecord, TradeRecord, User
from app.market_data.calendar import IST
from app.notifications.service import notify
from app.risk_engine.routes import get_tenant_risk_config
from app.observability.metrics import AI_DECISIONS, AI_PROPOSALS

TTL_HOURS = 24
LOSING_STREAK = 3
DAY_DRAWDOWN_PCT = 2.0
ERROR_STREAK = 3       # below the worker's own auto-pause (5) so the human hears first
STALE_POSITION_MINUTES = 120
WIN_RATE_DRIFT = 0.25      # absolute drop vs the backtest's win rate, with >= 10 trades
ACTIONS = ("PAUSE_DEPLOYMENT", "EXIT_POSITION", "REDUCE_RISK", "REVIEW_STRATEGY")
OPEN_STATES = ("PROPOSED", "APPROVED")


@dataclass
class Proposal:
    deployment_id: Optional[int]
    trade_id: Optional[int]
    action: str
    rule: str
    reason: str
    evidence: Dict


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
    capital = float(risk.capital) if risk is not None else 100_000.0
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
                             evidence_json=json.dumps(p.evidence, default=str), status="PROPOSED", expires_at=now + timedelta(hours=TTL_HOURS))
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
                     severity=NotificationSeverity.WARNING, related_trade_id=p.trade_id, ai_action_id=row.id)
        created.append(row)
    await session.commit()
    return created


async def expire_stale(session: AsyncSession, now: datetime) -> int:
    rows = await session.scalars(select(AiActionRecord).where(AiActionRecord.status == "PROPOSED"))
    count = 0
    for row in rows:
        if _utc(row.expires_at) <= now:
            row.status, row.result = "EXPIRED", "No decision within the time window"
            count += 1
    if count:
        await session.commit()
    return count


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


async def execute(session: AsyncSession, action: AiActionRecord, user: User, *, price_lookup=None, broker=None, now: Optional[datetime] = None) -> AiActionRecord:
    """Runs an APPROVED action through the ordinary services. Called right after approval by the
    API (so the human sees the result) - never by the agent on its own."""
    now = now or datetime.now(timezone.utc)
    if action.status != "APPROVED":
        raise ValueError(f"Action is {action.status}; only APPROVED actions execute")
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
                price = await price_lookup(trade.symbol) if callable(price_lookup) else None
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


def as_dict(row: AiActionRecord) -> dict:
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {"id": row.id, "deployment_id": row.deployment_id, "trade_id": row.trade_id, "action": row.action, "rule": row.rule, "reason": row.reason,
            "evidence": json.loads(row.evidence_json or "{}"), "status": row.status, "decided_by": row.decided_by, "decided_at": iso(row.decided_at),
            "decision_note": row.decision_note, "executed_at": iso(row.executed_at), "result": row.result, "expires_at": iso(row.expires_at),
            "created_at": iso(row.created_at)}
