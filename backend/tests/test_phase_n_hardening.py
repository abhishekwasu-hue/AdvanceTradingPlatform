"""Phase N: per-tenant envelope encryption, fine-grained scopes, email verification, feature flags,
the migration-hour guard and the platform mailer seam."""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.auth import scopes, verification
from app.core import config
from app.db.models import AlertChannelRecord, BrokerCredentialRecord, EmailVerificationRecord, PlatformControlRecord, TenantKeyRecord, User
from app.market_data.calendar import IST, session_status
from app.notifications import mailer
from app.platform import controls
from app.secrets_store import encryption, envelope
from tests.test_admin_api import _admin
from tests.test_auth_api import _register, _session_factory, client
from tests.test_deployments_api import _store_broker
from tests.test_mfa import _set_tenant
from tests.test_team_api import _accept, _invite
from app.auth.routes import verify_rate_limit
from app.main import app

app.dependency_overrides[verify_rate_limit] = lambda: None

STRATEGY = "ema_rsi_scalper_1m"


def _run(coro):
    return asyncio.run(coro)


def _owner(email: str, plan: str = "pro"):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = _set_tenant(headers, plan=plan)
    return headers, me


def _reset_controls():
    async def go():
        async with _session_factory() as session:
            for row in await session.scalars(select(PlatformControlRecord)):
                await session.delete(row)
            await session.commit()
    _run(go())


# --- N1: envelope encryption ------------------------------------------------------------------------

def test_register_creates_tenant_key_and_credentials_use_it():
    headers, me = _owner("n1-owner@example.com")

    async def check():
        async with _session_factory() as session:
            key = await session.get(TenantKeyRecord, me["tenant_id"])
            assert key is not None and key.key_version == 1
            # The stored wrapped key is not the raw key and unwraps under the master.
            raw = encryption.master_fernet().decrypt(key.wrapped_key.encode())
            Fernet(raw)  # valid Fernet key
            assert key.wrapped_key != raw.decode()
    _run(check())

    assert client.post("/api/broker/zerodha/credentials", headers=headers, json={"api_key": "k1", "access_token": "t1"}).status_code == 204

    async def stored():
        async with _session_factory() as session:
            rec = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == me["tenant_id"]))
            return rec.encrypted_payload
    payload = _run(stored())
    assert payload.startswith(f"t1:{me['tenant_id']}:")
    assert encryption.is_tenant_encrypted(payload)
    assert json.loads(encryption.decrypt_text(payload))["api_key"] == "k1"
    # Plain master-key decryption cannot read it (it is a different key)
    with pytest.raises(Exception):
        encryption.master_fernet().decrypt(payload.split(":", 2)[2].encode())


def test_legacy_master_encrypted_secret_still_decrypts():
    legacy = encryption.master_fernet().encrypt(b"old-secret").decode()
    assert not encryption.is_tenant_encrypted(legacy)
    assert encryption.decrypt_text(legacy) == "old-secret"


def test_encrypt_falls_back_to_master_when_tenant_key_not_loaded():
    token = encryption.encrypt_text("x", tenant_id=987654)  # never registered
    assert not encryption.is_tenant_encrypted(token)
    assert encryption.decrypt_text(token) == "x"


def test_tenant_keys_are_isolated_and_missing_key_is_a_clear_error():
    _, a = _owner("n1-a@example.com")
    _, b = _owner("n1-b@example.com")
    token_a = encryption.encrypt_text("secret-a", a["tenant_id"])
    assert encryption.decrypt_text(token_a) == "secret-a"
    # Re-label as tenant b's token: the ciphertext does not verify under b's key
    forged = f"t1:{b['tenant_id']}:" + token_a.split(":", 2)[2]
    with pytest.raises(ValueError):
        encryption.decrypt_text(forged)
    envelope.forget(a["tenant_id"])
    with pytest.raises(ValueError, match="not loaded"):
        encryption.decrypt_text(token_a)

    async def reload():
        async with _session_factory() as session:
            await envelope.ensure_tenant_key(session, a["tenant_id"])
    _run(reload())
    assert encryption.decrypt_text(token_a) == "secret-a"


def test_reencrypt_upgrades_legacy_rows_and_rotate_master_rewraps():
    headers, me = _owner("n1-rotate@example.com")
    legacy = encryption.master_fernet().encrypt(json.dumps({"api_key": "legacy", "access_token": "t"}).encode()).decode()

    async def seed():
        async with _session_factory() as session:
            session.add(BrokerCredentialRecord(tenant_id=me["tenant_id"], user_id=me["id"], broker_name="upstox", account_label="primary",
                                               encrypted_payload=legacy))
            session.add(AlertChannelRecord(tenant_id=me["tenant_id"], channel_type="TELEGRAM", created_by=me["id"],
                                           encrypted_config=encryption.master_fernet().encrypt(b'{"bot_token":"b","chat_id":"c"}').decode()))
            await session.commit()
            before = await envelope.status(session)
            counts = await envelope.reencrypt_tenant(session, me["tenant_id"])
            after = await envelope.status(session)
            rec = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == me["tenant_id"]))
            return before, counts, after, rec.encrypted_payload
    before, counts, after, payload = _run(seed())
    assert counts["broker_credentials"] == 1 and counts["alert_channels"] == 1
    assert after["secrets_legacy"] < before["secrets_legacy"]
    assert payload.startswith("t1:") and json.loads(encryption.decrypt_text(payload))["api_key"] == "legacy"

    # Master rotation: simulate the previous master being a different key.
    old_master = Fernet.generate_key().decode()

    async def rotate():
        async with _session_factory() as session:
            key = await session.get(TenantKeyRecord, me["tenant_id"])
            raw = encryption.master_fernet().decrypt(key.wrapped_key.encode())
            key.wrapped_key = Fernet(old_master.encode()).encrypt(raw).decode()  # as if wrapped by the old master
            await session.commit()
            envelope.forget(me["tenant_id"])
            with pytest.raises(ValueError, match="rotate-master"):
                await envelope.ensure_tenant_key(session, me["tenant_id"])
            n = await envelope.rotate_master(session, old_master)
            await session.refresh(key)
            return n, key.key_version, key.rotated_at
    n, version, rotated_at = _run(rotate())
    assert n >= 1 and version == 2 and rotated_at is not None
    assert json.loads(encryption.decrypt_text(payload))["api_key"] == "legacy"  # still readable after rotation


def test_admin_encryption_status_endpoint():
    headers, _ = _admin("n1-admin@example.com")
    r = client.get("/api/system/encryption", headers=headers)
    assert r.status_code == 200
    assert {"tenant_keys", "secrets_total", "secrets_legacy", "keys_loaded"} <= set(r.json())
    owner_headers, _ = _owner("n1-notadmin@example.com")
    assert client.get("/api/system/encryption", headers=owner_headers).status_code == 403


# --- N2: scopes -------------------------------------------------------------------------------------

def test_me_exposes_scopes_and_catalogue_lists_roles():
    headers, me = _owner("n2-owner@example.com")
    assert "trading:write" in me["scopes"] and "team:manage" in me["scopes"] and "admin:platform" not in me["scopes"]
    cat = client.get("/api/auth/scopes", headers=headers).json()
    entry = next(c for c in cat if c["scope"] == "trading:live")
    assert "OWNER" in entry["roles"] and "VIEWER" not in entry["roles"]


def test_owner_can_deny_scope_and_it_bites_on_trading_writes():
    owner_headers, owner = _owner("n2-deny-owner@example.com")
    invite = _invite(owner_headers, "n2-deny-member@example.com", role="USER").json()
    member_token = _accept(invite["invite_url"]).json()["access_token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}
    member = client.get("/api/auth/me", headers=member_headers).json()
    assert "trading:write" in member["scopes"] and "team:manage" not in member["scopes"]

    r = client.put(f"/api/team/members/{member['id']}/scopes", headers=owner_headers, json={"deny": ["trading:write", "trading:live"]})
    assert r.status_code == 200 and "trading:write" not in r.json()["scopes"]
    assert r.json()["overrides"] == {"deny": ["trading:live", "trading:write"], "grant": []}

    _store_broker(owner_headers)
    denied = client.post("/api/deployments", headers=member_headers, json={"strategy_id": STRATEGY, "symbol": "reliance"})
    assert denied.status_code == 403 and denied.headers.get("X-Missing-Scope") == "trading:write"
    # Reads still work
    assert client.get("/api/deployments", headers=member_headers).status_code == 200
    # Member cannot edit scopes (needs team:manage)
    assert client.put(f"/api/team/members/{owner['id']}/scopes", headers=member_headers, json={"deny": []}).status_code == 403
    # Lifting the denial restores it
    assert client.put(f"/api/team/members/{member['id']}/scopes", headers=owner_headers, json={"deny": []}).status_code == 200
    assert client.post("/api/deployments", headers=member_headers, json={"strategy_id": STRATEGY, "symbol": "reliance"}).status_code == 201


def test_scope_override_validation():
    owner_headers, owner = _owner("n2-validate@example.com")
    invite = _invite(owner_headers, "n2-validate-viewer@example.com", role="VIEWER").json()
    viewer_id = client.get("/api/auth/me", headers={"Authorization": f"Bearer {_accept(invite['invite_url']).json()['access_token']}"}).json()["id"]
    assert client.put(f"/api/team/members/{viewer_id}/scopes", headers=owner_headers, json={"deny": ["nope:x"]}).status_code == 400
    assert client.put(f"/api/team/members/{viewer_id}/scopes", headers=owner_headers, json={"grant": ["admin:platform"]}).status_code == 400
    assert client.put(f"/api/team/members/{viewer_id}/scopes", headers=owner_headers, json={"deny": ["risk:write"], "grant": ["risk:write"]}).status_code == 400
    assert client.put(f"/api/team/members/{owner['id']}/scopes", headers=owner_headers, json={"deny": ["team:manage"]}).status_code == 409
    # Granting a viewer trading:write works and shows in their scopes
    r = client.put(f"/api/team/members/{viewer_id}/scopes", headers=owner_headers, json={"grant": ["trading:write"]})
    assert r.status_code == 200 and "trading:write" in r.json()["scopes"]


def test_scope_matrix_pure_functions():
    u = User(id=1, tenant_id=1, email="x", hashed_password="h", role="VIEWER")
    assert scopes.scopes_for(u) == ["analytics:read"]
    u.scope_overrides = json.dumps({"grant": ["trading:write"], "deny": ["analytics:read"]})
    assert scopes.scopes_for(u) == ["trading:write"]
    sa = User(id=2, tenant_id=1, email="y", hashed_password="h", role="SUPER_ADMIN", scope_overrides=json.dumps({"deny": ["trading:write"]}))
    assert scopes.has_scope(sa, "trading:write") and scopes.has_scope(sa, "admin:platform")
    assert scopes.parse_overrides("not json") == {"deny": [], "grant": []}


# --- N3: email verification ------------------------------------------------------------------------

@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(config, "PLATFORM_SMTP_HOST", "smtp.test")
    monkeypatch.setattr(config, "PLATFORM_SMTP_FROM", "noreply@test")
    monkeypatch.setattr(mailer, "_deliver", lambda message: sent.append(message))
    return sent


def _verify_token_from(message) -> str:
    body = message.get_content()
    link = next(line for line in body.splitlines() if "?verify=" in line)
    return parse_qs(urlparse(link.strip()).query)["verify"][0]


def test_register_sends_verification_and_link_verifies(outbox):
    headers, me = _owner("n3-new@example.com")
    assert me["email_verified"] is False
    assert len(outbox) == 1 and outbox[0]["To"] == "n3-new@example.com"
    token = _verify_token_from(outbox[0])
    r = client.post(f"/api/auth/verify-email/{token}")
    assert r.status_code == 200 and r.json()["email_verified"] is True
    assert client.get("/api/auth/me", headers=headers).json()["email_verified"] is True
    # Single use
    assert client.post(f"/api/auth/verify-email/{token}").status_code == 400
    assert client.post("/api/auth/verify-email/garbage").status_code == 400
    assert client.post("/api/auth/verify-email/resend", headers=headers).json()["already_verified"] is True


def test_resend_and_expiry(outbox):
    headers, me = _owner("n3-resend@example.com")
    r = client.post("/api/auth/verify-email/resend", headers=headers)
    assert r.status_code == 200 and r.json()["sent"] is True and len(outbox) == 2
    token = _verify_token_from(outbox[1])

    async def expire():
        async with _session_factory() as session:
            rec = await session.scalar(select(EmailVerificationRecord).where(EmailVerificationRecord.token_hash == verification._hash(token)))
            rec.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            await session.commit()
    _run(expire())
    assert "expired" in client.post(f"/api/auth/verify-email/{token}").json()["detail"]


def test_without_mailer_registration_still_works_and_resend_reports_it():
    headers, me = _owner("n3-nomail@example.com")
    r = client.post("/api/auth/verify-email/resend", headers=headers)
    assert r.status_code == 200 and r.json() == {"sent": False, "mailer_configured": False, "expires_in_hours": 24, "already_verified": False}


def test_verification_gate_blocks_live_and_credentials_when_required(monkeypatch):
    headers, me = _owner("n3-gate@example.com")
    _store_broker(headers, token_status="VALID")
    monkeypatch.setattr(config, "EMAIL_VERIFICATION_REQUIRED", True)
    r = client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": "k", "api_secret": "s", "access_token": "t"})
    assert r.status_code == 403 and r.headers.get("X-Step-Up") == "email"
    live = client.post("/api/deployments", headers=headers, json={"strategy_id": STRATEGY, "symbol": "reliance", "mode": "LIVE", "broker_name": "upstox"})
    assert live.status_code == 403 and live.headers.get("X-Step-Up") == "email"
    # PAPER is unaffected
    assert client.post("/api/deployments", headers=headers, json={"strategy_id": STRATEGY, "symbol": "reliance"}).status_code == 201
    # Admin stamps it verified -> the credential store proceeds
    admin_headers, _ = _admin("n3-gate-admin@example.com")
    assert client.post(f"/api/team/members/{me['id']}/verify-email", headers=admin_headers).status_code == 200
    assert client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": "k", "api_secret": "s", "access_token": "t"}).status_code == 204


def test_invite_acceptance_counts_as_verified():
    owner_headers, _ = _owner("n3-inviter@example.com")
    invite = _invite(owner_headers, "n3-invitee@example.com", role="USER").json()
    token = _accept(invite["invite_url"]).json()["access_token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["email_verified"] is True


def test_password_reset_falls_back_to_platform_mailer(outbox):
    headers, me = _owner("n3-reset@example.com")
    outbox.clear()
    r = client.post("/api/auth/password/forgot", json={"email": "n3-reset@example.com"})
    assert r.status_code in (200, 202)
    assert len(outbox) == 1 and "Reset" in outbox[0]["Subject"]


# --- N4: feature flags + migration guard --------------------------------------------------------

def test_feature_flags_default_on_and_admin_can_kill_or_allowlist():
    _reset_controls()
    admin_headers, admin = _admin("n4-admin@example.com")
    owner_headers, owner = _owner("n4-owner@example.com")
    flags = client.get("/api/admin/controls/flags", headers=admin_headers).json()
    assert flags["ai_copilot"]["on"] is True and set(flags) == set(controls.FEATURE_FLAGS)
    features = client.get("/api/system/features", headers=owner_headers).json()
    # Kill flags are on by default; the few DEFAULT_OFF_FLAGS (news feed, Telegram inbound, market thesis) start off.
    assert all(v for k, v in features.items() if k not in controls.DEFAULT_OFF_FLAGS) and features["news_feed"] is False

    # Kill the optimizer platform-wide -> 503 with a machine-readable header
    r = client.put("/api/admin/controls/flags/backtest_optimizer", headers=admin_headers, json={"on": False})
    assert r.status_code == 200 and r.json()["backtest_optimizer"]["on"] is False
    assert client.get("/api/system/features", headers=owner_headers).json()["backtest_optimizer"] is False
    from tests.utils import make_series, noisy_uptrend
    df = make_series(noisy_uptrend(220))
    candles = [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for ts, r in df.iterrows()]
    body = {"strategy_id": STRATEGY, "symbol": "RELIANCE", "base_timeframe": "1min", "candles": candles, "param_grid": {"rsi_period": [7, 14]}}
    blocked = client.post("/api/backtest/optimize", headers=owner_headers, json=body)
    assert blocked.status_code == 503 and blocked.headers.get("X-Feature-Disabled") == "backtest_optimizer"

    # Allow-list this tenant -> works for them, still off for others
    client.put("/api/admin/controls/flags/backtest_optimizer", headers=admin_headers, json={"on": False, "tenants": [owner["tenant_id"]]})
    assert client.get("/api/system/features", headers=owner_headers).json()["backtest_optimizer"] is True
    other_headers, _ = _owner("n4-other@example.com")
    assert client.get("/api/system/features", headers=other_headers).json()["backtest_optimizer"] is False

    assert client.put("/api/admin/controls/flags/nonsense", headers=admin_headers, json={"on": False}).status_code == 400
    assert client.put("/api/admin/controls/flags/ai_copilot", headers=owner_headers, json={"on": False}).status_code == 403
    _reset_controls()


def test_live_trading_flag_blocks_live_not_paper_and_self_signup_flag():
    _reset_controls()
    admin_headers, _ = _admin("n4-live-admin@example.com")
    headers, me = _owner("n4-live-owner@example.com")
    _store_broker(headers, token_status="VALID")
    client.put("/api/admin/controls/flags/live_trading", headers=admin_headers, json={"on": False})
    live = client.post("/api/deployments", headers=headers, json={"strategy_id": STRATEGY, "symbol": "reliance", "mode": "LIVE", "broker_name": "upstox"})
    assert live.status_code == 503 and live.headers.get("X-Feature-Disabled") == "live_trading"
    assert client.post("/api/deployments", headers=headers, json={"strategy_id": STRATEGY, "symbol": "reliance"}).status_code == 201
    client.put("/api/admin/controls/flags/self_signup", headers=admin_headers, json={"on": False})
    assert client.post("/api/auth/register", json={"email": "n4-blocked@example.com", "password": "S3cur3Pass!"}).status_code == 403
    _reset_controls()
    assert client.post("/api/auth/register", json={"email": "n4-allowed@example.com", "password": "S3cur3Pass!"}).status_code == 201


def test_flag_helpers_pure():
    async def go():
        async with _session_factory() as session:
            assert await controls.flag_enabled(session, "does_not_exist") is True
            assert (await controls.features_for_tenant(session, None))["marketplace"] is True
    _run(go())


def test_migration_guard_decision_uses_market_calendar(tmp_path, monkeypatch):
    import importlib.util
    import os
    guard_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "migrate_guard.py")
    spec = importlib.util.spec_from_file_location("migrate_guard", guard_path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)

    # Pure calendar decision the guard relies on
    open_now = datetime(2026, 9, 28, 11, 0, tzinfo=IST)  # Monday 11:00 IST
    closed_now = datetime(2026, 9, 28, 17, 0, tzinfo=IST)
    assert session_status(open_now).is_open and not session_status(closed_now).is_open
    assert not session_status(open_now, holidays=[date(2026, 9, 28)]).is_open

    calls = []
    monkeypatch.setattr(guard, "pending_migrations", lambda: True)
    monkeypatch.setattr(guard, "load_holidays", _coro(()))
    monkeypatch.setattr(guard.subprocess, "call", lambda cmd: calls.append(cmd) or 0)

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return open_now.astimezone(tz or timezone.utc)
    monkeypatch.setattr(guard, "datetime", _Now)
    monkeypatch.setattr("sys.argv", ["migrate_guard.py"])
    monkeypatch.delenv("MIGRATION_FORCE", raising=False)
    assert guard.main() == 3 and calls == []
    monkeypatch.setattr("sys.argv", ["migrate_guard.py", "--force"])
    assert guard.main() == 0 and calls and calls[0][-2:] == ["upgrade", "head"]

    class _Closed(datetime):
        @classmethod
        def now(cls, tz=None):
            return closed_now.astimezone(tz or timezone.utc)
    monkeypatch.setattr(guard, "datetime", _Closed)
    monkeypatch.setattr("sys.argv", ["migrate_guard.py", "--dry-run"])
    assert guard.main() == 0
    monkeypatch.setattr(guard, "pending_migrations", lambda: False)
    monkeypatch.setattr(guard, "datetime", _Now)
    monkeypatch.setattr("sys.argv", ["migrate_guard.py"])
    assert guard.main() == 0  # nothing pending: a restart during market hours is fine


def _coro(value):
    async def _inner():
        return value
    return _inner
