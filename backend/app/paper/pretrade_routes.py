"""M76 MP-2: the check card's two calls (flag `trade_warnings`, with the F&O ticket's own flags; 404 while off).

* ``POST /api/paper/fno/order/checks`` - the draft on the ticket (no order is sent anywhere): the findings ready within
  300 ms, the ids still `pending` (ask again with ``only``), the types he accepted today for this contract, and his
  switches (on / ask me) so the card folds in the preview's and the screen's own lines by the same rules. Read-only.
* ``POST /api/paper/fno/order/checks/accept`` - he pressed Place with these lines shown: each type is not shown again
  today for this contract. Fire and forget for the ticket (it never waits); records nothing about the order itself.

Neither call can hold back, change or place an order, and neither is on any exit path.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import require_trader
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.paper import pretrade_checks as pc
from app.paper import pretrade_warnings as pw
from app.paper.fno_routes import MAX_PRICE, SYMBOL, _enabled, _name

router = APIRouter(prefix="/api/paper/fno/order", tags=["paper"])
FLAG = "trade_warnings"
checks_rate_limit = user_rate_limit("pretrade_checks", limit=240, window_seconds=60)


def session_factory() -> pc.SessionFactory:
    """The database sessions the checks open, one per check (tests override this dependency)."""
    from app.db import session as db
    return db._session_factory  # noqa: SLF001


class CheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    underlying: str = Field(pattern=SYMBOL)
    expiry: date
    strike: float | None = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    right: Literal["CE", "PE", "FUT"]
    side: Literal["BUY", "SELL"]
    lots: int = Field(ge=1, le=10_000)
    order_type: Literal["MARKET", "LIMIT", "SL", "SL-M"] | None = None
    product: Literal["MIS", "NRML"] | None = None
    price: float | None = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    ltp: float | None = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)     # the LTP on his screen
    only: list[str] | None = Field(default=None, max_length=20)
    # M76 MP-3: the workspace's open chart (instrument key + timeframe) when the ticket opens beside it (M76Q-7)
    chart_instrument: str | None = Field(default=None, min_length=1, max_length=100)
    chart_tf: Literal["1m", "3m", "5m", "15m", "30m", "1h", "1d"] | None = None


class AcceptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    underlying: str = Field(pattern=SYMBOL)
    expiry: date
    strike: float | None = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    right: Literal["CE", "PE", "FUT"]
    types: list[str] = Field(max_length=len(pw.WARNING_IDS))


async def _on(session: Any, user: User) -> None:
    await _enabled(session, user)
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": FLAG})


@router.post("/checks", dependencies=[Depends(checks_rate_limit)])
async def checks(body: CheckBody, user: User = Depends(require_trader), session: Any = Depends(get_session),
                 factory: pc.SessionFactory = Depends(session_factory)) -> dict[str, Any]:
    await _on(session, user)
    settings = await pw.read_settings(session, user)
    underlying = _name(body.underlying)
    instrument = pw.instrument_key(underlying, body.expiry.isoformat(), body.strike, body.right)
    accepted = await pw.accepted(user, instrument)
    await session.commit()                         # the request's own session is done before the checks open theirs
    draft = pc.Draft(underlying, body.expiry, body.strike, body.right, body.side, body.lots, body.order_type, body.product,
                     body.price, body.ltp, body.chart_instrument, body.chart_tf)
    out = await pc.run(factory, user, draft, settings, accepted=accepted, only=body.only)
    return {**out, "accepted": sorted(accepted), "instrument": instrument, "budget_ms": int(pc.BUDGET_S * 1000),
            "switches": {w: {"on": settings.on[w], "ask": settings.ask[w]} for w in pw.WARNING_IDS}}


@router.post("/checks/accept", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(checks_rate_limit)])
async def accept(body: AcceptBody, user: User = Depends(require_trader), session: Any = Depends(get_session)) -> Response:
    await _on(session, user)
    instrument = pw.instrument_key(_name(body.underlying), body.expiry.isoformat(), body.strike, body.right)
    await pw.accept(user, body.types, instrument)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
