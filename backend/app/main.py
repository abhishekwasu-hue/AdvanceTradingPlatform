import copy
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.core.enums import ExecutionMode
from app.core.models import (
    BacktestResult,
    OHLCVBar,
    RiskConfig,
    Signal,
    StrategyInfo,
    bars_to_dataframe,
)
from app.backtest.engine import run_backtest
from app.brokers.models import OptionChain
from app.brokers.registry import available_brokers
from app.execution.router import ExecutionResult, LiveTradingNotConfigured, OrderRouter
from app.option_chain.analysis import analyze_option_chain
from app.option_chain.models import OptionChainAnalysis
from app.price_action.candlestick_patterns import detect_patterns
from app.price_action.market_structure import analyze_market_structure
from app.price_action.models import MarketStructureResult, PatternMatch
from app.risk_engine.risk_manager import TradingDayState
from app.strategy_engine.registry import registry
from app.support_resistance.engine import SupportResistanceEngine
from app.support_resistance.models import SRZone

app = FastAPI(
    title="Advance Trading Platform - Strategy Engine",
    description="Inbuilt auto-executable multi-timeframe and indicator-based intraday scalping strategies.",
    version="0.1.0",
)

_paper_state = TradingDayState()
_default_risk_config = RiskConfig()


class SignalRequest(BaseModel):
    symbol: str
    candles: Dict[str, List[OHLCVBar]]


class PaperExecuteRequest(SignalRequest):
    risk_config: Optional[RiskConfig] = None


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
def list_strategies() -> List[StrategyInfo]:
    return [s.info() for s in registry.list_all()]


@app.get("/api/strategies/{strategy_id}", response_model=StrategyInfo)
def get_strategy(strategy_id: str) -> StrategyInfo:
    try:
        return registry.get(strategy_id).info()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/strategies/{strategy_id}/signal", response_model=Signal)
def generate_signal(strategy_id: str, request: SignalRequest) -> Signal:
    try:
        strategy = registry.get(strategy_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    data = {tf: bars_to_dataframe(bars) for tf, bars in request.candles.items()}
    return strategy.analyze(data, request.symbol)


@app.post("/api/strategies/{strategy_id}/paper-execute", response_model=PaperExecuteResponse)
async def paper_execute(strategy_id: str, request: PaperExecuteRequest) -> PaperExecuteResponse:
    try:
        strategy = registry.get(strategy_id)
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

    return PaperExecuteResponse(signal=signal, executed=result.executed, reasons=result.reasons)


@app.post("/api/backtest", response_model=BacktestResult)
def backtest(request: BacktestRequest) -> BacktestResult:
    try:
        strategy = copy.copy(registry.get(request.strategy_id))
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
    """Broker ids the abstraction layer can adapt to. Authentication/credential endpoints land
    once the secrets-storage layer exists - credentials are never accepted over this API without
    encryption at rest.
    """
    return {"brokers": available_brokers()}


@app.get("/api/system/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}
