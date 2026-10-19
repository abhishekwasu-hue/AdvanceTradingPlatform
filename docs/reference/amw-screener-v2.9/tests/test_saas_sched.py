"""Entitlements, scheduler and report/notification API tests (SIMULATED fixture data, mocked Telegram, fake clock)."""
import time

import pandas as pd
import pytest

from src.ai import crypto
from src.data.fixture_provider import FixtureProvider
from src.saas.entitlements import EntitlementError, check, plan_limits
from src.services.app_services import Services
from src.services.scheduler import Scheduler, slot_due, validate_schedule

MARKET = {"timezone": "Asia/Kolkata", "session_open": "09:15", "session_close": "15:30", "holidays": ["2026-10-20"]}


def utc(s):  # IST wall-clock -> naive UTC
    return pd.Timestamp(s, tz="Asia/Kolkata").tz_convert("UTC").tz_localize(None).to_pydatetime()


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("AMW_SECRET_KEY", crypto.generate_master_key())
    return Services(db_url=f"sqlite:///{tmp_path}/s.db", provider=FixtureProvider(end="2026-10-16"))


# ---------------------------------------------------------------- entitlements
def test_plan_limits_enforced_in_backend(svc):
    u, _ = svc.store.create_user("free_user", plan="free")
    assert plan_limits("free")["symbols_per_scan"] == 50 and plan_limits("enterprise")["symbols_per_scan"] is None
    check(svc.store, u, "symbols_per_scan", 50)
    with pytest.raises(EntitlementError):
        check(svc.store, u, "symbols_per_scan", 51)
    uni = pd.DataFrame({"symbol": [f"S{i}" for i in range(60)], "ticker": [f"S{i}.NS" for i in range(60)], "company": "", "industry": ""})
    with pytest.raises(EntitlementError):
        svc.start_scan(uni, "big", ["trend_continuation"], user_id=u)
    with pytest.raises(EntitlementError):
        check(svc.store, u, "min_schedule_interval_min", 5)


def test_concurrent_job_limit(svc):
    u, _ = svc.store.create_user("f2", plan="free")
    gate = __import__("threading").Event()
    svc.jobs.submit("hold", lambda progress, should_stop, job: gate.wait(5), owner=u)
    uni = pd.DataFrame({"symbol": ["A"], "ticker": ["A.NS"], "company": "", "industry": ""})
    with pytest.raises(EntitlementError):
        svc.start_scan(uni, "x", ["trend_continuation"], user_id=u)
    gate.set()


# ---------------------------------------------------------------- scheduler timing
def test_daily_slot_trading_days_and_no_double_run():
    sch = {"enabled": True, "at_time": "15:45", "interval_minutes": None, "trading_days_only": True, "last_run": None}
    assert slot_due(sch, utc("2026-10-16 15:44"), MARKET) is None                 # Friday, before time
    assert slot_due(sch, utc("2026-10-16 15:45"), MARKET) == "2026-10-16@15:45"
    sch["last_run"] = utc("2026-10-16 15:45")
    assert slot_due(sch, utc("2026-10-16 18:00"), MARKET) is None                 # already ran today
    assert slot_due(sch, utc("2026-10-17 16:00"), MARKET) is None                 # Saturday
    assert slot_due(sch, utc("2026-10-20 16:00"), MARKET) is None                 # configured holiday
    assert slot_due(sch, utc("2026-10-19 16:00"), MARKET) == "2026-10-19@15:45"   # Monday
    sch["enabled"] = False
    assert slot_due(sch, utc("2026-10-19 16:00"), MARKET) is None


def test_interval_slot_only_in_session():
    sch = {"enabled": True, "at_time": None, "interval_minutes": 30, "trading_days_only": True, "last_run": None}
    assert slot_due(sch, utc("2026-10-16 09:00"), MARKET) is None
    assert slot_due(sch, utc("2026-10-16 10:00"), MARKET)
    sch["last_run"] = utc("2026-10-16 10:00")
    assert slot_due(sch, utc("2026-10-16 10:20"), MARKET) is None
    assert slot_due(sch, utc("2026-10-16 10:30"), MARKET)
    assert slot_due(sch, utc("2026-10-16 15:45"), MARKET) is None


def test_schedule_validation(svc):
    u, _ = svc.store.create_user("f3", plan="free")
    with pytest.raises(ValueError):
        validate_schedule(svc.store, u, "scan", "25:00", None, svc.cfg(u))
    with pytest.raises(ValueError):
        validate_schedule(svc.store, u, "scan", "10:00", 30, svc.cfg(u))
    validate_schedule(svc.store, u, "scan", "10:00", None, svc.cfg(u))
    svc.store.schedule_add(u, name="a", kind="scan", config={}, at_time="10:00", enabled=True)
    with pytest.raises(EntitlementError):
        validate_schedule(svc.store, u, "scan", "11:00", None, svc.cfg(u))       # free plan: 1 schedule


def test_scheduler_runs_owner_scan_once(svc):
    u = svc.local_user
    sid = svc.store.schedule_add(u, name="eod", kind="scan", config={"universe": "Custom", "symbols": "AAA BBB",
                                 "strategies": ["trend_continuation"]}, at_time="15:45", trading_days_only=True, enabled=True)
    clock = {"t": utc("2026-10-16 15:46")}
    sch = Scheduler(svc, clock=lambda: clock["t"])
    out = sch.tick()
    assert len(out) == 1 and out[0]["schedule"] == sid and "job" in out[0]
    job = svc.jobs.get(out[0]["job"])
    assert job.owner == u
    while job.status in ("queued", "running"):
        time.sleep(0.05)
    for _ in range(40):
        if svc.store.schedule_list(u)[0]["last_status"] != "STARTED":
            break
        time.sleep(0.05)
    assert svc.store.schedule_list(u)[0]["last_status"] == "DONE"
    clock["t"] = utc("2026-10-16 17:00")
    assert sch.tick() == []                                                        # same day: not again


def test_scheduled_report_delivered_to_owner_telegram(svc, monkeypatch):
    from tests.test_notify import OWN, MockTG
    from src.services import alerts
    tg = MockTG()
    monkeypatch.setattr("src.notify.telegram.requests_transport", tg)
    u = svc.local_user
    alerts.set_own_bot(svc.store, u, OWN, "42", tg)
    svc.store.schedule_add(u, name="rep", kind="scan_report", config={"universe": "Custom", "symbols": "AAA BBB CCC DDD EEE FFF GGG HHH",
                           "strategies": ["trend_continuation", "pullback", "breakout"], "top_n": 2}, at_time="15:45", enabled=True)
    sch = Scheduler(svc, clock=lambda: utc("2026-10-16 16:00"))
    job = svc.jobs.get(sch.tick()[0]["job"])
    while job.status in ("queued", "running"):
        time.sleep(0.05)
    for _ in range(200):
        st = svc.store.schedule_list(u)[0]["last_status"]
        if st and st.startswith(("COMPLETED", "PARTIALLY")):
            break
        time.sleep(0.05)
    assert st.startswith("COMPLETED"), (st, svc.store.schedule_list(u)[0]["last_error"])
    docs = [c for c in tg.calls if c["method"] == "sendDocument"]
    n_reports = len([r for r in svc.store.report_list(u) if r["format"] == "pdf"])
    assert len(docs) == n_reports and all(d["body"]["chat_id"] == "42" for d in docs)
    assert all(d["files"]["document"][1][:5] == b"%PDF-" for d in docs)


# ---------------------------------------------------------------- API
def test_api_reports_schedules_notify_and_plan(svc, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    from tests.test_notify import OWN, MockTG
    monkeypatch.setenv("AMW_AUTH_MODE", "token")
    tg = MockTG()
    svc.telegram_transport = tg
    _, ta = svc.store.create_user("alice", plan="pro")
    _, tb = svc.store.create_user("bob", plan="free")
    c = TestClient(create_app(svc))
    A, B = {"Authorization": f"Bearer {ta}"}, {"Authorization": f"Bearer {tb}"}
    jid = c.post("/api/v1/scans", json={"universe": "Custom", "symbols": "AAA BBB CCC DDD EEE", "strategies": ["trend_continuation", "pullback"]},
                 headers=A).json()["job_id"]
    for _ in range(300):
        if c.get(f"/api/v1/jobs/{jid}", headers=A).json()["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    sigs = c.get("/api/v1/signals", headers=A).json()
    assert sigs
    key = sigs[0]["key"]
    assert c.post("/api/v1/reports", json={"signal_key": key, "format": "pdf"}, headers=B).status_code == 404
    r = c.post("/api/v1/reports", json={"signal_key": key, "format": "pdf"}, headers=A)
    assert r.status_code == 200
    rid = c.get("/api/v1/reports", headers=A).json()[0]["id"]
    d = c.get(f"/api/v1/reports/{rid}/download", headers=A)
    assert d.status_code == 200 and d.content[:5] == b"%PDF-" and "attachment" in d.headers["content-disposition"]
    assert c.get(f"/api/v1/reports/{rid}/download", headers=B).status_code == 404
    assert c.get("/api/v1/reports", headers=B).json() == []
    r = c.post("/api/v1/schedules", json={"name": "s", "at_time": "15:45"}, headers=B)
    assert r.status_code == 201
    r = c.post("/api/v1/schedules", json={"name": "s2", "at_time": "16:00"}, headers=B)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "plan_limit"
    sid = c.get("/api/v1/schedules", headers=B).json()[0]["id"]
    assert c.delete(f"/api/v1/schedules/{sid}", headers=A).status_code == 404
    r = c.put("/api/v1/notify/telegram/own", json={"bot_token": OWN, "chat_id": "77"}, headers=A)
    assert r.status_code == 200 and OWN not in r.text
    g = c.get("/api/v1/notify", headers=A)
    assert g.json()["telegram"]["verified"] and OWN not in g.text and "token_ct" not in g.text
    assert c.get("/api/v1/notify", headers=B).json()["telegram"]["verified"] is False
    assert c.put("/api/v1/notify", json={"quiet_hours": ["7am", "9am"], "max_per_hour": 5}, headers=A).status_code == 422
    p = c.get("/api/v1/plan", headers=B).json()
    assert p["plan"] == "free" and p["limits"]["schedules"] == 1 and p["usage"]["schedules"] == 1


def test_two_processes_never_run_the_same_scheduled_slot(tmp_path):
    """A-008: dashboard and API both run a scheduler; a due slot must start exactly once."""
    import datetime as _dt
    from src.data.fixture_provider import FixtureProvider
    from src.services.app_services import Services
    from src.services.scheduler import Scheduler
    db = f"sqlite:///{tmp_path}/two.db"
    a = Services(db_url=db, provider=FixtureProvider(end="2026-10-16"))
    b = Services(db_url=db, provider=FixtureProvider(end="2026-10-16"))
    u = a.local_user
    a.store.schedule_add(u, name="s", kind="scan", config={"universe": "Custom", "symbols": "A B",
                                                            "strategies": ["trend_continuation"]}, at_time="09:30")
    now = _dt.datetime(2026, 10, 16, 4, 30)                      # 10:00 IST, Friday
    sa, sb = Scheduler(a, clock=lambda: now), Scheduler(b, clock=lambda: now)
    sa_rows = list(a.store.schedule_list(None))
    started = []
    for s, rows in ((sa, sa_rows), (sb, sa_rows)):               # both read the same (stale) state
        s.svc.store.schedule_list = lambda uid, _r=rows: _r
        started += s.tick()
    assert len([x for x in started if "job" in x]) == 1


def test_saved_scanner_can_be_scheduled_and_feeds_forward_tests(tmp_path):
    import datetime as _dt
    import time as _t
    from src.data.fixture_provider import FixtureProvider
    from src.scanner import spec
    from src.services.app_services import Services
    from src.services.scheduler import Scheduler, validate_schedule
    s = Services(db_url=f"sqlite:///{tmp_path}/sc.db", provider=FixtureProvider(end="2026-10-16"))
    u = s.local_user
    ir = {"ir_version": 1, "long": {"type": "group", "logic": "AND", "children": [{"type": "rule", "name": "close > EMA20",
          "expr": {"t": "cmp", "op": ">", "a": {"t": "field", "name": "close"}, "b": {"t": "field", "name": "ema20"}}}]},
          "short": None, "stop": {"method": "atr", "mult": 1.5}, "target": {"r": 2.0}}
    sid = spec.save(s, u, "EMA", ir, {"alerts": {"enabled": True}})["scanner_id"]
    conf = {"universe": "Custom", "symbols": "A B C D", "scanner_id": sid}
    with pytest.raises(ValueError):
        validate_schedule(s.store, u, "scanner", "09:30", None, s.cfg(), {"scanner_id": "nope"})
    validate_schedule(s.store, u, "scanner", "09:30", None, s.cfg(), conf)
    s.store.schedule_add(u, name="sc", kind="scanner", config=conf, at_time="09:30")
    now = _dt.datetime(2026, 10, 16, 4, 30)
    out = Scheduler(s, clock=lambda: now).tick()
    job = s.jobs.get(out[0]["job"])
    while job.status in ("queued", "running"):
        _t.sleep(0.05)
    assert job.status == "done"
    q = [h for h in s.store.signal_history(u) if h["status"] == "qualified"]
    assert len(s.store.forward_list(u)) == len(q)                                   # forward tests for scanner signals
