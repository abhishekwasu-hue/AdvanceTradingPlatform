import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import SignalDirection
from app.core.models import RiskConfig, Trade
from app.db.models import RiskSettingsRecord, SignalHistoryRecord, TradeRecord, User
from app.market_data.calendar import trading_day_start
from app.risk_engine import loss_streak
from app.risk_engine.risk_manager import TradingDayState
from app.signal_scoring.models import EnrichedSignal
from app.trading.trade_split import not_a_split


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
    contract_meta: Optional[dict] = None, exit_rules: Optional[str] = None, account_id: Optional[int] = None,
    holding: str = "INTRADAY", journal_source: Optional[str] = None,
) -> TradeRecord:
    """Writes a filled trade (paper or live) to this tenant's history, attributed to the user who
    executed it. Only called for a real logged-in user or a deployment acting on the tenant's
    behalf - anonymous /paper-execute calls stay in-memory only, per the console's "try it
    without an account" demo flow. LIVE fills carry the broker's entry and stop-loss order ids so
    the position monitor can cancel the stop on a target exit and reconciliation can match fills.

    Z6: the journal captures the fill right after the commit - the flag is read on this session's own connection
    (cached), the capture itself is queued and runs off the trade path, and it never raises or waits.
    `journal_source` says where the trade came from when the caller knows (webhook: "WEBHOOK"; public API: "API";
    Z3 approvals will pass "PROPOSAL", scans "SCAN").
    """
    record = TradeRecord(
        tenant_id=user.tenant_id,
        user_id=user.id,
        mode=mode,
        broker_order_id=broker_order_id,
        sl_order_id=sl_order_id,
        broker_account_id=account_id, deployment_id=deployment_id,
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
    record.initial_stop_loss = trade.stop_loss  # Phase J1: the stop the rules tighten from
    record.holding = holding                     # Phase AS: SWING positions are held overnight
    if exit_rules:
        record.exit_rules = exit_rules
    session.add(record)
    await session.commit()
    await session.refresh(record)
    from app.journal import capture as journal_capture   # local: the journal imports the trade models only
    await journal_capture.on_entry(session, record, source=journal_source,
                                   underlying_price=(contract_meta or {}).get("underlying_entry"))
    return record


async def persist_paper_trade(session: AsyncSession, user: User, trade: Trade) -> TradeRecord:
    return await persist_trade(session, user, trade, mode="PAPER")


async def build_trading_day_state(session: AsyncSession, user: User, *, now: Optional[datetime] = None) -> TradingDayState:
    """Derives this tenant's risk-engine counters fresh from its persisted trade history on every
    call, rather than tracking them in a process-memory counter. A single in-memory
    `TradingDayState` shared across a multi-tenant server never reflects one tenant's real
    activity (it drifts across tenants and resets only on process restart) - counting straight
    from the DB is slower but always correct, and correct across restarts and multiple server
    processes. Scoped by tenant_id, not user_id: risk limits (daily loss, trade count,
    consecutive losses) are an org-wide budget shared by everyone trading under that tenant.

    CP6-15: the losses in a row count only the current session's losses ("Pause until the next session", the default)
    or the losses since the trader's last resume ("Pause until I resume") - `loss_streak_count`. Before CP6-15 they
    counted the last closed trades of all time, so the pause never ended. `now` is for tests.
    """
    today_start = trading_day_start(now)   # P0.5 / T6: the Indian trading day, not UTC midnight
    tenant_id = user.tenant_id

    open_positions = await session.scalar(
        select(func.count()).select_from(TradeRecord)
        .where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))
    )
    # P0.5 / T6: open positions count at their last mark, so a day going wrong while positions are still open
    # reaches the daily-loss limit before the exits realise it.
    unrealised = await open_unrealised_pnl(session, tenant_id)
    trades_today = await session.scalar(
        select(func.count()).select_from(TradeRecord)
        .where(TradeRecord.tenant_id == tenant_id, TradeRecord.entry_time >= today_start,
               not_a_split())
    )
    daily_pnl = await session.scalar(
        select(func.coalesce(func.sum(TradeRecord.pnl), 0.0))
        .where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time >= today_start)
    )

    consecutive_losses = (await loss_streak_count(session, tenant_id, now=now))["losses_in_a_row"]

    return TradingDayState(
        trades_today=trades_today or 0, daily_pnl=float(daily_pnl or 0.0) + unrealised,
        consecutive_losses=consecutive_losses, open_positions=open_positions or 0,
    )


async def _pause_settings(session: AsyncSession, tenant_id: int) -> Tuple[str, Optional[datetime], Optional[int]]:
    """(the pause choice, the last resume, the saved limit - None = not set, DEF-0) from the tenant's risk settings."""
    row = (await session.execute(
        select(RiskSettingsRecord.loss_streak_pause, RiskSettingsRecord.loss_streak_resumed_at, RiskSettingsRecord.max_consecutive_losses)
        .where(RiskSettingsRecord.tenant_id == tenant_id)
    )).first()
    if row is None:
        return loss_streak.NEXT_SESSION, None, None
    mode = row[0] if row[0] in loss_streak.PAUSE_MODES else loss_streak.NEXT_SESSION
    return mode, _aware(row[1]), int(row[2]) if row[2] is not None else None


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


async def loss_streak_count(session: AsyncSession, tenant_id: int, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """CP6-15: the tenant's losses in a row as the risk engine counts them (app/risk_engine/loss_streak.py): closed
    trades from the session's start (next session) or after the last resume (until I resume), newest first, until the
    first winning trade. Returns the count with the choice, the window's start and the last resume."""
    mode, resumed_at, limit = await _pause_settings(session, tenant_id)
    session_start = trading_day_start(now)
    since = loss_streak.counted_since(mode, session_start=session_start, resumed_at=resumed_at)
    query = select(TradeRecord.pnl).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_not(None))
    if since is not None:
        query = query.where(TradeRecord.exit_time >= since if mode == loss_streak.NEXT_SESSION else TradeRecord.exit_time > since)
    # Enough rows to reach any saved limit; a window bounded by the session or the resume is short anyway.
    rows = await session.scalars(query.order_by(TradeRecord.exit_time.desc()).limit(max(20, (limit or 0) + 1)))
    return {"mode": mode, "losses_in_a_row": loss_streak.streak_of(rows), "counted_since": since, "resumed_at": resumed_at,
            "session_start": session_start, "saved_limit": limit}


async def loss_streak_status(session: AsyncSession, tenant_id: int, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """CP6-15: what the Risk limits page shows - the losses in a row now, the limit the risk engine applies (the saved
    one, within the platform ceilings), whether new entries are paused, when the pause ends and the last resume.
    Exits are never paused (ADR-0004)."""
    from app.platform.controls import clamp_config, risk_ceilings
    from app.risk_engine.routes import get_tenant_risk_config
    cfg = await get_tenant_risk_config(tenant_id, session) or RiskConfig()
    cfg, _ = clamp_config(cfg, await risk_ceilings(session))
    counted = await loss_streak_count(session, tenant_id, now=now)
    limit = int(cfg.max_consecutive_losses) if cfg.max_consecutive_losses is not None else None   # DEF-0: None = not set
    paused = loss_streak.is_paused(counted["losses_in_a_row"], limit)
    mode = counted["mode"]
    return {
        "mode": mode, "mode_label": loss_streak.PAUSE_LABELS[mode], "limit": limit, "losses_in_a_row": counted["losses_in_a_row"],
        "paused": paused, "can_resume": paused and mode == loss_streak.UNTIL_RESUME,
        "ends": ("at the next session" if mode == loss_streak.NEXT_SESSION else "when you resume") if paused else None,
        "resumed_at": counted["resumed_at"].isoformat() if counted["resumed_at"] is not None else None,
        "exits_blocked": False,
    }


async def open_unrealised_pnl(session: AsyncSession, tenant_id: int, *, strategy_id: Optional[str] = None) -> float:
    """Marked-to-market P&L of the open positions that have a mark (P0.5 / T6). A position the monitor has not
    priced yet contributes nothing - it is never guessed."""
    day_start = trading_day_start()
    # Only positions opened today, marked today: a swing position carried from an earlier day has an unrealised
    # P&L that is not today's move (and a large carried gain must not hide today's losses from the limit); a
    # mark older than today's session is stale and is not used either.
    query = select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None), TradeRecord.mark_price.is_not(None),
                                      TradeRecord.entry_time >= day_start, TradeRecord.mark_time >= day_start)
    if strategy_id is not None:
        query = query.where(TradeRecord.strategy_id == strategy_id)
    total = 0.0
    for trade in await session.scalars(query):
        sign = 1.0 if trade.direction == "LONG" else -1.0
        total += sign * (float(trade.mark_price) - float(trade.entry_price)) * float(trade.quantity)
    return round(total, 2)
