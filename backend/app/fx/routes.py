"""Phase P3: `GET /api/fx/rates` (any user), `GET/PUT /api/admin/fx-rates` (platform admin)."""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_role
from app.db.models import FxRateRecord, User
from app.db.session import get_session
from app.fx import service as fx

router = APIRouter(prefix="/api/fx", tags=["fx"])
admin_router = APIRouter(prefix="/api/admin/fx-rates", tags=["admin"], dependencies=[Depends(require_role())])


class RateBody(BaseModel):
    base: str = Field(min_length=3, max_length=4)
    quote: str = Field(min_length=3, max_length=4)
    rate: float = Field(gt=0)
    source: str = Field(default="manual", max_length=40)


@router.get("/rates")
async def rates(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    rows = list(await session.scalars(select(FxRateRecord).order_by(FxRateRecord.base, FxRateRecord.quote)))
    return {"rates": [fx.rate_dict(r) for r in rows], "supported": list(fx.SUPPORTED_CURRENCIES)}


@admin_router.get("")
async def admin_rates(session: AsyncSession = Depends(get_session)) -> List[dict]:
    return [fx.rate_dict(r) for r in await session.scalars(select(FxRateRecord).order_by(FxRateRecord.base, FxRateRecord.quote))]


@admin_router.put("")
async def put_rate(body: RateBody, admin: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    try:
        return fx.rate_dict(await fx.set_rate(session, admin, body.base, body.quote, body.rate, body.source))
    except fx.FxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
