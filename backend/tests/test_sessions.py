"""Phase C1: login sessions, rotating refresh tokens, revocation and immediate token invalidation."""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.auth import sessions as sess
from app.auth.security import create_access_token, decode_access_token
from app.db.models import User, UserSessionRecord
from tests.test_auth_api import _register, _session_factory, client


def _run(coro):
    return asyncio.run(coro)


def _login(email: str, password="S3cur3Pass!"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _headers(token: str):
    return {"Authorization": f"Bearer {token}"}


def test_register_and_login_issue_session_tokens():
    reg = client.post("/api/auth/register", json={"email": "sess-reg@example.com", "password": "S3cur3Pass!"}).json()
    assert reg["refresh_token"] and reg["expires_in"] == 15 * 60
    assert decode_access_token(reg["access_token"])["sid"]

    login = _login("sess-reg@example.com").json()
    assert login["refresh_token"] != reg["refresh_token"]
    sessions = client.get("/api/auth/sessions", headers=_headers(login["access_token"])).json()
    assert len(sessions) == 2 and sum(1 for s in sessions if s["current"]) == 1


def test_access_token_without_session_is_rejected():
    _register("sess-nosid@example.com")

    async def user_id():
        async with _session_factory() as s:
            return (await s.scalar(select(User).where(User.email == "sess-nosid@example.com"))).id
    bare = create_access_token(_run(user_id()), "sess-nosid@example.com")  # no sid
    assert client.get("/api/auth/me", headers=_headers(bare)).status_code == 401


def test_refresh_rotates_and_reuse_revokes_the_session():
    body = client.post("/api/auth/register", json={"email": "sess-rot@example.com", "password": "S3cur3Pass!"}).json()
    first_refresh = body["refresh_token"]

    rotated = client.post("/api/auth/refresh", json={"refresh_token": first_refresh})
    assert rotated.status_code == 200
    second_refresh = rotated.json()["refresh_token"]
    assert second_refresh != first_refresh
    assert client.get("/api/auth/me", headers=_headers(rotated.json()["access_token"])).status_code == 200

    # Presenting the already-rotated token again = two holders of one credential -> session dies.
    reuse = client.post("/api/auth/refresh", json={"refresh_token": first_refresh})
    assert reuse.status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": second_refresh}).status_code == 401
    assert client.get("/api/auth/me", headers=_headers(rotated.json()["access_token"])).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": "garbage"}).status_code == 401


def test_logout_invalidates_access_token_immediately():
    body = client.post("/api/auth/register", json={"email": "sess-logout@example.com", "password": "S3cur3Pass!"}).json()
    headers = _headers(body["access_token"])
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]}).status_code == 401


def test_logout_everywhere_and_single_session_revoke():
    _register("sess-all@example.com")
    a = _login("sess-all@example.com").json()
    b = _login("sess-all@example.com").json()
    c = _login("sess-all@example.com").json()

    listed = client.get("/api/auth/sessions", headers=_headers(a["access_token"])).json()
    other = next(s for s in listed if not s["current"])
    assert client.delete(f"/api/auth/sessions/{other['id']}", headers=_headers(a["access_token"])).status_code == 204
    assert len(client.get("/api/auth/sessions", headers=_headers(a["access_token"])).json()) == 3  # register + 3 logins - 1

    assert client.post("/api/auth/logout-all", headers=_headers(a["access_token"])).status_code == 204
    for tok in (a, b, c):
        assert client.get("/api/auth/me", headers=_headers(tok["access_token"])).status_code == 401
    # Can't revoke someone else's session.
    other_user = client.post("/api/auth/register", json={"email": "sess-other@example.com", "password": "S3cur3Pass!"}).json()
    assert client.delete(f"/api/auth/sessions/{other['id']}", headers=_headers(other_user["access_token"])).status_code == 404


def test_expired_session_is_rejected():
    body = client.post("/api/auth/register", json={"email": "sess-exp@example.com", "password": "S3cur3Pass!"}).json()
    sid = decode_access_token(body["access_token"])["sid"]

    async def expire():
        async with _session_factory() as s:
            rec = await s.get(UserSessionRecord, sid)
            rec.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await s.commit()
    _run(expire())
    assert client.get("/api/auth/me", headers=_headers(body["access_token"])).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]}).status_code == 401


def test_removed_member_sessions_are_revoked_and_owner_can_log_member_out():
    owner = client.post("/api/auth/register", json={"email": "sess-owner@example.com", "password": "S3cur3Pass!"}).json()
    owner_headers = _headers(owner["access_token"])
    me = client.get("/api/auth/me", headers=owner_headers).json()

    async def upgrade():
        from app.db.models import Tenant
        async with _session_factory() as s:
            (await s.get(Tenant, me["tenant_id"])).plan = "pro"
            await s.commit()
    _run(upgrade())

    from urllib.parse import parse_qs, urlparse
    inv = client.post("/api/team/invites", headers=owner_headers, json={"email": "sess-member@example.com", "role": "USER"}).json()
    token = parse_qs(urlparse(inv["invite_url"]).query)["invite"][0]
    member = client.post(f"/api/auth/invite/{token}/accept", json={"password": "S3cur3Pass!"}).json()
    assert member["refresh_token"]
    member_headers = _headers(member["access_token"])
    member_id = client.get("/api/auth/me", headers=member_headers).json()["id"]

    assert client.post(f"/api/team/members/{member_id}/logout-all", headers=owner_headers).status_code == 204
    assert client.get("/api/auth/me", headers=member_headers).status_code == 401
    relogin = _login("sess-member@example.com").json()
    assert client.get("/api/auth/me", headers=_headers(relogin["access_token"])).status_code == 200

    assert client.delete(f"/api/team/members/{member_id}", headers=owner_headers).status_code == 204
    assert client.get("/api/auth/me", headers=_headers(relogin["access_token"])).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": relogin["refresh_token"]}).status_code == 401


def test_session_records_client_ip_and_agent():
    body = client.post("/api/auth/register", json={"email": "sess-ua@example.com", "password": "S3cur3Pass!"},
                       headers={"User-Agent": "TestBrowser/1.0"}).json()
    listed = client.get("/api/auth/sessions", headers=_headers(body["access_token"])).json()
    assert listed[0]["user_agent"] == "TestBrowser/1.0" and listed[0]["ip_address"]


def test_refresh_token_is_only_stored_hashed():
    body = client.post("/api/auth/register", json={"email": "sess-hash@example.com", "password": "S3cur3Pass!"}).json()

    async def stored():
        async with _session_factory() as s:
            return await s.get(UserSessionRecord, decode_access_token(body["access_token"])["sid"])
    rec = _run(stored())
    assert rec.refresh_token_hash == sess.hash_refresh_token(body["refresh_token"])
    assert body["refresh_token"] not in rec.refresh_token_hash
