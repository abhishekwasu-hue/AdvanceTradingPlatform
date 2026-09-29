"""Phase K1b: Razorpay Subscriptions behind the `BillingProvider` seam.

Flow: our (plan, cycle) is mirrored once as a Razorpay Plan (`billing_gateway_plans`); a tenant's
`subscribe()` creates a Razorpay Subscription whose hosted checkout link (`short_url`) is stored
on the row and shown in the Billing card - the tenant authorises the autopay mandate there (UPI
autopay, cards, netbanking). Razorpay then charges each cycle and tells us through webhooks
(`POST /api/billing/webhooks/razorpay`, HMAC-SHA256 over the raw body with the webhook secret,
idempotent on `x-razorpay-event-id`): `subscription.charged` -> record_payment; `payment.failed`
-> FAILED_PAYMENT + notice; `subscription.halted` -> PAST_DUE with grace; `subscription.cancelled`
-> cancelled. Amounts are paise on the wire, rupees in our tables. No card data ever reaches us.

Credentials are the operator's platform secrets (`RAZORPAY_KEY_ID/KEY_SECRET/WEBHOOK_SECRET` in
the environment, like `JWT_SECRET_KEY`) - not tenant data, so not in Settings.
"""
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import BillingGatewayPlanRecord, SubscriptionRecord, Tenant
from app.plans.registry import Plan

logger = logging.getLogger(__name__)

PERIODS = {"MONTHLY": "monthly", "YEARLY": "yearly"}
TOTAL_COUNT = {"MONTHLY": 120, "YEARLY": 10}     # Razorpay needs a finite cycle count; 10 years either way
TIMEOUT = 20.0


class GatewayError(RuntimeError):
    pass


@dataclass
class PaymentLink:
    """Phase X: a one-off payment request. `url` is the hosted page the payer opens; None under
    the manual provider, where the operator confirms the payment instead."""
    ref: str
    url: Optional[str] = None


@dataclass
class GatewaySubscription:
    ref: str
    checkout_url: Optional[str] = None


def _paise(amount_rupees: float) -> int:
    return int(round(amount_rupees * 100))


class RazorpayProvider:
    name = "razorpay"

    def __init__(self, key_id: str, key_secret: str, webhook_secret: str, *, base_url: Optional[str] = None,
                 client: Optional[httpx.AsyncClient] = None) -> None:
        self.key_id, self.key_secret, self.webhook_secret = key_id, key_secret, webhook_secret
        self.base_url = (base_url or config.RAZORPAY_BASE_URL).rstrip("/")
        self._client = client

    # --- HTTP -----------------------------------------------------------------------------------

    async def _request(self, method: str, path: str, body: Optional[dict] = None) -> Dict[str, Any]:
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT)
        try:
            response = await client.request(method, f"{self.base_url}{path}", json=body, auth=(self.key_id, self.key_secret))
        except httpx.HTTPError as exc:
            raise GatewayError(f"Razorpay unreachable: {exc.__class__.__name__}") from exc
        finally:
            if owns:
                await client.aclose()
        if response.status_code >= 400:
            try:
                detail = response.json().get("error", {}).get("description") or response.text
            except ValueError:
                detail = response.text
            raise GatewayError(f"Razorpay HTTP {response.status_code}: {detail[:200]}")
        try:
            return response.json()
        except ValueError as exc:
            raise GatewayError("Razorpay answered with non-JSON") from exc

    # --- seam -----------------------------------------------------------------------------------

    async def ensure_plan(self, session: AsyncSession, plan: Plan, cycle: str, amount: float) -> str:
        row = await session.scalar(select(BillingGatewayPlanRecord).where(
            BillingGatewayPlanRecord.provider == self.name, BillingGatewayPlanRecord.plan_id == plan.id, BillingGatewayPlanRecord.billing_cycle == cycle))
        if row is not None and abs(row.amount - amount) < 0.005:
            return row.gateway_plan_id
        created = await self._request("POST", "/plans", {
            "period": PERIODS[cycle], "interval": 1,
            "item": {"name": f"{plan.name} ({cycle.lower()})", "amount": _paise(amount), "currency": plan.currency,
                     "description": plan.description[:255] if plan.description else f"{plan.name} plan"},
            "notes": {"plan_id": plan.id, "billing_cycle": cycle},
        })
        gateway_plan_id = created["id"]
        if row is None:
            session.add(BillingGatewayPlanRecord(provider=self.name, plan_id=plan.id, billing_cycle=cycle, gateway_plan_id=gateway_plan_id, amount=amount))
        else:   # price changed: a new Razorpay plan (plans are immutable there)
            row.gateway_plan_id, row.amount = gateway_plan_id, amount
        await session.flush()
        return gateway_plan_id

    async def create_subscription(self, session: AsyncSession, tenant: Tenant, plan: Plan, cycle: str, *,
                                  amount: float, trial_end: Optional[datetime] = None) -> GatewaySubscription:
        gateway_plan_id = await self.ensure_plan(session, plan, cycle, amount)
        body: Dict[str, Any] = {
            "plan_id": gateway_plan_id, "total_count": TOTAL_COUNT[cycle], "quantity": 1, "customer_notify": 1,
            "notes": {"tenant_id": str(tenant.id), "organisation": (tenant.name or "")[:100], "plan_id": plan.id, "billing_cycle": cycle},
        }
        if trial_end is not None:
            # First charge when the trial ends; the mandate is authorised now (a nominal ₹1-ish auth, refunded by Razorpay).
            body["start_at"] = int(trial_end.astimezone(timezone.utc).timestamp())
        created = await self._request("POST", "/subscriptions", body)
        return GatewaySubscription(ref=created["id"], checkout_url=created.get("short_url"))

    async def change_plan(self, session: AsyncSession, subscription: SubscriptionRecord, plan: Plan, cycle: str, *, amount: float) -> GatewaySubscription:
        gateway_plan_id = await self.ensure_plan(session, plan, cycle, amount)
        if not subscription.provider_ref or not subscription.provider_ref.startswith("sub_"):
            # Was created under the manual provider: start a fresh gateway subscription instead.
            tenant = await session.get(Tenant, subscription.tenant_id)
            return await self.create_subscription(session, tenant, plan, cycle, amount=amount)
        updated = await self._request("PATCH", f"/subscriptions/{subscription.provider_ref}", {
            "plan_id": gateway_plan_id, "quantity": 1, "schedule_change_at": "now", "customer_notify": 1,
        })
        return GatewaySubscription(ref=updated.get("id", subscription.provider_ref), checkout_url=updated.get("short_url") or subscription.checkout_url)

    async def create_payment_link(self, *, amount: float, currency: str, description: str, reference_id: str,
                                  notes: Optional[Dict[str, str]] = None, customer_email: Optional[str] = None) -> PaymentLink:
        """Phase X: Razorpay Payment Links - one hosted page per marketplace charge. `reference_id`
        comes back on the `payment_link.paid` webhook so the charge is matched without guessing."""
        body: Dict[str, Any] = {"amount": _paise(amount), "currency": currency, "accept_partial": False, "description": description[:255],
                                "reference_id": reference_id[:40], "notes": notes or {}, "reminder_enable": True}
        if customer_email:
            body["customer"] = {"email": customer_email}
            body["notify"] = {"email": True}
        created = await self._request("POST", "/payment_links", body)
        return PaymentLink(ref=created["id"], url=created.get("short_url"))

    async def cancel(self, session: AsyncSession, subscription: SubscriptionRecord, *, immediately: bool = False) -> None:
        if not subscription.provider_ref or not subscription.provider_ref.startswith("sub_"):
            return
        try:
            await self._request("POST", f"/subscriptions/{subscription.provider_ref}/cancel", {"cancel_at_cycle_end": 0 if immediately else 1})
        except GatewayError as exc:
            # Already cancelled/completed at the gateway is not a failure for us.
            if "400" not in str(exc):
                raise
            logger.warning("Razorpay cancel for %s: %s", subscription.provider_ref, exc)

    # --- webhooks -------------------------------------------------------------------------------

    def verify_signature(self, body: bytes, signature: Optional[str]) -> bool:
        if not self.webhook_secret or not signature:
            return False
        expected = hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature.strip())


def subscription_entity(payload: dict) -> dict:
    return ((payload.get("payload") or {}).get("subscription") or {}).get("entity") or {}


def payment_link_entity(payload: dict) -> dict:
    return ((payload.get("payload") or {}).get("payment_link") or {}).get("entity") or {}


def payment_entity(payload: dict) -> dict:
    return ((payload.get("payload") or {}).get("payment") or {}).get("entity") or {}


def build_from_config(client: Optional[httpx.AsyncClient] = None) -> Optional[RazorpayProvider]:
    if config.BILLING_PROVIDER != "razorpay":
        return None
    if not (config.RAZORPAY_KEY_ID and config.RAZORPAY_KEY_SECRET):
        logger.warning("BILLING_PROVIDER=razorpay but RAZORPAY_KEY_ID/KEY_SECRET are missing - falling back to the manual provider")
        return None
    if not config.RAZORPAY_WEBHOOK_SECRET:
        logger.warning("RAZORPAY_WEBHOOK_SECRET is not set - webhooks will be rejected until it is")
    return RazorpayProvider(config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET, config.RAZORPAY_WEBHOOK_SECRET, client=client)


def dump_event(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)[:60000]
