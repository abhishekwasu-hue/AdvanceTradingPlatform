"""API surface for the News & Event engine - structured, cited macro/market news entries (RBI
policy, Union Budget, government policy, broad corporate news, global macro events, sector
developments). A person's entry belongs to their organisation: only its members read it (security fix; the
FEED rows below are shared). Writes require a trading role of an active organisation (`require_contributor`) - VIEWER
and platform SUPPORT accounts read only. Phase BB adds
FEED rows from public feeds (app/news_feed): unverified, with the item's URL, never a person's claim.
"""
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_contributor
from app.db.models import NewsEventRecord, User
from app.db.session import get_session
from app.fundamentals.visibility import viewer_tenant
from app.news_events import persistence as db
from app.news_events.models import NewsEvent, NewsEventCategory, NewsEventResponse

router = APIRouter(prefix="/api/news-events", tags=["news-events"])


@router.post("", response_model=NewsEventResponse, status_code=201, dependencies=[Depends(require_contributor)])
async def create_news_event(
    event: NewsEvent, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> NewsEventResponse:
    from app.fundamentals.visibility import check_dates
    check_dates(publication=event.source.publication_date if event.source is not None else None)
    record = db.news_event_from_model(event, user.id, tenant_id=user.tenant_id)
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return db.news_event_to_response(record)


@router.get("", response_model=List[NewsEventResponse])
async def list_news_events_route(
    category: Optional[NewsEventCategory] = None,
    symbol: Optional[str] = None,
    since: Optional[date] = None,
    origin: Optional[str] = Query(default=None, pattern="^(?i)(manual|feed)$", description="MANUAL (cited by a person) or FEED (unverified feed item)"),
    session: AsyncSession = Depends(get_session),
    viewer: Optional[int] = Depends(viewer_tenant),
) -> List[NewsEventResponse]:
    records = await db.list_news_event_records(session, category=category, symbol=symbol, since=since, origin=origin, viewer=viewer)
    return [db.news_event_to_response(r) for r in records]


@router.get("/{event_id}", response_model=NewsEventResponse)
async def get_news_event(event_id: int, session: AsyncSession = Depends(get_session), viewer: Optional[int] = Depends(viewer_tenant)
                         ) -> NewsEventResponse:
    record = await session.get(NewsEventRecord, event_id)
    if record is None or (record.tenant_id is not None and record.tenant_id != viewer):     # another organisation's entry
        raise HTTPException(status_code=404, detail="Unknown news event")
    return db.news_event_to_response(record)


@router.delete("/{event_id}", status_code=204, dependencies=[Depends(require_contributor)])
async def delete_news_event(
    event_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> None:
    record = await session.get(NewsEventRecord, event_id)
    if record is None or (record.tenant_id is not None and record.tenant_id != user.tenant_id):
        raise HTTPException(status_code=404, detail="Unknown news event")
    if record.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only the user who entered this event can delete it")
    await session.delete(record)
    await session.commit()
