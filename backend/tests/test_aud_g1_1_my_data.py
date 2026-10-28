"""AUD-G1.1 (owner audit 28 Oct; DPDP rights): a user exports their own data and deletes their own account, alone."""
import asyncio
import io
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

from app.core import config
from app.db.models import AuditLogRecord, User
from app.me.account import run_due_erasures
from tests.test_auth_api import _register, _session_factory, client

PASSWORD = "S3cur3Pass!"


def _user(tag: str):
    email = f"g11-{tag}-{uuid.uuid4().hex[:6]}@example.com"
    headers = {"Authorization": f"Bearer {_register(email, PASSWORD)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return email, headers, me


def test_export_is_a_zip_of_my_own_rows_without_secrets():
    email, headers, me = _user("export")
    assert client.put("/api/me/prefs/chart", headers=headers, json={}).status_code in (200, 422)
    res = client.get("/api/me/export", headers=headers)
    assert res.status_code == 200 and res.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(res.content))
    names = set(z.namelist())
    assert {"README.txt", "profile.json", "trades.json", "trades.csv", "journal_entries.json", "ai_conversations.json"} <= names
    profile = json.loads(z.read("profile.json"))[0]
    assert profile["email"] == email and profile["id"] == me["id"]
    blob = b"".join(z.read(n) for n in names).decode()
    assert "hashed_password" not in blob and "mfa_secret" not in blob


def test_delete_my_account_waits_for_the_grace_period_and_can_be_cancelled():
    email, headers, me = _user("erase")
    assert client.get("/api/me/erase", headers=headers).json()["requested"] is False
    assert client.post("/api/me/erase", headers=headers, json={"password": PASSWORD, "confirm": "no"}).status_code == 400
    assert client.post("/api/me/erase", headers=headers, json={"password": "wrong-one", "confirm": "ERASE"}).status_code == 400
    asked = client.post("/api/me/erase", headers=headers, json={"password": PASSWORD, "confirm": "erase"})
    assert asked.status_code == 200 and asked.json()["requested"] is True and asked.json()["grace_days"] == config.ACCOUNT_ERASE_GRACE_DAYS
    assert client.get("/api/auth/me", headers=headers).status_code == 200                 # still works in the grace period
    assert client.delete("/api/me/erase", headers=headers).json()["requested"] is False   # cancelled

    client.post("/api/me/erase", headers=headers, json={"password": PASSWORD, "confirm": "ERASE"})

    async def go(now):
        async with _session_factory() as session:
            n = await run_due_erasures(session, now)
            await session.commit()
            return n
    assert asyncio.run(go(datetime.now(timezone.utc))) == 0                                # inside the grace period
    later = datetime.now(timezone.utc) + timedelta(days=config.ACCOUNT_ERASE_GRACE_DAYS, minutes=1)
    assert asyncio.run(go(later)) >= 1

    async def check():
        async with _session_factory() as session:
            u = await session.get(User, me["id"])
            events = [r.event for r in (await session.execute(
                AuditLogRecord.__table__.select().where(AuditLogRecord.tenant_id == me["tenant_id"]))).all()]
            return u, events
    u, events = asyncio.run(check())
    assert u.email == f"erased-{me['id']}@erased.invalid" and not u.is_active
    assert {"account_erase_requested", "account_erase_cancelled", "user_erased"} <= set(events)
