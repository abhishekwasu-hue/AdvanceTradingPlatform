"""Phase K: plans with prices and feature gates, subscriptions + billing lifecycle + metering,
the strategy marketplace, public API keys and `/api/public/v1`, and the HMAC-signed webhook
alert channel."""
import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.alerts.dispatcher import sign_webhook
from app.billing import service as billing
from app.db.models import CustomStrategyRecord, SubscriptionRecord, Tenant, UsageRecord
from app.plans.limits import feature_allowed
from app.plans.registry import get_plan
from app.public_api.keys import limiter
from tests.test_admin_api import _admin
from tests.test_alerts import _auth as _alert_auth, _deliveries, _dispatch, _notify, _put
from tests.test_auth_api import _register, _session_factory, client
from tests.test_custom_strategies_api import _rsi_config
from app.core.enums import NotificationSeverity
from tests.utils import decline_then_rally, make_series


def _run(coro):
    return asyncio.run(coro)


def _owner(email: str, plan: str | None = None):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    if plan:
        _set_plan(me["tenant_id"], plan)
    return headers, me


def _set_plan(tenant_id: int, plan: str) -> None:
    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            tenant.plan = plan
            await session.commit()
    _run(go())


def _tenant(tenant_id: int) -> Tenant:
    async def go():
        async with _session_factory() as session:
            return await session.get(Tenant, tenant_id)
    return _run(go())


# --- K1: plans, subscriptions, billing lifecycle, metering ----------------------------------------

def test_plan_catalogue_carries_prices_limits_and_feature_flags():
    plans = {p["id"]: p for p in client.get("/api/billing/plans").json()}
    assert plans["free"]["price_monthly"] == 0 and plans["free"]["marketplace_access"] is False and plans["free"]["api_calls_per_day"] == 0
    assert plans["pro"]["price_monthly"] == 2999 and plans["pro"]["trial_days"] == 14 and plans["pro"]["option_features"] is True
    assert plans["business"]["price_yearly"] == 149990 and plans["business"]["support_level"] == "priority"
    assert get_plan("pro").max_api_calls_per_day > 0
    free = Tenant(id=0, name="x", plan="free", status="active")
    assert not feature_allowed(free, "public_api") and not feature_allowed(free, "marketplace_access")
    free.plan, free.status = "pro", "suspended"
    assert not feature_allowed(free, "public_api")   # suspended tenants lose paid features


def test_subscribe_starts_trial_then_invoice_payment_activates_and_cancel_ends_period():
    headers, me = _owner("billing-owner@example.com")
    overview = client.get("/api/billing", headers=headers).json()
    assert overview["subscription"]["status"] == "NONE" and overview["subscription"]["plan_id"] == "free"

    sub = client.post("/api/billing/subscribe", headers=headers, json={"plan_id": "pro", "billing_cycle": "MONTHLY"})
    assert sub.status_code == 200, sub.text
    assert sub.json()["status"] == "TRIALING" and sub.json()["plan_id"] == "pro" and sub.json()["trial_end"]
    assert _tenant(me["tenant_id"]).plan == "pro"          # the trial applies the plan immediately

    # Trial runs out: the sweep raises an invoice and the subscription goes PAST_DUE with a grace period.
    async def expire_trial():
        async with _session_factory() as session:
            record = await session.scalar(select(SubscriptionRecord).where(SubscriptionRecord.tenant_id == me["tenant_id"]))
            record.trial_end = record.current_period_end = datetime.now(timezone.utc) - timedelta(days=1)
            await session.commit()
            return await billing.sweep(session)
    counts = _run(expire_trial())
    assert counts["trial_ended"] == 1
    overview = client.get("/api/billing", headers=headers).json()
    assert overview["subscription"]["status"] == "PAST_DUE" and overview["subscription"]["grace_until"]
    txns = client.get("/api/billing/transactions", headers=headers).json()
    # The invoice raised at subscription (payable after the trial) plus the renewal raised by the sweep.
    assert [t["kind"] for t in txns] == ["INVOICE", "INVOICE"] and all(t["status"] == "OPEN" and t["amount"] == 2999 for t in txns)

    # Operator records the payment (manual provider): invoice PAID, ACTIVE, period extended.
    admin_headers, _ = _admin("billing-admin@example.com")
    paid = client.post(f"/api/admin/billing/{me['tenant_id']}/payment", headers=admin_headers, json={"amount": 2999, "reference": "UPI/123"})
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "ACTIVE" and paid.json()["current_period_end"]
    txns = client.get("/api/billing/transactions", headers=headers).json()
    assert {(t["kind"], t["status"]) for t in txns} == {("INVOICE", "PAID"), ("PAYMENT", "PAID")}
    assert _tenant(me["tenant_id"]).plan == "pro" and _tenant(me["tenant_id"]).status == "active"

    # Cancel at period end keeps the plan until then.
    cancelled = client.post("/api/billing/cancel", headers=headers, json={"immediately": False}).json()
    assert cancelled["cancel_at_period_end"] is True and cancelled["status"] == "ACTIVE"
    assert _tenant(me["tenant_id"]).plan == "pro"


def test_grace_period_running_out_downgrades_to_free_and_records_the_reason():
    headers, me = _owner("billing-grace@example.com")
    client.post("/api/billing/subscribe", headers=headers, json={"plan_id": "business", "billing_cycle": "YEARLY"})

    async def run_out():
        async with _session_factory() as session:
            record = await session.scalar(select(SubscriptionRecord).where(SubscriptionRecord.tenant_id == me["tenant_id"]))
            record.status, record.trial_end = "PAST_DUE", None
            record.current_period_end = datetime.now(timezone.utc) - timedelta(days=8)
            record.grace_until = datetime.now(timezone.utc) - timedelta(hours=1)
            await session.commit()
            return await billing.sweep(session)
    counts = _run(run_out())
    assert counts["grace_expired"] == 1
    tenant = _tenant(me["tenant_id"])
    assert tenant.status == "active" and tenant.plan == "free" and "grace" in (tenant.status_reason or "").lower()
    overview = client.get("/api/billing", headers=headers).json()
    assert overview["subscription"]["status"] == "CANCELLED" and overview["status_reason"]


def test_billing_endpoints_need_owner_and_unknown_plan_is_rejected():
    headers, _ = _owner("billing-plan-bad@example.com")
    assert client.post("/api/billing/subscribe", headers=headers, json={"plan_id": "platinum", "billing_cycle": "MONTHLY"}).status_code == 400
    assert client.get("/api/billing").status_code in (401, 403)
    assert client.post("/api/admin/billing/1/payment", headers=headers, json={"amount": 1}).status_code == 403


def test_backtests_are_metered_per_run():
    headers, me = _owner("billing-meter@example.com")
    df = make_series(decline_then_rally())
    candles = [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for ts, r in df.iterrows()]
    resp = client.post("/api/backtest", headers=headers, json={"strategy_id": "ema_rsi_scalper_1m", "symbol": "TEST", "base_timeframe": "1min", "candles": candles})
    assert resp.status_code == 200, resp.text
    usage = client.get("/api/billing/usage", headers=headers).json()["metrics"]
    assert usage.get("backtest") == 1

    # Every order attempt is metered at creation (see the public-API signal test for the count).
    async def order_rows():
        async with _session_factory() as session:
            return list(await session.scalars(select(UsageRecord).where(UsageRecord.tenant_id == me["tenant_id"], UsageRecord.metric == "order")))
    assert _run(order_rows()) == []


# --- K2: marketplace --------------------------------------------------------------------------

def _strategy_and_run(headers):
    created = client.post("/api/custom-strategies", headers=headers, json=_rsi_config("Market RSI"))
    assert created.status_code in (200, 201), created.text
    df = make_series(decline_then_rally())
    candles = [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for ts, r in df.iterrows()]
    bt = client.post("/api/backtest", headers=headers, json={"strategy_id": created.json()["strategy_id"], "symbol": "TEST", "base_timeframe": "1min", "candles": candles})
    assert bt.status_code == 200, bt.text
    return created.json()["id"], bt.json()["run_id"]


def test_marketplace_flow_create_submit_publish_discover_subscribe_copies_strategy():
    creator, creator_me = _owner("mp-creator@example.com", plan="pro")
    strategy_id, run_id = _strategy_and_run(creator)

    # Submitting without documented performance is refused (V3.14 rule 8).
    bare = client.post("/api/marketplace/listings", headers=creator, json={
        "custom_strategy_id": strategy_id, "title": "RSI cross", "description": "A" * 50}).json()
    assert client.post(f"/api/marketplace/listings/{bare['id']}/submit", headers=creator).status_code == 400

    listing = client.post("/api/marketplace/listings", headers=creator, json={
        "custom_strategy_id": strategy_id, "title": "RSI cross 1-minute", "backtest_run_id": run_id,
        "description": "Buys when RSI(14) crosses above 50 on 1-minute bars; flat in ranges, avoid expiry days.",
        "methodology": "One timeframe, fixed stop; performance shown is the attached backtest."})
    assert listing.status_code == 201, listing.text
    body = listing.json()
    assert body["status"] == "DRAFT" and body["performance"]["backtest_run_id"] == run_id and body["version_number"] == 1
    assert "not a promise" in body["disclaimer"]
    assert client.post(f"/api/marketplace/listings/{body['id']}/submit", headers=creator).json()["status"] == "PENDING_REVIEW"

    # Not visible to others until published; a free tenant is gated altogether.
    free_headers, _ = _owner("mp-free@example.com")
    assert client.get("/api/marketplace", headers=free_headers).status_code == 402
    subscriber, sub_me = _owner("mp-subscriber@example.com", plan="pro")
    assert client.get("/api/marketplace", headers=subscriber).json() == []
    assert client.get(f"/api/marketplace/{body['id']}", headers=subscriber).status_code == 404

    admin_headers, _ = _admin("mp-admin@example.com")
    pending = client.get("/api/admin/marketplace/pending", headers=admin_headers).json()
    assert [p["id"] for p in pending] == [body["id"]] and "config" in pending[0]
    published = client.post(f"/api/admin/marketplace/{body['id']}/publish", headers=admin_headers, json={"note": "ok"})
    assert published.status_code == 200 and published.json()["status"] == "PUBLISHED"

    # Creator edits the strategy afterwards - the listing keeps the frozen version.
    edited = dict(_rsi_config("Market RSI v2"))
    edited["long_conditions"][0]["right"]["value"] = 60
    assert client.put(f"/api/custom-strategies/{strategy_id}", headers=creator, json=edited).status_code == 200

    discovered = client.get("/api/marketplace", headers=subscriber).json()
    assert len(discovered) == 1 and discovered[0]["title"] == "RSI cross 1-minute" and "config" not in discovered[0]
    assert discovered[0]["custom_strategy_id"] is None   # creator internals are not exposed
    assert client.post(f"/api/marketplace/{body['id']}/subscribe", headers=creator).status_code == 400   # own listing

    sub = client.post(f"/api/marketplace/{body['id']}/subscribe", headers=subscriber)
    assert sub.status_code == 200, sub.text
    copy_id = sub.json()["custom_strategy_id"]
    assert sub.json()["strategy_id"] == f"custom_{copy_id}" and "paper-trade" in sub.json()["next"]

    async def copy_config():
        async with _session_factory() as session:
            record = await session.get(CustomStrategyRecord, copy_id)
            return record.tenant_id, json.loads(record.config_json)
    tenant_id, config = _run(copy_config())
    assert tenant_id == sub_me["tenant_id"] and tenant_id != creator_me["tenant_id"]
    assert config["long_conditions"][0]["right"]["value"] == 50      # frozen v1, not the creator's later edit
    assert "marketplace" in config["name"]
    mine = client.get("/api/custom-strategies", headers=subscriber).json()
    assert any(s["id"] == copy_id for s in mine)

    # Idempotent subscribe, counter, unsubscribe keeps the copy.
    assert client.post(f"/api/marketplace/{body['id']}/subscribe", headers=subscriber).json()["custom_strategy_id"] == copy_id
    assert client.get(f"/api/marketplace/{body['id']}", headers=subscriber).json()["subscriber_count"] == 1
    assert client.post(f"/api/marketplace/{body['id']}/unsubscribe", headers=subscriber).status_code == 204
    assert client.get(f"/api/marketplace/{body['id']}", headers=subscriber).json()["subscriber_count"] == 0
    assert any(s["id"] == copy_id for s in client.get("/api/custom-strategies", headers=subscriber).json())
    subs = client.get("/api/marketplace/subscriptions", headers=subscriber).json()
    assert subs[0]["status"] == "CANCELLED"

    # Unlist hides it from discovery; creator's own list still shows everything with review notes.
    assert client.post(f"/api/marketplace/listings/{body['id']}/unlist", headers=creator).json()["status"] == "UNLISTED"
    assert client.get("/api/marketplace", headers=subscriber).json() == []
    mine_listings = client.get("/api/marketplace/listings/mine", headers=creator).json()
    assert {l["status"] for l in mine_listings} == {"DRAFT", "UNLISTED"}


def test_marketplace_reject_returns_note_to_creator_and_review_needs_super_admin():
    creator, _ = _owner("mp-reject@example.com", plan="business")
    strategy_id, run_id = _strategy_and_run(creator)
    listing = client.post("/api/marketplace/listings", headers=creator, json={
        "custom_strategy_id": strategy_id, "title": "Rejectable", "backtest_run_id": run_id,
        "description": "Long enough description to pass validation of the marketplace listing."}).json()
    client.post(f"/api/marketplace/listings/{listing['id']}/submit", headers=creator)
    assert client.post(f"/api/admin/marketplace/{listing['id']}/publish", headers=creator, json={}).status_code == 403
    admin_headers, _ = _admin("mp-reject-admin@example.com")
    rejected = client.post(f"/api/admin/marketplace/{listing['id']}/reject", headers=admin_headers, json={"note": "Backtest period too short"}).json()
    assert rejected["status"] == "REJECTED" and rejected["review_note"] == "Backtest period too short"
    mine = client.get("/api/marketplace/listings/mine", headers=creator).json()
    assert mine[0]["review_note"] == "Backtest period too short"
    assert client.post(f"/api/marketplace/listings/{listing['id']}/submit", headers=creator).json()["status"] == "PENDING_REVIEW"


# --- K3: public API keys + /api/public/v1 --------------------------------------------------------

def test_api_keys_are_shown_once_scoped_rate_limited_and_metered():
    limiter.reset()
    headers, me = _owner("apikey-owner@example.com", plan="pro")
    assert client.post("/api/api-keys", headers=headers, json={"name": "bot", "scopes": ["read:nothing"]}).status_code == 400
    created = client.post("/api/api-keys", headers=headers, json={"name": "bot", "scopes": ["read:positions", "read:account"], "rate_limit_per_minute": 3})
    assert created.status_code == 201, created.text
    key = created.json()["key"]
    assert key.startswith("atp_") and created.json()["note"]
    listed = client.get("/api/api-keys", headers=headers).json()
    assert len(listed) == 1 and "key" not in listed[0] and listed[0]["key_prefix"] == created.json()["key_prefix"]

    assert client.get("/api/public/v1/positions").status_code == 401
    assert client.get("/api/public/v1/positions", headers={"X-API-Key": "atp_deadbeef_nope"}).status_code == 401
    assert client.get("/api/public/v1/orders", headers={"X-API-Key": key}).status_code == 403        # scope missing
    ok = client.get("/api/public/v1/positions", headers={"X-API-Key": key})
    assert ok.status_code == 200 and ok.json() == []
    account = client.get("/api/public/v1/account", headers={"X-API-Key": key}).json()
    assert account["plan"] == "pro" and account["tenant_id"] == me["tenant_id"] and account["key"] == "bot"
    # 3 per minute: the third call succeeds, the fourth is throttled.
    assert client.get("/api/public/v1/positions", headers={"X-API-Key": key}).status_code == 200
    assert client.get("/api/public/v1/positions", headers={"X-API-Key": key}).status_code == 429

    usage = client.get("/api/billing/usage", headers=headers).json()["metrics"]
    assert usage.get("api_call") == 3
    assert client.get("/api/api-keys", headers=headers).json()[0]["last_used_at"]

    # A public key never reaches console endpoints.
    assert client.get("/api/deployments", headers={"X-API-Key": key}).status_code in (401, 403)

    key_id = created.json()["id"]
    assert client.delete(f"/api/api-keys/{key_id}", headers=headers).status_code == 204
    limiter.reset()
    assert client.get("/api/public/v1/positions", headers={"X-API-Key": key}).status_code == 401
    docs = client.get("/api/public/v1/docs").json()
    assert docs["version"] == "v1" and any(e["path"] == "/signals" and e["method"] == "POST" for e in docs["endpoints"])


def test_public_api_is_plan_gated_and_signal_submission_executes_paper_idempotently():
    limiter.reset()
    free_headers, _ = _owner("apikey-free@example.com")
    assert client.post("/api/api-keys", headers=free_headers, json={"name": "bot", "scopes": ["read:account"]}).status_code == 402

    headers, me = _owner("apikey-signals@example.com", plan="business")
    key = client.post("/api/api-keys", headers=headers, json={"name": "signals", "scopes": ["write:signals", "read:orders", "read:strategies"]}).json()["key"]
    strategies = client.get("/api/public/v1/strategies", headers={"X-API-Key": key}).json()
    assert any(s["kind"] == "inbuilt" for s in strategies)
    body = {"strategy_id": "ema_rsi_scalper_1m", "symbol": "reliance", "direction": "LONG", "entry": 2500, "stop_loss": 2490, "target1": 2520,
            "idempotency_key": "external-alert-0001"}
    first = client.post("/api/public/v1/signals", headers={"X-API-Key": key}, json=body)
    assert first.status_code == 201, first.text
    second = client.post("/api/public/v1/signals", headers={"X-API-Key": key}, json=body)
    assert second.status_code == 201 and second.json()["order_id"] == first.json()["order_id"]
    orders = client.get("/api/public/v1/orders", headers={"X-API-Key": key}).json()
    assert len(orders) == 1 and orders[0]["symbol"] == "RELIANCE" and orders[0]["mode"] == "PAPER"
    usage = client.get("/api/billing/usage", headers=headers).json()["metrics"]
    assert usage.get("order") == 1 and usage.get("api_call") == 4      # idempotent replay creates no second order
    assert client.post("/api/public/v1/signals", headers={"X-API-Key": key}, json={**body, "mode": "LIVE"}).status_code == 409
    assert client.post("/api/public/v1/signals", headers={"X-API-Key": key}, json={**body, "direction": "NO_TRADE"}).status_code == 422


def test_expired_key_and_suspended_tenant_are_refused():
    limiter.reset()
    headers, me = _owner("apikey-expired@example.com", plan="pro")
    created = client.post("/api/api-keys", headers=headers, json={"name": "short", "scopes": ["read:account"], "expires_in_days": 1}).json()

    async def expire():
        async with _session_factory() as session:
            from app.db.models import ApiKeyRecord
            record = await session.get(ApiKeyRecord, created["id"])
            record.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            await session.commit()
    _run(expire())
    assert client.get("/api/public/v1/account", headers={"X-API-Key": created["key"]}).status_code == 401

    fresh = client.post("/api/api-keys", headers=headers, json={"name": "ok", "scopes": ["read:account"]}).json()["key"]
    assert client.get("/api/public/v1/account", headers={"X-API-Key": fresh}).status_code == 200

    async def suspend():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            tenant.status = "suspended"
            await session.commit()
    _run(suspend())
    assert client.get("/api/public/v1/account", headers={"X-API-Key": fresh}).status_code == 402


# --- K4: webhook alert channel ------------------------------------------------------------------

def test_webhook_channel_posts_signed_json_and_filters_event_types():
    headers, me = _alert_auth("alert-webhook@example.com")
    bad = _put(headers, "webhook", {"url": "http://example.com/hook", "secret": "s" * 20})
    assert bad.status_code == 422 or bad.status_code == 400   # https only
    created = _put(headers, "webhook", {"url": "https://hooks.example.com/atp", "secret": "supersecretvalue1234", "event_types": ["SYSTEM_FAILURE"]}, min_severity="INFO")
    assert created.status_code == 200, created.text
    assert created.json()["config"] == {"url": "https://hooks.example.com/atp", "event_types": ["SYSTEM_FAILURE"], "secret_set": True}
    assert "supersecretvalue1234" not in created.text

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})
    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    _notify(me["tenant_id"], NotificationSeverity.CRITICAL, title="Broker session expired")
    _dispatch(me["tenant_id"], client_=mock)
    assert len(seen) == 1
    request = seen[0]
    assert request.url == "https://hooks.example.com/atp" and request.headers["X-ATP-Event"] == "SYSTEM_FAILURE"
    payload = json.loads(request.content)
    assert payload["title"] == "Broker session expired" and payload["severity"] == "CRITICAL" and payload["tenant_id"] == me["tenant_id"]
    expected = "sha256=" + hmac.new(b"supersecretvalue1234", request.headers["X-ATP-Timestamp"].encode() + b"." + request.content, hashlib.sha256).hexdigest()
    assert request.headers["X-ATP-Signature"] == expected == sign_webhook("supersecretvalue1234", request.content, request.headers["X-ATP-Timestamp"])
    deliveries = _deliveries(me["tenant_id"])
    assert deliveries[-1].status == "SENT"

    # Endpoint failure is recorded, not swallowed.
    failing = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    _notify(me["tenant_id"], NotificationSeverity.CRITICAL, title="Second")
    _dispatch(me["tenant_id"], client_=failing)
    assert any("HTTP 500" in (d.last_error or "") for d in _deliveries(me["tenant_id"]))
