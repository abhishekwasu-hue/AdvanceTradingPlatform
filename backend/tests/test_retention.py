"""Phase D3: retention jobs and personal-data erasure - only finished operational rows past
their policy age go, regulatory tables are never touched, runs are audited, the worker runs it
once a day outside market hours, and erasure anonymises a removed teammate."""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text

from app.db.models import (
    AlertDeliveryRecord, AuditLogRecord, LoginEventRecord, NotificationRecord, OrderRecord, PasswordResetRecord,
    TenantInviteRecord, TradeRecord, User, UserSessionRecord,
)
from app.retention import service
from app.retention.policy import MIN_DAYS, NEVER_DELETED, RetentionPolicy, load_policy
from app.retention.service import erase_user, preview_retention, run_retention
from tests.test_admin_api import _admin
from tests.test_auth_api import _register, _session_factory, client
from tests.test_team_api import _join, _owner

NOW = datetime(2026, 9, 26, 5, 0, tzinfo=timezone.utc)
POLICY = RetentionPolicy(True, 365, 90, 180, 30, 7, 30, batch_size=1000)


def _run(coro):
    return asyncio.run(coro)


def _count(model, **where):
    async def go():
        async with _session_factory() as session:
            q = select(func.count()).select_from(model)
            for k, v in where.items():
                q = q.where(getattr(model, k) == v)
            return await session.scalar(q)
    return _run(go())


def _h(prefix: str, user_id: int) -> str:
    """Unique per seeded user: the token columns are UNIQUE."""
    return (prefix * 64)[:64 - len(str(user_id))] + str(user_id)


def _seed(tenant_id: int, user_id: int):
    """One eligible (old) and one fresh row in every governed table for this tenant/user."""
    old = NOW - timedelta(days=400)
    fresh = NOW - timedelta(days=1)

    async def go():
        async with _session_factory() as session:
            session.add_all([
                LoginEventRecord(user_id=user_id, tenant_id=tenant_id, email="ret@example.com", success=True, reason="ok", created_at=old),
                LoginEventRecord(user_id=user_id, tenant_id=tenant_id, email="ret@example.com", success=True, reason="ok", created_at=fresh),
                NotificationRecord(tenant_id=tenant_id, user_id=user_id, event_type="ENTRY", title="old", created_at=old),
                NotificationRecord(tenant_id=tenant_id, user_id=user_id, event_type="ENTRY", title="fresh", created_at=fresh),
                UserSessionRecord(user_id=user_id, tenant_id=tenant_id, refresh_token_hash=_h("a", user_id), expires_at=old, created_at=old, last_used_at=old),
                UserSessionRecord(user_id=user_id, tenant_id=tenant_id, refresh_token_hash=_h("b", user_id), expires_at=NOW + timedelta(days=10), created_at=fresh, last_used_at=fresh),
                UserSessionRecord(user_id=user_id, tenant_id=tenant_id, refresh_token_hash=_h("c", user_id), expires_at=NOW + timedelta(days=10), revoked_at=old, created_at=old, last_used_at=old),
                PasswordResetRecord(user_id=user_id, token_hash=_h("d", user_id), expires_at=old, created_at=old),
                PasswordResetRecord(user_id=user_id, token_hash=_h("e", user_id), expires_at=NOW + timedelta(hours=1), created_at=fresh),
                TenantInviteRecord(tenant_id=tenant_id, invited_by=user_id, email="x@example.com", role="USER", token_hash=_h("f", user_id), expires_at=old, created_at=old),
                TenantInviteRecord(tenant_id=tenant_id, invited_by=user_id, email="y@example.com", role="USER", token_hash=_h("g", user_id), expires_at=NOW + timedelta(days=1), created_at=fresh),
            ])
            await session.commit()
    _run(go())


def test_policy_floor_and_env(monkeypatch):
    monkeypatch.setenv("RETENTION_LOGIN_EVENTS_DAYS", "1")       # below the floor
    monkeypatch.setenv("RETENTION_NOTIFICATIONS_DAYS", "oops")   # not a number
    monkeypatch.setenv("RETENTION_SESSIONS_DAYS", "45")
    policy = load_policy()
    assert policy.login_events_days == MIN_DAYS
    assert policy.notifications_days == 180
    assert policy.sessions_days == 45
    assert "orders" in NEVER_DELETED and "trades" in NEVER_DELETED and "audit_logs" in NEVER_DELETED


def test_regulatory_tables_are_never_in_the_rules():
    tables = {table for table, _, _ in service._rules(NOW, POLICY)}
    assert tables.isdisjoint(set(NEVER_DELETED))
    assert tables == {"login_events", "alert_deliveries", "notifications", "user_sessions", "password_resets", "tenant_invites"}


def test_run_deletes_only_old_finished_rows_and_audits():
    headers, me = _owner("retention-owner@example.com")
    tenant_id, user_id = me["tenant_id"], me["id"]
    _seed(tenant_id, user_id)
    before_orders = _count(OrderRecord)
    before_trades = _count(TradeRecord)
    before_audit = _count(AuditLogRecord)

    async def go():
        async with _session_factory() as session:
            preview = await preview_retention(session, NOW, POLICY)
            report = await run_retention(session, NOW, POLICY)
            return preview, report
    preview, report = _run(go())

    for table in ("login_events", "notifications", "user_sessions", "password_resets", "tenant_invites"):
        assert preview.deleted[table] >= 1, table
        assert report.deleted[table] == preview.deleted[table], table
    assert report.deleted["user_sessions"] >= 2  # expired + revoked, never the live one

    assert _count(LoginEventRecord, tenant_id=tenant_id, email="ret@example.com") == 1
    assert _count(NotificationRecord, tenant_id=tenant_id, title="fresh") == 1 and _count(NotificationRecord, tenant_id=tenant_id, title="old") == 0
    assert _count(UserSessionRecord, user_id=user_id, refresh_token_hash=_h("b", user_id)) == 1
    assert _count(UserSessionRecord, user_id=user_id, refresh_token_hash=_h("a", user_id)) == 0
    assert _count(PasswordResetRecord, user_id=user_id) == 1
    assert _count(TenantInviteRecord, tenant_id=tenant_id, email="y@example.com") == 1
    assert _count(OrderRecord) == before_orders and _count(TradeRecord) == before_trades
    assert _count(AuditLogRecord) == before_audit + 1
    assert _count(AuditLogRecord, event="retention_run") >= 1

    # Second run: nothing eligible, no audit noise.
    async def again():
        async with _session_factory() as session:
            return await run_retention(session, NOW, POLICY)
    report2 = _run(again())
    assert report2.total == 0 and _count(AuditLogRecord) == before_audit + 1


def test_disabled_policy_deletes_nothing():
    headers, me = _owner("retention-off@example.com")
    _seed(me["tenant_id"], me["id"])
    disabled = RetentionPolicy(False, 365, 90, 180, 30, 7, 30, batch_size=1000)

    async def go():
        async with _session_factory() as session:
            return await run_retention(session, NOW, disabled)
    report = _run(go())
    assert report.total == 0
    assert _count(LoginEventRecord, tenant_id=me["tenant_id"], email="ret@example.com") == 2


def test_batch_size_bounds_each_run():
    headers, me = _owner("retention-batch@example.com")
    old = NOW - timedelta(days=400)

    async def seed():
        async with _session_factory() as session:
            session.add_all([LoginEventRecord(user_id=me["id"], tenant_id=me["tenant_id"], email="batch@example.com", success=False, reason="x", created_at=old) for _ in range(5)])
            await session.commit()
    _run(seed())
    # batch_size floor is 100 in load_policy, but the service honours whatever the policy says.
    small = RetentionPolicy(True, 365, 90, 180, 30, 7, 30, batch_size=2)

    async def go():
        async with _session_factory() as session:
            before = (await preview_retention(session, NOW, small)).deleted["login_events"]
            report = await run_retention(session, NOW, small)
            after = (await preview_retention(session, NOW, small)).deleted["login_events"]
            return before, report, after
    before, report, after = _run(go())
    assert before >= 5
    assert report.deleted["login_events"] == 2 and after == before - 2


def test_worker_runs_retention_once_per_day_outside_market_hours(monkeypatch):
    from tests.test_trading_worker import CLOSED_NOW, _FakeBroker, _worker
    calls = []

    async def fake_run(session, now=None, policy=None):
        calls.append(now)
        return service.RetentionReport(started_at=now, dry_run=False, deleted={"login_events": 3})
    monkeypatch.setattr("app.workers.trading_worker.run_retention", fake_run)
    worker = _worker(monkeypatch, _FakeBroker())

    first = _run(worker.run_cycle(now=CLOSED_NOW))
    second = _run(worker.run_cycle(now=CLOSED_NOW + timedelta(minutes=1)))
    next_day = _run(worker.run_cycle(now=CLOSED_NOW + timedelta(days=1)))
    assert first.retention is not None and first.retention.total == 3
    assert second.retention is None
    assert next_day.retention is not None
    assert len(calls) == 2


def test_admin_retention_status_and_run(monkeypatch):
    headers, admin = _admin("retention-admin@example.com")
    status = client.get("/api/admin/retention", headers=headers)
    assert status.status_code == 200
    body = status.json()
    assert body["policy"]["login_events_days"] >= MIN_DAYS and "orders" in body["policy"]["never_deleted"]
    assert set(body["eligible_now"]) >= {"login_events", "notifications"}

    ran = client.post("/api/admin/retention/run", headers=headers)
    assert ran.status_code == 200 and "deleted" in ran.json()
    assert client.get("/api/admin/retention", headers=headers).json()["last_run"] is not None or ran.json()["total"] == 0

    owner_headers, _ = _owner("retention-not-admin@example.com")
    assert client.get("/api/admin/retention", headers=owner_headers).status_code == 403


def test_owner_erases_removed_member_personal_data():
    owner_headers, owner = _owner("erase-owner@example.com")
    member_headers, member = _join(owner_headers, "erase-me@example.com", role="USER")
    # A login attempt carrying the email, and an order attributed to the member.
    client.post("/api/auth/login", json={"email": "erase-me@example.com", "password": "wrong-password-1!"})

    active = client.post(f"/api/team/members/{member['id']}/erase", json={"reason": "left"}, headers=owner_headers)
    assert active.status_code == 409  # must be removed first

    assert client.delete(f"/api/team/members/{member['id']}", headers=owner_headers).status_code == 204
    erased = client.post(f"/api/team/members/{member['id']}/erase", json={"reason": "left the company"}, headers=owner_headers)
    assert erased.status_code == 204

    async def load():
        async with _session_factory() as session:
            u = await session.get(User, member["id"])
            events = list(await session.scalars(select(LoginEventRecord).where(LoginEventRecord.user_id == member["id"])))
            by_email = await session.scalar(select(func.count()).select_from(LoginEventRecord).where(LoginEventRecord.email == "erase-me@example.com"))
            audit = await session.scalar(select(AuditLogRecord).where(AuditLogRecord.event == "user_erased").order_by(AuditLogRecord.id.desc()))
            return u, events, by_email, audit
    u, events, by_email, audit = _run(load())
    assert u.email == f"erased-{member['id']}@erased.invalid" and not u.is_active and not u.mfa_enabled
    assert u.hashed_password == "!erased"
    assert by_email == 0 and all(e.email.endswith("@erased.invalid") for e in events)
    assert audit is not None and f"user #{member['id']}" in audit.detail and "erase-me" not in audit.detail

    # Login with the old email is impossible; the old token is dead.
    assert client.post("/api/auth/login", json={"email": "erase-me@example.com", "password": "S3cur3Pass!"}).status_code in (401, 423)
    assert client.get("/api/auth/me", headers=member_headers).status_code == 401
    # Idempotent.
    assert client.post(f"/api/team/members/{member['id']}/erase", json={}, headers=owner_headers).status_code == 204
    # Other owners cannot reach it.
    other_headers, _ = _owner("erase-other@example.com")
    assert client.post(f"/api/team/members/{member['id']}/erase", json={}, headers=other_headers).status_code == 404
