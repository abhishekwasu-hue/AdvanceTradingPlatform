from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import OrderStatus
from app.core.models import Signal
from app.db.models import OrderEventRecord, OrderRecord, User
from app.execution.order_state_machine import assert_valid_transition


async def get_order_by_idempotency_key(session: AsyncSession, tenant_id: int, idempotency_key: str) -> Optional[OrderRecord]:
    """Looks up a previous order submitted under this exact (tenant, idempotency_key) pair, so a
    retried request (network retry, duplicate webhook delivery) can replay that order's recorded
    outcome instead of re-running risk checks and placing a second real/paper order.
    """
    return await session.scalar(
        select(OrderRecord).where(OrderRecord.tenant_id == tenant_id, OrderRecord.idempotency_key == idempotency_key)
    )


async def create_order(
    session: AsyncSession, user: User, *, mode: str, strategy_id: str, signal: Signal,
    idempotency_key: Optional[str] = None,
) -> OrderRecord:
    """Opens a new order at CREATED and immediately logs that first event. One row per execution
    *attempt* - callers still transition it to REJECTED/FAILED/etc. even when nothing ever fills.
    """
    order = OrderRecord(
        tenant_id=user.tenant_id, user_id=user.id, idempotency_key=idempotency_key, mode=mode,
        strategy_id=strategy_id, symbol=signal.symbol, direction=signal.direction.value,
        status=OrderStatus.CREATED.value, signal_json=signal.model_dump_json(),
    )
    session.add(order)
    await session.flush()
    session.add(OrderEventRecord(order_id=order.id, from_status=None, to_status=OrderStatus.CREATED.value, detail=""))
    await session.commit()
    await session.refresh(order)
    return order


async def transition_order(
    session: AsyncSession, order: OrderRecord, to_status: OrderStatus, detail: str = "",
) -> OrderRecord:
    """Validates and applies one state-machine transition, appending an immutable event row.
    Raises InvalidOrderTransition (a programming error, not a user-facing one) if the calling
    code attempts a transition the lifecycle doesn't allow.
    """
    from_status = OrderStatus(order.status)
    assert_valid_transition(from_status, to_status)
    order.status = to_status.value
    session.add(OrderEventRecord(order_id=order.id, from_status=from_status.value, to_status=to_status.value, detail=detail))
    await session.commit()
    await session.refresh(order)
    return order
