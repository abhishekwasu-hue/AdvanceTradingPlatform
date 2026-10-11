"""U1-a: point-in-time reads of the universe. Ranges are half-open [valid_from, valid_to); NULL valid_from = since
before our first record, NULL valid_to = still current. Historical universes keep names that were delisted later
(no survivorship bias)."""
from datetime import date
from typing import List, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SecurityRecord, SymbolHistoryRecord


def _in_range(day: date):
    return and_(or_(SymbolHistoryRecord.valid_from.is_(None), SymbolHistoryRecord.valid_from <= day),
                or_(SymbolHistoryRecord.valid_to.is_(None), SymbolHistoryRecord.valid_to > day))


async def symbol_on(session: AsyncSession, isin: str, day: date) -> Optional[str]:
    row = await session.scalar(select(SymbolHistoryRecord.symbol).where(SymbolHistoryRecord.isin == isin, _in_range(day)).limit(1))
    if row:
        return row
    sec = await session.get(SecurityRecord, isin)
    return sec.symbol if sec is not None and (sec.listing_date is None or sec.listing_date <= day) else None


async def isin_for(session: AsyncSession, symbol: str, day: date) -> Optional[str]:
    """The ISIN that traded as `symbol` on `day` (a symbol can be reused by another company later)."""
    symbol = symbol.strip().upper()
    return await session.scalar(select(SymbolHistoryRecord.isin).where(SymbolHistoryRecord.symbol == symbol, _in_range(day)).limit(1))


async def listed_on(session: AsyncSession, day: date, *, series: Optional[List[str]] = None, include_sme: bool = False,
                    include_etf: bool = False) -> List[str]:
    """ISINs listed on `day`: listed on or before it and not delisted by then - including names delisted since."""
    q = select(SecurityRecord.isin).where(or_(SecurityRecord.listing_date.is_(None), SecurityRecord.listing_date <= day),
                                          or_(SecurityRecord.delisting_date.is_(None), SecurityRecord.delisting_date > day))
    if not include_sme:
        q = q.where(SecurityRecord.is_sme.is_(False))
    if not include_etf:
        q = q.where(SecurityRecord.is_etf.is_(False))
    if series:
        q = q.where(SecurityRecord.series.in_([s.upper() for s in series]))
    return sorted(await session.scalars(q))
