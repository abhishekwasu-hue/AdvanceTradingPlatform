import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import SignalDirection
from app.core.models import Trade
from app.db.models import SignalHistoryRecord, TradeRecord, User
from app.risk_engine.risk_manager import TradingDayState
from app.signal_scoring.models import EnrichedSignal


async def persist_signal_history(session: AsyncSession, user: User, enriched: EnrichedSignal) -> SignalHistoryRecord:
    """Logs every enriched signal a logged-in user generates, tradeable or not - a history of
    what the engine said, independent of whether it was ever executed. Visible to the whole
    tenant (tenant_id), attributed to the user who generated it (user_id).
    """
    signal = enriched.signal
    record = SignalHistoryRecord(
        tenant_id=user.tenant_id,
        user_id=user.id,
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


async def persist_trade(
    session: AsyncSession, user: User, trade: Trade, *, mode: str = "PAPER",
    broker_order_id: Optional[str] = None, sl_order_id: Optional[str] = None, deployment_id: Optional[int] = None,
    contract_meta: Optional[dict] = None,
) -> TradeRecord:
    """Writes a filled trade (paper or live) to this tenant's history, attributed to the user who
    executed it. Only called for a real logged-in user or a deployment acting on the tenant's
    behalf - anonymous /paper-execute calls stay in-memory only, per the console's "try it
    without an account" demo flow. LIVE fills carry the broker's entry and stop-loss order ids so
    the position monitor can cancel the stop on a target exit and reconciliation can match fills.
    """
    record = TradeRecord(
        tenant_id=user.tenant_id,
        user_id=user.id,
        mode=mode,
        broker_order_id=broker_order_id,
        sl_order_id=sl_order_id,
        deployment_id=deployment_id,
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
        expected_price=trade.expected_price,
        entry_latency_ms=trade.entry_latency_ms,
    )
    if trade.expected_price:
        # Signed against the trade: positive = filled worse than the signal expected.
        sign = 1 if trade.direction == SignalDirection.LONG else -1
        record.slippage = round(sign * (trade.entry_price - trade.expected_price), 4)
    for key, value in (contract_meta or {}).items():
        if hasattr(record, key):
            setattr(record, key, value)
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def persist_paper_trade(session: AsyncSession, user: User, trade: Trade) -> TradeRecord:
    return await persist_trade(session, user, trade, mode="PAPER")


async def build_trading_day_state(session: AsyncSession, user: User) -> TradingDayState:
    """Derives this tenant's risk-engine counters fresh from its persisted trade history on every
    call, rather than tracking them in a process-memory counter. A single in-memory
    `TradingDayState` shared across a multi-tenant server never reflects one tenant's real
    activity (it drifts across tenants and resets only on process restart) - counting straight
    from the DB is slower but always correct, and correct across restarts and multiple server
    processes. Scoped by tenant_id, not user_id: risk limits (daily loss, trade count,
    consecutive losses) are an org-wide budget shared by everyone trading under that tenant.
    """
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    tenant_id = user.tenant_id

    open_positions = await session.scalar(
        select(func.count()).select_from(TradeRecord)
        .where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))
    )
    trades_today = await session.scalar(
        select(func.count()).select_from(TradeRecord)
        .where(TradeRecord.tenant_id == tenant_id, TradeRecord.entry_time >= today_start)
    )
    daily_pnl = await session.scalar(
        select(func.coalesce(func.sum(TradeRecord.pnl), 0.0))
        .where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time >= today_start)
    )

    recent_closed = await session.scalars(
        select(TradeRecord)
        .where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_not(None))
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
