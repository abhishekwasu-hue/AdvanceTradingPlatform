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
