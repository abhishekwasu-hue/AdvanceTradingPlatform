"""Phase C2: password policy, forgot/reset (no enumeration, single use, 1h, sessions ended),
owner-issued reset links, and logged-in password change."""
import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from app.alerts import dispatcher
from app.auth.passwords import password_problem
from app.db.models import PasswordResetRecord, Tenant, User
from tests.test_auth_api import _register, _session_factory, client


def _run(coro):
    return asyncio.run(coro)


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _login(email, password="S3cur3Pass!"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_password_policy_rules():
    assert password_problem("S3cur3Pass!") is None
    assert "at least 10" in password_problem("Short1!")
    assert "most common" in password_problem("Password123")
    assert "most common" in password_problem("welcome@123")
    assert "variety" in password_problem("aaaaaaaaaa")
    assert "mix at least two" in password_problem("abcdefghijkl")
    assert "email" in password_problem("ravikumar2026", "ravikumar@example.com")
    assert password_problem("ravikumar2026", "rk@example.com") is None  # local part too short to matter


def test_register_and_invite_enforce_policy():
    assert client.post("/api/auth/register", json={"email": "pol-short@example.com", "password": "Abc123!"}).status_code == 422
    weak = client.post("/api/auth/register", json={"email": "pol-common@example.com", "password": "password123"})
    assert weak.status_code == 400 and "common" in weak.json()["detail"]
    mine = client.post("/api/auth/register", json={"email": "meghana@example.com", "password": "meghana2026!"})
    assert mine.status_code == 400 and "email" in mine.json()["detail"]
    assert client.post("/api/auth/register", json={"email": "pol-ok@example.com", "password": "Correct-Horse-9"}).status_code == 201


def _tenant_pro(headers):
    me = client.get("/api/auth/me", headers=headers).json()

    async def go():
        async with _session_factory() as s:
            (await s.get(Tenant, me["tenant_id"])).plan = "pro"
            await s.commit()
    _run(go())
    return me


def test_forgot_never_reveals_whether_email_exists():
    a = client.post("/api/auth/password/forgot", json={"email": "nobody-here@example.com"})
    _register("pw-forgot@example.com")
    b = client.post("/api/auth/password/forgot", json={"email": "pw-forgot@example.com"})
    assert a.status_code == b.status_code == 202 and a.json() == b.json()


def test_forgot_emails_link_through_tenant_channel_and_reset_ends_sessions(monkeypatch):
    token = _register("pw-reset@example.com")
    headers = _headers(token)
    _tenant_pro(headers)
    client.put("/api/alert-channels/email", headers=headers, json={"config": {
        "smtp_host": "smtp.example.com", "from_address": "alerts@example.com", "to_addresses": ["ops@example.com"]}})
    sent = []
    monkeypatch.setattr(dispatcher, "_smtp_send", lambda config, message: sent.append((config, message)))
    other = _login("pw-reset@example.com").json()

    assert client.post("/api/auth/password/forgot", json={"email": "PW-Reset@example.com"}).status_code == 202
    assert len(sent) == 1
    config, message = sent[0]
    assert message["To"] == "pw-reset@example.com" and "Reset your" in message["Subject"]
    link = next(line for line in message.get_content().splitlines() if "?reset=" in line)
    reset_token = parse_qs(urlparse(link).query)["reset"][0]

    info = client.get(f"/api/auth/password/reset/{reset_token}").json()
    assert info["valid"] and info["email_hint"].startswith("pw") and "*" in info["email_hint"]

    weak = client.post(f"/api/auth/password/reset/{reset_token}", json={"password": "password123"})
    assert weak.status_code == 400
    done = client.post(f"/api/auth/password/reset/{reset_token}", json={"password": "Brand-New-Pass-77"})
    assert done.status_code == 200 and done.json()["refresh_token"]
    # Old sessions are dead, old password refused, new password works, link is burnt.
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.get("/api/auth/me", headers=_headers(other["access_token"])).status_code == 401
    assert _login("pw-reset@example.com").status_code == 401
    assert _login("pw-reset@example.com", "Brand-New-Pass-77").status_code == 200
    again = client.post(f"/api/auth/password/reset/{reset_token}", json={"password": "Another-Pass-88"})
    assert again.status_code == 410 and "already been used" in again.json()["detail"]
    assert client.get(f"/api/auth/password/reset/{reset_token}").json()["valid"] is False
    assert client.get("/api/auth/password/reset/not-a-token").status_code == 404


def test_reset_link_expires_after_an_hour():
    token = _register("pw-expire@example.com")
    headers = _headers(token)
    _tenant_pro(headers)
    me = client.get("/api/auth/me", headers=headers).json()
    issued = client.post(f"/api/team/members/{me['id']}/reset-link", headers=headers).json()
    reset_token = parse_qs(urlparse(issued["reset_url"]).query)["reset"][0]

    async def expire():
        async with _session_factory() as s:
            rec = await s.scalar(select(PasswordResetRecord).where(PasswordResetRecord.user_id == me["id"]))
            rec.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            await s.commit()
    _run(expire())
    assert client.post(f"/api/auth/password/reset/{reset_token}", json={"password": "Brand-New-Pass-77"}).status_code == 410


def test_owner_issues_reset_link_for_member_without_email_channel():
    owner_headers = _headers(_register("pw-owner@example.com"))
    _tenant_pro(owner_headers)
    inv = client.post("/api/team/invites", headers=owner_headers, json={"email": "pw-member@example.com", "role": "USER"}).json()
    inv_token = parse_qs(urlparse(inv["invite_url"]).query)["invite"][0]
    member = client.post(f"/api/auth/invite/{inv_token}/accept", json={"password": "Member-Pass-2026"}).json()
    member_id = client.get("/api/auth/me", headers=_headers(member["access_token"])).json()["id"]

    assert client.post(f"/api/team/members/{member_id}/reset-link", headers=_headers(member["access_token"])).status_code == 403
    issued = client.post(f"/api/team/members/{member_id}/reset-link", headers=owner_headers)
    assert issued.status_code == 200 and issued.json()["delivered_by_email"] is False
    reset_token = parse_qs(urlparse(issued.json()["reset_url"]).query)["reset"][0]
    assert client.post(f"/api/auth/password/reset/{reset_token}", json={"password": "Member-New-Pass-1"}).status_code == 200
    assert _login("pw-member@example.com", "Member-New-Pass-1").status_code == 200
    events = [l["event"] for l in client.get("/api/audit-logs", headers=owner_headers).json()]
    assert "password_reset_issued_by_owner" in events


def test_change_password_requires_current_and_ends_other_sessions():
    token = _register("pw-change@example.com")
    headers = _headers(token)
    other = _login("pw-change@example.com").json()

    assert client.post("/api/auth/password/change", headers=headers, json={"current_password": "wrong", "new_password": "Totally-New-99"}).status_code == 401
    assert client.post("/api/auth/password/change", headers=headers, json={"current_password": "S3cur3Pass!", "new_password": "S3cur3Pass!"}).status_code == 400
    assert client.post("/api/auth/password/change", headers=headers, json={"current_password": "S3cur3Pass!", "new_password": "qwertyuiop1"}).status_code == 400

    changed = client.post("/api/auth/password/change", headers=headers, json={"current_password": "S3cur3Pass!", "new_password": "Totally-New-99"})
    assert changed.status_code == 200
    new_headers = _headers(changed.json()["access_token"])
    assert client.get("/api/auth/me", headers=new_headers).status_code == 200
    assert client.get("/api/auth/me", headers=_headers(other["access_token"])).status_code == 401
    assert _login("pw-change@example.com").status_code == 401
    assert _login("pw-change@example.com", "Totally-New-99").status_code == 200
    events = [l["event"] for l in client.get("/api/audit-logs", headers=new_headers).json()]
    assert "password_changed" in events
