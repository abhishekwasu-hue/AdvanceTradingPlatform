"""M97 PR-5 (docs/design/M97_SUPPLY_DEMAND.md, Z6 + Z7, M97-Q5): the Supply & Demand overlay's server prefs (style
presets that only preselect timeframes, editable) and the Copilot chart answer's zone facts.

* ChartPrefs.sd_zones / sd_overlay: defaults off, round trip through /api/me/prefs/chart, bad values refused;
* the chart reader adds the zones (formation, state, proximal / distal, timeframe) only while the pref is on and the
  flag `sd_pack` is on; every number comes from the server tool; structure words only (no buy / sell / target / stop);
* the tool itself reads the pref on the server and refuses while it is off; the chat route reads the asker's own pref.
Synthetic data only. Nothing here touches an order.
"""
from __future__ import annotations

import asyncio
import copy
import uuid

import pytest

from app.ai import copilot_chat as chat
from app.ai import grounding
from app.ai.chart_answer import CHART_TOOLS, wording_problem
from app.ai.tools import ToolContext, registry
from app.ai.tools import copilot as tc
from app.me import prefs
from evals import runner
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

FIX = runner.copilot_fixtures()
ZONES = {"ok": True, "as_of": "2026-11-10T10:00:00+00:00", "source": "sd_zones", "data": {
    "symbol": "NIFTY", "timeframe": "1d", "as_of_bar": 1762732800, "bars": 700, "reason": None, "settings_note": "reference - edit before use",
    "zones": [{"timeframe": "1d", "side": "demand", "formation": "DBR", "state": "fresh", "tests": 0, "proximal": 24310.0, "distal": 24180.5},
              {"timeframe": "1d", "side": "supply", "formation": "RBD", "state": "tested", "tests": 2, "proximal": 25120.0, "distal": 25260.0}]}}


def _run(coro):
    return asyncio.run(coro)


def _answer(question: str, *, sd: bool, flags: dict | None = None):
    fixture = copy.deepcopy(FIX["default"])
    fixture["get_sd_zones:NIFTY"] = ZONES
    calls: list[dict] = []
    out = _run(chat.answer(question, runner.recorded_runner(fixture, calls), flags=flags, sd_zones=sd))
    return out, calls


# --------------------------------------------------------------------------------------------------- the prefs
def test_the_overlay_prefs_default_off_and_the_style_ladders_are_the_owners():
    d = prefs.defaults("chart")
    assert d["sd_zones"] is False
    assert d["sd_overlay"] == {"style": None, "timeframes": None, "presets": {}, "show_violated": False}
    assert prefs.SD_STYLE_TIMEFRAMES == {"investor": ("1M",), "positional": ("1w",), "swing": ("1d", "2h"), "short_term": ("1h", "30m"),
                                         "intraday": ("15m", "10m"), "scalper": ("5m", "3m")}


def test_the_overlay_prefs_round_trip_and_bad_values_are_refused():
    headers, _ = _owner(f"m97-prefs-{uuid.uuid4().hex[:10]}@example.com")
    body = {**prefs.defaults("chart"), "sd_zones": True,
            "sd_overlay": {"style": "swing", "timeframes": ["1d", "1h"], "presets": {"swing": ["1d", "1h"]}, "show_violated": True}}
    r = client.put("/api/me/prefs/chart", json=body, headers=headers)
    assert r.status_code == 200, r.text
    got = client.get("/api/me/prefs", headers=headers).json()["prefs"]["chart"]
    assert got["sd_zones"] is True and got["sd_overlay"]["presets"] == {"swing": ["1d", "1h"]} and got["sd_overlay"]["show_violated"] is True
    for bad in ({"style": "daytrader"}, {"timeframes": ["1d", "1d"]}, {"timeframes": ["1m", "5m", "15m", "1h", "1d"]}, {"timeframes": ["7m"]},
                {"presets": {"swing": []}}):
        r = client.put("/api/me/prefs/chart", json={**body, "sd_overlay": {**body["sd_overlay"], **bad}}, headers=headers)
        assert r.status_code == 422, bad


# --------------------------------------------------------------------------------------------- the Copilot facts
def test_the_chart_answer_carries_the_zones_only_while_the_overlay_is_on():
    off, calls = _answer("Read the NIFTY chart", sd=False)
    assert "get_sd_zones" not in [c["tool"] for c in calls]
    assert not any(s["title"] == "Supply & Demand zones" for s in off.sections)
    on, calls = _answer("Read the NIFTY chart", sd=True)
    asked = [c for c in calls if c["tool"] == "get_sd_zones"]
    assert asked == [{"tool": "get_sd_zones", "args": {"symbol": "NIFTY", "timeframe": "1d"}}]
    (section,) = [s for s in on.sections if s["title"] == "Supply & Demand zones"]
    assert section["lines"] == ["Demand zone (DBR, fresh) on the daily chart: proximal 24,310, distal 24,180.5.",
                                "Supply zone (RBD, tested 2 times) on the daily chart: proximal 25,120, distal 25,260."]
    # the asked timeframe goes to the server tool
    _, calls = _answer("read the NIFTY 15 min chart", sd=True)
    assert [c["args"] for c in calls if c["tool"] == "get_sd_zones"] == [{"symbol": "NIFTY", "timeframe": "15m"}]


def test_the_flag_off_reads_no_zones_even_with_the_overlay_on():
    flags = {name: True for name in chat.CHAT_FLAGS}
    flags["sd_pack"] = False
    out, calls = _answer("Read the NIFTY chart", sd=True, flags=flags)
    assert "get_sd_zones" not in [c["tool"] for c in calls]
    assert "sd_pack" in chat.CHAT_FLAGS


def test_every_zone_number_comes_from_the_tool_and_the_words_are_structure_words():
    out, _ = _answer("Read the NIFTY chart", sd=True)
    allowed = grounding.allowed_from_text("\n".join([*out.trusted_text, chat.FIXED_TEXT, "Read the NIFTY chart"]))
    lines = out.lines()
    assert any("proximal 24,310" in x for x in lines)
    for line in lines:
        assert grounding.check_numbers(line, allowed)[0], line
    assert "dropped_lines" not in out.diagnostics
    text = "\n".join(lines)
    assert wording_problem(text) is None
    for word in ("buy", "sell", "target", "stop"):
        assert word not in " ".join(chat.sd_zone_lines(chat.ToolResult(True, ZONES["data"], None, "sd_zones"))).lower()
    assert "get_sd_zones" in CHART_TOOLS                             # the planner's chart wording guard covers it


def test_no_zone_lines_when_the_tool_failed_and_a_plain_line_when_none_are_near():
    assert chat.sd_zone_lines(chat.ToolResult(False, None, None, "sd_zones", error=tc.SD_OFF)) == []
    empty = copy.deepcopy(ZONES)
    empty["data"]["zones"] = []
    fixture = copy.deepcopy(FIX["default"])
    fixture["get_sd_zones:NIFTY"] = empty
    out = _run(chat.answer("Read the NIFTY chart", runner.recorded_runner(fixture, []), sd_zones=True))
    assert any("supply / demand zone" in u for u in out.unknowns)


# ---------------------------------------------------------------------------------------- the tool and the route
def test_the_tool_is_read_only_flagged_strict_and_reads_the_pref_on_the_server(monkeypatch):
    reg = registry()
    assert reg["get_sd_zones"].kind == "read" and reg["get_sd_zones"].flag == "sd_pack"
    headers, me = _owner(f"m97-tool-{uuid.uuid4().hex[:10]}@example.com")
    seen: list[dict] = []

    async def fake_layer(**kw):
        seen.append(kw)
        return {"symbol": kw["symbol"], "at": "2026-11-10T10:00:00+00:00", "settings_note": "reference - edit before use",
                "timeframes": [{"timeframe": kw["timeframes"], "bars": 100, "as_of_bar": 1, "reason": None, "more": 0, "hidden_violated": 0,
                                "zones": [{**z, "id": "x", "label": "", "base_start": 0} for z in ZONES["data"]["zones"]]}]}
    from app.charts import sd_zones_layer as szl
    monkeypatch.setattr(szl, "sd_zones_layer", fake_layer)

    async def call(timeframe: str):
        from app.db.models import User
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            ctx = ToolContext(session=session, tenant_id=me["tenant_id"], user=user)
            return await tc.get_sd_zones(ctx, tc._SdArgs(symbol="NIFTY", timeframe=timeframe))
    off = _run(call("1d"))
    assert not off.ok and off.error == tc.SD_OFF and seen == []           # the pref is off: nothing is computed
    client.put("/api/me/prefs/chart", json={**prefs.defaults("chart"), "sd_zones": True}, headers=headers)
    on = _run(call("2h"))
    assert on.ok and seen[-1]["timeframes"] == "2h" and seen[-1]["include_violated"] is False
    assert on.data["zones"][0] == {"timeframe": "1d", "side": "demand", "formation": "DBR", "state": "fresh", "tests": 0,
                                   "proximal": 24310.0, "distal": 24180.5}
    with pytest.raises(ValueError):
        tc._SdArgs(symbol="NIFTY", timeframe="1d", proximal=1)            # strict: nothing else is taken from the client


def test_the_chat_route_reads_the_askers_own_pref():
    from app.ai.chat_routes import _sd_overlay
    headers, me = _owner(f"m97-route-{uuid.uuid4().hex[:10]}@example.com")
    other_headers, other = _owner(f"m97-route-b-{uuid.uuid4().hex[:10]}@example.com")
    client.put("/api/me/prefs/chart", json={**prefs.defaults("chart"), "sd_zones": True}, headers=other_headers)

    async def read(tenant_id, user_id):
        async with _session_factory() as session:
            return await _sd_overlay(session, tenant_id, user_id)
    assert _run(read(me["tenant_id"], me["id"])) is False
    assert _run(read(other["tenant_id"], other["id"])) is True
    assert _run(read(me["tenant_id"], other["id"])) is False              # never another organisation's user
