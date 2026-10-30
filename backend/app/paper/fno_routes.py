"""FNO-1: trading from the option chain in PAPER - the contract behind a chain cell, the caller's own positions on the
chain ("P"), and the one order ticket (preview with the confirm figures, then place). Behind `ui_v2` and the PAPER
account (`paper_account`, whose "Exit now" is the exit path of these positions): 404 while either is off.

Every answer is the caller's own (tenant + user). The only order this can lead to is ONE PAPER order through the
ordinary pipeline (app/paper/fno_ticket.py); there is no mode in the body and no LIVE path. Nothing here touches an exit.

- `GET  /api/paper/fno/contract`       the contract from the instrument master (trading symbol, lot and tick size); an
  option (`right` CE / PE with its strike) or, FNO-3, a future (`right` FUT, no strike).
- `GET  /api/paper/fno/held`           the caller's open PAPER option positions on one underlying and expiry.
- `POST /api/paper/fno/order/preview`  the ticket checked: price, units, margin, max loss, risk checks in plain words.
- `POST /api/paper/fno/order`          place it (at the live price; the pipeline checks again). TM-2: with
  `rest_if_waiting` a limit / stop the price has not reached waits in the resting book instead of being refused.
- `GET  /api/paper/fno/resting`        TM-2: the caller's own waiting orders and armed exits.
- `POST /api/paper/fno/resting/exit`   TM-2: arm a stop / target on the caller's own position (nothing is sent now).
- `POST /api/paper/fno/resting/{id}/cancel`  TM-2: cancel the caller's own waiting order.
CX (owner, 24 Oct), combined exits over the caller's own open positions (app/execution/group_exits.py; the trigger engine
fires them and closes the legs as one group, sold legs first - never blocked):
- `GET  /api/paper/fno/group-exits`               the caller's armed / exiting combined exits with the distance to trigger.
- `POST /api/paper/fno/group-exits/preview`       the plain sentence, the value now and the distance; nothing is stored.
- `POST /api/paper/fno/group-exits`               set one (idempotent on its reference).
- `POST /api/paper/fno/group-exits/{id}/edit`     change its levels (armed only, on the version shown).
- `POST /api/paper/fno/group-exits/{id}/cancel`   remove it (armed only; one already exiting finishes).
SX (owner, 24 Oct): `POST /api/paper/fno/basket/execute` may carry `exits` ("Exits for this strategy"); once legs fill,
they become one combined exit (origin SX) on the filled legs (app/execution/strategy_exits.py). No exits: none is set.

FNO-2, the basket (app/paper/fno_basket.py), same flags, same PAPER-only path - each leg is one ticket order:
- `POST /api/paper/fno/basket/evaluate`     the basket's payoff, max profit / loss, breakevens, PoP, Greeks, margin with
                                            the hedge (an estimate, labelled); computes only.
- `GET  /api/paper/fno/basket/settings`     the caller's own spread width / lots / sold-leg stop for the ready strategies.
- `POST /api/paper/fno/basket/preview`      the confirm step: every leg checked, hedge legs first, total margin and max loss.
- `POST /api/paper/fno/basket/execute`      every leg as a PAPER order, hedge legs first; per-leg results, never retried.
- `POST /api/paper/fno/basket/broker-link`  the legs pre-filled for the user's own broker app (ATP places nothing there).
TERM-2 step 4 (app/paper/fno_term.py, read only): `basket/evaluate` may carry `with_open` (R2 what-if, `term_whatif`)
and `margin_pair` (R4, `term_margin_pair`); `POST /api/paper/fno/roll/preview` (R1, `term_roll`) is the roll's preview -
the exit is Exit now / the group exit, the new legs the basket's own Place (two confirmed steps).

FNO-4, the watchlist's contract rows and the positions page (app/paper/fno_contracts.py), read only:
- `POST /api/paper/fno/contracts`         what saved contracts are: name, expiry, days left, "Expired" and the roll target
                                          (the same strike in the next expiry). Behind `ui_v2` only: it is reference data,
                                          reads no price and leads to no order (a roll only edits the watchlist).
- `GET  /api/paper/fno/positions/groups`  the caller's open PAPER legs that belong together (one basket, one structure),
                                          for the combined P&L. Same flags as the ticket. Every leg keeps its own Exit.

TT-8, a basket's legs as one group (app/paper/fno_groups.py) - exits, so no flag, kill switch or limit stops them:
- `POST /api/paper/fno/positions/{id}/exit-leg`    this leg only; a hedge before its sold leg: a warning until confirm.
- `POST /api/paper/fno/positions/{id}/exit-group`  every open leg of the basket, the sold legs first.

Terminal T2 (app/paper/fno_position_actions.py):
- `POST /api/paper/fno/positions/{id}/exit-part`   TT-9: N of the position's lots close now, the rest stays open (an exit:
                                                   never stopped by a flag, the kill switch, a limit or approval-only).
- `POST /api/paper/fno/positions/{id}/reverse`     TT-9: exit all, then the opposite side with the same lots through the
                                                   ticket (the entry half runs every entry check; `preview` changes nothing).
- `POST /api/paper/fno/resting/{id}/modify`        TT-10: the price / trigger / lots of the caller's own waiting order,
                                                   or the level of his own armed stop / target.
"""
import json
from datetime import date
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.execution.approval import ORIGIN_MANUAL
from app.paper import fno_basket, fno_contracts, fno_ticket

router = APIRouter(prefix="/api/paper/fno", tags=["paper"])
UI_V2_FLAG = "ui_v2"
MAX_PRICE = 1e7
SYMBOL = r"^[A-Za-z0-9&_\- ]{1,30}$"
# TT-2 (owner, 24 Oct; answers FNO-1-4): an order the user places and confirms himself on the ticket or the basket is his
# approval under approval-only mode. The origin is decided here, by the route - the request bodies forbid extra fields,
# so the browser cannot set it - and only these four routes (preview + place, basket preview + execute) pass it.
MANUAL = ORIGIN_MANUAL
place_rate_limit = user_rate_limit("paper_fno_order", limit=30, window_seconds=60)
basket_rate_limit = user_rate_limit("paper_fno_basket", limit=10, window_seconds=60)
evaluate_rate_limit = user_rate_limit("paper_fno_basket_eval", limit=120, window_seconds=60)
preview_rate_limit = user_rate_limit("paper_fno_preview", limit=120, window_seconds=60)
contracts_rate_limit = user_rate_limit("paper_fno_contracts", limit=60, window_seconds=60)


async def _enabled(session: AsyncSession, user: User) -> None:
    from app.paper import service
    from app.platform.controls import flag_enabled
    for flag in (UI_V2_FLAG, service.FLAG):
        if not await flag_enabled(session, flag, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": flag})


def _name(underlying: str) -> str:
    from app.option_chain.chain_view import underlying_name
    try:
        return underlying_name(underlying)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class OrderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    underlying: str = Field(pattern=SYMBOL)
    expiry: date
    strike: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)   # None for a future
    right: Literal["CE", "PE", "FUT"]
    side: Literal["BUY", "SELL"]
    lots: int = Field(ge=1, le=fno_ticket.MAX_LOTS)
    order_type: Literal["MARKET", "LIMIT", "SL", "SL-M"] = "MARKET"
    product: Literal["MIS", "NRML"] = "MIS"
    price: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    trigger_price: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    stop_loss: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    chain_ltp: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    client_ref: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9-]{8,64}$")
    # TM-2: "wait for my price" - a limit / stop the price has not reached rests in the resting book (needs client_ref).
    rest_if_waiting: bool = False
    trigger_mode: Literal["TOUCH", "CLOSE"] = "TOUCH"
    timeframe_minutes: Optional[int] = Field(default=None, ge=1, le=1440)

    @model_validator(mode="after")
    def _strike_for_options_only(self) -> "OrderBody":
        if self.right == "FUT" and self.strike is not None:
            raise ValueError("a future has no strike")
        if self.right != "FUT" and self.strike is None:
            raise ValueError("an option needs its strike")
        return self

    def ask(self) -> fno_ticket.OrderAsk:
        values = self.model_dump()
        values["underlying"] = _name(self.underlying)
        return fno_ticket.OrderAsk(**values)


@router.get("/contract")
async def contract(underlying: str = Query(pattern=SYMBOL), expiry: date = Query(),
                   strike: Optional[float] = Query(default=None, gt=0, lt=MAX_PRICE),
                   right: Literal["CE", "PE", "FUT"] = Query(), user: User = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    if (right == "FUT") != (strike is None):
        raise HTTPException(status_code=422, detail="An option needs its strike; a future has none.")
    try:
        found = await fno_ticket.find_contract(session, _name(underlying), expiry, strike, right)
    except fno_ticket.ContractNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return found.as_dict()


@router.get("/held")
async def held(underlying: str = Query(pattern=SYMBOL), expiry: date = Query(), user: User = Depends(get_current_user),
               session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    await _enabled(session, user)
    return await fno_ticket.held(session, user, _name(underlying), expiry)


@router.post("/order/preview", dependencies=[Depends(preview_rate_limit)])
async def preview(body: OrderBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    from app.ai.worker_bridge import price_lookup_for_user
    lookup = await price_lookup_for_user(session, user)            # the caller's own entitlement (BT1), never a teammate's
    view = await fno_ticket.assess(session, user, body.ask(), price_lookup=lookup, origin=MANUAL)
    await session.commit()
    return view


@router.post("/order", dependencies=[Depends(place_rate_limit)])
async def place(body: OrderBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Places one PAPER order, or says why not. Answers 200 either way, with the status."""
    await _enabled(session, user)
    from app.ai.worker_bridge import price_lookup_for_user
    lookup = await price_lookup_for_user(session, user)
    return await fno_ticket.place(session, user, body.ask(), price_lookup=lookup, origin=MANUAL)


# --- TM-2: the caller's own waiting orders and armed exits -------------------------------------------------------------

resting_rate_limit = user_rate_limit("paper_fno_resting", limit=60, window_seconds=60)


class ArmExitBody(BaseModel):
    """A stop or a target on the caller's own open position, waiting for its price (T-D2 / TT-7: every value his own)."""
    model_config = ConfigDict(extra="forbid")
    trade_id: int = Field(ge=1)
    kind: Literal["STOP", "TARGET"]
    order_type: Literal["SL-M", "LIMIT", "MARKET"]
    level: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)     # the premium level (PREMIUM)
    trigger_source: Literal["PREMIUM", "SPOT"] = "PREMIUM"
    spot_level: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    spot_side: Optional[Literal["ABOVE", "BELOW"]] = None
    trigger_mode: Literal["TOUCH", "CLOSE"] = "TOUCH"
    timeframe_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    client_ref: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")


@router.get("/resting")
async def resting(include_done: bool = Query(default=False), user: User = Depends(get_current_user),
                  session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    """The caller's own waiting orders and armed exits (with `include_done`, the recent finished ones too)."""
    await _enabled(session, user)
    from app.execution import resting_book
    return [resting_book.view(r) for r in await resting_book.list_for_user(session, user, include_final=include_done)]


@router.post("/resting/exit", dependencies=[Depends(resting_rate_limit)])
async def arm_exit(body: ArmExitBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Arms a stop or a target on the caller's own open position. Nothing is sent now: the trigger engine closes the
    position when the price reaches it (never blocked, ADR-0004)."""
    await _enabled(session, user)
    from app.db.models import TradeRecord
    from app.execution import resting_book
    trade = await session.get(TradeRecord, body.trade_id)
    if trade is None or trade.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Not found")
    if trade.user_id != user.id:
        raise HTTPException(status_code=403, detail="This is not your position")
    trigger = resting_book.Trigger(source=body.trigger_source, mode=body.trigger_mode, timeframe_minutes=body.timeframe_minutes,
                                   spot_level=body.spot_level, spot_side=body.spot_side)
    try:
        row = await resting_book.arm_exit(session, user, trade, client_ref=f"exit:{body.client_ref}", kind=body.kind,
                                          order_type=body.order_type, level=body.level or 0.0, trigger=trigger)
    except resting_book.RestingOrderError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return resting_book.view(row)


@router.post("/resting/{order_id}/cancel", dependencies=[Depends(resting_rate_limit)])
async def cancel_resting(order_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Cancels the caller's own waiting order (one already sent or done is returned as it is)."""
    await _enabled(session, user)
    from app.execution import resting_book
    try:
        row = await resting_book.cancel(session, user, order_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    return resting_book.view(row)


# --- CX: combined exits ------------------------------------------------------------------------------------------------

class GroupTrailBody(BaseModel):
    """CX2 optional trailing, the user's own numbers (no default): LOCK - once the gain reaches `after`, the stop moves to
    keep `lock`; TRAIL - every `step` of favourable move shifts the stop by `by`; SX2 LOCK_TRAIL - lock at `after`, then
    every further `step` shifts it by `by`; BREAK_EVEN - once the gain reaches `after`, the stop moves to break-even.
    ₹ for the combined MTM, points for a spot / premium level (SX: % of the net premium on the ticket). The stop only
    ever moves in the trade's favour (app/execution/group_exits.py)."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["LOCK", "TRAIL", "LOCK_TRAIL", "BREAK_EVEN"]
    after: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    lock: Optional[float] = Field(default=None, ge=0, lt=MAX_PRICE, allow_inf_nan=False)
    step: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    by: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)

    def as_tuple(self) -> Any:
        keys = {"LOCK": ("after", "lock"), "TRAIL": ("step", "by"), "LOCK_TRAIL": ("after", "lock", "step", "by"),
                "BREAK_EVEN": ("after",)}[self.kind]
        return self.kind, {k: getattr(self, k) for k in keys if getattr(self, k) is not None}


class GroupExitBody(BaseModel):
    """One combined stop-loss / target (CX1-CX3): the user's own numbers; the server reads the value now itself."""
    model_config = ConfigDict(extra="forbid")
    scope: Literal["SELECTED", "ALL_OPEN", "GROUP"] = "SELECTED"
    trade_ids: List[int] = Field(default_factory=list, max_length=50)
    measure: Literal["MTM_INR", "NET_PREMIUM", "SPOT"]
    sl_value: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    target_value: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    spot_symbol: Optional[str] = Field(default=None, pattern=SYMBOL)
    spot_exchange: Optional[Literal["NSE", "BSE"]] = None
    check_mode: Literal["TOUCH", "CLOSE"] = "TOUCH"
    timeframe_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    basket_ref: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9:_-]{1,64}$")
    client_ref: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")
    trailing: Optional[GroupTrailBody] = None


class GroupExitEditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    sl_value: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    target_value: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    check_mode: Optional[Literal["TOUCH", "CLOSE"]] = None
    timeframe_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    # Absent: the trailing stays as it is; null: removed; an object: replaced (CX2).
    trailing: Optional[GroupTrailBody] = None


async def _group_ask(session: AsyncSession, user: User, body: GroupExitBody) -> Any:
    from app.execution import group_exits
    ids = await group_exits.all_open_ids(session, user) if body.scope == "ALL_OPEN" else list(body.trade_ids)
    kind, spec = body.trailing.as_tuple() if body.trailing is not None else (group_exits.TRAIL_NONE, None)
    return group_exits.GroupExitAsk(client_ref=f"cx:{body.client_ref}", trade_ids=ids, measure=body.measure,
                                    sl_value=body.sl_value, target_value=body.target_value, spot_symbol=body.spot_symbol,
                                    spot_exchange=body.spot_exchange, check_mode=body.check_mode,
                                    timeframe_minutes=body.timeframe_minutes, scope=body.scope, origin="CX",
                                    basket_ref=body.basket_ref, trail_kind=kind, trail=spec)


async def _lookup(session: AsyncSession, user: User) -> Any:
    """The person's own live price (what "Exit now" reads)."""
    from app.ai.worker_bridge import price_lookup_for_user
    return await price_lookup_for_user(session, user)


async def _value_now(session: AsyncSession, user: User, ask: Any) -> Optional[float]:
    from app.execution import group_exits
    trades = await group_exits.own_open_trades(session, user, ask.trade_ids)
    return await group_exits.current_value(session, ask.measure, trades, await _lookup(session, user),
                                           spot_symbol=ask.spot_symbol, spot_exchange=ask.spot_exchange)


@router.get("/group-exits")
async def group_exits_list(include_done: bool = Query(default=False), user: User = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    """The caller's own combined exits (armed / exiting; with `include_done`, the finished ones too)."""
    await _enabled(session, user)
    from app.execution import group_exits
    return [group_exits.view(r) for r in await group_exits.list_for_user(session, user, include_final=include_done)]


@router.post("/group-exits/preview", dependencies=[Depends(resting_rate_limit)])
async def group_exit_preview(body: GroupExitBody, user: User = Depends(require_trader),
                             session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """CX6: what would be set, in plain words, with the value now and the distance to each level. Nothing is stored."""
    await _enabled(session, user)
    from app.db.models import GroupExitRecord
    from app.execution import group_exits
    ask = await _group_ask(session, user, body)
    trades = await group_exits.own_open_trades(session, user, ask.trade_ids)
    errors = group_exits.ask_errors(ask, trades)
    value = None if errors else await _value_now(session, user, ask)
    draft = GroupExitRecord(measure=ask.measure, sl_value=ask.sl_value, target_value=ask.target_value,
                            spot_symbol=ask.spot_symbol, check_mode=ask.check_mode, basis_value=value,
                            trail_kind=ask.trail_kind if not errors else group_exits.TRAIL_NONE,
                            trail_json=None if errors else group_exits.trail_json_of(ask.trail_kind, ask.trail),
                            legs_json=json.dumps([group_exits.leg_view(t) for t in trades]))
    if value is not None and ask.measure != group_exits.MTM_INR:
        draft.sl_side = group_exits.side_for(ask.sl_value, value) if ask.sl_value is not None else None
        draft.target_side = group_exits.side_for(ask.target_value, value) if ask.target_value is not None else None
    if value is None and not errors and ask.measure != group_exits.MTM_INR:
        errors.append("No live price for the group right now - try again in a moment")
    return {"ok": not errors, "reasons": errors, "words": group_exits.describe(draft), "value_now": value,
            "distance": group_exits.distance(draft, value), "legs": group_exits.legs_of(draft)}


@router.post("/group-exits", dependencies=[Depends(resting_rate_limit)])
async def group_exit_create(body: GroupExitBody, user: User = Depends(require_trader),
                            session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Sets one combined exit on the caller's own open positions. Nothing is sent now: the trigger engine closes the legs
    as one group when it is reached (never blocked, ADR-0004)."""
    await _enabled(session, user)
    from app.execution import group_exits
    ask = await _group_ask(session, user, body)
    value = await _value_now(session, user, ask)
    try:
        row = await group_exits.create(session, user, ask, now_value=value)
    except group_exits.GroupExitError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row.last_value = value if value is not None else row.last_value
    return group_exits.view(row)


@router.post("/group-exits/{exit_id}/edit", dependencies=[Depends(resting_rate_limit)])
async def group_exit_edit(exit_id: int, body: GroupExitEditBody, user: User = Depends(require_trader),
                          session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Changes the levels of the caller's own armed combined exit (CX5 / SX6)."""
    await _enabled(session, user)
    from app.db.models import GroupExitRecord
    from app.execution import group_exits
    row = await session.get(GroupExitRecord, exit_id)
    if row is None or row.tenant_id != user.tenant_id or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    value = None
    if row.measure != group_exits.MTM_INR:
        trades = await group_exits.own_open_trades(session, user, group_exits.trade_ids(row))
        value = await group_exits.current_value(session, row.measure, [t for t in trades if t.exit_time is None],
                                                await _lookup(session, user), spot_symbol=row.spot_symbol,
                                                spot_exchange=row.spot_exchange)
    try:
        row = await group_exits.edit(session, user, exit_id, version=body.version, sl_value=body.sl_value,
                                     target_value=body.target_value, check_mode=body.check_mode,
                                     timeframe_minutes=body.timeframe_minutes, now_value=value,
                                     trailing=_edit_trailing(body))
    except group_exits.GroupExitError as exc:
        await session.rollback()
        raise HTTPException(status_code=409 if "another screen" in str(exc) or "meanwhile" in str(exc) else 422,
                            detail=str(exc)) from exc
    return group_exits.view(row)


def _edit_trailing(body: GroupExitEditBody) -> Any:
    """None keeps the trailing; ("NONE", None) removes it; else the new kind and numbers."""
    if "trailing" not in body.model_fields_set:
        return None
    return body.trailing.as_tuple() if body.trailing is not None else ("NONE", None)


@router.post("/group-exits/{exit_id}/cancel", dependencies=[Depends(resting_rate_limit)])
async def group_exit_cancel(exit_id: int, user: User = Depends(require_trader),
                            session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Removes the caller's own armed combined exit (one already closing its legs finishes)."""
    await _enabled(session, user)
    from app.execution import group_exits
    try:
        row = await group_exits.cancel(session, user, exit_id)
    except group_exits.GroupExitError as exc:
        status = 404 if "not found" in str(exc) else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return group_exits.view(row)


# --- FNO-2: the basket ------------------------------------------------------------------------------------------------

class BasketLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    underlying: str = Field(pattern=SYMBOL)
    expiry: date
    strike: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)   # None for a future (FNO-3)
    right: Literal["CE", "PE", "FUT"]
    side: Literal["BUY", "SELL"]
    lots: int = Field(ge=1, le=fno_ticket.MAX_LOTS)
    price: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    stop_loss: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    iv: Optional[float] = Field(default=None, gt=0, lt=5, allow_inf_nan=False)

    @model_validator(mode="after")
    def _strike_for_options_only(self) -> "BasketLeg":
        if self.right == "FUT" and self.strike is not None:
            raise ValueError("a future has no strike")
        if self.right != "FUT" and self.strike is None:
            raise ValueError("an option needs its strike")
        return self

    def ask(self) -> fno_basket.LegAsk:
        values = self.model_dump()
        values["underlying"] = _name(self.underlying)
        return fno_basket.LegAsk(**values)


class BasketBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legs: List[BasketLeg] = Field(min_length=1, max_length=fno_basket.MAX_LEGS)
    spot: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    product: Literal["MIS", "NRML"] = "MIS"

    def asks(self) -> List[fno_basket.LegAsk]:
        legs = [leg.ask() for leg in self.legs]
        if len({leg.underlying for leg in legs}) > 1:
            raise HTTPException(status_code=422, detail="A basket holds one underlying. Make a separate basket for the other.")
        return legs


class StrategyExitsBody(BaseModel):
    """SX1 "Exits for this strategy": the user's own overall stop-loss / target over the basket's filled legs - combined
    ₹ MTM, % of the net premium, or the underlying's spot level - and an optional trailing. Nothing is filled in."""
    model_config = ConfigDict(extra="forbid")
    measure: Literal["MTM_INR", "PREMIUM_PCT", "SPOT"]
    sl_value: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    target_value: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    trailing: Optional[GroupTrailBody] = None


class ExecuteBody(BasketBody):
    basket_ref: str = Field(pattern=r"^[A-Za-z0-9-]{8,48}$", description="the panel's own id: a second tap never places twice")
    exits: Optional[StrategyExitsBody] = None


class EvaluateBody(BasketBody):
    """TERM-2 step 4: `with_open` (R2 what-if, flag `term_whatif`) adds the combined payoff of his own open PAPER legs on
    the same underlying and expiries; `margin_pair` (R4, flag `term_margin_pair`) adds margin required / available. Each
    is ignored while its flag (or `terminal`) is off, so the answer is exactly as before."""
    with_open: bool = False
    margin_pair: bool = False


@router.post("/basket/evaluate", dependencies=[Depends(evaluate_rate_limit)])
async def basket_evaluate(body: EvaluateBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    if body.spot is None:
        raise HTTPException(status_code=422, detail="The underlying's price (spot) is needed for the payoff.")
    legs = body.asks()
    out = await fno_basket.evaluate(session, legs, body.spot)
    if not out.get("ok") or not (body.with_open or body.margin_pair):
        return out
    from app.paper import fno_term
    if body.with_open and await fno_term.enabled(session, user.tenant_id, fno_term.WHATIF_FLAG):
        out["whatif"] = await fno_term.whatif(session, user, legs, body.spot)
    if body.margin_pair and await fno_term.enabled(session, user.tenant_id, fno_term.MARGIN_PAIR_FLAG):
        out["margin_pair"] = await fno_term.margin_pair(session, user, legs, out, body.product)
    return out


# --- TERM-2 R1: Roll to the next weekly (a preview; the exit and the new legs are two confirmed steps) --------------------

class RollBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_ids: list[int] = Field(min_length=1, max_length=fno_basket.MAX_LEGS)
    spot: float | None = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)


@router.post("/roll/preview", dependencies=[Depends(preview_rate_limit)])
async def roll_preview(body: RollBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """The roll of his own open PAPER legs: the exit of each leg and the same strikes on the next listed expiry, with the
    new legs' numbers from the basket's engine. Reads only; places and closes nothing (the exit is Exit now, the new
    legs are the basket's own Place). Flags ui_v2 + paper_account + terminal + term_roll (404 otherwise)."""
    await _enabled(session, user)
    from app.paper import fno_term
    if not await fno_term.enabled(session, user.tenant_id, fno_term.ROLL_FLAG):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": fno_term.ROLL_FLAG})
    from app.ai.worker_bridge import price_lookup_for_user
    lookup = await price_lookup_for_user(session, user)
    return await fno_term.roll_preview(session, user, body.trade_ids, price_lookup=lookup, spot=body.spot)


@router.get("/basket/settings")
async def basket_settings(underlying: str = Query(pattern=SYMBOL), user: User = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    return await fno_basket.settings(session, user, _name(underlying))


@router.post("/basket/preview", dependencies=[Depends(preview_rate_limit)])
async def basket_preview(body: BasketBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    from app.ai.worker_bridge import price_lookup_for_user
    lookup = await price_lookup_for_user(session, user)
    view = await fno_basket.preview(session, user, body.asks(), product=body.product, spot=body.spot, price_lookup=lookup, origin=MANUAL)
    await session.commit()
    return view


@router.post("/basket/execute", dependencies=[Depends(basket_rate_limit)])
async def basket_execute(body: ExecuteBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Places every leg as a PAPER order (hedge legs first), or says why not. Answers 200 either way, per leg."""
    await _enabled(session, user)
    from app.ai.worker_bridge import price_lookup_for_user
    lookup = await price_lookup_for_user(session, user)
    exits = None
    if body.exits is not None:
        from app.execution.strategy_exits import StrategyExitAsk
        kind, spec = body.exits.trailing.as_tuple() if body.exits.trailing is not None else ("NONE", None)
        exits = StrategyExitAsk(measure=body.exits.measure, sl_value=body.exits.sl_value,
                                target_value=body.exits.target_value, trail_kind=kind, trail=spec)
    return await fno_basket.execute(session, user, body.asks(), product=body.product, basket_ref=body.basket_ref, price_lookup=lookup,
                                    origin=MANUAL, exits=exits)


class CloseFilledBody(BaseModel):
    trade_ids: List[int] = Field(min_length=1, max_length=fno_basket.MAX_LEGS * 4)


async def _smart_enabled(session: AsyncSession, user: User) -> None:
    await _enabled(session, user)
    from app.execution.smart_execution import FLAG
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": FLAG})


@router.post("/basket/close-filled", dependencies=[Depends(basket_rate_limit)])
async def basket_close_filled(body: CloseFilledBody, user: User = Depends(require_trader),
                              session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Part 7 / TM-11 "Close the filled legs": the user's own open legs of an incomplete basket, closed through Exit now,
    the sold legs first (a hedge is never closed while its short is open). Answers 200 with each leg's outcome."""
    await _smart_enabled(session, user)
    return await fno_basket.close_filled(session, user, body.trade_ids)


class LegExitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False


@router.post("/positions/{trade_id}/exit-leg")
async def exit_leg(trade_id: int, body: Optional[LegExitBody] = None, user: User = Depends(require_trader),
                   session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """TT-8 "Exit this leg": closes this leg of a basket only. An exit (ADR-0004): no flag, kill switch, limit or
    approval-only rule stops it. A hedge whose sold leg is still open answers {"status": "CONFIRM", "warning"} and closes
    nothing until the body carries {"confirm": true} - a warning, never a refusal."""
    from app.paper import fno_groups
    return await fno_groups.exit_leg(session, user, trade_id, confirm=bool(body and body.confirm))


@router.post("/positions/{trade_id}/exit-group")
async def exit_group(trade_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """TT-8 "Exit the whole group": every open leg of this leg's basket, the sold legs first. An exit (ADR-0004): never
    stopped by a flag, the kill switch, a limit or approval-only."""
    from app.paper import fno_groups
    return await fno_groups.exit_group(session, user, trade_id)


# --- Terminal T2: Exit part, Reverse (TT-9) and Modify (TT-10) -----------------------------------------------------------

REF = r"^[A-Za-z0-9-]{8,64}$"


class ExitPartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lots: int = Field(ge=1, le=fno_ticket.MAX_LOTS * 10)
    client_ref: str = Field(pattern=REF)
    confirm: bool = False


class ReverseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_ref: str = Field(pattern=REF)
    stop_loss: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)   # the new side's own stop
    preview: bool = False
    confirm: bool = False


class ModifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    price: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    trigger_price: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    lots: Optional[int] = Field(default=None, ge=1, le=fno_ticket.MAX_LOTS)
    level: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    spot_level: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)


@router.post("/positions/{trade_id}/exit-part")
async def exit_part(trade_id: int, body: ExitPartBody, user: User = Depends(require_trader),
                    session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """TT-9 "Exit part": closes `lots` of this position now, priced as Exit now; the rest stays open. An exit (ADR-0004):
    no flag, kill switch, limit or approval-only rule stops it. More lots than are open: 422 in plain words, nothing
    closed. A hedge whose sold leg is open answers {"status": "CONFIRM"} until `confirm`. Same `client_ref`: the first result."""
    from app.paper import fno_position_actions
    return await fno_position_actions.exit_part(session, user, trade_id, lots=body.lots, client_ref=body.client_ref,
                                                confirm=body.confirm)


@router.post("/positions/{trade_id}/reverse", dependencies=[Depends(place_rate_limit)])
async def reverse(trade_id: int, body: ReverseBody, user: User = Depends(require_trader),
                  session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """TT-9 "Reverse": the whole position closes (never blocked), then the opposite side opens with the same lots through
    the ticket (TT-2: his own confirmed order, origin manual; every entry check runs). `preview`: the new side's confirm
    figures, nothing changes. A double tap with the same `client_ref` never exits or opens twice."""
    await _enabled(session, user)
    from app.paper import fno_position_actions
    return await fno_position_actions.reverse(session, user, trade_id, client_ref=body.client_ref, stop_loss=body.stop_loss,
                                              preview=body.preview, confirm=body.confirm)


@router.post("/resting/{order_id}/modify", dependencies=[Depends(resting_rate_limit)])
async def modify_resting(order_id: int, body: ModifyBody, user: User = Depends(require_trader),
                         session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """TT-10: changes the caller's own waiting order (404 for anyone else's); one already sent or done answers 409."""
    await _enabled(session, user)
    from app.paper import fno_position_actions
    return await fno_position_actions.modify_resting(session, user, order_id, price=body.price, trigger_price=body.trigger_price,
                                                     lots=body.lots, level=body.level, spot_level=body.spot_level)


@router.get("/settlement")
async def settlement(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Part 7: the physical-settlement warning for the caller's own open stock F&O positions (ITM or within his band,
    the days before expiry from his own settings), with the delivery value. Information only."""
    await _smart_enabled(session, user)
    from app.paper import settlement_watch
    return await settlement_watch.for_user(session, user)


@router.post("/basket/broker-link")
async def basket_broker_link(body: BasketBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    return await fno_basket.broker_link(session, user, body.asks())


# --- FNO-4: contract rows (watchlists) and position groups --------------------------------------------------------------

class ContractItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    symbol: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9 &._:-]{1,40}$")
    exchange: str = Field(min_length=2, max_length=12, pattern=r"^[A-Za-z_]{2,12}$")


class ContractsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: List[ContractItem] = Field(min_length=1, max_length=fno_contracts.MAX_ITEMS)


@router.post("/contracts", dependencies=[Depends(contracts_rate_limit)])
async def contracts(body: ContractsBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, UI_V2_FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": UI_V2_FLAG})
    items = await fno_contracts.lookup(session, [(i.exchange, " ".join(i.symbol.split())) for i in body.items])
    return {"items": items}


@router.get("/positions/groups")
async def position_groups(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    return {"groups": await fno_contracts.position_groups(session, user)}
