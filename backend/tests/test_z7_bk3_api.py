"""Z7 / U6 BK3: the VCP and indicator breakouts behind `screener_breakouts_bk3` (off by default) on top of
`screener_breakouts` - hidden from the registry, the catalog and the taxonomy and refused in screens, saved screens, runs
and alert rules unless BOTH flags are on; alert rules using them pause (stay active) while the BK3 flag is off. No new
route: the existing screener / breakout / alert routes carry the gate (tests/tenancy_route_table.py is unchanged)."""
from datetime import timedelta

import pytest

from app.screener.runtime import SymbolData
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_z7_bk1_api import _run, _set

BK3 = ("VCPBreakout", "VCPReady", "DonchianBreakout", "SqueezeBreakout", "MABreakout", "RegimeBreakout")


@pytest.fixture
def screener_on():
    _set(screener_v2=True, screener_breakouts=True, screener_breakouts_bk3=False)
    yield
    _set(screener_v2=False, screener_breakouts=False, screener_breakouts_bk3=False, screener_gallery=False)


def test_the_flag_exists_is_off_by_default_and_describes_research_only():
    from app.platform.controls import DEFAULT_OFF_FLAGS, FEATURE_FLAGS
    assert "screener_breakouts_bk3" in FEATURE_FLAGS and "screener_breakouts_bk3" in DEFAULT_OFF_FLAGS
    assert "places no orders" in FEATURE_FLAGS["screener_breakouts_bk3"] and "needs screener_breakouts" in FEATURE_FLAGS["screener_breakouts_bk3"]


def test_hidden_and_refused_while_the_bk3_flag_is_off(screener_on):
    headers, _ = _owner("bk3-off@example.com")
    reg = client.get("/api/screener/registry", headers=headers).json()["entries"]
    assert "RangeBreakout" in reg and not (set(BK3) & set(reg))
    v = client.post("/api/screener/validate", json={"source": 'DonchianBreakout("up") AND close > 1', "base_tf": "1d"}, headers=headers).json()
    msg = v["problems"][-1]["message"]
    assert not v["ok"] and "DonchianBreakout()" in msg and "screener_breakouts_bk3" in msg
    assert client.post("/api/screener/validate", json={"source": 'RangeBreakout("up")', "base_tf": "1d"}, headers=headers).json()["ok"]
    assert client.post("/api/screener/screens", json={"name": "b", "source": 'MABreakout("up")'}, headers=headers).status_code == 422
    run = client.post("/api/screener/run", json={"source": 'RegimeBreakout("down")', "symbols": ["X"]}, headers=headers)
    assert run.status_code == 422 and "screener_breakouts_bk3" in str(run.json())
    cat = client.get("/api/screener/breakouts/catalog", headers=headers).json()
    assert "daily_vcp" not in {t["id"] for t in cat["templates"]} and "B10" not in cat["screens"] and "B1" in cat["screens"]
    assert not ({"B3", "B10", "B11", "B12", "B15"} & {b["screen"] for b in cat["blocks"]["breakout"]})
    assert "vcp_ratio" not in {p["name"] for p in cat["params"]}
    r = client.post("/api/screener/breakouts/run", json={"screen": "B10", "signal_tf": "1d", "symbols": ["ABC"]}, headers=headers)
    assert r.status_code == 503 and r.headers.get("x-feature-disabled") == "screener_breakouts_bk3"
    r = client.post("/api/screener/breakouts/run", json={"template_id": "daily_vcp", "symbols": ["ABC"]}, headers=headers)
    assert r.status_code == 503
    # BK3 review L5: the flag is checked before the request is resolved - bad BK3 parameters still read "not enabled"
    r = client.post("/api/screener/breakouts/run", json={"screen": "B10", "signal_tf": "1d", "params": {"donchian_n": 99999}, "symbols": ["ABC"]},
                    headers=headers)
    assert r.status_code == 503 and r.headers.get("x-feature-disabled") == "screener_breakouts_bk3"
    r = client.post("/api/alerts/rules", json={"name": "b", "kind": "instrument", "symbol": "ABC", "condition": 'SqueezeBreakout("up")',
                                               "base_tf": "15m"}, headers=headers)
    assert r.status_code == 422 and "screener_breakouts_bk3" in str(r.json())


def test_the_bk3_flag_alone_is_not_enough(screener_on):
    _set(screener_breakouts=False, screener_breakouts_bk3=True)
    headers, _ = _owner("bk3-only@example.com")
    reg = client.get("/api/screener/registry", headers=headers).json()["entries"]
    assert not (set(BK3) & set(reg)) and "RangeBreakout" not in reg
    v = client.post("/api/screener/validate", json={"source": 'VCPBreakout("up")', "base_tf": "1d"}, headers=headers).json()
    assert not v["ok"] and "screener_breakouts, screener_breakouts_bk3" in v["problems"][-1]["message"]
    assert client.get("/api/screener/breakouts/catalog", headers=headers).status_code == 503


def test_listed_validated_catalogued_and_run_when_both_are_on(screener_on, monkeypatch):
    _set(screener_breakouts_bk3=True)
    headers, _ = _owner("bk3-on@example.com")
    reg = client.get("/api/screener/registry", headers=headers).json()["entries"]
    assert set(BK3) <= set(reg) and reg["SqueezeBreakout"]["choices"]["mode"] == ["bollinger", "ttm"]
    for src, tf in (('VCPBreakout("up") AND DowTrend(0)@1w != "DOWN"', "1d"), ('VCPReady("up", 2, 1, 5)', "1d"), ('DonchianBreakout("down", 55)', "15m"),
                    ('SqueezeBreakout("up", "ttm")', "15m"), ('MABreakout("up", 20, "ema")', "1h"), ('RegimeBreakout("up")', "5m")):
        v = client.post("/api/screener/validate", json={"source": src, "base_tf": tf}, headers=headers).json()
        assert v["ok"], (src, v)
    cat = client.get("/api/screener/breakouts/catalog", headers=headers).json()
    ids = {t["id"]: t for t in cat["templates"]}
    assert ids["daily_vcp"]["screenql"].startswith('VCPBreakout("up", 2, 1, 1.5, 0.2, TRUE, TRUE)')
    assert ids["daily_vcp_watchlist"]["screenql"].startswith('VCPReady("up"') and cat["screens"]["B15"] == "RSI / ADX regime breakout"

    from tests.breakout_bk3_fixtures import vcp
    f = vcp()

    asked = []

    async def fake_fetch(session, tenant_id, symbols, exchange, base_tf, lookback, now=None, include_forming=False, **kw):
        asked.append(dict(lookback))
        return [SymbolData(s, {base_tf: f.iloc[-60:] if s == "SHORT" else f}) for s in symbols], {}, "broker:fake"
    from app.screener.breakouts import routes as bk_routes
    monkeypatch.setattr(bk_routes, "fetch_frames", fake_fetch)
    out = client.post("/api/screener/breakouts/run", json={"template_id": "daily_vcp", "symbols": ["abc", "short"]}, headers=headers)
    assert out.status_code == 200, out.text
    # BK3 review L4: the fetch covers the trend template's look-backs end to end (the year 250 + the slow SMA 200 over its
    # 21-bar slope + ATR 14 + 1), and a short history is reported as such instead of running to a silent "no match"
    assert asked[-1] == {"1d": 486}
    assert "not enough history: 60 1d bars" in out.json()["problems"]["SHORT"] and "at least 251" in out.json()["problems"]["SHORT"]
    rows = out.json()["rows"]
    assert [r["symbol"] for r in rows] == ["ABC"] and rows[0]["event"] == "VCP3" and rows[0]["side"] == "up"
    out = client.post("/api/screener/breakouts/run", json={"template_id": "daily_vcp_watchlist", "symbols": ["ABC"], "within": 10}, headers=headers)
    assert out.status_code == 200 and {r["status"] for r in out.json()["rows"]} == {"ready"}
    bad = client.post("/api/screener/breakouts/run", json={"screen": "B3", "signal_tf": "1h", "symbols": ["ABC"]}, headers=headers)
    assert bad.status_code == 422 and "1d" in bad.text                                                  # VCP is a daily screen


def test_bk3_alert_rules_pause_while_the_flag_is_off_and_resume_after(screener_on):
    from sqlalchemy import select

    from app.alerts import barclose
    from app.db.models import AlertEventRecord, AlertRuleRecord
    from tests.test_s4a_barclose import TUE, FakeFetch, _bars, _rule, ist
    _, me = _owner("bk3-pause@example.com")
    rid = _rule(me["tenant_id"], condition='DonchianBreakout("up", 10, 0)', tf="5m")
    fetch = FakeFetch({"TCS": _bars(ist(TUE, 9, 15), [100.0] * 60 + [104.0])})            # a break on the last bar

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
    assert status == "active" and "screener_breakouts_bk3" in problem and fired == 0
    _set(screener_breakouts_bk3=True)
    status, problem, fired = cycle(now + timedelta(minutes=2))
    assert status == "active" and problem is None and fired == 1


def test_the_taxonomy_hides_the_bk3_templates_until_both_flags_are_on(screener_on):
    _set(screener_gallery=True)
    headers, _ = _owner("bk3-taxonomy@example.com")
    ids = lambda: {s["id"] for s in client.get("/api/screener/taxonomy", headers=headers).json()["screens"]}  # noqa: E731
    shown = ids()
    assert "breakouts.daily_52w_high" in shown and "breakouts_bk3.daily_vcp" not in shown
    _set(screener_breakouts_bk3=True)
    assert "breakouts_bk3.daily_vcp" in ids()
    _set(screener_breakouts=False)
    shown = ids()
    assert "breakouts_bk3.daily_vcp" not in shown and "breakouts.daily_52w_high" not in shown
    from app.screener import taxonomy as tx
    assert {"breakouts", "breakouts_bk3"} <= set(tx.default_hidden())


def test_history_counts_only_the_windows_the_chosen_mode_reads():
    """BK3 re-check: the template off leaves the VCP's base walk only; TTM does not read the band-width look-back; the MA
    stack's lines count only when it is on."""
    from app.screener.breakouts.routes import history_bars, min_signal_bars
    assert (min_signal_bars("B3", {}), history_bars("B3", "1d", {})) == (251, 486)
    assert (min_signal_bars("B3", {"tt_filter": False}), history_bars("B3", "1d", {"tt_filter": False})) == (126, 300)
    assert min_signal_bars("B11", {}) == 146 and min_signal_bars("B11", {"squeeze_mode": "ttm"}) == 21
    assert min_signal_bars("B12", {}) == 61 and min_signal_bars("B12", {"ma_stack": True}) == 201
    assert min_signal_bars("B10", {"donchian_n": 55}) == 56 and min_signal_bars("B15", {}) == 35 and min_signal_bars("B1", {}) == 0
