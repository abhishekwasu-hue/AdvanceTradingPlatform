from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import TradeRecord, User
from app.db.session import get_session

router = APIRouter(prefix="/api", tags=["trading"])


class TradeRecordResponse(BaseModel):
    id: int
    mode: str
    symbol: str
    strategy_id: str
    direction: str
    entry_time: str
    entry_price: float
    quantity: int
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
    """Every persisted paper (and eventually live) trade for the logged-in user, most recent first."""
    rows = await session.scalars(
        select(TradeRecord).where(TradeRecord.user_id == user.id).order_by(TradeRecord.entry_time.desc())
    )
    return [TradeRecordResponse.from_record(r) for r in rows]


@router.get("/positions", response_model=List[TradeRecordResponse])
async def list_open_positions(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> List[TradeRecordResponse]:
    """Trades with no exit yet. Nothing currently monitors live prices to close a paper-execute
    fill automatically, so today every persisted trade shows up here until manual exit tracking
    is wired in - see the TradeRecord docstring.
    """
    rows = await session.scalars(
        select(TradeRecord)
        .where(TradeRecord.user_id == user.id, TradeRecord.exit_time.is_(None))
        .order_by(TradeRecord.entry_time.desc())
    )
    return [TradeRecordResponse.from_record(r) for r in rows]
