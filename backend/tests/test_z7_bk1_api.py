"""Z7 / U6 BK1: the breakout screens behind `screener_breakouts` (off by default) - hidden from the registry and refused in
screens, saved screens and alert rules while off; the catalog and the graded run on server bars when on."""
import asyncio

import pytest

from app.screener.runtime import SymbolData
from tests.breakout_fixtures import TRUE_BREAK, base_then
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner


def _run(coro):
    return asyncio.run(coro)


def _set(**flags):
    async def go():
        from app.platform import controls
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            for name, on in flags.items():
                stored[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


@pytest.fixture
def screener_on():
    _set(screener_v2=True, screener_breakouts=False)
    yield
    _set(screener_v2=False, screener_breakouts=False)


def test_the_flag_exists_and_is_off_by_default():
    from app.platform.controls import DEFAULT_OFF_FLAGS, FEATURE_FLAGS
    assert "screener_breakouts" in FEATURE_FLAGS and "screener_breakouts" in DEFAULT_OFF_FLAGS
    assert "places no orders" in FEATURE_FLAGS["screener_breakouts"]


def test_breakout_functions_are_hidden_and_refused_while_off(screener_on):
    headers, _ = _owner("bk1-off@example.com")
    reg = client.get("/api/screener/registry", headers=headers).json()["entries"]
    assert "RangeBreakout" not in reg and "DowTrend" not in reg and "SwingHigh" in reg
    v = client.post("/api/screener/validate", json={"source": 'RangeBreakout("up") AND close > 1', "base_tf": "1d"}, headers=headers).json()
    assert not v["ok"] and "screener_breakouts" in v["problems"][-1]["message"] and "RangeBreakout()" in v["problems"][-1]["message"]
    saved = client.post("/api/screener/screens", json={"name": "b", "source": 'NRBreakout("up")'}, headers=headers)
    assert saved.status_code == 422
    run = client.post("/api/screener/run", json={"source": 'RangeExpansion("down")', "symbols": ["X"]}, headers=headers)
    assert run.status_code == 422 and "screener_breakouts" in str(run.json())
    assert client.get("/api/screener/breakouts/catalog", headers=headers).status_code == 503
    # a plain screen is unaffected
    assert client.post("/api/screener/validate", json={"source": "close > 1"}, headers=headers).json()["ok"]


def test_alert_rules_refuse_breakout_conditions_while_off(screener_on):
    headers, _ = _owner("bk1-alert@example.com")
    r = client.post("/api/alerts/rules", json={"name": "b", "kind": "instrument", "symbol": "ABC", "condition": 'VolumeBreakout("up")',
                                               "base_tf": "15m"}, headers=headers)
    assert r.status_code == 422 and "screener_breakouts" in str(r.json()), r.text
    ok = client.post("/api/alerts/rules", json={"name": "c", "kind": "instrument", "symbol": "ABC", "condition": "close > 1", "base_tf": "15m"},
                     headers=headers)
    assert ok.status_code in (200, 201), ok.text


def test_catalog_and_graded_run_when_on(screener_on, monkeypatch):
    _set(screener_breakouts=True)
    headers, _ = _owner("bk1-on@example.com")
    reg = client.get("/api/screener/registry", headers=headers).json()["entries"]
    assert reg["RangeBreakout"]["choices"]["side"] == ["up", "down"]
    v = client.post("/api/screener/validate", json={"source": 'RangeBreakout("up") AND DowTrend(0)@1w != "DOWN"', "base_tf": "1d"}, headers=headers).json()
    assert v["ok"], v
    cat = client.get("/api/screener/breakouts/catalog", headers=headers).json()
    assert {t["id"] for t in cat["templates"]} >= {"swing_1h_range", "weekly_52w_high"} and "recommendation" in cat["disclaimer"]
    assert cat["selector"]["styles"]["swing"] == ["30m", "1h", "2h", "4h", "1d", "1w"]

    f = base_then([TRUE_BREAK])
    asked = []

    async def fake_fetch(session, tenant_id, symbols, exchange, base_tf, lookback, now=None, include_forming=False):
        asked.append((tuple(symbols), base_tf))
        return [SymbolData(s, {base_tf: f}) for s in symbols if s != "NODATA"], {"NODATA": "no bars from the broker"}, "broker:fake"
    from app.screener.breakouts import routes as bk_routes
    monkeypatch.setattr(bk_routes, "fetch_frames", fake_fetch)
    out = client.post("/api/screener/breakouts/run", json={"screen": "B1", "signal_tf": "1d", "symbols": ["abc", "NODATA"]}, headers=headers)
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["context_tfs"] == ["1w", "1M"] and body["style"] == "swing" and body["screenql"].startswith('RangeBreakout("up", 20')
    assert [r["symbol"] for r in body["rows"]] == ["ABC"] and body["rows"][0]["grade"] in ("A", "B", "C")
    assert body["rows"][0]["base_len"] == 20 and body["problems"] == {"NODATA": "no bars from the broker"}
    assert asked == [(("ABC", "NODATA"), "1d")]                     # daily signal: the context is resampled, no second fetch
    # a template with an intraday signal also fetches daily bars for the daily context
    asked.clear()
    out = client.post("/api/screener/breakouts/run", json={"template_id": "intraday_15m_nr7", "symbols": ["ABC"]}, headers=headers)
    assert out.status_code == 200 and asked == [(("ABC",), "15m"), (("ABC",), "1d")]
    bad = client.post("/api/screener/breakouts/run", json={"screen": "B1", "signal_tf": "1d", "params": {"vol_mult": -2}, "symbols": ["ABC"]},
                      headers=headers)
    assert bad.status_code == 422
    assert client.post("/api/screener/breakouts/run", json={"template_id": "nope", "symbols": ["ABC"]}, headers=headers).status_code == 404


# ------------------------------------------------------------------------------------------------- review fixes (M1-M3)
def _save_breakout_screen(headers, source='RangeBreakout("up", 10, 0, 0)', tf="5m"):
    _set(screener_breakouts=True)
    made = client.post("/api/screener/screens", json={"name": "bk", "source": source, "base_tf": tf}, headers=headers)
    assert made.status_code == 201, made.text
    _set(screener_breakouts=False)
    return made.json()["id"]


def test_screen_alert_rules_are_gated_and_bar_close_only(screener_on):
    headers, _ = _owner("bk1-screen-rule@example.com")
    sid = _save_breakout_screen(headers)
    r = client.post("/api/alerts/rules", json={"name": "s", "kind": "screen", "screen_id": sid, "symbols": ["ABC"]}, headers=headers)
    assert r.status_code == 422 and "screener_breakouts" in str(r.json())                              # M1: flag off
    _set(screener_breakouts=True, screener_intrabar=True)
    try:
        r = client.post("/api/alerts/rules", json={"name": "s", "kind": "screen", "screen_id": sid, "symbols": ["ABC"], "fire_on": "intrabar"},
                        headers=headers)
        assert r.status_code == 422 and "bar close only" in str(r.json())                              # M3
        r = client.post("/api/alerts/rules", json={"name": "i", "kind": "instrument", "symbol": "ABC", "condition": 'VolumeBreakout("up")',
                                                   "base_tf": "15m", "fire_on": "intrabar"}, headers=headers)
        assert r.status_code == 422 and "bar close only" in str(r.json())
        ok = client.post("/api/alerts/rules", json={"name": "s", "kind": "screen", "screen_id": sid, "symbols": ["ABC"]}, headers=headers)
        assert ok.status_code == 201, ok.text
    finally:
        _set(screener_breakouts=False, screener_intrabar=False)


def test_breakout_rules_pause_while_the_flag_is_off_and_resume_after(screener_on):
    from datetime import timedelta

    from app.alerts import barclose
    from app.db.models import AlertEventRecord, AlertRuleRecord
    from sqlalchemy import select
    from tests.test_s4a_barclose import TUE, FakeFetch, _bars, _rule, ist
    _, me = _owner("bk1-pause@example.com")
    rid = _rule(me["tenant_id"], condition='RangeBreakout("up", 10, 0, 0)', tf="5m")
    fetch = FakeFetch({"TCS": _bars(ist(TUE, 9, 15), [100.0] * 60 + [104.0])})            # 09:15 .. 14:15, a break on the last bar

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
    assert status == "active" and "screener_breakouts" in problem and fired == 0                          # M2: paused, not dropped
    _set(screener_breakouts=True)
    status, problem, fired = cycle(now + timedelta(minutes=2))
    assert status == "active" and problem is None and fired == 1                                          # resumes on the same bar


# ----------------------------------------------------------------------------------------------- review fixes (M7, M8, L)
def test_run_hides_signals_that_fail_the_htf_filter_unless_asked():
    from app.screener.breakouts import routes as bk_routes
    from tests.test_z7_bk1_breakouts import _with_context
    from tests.breakout_fixtures import HTF_DOWN, HTF_UP
    spec = {"screen": "B1", "signal_tf": "1d", "params": {}, "context": ("1w", "1M")}
    rows, _, hidden = bk_routes._grade_all(spec, {"X": _with_context(HTF_DOWN)}, 500, False)
    assert rows == [] and hidden == 1
    rows, _, hidden = bk_routes._grade_all(spec, {"X": _with_context(HTF_DOWN)}, 500, True)
    assert len(rows) == 1 and rows[0]["htf_pass"] is False and hidden == 0
    rows, _, hidden = bk_routes._grade_all(spec, {"X": _with_context(HTF_UP)}, 500, False)
    assert len(rows) == 1 and rows[0]["grade"] == "A"


def test_run_limits_and_input_checks(screener_on, monkeypatch):
    _set(screener_breakouts=True)
    headers, _ = _owner("bk1-limits@example.com")
    from app.screener.breakouts import routes as bk_routes
    calls = []

    async def fake_fetch(*args, **kwargs):
        calls.append(args)
        return [], {}, "broker:fake"
    monkeypatch.setattr(bk_routes, "fetch_frames", fake_fetch)
    big = client.post("/api/screener/breakouts/run", json={"screen": "B1", "signal_tf": "1d", "within": 100, "symbols": [f"S{i}" for i in range(30)]},
                      headers=headers)
    assert big.status_code == 422 and "at most" in str(big.json())                                       # M8
    wrong_tf = client.post("/api/screener/breakouts/run", json={"screen": "B7", "signal_tf": "1h", "symbols": ["ABC"]}, headers=headers)
    assert wrong_tf.status_code == 422 and calls == []                                                   # L6: before any fetch
    bad = client.post("/api/screener/breakouts/run", json={"screen": "B1", "signal_tf": "1d", "symbols": ["ABC;DROP"]}, headers=headers)
    assert bad.status_code == 422 and calls == []                                                        # L7


def test_weekly_signal_reads_monthly_context_from_daily_bars(screener_on, monkeypatch):
    _set(screener_breakouts=True)
    headers, _ = _owner("bk1-weekly@example.com")
    from app.screener.breakouts import routes as bk_routes
    from app.screener.runtime import resample
    from tests.sample_market import generate
    daily = generate(272, 100.0, 3, daily=True)
    asked, seen = [], {}

    async def fake_fetch(session, tenant_id, symbols, exchange, base_tf, lookback, now=None, include_forming=False):
        asked.append(base_tf)
        return [SymbolData(s, {base_tf: daily}) for s in symbols], {}, "broker:fake"

    real = bk_routes.quality.run_breakout

    def spy(screen, frames, signal_tf, **kw):
        seen.update(frames)
        return real(screen, frames, signal_tf, **kw)
    monkeypatch.setattr(bk_routes, "fetch_frames", fake_fetch)
    monkeypatch.setattr(bk_routes.quality, "run_breakout", spy)
    out = client.post("/api/screener/breakouts/run", json={"template_id": "weekly_52w_high", "symbols": ["ABC"]}, headers=headers)
    assert out.status_code == 200, out.text
    assert asked == ["1d"]                                                                               # L8: one daily fetch
    assert seen["1M"].equals(resample(daily, "1d", "1M")) and seen["1w"].equals(resample(daily, "1d", "1w"))
    assert out.json()["history_note"] and "htf_failed_hidden" in out.json()
