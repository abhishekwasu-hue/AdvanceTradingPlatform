"""FYERS 1-minute feed tests. FYERS HTTP is MOCKED (no network, no account needed).

Covers: bar validation and aggregation, completed-bar rule, symbol mapping, OAuth (state, appIdHash, redaction),
encrypted credentials and tenant isolation, background poll (forming bars excluded, invalid rejected, revisions,
catch-up of missed minutes, token expiry, rate-limit guard), schedule of polls (session, weekends, holidays),
per-user broadcast over the hub and the WebSocket API, and scans on the user's FYERS data."""
import hashlib

import pandas as pd
import pytest

from src.ai import crypto
from src.feed import fyers as F
from src.feed.bars import Bar, MinuteAggregator, is_complete, resample, validate_bar
from src.feed.collector import FyersCollector
from src.feed.hub import BarHub

APP, SECRET, TOKEN = "FAKEFAKE-APP-100", "FAKEFAKExyz123", "eyJ-FAKEFAKE-ACCESS-TOKEN-0001"
MARKET = {"timezone": "Asia/Kolkata", "session_open": "09:15", "session_close": "15:30", "holidays": ["2026-10-20"]}


def utc(s):
    return pd.Timestamp(s, tz="Asia/Kolkata").tz_convert("UTC").tz_localize(None).to_pydatetime()


def ep(s):
    return int(pd.Timestamp(s, tz="Asia/Kolkata").timestamp())


class MockFyers:
    """Mock FYERS API: validate-authcode and data/history. Candles generated per symbol up to `now` (incl. forming bar)."""

    def __init__(self, now_ist="2026-10-16 10:03:20"):
        self.now = pd.Timestamp(now_ist, tz="Asia/Kolkata")
        self.calls, self.fail, self.override = [], [], {}
        self.valid_tokens = {TOKEN}

    def candles(self, symbol):
        start = self.now.normalize() + pd.Timedelta(hours=9, minutes=15)
        out, t, base = [], start, 100.0 + (hash(symbol) % 50)
        while t <= self.now.floor("min"):
            i = int((t - start).total_seconds() // 60)
            o = base + i * 0.1
            out.append([int(t.timestamp()), o, o + 0.5, o - 0.3, o + 0.2, 1000 + i])
            t += pd.Timedelta(minutes=1)
        for ts, row in self.override.get(symbol, {}).items():
            out = [row if c[0] == ts else c for c in out]
        return out

    def __call__(self, method, url, params, body, headers, timeout):
        self.calls.append({"method": method, "url": url, "params": params, "body": body, "headers": headers})
        if url.endswith("/validate-authcode"):
            if body["code"] == "GOODCODE0001" and body["appIdHash"] == hashlib.sha256(f"{APP}:{SECRET}".encode()).hexdigest():
                return 200, {"s": "ok", "access_token": TOKEN}
            return 400, {"s": "error", "message": f"invalid auth code {body['code']} hash {body['appIdHash']}"}
        if self.fail:
            st = self.fail.pop(0)
            return st, {"s": "error", "code": -16 if st == 401 else -429, "message": f"error {st} token {headers.get('Authorization')}"}
        tok = headers.get("Authorization", "").split(":", 1)[-1]
        if tok not in self.valid_tokens:
            return 401, {"s": "error", "code": -16, "message": "Could not authenticate the user, invalid token"}
        return 200, {"s": "ok", "candles": self.candles(params["symbol"])}


# ---------------------------------------------------------------- bars
def test_validate_bar_and_completion():
    good = Bar("A.NS", "2026-10-16T10:00:00+05:30", 100, 101, 99, 100.5, 10, "t")
    assert validate_bar(good) is None
    assert validate_bar(Bar("A", good.ts, 100, 99, 98, 100, 1, "t")) == "OHLC inconsistent"
    assert validate_bar(Bar("A", good.ts, float("nan"), 1, 1, 1, 1, "t"))
    assert validate_bar(Bar("A", good.ts, 1, 1, 1, 1, -5, "t"))
    assert "outside session" in validate_bar(Bar("A", "2026-10-16T09:00:00+05:30", 1, 1, 1, 1, 1, "t"))
    assert not is_complete("2026-10-16 10:00", utc("2026-10-16 10:01:01"), 2)
    assert is_complete("2026-10-16 10:00", utc("2026-10-16 10:01:03"), 2)


def test_aggregator_no_forward_fill_and_late_ticks():
    ag = MinuteAggregator("A.NS", "t", cumulative_volume=True)
    assert ag.add_tick("2026-10-16 10:00:05", 100, 1000) is None
    ag.add_tick("2026-10-16 10:00:40", 101, 1100)
    b = ag.add_tick("2026-10-16 10:03:01", 99, 1300)          # 10:01 and 10:02 had no ticks -> no bars
    assert b.ts.startswith("2026-10-16T10:00") and (b.open, b.high, b.low, b.close) == (100, 101, 100, 101)
    assert b.volume == 100                                      # first cumulative value is a baseline, not volume
    assert ag.add_tick("2026-10-16 10:02:59", 50, 1400) is None and ag.late == 1
    assert ag.flush(utc("2026-10-16 10:03:30")) is None
    f = ag.flush(utc("2026-10-16 10:04:05"))
    assert f.ts.startswith("2026-10-16T10:03") and f.volume == 200


def test_resample_aligns_to_session_open():
    idx = pd.date_range("2026-10-16 09:15", periods=30, freq="min", tz="Asia/Kolkata")
    df = pd.DataFrame({"Open": range(30), "High": range(1, 31), "Low": range(30), "Close": range(30), "Volume": 1.0}, index=idx)
    r = resample(df, "15min")
    assert [str(x)[11:16] for x in r.index] == ["09:15", "09:30"] and r.Volume.tolist() == [15.0, 15.0]


# ---------------------------------------------------------------- client
def test_symbol_mapping_hash_and_login_url():
    assert F.to_fyers_symbol("reliance.ns") == "NSE:RELIANCE-EQ" and F.to_fyers_symbol("^NSEI") == "NSE:NIFTY50-INDEX"
    assert F.from_fyers_symbol("NSE:M&M-EQ") == "M&M.NS" and F.from_fyers_symbol("NSE:NIFTY50-INDEX") == "^NSEI"
    assert F.app_id_hash(APP, SECRET) == hashlib.sha256(f"{APP}:{SECRET}".encode()).hexdigest()
    url = F.FyersClient(APP).login_url("http://localhost:8501/x", "st123")
    assert url.startswith(F.API + "/api/v3/generate-authcode?") and "client_id=FAKEFAKE-APP-100" in url and "state=st123" in url


def test_exchange_code_success_and_redacted_failure():
    m = MockFyers()
    assert F.FyersClient(APP, None, m).exchange_code(SECRET, "GOODCODE0001") == TOKEN
    with pytest.raises(F.FyersError) as e:
        F.FyersClient(APP, None, m).exchange_code(SECRET, "BADCODE00001")
    assert "BADCODE00001" not in str(e.value) and SECRET not in str(e.value)


def test_history_errors_and_redaction():
    m = MockFyers()
    c = F.FyersClient(APP, TOKEN, m, F.RateLimiter(100, 1000))
    m.fail = [429]
    with pytest.raises(F.FyersError) as e:
        c.history("NSE:A-EQ", "1", "2026-10-16", "2026-10-16")
    assert e.value.code == "rate_limited" and TOKEN not in str(e.value)
    m.fail = [401]
    with pytest.raises(F.FyersError) as e:
        c.history("NSE:A-EQ", "1", "2026-10-16", "2026-10-16")
    assert e.value.code == "token_expired"
    m.fail = [503]
    assert pytest.raises(F.FyersError, c.history, "NSE:A-EQ", "1", "d", "d").value.code == "unavailable"
    assert repr(c) == "<FyersClient token=[REDACTED]>"
    h = m.calls[-1]["headers"]["Authorization"]
    assert h == f"{APP}:{TOKEN}"


def test_rate_limiter_windows_and_breach_guard():
    t = {"now": 0.0}
    rl = F.RateLimiter(2, 3, clock=lambda: t["now"], sleeper=lambda s: t.__setitem__("now", t["now"] + s))
    for _ in range(3):
        rl.acquire()
    assert t["now"] >= 1.0                    # 3rd call waited for the per-second window
    rl.acquire()
    assert t["now"] >= 60.0                   # 4th call waited for the per-minute window
    rl.record_breach("d1")
    with pytest.raises(F.FyersError):
        rl.record_breach("d1")


def test_token_expiry_is_next_0600_ist():
    assert F.next_expiry(utc("2026-10-16 09:00")) == utc("2026-10-17 06:00")
    assert F.next_expiry(utc("2026-10-16 05:00")) == utc("2026-10-16 06:00")


def test_candles_to_bars_excludes_forming_and_invalid():
    now = utc("2026-10-16 09:18:30")
    cs = [[ep("2026-10-16 09:15"), 100, 101, 99, 100.5, 10], [ep("2026-10-16 09:16"), 100, 99, 98, 100, 10],
          [ep("2026-10-16 09:17"), 100, 101, 99, 100.5, 10], [ep("2026-10-16 09:18"), 100, 101, 99, 100.5, 10], ["bad"]]
    bars, rej = F.candles_to_bars("A.NS", cs, now)
    assert [b["ts"][11:16] for b in bars] == ["09:15", "09:17"]       # 09:18 forming, 09:16 invalid
    assert len(rej) == 2


# ---------------------------------------------------------------- storage + collector
@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("AMW_SECRET_KEY", crypto.generate_master_key())
    from src.data.fixture_provider import FixtureProvider
    from src.services.app_services import Services
    m = MockFyers()
    s = Services(db_url=f"sqlite:///{tmp_path}/f.db", provider=FixtureProvider(end="2026-10-16"), feed_transport=m)
    s.feed.clock = lambda: m.now.tz_convert("UTC").tz_localize(None).to_pydatetime()
    return s, m


def login(s, m, uid):
    s.feed.connect(uid, APP, SECRET, "http://localhost:8501/Live")
    url = s.feed.login_url(uid)
    state = url.split("state=")[1]
    s.feed.complete_login(uid, "GOODCODE0001", state)


def test_credentials_encrypted_state_one_time_and_isolated(svc):
    s, m = svc
    u = s.local_user
    other, _ = s.store.create_user("other")
    s.feed.connect(u, APP, SECRET, "http://localhost:8501/Live")
    state = s.feed.login_url(u).split("state=")[1]
    with pytest.raises(F.FyersError):
        s.feed.complete_login(u, "GOODCODE0001", "forged-state-000000")
    with pytest.raises(F.FyersError):
        s.feed.complete_login(u, "GOODCODE0001", state)                 # state consumed by the failed attempt
    login(s, m, u)
    from sqlalchemy import select
    from src.storage.db import broker_connections
    with s.store.engine.connect() as c:
        row = c.execute(select(broker_connections)).first()
    assert SECRET.encode() not in row.secret_ct and TOKEN.encode() not in row.token_ct
    pub = s.store.broker_public(u, "fyers", now=s.feed.clock())
    assert pub["token_valid"] and APP not in str(pub) and SECRET not in str(pub)
    assert s.store.broker_get(other, "fyers") is None and s.feed.provider_for(other) is None


def test_poll_stores_completed_bars_and_broadcasts_to_owner_only(svc):
    s, m = svc
    u = s.local_user
    other, _ = s.store.create_user("o2")
    login(s, m, u)
    mine = s.feed.hub.subscribe(u)
    theirs = s.feed.hub.subscribe(other)
    c = FyersCollector(s.store, s.feed.hub, u, ["RELIANCE.NS", "^NSEI"], MARKET, m, s.feed.clock, limiter=F.RateLimiter(100, 1000))
    out = c.poll_once()
    assert out["new"] == 2 * 48 and out["errors"] == []                  # 09:15..10:02 completed; 10:03 forming
    bars = s.store.bars_get(u, "RELIANCE.NS")
    assert bars[0]["ts"].startswith("2026-10-16T09:15") and bars[-1]["ts"].startswith("2026-10-16T10:02")
    assert s.store.bars_get(other, "RELIANCE.NS") == []
    msgs = []
    while not mine.q.empty():
        msgs.append(mine.q.get_nowait())
    assert sum(1 for x in msgs if x["type"] == "bar") == 96 and msgs[-1]["type"] == "status"
    assert theirs.q.empty()
    assert c.poll_once()["new"] == 0                                       # idempotent


def test_revision_and_catch_up(svc):
    s, m = svc
    u = s.local_user
    login(s, m, u)
    c = FyersCollector(s.store, s.feed.hub, u, ["RELIANCE.NS"], MARKET, m, s.feed.clock, limiter=F.RateLimiter(100, 1000))
    c.poll_once()
    t0 = ep("2026-10-16 09:20")
    m.override["NSE:RELIANCE-EQ"] = {t0: [t0, 100, 102, 99, 101, 5]}
    m.now += pd.Timedelta(minutes=5)                                       # 5 missed minutes
    out = c.poll_once()
    assert out["revised"] == 1 and out["new"] == 5
    r = [b for b in s.store.bars_get(u, "RELIANCE.NS") if b["ts"].startswith("2026-10-16T09:20")][0]
    assert r["revisions"] == 1 and r["high"] == 102


def test_token_expiry_pauses_feed(svc):
    s, m = svc
    u = s.local_user
    login(s, m, u)
    c = FyersCollector(s.store, s.feed.hub, u, ["RELIANCE.NS"], MARKET, m, s.feed.clock, limiter=F.RateLimiter(100, 1000))
    m.valid_tokens.clear()
    c.poll_once()
    assert c.status == "TOKEN_EXPIRED" and not s.store.broker_public(u, "fyers")["token_valid"]
    n = len(m.calls)
    c.poll_once()
    assert len(m.calls) == n                                               # no further FYERS calls until re-login


def test_rate_limit_guard_stops_after_two_breaches(svc):
    s, m = svc
    u = s.local_user
    login(s, m, u)
    c = FyersCollector(s.store, s.feed.hub, u, ["A.NS", "B.NS", "C.NS"], MARKET, m, s.feed.clock, limiter=F.RateLimiter(100, 1000))
    m.fail = [429, 429]
    c.poll_once()
    assert c.status == "RATE_LIMIT_GUARD"


def test_poll_schedule(svc):
    s, m = svc
    c = FyersCollector(s.store, s.feed.hub, s.local_user, ["A.NS"], MARKET, m, poll_every_min=5)
    assert not c.due(utc("2026-10-16 09:10:10"))                           # before open
    assert not c.due(utc("2026-10-16 09:20:02"))                           # within 5 s grace
    assert c.due(utc("2026-10-16 09:20:06")) and not c.due(utc("2026-10-16 09:20:40"))   # once per slot
    assert not c.due(utc("2026-10-16 09:21:06"))                           # not a 5-minute slot
    assert not c.due(utc("2026-10-17 10:00:06"))                           # Saturday
    assert not c.due(utc("2026-10-20 10:00:06"))                           # holiday
    with pytest.raises(ValueError):
        FyersCollector(s.store, s.feed.hub, 1, [f"S{i}" for i in range(101)], MARKET)


def test_hub_backpressure():
    h = BarHub()
    sub = h.subscribe(1, ["A.NS"], maxsize=2)
    for i in range(5):
        h.publish(1, {"type": "bar", "symbol": "A.NS", "i": i})
    h.publish(1, {"type": "bar", "symbol": "B.NS"})
    assert sub.dropped == 3 and [sub.q.get_nowait()["i"] for _ in range(2)] == [3, 4]
    assert h.publish(2, {"type": "bar", "symbol": "A.NS"}) == 0


def test_scan_uses_users_fyers_daily_data(svc):
    s, m = svc

    def daily(method, url, params, body, headers, timeout):
        if params and params.get("resolution") == "D":
            days = pd.bdate_range(end="2026-10-15", periods=260)
            return 200, {"s": "ok", "candles": [[int(pd.Timestamp(d, tz="Asia/Kolkata").timestamp()), 100 + i * 0.3, 101 + i * 0.3,
                                                99 + i * 0.3, 100.5 + i * 0.3, 10000] for i, d in enumerate(days)]}
        return m(method, url, params, body, headers, timeout)
    s.feed.transport = daily
    u = s.local_user
    login(s, m, u)
    s.store.set_pref(u, "data_source", "fyers")
    p = s.provider_for(u)
    assert p.name.startswith("FYERS")
    df = p.fetch_many(["RELIANCE.NS"], "1D")["RELIANCE.NS"]
    assert len(df) == 260 and df.index[-1].strftime("%Y-%m-%d") == "2026-10-15"
    s.store.set_pref(u, "data_source", "default")
    assert s.provider_for(u).name.startswith("FIXTURE")


def test_api_and_websocket_broadcast(svc, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    s, m = svc
    monkeypatch.setenv("AMW_AUTH_MODE", "token")
    ua, ta = s.store.create_user("alice")
    ub, tb = s.store.create_user("bob")
    c = TestClient(create_app(s))
    A, B = {"Authorization": f"Bearer {ta}"}, {"Authorization": f"Bearer {tb}"}
    r = c.put("/api/v1/broker/fyers", json={"app_id": APP, "secret": SECRET, "redirect_uri": "http://localhost:8501/Live"}, headers=A)
    assert r.status_code == 200 and SECRET not in r.text and APP not in r.text
    url = c.get("/api/v1/broker/fyers/login-url", headers=A).json()["url"]
    state = url.split("state=")[1]
    assert c.post("/api/v1/broker/fyers/callback", json={"auth_code": "GOODCODE0001", "state": state}, headers=B).status_code == 400
    state = c.get("/api/v1/broker/fyers/login-url", headers=A).json()["url"].split("state=")[1]
    assert c.post("/api/v1/broker/fyers/callback", json={"auth_code": "GOODCODE0001", "state": state}, headers=A).json()["logged_in"]
    assert TOKEN not in c.get("/api/v1/feed/status", headers=A).text
    assert c.get("/api/v1/feed/status", headers=B).json()["connected"] is False
    assert c.put("/api/v1/data-source?source=fyers", headers=A).json()["fallback"] is False      # alice logged in
    assert c.get("/api/v1/data-source", headers=B).json() == {"selected": "default", "active": s.provider_for(ub).name,
                                                               "data_kind": s.provider_for(ub).data_kind, "fallback": False,
                                                               "reason": None}
    with c.websocket_connect("/api/v1/stream/bars") as ws:
        ws.send_json({"token": "wrong"})
        assert ws.receive_json()["type"] == "error"
    with c.websocket_connect("/api/v1/stream/bars") as wa, c.websocket_connect("/api/v1/stream/bars") as wb:
        wa.send_json({"token": ta, "symbols": ["RELIANCE.NS"]})
        wb.send_json({"token": tb})
        assert wa.receive_json()["type"] == "subscribed" and wb.receive_json()["type"] == "subscribed"
        col = FyersCollector(s.store, s.feed.hub, ua, ["RELIANCE.NS", "TCS.NS"], MARKET, m, s.feed.clock, limiter=F.RateLimiter(100, 1000))
        col.poll_once()
        first = wa.receive_json()
        assert first["type"] == "bar" and first["symbol"] == "RELIANCE.NS" and first["ts"].startswith("2026-10-16T09:15")
        got = [first] + [wa.receive_json() for _ in range(47)]
        assert all(x["symbol"] == "RELIANCE.NS" for x in got)                # TCS filtered out by subscription
        assert s.feed.hub.publish(ub, {"type": "status", "symbol": None, "status": "x"}) == 1
        assert wb.receive_json()["status"] == "x"                            # bob receives only his own topic
    bars = c.get("/api/v1/feed/bars?symbol=RELIANCE.NS&limit=5", headers=A).json()
    assert len(bars) == 5 and c.get("/api/v1/feed/bars?symbol=RELIANCE.NS", headers=B).json() == []
    assert c.delete("/api/v1/broker/fyers", headers=A).json()["disconnected"] is True


def test_data_source_status_reports_fallback(svc):
    s, m = svc
    u = s.local_user
    s.store.set_pref(u, "data_source", "fyers")
    st = s.data_source_status(u)
    assert st["selected"] == "fyers" and st["fallback"] is True and st["active"].startswith("FIXTURE") and "log in" in st["reason"]
    login(s, m, u)
    st = s.data_source_status(u)
    assert st["fallback"] is False and st["active"].startswith("FYERS") and st["reason"] is None


def test_intraday_fetch_is_exchange_time_and_uses_resolution(svc):
    s, m = svc
    u = s.local_user
    login(s, m, u)
    s.store.set_pref(u, "data_source", "fyers")
    df = s.provider_for(u).fetch_many(["RELIANCE.NS"], "15m")["RELIANCE.NS"]
    call = [c for c in m.calls if c["url"].endswith("/data/history")][-1]
    assert call["params"]["resolution"] == "15" and str(df.index.tz) == "Asia/Kolkata"
    assert df.index[0].strftime("%H:%M") == "09:15"


def test_scan_stops_with_explicit_error_when_fyers_session_expires(svc):
    import time as _t
    s, m = svc
    u = s.local_user
    login(s, m, u)
    s.store.set_pref(u, "data_source", "fyers")
    m.valid_tokens.clear()                                                 # FYERS now rejects the token
    uni = pd.DataFrame({"symbol": list("ABCDEFGH"), "ticker": [f"{x}.NS" for x in "ABCDEFGH"], "company": "", "industry": ""})
    n0 = len(m.calls)
    j = s.start_scan(uni, "t", ["trend_continuation"])
    while j.status in ("queued", "running"):
        _t.sleep(0.05)
    assert j.status == "failed" and "data source stopped the scan" in j.error and "log in again" in j.error
    assert TOKEN not in j.error and len(m.calls) - n0 <= 6                 # aborted quickly, not one 401 per symbol


# ---------------------------------------------------------------- multi-process (UI process + API process, one DB)
def _wait(pred, timeout=10.0):
    import time as _t
    end = _t.time() + timeout
    while _t.time() < end:
        if pred():
            return True
        _t.sleep(0.05)
    return False


@pytest.fixture
def two_procs(tmp_path, monkeypatch):
    from src.data.fixture_provider import FixtureProvider
    from src.feed import collector as C
    from src.services.app_services import Services
    monkeypatch.setenv("AMW_SECRET_KEY", crypto.generate_master_key())
    monkeypatch.setenv("AMW_AUTH_MODE", "token")
    monkeypatch.setattr(C, "HEARTBEAT_S", 0.2)
    monkeypatch.setattr(C, "TOKEN_RECHECK_S", 0.2)
    m = MockFyers()
    db = f"sqlite:///{tmp_path}/shared.db"
    ui = Services(db_url=db, provider=FixtureProvider(end="2026-10-16"), feed_transport=m)
    api = Services(db_url=db, provider=FixtureProvider(end="2026-10-16"), feed_transport=m)
    ui.feed.clock = api.feed.clock = lambda: m.now.tz_convert("UTC").tz_localize(None).to_pydatetime()
    uid, tok = ui.store.create_user("trader")
    ui.feed.connect(uid, APP, SECRET, "http://localhost:8501/Live_Data_FYERS")
    yield ui, api, m, uid, tok
    for s in (ui, api):
        for c in list(s.feed.collectors.values()):
            c.stop()


def _login(s, m, uid):
    state = s.feed.login_url(uid).split("state=")[1]
    s.feed.complete_login(uid, "GOODCODE0001", state)


def test_one_collector_across_processes_stream_status_and_remote_stop(two_procs):
    import time as _t
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    ui, api, m, uid, tok = two_procs
    _login(ui, m, uid)
    c = TestClient(create_app(api))
    H = {"Authorization": f"Bearer {tok}"}
    with c.websocket_connect("/api/v1/stream/bars") as ws:
        ws.send_json({"token": tok, "symbols": ["RELIANCE.NS"]})
        assert ws.receive_json()["type"] == "subscribed"
        ui.feed.start(uid, ["RELIANCE.NS"], 1)                      # the collector runs in the UI process
        assert _wait(lambda: len(ui.store.bars_get(uid, "RELIANCE.NS")) == 48)
        got = []
        while sum(x["type"] == "bar" for x in got) < 48:             # ...and reaches the API process' stream via the DB
            got.append(ws.receive_json())
        bars = [x for x in got if x["type"] == "bar"]
        assert bars[0]["ts"].startswith("2026-10-16T09:15") and len({b["ts"] for b in bars}) == 48
    assert _wait(lambda: (api.feed.status(uid)["collector"] or {}).get("status") == "RUNNING")
    st = c.get("/api/v1/feed/status", headers=H).json()["collector"]
    assert st["runner"] == "another AMW process" and st["bars_new"] == 48 and "holder" not in str(st)
    with pytest.raises(ValueError, match="already running"):
        api.feed.start(uid, ["TCS.NS"], 1)                           # no second collector for the same account
    assert c.post("/api/v1/feed/start", json={"symbols": ["TCS.NS"], "poll_every_min": 1}, headers=H).status_code == 422
    api.store.set_pref(uid, "data_source", "fyers")
    lim = api.provider_for(uid).client.limiter                        # scans here share the account's minute budget
    assert lim.shared_minute is not None and lim is api.feed.limiter(uid)
    assert c.post("/api/v1/feed/stop", headers=H).json()["stopped"]  # Stop in the API process...
    local = ui.feed.collectors[uid]
    assert _wait(lambda: not local.alive())                           # ...stops the UI process' collector
    assert local.status == "STOPPED" and "another AMW session" in local.detail
    n = len(m.calls)
    _t.sleep(1.3)
    assert len(m.calls) == n                                          # no further FYERS calls
    api.feed.start(uid, ["RELIANCE.NS"], 1)                           # now the API process may run it
    assert ui.feed.status(uid)["collector"]["runner"] == "another AMW process"
    api.feed.stop(uid)
    assert ui.store.get_pref(uid, "feed", None)["desired"] == "stopped"


def test_login_in_other_process_resumes_expired_collector(two_procs):
    ui, api, m, uid, tok = two_procs
    _login(ui, m, uid)
    m.valid_tokens.clear()
    col = ui.feed.start(uid, ["RELIANCE.NS"], 1)
    assert _wait(lambda: col.status == "TOKEN_EXPIRED")
    assert _wait(lambda: (api.feed.status(uid)["collector"] or {}).get("status") == "TOKEN_EXPIRED")
    m.valid_tokens.add(TOKEN)
    _login(api, m, uid)                                               # user logs in through the API process
    assert _wait(lambda: col.status == "WAITING")                     # UI collector notices the new session
    m.now += pd.Timedelta(minutes=1)
    assert _wait(lambda: col.status == "RUNNING") and col.stats["bars_new"] == 49


def test_rate_limit_guard_is_shared_across_processes(two_procs):
    ui, api, m, uid, tok = two_procs
    _login(ui, m, uid)
    ui.feed.limiter(uid).record_breach("2026-10-16")                  # 1st breach seen by the UI process
    with pytest.raises(F.FyersError) as e:
        api.feed.limiter(uid).record_breach("2026-10-16")             # 2nd in the API process trips the shared guard
    assert e.value.code == "rate_limit_guard"
    col = FyersCollector(ui.store, ui.feed.hub, uid, ["RELIANCE.NS"], MARKET, m, ui.feed.clock, limiter=ui.feed.limiter(uid))
    n = len(m.calls)
    col.poll_once()
    assert col.status == "RATE_LIMIT_GUARD" and len(m.calls) == n
    api.store.set_pref(uid, "data_source", "fyers")
    with pytest.raises(F.FyersError):
        api.provider_for(uid).fetch_many(["RELIANCE.NS"], "1D")      # scans refuse too
    assert ui.feed.limiter(uid).breaches("2026-10-19") == 0           # next day: clear


def test_auto_resume_after_restart(two_procs):
    from sqlalchemy import update
    from src.storage.db import broker_connections
    ui, api, m, uid, tok = two_procs
    _login(ui, m, uid)
    col = ui.feed.start(uid, ["RELIANCE.NS"], 1)
    col._stop.set()                                                   # process "crashed": lease not released
    assert api.feed.resume_all(retry_after=0.3) == []                 # lease still looks alive -> deferred
    with ui.store.engine.begin() as cx:                               # ...until it goes stale
        cx.execute(update(broker_connections).values(feed_heartbeat=ui.feed.clock() - pd.Timedelta(minutes=5)))
    assert _wait(lambda: uid in api.feed.collectors and api.feed.collectors[uid].alive())
    api.feed.stop(uid)
    assert api.feed.resume_all() == []                                # user stopped it: not resumed


@pytest.mark.skipif(not __import__("os").environ.get("AMW_TEST_PG_URL"), reason="set AMW_TEST_PG_URL to run against PostgreSQL")
def test_feed_store_on_postgres(monkeypatch):
    """Lease compare-and-set, runtime status, bar upsert/revision and the gap-free stream tail on real PostgreSQL."""
    import os
    import secrets as _s
    from datetime import timedelta
    from src.storage.db import Store
    monkeypatch.setenv("AMW_SECRET_KEY", crypto.generate_master_key())
    s = Store(os.environ["AMW_TEST_PG_URL"])
    u, _ = s.create_user(f"pg_feed_{_s.token_hex(3)}")
    v, _ = s.create_user(f"pg_feed_{_s.token_hex(3)}")
    s.broker_put(u, "fyers", APP, SECRET, "http://localhost:8501/Live_Data_FYERS")
    now = utc("2026-10-16 10:03:20")
    assert s.feed_lease_acquire(u, "p1", now) and not s.feed_lease_acquire(u, "p2", now)
    assert s.feed_lease_renew(u, "p1", now, {"status": "RUNNING"}) and not s.feed_lease_renew(u, "p2", now)
    assert s.feed_lease_acquire(u, "p2", now + timedelta(seconds=91))          # stale holder replaced
    s.feed_lease_release(u, None, {"status": "STOPPED"})
    rt = s.feed_runtime(u)
    assert rt["feed_holder"] is None and rt["feed_status"]["status"] == "STOPPED"
    cur = s.bars_cursor(u)
    bars = [{"symbol": "RELIANCE.NS", "ts": f"2026-10-16T09:{15 + i}:00+05:30", "open": 1, "high": 2, "low": 0.5,
             "close": 1.5, "volume": 10, "source": "fyers"} for i in range(5)]
    assert len(s.bars_upsert(u, bars)["new"]) == 5
    rows, cur = s.bars_since(u, cur)
    assert [r["ts"][11:16] for r in rows] == ["09:15", "09:16", "09:17", "09:18", "09:19"]
    assert s.bars_since(u, cur)[0] == [] and s.bars_since(v, (None, 0))[0] == []
    assert s.bars_upsert(u, [{**bars[2], "high": 3}])["revised"]
    rows, cur = s.bars_since(u, cur)
    assert len(rows) == 1 and rows[0]["revisions"] == 1 and rows[0]["high"] == 3
    assert s.broker_delete(u, "fyers")


def test_minute_budget_is_shared_across_processes(two_procs):
    ui, api, m, uid, tok = two_procs
    a, b = ui.feed._shared_minute(uid), api.feed._shared_minute(uid)
    took = [a(5) for _ in range(3)] + [b(5) for _ in range(3)]
    assert took == [True] * 5 + [False]                                   # 5 per minute in total, not per process
    waits = []
    lim = F.RateLimiter(100, 10, clock=lambda: 0.0, sleeper=waits.append, shared_minute=lambda n: False, wall=lambda: 30.0)
    with pytest.raises(F.FyersError):
        lim.acquire(max_wait=10)                                          # waiting 30 s exceeds the allowed wait
    assert waits == []
