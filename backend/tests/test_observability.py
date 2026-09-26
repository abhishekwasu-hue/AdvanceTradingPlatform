"""Phase E1/E2: /metrics, deep health, request-id correlation and the /api/v1 alias."""
import asyncio
from datetime import datetime, timezone

from app.core import config
from app.db.models import WorkerHeartbeatRecord
from app.observability.middleware import sanitize_request_id
from tests.test_admin_api import _owner
from tests.test_auth_api import _session_factory, client


def test_metrics_endpoint_exposes_http_orders_and_platform_gauges():
    _owner("metrics-owner@example.com")  # generates requests + a login event
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "atp_http_requests_total" in body and 'route="/api/auth/register"' in body
    assert "atp_http_request_duration_seconds_bucket" in body
    assert "atp_login_attempts_total" in body
    assert "atp_active_deployments" in body and 'mode="LIVE"' in body
    assert "atp_open_positions" in body and "atp_alert_outbox_pending" in body
    assert "atp_worker_heartbeat_age_seconds" in body and "atp_login_failures_15m" in body
    # Route templates, never raw ids, so label cardinality stays bounded.
    client.get("/api/orders/999999/events")  # 401/404 - still observed under its template
    body = client.get("/metrics").text
    assert 'route="/api/orders/{order_id}/events"' in body and 'route="/api/orders/999999/events"' not in body


def test_metrics_token_protects_scrapes(monkeypatch):
    monkeypatch.setattr(config, "METRICS_TOKEN", "scrape-secret")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"}).status_code == 200


def test_orders_counter_increments_by_mode_and_status():
    from app.observability.metrics import ORDERS
    from tests.test_live_execution import _LiveBroker, _execute, _user

    before = ORDERS.labels(mode="LIVE", status="FILLED")._value.get()
    user = _user("metrics-live@example.com")
    result, order, _, _ = _execute(user, _LiveBroker())
    assert result.executed
    assert ORDERS.labels(mode="LIVE", status="FILLED")._value.get() == before + 1


def test_deep_health_reports_components_and_worker_state():
    resp = client.get("/api/system/health/deep")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert body["checks"]["database"]["status"] == "ok" and "latency_ms" in body["checks"]["database"]
    assert body["checks"]["redis"]["status"] in ("ok", "unreachable", "disabled")
    assert body["checks"]["migrations"]["status"] in ("ok", "unknown", "behind")
    assert body["checks"]["worker"]["status"] in ("never_seen", "ok", "stale", "stale_market_open")

    async def seed():
        async with _session_factory() as session:
            hb = await session.get(WorkerHeartbeatRecord, 1) if False else None
            from sqlalchemy import select
            hb = await session.scalar(select(WorkerHeartbeatRecord).where(WorkerHeartbeatRecord.worker_name == "trading_worker"))
            if hb is None:
                hb = WorkerHeartbeatRecord(worker_name="trading_worker")
                session.add(hb)
            hb.last_seen_at = datetime.now(timezone.utc)
            await session.commit()
    asyncio.run(seed())
    fresh = client.get("/api/system/health/deep").json()
    assert fresh["checks"]["worker"]["status"] == "ok" and fresh["checks"]["worker"]["seconds_since_heartbeat"] <= 5

    ready = client.get("/api/system/ready")
    assert ready.status_code == 200 and ready.json()["status"] == "ready"


def test_request_id_is_echoed_or_minted_and_sanitised():
    minted = client.get("/api/system/health")
    assert len(minted.headers["x-request-id"]) == 32
    echoed = client.get("/api/system/health", headers={"X-Request-ID": "trace-123.abc"})
    assert echoed.headers["x-request-id"] == "trace-123.abc"
    dirty = client.get("/api/system/health", headers={"X-Request-ID": "bad id\r\nX-Injected: 1" + "z" * 100})
    assert "\n" not in dirty.headers["x-request-id"] and len(dirty.headers["x-request-id"]) <= 64
    assert sanitize_request_id("") == ""


def test_api_v1_alias_serves_every_route_with_version_headers():
    headers, me = _owner("v1-owner@example.com")
    unversioned = client.get("/api/auth/me", headers=headers)
    versioned = client.get("/api/v1/auth/me", headers=headers)
    assert versioned.status_code == 200 and versioned.json() == unversioned.json()
    assert versioned.headers["x-api-version"] == "1" and "deprecation" not in versioned.headers
    assert unversioned.headers["x-api-version"] == "1" and unversioned.headers["deprecation"] == "true"
    assert unversioned.headers["link"] == '</api/v1/auth/me>; rel="successor-version"'

    # POST bodies and query strings pass through the rewrite unchanged.
    login = client.post("/api/v1/auth/login", json={"email": "v1-owner@example.com", "password": "S3cur3Pass!"})
    assert login.status_code == 200 and "access_token" in login.json()
    export = client.get("/api/v1/exports/orders?format=json", headers=headers)
    assert export.status_code == 200 and export.json()["manifest"]["dataset"] == "orders"
    # Unknown versions are not silently aliased.
    assert client.get("/api/v2/auth/me", headers=headers).status_code == 404
    # Non-API paths carry a request id but no API version header.
    metrics = client.get("/metrics")
    assert "x-request-id" in metrics.headers and "x-api-version" not in metrics.headers
