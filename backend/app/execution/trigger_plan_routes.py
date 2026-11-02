"""M75 W-8 (owner order M75 CW-6): his own trigger plans (`ui_v2` + `trigger_plans`; 404 while either is off).

* `GET  /api/trigger-plans`                 - his plans, newest first (Armed / Paused / Triggered / Done / Expired / Cancelled).
* `POST /api/trigger-plans`                 - arms a plan: his condition, order(s), exits, validity and choice "place at
                                              once" / "ask me first" (Not set = his Trade settings; neither = 422 asks).
                                              The client reference makes a second tap return the same plan.
* `PUT  /api/trigger-plans/{id}`            - edits an armed or paused plan (armed afresh from now).
* `POST /api/trigger-plans/{id}/pause`      - pauses an armed plan; `/resume` arms it again; `/cancel` cancels it.
* `POST /api/trigger-plans/dry-run`         - M76 MP-12 (D-2): a dry run of one of his saved plans (`plan_id`) or of the
                                              plan he is drafting - his condition replayed over the stored closed bars
                                              of the last N sessions he asks for, and what each order would send now
                                              (the ticket's own read-only checks). Writes nothing (the session is rolled
                                              back); app/execution/plan_dry_run.py.
Nothing here places an order: only the worker fires a plan (app/execution/trigger_plans.py), through the order ticket's
own `place`. Self-scoped: another member's plan is "not found". The AI has no tool that reaches these (ADR-0006).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.execution import plan_dry_run as pdr
from app.execution import trigger_plans as tp

router = APIRouter(prefix="/api/trigger-plans", tags=["trigger-plans"])
plan_rate_limit = user_rate_limit("trigger_plans", limit=30, window_seconds=60)
dry_run_rate_limit = user_rate_limit("trigger_plan_dry_run", limit=6, window_seconds=60)   # each one replays many candles
SYMBOL = r"^[A-Za-z0-9 &._-]{1,40}$"
MAX_PRICE = 10_000_000


class ConditionBody(BaseModel):
    """W-8: a price level (kind PRICE, the default). W-9: his area + the reader's rejection candle (AREA) or a BOS /
    CHoCH (STRUCTURE) on his timeframe; every field his own, checked by `trigger_plans.condition_errors`."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["PRICE", "AREA", "STRUCTURE"] = "PRICE"
    level: float | None = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    side: Literal["ABOVE", "BELOW"] | None = None
    mode: Literal["TOUCH", "CLOSE"] | None = None
    timeframe_minutes: int | None = Field(default=None, ge=1, le=tp.MAX_TIMEFRAME)
    window_start: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    window_end: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    area_side: Literal["buyer", "seller"] | None = None
    area_low: float | None = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    area_high: float | None = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    area_timeframe: str | None = Field(default=None, pattern=r"^[0-9A-Za-z]{1,8}$")
    event: Literal["BOS", "CHOCH"] | None = None
    direction: Literal["up", "down"] | None = None

    def condition(self) -> tp.Condition:
        if self.kind == "PRICE":
            return tp.Condition(level=self.level or 0.0, side=self.side or "", mode=self.mode or "", timeframe_minutes=self.timeframe_minutes,
                                window_start=self.window_start, window_end=self.window_end)
        area = self.kind == "AREA"
        return tp.Condition(kind=self.kind, timeframe_minutes=self.timeframe_minutes, window_start=self.window_start, window_end=self.window_end,
                            area_side=self.area_side if area else None, area_low=self.area_low if area else None,
                            area_high=self.area_high if area else None, area_timeframe=self.area_timeframe if area else None,
                            event=None if area else self.event, direction=None if area else self.direction)


class OrderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expiry: date
    strike: float | None = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    right: Literal["CE", "PE", "FUT"]
    side: Literal["BUY", "SELL"]
    lots: int = Field(ge=1, le=1000)
    order_type: Literal["MARKET", "LIMIT"]
    product: Literal["MIS", "NRML"]
    price: float | None = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    stop_loss: float | None = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    target: float | None = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)

    def plan_order(self) -> tp.PlanOrder:
        return tp.PlanOrder(expiry=self.expiry.isoformat(), strike=self.strike, right=self.right, side=self.side, lots=self.lots,
                            order_type=self.order_type, product=self.product, price=self.price, stop_loss=self.stop_loss, target=self.target)


class PlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=80)
    condition: ConditionBody
    orders: list[OrderBody] = Field(min_length=1, max_length=tp.MAX_ORDERS)
    approval: Literal["AUTO", "APPROVAL"] | None = None          # Not sent: his Trade settings; neither: the server asks
    valid_until: datetime

    def parts(self) -> tuple[tp.Condition, list[tp.PlanOrder], datetime]:
        cond = self.condition.condition()
        until = self.valid_until if self.valid_until.tzinfo else self.valid_until.replace(tzinfo=UTC)
        return cond, [o.plan_order() for o in self.orders], until


class CreateBody(PlanBody):
    underlying: str = Field(pattern=SYMBOL)
    client_ref: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")

    @model_validator(mode="after")
    def _watch(self) -> CreateBody:
        if not self.underlying.strip():
            raise ValueError("Choose the underlying the plan watches")
        return self


class DryRunBody(BaseModel):
    """M76 MP-12: his saved plan (`plan_id`) or his draft (underlying + condition [+ orders + his choice]); `sessions` is
    his own (no default)."""
    model_config = ConfigDict(extra="forbid")
    sessions: int = Field(ge=1, le=pdr.MAX_SESSIONS)
    plan_id: int | None = Field(default=None, ge=1)
    underlying: str | None = Field(default=None, pattern=SYMBOL)
    condition: ConditionBody | None = None
    orders: list[OrderBody] = Field(default_factory=list, max_length=tp.MAX_ORDERS)
    approval: Literal["AUTO", "APPROVAL"] | None = None

    @model_validator(mode="after")
    def _one(self) -> DryRunBody:
        draft = self.underlying is not None or self.condition is not None or bool(self.orders) or self.approval is not None
        saved_ok = self.plan_id is not None and not draft
        draft_ok = self.plan_id is None and self.underlying is not None and self.condition is not None
        if not (saved_ok or draft_ok):
            raise ValueError("Send a saved plan's id, or the underlying and the condition of the plan you are drafting")
        return self


async def _enabled(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    for name in ("ui_v2", tp.FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


def _underlying(raw: str) -> tuple[str, str, str]:
    """(the F&O underlying name, the symbol whose price is watched, its exchange): `NIFTY 50` -> NIFTY, `NIFTY 50`, NSE."""
    from app.option_chain.chain_view import spot_instrument, underlying_name
    try:
        name = underlying_name(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    symbol, exchange = spot_instrument(name)
    return name, symbol, exchange


@router.get("")
async def list_plans(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    await _enabled(session, user)
    return [tp.view(r) for r in await tp.list_for_user(session, user)]


@router.post("", dependencies=[Depends(plan_rate_limit)])
async def create_plan(body: CreateBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _enabled(session, user)
    name, symbol, exchange = _underlying(body.underlying)
    cond, orders, until = body.parts()
    try:
        row = await tp.create(session, user, client_ref=body.client_ref, name=body.name, underlying=name, watch_symbol=symbol,
                              watch_exchange=exchange, condition=cond, orders=orders, approval=body.approval, valid_until=until)
    except tp.PlanError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return tp.view(row)


@router.put("/{plan_id}", dependencies=[Depends(plan_rate_limit)])
async def edit_plan(plan_id: int, body: PlanBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _enabled(session, user)
    cond, orders, until = body.parts()
    try:
        row = await tp.edit(session, user, plan_id, condition=cond, orders=orders, approval=body.approval, valid_until=until, name=body.name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    except tp.PlanError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return tp.view(row)


@router.post("/dry-run", dependencies=[Depends(dry_run_rate_limit)])
async def dry_run_plan(body: DryRunBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Read-only: nothing is placed, armed, proposed or changed; the session is rolled back whatever happens."""
    await _enabled(session, user)
    from app.ai.worker_bridge import price_lookup_for_user
    choice: str | None
    try:
        if body.plan_id is not None:
            try:
                row = await tp.own(session, user, body.plan_id)
            except LookupError as exc:
                raise HTTPException(status_code=404, detail="Not found") from exc
            name, symbol, exchange = row.underlying, row.watch_symbol, row.watch_exchange
            cond, orders, choice = tp.condition_of(row), tp.orders_of(row), row.approval
        else:
            assert body.underlying is not None and body.condition is not None
            name, symbol, exchange = _underlying(body.underlying)
            cond, orders, choice = body.condition.condition(), [o.plan_order() for o in body.orders], body.approval
        lookup = await price_lookup_for_user(session, user) if orders else None   # his own entitlement, as the ticket's preview
        try:
            return await pdr.dry_run(session, user, underlying=name, symbol=symbol, exchange=exchange, condition=cond, orders=orders,
                                     choice=choice, sessions=body.sessions, plan_id=body.plan_id, price_lookup=lookup)
        except pdr.DryRunError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await session.rollback()                     # a dry run writes nothing


@router.post("/{plan_id}/{action}", dependencies=[Depends(plan_rate_limit)])
async def change_plan(plan_id: int, action: Literal["pause", "resume", "cancel"], user: User = Depends(require_trader),
                      session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _enabled(session, user)
    fn = {"pause": tp.pause, "resume": tp.resume, "cancel": tp.cancel}[action]
    try:
        row = await fn(session, user, plan_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    except tp.PlanError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return tp.view(row)
