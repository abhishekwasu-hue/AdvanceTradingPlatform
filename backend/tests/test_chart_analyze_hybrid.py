"""M59 CR-4: Analyze a chart, hybrid mode - "Advanced analysis (code + AI)" - end to end on the test app.

The user picks the symbol and timeframe; ATP reads its own structure (the price-action layer on stored lake candles, the
market-zones layer) and draws it by code on a server-rendered chart beside the user's picture; the private engine's
hybrid entry point (a fake here) gets only generic facts. Made-up data only: the public 15-minute fixture session
moved to recent weeks, a synthetic picture and a fake engine (tests/chart_analyze_fakes.py); no network.
"""
import asyncio
import inspect
import json
import uuid
from datetime import UTC, datetime

import pandas as pd
import pytest
from sqlalchemy import delete

from app.ai.chart_png import is_png
from app.chart_analyze import FLAG, HYBRID_FLAG, SHARE_FLAG, engine, hybrid, provider, reading_png, routes, service
from app.chart_analyze.engine import Answer, EngineUnavailable
from app.charts import price_action_layer as pal
from app.charts import zones_layer as zl
from app.core import rate_limit
from app.db.models import MdCandleRecord
from app.platform import controls
from app.signals import language
from tests.ch3_pa_cases import frame
from tests.chart_analyze_fakes import FakeProvider, ImageOnlyFakeProvider, answer, synthetic_chart
from tests.test_auth_api import _session_factory, client
from tests.test_chart_analyze_api import BASE, _make_super_admin, _owner, _run, _set_settings

PNG = synthetic_chart()
SYMBOL = f"HYB{uuid.uuid4().hex[:6].upper()}"
KEY = f"NSE:{SYMBOL}"
EMPTY_SYMBOL = f"HYE{uuid.uuid4().hex[:6].upper()}"


def _recent_frame() -> pd.DataFrame:
    """The public fixture session, moved forward by whole weeks so its last candle closed shortly before now."""
    f = frame()
    weeks = int((pd.Timestamp(datetime.now(UTC)) - f["bar_end"].iloc[-1]) / pd.Timedelta(days=7))
    shift = pd.Timedelta(days=7 * weeks)
    return f.assign(timestamp=f["timestamp"] + shift, bar_end=f["bar_end"] + shift)


F = _recent_frame()


async def _drop() -> None:
    async with _session_factory() as session:
        await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == KEY))
        await session.commit()


async def _put() -> None:
    async with _session_factory() as session:
        session.add_all([MdCandleRecord(instrument_key=KEY, timeframe="15m", ts=b.bar_end.to_pydatetime(), source="vendor:test", version=1,
                                        open=float(b.open), high=float(b.high), low=float(b.low), close=float(b.close), volume=0,
                                        ingested_at=b.bar_end.to_pydatetime()) for b in F.itertuples()])
        await session.commit()


@pytest.fixture(scope="module", autouse=True)
def _lake():
    _run(_drop())
    _run(_put())
    yield
    _run(_drop())


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CHART_ANALYZE_DATA_DIR", str(tmp_path / "private"))
    rate_limit.reset(engine.FEATURE)
    _set_settings(rate_limit_per_minute=600)
    pal.cache_clear()
    provider.set_for_tests(FakeProvider())
    yield
    provider.reset_for_tests()
    pal.cache_clear()
    rate_limit.reset(engine.FEATURE)


def _allow(name: str, tenant_id: int, on: bool = True) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get(name) or {"on": False, "tenants": []}
            tenants = set(entry.get("tenants") or [])
            (tenants.add if on else tenants.discard)(tenant_id)
            flags[name] = {"on": bool(entry.get("on")), "tenants": sorted(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _hybrid_owner(tag: str, *, hybrid_on: bool = True) -> dict:
    who = _owner(f"hyb-{tag}")
    _allow(pal.FLAG, who["tenant_id"])
    if hybrid_on:
        _allow(HYBRID_FLAG, who["tenant_id"])
    return who


def _add(who: dict, symbol: str | None = SYMBOL, timeframe: str | None = "15m", **extra):
    data = {"mode": "hybrid", **({"symbol": symbol} if symbol is not None else {}), **({"timeframe": timeframe} if timeframe is not None else {}),
            **extra}
    return client.post(f"{BASE}/items", headers=who["headers"], files={"file": ("chart.png", PNG, "image/png")}, data=data)


def _fake_ask(monkeypatch, *, error=None, raises=None):
    calls = []

    async def fake_ask(session, user, tenant, instr, png, settings, *, now, client=None):
        calls.append({"png": png, "user": instr.user, "system": instr.system})
        if raises is not None:
            raise raises
        return Answer(text=json.dumps(answer("forming")), error=error, provider="anthropic", model="claude-test", cost_usd=0.02,
                      billed_to_user_key=False)
    monkeypatch.setattr(service, "ask", fake_ask)
    return calls


def _zones(monkeypatch):
    """Two stored areas of interest near the fixture's last close (the test DB holds no zone snapshots)."""
    close = float(F["close"].iloc[-1])

    async def fake_zones(*, instrument, at, price, family, max_bands, user, session):
        return {"instrument": instrument, "bands": [
            {"role": "supply", "low": round(close * 1.004, 2), "high": round(close * 1.007, 2), "state": "fresh", "timeframe": "15m"},
            {"role": "demand", "low": round(close * 0.993, 2), "high": round(close * 0.996, 2), "state": "tested", "timeframe": "1h"}]}
    monkeypatch.setattr(zl, "market_zones_layer", fake_zones)
    return close


def _texts(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from _texts(v)
    elif isinstance(value, list):
        for v in value:
            yield from _texts(v)
    elif isinstance(value, str):
        yield value


# --- the flag ------------------------------------------------------------------------------------------------------------

def test_flag_is_registered_off_by_default_and_needs_the_engine():
    assert HYBRID_FLAG in controls.FEATURE_FLAGS and HYBRID_FLAG in controls.DEFAULT_OFF_FLAGS
    assert "places no orders" in controls.FEATURE_FLAGS[HYBRID_FLAG]
    assert HYBRID_FLAG in controls.ENGINE_FLAGS
    provider.set_for_tests(None)
    assert HYBRID_FLAG in controls.engine_missing_flags()
    provider.set_for_tests(FakeProvider())
    assert not set(controls.ENGINE_FLAGS) & set(controls.engine_missing_flags())   # M66: the chart flags; trade_engine has its own engine


def test_flag_off_a_user_reads_one_plain_line_the_admin_reads_the_flag_and_where():
    user = _hybrid_owner("off-user", hybrid_on=False)
    status = client.get(f"{BASE}/status", headers=user["headers"]).json()
    assert status["modes"]["hybrid"] is False and status["hybrid"]["message"] == hybrid.OFF_MESSAGE and "admin" not in status["hybrid"]
    refused = _add(user)
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail == {"message": hybrid.OFF_MESSAGE, "code": "hybrid_off"}
    assert "chart_analyze" not in json.dumps(detail) and "_" not in detail["message"]
    assert client.get(f"{BASE}/items", headers=user["headers"]).json()["items"] == []          # nothing stored

    admin = _hybrid_owner("off-admin", hybrid_on=False)
    _make_super_admin(admin["user_id"])
    detail = _add(admin).json()["detail"]
    assert detail["code"] == "hybrid_off" and detail["admin"]["flag"] == HYBRID_FLAG and "Feature flags" in detail["admin"]["where"]
    status = client.get(f"{BASE}/status", headers=admin["headers"]).json()
    assert status["hybrid"]["admin"]["flag"] == HYBRID_FLAG


def test_flag_switched_off_after_the_upload_refuses_the_analysis_and_the_download():
    who = _hybrid_owner("later-off")
    item = _add(who).json()
    _allow(HYBRID_FLAG, who["tenant_id"], on=False)
    run = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"])
    assert run.status_code == 409 and run.json()["detail"]["code"] == "hybrid_off"
    assert client.get(f"{BASE}/items/{item['id']}/reading.png", headers=who["headers"]).status_code == 409


# --- the pick ------------------------------------------------------------------------------------------------------------

def test_symbol_and_timeframe_are_optional_and_a_given_value_is_checked():
    """M84: both values are optional overrides (an empty one is read from the chart); a value given is still checked."""
    who = _hybrid_owner("pick")
    for kwargs, code in (({"timeframe": "7m"}, "bad_timeframe"), ({"symbol": "NIFTY<script>"}, "bad_symbol")):
        got = _add(who, **kwargs)
        assert got.status_code == 422 and got.json()["detail"]["code"] == code, (kwargs, got.text)
    assert client.get(f"{BASE}/items", headers=who["headers"]).json()["items"] == []
    for kwargs, pick in (({"symbol": None}, {"symbol": None, "timeframe": "15m"}), ({"symbol": "  "}, {"symbol": None, "timeframe": "15m"}),
                         ({"timeframe": None}, {"symbol": SYMBOL, "timeframe": None}), ({"symbol": None, "timeframe": None}, {"symbol": None, "timeframe": None})):
        got = _add(who, **kwargs)
        assert got.status_code == 201 and got.json()["pick"] == pick and got.json()["needs"] == [], (kwargs, got.text)
    ok = _add(who, symbol=" hyb test ".replace("hyb test", SYMBOL.lower()), timeframe="15m")
    assert ok.status_code == 201 and ok.json()["pick"] == {"symbol": SYMBOL, "timeframe": "15m"} and ok.json()["result"] is None


def test_the_request_contract_has_no_price_field_from_the_image(monkeypatch):
    """The upload takes the picture, the mode and the pick only; a price sent along is ignored, and every price in the
    reading comes from the stored candles."""
    params = set(inspect.signature(routes.add_item).parameters) - {"user", "session"}
    assert params == {"file", "link", "mode", "symbol", "timeframe"}
    who = _hybrid_owner("noprice")
    _fake_ask(monkeypatch)
    item = _add(who, price="99999", price_low="1", price_high="2", last_close="12345").json()
    assert item["pick"] == {"symbol": SYMBOL, "timeframe": "15m"}
    done = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"]).json()
    reading = done["hybrid"]["reading"]
    assert reading["last_close"] == round(float(F["close"].iloc[-1]), 2)
    lake = {round(float(v), 2) for v in pd.concat([F["open"], F["high"], F["low"], F["close"]])}
    assert {b["c"] for b in reading["bars"]} <= lake and "99999" not in json.dumps(reading) and "12345" not in json.dumps(done["hybrid"]["pick"])


# --- the analysis --------------------------------------------------------------------------------------------------------

def test_hybrid_happy_path_with_the_engines_hybrid_entry_point(monkeypatch):
    who = _hybrid_owner("happy")
    close = _zones(monkeypatch)
    calls = _fake_ask(monkeypatch)
    fake = FakeProvider()
    provider.set_for_tests(fake)
    status = client.get(f"{BASE}/status", headers=who["headers"]).json()
    assert status["modes"]["hybrid"] is True and status["mode_labels"] == {"image_only": "AI only", "hybrid": "Advanced analysis (code + AI)"}
    assert [t["id"] for t in status["hybrid"]["timeframes"]] == list(hybrid.TIMEFRAMES) and "beside your chart" in status["hybrid"]["note"]
    item = _add(who).json()
    done = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"])
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "done" and body["result"] is None and body["label"] == "Advanced analysis (code + AI)"
    res = body["hybrid"]
    assert res["mode"] == "hybrid" and res["method"] == "code_ai" and res["coordinate_space"] == "time_price" and res["approximate_prices"] is False
    reading = res["reading"]
    assert reading["symbol"] == SYMBOL and reading["timeframe"] == "15m" and 0 < len(reading["bars"]) <= hybrid.DRAWN_BARS
    assert [r["tf"] for r in reading["table"]][-1] == "15m" and {"1w", "1d", "1h"} <= {r["tf"] for r in reading["table"]}
    assert {a["where"] for a in reading["areas"]} == {"above", "below"}
    above = next(a for a in reading["areas"] if a["where"] == "above")
    assert above["distance"] == round(above["low"] - round(close, 2), 2)
    assert [c["name"] for c in reading["checklist"]][:3] == ["Trend on the 15-minute chart", "Protected level", "Last break of structure"]
    assert all(isinstance(c["present"], bool) and "score" not in c for c in reading["checklist"])
    assert reading["last_break"] and reading["trigger_candle"]["t"] == reading["last_break"]["time"]      # the fixture's last break is drawn
    # the AI part: the engine got ATP's rendered chart and generic facts - never the user's picture
    assert len(calls) == 1 and calls[0]["png"] != PNG and is_png(calls[0]["png"]) and calls[0]["user"].startswith("hybrid 960x540")
    assert set(fake.facts_seen) == {"symbol", "timeframe", "as_of", "last_close", "timeframes", "areas", "levels", "protected", "last_break",
                                    "trigger_candle", "bars", "chart", "coordinate_space"}
    assert res["ai"]["story"] and res["ai"]["setup"]["status"] == "forming" and "annotations" not in res["ai"]
    assert res["ai_version"] == {"version": "0.0-test-hybrid", "method_version": "test-hybrid-hash"}
    assert body["engine_version"].startswith("fake-engine") and body["cost_usd"] == pytest.approx(0.02)
    # signal language: ATP's own words carry no call wording
    for text in _texts({k: v for k, v in res.items() if k not in ("ai", "disclaimer")}):     # the fixed disclaimer says "not ... buy or sell"
        assert language.is_clean(text), text
    history = client.get(f"{BASE}/items", headers=who["headers"]).json()["items"]
    assert history[0]["mode"] == "hybrid" and history[0]["summary"]["symbol"] == SYMBOL and history[0]["hybrid"] is None


def test_hybrid_without_the_private_entry_point_is_code_only_without_ai_wording(monkeypatch):
    who = _hybrid_owner("code")
    _zones(monkeypatch)
    calls = _fake_ask(monkeypatch)
    provider.set_for_tests(ImageOnlyFakeProvider())
    assert not provider.supports_hybrid(provider.load())
    status = client.get(f"{BASE}/status", headers=who["headers"]).json()
    assert status["mode_labels"]["hybrid"] == "Advanced analysis (code)" and status["hybrid"]["ai_part"] is False
    item = _add(who).json()
    body = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"]).json()
    assert calls == [] and body["status"] == "done" and body["label"] == "Advanced analysis (code)"
    res = body["hybrid"]
    assert res["method"] == "code" and res["ai"] is None and res["notes"] == [] and body["cost_usd"] == 0
    assert not any(" AI" in t or t.startswith("AI") for t in _texts({k: v for k, v in res.items() if k not in ("disclaimer",)}))
    assert res["reading"]["checklist"] and body["engine_version"] == "ATP structure reading (code)"


def test_the_ai_part_failing_leaves_the_code_reading(monkeypatch):
    who = _hybrid_owner("ai-fails")
    _fake_ask(monkeypatch, raises=EngineUnavailable("No AI is set up for chart analysis."))
    item = _add(who).json()
    body = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"]).json()
    assert body["status"] == "done" and body["hybrid"]["method"] == "code" and body["hybrid"]["ai"] is None
    assert body["hybrid"]["notes"] == ["The AI part did not run: No AI is set up for chart analysis."]


def test_no_stored_candles_is_a_plain_failure():
    who = _hybrid_owner("empty")
    item = _add(who, symbol=EMPTY_SYMBOL, timeframe="1h").json()
    body = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"]).json()
    assert body["status"] == "failed" and body["error"].startswith(f"ATP has no stored 1-hour candles for {EMPTY_SYMBOL} yet")
    assert body["pick"] == {"symbol": EMPTY_SYMBOL, "timeframe": "1h"}          # kept for another try


def test_the_structure_layer_off_is_a_plain_failure():
    who = _owner("hyb-nolayer")
    _allow(HYBRID_FLAG, who["tenant_id"])
    item = _add(who).json()
    body = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"]).json()
    assert body["status"] == "failed" and body["error"] == hybrid.STRUCTURE_OFF and "_" not in body["error"]


# --- the download, tenancy, share ------------------------------------------------------------------------------------------

def test_the_annotated_png_downloads_and_is_the_callers_own(monkeypatch):
    who = _hybrid_owner("png")
    _zones(monkeypatch)
    _fake_ask(monkeypatch)
    item = _add(who).json()
    assert client.get(f"{BASE}/items/{item['id']}/reading.png", headers=who["headers"]).status_code == 409     # not analysed yet
    body = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"]).json()
    assert body["reading_png"] == f"/api/chart-analyze/items/{item['id']}/reading.png"
    got = client.get(f"{BASE}/items/{item['id']}/reading.png", headers=who["headers"])
    assert got.status_code == 200 and got.headers["content-type"] == "image/png" and is_png(got.content)
    assert got.headers["cache-control"] == "private, no-store" and f"atp-reading-{SYMBOL}-15m-{item['id']}.png" in got.headers["content-disposition"]
    assert got.content == reading_png.render(body["hybrid"]["reading"])                      # deterministic: the page's picture
    assert client.get(f"{BASE}/items/{item['id']}/reading.png", headers=who["headers"]).content == got.content
    other = _hybrid_owner("png-other")
    for method, path in (("GET", f"/items/{item['id']}"), ("GET", f"/items/{item['id']}/reading.png"), ("GET", f"/items/{item['id']}/image"),
                         ("POST", f"/items/{item['id']}/analyze")):
        assert client.request(method, BASE + path, headers=other["headers"]).status_code == 404, path
    assert client.get(f"{BASE}/items/{item['id']}/reading.png").status_code == 401
    image_only = client.post(f"{BASE}/items", headers=who["headers"], files={"file": ("c.png", PNG, "image/png")}, data={"mode": "image_only"}).json()
    assert client.get(f"{BASE}/items/{image_only['id']}/reading.png", headers=who["headers"]).status_code == 409


def test_a_hybrid_analysis_is_not_shared(monkeypatch):
    who = _hybrid_owner("share")
    _allow(SHARE_FLAG, who["tenant_id"])
    _fake_ask(monkeypatch)
    item = _add(who).json()
    client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"])
    got = client.post(f"{BASE}/items/{item['id']}/share", headers=who["headers"])
    assert got.status_code == 409 and got.json()["detail"]["code"] == "not_shareable"
    assert FLAG  # the feature's own flag stays the gate of every route above


# --- the pure parts ------------------------------------------------------------------------------------------------------

def _layer(tf: str = "1h") -> dict:
    t0 = 1_700_000_000
    return {"instrument": "NSE:DEMO", "bars": 80, "last_close": 105.0, "as_of": "2026-01-01T00:00:00+00:00",
            "structure_state": {"direction": "up (weak)", "protected": 100.0,
                                "last_event": {"type": "BOS", "name": "BOS", "direction": "up", "price": 104.0, "time": t0 + 3600 * 5}},
            "levels": [{"kind": "resistance", "price": 110.0, "status": "live"}, {"kind": "support", "price": 99.0, "status": "live"}],
            "mtf": [{"tf": "1w", "direction": "down", "protected": 120.0, "last_event": None, "bars": 12},
                    {"tf": "1d", "direction": "range", "protected": None, "last_event": {"type": "CHOCH", "price": 101.0, "time": t0}, "bars": 200},
                    {"tf": "1h", "direction": "up (weak)", "protected": 100.0, "last_event": None, "bars": 80}]}


def _frame(n: int = 10) -> pd.DataFrame:
    t0 = pd.Timestamp(1_700_000_000, unit="s", tz="UTC")
    return pd.DataFrame({"timestamp": [t0 + pd.Timedelta(hours=i) for i in range(n)], "open": [100.0 + i for i in range(n)],
                         "high": [101.5 + i for i in range(n)], "low": [99.0 + i for i in range(n)], "close": [101.0 + i for i in range(n)]})


def test_build_reading_table_areas_trigger_and_checklist():
    bands = [{"role": "supply", "low": 108.0, "high": 109.0, "state": "fresh"}, {"role": "demand", "low": 104.0, "high": 106.0, "state": "tested"}]
    r = hybrid.build_reading({"symbol": "DEMO", "timeframe": "1h"}, _layer(), bands, _frame())
    assert [row["tf"] for row in r["table"]] == ["1w", "1d", "1h"]
    weekly, daily, hourly = r["table"]
    assert weekly["ready"] is False and weekly["trend"] is None                      # too few bars: "data not ready", no reading
    assert daily["trend"] == "sideways" and daily["last_break"]["label"] == "Break against the trend"
    assert hourly["trend"] == "up" and hourly["weak"] is True and hourly["protected"] == 100.0 and hourly["last_break"]["label"] == "Swing break"
    assert r["trigger_candle"]["index"] == 5 and r["trigger_candle"]["t"] == 1_700_000_000 + 3600 * 5
    assert [(a["where"], a["distance"]) for a in r["areas"]] == [("inside", 0.0), ("above", 3.0)]
    marks = {c["name"]: c["present"] for c in r["checklist"]}
    assert marks["Price inside an area of interest"] is True and marks["Higher and lower timeframes read"] is False
    assert marks["Trigger candle marked"] is True
    assert all(language.is_clean(t) for t in _texts(r))


def test_reading_png_is_deterministic_and_marks_the_trigger_candle():
    r = hybrid.build_reading({"symbol": "DEMO", "timeframe": "1h"}, _layer(), [{"role": "demand", "low": 104.0, "high": 106.0}], _frame())
    a, b = reading_png.render(r), reading_png.render(r)
    assert a == b and is_png(a)
    no_trigger = reading_png.render({**r, "trigger_candle": None})
    assert no_trigger != a
    with pytest.raises(ValueError):
        reading_png.render({**r, "bars": []})


def test_facts_are_generic_and_carry_no_picture():
    r = hybrid.build_reading({"symbol": "DEMO", "timeframe": "1h"}, _layer(), [], _frame())
    facts = hybrid.facts(r, width=960, height=540)
    assert "image" not in json.dumps(facts) and facts["chart"] == {"width": 960, "height": 540}
    assert asyncio.iscoroutinefunction(hybrid.read)
