import copy
import hashlib
import json
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ALLOWED_ORIGINS, validate_production_config
from app.core.logging_config import configure_logging
from app.core.enums import ExecutionMode, OrderStatus
from app.core.models import (
    BacktestResult,
    OHLCVBar,
    RiskConfig,
    Signal,
    StrategyInfo,
    bars_to_dataframe,
)
from app.auth.dependencies import get_current_user_optional
from app.auth.routes import router as auth_router
from app.backtest.engine import run_backtest
from app.brokers.models import OptionChain
from app.brokers.registry import available_brokers
from app.brokers.routes import router as broker_router
from app.cache.client import cache_get, cache_set
from app.custom_strategies.resolver import custom_strategy_info, resolve_strategy
from app.custom_strategies.routes import router as custom_strategies_router
from app.fundamentals.routes import router as fundamentals_router
from app.db.models import CustomStrategyRecord, User
from app.db.session import get_session, init_models
from app.execution.order_persistence import get_order_by_idempotency_key
from app.execution.router import ExecutionResult, LiveTradingNotConfigured, OrderRouter
from app.execution.signal_execution import execute_signal_for_user
from app.instruments.models import ContractSpec
from app.instruments.registry import get_contract_spec, list_contract_specs
from app.kill_switch.checks import is_global_kill_switch_engaged
from app.kill_switch.routes import router as kill_switch_router
from app.news_events.routes import router as news_events_router
from app.option_chain.analysis import analyze_option_chain
from app.option_chain.leg_greeks import compute_strategy_greeks
from app.option_chain.models import OptionChainAnalysis, OptionLegInput, StrategyGreeksResult
from app.notifications.routes import router as notifications_router
from app.reconciliation.routes import router as reconciliation_router
from app.scanner.engine import run_scanner
from app.scanner.models import ScannerRequest, ScannerResult
from app.price_action.candlestick_patterns import detect_patterns
from app.price_action.market_structure import analyze_market_structure
from app.price_action.models import MarketStructureResult, PatternMatch
from app.risk_engine.risk_manager import TradingDayState
from app.risk_engine.routes import router as risk_settings_router
from app.signal_scoring.engine import enrich_signal
from app.signal_scoring.models import EnrichedSignal
from app.strategy_engine.registry import registry
from app.support_resistance.engine import SupportResistanceEngine
from app.support_resistance.models import SRZone
from app.trading.persistence import persist_signal_history
from app.trading.routes import router as trading_router
from app.webhooks.routes import router as webhooks_router
from app.workers.routes import router as workers_router
from app.deployments.routes import router as deployments_router
from app.market_data.routes import router as market_holidays_router

@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    validate_production_config()
    await init_models()
    yield


app = FastAPI(
    title="Advance Trading Platform - Strategy Engine",
    description="Inbuilt auto-executable multi-timeframe and indicator-based intraday scalping strategies.",
    version="0.1.0",
    lifespan=_lifespan,
)

# The Vite dev server proxies /api to this service in development, but CORS is still enabled
# for direct access (a separately-hosted frontend build, API docs "try it out", etc). Defaults to
# any origin for zero-config local dev; set ALLOWED_ORIGINS (comma-separated) to your real
# frontend domain(s) in production - validate_production_config() refuses to boot with the "*"
# default when ENVIRONMENT=production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(broker_router)
app.include_router(trading_router)
app.include_router(custom_strategies_router)
app.include_router(risk_settings_router)
app.include_router(fundamentals_router)
app.include_router(kill_switch_router)
app.include_router(reconciliation_router)
app.include_router(notifications_router)
app.include_router(webhooks_router)
app.include_router(news_events_router)
app.include_router(workers_router)
app.include_router(deployments_router)
app.include_router(market_holidays_router)

_default_risk_config = RiskConfig()


class SignalRequest(BaseModel):
    symbol: str
    candles: Dict[str, List[OHLCVBar]]


class PaperExecuteRequest(SignalRequest):
    risk_config: Optional[RiskConfig] = None
    idempotency_key: Optional[str] = None


class EnrichSignalRequest(SignalRequest):
    option_chain: Optional[OptionChain] = None
    swing_window: int = 3


class BacktestRequest(BaseModel):
    strategy_id: str
    symbol: str
    base_timeframe: str
    candles: List[OHLCVBar]
    risk_config: Optional[RiskConfig] = None
    strategy_params: Optional[Dict] = None


class PaperExecuteResponse(BaseModel):
    signal: Signal
    executed: bool
    reasons: List[str]
    order_id: Optional[int] = None
    idempotent_replay: bool = False


class CandlesRequest(BaseModel):
    symbol: str
    candles: List[OHLCVBar]
    timeframe: str = "1min"
    swing_window: int = 3


class SRZonesRequest(CandlesRequest):
    tolerance_pct: float = 0.15
    opening_range_minutes: int = 15


@app.get("/api/strategies", response_model=List[StrategyInfo])
async def list_strategies(
    user: Optional[User] = Depends(get_current_user_optional), session: AsyncSession = Depends(get_session),
) -> List[StrategyInfo]:
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


@app.get("/api/strategies/{strategy_id}", response_model=StrategyInfo)
async def get_strategy(
    strategy_id: str,
    user: Optional[User] = Depends(get_current_user_optional),
    session: AsyncSession = Depends(get_session),
) -> StrategyInfo:
    try:
        strategy = await resolve_strategy(strategy_id, user, session)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return strategy.info()


@app.post("/api/strategies/{strategy_id}/signal", response_model=Signal)
async def generate_signal(
    strategy_id: str, request: SignalRequest,
    user: Optional[User] = Depends(get_current_user_optional),
    session: AsyncSession = Depends(get_session),
) -> Signal:
    try:
        strategy = await resolve_strategy(strategy_id, user, session)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    data = {tf: bars_to_dataframe(bars) for tf, bars in request.candles.items()}
    return strategy.analyze(data, request.symbol)


@app.post("/api/strategies/{strategy_id}/signal/enrich", response_model=EnrichedSignal)
async def generate_and_enrich_signal(
    strategy_id: str, request: EnrichSignalRequest,
    user: Optional[User] = Depends(get_current_user_optional),
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

    data = {tf: bars_to_dataframe(bars) for tf, bars in request.candles.items()}
    signal = strategy.analyze(data, request.symbol)

    primary_tf = strategy.timeframes[0]
    ltf_df = data[primary_tf]
    enriched = enrich_signal(signal, ltf_df, option_chain=request.option_chain, swing_window=request.swing_window)

    if user is not None:
        await persist_signal_history(session, user, enriched)

    return enriched


@app.post("/api/strategies/{strategy_id}/paper-execute", response_model=PaperExecuteResponse)
async def paper_execute(
    strategy_id: str, request: PaperExecuteRequest,
    user: Optional[User] = Depends(get_current_user_optional),
    session: AsyncSession = Depends(get_session),
) -> PaperExecuteResponse:
    """Works anonymously (no persistence, matching the console's try-it-without-an-account
    flow) or, with a valid Authorization header, persists the fill to this user's trade history
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

    data = {tf: bars_to_dataframe(bars) for tf, bars in request.candles.items()}
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
        result, order = await execute_signal_for_user(
            session, user, mode="PAPER", strategy_id=strategy_id, signal=signal,
            idempotency_key=request.idempotency_key, risk_config=request.risk_config,
        )
        return PaperExecuteResponse(
            signal=signal, executed=result.executed, reasons=result.reasons, order_id=order.id,
        )

    # Anonymous demo path: no order record, no persistence, no notifications - matching the
    # console's try-it-without-an-account flow. Always starts with clean risk-engine state since
    # there is no history to derive it from.
    risk_config = request.risk_config or _default_risk_config
    router = OrderRouter(mode=ExecutionMode.PAPER, risk_config=risk_config)
    try:
        result = await router.execute(signal, TradingDayState())
    except LiveTradingNotConfigured as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return PaperExecuteResponse(signal=signal, executed=result.executed, reasons=result.reasons)


@app.post("/api/backtest", response_model=BacktestResult)
async def backtest(
    request: BacktestRequest,
    user: Optional[User] = Depends(get_current_user_optional),
    session: AsyncSession = Depends(get_session),
) -> BacktestResult:
    try:
        strategy = copy.copy(await resolve_strategy(request.strategy_id, user, session))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if request.strategy_params:
        strategy.params = {**strategy.params, **request.strategy_params}

    base_df = bars_to_dataframe(request.candles)
    risk_config = request.risk_config or _default_risk_config

    return run_backtest(strategy, base_df, request.symbol, request.base_timeframe, risk_config)


@app.post("/api/price-action/structure", response_model=MarketStructureResult)
def price_action_structure(request: CandlesRequest) -> MarketStructureResult:
    df = bars_to_dataframe(request.candles)
    return analyze_market_structure(df, window=request.swing_window)


@app.post("/api/price-action/patterns", response_model=List[PatternMatch])
def price_action_patterns(request: CandlesRequest) -> List[PatternMatch]:
    df = bars_to_dataframe(request.candles)
    return detect_patterns(df)


def _cache_key(prefix: str, payload: str) -> str:
    return f"{prefix}:{hashlib.sha256(payload.encode()).hexdigest()}"


@app.post("/api/support-resistance/zones", response_model=List[SRZone])
async def support_resistance_zones(request: SRZonesRequest) -> List[SRZone]:
    """Pure function of its input candles, so short-lived results are cached in Redis (when
    reachable - this fails open to a plain recompute otherwise) to avoid rebuilding the same
    swing-cluster/pivot/Fibonacci zones on every identical repeated call.
    """
    key = _cache_key("sr_zones", request.model_dump_json())
    cached = await cache_get(key)
    if cached is not None:
        return [SRZone.model_validate(z) for z in json.loads(cached)]

    df = bars_to_dataframe(request.candles)
    engine = SupportResistanceEngine(
        swing_window=request.swing_window,
        tolerance_pct=request.tolerance_pct,
        opening_range_minutes=request.opening_range_minutes,
    )
    zones = engine.build_zones(df, request.timeframe)
    await cache_set(key, "[" + ",".join(z.model_dump_json() for z in zones) + "]", ttl_seconds=5)
    return zones


class OptionChainAnalyzeRequest(BaseModel):
    chain: OptionChain
    top_n: int = 3


@app.post("/api/option-chain/analyze", response_model=OptionChainAnalysis)
async def option_chain_analyze(request: OptionChainAnalyzeRequest) -> OptionChainAnalysis:
    """Takes a raw OptionChain (e.g. from BrokerInterface.get_option_chain()) and returns PCR,
    Max Pain, ATM/ITM/OTM, OI buildup/unwinding, and a bias confirmed by more than PCR alone.
    Pure function of its input, so short-lived results are cached in Redis (fails open to a
    plain recompute if Redis isn't reachable) - useful once a real option chain is being polled
    repeatedly for the same underlying/expiry within the same few seconds.
    """
    key = _cache_key("option_chain_analysis", request.model_dump_json())
    cached = await cache_get(key)
    if cached is not None:
        return OptionChainAnalysis.model_validate_json(cached)

    result = analyze_option_chain(request.chain, top_n=request.top_n)
    await cache_set(key, result.model_dump_json(), ttl_seconds=5)
    return result


class GreeksRequest(BaseModel):
    legs: List[OptionLegInput]


@app.post("/api/option-chain/greeks", response_model=StrategyGreeksResult)
async def option_chain_greeks(request: GreeksRequest) -> StrategyGreeksResult:
    """Black-Scholes Delta/Gamma/Theta/Vega for one or more option legs, and the net Greeks of
    the combined position (a spread/straddle/strangle nets a short leg's Greeks against a long
    leg's). Each leg supplies either a real quoted `option_ltp` (implied volatility is solved
    from it) or an `implied_volatility` directly - never a fabricated one. A 422 means a leg's
    price is outside what's solvable (a stale/crossed quote), not a server error.
    """
    try:
        return compute_strategy_greeks(request.legs)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/scanner/run", response_model=ScannerResult)
async def scanner_run(request: ScannerRequest) -> ScannerResult:
    """Runs configurable indicator/price-action-structure/option-chain filters across a supplied
    list of symbols (each with its own OHLCV candles and, optionally, option chain) and returns
    only the ones that clear every filter. Indicator filters reuse the exact same `Condition`
    building block the no-code Strategy Builder uses. Pure function of its input - no persistence,
    works with or without a logged-in caller.
    """
    return run_scanner(request)


@app.get("/api/broker/available")
def list_available_brokers() -> Dict[str, List[str]]:
    """Broker ids the abstraction layer can adapt to. Storing credentials and authenticating
    against one requires a logged-in user - see /api/broker/{name}/credentials and
    /api/broker/{name}/authenticate - credentials are encrypted at rest, never in plaintext.
    """
    return {"brokers": available_brokers()}


@app.get("/api/instruments", response_model=List[ContractSpec])
def list_instruments() -> List[ContractSpec]:
    """Reference contract specs for MCX commodity and crypto symbols (see
    app/instruments/registry.py) - plain NSE/BSE equity & index-option symbols aren't in here,
    since they already size correctly off the tenant's own risk settings. Public, no auth: this
    is static reference metadata, not a live quote feed.
    """
    return list_contract_specs()


@app.get("/api/instruments/{symbol}", response_model=ContractSpec)
def get_instrument(symbol: str) -> ContractSpec:
    spec = get_contract_spec(symbol)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"No contract spec registered for '{symbol.upper()}'")
    return spec


@app.get("/api/system/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}
