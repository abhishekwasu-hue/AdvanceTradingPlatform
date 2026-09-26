"""Phase B3: the SUPER_ADMIN console - bootstrap from env, tenant list/detail, plan and status
changes (audited + notified), platform audit trail, and that nobody else can reach any of it."""
import asyncio

from sqlalchemy import select

from app.admin import bootstrap
from app.db.models import Tenant, User
from tests.test_auth_api import _register, _session_factory, client
from tests.utils import enable_mfa


def _run(coro):
    return asyncio.run(coro)


def _owner(email: str):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    return headers, client.get("/api/auth/me", headers=headers).json()


def _admin(email: str):
    headers, me = _owner(email)

    async def promote():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            user.role = "SUPER_ADMIN"
            await session.commit()
    _run(promote())
    enable_mfa(headers)  # platform administrators must use MFA (Phase C3)
    return headers, me


def test_admin_endpoints_are_super_admin_only():
    owner_headers, _ = _owner("admin-owner-only@example.com")
    for path in ("/api/admin/overview", "/api/admin/tenants", "/api/admin/plans", "/api/admin/audit-logs", "/api/admin/deployments"):
        assert client.get(path).status_code in (401, 403)
        assert client.get(path, headers=owner_headers).status_code == 403
    assert client.patch("/api/admin/tenants/1", headers=owner_headers, json={"plan": "pro"}).status_code == 403


def test_configured_super_admin_email_is_promoted_on_register_and_at_startup(monkeypatch):
    _, existing = _owner("existing@example.com")  # registered before the email was configured: plain OWNER
    monkeypatch.setattr(bootstrap, "SUPER_ADMIN_EMAILS", {"ops@example.com", "existing@example.com"})

    async def startup():
        async with _session_factory() as session:
            return await bootstrap.promote_configured_super_admins(session)
    assert _run(startup()) >= 1

    headers = {"Authorization": f"Bearer {_register('ops@example.com')}"}
    assert client.get("/api/auth/me", headers=headers).json()["role"] == "SUPER_ADMIN"
    assert client.get("/api/admin/overview", headers=headers).status_code == 403  # until MFA is on
    enable_mfa(headers)
    assert client.get("/api/admin/overview", headers=headers).status_code == 200

    async def role_of(user_id):
        async with _session_factory() as session:
            return (await session.get(User, user_id)).role
    assert _run(role_of(existing["id"])) == "SUPER_ADMIN"


def test_overview_and_tenant_listing_span_tenants():
    admin_headers, _ = _admin("admin-list@example.com")
    _, a = _owner("admin-tenant-a@example.com")
    _, b = _owner("admin-tenant-b@example.com")

    overview = client.get("/api/admin/overview", headers=admin_headers).json()
    assert overview["tenants_total"] >= 3 and overview["users_total"] >= 3
    assert "active" in overview["tenants_by_status"] and "free" in overview["tenants_by_plan"]
    assert isinstance(overview["worker_running"], bool) and overview["global_kill_switch_engaged"] is False

    tenants = client.get("/api/admin/tenants", headers=admin_headers).json()
    ids = {t["id"] for t in tenants}
    assert {a["tenant_id"], b["tenant_id"]} <= ids
    row = next(t for t in tenants if t["id"] == a["tenant_id"])
    assert row["owners"] == ["admin-tenant-a@example.com"] and row["members"] == 1 and row["plan"] == "free"

    searched = client.get("/api/admin/tenants?q=admin-tenant-b", headers=admin_headers).json()
    assert [t["id"] for t in searched] == [b["tenant_id"]]

    plans = client.get("/api/admin/plans", headers=admin_headers).json()
    assert [p["id"] for p in plans] == ["free", "pro", "business"]


def test_plan_and_status_change_is_audited_and_notified():
    admin_headers, admin = _admin("admin-change@example.com")
    owner_headers, owner = _owner("admin-target@example.com")

    changed = client.patch(f"/api/admin/tenants/{owner['tenant_id']}", headers=admin_headers, json={"plan": "pro", "reason": "paid invoice 42"})
    assert changed.status_code == 200 and changed.json()["plan"] == "pro"
    assert client.get("/api/team/tenant", headers=owner_headers).json()["limits"]["live_trading"] is True

    # The tenant sees the change on its own audit trail and as a notification.
    logs = client.get("/api/admin/audit-logs?tenant_id=" + str(owner["tenant_id"]), headers=admin_headers).json()
    entry = next(l for l in logs if l["event"] == "tenant_updated_by_admin")
    assert "plan free -> pro" in entry["detail"] and "invoice 42" in entry["detail"] and entry["user_email"] == admin["email"]
    notes = client.get("/api/notifications", headers=owner_headers).json()
    assert any("plan free -> pro" in n["message"] for n in notes)

    suspended = client.patch(f"/api/admin/tenants/{owner['tenant_id']}", headers=admin_headers, json={"status": "suspended", "reason": "chargeback"})
    assert suspended.status_code == 200 and suspended.json()["status"] == "suspended"
    assert client.post("/api/team/invites", headers=owner_headers, json={"email": "x@example.com", "role": "USER"}).status_code == 403
    critical = [n for n in client.get("/api/notifications", headers=owner_headers).json() if n["severity"] == "CRITICAL"]
    assert any("suspended" in n["title"].lower() for n in critical)

    assert client.patch(f"/api/admin/tenants/{owner['tenant_id']}", headers=admin_headers, json={"status": "active"}).status_code == 200
    assert client.patch(f"/api/admin/tenants/{owner['tenant_id']}", headers=admin_headers, json={"plan": "platinum"}).status_code == 400
    assert client.patch(f"/api/admin/tenants/{owner['tenant_id']}", headers=admin_headers, json={"status": "frozen"}).status_code == 400
    assert client.patch(f"/api/admin/tenants/{owner['tenant_id']}", headers=admin_headers, json={}).status_code == 400
    assert client.patch("/api/admin/tenants/999999", headers=admin_headers, json={"plan": "pro"}).status_code == 404


def test_tenant_detail_shows_usage_users_brokers_and_deployments():
    admin_headers, _ = _admin("admin-detail@example.com")
    owner_headers, owner = _owner("admin-detail-target@example.com")
    client.post("/api/broker/upstox/credentials", headers=owner_headers, json={"api_key": "k", "api_secret": "s"})
    client.post("/api/deployments", headers=owner_headers, json={"strategy_id": "ema_rsi_scalper_1m", "symbol": "RELIANCE"})

    detail = client.get(f"/api/admin/tenants/{owner['tenant_id']}", headers=admin_headers).json()
    assert detail["usage"]["active_deployments"] == 1 and detail["limits"]["active_deployments"] == 2
    assert [u["email"] for u in detail["users"]] == ["admin-detail-target@example.com"]
    assert detail["brokers"] == [{"broker_name": "upstox", "token_status": "UNKNOWN", "token_expires_at": None}]
    assert detail["deployments"][0]["symbol"] == "RELIANCE" and detail["tenant_kill_switch_engaged"] is False

    platform_deps = client.get("/api/admin/deployments?status_filter=active", headers=admin_headers).json()
    assert any(d["tenant_id"] == owner["tenant_id"] and d["tenant_name"] == "admin-detail-target@example.com" for d in platform_deps)
