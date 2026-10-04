"""Phase AO: run a strategy on the candles a chart is showing.

`POST /api/strategies/{strategy_id}/chart-run` walks the strategy bar by bar over the chart's own
candles with the backtest engine - the same entries, stops, targets and exits a deployment would
take on that data - and returns them for the chart to draw, plus the signal on the last bar.

It is a view, not a backtest run: nothing is recorded or metered. The strategy runs on its own
timeframes (what a deployment of it would trade), so the chart's candles must be at that
timeframe or a finer one that divides it (a 1-minute strategy cannot be run on 5-minute bars).
"""
import copy
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user_optional
from app.backtest.engine import run_backtest
from app.core.models import OHLCVBar, RiskConfig, Signal, Trade, bars_to_dataframe
from app.core.resampling import resample_ohlc
from app.custom_strategies.resolver import resolve_strategy
from app.db.models import User
from app.db.session import get_session

router = APIRouter(prefix="/api/strategies", tags=["strategies"])

MAX_BARS = 3000


def timeframe_minutes(tf: str) -> Optional[int]:
    tf = tf.strip().lower()
    if tf in ("day", "1d"):
        return 375  # one NSE session
    m = re.fullmatch(r"(\d+)\s*(min|m)", tf)
    return int(m.group(1)) if m else None


class ChartRunRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=50)
    timeframe: str = Field(description="Interval of the candles sent (the chart's timeframe)")
    candles: List[OHLCVBar] = Field(min_length=1)
    risk_config: Optional[RiskConfig] = None


class ChartRunResponse(BaseModel):
    strategy_id: str
    strategy_name: str
    strategy_timeframes: List[str]
    compatible: bool
    reason: Optional[str] = None
    bars_used: int = 0
    trades: List[Trade] = []
    total_trades: int = 0
    win_rate: float = 0.0
    net_pnl: float = 0.0
    profit_factor: Optional[float] = None
    last_signal: Optional[Signal] = None


@router.post("/{strategy_id}/chart-run", response_model=ChartRunResponse)
async def chart_run(
    strategy_id: str, body: ChartRunRequest,
    user: Optional[User] = Depends(get_current_user_optional), session: AsyncSession = Depends(get_session),
) -> ChartRunResponse:
    try:
        strategy = copy.copy(await resolve_strategy(strategy_id, user, session))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    base = timeframe_minutes(body.timeframe)
    if base is None:
        raise HTTPException(status_code=400, detail=f"Unknown timeframe {body.timeframe!r}")
    out = ChartRunResponse(strategy_id=strategy.id, strategy_name=strategy.name, strategy_timeframes=list(strategy.timeframes), compatible=True)
    needed = [timeframe_minutes(tf) for tf in strategy.timeframes]
    if body.timeframe != "day" and any(n is None or n < base or n % base for n in needed):
        out.compatible = False
        finest = min(n for n in needed if n is not None) if any(needed) else None
        out.reason = (f"{strategy.name} trades on {'/'.join(strategy.timeframes)} candles; switch the chart to "
                      f"{finest}m or finer to see it" if finest else f"{strategy.name} cannot run on {body.timeframe} candles")
        return out
    if body.timeframe == "day" and strategy.timeframes != ["day"]:
        out.compatible = False
        out.reason = f"{strategy.name} is intraday ({'/'.join(strategy.timeframes)}); switch the chart to an intraday timeframe"
        return out

    candles = body.candles[-MAX_BARS:]
    df = bars_to_dataframe(candles)
    out.bars_used = len(df)
    frames = {tf: (df if tf == body.timeframe else resample_ohlc(df, tf)) for tf in strategy.timeframes}
    short = [f"{need} x {tf} (have {len(frames[tf])})" for tf, need in strategy.min_history().items() if len(frames.get(tf, [])) < need]
    if short:
        out.reason = f"Not enough candles yet: needs {', '.join(short)} - load more history or a finer timeframe"
        return out

    result = run_backtest(strategy, df, body.symbol.upper(), body.timeframe, body.risk_config or RiskConfig())
    out.trades, out.total_trades, out.win_rate = result.trades, result.total_trades, result.win_rate
    out.net_pnl, out.profit_factor = result.net_pnl, result.profit_factor
    out.last_signal = strategy.analyze(frames, body.symbol.upper())
    return out
