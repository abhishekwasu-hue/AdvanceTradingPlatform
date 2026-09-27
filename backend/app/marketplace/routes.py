"""Phase K2: marketplace API (V3.14 rule 8 flows).

Creator: `POST /api/marketplace/listings`, `GET /api/marketplace/listings/mine`,
`POST /api/marketplace/listings/{id}/submit|unlist`. Subscriber: `GET /api/marketplace`,
`GET /api/marketplace/{id}`, `POST /api/marketplace/{id}/subscribe|unsubscribe`,
`GET /api/marketplace/subscriptions`. Operator: `GET /api/admin/marketplace/pending`,
`POST /api/admin/marketplace/{id}/publish|reject`. Marketplace access is plan-gated.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_owner, require_role, require_trader
from app.db.models import MarketplaceListingRecord, MarketplaceSubscriptionRecord, Tenant, User
from app.db.session import get_session
from app.marketplace import service
from app.marketplace.service import MarketplaceError, as_dict
from app.plans.limits import require_feature
from app.platform.controls import require_flag

router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])
admin_router = APIRouter(prefix="/api/admin/marketplace", tags=["admin"], dependencies=[Depends(require_role())])


class ListingCreate(BaseModel):
    custom_strategy_id: int
    title: str = Field(min_length=4, max_length=120)
    description: str = Field(min_length=1, max_length=5000)
    methodology: Optional[str] = Field(default=None, max_length=5000)
    backtest_run_id: Optional[int] = None
    version_number: Optional[int] = Field(default=None, ge=1)


class ReviewBody(BaseModel):
    note: Optional[str] = Field(default=None, max_length=500)


async def _gate(session: AsyncSession, user: User) -> Tenant:
    tenant = await session.get(Tenant, user.tenant_id)
    require_feature(tenant, "marketplace_access", "The strategy marketplace")
    await require_flag(session, "marketplace", user.tenant_id)  # Phase N4 operator kill flag
    return tenant


async def _listing(session: AsyncSession, listing_id: int) -> MarketplaceListingRecord:
    listing = await session.get(MarketplaceListingRecord, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="No such listing")
    return listing


@router.get("")
async def discover(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[dict]:
    await _gate(session, user)
    return [as_dict(l) for l in await service.published(session)]


@router.get("/listings/mine")
async def mine(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[dict]:
    rows = await session.scalars(select(MarketplaceListingRecord).where(MarketplaceListingRecord.tenant_id == user.tenant_id)
                                 .order_by(MarketplaceListingRecord.id.desc()))
    return [as_dict(l, include_config=True, mine=True) for l in rows]


@router.get("/subscriptions")
async def my_subscriptions(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[dict]:
    rows = await session.scalars(select(MarketplaceSubscriptionRecord).where(MarketplaceSubscriptionRecord.tenant_id == user.tenant_id))
    out = []
    for sub in rows:
        listing = await session.get(MarketplaceListingRecord, sub.listing_id)
        out.append({"id": sub.id, "listing_id": sub.listing_id, "status": sub.status, "custom_strategy_id": sub.custom_strategy_id,
                    "strategy_id": f"custom_{sub.custom_strategy_id}" if sub.custom_strategy_id else None,
                    "title": listing.title if listing else None, "created_at": sub.created_at.isoformat() if sub.created_at else None})
    return out


@router.post("/listings", status_code=201)
async def create(body: ListingCreate, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    await _gate(session, user)
    try:
        listing = await service.create_listing(session, user, custom_strategy_id=body.custom_strategy_id, title=body.title,
                                               description=body.description, methodology=body.methodology,
                                               backtest_run_id=body.backtest_run_id, version_number=body.version_number)
    except MarketplaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return as_dict(listing, include_config=True, mine=True)


@router.post("/listings/{listing_id}/submit")
async def submit(listing_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    listing = await _listing(session, listing_id)
    if listing.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="No such listing")
    try:
        return as_dict(await service.submit(session, user, listing), mine=True)
    except MarketplaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/listings/{listing_id}/unlist")
async def unlist(listing_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    listing = await _listing(session, listing_id)
    if listing.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="No such listing")
    return as_dict(await service.unlist(session, user, listing), mine=True)


@router.get("/{listing_id}")
async def detail(listing_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    await _gate(session, user)
    listing = await _listing(session, listing_id)
    if listing.status != "PUBLISHED" and listing.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="No such listing")
    return as_dict(listing, mine=listing.tenant_id == user.tenant_id)


@router.post("/{listing_id}/subscribe")
async def subscribe(listing_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    await _gate(session, user)
    listing = await _listing(session, listing_id)
    try:
        sub = await service.subscribe(session, user, listing)
    except MarketplaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": sub.id, "listing_id": listing.id, "status": sub.status, "custom_strategy_id": sub.custom_strategy_id,
            "strategy_id": f"custom_{sub.custom_strategy_id}", "disclaimer": service.DISCLAIMER,
            "next": "Backtest and paper-trade this copy from the Strategies tab before deploying it LIVE."}


@router.post("/{listing_id}/unsubscribe", status_code=204)
async def unsubscribe(listing_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> None:
    listing = await _listing(session, listing_id)
    await service.unsubscribe(session, user, listing)


@admin_router.get("/pending")
async def pending(session: AsyncSession = Depends(get_session)) -> List[dict]:
    rows = await session.scalars(select(MarketplaceListingRecord).where(MarketplaceListingRecord.status == "PENDING_REVIEW")
                                 .order_by(MarketplaceListingRecord.id))
    return [as_dict(l, include_config=True, mine=True) for l in rows]


@admin_router.post("/{listing_id}/publish")
async def publish(listing_id: int, body: ReviewBody, admin: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    listing = await _listing(session, listing_id)
    try:
        return as_dict(await service.review(session, admin, listing, publish=True, note=body.note), mine=True)
    except MarketplaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post("/{listing_id}/reject")
async def reject(listing_id: int, body: ReviewBody, admin: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    listing = await _listing(session, listing_id)
    return as_dict(await service.review(session, admin, listing, publish=False, note=body.note), mine=True)
