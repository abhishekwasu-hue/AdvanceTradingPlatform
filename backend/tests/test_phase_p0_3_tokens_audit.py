"""P0.3 (pro-grade upgrade plan, P0 security): access tokens with iss/aud/kid and secret rotation plus legacy
acceptance and the 72-byte bcrypt rule (S14); the refresh token in an HttpOnly cookie, body optional (S6); the
audit chain's append lock, daily anchors, range verification and RESTRICT foreign keys (S7); API keys resolved
by hash, the Redis day counter for usage and the hashed TradingView token with owner-only rotation (S13)."""
import asyncio
import hashlib
from datetime import datetime, timezone

import jwt
import pytest
from sqlalchemy import select

from app.audit import log as audit
from app.auth import security
from app.billing import service as billing
from app.core import config
from app.db.models import ApiKeyRecord, AuditAnchorRecord, AuditLogRecord, Tenant
from app.public_api import keys
from app.webhooks.routes import hash_webhook_token
from tests.test_auth_api import _register, _session_factory, client


def _run(coro):
    return asyncio.run(coro)


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_access_tokens_carry_issuer_audience_and_key_id_and_rotate(monkeypatch):
    token = security.create_access_token(7, "someone@example.com", 3)
    header, claims = jwt.get_unverified_header(token), jwt.decode(token, options={"verify_signature": False}, audience=config.JWT_AUDIENCE)
    assert header["kid"] == security.key_id(config.JWT_SECRET_KEY) and claims["iss"] == config.JWT_ISSUER and claims["aud"] == config.JWT_AUDIENCE
    assert "email" not in claims and claims["sub"] == "7" and claims["sid"] == 3
    assert security.decode_access_token(token)["sub"] == "7"
    # Wrong audience / issuer: refused even with the right secret.
    for bad in ({"iss": "other"}, {"aud": "other-app"}):
        forged = jwt.encode({**claims, **bad}, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)
        with pytest.raises(jwt.PyJWTError):
            security.decode_access_token(forged)
    # Rotation: the old secret moves to JWT_PREVIOUS_SECRET_KEYS and its tokens keep verifying until they expire.
    old_secret = config.JWT_SECRET_KEY
    monkeypatch.setattr(config, "JWT_SECRET_KEY", "brand-new-secret-after-rotation")
    monkeypatch.setattr(config, "JWT_PREVIOUS_SECRET_KEYS", [old_secret])
    assert security.decode_access_token(token)["sub"] == "7"                      # signed with the previous secret
    fresh = security.create_access_token(8)
    assert jwt.get_unverified_header(fresh)["kid"] == security.key_id("brand-new-secret-after-rotation")
    monkeypatch.setattr(config, "JWT_PREVIOUS_SECRET_KEYS", [])
    with pytest.raises(jwt.PyJWTError):
        security.decode_access_token(token)                                       # retired secret: gone for good
    # Legacy (pre-P0.3) tokens without iss/aud are accepted only while JWT_ACCEPT_LEGACY is on.
    legacy = jwt.encode({"sub": "9", "email": "x@example.com", "exp": datetime.now(timezone.utc).timestamp() + 60},
                        "brand-new-secret-after-rotation", algorithm=config.JWT_ALGORITHM)
    assert security.decode_access_token(legacy)["sub"] == "9"
    monkeypatch.setattr(config, "JWT_ACCEPT_LEGACY", False)
    with pytest.raises(jwt.PyJWTError):
        security.decode_access_token(legacy)


def test_passwords_longer_than_72_bytes_are_refused_not_silently_truncated():
    from app.auth.passwords import password_problem
    assert password_problem("x" * 72 + "A1!") is not None and "72 bytes" in password_problem("x" * 80)
    assert password_problem("\u0905" * 30) is not None                            # 30 Devanagari chars = 90 bytes
    with pytest.raises(ValueError):
        security.hash_password("x" * 73)
    # Pre-P0.3 hashes were made by bcrypt truncating at 72 bytes: verification keeps doing so, nobody is locked out.
    assert security.verify_password("x" * 73, security.hash_password("x" * 72)) is True
    assert client.post("/api/auth/register", json={"email": "p03-long@example.com", "password": "Str0ng!" + "x" * 70}).status_code == 400


def test_refresh_token_rides_in_an_httponly_cookie_and_the_body_is_optional():
    res = client.post("/api/auth/register", json={"email": "p03-cookie@example.com", "password": "S3cur3Pass!"})
    assert res.status_code == 201, res.text
    cookie = res.cookies.get(config.REFRESH_COOKIE_NAME)
    assert cookie and cookie == res.json()["refresh_token"]                        # REFRESH_TOKEN_IN_BODY stays on this release
    raw = next(h for h in res.headers.get_list("set-cookie") if h.startswith(config.REFRESH_COOKIE_NAME))
    assert "HttpOnly" in raw and "SameSite=strict" in raw and "Path=/api" in raw
    # Refresh with the cookie alone (no body) rotates and re-sets the cookie.
    client.cookies.set(config.REFRESH_COOKIE_NAME, cookie, path="/api")
    rotated = client.post("/api/auth/refresh", json={})
    assert rotated.status_code == 200, rotated.text
    new_cookie = rotated.cookies.get(config.REFRESH_COOKIE_NAME)
    assert new_cookie and new_cookie != cookie
    assert client.get("/api/auth/me", headers=_headers(rotated.json()["access_token"])).status_code == 200
    # The body still works (older UI builds) and sets the cookie as well.
    client.cookies.clear()
    via_body = client.post("/api/auth/refresh", json={"refresh_token": new_cookie})
    assert via_body.status_code == 200 and via_body.cookies.get(config.REFRESH_COOKIE_NAME)
    # Logout clears the cookie and ends the session; a refresh without any token is a clean 401.
    client.cookies.clear()
    out = client.post("/api/auth/logout", headers=_headers(via_body.json()["access_token"]))
    assert out.status_code == 204 and any("Max-Age=0" in h or "expires" in h.lower() for h in out.headers.get_list("set-cookie"))
    assert client.post("/api/auth/refresh", json={}).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": via_body.json()["refresh_token"]}).status_code == 401
    client.cookies.clear()


def test_audit_chain_anchors_and_range_verification():
    async def go():
        async with _session_factory() as session:
            for i in range(3):
                await audit.write_audit_log(session, None, None, "p03_anchor_test", f"row {i}")
            await session.commit()
            first = await audit.record_anchor(session)
            await session.commit()
            assert first is not None and first.head_hash == (await session.scalar(
                select(AuditLogRecord).order_by(AuditLogRecord.id.desc()).limit(1))).hash
            assert await audit.record_anchor(session) is None                          # chain did not grow: no new anchor
            await audit.write_audit_log(session, None, None, "p03_anchor_test", "after anchor")
            await session.commit()
            assert await audit.verify_audit_chain(session, since_anchor=True) == (True, None)
            whole, whole_broken = await audit.verify_audit_chain(session)
            assert whole or whole_broken < first.last_id - 2       # another test module may have left an earlier break
            # Tamper with the anchored row's fields (its stored hash untouched): the anchored check recomputes it.
            anchored = await session.get(AuditLogRecord, first.last_id)
            original = anchored.detail
            try:
                anchored.detail = "rewritten"
                await session.commit()
                intact, broken = await audit.verify_audit_chain(session, since_anchor=True)
                assert intact is False and broken == first.last_id
            finally:
                anchored.detail = original
                await session.commit()
            assert (await audit.verify_audit_chain(session, since_anchor=True))[0] is True
            # Tamper after the anchor: caught by the range check too.
            last = await session.scalar(select(AuditLogRecord).order_by(AuditLogRecord.id.desc()).limit(1))
            try:
                last.event = "changed"
                await session.commit()
                assert await audit.verify_audit_chain(session, since_anchor=True) == (False, last.id)
            finally:
                last.event = "p03_anchor_test"
                await session.commit()
            whole, whole_broken = await audit.verify_audit_chain(session)
            assert whole or whole_broken < first.last_id - 2
            anchors = list(await session.scalars(select(AuditAnchorRecord)))
            assert len(anchors) >= 1
    _run(go())
    # The append lock is a Postgres-only statement; on SQLite it is a no-op and the write still chains.
    from app.db.models import AuditLogRecord as _A
    assert next(iter(_A.__table__.c.tenant_id.foreign_keys)).ondelete == "RESTRICT"


def test_api_keys_resolve_by_hash_so_a_shared_prefix_cannot_hide_a_key():
    headers = _headers(_register("p03-keys@example.com"))
    plain1, prefix1, hash1 = keys.generate_key()
    plain2, _, hash2 = keys.generate_key()
    me = client.get("/api/auth/me", headers=headers).json()

    async def seed():
        async with _session_factory() as session:
            # Two keys that share the display prefix (a 4-byte prefix will collide eventually).
            session.add(ApiKeyRecord(tenant_id=me["tenant_id"], user_id=me["id"], name="a", key_prefix=prefix1, key_hash=hash1, scopes="read:account",
                                     rate_limit_per_minute=60))
            session.add(ApiKeyRecord(tenant_id=me["tenant_id"], user_id=me["id"], name="b", key_prefix=prefix1, key_hash=hash2, scopes="read:account",
                                     rate_limit_per_minute=60))
            await session.commit()
            second = plain2.split("_", 2)
            spoofed = f"{second[0]}_{prefix1}_{second[2]}"                              # key 2's secret under key 1's prefix
            found1 = await keys.resolve_key(session, plain1)
            found2 = await keys.resolve_key(session, spoofed)
            assert found1 is not None and found1.key_hash == hash1
            assert found2 is None                                                      # hash of the spoofed string matches nothing
            assert await keys.resolve_key(session, "atp_zzzz_nothing") is None and await keys.resolve_key(session, "garbage") is None
    _run(seed())


def test_usage_counter_prefers_redis_and_falls_back_to_the_sum(monkeypatch):
    headers = _headers(_register("p03-usage@example.com"))
    tenant_id = client.get("/api/auth/me", headers=headers).json()["tenant_id"]

    class _Redis:
        def __init__(self):
            self.values, self.ttl = {}, {}
        async def incrbyfloat(self, key, amount):
            self.values[key] = self.values.get(key, 0.0) + amount
            return self.values[key]
        async def expire(self, key, ttl):
            self.ttl[key] = ttl
        async def set(self, key, value, ex=None):
            self.values[key] = float(value)
            self.ttl[key] = ex
        async def get(self, key):
            v = self.values.get(key)
            return None if v is None else str(v)
    fake = _Redis()
    monkeypatch.setattr("app.cache.client._get_client", lambda: fake)

    async def go():
        async with _session_factory() as session:
            await billing.meter(session, tenant_id, "api_call", 1)
            await billing.meter(session, tenant_id, "api_call", 2)
            assert await billing.usage_today(session, tenant_id, "api_call") == 3.0 and list(fake.ttl.values()) == [2 * 86400]
            fake.values.clear()                                                        # Redis lost the key -> the SUM answers
            assert await billing.usage_today(session, tenant_id, "api_call") == 3.0
            # The next meter re-creates the key: it is seeded from the DB total, not from this one call.
            await billing.meter(session, tenant_id, "api_call", 1)
            assert fake.values and await billing.usage_today(session, tenant_id, "api_call") == 4.0
    _run(go())


def test_webhook_token_is_stored_hashed_rotation_is_owner_only_and_legacy_plaintext_still_matches():
    headers = _headers(_register("p03-webhook@example.com"))
    me = client.get("/api/auth/me", headers=headers).json()
    first = client.get("/api/webhooks/tradingview/token", headers=headers).json()
    assert first["webhook_token"] is None and first["webhook_url"] is None and first["configured"] is True
    rotated = client.post("/api/webhooks/tradingview/token/rotate", headers=headers).json()
    token = rotated["webhook_token"]
    assert token and rotated["webhook_url"].endswith(token)
    assert client.get("/api/webhooks/tradingview/token", headers=headers).json()["webhook_token"] is None     # shown once

    async def stored():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            return tenant.webhook_token, tenant.webhook_token_hash
    assert _run(stored()) == (None, hashlib.sha256(token.encode()).hexdigest()) and hash_webhook_token(token) == _run(stored())[1]
    # The hashed token authenticates the TradingView call; a wrong one does not.
    from tests.test_tradingview_webhook import _alert
    alert = _alert(alert_id="p03-alert-1")
    assert client.post(f"/api/webhooks/tradingview/{token}", json=alert).status_code == 200
    assert client.post(f"/api/webhooks/tradingview/{token}x", json=alert).status_code in (401, 404)
    # A pre-P0.3 organisation that still carries the plaintext matches on it once and gets its hash filled in.
    async def legacy():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            tenant.webhook_token, tenant.webhook_token_hash = "legacy-plain-token-0123456789", None
            await session.commit()
    _run(legacy())
    assert client.post("/api/webhooks/tradingview/legacy-plain-token-0123456789", json=_alert(alert_id="p03-alert-2")).status_code == 200
    assert _run(stored())[1] == hash_webhook_token("legacy-plain-token-0123456789")
    assert client.get("/api/webhooks/tradingview/token", headers=headers).json()["webhook_token"] == "legacy-plain-token-0123456789"
    # A trader (not the owner) may not rotate.
    from urllib.parse import parse_qs, urlparse
    from tests.test_team_api import _upgrade_plan
    _upgrade_plan(me["tenant_id"])                                                  # Free plan allows one member
    invite = client.post("/api/team/invites", headers=headers, json={"email": "p03-member@example.com", "role": "USER"})
    assert invite.status_code == 201, invite.text
    invite_token = parse_qs(urlparse(invite.json()["invite_url"]).query)["invite"][0]
    accepted = client.post(f"/api/auth/invite/{invite_token}/accept", json={"password": "S3cur3Pass!Long"})
    assert accepted.status_code == 201, accepted.text
    assert client.post("/api/webhooks/tradingview/token/rotate", headers=_headers(accepted.json()["access_token"])).status_code == 403
    # Telegram inbound names the organisation by the stored hash (or the plaintext token); unknown path -> 401.
    stored_hash = _run(stored())[1]
    assert client.post(f"/api/telegram/webhook/{stored_hash}", json={}).status_code == 403          # found; inbound not configured
    assert client.post("/api/telegram/webhook/not-a-token", json={}).status_code == 401
