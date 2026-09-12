import copy
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ExecutionMode
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
from app.custom_strategies.resolver import custom_strategy_info, resolve_strategy
from app.custom_strategies.routes import router as custom_strategies_router
from app.db.models import CustomStrategyRecord, User
from app.db.session import get_session, init_models
from app.execution.router import ExecutionResult, LiveTradingNotConfigured, OrderRouter
from app.option_chain.analysis import analyze_option_chain
from app.option_chain.models import OptionChainAnalysis
from app.price_action.candlestick_patterns import detect_patterns
from app.price_action.market_structure import analyze_market_structure
from app.price_action.models import MarketStructureResult, PatternMatch
from app.risk_engine.risk_manager import TradingDayState
from app.signal_scoring.engine import enrich_signal
from app.signal_scoring.models import EnrichedSignal
from app.strategy_engine.registry import registry
from app.support_resistance.engine import SupportResistanceEngine
from app.support_resistance.models import SRZone
from app.trading.persistence import persist_paper_trade, persist_signal_history
from app.trading.routes import router as trading_router

@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_models()
    yield


app = FastAPI(
    title="Advance Trading Platform - Strategy Engine",
    description="Inbuilt auto-executable multi-timeframe and indicator-based intraday scalping strategies.",
    version="0.1.0",
    lifespan=_lifespan,
)

# The Vite dev server proxies /api to this service in development, but CORS is still enabled
# for direct access (a separately-hosted frontend build, API docs "try it out", etc). Tightened
# to specific origins once real deployment domains exist.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(broker_router)
app.include_router(trading_router)
app.include_router(custom_strategies_router)

_paper_state = TradingDayState()
_default_risk_config = RiskConfig()


class SignalRequest(BaseModel):
    symbol: str
    candles: Dict[str, List[OHLCVBar]]


class PaperExecuteRequest(SignalRequest):
    risk_config: Optional[RiskConfig] = None


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
            .where(CustomStrategyRecord.user_id == user.id)
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
        await persist_signal_history(session, user.id, enriched)

    return enriched


@app.post("/api/strategies/{strategy_id}/paper-execute", response_model=PaperExecuteResponse)
async def paper_execute(
    strategy_id: str, request: PaperExecuteRequest,
    user: Optional[User] = Depends(get_current_user_optional),
    session: AsyncSession = Depends(get_session),
) -> PaperExecuteResponse:
    """Works anonymously (no persistence, matching the console's try-it-without-an-account
    flow) or, with a valid Authorization header, persists the fill to this user's trade history
    - see GET /api/trades and /api/positions.
    """
    try:
        strategy = await resolve_strategy(strategy_id, user, session)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    data = {tf: bars_to_dataframe(bars) for tf, bars in request.candles.items()}
    signal = strategy.analyze(data, request.symbol)

    risk_config = request.risk_config or _default_risk_config
    router = OrderRouter(mode=ExecutionMode.PAPER, risk_config=risk_config)
    try:
        result: ExecutionResult = await router.execute(signal, _paper_state)
    except LiveTradingNotConfigured as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if result.executed and result.trade is not None and user is not None:
        await persist_paper_trade(session, user.id, result.trade)

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


@app.post("/api/support-resistance/zones", response_model=List[SRZone])
def support_resistance_zones(request: SRZonesRequest) -> List[SRZone]:
    df = bars_to_dataframe(request.candles)
    engine = SupportResistanceEngine(
        swing_window=request.swing_window,
        tolerance_pct=request.tolerance_pct,
        opening_range_minutes=request.opening_range_minutes,
    )
    return engine.build_zones(df, request.timeframe)


class OptionChainAnalyzeRequest(BaseModel):
    chain: OptionChain
    top_n: int = 3


@app.post("/api/option-chain/analyze", response_model=OptionChainAnalysis)
def option_chain_analyze(request: OptionChainAnalyzeRequest) -> OptionChainAnalysis:
    """Takes a raw OptionChain (e.g. from BrokerInterface.get_option_chain()) and returns PCR,
    Max Pain, ATM/ITM/OTM, OI buildup/unwinding, and a bias confirmed by more than PCR alone.
    """
    return analyze_option_chain(request.chain, top_n=request.top_n)


@app.get("/api/broker/available")
def list_available_brokers() -> Dict[str, List[str]]:
    """Broker ids the abstraction layer can adapt to. Storing credentials and authenticating
    against one requires a logged-in user - see /api/broker/{name}/credentials and
    /api/broker/{name}/authenticate - credentials are encrypted at rest, never in plaintext.
    """
    return {"brokers": available_brokers()}


@app.get("/api/system/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}
