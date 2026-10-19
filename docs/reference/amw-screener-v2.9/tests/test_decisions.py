"""Approved decisions Q-014, Q-010, Q-012, Q-002, Q-005: configuration, authorization/ownership, retention safety and
API contracts."""
import time
from datetime import timedelta

import pandas as pd
import pytest

from src.data.fixture_provider import FixtureProvider
from src.services.app_services import Services
from src.storage.db import utcnow

UNI = pd.DataFrame({"symbol": list("ABCDEFGH"), "ticker": [f"{x}.NS" for x in "ABCDEFGH"], "company": "", "industry": ""})


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("AMW_REPORTS_DIR", str(tmp_path / "rep"))
    s = Services(db_url=f"sqlite:///{tmp_path}/d.db", provider=FixtureProvider(end="2026-10-16"))
    j = s.start_scan(UNI, "t", ["breakout", "pullback", "reversal", "trend_continuation"])
    while j.status in ("queued", "running"):
        time.sleep(0.05)
    return s


# ---------------- Q-014
def test_q014_worker_configuration_and_sync_default():
    import pathlib
    import yaml
    root = pathlib.Path(__file__).resolve().parents[1]
    dc = yaml.safe_load((root / "docker-compose.yml").read_text())
    assert dc["services"]["api"]["environment"]["AMW_API_WORKERS"] == "${AMW_API_WORKERS:-4}"
    assert 'os.environ.get("AMW_API_WORKERS", "1")' in (root / "api_server.py").read_text()   # desktop default


def test_q014_async_jobs_retry_transient_errors_and_record_attempts(svc):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise TimeoutError("provider slow")
        return {"ok": True}
    import src.services.jobs as J
    J.RETRY_BACKOFF_S = 0.01
    j = svc.jobs.submit_api("backtest", svc.local_user, flaky)
    while j.status in ("queued", "running"):
        time.sleep(0.02)
    assert j.status == "done" and calls["n"] == 2 and "2 attempt" in j.message
    rec = svc.store.job_run_get(j.id)
    assert rec["status"] == "done" and '"ok": true' in rec["result"]

    def broken():
        raise ValueError("bad input")                          # not transient: no retry, error recorded
    j2 = svc.jobs.submit_api("backtest", svc.local_user, broken)
    while j2.status in ("queued", "running"):
        time.sleep(0.02)
    assert j2.status == "failed" and "bad input" in svc.store.job_run_get(j2.id)["error"]


def test_q014_sync_contract_unchanged(svc, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    monkeypatch.setenv("AMW_AUTH_MODE", "local")
    c = TestClient(create_app(svc))
    r = c.post("/api/v1/backtests", json={"ticker": "Q.NS", "timeframe": "1D", "strategy": "breakout"}).json()
    assert "job_id" not in r and {"all", "trades", "in_sample", "out_of_sample"} <= set(r)


# ---------------- Q-010
def test_q010_subjective_patterns_excluded_and_guarded():
    from src.analysis import optional as O
    from src.scanner import nl
    assert O.REGISTRY == {} and "elliott_wave" in O.EXCLUDED_FROM_CORE
    assert nl.parse("daily close above ema 20 and elliott wave 3")["unsupported"]
    with pytest.raises(ValueError):
        O.register(O.OptionalModule("x", lambda a: [], affects_score=True))
    O.register(O.OptionalModule("demo", lambda a: [{"label": "wave 3", "evidence": "e", "uncertainty": "high"}]))
    try:
        with pytest.raises(ValueError, match="candidate"):
            O.run("demo", None)
    finally:
        O.REGISTRY.clear()
    import pathlib
    core = "".join(p.read_text() for p in pathlib.Path("src/strategies").glob("*.py"))
    assert "optional" not in core                                                     # core never reads the registry


# ---------------- Q-012
def test_q012_copy_is_independent_owned_and_leaks_nothing(svc):
    from src.scanner import spec
    u = svc.local_user
    bob, _ = svc.store.create_user("bob2")
    ir = {"ir_version": 1, "long": {"type": "group", "logic": "AND", "children": [{"type": "rule", "name": "c",
          "expr": {"t": "cmp", "op": ">", "a": {"t": "field", "name": "close"}, "b": {"t": "field", "name": "ema20"}}}]},
          "short": None, "stop": {"method": "atr", "mult": 1.5}, "target": {"r": 2.0}}
    sid = spec.save(svc, u, "Mine", ir, {"alerts": {"enabled": True, "channels": ["telegram"]}})["scanner_id"]
    copy = spec.share(svc, u, sid, "bob2")
    assert svc.store.lab_get(bob, copy)["user_id"] == bob and svc.store.lab_get(u, copy) is None
    spec.save(svc, bob, "[shared] Mine", {**ir, "target": {"r": 5.0}}, sid=copy)          # recipient edits own copy
    assert svc.store.lab_get(u, sid)["draft_ir"]["target"] == {"r": 2.0}                  # original unaffected
    spec.save(svc, u, "Mine", {**ir, "target": {"r": 3.0}}, sid=sid)                      # owner edits original
    assert svc.store.lab_versions(bob, copy)[0]["ir"]["target"] == {"r": 5.0}             # copy unaffected
    assert svc.store.lab_versions(bob, sid) == [] and svc.store.lab_runs(bob, sid) == []   # no access to the original
    got = svc.store.lab_get(bob, copy)["draft_spec"]["scanner"]
    assert got["alerts"] == {"enabled": False, "channels": ["in_app"]}                     # sender's delivery prefs not copied
    assert svc.store.signal_history(bob) == [] and svc.store.forward_list(bob) == []


def test_q012_api_requires_writer_and_ownership(svc, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    from src.scanner import spec
    monkeypatch.setenv("AMW_AUTH_MODE", "token")
    ua, ta = svc.store.create_user("own", "analyst")
    uv, tv = svc.store.create_user("viewer1", "viewer")
    svc.store.create_user("target1", "analyst")
    sid = spec.save(svc, ua, "S", {"ir_version": 1, "long": {"type": "group", "logic": "AND", "children": [
        {"type": "rule", "name": "c", "expr": {"t": "pred", "name": "data_quality_ok", "args": {}}}]}, "short": None})["scanner_id"]
    c = TestClient(create_app(svc))
    assert c.post(f"/api/v1/scanners/{sid}/share?to_username=target1", headers={"Authorization": f"Bearer {tv}"}).status_code == 403
    assert c.post(f"/api/v1/scanners/{sid}/share?to_username=own", headers={"Authorization": f"Bearer {ta}"}).status_code == 404
    assert c.post(f"/api/v1/scanners/{sid}/share?to_username=target1", headers={"Authorization": f"Bearer {ta}"}).json()["shared"]


# ---------------- Q-002
def test_q002_plans_are_provisional_and_config_driven(svc, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    from src.saas.entitlements import EntitlementError, check, commercial_status
    assert set(svc.base_cfg["plans"]) == {"free", "pro", "premium", "enterprise"}
    cs = commercial_status(svc.base_cfg)
    assert cs["commercial_status"] == "provisional" and cs["prices"] is None and cs["notice"]
    u, _ = svc.store.create_user("planuser", "analyst", "free")
    monkeypatch.setitem(svc.base_cfg["plans"]["free"], "alert_rules", 1)                  # change config, not code
    with pytest.raises(EntitlementError):
        check(svc.store, u, "alert_rules", 2, svc.base_cfg)
    monkeypatch.setenv("AMW_AUTH_MODE", "local")
    p = TestClient(create_app(svc)).get("/api/v1/plan").json()
    assert p["commercial_status"] == "provisional" and {"plan", "limits", "usage", "note"} <= set(p)   # contract kept


# ---------------- Q-005
def _make_reports(svc, n=3):
    from src.reports import builder
    u = svc.local_user
    key = svc.store.signal_history(u)[0]["key"]
    return [builder.generate(svc, u, key, "json")[0] for _ in range(n)]


def _age(svc, filename, days):
    from sqlalchemy import update
    from src.storage.db import reports
    with svc.store.engine.begin() as c:
        c.execute(update(reports).where(reports.c.filename == filename).values(created=utcnow() - timedelta(days=days)))


def test_q005_defaults_and_plan_aware_cleanup(svc):
    from src.reports import builder
    from src.saas.entitlements import plan_limits
    assert [plan_limits(p, svc.base_cfg)["report_retention_days"] for p in ("free", "pro", "premium", "enterprise")] == [7, 90, 365, 365]
    names = _make_reports(svc)
    svc.store.set_plan(svc.local_user, "pro")
    _age(svc, names[0], 100)
    _age(svc, names[1], 30)
    out = builder.apply_retention(svc, utcnow())
    rows = {r["filename"]: r for r in svc.store.reports_older_than(svc.local_user, utcnow() + timedelta(days=1))}
    assert out["deleted"] == 1 and rows[names[0]]["status"] == "DELETED" and "retention 90 days" in rows[names[0]]["error"]
    assert rows[names[1]]["status"] == "COMPLETED"
    assert any(a["action"] == "report_retention" for a in svc.store.audit_rows(20))         # deletion logged


def test_q005_legal_hold_recent_and_failed_cleanup_are_safe(svc, monkeypatch):
    from src.reports import builder
    names = _make_reports(svc)
    u = svc.local_user
    svc.store.set_plan(u, "free")
    rid = {r["filename"]: r["id"] for r in svc.store.reports_completed(u)}
    for n in names:
        _age(svc, n, 30)
    assert svc.store.report_set_hold(u, rid[names[0]], True)
    real_unlink = builder.Path.unlink

    def boom(self, *a, **k):
        if self.name == names[1]:
            raise PermissionError("locked")
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(builder.Path, "unlink", boom)
    out = builder.apply_retention(svc, utcnow())
    st = {r["filename"]: r["status"] for r in svc.store.reports_older_than(u, utcnow() + timedelta(days=1))}
    assert st[names[0]] == "COMPLETED" and st[names[1]] == "COMPLETED" and st[names[2]] == "DELETED"
    assert out == {**out, "protected": 1, "failed": 1, "deleted": 1}
    monkeypatch.undo()
    assert builder.apply_retention(svc, utcnow())["deleted"] == 1                         # failed one retried next run
    fresh = _make_reports(svc, 1)[0]                                                      # recent: protected from quota
    monkeypatch.setitem(svc.base_cfg["plans"]["free"], "report_storage_mb", 0)
    builder.enforce_quota(svc, u)
    assert {r["filename"] for r in svc.store.reports_completed(u)} >= {fresh}


def test_q005_isolation_and_admin_only_hold(svc, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    from src.reports import builder
    names = _make_reports(svc, 1)
    other, tok = svc.store.create_user("other5", "analyst", "free")
    rid = svc.store.reports_completed(svc.local_user)[0]["id"]
    assert svc.store.report_set_hold(other, rid, True) is False                           # cannot touch others' reports
    _age(svc, names[0], 3)
    svc.store.set_plan(svc.local_user, "pro")
    assert builder.apply_retention(svc, utcnow())["deleted"] == 0                         # other users' plans irrelevant
    monkeypatch.setenv("AMW_AUTH_MODE", "token")
    c = TestClient(create_app(svc))
    assert c.put(f"/api/v1/admin/reports/{rid}/legal-hold?user_id={svc.local_user}",
                 headers={"Authorization": f"Bearer {tok}"}).status_code == 403
