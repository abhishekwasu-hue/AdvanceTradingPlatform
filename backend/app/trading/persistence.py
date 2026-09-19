import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Trade
from app.db.models import SignalHistoryRecord, TradeRecord
from app.risk_engine.risk_manager import TradingDayState
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


async def build_trading_day_state(session: AsyncSession, user_id: int) -> TradingDayState:
    """Derives this user's risk-engine counters fresh from their persisted trade history on every
    call, rather than tracking them in a process-memory counter. A single in-memory
    `TradingDayState` shared across a multi-user server never reflects one user's real activity
    (it drifts across users and resets only on process restart) - counting straight from the DB
    is slower but always correct, and correct across restarts and multiple server processes.
    """
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    open_positions = await session.scalar(
        select(func.count()).select_from(TradeRecord)
        .where(TradeRecord.user_id == user_id, TradeRecord.exit_time.is_(None))
    )
    trades_today = await session.scalar(
        select(func.count()).select_from(TradeRecord)
        .where(TradeRecord.user_id == user_id, TradeRecord.entry_time >= today_start)
    )
    daily_pnl = await session.scalar(
        select(func.coalesce(func.sum(TradeRecord.pnl), 0.0))
        .where(TradeRecord.user_id == user_id, TradeRecord.exit_time >= today_start)
    )

    recent_closed = await session.scalars(
        select(TradeRecord)
        .where(TradeRecord.user_id == user_id, TradeRecord.exit_time.is_not(None))
        .order_by(TradeRecord.exit_time.desc())
        .limit(20)
    )
    consecutive_losses = 0
    for trade_record in recent_closed:
        if trade_record.pnl is not None and trade_record.pnl <= 0:
            consecutive_losses += 1
        else:
            break

    return TradingDayState(
        trades_today=trades_today or 0, daily_pnl=daily_pnl or 0.0,
        consecutive_losses=consecutive_losses, open_positions=open_positions or 0,
    )
