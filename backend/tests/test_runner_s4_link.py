"""Runner S4 (docs/design/USER_RUNNER.md section 4; flag `user_runner`, OFF): the runner <-> ATP link, end to end in one
process - ATP's real API (TestClient) and the runner's own link client, loop and local SQLite store (its own file):

1. pair -> heartbeat -> his saved rules pulled into the runner's store -> fake feed -> PAPER entry on the runner ->
   mirror upload -> ATP shows the copy; the F&O ban list arrives with the reference data;
2. only HIS rules are synced (not another member's deployment, no signal / evaluation fields, no platform control);
3. the kill switch: his own ATP button -> a command signed for his device -> the runner applies it (entries only) and
   acks it; another user, a same-organisation member and an admin are refused; a forged / replayed / wrong-device /
   wrong-origin command is refused by the runner;
4. ATP down: exits still run (the stop closes the position), new entries pause only after his grace setting, and resume
   when the link returns;
5. signatures: strictly newer timestamps, a replayed request and a foreign key are refused; his mirror switch OFF stops
   uploads.
Generic made-up numbers only (no market data, no symbol-specific content).
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pandas as pd
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import rate_limit, runtime_mode
from app.db.base import Base
from app.db.models import (
    CustomStrategyRecord,
    FoBanRecord,
    RunnerCommandRecord,
    RunnerDeviceRecord,
    StrategyDeploymentRecord,
    TradeRecord,
    User,
    UserPrefsRecord,
)
from app.platform import controls
from app.runner import link as runner_link
from app.runner.feed import FakeFeed
from app.runner.loop import Runner
from app.runner.settings import RunnerSettings
from app.runner_link import service, signing
from app.strategy_engine.declarative import Condition, CustomStrategyConfig, Operand
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth, _me

NOW = datetime(2026, 11, 16, 4, 30, tzinfo=UTC)          # a weekday, inside the NSE session (10:00 IST)
SYMBOL = "RUNNERX"


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
def _flags_on(monkeypatch):
    rate_limit.reset()
    _set_flags(ui_v2=True, user_runner=True)
    monkeypatch.setattr(runtime_mode, "_runner", True)        # this test's runner half (billing off, ban list from ATP)
    runtime_mode.set_ban_list(None)
    yield
    runtime_mode.set_ban_list(None)
    _set_flags(user_runner=False)
    rate_limit.reset()


def bars() -> pd.DataFrame:
    closes = [100.0 - 0.5 * i for i in range(30)] + [90.0, 80.0]    # ... fall, the cross bar, then the forming bar
    index = pd.date_range(end=NOW, periods=len(closes), freq="5min", tz="UTC")
    df = pd.DataFrame({"open": closes, "high": [c + 0.4 for c in closes], "low": [c - 0.4 for c in closes],
                       "close": closes, "volume": [1000.0] * len(closes)}, index=index)
    df.iloc[-2, df.columns.get_loc("open")] = 85.0
    df.iloc[-2, df.columns.get_loc("low")] = 84.8
    return df


CONFIG = CustomStrategyConfig(
    name="runner-s4", timeframe="5min", stop_loss_atr_mult=1.0, atr_period=5,
    long_conditions=[Condition(left=Operand(type="indicator", indicator="CLOSE"), operator="CROSSES_ABOVE",
                               right=Operand(type="indicator", indicator="SMA", period=5))])


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


def _saved_rules(tenant_id: int, user_id: int, *, mode: str = "PAPER") -> int:
    """His saved strategy + deployment in ATP (as the builder / deploy pages save them)."""
    async def go():
        async with _session_factory() as session:
            record = CustomStrategyRecord(tenant_id=tenant_id, user_id=user_id, name="runner-s4", config_json=CONFIG.model_dump_json())
            session.add(record)
            await session.flush()
            dep = StrategyDeploymentRecord(tenant_id=tenant_id, strategy_id=f"custom:{record.id}", symbol=SYMBOL, exchange="NSE",
                                           timeframe="5min", status="ACTIVE", entry_mode="AUTO", holding="SWING",
                                           order_style="MARKET", max_lots=1, created_by=user_id, mode=mode,
                                           last_error="server-side evaluation text", last_signal_at=NOW - timedelta(days=3))
            session.add(dep)
            await session.commit()
            return dep.id
    return _run(go())


class Store:
    """The runner's own local SQLite file (not ATP's database)."""

    def __init__(self, path) -> None:
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def all(self, model):
        async with self.sessions() as session:
            return list(await session.scalars(select(model)))


def _runner(tmp_path, email: str, *, atp: httpx.Client | None = None):
    headers = _auth(email)
    me = _me(headers)
    home = tmp_path / "home"
    settings = RunnerSettings.load(home)
    store = Store(tmp_path / "runner.db")
    _run(store.create())
    code = client.post("/api/runner/pairing-codes", headers=headers).json()["code"]
    pairing = runner_link.pair(home, code, atp_url="http://testserver", http=client, name="Pilot VPS")
    key = runner_link.load_key(settings.device_key_path)
    lc = runner_link.LinkClient(atp or client, key, pairing.device_id)
    feed = FakeFeed()
    feed.set_frames(SYMBOL, {"5min": bars()})
    feed.set_price(SYMBOL, 90.0)
    runner = Runner(settings, feed, sessions=store.sessions)
    runner.link = runner_link.Link(settings, pairing, lc, store.sessions, audit=runner.audit,
                                   login_status=lambda now: "broker session valid")
    return headers, me, settings, store, pairing, runner, feed


def _device(uid: str) -> RunnerDeviceRecord:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(RunnerDeviceRecord).where(RunnerDeviceRecord.device_uid == uid))
    return _run(go())


# --- 1. end to end ------------------------------------------------------------------------------------------------------

def test_pair_heartbeat_rules_paper_entry_mirror_and_atp_shows_the_copy(tmp_path):
    headers, me, settings, store, pairing, runner, feed = _runner(tmp_path, "rs4-e2e@example.com")
    dep_id = _saved_rules(me["tenant_id"], me["id"])

    async def ban():
        async with _session_factory() as session:
            session.add(FoBanRecord(underlying="BANX", trade_date=date(2026, 11, 13), source="test", checksum="0" * 64))
            await session.commit()
    _run(ban())
    assert settings.device_key_path.stat().st_mode & 0o777 == 0o600 and settings.link_path.stat().st_mode & 0o777 == 0o600
    assert pairing.user_ref == me["id"] and pairing.command_key == service.command_public_key()

    first = _run(runner.run_cycle(now=NOW))
    assert first.errors == [] and first.entries_placed == 1 and first.entries_paused is None
    deps = _run(store.all(StrategyDeploymentRecord))
    assert [(d.id, d.status, d.mode) for d in deps] == [(dep_id, "ACTIVE", "PAPER")]       # ATP's own id
    assert deps[0].last_error is None and deps[0].last_signal_at is not None              # the runner's own run state
    assert runtime_mode.ban_list() == frozenset({"BANX"})                                  # the reference data arrived
    device = _device(pairing.device_id)
    assert device.status == "ONLINE" and device.broker_session == "valid" and device.broker_login == "broker session valid"
    assert device.kill_switch_on is False and device.open_positions == 0

    second = _run(runner.run_cycle(now=NOW + timedelta(seconds=61)))                     # the next heartbeat mirrors
    assert second.errors == [] and second.entries_placed == 0                            # one entry per signal bar
    mirror = client.get(f"/api/runner/devices/{pairing.device_id}/mirror", headers=headers).json()
    assert [p["status"] for p in mirror["positions"]] == ["OPEN"] and mirror["positions"][0]["mode"] == "PAPER"
    assert mirror["positions"][0]["deployment_ref"] == f"deployment:{dep_id}" and mirror["positions"][0]["symbol"] == SYMBOL
    assert len(mirror["orders"]) == 1 and mirror["orders"][0]["side"] == "BUY"
    kinds = {a["action"] for a in mirror["audit"]}
    assert {"entry_intent", "entry_result", "rules_synced"} <= kinds
    assert _device(pairing.device_id).open_positions == 1
    # The same rows again change nothing on ATP (idempotent): the next mirror sends nothing new for the audit log.
    before = len(mirror["audit"])
    _run(runner.run_cycle(now=NOW + timedelta(seconds=122)))
    again = client.get(f"/api/runner/devices/{pairing.device_id}/mirror", headers=headers).json()
    assert len(again["positions"]) == 1 and len(again["orders"]) == 1 and len(again["audit"]) >= before


def test_only_his_own_rules_are_synced_never_signals_or_platform_controls(tmp_path):
    headers, me, settings, store, pairing, runner, _ = _runner(tmp_path, "rs4-rules@example.com")
    mine = _saved_rules(me["tenant_id"], me["id"])
    member = _member_of(me["tenant_id"], "rs4-rules-member@example.com")
    theirs = _saved_rules(me["tenant_id"], _me(member)["id"])
    resp = runner.link.client.request("GET", runner_link.RULES_PATH)
    assert resp.status_code == 200
    snap = resp.json()
    assert snap["user_ref"] == me["id"] and [d["id"] for d in snap["deployments"]] == [mine] and theirs != mine
    assert set(snap) == {"user_ref", "org_ref", "strategies", "deployments", "settings", "etag"}
    for dep in snap["deployments"]:
        assert not set(dep) & {"last_signal_at", "last_error", "last_evaluated_at", "consecutive_failures", "created_by"}
    assert "kill" not in json.dumps(snap).lower() and "maintenance" not in json.dumps(snap).lower()
    # a deployment he stops in ATP is STOPPED on the runner at the next sync (never deleted)
    _run(runner.run_cycle(now=NOW))

    async def stop():
        async with _session_factory() as session:
            await session.execute(update(StrategyDeploymentRecord).where(StrategyDeploymentRecord.id == mine).values(status="STOPPED"))
            await session.commit()
    _run(stop())
    _run(runner.run_cycle(now=NOW + timedelta(seconds=61)))
    assert [(d.id, d.status) for d in _run(store.all(StrategyDeploymentRecord))] == [(mine, "STOPPED")]


# --- 2. the kill switch: his own, relayed ------------------------------------------------------------------------------------

def test_his_kill_button_reaches_his_runner_and_nobody_elses_does(tmp_path):
    headers, me, settings, store, pairing, runner, feed = _runner(tmp_path, "rs4-kill@example.com")
    _saved_rules(me["tenant_id"], me["id"])
    _run(runner.run_cycle(now=NOW))
    listed = client.get("/api/runner/devices", headers=headers).json()["devices"]
    assert [d["kill_relay"] for d in listed] == [True]                              # the My runner page shows his button
    url = f"/api/runner/devices/{pairing.device_id}/kill-switch"
    other = _auth("rs4-kill-other@example.com")
    member = _member_of(me["tenant_id"], "rs4-kill-member@example.com")
    admin = _auth("rs4-kill-admin@example.com")

    async def promote():
        async with _session_factory() as session:
            await session.execute(update(User).where(User.email == "rs4-kill-admin@example.com").values(role="SUPER_ADMIN"))
            await session.commit()
    _run(promote())
    for who in (other, member, admin):
        assert client.post(url, headers=who, json={"on": True}).status_code == 404
    assert client.post(url, json={"on": True}).status_code in (401, 403)

    resp = client.post(url, headers=headers, json={"on": True})
    assert resp.status_code == 201, resp.text
    assert not settings.killed()
    killed = _run(runner.run_cycle(now=NOW + timedelta(seconds=61)))                  # delivered with the heartbeat
    assert settings.killed() and killed.entries_paused and "exits continue" in killed.entries_paused
    _run(runner.run_cycle(now=NOW + timedelta(seconds=122)))                           # acked; the state reported

    async def commands():
        async with _session_factory() as session:
            return list(await session.scalars(select(RunnerCommandRecord)))
    rows = [c for c in _run(commands()) if c.user_id == me["id"]]
    assert len(rows) == 1 and rows[0].acked_at is not None and rows[0].delivered_at is not None
    assert _device(pairing.device_id).kill_switch_on is True
    assert client.post(url, headers=headers, json={"on": False}).status_code == 201
    _run(runner.run_cycle(now=NOW + timedelta(seconds=183)))
    assert not settings.killed()
    # a runner he removed takes no command, and the page shows no button for it
    assert client.post(f"/api/runner/devices/{pairing.device_id}/revoke", headers=headers).status_code == 200
    assert client.post(url, headers=headers, json={"on": True}).status_code == 409
    assert [d["kill_relay"] for d in client.get("/api/runner/devices", headers=headers).json()["devices"]] == [False]


def test_the_runner_refuses_any_command_that_is_not_his_own_signed_one(tmp_path):
    _, me, settings, _, pairing, _, _ = _runner(tmp_path, "rs4-forged@example.com")
    good = {"v": 1, "command_id": 7, "device_id": pairing.device_id, "user_ref": me["id"], "kind": "kill_switch", "on": True,
            "origin": "user_signed", "issued_at": NOW.isoformat()}

    def cmd(payload, key=None):
        return {"id": payload.get("command_id"), "payload": payload,
                "signature": signing.sign(key or service._command_key(), signing.command_message(payload))}
    assert runner_link.verify_command(cmd(good), pairing, 0) == (good, None)
    refusals = {
        "foreign key": runner_link.verify_command(cmd(good, Ed25519PrivateKey.generate()), pairing, 0),
        "other device": runner_link.verify_command(cmd({**good, "device_id": "0" * 32}), pairing, 0),
        "other user": runner_link.verify_command(cmd({**good, "user_ref": me["id"] + 1}), pairing, 0),
        "admin origin": runner_link.verify_command(cmd({**good, "origin": "admin"}), pairing, 0),
        "platform kind": runner_link.verify_command(cmd({**good, "kind": "maintenance"}), pairing, 0),
        "replay": runner_link.verify_command(cmd(good), pairing, 7),
        "tampered": runner_link.verify_command({**cmd(good), "payload": {**good, "on": False}}, pairing, 0),
    }
    for name, (payload, reason) in refusals.items():
        assert payload is None and reason, name
    assert refusals["replay"][1] == "already applied"
    with pytest.raises(PermissionError):
        settings.set_killed(True, origin="admin")                                      # S2's one setter, unchanged
    assert not settings.killed()


# --- 3. ATP down: exits run, entries pause after the grace -------------------------------------------------------------

def test_atp_down_exits_still_run_entries_pause_after_grace_and_resume(tmp_path):
    headers, me, settings, store, pairing, runner, feed = _runner(tmp_path, "rs4-down@example.com")
    _saved_rules(me["tenant_id"], me["id"])
    assert _run(runner.run_cycle(now=NOW)).entries_placed == 1
    trade = _run(store.all(TradeRecord))[0]

    def down(request):
        raise httpx.ConnectError("ATP unreachable", request=request)
    runner.link.client.http = httpx.Client(base_url="http://testserver", transport=httpx.MockTransport(down))
    within = runner.link.entries_paused(NOW + timedelta(minutes=settings.link_grace_minutes))
    assert within is None                                                              # inside his grace: no pause
    feed.set_price(SYMBOL, float(trade.stop_loss) - 1.0)
    later = NOW + timedelta(minutes=settings.link_grace_minutes, seconds=61)
    out = _run(runner.run_cycle(now=later))
    assert out.errors == [] and out.exits_closed == 1                                  # the stop closed it without ATP
    assert out.entries_paused and "ATP has not answered" in out.entries_paused and "exits continue" in out.entries_paused
    assert [t.exit_reason for t in _run(store.all(TradeRecord))] == ["Stop Loss"]
    log = settings.link_log_path.read_text()
    assert "new entries pause" in log

    runner.link.client.http = client                                                   # the link returns
    back = _run(runner.run_cycle(now=later + timedelta(seconds=61)))
    assert back.entries_paused is None and "new entries resume" in settings.link_log_path.read_text()
    mirror = client.get(f"/api/runner/devices/{pairing.device_id}/mirror", headers=headers).json()
    assert [p["status"] for p in mirror["positions"]] == ["CLOSED"]                    # the copy catches up


# --- 4. signatures, replay, mirror switch ---------------------------------------------------------------------------------

def test_signed_requests_are_strictly_newer_and_a_replay_or_foreign_key_is_refused(tmp_path):
    headers, me, settings, store, pairing, runner, _ = _runner(tmp_path, "rs4-sig@example.com")
    lc = runner.link.client
    stamps = [lc._stamp() for _ in range(5)]
    assert stamps == sorted(set(stamps))
    beat = {"run_state": "running", "version": runner_link.RUNNER_VERSION, "kill_switch_on": False, "open_positions": 0}
    body = json.dumps(beat).encode()
    ts = lc._stamp()
    sig_headers = {**signing.request_headers(lc.key, pairing.device_id, "POST", runner_link.HEARTBEAT_PATH, body, ts),
                   "Content-Type": "application/json"}
    assert client.post(runner_link.HEARTBEAT_PATH, content=body, headers=sig_headers).status_code == 200
    replay = client.post(runner_link.HEARTBEAT_PATH, content=body, headers=sig_headers)
    assert replay.status_code == 401 and replay.json()["detail"]["code"] == "replay"
    foreign = runner_link.LinkClient(client, Ed25519PrivateKey.generate(), pairing.device_id)
    assert foreign.request("GET", runner_link.RULES_PATH).status_code == 401
    assert foreign.request("POST", runner_link.HEARTBEAT_PATH, beat).status_code == 401
    stale = signing.request_headers(lc.key, pairing.device_id, "GET", runner_link.RULES_PATH, b"",
                                    int((datetime.now(UTC) - timedelta(minutes=10)).timestamp() * 1000))
    assert client.get(runner_link.RULES_PATH, headers=stale).status_code == 401
    with pytest.raises(runner_link.LinkError):
        runner_link.check_atp_url("http://example.com")                               # https only (http: this machine)


def test_mirror_switch_off_means_nothing_is_uploaded(tmp_path):
    headers, me, settings, store, pairing, runner, _ = _runner(tmp_path, "rs4-mirror-off@example.com")
    _saved_rules(me["tenant_id"], me["id"])

    async def off():
        async with _session_factory() as session:
            session.add(UserPrefsRecord(tenant_id=me["tenant_id"], user_id=me["id"], namespace="runner",
                                        value_json=json.dumps({"mirror_upload": False})))
            await session.commit()
    _run(off())
    assert _run(runner.run_cycle(now=NOW)).entries_placed == 1
    _run(runner.run_cycle(now=NOW + timedelta(seconds=61)))
    assert runner.link.mirror_upload is False
    mirror = client.get(f"/api/runner/devices/{pairing.device_id}/mirror", headers=headers).json()
    assert mirror["orders"] == [] and mirror["positions"] == [] and mirror["audit"] == []


def test_the_migration_matches_the_models():
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    versions = Path(__file__).resolve().parents[1] / "alembic/versions"

    def load(rev):
        path = next(versions.glob(f"{rev}_*.py"))
        spec = importlib.util.spec_from_file_location(f"mig_{rev}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    s3, s4 = load("c1e3a5b7d9f2"), load("d2f4b6c8e0a1")
    assert s4.down_revision == "c1e3a5b7d9f2" and s4.ROLLBACK_SAFE is True
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for name in ("tenants", "users"):
            Base.metadata.tables[name].create(conn)
        ops = Operations(MigrationContext.configure(conn))
        s3.op = s4.op = ops
        s3.upgrade()
        s4.upgrade()
        for name in ("runner_commands", "runner_devices"):
            assert {c["name"] for c in sa.inspect(conn).get_columns(name)} == set(Base.metadata.tables[name].c.keys())
        s4.downgrade()
        assert "runner_commands" not in sa.inspect(conn).get_table_names()
        assert "broker_login" not in {c["name"] for c in sa.inspect(conn).get_columns("runner_devices")}
