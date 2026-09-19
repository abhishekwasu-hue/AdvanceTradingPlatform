import json
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ExecutionMode, NotificationSeverity, NotificationType, OrderStatus
from app.core.models import RiskConfig, Signal
from app.db.models import OrderRecord, User
from app.execution.order_persistence import create_order, transition_order
from app.execution.router import ExecutionResult, OrderRouter
from app.kill_switch.checks import active_kill_switch_reasons
from app.notifications.service import notify
from app.risk_engine.routes import get_tenant_risk_config
from app.trading.persistence import build_trading_day_state, persist_paper_trade


async def execute_signal_for_user(
    session: AsyncSession, user: User, *, mode: str, strategy_id: str, signal: Signal,
    idempotency_key: Optional[str] = None, risk_config: Optional[RiskConfig] = None,
) -> Tuple[ExecutionResult, OrderRecord]:
    """The full logged-in execution path a pre-formed `Signal` goes through, regardless of where
    it came from (the platform's own strategy engine via /paper-execute, or an external source
    like a TradingView webhook alert): create the formal order record, check kill switches,
    run the risk engine, route to the paper broker, persist a fill, and notify - the exact same
    sequence and state-machine transitions POST /api/strategies/{id}/paper-execute already uses
    for a logged-in caller, factored out here so a second real caller (the TradingView webhook)
    doesn't have to re-implement it by hand.
    """
    order = await create_order(
        session, user, mode=mode, strategy_id=strategy_id, signal=signal, idempotency_key=idempotency_key,
    )
    order = await transition_order(session, order, OrderStatus.VALIDATING, detail="Signal received")

    kill_switch_reasons = await active_kill_switch_reasons(session, user.tenant_id, strategy_id)
    if kill_switch_reasons:
        order = await transition_order(session, order, OrderStatus.REJECTED, detail="; ".join(kill_switch_reasons))
        await notify(
            session, user.tenant_id, NotificationType.REJECTION,
            title=f"Order rejected: {signal.symbol}", message="; ".join(kill_switch_reasons),
            severity=NotificationSeverity.WARNING, user_id=user.id, related_order_id=order.id,
        )
        return ExecutionResult(executed=False, reasons=kill_switch_reasons), order

    order = await transition_order(session, order, OrderStatus.RISK_CHECK, detail="Running risk checks")

    effective_risk_config = risk_config
    if effective_risk_config is None:
        effective_risk_config = await get_tenant_risk_config(user.tenant_id, session)
    effective_risk_config = effective_risk_config or RiskConfig()

    state = await build_trading_day_state(session, user)
    order_router = OrderRouter(mode=ExecutionMode.PAPER, risk_config=effective_risk_config)
    result: ExecutionResult = await order_router.execute(signal, state)

    order.reasons_json = json.dumps(result.reasons)
    if not result.executed:
        order = await transition_order(session, order, OrderStatus.REJECTED, detail="; ".join(result.reasons))
        is_daily_loss = any("daily loss limit" in r.lower() for r in result.reasons)
        await notify(
            session, user.tenant_id,
            NotificationType.DAILY_LOSS_LIMIT if is_daily_loss else NotificationType.RISK_REJECTION,
            title=f"Order rejected: {signal.symbol}", message="; ".join(result.reasons),
            severity=NotificationSeverity.CRITICAL if is_daily_loss else NotificationSeverity.WARNING,
            user_id=user.id, related_order_id=order.id,
        )
        return result, order

    if result.trade is not None:
        order.quantity = result.trade.quantity
    order = await transition_order(session, order, OrderStatus.SUBMITTED, detail="Submitted to paper broker")
    order = await transition_order(session, order, OrderStatus.PENDING, detail="Awaiting fill")
    order = await transition_order(session, order, OrderStatus.FILLED, detail="Paper fill")

    if result.trade is not None:
        trade_record = await persist_paper_trade(session, user, result.trade)
        order.trade_id = trade_record.id
        order = await transition_order(
            session, order, OrderStatus.POSITION_OPEN, detail=f"Position opened (trade #{trade_record.id})"
        )
        await notify(
            session, user.tenant_id, NotificationType.ENTRY,
            title=f"{result.trade.direction.value} entry filled: {result.trade.symbol}",
            message="; ".join(result.reasons), severity=NotificationSeverity.INFO,
            user_id=user.id, related_trade_id=trade_record.id, related_order_id=order.id,
        )

    return result, order
