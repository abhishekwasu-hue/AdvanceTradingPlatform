"""Phase BB: the news feed API. Reads need a login (the items carry an organisation's own AI
classification); turning sources on or off and forcing a fetch are the platform operator's."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import settings as ai_settings
from app.ai.providers import ProviderError
from app.auth.dependencies import get_current_user, require_role
from app.db.models import Tenant, User
from app.db.session import get_session
from app.news_feed import service
from app.news_feed.sources import BY_ID
from app.platform.controls import require_flag

router = APIRouter(prefix="/api/news-feed", tags=["news-feed"])


class SourceToggle(BaseModel):
    on: bool


@router.get("/status")
async def feed_status(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """The flag, every registered source with its terms note and on/off state, the last run."""
    settings = await service.source_settings(session)
    return {"enabled": await service.enabled(session, user.tenant_id), "flag": service.FLAG, "sources": service.source_rows(settings),
            "last_run": service.last_run(), "cadence_seconds": await service.cadence_seconds(session, datetime.now(timezone.utc)),
            "note": "Feed items are unverified: headline and link from the publisher's own feed, nothing scraped, no body stored. Read the source before acting."}


@router.put("/sources/{source_id}")
async def toggle_source(source_id: str, body: SourceToggle, user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    if source_id not in BY_ID:
        raise HTTPException(status_code=404, detail="Unknown feed source")
    settings = await service.set_source(session, source_id, body.on, user)
    return {"sources": service.source_rows(settings)}


@router.post("/refresh")
async def refresh_now(user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    await require_flag(session, service.FLAG, user.tenant_id)
    return await service.ingest(session, datetime.now(timezone.utc))


@router.get("/items")
async def list_items(hours: int = Query(default=24, ge=1, le=24 * 14), min_severity: int = Query(default=1, ge=1, le=5),
                     user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[dict]:
    await require_flag(session, service.FLAG, user.tenant_id)
    return await service.items(session, user.tenant_id, hours=hours, min_severity=min_severity)


@router.post("/classify")
async def classify_now(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Classify this organisation's pending feed items with its own provider key (metered)."""
    await require_flag(session, service.FLAG, user.tenant_id)
    tenant = await session.get(Tenant, user.tenant_id)
    provider = await ai_settings.provider_for(session, tenant)
    try:
        result = await service.classify_for_tenant(session, tenant, provider)
    except ProviderError as exc:
        await ai_settings.mark_used(session, user.tenant_id, error=str(exc))
        await session.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await ai_settings.mark_used(session, user.tenant_id, error=None)
    await session.commit()
    return result
