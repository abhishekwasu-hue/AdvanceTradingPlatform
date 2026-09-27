"""Phase J2: backtest records and robustness endpoints.

* `GET  /api/backtests`            - this organisation's saved runs (newest first)
* `GET  /api/backtests/{id}`       - one run with its stored metrics and analytics
* `POST /api/backtest/monte-carlo` - run the backtest, then resample its trades
* `POST /api/backtest/walk-forward`- run the same strategy on consecutive windows

`/api/backtest` itself (app/main.py) records a run whenever the caller is logged in.
"""
import copy
import json
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_current_user_optional
from app.backtest.engine import ENGINE_VERSION, run_backtest
from app.backtest.optimizer import MAX_COMBOS, optimize
from app.backtest.robustness import monte_carlo, walk_forward
from app.core.models import BacktestResult, OHLCVBar, RiskConfig, bars_to_dataframe
from app.custom_strategies.resolver import resolve_strategy
from app.db.models import BacktestRunRecord, User
from app.db.session import get_session
from app.trading.exit_rules import ExitRules

router = APIRouter(tags=["backtest"])


class ExitRulesBody(BaseModel):
    trailing_stop_pct: Optional[float] = Field(default=None, gt=0, le=50)
    break_even_at_r: Optional[float] = Field(default=None, gt=0, le=10)
    time_exit_minutes: Optional[int] = Field(default=None, ge=1, le=375)
    time_exit_at: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    def to_rules(self) -> ExitRules:
        return ExitRules(**self.model_dump())


class BacktestBody(BaseModel):
    strategy_id: str
    symbol: str
    base_timeframe: str
    candles: List[OHLCVBar]
    risk_config: Optional[RiskConfig] = None
    strategy_params: Optional[Dict] = None
    exit_rules: Optional[ExitRulesBody] = None
    data_source: str = Field(default="uploaded", max_length=30)


async def _strategy(body: BacktestBody, user: Optional[User], session: AsyncSession):
    try:
        strategy = copy.copy(await resolve_strategy(body.strategy_id, user, session))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if body.strategy_params:
        strategy.params = {**strategy.params, **body.strategy_params}
    return strategy


async def record_run(session: AsyncSession, user: Optional[User], body: BacktestBody, result: BacktestResult) -> Optional[int]:
    """Persist a run for a logged-in caller: what ran, on what data, with which result."""
    if user is None or not body.candles:
        return None
    headline = result.model_dump(exclude={"trades", "equity_curve", "analytics"})
    headline["analytics"] = result.analytics
    record = BacktestRunRecord(
        tenant_id=user.tenant_id, user_id=user.id, strategy_id=body.strategy_id, symbol=body.symbol.upper(),
        base_timeframe=body.base_timeframe, params_json=json.dumps(body.strategy_params) if body.strategy_params else None,
        exit_rules=result.exit_rules, data_source=body.data_source, bars=len(body.candles),
        data_from=body.candles[0].timestamp, data_to=body.candles[-1].timestamp, engine_version=ENGINE_VERSION,
        metrics_json=json.dumps(headline, default=str),
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record.id


def _run_summary(r: BacktestRunRecord, full: bool = False) -> dict:
    metrics = json.loads(r.metrics_json or "{}")
    out = {
        "id": r.id, "strategy_id": r.strategy_id, "symbol": r.symbol, "base_timeframe": r.base_timeframe,
        "params": json.loads(r.params_json) if r.params_json else None, "exit_rules": json.loads(r.exit_rules) if r.exit_rules else None,
        "data_source": r.data_source, "bars": r.bars, "data_from": r.data_from.isoformat() if r.data_from else None,
        "data_to": r.data_to.isoformat() if r.data_to else None, "engine_version": r.engine_version,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "total_trades": metrics.get("total_trades"), "net_pnl": metrics.get("net_pnl"), "win_rate": metrics.get("win_rate"),
        "max_drawdown": metrics.get("max_drawdown"), "profit_factor": metrics.get("profit_factor"),
    }
    if full:
        out["metrics"] = metrics
    return out


@router.get("/api/backtests")
async def list_runs(limit: int = 50, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[dict]:
    rows = await session.scalars(select(BacktestRunRecord).where(BacktestRunRecord.tenant_id == user.tenant_id)
                                 .order_by(BacktestRunRecord.id.desc()).limit(max(1, min(limit, 500))))
    return [_run_summary(r) for r in rows]


@router.get("/api/backtests/{run_id}")
async def get_run(run_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    record = await session.get(BacktestRunRecord, run_id)
    if record is None or record.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="No such backtest run")
    return _run_summary(record, full=True)


@router.post("/api/backtest/monte-carlo")
async def backtest_monte_carlo(
    body: BacktestBody, runs: int = 1000, seed: Optional[int] = 42,
    user: Optional[User] = Depends(get_current_user_optional), session: AsyncSession = Depends(get_session),
) -> dict:
    strategy = await _strategy(body, user, session)
    risk = body.risk_config or RiskConfig()
    result = run_backtest(strategy, bars_to_dataframe(body.candles), body.symbol, body.base_timeframe, risk,
                          exit_rules=body.exit_rules.to_rules() if body.exit_rules else None)
    return {"backtest": result.model_dump(exclude={"trades", "equity_curve", "analytics"}),
            "monte_carlo": monte_carlo(result.trades, risk.capital, runs=max(100, min(runs, 5000)), seed=seed)}


@router.post("/api/backtest/walk-forward")
async def backtest_walk_forward(
    body: BacktestBody, folds: int = 4,
    user: Optional[User] = Depends(get_current_user_optional), session: AsyncSession = Depends(get_session),
) -> dict:
    strategy = await _strategy(body, user, session)
    risk = body.risk_config or RiskConfig()
    return walk_forward(strategy, bars_to_dataframe(body.candles), body.symbol, body.base_timeframe, risk, folds=folds)


class OptimizeBody(BaseModel):
    strategy_id: str
    symbol: str
    base_timeframe: str
    candles: List[OHLCVBar]
    param_grid: Dict[str, List] = Field(description=f"parameter -> candidate values; at most {MAX_COMBOS} combinations")
    metric: str = Field(default="net_pnl", pattern=r"^(net_pnl|expectancy|profit_factor|win_rate)$")
    split: float = Field(default=0.7, ge=0.5, le=0.9)
    risk_config: Optional[RiskConfig] = None


@router.post("/api/backtest/optimize")
async def backtest_optimize(body: OptimizeBody, user: Optional[User] = Depends(get_current_user_optional),
                            session: AsyncSession = Depends(get_session)) -> dict:
    """Phase M / V4.6: grid search on the in-sample part, ranked by the out-of-sample metric."""
    strategy = await _strategy(BacktestBody(strategy_id=body.strategy_id, symbol=body.symbol, base_timeframe=body.base_timeframe,
                                            candles=body.candles, risk_config=body.risk_config), user, session)
    try:
        return optimize(strategy, bars_to_dataframe(body.candles), body.symbol, body.base_timeframe, body.risk_config or RiskConfig(),
                        body.param_grid, metric=body.metric, split=body.split)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
