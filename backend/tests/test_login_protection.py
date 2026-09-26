"""Phase C4: login events, lockout per email and per IP, new-device notification, login history."""
import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.auth import lockout
from app.db.models import LoginEventRecord, NotificationRecord, User
from app.db.session import get_session
from app.main import app
from tests.test_auth_api import _override_get_session, _register, _session_factory, client
from tests.utils import enable_mfa


def _run(coro):
    return asyncio.run(coro)


def _login(email, password="S3cur3Pass!", c=client, ua="TestBrowser/1.0"):
    return c.post("/api/auth/login", json={"email": email, "password": password}, headers={"User-Agent": ua})


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _isolated(ip: str) -> TestClient:
    app.dependency_overrides[get_session] = _override_get_session
    return TestClient(app, client=(ip, 12345))


def test_every_attempt_is_recorded_with_ip_and_agent():
    _register("lp-record@example.com")
    _login("lp-record@example.com", "wrong-password-1")
    ok = _login("lp-record@example.com")
    assert ok.status_code == 200
    history = client.get("/api/auth/login-history", headers=_headers(ok.json()["access_token"])).json()
    assert [h["success"] for h in history[:2]] == [True, False]
    assert history[0]["reason"] == "password" and history[1]["reason"] == "bad_password"
    assert history[0]["ip_address"] and history[0]["user_agent"] == "TestBrowser/1.0"
    # Unknown emails are recorded too (for per-IP counting) but never shown to anyone else.
    _login("ghost@example.com", "whatever-123")

    async def ghost():
        async with _session_factory() as s:
            return await s.scalar(select(LoginEventRecord).where(LoginEventRecord.email == "ghost@example.com"))
    rec = _run(ghost())
    assert rec is not None and rec.user_id is None and rec.reason == "unknown_email"


def test_account_locks_after_ten_failures_even_with_the_right_password():
    _register("lp-lock@example.com")
    c = _isolated("203.0.113.50")
    for _ in range(lockout.MAX_FAILURES_PER_EMAIL):
        assert _login("lp-lock@example.com", "wrong-password-1", c=c).status_code == 401
    locked = _login("lp-lock@example.com", c=c)
    assert locked.status_code == 423 and "try again in 15 minutes" in locked.json()["detail"]
    # The lock is on the email, so it also holds from another IP...
    assert _login("lp-lock@example.com", c=_isolated("203.0.113.51")).status_code == 423
    # ...and lifts once the failures fall out of the window.
    async def age():
        async with _session_factory() as s:
            await s.execute(update(LoginEventRecord).where(LoginEventRecord.email == "lp-lock@example.com")
                            .values(created_at=datetime.now(timezone.utc) - timedelta(minutes=lockout.LOCKOUT_WINDOW_MINUTES + 1)))
            await s.commit()
    _run(age())
    assert _login("lp-lock@example.com", c=c).status_code == 200


def test_ip_lock_after_spraying_many_emails():
    c = _isolated("198.51.100.77")
    for i in range(lockout.MAX_FAILURES_PER_IP):
        assert _login(f"spray-{i}@example.com", "wrong-password-1", c=c).status_code == 401
    _register("lp-victim@example.com")
    blocked = _login("lp-victim@example.com", c=c)
    assert blocked.status_code == 423 and "network" in blocked.json()["detail"]
    assert _login("lp-victim@example.com").status_code == 200  # other IPs unaffected


def test_mfa_failures_count_towards_lockout():
    headers = _headers(_register("lp-mfa@example.com"))
    enable_mfa(headers)
    c = _isolated("203.0.113.60")
    for _ in range(lockout.MAX_FAILURES_PER_EMAIL):
        challenge = _login("lp-mfa@example.com", c=c).json()["mfa_token"]
        assert c.post("/api/auth/mfa/verify", json={"mfa_token": challenge, "code": "000000"}).status_code == 401
    assert _login("lp-mfa@example.com", c=c).status_code == 423


def test_new_device_login_raises_a_security_notification_once():
    token = _register("lp-device@example.com")
    me = client.get("/api/auth/me", headers=_headers(token)).json()

    def security_notes():
        async def go():
            async with _session_factory() as s:
                return list(await s.scalars(select(NotificationRecord).where(
                    NotificationRecord.tenant_id == me["tenant_id"], NotificationRecord.event_type == "SECURITY")))
        return _run(go())

    assert security_notes() == []  # registration itself is not a "login"
    assert _login("lp-device@example.com", ua="Laptop/1.0").status_code == 200
    assert len(security_notes()) == 1 and "New device login" in security_notes()[0].title
    assert _login("lp-device@example.com", ua="Laptop/1.0").status_code == 200
    assert len(security_notes()) == 1  # same device again: no new alert
    assert _login("lp-device@example.com", ua="Phone/2.0").status_code == 200
    assert len(security_notes()) == 2
    assert security_notes()[1].severity == "WARNING"


def test_admin_sees_platform_login_events():
    admin_headers = _headers(_register("lp-admin@example.com"))
    me = client.get("/api/auth/me", headers=admin_headers).json()

    async def promote():
        async with _session_factory() as s:
            (await s.get(User, me["id"])).role = "SUPER_ADMIN"
            await s.commit()
    _run(promote())
    enable_mfa(admin_headers)
    _login("lp-target@example.com", "wrong-password-1")
    rows = client.get("/api/admin/login-events?email=lp-target&failures_only=true", headers=admin_headers).json()
    assert rows and rows[0]["email"] == "lp-target@example.com" and rows[0]["success"] is False
