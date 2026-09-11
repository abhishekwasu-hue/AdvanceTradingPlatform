from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Trade
from app.db.models import TradeRecord


async def persist_paper_trade(session: AsyncSession, user_id: int, trade: Trade) -> TradeRecord:
    """Writes a filled paper trade to this user's history. Only called when a real logged-in
    user executed the trade - anonymous /paper-execute calls stay in-memory only, per the
    console's "try it without an account" demo flow.
    """
    record = TradeRecord(
        user_id=user_id,
        mode="PAPER",
        symbol=trade.symbol,
        strategy_id=trade.strategy_id,
        direction=trade.direction.value,
        entry_time=trade.entry_time,
        entry_price=trade.entry_price,
        quantity=trade.quantity,
        stop_loss=trade.stop_loss,
        target1=trade.target1,
        target2=trade.target2,
        charges=trade.charges,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record
