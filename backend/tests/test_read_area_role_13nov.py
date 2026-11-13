"""Owner 13 Nov - the Copilot "Read chart" area role and timeframe (report: "look at wrong area marking its a seller area").

Root cause: the chart read measured the areas' roles against the LAST CLOSE (the zones tool got `price = last_close`),
while Trade's market_zones.compute_current_role reads the role against the CURRENT LTP (page_dashboard: "the role is
decided only against the current LTP: above it = resistance / supply, below = support / demand"). With the last daily
close inside a buyer area and the live price below it, ATP said "Demand · price inside"; Trade says supply (seller).

Covered here (synthetic numbers only, no market data):
* the exact situation: a daily buyer area, the last closed bar inside it, the live price below it -> a seller area
  above the price (headline, card, key-area lines); with the market closed the last close is the reference again;
* the area's bounds, state and touches come from the stored snapshot (closed bars) - the live price changes the role
  and the distance only (no look-ahead: a later snapshot is never read);
* zero-width areas: an S/R line (low = high, as Trade stores SUPPORT / RESISTANCE / DYNAMIC_SR) is a "level", one price;
* owner 13 Nov ("timeframe nivadlya nusar analysis"): the read follows the selected timeframe - its own areas, higher
  timeframes only as labelled context; the open chart's timeframe when the question names none; daily is said, never
  silent. The same symbol on 15m vs 1d gives timeframe-specific areas.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from app.ai import chart_answer
from app.ai import copilot_chat as chat
from app.ai.tools import ToolContext, ToolResult
from app.core import config
from app.db.models import MarketZoneRecord, MarketZoneRunRecord, MarketZoneSettingRecord, User
from app.price_action.zones import chart
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_zones_chart_screenql import D1, KEY, _set, _write_run, _zrow, ist

CLOSE, LIVE = 1030.0, 990.0            # the last closed daily bar is inside the buyer area; the live price is below it
BUYER = {"role": "demand", "zone_type": "DEMAND_ZONE", "family": "DEMAND_ZONE", "kind": "band", "low": 1000.0, "high": 1040.0,
         "state": "tested", "touches": 1, "distance_pct": 0.0, "timeframe": "1d"}
LINE = {"role": "supply", "zone_type": "SUPPORT", "family": "SR", "kind": "line", "low": 1050.0, "high": 1050.0, "state": "fresh",
        "touches": 0, "distance_pct": 1.9, "timeframe": "1d"}
LOWER = {**BUYER, "low": 940.0, "high": 960.0, "state": "fresh", "touches": 0}
AS_OF = "2031-01-10T10:00:00+00:00"


def _structure(tf="1d"):
    return ToolResult(True, {"symbol": "ZZZ", "timeframe": tf, "last_close": CLOSE, "official_close": {"close": CLOSE, "date": "2031-01-10"},
                             "as_of": AS_OF, "chart_state": {"direction": "up", "protected": 950.0},
                             "timeframes": [{"tf": tf, "direction": "up", "protected": 950.0, "short": False}], "levels": []},
                      AS_OF, "price_action_layer")


def _zones(bands, price=CLOSE, live=False, context=()):
    data = {"as_of": AS_OF, "price": price, "price_source": "live" if live else "snapshot", "price_at": "2031-01-11T06:45:00+00:00" if live else None,
            "bands": [dict(b) for b in bands], "context_bands": [dict(b, context=True) for b in context]}
    return ToolResult(True, data, AS_OF, "market_zones")


# ------------------------------------------------------------------------------------------------ the reported situation
def test_closed_bar_inside_live_below_reads_a_seller_area_like_trade():
    live = {"price": LIVE, "at": "2031-01-11T06:45:00+00:00"}
    head = chat.chart_headline("ZZZ", "1d", _structure(), _zones([BUYER, LOWER], price=LIVE, live=True), live)
    assert head["live_price"] == LIVE and head["price"] == CLOSE
    area = head["area"]
    # Trade compute_current_role(1000, 1040, 990): mid 1020 > 990 -> RESISTANCE_SUPPLY
    assert (area["side"], area["where"], area["distance"], area["from"], area["to"]) == ("supply", "above", 10.0, 1000.0, 1040.0)
    assert area["state"] == "tested" and area["timeframe"] == "1d" and area["as_of"] == AS_OF     # the state: closed bars
    card = chat.structure_card("ZZZ", "1d", _structure(), _zones([BUYER, LOWER], price=LIVE, live=True), live)
    assert [(a["side"], a["where"], a["distance"]) for a in card["areas_above"]] == [("supply", "above", 10.0)]
    assert [(a["side"], a["from"], a["distance"]) for a in card["areas_below"]] == [("demand", 940.0, 30.0)]
    assert card["live_price"] == LIVE and card["areas_as_of"] == AS_OF
    assert not any(a["where"] == "inside" for a in card["areas_above"] + card["areas_below"])     # never "price inside" from yesterday


def test_market_closed_the_last_close_is_the_reference():
    head = chat.chart_headline("ZZZ", "1d", _structure(), _zones([BUYER, LOWER]), None)
    assert head["live_price"] is None and head["area"]["side"] == "demand" and head["area"]["where"] == "inside"
    card = chat.structure_card("ZZZ", "1d", _structure(), _zones([BUYER, LOWER]))
    assert card["areas_below"][0]["side"] == "demand" and card["areas_below"][0]["where"] == "inside"


def test_the_role_rule_is_trades_compute_current_role():
    def trade(lo, hi, ltp):                                   # Trade market_zones.compute_current_role, word for word
        return "RESISTANCE_SUPPLY" if (lo + hi) / 2 > ltp else "SUPPORT_DEMAND"
    for lo, hi, ltp in ((1000, 1040, 990), (1000, 1040, 1030), (1000, 1040, 1020), (1050, 1050, 1049.9), (1050, 1050, 1050)):
        ours = chart.role_at({"zone_low": lo, "zone_high": hi}, ltp)
        assert {"supply": "RESISTANCE_SUPPLY", "demand": "SUPPORT_DEMAND"}[ours] == trade(lo, hi, ltp)


def test_the_live_price_changes_role_and_distance_only_never_the_stored_state():
    bands = [BUYER, LINE]
    before = [dict(b) for b in bands]
    for ref in (CLOSE, LIVE, 1100.0):
        for b, orig in zip(chat.read_bands(bands, ref), before):
            assert {k: b[k] for k in ("low", "high", "state", "touches", "timeframe")} == {k: orig[k] for k in ("low", "high", "state", "touches", "timeframe")}
    assert bands == before                                   # the tool's rows are never edited in place


def test_planner_path_rereads_the_roles_at_the_live_price():
    calls = [{"id": "s", "name": "get_chart_structure", "ok": True, "arguments": {"symbol": "ZZZ"}, "as_of": AS_OF},
             {"id": "z", "name": "get_market_zones", "ok": True, "arguments": {"symbol": "ZZZ", "price": CLOSE}, "as_of": AS_OF}]
    results = {"s": _structure().data, "z": _zones([BUYER, LOWER]).data}        # the model sent yesterday's close
    head, card = chart_answer.chart_parts(calls, results, {"price": LIVE, "at": None})
    assert head["area"]["side"] == "supply" and head["area"]["where"] == "above"
    assert card["areas_above"][0]["side"] == "supply"
    head, _card = chart_answer.chart_parts(calls, {"s": _structure().data, "z": _zones([BUYER, LOWER], price=LIVE, live=True).data})
    assert head["live_price"] == LIVE and head["area"]["side"] == "supply"     # the zones tool's own live price is used


# ----------------------------------------------------------------------------------------------------- zero-width areas
def test_a_line_level_is_one_price_never_a_zero_width_range():
    card = chat.structure_card("ZZZ", "1d", _structure(), _zones([BUYER, LINE]))
    line = next(a for a in card["areas_above"] if a["from"] == 1050.0)
    assert line["kind"] == "line" and line["from"] == line["to"] and line["side"] == "supply"
    assert next(a for a in card["areas_below"])["kind"] == "band"
    text = chat._zone_lines(_zones([BUYER, LINE]))
    assert "Supply level 1,050 (support, 1d, fresh)" in text and not any("1,050 - 1,050" in t for t in text)
    assert chat._band_text({**LINE}) == "supply level 1,050 (fresh)"
    assert chat.nearest_area(1049.0, [LINE])["kind"] == "line"


# ------------------------------------------------------------------------------------ the read follows the chosen timeframe
FIFTEEN = {**BUYER, "low": 1020.0, "high": 1025.0, "timeframe": "15m", "state": "fresh", "touches": 0}
HOURLY = {**BUYER, "low": 1060.0, "high": 1070.0, "timeframe": "1h", "state": "fresh", "touches": 0}


def test_split_by_timeframe_keeps_the_read_and_higher_context_only():
    cands = [{"timeframe": t, "low": 1.0, "high": 2.0} for t in ("5m", "15m", "1h", "1d", "1w")]
    own, higher = chart.split_by_timeframe(cands, "1h")
    assert [c["timeframe"] for c in own] == ["1h"] and [c["timeframe"] for c in higher] == ["1d", "1w"]
    with pytest.raises(ValueError):
        chart.split_by_timeframe(cands, "2d")


def test_the_same_symbol_on_15m_and_1d_gives_timeframe_specific_areas():
    mixed = [BUYER, FIFTEEN, HOURLY]
    on_15 = chat.structure_card("ZZZ", "15m", _structure("15m"), _zones([FIFTEEN], context=[BUYER, HOURLY]))
    on_1d = chat.structure_card("ZZZ", "1d", _structure("1d"), _zones([BUYER]))
    own_15 = [a for a in on_15["areas_above"] + on_15["areas_below"] if not a["context"]]
    assert [(a["timeframe"], a["from"]) for a in own_15] == [("15m", 1020.0)]
    assert {a["timeframe"] for a in on_15["areas_above"] + on_15["areas_below"] if a["context"]} == {"1d", "1h"}
    assert [(a["timeframe"], a["from"], a["context"]) for a in on_1d["areas_above"] + on_1d["areas_below"]] == [("1d", 1000.0, False)]
    # a zones answer that still carries every timeframe: the read keeps its own, a lower one never leaks into it
    assert [b["timeframe"] for b in chat.read_bands(mixed, CLOSE, "15m")] == ["15m"]
    assert chat.chart_headline("ZZZ", "15m", _structure("15m"), _zones([FIFTEEN], context=[BUYER]))["area"]["timeframe"] == "15m"
    assert chat.chart_headline("ZZZ", "15m", _structure("15m"), _zones([], context=[BUYER]))["area"] is None   # context never replaces


def test_the_timeframe_comes_from_the_question_then_the_open_chart_and_daily_is_said():
    assert chat.timeframe_in("read the nifty 15 min chart") == "15m" and chat.timeframe_note("read the nifty 15 min chart") is None
    assert chat.timeframe_in("read the nifty chart") == "1d" and "daily" in (chat.timeframe_note("read the nifty chart") or "")
    token = chat.CONTEXT_TIMEFRAME.set(chat.context_timeframe("15min"))
    try:
        assert chat.timeframe_in("read the nifty chart") == "15m" and chat.timeframe_note("read the nifty chart") is None
        assert chat.timeframe_in("read the nifty 1 hour chart") == "1h"          # the question's own word wins
        from app.ai.tools.copilot import read_timeframe
        assert read_timeframe(None) == "15m" and read_timeframe("1d") == "1d"
    finally:
        chat.CONTEXT_TIMEFRAME.reset(token)
    assert [chat.context_timeframe(x) for x in ("day", "60min", "15min", "5min", None)] == ["1d", "1h", "15m", None, None]


# ----------------------------------------------------------------------------------------------- the layer and the tool
def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def zones_on(monkeypatch):
    monkeypatch.setattr(config, "MARKET_ZONES_DEFAULTS", {})

    async def clean():
        async with _session_factory() as session:
            for model in (MarketZoneRecord, MarketZoneRunRecord, MarketZoneSettingRecord):
                await session.execute(delete(model))
            await session.commit()
    _run(clean())
    _set(market_zones=True)
    rows = [_zrow("d1d", "DEMAND_ZONE", 1000.0, 1040.0, "tested", 1, tf="1d"), _zrow("d15", "DEMAND_ZONE", 1020.0, 1025.0, tf="15m"),
            _zrow("s1h", "SUPPLY_ZONE", 1060.0, 1070.0, tf="1h"), _zrow("l15", "SUPPORT", 1050.0, 1050.0, tf="15m", strength=3),
            _zrow("d5", "DEMAND_ZONE", 1010.0, 1012.0, tf="5m")]
    _run(_write_run(KEY, ist(D1, 15, 30), rows, price=CLOSE))
    yield
    _run(clean())
    _set(market_zones=False)


def test_layer_bands_follow_the_timeframe_with_higher_context(zones_on):
    headers, _ = _owner("read-role-layer@example.com")
    url, at = "/api/charts/layers/market-zones", ist(D1, 16, 0).isoformat()
    every = client.get(url, params={"instrument": KEY, "at": at}, headers=headers).json()
    # no timeframe: every timeframe competes for the bands, as before (here the 5m zone is in them)
    assert "5m" in {b["timeframe"] for b in every["bands"]} and "context_bands" not in every
    m15 = client.get(url, params={"instrument": KEY, "at": at, "timeframe": "15m"}, headers=headers).json()
    assert {b["timeframe"] for b in m15["bands"]} == {"15m"} and {b["timeframe"] for b in m15["context_bands"]} <= {"1h", "1d"}
    assert next(b for b in m15["bands"] if b["zone_key"] == "l15")["kind"] == "line"
    d1 = client.get(url, params={"instrument": KEY, "at": at, "timeframe": "1d", "price": LIVE}, headers=headers).json()
    assert [(b["zone_key"], b["role"], b["state"]) for b in d1["bands"]] == [("d1d", "supply", "tested")] and d1["context_bands"] == []
    assert client.get(url, params={"instrument": KEY, "timeframe": "2d"}, headers=headers).status_code == 422
    # no look-ahead: a snapshot stored after `at` is never read
    _run(_write_run(KEY, ist(D1, 16, 30), [_zrow("late", "SUPPLY_ZONE", 1001.0, 1002.0, tf="1d")], price=CLOSE))
    again = client.get(url, params={"instrument": KEY, "at": at, "timeframe": "1d"}, headers=headers).json()
    assert [b["zone_key"] for b in again["bands"]] == ["d1d"]


def test_zones_tool_reads_the_roles_at_the_live_price(zones_on, monkeypatch):
    from app.ai.tools.copilot import _ZoneArgs, get_market_zones
    _h, me = _owner("read-role-tool@example.com")
    seen = []

    async def live(ctx, symbol, exchange):
        seen.append(symbol)
        return {"price": LIVE, "at": "2031-01-11T06:45:00+00:00"}
    monkeypatch.setattr(chart_answer, "live_price", live)

    async def go(args):
        async with _session_factory() as session:
            user = (await session.execute(select(User).where(User.id == me["id"]))).scalar_one()
            return await get_market_zones(ToolContext(session, me["tenant_id"], user, datetime.now(timezone.utc)), args)
    res = _run(go(_ZoneArgs(symbol=KEY, timeframe="1d")))
    assert res.ok and res.data["price"] == LIVE and res.data["price_source"] == "live" and seen == [KEY]
    assert [(b["role"], b["low"], b["state"]) for b in res.data["bands"]] == [("supply", 1000.0, "tested")]
    assert chat.zones_live(res) == {"price": LIVE, "at": "2031-01-11T06:45:00+00:00"}
    res = _run(go(_ZoneArgs(symbol=KEY, timeframe="1d", price=CLOSE)))           # a price he states is used as stated
    assert res.data["price"] == CLOSE and res.data["bands"][0]["role"] == "demand" and len(seen) == 1
