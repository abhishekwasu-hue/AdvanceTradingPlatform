import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Trade
from app.db.models import SignalHistoryRecord, TradeRecord
from app.signal_scoring.models import EnrichedSignal


async def persist_signal_history(session: AsyncSession, user_id: int, enriched: EnrichedSignal) -> SignalHistoryRecord:
    """Logs every enriched signal a logged-in user generates, tradeable or not - a history of
    what the engine said, independent of whether it was ever executed.
    """
    signal = enriched.signal
    record = SignalHistoryRecord(
        user_id=user_id,
        strategy_id=signal.strategy_id,
        symbol=signal.symbol,
        direction=signal.direction.value,
        signal_time=signal.timestamp,
        entry=signal.entry,
        stop_loss=signal.stop_loss,
        target1=signal.target1,
        target2=signal.target2,
        risk_reward=signal.risk_reward,
        score=enriched.composite_score,
        grade=enriched.grade.value,
        reasons_json=json.dumps(signal.reasons),
        timeframe_combo=signal.timeframe_combo,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


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
