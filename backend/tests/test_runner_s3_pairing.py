"""Runner S3 (docs/design/USER_RUNNER.md section 4; flag `user_runner`, OFF): the ATP side of the user's own runner -
pairing (expiry, single use, wrong code), the device signature (bad signature, replay window, replay), heartbeat -> one
offline alert, mirror idempotency + size cap + the user's off switch, revoke, isolation between users and organisations,
no admin / platform path to a runner's kill switch, and the migration."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import select, update

from app.core import rate_limit
from app.db.base import Base
from app.db.models import NotificationRecord, RunnerDeviceRecord, RunnerMirrorAuditRecord, RunnerMirrorOrderRecord, RunnerPairingCodeRecord, User
from app.main import app
from app.platform import controls
from app.runner_link import service, signing
from tests.route_inventory import iter_api_routes
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth, _me

BACKEND = Path(__file__).resolve().parents[1]
HB = "/api/runner/device/heartbeat"
MIRROR = "/api/runner/device/mirror"


def _run(coro):
    return asyncio.run(coro)


def _set_flags(**flags):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            for name, on in flags.items():
                stored[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


@pytest.fixture(autouse=True)
def _flags_on():
    rate_limit.reset()
    _set_flags(ui_v2=True, user_runner=True)
    yield
    _set_flags(user_runner=False)
    rate_limit.reset()


class Runner:
    """A fake runner: its own Ed25519 key, pairing and signed requests, as S4's runner will send them."""

    def __init__(self) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.public_hex = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        self.device_id = ""

    def proof(self, code: str) -> str:
        return base64.urlsafe_b64encode(self.key.sign(signing.pair_message(service.normalise_code(code), self.public_hex))).decode()

    def pair(self, code: str, name: str = "My VPS"):
        resp = client.post("/api/runner/pair", json={"code": code, "public_key": self.public_hex, "proof": self.proof(code),
                                                     "name": name, "version": "0.3.0"})
        if resp.status_code == 201:
            self.device_id = resp.json()["device_id"]
        return resp

    def signed(self, path: str, payload, *, ts_ms: int | None = None, key: Ed25519PrivateKey | None = None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        ts = ts_ms if ts_ms is not None else int(time.time() * 1000)
        sig = (key or self.key).sign(signing.canonical("POST", path, ts, self.device_id, body))
        headers = {signing.HEADER_DEVICE: self.device_id, signing.HEADER_TIMESTAMP: str(ts),
                   signing.HEADER_SIGNATURE: base64.urlsafe_b64encode(sig).decode().rstrip("="), "Content-Type": "application/json"}
        return path, body, headers

    def send(self, path: str, payload, **kw):
        path, body, headers = self.signed(path, payload, **kw)
        return client.post(path, content=body, headers=headers)


BEAT = {"run_state": "running", "version": "0.3.0", "kill_switch_on": False, "open_positions": 2, "broker_session": "valid"}


def _code(headers) -> str:
    resp = client.post("/api/runner/pairing-codes", headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", body["code"]) and body["expires_in_minutes"] == service.PAIRING_CODE_MINUTES
    return body["code"]


def _paired(email: str):
    headers = _auth(email)
    runner = Runner()
    assert runner.pair(_code(headers)).status_code == 201
    return headers, runner


def _device(device_uid: str) -> RunnerDeviceRecord:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(RunnerDeviceRecord).where(RunnerDeviceRecord.device_uid == device_uid))
    return _run(go())


# --- flag ------------------------------------------------------------------------------------------------------------------

def test_off_by_default_and_unfinished_in_the_catalog():
    from app.platform import flag_catalog, flag_state
    assert "user_runner" in controls.FEATURE_FLAGS and "user_runner" in controls.DEFAULT_OFF_FLAGS
    assert flag_catalog.FLAG_INFO["user_runner"].status == "unfinished"
    assert flag_state.FLAG_STATUS["user_runner"].state == "not_built"
    headers = _auth("rs3-off@example.com")
    _set_flags(user_runner=False)
    for method, path in (("POST", "/api/runner/pairing-codes"), ("GET", "/api/runner/devices")):
        resp = client.request(method, path, headers=headers)
        assert resp.status_code == 404 and resp.headers["X-Feature-Disabled"] == "user_runner"


# --- pairing -------------------------------------------------------------------------------------------------------------

def test_pairing_code_is_hashed_single_use_and_pairs_one_device():
    headers = _auth("rs3-pair@example.com")
    code = _code(headers)

    async def stored():
        async with _session_factory() as session:
            return list(await session.scalars(select(RunnerPairingCodeRecord.code_hash)))
    hashes = _run(stored())
    assert hashlib.sha256(service.normalise_code(code).encode()).hexdigest() in hashes
    assert code not in json.dumps(hashes)                                         # never the plain code

    runner = Runner()
    first = runner.pair(code.lower().replace("-", " "))                           # typed loosely: still the same code
    assert first.status_code == 201 and re.fullmatch(r"[0-9a-f]{32}", first.json()["device_id"])
    again = Runner().pair(code)                                                   # single use
    assert again.status_code == 400 and again.json()["detail"]["code"] == "bad_code"
    devices = client.get("/api/runner/devices", headers=headers).json()["devices"]
    assert [d["device_id"] for d in devices] == [runner.device_id] and devices[0]["status"] == "PAIRED"
    assert devices[0]["name"] == "My VPS" and devices[0]["kill_switch_on"] is None


def test_wrong_expired_and_replaced_codes_are_refused_alike():
    headers = _auth("rs3-codes@example.com")
    wrong = Runner().pair("ABCD-EFGH")
    assert wrong.status_code == 400 and wrong.json()["detail"]["code"] == "bad_code"

    old = _code(headers)
    new = _code(headers)                                                          # a new code ends the earlier one
    assert Runner().pair(old).json()["detail"]["code"] == "bad_code"

    async def expire():
        async with _session_factory() as session:
            await session.execute(update(RunnerPairingCodeRecord).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
            await session.commit()
    _run(expire())
    assert Runner().pair(new).json()["detail"]["code"] == "bad_code"


def test_pairing_needs_a_real_key_and_its_proof():
    headers = _auth("rs3-proof@example.com")
    code = _code(headers)
    runner, other = Runner(), Runner()
    bad_proof = client.post("/api/runner/pair", json={"code": code, "public_key": runner.public_hex, "proof": other.proof(code),
                                                      "name": "x", "version": "1"})
    assert bad_proof.status_code == 400 and bad_proof.json()["detail"]["code"] == "bad_proof"
    bad_key = client.post("/api/runner/pair", json={"code": code, "public_key": "00" * 32, "proof": runner.proof(code), "name": "x"})
    assert bad_key.status_code == 400
    assert runner.pair(code).status_code == 201                                   # the failures did not spend the code


def test_pairing_is_rate_limited_per_client():
    codes = [Runner().pair("WXYZ-2345").status_code for _ in range(12)]
    assert codes[:10] == [400] * 10 and codes[10:] == [429, 429]


def test_pairing_into_an_organisation_with_the_flag_off_is_refused():
    headers = _auth("rs3-pair-off@example.com")
    code = _code(headers)
    _set_flags(user_runner=False)
    assert Runner().pair(code).status_code == 404


# --- the device signature -------------------------------------------------------------------------------------------------

def test_signed_heartbeat_is_stored_as_reported():
    headers, runner = _paired("rs3-hb@example.com")
    resp = runner.send(HB, BEAT)
    assert resp.status_code == 200, resp.text
    answer = resp.json()
    assert answer["device_status"] == "ONLINE" and answer["mirror_upload"] is True and answer["commands"] == []
    device = client.get("/api/runner/devices", headers=headers).json()["devices"][0]
    assert device["status"] == "ONLINE" and device["version"] == "0.3.0" and device["open_positions"] == 2
    assert device["kill_switch_on"] is False and device["run_state"] == "running" and device["broker_session"] == "valid"
    assert device["last_heartbeat_at"] is not None
    assert runner.send(HB, {**BEAT, "kill_switch_on": True}).status_code == 200   # the runner's own switch, as reported
    assert client.get("/api/runner/devices", headers=headers).json()["devices"][0]["kill_switch_on"] is True


def test_bad_signature_wrong_device_and_unknown_fields_are_refused():
    _, runner = _paired("rs3-badsig@example.com")
    stranger = Ed25519PrivateKey.generate()
    resp = runner.send(HB, BEAT, key=stranger)
    assert resp.status_code == 401 and resp.json()["detail"]["code"] == "bad_signature"
    path, body, headers = runner.signed(HB, BEAT)
    tampered = client.post(path, content=body.replace(b'"open_positions": 2', b'"open_positions": 9'), headers=headers)
    assert tampered.status_code == 401                                            # the body is signed
    assert client.post(HB, json=BEAT).status_code == 401                          # no signature at all
    ghost = Runner()
    ghost.device_id = "f" * 32
    assert ghost.send(HB, BEAT).status_code == 401
    extra = runner.send(HB, {**BEAT, "api_key": "k"})
    assert extra.status_code == 422                                               # no field the schema does not name


def test_replay_and_stale_timestamps_are_refused():
    _, runner = _paired("rs3-replay@example.com")
    path, body, headers = runner.signed(HB, BEAT)
    assert client.post(path, content=body, headers=headers).status_code == 200
    replay = client.post(path, content=body, headers=headers)
    assert replay.status_code == 401 and replay.json()["detail"]["code"] == "replay"
    now_ms = int(time.time() * 1000)
    older = runner.send(HB, BEAT, ts_ms=now_ms - 5_000)                           # inside the window, but older than the last
    assert older.status_code == 401 and older.json()["detail"]["code"] == "replay"
    outside = runner.send(HB, BEAT, ts_ms=now_ms + (signing.REPLAY_WINDOW_SECONDS + 5) * 1000)
    assert outside.status_code == 401 and outside.json()["detail"]["code"] == "stale"
    assert runner.send(HB, BEAT, ts_ms=now_ms + 1_000).status_code == 200


def _alerts(user_id: int) -> int:
    async def go():
        async with _session_factory() as session:
            return len(list(await session.scalars(select(NotificationRecord.id).where(
                NotificationRecord.user_id == user_id, NotificationRecord.event_type == "RUNNER_OFFLINE"))))
    return _run(go())


def _check(minutes_later: float) -> None:
    """Runs the worker's offline check as if `minutes_later` had passed (other tests' runners share the database)."""
    async def go():
        async with _session_factory() as session:
            await service.check_offline(session, datetime.now(UTC) + timedelta(minutes=minutes_later))
    _run(go())


# --- runner offline -------------------------------------------------------------------------------------------------------

def test_heartbeat_gap_raises_one_offline_alert_and_rearms_on_the_next_heartbeat(monkeypatch):
    headers, runner = _paired("rs3-offline@example.com")
    sent = []

    async def fake_telegram(session, tenant_id, user_id, title, message, client_):
        sent.append((user_id, title))
    monkeypatch.setattr(service, "_telegram", fake_telegram)
    assert runner.send(HB, BEAT).status_code == 200
    me = _me(headers)

    def check(minutes_later: float) -> int:
        before = _alerts(me["id"])
        _check(minutes_later)
        return _alerts(me["id"]) - before
    assert check(2) == 0                                                          # inside his 3 minutes (the default)
    assert check(4) == 1
    assert check(5) == 0 and check(60) == 0                                       # once per offline spell
    assert [s for s in sent if s[0] == me["id"]] == [(me["id"], "Runner offline: My VPS")]
    device = client.get("/api/runner/devices", headers=headers).json()["devices"][0]
    assert device["status"] == "OFFLINE"
    feed = [n for n in client.get("/api/notifications", headers=headers).json() if n["event_type"] == "RUNNER_OFFLINE"]
    assert len(feed) == 1 and "has not reported since" in feed[0]["message"]

    assert runner.send(HB, BEAT).status_code == 200                               # back: re-armed
    assert client.get("/api/runner/devices", headers=headers).json()["devices"][0]["status"] == "ONLINE"
    # His own setting moves the line; Telegram off keeps the alert in the bell only.
    put = client.put("/api/me/prefs/runner", headers=headers, json={"offline_after_minutes": 10, "offline_telegram": False})
    assert put.status_code == 200, put.text
    assert check(5) == 0 and check(11) == 1
    assert len([s for s in sent if s[0] == me["id"]]) == 1


def test_offline_alert_is_the_users_own_and_needs_the_flag():
    headers, runner = _paired("rs3-offline-own@example.com")
    assert runner.send(HB, BEAT).status_code == 200
    tenant_id = _me(headers)["tenant_id"]
    _set_flags(user_runner=False)
    _check(30)
    assert _alerts(_me(headers)["id"]) == 0
    _set_flags(user_runner=True)
    _check(30)
    assert _alerts(_me(headers)["id"]) == 1

    async def addressed():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord.user_id).where(
                NotificationRecord.tenant_id == tenant_id, NotificationRecord.event_type == "RUNNER_OFFLINE")))
    assert _run(addressed()) == [_me(headers)["id"]]


# --- mirror -------------------------------------------------------------------------------------------------------------------

def _upload(ts: str = "2026-11-14T10:00:00+05:30", status: str = "OPEN"):
    return {"orders": [{"ref": "o-1", "deployment_ref": "bot-7", "mode": "PAPER", "symbol": "NIFTY25NOV24500CE", "exchange": "NFO",
                        "side": "BUY", "order_type": "LIMIT", "quantity": 75, "price": 101.5, "status": "COMPLETE",
                        "filled_quantity": 75, "average_price": 101.4, "placed_at": ts, "updated_at": ts}],
            "positions": [{"ref": "p-1", "mode": "PAPER", "symbol": "NIFTY25NOV24500CE", "exchange": "NFO", "status": status,
                           "quantity": 75, "average_price": 101.4, "last_price": 104.0, "unrealised_pnl": 195.0, "updated_at": ts}],
            "audit": [{"seq": 1, "action": "order.placed", "summary": "PAPER BUY 75 NIFTY25NOV24500CE", "row_hash": "a" * 64,
                       "occurred_at": ts},
                      {"seq": 2, "action": "order.filled", "summary": "filled 75", "row_hash": "b" * 64, "prev_hash": "a" * 64,
                       "occurred_at": ts}]}


def test_mirror_upload_is_idempotent_and_newer_rows_update():
    headers, runner = _paired("rs3-mirror@example.com")
    first = runner.send(MIRROR, _upload())
    assert first.status_code == 200, first.text
    assert first.json() == {"ok": True, "inserted": 4, "updated": 0, "unchanged": 0, "conflicts": 0}
    again = runner.send(MIRROR, _upload())                                        # the same rows again (a retry)
    assert again.json() == {"ok": True, "inserted": 0, "updated": 0, "unchanged": 4, "conflicts": 0}
    newer = _upload("2026-11-14T10:05:00+05:30", status="CLOSED")
    newer["audit"][1]["row_hash"] = "c" * 64                                      # a stored audit row is never overwritten
    assert runner.send(MIRROR, newer).json() == {"ok": True, "inserted": 0, "updated": 2, "unchanged": 1, "conflicts": 1}
    older = _upload("2026-11-14T09:00:00+05:30")                                  # an older copy never wins
    assert runner.send(MIRROR, older).json()["updated"] == 0
    view = client.get(f"/api/runner/devices/{runner.device_id}/mirror", headers=headers).json()
    assert [p["status"] for p in view["positions"]] == ["CLOSED"] and len(view["orders"]) == 1
    assert [a["row_hash"][:1] for a in view["audit"]] == ["b", "a"]


def test_mirror_upload_is_size_capped_and_refuses_credential_fields():
    _, runner = _paired("rs3-mirror-cap@example.com")
    body = json.dumps(_upload()).encode() + b" " * service.MAX_MIRROR_BYTES      # valid JSON, over the cap
    assert len(body) > service.MAX_MIRROR_BYTES
    resp = runner.send(MIRROR, body)
    assert resp.status_code == 413 and resp.json()["detail"]["code"] == "too_large"
    too_many = _upload()
    too_many["orders"] = too_many["orders"] * (service.MAX_MIRROR_ROWS + 1)
    assert runner.send(MIRROR, too_many).status_code in (413, 422)
    leaky = _upload()
    leaky["orders"][0]["access_token"] = "secret"
    assert runner.send(MIRROR, leaky).status_code == 422

    async def count():
        async with _session_factory() as session:
            return len(list(await session.scalars(select(RunnerMirrorOrderRecord.id).join(
                RunnerDeviceRecord, RunnerDeviceRecord.id == RunnerMirrorOrderRecord.device_id).where(
                RunnerDeviceRecord.device_uid == runner.device_id))))
    assert _run(count()) == 0


def test_mirror_upload_follows_the_users_own_switch():
    headers, runner = _paired("rs3-mirror-off@example.com")
    assert client.put("/api/me/prefs/runner", headers=headers, json={"mirror_upload": False}).status_code == 200
    assert runner.send(HB, BEAT).json()["mirror_upload"] is False                # the runner is told in each heartbeat
    resp = runner.send(MIRROR, _upload())
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "mirror_off"
    assert client.get(f"/api/runner/devices/{runner.device_id}/mirror", headers=headers).json()["orders"] == []


def test_no_mirror_or_device_column_can_hold_a_broker_credential():
    words = ("key", "secret", "token", "password", "totp", "pin", "otp", "credential")
    for name in ("runner_devices", "runner_pairing_codes", "runner_mirror_orders", "runner_mirror_positions", "runner_mirror_audit"):
        cols = Base.metadata.tables[name].c.keys()
        allowed = {"public_key"}                                                  # the device's PUBLIC signature key
        assert not [c for c in cols if c not in allowed and any(re.search(rf"(^|_){w}(_|$)", c) for w in words)], (name, cols)
    for model in (service.MirrorOrder, service.MirrorPosition, service.MirrorAudit, service.Heartbeat):
        assert not [f for f in model.model_fields if any(w in f for w in ("secret", "token", "password", "totp", "api_key"))]


# --- revoke and isolation -------------------------------------------------------------------------------------------------

def test_revoke_refuses_the_devices_signed_requests():
    headers, runner = _paired("rs3-revoke@example.com")
    assert runner.send(HB, BEAT).status_code == 200
    resp = client.post(f"/api/runner/devices/{runner.device_id}/revoke", headers=headers)
    assert resp.status_code == 200 and resp.json()["status"] == "REVOKED" and resp.json()["revoked_at"]
    refused = runner.send(HB, BEAT)
    assert refused.status_code == 401 and refused.json()["detail"]["code"] == "revoked"
    assert runner.send(MIRROR, _upload()).status_code == 401
    _check(60)
    assert _alerts(_me(headers)["id"]) == 0                                       # a removed runner raises no alert


def test_another_member_or_organisation_never_reaches_a_users_runner():
    headers_a, runner = _paired("rs3-iso-a@example.com")
    assert runner.send(MIRROR, _upload()).status_code == 200
    headers_b = _auth("rs3-iso-b@example.com")                                    # another organisation
    member = _member_of(_me(headers_a)["tenant_id"], "rs3-iso-member@example.com")  # the same organisation, another user
    for other in (headers_b, member):
        assert client.get("/api/runner/devices", headers=other).json()["devices"] == []
        assert client.get(f"/api/runner/devices/{runner.device_id}/mirror", headers=other).status_code == 404
        assert client.post(f"/api/runner/devices/{runner.device_id}/revoke", headers=other).status_code == 404
    assert _device(runner.device_id).status == "PAIRED"
    # The device's rows are written under its own user only.

    async def owners():
        async with _session_factory() as session:
            return {r for r in await session.scalars(select(RunnerMirrorAuditRecord.user_id).join(
                RunnerDeviceRecord, RunnerDeviceRecord.id == RunnerMirrorAuditRecord.device_id).where(
                RunnerDeviceRecord.device_uid == runner.device_id))}
    assert _run(owners()) == {_me(headers_a)["id"]}


def _member_of(tenant_id: int, email: str):
    from app.auth.security import hash_password

    async def create():
        async with _session_factory() as session:
            session.add(User(tenant_id=tenant_id, email=email, hashed_password=hash_password("S3cur3Pass!"), role="USER"))
            await session.commit()
    _run(create())
    resp = client.post("/api/auth/login", json={"email": email, "password": "S3cur3Pass!"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# --- the kill switch is the user's: no admin / platform path ------------------------------------------------------------

def test_no_route_lets_anyone_set_a_runners_kill_switch():
    routes = iter_api_routes(app)
    assert {r.path for r in routes if "runner" in r.path} == {"/api/runner/pairing-codes", "/api/runner/devices", "/api/runner/devices/{device_id}/revoke",
                                              "/api/runner/devices/{device_id}/mirror", "/api/runner/pair", HB, MIRROR}
    assert not [r.path for r in routes if r.path.startswith("/api/admin") and "runner" in r.path]
    # Only the device's own signed heartbeat writes `kill_switch_on` (what it reports); no other code in the app does.
    writers = [str(p.relative_to(BACKEND)) for p in (BACKEND / "app").rglob("*.py")
               if re.search(r"kill_switch_on\s*=(?!=)", p.read_text(encoding="utf-8"))]
    assert writers == ["app/runner_link/service.py"]
    source = (BACKEND / "app/runner_link/service.py").read_text(encoding="utf-8")
    assert source.count("kill_switch_on =") == 1 and "device.kill_switch_on = beat.kill_switch_on" in source


def test_a_super_admin_cannot_see_or_stop_a_users_runner():
    _, runner = _paired("rs3-admin-user@example.com")
    assert runner.send(HB, {**BEAT, "kill_switch_on": False}).status_code == 200
    admin = _auth("rs3-admin@example.com")

    async def promote():
        async with _session_factory() as session:
            await session.execute(update(User).where(User.email == "rs3-admin@example.com").values(role="SUPER_ADMIN"))
            await session.commit()
    _run(promote())
    assert client.get("/api/runner/devices", headers=admin).json()["devices"] == []
    assert client.post(f"/api/runner/devices/{runner.device_id}/revoke", headers=admin).status_code == 404
    device = _device(runner.device_id)
    assert device.status == "ONLINE" and device.kill_switch_on is False and device.revoked_at is None


# --- migration ----------------------------------------------------------------------------------------------------------------

def test_migration_creates_and_drops_only_the_runner_tables():
    import importlib.util

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = next((BACKEND / "alembic/versions").glob("c1e3a5b7d9f2_*.py"))
    spec = importlib.util.spec_from_file_location("mig_runner_s3", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.ROLLBACK_SAFE is True and module.down_revision == "b4d6f8a0c2e5"
    engine = sa.create_engine("sqlite://")
    names = ("runner_devices", "runner_pairing_codes", "runner_mirror_orders", "runner_mirror_positions", "runner_mirror_audit")
    with engine.begin() as conn:
        for name in ("tenants", "users"):                                         # the referenced tables, as they are
            Base.metadata.tables[name].create(conn)
        ops = Operations(MigrationContext.configure(conn))
        module.op = ops
        module.upgrade()
        tables = set(sa.inspect(conn).get_table_names())
        assert set(names) <= tables
        for name in names:                                                        # the migration matches the models
            assert {c["name"] for c in sa.inspect(conn).get_columns(name)} == set(Base.metadata.tables[name].c.keys())
        module.downgrade()
        assert not set(names) & set(sa.inspect(conn).get_table_names())
        assert {"tenants", "users"} <= set(sa.inspect(conn).get_table_names())
