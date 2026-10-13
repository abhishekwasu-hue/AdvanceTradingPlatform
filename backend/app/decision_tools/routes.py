"""N2-3 (P1 spec 1.5): decision tools - information, never advice. Behind the `decision_tools` flag (404 while off).

- `POST /api/decision/ruin`: the risk-of-ruin and Kelly calculator on typed inputs. It computes over the body and
  stores nothing.
- `GET /api/decision/positions`: the caller's own open positions, grouped by underlying, as what-if subjects.
- `POST /api/decision/what-if`: one subject's P&L now, on a later day with an IV shift, and at expiry, at the spot and
  IVs typed in.
- `GET /api/decision/exposure` (N2-4): the caller's own open positions by underlying and sector, beta to the NIFTY 50
  and the pairs whose daily returns move together, from the shared daily lake.
- `GET /api/decision/expected-move` (N2-4): the expected-move cone for an instrument from its daily closes (historical
  volatility) or a typed IV, for the chart overlay.

Every position read is the caller's own (tenant + user). Nothing here places, changes or closes an order.
"""
from typing import Annotated, Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import InstrumentRecord, TradeRecord, User
from app.db.session import get_session
from app.decision_tools import calculators, exposure, market, stats, whatif

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


# --- N2-4 ----------------------------------------------------------------------------------------------------------------

LOOKBACK_DAYS = (30, 365)
CONE_HORIZONS = (1, 5, 10, 20)


@router.get("/exposure")
async def exposure_view(days: int = Query(default=90, ge=LOOKBACK_DAYS[0], le=LOOKBACK_DAYS[1]), user: User = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The caller's own open positions: by underlying and sector, beta to the NIFTY 50 and correlated pairs, over the
    last `days` calendar days of daily closes."""
    await _enabled(session, user)
    trades = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.exit_time.is_(None)).order_by(TradeRecord.id)))
    rows = exposure.book(trades)
    # Review (DoS): lake reads and the pair maths for at most MAX_COMPUTED underlyings (largest risk first); the pure
    # maths runs in the thread pool, off the event loop.
    returns = {name: stats.log_returns(await market.daily_closes(session, name, days=days)) for name in exposure.computed_names(rows)}
    nifty = stats.log_returns(await market.daily_closes(session, market.MARKET, days=days))
    sectors = await market.sectors(session, rows)
    return await run_in_threadpool(exposure.assemble, rows, sectors, returns, nifty, days=days)


@router.get("/expected-move")
async def expected_move(instrument: str = Query(min_length=1, max_length=64), horizon: int = Query(default=5, ge=1, le=60),
                        iv: Optional[float] = Query(default=None, ge=0.01, le=3.0), window: int = Query(default=20, ge=5, le=120),
                        user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The expected-move cone from the last daily close: ±1 sd and ±2 sd over 1 / 5 / 10 / 20 sessions and `horizon`,
    at `iv` when given, else the historical volatility of the last `window` daily returns. `instrument` is a lake key
    ("NSE:RELIANCE", "NSE:NIFTY 50") or a plain name."""
    await _enabled(session, user)
    name = instrument.split(":", 1)[1] if ":" in instrument else instrument
    closes = await market.daily_closes(session, name, days=max(60, window * 3))
    if not closes:
        raise HTTPException(status_code=404, detail=f"No daily closes for {name} in the lake")
    last_day = max(closes)
    sigma, method = (iv, "iv") if iv is not None else (stats.hv(closes, window), f"hv{window}")
    if sigma is None:
        raise HTTPException(status_code=422, detail=f"{len(closes)} daily closes for {name}: {window + 1} are needed for the historical volatility")
    horizons = sorted(set(CONE_HORIZONS) | {horizon})
    rows = stats.cone(closes[last_day], sigma, horizons)
    return {"instrument": name.strip().upper(), "close": closes[last_day], "close_date": last_day.isoformat(), "sigma": round(sigma, 4),
            "method": method, "window": window, "horizon": horizon, "cone": rows,
            "selected": next(r for r in rows if r["sessions"] == horizon),
            "note": "A lognormal model of past daily moves (or the IV you gave): about 68 % of outcomes inside ±1 sd and 95 % "
                    "inside ±2 sd under that model. Not a forecast."}
