"""SFC P5 (owner APPROVED 19 Nov, docs/plans/strategy-first-chart-PLAN.md): the builder's server side.

Checks: the blocks a condition can read come from the registry (no cross-sectional / option-chain / reference / zone
function, nothing with a value pre-filled); `check` gives the compiler's own problems and never saves; save / read /
edit / delete are his own (another user: 404), behind the flag; a saved strategy is drawn on the chart at once; the v1
list and v1 read never meet a v2 strategy (no crash, no leak). Made-up data only."""
from __future__ import annotations

import copy

from tests.test_auth_api import client
from tests.test_sfc_p1 import RAW, _bars, _fake_reads, _org


def test_blocks_are_the_registry_without_cross_sectional_or_chain_functions_and_nothing_prefilled():
    me = _org("b")
    blocks = client.get("/api/strategies/v2/blocks", headers=me["headers"]).json()["blocks"]
    names = {b["block"] for b in blocks}
    assert {"EMA", "RSI", "SMA"} <= names
    from app.screener.registry import FUNCTIONS
    assert not any(FUNCTIONS[n].cross_sectional or "option_chain" in FUNCTIONS[n].needs for n in names)
    assert all("default" not in a for b in blocks for a in (*b["args"], *b["kwargs"]))           # DEF-0: no value given
    off = _org("b-off", flag_on=False)
    assert client.get("/api/strategies/v2/blocks", headers=off["headers"]).status_code in (403, 404, 503)


def test_check_gives_the_compilers_problems_and_saves_nothing():
    me = _org("c")
    assert client.post("/api/strategies/v2/check", headers=me["headers"], json={"strategy": RAW}).json() == {"ok": True, "problems": []}
    bad = copy.deepcopy(RAW)
    bad["entry"]["long"]["items"][0]["left"] = {"kind": "indicator", "block": "EMA", "params": {}}     # no length: refused, never filled
    got = client.post("/api/strategies/v2/check", headers=me["headers"], json={"strategy": bad}).json()
    assert got["ok"] is False and any("EMA" in p for p in got["problems"])
    assert client.get("/api/strategies/v2/chart-list", headers=me["headers"]).json()["strategies"] == []


def test_save_read_edit_delete_are_his_own_and_a_saved_one_is_drawn(monkeypatch):
    _fake_reads(monkeypatch, _bars())
    me, other = _org("s"), _org("s-other")
    made = client.post("/api/strategies/v2", headers=me["headers"], json={"strategy": RAW})
    assert made.status_code == 201, made.text
    rid, sid = made.json()["record_id"], made.json()["id"]
    assert made.json()["strategy"]["schema"] == "atp.strategy/2"
    assert client.get(f"/api/strategies/v2/{rid}", headers=me["headers"]).json()["name"] == "EMA cross + RSI"
    assert client.get(f"/api/strategies/v2/{rid}", headers=other["headers"]).status_code == 404
    listed = client.get("/api/strategies/v2/chart-list", headers=me["headers"]).json()["strategies"]
    assert [s["id"] for s in listed] == [sid] and listed[0]["drawable"]
    plot = client.post("/api/strategies/v2/plot", headers=me["headers"], json={"strategy_id": sid, "symbol": "NIFTY 50", "exchange": "NSE"})
    assert plot.json()["available"] and plot.json()["markers"]

    edited = copy.deepcopy(RAW)
    edited["name"] = "EMA cross only"
    edited["entry"]["long"]["items"] = edited["entry"]["long"]["items"][:1]
    assert client.put(f"/api/strategies/v2/{rid}", headers=other["headers"], json={"strategy": edited}).status_code == 404
    put = client.put(f"/api/strategies/v2/{rid}", headers=me["headers"], json={"strategy": edited})
    assert put.status_code == 200 and put.json()["name"] == "EMA cross only"
    bad = copy.deepcopy(edited)
    bad["entry"]["long"]["items"][0]["cmp"] = "gt"
    bad["entry"]["long"]["items"][0].pop("right")
    refused = client.put(f"/api/strategies/v2/{rid}", headers=me["headers"], json={"strategy": bad})
    assert refused.status_code in (400, 422)
    assert client.get(f"/api/strategies/v2/{rid}", headers=me["headers"]).json()["name"] == "EMA cross only"   # kept as it was

    # the v1 routes never meet it: not listed, not readable (and nothing crashes)
    v1_list = client.get("/api/custom-strategies", headers=me["headers"])
    assert v1_list.status_code == 200 and all(r["id"] != rid for r in v1_list.json())
    assert client.get(f"/api/custom-strategies/{rid}", headers=me["headers"]).status_code == 404

    assert client.delete(f"/api/strategies/v2/{rid}", headers=other["headers"]).status_code == 404
    assert client.delete(f"/api/strategies/v2/{rid}", headers=me["headers"]).status_code == 204
    assert client.get(f"/api/strategies/v2/{rid}", headers=me["headers"]).status_code == 404
