"""P3-4-B10 / B7 / B22 (DPDP s.14, s.11(1), s.8(2)): a user's nominee, and the processors in the data export."""
import asyncio
import io
import json
import re
import uuid
import zipfile
from pathlib import Path

from sqlalchemy import select

from app.db.models import AuditLogRecord, UserAiKeyRecord, UserNomineeRecord
from app.legal.processors import PROCESSORS
from app.me.account import KEY_ERASE_REQUESTS  # noqa: F401  (the module imports cleanly)
from app.retention.service import erase_user
from tests.test_auth_api import _register, _session_factory, client

PASSWORD = "S3cur3Pass!"
NOMINEE = {"name": "Asha Patil", "relationship": "Spouse", "email": "asha.nominee@example.com", "phone": None}


def _user(tag: str):
    email = f"p34-{tag}-{uuid.uuid4().hex[:6]}@example.com"
    headers = {"Authorization": f"Bearer {_register(email, PASSWORD)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return headers, me


def _zip(headers):
    res = client.get("/api/me/export", headers=headers)
    assert res.status_code == 200
    return zipfile.ZipFile(io.BytesIO(res.content))


def test_nominee_set_view_change_remove():
    headers, _ = _user("nom")
    assert client.get("/api/me/nominee", headers=headers).json() == {"nominee": None}
    put = client.put("/api/me/nominee", headers=headers, json=NOMINEE)
    assert put.status_code == 200 and put.json()["nominee"]["name"] == "Asha Patil"
    changed = client.put("/api/me/nominee", headers=headers, json={**NOMINEE, "email": None, "phone": "+91 98765 43210", "relationship": "Brother"})
    assert changed.json()["nominee"]["phone"] == "+91 98765 43210" and changed.json()["nominee"]["email"] is None
    assert client.get("/api/me/nominee", headers=headers).json()["nominee"]["relationship"] == "Brother"
    assert client.delete("/api/me/nominee", headers=headers).json() == {"nominee": None}
    assert client.get("/api/me/nominee", headers=headers).json() == {"nominee": None}


def test_nominee_is_validated_and_private_to_the_user():
    headers, _ = _user("val")
    other, _ = _user("oth")
    assert client.put("/api/me/nominee", headers=headers, json={**NOMINEE, "email": None}).status_code == 422       # no contact
    assert client.put("/api/me/nominee", headers=headers, json={**NOMINEE, "name": "x" * 121}).status_code == 422
    assert client.put("/api/me/nominee", headers=headers, json={**NOMINEE, "relationship": ""}).status_code == 422
    assert client.put("/api/me/nominee", headers=headers, json={**NOMINEE, "email": "not-an-email"}).status_code == 422
    assert client.put("/api/me/nominee", headers=headers, json={**NOMINEE, "email": None, "phone": "12ab"}).status_code == 422
    assert client.put("/api/me/nominee", headers=headers, json=NOMINEE).status_code == 200
    assert client.get("/api/me/nominee", headers=other).json() == {"nominee": None}
    assert client.get("/api/me/nominee").status_code in (401, 403)


def test_nominee_contact_is_never_in_the_audit_log_and_is_in_the_export():
    headers, me = _user("aud")
    client.put("/api/me/nominee", headers=headers, json=NOMINEE)
    nominee = json.loads(_zip(headers).read("nominee.json"))
    assert nominee[0]["name"] == "Asha Patil" and nominee[0]["email"] == "asha.nominee@example.com"

    async def audit():
        async with _session_factory() as s:
            rows = await s.scalars(select(AuditLogRecord).where(AuditLogRecord.user_id == me["id"]))
            return [(r.event, r.detail) for r in rows]
    rows = asyncio.run(audit())
    assert any(e == "nominee_saved" for e, _ in rows)
    assert not any("asha" in (d or "").lower() for _, d in rows)


def test_nominee_goes_with_the_account():
    headers, me = _user("era")
    client.put("/api/me/nominee", headers=headers, json=NOMINEE)

    async def erase():
        async with _session_factory() as s:
            user = await s.get(__import__("app.db.models", fromlist=["User"]).User, me["id"])
            await erase_user(s, user, actor_id=user.id, reason="test")
            await s.commit()
            return await s.scalar(select(UserNomineeRecord).where(UserNomineeRecord.user_id == me["id"]))
    assert asyncio.run(erase()) is None


def test_export_has_a_processors_file_with_the_ones_this_user_uses():
    headers, me = _user("proc")
    z = _zip(headers)
    rows = json.loads(z.read("processors.json"))
    assert [r["name"] for r in rows] == [p.name for p in PROCESSORS]
    by = {r["name"].split(" ")[0]: r for r in rows}
    assert by["Hosting"]["you_use_it"] is True and by["AI"]["you_use_it"] is False and by["Telegram"]["you_use_it"] is False
    assert all(r["purpose"] and r["data_shared"] for r in rows)
    assert "README.txt" in z.namelist() and "processors.json" in z.read("README.txt").decode()

    async def add_key():
        async with _session_factory() as s:
            s.add(UserAiKeyRecord(tenant_id=me["tenant_id"], user_id=me["id"], provider="openai", encrypted_key="x", key_hint="ab"))
            await s.commit()
    asyncio.run(add_key())
    ai = next(r for r in json.loads(_zip(headers).read("processors.json")) if r["name"].startswith("AI"))
    assert ai["you_use_it"] is True and ai["detail"] == "openai"
    assert "encrypted_key" not in _zip(headers).read("processors.json").decode()


def test_register_document_names_every_processor():
    doc = (Path(__file__).resolve().parents[2] / "docs" / "legal" / "PROCESSORS.md").read_text()
    for p in PROCESSORS:
        assert p.name in doc, p.name
    assert "to be confirmed" in doc.lower()
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", doc)          # no IP address in a public repo


# --- P3-4-B7: the rest of the personal-data tables in the export ---------------------------------------------------

def test_export_has_login_events_sessions_and_consents_only_his_own_and_no_secrets():
    from datetime import UTC, datetime, timedelta

    from app.db.models import AiAcknowledgementRecord, LoginEventRecord, NotificationRecord, UserSessionRecord
    mine, me = _user("exp-a")
    other, them = _user("exp-b")

    async def seed():
        async with _session_factory() as s:
            now = datetime.now(UTC)
            for u, tag in ((me, "MINE"), (them, "THEIRS")):
                s.add(LoginEventRecord(user_id=u["id"], tenant_id=u["tenant_id"], email=f"{tag}@example.com", success=True, reason="", ip_address=f"10.0.0.{1 if tag == 'MINE' else 2}", user_agent=tag))
                s.add(AiAcknowledgementRecord(tenant_id=u["tenant_id"], user_id=u["id"], kind="copilot_terms", version=f"v-{tag}", text_sha256="a" * 64))
                s.add(NotificationRecord(tenant_id=u["tenant_id"], user_id=u["id"], event_type="x", title=f"T-{tag}", message=""))
                s.add(UserSessionRecord(user_id=u["id"], tenant_id=u["tenant_id"], refresh_token_hash=f"RT{tag}".ljust(64, "0"), previous_token_hash=f"PT{tag}".ljust(64, "0"),
                                        user_agent=f"UA-{tag}", expires_at=now + timedelta(days=1)))
            await s.commit()
    asyncio.run(seed())
    z = _zip(mine)
    names = z.namelist()
    for f in ("login_events", "sessions", "consents", "notifications", "deployments", "risk_limits", "alert_channels", "broker_accounts",
              "linked_logins", "api_keys", "watchlists"):
        assert f"{f}.json" in names and f"{f}.csv" in names, f
    logins = json.loads(z.read("login_events.json"))
    assert logins and all(r["user_id"] == me["id"] for r in logins) and "THEIRS" not in json.dumps(logins)
    sessions = json.loads(z.read("sessions.json"))
    assert any(r["user_agent"] == "UA-MINE" for r in sessions) and "UA-THEIRS" not in json.dumps(sessions)
    consents = json.loads(z.read("consents.json"))
    assert "v-MINE" in [r["version"] for r in consents] and "v-THEIRS" not in json.dumps(consents)
    assert all(r["user_id"] == me["id"] for r in consents)
    assert "T-MINE" in [r["title"] for r in json.loads(z.read("notifications.json"))]
    everything = " ".join(z.read(n).decode() for n in names)
    for secret in ("refresh_token_hash", "previous_token_hash", "encrypted_config", "key_hash", "password_hash", "RTMINE", "PTMINE", "RTTHEIRS"):
        assert secret not in everything, secret
    assert "THEIRS" not in everything
