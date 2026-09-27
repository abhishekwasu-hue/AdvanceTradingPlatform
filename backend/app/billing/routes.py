"""Phase K1: billing API.

Tenant (OWNER): `GET /api/billing` (plan, subscription, usage), `GET /api/billing/plans`,
`POST /api/billing/subscribe`, `POST /api/billing/cancel`, `GET /api/billing/transactions`,
`GET /api/billing/usage`. Operator (SUPER_ADMIN): `POST /api/admin/billing/{tenant_id}/payment`
records a payment against the open invoice (the manual provider's "gateway webhook").
"""
from typing import List, Optional

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_owner, require_role
from app.billing.service import (
    cancel, get_subscription, plans_catalogue, record_payment, subscribe, subscription_dict, usage_summary,
)
from app.billing.razorpay import GatewayError, RazorpayProvider
from app.billing.webhooks import WebhookRejected, handle_razorpay
from app.billing.service import provider as billing_provider
from app.db.models import BillingTransactionRecord, Tenant, User
from app.db.session import get_session
from app.plans.limits import limits, usage as plan_usage
from app.plans.registry import PLANS, get_plan

router = APIRouter(prefix="/api/billing", tags=["billing"])
admin_router = APIRouter(prefix="/api/admin/billing", tags=["admin"], dependencies=[Depends(require_role())])


class SubscribeRequest(BaseModel):
    plan_id: str
    billing_cycle: str = Field(default="MONTHLY", pattern=r"^(MONTHLY|YEARLY|monthly|yearly)$")


class CancelRequest(BaseModel):
    immediately: bool = False


class PaymentRequest(BaseModel):
    amount: float = Field(gt=0)
    reference: Optional[str] = Field(default=None, max_length=200)


def _tx(row: BillingTransactionRecord) -> dict:
    return {"id": row.id, "kind": row.kind, "amount": row.amount, "currency": row.currency, "status": row.status,
            "description": row.description, "provider_ref": row.provider_ref, "created_at": row.created_at.isoformat() if row.created_at else None}


@router.get("/plans")
async def plans() -> List[dict]:
    return plans_catalogue()


@router.get("")
async def billing_overview(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    tenant = await session.get(Tenant, user.tenant_id)
    sub = await get_subscription(session, tenant.id)
    return {"subscription": subscription_dict(sub, tenant), "limits": limits(get_plan(tenant.plan)),
            "usage": await plan_usage(session, tenant.id), "metered_30d": await usage_summary(session, tenant.id, days=30),
            "tenant_status": tenant.status, "status_reason": tenant.status_reason}


@router.post("/subscribe")
async def subscribe_plan(body: SubscribeRequest, user: User = Depends(require_owner), session: AsyncSession = Depends(get_session)) -> dict:
    if body.plan_id.lower() not in PLANS:
        raise HTTPException(status_code=400, detail=f"Unknown plan '{body.plan_id}'. Plans: {list(PLANS)}")
    tenant = await session.get(Tenant, user.tenant_id)
    try:
        sub = await subscribe(session, tenant, body.plan_id.lower(), body.billing_cycle.upper(), user_id=user.id)
    except (ValueError, GatewayError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.refresh(tenant)
    return subscription_dict(sub, tenant)


@router.post("/cancel")
async def cancel_plan(body: CancelRequest, user: User = Depends(require_owner), session: AsyncSession = Depends(get_session)) -> dict:
    tenant = await session.get(Tenant, user.tenant_id)
    try:
        sub = await cancel(session, tenant, immediately=body.immediately, user_id=user.id)
    except (ValueError, GatewayError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.refresh(tenant)
    return subscription_dict(sub, tenant)


@router.get("/transactions")
async def transactions(limit: int = Query(default=100, ge=1, le=500), user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)) -> List[dict]:
    rows = await session.scalars(select(BillingTransactionRecord).where(BillingTransactionRecord.tenant_id == user.tenant_id)
                                 .order_by(BillingTransactionRecord.id.desc()).limit(limit))
    return [_tx(r) for r in rows]


@router.get("/usage")
async def usage(days: int = Query(default=30, ge=1, le=365), user: User = Depends(get_current_user),
                session: AsyncSession = Depends(get_session)) -> dict:
    return {"days": days, "metrics": await usage_summary(session, user.tenant_id, days=days)}


@admin_router.post("/{tenant_id}/payment")
async def admin_record_payment(tenant_id: int, body: PaymentRequest, admin: User = Depends(require_role()),
                               session: AsyncSession = Depends(get_session)) -> dict:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Unknown organisation")
    try:
        sub = await record_payment(session, tenant, body.amount, reference=body.reference, user_id=admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.refresh(tenant)
    return subscription_dict(sub, tenant)


@admin_router.get("/{tenant_id}")
async def admin_billing(tenant_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Unknown organisation")
    sub = await get_subscription(session, tenant_id)
    rows = await session.scalars(select(BillingTransactionRecord).where(BillingTransactionRecord.tenant_id == tenant_id)
                                 .order_by(BillingTransactionRecord.id.desc()).limit(50))
    return {"subscription": subscription_dict(sub, tenant), "transactions": [_tx(r) for r in rows]}


# --- gateway webhooks (Phase K1b) -----------------------------------------------------------------

webhook_router = APIRouter(prefix="/api/billing/webhooks", tags=["billing"])


@webhook_router.post("/razorpay")
async def razorpay_webhook(request: Request, session: AsyncSession = Depends(get_session),
                           x_razorpay_signature: Optional[str] = Header(default=None, alias="X-Razorpay-Signature"),
                           x_razorpay_event_id: Optional[str] = Header(default=None, alias="X-Razorpay-Event-Id")) -> dict:
    """Unauthenticated by design (Razorpay calls it); the HMAC over the raw body is the auth.
    Always answers 200 once verified so Razorpay does not retry a delivery we recorded."""
    current = billing_provider()
    if not isinstance(current, RazorpayProvider):
        raise HTTPException(status_code=404, detail="Razorpay is not the configured billing provider")
    body = await request.body()
    try:
        payload = json.loads(body or b"{}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Body is not JSON") from exc
    try:
        record = await handle_razorpay(session, current, body, x_razorpay_signature, x_razorpay_event_id, payload)
    except WebhookRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "event_id": record.event_id, "event": record.event_type, "result": record.result}
