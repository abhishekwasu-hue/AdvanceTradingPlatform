"""M75 W-9 (owner order M75 CW-6; DECISIONS 1 Nov M75Q-4): method conditions of a trigger plan.

Proofs (the PR plan's W-9 row): his area + the reader's rejection candle, and BOS / CHoCH on his timeframe; every field
his own (nothing pre-set); ONE source per reading - the engine when it reads the chart, else ATP's readers, never both
(an engine that does not give a part answers "not given", ATP is not asked); no look-ahead (a bar that closed before the
plan was armed never counts; a reading never names a bar after `now`); parity: replaying the closed bars one at a time
(the backtest way) meets the condition on the bar the tracker marks in the full reading, and the refactored single-area
gate read equals the chart reading's; the plan fires once through the same ticket path, its note names the source.
"""
import asyncio
import json
import math
import random
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.charts import price_action_layer as pal
from app.charts import read_chart as RC
from app.charts import workspace_reading as WR
from app.engine_provider.contract import Area, EngineResult, PriceRange, Signal, StructureEvent
from app.execution import plan_conditions as PC
from app.execution import trigger_plans
from app.signals.language import SignalStructure
from tests.test_fno1_chain_ticket import _contract, _expiry, _org_tenant, _price
from tests.test_m75_w8_trigger_plans import SPOT, _body, _create, _orders, _pass, _plan
from tests.test_z5_p1d_risk_by_default import _flag

T0 = datetime(2026, 1, 5, 3, 45, tzinfo=UTC)


@pytest.fixture
def setup(monkeypatch):
    """The W-8 setup: an organisation with `trigger_plans` on, a contract and its price."""
    t = _org_tenant("w9")
    _flag(trigger_plans.FLAG, t["tenant_id"])
    expiry = _expiry()
    strike = float(random.randrange(30000, 40000, 50))
    symbol = _contract(expiry, strike)
    _price(monkeypatch, 100.0)
    return t, expiry, strike, symbol


def _frame(n=400, seed=3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows, p = [], 1000.0
    for i in range(n):
        drift = 6 * math.sin(i / 25.0) + (0.8 if i < 200 else -0.8)
        o = p
        c = p + drift + rng.normal(0, 3)
        h, low = max(o, c) + abs(rng.normal(0, 2)), min(o, c) - abs(rng.normal(0, 2))
        p = c
        ts = T0 + timedelta(minutes=15 * i)
        rows.append({"timestamp": ts, "bar_end": ts + timedelta(minutes=15), "open": o, "high": h, "low": low, "close": c, "volume": 100.0})
    return pd.DataFrame(rows)


def _cond(**kw) -> trigger_plans.Condition:
    base = {"kind": PC.STRUCTURE, "timeframe_minutes": 15, "event": "CHOCH", "direction": "down"}
    base.update(kw)
    return trigger_plans.Condition(**base)


def _area(**kw) -> trigger_plans.Condition:
    base = {"kind": PC.AREA, "timeframe_minutes": 15, "area_side": "buyer", "area_low": 990.0, "area_high": 1010.0, "area_timeframe": "1h"}
    base.update(kw)
    return trigger_plans.Condition(**base)


# --- his own values only ------------------------------------------------------------------------------------------------

def test_every_method_field_is_his_own():
    assert trigger_plans.condition_errors(_cond()) == []
    assert trigger_plans.condition_errors(_area()) == []
    assert any("timeframe" in e for e in trigger_plans.condition_errors(_cond(timeframe_minutes=None)))   # no preset timeframe
    assert any("timeframe" in e for e in trigger_plans.condition_errors(_cond(timeframe_minutes=7)))      # only a read timeframe
    assert any("BOS" in e for e in trigger_plans.condition_errors(_cond(event=None)))
    assert any("up or down" in e for e in trigger_plans.condition_errors(_cond(direction=None)))
    assert any("buyer or seller" in e for e in trigger_plans.condition_errors(_area(area_side=None)))
    assert any("Pick the area" in e for e in trigger_plans.condition_errors(_area(area_low=1020.0)))
    assert trigger_plans.condition_errors(trigger_plans.Condition(kind="CLOCK")) == ["Choose what the plan watches."]
    # a W-8 row (no kind) still reads as the price condition
    old = trigger_plans.Condition(**{"level": 24700.0, "side": "ABOVE", "mode": "TOUCH", "timeframe_minutes": None,
                                     "window_start": None, "window_end": None})
    assert old.kind == PC.PRICE and trigger_plans.condition_errors(old) == []


# --- ATP's structure tracker: armed-after, parity, no look-ahead ---------------------------------------------------------

def test_structure_hit_is_the_trackers_event_after_arming_and_replay_meets_it_on_the_same_bar():
    frame = _frame()
    layer = pal.compute_layer(frame, "15m")
    marked = [e for e in layer["structure"] if e["type"] == "CHOCH" and e["direction"] == "down"]
    assert marked, "the synthetic chart must carry a CHoCH down"
    event = marked[0]
    at = int(event["time"])
    idx = int(np.nonzero(pd.to_datetime(frame["timestamp"], utc=True).map(lambda t: int(t.timestamp())).to_numpy() == at)[0][0])
    c = _cond()
    armed = T0 + timedelta(minutes=15 * 10)
    hit = PC.atp_structure_hit(layer, frame, c, armed)
    assert hit is not None and hit.source == PC.SOURCE_ATP and hit.at == frame["bar_end"].iloc[idx].to_pydatetime()
    assert hit.price == pytest.approx(float(frame["close"].iloc[idx])) and "ATP's chart readers" in hit.words
    # armed after that bar closed: the same event never counts (no look-ahead into the past either)
    later = PC.atp_structure_hit(layer, frame, c, frame["bar_end"].iloc[idx].to_pydatetime())
    assert later is None or later.at > frame["bar_end"].iloc[idx].to_pydatetime()
    # the backtest way: one closed bar at a time; the first bar it is met on is the bar the full reading marks
    first = None
    for k in range(idx - 3, idx + 2):
        part = frame.iloc[: k + 1]
        h = PC.atp_structure_hit(pal.compute_layer(part, "15m"), part, c, armed)
        assert h is None or h.at <= part["bar_end"].iloc[-1]                     # never a bar after "now"
        if h is not None and first is None:
            first = (k, h.at)
    assert first is not None and first[0] == idx and first[1] == hit.at


def test_structure_kind_and_direction_are_his():
    frame = _frame()
    layer = pal.compute_layer(frame, "15m")
    armed = T0
    for event, direction in (("BOS", "up"), ("BOS", "down"), ("CHOCH", "up"), ("CHOCH", "down")):
        hit = PC.atp_structure_hit(layer, frame, _cond(event=event, direction=direction), armed)
        has = any(e["type"] == event and e["direction"] == direction for e in layer["structure"])
        assert (hit is not None) == has


# --- ATP's level gate at his area -----------------------------------------------------------------------------------------

def _gate(monkeypatch, confirm_bar, side):
    calls = []

    def fake(bars, area, ctx, others, start):
        calls.append((area, tuple(others), start))
        return {"confirm_bar": confirm_bar, "side": side, "form": "engulf"}
    monkeypatch.setattr(RC.G, "safe_evaluate", fake)
    monkeypatch.setattr(RC.G, "form_name", lambda form, side: "engulfing")
    return calls


def test_single_area_read_equals_the_chart_readings_and_the_others_are_its_neighbours(monkeypatch):
    frame = _frame(60)
    calls = _gate(monkeypatch, 50, RC.G.BULL)
    areas = [{"key": "a", "low": 990.0, "high": 1010.0, "low_text": "990", "high_text": "1,010"},
             {"key": "b", "low": 1100.0, "high": 1120.0, "low_text": "1,100", "high_text": "1,120"}]
    whole = RC.commitment_part(frame, areas, {"trend": "UP"}, T0)
    one = RC.area_commitment(frame, areas, 0, {"trend": "UP"}, T0)
    assert one is not None and whole is not None
    assert {k: one[k] for k in whole} == whole                                    # the same reading of the same area
    assert calls[-1][1] == ((1100.0, 1120.0),)                                    # his area's neighbours, as the reading
    assert RC.area_commitment(frame, areas, 0, None, T0) is None                # no Daily context: nothing


def test_area_hit_needs_his_side_his_area_and_a_candle_after_arming(monkeypatch):
    frame = _frame(60)
    bar = 50
    row = frame.iloc[bar]
    c = _area(area_low=float(row["low"]) - 1, area_high=float(row["high"]) + 1)
    areas, index = PC.plan_areas([], c)
    assert index == 0 and areas[0]["role"] == "demand"
    _gate(monkeypatch, bar, RC.G.BULL)
    hit = PC.atp_area_hit(frame, areas, index, {"trend": "UP"}, c, T0)
    assert hit is not None and hit.at == row["bar_end"].to_pydatetime() and hit.price == pytest.approx(float(row["close"]))
    assert "commitment candle (engulfing)" in hit.words and "buyer area" in hit.words
    assert PC.atp_area_hit(frame, areas, index, {"trend": "UP"}, c, row["bar_end"].to_pydatetime()) is None   # armed after it
    _gate(monkeypatch, bar, RC.G.BEAR)
    assert PC.atp_area_hit(frame, areas, index, {"trend": "UP"}, c, T0) is None                              # the other side
    _gate(monkeypatch, bar, RC.G.BULL)
    away = _area(area_low=float(row["high"]) + 50, area_high=float(row["high"]) + 60)
    areas2, i2 = PC.plan_areas([], away)
    assert PC.atp_area_hit(frame, areas2, i2, {"trend": "UP"}, away, T0) is None                              # not inside his area


def test_his_stored_area_is_found_among_the_stored_ones():
    bands = [{"zone_key": "z1", "role": "supply", "low": 1100.0, "high": 1120.0, "low_text": "1,100", "high_text": "1,120", "timeframe": "1h"},
             {"zone_key": "z2", "role": "demand", "low": 990.0, "high": 1010.0, "low_text": "990", "high_text": "1,010", "timeframe": "1h"}]
    areas, index = PC.plan_areas(bands, _area())
    assert len(areas) == 2 and index == 1 and areas[1]["key"] == "z2"


# --- the engine's reading -------------------------------------------------------------------------------------------------

def _engine(events=(), signals=(), areas=()):
    return EngineResult(interface=1, version="t", areas=tuple(areas), signals=tuple(signals), events=tuple(events))


def test_engine_structure_and_commitment_after_arming_on_his_timeframe():
    at = T0 + timedelta(hours=5)
    ev = StructureEvent(kind="BOS", direction="up", at=at, level=1000.0, timeframe="15m")
    other_tf = StructureEvent(kind="BOS", direction="up", at=at, level=1000.0, timeframe="1h")
    c = _cond(event="BOS", direction="up")
    hit = PC.engine_structure_hit(_engine(events=[other_tf, ev]), c, T0, 1001.0)
    assert hit is not None and hit.source == PC.SOURCE_ENGINE and hit.at == at and "signal engine" in hit.words
    assert PC.engine_structure_hit(_engine(events=[ev]), c, at, 1001.0) is None                     # not after arming
    assert PC.engine_structure_hit(_engine(events=[other_tf]), c, T0, 1001.0) is None               # another timeframe
    area = Area(area_id="x", side="buyer", state="s", band=PriceRange(995.0, 1005.0), timeframe="1h", formed_at=T0)
    sig = Signal(model_id="m", timeframe="15m", bar_end=at, side="buyer", entry_zone=PriceRange(995.0, 1005.0),
                 card=SignalStructure(), area_id="x")
    found = PC.engine_area_hit(_engine(signals=[sig], areas=[area]), _area(), T0, 1002.0)
    assert found is not None and found.at == at and found.price == 1002.0
    assert PC.engine_area_hit(_engine(signals=[sig], areas=[area]), _area(area_side="seller"), T0, 1002.0) is None
    assert PC.engine_area_hit(_engine(signals=[sig], areas=[area]), _area(area_low=2000.0, area_high=2010.0), T0, 1002.0) is None
    assert PC.engine_area_hit(_engine(signals=[sig], areas=[area]), _area(), at, 1002.0) is None


def test_one_source_only_an_engine_without_events_says_not_given_and_atp_is_never_asked(monkeypatch):
    async def engine_result(session, user, key, now):
        return _engine(), "15m", 1000.0, (T0 + timedelta(hours=1)).isoformat()

    async def never(*a, **k):
        raise AssertionError("ATP's bars were read beside the engine")
    monkeypatch.setattr(WR, "engine_result", engine_result)
    monkeypatch.setattr(pal, "load_frame", never)
    reading = asyncio.run(PC.read(None, object(), "NIFTY 50", "NSE", _cond(), T0, T0 + timedelta(hours=2)))
    assert reading.source == PC.SOURCE_ENGINE and reading.hit is None and reading.note == PC.NOT_GIVEN


def test_atp_reads_closed_bars_only(monkeypatch):
    frame = _frame(80)
    now = frame["bar_end"].iloc[49].to_pydatetime() + timedelta(minutes=5)      # bar 50 is still forming

    async def no_engine(*a, **k):
        return None

    async def bars(session, key, tf, start, end, private=None):
        return frame, "lake"

    async def scope(session, user, key):
        return None
    from app.market_lake import own_bars
    monkeypatch.setattr(WR, "engine_result", no_engine)
    monkeypatch.setattr(pal, "load_frame", bars)
    monkeypatch.setattr(own_bars, "scope_for", scope)
    reading = asyncio.run(PC.read(None, object(), "NIFTY 50", "NSE", _cond(), T0, now))
    assert reading.source == PC.SOURCE_ATP and reading.last_end == frame["bar_end"].iloc[49].to_pydatetime()
    assert reading.hit is None or reading.hit.at <= reading.last_end


def test_a_new_reading_waits_for_a_new_candle_of_his_timeframe():
    PC.clear()
    now = T0 + timedelta(hours=3)
    assert PC.due(1, "{}", T0, now)
    PC.seen(1, "{}", T0, PC.Reading(PC.SOURCE_ATP, last_end=now - timedelta(minutes=2)), 15, now)
    assert not PC.due(1, "{}", T0, now + timedelta(minutes=12))
    assert PC.due(1, "{}", T0, now + timedelta(minutes=13))
    assert PC.due(1, "{}", T0 + timedelta(minutes=1), now)                       # armed afresh: read at once


# --- fires once through the same path; the source is named ----------------------------------------------------------------

def _method_body(expiry, strike, condition):
    return _body(expiry, strike, condition=condition)


def test_route_takes_his_method_condition_and_refuses_a_preset(setup):
    t, expiry, strike, _ = setup
    bad = _create(t, _method_body(expiry, strike, {"kind": "STRUCTURE", "event": "BOS", "direction": "up"}), status=422)
    assert "timeframe" in bad["detail"]
    plan = _create(t, _method_body(expiry, strike, {"kind": "AREA", "timeframe_minutes": 15, "area_side": "buyer",
                                                    "area_low": 24500.0, "area_high": 24550.0, "area_timeframe": "1h"}))
    assert plan["condition"]["kind"] == "AREA" and plan["condition"]["level"] == 0.0
    assert "inside the buyer area 24500 - 24550" in plan["condition_words"]


def test_method_plan_fires_once_and_names_the_source(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    PC.clear()
    plan = _create(t, _method_body(expiry, strike, {"kind": "STRUCTURE", "timeframe_minutes": 15, "event": "BOS", "direction": "up"}))
    state = {"hit": None, "reads": 0}

    async def read(session, user, symbol_, exchange, c, armed_at, now):
        state["reads"] += 1
        assert (symbol_, exchange) == SPOT and c.event == "BOS"
        return PC.Reading(PC.SOURCE_ATP, hit=state["hit"], last_end=None, last_close=24600.0)
    monkeypatch.setattr(PC, "read", read)
    assert _pass(t["tenant_id"], {SPOT[0]: 24600.0, symbol: 100.0})["triggered"] == 0
    assert _plan(plan["id"]).status == "ARMED"
    PC.clear()
    state["hit"] = PC.Hit(PC.SOURCE_ATP, datetime.now(UTC), 24610.0, "Met by ATP's chart readers: break of structure up on the 15-minute chart.")
    report = _pass(t["tenant_id"], {SPOT[0]: 24610.0, symbol: 100.0})
    assert report["triggered"] == 1 and report["done"] == 1
    row = _plan(plan["id"])
    assert row.status == "DONE" and row.trigger_price == 24610.0
    assert row.note.startswith("Met by ATP's chart readers") and "1 of 1 order(s) placed" in row.note
    assert json.loads(row.results_json)["0"]["status"] == "PLACED"
    PC.clear()
    assert _pass(t["tenant_id"], {SPOT[0]: 24800.0, symbol: 100.0})["triggered"] == 0
    assert len(_orders(t["user_id"], plan["id"])) == 1                                         # once


def test_no_reading_keeps_the_plan_armed(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    PC.clear()
    plan = _create(t, _method_body(expiry, strike, {"kind": "STRUCTURE", "timeframe_minutes": 15, "event": "CHOCH", "direction": "down"}))

    async def read(*a, **k):
        raise RuntimeError("no data")
    monkeypatch.setattr(PC, "read", read)
    assert _pass(t["tenant_id"], {SPOT[0]: 24600.0, symbol: 100.0})["triggered"] == 0
    assert _plan(plan["id"]).status == "ARMED" and _orders(t["user_id"], plan["id"]) == []
