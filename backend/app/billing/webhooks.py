"""Phase K1b: gateway webhook handling - verify, de-duplicate, apply, record. Every delivery
lands in `billing_webhook_events`; the business effect goes through the same service calls the
operator uses (`record_payment`, status changes), so the manual and gateway paths cannot drift."""
import logging
from datetime import timedelta
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import service
from app.billing.razorpay import RazorpayProvider, dump_event, payment_entity, subscription_entity
from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import BillingWebhookEventRecord, SubscriptionRecord, Tenant
from app.notifications.service import notify
from app.plans.registry import DEFAULT_PLAN_ID

logger = logging.getLogger(__name__)


class WebhookRejected(ValueError):
    pass


async def _subscription_for(session: AsyncSession, payload: dict) -> Tuple[Optional[SubscriptionRecord], Optional[Tenant]]:
    entity = subscription_entity(payload)
    ref = entity.get("id")
    sub = await session.scalar(select(SubscriptionRecord).where(SubscriptionRecord.provider_ref == ref)) if ref else None
    if sub is None:
        notes = entity.get("notes") or payment_entity(payload).get("notes") or {}
        tenant_id = notes.get("tenant_id")
        if tenant_id and str(tenant_id).isdigit():
            sub = await session.scalar(select(SubscriptionRecord).where(SubscriptionRecord.tenant_id == int(tenant_id)))
    tenant = await session.get(Tenant, sub.tenant_id) if sub is not None else None
    return sub, tenant


async def handle_razorpay(session: AsyncSession, provider: RazorpayProvider, body: bytes, signature: Optional[str], event_id: Optional[str],
                          payload: dict) -> BillingWebhookEventRecord:
    if not provider.verify_signature(body, signature):
        raise WebhookRejected("Invalid webhook signature")
    event_type = str(payload.get("event") or "unknown")[:60]
    event_id = (event_id or f"{event_type}:{payment_entity(payload).get('id') or subscription_entity(payload).get('id') or ''}:{payload.get('created_at', '')}")[:100]
    existing = await session.scalar(select(BillingWebhookEventRecord).where(BillingWebhookEventRecord.event_id == event_id))
    if existing is not None:
        return existing   # redelivery: already applied
    record = BillingWebhookEventRecord(provider=provider.name, event_id=event_id, event_type=event_type, payload_json=dump_event(payload))
    session.add(record)
    await session.flush()
    sub, tenant = await _subscription_for(session, payload)
    record.tenant_id = tenant.id if tenant else None
    try:
        record.result = await _apply(session, event_type, payload, sub, tenant)
    except Exception as exc:  # noqa: BLE001 - keep the delivery, surface the failure
        logger.exception("Razorpay webhook %s failed", event_type)
        record.result = f"error: {exc}"[:300]
    await session.commit()
    await session.refresh(record)
    return record


async def _apply(session: AsyncSession, event_type: str, payload: dict, sub: Optional[SubscriptionRecord], tenant: Optional[Tenant]) -> str:
    if event_type.startswith("payment_link."):
        # Phase X: a marketplace purchase paid through a hosted payment link.
        from app.marketplace import billing as marketplace_billing
        return await marketplace_billing.handle_payment_link_event(session, event_type, payload)
    if sub is None or tenant is None:
        return "ignored: no matching subscription"
    payment = payment_entity(payload)
    if event_type == "subscription.charged":
        amount = (payment.get("amount") or 0) / 100.0
        if amount <= 0:
            return "ignored: zero amount"
        await service.record_payment(session, tenant, amount, reference=payment.get("id"))
        return f"payment {payment.get('id')} booked: {amount:g}"
    if event_type in ("subscription.activated", "subscription.authenticated"):
        if sub.status == service.STATUS_PAST_DUE and not sub.trial_end:
            return "mandate authorised; awaiting first charge"
        return f"noted ({event_type})"
    if event_type == "payment.failed":
        amount = (payment.get("amount") or 0) / 100.0
        await service._transaction(session, tenant.id, sub.id, "FAILED_PAYMENT", amount, sub and service.get_plan(sub.plan_id).currency or "INR", "FAILED",
                                   f"Payment failed at Razorpay: {payment.get('error_description') or payment.get('error_code') or 'unknown'}", payment.get("id"))
        await notify(session, tenant.id, NotificationType.SECURITY, title="Payment failed",
                     message="Razorpay could not collect the subscription charge. Update the payment method from the checkout link under Plan & billing.",
                     severity=NotificationSeverity.WARNING)
        await session.commit()
        return f"failed payment {payment.get('id')} recorded"
    if event_type in ("subscription.halted", "subscription.pending"):
        if sub.status in (service.STATUS_ACTIVE, service.STATUS_TRIALING):
            sub.status = service.STATUS_PAST_DUE
            sub.grace_until = service._utcnow() + timedelta(days=service.GRACE_DAYS)
            await notify(session, tenant.id, NotificationType.SECURITY, title="Payment due",
                         message=f"Razorpay paused the subscription after failed charges; pay within {service.GRACE_DAYS} days to keep live trading.",
                         severity=NotificationSeverity.WARNING)
            await session.commit()
            return "subscription past due (halted at gateway)"
        return f"noted ({event_type}) - already {sub.status}"
    if event_type in ("subscription.cancelled", "subscription.completed"):
        if sub.status != service.STATUS_CANCELLED:
            sub.status = service.STATUS_CANCELLED
            sub.cancelled_at = service._utcnow()
            tenant.plan = DEFAULT_PLAN_ID
            await notify(session, tenant.id, NotificationType.SECURITY, title="Subscription ended",
                         message="The Razorpay subscription ended. Your plan is now Free. Live trading is paused.", severity=NotificationSeverity.WARNING)
            await session.commit()
            return "subscription cancelled (gateway)"
        return "noted (already cancelled)"
    return f"ignored ({event_type})"
