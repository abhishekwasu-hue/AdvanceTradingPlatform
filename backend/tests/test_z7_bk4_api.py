"""Z7 / U6 BK4: the trendline / pattern / gap / retest breakouts behind `screener_breakouts_bk4` (off by default) on top of
`screener_breakouts` - hidden from the registry, the catalog and the taxonomy and refused in screens, saved screens, runs
and alert rules unless BOTH flags are on; alert rules using them pause while the BK4 flag is off. No new route
(tests/tenancy_route_table.py is unchanged)."""
from datetime import timedelta

import pytest

from app.screener.runtime import SymbolData
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_z7_bk1_api import _run, _set

BK4 = ("TrendlineBreakout", "PatternBreakout", "GapBreakout", "GapType", "RetestEntry")


@pytest.fixture
def screener_on():
    _set(screener_v2=True, screener_breakouts=True, screener_breakouts_bk3=False, screener_breakouts_bk4=False)
    yield
    _set(screener_v2=False, screener_breakouts=False, screener_breakouts_bk3=False, screener_breakouts_bk4=False, screener_gallery=False)


def test_the_flag_exists_is_off_by_default_and_describes_research_only():
    from app.platform.controls import DEFAULT_OFF_FLAGS, FEATURE_FLAGS
    assert "screener_breakouts_bk4" in FEATURE_FLAGS and "screener_breakouts_bk4" in DEFAULT_OFF_FLAGS
    assert "places no orders" in FEATURE_FLAGS["screener_breakouts_bk4"] and "needs screener_breakouts" in FEATURE_FLAGS["screener_breakouts_bk4"]


def test_hidden_and_refused_while_the_bk4_flag_is_off(screener_on):
    _set(screener_breakouts_bk3=True)                          # the BK3 set on does not open BK4
    headers, _ = _owner("bk4-off@example.com")
    reg = client.get("/api/screener/registry", headers=headers).json()["entries"]
    assert "DonchianBreakout" in reg and not (set(BK4) & set(reg))
    v = client.post("/api/screener/validate", json={"source": 'TrendlineBreakout("up") AND close > 1', "base_tf": "1d"}, headers=headers).json()
    msg = v["problems"][-1]["message"]
    assert not v["ok"] and "TrendlineBreakout()" in msg and "screener_breakouts_bk4" in msg
    assert client.post("/api/screener/screens", json={"name": "b", "source": 'RetestEntry("up")'}, headers=headers).status_code == 422
    run = client.post("/api/screener/run", json={"source": 'PatternBreakout("up")', "symbols": ["X"]}, headers=headers)
    assert run.status_code == 422 and "screener_breakouts_bk4" in str(run.json())
    cat = client.get("/api/screener/breakouts/catalog", headers=headers).json()
    assert "daily_flag" not in {t["id"] for t in cat["templates"]} and "B5" not in cat["screens"] and "B10" in cat["screens"]
    assert "tl_mode" not in {p["name"] for p in cat["params"]}
    r = client.post("/api/screener/breakouts/run", json={"screen": "B8", "signal_tf": "1d", "symbols": ["ABC"]}, headers=headers)
    assert r.status_code == 503 and r.headers.get("x-feature-disabled") == "screener_breakouts_bk4"
    r = client.post("/api/alerts/rules", json={"name": "b", "kind": "instrument", "symbol": "ABC", "condition": 'GapType("up") == "GAP_AND_GO"',
                                               "base_tf": "15m"}, headers=headers)
    assert r.status_code == 422 and "screener_breakouts_bk4" in str(r.json())


def test_listed_validated_and_run_when_both_are_on(screener_on, monkeypatch):
    _set(screener_breakouts_bk4=True)
    headers, _ = _owner("bk4-on@example.com")
    reg = client.get("/api/screener/registry", headers=headers).json()["entries"]
    assert set(BK4) <= set(reg) and "VCPBreakout" not in reg                     # BK3 stays behind its own flag
    for src, tf in (('TrendlineBreakout("up", "channel")', "1h"), ('PatternBreakout("down", "triangle")', "1d"),
                    ('GapBreakout("up", "prev_day", 30)', "5m"), ('GapType("down") != "GAP_FILL"', "1d"), ('RetestEntry("up", "donchian", 15)', "15m")):
        v = client.post("/api/screener/validate", json={"source": src, "base_tf": tf}, headers=headers).json()
        assert v["ok"], (src, v)
    cat = client.get("/api/screener/breakouts/catalog", headers=headers).json()
    assert {"daily_flag", "intraday_5m_gap_and_go", "daily_range_retest"} <= {t["id"] for t in cat["templates"]}
    assert "measured_move" in cat["blocks"]["result_columns"]

    from tests.breakout_bk4_fixtures import flag, lifted
    f = lifted(flag())

    async def fake_fetch(session, tenant_id, symbols, exchange, base_tf, lookback, now=None, include_forming=False, **kw):
        return [SymbolData(s, {base_tf: f}) for s in symbols], {}, "broker:fake"
    from app.screener.breakouts import routes as bk_routes
    monkeypatch.setattr(bk_routes, "fetch_frames", fake_fetch)
    out = client.post("/api/screener/breakouts/run", json={"template_id": "daily_flag", "symbols": ["abc"]}, headers=headers)
    assert out.status_code == 200, out.text
    rows = out.json()["rows"]
    assert [r["symbol"] for r in rows] == ["ABC"] and rows[0]["event"] == "FLAG" and rows[0]["measured_move"] > rows[0]["level"]
    bad = client.post("/api/screener/breakouts/run", json={"screen": "B8", "signal_tf": "1h", "symbols": ["ABC"]}, headers=headers)
    assert bad.status_code == 422                                                         # gaps: intraday sessions or Daily


def test_intraday_gap_runs_fetch_whole_sessions():
    from app.screener.breakouts import routes as bk_routes
    assert bk_routes.history_bars("B8", "5m", {}) >= (1 + 14 + 1) * 75
    assert bk_routes.history_bars("B8", "5m", {"gap_level": "base", "gap_base_n": 40}) >= 41 * 75
    assert bk_routes.history_bars("B8", "1d", {}) == bk_routes.SIGNAL_HISTORY_BARS


def test_bk4_alert_rules_pause_while_the_flag_is_off_and_resume_after(screener_on):
    from sqlalchemy import select

    from app.alerts import barclose
    from app.db.models import AlertEventRecord, AlertRuleRecord
    from tests.test_s4a_barclose import TUE, FakeFetch, _bars, _rule, ist
    _, me = _owner("bk4-pause@example.com")
    rid = _rule(me["tenant_id"], condition='RetestEntry("up", "range", 10, FALSE) OR TrendlineBreakout("up", "line", 2, 0)', tf="5m")
    fetch = FakeFetch({"TCS": _bars(ist(TUE, 9, 15), [100.0] * 60 + [104.0])})

    def cycle(now):
        async def go():
            async with _session_factory() as session:
                await barclose.run_due(session, now=now, fetch=fetch, holidays=set())
                rule = await session.get(AlertRuleRecord, rid)
                events = list(await session.scalars(select(AlertEventRecord).where(AlertEventRecord.rule_id == rid)))
                return rule.status, rule.last_problem, len(events)
        return _run(go())
    now = ist(TUE, 14, 21)
    status, problem, fired = cycle(now)
    assert status == "active" and "screener_breakouts_bk4" in problem and fired == 0
    _set(screener_breakouts_bk4=True)
    status, problem, fired = cycle(now + timedelta(minutes=2))
    assert status == "active" and problem is None             # evaluated again (61 bars: too few for a trendline, so no match yet)


def test_the_taxonomy_hides_the_bk4_templates_until_both_flags_are_on(screener_on):
    _set(screener_gallery=True)
    headers, _ = _owner("bk4-taxonomy@example.com")
    ids = lambda: {s["id"] for s in client.get("/api/screener/taxonomy", headers=headers).json()["screens"]}  # noqa: E731
    assert "breakouts_bk4.daily_flag" not in ids()
    _set(screener_breakouts_bk4=True)
    shown = ids()
    assert "breakouts_bk4.daily_flag" in shown and "breakouts_bk3.daily_vcp" not in shown
    from app.screener import taxonomy as tx
    assert {"breakouts", "breakouts_bk3", "breakouts_bk4"} <= set(tx.default_hidden())
