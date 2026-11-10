"""M95 PR-3 (M95-Q3 approved): "Ask for an invite" while `self_signup` is OFF - anonymous, per-IP limited, deduped per
address and day, the IP stored only as a keyed hash; the admin list and its invited / dismissed marks are SUPER_ADMIN
only; the migration is additive and rollback-safe on the one head chain."""
import asyncio
import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.auth.invite_requests import ACCEPTED, SIGN_UP_OPEN, ip_hash
from app.core import rate_limit as core_rate_limit
from app.db.models import AuditLogRecord, InviteRequestRecord
from tests.test_admin_api import _admin
from tests.test_auth_api import _register, _session_factory, client
from tests.test_phase_n_hardening import _reset_controls

URL = "/api/auth/invite-request"
ADMIN_URL = "/api/admin/invite-requests"
BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "a3c5e7f9b1d4_m95_invite_requests.py"


def _run(coro):
    return asyncio.run(coro)


def _rows(email: str) -> list[InviteRequestRecord]:
    async def go():
        async with _session_factory() as session:
            return list((await session.scalars(select(InviteRequestRecord).where(InviteRequestRecord.email == email))).all())
    return _run(go())


@pytest.fixture()
def admin_headers():
    core_rate_limit.reset("auth_invite_request")
    _reset_controls()
    headers, _ = _admin(f"m95-admin-{uuid.uuid4().hex[:8]}@example.com")
    assert client.put("/api/admin/controls/flags/self_signup", headers=headers, json={"on": False}).status_code == 200
    yield headers
    _reset_controls()
    core_rate_limit.reset("auth_invite_request")


def test_anonymous_request_is_accepted_only_while_sign_up_is_closed(admin_headers):
    res = client.post(URL, json={"email": "  Friend.One@Example.COM ", "note": "  A friend of Abhi  "})
    assert res.status_code == 202 and res.json() == {"message": ACCEPTED}
    rows = _rows("friend.one@example.com")
    assert len(rows) == 1
    row = rows[0]
    assert row.note == "A friend of Abhi" and row.status == "new" and row.handled_by is None and row.handled_at is None
    assert row.ip_hash == ip_hash("testclient") and "testclient" not in row.ip_hash and len(row.ip_hash) == 64
    # an empty note is no note
    assert client.post(URL, json={"email": "friend.two@example.com", "note": "   "}).status_code == 202
    assert _rows("friend.two@example.com")[0].note is None
    # sign-up open: 409 with the plain sentence, nothing stored
    _reset_controls()
    open_ = client.post(URL, json={"email": "friend.three@example.com"})
    assert open_.status_code == 409 and open_.json()["detail"] == SIGN_UP_OPEN
    assert _rows("friend.three@example.com") == []


def test_the_email_is_checked_and_the_note_is_capped(admin_headers):
    assert client.post(URL, json={"email": "not-an-address"}).status_code == 422
    assert client.post(URL, json={}).status_code == 422
    assert client.post(URL, json={"email": "long@example.com", "note": "x" * 501}).status_code == 422
    assert client.post(URL, json={"email": "long@example.com", "note": "x" * 500}).status_code == 202
    assert len(_rows("long@example.com")[0].note or "") == 500


def test_the_same_address_within_a_day_is_stored_once_and_answered_the_same(admin_headers):
    first = client.post(URL, json={"email": "twice@example.com", "note": "first"})
    second = client.post(URL, json={"email": "TWICE@example.com", "note": "second"})
    assert first.status_code == second.status_code == 202 and first.json() == second.json()
    rows = _rows("twice@example.com")
    assert len(rows) == 1 and rows[0].note == "first"


def test_the_per_ip_limit(admin_headers):
    for n in range(5):
        assert client.post(URL, json={"email": f"limit{n}@example.com"}).status_code == 202
    blocked = client.post(URL, json={"email": "limit5@example.com"})
    assert blocked.status_code == 429 and "Too many invite requests" in blocked.json()["detail"]
    assert blocked.headers.get("Retry-After") == "3600"
    assert _rows("limit5@example.com") == []


def test_admin_lists_and_marks_requests(admin_headers):
    client.post(URL, json={"email": "list.a@example.com", "note": "hello"})
    client.post(URL, json={"email": "list.b@example.com"})
    listed = client.get(ADMIN_URL, headers=admin_headers)
    assert listed.status_code == 200
    body = listed.json()
    emails = [r["email"] for r in body["requests"]]
    assert emails.index("list.b@example.com") < emails.index("list.a@example.com")    # newest first
    assert set(body["counts"]) == {"new", "invited", "dismissed"} and body["counts"]["new"] >= 2
    assert "ip_hash" not in body["requests"][0]
    target = next(r for r in body["requests"] if r["email"] == "list.a@example.com")
    assert target["note"] == "hello" and target["status"] == "new"

    marked = client.patch(f"{ADMIN_URL}/{target['id']}", headers=admin_headers, json={"status": "invited"})
    assert marked.status_code == 200
    assert marked.json()["status"] == "invited" and marked.json()["handled_by"] and marked.json()["handled_at"]
    new_only = client.get(ADMIN_URL, params={"status": "new"}, headers=admin_headers).json()["requests"]
    assert all(r["status"] == "new" for r in new_only) and target["id"] not in {r["id"] for r in new_only}
    assert client.patch(f"{ADMIN_URL}/{target['id']}", headers=admin_headers, json={"status": "dismissed"}).json()["status"] == "dismissed"
    assert client.patch(f"{ADMIN_URL}/{target['id']}", headers=admin_headers, json={"status": "new"}).status_code == 422
    assert client.patch(f"{ADMIN_URL}/999999", headers=admin_headers, json={"status": "invited"}).status_code == 404

    async def audited():
        async with _session_factory() as session:
            return list((await session.scalars(select(AuditLogRecord).where(AuditLogRecord.event == "invite_request_marked"))).all())
    assert any(f"request {target['id']}: invited" in a.detail for a in _run(audited()))


def test_admin_routes_refuse_a_user_and_an_anonymous_caller():
    _reset_controls()
    user = {"Authorization": f"Bearer {_register(f'm95-user-{uuid.uuid4().hex[:8]}@example.com')}"}
    assert client.get(ADMIN_URL, headers=user).status_code == 403
    assert client.patch(f"{ADMIN_URL}/1", headers=user, json={"status": "invited"}).status_code == 403
    assert client.get(ADMIN_URL).status_code in (401, 403)
    assert client.patch(f"{ADMIN_URL}/1", json={"status": "invited"}).status_code in (401, 403)


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_is_additive_rollback_safe_and_the_one_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from scripts.migration_rollback_safety import lint, risky_operations
    module = _module("m95_invite_requests_migration")
    assert module.revision == "a3c5e7f9b1d4" and module.down_revision == "f2b4d6e8a0c1" and module.ROLLBACK_SAFE is True
    source = MIGRATION.read_text(encoding="utf-8")
    assert risky_operations(source) == [] and not hasattr(module, "ROLLBACK_SAFE_REASON")
    assert not [p for p in lint() if p.startswith(MIGRATION.name)]
    assert ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini"))).get_heads() == ["a3c5e7f9b1d4"]


def test_the_migration_runs_up_and_down_on_sqlite(tmp_path):
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    module = _module("m95_invite_requests_migration_run")
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with eng.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            module.upgrade()
        cols = {c["name"] for c in sa.inspect(conn).get_columns("invite_requests")}
        assert cols == {"id", "email", "note", "ip_hash", "status", "handled_by", "handled_at", "created_at"}
        assert cols == set(InviteRequestRecord.__table__.c.keys())
        with Operations.context(ctx):
            module.downgrade()
        assert "invite_requests" not in sa.inspect(conn).get_table_names()
