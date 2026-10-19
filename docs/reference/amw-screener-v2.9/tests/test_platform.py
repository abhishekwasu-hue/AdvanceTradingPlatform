"""Platform tests: scanner, export, signal lifecycle, alerts, paper trading, risk, built-in backtest, storage/tenancy,
billing idempotency, analyst grounding, jobs and API auth. SYNTHETIC fixtures only; no network."""
import hashlib
import hmac
import io
import json
import time
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import run_backtest
from src.risk import position_size
from src.screener.export import to_csv_bytes
from src.screener.scanner import apply_filters, run_scan, signals_frame
from src.services.alerts import process_alerts
from src.services.analyst import analyse, validate_llm_text
from src.services.jobs import JobManager
from src.services.lifecycle import update_lifecycle
from src.services.paper import PaperAccount
from src.storage.db import Store, utcnow
from src.strategies.registry import enabled_strategies
from tests.conftest import FakeProvider, make_ohlc

UP = make_ohlc(np.linspace(100, 200, 300) + np.sin(np.arange(300)) * 2, spread=0.5, start="2025-06-01")
DN = make_ohlc(np.linspace(200, 100, 300) + np.sin(np.arange(300)) * 2, spread=0.5, start="2025-06-01")
NOW = UP.index[-1] + pd.Timedelta(hours=18)


def uni(*t):
    return pd.DataFrame({"symbol": [x.split(".")[0] for x in t], "ticker": list(t), "company": [f"{x} Ltd" for x in t], "industry": "IT"})


def test_scan_isolation_filters_and_export(cfg):
    prov = FakeProvider({"UP.NS": UP, "DN.NS": DN, "^NSEI": UP}, fail=("5m",))
    res = run_scan(uni("UP.NS", "DN.NS", "MISS.NS"), cfg, prov, enabled_strategies(cfg, ["trend_continuation"]),
                   sleeper=lambda s: None, now=NOW)
    q = pd.DataFrame(res.quality)
    assert set(q[q.ticker == "MISS.NS"].status) == {"UNAVAILABLE"}
    sig = signals_frame(res)
    assert set(sig.direction[sig.ticker == "UP.NS"]) <= {"bullish"}
    assert list(apply_filters(sig, {"direction": "bearish"}).ticker.unique()) in ([], ["DN.NS"])
    assert apply_filters(sig, {"min_score": 101}).empty and apply_filters(pd.DataFrame(), {"min_score": 1}).empty
    back = pd.read_csv(io.StringIO(to_csv_bytes(sig).decode("utf-8-sig")))
    assert len(back) == len(sig)
    ohlc = pd.read_csv(io.StringIO(to_csv_bytes(UP).decode("utf-8-sig")))
    assert ohlc.columns[0] == "timestamp" and "close" in ohlc.columns


def test_scan_cancel_and_resume(cfg):
    cfg["data"]["batch_size"] = 2
    prov = FakeProvider({f"{x}.NS": UP for x in "ABCD"} | {"^NSEI": UP})
    n = {"c": 0}

    def stop():
        n["c"] += 1
        return n["c"] > 1
    s = enabled_strategies(cfg, ["trend_continuation"])
    r = run_scan(uni("A.NS", "B.NS", "C.NS", "D.NS"), cfg, prov, s, should_stop=stop, sleeper=lambda x: None, now=NOW)
    assert r.stopped_early and r.symbols_done == 2
    r = run_scan(uni("A.NS", "B.NS", "C.NS", "D.NS"), cfg, prov, s, sleeper=lambda x: None, resume=r, now=NOW)
    assert r.symbols_done == 4 and not r.stopped_early


def _row(key="k1", status="qualified", stop=90.0, target=120.0, st="2026-10-16 00:00", score=80):
    return {"key": key, "ticker": "A.NS", "symbol": "A", "strategy": "breakout", "strategy_version": 1, "setup": "Breakout",
            "timeframe": "1D", "direction": "bullish", "signal_time": st, "data_time": st, "status": status, "label": "Strong Candidate",
            "score": score, "rr": 2.0, "stop": stop, "target": target, "expiry": "3 completed 1D bars"}


def test_signal_dedup_and_lifecycle(tmp_path):
    s = Store(f"sqlite:///{tmp_path}/a.db")
    u = s.ensure_local_user()
    K = lambda k: s.scoped_key(u, k)  # noqa: E731
    assert s.record_signals([_row()], u) == 1 and s.record_signals([_row()], u) == 0
    assert s.get_signal(K("k1"), u)["state"] == "qualified"
    with pytest.raises(ValueError):
        s.transition(K("k1"), "candidate", "illegal", u)
    times = ["2026-10-16 00:00", "2026-10-17 00:00"]
    update_lifecycle(s, [], {("A.NS", "1D"): {"close": 85.0, "high": 95, "low": 84, "times": times}}, u)
    assert s.get_signal(K("k1"), u)["state"] == "invalidated"
    s.record_signals([_row("k2")], u)
    update_lifecycle(s, [], {("A.NS", "1D"): {"close": 110.0, "high": 121, "low": 105, "times": times}}, u)
    assert s.get_signal(K("k2"), u)["state"] == "completed"
    s.record_signals([_row("k3")], u)
    update_lifecycle(s, [], {("A.NS", "1D"): {"close": 105.0, "high": 106, "low": 104,
                                             "times": ["2026-10-16 00:00", "a", "b", "c", "d"]}}, u)
    assert s.get_signal(K("k3"), u)["state"] == "expired"
    s.record_signals([_row("k4", status="candidate")], u)
    assert s.get_signal(K("k4"), u)["state"] == "awaiting_confirmation"
    update_lifecycle(s, [_row("k5")], {}, u)
    assert s.get_signal(K("k4"), u)["state"] == "qualified"
    assert [e["to_state"] for e in s.signal_events(K("k4"), u)] == ["awaiting_confirmation", "qualified"]


def test_signals_and_lifecycle_are_owner_scoped(tmp_path):
    """Regression for SEC-002: identical setups are separate records per owner; no cross-tenant reads or writes."""
    s = Store(f"sqlite:///{tmp_path}/iso.db")
    a, _ = s.create_user("a")
    b, _ = s.create_user("b")
    assert s.record_signals([_row("same")], a) == 1 and s.record_signals([_row("same")], b) == 1
    ka, kb = s.scoped_key(a, "same"), s.scoped_key(b, "same")
    assert ka != kb
    assert s.get_signal(ka, b) is None and s.signal_events(ka, b) == []
    assert [x["key"] for x in s.signal_history(a)] == [ka] and [x["key"] for x in s.signal_history(b)] == [kb]
    with pytest.raises(KeyError):
        s.transition(ka, "invalidated", "attack", b)
    update_lifecycle(s, [], {("A.NS", "1D"): {"close": 1.0, "high": 1, "low": 1, "times": ["x"]}}, b)   # b's scan
    assert s.get_signal(ka, a)["state"] == "qualified" and s.get_signal(kb, b)["state"] == "invalidated"


def _alert_store(tmp_path):
    s = Store(f"sqlite:///{tmp_path}/b.db")
    uid = s.ensure_local_user()
    s.add_alert_rule(uid, name="r", enabled=True, strategies=["breakout"], min_score=50, directions=[], timeframes=[], symbols=[],
                     cooldown_minutes=60, channels=["in_app", "telegram"])
    return s, uid


def _rec(s, uid, *rows):
    s.record_signals(list(rows), uid)
    return list(rows)


def test_alert_dedup_cooldown_and_channels(tmp_path):
    s, uid = _alert_store(tmp_path)
    sent = []
    senders = {"in_app": lambda u, t: ("sent", None), "telegram": lambda u, t: (sent.append((u, t)) or ("sent", None))}
    out = process_alerts(s, _rec(s, uid, _row("a1")), uid, senders)
    assert {(o["channel"], o["status"]) for o in out} == {("in_app", "sent"), ("telegram", "sent")}
    assert process_alerts(s, [_row("a1")], uid, senders) == []                  # duplicate signal -> nothing
    out = process_alerts(s, _rec(s, uid, _row("a2")), uid, senders)            # same ticker, in cooldown
    assert {o["status"] for o in out} == {"suppressed"}
    out = process_alerts(s, _rec(s, uid, _row("a3")), uid, senders, now=utcnow() + timedelta(minutes=61))
    assert {o["status"] for o in out} == {"sent"}
    assert process_alerts(s, _rec(s, uid, _row("a4", score=10)), uid, senders) == []
    out = process_alerts(s, _rec(s, uid, _row("a5")), uid, {"in_app": lambda u, t: ("failed", "boom"), "telegram": lambda u, t: ("failed", "x")},
                         now=utcnow() + timedelta(days=1))
    assert {o["status"] for o in out} == {"failed"}
    assert "not an order" in sent[0][1] and sent[0][0] == uid
    assert "rule-based, not a probability" in sent[0][1]


def test_alerts_only_use_the_scan_owners_rules(tmp_path):
    """Regression for SEC-003: user B's rules never fire on user A's scan results."""
    s = Store(f"sqlite:///{tmp_path}/c.db")
    a, _ = s.create_user("a")
    b, _ = s.create_user("b")
    s.add_alert_rule(b, name="b-rule", enabled=True, strategies=[], min_score=0, directions=[], timeframes=[], symbols=[],
                     cooldown_minutes=0, channels=["telegram"])
    calls = []
    out = process_alerts(s, _rec(s, a, _row("x")), a, {"telegram": lambda u, t: (calls.append(u) or ("sent", None))})
    assert out == [] and calls == []
    assert process_alerts(s, [_row("x")], None) == []


def test_quiet_hours_and_rate_limit(tmp_path):
    from src.services.alerts import notify_settings, save_notify
    s, uid = _alert_store(tmp_path)
    st = notify_settings(s, uid)
    st.update(quiet_hours=["00:00", "23:59"], max_per_hour=1)
    save_notify(s, uid, st)
    calls = []
    snd = {"in_app": lambda u, t: ("sent", None), "telegram": lambda u, t: (calls.append(t) or ("sent", None))}
    out = process_alerts(s, _rec(s, uid, _row("q1")), uid, snd)
    assert {(o["channel"], o["status"], o["error"]) for o in out} == {("in_app", "sent", None), ("telegram", "suppressed", "quiet hours")}
    st.update(quiet_hours=None)
    save_notify(s, uid, st)
    s.add_alert_rule(uid, name="r2", enabled=True, strategies=[], min_score=0, directions=[], timeframes=[], symbols=[],
                     cooldown_minutes=0, channels=["telegram"])
    out = process_alerts(s, _rec(s, uid, _row("q2", st="2026-10-17 00:00")), uid, snd, now=utcnow() + timedelta(hours=2))
    tg = [o for o in out if o["channel"] == "telegram"]
    assert [o["status"] for o in tg].count("sent") == 1 and any("rate limit" in (o["error"] or "") for o in tg)
    with pytest.raises(ValueError):
        save_notify(s, uid, {**st, "quiet_hours": ["7am", "9am"]})


def test_risk_requires_configured_equity():
    r = {"account_equity": None, "risk_per_trade_pct": 1, "max_open_risk_pct": 6, "max_daily_loss_pct": 3, "lot_size": 1}
    assert not position_size(100, 95, "bullish", r).ok
    r["account_equity"] = 100000
    p = position_size(100, 95, "bullish", r)
    assert p.ok and p.quantity == 200 and p.risk_amount == 1000
    assert not position_size(100, 105, "bullish", r).ok
    assert not position_size(100, 95, "bullish", r, day_realized_loss=3000).ok


def test_paper_account_reconciles(cfg):
    cfg["risk"]["account_equity"] = 100000
    a = PaperAccount(100000, cfg)
    p1 = a.open("A", "bullish", 100, 95, 110, "2026-10-16 10:00")
    p2 = a.open("B", "bearish", 50, 52, 46, "2026-10-16 10:05")
    a.mark({"A": {"open": 101, "high": 111, "low": 100, "close": 110}, "B": {"open": 53, "high": 53, "low": 52, "close": 52.5}}, "2026-10-17 15:30")
    assert p1.reason == "target" and p2.reason == "gap stop" and p2.exit == 53
    assert a.reconcile() == pytest.approx(0, abs=1e-6)
    assert a.cash == pytest.approx(100000 + a.realised_total(), rel=1e-9)
    with pytest.raises(ValueError):
        PaperAccount(None, cfg)


def test_builtin_backtest_sequencing_and_costs(cfg):
    clean = make_ohlc(np.linspace(100, 200, 420) + np.sin(np.arange(420)) * 3, spread=0.6)
    r = run_backtest(clean, "1D", cfg, "pullback", warmup=210)
    tr = r["trades"]
    if len(tr):
        assert (pd.to_datetime(tr.entry_time) > pd.to_datetime(tr.signal_time)).all()
    assert r["version"] == cfg["strategies"]["pullback"]["version"]
    short = run_backtest(clean.iloc[:380], "1D", cfg, "pullback", warmup=210)
    s1 = list(short["trades"].signal_time) if len(short["trades"]) else []
    s2 = list(tr.signal_time) if len(tr) else []
    assert s2[:max(0, len(s1) - 1)] == s1[:max(0, len(s1) - 1)]                    # no look-ahead
    cfg["costs"].update(brokerage_bps=100, exchange_tax_bps=100, slippage_bps=100)
    r2 = run_backtest(clean, "1D", cfg, "pullback", warmup=210)
    if len(tr) and len(r2["trades"]):
        assert r2["trades"].ret_pct.iloc[0] < tr.ret_pct.iloc[0]


def test_tenant_isolation_and_roles(tmp_path):
    s = Store(f"sqlite:///{tmp_path}/c.db")
    a, ta = s.create_user("a")
    b, _ = s.create_user("b", "viewer")
    s.save_watchlist(a, "mine", ["A.NS"])
    s.add_trade(a, symbol="A", direction="bullish", entry_price=10, qty=1, stop=9, entry_time="t")
    assert s.watchlists(b) == [] and s.trades(b) == []
    assert not s.close_trade(b, 1, 11, "t", True)
    assert s.user_by_token(ta)["id"] == a and s.user_by_token("wrong") is None
    with pytest.raises(Exception):
        s.add_trade(a, symbol="A", direction="bullish", entry_price=10, qty=0, stop=9, entry_time="t")   # CHECK qty > 0
    with pytest.raises(ValueError):
        s.create_user("c", "superuser")


def test_billing_idempotent(tmp_path):
    s = Store(f"sqlite:///{tmp_path}/d.db")
    u, _ = s.create_user("a")
    assert s.apply_billing_event("evt1", u, "subscription.activated", "pro", {})
    assert not s.apply_billing_event("evt1", u, "subscription.activated", "pro", {})
    assert s.get_user(u)["plan"] == "pro"
    s.apply_billing_event("evt2", u, "subscription.cancelled", None, {})
    assert s.get_user(u)["plan"] == "free"


def test_analyst_grounding():
    ev = {"symbol": "A", "generated_utc": "2026-10-16 10:00:00", "regime": {}, "timeframes": {"1D": {"close": 123.45, "data_status": "OK", "last_bar": "2026-10-16", "trend": "Bullish",
                                  "trend_score": 0.8, "structure_trend": "up", "rsi14": 61.0, "adx": 25.0, "rvol": 1.2,
                                  "volatility": "normal", "structure_events": [], "patterns": []}}, "signals": []}
    assert validate_llm_text("Close is 123.45", ev)[0]
    ok, bad = validate_llm_text("Target 999.99 looks likely", ev)
    assert not ok and bad == ["999.99"]

    class Liar:
        name = "fake"

        def complete(self, system, user, timeout):
            return "The stock will reach 777.77"
    r = analyse(ev, client=Liar())
    assert r.llm_text is None and "rejected" in r.llm_status

    class Down:
        name = "fake"

        def complete(self, *a, **k):
            raise TimeoutError("x")
    r = analyse(ev, client=Down(), retries=0)
    assert r.llm_status.startswith("unavailable") and r.deterministic


def test_job_manager_cancel_and_failure():
    jm = JobManager()

    def slow(progress, should_stop, job):
        for i in range(100):
            if should_stop():
                return "stopped"
            progress(i / 100, str(i))
            time.sleep(0.01)
    j = jm.submit("x", slow)
    time.sleep(0.05)
    jm.cancel(j.id)
    while j.status in ("queued", "running"):
        time.sleep(0.01)
    assert j.status == "cancelled"
    j2 = jm.submit("y", lambda progress, should_stop, job: 1 / 0)
    while j2.status in ("queued", "running"):
        time.sleep(0.01)
    assert j2.status == "failed" and "ZeroDivisionError" in j2.error


def test_api_auth_roles_ratelimit_billing(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    from src.data.fixture_provider import FixtureProvider
    from src.services.app_services import Services
    monkeypatch.setenv("AMW_AUTH_MODE", "token")
    monkeypatch.setenv("AMW_RATE_LIMIT_PER_MIN", "1000")
    svc = Services(db_url=f"sqlite:///{tmp_path}/e.db", provider=FixtureProvider(end="2026-10-16"))
    _, admin = svc.store.create_user("adm", "admin")
    _, viewer = svc.store.create_user("v", "viewer")
    c = TestClient(create_app(svc))
    A, V = {"Authorization": f"Bearer {admin}"}, {"Authorization": f"Bearer {viewer}"}
    assert c.get("/health/live").json() == {"status": "alive"}
    assert c.get("/health/ready").status_code == 200
    assert c.get("/api/v1/meta").status_code == 401
    assert c.get("/api/v1/meta", headers=V).status_code == 200
    assert c.post("/api/v1/watchlists", json={"name": "x", "symbols": ["A.NS"]}, headers=V).status_code == 403
    assert c.get("/api/v1/admin/health", headers=V).status_code == 403
    assert c.get("/api/v1/admin/health", headers=A).status_code == 200
    r = c.get("/api/v1/ohlcv/A.NS?tf=1D&bars=50", headers=A)
    assert r.status_code == 200 and r.json()["data_kind"] == "simulated" and len(r.json()["bars"]) == 50
    assert c.get("/api/v1/features/ZZZ.NS?tf=1D", headers=A).status_code == 422          # unavailable -> no fake features
    assert "X-Request-ID" in r.headers
    body = json.dumps({"id": "e1", "user_id": 1, "type": "subscription.activated", "plan": "pro"}).encode()
    assert c.post("/api/v1/billing/webhook", content=body).status_code == 503              # not configured
    monkeypatch.setenv("AMW_BILLING_WEBHOOK_SECRET", "whsec")
    assert c.post("/api/v1/billing/webhook", content=body, headers={"X-Signature": "bad"}).status_code == 401
    sig = hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
    assert c.post("/api/v1/billing/webhook", content=body, headers={"X-Signature": sig}).json() == {"applied": True, "duplicate": False}
    assert c.post("/api/v1/billing/webhook", content=body, headers={"X-Signature": sig}).json()["duplicate"] is True
    monkeypatch.setenv("AMW_RATE_LIMIT_PER_MIN", "2")
    c2 = TestClient(create_app(svc))
    codes = [c2.get("/api/v1/meta", headers=V).status_code for _ in range(3)]
    assert codes[-1] == 429


def test_no_order_endpoints_exist(tmp_path):
    from src.api.main import create_app
    from src.services.app_services import Services
    app = create_app(Services(db_url=f"sqlite:///{tmp_path}/f.db"))
    paths = [r.path.lower() for r in app.routes]
    banned = ("order", "position", "holding", "funds", "margin", "place", "modify", "exit")
    assert not [p for p in paths if any(b in p for b in banned)]
    # the only broker routes are the read-only FYERS data connection (credentials + daily login), never trading
    assert {p for p in paths if "broker" in p} == {"/api/v1/broker/fyers", "/api/v1/broker/fyers/login-url",
                                                   "/api/v1/broker/fyers/callback"}
    import pathlib
    import re
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("src/feed/fyers.py").read_text().lower()
    urls = set(re.findall(r'f"\{api\}(/[a-z0-9/_-]+)', src))
    assert urls == {"/api/v3/generate-authcode", "/api/v3/validate-authcode", "/data/history"}, urls


@pytest.mark.skipif(not __import__("os").environ.get("AMW_TEST_PG_URL"), reason="set AMW_TEST_PG_URL to run against PostgreSQL")
def test_postgres_store_end_to_end():
    """Same storage contract on a real PostgreSQL server (constraints, tenancy, dedup, lifecycle, lab versions)."""
    import os
    import secrets
    from sqlalchemy import text
    s = Store(os.environ["AMW_TEST_PG_URL"])
    from src.storage.db import SCHEMA_VERSION
    assert s.backend == "postgresql" and s.schema_version() == SCHEMA_VERSION
    a, _ = s.create_user(f"pg_a_{secrets.token_hex(3)}")
    b, _ = s.create_user(f"pg_b_{secrets.token_hex(3)}")
    key = f"pg{secrets.token_hex(6)}"
    assert s.record_signals([_row(key)], a) == 1 and s.record_signals([_row(key)], a) == 0
    k = s.scoped_key(a, key)
    s.transition(k, "active", "filled (paper)", a)
    with pytest.raises(ValueError):
        s.transition(k, "qualified", "illegal", a)
    with pytest.raises(KeyError):
        s.transition(k, "completed", "cross-tenant", b)
    assert s.get_signal(k, b) is None
    sid = s.lab_create(a, "pg strat", "pine", source="//@version=5\nlong_signal = close > open\n", spec={"language": "pine"})
    assert s.lab_publish(a, sid) == 1 and s.lab_get(b, sid) is None
    with pytest.raises(Exception):
        s.add_trade(a, symbol="A", direction="bullish", entry_price=1, qty=-1, stop=0.5, entry_time="t")
    with s.engine.connect() as c:
        assert c.execute(text("select count(*) from lab_versions where strategy_id = :s"), {"s": sid}).scalar() == 1


def test_api_jobs_and_signals_are_owner_scoped(tmp_path, monkeypatch):
    """Regression for SEC-001/SEC-002: a user cannot list, read, cancel or read signals of another user's job."""
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    from src.data.fixture_provider import FixtureProvider
    from src.services.app_services import Services
    monkeypatch.setenv("AMW_AUTH_MODE", "token")
    svc = Services(db_url=f"sqlite:///{tmp_path}/j.db", provider=FixtureProvider(end="2026-10-16"))
    _, ta = svc.store.create_user("alice")
    _, tb = svc.store.create_user("bob")
    _, tadm = svc.store.create_user("admin", "admin")
    c = TestClient(create_app(svc))
    A, B, ADM = ({"Authorization": f"Bearer {t}"} for t in (ta, tb, tadm))
    jid = c.post("/api/v1/scans", json={"universe": "Custom", "symbols": "AAA BBB", "strategies": ["trend_continuation"]}, headers=A).json()["job_id"]
    for _ in range(200):
        if c.get(f"/api/v1/jobs/{jid}", headers=A).json()["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert c.get(f"/api/v1/jobs/{jid}", headers=A).json()["status"] == "done"
    assert c.get(f"/api/v1/jobs/{jid}", headers=B).status_code == 404
    assert c.get(f"/api/v1/jobs/{jid}/signals", headers=B).status_code == 404
    assert c.post(f"/api/v1/jobs/{jid}/cancel", headers=B).status_code == 404
    assert jid not in [j["id"] for j in c.get("/api/v1/jobs", headers=B).json()]
    assert jid in [j["id"] for j in c.get("/api/v1/jobs", headers=ADM).json()]
    sa = c.get("/api/v1/signals", headers=A).json()
    assert c.get("/api/v1/signals", headers=B).json() == []
    if sa:
        assert c.get(f"/api/v1/signals/{sa[0]['key']}", headers=B).status_code == 404
        rec = c.get(f"/api/v1/signals/{sa[0]['key']}", headers=A).json()
        assert rec["payload"]["schema"] == "amw.signal.v1" and rec["payload"]["data_snapshot"] and rec["payload"]["provider"]


def test_schema_migration_adds_owner_columns_to_old_database(tmp_path):
    """A v3 database (signals without user_id) is upgraded additively; legacy rows stay ownerless."""
    import sqlite3
    db = tmp_path / "old.db"
    Store(f"sqlite:///{db}")
    con = sqlite3.connect(db)
    con.execute("DROP INDEX ix_signals_user_first_seen")       # did not exist in v3 (added in v9)
    con.execute("DROP INDEX ix_signals_user_id")
    con.execute("DROP INDEX ix_signals_canonical_key")
    con.execute("ALTER TABLE signals DROP COLUMN user_id")
    con.execute("ALTER TABLE signals DROP COLUMN canonical_key")
    con.execute("DELETE FROM schema_version WHERE version >= 4")
    con.execute("INSERT INTO signals (key, ticker, state) VALUES ('legacy', 'A.NS', 'qualified')")
    con.commit()
    con.close()
    s = Store(f"sqlite:///{db}")
    from src.storage.db import SCHEMA_VERSION
    assert s.schema_version() == SCHEMA_VERSION
    assert s.get_signal("legacy", None)["user_id"] is None and s.get_signal("legacy", s.ensure_local_user()) is None
    con = sqlite3.connect(db)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='signals'")}
    con.close()
    assert {"ix_signals_user_id", "ix_signals_user_first_seen"} <= names         # additive migrations re-created them


def test_backtest_metrics_flag_small_samples():
    from src.backtest.engine import MIN_TRADES_FOR_EVIDENCE, metrics
    few = pd.DataFrame({"r_multiple": [2.0, -1.0, 1.5], "ret_pct": [2, -1, 1.5], "bars": [3, 2, 4], "exit_time": ["a", "b", "c"]})
    m = metrics(few, 100, 0.01)
    assert m["evidence"].startswith("INSUFFICIENT EVIDENCE") and m["trades"] == 3
    many = pd.concat([few] * 10, ignore_index=True)
    assert len(many) >= MIN_TRADES_FOR_EVIDENCE and metrics(many, 500, 0.01)["evidence"] == "OK"
    assert metrics(pd.DataFrame(), 100, 0.01)["evidence"].startswith("INSUFFICIENT")
    allwin = pd.DataFrame({"r_multiple": [1.0] * 3, "ret_pct": [1] * 3, "bars": [1] * 3, "exit_time": list("abc")})
    assert metrics(allwin, 10, 0.01)["profit_factor"] == "inf (no losses)"          # zero denominator handled


def test_concurrent_duplicate_signal_does_not_lose_the_batch(tmp_path, monkeypatch):
    """Regression A-002: a signal inserted by a concurrent scan between the existence check and the insert must be
    skipped, not abort the transaction (which lost every other signal of the batch)."""
    s = Store(f"sqlite:///{tmp_path}/race.db")
    u = s.ensure_local_user()
    a, b = _row("racekey000000001"), _row("racekey000000002")
    s.record_signals([a], u)
    real = s.engine.begin

    class Stale:                                    # the pre-check sees no rows, as if read before the other commit
        def __init__(self):
            self.cm = real()

        def __enter__(self):
            c = self.cm.__enter__()
            orig = c.execute

            def ex(q, *x, **k):
                r = orig(q, *x, **k)
                if "SELECT signals.key" in str(q):
                    class E:
                        def scalars(self_inner):
                            return iter(())
                    return E()
                return r
            c.execute = ex
            return c

        def __exit__(self, *e):
            return self.cm.__exit__(*e)
    monkeypatch.setattr(s.engine, "begin", lambda: Stale())
    assert s.record_signals([a, b], u) == 1                     # a skipped atomically, b stored
    monkeypatch.undo()
    assert {h["canonical_key"] for h in s.signal_history(u)} == {"racekey000000001", "racekey000000002"}


def test_security_headers_on_api_responses(tmp_path):
    """S-001: every response carries hardening headers; API JSON is not cacheable and cannot be framed."""
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    from src.services.app_services import Services
    c = TestClient(create_app(Services(db_url=f"sqlite:///{tmp_path}/h.db")))
    for path in ("/health/live", "/api/v1/meta"):
        h = c.get(path).headers
        assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in h["Content-Security-Policy"] and h["Referrer-Policy"] == "no-referrer"
    assert c.get("/api/v1/meta").headers["Cache-Control"] == "no-store"
    assert "default-src" not in c.get("/docs").headers["Content-Security-Policy"]     # Swagger UI still loads
    assert "Strict-Transport-Security" in c.get("/health/live", headers={"x-forwarded-proto": "https"}).headers


def test_background_api_jobs_return_the_same_result_and_are_owner_scoped(tmp_path, monkeypatch):
    """Q-014 step 1: ?async_job=true for slow endpoints; result persisted (visible from another process); owner-only."""
    import time as _t
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    from src.data.fixture_provider import FixtureProvider
    from src.services.app_services import Services
    monkeypatch.setenv("AMW_AUTH_MODE", "token")
    db = f"sqlite:///{tmp_path}/aj.db"
    svc = Services(db_url=db, provider=FixtureProvider(end="2026-10-16"))
    ua, ta = svc.store.create_user("aa", "analyst", "pro")
    ub, tb = svc.store.create_user("bb", "analyst", "pro")
    c = TestClient(create_app(svc))
    A, B = {"Authorization": f"Bearer {ta}"}, {"Authorization": f"Bearer {tb}"}
    body = {"ticker": "Q.NS", "timeframe": "1D", "strategy": "breakout"}
    sync = c.post("/api/v1/backtests", json=body, headers=A).json()
    jid = c.post("/api/v1/backtests?async_job=true", json=body, headers=A).json()["job_id"]
    for _ in range(600):
        j = c.get(f"/api/v1/jobs/{jid}", headers=A).json()
        if j["status"] not in ("queued", "running"):
            break
        _t.sleep(0.05)
    assert j["status"] == "done" and j["result"]["trades"] == sync["trades"] and j["result"]["all"] == sync["all"]
    assert c.get(f"/api/v1/jobs/{jid}", headers=B).status_code == 404
    other = Services(db_url=db, provider=FixtureProvider(end="2026-10-16"))          # another process
    assert other.jobs.get(jid, ua).result["all"] == sync["all"]
    assert c.post("/api/v1/backtests?async_job=true", json={**body, "strategy": "intraday"}, headers=A).status_code == 400
