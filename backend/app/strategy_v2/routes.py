"""SFC P1 (flag `strategy_chart`): his own strategies on the chart.

* GET  /api/strategies/v2/chart-list - his own saved strategies that can be drawn: every `atp.strategy/2` one and every
  v1 no-code one with an exact v2 form (translate); the others are listed with the reason they cannot be drawn yet.
* POST /api/strategies/v2/plot - one strategy on one symbol: the bars are read the way his bot reads them (his own
  store and his own broker's top-up, M86: never another user's intraday data; Daily from the exchange's files), on the
  strategy's own timeframe, and the same evaluation the backtest / bot use gives the series, markers, why and trades.
Reference only: nothing is stored and nothing is ordered.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.models import RiskConfig
from app.core.rate_limit import analysis_rate_limit
from app.db.models import CustomStrategyRecord, User
from app.db.session import get_session
from app.platform.controls import require_flag
from app.strategy_v2.adapter import ENGINE_LABEL, StrategyV2Strategy
from app.strategy_v2.compile import StrategyError
from app.strategy_v2.schema import StrategyV2, is_v2

FLAG = "strategy_chart"
router = APIRouter(prefix="/api/strategies/v2", tags=["strategies"])
# Calendar days of bars read per timeframe (about MAX_BARS bars plus the warm-up; his own store holds what it holds)
DAYS: dict[str, int] = {"1m": 7, "3m": 15, "5m": 45, "15m": 120, "30m": 200, "1h": 365, "1d": 1500}
NOT_DRAWABLE = "Built with two timeframes in one condition - open it in the builder to redraw it"


def _strategy_of(record: CustomStrategyRecord) -> tuple[StrategyV2 | None, str | None]:
    """The v2 form of a saved strategy, or (None, why not)."""
    try:
        raw = json.loads(record.config_json or "{}")
    except ValueError:
        return None, "This strategy could not be read"
    if is_v2(raw):
        try:
            return StrategyV2.model_validate(raw), None
        except ValueError:
            return None, "This strategy could not be read"
    from app.strategy_engine.declarative import CustomStrategyConfig
    from app.strategy_v2.translate import translate
    try:
        v2 = translate(CustomStrategyConfig.model_validate(raw))
    except ValueError:
        return None, "This strategy could not be read"
    return (v2, None) if v2 is not None else (None, NOT_DRAWABLE)


async def _own(session: AsyncSession, user: User) -> list[CustomStrategyRecord]:
    rows = await session.scalars(select(CustomStrategyRecord).where(CustomStrategyRecord.tenant_id == user.tenant_id,
                                                                    CustomStrategyRecord.user_id == user.id)
                                 .order_by(CustomStrategyRecord.id))
    return list(rows.all())


@router.get("/chart-list")
async def chart_list(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    out = []
    for record in await _own(session, user):
        v2, why = _strategy_of(record)
        out.append({"id": f"custom:{record.id}", "name": record.name, "timeframe": v2.timeframe if v2 else None,
                    "drawable": v2 is not None and v2.timeframe in ENGINE_LABEL, "reason": why})
    return {"strategies": out}


class FormingBar(BaseModel):
    """P2: the chart's forming candle (display only: the pending hint; never stored, never acted on)."""
    t: datetime
    o: float = Field(allow_inf_nan=False)
    h: float = Field(allow_inf_nan=False)
    l: float = Field(allow_inf_nan=False)  # noqa: E741 - the bar's own field name
    c: float = Field(allow_inf_nan=False)
    v: float | None = Field(default=None, allow_inf_nan=False)


class PlotBody(BaseModel):
    strategy_id: str = Field(pattern=r"^custom:\d{1,12}$")
    symbol: str = Field(min_length=1, max_length=60)
    exchange: str = Field(default="NSE", max_length=20)
    forming: FormingBar | None = None


@router.post("/plot", dependencies=[Depends(analysis_rate_limit)])
async def plot_route(body: PlotBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    record = await session.get(CustomStrategyRecord, int(body.strategy_id.split(":")[1]))
    if record is None or record.tenant_id != user.tenant_id or record.user_id != user.id:
        raise HTTPException(status_code=404, detail="Strategy not found")
    v2, why = _strategy_of(record)
    head = {"strategy_id": body.strategy_id, "name": record.name, "timeframe": v2.timeframe if v2 else None}
    if v2 is None or v2.timeframe not in ENGINE_LABEL:
        return {**head, "available": False, "reason": why or "This timeframe cannot be drawn yet"}
    try:
        strategy = StrategyV2Strategy(body.strategy_id, v2)
    except StrategyError as exc:
        return {**head, "available": False, "reason": "; ".join(exc.problems)[:300]}
    from app.market_store import read_bars
    from app.market_store.sources import own_rest
    end = datetime.now(UTC)
    start = end - timedelta(days=DAYS.get(v2.timeframe, 90))
    rest = None if v2.timeframe in ("1d", "1w") else await own_rest(session, user, body.symbol, body.exchange)
    got = await read_bars(session, body.symbol, v2.timeframe, start, end, user=user, purpose="chart", exchange=body.exchange,
                          rest=rest, dh3=False)
    if got.frame.empty:
        return {**head, "available": False, "reason": (got.gap_lines() or ["No bars are stored for this symbol yet"])[0]}
    from app.paper import paper_settings
    risk, charges = await paper_settings.backtest_inputs(session, user, RiskConfig())
    if getattr(risk, "capital", None) is None:
        risk = RiskConfig()          # the walk needs a size; markers and exits do not depend on it (P&L is P4's)
    from app.strategy_v2.plot import plot
    forming = body.forming.model_dump(mode="json") if body.forming is not None else None
    drawn = await run_in_threadpool(plot, strategy, got.frame, body.symbol, risk, charges, forming)
    return {**head, "available": True, "reason": None, "gaps": got.gap_lines(), **drawn}


__all__ = ["router", "FLAG", "chart_list", "plot_route"]
