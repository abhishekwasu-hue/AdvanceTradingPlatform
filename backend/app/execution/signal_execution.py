import json
import logging
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ExecutionMode, NotificationSeverity, NotificationType, OrderStatus
from app.core.logging_config import bind_log_context, update_log_context
from app.core.models import RiskConfig, Signal
from app.db.models import OrderRecord, User
from app.execution.order_persistence import create_order, execution_result_from_order, transition_order
from app.execution.router import ExecutionResult, OrderRouter
from app.instruments.registry import get_contract_spec
from app.kill_switch.checks import active_kill_switch_reasons
from app.notifications.service import notify
from app.risk_engine.routes import get_tenant_risk_config
from app.trading.persistence import build_trading_day_state, persist_paper_trade

logger = logging.getLogger(__name__)


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
    with bind_log_context(
        tenant_id=user.tenant_id, strategy_id=strategy_id,
        signal_ref=f"{signal.symbol}@{signal.timestamp.isoformat()}",
    ):
        order, was_newly_created = await create_order(
            session, user, mode=mode, strategy_id=strategy_id, signal=signal, idempotency_key=idempotency_key,
        )
        update_log_context(order_id=order.id)
        if not was_newly_created:
            # A genuine race: another concurrent request for this same idempotency_key won and
            # already committed its own order before this one's pre-check could see it (see
            # create_order's own docstring). `order` here is that winning row, already fully
            # decided one way or another - replay its outcome rather than attempting to run this
            # already-settled order through the pipeline a second time.
            logger.info("Idempotency race detected - returning existing order's outcome")
            return execution_result_from_order(order), order

        logger.info("Order created (%s, %s)", signal.direction.value, mode)
        order = await transition_order(session, order, OrderStatus.VALIDATING, detail="Signal received")

        kill_switch_reasons = await active_kill_switch_reasons(session, user.tenant_id, strategy_id)
        if kill_switch_reasons:
            order.reasons_json = json.dumps(kill_switch_reasons)
            order = await transition_order(
                session, order, OrderStatus.REJECTED, detail="; ".join(kill_switch_reasons)
            )
            logger.warning("Order rejected by kill switch: %s", "; ".join(kill_switch_reasons))
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
        contract_spec = get_contract_spec(signal.symbol)
        order_router = OrderRouter(
            mode=ExecutionMode.PAPER, risk_config=effective_risk_config,
            exchange=contract_spec.exchange if contract_spec else "NSE",
        )
        result: ExecutionResult = await order_router.execute(signal, state)

        order.reasons_json = json.dumps(result.reasons)
        if not result.executed:
            if result.system_failure:
                # The broker call itself errored (see OrderRouter.execute) - not a business
                # decision either way, so this is FAILED, not REJECTED, and a CRITICAL
                # SYSTEM_FAILURE notification (the same category app/reconciliation/ and
                # app/brokers/routes.py already use for a broker-side exception), not a routine
                # rejection notice.
                order = await transition_order(session, order, OrderStatus.FAILED, detail="; ".join(result.reasons))
                logger.error("Order failed - broker call raised: %s", "; ".join(result.reasons))
                await notify(
                    session, user.tenant_id, NotificationType.SYSTEM_FAILURE,
                    title=f"Order failed: {signal.symbol}", message="; ".join(result.reasons),
                    severity=NotificationSeverity.CRITICAL, user_id=user.id, related_order_id=order.id,
                )
                return result, order

            order = await transition_order(session, order, OrderStatus.REJECTED, detail="; ".join(result.reasons))
            is_daily_loss = any("daily loss limit" in r.lower() for r in result.reasons)
            logger.warning("Order rejected by risk engine: %s", "; ".join(result.reasons))
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
        logger.info("Order filled")

        if result.trade is not None:
            trade_record = await persist_paper_trade(session, user, result.trade)
            order.trade_id = trade_record.id
            update_log_context(trade_id=trade_record.id)
            order = await transition_order(
                session, order, OrderStatus.POSITION_OPEN, detail=f"Position opened (trade #{trade_record.id})"
            )
            logger.info("Position opened")
            await notify(
                session, user.tenant_id, NotificationType.ENTRY,
                title=f"{result.trade.direction.value} entry filled: {result.trade.symbol}",
                message="; ".join(result.reasons), severity=NotificationSeverity.INFO,
                user_id=user.id, related_trade_id=trade_record.id, related_order_id=order.id,
            )

        return result, order
