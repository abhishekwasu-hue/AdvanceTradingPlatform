import json
import logging
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import OrderStatus
from app.core.models import Signal
from app.db.models import OrderEventRecord, OrderRecord, User
from app.execution.order_state_machine import assert_valid_transition
from app.execution.router import ExecutionResult

logger = logging.getLogger(__name__)


async def get_order_by_idempotency_key(session: AsyncSession, tenant_id: int, idempotency_key: str) -> Optional[OrderRecord]:
    """Looks up a previous order submitted under this exact (tenant, idempotency_key) pair, so a
    retried request (network retry, duplicate webhook delivery) can replay that order's recorded
    outcome instead of re-running risk checks and placing a second real/paper order.
    """
    return await session.scalar(
        select(OrderRecord).where(OrderRecord.tenant_id == tenant_id, OrderRecord.idempotency_key == idempotency_key)
    )


def execution_result_from_order(order: OrderRecord) -> ExecutionResult:
    """Reconstructs the ExecutionResult an already-decided order represents, from its persisted
    fields alone - the one place that knows how to turn a stored OrderRecord back into a response,
    used by every idempotent-replay path (a caller's own pre-check lookup, and the race window
    `create_order` itself catches below).

    Known nuance, confirmed directly under real concurrent load (8 simultaneous requests against
    a real Postgres instance, one shared idempotency key): a *race-detected* replay (the loser of
    `create_order`'s IntegrityError race, not the ordinary "request came in well after the first
    one finished" replay path) reads whichever status the winning order happens to be in *at that
    exact moment* - which can still be an intermediate, non-terminal status if the winner's own
    pipeline (risk checks, broker call, notifications) hasn't finished yet. `executed=False` from
    such a response means "not yet decided", not "rejected" - a caller that cares about the final
    outcome should treat this the way any async job's still-pending status is treated, and confirm
    via `GET /api/orders/{id}` once settled, rather than trusting this snapshot as final. The one
    guarantee this function and the race handling around it *do* provide unconditionally is the
    one that actually matters for correctness: no duplicate order or trade is ever created, and no
    request crashes, no matter how many concurrent callers share one idempotency key.
    """
    return ExecutionResult(
        executed=order.status in (OrderStatus.FILLED.value, OrderStatus.POSITION_OPEN.value),
        reasons=json.loads(order.reasons_json),
    )


async def create_order(
    session: AsyncSession, user: User, *, mode: str, strategy_id: str, signal: Signal,
    idempotency_key: Optional[str] = None,
) -> Tuple[OrderRecord, bool]:
    """Opens a new order at CREATED and immediately logs that first event, returning
    `(order, was_newly_created)`. One row per execution *attempt* - callers still transition it to
    REJECTED/FAILED/etc. even when nothing ever fills.

    `was_newly_created` is False in the one case a caller's own pre-check (`
    get_order_by_idempotency_key` before ever calling this) can still miss: two concurrent
    requests for the same `(tenant, idempotency_key)` both passing that check before either one
    commits - confirmed directly (not just reasoned about): two concurrent
    `execute_signal_for_user` calls sharing an idempotency key reliably produced an unhandled
    `sqlalchemy.exc.IntegrityError` on this table's `(tenant_id, idempotency_key)` unique
    constraint before this fix. That constraint is the real backstop; this catches the
    IntegrityError it raises and returns whichever row actually won the race, instead of letting
    the loser's request crash with an unhandled 500.
    """
    # Captured before any DB operation: `session.rollback()` below expires every object already
    # loaded into this session, and `user` was loaded by the caller before create_order was ever
    # called - accessing `user.tenant_id` after a rollback would trigger an implicit lazy-reload
    # that async SQLAlchemy can't perform outside an explicit await, raising MissingGreenlet
    # instead of the tenant_id it looks like it should just return (confirmed directly: this was
    # the actual first version of this fix, and it failed exactly that way).
    tenant_id = user.tenant_id

    order = OrderRecord(
        tenant_id=tenant_id, user_id=user.id, idempotency_key=idempotency_key, mode=mode,
        strategy_id=strategy_id, symbol=signal.symbol, direction=signal.direction.value,
        status=OrderStatus.CREATED.value, signal_json=signal.model_dump_json(),
    )
    session.add(order)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing = await get_order_by_idempotency_key(session, tenant_id, idempotency_key) if idempotency_key else None
        if existing is None:
            raise
        logger.warning("Idempotency race detected for key %s - returning the order that won it", idempotency_key)
        return existing, False

    session.add(OrderEventRecord(order_id=order.id, from_status=None, to_status=OrderStatus.CREATED.value, detail=""))
    await session.commit()
    await session.refresh(order)
    return order, True


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
    logger.debug("Order %s -> %s: %s", from_status.value, to_status.value, detail)
    return order
