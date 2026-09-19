"""API surface for the News & Event engine - structured, cited macro/market news entries (RBI
policy, Union Budget, government policy, broad corporate news, global macro events, sector
developments). Shared reference data (like the fundamentals module), not tenant-private: reads
are open to everyone, writes require auth so every entry is attributed (`created_by`). There is
no live news feed wired in - every entry is exactly as reliable as its cited source.
"""
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import NewsEventRecord, User
from app.db.session import get_session
from app.news_events import persistence as db
from app.news_events.models import NewsEvent, NewsEventCategory, NewsEventResponse

router = APIRouter(prefix="/api/news-events", tags=["news-events"])


@router.post("", response_model=NewsEventResponse, status_code=201)
async def create_news_event(
    event: NewsEvent, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> NewsEventResponse:
    record = db.news_event_from_model(event, user.id)
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return db.news_event_to_response(record)


@router.get("", response_model=List[NewsEventResponse])
async def list_news_events_route(
    category: Optional[NewsEventCategory] = None,
    symbol: Optional[str] = None,
    since: Optional[date] = None,
    session: AsyncSession = Depends(get_session),
) -> List[NewsEventResponse]:
    records = await db.list_news_event_records(session, category=category, symbol=symbol, since=since)
    return [db.news_event_to_response(r) for r in records]


@router.get("/{event_id}", response_model=NewsEventResponse)
async def get_news_event(event_id: int, session: AsyncSession = Depends(get_session)) -> NewsEventResponse:
    record = await session.get(NewsEventRecord, event_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown news event")
    return db.news_event_to_response(record)


@router.delete("/{event_id}", status_code=204)
async def delete_news_event(
    event_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> None:
    record = await session.get(NewsEventRecord, event_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown news event")
    if record.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only the user who entered this event can delete it")
    await session.delete(record)
    await session.commit()
