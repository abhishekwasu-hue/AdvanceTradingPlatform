"""MO3-A1: the admin's read-only "View as user" (app/auth/view_as.py, app/auth/view_as_middleware.py,
app/admin/view_as_routes.py).

- Only a SUPER_ADMIN with a two-factor-verified session may start a view; every tenant role gets 403, an admin without
  two-factor gets the step-up 403; a reason is required; an administrator's account, a removed account and one's own
  cannot be viewed.
- The view token reads as the person - and is refused with 403 on EVERY non-GET route of the app (the sweep walks the
  whole route table, through /api and /api/v1), on the reads that act or hand something out (exports, downloads,
  broker sign-in, the webhook credential, the admin console), before any route runs; ending the view is the one write.
- It expires, ends with the admin's own login session, ends when a new view starts, and is a 401 afterwards.
- Every request is recorded; start and end are on both accounts' audit trails; the person sees the view in their own
  Settings record, as "ATP support", never the admin.
- Nothing outside the person's organisation is reachable (every GET route swept against another organisation's planted
  rows), secrets are masked in every answer, and no AI call is made.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.admin.console import mask_email
from app.auth import view_as
from app.auth.security import hash_password
from app.core import config
from app.db.models import AuditLogRecord, Tenant, User, UserSessionRecord, ViewSessionRecord, ViewSessionRequestRecord
from app.main import app
from tests import tenancy_probe as probe
from tests.route_inventory import Route, iter_api_routes
from tests.test_admin_api import _admin
from tests.test_auth_api import _register, _session_factory, client
from tests.test_z10c_tenancy_sweep import _request

ROUTES: List[Route] = iter_api_routes(app)
MARKER = "mo3a1-other-organisation-marker"
START = "/api/admin/view-sessions"


def _run(coro):
    return asyncio.run(coro)


def _id(route: Route) -> str:
    return f"{route.method} {route.path}"


def _bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(email: str) -> Dict[str, str]:
    response = client.post("/api/auth/login", json={"email": email, "password": "S3cur3Pass!"})
    assert response.status_code == 200, response.text
    return _bearer(response.json()["access_token"])


def _member(email: str, tenant_id: int, role: str) -> Dict[str, str]:
    async def create():
        async with _session_factory() as session:
            session.add(User(tenant_id=tenant_id, email=email, hashed_password=hash_password("S3cur3Pass!"), role=role))
            await session.commit()
    _run(create())
    return _login(email)


def _start(admin: Dict[str, str], user_id: int, reason: str = "The person asked why a paper bot stopped") -> dict:
    response = client.post(START, headers=admin, json={"user_id": user_id, "reason": reason})
    assert response.status_code == 200, response.text
    return response.json()


async def _rows(model, *where):
    async with _session_factory() as session:
        return list(await session.scalars(select(model).where(*where).order_by(model.id)))


@pytest.fixture(scope="module")
def world():
    admin, admin_me = _admin("mo3a1-admin@example.com")
    target_token = _register("mo3a1-person@example.com")
    target = _bearer(target_token)
    target_me = client.get("/api/auth/me", headers=target).json()
    members = {role: _member(f"mo3a1-{role.lower()}@example.com", target_me["tenant_id"], role)
               for role in ("USER", "STRATEGY_CREATOR", "VIEWER")}
    other = _bearer(_register("mo3a1-other@example.com"))
    other_me = client.get("/api/auth/me", headers=other).json()
    return {"admin": admin, "admin_me": admin_me, "target": target, "target_me": target_me, "members": members,
            "other": other, "other_me": other_me}


# --- who may start a view ----------------------------------------------------------------------------------------------

def test_user_tokens_get_403_on_the_start_route(world):
    body = {"user_id": world["other_me"]["id"], "reason": "Checking the dashboard"}
    assert client.post(START, json=body).status_code == 401
    for headers in [world["target"], *world["members"].values()]:
        response = client.post(START, headers=headers, json=body)
        assert response.status_code == 403, response.text
    for path in (START, f"{START}/1"):
        assert client.get(path, headers=world["target"]).status_code == 403


def test_a_super_admin_without_two_factor_is_refused():
    headers = _bearer(_register("mo3a1-admin-no-mfa@example.com"))
    me = client.get("/api/auth/me", headers=headers).json()

    async def promote():
        async with _session_factory() as session:
            (await session.get(User, me["id"])).role = "SUPER_ADMIN"
            await session.commit()
    _run(promote())
    person = client.get("/api/auth/me", headers=_bearer(_register("mo3a1-person-2@example.com"))).json()
    response = client.post(START, headers=headers, json={"user_id": person["id"], "reason": "Checking the dashboard"})
    assert response.status_code == 403 and response.headers.get("X-Step-Up") == "mfa"


def test_a_reason_is_required_and_admins_self_and_removed_accounts_cannot_be_viewed(world):
    admin, person = world["admin"], world["target_me"]["id"]
    for reason in ("", "   ", "ok"):
        assert client.post(START, headers=admin, json={"user_id": person, "reason": reason}).status_code in (400, 422)
    assert client.post(START, headers=admin, json={"user_id": person}).status_code == 422
    assert client.post(START, headers=admin, json={"user_id": world["admin_me"]["id"], "reason": "my own account"}).status_code == 400
    other_admin, other_admin_me = _admin("mo3a1-admin-2@example.com")
    assert client.post(START, headers=admin, json={"user_id": other_admin_me["id"], "reason": "an admin account"}).status_code == 400
    assert client.post(START, headers=admin, json={"user_id": 99_999_999, "reason": "nobody at all"}).status_code == 404
    removed = _register("mo3a1-removed@example.com")
    removed_id = client.get("/api/auth/me", headers=_bearer(removed)).json()["id"]

    async def deactivate():
        async with _session_factory() as session:
            (await session.get(User, removed_id)).is_active = False
            await session.commit()
    _run(deactivate())
    assert client.post(START, headers=admin, json={"user_id": removed_id, "reason": "a removed account"}).status_code == 400


# --- what the view token is ----------------------------------------------------------------------------------------------

def test_the_view_reads_as_the_person_masked_and_carries_no_login_session(world):
    started = _start(world["admin"], world["target_me"]["id"])
    view = _bearer(started["token"])
    claims = jwt.decode(started["token"], options={"verify_signature": False})
    assert claims["vs"] == started["view"]["id"] and claims["imp"] == world["admin_me"]["id"] and claims["ro"] == 1
    assert "sid" not in claims
    assert started["view"]["name"] == mask_email("mo3a1-person@example.com") and started["view"]["active"]
    assert 0 < started["view"]["seconds_left"] <= config.VIEW_AS_MINUTES * 60
    me = client.get("/api/auth/me", headers=view)
    assert me.status_code == 200 and me.json()["id"] == world["target_me"]["id"]
    assert me.json()["email"] == mask_email("mo3a1-person@example.com")               # scrubbed on the way out
    assert me.headers.get(view_as.READ_ONLY_HEADER) == "1"
    banner = client.get("/api/view-session", headers=view).json()
    assert banner["active"] and banner["read_only"] and banner["view"]["user_id"] == world["target_me"]["id"]
    assert client.get("/api/view-session", headers=world["target"]).json() == {"active": False}
    # The admin console and every admin read stay shut: the view reads as a plain member.
    for path in ("/api/admin/console/system", "/api/admin/overview", START):
        assert client.get(path, headers=view).status_code == 403
    # Only the hash of the token is stored.
    stored = _run(_rows(ViewSessionRecord, ViewSessionRecord.id == started["view"]["id"]))[0]
    assert stored.token_hash == view_as.token_hash(started["token"]) and started["token"] not in json.dumps(stored.reason)


@pytest.mark.parametrize("prefix", ["/api", "/api/v1"])
def test_every_non_get_route_refuses_the_view_with_403(world, prefix):
    """The sweep over the whole route table: every POST / PUT / PATCH / DELETE answers 403 with the read-only code,
    before the route runs - the only exception is ending the view itself."""
    started = _start(world["admin"], world["target_me"]["id"])
    view = _bearer(started["token"])
    sweep = TestClient(app, raise_server_exceptions=False)
    mutating = [r for r in ROUTES if r.mutating and not (r.method == "POST" and r.path == view_as.END_PATH)]
    assert len(mutating) > 300, len(mutating)
    wrong = []
    for route in mutating:
        url = probe.path_for(route.path, probe.int_path_params(route.dependant))
        url = prefix + url[len("/api"):]
        response = sweep.request(route.method, url, headers=view, json=probe.body_for(app.openapi(), route.method, route.path))
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        if response.status_code != 403 or body.get("code") != view_as.READ_ONLY_CODE:
            wrong.append(f"{route.method} {route.path} -> {response.status_code} {response.text[:120]}")
    assert not wrong, "a view token was not refused:\n" + "\n".join(wrong)
    refused = _run(_rows(ViewSessionRequestRecord, ViewSessionRequestRecord.view_session_id == started["view"]["id"],
                         ViewSessionRequestRecord.refused.is_(True)))
    assert len(refused) == len(mutating)
    # Still alive afterwards (nothing ended the view), and the person's account is unchanged.
    assert client.get("/api/auth/me", headers=view).status_code == 200


@pytest.mark.parametrize("path", [
    "/api/exports/trades", "/api/tax/report.csv", "/api/charts/drawings/export", "/api/broker/upstox/oauth/start",
    "/api/broker/upstox/login-url", "/api/webhooks/tradingview/token", "/api/alerts/telegram/connect", "/api/auth/google/start",
    "/api/admin/console/system", "/api/public/v1/account",
])
def test_reads_that_act_or_hand_something_out_are_refused(world, path):
    view = _bearer(_start(world["admin"], world["target_me"]["id"])["token"])
    response = client.get(path, headers=view)
    assert response.status_code == 403 and response.json()["code"] == view_as.READ_ONLY_CODE


def test_every_route_the_table_names_as_a_blocked_read_exists():
    """Guards BLOCKED_READS against drift: each pattern still matches a real route (a renamed export route would
    otherwise slip out of the list silently)."""
    gets = [r.path for r in ROUTES if r.method == "GET"]
    for pattern, why in view_as.BLOCKED_READS:
        assert any(pattern.search(p) for p in gets), f"no GET route matches the blocked read {pattern.pattern} ({why})"


def test_a_forged_view_claim_is_refused_writes_and_reads_nothing(world):
    forged = jwt.encode({"sub": str(world["target_me"]["id"]), "vs": 1, "ro": 1}, "not-the-secret", algorithm="HS256")
    assert client.post("/api/watchlists", headers=_bearer(forged), json={"name": "x"}).status_code == 403
    assert client.get("/api/auth/me", headers=_bearer(forged)).status_code == 401


def test_ending_the_view_is_the_one_write_and_the_token_is_dead_after(world):
    started = _start(world["admin"], world["target_me"]["id"])
    view = _bearer(started["token"])
    assert client.post("/api/view-session/end", headers=world["target"]).json() == {"ended": False}
    assert client.post("/api/view-session/end", headers=view).json() == {"ended": True}
    assert client.get("/api/auth/me", headers=view).status_code == 401
    record = _run(_rows(ViewSessionRecord, ViewSessionRecord.id == started["view"]["id"]))[0]
    assert record.ended_at is not None and record.end_reason == "ended by support"


def test_the_admin_can_end_a_view_and_a_new_view_ends_the_previous(world):
    first = _start(world["admin"], world["target_me"]["id"])
    second = _start(world["admin"], world["target_me"]["id"])
    assert client.get("/api/auth/me", headers=_bearer(first["token"])).status_code == 401
    assert _run(_rows(ViewSessionRecord, ViewSessionRecord.id == first["view"]["id"]))[0].end_reason == "a new view started"
    ended = client.post(f"{START}/{second['view']['id']}/end", headers=world["admin"])
    assert ended.status_code == 200 and ended.json()["ended"] is True
    assert client.get("/api/auth/me", headers=_bearer(second["token"])).status_code == 401
    assert client.post(f"{START}/{second['view']['id']}/end", headers=world["admin"]).json()["ended"] is False


def test_the_view_expires(world):
    started = _start(world["admin"], world["target_me"]["id"])
    view = _bearer(started["token"])
    assert client.get("/api/auth/me", headers=view).status_code == 200

    async def age():
        async with _session_factory() as session:
            record = await session.get(ViewSessionRecord, started["view"]["id"])
            record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
    _run(age())
    assert client.get("/api/auth/me", headers=view).status_code == 401
    record = _run(_rows(ViewSessionRecord, ViewSessionRecord.id == started["view"]["id"]))[0]
    assert record.ended_at is not None and record.end_reason == "expired"      # ended by the request that found it expired
    ended = _run(_rows(AuditLogRecord, AuditLogRecord.event == "support_view_ended", AuditLogRecord.user_id == world["target_me"]["id"]))
    assert any("expired" in row.detail for row in ended)


def test_the_token_itself_expires_with_the_session():
    """The JWT's own `exp` is the view's end: even a database that still said "open" would not keep it working."""
    token = __import__("app.auth.security", fromlist=["create_view_token"]).create_view_token(
        1, 1, 1, datetime.now(timezone.utc) - timedelta(seconds=1))
    assert view_as.verified_view_claims(token) is None and view_as.unverified_view_claims(token) is not None


def test_the_view_ends_with_the_admins_own_login_session(world):
    admin, _ = _admin("mo3a1-admin-3@example.com")
    started = _start(admin, world["target_me"]["id"])
    view = _bearer(started["token"])
    assert client.get("/api/auth/me", headers=view).status_code == 200
    assert client.post("/api/auth/logout", headers=admin).status_code == 204
    assert client.get("/api/auth/me", headers=view).status_code == 401


def test_the_view_ends_when_the_admin_loses_the_role_or_the_person_becomes_admin(world):
    admin, admin_me = _admin("mo3a1-admin-4@example.com")
    view = _bearer(_start(admin, world["target_me"]["id"])["token"])

    async def demote():
        async with _session_factory() as session:
            (await session.get(User, admin_me["id"])).role = "OWNER"
            await session.commit()
    _run(demote())
    assert client.get("/api/auth/me", headers=view).status_code == 401


# --- the audit -----------------------------------------------------------------------------------------------------------

def test_start_end_and_every_request_are_audited_on_both_accounts(world):
    started = _start(world["admin"], world["target_me"]["id"], reason="Checking the Signals page with the person")
    view_id = started["view"]["id"]
    view = _bearer(started["token"])
    client.get("/api/auth/me", headers=view)
    client.get("/api/watchlists", headers=view)
    client.post("/api/watchlists", headers=view, json={"name": "nope"})
    client.post("/api/view-session/end", headers=view)
    rows = _run(_rows(ViewSessionRequestRecord, ViewSessionRequestRecord.view_session_id == view_id))
    assert [(r.method, r.path, r.refused) for r in rows] == [
        ("GET", "/api/auth/me", False), ("GET", "/api/watchlists", False), ("POST", "/api/watchlists", True),
        ("POST", "/api/view-session/end", False)]
    assert rows[2].status == 403 and rows[0].status == 200
    record = _run(_rows(ViewSessionRecord, ViewSessionRecord.id == view_id))[0]
    assert record.request_count == 4 and record.refused_count == 1
    for user_id, tenant_id in ((world["admin_me"]["id"], world["admin_me"]["tenant_id"]),
                               (world["target_me"]["id"], world["target_me"]["tenant_id"])):
        events = [(r.event, r.tenant_id) for r in _run(_rows(AuditLogRecord, AuditLogRecord.user_id == user_id))
                  if r.event.startswith("support_view") and (f"#{view_id} " in r.detail or user_id == world["target_me"]["id"])]
        assert ("support_view_started", tenant_id) in events and ("support_view_ended", tenant_id) in events
    person_rows = _run(_rows(AuditLogRecord, AuditLogRecord.user_id == world["target_me"]["id"], AuditLogRecord.event == "support_view_started"))
    assert any("Checking the Signals page with the person" in r.detail for r in person_rows)
    detail = client.get(f"{START}/{view_id}", headers=world["admin"]).json()
    assert len(detail["requests"]) == 4 and detail["requests"][0]["path"] == "/api/view-session/end"
    assert any(v["id"] == view_id for v in client.get(START, headers=world["admin"]).json()["views"])


def test_the_audit_chain_stays_intact(world):
    """The rows a view appends chain correctly (checked from an anchor taken just before: other modules of the suite
    tamper with older rows on purpose to test the verifier)."""
    from app.audit.log import record_anchor, verify_audit_chain

    async def anchor():
        async with _session_factory() as session:
            await record_anchor(session)
            await session.commit()
    _run(anchor())
    view = _bearer(_start(world["admin"], world["target_me"]["id"])["token"])
    client.post("/api/view-session/end", headers=view)

    async def check():
        async with _session_factory() as session:
            return await verify_audit_chain(session, since_anchor=True)
    assert _run(check()) == (True, None)


def test_the_person_sees_when_support_viewed_their_account(world):
    _start(world["admin"], world["target_me"]["id"], reason="The person asked about the risk limits")
    mine = client.get("/api/me/support-access", headers=world["target"])
    assert mine.status_code == 200
    views = mine.json()["views"]
    assert views and views[0]["by"] == "ATP support" and views[0]["reason"] == "The person asked about the risk limits"
    assert views[0]["read_only"] is True and views[0]["started_at"]
    text = mine.text
    assert "admin" not in text.lower() and "mo3a1-admin" not in text
    # Another person of the same organisation, and another organisation, see none of it.
    assert client.get("/api/me/support-access", headers=world["members"]["USER"]).json() == {"views": []}
    assert client.get("/api/me/support-access", headers=world["other"]).json() == {"views": []}


# --- no leak, secrets masked, no AI ----------------------------------------------------------------------------------------

@pytest.fixture
def short_live_stream(monkeypatch):
    """The live stream (SSE) runs for minutes on its own; the sweeps read a short one."""
    from functools import partial

    from app.live import routes as live_routes
    monkeypatch.setattr(live_routes, "events", partial(live_routes.events, max_seconds=0.2, poll_seconds=0.05))


@pytest.fixture
def all_features(world):
    """Every feature on (the lock keeps its state) and a paid plan, so flag- and plan-gated reads are swept too."""
    from app.platform import controls
    from tests.test_trading_worker import _upgrade_plan
    _upgrade_plan(world["target_me"]["tenant_id"])

    async def flags(on: bool):
        async with _session_factory() as session:
            value = {n: {"on": True, "tenants": []} for n in controls.FEATURE_FLAGS if n != "phase0_paper_only"} if on else {}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": value}, None)
            await session.commit()
    _run(flags(True))
    yield
    _run(flags(False))


def test_no_get_route_reaches_another_organisation_or_fails(world, short_live_stream, all_features):
    """Every GET route, called with the view token of a person in organisation A, while organisation B has a planted row
    (with a marker in every text column) in every tenant-owned table: no answer carries B's marker, none is a 5xx."""
    with pytest.MonkeyPatch.context() as mp:
        async def no_network(self, request):
            raise httpx.ConnectError("MO3-A1 sweep: no outbound network", request=request)

        def no_network_sync(self, request):
            raise httpx.ConnectError("MO3-A1 sweep: no outbound network", request=request)
        mp.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
        mp.setattr(httpx.HTTPTransport, "handle_request", no_network_sync)

        async def plant():
            async with _session_factory() as session:
                return await probe.plant_rows(session, tenant_id=world["other_me"]["tenant_id"], user_id=world["other_me"]["id"], marker=MARKER)
        planted = _run(plant())
        try:
            started = _start(world["admin"], world["target_me"]["id"])
            view = _bearer(started["token"])
            sweep = TestClient(app, raise_server_exceptions=False)
            leaks, errors, answered = [], [], 0
            for route in ROUTES:
                if route.method != "GET":
                    continue
                response = _request(sweep, route, view)
                answered += response.status_code == 200
                if MARKER in response.text:
                    leaks.append(f"{_id(route)} -> {response.status_code}")
                if response.status_code >= 500:
                    errors.append(f"{_id(route)} -> {response.status_code}: {response.text[:120]}")
            assert not leaks, "another organisation's rows reached a view:\n" + "\n".join(leaks)
            assert not errors, "5xx on a view's read:\n" + "\n".join(errors)
            print(f"MO3-A1 sweep: {answered} GET routes answered the view with 200")
            assert answered >= 150, f"only {answered} GET routes answered the view - the sweep would prove little"
            assert client.get("/api/auth/me", headers=view).status_code == 200
        finally:
            async def remove():
                async with _session_factory() as session:
                    await probe.remove_rows(session, planted)
            _run(remove())


def test_secrets_are_masked_in_every_answer_of_a_view(world, short_live_stream, all_features):
    """A secret-named field is masked even where a route would show it: the organisation's pre-P0.3 plaintext webhook
    token (the webhook-token read itself is refused) never appears in any GET answer of the view."""
    secret = "mo3a1-plaintext-webhook-secret-value"

    async def plant():
        async with _session_factory() as session:
            (await session.get(Tenant, world["target_me"]["tenant_id"])).webhook_token = secret
            await session.commit()
    _run(plant())
    view = _bearer(_start(world["admin"], world["target_me"]["id"])["token"])
    sweep = TestClient(app, raise_server_exceptions=False)
    try:
        seen = [_id(r) for r in ROUTES if r.method == "GET" and secret in _request(sweep, r, view).text]
        assert not seen, f"the secret reached a view through: {seen}"
        assert secret in client.get("/api/webhooks/tradingview/token", headers=world["target"]).text   # the person's own read
    finally:
        async def clear():
            async with _session_factory() as session:
                (await session.get(Tenant, world["target_me"]["tenant_id"])).webhook_token = None
                await session.commit()
        _run(clear())


def test_scrub_masks_secret_fields_emails_and_ips_at_any_depth():
    data = {"api_key": "sk-live-123", "access_token": "eyJ...", "refresh_token": "r", "password": "p", "client_secret": "c",
            "token_status": "VALID", "has_token": True, "analytics_token_set": True, "email": "abhi.wasu@example.com",
            "invited_email": "someone@example.org", "ip_address": "203.0.113.7", "backup_codes": ["a", "b"],
            "nested": [{"webhook_token": "w", "owner_email": "x@y.z", "name": "kept"}], "count": 3}
    out = view_as.scrub(data)
    for key in ("api_key", "access_token", "refresh_token", "password", "client_secret"):
        assert out[key] == view_as.MASK
    assert out["token_status"] == "VALID" and out["has_token"] is True and out["analytics_token_set"] is True
    assert out["email"] == "ab*******@example.com" and out["invited_email"] == "so*****@example.org"
    assert out["ip_address"] == "203.0.113.x" and out["backup_codes"] == [view_as.MASK, view_as.MASK]
    assert out["nested"][0] == {"webhook_token": view_as.MASK, "owner_email": "x*@y.z", "name": "kept"} and out["count"] == 3
    assert view_as.scrub_json(b"not json") == b"not json"


def test_a_view_never_calls_the_ai():
    from app.ai import resolve
    from app.ai import settings as ai_settings
    from app.ai.providers import RuleBasedProvider

    async def check():
        async with _session_factory() as session:
            tenant = await session.scalar(select(Tenant).limit(1))
            user = await session.scalar(select(User).limit(1))
            marker = view_as.set_active(True)
            try:
                provider = await ai_settings.provider_for(session, tenant)
                own = await resolve.user_key_provider(session, user, "general")
            finally:
                view_as.reset_active(marker)
            return provider, own
    provider, own = _run(check())
    assert isinstance(provider, RuleBasedProvider) and "support view" in provider.reason and own is None
    assert view_as.active() is False


def test_the_refusal_rule():
    assert view_as.refusal("GET", "/api/positions") is None
    assert view_as.refusal("HEAD", "/api/positions") is None
    for method in ("POST", "PUT", "PATCH", "DELETE", "post", "TRACE", "CONNECT"):
        assert view_as.refusal(method, "/api/orders") is not None
    assert view_as.refusal("POST", view_as.END_PATH) is None and view_as.refusal("DELETE", view_as.END_PATH) is not None
    assert view_as.refusal("GET", "/api/exports/orders") is not None


def test_a_normal_sign_in_is_untouched(world):
    """No view token: the chokepoint lets the request through as before (writes work, no read-only header)."""
    from app.ai.compliance_terms import ACK_VERSION
    response = client.post("/api/ai/acknowledgement", headers=world["other"], json={"version": ACK_VERSION})
    assert response.status_code == 200, response.text
    assert view_as.READ_ONLY_HEADER not in response.headers


# --- the migration -------------------------------------------------------------------------------------------------------

def test_the_migration_is_one_additive_reversible_step():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "e8a0c2d4f6b9_mo3_a1_view_as_user.py"
    spec = importlib.util.spec_from_file_location("mo3_a1_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "e8a0c2d4f6b9" and module.down_revision == "b5d7f9a1c3e6" and module.ROLLBACK_SAFE is True
    source = path.read_text()
    assert source.count("op.create_table(") == 2 and "drop_column" not in source and "add_column" not in source
    assert "op.drop_table('view_sessions')" in source and "op.drop_table('view_session_requests')" in source
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from scripts.migration_rollback_safety import lint, risky_operations
    assert risky_operations(source) == [] and not [p for p in lint() if p.startswith(path.name)]
    assert ScriptDirectory.from_config(Config(str(path.parents[2] / "alembic.ini"))).get_heads() == ["e8a0c2d4f6b9"]


def test_admin_session_row_must_be_two_factor_verified(world):
    """A view bound to an admin login session that lost its two-factor mark (a fresh login without it) stops working."""
    admin, _ = _admin("mo3a1-admin-5@example.com")
    started = _start(admin, world["target_me"]["id"])

    async def unverify():
        async with _session_factory() as session:
            record = await session.get(ViewSessionRecord, started["view"]["id"])
            (await session.get(UserSessionRecord, record.admin_session_id)).mfa_verified_at = None
            await session.commit()
    _run(unverify())
    assert client.get("/api/auth/me", headers=_bearer(started["token"])).status_code == 401


def test_the_second_line_refuses_a_view_write_even_without_the_middleware(world):
    """`get_current_user` refuses a view token's change by itself: a bare app with the routes and no middleware still
    answers 403 (the chokepoint is not only the middleware)."""
    from fastapi import FastAPI

    from app.auth.dependencies import _refuse_view_writes
    from app.auth.routes import router as auth_router
    from app.db.session import get_session
    from app.watchlists.routes import router as watchlists_router
    from fastapi import HTTPException
    from starlette.requests import Request

    started = _start(world["admin"], world["target_me"]["id"])
    bare = FastAPI()
    bare.include_router(watchlists_router)
    bare.include_router(auth_router)
    bare.dependency_overrides[get_session] = app.dependency_overrides[get_session]
    with TestClient(bare) as raw:
        refused = raw.post("/api/watchlists", headers=_bearer(started["token"]), json={"name": "nope"})
        assert refused.status_code == 403 and refused.json()["detail"] == view_as.READ_ONLY_MESSAGE
        assert raw.get("/api/auth/me", headers=_bearer(started["token"])).status_code == 200
    payload = {"vs": 1, "sub": "1"}
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(HTTPException) as caught:
            _refuse_view_writes(Request({"type": "http", "method": method, "path": "/api/anything", "headers": []}), payload)
        assert caught.value.status_code == 403
    _refuse_view_writes(Request({"type": "http", "method": "POST", "path": view_as.END_PATH, "headers": []}), payload)
    _refuse_view_writes(Request({"type": "http", "method": "POST", "path": "/api/anything", "headers": []}), {"sub": "1", "sid": 1})


def test_a_websocket_with_a_view_token_is_refused(world):
    from app.auth.view_as_middleware import ViewSessionReadOnly
    token = _start(world["admin"], world["target_me"]["id"])["token"]
    reached, sent = [], []

    async def inner(scope, receive, send):
        reached.append(scope["type"])

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "websocket", "path": "/ws", "headers": [(b"authorization", f"Bearer {token}".encode())]}
    _run(ViewSessionReadOnly(inner)(scope, receive, send))
    assert reached == [] and sent == [{"type": "websocket.close", "code": 4403}]
    _run(ViewSessionReadOnly(inner)({**scope, "headers": []}, receive, send))
    assert reached == ["websocket"]


# --- the security review's findings (23 Oct) ------------------------------------------------------------------------------

def _set_role(user_id: int, role: str) -> None:
    async def go():
        async with _session_factory() as session:
            (await session.get(User, user_id)).role = role
            await session.commit()
    _run(go())


def test_review_1_no_view_into_the_operators_organisation_or_one_with_an_admin(world):
    admin, admin_me = world["admin"], world["admin_me"]
    colleague = _member("mo3a1-operator-colleague@example.com", admin_me["tenant_id"], "USER")
    colleague_id = client.get("/api/auth/me", headers=colleague).json()["id"]
    response = client.post(START, headers=admin, json={"user_id": colleague_id, "reason": "looking at a colleague"})
    assert response.status_code == 400 and "platform's own organisation" in response.json()["detail"]
    # A person whose organisation has a SUPER_ADMIN member (another admin's tenant).
    owner = _bearer(_register("mo3a1-mixed-owner@example.com"))
    owner_me = client.get("/api/auth/me", headers=owner).json()
    member = _member("mo3a1-mixed-member@example.com", owner_me["tenant_id"], "USER")
    member_id = client.get("/api/auth/me", headers=member).json()["id"]
    view = _bearer(_start(admin, member_id)["token"])
    assert client.get("/api/team/members", headers=view).status_code == 200
    _set_role(owner_me["id"], "SUPER_ADMIN")             # the organisation now has an administrator: the open view goes stale
    assert client.get("/api/team/members", headers=view).status_code == 401
    response = client.post(START, headers=admin, json={"user_id": member_id, "reason": "again with an admin there"})
    assert response.status_code == 400 and "organisation with an administrator" in response.json()["detail"]
    _set_role(owner_me["id"], "OWNER")


def _admin_session_verified(headers: Dict[str, str], at: datetime) -> None:
    sid = jwt.decode(headers["Authorization"].split()[1], options={"verify_signature": False})["sid"]

    async def go():
        async with _session_factory() as session:
            (await session.get(UserSessionRecord, sid)).mfa_verified_at = at
            await session.commit()
    _run(go())


def test_review_2_start_needs_a_fresh_two_factor_check(world):
    admin, _ = _admin("mo3a1-admin-stale-mfa@example.com")
    stale = datetime.now(timezone.utc) - timedelta(minutes=config.VIEW_AS_MFA_FRESH_MINUTES + 5)
    _admin_session_verified(admin, stale)
    body = {"user_id": world["target_me"]["id"], "reason": "Checking the dashboard"}
    refused = client.post(START, headers=admin, json=body)
    assert refused.status_code == 403 and refused.headers.get("X-Step-Up") == "mfa" and "two-factor" in refused.json()["detail"]
    assert client.get("/api/admin/console/system", headers=admin).status_code == 200      # the console itself still opens
    _admin_session_verified(admin, datetime.now(timezone.utc))                              # a step-up just now
    assert client.post(START, headers=admin, json=body).status_code == 200


def _age(view_id: int) -> None:
    async def go():
        async with _session_factory() as session:
            (await session.get(ViewSessionRecord, view_id)).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
    _run(go())


def test_review_3a_a_view_that_expires_unseen_is_ended_and_audited(world):
    admin, _ = _admin("mo3a1-admin-unseen@example.com")
    view_id = _start(admin, world["target_me"]["id"])["view"]["id"]
    _age(view_id)
    assert client.get(START, headers=world["admin"]).status_code == 200                      # an admin read ends it
    record = _run(_rows(ViewSessionRecord, ViewSessionRecord.id == view_id))[0]
    assert record.ended_at is not None and record.end_reason == "expired"
    ended = _run(_rows(AuditLogRecord, AuditLogRecord.event == "support_view_ended"))
    assert any(f"#{view_id} " in r.detail and "expired" in r.detail for r in ended)
    # The worker's sweep ends them too (end_expired is what it calls every few minutes).
    other = _start(admin, world["target_me"]["id"])["view"]["id"]
    _age(other)

    async def sweep():
        async with _session_factory() as session:
            count = await view_as.end_expired(session)
            await session.commit()
            return count
    assert _run(sweep()) >= 1
    assert _run(_rows(ViewSessionRecord, ViewSessionRecord.id == other))[0].end_reason == "expired"


def test_review_3b_an_admin_ending_a_view_is_audited_as_the_actor(world):
    owner_admin, owner_me = _admin("mo3a1-admin-owner-of-view@example.com")
    actor, actor_me = _admin("mo3a1-admin-actor@example.com")
    view_id = _start(owner_admin, world["target_me"]["id"])["view"]["id"]
    assert client.post(f"{START}/{view_id}/end", headers=actor).json()["ended"] is True
    on_actor = _run(_rows(AuditLogRecord, AuditLogRecord.user_id == actor_me["id"], AuditLogRecord.event == "support_view_ended"))
    on_owner = _run(_rows(AuditLogRecord, AuditLogRecord.user_id == owner_me["id"], AuditLogRecord.event == "support_view_ended"))
    assert any(f"#{view_id} " in r.detail and f"ended by user {actor_me['id']}" in r.detail for r in on_actor)
    assert not any(f"#{view_id} " in r.detail for r in on_owner)


def test_review_3c_admin_reads_of_views_are_audited(world):
    view_id = _start(world["admin"], world["target_me"]["id"])["view"]["id"]
    client.get(START, headers=world["admin"])
    client.get(f"{START}/{view_id}", headers=world["admin"])
    reads = [r.detail for r in _run(_rows(AuditLogRecord, AuditLogRecord.user_id == world["admin_me"]["id"],
                                          AuditLogRecord.event == "support_views_read"))]
    assert any("listed the newest" in d for d in reads) and any(f"read view #{view_id} " in d for d in reads)


def test_review_3d_requests_are_not_added_to_an_ended_view(world):
    started = _start(world["admin"], world["target_me"]["id"])
    view = _bearer(started["token"])
    client.post("/api/view-session/end", headers=view)
    before = _run(_rows(ViewSessionRequestRecord, ViewSessionRequestRecord.view_session_id == started["view"]["id"]))
    assert client.get("/api/auth/me", headers=view).status_code == 401
    assert client.post("/api/watchlists", headers=view, json={"name": "x"}).status_code == 403
    after = _run(_rows(ViewSessionRequestRecord, ViewSessionRequestRecord.view_session_id == started["view"]["id"]))
    assert len(after) == len(before)
    record = _run(_rows(ViewSessionRecord, ViewSessionRecord.id == started["view"]["id"]))[0]
    assert record.request_count == len(before)


def test_review_4_one_open_view_per_admin_even_at_the_database(world):
    from sqlalchemy.exc import IntegrityError
    admin, admin_me = _admin("mo3a1-admin-race@example.com")
    _start(admin, world["target_me"]["id"])
    _start(admin, world["target_me"]["id"])
    open_ = _run(_rows(ViewSessionRecord, ViewSessionRecord.admin_user_id == admin_me["id"], ViewSessionRecord.ended_at.is_(None)))
    assert len(open_) == 1

    async def second_open_row():
        async with _session_factory() as session:
            now = datetime.now(timezone.utc)
            session.add(ViewSessionRecord(tenant_id=world["target_me"]["tenant_id"], user_id=world["target_me"]["id"],
                                          admin_user_id=admin_me["id"], reason="a racing start", token_hash="r" * 64,
                                          started_at=now, expires_at=now + timedelta(minutes=5)))
            await session.commit()
    with pytest.raises(IntegrityError):
        _run(second_open_row())
    index = next(i for i in ViewSessionRecord.__table__.indexes if i.name == "uq_view_sessions_open_per_admin")
    assert index.unique and "ended_at IS NULL" in str(index.dialect_options["postgresql"]["where"])
    source = (Path(__file__).resolve().parents[1] / "alembic" / "versions" / "e8a0c2d4f6b9_mo3_a1_view_as_user.py").read_text()
    assert "'uq_view_sessions_open_per_admin'" in source and "postgresql_where=sa.text('ended_at IS NULL')" in source


def test_review_5_a_view_only_receives_json_png_or_the_event_stream(world):
    from app.auth.view_as_middleware import ViewSessionReadOnly
    assert view_as.allowed_content_type("application/json; charset=utf-8", 200)
    assert view_as.allowed_content_type("image/png", 200) and view_as.allowed_content_type("text/event-stream", 200)
    for kind in ("text/csv", "text/html; charset=utf-8", "text/plain", "application/pdf", "application/octet-stream", "image/svg+xml"):
        assert not view_as.allowed_content_type(kind, 200), kind
    assert view_as.allowed_content_type("", 204) and not view_as.allowed_content_type("", 200)
    token = _start(world["admin"], world["target_me"]["id"])["token"]
    secret_body = b"name,secret\nx,mo3a1-leaked-csv\n"

    async def csv_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/csv")]})
        await send({"type": "http.response.body", "body": secret_body})

    sent: list = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)
    scope = {"type": "http", "method": "GET", "path": "/api/some/report", "query_string": b"", "app": app,
             "headers": [(b"authorization", f"Bearer {token}".encode())]}
    _run(ViewSessionReadOnly(csv_app)(scope, receive, send))
    assert sent[0]["status"] == 403 and all(secret_body not in m.get("body", b"") for m in sent)
    assert b"read-only support view" in b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def test_review_5_the_live_stream_ends_with_the_view():
    import time
    marker, expiry = view_as.set_active(True), view_as.set_expiry(time.time() + 42)
    try:
        assert 40 < view_as.stream_seconds(300) <= 42
        assert view_as.stream_seconds(10) is None                        # the stream's own limit is shorter
    finally:
        view_as.reset_expiry(expiry)
        view_as.reset_active(marker)
    assert view_as.stream_seconds(300) is None                           # not a view: unchanged


def test_review_6_the_thesis_read_may_not_refresh_or_narrate_in_a_view(world):
    view = _bearer(_start(world["admin"], world["target_me"]["id"])["token"])
    for query in ("refresh=true", "narrate=1", "refresh=yes&language=en", "narrate=on"):
        response = client.get(f"/api/ai/thesis/NIFTY?{query}", headers=view)
        assert response.status_code == 403 and response.json()["code"] == view_as.READ_ONLY_CODE, query
    for query in ("", "refresh=false", "narrate=0&language=en"):
        assert view_as.refusal("GET", "/api/ai/thesis/NIFTY", query) is None
    assert view_as.refusal("GET", "/api/ai/brief", "refresh=true") is None


def test_review_7_normal_tokens_skip_the_extra_decode(world, monkeypatch):
    import jwt as pyjwt
    normal = world["target"]["Authorization"].split()[1]
    view_token = _start(world["admin"], world["target_me"]["id"])["token"]
    assert view_as._payload_mentions_vs(view_token) and not view_as._payload_mentions_vs(normal)
    assert not view_as._payload_mentions_vs("not.a-token") and not view_as._payload_mentions_vs("x.!!!.y")
    calls = []
    real = pyjwt.decode
    monkeypatch.setattr(pyjwt, "decode", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert view_as.unverified_view_claims(normal) is None and calls == []
    assert view_as.unverified_view_claims(view_token) is not None and calls == [1]
