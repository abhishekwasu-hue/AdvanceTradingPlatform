"""`POST /api/backtest`: the console's backtest of a strategy on the candles the request sends.

M55 Q1: moved from app/main.py (which now only includes routers) as it was - only the annotations are in built-in
generics; included at the end of the router list in the same order as before, so every path, method, dependency
and the match order are unchanged (tests/test_m55_q1_route_table.py).
"""
import copy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.backtest.routes import BacktestBody, BacktestRunner, ExitRulesBody, OptionBacktestBody, record_run, with_template_exits
from app.billing.service import meter
from app.core.models import BacktestResult, OHLCVBar, RiskConfig, bars_to_dataframe
from app.core.rate_limit import analysis_rate_limit
from app.custom_strategies.resolver import resolve_strategy
from app.db.models import User
from app.db.session import get_session
from app.strategy_templates.exits import TemplateExitsBody

router = APIRouter()

_default_risk_config = RiskConfig()


class BacktestRequest(BaseModel):
    strategy_id: str
    symbol: str
    base_timeframe: str
    candles: list[OHLCVBar]
    risk_config: RiskConfig | None = None
    strategy_params: dict | None = None
    # Phase J: dynamic exits and a label for where the candles came from (recorded on the run).
    exit_rules: ExitRulesBody | None = None
    data_source: str = "uploaded"
    # Phase W: present = run the signals as option structures (app/backtest/options_engine.py).
    options: OptionBacktestBody | None = None
    execution_models: dict[str, Any] | None = None   # realism C2 (omitted = default models)
    template_exits: TemplateExitsBody | None = None   # strategy templates section 5 (omitted = as before)


# P0.2 / S1: `analysis_rate_limit` (app/core/rate_limit.py) - one account cannot monopolise the analysis threadpool.


@router.post("/api/backtest", response_model=BacktestResult, dependencies=[Depends(analysis_rate_limit)])
async def backtest(
    request: BacktestRequest,
    user: User = Depends(get_current_user),
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

    body = BacktestBody(**request.model_dump())
    strategy = with_template_exits(strategy, body)                     # section 5: a template's exit choice (422 if it does not fit)
    runner = await BacktestRunner.build(body, risk_config, session, tenant_id=user.tenant_id if user is not None else None)
    # REV-S3 (R6): a template gets the history its recipe reads before the window (warm-up only, never traded), or the
    # result says plainly what the test lacked (app/backtest/warmup.py).
    from app.backtest import warmup
    history = await warmup.fetch_history(session, user, request.data_source, request.symbol, request.base_timeframe, base_df, strategy)
    prepared = await run_in_threadpool(warmup.prepare, strategy, base_df, history)
    result = await run_in_threadpool(runner.run, prepared.strategy, prepared.frame)   # CPU-bound pandas work off the event loop (P0.1 / S3)
    result.history = prepared.history()
    # Phase J2: the run is recorded (strategy, params, data span, metrics) and metered against the plan.
    result.run_id = await record_run(session, user, body, result)
    await meter(session, user.tenant_id, "backtest", 1, source="api", metadata={"strategy_id": request.strategy_id, "bars": len(request.candles)})
    return result
