"""Phase X: marketplace revenue share - paid listings, charges settled by the operator or a
Razorpay payment link, the frozen platform fee, creator earnings, payout requests (encrypted
destination) settled or rejected by the operator, platform revenue and terms."""
import asyncio
import json

import httpx
from sqlalchemy import select

from app.billing import service as billing
from app.billing.razorpay import RazorpayProvider
from app.db.models import CustomStrategyRecord, MarketplacePayoutRecord
from tests.test_admin_api import _admin
from tests.test_auth_api import _session_factory, client
from tests.test_billing_razorpay import SECRET, _signed
from tests.test_phase_k_commercial import _owner, _strategy_and_run


def _run(coro):
    return asyncio.run(coro)


def _published(creator, admin, *, price: float, title="Paid RSI"):
    strategy_id, run_id = _strategy_and_run(creator)
    created = client.post("/api/marketplace/listings", headers=creator, json={
        "custom_strategy_id": strategy_id, "title": title, "backtest_run_id": run_id, "price": price,
        "description": "Buys when RSI crosses above 50 on 1-minute bars; stays flat in ranges and on expiry days."})
    assert created.status_code == 201, created.text
    listing = created.json()
    assert listing["price"] == price
    assert client.post(f"/api/marketplace/listings/{listing['id']}/submit", headers=creator).json()["status"] == "PENDING_REVIEW"
    published = client.post(f"/api/admin/marketplace/{listing['id']}/publish", headers=admin, json={"note": "ok"}).json()
    assert published["status"] == "PUBLISHED"
    return published


def _copies(tenant_id: int) -> int:
    async def go():
        async with _session_factory() as session:
            return len(list(await session.scalars(select(CustomStrategyRecord.id).where(CustomStrategyRecord.tenant_id == tenant_id))))
    return _run(go())


def test_paid_listing_is_copied_only_after_the_operator_confirms_payment():
    creator, creator_me = _owner("mpx-creator@example.com", plan="pro")
    admin, _ = _admin("mpx-admin@example.com")
    listing = _published(creator, admin, price=1500)
    mine = client.get("/api/marketplace/listings/mine", headers=creator).json()[0]
    assert mine["platform_fee_pct"] == 20.0 and mine["creator_net_per_sale"] == 1200.0    # frozen at publish

    buyer, buyer_me = _owner("mpx-buyer@example.com", plan="pro")
    seen = client.get("/api/marketplace", headers=buyer).json()[0]
    assert seen["price"] == 1500 and seen["currency"] == "INR" and seen["platform_fee_pct"] is None   # the split is the creator's business
    before = _copies(buyer_me["tenant_id"])
    first = client.post(f"/api/marketplace/{listing['id']}/subscribe", headers=buyer)
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["status"] == "PENDING_PAYMENT" and body["custom_strategy_id"] is None and body["checkout_url"] is None
    charge = body["charge"]
    assert charge["status"] == "OPEN" and charge["amount"] == 1500 and charge["platform_fee"] == 300 and charge["creator_net"] == 1200 and charge["provider"] == "manual"
    assert "operator" in body["next"] and _copies(buyer_me["tenant_id"]) == before          # nothing copied yet
    # A second click returns the same open charge.
    again = client.post(f"/api/marketplace/{listing['id']}/subscribe", headers=buyer)
    assert again.status_code == 202 and again.json()["charge"]["id"] == charge["id"]
    assert client.get("/api/marketplace/purchases", headers=buyer).json()[0]["status"] == "OPEN"
    assert client.get("/api/marketplace/earnings", headers=creator).json()["sales"] == 0

    # The operator sees the open charge and confirms the bank transfer.
    open_charges = client.get("/api/admin/marketplace/charges?status=OPEN", headers=admin).json()
    assert [c["id"] for c in open_charges] == [charge["id"]]
    assert client.post(f"/api/admin/marketplace/charges/{charge['id']}/paid", headers=buyer, json={"reference": "UTR1"}).status_code == 403
    paid = client.post(f"/api/admin/marketplace/charges/{charge['id']}/paid", headers=admin, json={"reference": "UTR-0001"})
    assert paid.status_code == 200 and paid.json()["status"] == "PAID" and paid.json()["payment_ref"] == "UTR-0001"
    assert _copies(buyer_me["tenant_id"]) == before + 1
    subs = client.get("/api/marketplace/subscriptions", headers=buyer).json()
    assert subs[0]["status"] == "ACTIVE" and subs[0]["custom_strategy_id"]
    # Settling twice changes nothing; the subscribe call now reports the active copy.
    assert client.post(f"/api/admin/marketplace/charges/{charge['id']}/paid", headers=admin, json={"reference": "UTR-0001"}).json()["status"] == "PAID"
    assert _copies(buyer_me["tenant_id"]) == before + 1
    assert client.post(f"/api/marketplace/{listing['id']}/subscribe", headers=buyer).status_code == 200

    earnings = client.get("/api/marketplace/earnings", headers=creator).json()
    assert earnings["sales"] == 1 and earnings["gross"] == 1500 and earnings["platform_fees"] == 300 and earnings["net"] == 1200 and earnings["available"] == 1200
    assert earnings["sales_rows"][0]["buyer_tenant_id"] == buyer_me["tenant_id"] and "checkout_url" not in {k for k, v in earnings["sales_rows"][0].items() if v}
    assert client.get("/api/marketplace/purchases", headers=buyer).json()[0]["status"] == "PAID"
    # Notifications reached both sides.
    creator_notes = client.get("/api/notifications", headers=creator).json()
    assert any("Marketplace sale" in n["title"] for n in (creator_notes if isinstance(creator_notes, list) else creator_notes.get("items", [])))

    # A voided charge cancels the pending subscription; buying again opens a fresh charge.
    other, other_me = _owner("mpx-other@example.com", plan="pro")
    pending = client.post(f"/api/marketplace/{listing['id']}/subscribe", headers=other).json()["charge"]
    voided = client.post(f"/api/admin/marketplace/charges/{pending['id']}/void", headers=admin, json={"note": "buyer withdrew"}).json()
    assert voided["status"] == "VOID"
    assert client.get("/api/marketplace/subscriptions", headers=other).json()[0]["status"] == "CANCELLED"
    fresh = client.post(f"/api/marketplace/{listing['id']}/subscribe", headers=other).json()["charge"]
    assert fresh["id"] != pending["id"] and fresh["status"] == "OPEN"
    assert client.post(f"/api/admin/marketplace/charges/{pending['id']}/paid", headers=admin, json={}).status_code == 400   # void stays void

    # Re-pricing: refused while published, allowed once unlisted, capped by the terms.
    assert client.put(f"/api/marketplace/listings/{listing['id']}/price", headers=creator, json={"price": 900}).status_code == 400
    client.post(f"/api/marketplace/listings/{listing['id']}/unlist", headers=creator)
    assert client.put(f"/api/marketplace/listings/{listing['id']}/price", headers=creator, json={"price": 900}).json()["price"] == 900
    assert client.put(f"/api/marketplace/listings/{listing['id']}/price", headers=creator, json={"price": 10_000_000}).status_code == 400


def test_payouts_need_the_minimum_store_the_destination_encrypted_and_settle_or_release():
    creator, creator_me = _owner("mpx-payee@example.com", plan="pro")
    admin, _ = _admin("mpx-payout-admin@example.com")
    assert client.put("/api/admin/controls/marketplace-terms", headers=admin, json={"values": {"min_payout": 1000}}).json()["min_payout"] == 1000
    listing = _published(creator, admin, price=600, title="Paid ATR")

    def buy(email):
        buyer, _ = _owner(email, plan="pro")
        charge = client.post(f"/api/marketplace/{listing['id']}/subscribe", headers=buyer).json()["charge"]
        assert client.post(f"/api/admin/marketplace/charges/{charge['id']}/paid", headers=admin, json={"reference": f"UTR-{charge['id']}"}).status_code == 200
    buy("mpx-b1@example.com")
    assert client.get("/api/marketplace/earnings", headers=creator).json()["available"] == 480
    assert client.post("/api/marketplace/payouts", headers=creator, json={"destination": "payee@okhdfcbank"}).status_code == 400   # below minimum
    buy("mpx-b2@example.com")
    e = client.get("/api/marketplace/earnings", headers=creator).json()
    assert e["available"] == 960 and e["can_request_payout"] is False
    assert client.put("/api/admin/controls/marketplace-terms", headers=admin, json={"values": {"min_payout": 500}}).status_code == 200
    assert client.get("/api/marketplace/earnings", headers=creator).json()["can_request_payout"] is True

    requested = client.post("/api/marketplace/payouts", headers=creator, json={"destination": "payee@okhdfcbank"})
    assert requested.status_code == 201, requested.text
    payout = requested.json()
    assert payout["amount"] == 960 and payout["status"] == "REQUESTED" and payout["destination_hint"] == "…bank"
    e = client.get("/api/marketplace/earnings", headers=creator).json()
    assert e["available"] == 0 and e["pending_payout"] == 960 and e["payouts"][0]["id"] == payout["id"]
    assert client.post("/api/marketplace/payouts", headers=creator, json={"destination": "payee@okhdfcbank"}).status_code == 400   # one at a time

    async def stored():
        async with _session_factory() as session:
            return await session.get(MarketplacePayoutRecord, payout["id"])
    row = _run(stored())
    assert "payee@okhdfcbank" not in row.destination_encrypted and row.destination_hint == "…bank"
    assert client.get(f"/api/admin/marketplace/payouts/{payout['id']}/destination", headers=creator).status_code == 403
    assert client.get(f"/api/admin/marketplace/payouts/{payout['id']}/destination", headers=admin).json()["destination"] == "payee@okhdfcbank"

    # Rejecting releases the earnings; a new request can then be paid, with a reference.
    rejected = client.post(f"/api/admin/marketplace/payouts/{payout['id']}/reject", headers=admin, json={"note": "account name mismatch"}).json()
    assert rejected["status"] == "REJECTED" and rejected["note"] == "account name mismatch"
    e = client.get("/api/marketplace/earnings", headers=creator).json()
    assert e["available"] == 960 and e["pending_payout"] == 0
    second = client.post("/api/marketplace/payouts", headers=creator, json={"destination": "HDFC0000123 / 50100012345678"}).json()
    assert client.post(f"/api/admin/marketplace/payouts/{second['id']}/paid", headers=admin, json={}).status_code == 400   # reference required
    paid = client.post(f"/api/admin/marketplace/payouts/{second['id']}/paid", headers=admin, json={"reference": "NEFT-778899"}).json()
    assert paid["status"] == "PAID" and paid["reference"] == "NEFT-778899"
    assert client.post(f"/api/admin/marketplace/payouts/{second['id']}/paid", headers=admin, json={"reference": "x"}).status_code == 400
    e = client.get("/api/marketplace/earnings", headers=creator).json()
    assert e["paid_out"] == 960 and e["available"] == 0 and e["pending_payout"] == 0
    revenue = client.get("/api/admin/marketplace/revenue", headers=admin).json()
    assert revenue["gross"] >= 1200 and revenue["platform_fees"] >= 240 and revenue["paid_out"] >= 960 and revenue["terms"]["min_payout"] == 500
    assert client.get("/api/admin/marketplace/payouts?status=PAID", headers=admin).json()[0]["id"] == second["id"]


def test_razorpay_payment_link_settles_the_charge_through_the_webhook_once():
    links = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/payment_links":
            body = json.loads(request.content)
            links.append(body)
            return httpx.Response(200, json={"id": f"plink_{len(links):03d}", "short_url": f"https://rzp.io/l/{len(links):03d}", "amount": body["amount"],
                                             "reference_id": body["reference_id"], "status": "created"})
        return httpx.Response(404, json={"error": {"description": "not found"}})

    billing.set_provider(RazorpayProvider("rzp_test_key", "rzp_test_secret", SECRET, base_url="https://api.razorpay.com/v1",
                                          client=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    try:
        creator, _ = _owner("mpx-rzp-creator@example.com", plan="pro")
        admin, _ = _admin("mpx-rzp-admin@example.com")
        listing = _published(creator, admin, price=2000, title="Paid via link")
        buyer, buyer_me = _owner("mpx-rzp-buyer@example.com", plan="pro")
        before = _copies(buyer_me["tenant_id"])
        resp = client.post(f"/api/marketplace/{listing['id']}/subscribe", headers=buyer)
        assert resp.status_code == 202, resp.text
        charge = resp.json()["charge"]
        assert resp.json()["checkout_url"] == "https://rzp.io/l/001" and charge["provider"] == "razorpay"
        assert links[0]["amount"] == 200000 and links[0]["reference_id"] == f"mpc-{charge['id']}" and links[0]["notes"]["charge_id"] == str(charge["id"])

        payload = {"event": "payment_link.paid", "created_at": 1_800_000_000, "payload": {
            "payment_link": {"entity": {"id": "plink_001", "reference_id": f"mpc-{charge['id']}", "amount": 200000, "amount_paid": 200000,
                                        "notes": {"charge_id": str(charge["id"])}, "status": "paid"}},
            "payment": {"entity": {"id": "pay_link_001", "amount": 200000, "status": "captured"}}}}
        body, headers = _signed(payload, "evt_plink_1")
        hook = client.post("/api/billing/webhooks/razorpay", content=body, headers=headers)
        assert hook.status_code == 200, hook.text
        assert "settled" in hook.json()["result"]
        assert _copies(buyer_me["tenant_id"]) == before + 1
        assert client.get("/api/marketplace/purchases", headers=buyer).json()[0]["payment_ref"] == "pay_link_001"
        # Redelivery of the same event: recorded once, no second copy.
        redelivered = client.post("/api/billing/webhooks/razorpay", content=body, headers=headers).json()
        assert redelivered["event_id"] == "evt_plink_1" and _copies(buyer_me["tenant_id"]) == before + 1
        # An underpaid or unknown link is ignored, not booked.
        under = dict(payload); under["payload"] = json.loads(json.dumps(payload["payload"]))
        under["payload"]["payment"]["entity"]["amount"] = 100
        b2, h2 = _signed(under, "evt_plink_2")
        assert "ignored" in client.post("/api/billing/webhooks/razorpay", content=b2, headers=h2).json()["result"]
        unknown = dict(payload); unknown["payload"] = json.loads(json.dumps(payload["payload"]))
        unknown["payload"]["payment_link"]["entity"]["reference_id"] = "mpc-999999"; unknown["payload"]["payment_link"]["entity"]["notes"] = {}
        b3, h3 = _signed(unknown, "evt_plink_3")
        assert "unknown" in client.post("/api/billing/webhooks/razorpay", content=b3, headers=h3).json()["result"]
    finally:
        billing.set_provider(None)


def test_terms_are_validated_and_the_fee_is_frozen_per_listing_at_publish():
    admin, _ = _admin("mpx-terms-admin@example.com")
    assert client.put("/api/admin/controls/marketplace-terms", headers=admin, json={"values": {"platform_fee_pct": 95}}).status_code == 400
    assert client.put("/api/admin/controls/marketplace-terms", headers=admin, json={"values": {"bogus": 1}}).status_code == 400
    assert client.put("/api/admin/controls/marketplace-terms", headers=admin, json={"values": {"platform_fee_pct": 20, "max_listing_price": 5000}}).status_code == 200
    creator, _ = _owner("mpx-terms-creator@example.com", plan="pro")
    strategy_id, run_id = _strategy_and_run(creator)
    too_dear = client.post("/api/marketplace/listings", headers=creator, json={
        "custom_strategy_id": strategy_id, "title": "Too dear", "backtest_run_id": run_id, "price": 6000, "description": "A" * 50})
    assert too_dear.status_code == 400 and "cap" in too_dear.json()["detail"]
    assert client.get("/api/marketplace/terms", headers=creator).json()["platform_fee_pct"] == 20
    listing = _published(creator, admin, price=1000, title="Frozen fee")
    assert client.put("/api/admin/controls/marketplace-terms", headers=admin, json={"values": {"platform_fee_pct": 30}}).json()["platform_fee_pct"] == 30
    buyer, _ = _owner("mpx-terms-buyer@example.com", plan="pro")
    charge = client.post(f"/api/marketplace/{listing['id']}/subscribe", headers=buyer).json()["charge"]
    assert charge["platform_fee_pct"] == 20 and charge["creator_net"] == 800     # the listing keeps the fee it was published under
    # Free listings are untouched by all of this.
    free = _published(creator, admin, price=0, title="Still free")
    assert client.post(f"/api/marketplace/{free['id']}/subscribe", headers=buyer).status_code == 200
    # Restore the default terms for the other tests.
    client.put("/api/admin/controls/marketplace-terms", headers=admin, json={"values": {"platform_fee_pct": 20, "max_listing_price": 50000, "min_payout": 500}})
