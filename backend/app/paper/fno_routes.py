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
from app.paper import fno_ticket

router = APIRouter(prefix="/api/paper/fno", tags=["paper"])
UI_V2_FLAG = "ui_v2"
MAX_PRICE = 1e7
SYMBOL = r"^[A-Za-z0-9&_\- ]{1,30}$"
place_rate_limit = user_rate_limit("paper_fno_order", limit=30, window_seconds=60)
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
