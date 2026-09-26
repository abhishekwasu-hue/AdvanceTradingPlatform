"""Phase I1: the risk hierarchy (master prompt V3.4 / V4.5, section 17).

Limits live at scopes - GLOBAL -> TENANT -> USER -> ACCOUNT -> STRATEGY -> INSTRUMENT - and an
order is checked against *every* limit that applies to it; when several scopes carry the same
limit type, the strictest (smallest) wins. Each check writes a `risk_events` row (PASS, WARN at
80% of the limit, BLOCK), so "why was this order refused" and "how close are we" are both
answered from one append-only table.

The evaluator runs *after* the risk engine has sized the order (it needs the quantity) and
*before* anything reaches a broker - `OrderRouter.execute` calls it through `pre_place_check`,
the multi-leg executor calls it directly. It never raises into the order path: an evaluation
error blocks the order ("any critical uncertainty must fail safe").

Consequences beyond BLOCK_ORDER: a MAX_STRATEGY_LOSS breach also engages the strategy kill
switch (STOP_STRATEGY), a TENANT-scope MAX_DAILY_LOSS breach engages the tenant kill switch -
both idempotent, both audited, both raising a CRITICAL notification - so the next signal is
stopped at the door rather than re-measured every cycle.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import KillSwitchScope, NotificationSeverity, NotificationType, RiskAction, RiskLimitType, RiskScope
from app.db.models import RiskEventRecord, RiskLimitRecord, TradeRecord, User
from app.kill_switch.checks import engage
from app.notifications.service import notify

logger = logging.getLogger(__name__)

WARN_FRACTION = 0.8

CURRENCY_TYPES = {RiskLimitType.MAX_DAILY_LOSS, RiskLimitType.MAX_STRATEGY_LOSS, RiskLimitType.MAX_LOSS_PER_TRADE, RiskLimitType.MAX_ORDER_VALUE}


@dataclass
class RiskContext:
    tenant_id: int
    user_id: int
    strategy_id: str
    symbol: str
    quantity: float
    entry: Optional[float]
    stop_loss: Optional[float]
    capital: float
    account_id: Optional[int] = None
    mode: str = "PAPER"
    order_id: Optional[int] = None
    # Multi-leg: the structure's max loss per unit replaces |entry - stop|.
    risk_per_unit: Optional[float] = None


@dataclass
class RiskCheck:
    rule: RiskLimitRecord
    current: float
    limit: float
    status: str          # PASS / WARN / BLOCK
    action: RiskAction
    reason: str


@dataclass
class RiskVerdict:
    allowed: bool
    reasons: List[str] = field(default_factory=list)
    checks: List[RiskCheck] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _scope_ids(ctx: RiskContext) -> Dict[RiskScope, str]:
    return {
        RiskScope.GLOBAL: "", RiskScope.TENANT: "", RiskScope.USER: str(ctx.user_id),
        RiskScope.ACCOUNT: str(ctx.account_id) if ctx.account_id is not None else None,
        RiskScope.STRATEGY: ctx.strategy_id, RiskScope.INSTRUMENT: ctx.symbol.upper(),
    }


async def applicable_limits(session: AsyncSession, ctx: RiskContext) -> List[RiskLimitRecord]:
    """Every enabled limit whose scope matches this order, GLOBAL rows included."""
    rows = list(await session.scalars(select(RiskLimitRecord).where(
        RiskLimitRecord.enabled.is_(True),
        (RiskLimitRecord.tenant_id == ctx.tenant_id) | (RiskLimitRecord.scope == RiskScope.GLOBAL.value),
    )))
    ids = _scope_ids(ctx)
    out = []
    for row in rows:
        wanted = ids.get(RiskScope(row.scope))
        if wanted is None:
            continue
        if row.scope in (RiskScope.GLOBAL.value, RiskScope.TENANT.value) or row.scope_id == wanted:
            out.append(row)
    return out


def strictest(limits: List[RiskLimitRecord]) -> Dict[str, RiskLimitRecord]:
    """One limit per type: the smallest value across scopes (all types are maxima)."""
    best: Dict[str, RiskLimitRecord] = {}
    for row in limits:
        cur = best.get(row.limit_type)
        if cur is None or row.limit_value < cur.limit_value:
            best[row.limit_type] = row
    return best


async def _measure(session: AsyncSession, ctx: RiskContext, limit_type: RiskLimitType, rule: RiskLimitRecord) -> Tuple[float, str]:
    """Current value of the quantity `limit_type` bounds, and a short label for it."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if limit_type == RiskLimitType.MAX_DAILY_LOSS:
        pnl = await session.scalar(select(func.coalesce(func.sum(TradeRecord.pnl), 0.0)).where(
            TradeRecord.tenant_id == ctx.tenant_id, TradeRecord.exit_time >= today_start))
        return float(-min(0.0, pnl or 0.0)), "realised loss today"
    if limit_type == RiskLimitType.MAX_STRATEGY_LOSS:
        pnl = await session.scalar(select(func.coalesce(func.sum(TradeRecord.pnl), 0.0)).where(
            TradeRecord.tenant_id == ctx.tenant_id, TradeRecord.strategy_id == ctx.strategy_id, TradeRecord.exit_time >= today_start))
        return float(-min(0.0, pnl or 0.0)), f"{ctx.strategy_id} realised loss today"
    if limit_type == RiskLimitType.MAX_LOSS_PER_TRADE:
        per_unit = ctx.risk_per_unit if ctx.risk_per_unit is not None else (abs(ctx.entry - ctx.stop_loss) if ctx.entry is not None and ctx.stop_loss is not None else 0.0)
        return float(per_unit * ctx.quantity), "loss at the stop"
    if limit_type == RiskLimitType.MAX_ORDER_VALUE:
        return float((ctx.entry or 0.0) * ctx.quantity), "order value"
    if limit_type == RiskLimitType.MAX_POSITION_QUANTITY:
        return float(ctx.quantity), "quantity"
    if limit_type == RiskLimitType.MAX_OPEN_POSITIONS:
        query = select(func.count()).select_from(TradeRecord).where(TradeRecord.tenant_id == ctx.tenant_id, TradeRecord.exit_time.is_(None))
        if rule.scope == RiskScope.STRATEGY.value:
            query = query.where(TradeRecord.strategy_id == ctx.strategy_id)
        elif rule.scope == RiskScope.INSTRUMENT.value:
            query = query.where(TradeRecord.symbol == ctx.symbol)
        elif rule.scope == RiskScope.USER.value:
            query = query.where(TradeRecord.user_id == ctx.user_id)
        return float((await session.scalar(query)) or 0) + 1.0, "open positions after this order"
    if limit_type == RiskLimitType.MAX_TRADES_PER_DAY:
        query = select(func.count()).select_from(TradeRecord).where(TradeRecord.tenant_id == ctx.tenant_id, TradeRecord.entry_time >= today_start)
        if rule.scope == RiskScope.STRATEGY.value:
            query = query.where(TradeRecord.strategy_id == ctx.strategy_id)
        elif rule.scope == RiskScope.USER.value:
            query = query.where(TradeRecord.user_id == ctx.user_id)
        return float((await session.scalar(query)) or 0) + 1.0, "trades today including this one"
    if limit_type == RiskLimitType.MAX_CAPITAL_ALLOCATION_PCT:
        value = (ctx.entry or 0.0) * ctx.quantity
        return float(value / ctx.capital * 100.0) if ctx.capital > 0 else 100.0, "share of capital"
    return 0.0, limit_type.value


def _fmt(limit_type: RiskLimitType, value: float) -> str:
    if limit_type in CURRENCY_TYPES:
        return f"{value:,.0f}"
    if limit_type == RiskLimitType.MAX_CAPITAL_ALLOCATION_PCT:
        return f"{value:.1f}%"
    return f"{value:g}"


async def evaluate(session: AsyncSession, ctx: RiskContext, *, user: Optional[User] = None) -> RiskVerdict:
    """Check the order against the hierarchy, record every check, act on breaches. Commits."""
    verdict = RiskVerdict(allowed=True)
    try:
        limits = await applicable_limits(session, ctx)
    except Exception as exc:  # noqa: BLE001 - fail safe
        logger.exception("Risk hierarchy could not be read")
        verdict.allowed = False
        verdict.reasons.append(f"Risk limits unavailable ({type(exc).__name__}) - order refused")
        return verdict
    if not limits:
        return verdict
    by_type = strictest(limits)
    for type_name, rule in by_type.items():
        limit_type = RiskLimitType(type_name)
        try:
            current, label = await _measure(session, ctx, limit_type, rule)
        except Exception as exc:  # noqa: BLE001
            current, label = float("inf"), f"{limit_type.value} unmeasurable ({type(exc).__name__})"
        if current > rule.limit_value:
            status, action, severity = "BLOCK", RiskAction.BLOCK_ORDER, NotificationSeverity.CRITICAL
            reason = f"{limit_type.value} ({rule.scope.lower()}): {label} {_fmt(limit_type, current)} exceeds limit {_fmt(limit_type, rule.limit_value)}"
        elif current >= rule.limit_value * WARN_FRACTION:
            status, action, severity = "WARN", RiskAction.WARN, NotificationSeverity.WARNING
            reason = f"{limit_type.value} ({rule.scope.lower()}): {label} {_fmt(limit_type, current)} is within 20% of limit {_fmt(limit_type, rule.limit_value)}"
        else:
            status, action, severity = "PASS", RiskAction.ALLOW, NotificationSeverity.INFO
            reason = f"{limit_type.value} ({rule.scope.lower()}): {label} {_fmt(limit_type, current)} within limit {_fmt(limit_type, rule.limit_value)}"
        if status == "BLOCK" and limit_type == RiskLimitType.MAX_STRATEGY_LOSS:
            action = RiskAction.STOP_STRATEGY
        if status == "BLOCK" and limit_type == RiskLimitType.MAX_DAILY_LOSS and rule.scope in (RiskScope.TENANT.value, RiskScope.GLOBAL.value):
            action = RiskAction.GLOBAL_KILL if rule.scope == RiskScope.GLOBAL.value else RiskAction.STOP_STRATEGY
        check = RiskCheck(rule=rule, current=current if current != float("inf") else -1.0, limit=rule.limit_value, status=status, action=action, reason=reason)
        verdict.checks.append(check)
        session.add(RiskEventRecord(
            tenant_id=ctx.tenant_id, account_id=ctx.account_id, strategy_id=ctx.strategy_id, symbol=ctx.symbol,
            rule_id=rule.id, rule_type=limit_type.value, scope=rule.scope, current_value=check.current, limit_value=rule.limit_value,
            severity=severity.value, action=action.value, status=status, reason=reason[:300], order_id=ctx.order_id,
            metadata_json=json.dumps({"mode": ctx.mode, "quantity": ctx.quantity, "entry": ctx.entry}),
        ))
        if status == "BLOCK":
            verdict.allowed = False
            verdict.reasons.append(reason)
        elif status == "WARN":
            verdict.notes.append(reason)
    await session.commit()

    # Consequences: stop the strategy / the tenant so the next signal is refused at the door.
    for check in verdict.checks:
        if check.status != "BLOCK" or user is None:
            continue
        limit_type = RiskLimitType(check.rule.limit_type)
        try:
            if limit_type == RiskLimitType.MAX_STRATEGY_LOSS:
                await engage(session, KillSwitchScope.STRATEGY, ctx.tenant_id, user, f"Risk limit: {check.reason}", strategy_id=ctx.strategy_id)
                await notify(session, ctx.tenant_id, NotificationType.RISK_REJECTION, title=f"Strategy stopped: {ctx.strategy_id}",
                             message=check.reason, severity=NotificationSeverity.CRITICAL, user_id=ctx.user_id)
            elif limit_type == RiskLimitType.MAX_DAILY_LOSS and check.rule.scope in (RiskScope.TENANT.value, RiskScope.GLOBAL.value):
                await engage(session, KillSwitchScope.TENANT, ctx.tenant_id, user, f"Risk limit: {check.reason}")
                await notify(session, ctx.tenant_id, NotificationType.DAILY_LOSS_LIMIT, title="Daily loss limit reached - trading stopped",
                             message=check.reason, severity=NotificationSeverity.CRITICAL, user_id=ctx.user_id)
        except Exception:  # noqa: BLE001 - the block already holds; the switch is belt and braces
            logger.exception("Risk consequence failed for %s", limit_type.value)
    return verdict
