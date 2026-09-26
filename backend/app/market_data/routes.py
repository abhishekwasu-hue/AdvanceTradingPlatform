from datetime import date
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_role
from app.core.enums import UserRole
from app.db.models import MarketHolidayRecord, User
from app.db.session import get_session

router = APIRouter(prefix="/api/market-holidays", tags=["market-data"])


class MarketHolidayResponse(BaseModel):
    id: int
    exchange: str
    holiday_date: date
    description: str


class MarketHolidayCreateRequest(BaseModel):
    exchange: str = Field(default="NSE", min_length=1, max_length=20)
    holiday_date: date
    description: str = Field(default="", max_length=200)


@router.get("", response_model=List[MarketHolidayResponse])
async def list_market_holidays(
    exchange: str = "NSE", year: int | None = None,
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> List[MarketHolidayResponse]:
    """The exchange holidays the worker treats as closed days. Shared reference data - the same
    list for every tenant - seeded from NSE's annual circular and extended by a SUPER_ADMIN each
    December when the next year's list is published."""
    query = select(MarketHolidayRecord).where(MarketHolidayRecord.exchange == exchange.upper())
    if year is not None:
        query = query.where(MarketHolidayRecord.holiday_date >= date(year, 1, 1), MarketHolidayRecord.holiday_date <= date(year, 12, 31))
    rows = await session.scalars(query.order_by(MarketHolidayRecord.holiday_date))
    return [MarketHolidayResponse(id=r.id, exchange=r.exchange, holiday_date=r.holiday_date, description=r.description) for r in rows]


@router.post("", response_model=MarketHolidayResponse, status_code=status.HTTP_201_CREATED)
async def add_market_holiday(
    request: MarketHolidayCreateRequest,
    user: User = Depends(require_role(UserRole.SUPER_ADMIN)), session: AsyncSession = Depends(get_session),
) -> MarketHolidayResponse:
    record = MarketHolidayRecord(exchange=request.exchange.upper(), holiday_date=request.holiday_date, description=request.description)
    session.add(record)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="That holiday is already recorded") from exc
    await write_audit_log(session, user.tenant_id, user.id, "market_holiday_added", f"{record.exchange} {record.holiday_date} {record.description}")
    await session.commit()
    await session.refresh(record)
    return MarketHolidayResponse(id=record.id, exchange=record.exchange, holiday_date=record.holiday_date, description=record.description)


@router.delete("/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_market_holiday(
    holiday_id: int, user: User = Depends(require_role(UserRole.SUPER_ADMIN)), session: AsyncSession = Depends(get_session),
) -> None:
    record = await session.get(MarketHolidayRecord, holiday_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown holiday")
    await session.delete(record)
    await write_audit_log(session, user.tenant_id, user.id, "market_holiday_removed", f"{record.exchange} {record.holiday_date}")
    await session.commit()
