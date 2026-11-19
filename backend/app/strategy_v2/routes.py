"""SFC P1 (flag `strategy_chart`): his own strategies on the chart.

* GET  /api/strategies/v2/chart-list - his own saved strategies that can be drawn: every `atp.strategy/2` one and every
  v1 no-code one with an exact v2 form (translate); the others are listed with the reason they cannot be drawn yet.
* POST /api/strategies/v2/plot - one strategy on one symbol: the bars are read the way his bot reads them (his own
  store and his own broker's top-up, M86: never another user's intraday data; Daily from the exchange's files), on the
  strategy's own timeframe, and the same evaluation the backtest / bot use gives the series, markers, why and trades.
Reference only: nothing is stored and nothing is ordered.

SFC P5, the builder (his own strategies only; nothing is ordered, a saved strategy runs nowhere until he deploys it):
* GET    /api/strategies/v2/blocks - what a condition can read: every registry function this engine evaluates on bars
  (not the cross-sectional ones, not the ones needing an option chain / reference lists / stored zones, not a feature the
  operator has off), with its settings (no value pre-filled: DEF-0);
* POST   /api/strategies/v2/check - would it save, and if not, why (the compiler's own problems);
* POST   /api/strategies/v2 · GET / PUT / DELETE /api/strategies/v2/{id} - save, read, edit, delete one of his own.
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

from app.auth.dependencies import get_current_user, require_trader
from app.auth.scopes import require_scope
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
                    "drawable": v2 is not None and v2.timeframe in ENGINE_LABEL, "reason": why,
                    "editable": '"atp.strategy/2"' in (record.config_json or "")[:200]})     # P5: built in the chart's builder
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


# ------------------------------------------------------------------------------------------------------ P5: the builder
SKIP_NEEDS = ("option_chain", "reference", "zones", "chains")


def _arg(a: Any) -> dict[str, Any]:
    # `series`: the input is a number series (close, high ...), not a setting; no default is ever sent (DEF-0)
    return {"name": a.name, "type": a.type, "required": bool(a.required), "choices": [str(c) for c in a.choices],
            "bounds": list(a.bounds) if a.bounds else None, "series": a.type == "num" and not a.literal_only}


@router.get("/blocks")
async def blocks(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """What a condition can read: every function the strategy engine evaluates on his bars, with its settings."""
    await require_flag(session, FLAG, user.tenant_id)
    from app.platform.controls import flag_enabled
    from app.screener.registry import FUNCTIONS
    from app.strategy_v2.compile import LAST_BAR_ONLY
    flags: dict[str, bool] = {}
    out = []
    for name, spec in sorted(FUNCTIONS.items()):
        if spec.cross_sectional or name in LAST_BAR_ONLY or any(need in SKIP_NEEDS for need in spec.needs):
            continue
        needed = [f for f in (spec.flag, *spec.also_flags) if f]
        for f in needed:
            if f not in flags:
                flags[f] = await flag_enabled(session, f, user.tenant_id)
        if not all(flags[f] for f in needed):
            continue
        out.append({"block": name, "kind": spec.kind, "returns": spec.returns, "unit": spec.unit, "doc": (spec.doc or "")[:200],
                    "timeframed": spec.timeframed, "args": [_arg(a) for a in spec.args], "kwargs": [_arg(a) for a in spec.kwargs],
                    "values": list(spec.values)})
    return {"blocks": out}


def _compile_problems(raw: dict[str, Any]) -> tuple[StrategyV2 | None, list[str]]:
    try:
        v2 = StrategyV2.model_validate(raw)
    except ValueError as exc:
        errors = getattr(exc, "errors", None)
        if callable(errors):
            return None, [f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg', '')}".strip(": ") for e in errors()][:20]
        return None, [str(exc)[:300]]
    if v2.timeframe not in ENGINE_LABEL:
        return None, [f"The {v2.timeframe} timeframe cannot be run yet"]
    try:
        StrategyV2Strategy("custom:0", v2)
    except StrategyError as exc:
        return None, exc.problems[:20]
    return v2, []


class StrategyBody(BaseModel):
    strategy: dict[str, Any]


@router.post("/check")
async def check(body: StrategyBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    v2, problems = _compile_problems(body.strategy)
    return {"ok": v2 is not None, "problems": problems}


def _saved(record: CustomStrategyRecord, v2: StrategyV2) -> dict[str, Any]:
    return {"id": f"custom:{record.id}", "record_id": record.id, "name": record.name,
            "strategy": json.loads(v2.model_dump_json(by_alias=True, exclude_none=True))}


def _valid_or_400(raw: dict[str, Any]) -> StrategyV2:
    v2, problems = _compile_problems(raw)
    if v2 is None:
        raise HTTPException(status_code=400, detail={"problems": problems})
    return v2


async def _own_v2(session: AsyncSession, user: User, record_id: int) -> tuple[CustomStrategyRecord, StrategyV2]:
    record = await session.get(CustomStrategyRecord, record_id)
    if record is None or record.tenant_id != user.tenant_id or record.user_id != user.id:
        raise HTTPException(status_code=404, detail="Strategy not found")
    try:
        raw = json.loads(record.config_json or "{}")
    except ValueError:
        raw = {}
    if not is_v2(raw):
        raise HTTPException(status_code=404, detail="Strategy not found")
    return record, StrategyV2.model_validate(raw)


@router.post("", status_code=201, dependencies=[Depends(require_scope("strategies:write"))])
async def create(body: StrategyBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    v2 = _valid_or_400(body.strategy)
    from app.plans.limits import check_can_add_custom_strategy, load_tenant
    await check_can_add_custom_strategy(session, await load_tenant(session, user.tenant_id))
    record = CustomStrategyRecord(tenant_id=user.tenant_id, user_id=user.id, name=v2.name,
                                  config_json=v2.model_dump_json(by_alias=True, exclude_none=True))
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return _saved(record, v2)


@router.get("/{record_id:int}")
async def read(record_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    record, v2 = await _own_v2(session, user, record_id)
    return _saved(record, v2)


@router.put("/{record_id:int}", dependencies=[Depends(require_scope("strategies:write"))])
async def update(record_id: int, body: StrategyBody, user: User = Depends(require_trader),
                 session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    record, _old = await _own_v2(session, user, record_id)
    v2 = _valid_or_400(body.strategy)
    record.name = v2.name
    record.config_json = v2.model_dump_json(by_alias=True, exclude_none=True)
    await session.commit()
    await session.refresh(record)
    return _saved(record, v2)


@router.delete("/{record_id:int}", status_code=204, dependencies=[Depends(require_scope("strategies:write"))])
async def delete(record_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> None:
    await require_flag(session, FLAG, user.tenant_id)
    record, _v2 = await _own_v2(session, user, record_id)
    await session.delete(record)
    await session.commit()


__all__ = ["router", "FLAG", "chart_list", "plot_route", "blocks", "check", "create", "read", "update", "delete"]
