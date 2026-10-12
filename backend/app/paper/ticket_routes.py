"""Z5 / P1-d: the caller's plan-before-trade PAPER ticket and risk-by-default state. Behind the `risk_by_default` flag
(404 while off). Every answer is the caller's own (tenant + user); the only order this can lead to is one PAPER order
through the ordinary pipeline (app/paper/ticket.py), never a broker order, and nothing here touches an exit.

- `GET  /api/paper/risk`            settings, cap, today's P&L against the daily limit, lockout (why, until when),
                                    entries today, overrides, India VIX gate.
- `PUT  /api/paper/risk`            change the caller's own settings (within the bounds and the platform ceilings).
- `POST /api/paper/ticket/preview`  the plan checked: sizing, refusals, warnings, checklist, cost line.
- `POST /api/paper/ticket`          place it (re-priced at the live price; stored with its order and position).
- `GET  /api/paper/plans`           the caller's recent plans with what became of them.
"""
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.paper import ticket
from app.risk_engine import by_default

router = APIRouter(prefix="/api/paper", tags=["paper"])
MAX_PRICE = 1e7
place_rate_limit = user_rate_limit("paper_ticket", limit=30, window_seconds=60)
preview_rate_limit = user_rate_limit("paper_ticket_preview", limit=120, window_seconds=60)


async def _enabled(session: AsyncSession, user: User) -> None:
    if not await by_default.enabled(session, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": by_default.FLAG})


class PlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9&._\- ]+$")
    direction: Literal["LONG", "SHORT"]
    setup: Literal["PULLBACK_AT_LEVEL", "SR_FLIP", "BREAKOUT_RETEST", "RANGE_EDGE", "OI_IV", "OTHER"]
    entry: float = Field(gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    stop_loss: float = Field(gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    target: float = Field(gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    time_stop: Optional[str] = Field(default=None, max_length=5)
    invalidation: Optional[str] = Field(default=None, max_length=300)
    checklist: Dict[str, bool] = Field(default_factory=dict, max_length=20)
    quantity: Optional[float] = Field(default=None, gt=0, lt=1e9, allow_inf_nan=False)
    override: bool = False
    override_reason: Optional[str] = Field(default=None, max_length=300)

    def plan(self) -> ticket.Plan:
        return ticket.Plan(**self.model_dump())


class RiskSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    risk_per_trade_pct: Optional[float] = None
    risk_per_trade_amount: Optional[float] = None
    daily_loss_pct: Optional[float] = None
    max_trades_per_day: Optional[int] = None
    min_rr: Optional[float] = None


@router.get("/risk")
async def risk(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    await _enabled(session, user)
    return await ticket.risk_status(session, user)


@router.put("/risk")
async def set_risk(body: RiskSettingsBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    await _enabled(session, user)
    values = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        change = await ticket.save_settings(session, user, values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**await ticket.risk_status(session, user), "change": change}


@router.post("/ticket/preview", dependencies=[Depends(preview_rate_limit)])
async def preview(body: PlanBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    await _enabled(session, user)
    view = await ticket.assess(session, user, body.plan())
    await session.commit()
    return view


@router.post("/ticket", dependencies=[Depends(place_rate_limit)])
async def place(body: PlanBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    """Places one PAPER order from the plan, or stores why not. Answers 200 either way, with the plan's status."""
    await _enabled(session, user)
    from app.ai.worker_bridge import price_lookup_for_user
    lookup = await price_lookup_for_user(session, user)            # the user's own entitlement (BT1), never a teammate's
    return await ticket.place(session, user, body.plan(), price_lookup=lookup)


@router.get("/plans")
async def plans(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[dict]:
    await _enabled(session, user)
    return await ticket.recent_plans(session, user)
