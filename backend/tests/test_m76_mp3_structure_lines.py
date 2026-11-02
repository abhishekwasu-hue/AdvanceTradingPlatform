"""M76 MP-3 (owner order M76 W-1 + amendment 1-5; OPEN_QUESTIONS M76Q-7 default): the check card's structure lines.

* The order's direction (a plain fact of the ticket) beside the SAME reading the Copilot's workspace shows (M75 W-3): the
  signal engine's when it reads the chart for him, else ATP's readers - each line labelled with its source, never mixed.
* One reading per timeframe and every line names its timeframe: a line uses only that timeframe's trend, its own areas
  and its own last close (never merged). Timeframes: the workspace's open one (same underlying), else his own signal
  timeframe (only when saved) and the daily.
* Each line type has its own switch; accepted today = hidden today; one line of facts, no advice / judgement word.
* No look-ahead (closed bars only); no engine token in a response; the 300 ms budget (a slow reading is pending, Place
  never waits); Place is unchanged with structure lines shown (accept and continue). Nothing here touches an order.
"""
import asyncio
import inspect
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from app.charts import price_action_layer as pal
from app.charts import read_chart as R
from app.charts import workspace_reading as WR
from app.charts import zones_layer as zl
from app.main import app
from app.me import trade_settings as ts
from app.paper import pretrade_checks as pc
from app.paper import pretrade_routes
from app.paper import pretrade_structure as ps
from app.paper import pretrade_warnings as pw
from app.signals.language import is_clean
from app.workers import engine_runner
from tests.test_aud_t5_read_chart import CUT, STRICT, F, _bands
from tests.test_auth_api import _session_factory, client
from tests.test_fno1_chain_ticket import _body, _contract, _expiry, _org_tenant, _place
from tests.test_m75_w3_annotations import MTF, FakeEngine, _clean, _request
from tests.test_z3_approval_entries import _price
from tests.test_z5_p1d_risk_by_default import _flag

app.dependency_overrides[pretrade_routes.session_factory] = lambda: _session_factory
app.dependency_overrides[pretrade_routes.checks_rate_limit] = lambda: None
URL = "/api/paper/fno/order/checks"
ADVICE = ("risky", "should", "avoid", "recommend", "buy now", "sell now", "target", "stop-loss", "dangerous", "bad", "good")


def _area(side, low, high, tf):
    return {"side": side, "role": WR.ROLE_OF_SIDE[side], "low": low, "high": high, "low_text": pal.price_text(low),
            "high_text": pal.price_text(high), "timeframe": tf}


def _reading(*, source="atp", trend=(("1d", "down"),), events=(), close=24500.0, areas=(), not_given=()):
    return {"source": source, "trend": [{"timeframe": tf, "direction": d} for tf, d in trend],
            "events": [{"type": t, "name": WR.EVENT_NAMES[t], "direction": d, "timeframe": tf, "price_text": "24380.00"} for t, d, tf in events],
            "last_close": close, "areas": list(areas), "not_given": list(not_given)}


def _ids(lines):
    return [x["id"] for x in lines]


# --- the order's direction -------------------------------------------------------------------------------------------

def test_the_orders_direction_is_a_plain_fact_of_the_ticket():
    assert [ps.setup_of(s, r) for s, r in (("BUY", "CE"), ("SELL", "PE"), ("BUY", "FUT"))] == ["bullish"] * 3
    assert [ps.setup_of(s, r) for s, r in (("SELL", "CE"), ("BUY", "PE"), ("SELL", "FUT"))] == ["bearish"] * 3
    assert ps.setup_of("BUY", "XX") is None


# --- every type with a fixture (pure) ---------------------------------------------------------------------------------

def test_trend_conflict_and_counter_trend_one_line_per_fact():
    r = _reading(events=(("BOS", "down", "1d"),))
    lines = ps.lines_for(r, "1d", "bullish", side="BUY", right="CE", strike=24500.0)
    assert [x["text"] for x in lines] == ["Daily trend: down · last structure event: BOS down at 24380.00 · your setup: bullish · ATP chart readers"]
    assert _ids(lines) == ["counter_trend"]
    # his counter-trend switch off: the trend line instead (never both for one fact)
    off = ps.lines_for(r, "1d", "bullish", side="BUY", right="CE", strike=24500.0, counter_on=False)
    assert [x["text"] for x in off] == ["Daily trend: down · your setup: bullish · ATP chart readers"] and _ids(off) == ["trend_conflict"]
    # a CHoCH the order's way on that chart: no counter-trend line, the trend fact stays
    choch = _reading(events=(("BOS", "down", "1d"), ("CHOCH", "up", "1d")))
    assert _ids(ps.lines_for(choch, "1d", "bullish", side="BUY", right="CE", strike=None)) == ["trend_conflict"]
    # no event read on that chart
    none = ps.lines_for(_reading(), "1d", "bullish", side="BUY", right="CE", strike=None)
    assert none[0]["text"].startswith("Daily trend: down · no BOS or CHoCH read on this chart · your setup: bullish")
    # the source does not give events (engine interface 1): no counter-trend claim, the trend line only
    eng = ps.lines_for(_reading(source="engine", not_given=("events",)), "1d", "bullish", side="BUY", right="CE", strike=None)
    assert _ids(eng) == ["trend_conflict"] and eng[0]["text"].endswith("· your signal engine")
    # same direction, a range or no trend: no line
    for d in ("up", "up (weak)", "range", "none", "sideways", "unclear"):
        assert ps.lines_for(_reading(trend=(("1d", d),)), "1d", "bullish", side="BUY", right="CE", strike=None) == [], d
    assert _ids(ps.lines_for(_reading(trend=(("1d", "up (weak)"),)), "1d", "bearish", side="BUY", right="PE", strike=None)) == ["counter_trend"]


def test_area_conflict_room_and_strike_in_area():
    areas = (_area("seller", 24480.0, 24560.0, "1d"), _area("seller", 24700.0, 24760.0, "1d"), _area("buyer", 24300.0, 24400.0, "1d"))
    r = _reading(trend=(("1d", "up"),), areas=areas, close=24500.0)
    bull = ps.lines_for(r, "1d", "bullish", side="SELL", right="PE", strike=24350.0)
    assert [x["text"] for x in bull] == [
        "Last close 24500.00 inside a seller area (daily, 24480.00 - 24560.00) · your setup: bullish · ATP chart readers",
        "Next seller area (daily, 24700.00 - 24760.00): 200.00 points above the last close 24500.00 · ATP chart readers",
        "Short 24350.00 PE inside a buyer area (daily, 24300.00 - 24400.00) · ATP chart readers"]
    assert _ids(bull) == ["area_conflict", "room", "strike_in_area"] and bull[1]["facts"]["points"] == 200.0
    bear = ps.lines_for(_reading(trend=(("1d", "down"),), areas=areas, close=24500.0), "1d", "bearish", side="BUY", right="PE", strike=24500.0)
    assert [x["text"] for x in bear] == [
        "Next buyer area (daily, 24300.00 - 24400.00): 100.00 points below the last close 24500.00 · ATP chart readers"]   # bought: no strike line


def test_timeframes_are_never_merged():
    # a 1-hour area and a weekly trend in a daily reading: the daily line uses only the daily's own parts
    r = _reading(trend=(("1w", "down"), ("1d", "up")), areas=(_area("seller", 24480.0, 24560.0, "1h"),), close=24500.0)
    assert ps.lines_for(r, "1d", "bullish", side="BUY", right="CE", strike=None) == []
    one_hour = ps.lines_for(r | {"trend": [{"timeframe": "60m", "direction": "down"}]}, "1h", "bullish", side="BUY", right="CE", strike=None)
    assert _ids(one_hour) == ["counter_trend", "area_conflict"] and all(x["facts"]["timeframe"] == "1h" for x in one_hour)
    assert all("(1-hour," in x["text"] or x["text"].startswith("1-hour trend") for x in one_hour)


def test_wording_is_one_line_of_facts_no_advice():
    areas = (_area("seller", 24480.0, 24560.0, "1d"), _area("buyer", 24300.0, 24400.0, "1d"), _area("seller", 24700.0, 24760.0, "1d"))
    lines = ps.lines_for(_reading(areas=areas, events=(("BOS", "down", "1d"),)), "1d", "bullish", side="SELL", right="PE", strike=24350.0)
    assert len(lines) == 4
    for x in lines:
        assert "\n" not in x["text"] and is_clean(x["text"]) and any(ch.isdigit() for ch in x["text"]), x["text"]
        assert not [w for w in ADVICE + tuple(STRICT) if w in x["text"].lower()], x["text"]
        assert x["severity"] == "fact"


# --- the real readings ------------------------------------------------------------------------------------------------

def _atp_reading(frame):
    cut = frame[frame["bar_end"] <= CUT]
    return R.read_closed(frame, "15m", CUT, bands=_bands(cut), mtf=MTF)["annotations"]


def test_lines_from_atp_readers_and_no_lookahead():
    cut = F[F["bar_end"] <= CUT]
    before = _atp_reading(cut)
    after = _atp_reading(F)                                  # the whole fixture, read as of CUT
    for setup, side, right in (("bullish", "BUY", "CE"), ("bearish", "BUY", "PE")):
        a = ps.lines_for(before, "15m", setup, side=side, right=right, strike=None)
        assert a == ps.lines_for(after, "15m", setup, side=side, right=right, strike=None)
        assert all(x["facts"]["source"] == "atp" and x["text"].endswith("· ATP chart readers") for x in a)
    # the fixture's 15m trend is "up" (MTF): a bearish order gets the trend fact, a bullish one does not
    assert {"counter_trend", "trend_conflict"} & set(_ids(ps.lines_for(before, "15m", "bearish", side="BUY", right="PE", strike=None)))
    assert not {"counter_trend", "trend_conflict"} & set(_ids(ps.lines_for(before, "15m", "bullish", side="BUY", right="CE", strike=None)))


def test_lines_from_the_engine_carry_no_engine_token():
    result = FakeEngine().read(_request())
    a = WR.engine_annotations(result, tf="15m", signal_tf="15m", last_close=101.5, as_of=CUT.isoformat())
    for setup, side, right in (("bullish", "SELL", "PE"), ("bearish", "SELL", "CE")):
        lines = ps.lines_for(a, "15m", setup, side=side, right=right, strike=101.0)
        _clean(lines)
        assert all(x["text"].endswith("· your signal engine") for x in lines)
    assert "room" in _ids(ps.lines_for(a, "15m", "bearish", side="BUY", right="PE", strike=None))


def test_workspace_annotations_reads_closed_bars_only(monkeypatch):
    seen = {}

    async def load(session, key, tf, start, end, max_bars=None, private=None):
        seen["end"] = end
        return F, "lake:15m"                                 # more bars than the time allows: the forming / later ones are cut

    async def zones(**kw):
        seen["at"] = kw["at"]
        return {"bands": _bands(F[F["bar_end"] <= CUT]), "as_of": None}

    async def rows(session, key, when, daily=None, *, owner, private=None):
        return MTF

    async def no_engine(*a, **k):
        return None
    monkeypatch.setattr(pal, "load_frame", load)
    monkeypatch.setattr(zl, "market_zones_layer", zones)
    monkeypatch.setattr(pal, "mtf_rows", rows)
    monkeypatch.setattr(WR, "engine_reading", no_engine)
    user = SimpleNamespace(id=10**9 + 7, tenant_id=10**9 + 7)

    async def go():
        async with _session_factory() as session:
            return await R.workspace_annotations(session, user, "NSE:NIFTY BANK", "15m", CUT)
    a = asyncio.run(go())
    assert a["source"] == "atp" and seen["end"] == CUT
    assert seen["at"] == pd.Timestamp(F[F["bar_end"] <= CUT]["bar_end"].iloc[-1]).to_pydatetime()
    assert a == _atp_reading(F)
    assert asyncio.run(_wrong_tf(user)) is None


async def _wrong_tf(user):
    async with _session_factory() as session:
        return await R.workspace_annotations(session, user, "NSE:NIFTY BANK", "10m", CUT)


# --- the check service ------------------------------------------------------------------------------------------------

@pytest.fixture
def on(monkeypatch):
    t = _org_tenant(f"m76s-{uuid.uuid4().hex[:4]}")
    _flag("trade_warnings", t["tenant_id"])
    _flag("copilot_workspace", t["tenant_id"])
    expiry = _expiry()
    strike = float(30000 + 50 * (uuid.uuid4().int % 100))
    _contract(expiry, strike)
    _contract(expiry, strike, "PE")
    _price(monkeypatch, 100.0)
    asked: list = []

    async def reading(session, user, key, tf, now):
        asked.append((key, tf))
        return _reading(trend=((tf, "down"),), events=((("BOS", "down", tf)),), close=strike,
                        areas=(_area("seller", strike - 20, strike + 20, tf), _area("buyer", strike - 300, strike - 200, tf)))
    monkeypatch.setattr(R, "workspace_annotations", reading)
    return t, expiry, strike, asked


def _checks(t, body, **kw):
    r = client.post(URL, headers=t["headers"], json={**body, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _structure(out):
    return [f for f in out["findings"] if f["id"] in ps.STRUCTURE_IDS]


def test_one_switch_per_structure_type_on_as_information():
    for wid in ps.STRUCTURE_IDS:
        switch, ask = ts.BY_KEY[f"warnings.{wid}"], ts.BY_KEY[f"warnings.{wid}_ask"]
        assert switch.default is True and switch.default_by == "owner" and ask.default is False
        assert switch.flag == "trade_warnings" and wid in pw.WARNING_IDS
    assert "false_break_entry" not in pw.WARNING_IDS      # no public phrase in either source: the type does not exist yet


def test_daily_by_default_the_lines_come_back_labelled_and_after_the_facts(on):
    t, expiry, strike, asked = on
    out = _checks(t, _body(expiry, strike))
    assert [tf for _k, tf in asked] == ["1d"]              # no signal timeframe saved: the daily only
    assert asked[0][0] == ps.chart_key("NIFTY")
    lines = _structure(out)
    assert [f["id"] for f in lines] == ["counter_trend", "area_conflict"]
    assert all(f["severity"] == "fact" and f["text"].endswith("· ATP chart readers") for f in lines)
    assert set(out["switches"]) == set(pw.WARNING_IDS) and out["switches"]["room"] == {"on": True, "ask": False}
    # a sold PE: the strike line; a bearish order (buy PE): the room to the buyer area below
    assert "strike_in_area" in [f["id"] for f in _structure(_checks(t, _body(expiry, strike, right="PE", side="SELL")))]
    bear = _structure(_checks(t, _body(expiry, strike, right="PE")))
    assert [f["id"] for f in bear] == ["room"] and "200.00 points below" in bear[0]["text"]


def test_the_workspace_timeframe_or_his_signal_timeframe_and_the_daily(on, monkeypatch):
    t, expiry, strike, asked = on
    _checks(t, _body(expiry, strike), chart_instrument=ps.chart_key("NIFTY"), chart_tf="15m")
    assert asked[-1][1] == "15m" and len(asked) == 1        # the workspace's open timeframe, alone
    _checks(t, _body(expiry, strike), chart_instrument="NSE:RELIANCE", chart_tf="15m")
    assert asked[-1][1] == "1d"                              # another underlying open: his own timeframes
    from sqlalchemy import select

    from app.db.models import User

    async def owner():
        async with _session_factory() as session:
            return (await session.execute(select(User.id).where(User.tenant_id == t["tenant_id"]))).scalars().first()
    uid = asyncio.run(owner())

    async def stored(session):
        return {str(t["tenant_id"]): {"user_id": uid, "signal_timeframe": "5m"}}
    monkeypatch.setattr(engine_runner, "_stored_settings", stored)
    asked.clear()
    _checks(t, _body(expiry, strike, lots=2))
    assert sorted(tf for _k, tf in asked) == ["1d", "5m"]


def test_switches_accepted_today_and_the_workspace_flag(on):
    t, expiry, strike, asked = on
    r = client.put("/api/me/trade-settings", headers=t["headers"], json={"values": {"warnings.counter_trend": False}})
    assert r.status_code == 200, r.text
    assert [f["id"] for f in _structure(_checks(t, _body(expiry, strike)))] == ["trend_conflict", "area_conflict"]
    client.put("/api/me/trade-settings", headers=t["headers"], json={"values": {"warnings.trend_conflict": False, "warnings.area_conflict": False,
                                                                              "warnings.room": False, "warnings.strike_in_area": False}})
    asked.clear()
    assert _structure(_checks(t, _body(expiry, strike))) == [] and asked == []      # every type off: not even read
    client.put("/api/me/trade-settings", headers=t["headers"], json={"values": {"warnings.counter_trend": True, "warnings.trend_conflict": True,
                                                                              "warnings.area_conflict": True}})
    acc = client.post(URL + "/accept", headers=t["headers"], json={"underlying": "NIFTY", "expiry": expiry.isoformat(), "strike": strike,
                                                                   "right": "CE", "types": ["counter_trend"]})
    assert acc.status_code == 204
    assert [f["id"] for f in _structure(_checks(t, _body(expiry, strike, lots=3)))] == ["area_conflict"]
    _flag("copilot_workspace", t["tenant_id"], on=False)
    asked.clear()
    assert _structure(_checks(t, _body(expiry, strike, lots=4))) == [] and asked == []


def test_place_is_unchanged_with_structure_lines_shown(on):
    t, expiry, strike, _asked = on
    assert _structure(_checks(t, _body(expiry, strike)))
    assert _place(t, _body(expiry, strike))["status"] == "PLACED"          # accept and continue = pressing Place


def test_a_slow_reading_is_pending_and_a_follow_up_collects_it(monkeypatch):
    async def slow(session, user, key, tf, now):
        await asyncio.sleep(0.6)
        return _reading(trend=((tf, "up"),))

    async def flag_on(session, name, tenant_id):
        return True
    from app.platform import controls
    monkeypatch.setattr(R, "workspace_annotations", slow)
    monkeypatch.setattr(controls, "flag_enabled", flag_on)
    async def stored(session):
        return {}
    monkeypatch.setattr(engine_runner, "_stored_settings", stored)
    monkeypatch.setattr(pc, "CHECKS", {"structure": pc.check_structure})
    d = pc.Draft("NIFTY", (datetime.now(UTC) + timedelta(days=9)).date(), 24500.0, "PE", "BUY", 1, "MARKET", "NRML", None, 100.0)
    user = SimpleNamespace(id=4545, tenant_id=1)
    s = pw.settings_from({})

    async def go():
        t0 = time.perf_counter()
        first = await pc.run(_session_factory, user, d, s, accepted=set())
        took = time.perf_counter() - t0
        await asyncio.sleep(0.4)
        second = await pc.run(_session_factory, user, d, s, accepted=set(), only=first["pending"])
        return first, took, second
    first, took, second = asyncio.run(go())
    assert took < 0.4 and first["pending"] == ["structure"] and first["findings"] == []
    assert [f["id"] for f in second["findings"]] == ["counter_trend"] and second["pending"] == []


def test_nothing_here_touches_an_order():
    src = inspect.getsource(ps) + inspect.getsource(R.workspace_annotations)
    for word in ("fno_ticket", "execute_signal", "exit_now", ".add(", "delete(", "update(", ".place(", "place_order"):
        assert word not in src, word
