import json
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import AuditLogRecord, OrderEventRecord, OrderRecord, SignalHistoryRecord, TradeRecord, User
from app.db.session import get_session
from app.execution.paper_broker import PaperBroker
from app.notifications.service import notify
from app.trading.analytics import AnalyticsSummary, build_analytics_summary
from app.trading.exit_logic import check_exit

router = APIRouter(prefix="/api", tags=["trading"])


class TradeRecordResponse(BaseModel):
    id: int
    mode: str
    symbol: str
    strategy_id: str
    direction: str
    entry_time: str
    entry_price: float
    quantity: float
    stop_loss: float
    target1: float
    target2: Optional[float]
    exit_time: Optional[str]
    exit_price: Optional[float]
    exit_reason: Optional[str]
    pnl: Optional[float]
    charges: float

    @classmethod
    def from_record(cls, record: TradeRecord) -> "TradeRecordResponse":
        return cls(
            id=record.id, mode=record.mode, symbol=record.symbol, strategy_id=record.strategy_id,
            direction=record.direction, entry_time=record.entry_time.isoformat(),
            entry_price=record.entry_price, quantity=record.quantity, stop_loss=record.stop_loss,
            target1=record.target1, target2=record.target2,
            exit_time=record.exit_time.isoformat() if record.exit_time else None,
            exit_price=record.exit_price, exit_reason=record.exit_reason, pnl=record.pnl,
            charges=record.charges,
        )


@router.get("/trades", response_model=List[TradeRecordResponse])
async def list_trades(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> List[TradeRecordResponse]:
    """Every persisted paper (and eventually live) trade for the logged-in user's tenant, most
    recent first."""
    rows = await session.scalars(
        select(TradeRecord).where(TradeRecord.tenant_id == user.tenant_id).order_by(TradeRecord.entry_time.desc())
    )
    return [TradeRecordResponse.from_record(r) for r in rows]


@router.get("/positions", response_model=List[TradeRecordResponse])
async def list_open_positions(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> List[TradeRecordResponse]:
    """Trades with no exit yet. See POST /api/positions/{id}/mark-price for how a position
    actually gets closed - there's no live broker feed yet, so exit tracking is a manual/periodic
    price check rather than continuous monitoring.
    """
    rows = await session.scalars(
        select(TradeRecord)
        .where(TradeRecord.tenant_id == user.tenant_id, TradeRecord.exit_time.is_(None))
        .order_by(TradeRecord.entry_time.desc())
    )
    return [TradeRecordResponse.from_record(r) for r in rows]


class SignalHistoryResponse(BaseModel):
    id: int
    strategy_id: str
    symbol: str
    direction: str
    signal_time: str
    entry: Optional[float]
    stop_loss: Optional[float]
    target1: Optional[float]
    target2: Optional[float]
    risk_reward: Optional[float]
    score: int
    grade: str
    reasons: List[str]
    timeframe_combo: str
    created_at: str

    @classmethod
    def from_record(cls, record: SignalHistoryRecord) -> "SignalHistoryResponse":
        return cls(
            id=record.id, strategy_id=record.strategy_id, symbol=record.symbol,
            direction=record.direction, signal_time=record.signal_time.isoformat(),
            entry=record.entry, stop_loss=record.stop_loss, target1=record.target1,
            target2=record.target2, risk_reward=record.risk_reward, score=record.score,
            grade=record.grade, reasons=json.loads(record.reasons_json),
            timeframe_combo=record.timeframe_combo, created_at=record.created_at.isoformat(),
        )


@router.get("/signal-history", response_model=List[SignalHistoryResponse])
async def list_signal_history(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session), limit: int = 100,
) -> List[SignalHistoryResponse]:
    """Every enriched signal this user has generated (tradeable or not), most recent first -
    independent of the Trade Journal, which only has rows for signals that were actually filled.
    """
    rows = await session.scalars(
        select(SignalHistoryRecord)
        .where(SignalHistoryRecord.tenant_id == user.tenant_id)
        .order_by(SignalHistoryRecord.created_at.desc())
        .limit(limit)
    )
    return [SignalHistoryResponse.from_record(r) for r in rows]


class MarkPriceRequest(BaseModel):
    current_price: float


class MarkPriceResponse(BaseModel):
    closed: bool
    exit_reason: Optional[str] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None


@router.post("/positions/{trade_id}/mark-price", response_model=MarkPriceResponse)
async def mark_price(
    trade_id: int, request: MarkPriceRequest,
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> MarkPriceResponse:
    """Checks a supplied current price against this open position's stop loss/targets and closes
    it if hit. There's no live broker market-data stream yet, so this is the honest replacement
    for continuous monitoring: the console (or, later, a scheduled job once a broker quote feed
    exists) calls this periodically with the latest price rather than a background task silently
    watching prices that don't actually exist yet.
    """
    trade = await session.get(TradeRecord, trade_id)
    if trade is None or trade.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Unknown position")
    if trade.exit_time is not None:
        raise HTTPException(status_code=409, detail="Position is already closed")

    outcome = check_exit(trade, request.current_price)
    if outcome is None:
        return MarkPriceResponse(closed=False)

    reason, exit_price = outcome
    broker = PaperBroker()
    direction_sign = 1 if trade.direction == "LONG" else -1
    gross_pnl = direction_sign * (exit_price - trade.entry_price) * trade.quantity
    charges = broker.estimate_round_trip_costs(trade.entry_price, exit_price, trade.quantity)

    trade.exit_price = round(exit_price, 2)
    trade.exit_time = datetime.now(timezone.utc)
    trade.exit_reason = reason
    trade.charges = charges
    trade.pnl = round(gross_pnl - charges, 2)
    await session.commit()

    await notify(
        session, user.tenant_id, NotificationType.EXIT,
        title=f"{trade.direction} position closed: {trade.symbol}",
        message=f"{reason} at {trade.exit_price}, P&L {trade.pnl}",
        severity=NotificationSeverity.WARNING if trade.pnl is not None and trade.pnl < 0 else NotificationSeverity.INFO,
        user_id=user.id, related_trade_id=trade.id,
    )

    return MarkPriceResponse(closed=True, exit_reason=reason, exit_price=trade.exit_price, pnl=trade.pnl)


@router.get("/analytics/summary", response_model=AnalyticsSummary)
async def analytics_summary(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> AnalyticsSummary:
    """Win rate / P&L breakdown by strategy and symbol, computed from this tenant's full trade
    history (GET /api/trades) - closed trades only for win-rate/profit-factor purposes.
    """
    rows = list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == user.tenant_id)))
    return build_analytics_summary(rows)


class AuditLogResponse(BaseModel):
    id: int
    event: str
    detail: str
    created_at: str

    @classmethod
    def from_record(cls, record: AuditLogRecord) -> "AuditLogResponse":
        return cls(id=record.id, event=record.event, detail=record.detail, created_at=record.created_at.isoformat())


@router.get("/audit-logs", response_model=List[AuditLogResponse])
async def list_audit_logs(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session), limit: int = 200,
) -> List[AuditLogResponse]:
    """This user's own security-relevant events (register, login, credential stored/deleted,
    broker authenticated/failed, ...), most recent first. Never another user's - System Logs is
    a per-account audit trail, not a global admin view.
    """
    rows = await session.scalars(
        select(AuditLogRecord)
        .where(AuditLogRecord.user_id == user.id)
        .order_by(AuditLogRecord.created_at.desc())
        .limit(limit)
    )
    return [AuditLogResponse.from_record(r) for r in rows]


class OrderResponse(BaseModel):
    id: int
    mode: str
    strategy_id: str
    symbol: str
    direction: str
    quantity: float
    status: str
    idempotency_key: Optional[str]
    broker_order_id: Optional[str]
    trade_id: Optional[int]
    reasons: List[str]
    created_at: str
    updated_at: str

    @classmethod
    def from_record(cls, record: OrderRecord) -> "OrderResponse":
        return cls(
            id=record.id, mode=record.mode, strategy_id=record.strategy_id, symbol=record.symbol,
            direction=record.direction, quantity=record.quantity, status=record.status,
            idempotency_key=record.idempotency_key, broker_order_id=record.broker_order_id,
            trade_id=record.trade_id, reasons=json.loads(record.reasons_json),
            created_at=record.created_at.isoformat(), updated_at=record.updated_at.isoformat(),
        )


class OrderEventResponse(BaseModel):
    id: int
    from_status: Optional[str]
    to_status: str
    detail: str
    created_at: str

    @classmethod
    def from_record(cls, record: OrderEventRecord) -> "OrderEventResponse":
        return cls(
            id=record.id, from_status=record.from_status, to_status=record.to_status,
            detail=record.detail, created_at=record.created_at.isoformat(),
        )


@router.get("/orders", response_model=List[OrderResponse])
async def list_orders(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session), limit: int = 200,
) -> List[OrderResponse]:
    """This tenant's full order ledger - every paper/live execution attempt, rejected or filled,
    most recent first. Unlike GET /api/trades (only rows that actually filled), this is the
    complete audit trail the formal order state machine produces."""
    rows = await session.scalars(
        select(OrderRecord)
        .where(OrderRecord.tenant_id == user.tenant_id)
        .order_by(OrderRecord.created_at.desc())
        .limit(limit)
    )
    return [OrderResponse.from_record(r) for r in rows]


@router.get("/orders/{order_id}/events", response_model=List[OrderEventResponse])
async def list_order_events(
    order_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> List[OrderEventResponse]:
    """The full append-only state-transition history for one order, oldest first."""
    order = await session.get(OrderRecord, order_id)
    if order is None or order.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Unknown order")

    rows = await session.scalars(
        select(OrderEventRecord).where(OrderEventRecord.order_id == order_id).order_by(OrderEventRecord.created_at.asc())
    )
    return [OrderEventResponse.from_record(r) for r in rows]
