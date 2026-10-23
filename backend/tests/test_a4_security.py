"""A4 (production readiness, security): two-factor on every SUPER_ADMIN route, the step-up freshness window for admin
writes, the absolute session cap, "log out other devices", the per-account rate limits with plain messages, the AI
limit on the two AI calls outside `/api/ai`, the API's own security headers, and the CI / edge checks they rely on."""
import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyotp
import pytest
from sqlalchemy import select, update

from app.core import rate_limit as core_rate_limit
from app.db.models import User, UserSessionRecord
from tests.route_inventory import dependency_calls, gate_of, iter_api_routes
from tests.test_auth_api import _register, _session_factory, app, client
from tests.utils import enable_mfa

ROOT = Path(__file__).resolve().parents[2]


def _run(coro):
    return asyncio.run(coro)


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _me(headers):
    return client.get("/api/auth/me", headers=headers).json()


def _promote(headers):
    me = _me(headers)

    async def go():
        async with _session_factory() as s:
            (await s.get(User, me["id"])).role = "SUPER_ADMIN"
            await s.commit()
    _run(go())
    return me


def _age_sessions(user_id, **fields):
    async def go():
        async with _session_factory() as s:
            await s.execute(update(UserSessionRecord).where(UserSessionRecord.user_id == user_id).values(**fields))
            await s.commit()
    _run(go())


def _next_code(secret, step):
    return pyotp.TOTP(secret).at(datetime.now(timezone.utc) + timedelta(seconds=30 * step))


def _admin(email):
    headers = _headers(_register(email))
    me = _promote(headers)
    secret = enable_mfa(headers)
    return headers, me, secret


# --- 1. MFA on every SUPER_ADMIN route ----------------------------------------------------------------------------------

def _admin_only(route) -> bool:
    gate = gate_of(route) or ""
    return any(part == "admin" or part.split(" ")[0] == "role:SUPER_ADMIN" for part in gate.split("; "))


_MFA_GATES = {"require_mfa_session", "require_checked_mfa_session"}


def _has_mfa(route) -> bool:
    return any(getattr(call, "__qualname__", "") in _MFA_GATES for call in dependency_calls(route.dependant))


def test_every_super_admin_only_route_requires_a_two_factor_session():
    """Review R5 b: a `require_role()` (SUPER_ADMIN only) route without `require_mfa_session` fails this test - the next
    admin route cannot forget it."""
    routes = [r for r in iter_api_routes(app) if _admin_only(r)]
    assert len(routes) > 50                                              # the sweep sees the admin routers
    missing = sorted(f"{r.method} {r.path}" for r in routes if not _has_mfa(r))
    assert missing == []


@pytest.mark.parametrize("method,path,body", [
    ("PUT", "/api/news-feed/sources/x", {"on": True}),
    ("POST", "/api/news-feed/refresh", None),
    ("POST", "/api/team/members/1/verify-email", None),
    ("PUT", "/api/compliance/golive/evidence", {"item_id": "vendor.cert_in_vapt", "reference": "r"}),
    ("GET", "/api/compliance/golive", None),
    ("POST", "/api/market-holidays", {"exchange": "NSE", "holiday_date": "2031-01-26", "description": "x"}),
    ("DELETE", "/api/market-holidays/999999", None),
    ("GET", "/api/system/encryption", None),
    ("GET", "/api/audit-logs/verify", None),
])
def test_the_review_routes_refuse_a_super_admin_without_two_factor(method, path, body):
    headers = _headers(_register(f"a4-nomfa-{abs(hash((method, path))) % 10**8}@example.com"))
    _promote(headers)
    res = client.request(method, path, headers=headers, json=body)
    assert res.status_code == 403 and "two-factor" in res.json()["detail"] and res.headers.get("x-step-up") == "mfa"


def test_platform_wide_writes_inside_member_routes_need_two_factor():
    """A GLOBAL risk limit and a market event for every organisation are SUPER_ADMIN writes on routes every member uses."""
    headers = _headers(_register("a4-global-writes@example.com"))
    _promote(headers)
    limit = {"scope": "GLOBAL", "scope_id": "", "limit_type": "MAX_OPEN_POSITIONS", "limit_value": 50, "enabled": True}
    res = client.put("/api/risk/limits", headers=headers, json=limit)
    assert res.status_code == 403 and "two-factor" in res.json()["detail"], res.text
    event = {"event_date": "2031-02-01", "kind": "OTHER", "action": "BLOCK", "description": "x", "global_event": True}
    res = client.post("/api/risk-guardian/events", headers=headers, json=event)
    assert res.status_code == 403 and "two-factor" in res.json()["detail"], res.text
    enable_mfa(headers)
    saved = client.put("/api/risk/limits", headers=headers, json={**limit, "limit_value": 100000})
    assert saved.status_code == 200, saved.text
    assert client.delete(f"/api/risk/limits/{saved.json()['id']}", headers=headers).status_code == 204   # never left for others


# --- 1b. The step-up freshness window --------------------------------------------------------------------------------

def test_an_admin_write_needs_a_recent_check_a_read_does_not(monkeypatch):
    headers, me, secret = _admin("a4-fresh@example.com")
    monkeypatch.setattr("app.core.config.MFA_STEP_UP_MINUTES", 30)
    body = {"exchange": "NSE", "holiday_date": "2031-03-03", "description": "A4 test"}
    _age_sessions(me["id"], mfa_verified_at=datetime.now(timezone.utc) - timedelta(minutes=31))
    assert client.get("/api/admin/overview", headers=headers).status_code == 200          # a read: checked is enough
    refused = client.post("/api/market-holidays", headers=headers, json=body)
    assert refused.status_code == 403 and refused.headers.get("x-step-up") == "mfa"
    assert "more than 30 minutes ago" in refused.json()["detail"] and "two-factor" in refused.json()["detail"]
    assert client.post("/api/auth/mfa/step-up", headers=headers, json={"code": _next_code(secret, 1)}).status_code == 204
    added = client.post("/api/market-holidays", headers=headers, json=body)
    assert added.status_code == 201, added.text
    assert client.delete(f"/api/market-holidays/{added.json()['id']}", headers=headers).status_code == 204


def test_engaging_the_global_kill_switch_is_never_held_up_by_the_window_disengaging_is(monkeypatch):
    """Stopping everything only makes things safer: a checked session is enough. Starting again needs a recent check."""
    headers, me, secret = _admin("a4-kill@example.com")
    monkeypatch.setattr("app.core.config.MFA_STEP_UP_MINUTES", 30)
    _age_sessions(me["id"], mfa_verified_at=datetime.now(timezone.utc) - timedelta(hours=5))
    assert client.post("/api/kill-switch/global/engage", headers=headers, json={"reason": "A4 test"}).status_code == 200
    refused = client.post("/api/kill-switch/global/disengage", headers=headers)
    assert refused.status_code == 403 and refused.headers.get("x-step-up") == "mfa"
    assert client.post("/api/auth/mfa/step-up", headers=headers, json={"code": _next_code(secret, 1)}).status_code == 204
    assert client.post("/api/kill-switch/global/disengage", headers=headers).status_code == 200


def test_the_window_is_a_setting(monkeypatch):
    headers, me, _ = _admin("a4-window@example.com")
    _age_sessions(me["id"], mfa_verified_at=datetime.now(timezone.utc) - timedelta(minutes=31))
    monkeypatch.setattr("app.core.config.MFA_STEP_UP_MINUTES", 60)
    body = {"exchange": "NSE", "holiday_date": "2031-04-04", "description": "A4 window"}
    added = client.post("/api/market-holidays", headers=headers, json=body)
    assert added.status_code == 201, added.text
    client.delete(f"/api/market-holidays/{added.json()['id']}", headers=headers)


def test_settings_fall_back_to_their_defaults_when_out_of_range(monkeypatch):
    from app.core.config import _env_int_in
    monkeypatch.setenv("A4_X", "0")
    assert _env_int_in("A4_X", 30, 1, 1440) == 30
    monkeypatch.setenv("A4_X", "abc")
    assert _env_int_in("A4_X", 30, 1, 1440) == 30
    monkeypatch.setenv("A4_X", "45")
    assert _env_int_in("A4_X", 30, 1, 1440) == 45


# --- 1c. The absolute session cap ------------------------------------------------------------------------------------

def test_a_session_past_the_cap_ends_however_often_it_was_refreshed(monkeypatch):
    monkeypatch.setattr("app.core.config.SESSION_MAX_DAYS", 90)
    res = client.post("/api/auth/register", json={"email": "a4-cap@example.com", "password": "S3cur3Pass!"})
    headers, refresh = _headers(res.json()["access_token"]), res.json()["refresh_token"]
    me = _me(headers)
    # Signed in 89 days ago and refreshed every day since: the next refresh is capped at day 90, not day 89 + 30.
    _age_sessions(me["id"], created_at=datetime.now(timezone.utc) - timedelta(days=89))
    rotated = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert rotated.status_code == 200, rotated.text
    headers, refresh = _headers(rotated.json()["access_token"]), rotated.json()["refresh_token"]
    sessions = client.get("/api/auth/sessions", headers=headers).json()
    expires = datetime.fromisoformat(sessions[0]["expires_at"])
    assert timedelta(hours=23) < expires - datetime.now(timezone.utc) < timedelta(hours=25)
    # Past the cap: the access token stops working at once and the refresh token cannot renew it.
    _age_sessions(me["id"], created_at=datetime.now(timezone.utc) - timedelta(days=91))
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": refresh}).status_code == 401


def test_session_is_live_checks_the_cap_even_when_expires_at_is_later(monkeypatch):
    from app.auth.sessions import session_cap, session_is_live
    monkeypatch.setattr("app.core.config.SESSION_MAX_DAYS", 10)
    now = datetime.now(timezone.utc)
    row = UserSessionRecord(created_at=now - timedelta(days=11), expires_at=now + timedelta(days=5), revoked_at=None)
    assert session_is_live(row) is False
    row.created_at = now - timedelta(days=9)
    assert session_is_live(row) is True and session_cap(row.created_at) - now < timedelta(days=2)


# --- 6. Log out other devices ---------------------------------------------------------------------------------------

def _login(email):
    res = client.post("/api/auth/login", json={"email": email, "password": "S3cur3Pass!"})
    assert res.status_code == 200, res.text
    return _headers(res.json()["access_token"])


def test_log_out_other_devices_keeps_this_one():
    phone = _headers(_register("a4-others@example.com"))
    laptop, tablet = _login("a4-others@example.com"), _login("a4-others@example.com")
    assert len(client.get("/api/auth/sessions", headers=laptop).json()) == 3
    res = client.post("/api/auth/logout-others", headers=laptop)
    assert res.status_code == 200 and res.json() == {"signed_out": 2}
    assert client.get("/api/auth/me", headers=laptop).status_code == 200
    assert client.get("/api/auth/me", headers=phone).status_code == 401
    assert client.get("/api/auth/me", headers=tablet).status_code == 401
    rows = client.get("/api/auth/sessions", headers=laptop).json()
    assert [r["current"] for r in rows] == [True]
    assert client.post("/api/auth/logout-others", headers=laptop).json() == {"signed_out": 0}


def test_log_out_other_devices_never_touches_another_account():
    mine = _headers(_register("a4-others-mine@example.com"))
    theirs = _headers(_register("a4-others-theirs@example.com"))
    assert client.post("/api/auth/logout-others", headers=mine).status_code == 200
    assert client.get("/api/auth/me", headers=theirs).status_code == 200


# --- 2. Rate limits with plain messages -----------------------------------------------------------------------------

@pytest.fixture
def live_limits():
    """Re-enables the per-account limiters the suite turns off (tests/test_auth_api.py) for one test."""
    from app.auth import routes
    names = ("mfa_user_rate_limit", "password_change_rate_limit", "resend_user_rate_limit")
    saved = {n: app.dependency_overrides.pop(getattr(routes, n), None) for n in names}
    core_rate_limit.reset()
    yield
    core_rate_limit.reset()
    for n, override in saved.items():
        if override is not None:
            app.dependency_overrides[getattr(routes, n)] = override


def test_step_up_codes_are_limited_per_account_with_a_plain_message(live_limits):
    headers = _headers(_register("a4-mfa-limit@example.com"))
    core_rate_limit.reset()
    enable_mfa(headers)                                                     # confirm counts as one
    answers = [client.post("/api/auth/mfa/step-up", headers=headers, json={"code": "000000"}) for _ in range(10)]
    assert [a.status_code for a in answers[:9]] == [401] * 9
    refused = answers[9]
    assert refused.status_code == 429 and refused.headers.get("retry-after") == "300"
    assert refused.json()["detail"].startswith("Too many code attempts. Please wait 5 minutes")


def test_password_change_is_limited_per_account(live_limits):
    headers = _headers(_register("a4-pw-limit@example.com"))
    body = {"current_password": "wrong-password-1", "new_password": "An0ther-Passw0rd!"}
    answers = [client.post("/api/auth/password/change", headers=headers, json=body).status_code for _ in range(6)]
    assert answers == [401] * 5 + [429]


def test_verification_resend_is_limited_per_account(live_limits):
    from app.auth import routes
    saved = app.dependency_overrides.get(routes.verify_rate_limit)
    app.dependency_overrides[routes.verify_rate_limit] = lambda: None          # only the per-account limit counts here
    try:
        headers = _headers(_register("a4-resend@example.com"))
        answers = [client.post("/api/auth/verify-email/resend", headers=headers) for _ in range(4)]
    finally:
        if saved is None:
            app.dependency_overrides.pop(routes.verify_rate_limit, None)
        else:
            app.dependency_overrides[routes.verify_rate_limit] = saved
    assert [a.status_code for a in answers[:3]] == [200] * 3
    assert answers[3].status_code == 429 and "Check your inbox" in answers[3].json()["detail"]


def test_password_forgot_is_limited_per_account_without_telling_the_caller(monkeypatch):
    """Over the per-account limit the answer is the same 202 (no enumeration), and no new link is made."""
    core_rate_limit.reset("auth_forgot_account")
    _register("a4-forgot@example.com")
    answers = [client.post("/api/auth/password/forgot", json={"email": "a4-forgot@example.com"}) for _ in range(5)]
    assert {a.status_code for a in answers} == {202} and len({a.json()["detail"] for a in answers}) == 1

    async def links():
        from app.db.models import PasswordResetRecord
        async with _session_factory() as s:
            user = await s.scalar(select(User).where(User.email == "a4-forgot@example.com"))
            return len((await s.scalars(select(PasswordResetRecord).where(PasswordResetRecord.user_id == user.id))).all())
    assert _run(links()) == 3


def test_the_auth_limiters_answer_in_plain_words():
    from app.auth import routes

    async def refuse(limiter):
        from starlette.requests import Request
        request = Request({"type": "http", "client": ("203.0.113.9", 1), "headers": [], "method": "POST", "path": "/"})
        for _ in range(200):
            try:
                await limiter(request)
            except Exception as exc:  # noqa: BLE001 - the HTTPException under test
                return exc
        return None
    core_rate_limit.reset()
    for limiter, words in ((routes.login_rate_limit, "sign-in attempts"), (routes.register_rate_limit, "sign-up attempts"),
                           (routes.forgot_rate_limit, "password-reset requests"), (routes.mfa_rate_limit, "code attempts"),
                           (routes.verify_rate_limit, "verification attempts")):
        exc = _run(refuse(limiter))
        assert exc is not None and exc.status_code == 429 and words in exc.detail and "Please wait" in exc.detail
        assert int(exc.headers["Retry-After"]) >= 60
    core_rate_limit.reset()


def test_the_ai_calls_outside_api_ai_count_toward_the_ai_limit():
    classify = [r for r in iter_api_routes(app) if r.key == ("POST", "/api/news-feed/classify")]
    assert classify and any(getattr(c, "__qualname__", "") == "ai_rate_limit" for c in dependency_calls(classify[0].dependant))
    source = (ROOT / "backend/app/journal/routes.py").read_text()
    narrate = source.split("async def weekly_review", 1)[1].split("async def _narrate", 1)[0]
    assert "await ai_rate_limit(http_request, user, session)" in narrate


# --- 5. Security headers --------------------------------------------------------------------------------------------

def test_the_api_sends_its_own_security_headers():
    res = client.get("/api/system/health")
    h = res.headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in h["content-security-policy"] and "default-src 'none'" in h["content-security-policy"]
    assert "camera=()" in h["permissions-policy"]
    assert "strict-transport-security" not in h                         # plain HTTP: no HSTS
    from fastapi.testclient import TestClient
    secure = TestClient(app, base_url="https://testserver").get("/api/system/health")
    assert secure.headers["strict-transport-security"].startswith("max-age=31536000")
    assert "x-frame-options" in client.get("/api/auth/me").headers           # an error answer too (401)


def test_the_edge_and_the_frontend_image_send_every_header():
    caddy = (ROOT / "deploy/Caddyfile").read_text()
    main_block = caddy.split("{$DOMAIN} {", 2)[-1]
    for header in ("Strict-Transport-Security", "X-Content-Type-Options", "X-Frame-Options DENY", "Referrer-Policy",
                   "Content-Security-Policy", "frame-ancestors 'none'", "Permissions-Policy"):
        assert header in main_block, header
    ip_edge = (ROOT / "deploy/Caddyfile.ip").read_text()
    for header in ("X-Frame-Options DENY", "frame-ancestors 'none'", "Permissions-Policy", "Referrer-Policy"):
        assert header in ip_edge, header
    nginx = (ROOT / "frontend/nginx.conf").read_text()
    for header in ("Content-Security-Policy", "frame-ancestors 'none'", "Permissions-Policy", "Referrer-Policy", "X-Content-Type-Options"):
        assert header in nginx, header


# --- 4. CI scanning -------------------------------------------------------------------------------------------------

def test_ci_scans_dependencies_and_images_and_fails_on_high_findings():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "pip-audit -r requirements.txt" in ci and "npm audit --omit=dev\n" in ci
    scan = ci.split("  container-scan:", 1)[1]
    assert "needs:" not in scan.split("steps:", 1)[0]                      # runs even while a test job is red
    for image in ("atp-backend:ci", "atp-frontend:ci"):
        step = scan.split(f"image-ref: {image}", 1)[1].split("- name:", 1)[0]
        assert 'severity: "CRITICAL,HIGH"' in step and 'exit-code: "1"' in step, image
    assert "image-ref: atp-backup:ci" in scan and re.search(r"for img in redis:7-alpine caddy:2-alpine rclone/rclone:1", scan)
