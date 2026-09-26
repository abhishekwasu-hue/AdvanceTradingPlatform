import json
import logging
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface
from app.core.enums import ExecutionMode, NotificationSeverity, NotificationType, OrderStatus
from app.core.logging_config import bind_log_context, update_log_context
from app.core.models import RiskConfig, Signal
from app.db.models import OrderRecord, Tenant, User
from app.execution.order_persistence import create_order, execution_result_from_order, transition_order
from app.execution.router import ExecutionResult, OrderRouter
from app.instruments.registry import get_contract_spec
from app.kill_switch.checks import active_kill_switch_reasons
from app.notifications.service import notify
from app.plans.limits import live_allowed, tenant_is_active
from app.risk_engine.routes import get_tenant_risk_config
from app.trading.persistence import build_trading_day_state, persist_trade

logger = logging.getLogger(__name__)


async def execute_signal_for_user(
    session: AsyncSession, user: User, *, mode: str, strategy_id: str, signal: Signal,
    idempotency_key: Optional[str] = None, risk_config: Optional[RiskConfig] = None,
    broker: Optional[BrokerInterface] = None, deployment_id: Optional[int] = None,
) -> Tuple[ExecutionResult, OrderRecord]:
    """The full logged-in execution path a pre-formed `Signal` goes through, regardless of where
    it came from (the platform's own strategy engine via /paper-execute, a TradingView webhook
    alert, or the autonomous worker acting on a deployment): create the formal order record,
    check kill switches, run the risk engine, route to the paper broker or - for mode "LIVE" with
    an authenticated `broker` adapter - the real broker, persist the fill (with the broker's entry
    and protective stop-loss order ids), and notify. One sequence, one state machine, for every
    caller. A LIVE request without a broker is REJECTED on the order trail rather than raised:
    the worker's token gate normally prevents it, but if it ever happens it must be visible.
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
        tenant = await session.get(Tenant, user.tenant_id)
        if tenant is not None and not tenant_is_active(tenant):
            kill_switch_reasons.append(f"Organisation is {tenant.status}: no new orders")
        if mode == ExecutionMode.LIVE.value and not live_allowed(tenant):
            kill_switch_reasons.append("Plan does not include live trading - order refused")
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

        execution_mode = ExecutionMode(mode)
        if execution_mode == ExecutionMode.LIVE and broker is None:
            reason = "Live trading not configured: no authenticated broker adapter for this tenant"
            order.reasons_json = json.dumps([reason])
            order = await transition_order(session, order, OrderStatus.REJECTED, detail=reason)
            logger.error("LIVE order rejected - no broker adapter supplied")
            return ExecutionResult(executed=False, reasons=[reason]), order

        state = await build_trading_day_state(session, user)
        contract_spec = get_contract_spec(signal.symbol)
        order_router = OrderRouter(
            mode=execution_mode, risk_config=effective_risk_config, broker=broker,
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
        if result.broker_order_id:
            order.broker_order_id = result.broker_order_id
        venue = broker.name if (execution_mode == ExecutionMode.LIVE and broker is not None) else "paper broker"
        order = await transition_order(session, order, OrderStatus.SUBMITTED, detail=f"Submitted to {venue}")
        order = await transition_order(session, order, OrderStatus.PENDING, detail="Awaiting fill")
        order = await transition_order(
            session, order, OrderStatus.FILLED,
            detail="Paper fill" if execution_mode == ExecutionMode.PAPER else f"Live fill {result.broker_order_id}",
        )
        logger.info("Order filled")

        if result.trade is not None:
            trade_record = await persist_trade(
                session, user, result.trade, mode=execution_mode.value, broker_order_id=result.broker_order_id,
                sl_order_id=result.sl_order_id, deployment_id=deployment_id,
            )
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
            if result.sl_failed:
                # A live position with no broker-side stop is the single most dangerous state
                # this platform can be in - CRITICAL, immediately, to the whole tenant.
                await notify(
                    session, user.tenant_id, NotificationType.SYSTEM_FAILURE,
                    title=f"No broker-side stop-loss on {result.trade.symbol}",
                    message="The entry filled but the protective SL-M order failed. The position monitor will "
                            "enforce the stop in software; place a manual stop at the broker as a backup.",
                    severity=NotificationSeverity.CRITICAL, user_id=user.id,
                    related_trade_id=trade_record.id, related_order_id=order.id,
                )

        return result, order
