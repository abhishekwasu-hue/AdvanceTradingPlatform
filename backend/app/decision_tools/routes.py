"""N2-3 (P1 spec 1.5): decision tools - information, never advice. Behind the `decision_tools` flag (404 while off).

- `POST /api/decision/ruin`: the risk-of-ruin and Kelly calculator on typed inputs. It computes over the body and
  stores nothing.
- `GET /api/decision/positions`: the caller's own open positions, grouped by underlying, as what-if subjects.
- `POST /api/decision/what-if`: one subject's P&L now, on a later day with an IV shift, and at expiry, at the spot and
  IVs typed in.

Every position read is the caller's own (tenant + user). Nothing here places, changes or closes an order.
"""
from typing import Annotated, Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import InstrumentRecord, TradeRecord, User
from app.db.session import get_session
from app.decision_tools import calculators, whatif

FLAG = "decision_tools"
router = APIRouter(prefix="/api/decision", tags=["decision-tools"])


async def _enabled(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": FLAG})


class RuinBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    win_rate: float = Field(gt=0, lt=1, allow_inf_nan=False)
    payoff_ratio: float = Field(gt=0, le=1000, allow_inf_nan=False)
    risk_pct: float = Field(ge=0.01, lt=100, allow_inf_nan=False)
    ruin_pct: float = Field(gt=0, le=100, allow_inf_nan=False)
    compare: Optional[List[Annotated[float, Field(allow_inf_nan=False)]]] = Field(default=None, max_length=calculators.MAX_COMPARE)


class WhatIfBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    underlying: str = Field(min_length=1, max_length=60)
    spot: float = Field(gt=0, le=1e7, allow_inf_nan=False)
    iv: Optional[float] = Field(default=None, ge=0.01, le=3.0, allow_inf_nan=False)
    ivs: Optional[Dict[int, Annotated[float, Field(ge=0.01, le=3.0, allow_inf_nan=False)]]] = Field(default=None, max_length=200)
    days_forward: float = Field(default=0.0, ge=0.0, le=366.0)
    iv_shift: float = Field(default=0.0, ge=-0.5, le=1.0)
    range_pct: float = Field(default=10.0, ge=0.5, le=50.0)


async def _own_subjects(session: AsyncSession, user: User) -> List[Dict[str, Any]]:
    """The caller's own open trades as what-if subjects, option strikes and rights from the instrument master when it
    knows the trading symbol (any broker's master: shared reference data)."""
    trades = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.exit_time.is_(None)).order_by(TradeRecord.id)))
    symbols = sorted({t.symbol.upper() for t in trades if t.instrument_kind == "OPTION"})
    masters: Dict[str, Tuple[float, str]] = {}
    if symbols:
        rows = await session.execute(select(InstrumentRecord.tradingsymbol, InstrumentRecord.strike, InstrumentRecord.instrument_type)
                                     .where(InstrumentRecord.tradingsymbol.in_(symbols), InstrumentRecord.instrument_type.in_(("CE", "PE"))))
        for symbol, strike, kind in rows:
            if strike is not None:
                masters.setdefault(symbol.upper(), (float(strike), kind))
    return whatif.subjects(trades, masters)


@router.post("/ruin")
async def ruin(body: RuinBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    try:
        return calculators.calculate(body.win_rate, body.payoff_ratio, body.risk_pct, body.ruin_pct, body.compare)
    except calculators.CalculatorError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/positions")
async def positions(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    return {"subjects": await _own_subjects(session, user)}


@router.post("/what-if")
async def what_if(body: WhatIfBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    subject = next((s for s in await _own_subjects(session, user) if s["underlying"] == body.underlying), None)
    if subject is None:
        raise HTTPException(status_code=404, detail=f"No open position of yours on {body.underlying}")
    try:
        out = whatif.what_if(subject["legs"], spot=body.spot, iv=body.iv, ivs=body.ivs, days_forward=body.days_forward,
                             iv_shift=body.iv_shift, range_pct=body.range_pct)
    except whatif.WhatIfError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"underlying": subject["underlying"], "skipped": subject["skipped"], **out}
