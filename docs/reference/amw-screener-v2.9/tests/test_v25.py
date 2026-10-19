"""v2.5: durable job records + orphan recovery, paper forward tests, report retention/quota, signal -> chart/report
navigation. Synthetic fixtures only; no network."""
import time
from datetime import timedelta

import pandas as pd
import pytest

from src.data.fixture_provider import FixtureProvider
from src.services import forward as FW
from src.services.app_services import Services
from src.storage.db import utcnow

UNI = pd.DataFrame({"symbol": list("ABCDEFGH"), "ticker": [f"{x}.NS" for x in "ABCDEFGH"], "company": "", "industry": ""})


def wait(job, timeout=60):
    end = time.time() + timeout
    while job.status in ("queued", "running") and time.time() < end:
        time.sleep(0.05)
    return job


@pytest.fixture
def svc(tmp_path):
    return Services(db_url=f"sqlite:///{tmp_path}/v25.db", provider=FixtureProvider(end="2026-10-16"))


# ---------------------------------------------------------------- A. durable jobs
def test_jobs_are_recorded_and_visible_from_another_process(svc, tmp_path):
    j = wait(svc.start_scan(UNI, "t", ["trend_continuation"]))
    assert j.status == "done"
    rec = svc.store.job_run_get(j.id)
    assert rec["status"] == "done" and rec["kind"] == "scan" and rec["user_id"] == svc.local_user and rec["finished"]
    other = Services(db_url=f"sqlite:///{tmp_path}/v25.db", provider=FixtureProvider(end="2026-10-16"))
    seen = other.jobs.get(j.id, svc.local_user)
    assert seen is not None and seen.status == "done" and getattr(seen, "remote", False)
    assert other.jobs.get(j.id, 9999) is None                         # still owner-scoped
    assert j.id in [x["id"] for x in other.jobs.list(svc.local_user)]


def test_failed_job_error_is_persisted(svc):
    def boom(progress, should_stop, job):
        raise RuntimeError("provider exploded")
    j = wait(svc.jobs.submit("x", boom, owner=svc.local_user))
    assert j.status == "failed" and "provider exploded" in svc.store.job_run_get(j.id)["error"]


def test_restart_marks_orphans_failed_with_reason(svc, tmp_path):
    now = utcnow()
    svc.store.job_record("deadjob00001", user_id=svc.local_user, kind="scan", status="running", progress=0.4,
                         heartbeat=now - timedelta(minutes=10), started=now - timedelta(minutes=11))
    svc.store.job_record("livejob00001", user_id=svc.local_user, kind="scan", status="running", heartbeat=now)
    sid = svc.store.schedule_add(svc.local_user, name="s", kind="scan", config={}, at_time="09:30") \
        if hasattr(svc.store, "schedule_add") else None
    if sid:
        svc.store.schedule_update(svc.local_user, sid, last_run=now - timedelta(hours=2), last_status="STARTED")
    Services(db_url=f"sqlite:///{tmp_path}/v25.db", provider=FixtureProvider(end="2026-10-16"))   # "restart"
    dead, live = svc.store.job_run_get("deadjob00001"), svc.store.job_run_get("livejob00001")
    assert dead["status"] == "failed" and "restarted" in dead["error"]
    assert live["status"] == "running"                                # a healthy job of another process is untouched
    if sid:
        sch = [x for x in svc.store.schedule_list(svc.local_user) if x["id"] == sid][0]
        assert sch["last_status"] == "FAILED" and "restarted" in sch["last_error"]


# ---------------------------------------------------------------- C. forward tests
def _bars(rows, start="2026-10-12"):
    idx = pd.date_range(start, periods=len(rows), freq="B")
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)


T = {"signal_time": "2026-10-12 00:00", "direction": "bullish", "stop": 95.0, "target": 110.0, "max_bars": 5}


def test_forward_entry_is_next_bar_open_and_exits():
    b = _bars([(100, 101, 99, 100), (100, 103, 99, 102), (102, 111, 101, 110), (110, 112, 109, 111)])
    r = FW.evaluate(T, b)
    assert r["entry_time"].startswith("2026-10-13") and r["entry_price"] == 100          # not the signal bar
    assert r["status"] == "WIN" and r["exit_price"] == 110 and r["r_multiple"] == 2.0 and r["bars_held"] == 2


def test_forward_stop_wins_ties_and_gaps_fill_at_open():
    both = _bars([(100, 101, 99, 100), (100, 101, 99, 100), (100, 112, 94, 100)])
    r = FW.evaluate(T, both)
    assert r["status"] == "LOSS" and "stop assumed" in r["exit_reason"] and r["r_multiple"] == -1.0
    gap = _bars([(100, 101, 99, 100), (100, 101, 99, 100), (90, 91, 88, 89)])
    g = FW.evaluate(T, gap)
    assert g["status"] == "LOSS" and g["exit_price"] == 90 and g["r_multiple"] == -2.0  # gap beyond the stop


def test_forward_expiry_open_and_short_side():
    flat = _bars([(100, 101, 99, 100)] * 8)
    assert FW.evaluate(T, flat)["status"] == "EXPIRED"
    assert FW.evaluate(T, flat.iloc[:3])["status"] == "OPEN"
    assert FW.evaluate(T, flat.iloc[:1]) == {}                                       # no bar after the signal yet
    short = {**T, "direction": "bearish", "stop": 105.0, "target": 90.0}
    r = FW.evaluate(short, _bars([(100, 101, 99, 100), (100, 101, 89, 90)]))
    assert r["status"] == "WIN" and r["r_multiple"] == 2.0


def test_scan_enrolls_qualified_signals_and_mark_and_stats(svc, monkeypatch):
    wait(svc.start_scan(UNI, "t", ["breakout", "pullback", "reversal", "trend_continuation"]))
    items = svc.store.forward_list(svc.local_user)
    qual = [h for h in svc.store.signal_history(svc.local_user) if h["status"] == "qualified"]
    assert items and len(items) == len(qual) and all(i["status"] == "PENDING" for i in items)
    wait(svc.start_scan(UNI, "t", ["breakout", "pullback", "reversal", "trend_continuation"]))
    assert len(svc.store.forward_list(svc.local_user)) == len(items)               # idempotent
    svc._provider = FixtureProvider(end="2026-11-20")                               # later data arrives
    res = FW.mark(svc, svc.local_user)
    assert res["checked"] == len(items) and res["errors"] == 0
    after = svc.store.forward_list(svc.local_user)
    assert all(i["status"] != "PENDING" for i in after) and all(i["entry_time"] > i["signal_time"] for i in after)
    st = FW.stats(svc.store, svc.local_user)
    assert st and all(s["evidence"] == "INSUFFICIENT EVIDENCE" for s in st)
    assert FW.stats(svc.store, 9999) == [] and svc.store.forward_list(9999) == []


def test_forward_api_is_owner_scoped(svc, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    monkeypatch.setenv("AMW_AUTH_MODE", "token")
    ua, ta = svc.store.create_user("fa")
    ub, tb = svc.store.create_user("fb")
    wait(svc.start_scan(UNI, "t", ["breakout", "pullback", "reversal", "trend_continuation"], user_id=ua))
    c = TestClient(create_app(svc))
    a = c.get("/api/v1/forward", headers={"Authorization": f"Bearer {ta}"}).json()
    assert a["items"] and c.get("/api/v1/forward", headers={"Authorization": f"Bearer {tb}"}).json()["items"] == []
    assert c.post("/api/v1/forward/mark", headers={"Authorization": f"Bearer {ta}"}).json()["checked"] == len(a["items"])


# ---------------------------------------------------------------- E. report retention + quota
def test_report_retention_and_storage_quota(svc, monkeypatch, tmp_path):
    from src.reports import builder
    monkeypatch.setenv("AMW_REPORTS_DIR", str(tmp_path / "rep"))
    wait(svc.start_scan(UNI, "t", ["breakout", "pullback", "reversal", "trend_continuation"]))
    u = svc.local_user
    key = svc.store.signal_history(u)[0]["key"]
    names = [builder.generate(svc, u, key, "json")[0] for _ in range(2)]
    svc.store.set_user_plan(u, "free") if hasattr(svc.store, "set_user_plan") else None
    from sqlalchemy import update
    from src.storage.db import reports, users
    with svc.store.engine.begin() as cx:
        cx.execute(update(users).where(users.c.id == u).values(plan="free"))
        cx.execute(update(reports).where(reports.c.filename == names[0]).values(created=utcnow() - timedelta(days=30)))
    out = builder.apply_retention(svc, utcnow())
    assert out["deleted"] >= 1 and not (tmp_path / "rep" / str(u) / names[0]).exists()
    old = [r for r in svc.store.reports_older_than(u, utcnow() + timedelta(days=1)) if r["filename"] == names[0]][0]
    assert old["status"] == "DELETED" and "retention 7 days" in old["error"]
    with pytest.raises(KeyError):
        builder.read_report(svc, u, old["id"])
    with svc.store.engine.begin() as cx:                                           # older than the 30-min delivery window
        cx.execute(update(reports).where(reports.c.status == "COMPLETED").values(created=utcnow() - timedelta(hours=2)))
    monkeypatch.setitem(svc.base_cfg["plans"]["free"], "report_storage_mb", 0)   # quota: keep only the newest
    newest = builder.generate(svc, u, key, "csv")
    assert (tmp_path / "rep" / str(u) / newest[0]).exists()
    left = [r for r in svc.store.reports_completed(u)]
    assert [r["filename"] for r in left] == [newest[0]]


# ---------------------------------------------------------------- D. navigation
def test_signal_intelligence_opens_chart_with_stored_levels(monkeypatch, tmp_path):
    st_testing = pytest.importorskip("streamlit.testing.v1")
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    monkeypatch.setenv("AMW_PROVIDER", "fixture")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/nav.db")
    from src.ai.crypto import generate_master_key
    monkeypatch.setenv("AMW_SECRET_KEY", generate_master_key())
    import ui_common
    ui_common.services.clear()
    s = ui_common.services()
    wait(s.start_scan(UNI, "t", ["breakout", "pullback", "reversal", "trend_continuation"]))
    rec = s.store.signal_history(s.local_user)[0]
    a = st_testing.AppTest.from_file(str(root / "pages/3_Price_Action_Workbench.py"), default_timeout=120)
    a.session_state["detail_ticker"], a.session_state["detail_tf"], a.session_state["focus_signal"] = \
        rec["ticker"], rec["timeframe"], rec["key"]
    a.run()
    assert not a.exception, [e.value for e in a.exception]
    assert any(f"Stored signal {rec['key'][:8]}" in i.value for i in a.info)
    a2 = st_testing.AppTest.from_file(str(root / "pages/12_Report_Center.py"), default_timeout=120)
    a2.session_state["report_key"] = rec["key"]
    a2.run()
    assert not a2.exception and a2.selectbox[0].value == rec["key"]
