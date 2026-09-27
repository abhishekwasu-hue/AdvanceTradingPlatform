"""Phase K1b: the Razorpay provider behind the billing seam - plan mirroring, hosted checkout,
plan change, cancel, and the signed, idempotent webhook that books payments."""
import asyncio
import hashlib
import hmac
import itertools
import json
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from app.billing import service as billing
from app.billing.razorpay import RazorpayProvider
from app.db.models import BillingGatewayPlanRecord, BillingTransactionRecord, BillingWebhookEventRecord, SubscriptionRecord, Tenant
from tests.test_auth_api import _register, _session_factory, client

SECRET = "whsec_test_secret_value"


def _run(coro):
    return asyncio.run(coro)


_PLAN_IDS = itertools.count(1)
_SUB_IDS = itertools.count(1)


class _Razorpay:
    """A fake api.razorpay.com: records requests, hands out globally unique ids (as the real one does)."""

    def __init__(self):
        self.requests = []
        self.plans, self.subs = 0, 0
        self.plan_ids, self.sub_ids = [], []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers.get("Authorization", "").startswith("Basic ")
        path = request.url.path
        body = json.loads(request.content or b"{}")
        if request.method == "POST" and path == "/v1/plans":
            self.plans += 1
            self.plan_ids.append(f"plan_{next(_PLAN_IDS):03d}")
            return httpx.Response(200, json={"id": self.plan_ids[-1], "entity": "plan", "period": body["period"], "item": body["item"]})
        if request.method == "POST" and path == "/v1/subscriptions":
            self.subs += 1
            self.sub_ids.append(f"sub_{next(_SUB_IDS):03d}")
            return httpx.Response(200, json={"id": self.sub_ids[-1], "entity": "subscription", "status": "created", "plan_id": body["plan_id"],
                                             "short_url": f"https://rzp.io/i/{self.sub_ids[-1]}", "start_at": body.get("start_at")})
        if request.method == "PATCH" and path.startswith("/v1/subscriptions/"):
            return httpx.Response(200, json={"id": path.rsplit("/", 1)[1], "plan_id": body["plan_id"], "status": "active"})
        if request.method == "POST" and path.endswith("/cancel"):
            return httpx.Response(200, json={"id": path.split("/")[3], "status": "cancelled"})
        return httpx.Response(404, json={"error": {"description": "not found"}})


@pytest.fixture
def gateway():
    fake = _Razorpay()
    provider = RazorpayProvider("rzp_test_key", "rzp_test_secret", SECRET, base_url="https://api.razorpay.com/v1",
                                client=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    billing.set_provider(provider)
    yield fake, provider
    billing.set_provider(None)


def _owner(email: str):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    return headers, client.get("/api/auth/me", headers=headers).json()


def _signed(payload: dict, event_id: str, secret: str = SECRET):
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id, "Content-Type": "application/json"}


def _sub(tenant_id: int) -> SubscriptionRecord:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(SubscriptionRecord).where(SubscriptionRecord.tenant_id == tenant_id))
    return _run(go())


def test_subscribe_mirrors_plan_once_and_stores_hosted_checkout(gateway):
    fake, _ = gateway
    headers, me = _owner("rzp-owner@example.com")
    sub = client.post("/api/billing/subscribe", headers=headers, json={"plan_id": "pro", "billing_cycle": "MONTHLY"})
    assert sub.status_code == 200, sub.text
    body = sub.json()
    assert body["provider"] == "razorpay" and body["checkout_url"] == f"https://rzp.io/i/{fake.sub_ids[0]}" and body["status"] == "TRIALING"
    plan_req = json.loads(fake.requests[0].content)
    assert fake.requests[0].url.path == "/v1/plans" and plan_req["period"] == "monthly" and plan_req["item"]["amount"] == 299900 and plan_req["item"]["currency"] == "INR"
    sub_req = json.loads(fake.requests[1].content)
    assert sub_req["plan_id"] == fake.plan_ids[0] and sub_req["notes"]["tenant_id"] == str(me["tenant_id"]) and sub_req["total_count"] == 120
    # First charge is deferred to the end of the 14-day trial.
    assert abs(sub_req["start_at"] - (datetime.now(timezone.utc).timestamp() + 14 * 86400)) < 120

    # A second tenant on the same plan/cycle reuses the mirrored Razorpay plan.
    headers2, _ = _owner("rzp-owner2@example.com")
    client.post("/api/billing/subscribe", headers=headers2, json={"plan_id": "pro", "billing_cycle": "MONTHLY"})
    assert fake.plans == 1 and fake.subs == 2

    async def rows():
        async with _session_factory() as session:
            return list(await session.scalars(select(BillingGatewayPlanRecord)))
    mapped = _run(rows())
    assert [(r.plan_id, r.billing_cycle, r.gateway_plan_id) for r in mapped if r.plan_id == "pro"] == [("pro", "MONTHLY", fake.plan_ids[0])]
    assert client.get("/api/billing", headers=headers).json()["subscription"]["checkout_url"] == f"https://rzp.io/i/{fake.sub_ids[0]}"


def test_webhook_rejects_bad_signature_books_charges_once_and_handles_failures(gateway):
    fake, _ = gateway
    headers, me = _owner("rzp-webhook@example.com")
    client.post("/api/billing/subscribe", headers=headers, json={"plan_id": "business", "billing_cycle": "YEARLY"})
    ref = _sub(me["tenant_id"]).provider_ref
    assert ref.startswith("sub_")

    charged = {"event": "subscription.charged", "created_at": 1_800_000_000,
               "payload": {"subscription": {"entity": {"id": ref, "status": "active"}}, "payment": {"entity": {"id": "pay_001", "amount": 14999000, "currency": "INR"}}}}
    body, hdrs = _signed(charged, "evt_1", secret="wrong")
    assert client.post("/api/billing/webhooks/razorpay", content=body, headers=hdrs).status_code == 400
    assert client.post("/api/billing/webhooks/razorpay", content=body, headers={"Content-Type": "application/json"}).status_code == 400

    body, hdrs = _signed(charged, "evt_1")
    ok = client.post("/api/billing/webhooks/razorpay", content=body, headers=hdrs)
    assert ok.status_code == 200, ok.text
    assert ok.json()["result"].startswith("payment pay_001 booked"), ok.json()
    sub = _sub(me["tenant_id"])
    assert sub.status == "ACTIVE" and sub.grace_until is None
    txns = client.get("/api/billing/transactions", headers=headers).json()
    assert {(t["kind"], t["status"]) for t in txns} == {("INVOICE", "PAID"), ("PAYMENT", "PAID")}
    assert next(t for t in txns if t["kind"] == "PAYMENT")["amount"] == 149990 and next(t for t in txns if t["kind"] == "PAYMENT")["provider_ref"] == "pay_001"

    # Redelivery (same event id) and a duplicate payment id under a new event id both book nothing.
    assert client.post("/api/billing/webhooks/razorpay", content=body, headers=hdrs).status_code == 200
    body2, hdrs2 = _signed(charged, "evt_1_retry")
    assert client.post("/api/billing/webhooks/razorpay", content=body2, headers=hdrs2).status_code == 200
    txns = client.get("/api/billing/transactions", headers=headers).json()
    assert sum(1 for t in txns if t["kind"] == "PAYMENT") == 1

    failed = {"event": "payment.failed", "payload": {"subscription": {"entity": {"id": ref}}, "payment": {"entity": {"id": "pay_002", "amount": 14999000, "error_description": "Card declined"}}}}
    body, hdrs = _signed(failed, "evt_2")
    assert "recorded" in client.post("/api/billing/webhooks/razorpay", content=body, headers=hdrs).json()["result"]
    txns = client.get("/api/billing/transactions", headers=headers).json()
    assert any(t["kind"] == "FAILED_PAYMENT" and "Card declined" in t["description"] for t in txns)
    notes = client.get("/api/notifications", headers=headers).json()
    items = notes if isinstance(notes, list) else notes.get("items", [])
    assert any(n["title"] == "Payment failed" for n in items)

    halted = {"event": "subscription.halted", "payload": {"subscription": {"entity": {"id": ref, "status": "halted"}}}}
    body, hdrs = _signed(halted, "evt_3")
    client.post("/api/billing/webhooks/razorpay", content=body, headers=hdrs)
    sub = _sub(me["tenant_id"])
    assert sub.status == "PAST_DUE" and sub.grace_until is not None

    cancelled = {"event": "subscription.cancelled", "payload": {"subscription": {"entity": {"id": ref, "status": "cancelled"}}}}
    body, hdrs = _signed(cancelled, "evt_4")
    client.post("/api/billing/webhooks/razorpay", content=body, headers=hdrs)
    sub = _sub(me["tenant_id"])

    async def tenant():
        async with _session_factory() as session:
            return await session.get(Tenant, me["tenant_id"])
    assert sub.status == "CANCELLED" and _run(tenant()).plan == "free"

    async def events():
        async with _session_factory() as session:
            return [(e.event_id, e.event_type) for e in await session.scalars(select(BillingWebhookEventRecord).where(BillingWebhookEventRecord.tenant_id == me["tenant_id"]).order_by(BillingWebhookEventRecord.id))]
    assert _run(events()) == [("evt_1", "subscription.charged"), ("evt_1_retry", "subscription.charged"), ("evt_2", "payment.failed"),
                              ("evt_3", "subscription.halted"), ("evt_4", "subscription.cancelled")]
    unknown = {"event": "refund.created", "payload": {"subscription": {"entity": {"id": ref}}}}
    body, hdrs = _signed(unknown, "evt_5")
    assert client.post("/api/billing/webhooks/razorpay", content=body, headers=hdrs).json()["result"].startswith("ignored")


def test_plan_change_and_cancel_go_to_the_gateway(gateway):
    fake, _ = gateway
    headers, me = _owner("rzp-change@example.com")
    client.post("/api/billing/subscribe", headers=headers, json={"plan_id": "pro", "billing_cycle": "MONTHLY"})
    changed = client.post("/api/billing/subscribe", headers=headers, json={"plan_id": "business", "billing_cycle": "YEARLY"})
    assert changed.status_code == 200, changed.text
    patch = [r for r in fake.requests if r.method == "PATCH"]

    async def mapped():
        async with _session_factory() as session:
            return await session.scalar(select(BillingGatewayPlanRecord.gateway_plan_id).where(
                BillingGatewayPlanRecord.plan_id == "business", BillingGatewayPlanRecord.billing_cycle == "YEARLY"))
    assert len(patch) == 1 and json.loads(patch[0].content)["plan_id"] == _run(mapped()) and json.loads(patch[0].content)["schedule_change_at"] == "now"
    assert changed.json()["plan_id"] == "business"

    cancelled = client.post("/api/billing/cancel", headers=headers, json={"immediately": False})
    assert cancelled.status_code == 200
    cancel_calls = [r for r in fake.requests if r.url.path.endswith("/cancel")]
    assert len(cancel_calls) == 1
    # A TRIALING subscription cancels now, so the gateway is told not to wait for the cycle end.
    assert json.loads(cancel_calls[0].content) == {"cancel_at_cycle_end": 0}
    assert _sub(me["tenant_id"]).status == "CANCELLED"


def test_webhook_is_404_under_the_manual_provider():
    billing.set_provider(None)
    body, hdrs = _signed({"event": "subscription.charged", "payload": {}}, "evt_manual")
    assert client.post("/api/billing/webhooks/razorpay", content=body, headers=hdrs).status_code == 404


def test_gateway_errors_surface_as_400_on_subscribe():
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "BAD_REQUEST_ERROR", "description": "Authentication failed"}})
    billing.set_provider(RazorpayProvider("k", "s", SECRET, client=httpx.AsyncClient(transport=httpx.MockTransport(failing))))
    try:
        headers, _ = _owner("rzp-fail@example.com")
        resp = client.post("/api/billing/subscribe", headers=headers, json={"plan_id": "pro", "billing_cycle": "MONTHLY"})
        assert resp.status_code == 400 and "Razorpay HTTP 401" in resp.json()["detail"]
    finally:
        billing.set_provider(None)
