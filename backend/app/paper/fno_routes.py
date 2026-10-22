"""FNO-1: trading from the option chain in PAPER - the contract behind a chain cell, the caller's own positions on the
chain ("P"), and the one order ticket (preview with the confirm figures, then place). Behind `ui_v2` and the PAPER
account (`paper_account`, whose "Exit now" is the exit path of these positions): 404 while either is off.

Every answer is the caller's own (tenant + user). The only order this can lead to is ONE PAPER order through the
ordinary pipeline (app/paper/fno_ticket.py); there is no mode in the body and no LIVE path. Nothing here touches an exit.

- `GET  /api/paper/fno/contract`       the contract from the instrument master (trading symbol, lot and tick size); an
  option (`right` CE / PE with its strike) or, FNO-3, a future (`right` FUT, no strike).
- `GET  /api/paper/fno/held`           the caller's open PAPER option positions on one underlying and expiry.
- `POST /api/paper/fno/order/preview`  the ticket checked: price, units, margin, max loss, risk checks in plain words.
- `POST /api/paper/fno/order`          place it (at the live price; the pipeline checks again).

FNO-2, the basket (app/paper/fno_basket.py), same flags, same PAPER-only path - each leg is one ticket order:
- `POST /api/paper/fno/basket/evaluate`     the basket's payoff, max profit / loss, breakevens, PoP, Greeks, margin with
                                            the hedge (an estimate, labelled); computes only.
- `GET  /api/paper/fno/basket/settings`     the caller's own spread width / lots / sold-leg stop for the ready strategies.
- `POST /api/paper/fno/basket/preview`      the confirm step: every leg checked, hedge legs first, total margin and max loss.
- `POST /api/paper/fno/basket/execute`      every leg as a PAPER order, hedge legs first; per-leg results, never retried.
- `POST /api/paper/fno/basket/broker-link`  the legs pre-filled for the user's own broker app (ATP places nothing there).
"""
from datetime import date
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.paper import fno_basket, fno_ticket

router = APIRouter(prefix="/api/paper/fno", tags=["paper"])
UI_V2_FLAG = "ui_v2"
MAX_PRICE = 1e7
SYMBOL = r"^[A-Za-z0-9&_\- ]{1,30}$"
place_rate_limit = user_rate_limit("paper_fno_order", limit=30, window_seconds=60)
basket_rate_limit = user_rate_limit("paper_fno_basket", limit=10, window_seconds=60)
evaluate_rate_limit = user_rate_limit("paper_fno_basket_eval", limit=120, window_seconds=60)
preview_rate_limit = user_rate_limit("paper_fno_preview", limit=120, window_seconds=60)


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
    view = await fno_ticket.assess(session, user, body.ask(), price_lookup=lookup)
    await session.commit()
    return view


@router.post("/order", dependencies=[Depends(place_rate_limit)])
async def place(body: OrderBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Places one PAPER order, or says why not. Answers 200 either way, with the status."""
    await _enabled(session, user)
    from app.ai.worker_bridge import price_lookup_for_user
    lookup = await price_lookup_for_user(session, user)
    return await fno_ticket.place(session, user, body.ask(), price_lookup=lookup)


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


class ExecuteBody(BasketBody):
    basket_ref: str = Field(pattern=r"^[A-Za-z0-9-]{8,48}$", description="the panel's own id: a second tap never places twice")


@router.post("/basket/evaluate", dependencies=[Depends(evaluate_rate_limit)])
async def basket_evaluate(body: BasketBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    if body.spot is None:
        raise HTTPException(status_code=422, detail="The underlying's price (spot) is needed for the payoff.")
    return await fno_basket.evaluate(session, body.asks(), body.spot)


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
    view = await fno_basket.preview(session, user, body.asks(), product=body.product, spot=body.spot, price_lookup=lookup)
    await session.commit()
    return view


@router.post("/basket/execute", dependencies=[Depends(basket_rate_limit)])
async def basket_execute(body: ExecuteBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Places every leg as a PAPER order (hedge legs first), or says why not. Answers 200 either way, per leg."""
    await _enabled(session, user)
    from app.ai.worker_bridge import price_lookup_for_user
    lookup = await price_lookup_for_user(session, user)
    return await fno_basket.execute(session, user, body.asks(), product=body.product, basket_ref=body.basket_ref, price_lookup=lookup)


@router.post("/basket/broker-link")
async def basket_broker_link(body: BasketBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _enabled(session, user)
    return await fno_basket.broker_link(session, user, body.asks())
