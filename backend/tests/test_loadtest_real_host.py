"""Real-host load test (`deploy/atp.sh loadtest`, docs/deploy/LOADTEST_REAL_HOST.md).

Nothing here reaches a server. What is pinned:
- the guards: refused outside NSE market hours (the app's calendar, the clock when it does not answer, too little time
  left before the close) unless --force-offhours; refused unless PAPER-only Phase 0 (exit 10); nothing created by either;
- a whole run against a fake docker: organisation seeded before any client starts, the clients on the compose network
  with one IP each, cleanup after them, the report written; a failed start and a Ctrl-C still clean up; no secret from
  .env in any output;
- the synthetic organisation (backend/scripts/loadtest_org.py) on the test database: users made PAPER-only with no broker
  session; cleanup deletes every row that names them, keeps the audit chain and the users it names as inert tombstones,
  never touches another organisation, refuses an organisation that is not a load-test one, and is idempotent;
- the verdict (deploy/hostinger/loadtest_summary.py): login p95 <= 2 s, every other scenario <= 0.5 s, zero errors, "no
  broker session" reported as not measured, memory peaks per container and for the host;
- loadtest.py's real-host options: the shared-lake chart scenario, the Host header and the name-check-free CA context.
"""
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
ATP_SH = ROOT / "deploy" / "atp.sh"
sys.path.insert(0, str(ROOT / "deploy" / "hostinger"))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))
import loadtest  # noqa: E402
import loadtest_org  # noqa: E402
import loadtest_summary  # noqa: E402

SECRETS = {"POSTGRES_PASSWORD": "FAKEFAKE-pg-11aa", "JWT_SECRET_KEY": "FAKEFAKE-jwt-22bb", "SECRETS_ENCRYPTION_KEY": "FAKEFAKE-key-33cc",
           "METRICS_TOKEN": "FAKEFAKE-metrics-44dd", "BACKUP_ENCRYPTION_PASSPHRASE": "FAKEFAKE-backup-55ee"}
POSTURE_OK = {"live_trading": {"on": False, "tenants": 0, "stored": True}, "phase0_paper_only": {"known": True, "on": True, "stored": True},
              "stored_on": ["phase0_paper_only"], "deployment_phase": "phase0"}
WEDNESDAY_10 = "2026-10-21 10:00"

needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in ("bash", "flock", "openssl", "tar", "sha256sum", "awk")),
                                 reason="needs bash, flock, openssl, tar, sha256sum, awk")


# --- the verdict ---------------------------------------------------------------------------------------------------

def _scenario(p95, outcomes, n=100):
    return {"n": n, "p50_ms": p95 / 3, "p95_ms": p95, "p99_ms": p95 * 1.5, "mean_ms": p95 / 2, "outcomes": outcomes}


def _result(**over):
    scenarios = {"login": _scenario(1800.0, {"ok": 500}), "dashboard /api/auth/me": _scenario(120.0, {"ok": 4000}),
                 "chart": _scenario(4.0, {"unavailable": 300}), "chart_lake": _scenario(310.0, {"ok": 250, "ok_empty": 50}),
                 "screener_run": _scenario(5.0, {"unavailable": 120}), "paper_account": _scenario(20.0, {"disabled": 200})}
    scenarios.update(over)
    return {"users": 500, "seconds": 450.0, "requests": 9000, "rps": 20.0, "scenarios": scenarios, "sessions": {"logged_in": 500}}


def test_slos_login_two_seconds_api_half_a_second_and_no_broker_is_not_measured():
    v = loadtest_summary.judge(_result())
    assert v["slo_pass"], v["reasons"]
    assert v["scenarios"]["login"]["limit_ms"] == 2000 and v["scenarios"]["chart_lake"]["limit_ms"] == 500
    assert v["scenarios"]["chart"]["verdict"] == "NOT MEASURED" and v["scenarios"]["paper_account"]["verdict"] == "NOT MEASURED"
    assert any("no broker session" in x for x in v["not_measured"]) and any("switched off" in x for x in v["not_measured"])
    assert v["scenarios"]["chart_lake"]["verdict"] == "PASS" and v["scenarios"]["chart_lake"]["empty"] == 50


@pytest.mark.parametrize("over, reason", [
    ({"login": _scenario(2100.0, {"ok": 500})}, "login p95 2,100 ms is over 2,000 ms"),
    ({"dashboard /api/auth/me": _scenario(510.0, {"ok": 4000})}, "dashboard /api/auth/me p95 510 ms is over 500 ms"),
    ({"chart_lake": _scenario(100.0, {"ok": 99, "server_error": 1})}, "chart_lake: 1 error(s) (server_error 1)"),
    ({"dashboard /api/auth/me": _scenario(100.0, {"ok": 99, "client_error": 1})}, "client_error 1"),
    ({"login": _scenario(900.0, {"ok": 499, "throttled": 1})}, "throttled 1"),
    ({"login": _scenario(900.0, {"ok": 499, "transport": 1})}, "transport 1"),
    ({"login": _scenario(0.0, {"unavailable": 500})}, "login was not measured"),
])
def test_each_slo_miss_fails_the_run_with_a_plain_reason(over, reason):
    v = loadtest_summary.judge(_result(**over))
    assert not v["slo_pass"] and any(reason in r for r in v["reasons"]), v["reasons"]


def test_users_without_a_login_and_a_missing_result_fail():
    r = _result()
    r["sessions"] = {"logged_in": 490, "no_login": 10}
    assert any("10 of 500 users could not log in" in x for x in loadtest_summary.judge(r)["reasons"])
    assert not loadtest_summary.judge(None)["slo_pass"] and not loadtest_summary.judge({"requests": 0})["slo_pass"]


def _write_run(tmp_path, result=None, cleanup=None):
    d = tmp_path / "run"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"stamp": "20261021-101500", "client_prefix": "atp-loadtest-20261021-101500-", "vcpus": 2,
                                             "client_cpus_per_shard": 0.25, "market": "open (NSE)", "sample_seconds": 5,
                                             "flags_unchanged": "yes"}))
    if result is not None:
        (d / "result.json").write_text(json.dumps(result))
    if cleanup is not None:
        (d / "cleanup.json").write_text(json.dumps(cleanup))
    (d / "docker-stats.csv").write_text(
        "epoch,container,cpu,mem_usage,mem_pct\n"
        "100,atp-backend-1,80.5%,600MiB / 1.5GiB,39%\n100,atp-postgres-1,20%,1.2GiB / 2GiB,60%\n"
        "100,atp-loadtest-20261021-101500-0,10%,60MiB / 192MiB,31%\n100,atp-loadtest-20261021-101500-1,14%,62MiB / 192MiB,32%\n"
        "105,atp-backend-1,150%,900MiB / 1.5GiB,58%\n105,atp-postgres-1,40%,1.25GiB / 2GiB,62%\n"
        "105,atp-loadtest-20261021-101500-0,23%,70MiB / 192MiB,36%\n105,atp-loadtest-20261021-101500-1,24%,64MiB / 192MiB,33%\n")
    (d / "host-memory.csv").write_text(
        "epoch,mem_total_kb,mem_free_kb,mem_available_kb,swap_total_kb,swap_free_kb,pswpin,pswpout\n"
        "100,8000000,3000000,5000000,2097152,2000000,10,20\n105,8000000,2500000,4000000,2097152,1900000,12,30\n")
    return d


def test_report_has_the_table_memory_peaks_client_cpu_and_cleanup(tmp_path):
    clean = {"tenant": "loadtest-20261021-101500", "clean": True, "deleted": {"sessions": 500, "users": 0},
             "ledger_rows_kept": {"audit_logs": 502}, "remaining": {}, "tombstone": {"status": "suspended", "users_kept": 500,
                                                                                    "active_users": 0, "usable_passwords": 0},
             "audit_chain": "intact"}
    text, terminal, code = loadtest_summary.build(_write_run(tmp_path, _result(), clean))
    assert code == 0 and "SLO verdict: PASS" in text and "Cleanup: verified clean" in text
    assert "| login | 100 |" in text and "| 2,000 | PASS |" in text and "| chart | 100 |" in text
    assert "| atp-backend-1 | 750 | 900 | 1,536 | 59% | 115 | 150 |" in text           # MiB mean / peak / limit; CPU mean / peak
    assert "| atp-postgres-1 |" in text and "atp-loadtest" not in text.split("## Memory (LAB-6)")[1].split("Host (")[0]
    assert "| MemAvailable, lowest | 3,906 |" in text and "| Swap used, peak | 193 |" in text
    assert "CPU: mean 36% / peak 47% of one vCPU" in text and "audit_logs 502" in text and "Audit chain (from its latest anchor): intact" in text
    assert "LOAD TEST 20261021-101500 - SLO: PASS" in terminal and "Memory peaks (MiB): atp-backend-1 900" in terminal


def test_an_interrupted_run_still_gets_a_report_and_an_unverified_cleanup_outranks_it(tmp_path):
    text, terminal, code = loadtest_summary.build(_write_run(tmp_path, None, {"tenant": "x", "clean": False, "remaining": {"sessions": 3}}))
    assert code == 4 and "no load result" in text and "Cleanup: NOT VERIFIED" in text and "sessions 3" in text
    assert loadtest_summary.build(_write_run(tmp_path / "b", None, {"tenant": "x", "clean": True}))[2] == 1


def test_exit_codes_put_the_cleanup_first_then_an_abort_then_the_slos(tmp_path):
    clean, dirty = {"tenant": "x", "clean": True}, {"tenant": "x", "clean": False}
    slow = _result(login=_scenario(2500.0, {"ok": 500}))
    cases = [(_result(), clean, None, 0), (slow, clean, None, 1), (_result(), clean, "deep health failed", 5),
             (slow, dirty, "deep health failed", 4), (_result(), dirty, None, 4)]
    for i, (result, cleanup, aborted, code) in enumerate(cases):
        d = _write_run(tmp_path / str(i), result, cleanup)
        if aborted:
            meta = json.loads((d / "meta.json").read_text())
            (d / "meta.json").write_text(json.dumps({**meta, "aborted": aborted}))
        text, _, got = loadtest_summary.build(d)
        assert got == code, (i, got)
        if aborted:
            assert "stopped early: production degraded - deep health failed" in text and "ABORTED by the watchdog" in text


def test_slos_met_but_cleanup_not_verified_exits_4(tmp_path):
    assert loadtest_summary.build(_write_run(tmp_path, _result(), None))[2] == 4


@pytest.mark.parametrize("left, code", [(0, 0), (1, 4), ("unknown", 4)])
def test_a_load_test_network_left_over_is_a_cleanup_not_verified(tmp_path, left, code):
    """Review follow-up: a failed Caddy detach leaves the network; that outranks an abort and the SLOs, as 4."""
    d = _write_run(tmp_path, _result(), {"tenant": "x", "clean": True})
    meta = json.loads((d / "meta.json").read_text())
    (d / "meta.json").write_text(json.dumps({**meta, "network_left": left, "aborted": "deep health failed" if code else None}))
    text, terminal, got = loadtest_summary.build(d)
    assert got == code
    if code:
        assert "Cleanup: NOT VERIFIED - the load-test network is still there" in text and "Verified: NO" in text
        assert "loadtest-cleanup --all" in terminal


# --- loadtest.py: the real-host options ----------------------------------------------------------------------------

def test_chart_lake_is_off_by_default_and_reads_a_year_of_daily_bars():
    assert [s for s, _ in loadtest.scenarios()] == [s for s, _ in loadtest.EXTRA]
    assert ("chart_lake", 0.25) in loadtest.scenarios(True)
    p = loadtest.lake_params("TCS", now=1_790_000_000)
    assert p["instrument_key"] == "NSE:TCS" and p["timeframe"] == "day" and p["start"].startswith("2025-") and p["end"].startswith("2026-")


def test_chart_lake_without_bars_is_ok_empty_and_still_judged():
    def handler(request):
        assert request.headers["host"] == "203.0.113.7"                      # --host-header reaches the edge
        bars = [{"close": 1}] if "TCS" in str(request.url) else []
        return httpx.Response(200, json={"candles": bars})

    async def go():
        stats = loadtest.Stats()
        async with httpx.AsyncClient(base_url="https://172.18.0.9", transport=httpx.MockTransport(handler), headers={"Host": "203.0.113.7"}) as c:
            for sym in ("TCS", "INFY"):
                await loadtest._timed(stats, "chart_lake", lambda sym=sym: c.get("/api/market-data/history", params=loadtest.lake_params(sym)))
        return stats

    stats = asyncio.run(go())
    assert dict(stats.outcomes["chart_lake"]) == {"ok": 1, "ok_empty": 1}
    only_empty = loadtest.Stats()
    only_empty.add("chart_lake", 10.0, "ok_empty")
    assert loadtest.report(only_empty, 1, 1.0)["scenarios"]["chart_lake"]["pass"]


def test_ssl_context_keeps_the_chain_check_and_can_drop_the_name_check(tmp_path):
    import ssl
    shared = loadtest.ssl_context(None)
    assert isinstance(shared, ssl.SSLContext) and shared.check_hostname and shared.verify_mode == ssl.CERT_REQUIRED
    ca = Path(ssl.get_default_verify_paths().cafile or "")
    if not ca.is_file():
        pytest.skip("no system CA bundle to load")
    ctx = loadtest.ssl_context(str(ca), verify_hostname=False)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is False
    assert loadtest.ssl_context(str(ca)).check_hostname is True


# --- the synthetic organisation on the test database --------------------------------------------------------------

@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setenv("ATP_DEPLOYMENT_PHASE", "phase0")
    from tests import test_auth_api as api
    return api


_STAMPS = iter(range(-600, 600, 7))


def _stamp():
    """A fresh stamp near now (IST), as atp.sh makes one: a different second for every call."""
    from datetime import datetime, timedelta
    return (datetime.now(loadtest_org.IST) + timedelta(seconds=next(_STAMPS))).strftime("%Y%m%d-%H%M%S")


def _run(coro):
    return asyncio.run(coro)


def _audit_rows(api, tenant_id):
    from sqlalchemy import select

    from app.db.models import AuditLogRecord

    async def go():
        async with api._session_factory() as s:
            return [(r.id, r.hash, r.event) for r in await s.scalars(select(AuditLogRecord).where(AuditLogRecord.tenant_id == tenant_id).order_by(AuditLogRecord.id))]
    return _run(go())


def _seeded(api, n):
    stamp = _stamp()
    accounts, info = _run(loadtest_org.seed(stamp, n, api._session_factory))
    return stamp, accounts, info


def test_seed_login_cleanup_keeps_the_audit_chain_and_leaves_nothing_usable(db):
    api = db
    other = api._register("loadtest-bystander@example.com")
    stamp, accounts, info = _seeded(api, 5)
    assert info["users"] == 5 and [e for e, _ in accounts] == [loadtest_org.email_for(stamp, i) for i in range(5)] and len(info["user_ids"]) == 5
    password = accounts[0][1]
    tokens = []
    for email, _ in accounts[:2]:
        r = api.client.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        tokens.append(r.json()["access_token"])
    head = {"Authorization": f"Bearer {tokens[1]}"}
    for path in ("/api/auth/me", "/api/notifications", "/api/deployments", "/api/portfolio/exposure", "/api/paper/account"):
        assert api.client.get(path, headers=head).status_code in (200, 404), path
    chart = api.client.post("/api/market-data/candles", headers=head, json={"symbols": ["TCS"], "timeframe": "5min"})
    assert chart.status_code == 409                                               # no broker session: refused before any broker call

    before = _run(loadtest_org.counts(stamp, api._session_factory))
    assert before["users"] == 5 and before["rows"]["login_events"] == 2 and before["rows"]["audit_logs"] >= 3
    assert before["rows"]["tenant_keys"] == 1                                     # L2: the data key a sign-up gets
    assert "broker_credentials" not in before["rows"]
    audit_before = _audit_rows(api, info["tenant_id"])

    report = _run(loadtest_org.cleanup(stamp, api._session_factory, tenant_id=info["tenant_id"], user_ids=info["user_ids"]))
    assert report["clean"], report
    assert report["deleted"]["login_events"] == 2 and report["deleted"]["tenant_keys"] == 1
    assert report["deleted"]["users"] == 3                                        # the 3 that never logged in go entirely
    assert set(report["deleted"]) <= loadtest_org.ALLOWLIST | {"users"}
    assert report["tombstone"]["users_kept"] == 2 and report["tombstone"]["active_users"] == 0
    assert report["tombstone"]["usable_passwords"] == 0 and report["tombstone"]["status"] == "suspended"
    assert report["remaining"] == {} and report["ledger_rows_kept"]["audit_logs"] == len(audit_before) + 1
    assert _audit_rows(api, info["tenant_id"])[:-1] == audit_before                # the chain's rows are untouched; one row appended
    assert _audit_rows(api, info["tenant_id"])[-1][2] == loadtest_org.CLEAN_EVENT
    again = _run(loadtest_org.cleanup(stamp, api._session_factory, tenant_id=info["tenant_id"]))   # idempotent, no new audit row
    assert again["clean"] and again["deleted"] == {} and len(_audit_rows(api, info["tenant_id"])) == len(audit_before) + 1

    assert api.client.get("/api/auth/me", headers=head).status_code == 401         # the session is gone
    for email, _ in accounts:                                                      # an ordinary 401, never a 500
        assert api.client.post("/api/auth/login", json={"email": email, "password": password}).status_code == 401
    assert api.client.get("/api/auth/me", headers={"Authorization": f"Bearer {other}"}).status_code == 200   # the bystander is untouched
    assert _run(loadtest_org.cleanup(stamp, api._session_factory))["clean"]          # and the refused attempts' login events go too
    listed = {r["stamp"]: r for r in _run(loadtest_org.leftovers(api._session_factory))}
    assert listed[stamp]["active_users"] == 0 and listed[stamp]["ours"] and listed[stamp]["tenant_id"] == info["tenant_id"]


def test_reviewer_poc_a_sign_up_renamed_to_look_like_a_load_test_org_is_refused(db):
    """PR #284 review H1: a real sign-up with a load-test address, renamed to loadtest-<stamp>, is never listed as ours
    and never cleaned; platform-wide rows its user made survive."""
    api = db
    from datetime import date

    from sqlalchemy import select

    from app.db.models import MarketEventRecord, Tenant, User
    stamp = _stamp()
    email = loadtest_org.email_for(stamp, 0)
    tok = api._register(email)
    r = api.client.patch("/api/team/tenant", headers={"Authorization": f"Bearer {tok}"}, json={"name": loadtest_org.tenant_name(stamp)})
    assert r.status_code == 200, r.text

    async def plant():
        async with api._session_factory() as s:
            u = await s.scalar(select(User).where(User.email == email))
            s.add(MarketEventRecord(tenant_id=None, event_date=date(2026, 10, 20), kind="OTHER", description="GLOBAL event H1", created_by=u.id))
            await s.commit()
            return (await s.scalar(select(Tenant).where(Tenant.name == loadtest_org.tenant_name(stamp)))).id
    tid = _run(plant())
    assert stamp not in {r["stamp"] for r in _run(loadtest_org.leftovers(api._session_factory)) if r["ours"]}
    for kwargs in ({}, {"tenant_id": tid}, {"tenant_id": tid, "user_ids": [1, 2, 3]}):
        with pytest.raises(loadtest_org.Refused, match="no loadtest_org_seeded audit record"):
            _run(loadtest_org.cleanup(stamp, api._session_factory, **kwargs))

    async def still():
        async with api._session_factory() as s:
            return (await s.scalar(select(MarketEventRecord.id).where(MarketEventRecord.description == "GLOBAL event H1")),
                    (await s.get(Tenant, tid)).status, await s.scalar(select(User.is_active).where(User.email == email)))
    event, status, active = _run(still())
    assert event is not None and status == "active" and active is True
    assert api.client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 200


def test_a_renamed_or_joined_or_foreign_id_load_test_org_is_refused_whole(db):
    api = db
    from sqlalchemy import select, update

    from app.auth.security import hash_password
    from app.db.models import Tenant, User
    stamp, accounts, info = _seeded(api, 2)
    with pytest.raises(loadtest_org.Refused, match="its seed did not make"):           # an id the org file does not list
        _run(loadtest_org.cleanup(stamp, api._session_factory, tenant_id=info["tenant_id"], user_ids=info["user_ids"][:1]))
    with pytest.raises(loadtest_org.Refused, match="no loadtest_org_seeded audit record"):   # the wrong organisation id
        _run(loadtest_org.cleanup(stamp, api._session_factory, tenant_id=info["tenant_id"] + 999))

    async def add_member(email):
        async with api._session_factory() as s:
            s.add(User(tenant_id=info["tenant_id"], email=email, hashed_password=hash_password("x-Pass-123456"), role="USER"))
            await s.commit()
    _run(add_member("real.person@example.org"))
    with pytest.raises(loadtest_org.Refused, match="its seed did not make"):
        _run(loadtest_org.cleanup(stamp, api._session_factory))

    stamp2, _, info2 = _seeded(api, 1)

    async def rename(name):
        async with api._session_factory() as s:
            await s.execute(update(Tenant).where(Tenant.id == info2["tenant_id"]).values(name=name))
            await s.commit()
    _run(rename("Someone's real trading desk"))
    with pytest.raises(loadtest_org.Refused, match="renamed"):
        _run(loadtest_org.cleanup(stamp2, api._session_factory, tenant_id=info2["tenant_id"], user_ids=info2["user_ids"]))
    listed = {r["tenant_id"]: r for r in _run(loadtest_org.leftovers(api._session_factory))}
    assert not listed[info["tenant_id"]]["ours"] and not listed[info2["tenant_id"]]["ours"]

    async def active():
        async with api._session_factory() as s:
            return list(await s.scalars(select(User.is_active).where(User.tenant_id.in_([info["tenant_id"], info2["tenant_id"]]))))
    assert all(_run(active()))


def test_an_org_with_no_member_is_not_ours(db):
    api = db
    from sqlalchemy import delete

    from app.db.models import User
    stamp, _, info = _seeded(api, 1)

    async def empty():
        async with api._session_factory() as s:
            await s.execute(delete(User).where(User.tenant_id == info["tenant_id"]))
            await s.commit()
    _run(empty())
    with pytest.raises(loadtest_org.Refused, match="no member"):
        _run(loadtest_org.cleanup(stamp, api._session_factory, tenant_id=info["tenant_id"]))
    assert not {r["tenant_id"]: r for r in _run(loadtest_org.leftovers(api._session_factory))}[info["tenant_id"]]["ours"]


def test_platform_wide_and_other_tenant_rows_survive_the_cleanup(db):
    """PR #284 review H2: rows outside the allowlist that only name a load-test user or the organisation through a
    "who acted" / other column are never deleted: a platform-wide market event, a platform control, another
    organisation's marketplace charge naming the load-test organisation as its creator."""
    api = db
    from datetime import date

    from sqlalchemy import delete, select

    from app.db.models import (CustomStrategyRecord, MarketEventRecord, MarketplaceChargeRecord, MarketplaceListingRecord,
                               PlatformControlRecord, User)
    api._register("h2.bystander@example.org")
    stamp, accounts, info = _seeded(api, 2)
    lt_user = info["user_ids"][0]

    async def plant():
        async with api._session_factory() as s:
            other = await s.scalar(select(User).where(User.email == "h2.bystander@example.org"))
            s.add(MarketEventRecord(tenant_id=None, event_date=date(2026, 10, 20), kind="OTHER", description="GLOBAL event H2", created_by=lt_user))
            s.add(PlatformControlRecord(key="loadtest_h2_probe", value_json="{}", updated_by=lt_user))
            strat = CustomStrategyRecord(tenant_id=other.tenant_id, user_id=other.id, name="h2", config_json="{}")
            s.add(strat)
            await s.flush()
            listing = MarketplaceListingRecord(tenant_id=other.tenant_id, custom_strategy_id=strat.id, version_number=1, config_json="{}",
                                               title="h2", description="h2")
            s.add(listing)
            await s.flush()
            s.add(MarketplaceChargeRecord(listing_id=listing.id, creator_tenant_id=info["tenant_id"], tenant_id=other.tenant_id, user_id=other.id,
                                          amount=1.0, platform_fee_pct=10.0, platform_fee=0.1, creator_net=0.9))
            await s.commit()
    _run(plant())
    report = _run(loadtest_org.cleanup(stamp, api._session_factory, tenant_id=info["tenant_id"], user_ids=info["user_ids"]))
    assert report["clean"], report
    assert set(report["deleted"]) <= loadtest_org.ALLOWLIST | {"users"} and "market_events" not in report["deleted"]

    async def survivors():
        async with api._session_factory() as s:
            out = (await s.scalar(select(MarketEventRecord.created_by).where(MarketEventRecord.description == "GLOBAL event H2")),
                   await s.scalar(select(PlatformControlRecord.updated_by).where(PlatformControlRecord.key == "loadtest_h2_probe")),
                   await s.scalar(select(MarketplaceChargeRecord.creator_tenant_id).where(MarketplaceChargeRecord.creator_tenant_id == info["tenant_id"])),
                   await s.get(User, lt_user))
            await s.execute(delete(PlatformControlRecord).where(PlatformControlRecord.key == "loadtest_h2_probe"))
            await s.commit()
            return out
    event_by, control_by, charge_creator, user = _run(survivors())
    assert event_by == lt_user and control_by == lt_user and charge_creator == info["tenant_id"]
    assert user is not None and not user.is_active                                # named elsewhere: kept as a tombstone, never deleted


def test_rows_the_org_owns_outside_the_allowlist_refuse_the_whole_cleanup(db):
    api = db
    from sqlalchemy import func, select

    from app.db.models import CustomStrategyRecord, UserSessionRecord
    from sqlalchemy import update

    from app.db.models import User
    stamp, accounts, info = _seeded(api, 1)
    bystander = api._register(f"refused.bystander.{stamp}@example.org")             # another organisation: must stay as it is
    assert api.client.post("/api/auth/login", json={"email": accounts[0][0], "password": accounts[0][1]}).status_code == 200

    async def plant():
        async with api._session_factory() as s:
            s.add(CustomStrategyRecord(tenant_id=info["tenant_id"], user_id=info["user_ids"][0], name="h2b", config_json="{}"))
            await s.execute(update(User).where(User.id == info["user_ids"][0]).values(mfa_enabled=True, mfa_secret_encrypted="x"))
            await s.commit()
    _run(plant())
    with pytest.raises(loadtest_org.Refused, match="owns rows a load test does not write: custom_strategies 1"):
        _run(loadtest_org.cleanup(stamp, api._session_factory, tenant_id=info["tenant_id"], user_ids=info["user_ids"]))

    async def sessions():
        async with api._session_factory() as s:
            return await s.scalar(select(func.count()).select_from(UserSessionRecord).where(UserSessionRecord.tenant_id == info["tenant_id"]))
    assert _run(sessions()) == 1                                                  # nothing was deleted

    # Review follow-up: the proven organisation is still made inert before the refusal - no deletes.
    from app.db.models import AuditLogRecord, Tenant

    async def state():
        async with api._session_factory() as s:
            tenant = await s.get(Tenant, info["tenant_id"])
            users = list(await s.scalars(select(User).where(User.tenant_id == info["tenant_id"])))
            audit = list(await s.scalars(select(AuditLogRecord.event).where(AuditLogRecord.tenant_id == info["tenant_id"])))
            return tenant.status, tenant.status_reason, [(u.is_active, u.mfa_enabled, u.mfa_secret_encrypted) for u in users], audit

    async def bystander_state():
        async with api._session_factory() as s:
            user = await s.scalar(select(User).where(User.email == f"refused.bystander.{stamp}@example.org"))
            return user.is_active, (await s.get(Tenant, user.tenant_id)).status
    status, reason, users, audit = _run(state())
    assert status == "suspended" and reason.startswith(f"loadtest {stamp}: ") and users == [(False, False, None)]   # MFA reset too
    assert audit.count(loadtest_org.CLEAN_EVENT) == 1
    assert _run(bystander_state()) == (True, "active")
    assert api.client.get("/api/auth/me", headers={"Authorization": f"Bearer {bystander}"}).status_code == 200
    assert api.client.post("/api/auth/login", json={"email": accounts[0][0], "password": accounts[0][1]}).status_code == 401
    with pytest.raises(loadtest_org.Refused, match="users were deactivated"):            # again: still refused, no new audit row
        _run(loadtest_org.cleanup(stamp, api._session_factory, tenant_id=info["tenant_id"], user_ids=info["user_ids"]))
    assert _run(state())[3].count(loadtest_org.CLEAN_EVENT) == 1 and _run(sessions()) == 1

    async def unplant():                                       # the planted strategy is outside the allowlist: removed here
        from sqlalchemy import delete
        async with api._session_factory() as s:
            await s.execute(delete(CustomStrategyRecord).where(CustomStrategyRecord.tenant_id == info["tenant_id"], CustomStrategyRecord.name == "h2b"))
            await s.commit()
    _run(unplant())


def test_the_org_file_is_seeds_and_the_stamp_is_ascii_and_current(db):
    api = db
    stamp, _, info = _seeded(api, 1)
    line = "org," + json.dumps({"stamp": stamp, "tenant_id": info["tenant_id"], "user_ids": info["user_ids"]})
    assert loadtest_org.read_org_file("noise\n" + line + "\n", stamp) == (info["tenant_id"], info["user_ids"])
    for bad in ("", "org,{}", "org," + json.dumps({"stamp": "20200101-000000", "tenant_id": 1, "user_ids": [1]}),
                "org," + json.dumps({"stamp": stamp, "tenant_id": 1, "user_ids": []})):
        with pytest.raises(loadtest_org.Refused):
            loadtest_org.read_org_file(bad, stamp)
    for bad in ("2026-10-21; drop table users", "٢٠٢٦١٠٢١-101500", "20261021-1015001"):
        with pytest.raises(loadtest_org.Refused):
            loadtest_org.check_stamp(bad)
    assert not loadtest_org.is_loadtest_email(stamp, f"lt-{stamp}-١٢٣٤@example.com")
    with pytest.raises(loadtest_org.Refused, match="not the current IST time"):
        _run(loadtest_org.seed("20200101-101500", 1, api._session_factory))


def test_absent_ok_is_clean_only_when_no_seed_record_exists_and_ledgers_are_never_on_the_list(db):
    api = db
    assert not loadtest_org.ALLOWLIST & loadtest_org.LEDGERS                     # never deleted from, whatever the role may do
    never = _stamp()
    r = _run(loadtest_org.cleanup(never, api._session_factory, absent_ok=True))
    assert r["clean"] and r["exists"] is False and "never committed" in r["note"]
    with pytest.raises(loadtest_org.Refused):
        _run(loadtest_org.cleanup(never, api._session_factory))                  # without --absent-ok: refused, never "clean"
    stamp, _, info = _seeded(api, 1)
    r = _run(loadtest_org.cleanup(stamp, api._session_factory, absent_ok=True))    # a record exists: the real cleanup runs
    assert r["clean"] and r["tenant_id"] == info["tenant_id"] and r["tombstone"]["status"] == "suspended"


def test_seed_refuses_unless_paper_only_phase0_and_creates_nothing(db, monkeypatch):
    api = db
    from app.platform import controls

    async def set_flags(flags):
        async with api._session_factory() as s:
            await controls._set(s, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await s.commit()
    stamp = _stamp()
    try:
        _run(set_flags({"live_trading": {"on": False, "tenants": [7]}}))
        with pytest.raises(loadtest_org.Refused, match="live_trading is on for 1 organisation"):
            _run(loadtest_org.seed(stamp, 2, api._session_factory))
        _run(set_flags({"phase0_paper_only": {"on": False, "tenants": []}}))
        with pytest.raises(loadtest_org.Refused, match="phase0_paper_only is not on"):
            _run(loadtest_org.seed(stamp, 2, api._session_factory))
        _run(set_flags({}))
        monkeypatch.setenv("ATP_DEPLOYMENT_PHASE", "")
        with pytest.raises(loadtest_org.Refused, match="not phase0"):
            _run(loadtest_org.seed(stamp, 2, api._session_factory))
    finally:
        _run(set_flags({}))
    with pytest.raises(loadtest_org.Refused, match="no loadtest_org_seeded audit record"):
        _run(loadtest_org.counts(stamp, api._session_factory))


# --- atp.sh loadtest against a fake host ---------------------------------------------------------------------------

FAKE_DOCKER = r'''
import fcntl, json, os, subprocess, sys, time
STATE = os.environ["FAKE_STATE"]
_lock = open(STATE + ".lock", "w")
fcntl.flock(_lock, fcntl.LOCK_EX)          # the samplers call docker while the script does: one state change at a time
with open(STATE) as fh:
    st = json.load(fh)
argv = sys.argv[1:]
st["calls"].append(argv)
rc, out = 0, []

def save():
    with open(STATE, "w") as fh:
        json.dump(st, fh)

def finish():
    save()
    if out:
        sys.stdout.write("\n".join(out) + "\n")
    sys.exit(rc)

def flag(name):
    return argv[argv.index(name) + 1] if name in argv else None

if argv[:1] == ["info"]:
    finish()
if argv[:2] == ["compose", "version"]:
    out.append("2.29.1"); finish()
if argv[:2] == ["image", "inspect"]:
    finish()
if argv[:1] == ["network"]:
    sub = argv[1]
    if sub == "create":
        st["networks"].append({"name": argv[-1], "internal": "--internal" in argv, "label": flag("--label")})
        out.append("net-" + argv[-1])
    elif sub == "connect":
        st["connected"].append(argv[2])
    elif sub == "disconnect":
        if st.get("fail_disconnect"):              # Caddy stays attached; the network cannot be removed under it
            rc = 1; finish()
        st["disconnected"].append(argv[-2])
        st["connected"] = [n for n in st["connected"] if n != argv[-2]]
    elif sub == "rm":
        if argv[-1] in st["connected"]:
            rc = 1; finish()
        st["networks"] = [n for n in st["networks"] if n["name"] != argv[-1]]
    elif sub == "ls":
        label = next((a.split("=", 1)[1] for a in argv if a.startswith("label=")), "")
        out += [n["name"] for n in st["networks"] if n["label"] == label or (label == "atp.loadtest" and n["label"].startswith("atp.loadtest="))]
    finish()
if argv[:1] == ["inspect"]:
    fmt = argv[2]
    if "RestartCount" in fmt:
        st["restart_checks"] = st.get("restart_checks", 0) + 1
        bumped = st.get("bump_restart") and st["restart_checks"] > 1
        out += [f"/atp-{i}-1 {1 if bumped else 0} false" for i in argv[3:]]
    else:
        out.append("172.30.0.2" if "atp-loadtest-" in fmt else "atp_default ")
    finish()
if argv[:1] == ["stats"]:
    out += ["atp-backend-1,50%,500MiB / 1.5GiB,33%"] + [f"{c},5%,60MiB / 128MiB,31%" for c in st["containers"]]; finish()
if argv[:1] == ["ps"]:
    label = next((a.split("=", 1)[1] for a in argv if a.startswith("label=")), "")
    names = [c for c in st["containers"] if label == "atp.loadtest" or c.startswith("atp-loadtest-" + label.split("=")[-1] + "-")]
    if "-aq" not in argv and not st.get("keep_running"):
        names = []
    out += names; finish()
if argv[:1] == ["logs"]:
    out.append("shard log"); finish()
if argv[:2] == ["rm", "-f"]:
    st["removed"] += argv[2:]
    st["containers"] = [c for c in st["containers"] if c not in argv[2:]]; finish()
if argv[:1] == ["run"]:
    vol = flag("-v")
    host_dir = vol.rsplit(":/lt", 1)[0]
    if "--merge" in argv:
        files = [a.replace("/lt", host_dir, 1) for a in argv[argv.index("--merge") + 1:] if a.startswith("/lt/shard-")]
        res = subprocess.run([sys.executable, os.environ["LOADTEST_PY"], "--merge", *files, "--json-out", host_dir + "/result.json"],
                             capture_output=True, text=True)
        st["merged"] = res.returncode in (0, 1); finish()
    if st.get("fail_shard_start"):
        rc = 125; finish()
    name = flag("--name")
    st["containers"].append(name)
    st["shards"].append({"name": name, "network": flag("--network"), "cpus": flag("--cpus"), "shares": flag("--cpu-shares"),
                         "memory": flag("--memory"), "add_host": flag("--add-host"), "base": flag("--base"), "host_header": flag("--host-header"),
                         "allowed": next((a for a in argv if a.startswith("LOADTEST_ALLOWED_HOSTS=")), None),
                         "chart_lake": "--chart-lake" in argv, "user": flag("--user"), "read_only": "--read-only" in argv})
    raw = flag("--raw-out").replace("/lt", host_dir, 1)
    n = int(flag("--users"))
    lat = {"login": [300.0] * n, "dashboard /api/auth/me": [60.0] * (4 * n), "chart": [4.0] * n, "chart_lake": [90.0] * n}
    outc = {"login": {"ok": n}, "dashboard /api/auth/me": {"ok": 4 * n}, "chart": {"unavailable": n}, "chart_lake": {"ok": n}}
    with open(raw, "w") as fh:
        json.dump({"users": n, "seconds": 60.0, "latency": lat, "outcomes": outc, "sessions": {"logged_in": n}}, fh)
    out.append("cid-" + name); finish()
if argv[0] != "compose":
    rc = 2; finish()
rest, i = [], 1
while i < len(argv):
    if argv[i] in ("-f", "-p"):
        i += 2; continue
    rest.append(argv[i]); i += 1
sub, args = rest[0], rest[1:]
if sub == "ps" and "--services" in args:
    out += st["running"]; finish()
if sub == "ps" and "-q" in args:
    out.append("caddyid"); finish()
if sub == "exec" and "postgres" in args:
    sql = args[args.index("-c") + 1].lower() if "-c" in args else ""
    if "to_regclass" in sql:
        out.append("platform_controls")
    elif "value_json" in sql:
        out.append(st["flags_json"])
    elif "broker_credentials" in sql:
        st["creds_sql"] = sql
        out.append(str(st.get("creds", 0)))
    elif "md5" in sql:
        out.append("aaa:bbb")
    finish()
if sub == "exec" and "backend" in args:
    script = " ".join(args)
    if "flag_posture" in script:
        if st["posture"] is None:
            rc = 1
        else:
            out.append(json.dumps(st["posture"]))
    elif "metrics" in script:
        out.append("# HELP none")
    finish()
if sub == "exec" and "caddy" in args:
    out.append("-----BEGIN CERTIFICATE-----"); finish()
if sub == "run" and "backend" in args:
    script = " ".join(args)
    if "session_check.py" in script:
        if st["session"] is None:
            rc = 1
        else:
            out.append(json.dumps(st["session"]))
        finish()
    if "loadtest_org.py" in script:
        cmd = args[args.index("scripts/loadtest_org.py") + 1]
        stamp = flag("--stamp")
        st["org_calls"].append([cmd, stamp])
        if cmd == "seed":
            n = int(flag("--count"))
            st["seeded"] = stamp
            st["seed_name"] = flag("--name")
            if st.get("seed_hang"):                  # Ctrl-C during seeding: runs until its container is removed
                st["seed_started"] = True
                save()
                fcntl.flock(_lock, fcntl.LOCK_UN)
                for _ in range(300):
                    time.sleep(0.1)
                    with open(STATE) as fh:
                        if flag("--name") in json.load(fh)["removed"]:
                            sys.exit(137)
                sys.exit(137)
            sys.stdout.write("a stray log line\n" + "".join(f"account,lt-{stamp}-{k:04d}@example.com,pw-throwaway\n" for k in range(n)))
            sys.stdout.write("org," + json.dumps({"stamp": stamp, "tenant_id": 9, "user_ids": list(range(100, 100 + n))}) + "\n")
            print(json.dumps({"tenant_id": 9, "users": n}), file=sys.stderr); finish()
        if cmd == "list":
            out.append(json.dumps(st["orgs"])); finish()
        if cmd == "cleanup":
            st["cleanup_args"].append(args[args.index("scripts/loadtest_org.py") + 1:])
            st["cleanup_stdin"].append(sys.stdin.read() if "--org-file" in args else "")
            if st.get("refuse_cleanup"):
                out.append(json.dumps({"refused": "organisation #9 owns rows a load test does not write: custom_strategies 1"})); rc = 2; finish()
            out.append(json.dumps({"tenant": "loadtest-" + stamp, "tenant_id": 9, "clean": st.get("clean", True), "deleted": {"user_sessions": 4, "users": 0},
                                   "ledger_rows_kept": {"audit_logs": 6}, "remaining": {}, "failed": {}, "audit_chain": "intact",
                                   "tombstone": {"tenant_id": 9, "status": "suspended", "users_kept": 4, "active_users": 0, "usable_passwords": 0}}))
            finish()
rc = 9; finish()
'''

FAKE_CURL = r'''
import fcntl, json, os, sys
state = os.environ["FAKE_STATE"]
with open(state + ".lock", "w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    with open(state) as fh:
        st = json.load(fh)
    st["deep_calls"] = st.get("deep_calls", 0) + 1
    with open(state, "w") as fh:
        json.dump(st, fh)
bad = st.get("deep_bad_after") is not None and st["deep_calls"] > st["deep_bad_after"]
print(json.dumps({"status": "degraded" if bad else "ok", "checks": {"database": {"status": "ok"}, "migrations": {"status": "ok"},
                  "worker": st.get("worker") or {"status": "ok", "seconds_since_heartbeat": 5}, "redis": {"status": "ok"}}}))
'''


class FakeHost:
    def __init__(self, tmp: Path, caddyfile: str = "Caddyfile", phase: str = "phase0"):
        self.dir = tmp / "opt-atp"
        self.bin = tmp / "bin"
        self.reports = tmp / "reports"
        for d in (self.dir / ".deploy", self.bin):
            d.mkdir(parents=True)
        (self.dir / ".deploy/current").write_text("0123456789abcdef0123456789abcdef01234567\n")
        env = self.dir / ".env"
        env.write_text("\n".join([f"{k}={v}" for k, v in SECRETS.items()] + ["DOMAIN=atp.example.in", "ACME_EMAIL=ops@example.in",
                                                                              f"CADDYFILE={caddyfile}", f"ATP_DEPLOYMENT_PHASE={phase}"]) + "\n")
        env.chmod(0o600)
        if os.getuid() == 0:   # the script refuses root; the CI runner is not root, this sandbox may be (as test_z11_deploy_bundle)
            (self.bin / "id").write_text("#!/bin/sh\ncase \"$1\" in -u) echo 1000;; -un) echo atp;; *) exec /usr/bin/id \"$@\";; esac\n")
            (self.bin / "id").chmod(0o755)
            os.chown(env, 1000, 1000)
        self.state_path = tmp / "state.json"
        self.state = {"calls": [], "containers": [], "removed": [], "shards": [], "org_calls": [], "orgs": [], "networks": [],
                      "connected": [], "disconnected": [], "cleanup_args": [], "cleanup_stdin": [],
                      "running": ["postgres", "redis", "backend", "worker", "caddy", "frontend"], "posture": dict(POSTURE_OK),
                      "session": {"open_sessions": ["NSE"], "open_positions": 0, "now_ist": WEDNESDAY_10}, "flags_json": '{"flags": {}}'}
        self.save()
        for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL)):
            p = self.bin / name
            p.write_text(f"#!{sys.executable}\n{body}")
            p.chmod(0o755)

    def save(self):
        self.state_path.write_text(json.dumps(self.state))

    def load(self):
        self.state = json.loads(self.state_path.read_text())
        return self.state

    def env(self, now=WEDNESDAY_10):
        return {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": str(self.dir.parent), "ATP_DIR": str(self.dir), "FAKE_STATE": str(self.state_path),
                "ATP_LOADTEST_NOW": now, "ATP_REPORTS_DIR": str(self.reports), "LOADTEST_PY": str(ROOT / "backend/scripts/loadtest.py"),
                "ATP_POLL_SECONDS": "1", "ATP_LOADTEST_WATCH_SECONDS": "1"}

    def run(self, *args, now=WEDNESDAY_10, timeout=120):
        return subprocess.run(["bash", str(ATP_SH), *args], env=self.env(now), capture_output=True, text=True, timeout=timeout)


SMALL = ("--users", "4", "--ramp", "30", "--seconds", "30", "--think", "1")


def _no_secret(*texts):
    for text in texts:
        for value in SECRETS.values():
            assert value not in text


@needs_tools
@pytest.mark.parametrize("now, session, expect", [
    ("2026-10-21 10:00", {"open_sessions": [], "open_positions": 0}, "closed by the app's calendar"),       # a trading holiday
    ("2026-10-24 11:00", None, "closed by the clock only"),                                                  # Saturday, no calendar
    ("2026-10-21 08:30", None, "closed by the clock only"),                                                  # before the open
    ("2026-10-21 15:27", {"open_sessions": ["NSE"], "open_positions": 0}, "left before the NSE close"),     # too late to finish
])
def test_refused_outside_market_hours_without_force_and_nothing_created(tmp_path, now, session, expect):
    host = FakeHost(tmp_path)
    host.state["session"] = session
    host.save()
    r = host.run("loadtest", *SMALL, now=now)
    assert r.returncode == 3, r.stdout + r.stderr
    assert expect in r.stderr and "--force-offhours" in r.stderr
    st = host.load()
    assert st["org_calls"] == [] and st["shards"] == []


@needs_tools
def test_force_offhours_runs_the_checks_and_marks_the_run(tmp_path):
    host = FakeHost(tmp_path)
    host.state["session"] = {"open_sessions": [], "open_positions": 0}
    host.save()
    r = host.run("loadtest", *SMALL, "--force-offhours", "--check", now="2026-10-21 19:00")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "running anyway (--force-offhours)" in r.stdout and "ready. Nothing was created" in r.stdout
    assert [c[0] for c in host.load()["org_calls"]] == ["list"]                  # --check: a read-only look for leftovers only


@needs_tools
@pytest.mark.parametrize("posture, phase, expect", [
    ({**POSTURE_OK, "live_trading": {"on": True, "tenants": 0}}, "phase0", "live_trading is on"),
    ({**POSTURE_OK, "live_trading": {"on": False, "tenants": 2}}, "phase0", "organisations: 2"),
    ({**POSTURE_OK, "phase0_paper_only": {"known": True, "on": False}}, "phase0", "phase0_paper_only is not on"),
    ({**POSTURE_OK, "phase0_paper_only": {"known": False, "on": False}}, "phase0", "phase0_paper_only is not on"),
    (None, "phase0", "could not report its flag posture"),
    (POSTURE_OK, "normal", "ATP_DEPLOYMENT_PHASE is 'normal'"),
    ({**POSTURE_OK, "deployment_phase": "none"}, "phase0", "deployment phase 'none'"),
])
def test_refused_unless_paper_only_phase0(tmp_path, posture, phase, expect):
    host = FakeHost(tmp_path, phase=phase)
    host.state["posture"] = posture
    host.save()
    r = host.run("loadtest", *SMALL)
    assert r.returncode == 10, r.stdout + r.stderr
    assert expect in r.stderr
    st = host.load()
    assert st["org_calls"] == [] and st["shards"] == []


@needs_tools
@pytest.mark.parametrize("caddyfile", ["Caddyfile", "Caddyfile.ip"])
def test_a_whole_run_seeds_loads_cleans_up_and_reports(tmp_path, caddyfile):
    host = FakeHost(tmp_path, caddyfile=caddyfile)
    r = host.run("loadtest", *SMALL)
    st = host.load()
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    _no_secret(out)
    stamp = st["seeded"]
    assert [c[0] for c in st["org_calls"]] == ["list", "seed", "cleanup"] and all(c[1] == stamp for c in st["org_calls"][1:])
    assert st["seed_name"] == f"atp-loadtest-{stamp}-seed"                      # L1: a named container an exit can stop
    first_run = next(i for i, c in enumerate(st["calls"]) if c[:2] == ["run", "-d"])
    seed_at = next(i for i, c in enumerate(st["calls"]) if "seed" in c)
    clean_at = next(i for i, c in enumerate(st["calls"]) if "cleanup" in c)
    assert seed_at < first_run < clean_at
    # H1: the cleanup gets the ids seed reported, on stdin, never a name to look for
    assert st["cleanup_args"][0][:5] == ["cleanup", "--stamp", stamp, "--org-file", "-"]
    assert json.loads(st["cleanup_stdin"][0]) == {"stamp": stamp, "tenant_id": 9, "user_ids": [100, 101, 102, 103]}
    assert "tenant_id = 9" in st["creds_sql"]
    assert len(st["shards"]) == 1 and st["containers"] == [] and st["removed"] == [f"atp-loadtest-{stamp}-0"]
    shard = st["shards"][0]
    # L5: the clients on an internal network of the run's own, with only Caddy attached; detached and removed after
    assert st["networks"] == [] and st["connected"] == [] and st["disconnected"] == [f"atp-loadtest-{stamp}"]
    created = next(c for c in st["calls"] if c[:2] == ["network", "create"])
    assert "--internal" in created and created[-1] == f"atp-loadtest-{stamp}"
    assert shard["network"] == f"atp-loadtest-{stamp}" and shard["cpus"] == "0.25" and shard["memory"] == "128m"
    assert shard["read_only"] and shard["chart_lake"]
    assert shard["user"] == f"{1000 if os.getuid() == 0 else os.getuid()}:{os.getgid()}"
    if caddyfile == "Caddyfile":
        assert shard["base"] == "https://atp.example.in" and shard["add_host"] == "atp.example.in:172.30.0.2" and shard["host_header"] is None
        assert shard["allowed"] == "LOADTEST_ALLOWED_HOSTS=atp.example.in"
    else:
        assert shard["base"] == "https://172.30.0.2" and shard["host_header"] == "atp.example.in" and shard["add_host"] is None
    assert (host.reports / "loadtest-latest.md").resolve().name == f"loadtest-{stamp}.md"
    run_dir = host.dir / ".deploy/loadtest" / stamp
    assert not (run_dir / "accounts.csv").exists() and (run_dir / "cleanup.json").exists() and st.get("merged")
    report = (host.reports / f"loadtest-{stamp}.md").read_text()
    assert "SLO verdict: PASS" in report and "Cleanup: verified clean" in report and "| chart | " in report and "NOT MEASURED" in report
    assert "Feature flags unchanged by the run: yes" in report and "Broker credentials in the load-test organisation: none" in report
    assert "Load-test networks left: 0" in report and "Watchdog: every 1s" in report
    assert f"Report: {host.reports}/loadtest-{stamp}.md" in r.stdout and "cleanup verified" in r.stdout
    assert "(paste this back)" in r.stdout and "# Load test on the production host" in r.stdout   # L3: printed by the run itself
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["shards"] == 1 and meta["market"].startswith("open (NSE") and meta["tenant_id"] == 9
    _no_secret(report, (run_dir / "meta.json").read_text())


@needs_tools
def test_a_failed_client_start_still_cleans_up(tmp_path):
    host = FakeHost(tmp_path)
    host.state["fail_shard_start"] = True
    host.save()
    r = host.run("loadtest", *SMALL)
    st = host.load()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "could not start load-client container" in r.stderr
    assert [c[0] for c in st["org_calls"]] == ["list", "seed", "cleanup"] and st["networks"] == []
    assert "cleanup after an interrupted, aborted or failed run" in r.stdout and "no load result" in r.stdout
    assert not list((host.dir / ".deploy/loadtest").glob("*/accounts.csv"))


def _interrupt(host, marker, extra_state=None):
    host.state.update(extra_state or {})
    host.save()
    proc = subprocess.Popen(["bash", str(ATP_SH), "loadtest", *SMALL], env=host.env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    seen = []
    deadline = time.time() + 60
    for line in proc.stdout:
        seen.append(line)
        if marker in line or time.time() > deadline:
            break
    if marker.startswith("==> load-test organisation"):
        while not host.load().get("seed_started") and time.time() < deadline:
            time.sleep(0.1)
    proc.send_signal(signal.SIGINT)
    rest, _ = proc.communicate(timeout=90)
    return proc.returncode, "".join(seen) + rest, host.load()


@needs_tools
def test_ctrl_c_during_the_run_stops_the_clients_and_cleans_up(tmp_path):
    host = FakeHost(tmp_path)
    rc, out, st = _interrupt(host, "==> running:", {"keep_running": True})
    assert rc == 130, out
    assert [c[0] for c in st["org_calls"]] == ["list", "seed", "cleanup"] and st["containers"] == [] and st["networks"] == []
    assert "cleanup after an interrupted, aborted or failed run (exit 130)" in out and "cleanup verified" in out
    assert "\ncleanup: verified" in out
    report = "".join(p.read_text() for p in host.reports.glob("loadtest-2*.md"))
    assert "no load result" in report and "Feature flags unchanged by the run: yes" in report
    assert not list((host.dir / ".deploy/loadtest").glob("*/accounts.csv"))


@needs_tools
def test_ctrl_c_during_seeding_stops_the_seed_container_before_the_cleanup(tmp_path):
    """L1: the seed's container is removed (its transaction rolls back), then the cleanup runs by the seed record."""
    host = FakeHost(tmp_path)
    rc, out, st = _interrupt(host, "==> load-test organisation", {"seed_hang": True})
    assert rc == 130, out
    stamp = st["seeded"]
    assert f"atp-loadtest-{stamp}-seed" in st["removed"] and "the seeding was stopped" in out
    removed_at = next(i for i, c in enumerate(st["calls"]) if c[:2] == ["rm", "-f"] and f"atp-loadtest-{stamp}-seed" in c)
    clean_at = next(i for i, c in enumerate(st["calls"]) if "cleanup" in c)
    assert removed_at < clean_at
    assert st["cleanup_args"][0][:4] == ["cleanup", "--stamp", stamp, "--absent-ok"]     # no org file: the seed never reported
    assert st["shards"] == []


@needs_tools
def test_an_unverified_cleanup_outranks_every_other_outcome(tmp_path):
    host = FakeHost(tmp_path)
    host.state["clean"] = False
    host.save()
    r = host.run("loadtest", *SMALL)
    assert r.returncode == 4 and "cleanup: NOT VERIFIED (exit 4)" in r.stdout, r.stdout + r.stderr
    host2 = FakeHost(tmp_path / "b")
    rc, out, _ = _interrupt(host2, "==> running:", {"keep_running": True, "refuse_cleanup": True})
    assert rc == 4 and "refused: organisation #9 owns rows a load test does not write" in out, out


@needs_tools
@pytest.mark.parametrize("state, reason", [
    ({"deep_bad_after": 1}, "the API's deep health failed twice in a row"),
    ({"bump_restart": True}, "restarted (restart count 1)"),
])
def test_the_watchdog_stops_a_run_when_production_degrades(tmp_path, state, reason):
    """M1: deep health, a container restart (or OOM, the worker heartbeat, memory, swap) stop the run; it still cleans up."""
    host = FakeHost(tmp_path)
    host.state.update({"keep_running": True, **state})
    host.save()
    r = host.run("loadtest", *SMALL, timeout=120)
    st = host.load()
    out = r.stdout + r.stderr
    assert r.returncode == 5, out
    assert "ABORTED: production degraded" in out and reason in out
    assert [c[0] for c in st["org_calls"]] == ["list", "seed", "cleanup"] and st["containers"] == [] and st["networks"] == []
    report = "".join(p.read_text() for p in host.reports.glob("loadtest-2*.md"))
    assert "The run was stopped early: production degraded" in report and reason in report and "Cleanup: verified clean" in report


@needs_tools
def test_caddy_left_attached_to_the_run_network_is_a_cleanup_not_verified(tmp_path):
    """Review follow-up: a failed Caddy detach leaves the network behind - exit 4, even with the organisation clean."""
    host = FakeHost(tmp_path)
    host.state["fail_disconnect"] = True
    host.save()
    r = host.run("loadtest", *SMALL)
    out = r.stdout + r.stderr
    assert r.returncode == 4, out
    assert "the load-test network is still there" in out and "cleanup: NOT VERIFIED (exit 4)" in r.stdout
    report = "".join(p.read_text() for p in host.reports.glob("loadtest-2*.md"))
    assert "Load-test networks left: 1" in report and "Cleanup: NOT VERIFIED - the load-test network is still there" in report


@needs_tools
@pytest.mark.parametrize("worker, reason", [
    ({"status": "never_seen"}, "the trading worker reports never_seen"),
    ({"status": "stale", "seconds_since_heartbeat": 900, "cycle_seconds": 60}, "heartbeat is 900s old (limit 180s)"),
    ({"status": "stale", "seconds_since_heartbeat": 100, "cycle_seconds": 30}, "the trading worker reports stale"),
    ({"status": "ok", "seconds_since_heartbeat": 200, "cycle_seconds": 30}, "heartbeat is 200s old (limit 180s)"),     # never under 180
    ({"status": "ok", "seconds_since_heartbeat": 200, "cycle_seconds": "x"}, "heartbeat is 200s old (limit 180s)"),    # unusable: 180
])
def test_the_preflight_refuses_a_worker_state_the_watchdog_stops_on(tmp_path, worker, reason):
    """Review follow-up: never_seen / stale / an old heartbeat are refused before anything is created, not ~30 s after."""
    host = FakeHost(tmp_path)
    host.state["worker"] = worker
    host.save()
    r = host.run("loadtest", *SMALL)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAILED: refused: the trading worker" in r.stderr and reason in r.stderr and "Nothing was created" in r.stderr
    st = host.load()
    assert st["org_calls"] == [] and st["shards"] == [] and st["networks"] == []


@needs_tools
def test_the_heartbeat_limit_is_three_worker_cycles(tmp_path):
    host = FakeHost(tmp_path)
    host.state["worker"] = {"status": "ok", "seconds_since_heartbeat": 300, "cycle_seconds": 120}
    host.save()
    r = host.run("loadtest", *SMALL, "--check")
    assert r.returncode == 0 and "heartbeat 300s ago, limit 360s" in r.stdout, r.stdout + r.stderr


@needs_tools
def test_the_heartbeat_limit_is_capped_at_fifteen_minutes(tmp_path):
    host = FakeHost(tmp_path)
    host.state["worker"] = {"status": "ok", "seconds_since_heartbeat": 950, "cycle_seconds": 600}
    host.save()
    r = host.run("loadtest", *SMALL, "--check")
    assert r.returncode == 1 and "heartbeat is 950s old (limit 900s)" in r.stderr, r.stdout + r.stderr
    assert host.load()["org_calls"] == []


@needs_tools
def test_an_unverified_cleanup_is_4_even_when_the_summary_says_0(tmp_path):
    """The run's exit code does not rest on the summary alone: LT_CLEAN=0 is 4 whatever it read (meta.json) or said."""
    host = FakeHost(tmp_path)
    host.state["fail_disconnect"] = True
    host.save()
    deploy = tmp_path / "deploy"
    (deploy / "hostinger").mkdir(parents=True)
    shutil.copy(ATP_SH, deploy / "atp.sh")
    (deploy / "hostinger/loadtest_summary.py").write_text(
        "import sys\nout = sys.argv[sys.argv.index('--out') + 1]\nopen(out, 'w').write('# stub: clean\\n')\nprint('Report: ' + out)\nsys.exit(0)\n")
    r = subprocess.run(["bash", str(deploy / "atp.sh"), "loadtest", *SMALL], env=host.env(), capture_output=True, text=True, timeout=120)
    assert r.returncode == 4 and "cleanup: NOT VERIFIED (exit 4)" in r.stdout, r.stdout + r.stderr


@needs_tools
def test_too_little_memory_is_its_own_refusal(tmp_path):
    host = FakeHost(tmp_path)
    env = {**host.env(), "ATP_LOADTEST_MIN_AVAILABLE_MIB": "99999999"}
    r = subprocess.run(["bash", str(ATP_SH), "loadtest", *SMALL], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 6 and "refused: only" in r.stderr and "Nothing was created" in r.stderr, r.stdout + r.stderr
    assert host.load()["org_calls"] == []


@needs_tools
def test_manual_cleanup_lists_and_cleans_only_provable_load_test_organisations(tmp_path):
    host = FakeHost(tmp_path)
    host.state["orgs"] = [{"stamp": "20261021-101500", "tenant_id": 9, "name": "loadtest-20261021-101500", "status": "active", "users": 500,
                           "active_users": 500, "ours": True, "reason": ""},
                          {"stamp": "20261020-090000", "tenant_id": 4, "name": "Renamed desk", "status": "active", "users": 3, "active_users": 3,
                           "ours": False, "reason": "organisation #4 is now named 'Renamed desk' - renamed, not touching it"}]
    host.state["containers"] = ["atp-loadtest-20261021-101500-0"]
    host.state["networks"] = [{"name": "atp-loadtest-20261021-101500", "internal": True, "label": "atp.loadtest=20261021-101500"}]
    host.save()
    listed = host.run("loadtest-cleanup", "--list")
    assert listed.returncode == 0 and "NOT provably a load-test organisation, never touched: organisation #4 is now named" in listed.stdout
    assert [c[0] for c in host.load()["org_calls"]] == ["list"]
    r = host.run("loadtest-cleanup", "--all")
    st = host.load()
    assert r.returncode == 0, r.stdout + r.stderr
    assert ["cleanup", "20261021-101500"] in st["org_calls"] and ["cleanup", "20261020-090000"] not in st["org_calls"]
    assert st["cleanup_args"][-1][:5] == ["cleanup", "--stamp", "20261021-101500", "--tenant-id", "9"]
    assert st["containers"] == [] and st["networks"] == [] and "loadtest-cleanup: done" in r.stdout
    refused = host.run("loadtest-cleanup", "20261020-090000")                   # named by the owner, still not provable: refused
    assert refused.returncode == 1 and "no provable load-test organisation" in refused.stderr
    assert host.run("loadtest-cleanup", "x; rm -rf /").returncode == 2


@needs_tools
@pytest.mark.parametrize("target, orgs", [("--all", False), ("--all", True), ("20261021-101500", True)])
def test_manual_cleanup_with_caddy_still_attached_is_not_verified(tmp_path, target, orgs):
    """A load-test network the manual cleanup could not remove (Caddy detach failed) is exit 4, even with no organisation."""
    host = FakeHost(tmp_path)
    net = "atp-loadtest-20261021-101500"
    host.state.update({"fail_disconnect": True, "connected": [net],
                       "networks": [{"name": net, "internal": True, "label": "atp.loadtest=20261021-101500"}],
                       "orgs": [{"stamp": "20261021-101500", "tenant_id": 9, "name": "loadtest-20261021-101500", "status": "active", "users": 4,
                                 "active_users": 4, "ours": True, "reason": ""}] if orgs else []})
    host.save()
    r = host.run("loadtest-cleanup", target)
    assert r.returncode == 4, r.stdout + r.stderr
    assert "the load-test network is still there" in r.stdout and "NOT verified - 1 load-test network(s) still there" in r.stdout
    assert "nothing to clean up" not in r.stdout and "loadtest-cleanup: done" not in r.stdout
    assert (["cleanup", "20261021-101500"] in host.load()["org_calls"]) == orgs           # the organisation is still cleaned


@needs_tools
def test_bad_options_are_refused_before_anything_runs(tmp_path):
    host = FakeHost(tmp_path)
    assert host.run("loadtest", "--users", "0").returncode == 2
    assert host.run("loadtest", "--ramp", "600", "--seconds", "600").returncode == 2        # past the 15-minute token life
    assert host.run("loadtest", "--client-cpus", "1;x").returncode == 2
    assert host.run("loadtest", "--nope").returncode == 2
    assert host.load()["calls"] == []


def test_the_documented_command_block_labels_only_the_load_tests_exit_code(tmp_path):
    """Review follow-up: when `health` fails, the block says so and prints no exit code that could pass for the load test's."""
    block = (ROOT / "docs/deploy/LOADTEST_REAL_HOST.md").read_text(encoding="utf-8").split("## The command block", 1)[1].split("```")[1]
    commands = [line for line in block.splitlines() if line.strip() and not line.startswith("#")]
    assert len(commands) == 1 and commands[0].count("/opt/atp/deploy/atp.sh") == 2                # one copy-pasteable line
    stub = tmp_path / "atp.sh"
    stub.write_text('#!/bin/sh\ncase "$1" in health) exit "$HEALTH_RC" ;; loadtest) exit 5 ;; esac\nexit 99\n')
    stub.chmod(0o755)
    line = commands[0].replace("/opt/atp/deploy/atp.sh", str(stub))
    failed = subprocess.run(["bash", "-c", line], env={**os.environ, "HEALTH_RC": "1"}, capture_output=True, text=True)
    assert failed.stdout == "health check failed - load test not started\n" and failed.returncode != 0
    ran = subprocess.run(["bash", "-c", line], env={**os.environ, "HEALTH_RC": "0"}, capture_output=True, text=True)
    assert ran.stdout == "loadtest exit code: 5\n"
