"""Phase K1: subscriptions, billing transactions and usage metering (master prompt V3.6-3.8,
V3.14 rules 5-7, section 43).

Money never moves through this code: a `BillingProvider` is the abstraction the platform calls
(create/change/cancel a subscription, record a payment), and the one provider shipped is
`ManualProvider` - invoices are recorded as transactions, an operator (SUPER_ADMIN) marks them
paid, and the entitlement follows. A Razorpay/Stripe provider implements the same four calls
and posts payment webhooks into `record_payment`; nothing else changes.

Entitlement is `tenants.plan`: the subscription is the *reason* a tenant is on a plan, and every
transition here (trial start, payment, cancellation at period end, grace expiry) rewrites
`tenants.plan` so the limit checks in app/plans/limits.py keep working unchanged. A tenant with
no subscription row is on the free plan. Usage records (`usage_records`) are written by
`meter()` at the call sites that matter for a tier (backtests, public API calls, webhook
events, AI requests, orders) and summarised per period.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.billing.razorpay import GatewaySubscription, build_from_config
from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import BillingTransactionRecord, SubscriptionRecord, Tenant, UsageRecord
from app.notifications.service import notify
from app.plans.registry import DEFAULT_PLAN_ID, PLANS, Plan, get_plan
from app.observability.metrics import BILLING_PAYMENTS, BILLING_TRANSITIONS

logger = logging.getLogger(__name__)

GRACE_DAYS = 7
STATUS_TRIALING, STATUS_ACTIVE, STATUS_PAST_DUE, STATUS_CANCELLED = "TRIALING", "ACTIVE", "PAST_DUE", "CANCELLED"
CYCLE_DAYS = {"MONTHLY": 30, "YEARLY": 365}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def price_for(plan: Plan, cycle: str) -> float:
    return plan.price_yearly if cycle == "YEARLY" else plan.price_monthly


@dataclass
class BillingProvider:
    """The seam a payment gateway plugs into. The manual provider does the bookkeeping only;
    `app/billing/razorpay.py::RazorpayProvider` implements the same three calls against the
    gateway and returns the hosted checkout link."""
    name: str = "manual"

    async def create_subscription(self, session: AsyncSession, tenant: Tenant, plan: Plan, cycle: str, *,
                                  amount: float, trial_end: Optional[datetime] = None) -> GatewaySubscription:
        return GatewaySubscription(ref=f"manual:{tenant.id}:{plan.id}:{cycle}")

    async def change_plan(self, session: AsyncSession, subscription: SubscriptionRecord, plan: Plan, cycle: str, *, amount: float) -> GatewaySubscription:
        return GatewaySubscription(ref=f"manual:{subscription.tenant_id}:{plan.id}:{cycle}")

    async def cancel(self, session: AsyncSession, subscription: SubscriptionRecord, *, immediately: bool = False) -> None:
        return None


_PROVIDER: Optional[object] = None


def provider():
    """The configured provider: Razorpay when BILLING_PROVIDER=razorpay and its keys are set,
    otherwise manual. Built once; `set_provider()` swaps it (tests, hot reconfiguration)."""
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = build_from_config() or BillingProvider()
    return _PROVIDER


def set_provider(instance: Optional[object]) -> None:
    global _PROVIDER
    _PROVIDER = instance


async def get_subscription(session: AsyncSession, tenant_id: int) -> Optional[SubscriptionRecord]:
    return await session.scalar(select(SubscriptionRecord).where(SubscriptionRecord.tenant_id == tenant_id))


async def _transaction(session: AsyncSession, tenant_id: int, subscription_id: Optional[int], kind: str, amount: float, currency: str,
                       status: str, description: str, provider_ref: Optional[str] = None) -> BillingTransactionRecord:
    row = BillingTransactionRecord(tenant_id=tenant_id, subscription_id=subscription_id, kind=kind, amount=amount, currency=currency,
                                   status=status, description=description[:300], provider_ref=provider_ref)
    session.add(row)
    await session.flush()
    return row


async def subscribe(session: AsyncSession, tenant: Tenant, plan_id: str, cycle: str = "MONTHLY", *, user_id: Optional[int] = None) -> SubscriptionRecord:
    """Start (or change to) a paid plan. A plan with trial days starts TRIALING on the plan right
    away; otherwise an invoice is raised and the plan applies once it is paid (ACTIVE)."""
    plan = get_plan(plan_id)
    if plan.id == DEFAULT_PLAN_ID:
        raise ValueError("The free plan needs no subscription - cancel the current one instead")
    cycle = cycle.upper()
    if cycle not in CYCLE_DAYS:
        raise ValueError("billing_cycle must be MONTHLY or YEARLY")
    now = _utcnow()
    existing = await get_subscription(session, tenant.id)
    amount = price_for(plan, cycle)
    if existing is None:
        trial_end = now + timedelta(days=plan.trial_days) if plan.trial_days else None
        gateway = await provider().create_subscription(session, tenant, plan, cycle, amount=amount, trial_end=trial_end)
        existing = SubscriptionRecord(tenant_id=tenant.id, plan_id=plan.id, billing_cycle=cycle, provider=provider().name, provider_ref=gateway.ref,
                                      checkout_url=gateway.checkout_url,
                                      status=STATUS_TRIALING if plan.trial_days else STATUS_PAST_DUE,
                                      current_period_start=now,
                                      current_period_end=now + timedelta(days=plan.trial_days if plan.trial_days else CYCLE_DAYS[cycle]),
                                      trial_end=trial_end)
        session.add(existing)
        await session.flush()
        await _transaction(session, tenant.id, existing.id, "INVOICE", amount, plan.currency, "OPEN",
                           f"{plan.name} {cycle.lower()} subscription" + (f" (after {plan.trial_days}-day trial)" if plan.trial_days else ""), gateway.ref)
        if plan.trial_days:
            tenant.plan = plan.id   # trial entitles right away
        event = f"subscribed to {plan.id} ({cycle.lower()}), status {existing.status}"
    else:
        gateway = await provider().change_plan(session, existing, plan, cycle, amount=amount)
        old_plan = existing.plan_id
        existing.plan_id, existing.billing_cycle, existing.provider_ref = plan.id, cycle, gateway.ref
        existing.provider = provider().name
        if gateway.checkout_url:
            existing.checkout_url = gateway.checkout_url
        existing.cancel_at_period_end = False
        if existing.status in (STATUS_ACTIVE, STATUS_TRIALING):
            tenant.plan = plan.id
            await _transaction(session, tenant.id, existing.id, "INVOICE", amount, plan.currency, "OPEN",
                               f"Plan change {old_plan} -> {plan.id} ({cycle.lower()}), applies now; difference billed next cycle", gateway.ref)
        else:
            await _transaction(session, tenant.id, existing.id, "INVOICE", amount, plan.currency, "OPEN",
                               f"{plan.name} {cycle.lower()} subscription", gateway.ref)
        event = f"plan change {old_plan} -> {plan.id} ({cycle.lower()}), status {existing.status}"
    await write_audit_log(session, tenant.id, user_id, "billing_subscription", event)
    await session.commit()
    await session.refresh(existing)
    return existing


async def record_payment(session: AsyncSession, tenant: Tenant, amount: float, *, reference: Optional[str] = None,
                         user_id: Optional[int] = None) -> SubscriptionRecord:
    """A payment arrived (operator or gateway webhook): close open invoices, extend the period,
    ACTIVE, entitle the plan."""
    sub = await get_subscription(session, tenant.id)
    if sub is None:
        raise ValueError("No subscription to pay for")
    plan = get_plan(sub.plan_id)
    now = _utcnow()
    if reference:
        duplicate = await session.scalar(select(BillingTransactionRecord.id).where(
            BillingTransactionRecord.tenant_id == tenant.id, BillingTransactionRecord.kind == "PAYMENT", BillingTransactionRecord.provider_ref == reference))
        if duplicate is not None:
            return sub   # the gateway redelivered a payment we already booked
    open_invoices = list(await session.scalars(select(BillingTransactionRecord).where(
        BillingTransactionRecord.subscription_id == sub.id, BillingTransactionRecord.kind == "INVOICE", BillingTransactionRecord.status == "OPEN")))
    for inv in open_invoices:
        inv.status = "PAID"
    await _transaction(session, tenant.id, sub.id, "PAYMENT", amount, plan.currency, "PAID", f"Payment received ({provider().name})", reference)
    BILLING_PAYMENTS.labels(source=provider().name).inc()
    base = max(now, _aware(sub.current_period_end) or now) if sub.status in (STATUS_ACTIVE, STATUS_TRIALING) else now
    sub.current_period_start = now
    sub.current_period_end = base + timedelta(days=CYCLE_DAYS.get(sub.billing_cycle, 30))
    sub.grace_until = None
    sub.status = STATUS_ACTIVE
    tenant.plan = plan.id
    if tenant.status == "suspended" and (tenant.status_reason or "").startswith("billing"):
        tenant.status = "active"
    await write_audit_log(session, tenant.id, user_id, "billing_payment", f"{amount:g} {plan.currency} ref={reference or '-'}; period to {sub.current_period_end.date()}")
    await session.commit()
    await session.refresh(sub)
    return sub


async def cancel(session: AsyncSession, tenant: Tenant, *, immediately: bool = False, user_id: Optional[int] = None) -> SubscriptionRecord:
    sub = await get_subscription(session, tenant.id)
    if sub is None:
        raise ValueError("No subscription to cancel")
    await provider().cancel(session, sub, immediately=immediately or sub.status in (STATUS_PAST_DUE, STATUS_TRIALING))
    if immediately or sub.status in (STATUS_PAST_DUE, STATUS_TRIALING):
        sub.status = STATUS_CANCELLED
        sub.cancelled_at = _utcnow()
        tenant.plan = DEFAULT_PLAN_ID
        for inv in await session.scalars(select(BillingTransactionRecord).where(
                BillingTransactionRecord.subscription_id == sub.id, BillingTransactionRecord.kind == "INVOICE", BillingTransactionRecord.status == "OPEN")):
            inv.status = "VOID"
        event = "cancelled now; back on the free plan"
    else:
        sub.cancel_at_period_end = True
        event = f"cancels at period end {sub.current_period_end.date() if sub.current_period_end else '?'}"
    await write_audit_log(session, tenant.id, user_id, "billing_cancel", event)
    await session.commit()
    await session.refresh(sub)
    return sub


async def sweep(session: AsyncSession, now: Optional[datetime] = None) -> Dict[str, int]:
    """The daily lifecycle pass (worker): trials and periods that ended, grace periods that ran
    out, cancellations that fell due. Returns counts per transition."""
    now = now or _utcnow()
    counts = {"trial_ended": 0, "period_ended": 0, "grace_expired": 0, "cancelled": 0}
    for sub in await session.scalars(select(SubscriptionRecord).where(SubscriptionRecord.status != STATUS_CANCELLED)):
        tenant = await session.get(Tenant, sub.tenant_id)
        if tenant is None:
            continue
        end = _aware(sub.current_period_end)
        if end is None or end > now:
            continue
        if sub.cancel_at_period_end:
            sub.status, sub.cancelled_at, tenant.plan = STATUS_CANCELLED, now, DEFAULT_PLAN_ID
            counts["cancelled"] += 1
            await notify(session, tenant.id, NotificationType.SECURITY, title="Subscription ended", message="Your plan is now Free. Live trading is paused.",
                         severity=NotificationSeverity.WARNING)
            continue
        if sub.status in (STATUS_TRIALING, STATUS_ACTIVE):
            # Period over, not paid for the next one: PAST_DUE with grace; the plan stays during grace.
            sub.status = STATUS_PAST_DUE
            sub.grace_until = now + timedelta(days=GRACE_DAYS)
            counts["trial_ended" if _aware(sub.trial_end) and _aware(sub.trial_end) <= now and sub.trial_end == sub.current_period_end else "period_ended"] += 1
            plan = get_plan(sub.plan_id)
            await _transaction(session, tenant.id, sub.id, "INVOICE", price_for(plan, sub.billing_cycle), plan.currency, "OPEN",
                               f"{plan.name} {sub.billing_cycle.lower()} renewal", sub.provider_ref)
            await notify(session, tenant.id, NotificationType.SECURITY, title="Payment due",
                         message=f"Your {plan.name} period ended; pay within {GRACE_DAYS} days to keep live trading.", severity=NotificationSeverity.WARNING)
        elif sub.status == STATUS_PAST_DUE and _aware(sub.grace_until) is not None and _aware(sub.grace_until) <= now:
            tenant.plan = DEFAULT_PLAN_ID
            tenant.status_reason = f"Subscription lapsed on {now.date().isoformat()}: no payment within the {GRACE_DAYS}-day grace period"
            sub.status = STATUS_CANCELLED
            sub.cancelled_at = now
            counts["grace_expired"] += 1
            await notify(session, tenant.id, NotificationType.SECURITY, title="Subscription lapsed",
                         message="The grace period ended without payment. Your plan is now Free; live deployments will not fire.",
                         severity=NotificationSeverity.CRITICAL)
    await session.commit()
    for transition, count in counts.items():
        if count:
            BILLING_TRANSITIONS.labels(transition=transition).inc(count)
    return counts


# --- usage metering ------------------------------------------------------------------------

async def meter(session: AsyncSession, tenant_id: int, metric: str, quantity: float = 1.0, *, source: str = "api",
                metadata: Optional[dict] = None, commit: bool = True) -> None:
    """One usage record. Daily period buckets keep the table small enough to sum per month."""
    now = _utcnow()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    session.add(UsageRecord(tenant_id=tenant_id, metric=metric, quantity=quantity, period_start=start, period_end=start + timedelta(days=1),
                            source=source, metadata_json=json.dumps(metadata) if metadata else None))
    if commit:
        await session.commit()


async def usage_summary(session: AsyncSession, tenant_id: int, *, days: int = 30) -> Dict[str, float]:
    since = _utcnow() - timedelta(days=days)
    rows = await session.execute(select(UsageRecord.metric, func.sum(UsageRecord.quantity)).where(
        UsageRecord.tenant_id == tenant_id, UsageRecord.period_start >= since).group_by(UsageRecord.metric))
    return {metric: float(total or 0) for metric, total in rows.all()}


async def usage_today(session: AsyncSession, tenant_id: int, metric: str) -> float:
    start = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    total = await session.scalar(select(func.coalesce(func.sum(UsageRecord.quantity), 0.0)).where(
        UsageRecord.tenant_id == tenant_id, UsageRecord.metric == metric, UsageRecord.period_start >= start))
    return float(total or 0)


def subscription_dict(sub: Optional[SubscriptionRecord], tenant: Tenant) -> dict:
    plan = get_plan(tenant.plan)
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    base = {"plan_id": plan.id, "plan_name": plan.name, "price_monthly": plan.price_monthly, "price_yearly": plan.price_yearly,
            "currency": plan.currency, "trial_days": plan.trial_days, "provider": provider().name}
    if sub is None:
        return {**base, "status": "NONE", "billing_cycle": None, "current_period_end": None, "grace_until": None, "cancel_at_period_end": False,
                "checkout_url": None, "subscription_provider": None}
    return {**base, "subscription_id": sub.id, "subscribed_plan_id": sub.plan_id, "status": sub.status, "billing_cycle": sub.billing_cycle,
            "checkout_url": sub.checkout_url, "subscription_provider": sub.provider,
            "current_period_start": iso(sub.current_period_start), "current_period_end": iso(sub.current_period_end),
            "trial_end": iso(sub.trial_end), "grace_until": iso(sub.grace_until), "cancel_at_period_end": sub.cancel_at_period_end,
            "cancelled_at": iso(sub.cancelled_at)}


def plans_catalogue() -> List[dict]:
    from app.plans.limits import limits
    return [{"id": p.id, "name": p.name, "description": p.description, **limits(p)} for p in PLANS.values()]
