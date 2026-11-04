"""ADV-2 (owner 4 Nov, docs/design/ADV2_SPEC.md A, B, D): the hybrid reading from one candle source per timeframe, areas
computed on the spot, and the depth fields - end to end on the test app with a FAKE broker read (no network).

A: stored candles when they hold at least MIN_BARS, else the user's own broker candles (read only - nothing is written to
   the lake); no broker session of his own and too few stored candles = one positive sentence.
B: no bands from the zones job (zones off here) -> bands computed for this read with the zones engine's own functions.
D: swings, state, protected distance, higher degree, scenarios, watch - structure words only (the signal-language check).
Made-up data only: seeded random walks and the public 15-minute fixture session.
"""
import json
import uuid
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select, update

from app.chart_analyze import HYBRID_FLAG, engine, hybrid, hybrid_depth, hybrid_sources, provider, reading_png
from app.charts import price_action_layer as pal
from app.core import rate_limit
from app.db.models import ChartAnalysisRecord, MdCandleRecord
from app.market_data import candles_routes
from app.market_data.calendar import IST
from app.platform import controls
from app.signals import language
from tests.ch3_pa_cases import frame as fixture_frame
from tests.chart_analyze_fakes import ImageOnlyFakeProvider, synthetic_chart
from tests.test_auth_api import _session_factory, client
from tests.test_chart_analyze_api import BASE, _owner, _run, _set_settings

PNG = synthetic_chart()
SHORT = f"ADS{uuid.uuid4().hex[:6].upper()}"          # 10 stored 15-minute candles only
FULL = f"ADF{uuid.uuid4().hex[:6].upper()}"           # the full fixture session stored
NONE = f"ADN{uuid.uuid4().hex[:6].upper()}"           # nothing stored
ADVICE = ("buy", "sell", "target", "stop", "entry", "exit", "profit", "loss", "should", "recommend")
NEGATIVE = ("not read yet", "not formed yet", "none ", "data not ready", "not known", "no break")


def _recent(f: pd.DataFrame) -> pd.DataFrame:
    weeks = int((pd.Timestamp(datetime.now(UTC)) - f["bar_end"].iloc[-1]) / pd.Timedelta(days=7))
    shift = pd.Timedelta(days=7 * weeks)
    return f.assign(timestamp=f["timestamp"] + shift, bar_end=f["bar_end"] + shift)


F = _recent(fixture_frame())


def _rows(key: str, f: pd.DataFrame) -> list[MdCandleRecord]:
    return [MdCandleRecord(instrument_key=key, timeframe="15m", ts=b.bar_end.to_pydatetime(), source="vendor:test", version=1, open=float(b.open),
                           high=float(b.high), low=float(b.low), close=float(b.close), volume=0, ingested_at=b.bar_end.to_pydatetime())
            for b in f.itertuples()]


async def _drop() -> None:
    async with _session_factory() as session:
        await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key.in_([f"NSE:{SHORT}", f"NSE:{FULL}", f"NSE:{NONE}"])))
        await session.commit()


async def _put() -> None:
    async with _session_factory() as session:
        session.add_all(_rows(f"NSE:{SHORT}", F.tail(10)) + _rows(f"NSE:{FULL}", F))
        await session.commit()


async def _count(key: str) -> int:
    async with _session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(MdCandleRecord).where(MdCandleRecord.instrument_key == key)) or 0)


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
    hybrid_sources.bands_cache_clear()
    provider.set_for_tests(ImageOnlyFakeProvider())                 # code only: no AI part
    yield
    provider.reset_for_tests()
    pal.cache_clear()
    hybrid_sources.bands_cache_clear()
    rate_limit.reset(engine.FEATURE)


def _allow(name: str, tenant_id: int) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get(name) or {"on": False, "tenants": []}
            flags[name] = {"on": bool(entry.get("on")), "tenants": sorted(set(entry.get("tenants") or []) | {tenant_id})}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _who(tag: str) -> dict:
    who = _owner(f"adv2-{tag}")
    _allow(pal.FLAG, who["tenant_id"])
    _allow(HYBRID_FLAG, who["tenant_id"])
    return who


def _analyze(who: dict, symbol: str, timeframe: str) -> dict:
    item = client.post(f"{BASE}/items", headers=who["headers"], files={"file": ("chart.png", PNG, "image/png")},
                       data={"mode": "hybrid", "symbol": symbol, "timeframe": timeframe}).json()
    got = client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"])
    assert got.status_code == 200, got.text
    return got.json()


# --- the fake broker read (the chart's own candle route, replaced) --------------------------------------------------------

def _walk(n: int, seed: int, start: float, step: float) -> np.ndarray:
    return start + np.cumsum(np.random.default_rng(seed).normal(0, step, n))


def _daily_bars(n: int = 520) -> list[dict]:
    days = pd.bdate_range(end=pd.Timestamp(datetime.now(IST).date()) - pd.Timedelta(days=1), periods=n)
    close = _walk(n, 7, 1000.0, 12.0)
    rng = np.random.default_rng(8)
    out = []
    for d, c in zip(days, close):
        o = c + rng.normal(0, 5)
        out.append({"timestamp": f"{d.date()}T00:00:00+05:30", "open": o, "high": max(o, c) + abs(rng.normal(0, 6)),
                    "low": min(o, c) - abs(rng.normal(0, 6)), "close": c, "volume": 1000.0})
    return out


def _minute_bars(days: int = 25) -> list[dict]:
    now = pd.Timestamp(datetime.now(IST))
    sessions = [d for d in pd.bdate_range(end=now.normalize().tz_localize(None), periods=days)]
    starts = [pd.Timestamp(d).tz_localize(IST) + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(minutes=m) for d in sessions for m in range(375)]
    starts = [s for s in starts if s + pd.Timedelta(minutes=1) <= now]
    close = _walk(len(starts), 11, 1000.0, 0.8)
    rng = np.random.default_rng(12)
    out = []
    for s, c in zip(starts, close):
        o = c + rng.normal(0, 0.3)
        out.append({"timestamp": s.isoformat(), "open": o, "high": max(o, c) + abs(rng.normal(0, 0.3)), "low": min(o, c) - abs(rng.normal(0, 0.3)),
                    "close": c, "volume": 10.0})
    return out


DAILY, MINUTES = _daily_bars(), _minute_bars()


def _fake_broker(monkeypatch, *, no_session: bool = False) -> list[dict]:
    calls: list[dict] = []

    async def fake_candles(body, user, session):
        calls.append({"user_id": user.id, "tenant_id": user.tenant_id, "symbols": list(body.symbols), "exchange": body.exchange,
                      "timeframe": body.timeframe, "lookback_days": body.lookback_days, "forming": body.forming})
        if no_session:
            raise HTTPException(status_code=409, detail="Live prices need your own broker session.")
        bars = DAILY if body.timeframe == "day" else MINUTES
        return {"source": {"broker": "upstox", "account_label": "primary"}, "symbols": {body.symbols[0]: {"bars": bars, "error": None}}}
    monkeypatch.setattr(candles_routes, "candles", fake_candles)
    return calls


def _texts(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from _texts(v)
    elif isinstance(value, list):
        for v in value:
            yield from _texts(v)
    elif isinstance(value, str):
        yield value


# --- A: one candle source --------------------------------------------------------------------------------------------------

def test_too_few_stored_candles_read_the_users_own_broker_candles_and_never_write_them(monkeypatch):
    who = _who("short")
    calls = _fake_broker(monkeypatch)
    before = _run(_count(f"NSE:{SHORT}"))
    body = _analyze(who, SHORT, "15m")
    assert body["status"] == "done", body.get("error")
    reading = body["hybrid"]["reading"]
    assert reading["source"] == "broker" and reading["bars_read"] >= hybrid.MIN_BARS
    rows = {r["tf"]: r for r in reading["table"]}
    assert all(rows[t]["ready"] for t in ("1w", "1d", "1h", "15m")) and rows["15m"]["trend"] is not None
    # only the requesting user's own read, once per series, closed bars only (no forming bar asked for)
    assert calls and {c["user_id"] for c in calls} == {who["user_id"]} and {c["tenant_id"] for c in calls} == {who["tenant_id"]}
    assert sorted(c["timeframe"] for c in calls) == ["1min", "day"] and not any(c["forming"] for c in calls)
    assert {c["symbols"][0] for c in calls} == {SHORT} and {c["exchange"] for c in calls} == {"NSE"}
    assert _run(_count(f"NSE:{SHORT}")) == before                    # the shared lake is never written from broker candles
    last_end = max(pd.Timestamp(b["timestamp"]) for b in MINUTES) + pd.Timedelta(minutes=1)
    assert pd.Timestamp(reading["as_of"]) <= last_end                # no bar after the last closed one


def test_enough_stored_candles_stay_stored_and_the_short_rows_come_from_the_broker(monkeypatch):
    who = _who("mixed")
    _fake_broker(monkeypatch)
    reading = _analyze(who, FULL, "15m")["hybrid"]["reading"]
    assert reading["source"] == "stored+broker" and reading["bars_read"] >= hybrid.MIN_BARS
    assert {r["tf"]: r["ready"] for r in reading["table"]} == {"1w": True, "1d": True, "1h": True, "15m": True}


def test_no_broker_session_and_too_few_candles_is_one_positive_sentence(monkeypatch):
    who = _who("nosession")
    _fake_broker(monkeypatch, no_session=True)
    body = _analyze(who, SHORT, "1d")
    assert body["status"] == "failed"
    assert body["error"] == hybrid.no_session_message(SHORT, "1d", ["15m"]) == (
        f"Advanced analysis for {SHORT} works on 15-minute candles now, and on Daily candles too once you log in to your broker on "
        "the Broker page - or read this chart with AI only.")
    nothing = _analyze(who, NONE, "15m")
    assert nothing["status"] == "failed" and nothing["error"] == hybrid.no_session_message(NONE, "15m", [])
    for text in (body["error"], nothing["error"]):
        assert text.count(". ") == 0 and text.endswith(".") and language.is_clean(text)
        assert not any(w in text.lower() for w in ("not ", "cannot", "no candles", "data not ready"))


def test_with_enough_stored_candles_the_broker_is_not_needed_for_the_pick(monkeypatch):
    who = _who("stored")
    _fake_broker(monkeypatch, no_session=True)
    reading = _analyze(who, FULL, "15m")["hybrid"]["reading"]
    assert reading["source"] == "stored" and reading["bars_read"] >= hybrid.MIN_BARS


# --- B: areas on the spot --------------------------------------------------------------------------------------------------

def test_areas_are_computed_on_the_spot_when_zones_give_none(monkeypatch):
    who = _who("areas")                                              # market_zones stays off for this organisation
    _fake_broker(monkeypatch)
    reading = _analyze(who, SHORT, "1d")["hybrid"]["reading"]
    assert reading["areas"] and reading["zones_note"] is None
    close = reading["last_close"]
    for a in reading["areas"]:
        assert a["where"] in ("above", "below", "inside") and a["low"] <= a["high"]
        assert a["distance_pct"] == round(abs(a["distance"]) / close * 100.0, 2)
    assert any(c["name"] == "Area of interest nearby" and c["present"] for c in reading["checklist"])


def test_the_bands_are_the_zones_engines_own_on_the_same_frame():
    from app.price_action.zones import chart, job, snapshot, store
    when = datetime.now(UTC).replace(second=0, microsecond=0)
    f = pal._to_frame(hybrid_sources.broker_rows(DAILY, "day", "NSE:DEMO"), "1d", "NSE:DEMO", when, False)
    price = float(f["close"].iloc[-1])
    s = store.platform_settings()
    frames = job.frames_from({"1d": hybrid_sources._zone_frame(f)}, s, when)
    expected = chart.bands_from_rows(snapshot.build_snapshot(frames.frames, frames.dyn, s, None), price)
    assert hybrid_sources.compute_bands({"1d": f}, when, price) == expected and expected


# --- the candle conversion -------------------------------------------------------------------------------------------------

def test_broker_rows_are_end_labelled_closed_and_cut_at_the_as_of_minute():
    start = pd.Timestamp("2026-10-20T09:15:00+05:30")
    bars = [{"timestamp": (start + pd.Timedelta(minutes=m)).isoformat(), "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1}
            for m in range(45)] + [{"timestamp": (start + pd.Timedelta(minutes=45)).isoformat(), "open": 1, "high": 1, "low": 1, "close": 1, "forming": True}]
    rows = hybrid_sources.broker_rows(bars, "1min", "NSE:DEMO")
    assert len(rows) == 45 and rows[0][0] == (start + pd.Timedelta(minutes=1)).tz_convert("UTC").to_pydatetime()
    when = (start + pd.Timedelta(minutes=40)).tz_convert("UTC").to_pydatetime()      # mid-way through the third 15-minute bar
    f = pal._to_frame(rows, "15m", "NSE:DEMO", when, True)
    assert len(f) == 2 and (pd.to_datetime(f["bar_end"], utc=True) <= pd.Timestamp(when)).all()
    day = hybrid_sources.broker_rows([{"timestamp": "2026-10-20T00:00:00+05:30", "open": 1, "high": 2, "low": 0.5, "close": 1.5}], "day", "NSE:DEMO")
    assert day[0][0] == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)                    # the session's regular close, 15:30 IST


def test_broker_symbol_maps_an_index_to_its_spot_name():
    assert hybrid_sources.broker_symbol("NIFTY") == ("NIFTY 50", "NSE")
    assert hybrid_sources.broker_symbol("BANKNIFTY") == ("NIFTY BANK", "NSE")
    assert hybrid_sources.broker_symbol("SENSEX") == ("SENSEX", "BSE")
    assert hybrid_sources.broker_symbol("reliance") == ("RELIANCE", "NSE")
    assert hybrid_sources.broker_symbol("BSE:TCS") == ("TCS", "BSE")


# --- D: the depth fields ---------------------------------------------------------------------------------------------------

def _daily_layer() -> tuple[dict, pd.DataFrame]:
    when = datetime.now(UTC).replace(second=0, microsecond=0)
    f = pal._to_frame(hybrid_sources.broker_rows(DAILY, "day", "NSE:DEMO"), "1d", "NSE:DEMO", when, False)
    return {**pal.build(f, "1d", pal.DEFAULT_DEGREE, False, None), "instrument": "NSE:DEMO"}, f


def test_the_new_fields_come_from_the_layer():
    layer, f = _daily_layer()
    price = float(f["close"].iloc[-1])
    bands = [{"role": "supply", "low": price * 1.02, "high": price * 1.03, "state": "fresh", "timeframe": "1d"},
             {"role": "demand", "low": price * 0.97, "high": price * 0.98, "state": "tested", "timeframe": "1d"}]
    r = hybrid.build_reading({"symbol": "DEMO", "timeframe": "1d"}, layer, bands, f, source="broker")
    assert r["source"] == "broker" and r["bars_read"] == len(f)
    assert 0 < len(r["swings"]) <= hybrid_depth.MAX_SWINGS and all(s["label"] in hybrid_depth.SWING_LABELS for s in r["swings"])
    assert [s["time"] for s in r["swings"]] == sorted(s["time"] for s in r["swings"])
    assert r["state"]["phase"] == layer["structure_state"]["state"] and r["state"]["text"]
    assert r["protected_distance_pct"] == round(abs(r["last_close"] - r["protected"]) / r["last_close"] * 100, 2)
    assert r["higher_degree"] is None or set(r["higher_degree"]) == {"trend", "text"}
    assert set(r["scenarios"]) == {"main", "other"} and all(r["scenarios"].values())
    assert 0 < len(r["watch"]) <= hybrid_depth.MAX_WATCH and all(set(w) == {"when", "meaning"} for w in r["watch"])
    assert all(a["distance_pct"] is not None for a in r["areas"])
    assert all(language.is_clean(t) for t in _texts(r))


PHASES = list(hybrid_depth.PHASES)


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("protected", [95.0, None])
def test_scenario_and_watch_text_is_structure_words_only(phase, protected):
    layer = {"degree": 2, "structure_state": {"state": phase, "protected": protected, "range_high": 110.0, "range_low": 90.0},
             "swings": [{"kind": "H", "label": "HH", "price": 112.0, "time": 1, "status": "confirmed"},
                        {"kind": "L", "label": "HL", "price": 96.0, "time": 2, "status": "confirmed"}],
             "trend": [{"degree": 3, "trend": "up", "basis": "HH 112 + HL 96"}]}
    areas = [{"where": "above", "low": 104.0, "high": 106.0, "distance": 4.0}, {"where": "below", "low": 97.0, "high": 98.0, "distance": 2.0}]
    sc = hybrid_depth.scenarios(layer, protected, areas)
    w = hybrid_depth.watch(layer, "Daily", protected, areas, 100.0)
    hd = hybrid_depth.higher_degree(layer, "Daily")
    assert sc is not None and sc["main"] and sc["other"] and len(w) <= hybrid_depth.MAX_WATCH
    for text in [sc["main"], sc["other"], hd["text"], *[x for item in w for x in item.values()]]:
        assert language.is_clean(text), text
        assert not any(word in text.lower().split() for word in ADVICE), text


def test_checklist_reasons_are_facts_never_placeholders():
    bare = {"instrument": "NSE:DEMO", "bars": 5, "last_close": None, "structure_state": {}, "mtf": [{"tf": "1d", "bars": 3}]}
    f = pd.DataFrame({"timestamp": [pd.Timestamp("2026-01-01", tz="UTC")], "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]})
    r = hybrid.build_reading({"symbol": "DEMO", "timeframe": "1h"}, bare, [], f)
    assert r["checklist"] == []                                     # nothing read: no row at all, no negative placeholder
    assert r["swings"] == [] and r["state"] is None and r["scenarios"] is None and r["watch"] == []
    layer, frame = _daily_layer()
    full = hybrid.build_reading({"symbol": "DEMO", "timeframe": "1d"}, layer, [], frame)
    assert full["checklist"]
    for row in full["checklist"]:
        assert isinstance(row["present"], bool) and row["reason"] and not any(n in row["reason"].lower() for n in NEGATIVE), row


# --- old stored results ----------------------------------------------------------------------------------------------------

NEW = ("bars_read", "swings", "state", "protected_distance_pct", "higher_degree", "scenarios", "watch")


def test_an_old_stored_result_still_renders(monkeypatch):
    who = _who("old")
    _fake_broker(monkeypatch)
    body = _analyze(who, FULL, "15m")
    old = dict(body["hybrid"])
    reading = {k: v for k, v in old["reading"].items() if k not in NEW}
    reading["source"] = "stored candles"
    reading["areas"] = [{k: v for k, v in a.items() if k != "distance_pct"} for a in reading["areas"]]
    old["reading"] = reading

    async def store_old():
        async with _session_factory() as session:
            await session.execute(update(ChartAnalysisRecord).where(ChartAnalysisRecord.id == body["id"]).values(result_json=json.dumps(old)))
            await session.commit()
    _run(store_old())
    got = client.get(f"{BASE}/items/{body['id']}", headers=who["headers"])
    assert got.status_code == 200 and got.json()["hybrid"]["reading"] == reading
    assert hybrid.summary(old)["symbol"] == FULL
    assert hybrid.facts(reading, width=reading_png.WIDTH, height=reading_png.HEIGHT)["timeframe"] == "15m"
    assert reading_png.render(reading)[:8] == b"\x89PNG\r\n\x1a\n"
    listed = client.get(f"{BASE}/items", headers=who["headers"]).json()["items"]
    assert any(i["id"] == body["id"] for i in listed)


def test_the_reading_window_stays_the_layers(monkeypatch):
    """The broker read asks for no more than the broker serves (the route's own caps), never a forming bar."""
    who = _who("window")
    calls = _fake_broker(monkeypatch)
    _analyze(who, SHORT, "1h")
    caps = {"1min": candles_routes.INTRADAY_MAX_LOOKBACK_DAYS, "day": candles_routes.DAILY_MAX_LOOKBACK_DAYS}
    assert calls and all(c["lookback_days"] == caps[c["timeframe"]] for c in calls) and not any(c["forming"] for c in calls)
