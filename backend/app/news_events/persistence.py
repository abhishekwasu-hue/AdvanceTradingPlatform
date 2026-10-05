"""Pydantic <-> SQLAlchemy conversion for News & Event records, keeping the ORM out of the
routes/engine layer - same pattern as `app/fundamentals/persistence.py`.
"""
import json
from datetime import date
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Bias
from app.db.models import NewsEventRecord
from app.fundamentals.models import SourceCitation
from app.news_events.models import NewsEvent, NewsEventCategory, NewsEventResponse


def news_event_to_response(record: NewsEventRecord) -> NewsEventResponse:
    return NewsEventResponse(
        id=record.id,
        category=NewsEventCategory(record.category),
        headline=record.headline,
        description=record.description,
        event_date=record.event_date,
        affected_symbols=json.loads(record.affected_symbols_json),
        sentiment=Bias(record.sentiment),
        source=SourceCitation.model_validate_json(record.source_json),
        created_by=record.created_by,
        created_at=record.created_at,
        origin=record.origin or "MANUAL",
        verified=bool(record.verified) if record.verified is not None else True,
        source_url=record.source_url,
        feed_id=record.feed_id,
        published_at=record.published_at,
        classification=json.loads(record.classification_json) if record.classification_json else None,
    )


def news_event_from_model(event: NewsEvent, created_by: Optional[int]) -> NewsEventRecord:
    return NewsEventRecord(
        category=event.category.value,
        headline=event.headline,
        description=event.description,
        event_date=event.event_date,
        affected_symbols_json=json.dumps([s.upper() for s in event.affected_symbols]),
        sentiment=event.sentiment.value,
        source_json=event.source.model_dump_json(),
        created_by=created_by,
        origin="MANUAL", verified=True, source_url=event.source.source_url,
    )


async def list_news_event_records(
    session: AsyncSession,
    category: Optional[NewsEventCategory] = None,
    symbol: Optional[str] = None,
    since: Optional[date] = None,
    origin: Optional[str] = None,
) -> List[NewsEventRecord]:
    stmt = select(NewsEventRecord).order_by(NewsEventRecord.event_date.desc(), NewsEventRecord.id.desc())
    if category is not None:
        stmt = stmt.where(NewsEventRecord.category == category.value)
    if origin:
        stmt = stmt.where(NewsEventRecord.origin == origin.upper())
    if since is not None:
        stmt = stmt.where(NewsEventRecord.event_date >= since)
    records = list(await session.scalars(stmt))
    if symbol is not None:
        target = symbol.upper()
        records = [r for r in records if target in json.loads(r.affected_symbols_json)]
    return records
