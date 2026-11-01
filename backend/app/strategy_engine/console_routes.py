"""The console's strategy endpoints: the strategy list, one strategy, a signal, an enriched signal and the console's
PAPER execute.

M55 Q1: moved from app/main.py (which now only includes routers) as it was - only the annotations are in built-in
generics; included at the end of the router list in the same order as before, so every path, method, dependency
and the match order are unchanged (tests/test_m55_q1_route_table.py).
"""
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_current_user_optional, require_trader
from app.brokers.models import OptionChain
from app.core.enums import ExecutionMode, OrderStatus
from app.core.models import OHLCVBar, RiskConfig, Signal, StrategyInfo, bars_to_dataframe
from app.core.rate_limit import analysis_rate_limit
from app.custom_strategies.resolver import custom_strategy_info, resolve_strategy
from app.db.models import CustomStrategyRecord, User
from app.db.session import get_session
from app.execution.order_persistence import get_order_by_idempotency_key
from app.execution.router import LiveTradingNotConfigured, OrderRouter
from app.execution.signal_execution import execute_signal_for_user
from app.kill_switch.checks import is_global_kill_switch_engaged
from app.risk_engine.risk_manager import TradingDayState
from app.signal_scoring.engine import enrich_signal
from app.signal_scoring.models import EnrichedSignal
from app.strategy_engine.registry import registry
from app.trading.persistence import persist_signal_history

router = APIRouter()

_default_risk_config = RiskConfig()


class SignalRequest(BaseModel):
    symbol: str
    candles: dict[str, list[OHLCVBar]]


class PaperExecuteRequest(SignalRequest):
    risk_config: RiskConfig | None = None
    idempotency_key: str | None = None

    @field_validator("idempotency_key")
    @classmethod
    def not_reserved(cls, value: str | None) -> str | None:
        """N1 X2 (review L1): `intent:` keys belong to the execution core's Auto PAPER orders - a caller may not claim one."""
        if value is not None and value.strip().lower().startswith("intent:"):
            raise ValueError("idempotency keys starting with 'intent:' are reserved")
        return value


class EnrichSignalRequest(SignalRequest):
    option_chain: OptionChain | None = None
    swing_window: int = 3


class PaperExecuteResponse(BaseModel):
    signal: Signal
    executed: bool
    reasons: list[str]
    order_id: int | None = None
    idempotent_replay: bool = False


@router.get("/api/strategies", response_model=list[StrategyInfo])
async def list_strategies(
    user: User | None = Depends(get_current_user_optional), session: AsyncSession = Depends(get_session),
) -> list[StrategyInfo]:
    """Every inbuilt strategy, plus - when logged in - this user's own saved custom strategies
    (id "custom:<id>"), so the rest of the console treats the two identically.
    """
    strategies = [s.info() for s in registry.list_all()]
    if user is not None:
        rows = await session.scalars(
            select(CustomStrategyRecord)
            .where(CustomStrategyRecord.tenant_id == user.tenant_id)
            .order_by(CustomStrategyRecord.created_at.desc())
        )
        strategies.extend(custom_strategy_info(r) for r in rows)
    return strategies


@router.get("/api/strategies/{strategy_id}", response_model=StrategyInfo)
async def get_strategy(
    strategy_id: str,
    user: User | None = Depends(get_current_user_optional),
    session: AsyncSession = Depends(get_session),
) -> StrategyInfo:
    try:
        strategy = await resolve_strategy(strategy_id, user, session)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return strategy.info()


def _candle_frames(candles: dict[str, list[OHLCVBar]], need: str | None = None) -> dict[str, Any]:
    """The request's candles as frames. M55 O2 (found by the tenancy sweep once these routes were signed in): an empty
    list, or a missing timeframe the route reads directly, is a 422 the caller can act on - it used to be a 500."""
    empty = sorted(tf for tf, bars in candles.items() if not bars)
    if empty:
        raise HTTPException(status_code=422, detail=f"No candles for {', '.join(empty)}")
    if need is not None and need not in candles:
        raise HTTPException(status_code=422, detail=f"Candles for {need} are needed")
    return {tf: bars_to_dataframe(bars) for tf, bars in candles.items()}


@router.post("/api/strategies/{strategy_id}/signal", response_model=Signal, dependencies=[Depends(analysis_rate_limit)])
async def generate_signal(
    strategy_id: str, request: SignalRequest,
    user: User = Depends(get_current_user),          # M55 O2: signed in, within the analysis limit
    session: AsyncSession = Depends(get_session),
) -> Signal:
    try:
        strategy = await resolve_strategy(strategy_id, user, session)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    data = _candle_frames(request.candles)
    return strategy.analyze(data, request.symbol)


@router.post("/api/strategies/{strategy_id}/signal/enrich", response_model=EnrichedSignal, dependencies=[Depends(analysis_rate_limit)])
async def generate_and_enrich_signal(
    strategy_id: str, request: EnrichSignalRequest,
    user: User = Depends(get_current_user),          # M55 O2: signed in, within the analysis limit
    session: AsyncSession = Depends(get_session),
) -> EnrichedSignal:
    """Generates a signal the same way /signal does, then cross-checks it against market
    structure, support/resistance, candlestick patterns, volume, and (if supplied) option chain
    bias to produce the weighted composite score and the "why this trade" breakdown. Logged-in
    calls are also appended to this user's signal history - see GET /api/signal-history.
    """
    try:
        strategy = await resolve_strategy(strategy_id, user, session)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    data = _candle_frames(request.candles, need=strategy.timeframes[0])
    signal = strategy.analyze(data, request.symbol)

    primary_tf = strategy.timeframes[0]
    ltf_df = data[primary_tf]
    enriched = enrich_signal(signal, ltf_df, option_chain=request.option_chain, swing_window=request.swing_window)

    if user is not None:
        await persist_signal_history(session, user, enriched)

    return enriched


@router.post("/api/strategies/{strategy_id}/paper-execute", response_model=PaperExecuteResponse, dependencies=[Depends(analysis_rate_limit)])
async def paper_execute(
    strategy_id: str, request: PaperExecuteRequest,
    user: User = Depends(require_trader),            # M55 O2: a signed-in trader (the console order path), within the analysis limit
    session: AsyncSession = Depends(get_session),
) -> PaperExecuteResponse:
    """M55 O2: signed in only (the anonymous demo branch below stays, unreached). With a valid
    Authorization header the call persists the fill to this user's trade history
    - see GET /api/trades and /api/positions. When the request doesn't explicitly pass a
    risk_config, a logged-in user's own saved risk settings apply (see GET/PUT
    /api/risk-settings) instead of the platform default.

    Every logged-in call also creates a formal order-lifecycle record (`app/db/models.py::
    OrderRecord` + an append-only `OrderEventRecord` trail per transition - see
    `app/execution/order_state_machine.py`), whether or not it ends up filling, and passing the
    same `idempotency_key` twice replays the first attempt's recorded outcome instead of
    re-running risk checks and placing a second order - see GET /api/orders/{id}/events.
    """
    try:
        strategy = await resolve_strategy(strategy_id, user, session)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    data = _candle_frames(request.candles)
    signal = strategy.analyze(data, request.symbol)

    if user is not None and request.idempotency_key:
        existing = await get_order_by_idempotency_key(session, user.tenant_id, request.idempotency_key)
        if existing is not None:
            return PaperExecuteResponse(
                signal=Signal.model_validate_json(existing.signal_json),
                executed=existing.status in (OrderStatus.FILLED.value, OrderStatus.POSITION_OPEN.value),
                reasons=json.loads(existing.reasons_json), order_id=existing.id, idempotent_replay=True,
            )

    if user is None:
        # No tenant to check a TENANT/STRATEGY switch against on the anonymous demo path - only
        # a platform-wide GLOBAL kill switch applies.
        global_reason = await is_global_kill_switch_engaged(session)
        if global_reason:
            reasons = [f"Global kill switch engaged: {global_reason}".rstrip(": ")]
            return PaperExecuteResponse(signal=signal, executed=False, reasons=reasons)

    if user is not None:
        from app.execution import approval
        # Z3 (review M1): a persisted entry needs a trading role and the trading:write scope, like every other order route.
        if not approval.can_trade(user):
            raise HTTPException(status_code=403, detail="Insufficient role for this action")
        if await approval.required(session, user.tenant_id, user_id=user.id):   # M68 PR-6: his own "Ask me first" too
            # Z3 / BT2: approval-only - the console's signal (priced from the candles this request sent) becomes an ENTRY
            # proposal like any other; Approve re-quotes it at the live price, so a client-sent price never fills.
            if not signal.is_tradeable or signal.entry is None:
                return PaperExecuteResponse(signal=signal, executed=False, reasons=list(signal.reasons) or ["No trade signal"])
            key = f"console:{user.id}:{request.idempotency_key}" if request.idempotency_key else f"console:{user.id}:{uuid.uuid4().hex}"
            try:
                row = await approval.propose_entry(session, user.tenant_id, signal=signal, strategy_id=strategy_id, source="console",
                                                   idempotency_key=key, user_id=user.id)
            except approval.EntriesPaused as exc:          # Z3-b (review V4): the user's PAPER pause - an answer, not a 500
                return PaperExecuteResponse(signal=signal, executed=False, reasons=[str(exc)])
            return PaperExecuteResponse(signal=signal, executed=False, reasons=[
                f"Approval-only mode: entry proposal #{row.id} raised - approve it to place the PAPER order at the live price"
                if row is not None else "Approval-only mode: this signal was already proposed"])
        result, order = await execute_signal_for_user(
            session, user, mode="PAPER", strategy_id=strategy_id, signal=signal,
            idempotency_key=request.idempotency_key, risk_config=request.risk_config,
        )
        if order.trade_id is not None:
            # Z3 (review M1/H2): priced from candles the browser sent - kept out of the PAPER account and the ladder.
            from app.db.models import TradeRecord
            trade = await session.get(TradeRecord, order.trade_id)
            if trade is not None:
                trade.price_source = "client"
                await session.commit()
        return PaperExecuteResponse(
            signal=signal, executed=result.executed, reasons=result.reasons, order_id=order.id,
        )

    # Anonymous demo path: no order record, no persistence, no notifications - matching the
    # console's try-it-without-an-account flow. Always starts with clean risk-engine state since
    # there is no history to derive it from.
    risk_config = request.risk_config or _default_risk_config
    # M68 PR-1: the anonymous demo has no account, so no Trade settings; it places and stores nothing - its simulated fill
    # is shown at the signal's own price as a market fill (allow-list: a demo, not an order).
    router = OrderRouter(mode=ExecutionMode.PAPER, risk_config=risk_config, product="MIS", order_style="MARKET")
    router.risk_manager.one_lot_if_unsized = True     # M68 PR-2: the demo's simulated fill is one lot (stores nothing)
    try:
        result = await router.execute(signal, TradingDayState())
    except LiveTradingNotConfigured as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return PaperExecuteResponse(signal=signal, executed=result.executed, reasons=result.reasons)
