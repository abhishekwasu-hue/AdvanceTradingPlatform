"""Phase K2: marketplace API (V3.14 rule 8 flows).

Creator: `POST /api/marketplace/listings`, `GET /api/marketplace/listings/mine`,
`POST /api/marketplace/listings/{id}/submit|unlist`. Subscriber: `GET /api/marketplace`,
`GET /api/marketplace/{id}`, `POST /api/marketplace/{id}/subscribe|unsubscribe`,
`GET /api/marketplace/subscriptions`. Operator: `GET /api/admin/marketplace/pending`,
`POST /api/admin/marketplace/{id}/publish|reject`. Marketplace access is plan-gated.

Phase X (revenue share): a paid listing's `POST /{id}/subscribe` answers 202 with the OPEN charge
and its checkout link (none under the manual provider - the operator confirms the payment);
`GET /purchases`, `GET /terms`, `GET /earnings`, `POST|GET /payouts` for creators; operator
`GET /admin/marketplace/charges|payouts|revenue`, `POST .../charges/{id}/paid|void`,
`POST .../payouts/{id}/paid|reject`.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_owner, require_role, require_trader
from fastapi.responses import JSONResponse

from app.audit.log import write_audit_log
from app.billing.razorpay import GatewayError
from app.db.models import MarketplaceChargeRecord, MarketplaceListingRecord, MarketplacePayoutRecord, MarketplaceSubscriptionRecord, Tenant, User
from app.db.session import get_session
from app.marketplace import billing, service
from app.marketplace.service import MarketplaceError, as_dict
from app.plans.limits import require_feature
from app.platform.controls import marketplace_terms, require_flag

router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])
admin_router = APIRouter(prefix="/api/admin/marketplace", tags=["admin"], dependencies=[Depends(require_role())])


class ListingCreate(BaseModel):
    custom_strategy_id: int
    title: str = Field(min_length=4, max_length=120)
    description: str = Field(min_length=1, max_length=5000)
    methodology: Optional[str] = Field(default=None, max_length=5000)
    backtest_run_id: Optional[int] = None
    version_number: Optional[int] = Field(default=None, ge=1)
    price: float = Field(default=0.0, ge=0, description="Phase X: one-time price in INR; 0 = free")


class PriceBody(BaseModel):
    price: float = Field(ge=0)


class ReviewBody(BaseModel):
    note: Optional[str] = Field(default=None, max_length=500)


class PayoutRequestBody(BaseModel):
    destination: str = Field(min_length=6, max_length=200, description="UPI id or bank account + IFSC; stored encrypted")


class SettleBody(BaseModel):
    reference: Optional[str] = Field(default=None, max_length=200)
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
                    "strategy_id": f"custom:{sub.custom_strategy_id}" if sub.custom_strategy_id else None,
                    "title": listing.title if listing else None, "created_at": sub.created_at.isoformat() if sub.created_at else None})
    return out


@router.get("/terms")
async def terms(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Phase X: what a creator keeps and when a payout can be requested."""
    return await marketplace_terms(session)


@router.get("/purchases")
async def my_purchases(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[dict]:
    return await billing.purchases(session, user.tenant_id)


@router.get("/earnings")
async def my_earnings(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    return await billing.earnings(session, user.tenant_id)


@router.get("/payouts")
async def my_payouts(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[dict]:
    return (await billing.earnings(session, user.tenant_id))["payouts"]


@router.post("/payouts", status_code=201)
async def request_payout(body: PayoutRequestBody, user: User = Depends(require_owner), session: AsyncSession = Depends(get_session)) -> dict:
    """Owner only: money leaves the organisation's earnings. The destination is stored encrypted."""
    try:
        payout = await billing.request_payout(session, user, body.destination)
    except MarketplaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return billing.payout_dict(payout)


@router.post("/listings", status_code=201)
async def create(body: ListingCreate, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    await _gate(session, user)
    try:
        price = await billing.validate_price(session, body.price)
        listing = await service.create_listing(session, user, custom_strategy_id=body.custom_strategy_id, title=body.title,
                                               description=body.description, methodology=body.methodology,
                                               backtest_run_id=body.backtest_run_id, version_number=body.version_number)
        if price:
            listing.price = price
            await session.commit()
            await session.refresh(listing)
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
        charge, sub = await billing.purchase(session, user, listing)
    except MarketplaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=f"Payment link could not be created: {exc}") from exc
    if charge is not None and sub.status != "ACTIVE":
        # Phase X: paid listing awaiting payment - the copy is made once the charge is settled.
        next_step = ("Open the checkout link to pay; the strategy lands in your custom strategies as soon as the payment is confirmed."
                     if charge.checkout_url else
                     "Pay the operator by bank transfer / UPI quoting this charge number; the strategy is copied once the payment is confirmed.")
        return JSONResponse(status_code=202, content={
            "id": sub.id, "listing_id": listing.id, "status": sub.status, "custom_strategy_id": None, "strategy_id": None,
            "charge": billing.charge_dict(charge, listing_title=listing.title), "checkout_url": charge.checkout_url,
            "disclaimer": service.DISCLAIMER, "next": next_step})
    return {"id": sub.id, "listing_id": listing.id, "status": sub.status, "custom_strategy_id": sub.custom_strategy_id,
            "strategy_id": f"custom:{sub.custom_strategy_id}", "disclaimer": service.DISCLAIMER,
            "next": "Backtest and paper-trade this copy from the Strategies tab before deploying it LIVE."}


@router.put("/listings/{listing_id}/price")
async def set_price(listing_id: int, body: PriceBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    """Phase X: re-price a listing that is not live (DRAFT / REJECTED / UNLISTED)."""
    listing = await _listing(session, listing_id)
    if listing.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="No such listing")
    if listing.status in ("PUBLISHED", "PENDING_REVIEW"):
        raise HTTPException(status_code=400, detail="Unlist the listing before changing its price - buyers see the published price")
    try:
        listing.price = await billing.validate_price(session, body.price)
    except MarketplaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    await session.refresh(listing)
    return as_dict(listing, mine=True)


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
    await billing.freeze_fee(session, listing)   # Phase X: the fee this listing carries from now on
    try:
        return as_dict(await service.review(session, admin, listing, publish=True, note=body.note), mine=True)
    except MarketplaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post("/{listing_id}/reject")
async def reject(listing_id: int, body: ReviewBody, admin: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    listing = await _listing(session, listing_id)
    return as_dict(await service.review(session, admin, listing, publish=False, note=body.note), mine=True)


# --- Phase X: operator side of the revenue share --------------------------------------------------

async def _charge(session: AsyncSession, charge_id: int) -> MarketplaceChargeRecord:
    charge = await session.get(MarketplaceChargeRecord, charge_id)
    if charge is None:
        raise HTTPException(status_code=404, detail="No such charge")
    return charge


async def _payout(session: AsyncSession, payout_id: int) -> MarketplacePayoutRecord:
    payout = await session.get(MarketplacePayoutRecord, payout_id)
    if payout is None:
        raise HTTPException(status_code=404, detail="No such payout")
    return payout


@admin_router.get("/charges")
async def admin_charges(status: Optional[str] = None, session: AsyncSession = Depends(get_session)) -> List[dict]:
    return await billing.charges(session, status=status)


@admin_router.post("/charges/{charge_id}/paid")
async def admin_charge_paid(charge_id: int, body: SettleBody, admin: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    """The buyer paid out of band (manual provider): confirm with the transfer reference."""
    charge = await _charge(session, charge_id)
    try:
        charge = await billing.settle_charge(session, charge, payment_ref=body.reference, actor_user_id=admin.id, source="operator")
    except MarketplaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return billing.charge_dict(charge, for_creator=True)


@admin_router.post("/charges/{charge_id}/void")
async def admin_charge_void(charge_id: int, body: SettleBody, admin: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    charge = await _charge(session, charge_id)
    try:
        charge = await billing.void_charge(session, charge, actor_user_id=admin.id, reason=body.note or "voided by operator")
    except MarketplaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return billing.charge_dict(charge, for_creator=True)


@admin_router.get("/payouts")
async def admin_payouts(status: Optional[str] = None, session: AsyncSession = Depends(get_session)) -> List[dict]:
    return await billing.payouts(session, status=status)


@admin_router.get("/payouts/{payout_id}/destination")
async def admin_payout_destination(payout_id: int, admin: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    """The full destination, decrypted for the transfer; the read is audited."""
    payout = await _payout(session, payout_id)
    await write_audit_log(session, payout.tenant_id, admin.id, "marketplace_payout_destination_read", f"payout {payout.id}")
    await session.commit()
    return {"id": payout.id, "destination": billing.payout_destination(payout)}


@admin_router.post("/payouts/{payout_id}/paid")
async def admin_payout_paid(payout_id: int, body: SettleBody, admin: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    payout = await _payout(session, payout_id)
    try:
        return billing.payout_dict(await billing.settle_payout(session, admin, payout, paid=True, reference=body.reference, note=body.note))
    except MarketplaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.post("/payouts/{payout_id}/reject")
async def admin_payout_reject(payout_id: int, body: SettleBody, admin: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    payout = await _payout(session, payout_id)
    try:
        return billing.payout_dict(await billing.settle_payout(session, admin, payout, paid=False, reference=body.reference, note=body.note))
    except MarketplaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/revenue")
async def admin_revenue(session: AsyncSession = Depends(get_session)) -> dict:
    return await billing.platform_revenue(session)
