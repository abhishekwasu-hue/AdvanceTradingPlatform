"""MO3 addition (a): the admin console.

- Every admin endpoint (every route under `/api/admin`, plus every route tests/tenancy_route_table.py classifies
  "admin") refuses a tenant token of every role - OWNER, USER, STRATEGY_CREATOR and VIEWER - with 403. The list comes
  from the app itself, so a new admin route is covered the day it merges.
- A SUPER_ADMIN whose session has not passed two-factor is refused by every route under `/api/admin` (MfaRequired).
- Each new section answers with its data (System, Users & business, AI cost, Support, Diagnostics), masks e-mail
  addresses and IP addresses, and never returns a secret, a prompt or an AI answer.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.admin.console import mask_email, mask_ip
from app.auth.security import hash_password
from app.db.models import (
    BrokerCredentialRecord,
    LlmCallRecord,
    LoginEventRecord,
    StrategyDeploymentRecord,
    User,
    WorkerHeartbeatRecord,
)
from app.main import app
from app.observability import recent_errors
from app.observability.middleware import ObservabilityMiddleware
from tests.route_inventory import Route, iter_api_routes
from tests.tenancy_route_table import ROUTE_TABLE
from tests.test_admin_api import _admin
from tests.test_auth_api import _register, _session_factory, client
from tests.test_z10c_tenancy_sweep import _request

ROUTES: List[Route] = iter_api_routes(app)
ADMIN_ROUTES = [r for r in ROUTES if r.path.startswith("/api/admin") or ROUTE_TABLE.get(r.key, "").split(":")[0] == "admin"]
UNDER_ADMIN = [r for r in ROUTES if r.path.startswith("/api/admin")]
CONSOLE = ["/api/admin/console/system", "/api/admin/console/users", "/api/admin/console/ai-cost",
           "/api/admin/console/diagnostics"]
TENANT_ROLES = ("OWNER", "USER", "STRATEGY_CREATOR", "VIEWER")


def _run(coro):
    return asyncio.run(coro)


def _id(route: Route) -> str:
    return f"{route.method} {route.path}"


def _login(email: str) -> Dict[str, str]:
    response = client.post("/api/auth/login", json={"email": email, "password": "S3cur3Pass!"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _member(email: str, tenant_id: int, role: str) -> Dict[str, str]:
    async def create():
        async with _session_factory() as session:
            session.add(User(tenant_id=tenant_id, email=email, hashed_password=hash_password("S3cur3Pass!"), role=role))
            await session.commit()
    _run(create())
    return _login(email)


@pytest.fixture(scope="module")
def tokens():
    owner = {"Authorization": f"Bearer {_register('mo3-console-owner@example.com')}"}
    tenant_id = client.get("/api/auth/me", headers=owner).json()["tenant_id"]
    found = {"OWNER": owner}
    for role in TENANT_ROLES[1:]:
        found[role] = _member(f"mo3-console-{role.lower()}@example.com", tenant_id, role)
    # A SUPER_ADMIN whose session never passed two-factor (MFA not enrolled).
    no_mfa = {"Authorization": f"Bearer {_register('mo3-console-admin-no-mfa@example.com')}"}
    me = client.get("/api/auth/me", headers=no_mfa).json()

    async def promote():
        async with _session_factory() as session:
            (await session.get(User, me["id"])).role = "SUPER_ADMIN"
            await session.commit()
    _run(promote())
    found["SUPER_ADMIN_NO_MFA"] = no_mfa
    return {"headers": found, "sweep": TestClient(app, raise_server_exceptions=False)}


def test_the_admin_route_list_is_not_empty_and_holds_the_console():
    keys = {r.key for r in ADMIN_ROUTES}
    assert len(ADMIN_ROUTES) > 60
    for path in CONSOLE + ["/api/admin/console/support/users/{user_id}"]:
        assert ("GET", path) in keys
    # every route under /api/admin is classified "admin" (never public, member or owner)
    assert all(ROUTE_TABLE.get(r.key, "").startswith("admin") for r in UNDER_ADMIN), \
        [r.key for r in UNDER_ADMIN if not ROUTE_TABLE.get(r.key, "").startswith("admin")]


@pytest.mark.parametrize("role", TENANT_ROLES)
@pytest.mark.parametrize("route", ADMIN_ROUTES, ids=_id)
def test_every_tenant_role_is_refused_every_admin_route(tokens, route, role):
    response = _request(tokens["sweep"], route, tokens["headers"][role])
    assert response.status_code == 403, f"{role} got {response.status_code} on {route.method} {route.path}: {response.text[:200]}"
    assert "detail" in response.json() and len(response.json()) == 1     # the refusal only, never data


@pytest.mark.parametrize("route", UNDER_ADMIN, ids=_id)
def test_super_admin_without_two_factor_is_refused_every_admin_route(tokens, route):
    response = _request(tokens["sweep"], route, tokens["headers"]["SUPER_ADMIN_NO_MFA"])
    assert response.status_code == 403, f"{route.method} {route.path}: {response.status_code} {response.text[:200]}"
    assert response.headers.get("X-Step-Up") == "mfa"


def test_console_endpoints_refuse_anonymous_requests():
    for path in CONSOLE + ["/api/admin/console/support/users/1"]:
        assert client.get(path).status_code in (401, 403)


# --- the sections' data -----------------------------------------------------------------------------------------------

def test_masks():
    assert mask_email("abhi.wasu@example.com") == "ab*******@example.com"
    assert mask_email("a@example.com") == "a*@example.com"
    assert mask_ip("203.0.113.7") == "203.0.113.x"
    assert mask_ip("2001:db8:85a3::8a2e:370:7334") == "2001:db8:85a3:x"
    assert mask_ip(None) is None


def test_system_section_reports_health_workers_and_the_data_feed():
    admin, _ = _admin("mo3-system-admin@example.com")

    async def beat():
        async with _session_factory() as session:
            now = datetime.now(timezone.utc)
            for name, seen in (("mo3_fresh_worker", now), ("mo3_quiet_worker", now - timedelta(hours=2))):
                row = await session.scalar(select(WorkerHeartbeatRecord).where(WorkerHeartbeatRecord.worker_name == name))
                if row is None:
                    session.add(WorkerHeartbeatRecord(worker_name=name, last_seen_at=seen, cycle_count=3, last_error=None if name.endswith("fresh_worker") else "boom " * 80))
            await session.commit()
    _run(beat())
    body = client.get("/api/admin/console/system", headers=admin).json()
    assert body["health"]["checks"]["database"]["status"] == "ok"
    assert "live_paths" in body["health"]["checks"]                       # the Phase 0 posture, read-only
    workers = {w["name"]: w for w in body["workers"]}
    assert workers["mo3_fresh_worker"]["state"] == "ok"
    assert workers["mo3_quiet_worker"]["state"] == "stale"
    assert len(workers["mo3_quiet_worker"]["last_error"]) <= 160          # cut short
    feed = body["data_feed"]
    assert set(feed) >= {"market_open", "market_memory", "instrument_master", "data_quality_24h", "broker_circuits"}
    market_feed = body["market_feed"]                                      # M71 PL-4: the universe feed's health
    assert {"state", "connections", "symbols_subscribed", "last_tick_age_seconds", "reconnects_today",
            "gaps_detected_today", "gaps_filled_today", "gaps_pending"} <= set(market_feed)


def _seed_ai_calls(tenant_id: int, user_id: int) -> None:
    async def go():
        async with _session_factory() as session:
            common = dict(tenant_id=tenant_id, user_id=user_id, provider="anthropic", model="test-model-a", system_sha256="0" * 64,
                          user_sha256="0" * 64, system_text="MO3-PRIVATE-PROMPT", user_text="MO3-PRIVATE-QUESTION")
            session.add(LlmCallRecord(feature="copilot", input_tokens=1000, output_tokens=200, cost_usd=0.5, response_text="MO3-ANSWER", **common))
            session.add(LlmCallRecord(feature="copilot", input_tokens=500, output_tokens=100, cost_usd=0.25, billed_to_user_key=True, **common))
            session.add(LlmCallRecord(feature="classification", input_tokens=10, output_tokens=0, cost_usd=0.0, status="error: timeout", **common))
            await session.commit()
    _run(go())


def test_users_section_lists_people_with_masked_emails_plan_and_last_seen():
    admin, _ = _admin("mo3-users-admin@example.com")
    member = {"Authorization": f"Bearer {_register('mo3-users-person@example.com')}"}
    me = client.get("/api/auth/me", headers=member).json()
    body = client.get("/api/admin/console/users?q=mo3-users-person", headers=admin).json()
    assert body["totals"]["users"] >= 2 and body["totals"]["signups_7d"] >= 1 and body["totals"]["active_7d"] >= 1
    [row] = body["users"]
    assert row["id"] == me["id"] and row["email"] == mask_email("mo3-users-person@example.com") == "mo" + "*" * 14 + "@example.com"
    assert row["plan"] and row["signed_up_at"] and row["last_seen_at"]        # registering opened a session
    assert "mo3-users-person@example.com" not in json.dumps(body)


def test_ai_cost_section_sums_per_tenant_and_per_task_without_any_text():
    admin, _ = _admin("mo3-ai-admin@example.com")
    member = {"Authorization": f"Bearer {_register('mo3-ai-person@example.com')}"}
    me = client.get("/api/auth/me", headers=member).json()
    _seed_ai_calls(me["tenant_id"], me["id"])
    body = client.get("/api/admin/console/ai-cost?days=7", headers=admin).json()
    row = next(t for t in body["tenants"] if t["tenant_id"] == me["tenant_id"])
    assert row["calls"] == 3 and row["input_tokens"] == 1510 and row["output_tokens"] == 300
    assert row["cost_usd"] == pytest.approx(0.75) and row["own_key_cost_usd"] == pytest.approx(0.25) and row["failed"] == 1
    assert row["cost_inr"] > 0
    task = next(t for t in body["tasks"] if t["task"] == "copilot" and t["model"] == "test-model-a")
    assert task["tier"] == "fast" and task["provider"] == "anthropic" and task["calls"] == 2
    cheap = next(t for t in body["tasks"] if t["task"] == "classification" and t["model"] == "test-model-a")
    assert cheap["tier"] == "cheap"
    text = json.dumps(body)
    assert "MO3-PRIVATE" not in text and "MO3-ANSWER" not in text


def test_support_section_shows_one_persons_status_and_never_a_secret():
    admin, _ = _admin("mo3-support-admin@example.com")
    member = {"Authorization": f"Bearer {_register('mo3-support-person@example.com')}"}
    me = client.get("/api/auth/me", headers=member).json()

    async def seed():
        async with _session_factory() as session:
            session.add(BrokerCredentialRecord(tenant_id=me["tenant_id"], user_id=me["id"], broker_name="upstox",
                                               encrypted_payload="MO3-CIPHERTEXT", token_status="EXPIRED"))
            session.add(LoginEventRecord(user_id=me["id"], tenant_id=me["tenant_id"], email="mo3-support-person@example.com", success=False,
                                         reason="bad_password", ip_address="198.51.100.23",
                                         user_agent="Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36"))
            session.add(StrategyDeploymentRecord(tenant_id=me["tenant_id"], strategy_id="orb_15m_5m", symbol="NIFTY 50", mode="PAPER",
                                                 status="ERROR", last_error="broker said no " * 30, consecutive_failures=4))
            await session.commit()
    _run(seed())
    response = client.get(f"/api/admin/console/support/users/{me['id']}", headers=admin)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["email"] == mask_email("mo3-support-person@example.com") == "mo" + "*" * 16 + "@example.com" and body["tenant"]["id"] == me["tenant_id"]
    assert body["brokers"] == [{"broker": "upstox", "account": "primary", "status": "EXPIRED", "expires_at": None, "last_verified_at": None}]
    failed = next(s for s in body["sign_ins"] if not s["success"])
    assert failed["network"] == "198.51.100.x" and failed["device"] == "Chrome on Android" and failed["reason"] == "bad_password"
    [bot] = body["bots_with_errors"]
    assert bot["failures_in_a_row"] == 4 and len(bot["error"]) <= 160
    text = json.dumps(body)
    for leak in ("MO3-CIPHERTEXT", "198.51.100.23", "Mozilla", "mo3-support-person@example.com", "encrypted_payload"):
        assert leak not in text
    assert client.get("/api/admin/console/support/users/999999", headers=admin).status_code == 404


def test_diagnostics_drawer_lists_recent_errors_with_request_ids():
    admin, _ = _admin("mo3-diag-admin@example.com")
    member = {"Authorization": f"Bearer {_register('mo3-diag-person@example.com')}"}
    me = client.get("/api/auth/me", headers=member).json()
    _seed_ai_calls(me["tenant_id"], me["id"])
    recent_errors.clear()
    recent_errors.record("req-mo3-1", "GET", "/api/positions", 500, 12.3)
    body = client.get("/api/admin/console/diagnostics", headers=admin).json()
    assert body["server_errors"][0]["request_id"] == "req-mo3-1" and body["server_errors"][0]["route"] == "/api/positions"
    assert any(f["tenant_id"] == me["tenant_id"] and f["status"] == "error: timeout" for f in body["ai_failures"])
    assert set(body) >= {"worker_errors", "bots_with_errors", "alert_deliveries_failed", "data_quality", "failed_sign_ins_24h"}
    assert "MO3-PRIVATE" not in json.dumps(body)


def test_middleware_keeps_the_last_server_errors_by_request_id_and_route_template():
    recent_errors.clear()
    inner = FastAPI()

    @inner.get("/api/things/{thing_id}")
    def boom(thing_id: int):
        raise RuntimeError("boom")

    @inner.get("/api/fine")
    def fine():
        return {"ok": True}

    wrapped = TestClient(ObservabilityMiddleware(inner), raise_server_exceptions=False)
    assert wrapped.get("/api/fine").status_code == 200
    response = wrapped.get("/api/things/7?secret=abc", headers={"X-Request-ID": "req-mo3-boom"})
    assert response.status_code == 500
    [entry] = recent_errors.recent()
    assert entry["request_id"] == "req-mo3-boom" and entry["route"] == "/api/things/{thing_id}" and entry["status"] == 500
    assert "secret" not in json.dumps(entry)
    for n in range(recent_errors.MAX_KEPT + 5):
        recent_errors.record(f"r{n}", "GET", "/api/x", 502, 1.0)
    assert len(recent_errors.recent(1000)) == recent_errors.MAX_KEPT and recent_errors.recent(1)[0]["request_id"] == f"r{recent_errors.MAX_KEPT + 4}"
    recent_errors.clear()
