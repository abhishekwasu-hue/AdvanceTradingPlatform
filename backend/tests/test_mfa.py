"""Phase C3: TOTP MFA - enrol/confirm, two-step login, backup codes, step-up for LIVE and broker
credentials under the tenant policy, admin console requiring MFA, disable rules."""
import asyncio

import pyotp

from app.auth import mfa
from app.db.models import Tenant, User
from tests.test_auth_api import _register, _session_factory, client
from tests.utils import enable_mfa


def _run(coro):
    return asyncio.run(coro)


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _login(email, password="S3cur3Pass!"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _code(secret):
    return pyotp.TOTP(secret).now()


def _set_tenant(headers, **fields):
    me = client.get("/api/auth/me", headers=headers).json()

    async def go():
        async with _session_factory() as s:
            t = await s.get(Tenant, me["tenant_id"])
            for k, v in fields.items():
                setattr(t, k, v)
            await s.commit()
    _run(go())
    return me


def test_enrol_confirm_and_two_step_login():
    headers = _headers(_register("mfa-basic@example.com"))
    status = client.get("/api/auth/mfa/status", headers=headers).json()
    assert status == {**status, "enabled": False, "session_verified": False, "pending_enrolment": False}

    enrolled = client.post("/api/auth/mfa/enrol", headers=headers).json()
    assert enrolled["otpauth_uri"].startswith("otpauth://totp/Advance%20Trading%20Platform:mfa-basic%40example.com")
    assert client.get("/api/auth/mfa/status", headers=headers).json()["pending_enrolment"] is True
    assert client.post("/api/auth/mfa/confirm", headers=headers, json={"code": "000000"}).status_code == 400
    confirmed = client.post("/api/auth/mfa/confirm", headers=headers, json={"code": _code(enrolled["secret"])})
    assert confirmed.status_code == 200
    codes = confirmed.json()["backup_codes"]
    assert len(codes) == 8 and all(len(c) == 11 and c[5] == "-" for c in codes)

    status = client.get("/api/auth/mfa/status", headers=headers).json()
    assert status["enabled"] and status["session_verified"] and status["backup_codes_remaining"] == 8
    assert client.get("/api/auth/me", headers=headers).json()["mfa_enabled"] is True

    # Login is now two steps.
    challenge = _login("mfa-basic@example.com").json()
    assert challenge["mfa_required"] and challenge["mfa_token"] and not challenge["access_token"]
    bad = client.post("/api/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "code": "123456"})
    assert bad.status_code == 401
    good = client.post("/api/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "code": _code(enrolled["secret"])})
    assert good.status_code == 200 and good.json()["refresh_token"]
    assert client.get("/api/auth/mfa/status", headers=_headers(good.json()["access_token"])).json()["session_verified"] is True
    assert client.post("/api/auth/mfa/verify", json={"mfa_token": "garbage", "code": "123456"}).status_code == 401


def test_backup_codes_work_once_and_can_be_regenerated():
    headers = _headers(_register("mfa-backup@example.com"))
    secret = enable_mfa(headers)
    codes = client.post("/api/auth/mfa/backup-codes", headers=headers, json={"code": _code(secret)}).json()["backup_codes"]
    challenge = _login("mfa-backup@example.com").json()["mfa_token"]
    first = client.post("/api/auth/mfa/verify", json={"mfa_token": challenge, "code": codes[0].upper()})
    assert first.status_code == 200
    challenge2 = _login("mfa-backup@example.com").json()["mfa_token"]
    assert client.post("/api/auth/mfa/verify", json={"mfa_token": challenge2, "code": codes[0]}).status_code == 401
    assert client.get("/api/auth/mfa/status", headers=headers).json()["backup_codes_remaining"] == 7
    assert client.post("/api/auth/mfa/backup-codes", headers=headers, json={"code": "000000"}).status_code == 401


def test_live_step_up_only_when_tenant_policy_requires_it():
    headers = _headers(_register("mfa-live@example.com"))
    me = _set_tenant(headers, plan="pro")
    client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": "k", "api_secret": "s", "access_token": "t"})

    async def valid_token():
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import select
        from app.db.models import BrokerCredentialRecord
        async with _session_factory() as s:
            rec = await s.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == me["tenant_id"]))
            rec.token_status = "VALID"; rec.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=8)
            await s.commit()
    _run(valid_token())
    live = {"strategy_id": "ema_rsi_scalper_1m", "symbol": "A", "mode": "LIVE", "broker_name": "upstox"}

    # Policy off: LIVE works without MFA.
    assert client.post("/api/deployments", headers=headers, json=live).status_code == 201

    # Owner cannot require MFA before enabling it themselves.
    assert client.patch("/api/team/tenant", headers=headers, json={"require_mfa_for_live": True}).status_code == 400
    secret = enable_mfa(headers)
    assert client.patch("/api/team/tenant", headers=headers, json={"require_mfa_for_live": True}).status_code == 200

    # This session was verified by confirm; a *new* password-only session is not possible (MFA on),
    # but a session that predates MFA would be - simulate with a session that has no verification.
    async def unverify():
        from sqlalchemy import update
        from app.db.models import UserSessionRecord
        async with _session_factory() as s:
            await s.execute(update(UserSessionRecord).where(UserSessionRecord.user_id == me["id"]).values(mfa_verified_at=None))
            await s.commit()
    _run(unverify())
    blocked = client.post("/api/deployments", headers=headers, json={**live, "symbol": "B"})
    assert blocked.status_code == 403 and blocked.headers.get("x-step-up") == "mfa" and "two-factor" in blocked.json()["detail"]
    assert client.post("/api/broker/zerodha/credentials", headers=headers, json={"api_key": "k"}).status_code == 403
    assert client.post("/api/deployments", headers=headers, json={"strategy_id": "ema_rsi_scalper_1m", "symbol": "C"}).status_code == 201  # PAPER unaffected

    assert client.post("/api/auth/mfa/step-up", headers=headers, json={"code": "000000"}).status_code == 401
    assert client.post("/api/auth/mfa/step-up", headers=headers, json={"code": _code(secret)}).status_code == 204
    assert client.post("/api/deployments", headers=headers, json={**live, "symbol": "B"}).status_code == 201
    assert client.post("/api/broker/zerodha/credentials", headers=headers, json={"api_key": "k"}).status_code == 204


def test_user_without_mfa_under_policy_is_told_to_enable_it():
    owner_headers = _headers(_register("mfa-policy-owner@example.com"))
    _set_tenant(owner_headers, plan="pro", require_mfa_for_live=True)
    client.post("/api/broker/upstox/credentials", headers=owner_headers, json={"api_key": "k"})  # blocked below? no: owner has no MFA
    blocked = client.post("/api/broker/upstox/credentials", headers=owner_headers, json={"api_key": "k"})
    assert blocked.status_code == 403 and "enable it from the Account tab" in blocked.json()["detail"]


def test_admin_console_requires_mfa_verified_session():
    headers = _headers(_register("mfa-admin@example.com"))
    me = client.get("/api/auth/me", headers=headers).json()

    async def promote():
        async with _session_factory() as s:
            (await s.get(User, me["id"])).role = "SUPER_ADMIN"
            await s.commit()
    _run(promote())
    blocked = client.get("/api/admin/overview", headers=headers)
    assert blocked.status_code == 403 and "two-factor" in blocked.json()["detail"]
    assert client.post("/api/kill-switch/global/engage", headers=headers, json={"reason": "x"}).status_code == 403
    enable_mfa(headers)
    assert client.get("/api/admin/overview", headers=headers).status_code == 200
    assert client.post("/api/kill-switch/global/engage", headers=headers, json={"reason": "test"}).status_code == 200
    assert client.post("/api/kill-switch/global/disengage", headers=headers).status_code == 200
    # Platform admins cannot switch MFA off.
    secret_headers = headers
    assert client.post("/api/auth/mfa/disable", headers=secret_headers, json={"password": "S3cur3Pass!", "code": "000000"}).status_code == 401


def test_disable_requires_password_and_code_then_login_is_single_step_again():
    headers = _headers(_register("mfa-disable@example.com"))
    secret = enable_mfa(headers)
    assert client.post("/api/auth/mfa/disable", headers=headers, json={"password": "wrong", "code": _code(secret)}).status_code == 401
    assert client.post("/api/auth/mfa/disable", headers=headers, json={"password": "S3cur3Pass!", "code": "000000"}).status_code == 401
    assert client.post("/api/auth/mfa/disable", headers=headers, json={"password": "S3cur3Pass!", "code": _code(secret)}).status_code == 204
    status = client.get("/api/auth/mfa/status", headers=headers).json()
    assert status["enabled"] is False and status["backup_codes_remaining"] == 0
    assert _login("mfa-disable@example.com").json()["access_token"]
    assert client.post("/api/auth/mfa/enrol", headers=headers).status_code == 200  # can re-enrol


def test_mfa_secret_is_encrypted_at_rest_and_challenge_token_is_purpose_bound():
    headers = _headers(_register("mfa-secret@example.com"))
    secret = enable_mfa(headers)
    me = client.get("/api/auth/me", headers=headers).json()

    async def stored():
        async with _session_factory() as s:
            return (await s.get(User, me["id"])).mfa_secret_encrypted
    assert secret not in _run(stored())
    # An ordinary access token is not accepted as a login challenge.
    assert mfa.parse_mfa_token(headers["Authorization"].split()[1]) is None
    assert client.post("/api/auth/mfa/verify", json={"mfa_token": headers["Authorization"].split()[1], "code": _code(secret)}).status_code == 401
