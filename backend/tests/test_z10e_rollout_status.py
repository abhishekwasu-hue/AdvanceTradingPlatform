"""Z10-e (G7 + rollout): the staged beta cohort (50 -> 200 -> 500, audited) and the public status page."""
import asyncio
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.beta import invites
from app.core import config as app_config
from app.core import rate_limit
from app.db.base import Base
from app.db.models import (AlertDeliveryRecord, AuditLogRecord, BrokerCredentialRecord, IncidentRecord, IndexEodRecord, PlatformControlRecord,
                           WorkerHeartbeatRecord)
from app.market_data import entitlement
from app.observability import status_page
from app.platform import controls
from tests.test_admin_api import _admin
from tests.test_auth_api import _register, _session_factory, client

ADMIN = "/api/admin/beta"
PASSWORD = "S3cur3Pass!"


def _run(coro):
    return asyncio.run(coro)


def _set_flags(**flags: bool) -> None:
    async def go():
        async with _session_factory() as session:
            stored = (await controls._get(session, controls.KEY_FEATURE_FLAGS)).get("flags") or {}
            for name, on in flags.items():
                stored.pop(name, None)
                if on:
                    stored[name] = {"on": True, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


def _clear_stage() -> None:
    async def go():
        async with _session_factory() as session:
            row = await session.get(PlatformControlRecord, invites.KEY_STAGE)
            if row is not None:
                await session.delete(row)
                await session.commit()
    _run(go())


def _audit(event: str) -> list:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(AuditLogRecord).where(AuditLogRecord.event == event).order_by(AuditLogRecord.id)))
    return _run(go())


@pytest.fixture(scope="module")
def admin_headers():
    headers, _ = _admin("z10e-admin@example.com")
    return headers


@pytest.fixture
def stage_reset():
    _clear_stage()
    yield
    _clear_stage()


# --- staged rollout -----------------------------------------------------------------------------------------------------

def test_stage_setting_defaults_to_50_and_is_one_of_the_stages():
    assert app_config.BETA_COHORT_STAGES == (50, 200, 500)
    assert app_config.BETA_COHORT_STAGE == 50
    assert app_config.BETA_COHORT_CAP == 500                       # the PW6 ceiling is unchanged


def test_cohort_view_shows_stage_caps_and_headroom(admin_headers, stage_reset):
    body = client.get(f"{ADMIN}/cohort", headers=admin_headers).json()
    assert body["stage"] == 50 and body["stage_source"] == "default" and body["stages"] == [50, 200, 500]
    assert body["env_cap"] == 500 and body["cap"] == 50 and body["capped_by_env"] is False
    assert body["next_stage"] == 200
    assert body["headroom"] == body["remaining"] == max(0, 50 - body["used"])
    assert body["stage_set_by"] is None and body["stage_set_at"] is None


def test_effective_cap_is_min_of_stage_and_env_cap(admin_headers, stage_reset, monkeypatch):
    monkeypatch.setattr(app_config, "BETA_COHORT_CAP", 30)
    body = client.get(f"{ADMIN}/cohort", headers=admin_headers).json()
    assert body["stage"] == 50 and body["cap"] == 30 and body["capped_by_env"] is True
    monkeypatch.setattr(app_config, "BETA_COHORT_CAP", 500)
    monkeypatch.setattr(app_config, "BETA_COHORT_STAGE", 200)      # the setting applies while nothing is stored
    assert client.get(f"{ADMIN}/cohort", headers=admin_headers).json()["cap"] == 200


def test_moving_up_is_one_stage_at_a_time_with_a_reason_and_audited(admin_headers, stage_reset):
    before = len(_audit("beta_cohort_stage_raised"))
    skip = client.put(f"{ADMIN}/cohort/stage", headers=admin_headers, json={"stage": 500, "reason": "go big"})
    assert skip.status_code == 400 and "one stage at a time" in skip.json()["detail"]
    no_reason = client.put(f"{ADMIN}/cohort/stage", headers=admin_headers, json={"stage": 200})
    assert no_reason.status_code == 400 and "reason" in no_reason.json()["detail"]
    assert client.put(f"{ADMIN}/cohort/stage", headers=admin_headers, json={"stage": 300, "reason": "odd"}).status_code == 400
    assert client.put(f"{ADMIN}/cohort/stage", headers=admin_headers, json={"stage": 200, "reason": "x", "extra": 1}).status_code == 422
    assert len(_audit("beta_cohort_stage_raised")) == before        # refusals write nothing

    r = client.put(f"{ADMIN}/cohort/stage", headers=admin_headers, json={"stage": 200, "reason": "wave 1 stable for a week"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stage"] == 200 and body["cap"] == 200 and body["stage_source"] == "admin" and body["next_stage"] == 500
    assert body["stage_set_by"] is not None and body["stage_set_at"]
    rows = _audit("beta_cohort_stage_raised")
    assert len(rows) == before + 1
    assert rows[-1].detail.startswith("50 -> 200 (effective cap 200, BETA_COHORT_CAP 500, used ") and rows[-1].detail.endswith(": wave 1 stable for a week")
    assert rows[-1].tenant_id is None

    # The same stage again changes nothing and writes nothing.
    assert client.put(f"{ADMIN}/cohort/stage", headers=admin_headers, json={"stage": 200}).status_code == 200
    assert len(_audit("beta_cohort_stage_raised")) == before + 1

    assert client.put(f"{ADMIN}/cohort/stage", headers=admin_headers, json={"stage": 500, "reason": "wave 2"}).json()["next_stage"] is None


def test_moving_down_may_skip_and_needs_no_reason(admin_headers, stage_reset):
    for stage, reason in ((200, "wave 1"), (500, "wave 2")):
        assert client.put(f"{ADMIN}/cohort/stage", headers=admin_headers, json={"stage": stage, "reason": reason}).status_code == 200
    before = len(_audit("beta_cohort_stage_lowered"))
    r = client.put(f"{ADMIN}/cohort/stage", headers=admin_headers, json={"stage": 50})
    assert r.status_code == 200 and r.json()["stage"] == 50
    rows = _audit("beta_cohort_stage_lowered")
    assert len(rows) == before + 1 and rows[-1].detail.startswith("500 -> 50 ")


def test_unreadable_stored_stage_falls_back_to_the_setting(admin_headers, stage_reset):
    async def corrupt():
        async with _session_factory() as session:
            session.add(PlatformControlRecord(key=invites.KEY_STAGE, value_json=json.dumps({"stage": 123})))
            await session.commit()
    _run(corrupt())
    body = client.get(f"{ADMIN}/cohort", headers=admin_headers).json()
    assert body["stage"] == 50 and body["stage_source"] == "default"


def test_gate_stops_at_the_stage(admin_headers, stage_reset, monkeypatch):
    """With the gate on, sign-up stops at min(stage, BETA_COHORT_CAP) - here the stage is the binding one."""
    _set_flags(beta_cohort_gate=True)
    try:
        code = client.post(f"{ADMIN}/invite-codes", headers=admin_headers, json={"max_uses": 5}).json()["code"]
        used = client.get(f"{ADMIN}/cohort", headers=admin_headers).json()["used"]
        monkeypatch.setattr(app_config, "BETA_COHORT_STAGES", (used + 1, 200, 500))
        monkeypatch.setattr(app_config, "BETA_COHORT_STAGE", used + 1)
        assert client.get(f"{ADMIN}/cohort", headers=admin_headers).json()["remaining"] == 1
        assert client.post("/api/auth/register", json={"email": "z10e-last@example.com", "password": PASSWORD, "invite_code": code}).status_code == 201
        full = client.post("/api/auth/register", json={"email": "z10e-over@example.com", "password": PASSWORD, "invite_code": code})
        assert full.status_code == 403 and full.json()["detail"]["code"] == "beta_cohort_full"
        # Moving up a stage opens places again; existing accounts are never touched.
        assert client.put(f"{ADMIN}/cohort/stage", headers=admin_headers, json={"stage": 200, "reason": "next wave"}).status_code == 200
        assert client.post("/api/auth/register", json={"email": "z10e-over@example.com", "password": PASSWORD, "invite_code": code}).status_code == 201
    finally:
        _set_flags(beta_cohort_gate=False)


def test_stage_endpoint_is_super_admin_only(admin_headers):
    owner = {"Authorization": f"Bearer {_register('z10e-owner@example.com')}"}
    assert client.put(f"{ADMIN}/cohort/stage", json={"stage": 200, "reason": "nope"}).status_code in (401, 403)
    assert client.put(f"{ADMIN}/cohort/stage", headers=owner, json={"stage": 200, "reason": "nope"}).status_code == 403


# --- public status page: the API ------------------------------------------------------------------------------------------

@pytest.fixture
def status_on():
    status_page.reset_cache()
    rate_limit.reset("public_status")
    _set_flags(public_status_page=True)
    yield
    _set_flags(public_status_page=False)
    status_page.reset_cache()
    rate_limit.reset("public_status")


def test_status_flag_is_registered_and_off_by_default():
    assert "public_status_page" in controls.FEATURE_FLAGS and "public_status_page" in controls.DEFAULT_OFF_FLAGS
    status_page.reset_cache()
    rate_limit.reset("public_status")
    r = client.get("/api/status")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}      # says nothing about a feature


FORBIDDEN_KEYS = {"error", "last_error", "latency_ms", "environment", "current", "head", "tenant_id", "user_id", "title",
                  "root_cause", "actions_taken", "email", "version", "commit", "cycle_seconds", "opened_by", "source"}


def _keys(value) -> set:
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_keys(v) for v in value)) if value else set()
    return set()


def test_status_is_public_read_only_and_shaped(status_on):
    r = client.get("/api/status")                                   # no login
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "public, max-age=15"
    body = r.json()
    assert body["status"] in ("operational", "degraded", "outage", "unknown")
    assert set(body["components"]) == {"api", "worker", "data_feed", "alerts"}
    for component in body["components"].values():
        assert component["state"] in ("operational", "degraded", "outage", "unknown") and component["summary"]
    assert set(body["components"]["data_feed"]) >= {"market_open", "eod", "tier_a"}
    assert set(body["banner"]) == {"maintenance", "maintenance_message", "incidents"}
    assert not (_keys(body) & FORBIDDEN_KEYS), _keys(body) & FORBIDDEN_KEYS
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/api/status").status_code == 405


def test_status_is_cached_between_calls(status_on, monkeypatch):
    calls = []
    real = status_page.build

    async def counting(session, now=None):
        calls.append(1)
        return await real(session, now)
    monkeypatch.setattr(status_page, "build", counting)
    assert client.get("/api/status").status_code == 200
    assert client.get("/api/status").status_code == 200
    assert len(calls) == 1


def test_status_is_rate_limited_per_ip(status_on):
    for _ in range(60):
        assert client.get("/api/status").status_code == 200
    assert client.get("/api/status").status_code == 429


def test_tenant_incidents_and_incident_text_never_reach_the_public_body(status_on):
    """The suite shares one database: incidents other tests left open (the kill switch opens platform-wide ones) and
    their maintenance note are set aside for this test and put back afterwards."""
    async def set_aside():
        async with _session_factory() as session:
            others = {r.id: r.status for r in await session.scalars(select(IncidentRecord).where(IncidentRecord.status.in_(("OPEN", "MITIGATED"))))}
            for row in await session.scalars(select(IncidentRecord).where(IncidentRecord.id.in_(list(others) or [-1]))):
                row.status = "RESOLVED"
            maintenance = await controls._get(session, controls.KEY_MAINTENANCE)
            await controls._set(session, controls.KEY_MAINTENANCE, {"on": False, "message": ""}, None)
            session.add(IncidentRecord(title="Tenant 7 broker keys leaked", severity="CRITICAL", summary="internal detail", status="OPEN",
                                       source="operator", tenant_id=1))
            session.add(IncidentRecord(title="Postgres replica lag on db-2", severity="WARNING", summary="internal", status="OPEN",
                                       source="operator", tenant_id=None, root_cause="secret cause"))
            await session.commit()
            return others, maintenance

    async def put_back(others, maintenance):
        async with _session_factory() as session:
            for row in await session.scalars(select(IncidentRecord).where(IncidentRecord.status.in_(("OPEN", "MITIGATED")))):
                row.status = "RESOLVED"                                      # the two seeded here
            for row in await session.scalars(select(IncidentRecord).where(IncidentRecord.id.in_(list(others) or [-1]))):
                row.status = others[row.id]
            await controls._set(session, controls.KEY_MAINTENANCE, maintenance, None)
            await session.commit()

    others, maintenance = _run(set_aside())
    try:
        status_page.reset_cache()
        text = client.get("/api/status").text
        for leaked in ("Tenant 7", "broker keys", "Postgres", "db-2", "internal", "secret cause", "operator"):
            assert leaked not in text, leaked
        incidents = json.loads(text)["banner"]["incidents"]
        # Only the platform-wide WARNING: the CRITICAL one is organisation-scoped and left out.
        assert [{k: i[k] for k in ("impact", "state")} for i in incidents] == [{"impact": "minor", "state": "investigating"}]
    finally:
        _run(put_back(others, maintenance))
        status_page.reset_cache()


# --- public status page: the body, on a database of its own ---------------------------------------------------------------

@pytest.fixture
def fresh(monkeypatch):
    from app.observability import routes as health_routes

    async def redis_ok():
        return {"status": "ok"}
    monkeypatch.setattr(health_routes, "_check_redis", redis_ok)       # CI has Redis, a laptop may not: pin it
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": {"public_status_page": {"on": True, "tenants": []}}}, None)
            await session.commit()
    _run(setup())
    yield factory
    _run(engine.dispose())


# Tuesday 2026-10-13, 11:00 IST (market open) and 20:00 IST (closed).
OPEN_NOW = datetime(2026, 10, 13, 5, 30, tzinfo=timezone.utc)
CLOSED_NOW = datetime(2026, 10, 13, 14, 30, tzinfo=timezone.utc)


def _build(factory, now, **seed):
    async def go():
        async with factory() as session:
            for row in seed.get("rows", []):
                session.add(row)
            await session.commit()
            return await status_page.build(session, now)
    return _run(go())


def test_build_returns_none_while_the_flag_is_off(fresh):
    async def off():
        async with fresh() as session:
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": {}}, None)
            await session.commit()
            return await status_page.build(session, OPEN_NOW)
    assert _run(off()) is None


def test_fresh_platform_reads_unknown_not_broken(fresh):
    body = _build(fresh, CLOSED_NOW)
    assert body["components"]["api"]["state"] == "operational", body
    assert body["components"]["worker"]["state"] == "unknown"
    assert body["components"]["data_feed"]["eod"] == {"state": "unknown", "date": None, "summary": "No end-of-day data has been loaded yet."}
    assert body["components"]["data_feed"]["tier_a"] == {"enabled": False, "sessions": 0, "summary": "Live data through your own broker is not enabled."}
    assert body["components"]["alerts"]["state"] == "operational" and body["components"]["alerts"]["lag_seconds"] == 0
    assert body["status"] == "operational"


def test_worker_heartbeat_bands(fresh):
    beat = WorkerHeartbeatRecord(worker_name="trading_worker", last_seen_at=OPEN_NOW - timedelta(seconds=20))
    assert _build(fresh, OPEN_NOW, rows=[beat])["components"]["worker"]["state"] == "operational"

    async def age(seconds):
        async with fresh() as session:
            row = await session.scalar(select(WorkerHeartbeatRecord))
            row.last_seen_at = OPEN_NOW - timedelta(seconds=seconds)
            await session.commit()
    _run(age(20 * 60))
    body = _build(fresh, OPEN_NOW)
    assert body["components"]["worker"]["state"] == "outage" and "20 minutes during market hours" in body["components"]["worker"]["summary"]
    assert body["status"] == "outage"
    _run(age(20 * 60 + 9 * 3600))                                    # same age at 20:00 IST
    body = _build(fresh, CLOSED_NOW)
    assert body["components"]["worker"]["state"] == "degraded" and "market is closed" in body["components"]["worker"]["summary"]


def test_eod_date_and_staleness(fresh, monkeypatch):
    rows = [IndexEodRecord(index_code="NIFTY 50", trade_date=date(2026, 10, 12), close=1.0, source="test", checksum="x")]
    body = _build(fresh, CLOSED_NOW, rows=rows)
    assert body["components"]["data_feed"]["eod"]["state"] == "operational" and body["components"]["data_feed"]["eod"]["date"] == "2026-10-12"
    # Two sessions later the same file is stale: alerts on it are held back (G5).
    later = CLOSED_NOW + timedelta(days=2)
    body = _build(fresh, later)
    eod = body["components"]["data_feed"]["eod"]
    assert eod["state"] == "degraded" and "held back" in eod["summary"] and body["components"]["data_feed"]["state"] == "degraded"
    monkeypatch.setattr(entitlement, "eod_as_of", lambda session: _raise())
    assert _build(fresh, later)["components"]["data_feed"]["state"] == "unknown"     # a failing probe is unknown, not a 500


async def _raise():
    raise RuntimeError("boom")


def test_tier_a_sessions_are_a_count_only(fresh):
    async def flag():
        async with fresh() as session:
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": {"public_status_page": {"on": True, "tenants": []},
                                                                                 "byob_data_tier": {"on": True, "tenants": []}}}, None)
            await session.commit()
    _run(flag())
    valid = dict(token_status="VALID", token_expires_at=OPEN_NOW + timedelta(hours=8), encrypted_payload="x")
    rows = [BrokerCredentialRecord(tenant_id=1, user_id=11, broker_name="upstox", account_label="primary", **valid),
            BrokerCredentialRecord(tenant_id=2, user_id=12, broker_name="dhan", account_label="primary", **valid),
            BrokerCredentialRecord(tenant_id=3, user_id=13, broker_name="upstox", account_label="primary", token_status="EXPIRED", encrypted_payload="x"),
            BrokerCredentialRecord(tenant_id=4, user_id=14, broker_name="zerodha", account_label="primary", **valid)]   # terms not confirmed
    body = _build(fresh, OPEN_NOW, rows=rows)
    assert body["components"]["data_feed"]["tier_a"] == {"enabled": True, "sessions": 2, "summary": "2 live sessions through users' own brokers."}
    assert "upstox" not in json.dumps(body) and "dhan" not in json.dumps(body)
    closed = _build(fresh, CLOSED_NOW)["components"]["data_feed"]["tier_a"]
    assert closed["sessions"] == 0 and "open with the market" in closed["summary"]


def test_alert_outbox_lag_bands(fresh):
    def due(seconds_ago, status="PENDING"):
        return AlertDeliveryRecord(tenant_id=1, notification_id=1, channel_id=1, status=status, next_attempt_at=OPEN_NOW - timedelta(seconds=seconds_ago))
    future = AlertDeliveryRecord(tenant_id=1, notification_id=1, channel_id=1, status="PENDING", next_attempt_at=OPEN_NOW + timedelta(hours=1))
    body = _build(fresh, OPEN_NOW, rows=[future, due(5000, "SENT"), due(5000, "FAILED"), due(60)])
    assert body["components"]["alerts"] == {"state": "operational", "lag_seconds": 60, "summary": "Alerts are being delivered."}
    body = _build(fresh, OPEN_NOW, rows=[due(400)])
    assert body["components"]["alerts"]["state"] == "degraded" and body["components"]["alerts"]["lag_seconds"] == 400
    assert "delayed by about 6 minutes" in body["components"]["alerts"]["summary"]
    assert _build(fresh, OPEN_NOW, rows=[due(1800)])["components"]["alerts"]["state"] == "outage"


def test_banner_maintenance_and_incident_bands(fresh):
    async def maintenance():
        async with fresh() as session:
            await controls._set(session, controls.KEY_MAINTENANCE, {"on": True, "message": "Upgrade until 18:00 IST"}, None)
            await session.commit()
    _run(maintenance())
    rows = [IncidentRecord(title="x", severity="WARNING", status="MITIGATED", source="operator", started_at=OPEN_NOW - timedelta(hours=1))]
    body = _build(fresh, OPEN_NOW, rows=rows)
    assert body["banner"]["maintenance"] is True and body["banner"]["maintenance_message"] == "Upgrade until 18:00 IST"
    assert body["banner"]["incidents"] == [{"impact": "minor", "state": "monitoring", "started_at": (OPEN_NOW - timedelta(hours=1)).isoformat()}]
    assert body["status"] == "degraded"                               # an open minor incident lowers an all-green page
    body = _build(fresh, OPEN_NOW, rows=[IncidentRecord(title="y", severity="EMERGENCY", status="OPEN", source="kill_switch")])
    assert any(i["impact"] == "major" for i in body["banner"]["incidents"]) and body["status"] == "outage"


def test_database_down_is_an_outage_body(fresh, monkeypatch):
    from app.observability import routes as health_routes

    async def down(session):
        return {"status": "down", "error": "OperationalError"}
    monkeypatch.setattr(health_routes, "_check_db", down)
    body = _build(fresh, OPEN_NOW)
    assert body["status"] == "outage" and body["components"]["api"]["state"] == "outage"
    assert "OperationalError" not in json.dumps(body)

    status_page.reset_cache()
    rate_limit.reset("public_status")
    r = client.get("/api/status")                                     # answered even though the flag cannot be read
    assert r.status_code == 503 and r.json()["status"] == "outage"
    status_page.reset_cache()


def test_redis_unreachable_reads_as_degraded_api(fresh, monkeypatch):
    from app.observability import routes as health_routes

    async def redis_down():
        return {"status": "unreachable", "error": "ConnectionError"}
    monkeypatch.setattr(health_routes, "_check_redis", redis_down)
    body = _build(fresh, CLOSED_NOW)
    assert body["components"]["api"]["state"] == "degraded" and "ConnectionError" not in json.dumps(body)


def test_hung_database_is_an_outage_within_the_timeout(fresh, monkeypatch):
    from app.observability import routes as health_routes

    async def hang(session):
        await asyncio.sleep(30)
    monkeypatch.setattr(health_routes, "_check_db", hang)
    monkeypatch.setattr(status_page, "DB_CHECK_TIMEOUT_SECONDS", 0.05)
    assert _build(fresh, OPEN_NOW)["status"] == "outage"


def test_failing_flag_read_is_an_outage_not_a_500(fresh, monkeypatch):
    async def broken(session, name, tenant_id=None):
        raise RuntimeError("relation does not exist")
    monkeypatch.setattr(controls, "flag_enabled", broken)
    body = _build(fresh, OPEN_NOW)
    assert body["status"] == "outage" and "relation" not in json.dumps(body)


def test_maintenance_message_is_null_while_maintenance_is_off(fresh):
    async def off_with_message():
        async with fresh() as session:
            await controls._set(session, controls.KEY_MAINTENANCE, {"on": False, "message": "old note"}, None)
            await session.commit()
    _run(off_with_message())
    banner = _build(fresh, OPEN_NOW)["banner"]
    assert banner["maintenance"] is False and banner["maintenance_message"] is None


def test_outage_body_is_cached_briefly_and_builds_are_single_flight(monkeypatch):
    from starlette.responses import Response as StarletteResponse

    calls = []

    async def slow_outage(session, now=None):
        calls.append(1)
        await asyncio.sleep(0.05)
        return status_page._outage(OPEN_NOW)
    monkeypatch.setattr(status_page, "build", slow_outage)
    status_page.reset_cache()

    async def burst():
        responses = [StarletteResponse() for _ in range(5)]
        bodies = await asyncio.gather(*(status_page.public_status(r, session=None) for r in responses))
        return responses, bodies
    responses, bodies = _run(burst())
    assert len(calls) == 1                                           # five requests, one build
    assert all(r.status_code == 503 and r.headers["cache-control"] == "public, max-age=5" for r in responses)
    assert all(b["status"] == "outage" for b in bodies)
    stored_at, ttl, _ = status_page._cache["body"]
    assert ttl == status_page.OUTAGE_CACHE_SECONDS
    status_page._cache["body"] = (stored_at - ttl - 1, ttl, bodies[0])  # expired: the next request builds again
    _run(burst())
    assert len(calls) == 2
    status_page.reset_cache()


def test_turning_the_flag_on_or_off_clears_the_cache(admin_headers):
    status_page._cache["body"] = (status_page.time.monotonic(), 999.0, {"stale": True})
    r = client.put("/api/admin/controls/flags/public_status_page", headers=admin_headers, json={"on": False})
    assert r.status_code == 200, r.text
    assert "body" not in status_page._cache


def test_concurrent_first_stage_move_answers_409(admin_headers, stage_reset, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    async def collide(session, admin, stage, reason):
        raise IntegrityError("INSERT INTO platform_controls", {}, Exception("UNIQUE constraint failed: platform_controls.key"))
    monkeypatch.setattr(invites, "set_stage", collide)
    r = client.put(f"{ADMIN}/cohort/stage", headers=admin_headers, json={"stage": 200, "reason": "race"})
    assert r.status_code == 409 and "UNIQUE" not in r.text


def test_worst_ignores_unknown():
    assert status_page.worst(["unknown", "operational"]) == "operational"
    assert status_page.worst(["degraded", "operational", "outage"]) == "outage"
    assert status_page.worst(["unknown"]) == "unknown"


# --- runbooks -------------------------------------------------------------------------------------------------------------

def _anchors(markdown: str) -> set:
    out = set()
    for line in markdown.splitlines():
        m = re.match(r"^#{1,6}\s+(.*)$", line)
        if m:
            slug = re.sub(r"[^\w\- ]", "", m.group(1).strip().lower()).replace(" ", "-")
            out.add(slug)
    return out


def test_every_alert_runbook_link_resolves_and_each_alert_has_a_page():
    root = Path(__file__).resolve().parents[2]
    ops = (root / "docs/OPERATIONS.md").read_text(encoding="utf-8")
    anchors = _anchors(ops)
    for page in ("191-data-stale", "192-worker-stale", "193-outbox-backlog", "194-broker-down", "195-rollback", "196-staged-beta-rollout-50---200---500"):
        assert page in anchors, page
    rules = (root / "scripts/monitoring/prometheus-alerts.yml").read_text(encoding="utf-8")
    for anchor in re.findall(r'runbook: "docs/OPERATIONS\.md#([^"]+)"', rules):
        assert anchor in anchors, anchor
    for alert, page in (("TradingWorkerStalled", "192"), ("MarketDataStale", "191"), ("AlertOutboxBacklog", "193"), ("BrokerCircuitOpen", "194")):
        block = rules.split(f"alert: {alert}", 1)[1].split("- alert:", 1)[0]
        assert f"OPERATIONS.md#{page}-" in block, alert
    # The Z11 deploy runbook is linked, not copied.
    assert "deploy/HOSTINGER_RUNBOOK.md#9-rollback-and-when-a-migration-makes-it-unsafe" in ops
    assert "deploy/atp.sh rollback" in ops and "--restore-db" in ops
    assert "deploy/HOSTINGER_RUNBOOK.md#4a-phase-0-flags-nothing-to-set" in ops
    assert "backend/scripts/flag_posture.py" in ops and "backend/scripts/session_check.py" in ops and "deploy/atp.sh health" in ops
