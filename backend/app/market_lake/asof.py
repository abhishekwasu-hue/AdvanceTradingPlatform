"""Part B1: point-in-time reads of the lake ("what did we know at T").

A row is visible `as_of` T when it was ingested at or before T. Where a source corrects itself it writes a higher
`version` for the same key; per key the highest visible version wins - so a correction changes answers only for
`as_of` after it was ingested, and a backtest replayed with the same `as_of` sees the same data (realism C4).
`as_of=None` means "everything known now". Times are UTC; candle `ts` is the bar END.
"""
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InstrumentMasterVersionRecord, MdCandleRecord, MdPositionLimitRecord


def _visible(model, as_of: Optional[datetime]):
    return [] if as_of is None else [model.ingested_at <= as_of]


def _rank(sources: Optional[Sequence[str]], source: str) -> int:
    """Lower is better: the caller's source preference order, unknown sources last."""
    if not sources:
        return 0
    return list(sources).index(source) if source in sources else len(sources)


async def candles_as_of(session: AsyncSession, instrument_key: str, timeframe: str, start: datetime, end: datetime, *,
                        as_of: Optional[datetime] = None, sources: Optional[Sequence[str]] = None) -> List[MdCandleRecord]:
    """Bars with start < ts <= end (bar END labels), one per ts: the preferred source, then its highest visible version."""
    query = select(MdCandleRecord).where(
        MdCandleRecord.instrument_key == instrument_key, MdCandleRecord.timeframe == timeframe,
        MdCandleRecord.ts > start, MdCandleRecord.ts <= end, *_visible(MdCandleRecord, as_of))
    if sources:
        query = query.where(MdCandleRecord.source.in_(list(sources)))
    best: Dict[datetime, Tuple[Tuple[int, int], MdCandleRecord]] = {}
    for row in await session.scalars(query):
        key = (_rank(sources, row.source), -row.version)
        ts = row.ts
        if ts not in best or key < best[ts][0]:
            best[ts] = (key, row)
    return [best[ts][1] for ts in sorted(best)]


async def instrument_as_of(session: AsyncSession, instrument_key: str, day: date, *, as_of: Optional[datetime] = None,
                           source: Optional[str] = None) -> Optional[InstrumentMasterVersionRecord]:
    """The contract terms in force on `day` (latest valid_from <= day) as known at `as_of`."""
    query = select(InstrumentMasterVersionRecord).where(
        InstrumentMasterVersionRecord.instrument_key == instrument_key, InstrumentMasterVersionRecord.valid_from <= day,
        *_visible(InstrumentMasterVersionRecord, as_of))
    if source:
        query = query.where(InstrumentMasterVersionRecord.source == source)
    query = query.order_by(InstrumentMasterVersionRecord.valid_from.desc(), InstrumentMasterVersionRecord.ingested_at.desc()).limit(1)
    return await session.scalar(query)


async def position_limit_as_of(session: AsyncSession, underlying: str, day: date, *, as_of: Optional[datetime] = None,
                               source: Optional[str] = None) -> Optional[MdPositionLimitRecord]:
    """MWPL and open interest for `underlying` on `day` (highest visible version) - None when the lake has no row."""
    query = select(MdPositionLimitRecord).where(
        MdPositionLimitRecord.underlying == underlying, MdPositionLimitRecord.trade_date == day, *_visible(MdPositionLimitRecord, as_of))
    if source:
        query = query.where(MdPositionLimitRecord.source == source)
    return await session.scalar(query.order_by(MdPositionLimitRecord.version.desc()).limit(1))
