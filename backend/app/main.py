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
from app.execution.router import ExecutionResult, LiveTradingNotConfigured, OrderRouter
from app.risk_engine.risk_manager import TradingDayState
from app.strategy_engine.registry import registry

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
def paper_execute(strategy_id: str, request: PaperExecuteRequest) -> PaperExecuteResponse:
    try:
        strategy = registry.get(strategy_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    data = {tf: bars_to_dataframe(bars) for tf, bars in request.candles.items()}
    signal = strategy.analyze(data, request.symbol)

    risk_config = request.risk_config or _default_risk_config
    router = OrderRouter(mode=ExecutionMode.PAPER, risk_config=risk_config)
    try:
        result: ExecutionResult = router.execute(signal, _paper_state)
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


@app.get("/api/system/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}
