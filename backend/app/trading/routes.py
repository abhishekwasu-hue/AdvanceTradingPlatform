import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import verify_audit_chain
from app.auth.dependencies import get_current_user, require_role, require_trader
from app.db.models import AuditLogRecord, OrderEventRecord, OrderRecord, SignalHistoryRecord, TradeRecord, User
from app.db.session import get_session
from app.notifications.service import notify
from app.trading.analytics import AnalyticsSummary, build_analytics_summary
from app.trading.exit_logic import check_contract_exit
from app.trading.position_monitor import broker_for_trade, close_position

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
    target1: Optional[float]
    target2: Optional[float]
    exit_time: Optional[str]
    exit_price: Optional[float]
    exit_reason: Optional[str]
    pnl: Optional[float]
    charges: float
    charges_source: str = "ESTIMATED"
    broker_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None
    instrument_kind: str = "UNDERLYING"
    exchange: Optional[str] = None
    lot_size: Optional[int] = None
    expiry: Optional[str] = None
    option_position: Optional[str] = None
    premium_stop_pct: Optional[float] = None
    underlying_symbol: Optional[str] = None
    underlying_direction: Optional[str] = None
    underlying_stop_loss: Optional[float] = None
    underlying_target1: Optional[float] = None
    underlying_target2: Optional[float] = None
    expected_price: Optional[float] = None
    slippage: Optional[float] = None
    entry_latency_ms: Optional[int] = None
    # Phase H2: multi-leg grouping.
    leg_group_id: Optional[str] = None
    leg_role: Optional[str] = None
    option_strategy: Optional[str] = None
    group_meta: Optional[dict] = None

    @classmethod
    def from_record(cls, record: TradeRecord) -> "TradeRecordResponse":
        return cls(
            id=record.id, mode=record.mode, symbol=record.symbol, strategy_id=record.strategy_id,
            direction=record.direction, entry_time=record.entry_time.isoformat(),
            entry_price=record.entry_price, quantity=record.quantity, stop_loss=record.stop_loss,
            target1=record.target1, target2=record.target2,
            exit_time=record.exit_time.isoformat() if record.exit_time else None,
            exit_price=record.exit_price, exit_reason=record.exit_reason, pnl=record.pnl,
            charges=record.charges, charges_source=record.charges_source or "ESTIMATED",
            broker_order_id=record.broker_order_id, exit_order_id=record.exit_order_id,
            instrument_kind=record.instrument_kind or "UNDERLYING", exchange=record.exchange, lot_size=record.lot_size,
            expiry=record.expiry.isoformat() if record.expiry else None, option_position=record.option_position,
            premium_stop_pct=record.premium_stop_pct, underlying_symbol=record.underlying_symbol,
            underlying_direction=record.underlying_direction, underlying_stop_loss=record.underlying_stop_loss,
            underlying_target1=record.underlying_target1, underlying_target2=record.underlying_target2,
            expected_price=record.expected_price, slippage=record.slippage, entry_latency_ms=record.entry_latency_ms,
            leg_group_id=record.leg_group_id, leg_role=record.leg_role, option_strategy=record.option_strategy,
            group_meta=json.loads(record.group_meta) if record.group_meta else None,
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


class PositionGreeksResponse(BaseModel):
    """Phase H2 / master prompt section 25: Greeks per open option leg and per structure, from
    live premiums (IV solved from each leg's last price) and the underlying's spot."""
    as_of: str
    spot: dict
    legs: List[dict]
    groups: List[dict]
    net: dict
    skipped: List[dict]


@router.get("/positions/greeks", response_model=PositionGreeksResponse)
async def open_position_greeks(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> PositionGreeksResponse:
    from datetime import date, datetime, timezone
    from app.brokers.token_lifecycle import build_adapter, token_is_usable
    from app.db.models import BrokerCredentialRecord
    from app.execution.contract_execution import ContractExecutionError
    from app.instruments import master as instrument_master
    from app.option_chain.leg_greeks import compute_strategy_greeks
    from app.option_chain.models import OptionLegInput, OptionType

    rows = list(await session.scalars(
        select(TradeRecord).where(TradeRecord.tenant_id == user.tenant_id, TradeRecord.exit_time.is_(None),
                                  TradeRecord.instrument_kind == "OPTION").order_by(TradeRecord.id)
    ))
    stored = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == user.tenant_id)))
    usable = [r for r in stored if token_is_usable(r)]
    if rows and not usable:
        raise HTTPException(status_code=409, detail="Greeks need live premiums - no usable broker session (log in from Settings)")
    adapter = build_adapter(usable[0]) if usable else None
    today = date.today().isoformat()
    spots: dict = {}
    legs_out: List[dict] = []
    skipped: List[dict] = []
    inputs_by_group: dict = {}
    for trade in rows:
        if not trade.expiry or trade.underlying_symbol is None:
            skipped.append({"trade_id": trade.id, "reason": "no expiry/underlying on the trade"})
            continue
        strike = _strike_of(trade.symbol)
        right = "CE" if trade.symbol.rstrip().endswith("CE") or " CE " in trade.symbol else ("PE" if "PE" in trade.symbol else None)
        if strike is None or right is None:
            skipped.append({"trade_id": trade.id, "reason": f"cannot read strike/right from {trade.symbol}"})
            continue
        try:
            if trade.underlying_symbol not in spots:
                underlying = instrument_master.underlying_of(trade.underlying_symbol)
                exchange = instrument_master.INDEX_EXCHANGE.get(underlying, "NSE")
                spots[trade.underlying_symbol] = float(await adapter.get_ltp_for_symbol(trade.underlying_symbol, exchange))
            attempts = [trade.instrument_key] if trade.instrument_key and "|" in trade.instrument_key else []
            attempts.append(trade.symbol)
            premium = None
            for symbol in attempts:
                try:
                    premium = float(await adapter.get_ltp_for_symbol(symbol, trade.exchange or "NFO"))
                    break
                except Exception:  # noqa: BLE001
                    continue
            if premium is None or premium <= 0:
                raise ContractExecutionError("no premium quote")
        except Exception as exc:  # noqa: BLE001
            skipped.append({"trade_id": trade.id, "reason": f"quote unavailable: {exc}"})
            continue
        signed_qty = int(trade.quantity) if trade.direction == "LONG" else -int(trade.quantity)
        leg_input = OptionLegInput(strike=strike, option_type=OptionType.CALL if right == "CE" else OptionType.PUT, quantity=signed_qty,
                                   underlying_ltp=spots[trade.underlying_symbol], expiry=trade.expiry.isoformat(), option_ltp=premium, as_of=today)
        try:
            result = compute_strategy_greeks([leg_input]).legs[0]
        except Exception as exc:  # noqa: BLE001 - a premium below intrinsic cannot be solved
            skipped.append({"trade_id": trade.id, "reason": f"IV not solvable from premium {premium}: {exc}"})
            continue
        legs_out.append({"trade_id": trade.id, "symbol": trade.symbol, "leg_group_id": trade.leg_group_id, "leg_role": trade.leg_role,
                         "option_strategy": trade.option_strategy, "quantity": signed_qty, "premium": premium,
                         "implied_volatility": round(result.greeks.implied_volatility, 4), "delta": round(result.greeks.delta, 4),
                         "gamma": round(result.greeks.gamma, 6), "theta": round(result.greeks.theta, 4), "vega": round(result.greeks.vega, 4),
                         "position_delta": round(result.position_delta, 2), "position_gamma": round(result.position_gamma, 4),
                         "position_theta": round(result.position_theta, 2), "position_vega": round(result.position_vega, 2)})
        inputs_by_group.setdefault(trade.leg_group_id or f"single:{trade.id}", []).append(leg_input)
    groups = []
    for group_id, inputs in inputs_by_group.items():
        g = compute_strategy_greeks(inputs)
        groups.append({"leg_group_id": None if group_id.startswith("single:") else group_id, "legs": len(inputs),
                       "net_delta": round(g.net_delta, 2), "net_gamma": round(g.net_gamma, 4), "net_theta": round(g.net_theta, 2), "net_vega": round(g.net_vega, 2)})
    net = {"net_delta": round(sum(g["net_delta"] for g in groups), 2), "net_gamma": round(sum(g["net_gamma"] for g in groups), 4),
           "net_theta": round(sum(g["net_theta"] for g in groups), 2), "net_vega": round(sum(g["net_vega"] for g in groups), 2)}
    return PositionGreeksResponse(as_of=datetime.now(timezone.utc).isoformat(), spot=spots, legs=legs_out, groups=groups, net=net, skipped=skipped)


def _strike_of(tradingsymbol: str) -> Optional[float]:
    """'NIFTY 24500 CE 01 OCT 26' / 'NIFTY26O0124500CE' -> 24500."""
    import re
    m = re.search(r"\b(\d{3,6}(?:\.\d+)?)\s*(?:CE|PE)\b", tradingsymbol)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d{3,6}(?:\.\d+)?)(?:CE|PE)$", tradingsymbol.replace(" ", ""))
    return float(m.group(1)) if m else None


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
    # Phase F4: for an option position, the underlying's price too - the strategy's stop/targets
    # are on it; `current_price` is the contract's premium.
    underlying_price: Optional[float] = None


class MarkPriceResponse(BaseModel):
    closed: bool
    exit_reason: Optional[str] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None


@router.post("/positions/{trade_id}/mark-price", response_model=MarkPriceResponse)
async def mark_price(
    trade_id: int, request: MarkPriceRequest,
    user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
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

    hit = check_contract_exit(trade, request.current_price, request.underlying_price)
    if hit is None:
        return MarkPriceResponse(closed=False)

    reason, exit_price = hit
    # LIVE positions are squared off at the broker inside close_position (using the deployment's
    # broker session); a LIVE trade with no usable session stays open and the 409 says so.
    broker = await broker_for_trade(session, trade)
    outcome = await close_position(session, trade, exit_price, reason, broker=broker, user_id=user.id)
    if not outcome.closed:
        raise HTTPException(status_code=409, detail="; ".join(outcome.warnings) or "Position could not be closed")
    return MarkPriceResponse(closed=True, exit_reason=reason, exit_price=outcome.exit_price, pnl=outcome.pnl)


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


class AuditChainVerificationResponse(BaseModel):
    intact: bool
    first_broken_row_id: Optional[int] = None


@router.get("/audit-logs/verify", response_model=AuditChainVerificationResponse)
async def verify_audit_logs(
    user: User = Depends(require_role()),  # SUPER_ADMIN only - the chain spans every tenant
    session: AsyncSession = Depends(get_session),
) -> AuditChainVerificationResponse:
    """Recomputes the whole platform's audit-log hash chain (master prompt Section 48) and
    reports whether it is intact - a platform operator's tamper check, not a per-tenant view.
    """
    intact, first_broken_row_id = await verify_audit_chain(session)
    return AuditChainVerificationResponse(intact=intact, first_broken_row_id=first_broken_row_id)


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
    algo_tag: Optional[str] = None
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
            algo_tag=record.algo_tag, trade_id=record.trade_id, reasons=json.loads(record.reasons_json),
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
