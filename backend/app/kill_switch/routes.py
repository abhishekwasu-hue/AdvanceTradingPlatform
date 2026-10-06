import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_mfa_session, require_role, require_trader
from app.core.enums import KillSwitchScope, NotificationSeverity, NotificationType, OrderStatus
from app.db.models import KillSwitchRecord, OrderRecord, TradeRecord, User
from app.db.session import get_session
from app.execution.order_persistence import transition_order
from app.execution.order_state_machine import TERMINAL_STATUSES
from app.kill_switch import checks
from app.notifications.service import notify
from app.brokers.token_lifecycle import build_adapter, token_is_usable
from app.db.models import BrokerCredentialRecord
from app.trading.position_monitor import broker_for_trade, close_position

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kill-switch", tags=["kill-switch"])


class KillSwitchStateResponse(BaseModel):
    scope: str
    strategy_id: Optional[str] = None
    engaged: bool
    reason: str
    engaged_at: Optional[str] = None
    disengaged_at: Optional[str] = None

    @classmethod
    def from_record(cls, scope: KillSwitchScope, record: Optional[KillSwitchRecord], strategy_id: Optional[str] = None) -> "KillSwitchStateResponse":
        if record is None:
            return cls(scope=scope.value, strategy_id=strategy_id, engaged=False, reason="")
        return cls(
            scope=record.scope, strategy_id=record.strategy_id, engaged=record.engaged, reason=record.reason,
            engaged_at=record.engaged_at.isoformat() if record.engaged_at else None,
            disengaged_at=record.disengaged_at.isoformat() if record.disengaged_at else None,
        )


class KillSwitchStatusResponse(BaseModel):
    global_switch: KillSwitchStateResponse
    tenant_switch: KillSwitchStateResponse
    strategy_switches: List[KillSwitchStateResponse]


@router.get("/status", response_model=KillSwitchStatusResponse)
async def get_status(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> KillSwitchStatusResponse:
    global_record = await checks.get_switch(session, KillSwitchScope.GLOBAL, None)
    tenant_record = await checks.get_switch(session, KillSwitchScope.TENANT, user.tenant_id)
    strategy_rows = await session.scalars(
        select(KillSwitchRecord).where(
            KillSwitchRecord.tenant_id == user.tenant_id, KillSwitchRecord.scope == KillSwitchScope.STRATEGY.value,
        )
    )
    return KillSwitchStatusResponse(
        global_switch=KillSwitchStateResponse.from_record(KillSwitchScope.GLOBAL, global_record),
        tenant_switch=KillSwitchStateResponse.from_record(KillSwitchScope.TENANT, tenant_record),
        strategy_switches=[
            KillSwitchStateResponse.from_record(KillSwitchScope.STRATEGY, r, r.strategy_id) for r in strategy_rows
        ],
    )


class KillSwitchRequest(BaseModel):
    reason: str = ""


@router.post("/global/engage", response_model=KillSwitchStateResponse)
async def engage_global(
    request: KillSwitchRequest,
    user: User = Depends(require_role()),  # SUPER_ADMIN only - require_role() with no roles listed
    session: AsyncSession = Depends(get_session), _: User = Depends(require_mfa_session),
) -> KillSwitchStateResponse:
    """Platform-wide emergency stop. Blocks every new order across every tenant until
    disengaged - reserved for a platform operator, never something a tenant's own users can flip."""
    record = await checks.engage(session, KillSwitchScope.GLOBAL, None, user, request.reason)
    await write_audit_log(session, None, user.id, "kill_switch_global_engaged", request.reason)
    # Phase M / V4.10: a platform-wide stop is an incident by definition - open the record now so
    # the post-mortem has its start time and audit range.
    from app.incidents.service import open_incident
    await open_incident(session, title=f"Global kill switch engaged: {request.reason or 'no reason given'}"[:200], severity="EMERGENCY",
                        summary="Opened automatically when the global kill switch was engaged.", source="kill_switch", user=user, commit=False)
    await session.commit()
    return KillSwitchStateResponse.from_record(KillSwitchScope.GLOBAL, record)


@router.post("/global/disengage", response_model=KillSwitchStateResponse)
async def disengage_global(
    user: User = Depends(require_role()), session: AsyncSession = Depends(get_session),
    _: User = Depends(require_mfa_session),
) -> KillSwitchStateResponse:
    record = await checks.disengage(session, KillSwitchScope.GLOBAL, None)
    await write_audit_log(session, None, user.id, "kill_switch_global_disengaged")
    await session.commit()
    return KillSwitchStateResponse.from_record(KillSwitchScope.GLOBAL, record)


@router.post("/tenant/engage", response_model=KillSwitchStateResponse)
async def engage_tenant(
    request: KillSwitchRequest, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
) -> KillSwitchStateResponse:
    """Blocks every new order for this user's own tenant - the "stop everything for my account"
    panic button, available to any logged-in user of that tenant."""
    record = await checks.engage(session, KillSwitchScope.TENANT, user.tenant_id, user, request.reason)
    await write_audit_log(session, user.tenant_id, user.id, "kill_switch_tenant_engaged", request.reason)
    await session.commit()
    return KillSwitchStateResponse.from_record(KillSwitchScope.TENANT, record)


@router.post("/tenant/disengage", response_model=KillSwitchStateResponse)
async def disengage_tenant(
    user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
) -> KillSwitchStateResponse:
    record = await checks.disengage(session, KillSwitchScope.TENANT, user.tenant_id)
    await write_audit_log(session, user.tenant_id, user.id, "kill_switch_tenant_disengaged")
    await session.commit()
    return KillSwitchStateResponse.from_record(KillSwitchScope.TENANT, record)


@router.post("/strategy/{strategy_id}/engage", response_model=KillSwitchStateResponse)
async def engage_strategy(
    strategy_id: str, request: KillSwitchRequest,
    user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
) -> KillSwitchStateResponse:
    """Blocks new orders for one strategy within this tenant only - other strategies keep trading."""
    record = await checks.engage(session, KillSwitchScope.STRATEGY, user.tenant_id, user, request.reason, strategy_id)
    await write_audit_log(
        session, user.tenant_id, user.id, "kill_switch_strategy_engaged", f"{strategy_id}: {request.reason}",
    )
    await session.commit()
    return KillSwitchStateResponse.from_record(KillSwitchScope.STRATEGY, record, strategy_id)


@router.post("/strategy/{strategy_id}/disengage", response_model=KillSwitchStateResponse)
async def disengage_strategy(
    strategy_id: str, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
) -> KillSwitchStateResponse:
    record = await checks.disengage(session, KillSwitchScope.STRATEGY, user.tenant_id, strategy_id)
    await write_audit_log(session, user.tenant_id, user.id, "kill_switch_strategy_disengaged", strategy_id)
    await session.commit()
    return KillSwitchStateResponse.from_record(KillSwitchScope.STRATEGY, record, strategy_id)


class EmergencyExitRequest(BaseModel):
    reason: str = "Emergency exit"
    # Current price per symbol, required to close a position - there is no live broker quote
    # feed wired in yet (see mark-price's own docstring for the same honest limitation), so an
    # open position with no supplied price is skipped rather than closed at a fabricated price.
    prices: Dict[str, float] = {}


class EmergencyExitResponse(BaseModel):
    tenant_kill_switch_engaged: bool
    cancelled_order_ids: List[int]
    closed_trade_ids: List[int]
    skipped_symbols: List[str]
    # P0.5 / T4: what happened at the broker for LIVE orders that were still working.
    broker_cancelled: List[str] = []
    broker_cancel_failures: List[str] = []


async def _live_brokers(session: AsyncSession, tenant_id: int) -> List:
    """Every usable broker session of the organisation (an order record does not name its broker)."""
    adapters = []
    for record in await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id)):
        if token_is_usable(record):
            try:
                adapters.append(build_adapter(record))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Emergency exit: could not build adapter for credential %s: %s", record.id, exc)
    return adapters


async def _cancel_at_brokers(adapters: List, broker_order_id: str) -> Optional[str]:
    """Tries every usable session; the broker that knows the order cancels it. Returns the broker name or None."""
    for adapter in adapters:
        try:
            response = await adapter.cancel_order(broker_order_id)
            if (response.status or "").upper() not in ("REJECTED", "ERROR", "UNKNOWN"):
                return adapter.name
        except Exception as exc:  # noqa: BLE001 - another session may own the order
            logger.info("Emergency exit: %s could not cancel %s: %s", adapter.name, broker_order_id, exc)
    return None


@router.post("/emergency-exit", response_model=EmergencyExitResponse)
async def emergency_exit(
    request: EmergencyExitRequest, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
) -> EmergencyExitResponse:
    """The full emergency-stop sequence for this tenant: (1) engage the tenant kill switch so no
    new order can enter, (2) cancel every order still in a non-terminal state, (3) close every
    open position a price was supplied for, (4) leave an audit trail. Positions with no supplied
    price are left open and reported back rather than guessed at.
    """
    await checks.engage(session, KillSwitchScope.TENANT, user.tenant_id, user, request.reason)

    pending_orders = await session.scalars(
        select(OrderRecord).where(
            OrderRecord.tenant_id == user.tenant_id,
            OrderRecord.status.notin_([s.value for s in TERMINAL_STATUSES]),
        )
    )
    cancelled_ids: List[int] = []
    broker_cancelled: List[str] = []
    broker_failures: List[str] = []
    adapters = None
    for order in pending_orders:
        # P0.5 / T4: a LIVE order still working at the broker is cancelled *there* first; the platform record
        # follows. One that cannot be cancelled is reported, and the record still closes so nothing new enters.
        if order.mode == "LIVE" and order.broker_order_id:
            if adapters is None:
                adapters = await _live_brokers(session, user.tenant_id)
            broker_name = await _cancel_at_brokers(adapters, order.broker_order_id)
            if broker_name:
                broker_cancelled.append(f"{order.broker_order_id}@{broker_name}")
            else:
                broker_failures.append(order.broker_order_id)
        await transition_order(session, order, OrderStatus.CANCELLED, detail=f"Emergency exit: {request.reason}")
        cancelled_ids.append(order.id)

    open_trades = list(
        await session.scalars(
            select(TradeRecord).where(TradeRecord.tenant_id == user.tenant_id, TradeRecord.exit_time.is_(None))
        )
    )
    # P0.5 / T4: shorts (written options, short futures/equity) are bought back before any long is sold, so the
    # account is never left naked half-way through the exit.
    open_trades.sort(key=lambda t: (0 if (t.direction == "SHORT" or getattr(t, "leg_role", None) == "SHORT") else 1, t.id))
    closed_ids: List[int] = []
    skipped_symbols: List[str] = []
    for trade in open_trades:
        price = request.prices.get(trade.symbol)
        if price is None:
            skipped_symbols.append(trade.symbol)
            continue
        # LIVE positions are squared off at the broker inside close_position; one that can't be
        # (no usable broker session, exit order rejected) is left open and reported, never marked
        # closed on paper while it may still exist at the exchange.
        broker = await broker_for_trade(session, trade)
        outcome = await close_position(session, trade, price, "Emergency Exit", broker=broker, user_id=user.id)
        if outcome.closed:
            closed_ids.append(trade.id)
        else:
            skipped_symbols.append(f"{trade.symbol} ({'; '.join(outcome.warnings) or 'not closed'})")

    await write_audit_log(
        session, user.tenant_id, user.id, "emergency_exit_triggered",
        (
            f"reason={request.reason}; cancelled_orders={len(cancelled_ids)}; broker_cancelled={broker_cancelled}; "
            f"broker_cancel_failures={broker_failures}; closed_trades={len(closed_ids)}; skipped_symbols={skipped_symbols}"
        ),
    )
    await session.commit()

    await notify(
        session, user.tenant_id, NotificationType.EMERGENCY_EXIT,
        title="Emergency exit triggered", severity=NotificationSeverity.EMERGENCY, user_id=user.id,
        message=(
            f"reason={request.reason}; cancelled_orders={len(cancelled_ids)}; "
            f"closed_trades={len(closed_ids)}; skipped_symbols={skipped_symbols}"
        ),
    )

    return EmergencyExitResponse(
        tenant_kill_switch_engaged=True, cancelled_order_ids=cancelled_ids,
        closed_trade_ids=closed_ids, skipped_symbols=skipped_symbols,
        broker_cancelled=broker_cancelled, broker_cancel_failures=broker_failures,
    )
