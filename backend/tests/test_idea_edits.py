"""Handover 11j step 6 "edits by chat" on a scan from an idea (app/screener/idea_edits.py, the `edits` of
POST /api/screener/copilot/idea behind `screener_copilot`).

- an edit only removes parts or adds parts the idea reader builds from registry functions; every result validates;
- each edit has one plain line of feedback, and says when nothing changed and why;
- trade wording changes nothing; English only;
- the idea is replayed with its edits from the start, so a version is the idea plus its first n edits.
All ideas here are made up for the test."""
import re
import uuid

import pytest

from app.screener import compile_screen, idea, idea_edits
from app.signals.language import check
from tests.test_auth_api import client
from tests.test_n1_e1_execution_core import _flag
from tests.test_trading_worker import _tenant

URL = "/api/screener/copilot/idea"
IDEA = "F&O stocks in a Daily uptrend, at a 1H demand zone on falling volume, RSI near 40"
START = ["IsFnO()", 'Trend() == "UPTREND"', 'NearZone("demand")@1h', "volume@1h < SMA(volume, 20)@1h", "RSI() >= 35 AND RSI() <= 45"]


def _run(*edits, base_tf="1d"):
    draft, tf, feedback = idea_edits.replay(IDEA, base_tf, list(edits))
    return [p.condition for p in draft.parts], tf, feedback


def test_no_edit_is_the_idea_as_read():
    conditions, tf, feedback = _run()
    assert conditions == START and tf == "1d" and feedback == []


@pytest.mark.parametrize("edit, removed", [
    ("drop the volume condition", "volume@1h < SMA(volume, 20)@1h"),
    ("remove the demand zone", 'NearZone("demand")@1h'),
    ("drop the 1h zone", 'NearZone("demand")@1h'),
    ("no F&O", "IsFnO()"),
    ("without the uptrend", 'Trend() == "UPTREND"'),
    ("Please remove RSI.", "RSI() >= 35 AND RSI() <= 45"),
])
def test_a_remove_takes_out_only_the_part_it_names(edit, removed):
    conditions, _, [fb] = _run(edit)
    assert conditions == [c for c in START if c != removed]
    assert fb.changed and fb.kind == "removed" and fb.message.startswith("Removed: ") and len(fb.removed) == 1


def test_a_remove_of_a_part_that_is_not_there_or_not_named_changes_nothing():
    conditions, _, [a, b] = _run("drop the daily zone", "remove everything")
    assert conditions == START
    assert not a.changed and a.message == "Your scan has no “daily zone” part, so nothing changed."
    assert not b.changed and "could not tell which part" in b.message


def test_an_add_appends_and_a_contradicting_part_on_the_same_chart_is_replaced():
    conditions, _, [fb] = _run("also above the 200 EMA")
    assert conditions == START + ["close > EMA(close, 200)"] and fb.kind == "added" and fb.added == ["the close is above its 200-bar EMA"]
    conditions, _, [fb] = _run("downtrend")
    assert conditions[1] == 'Trend() == "DOWNTREND"' and len(conditions) == 5 and fb.kind == "replaced"
    assert fb.message == "Changed: the market-structure trend is up → the market-structure trend is down."
    conditions, _, [fb] = _run("weekly downtrend")                # another chart: both stay
    assert 'Trend() == "UPTREND"' in conditions and 'DowTrend()@1w == "DOWN"' in conditions and fb.kind == "added"


@pytest.mark.parametrize("edit", ["change RSI to above 60", "RSI above 60 instead of RSI near 40", "replace the RSI with RSI above 60",
                                  "make it RSI above 60"])
def test_a_change_swaps_the_part_in_place(edit):
    conditions, _, [fb] = _run(edit)
    assert conditions == START[:4] + ["RSI() > 60"]
    assert fb.changed and fb.message == "Changed: RSI is between 35 and 45 → RSI is above 60."


def test_the_timeframe_edits():
    conditions, tf, [fb] = _run("weekly instead of daily")
    assert tf == "1w" and conditions == START and "Daily chart is now the Weekly chart" in fb.message
    conditions, tf, [fb] = _run("use the 1h chart")
    assert tf == "1h" and conditions[1] == 'DowTrend()@1d == "UP"' and conditions[2] == 'NearZone("demand")'
    assert "Parts that named their own chart keep it" in fb.message
    conditions, tf, [fb] = _run("weekly instead of 1h")             # not the scan's chart: only the parts that named it move
    assert tf == "1d" and conditions[2] == 'NearZone("demand")@1w' and "scan's own chart stays the same" in fb.message
    _, tf, [fb] = _run("use daily")
    assert tf == "1d" and not fb.changed and fb.message == "The scan already reads the Daily chart, so nothing changed."


def test_trade_wording_sectors_and_other_languages_change_nothing():
    for edit, why in [("tighter SL", idea.TRADE_WORDS_WHY), ("add a target", idea.TRADE_WORDS_WHY), ("only banks", "not supported yet"),
                      ("hello", "not supported yet"), ("\u092b\u0915\u094d\u0924 \u092c\u0901\u0915\u093e", "only English")]:
        conditions, _, [fb] = _run(edit)
        assert conditions == START and not fb.changed and fb.kind == "unchanged", edit
        assert why.split(";")[0].lower() in fb.message.lower(), (edit, fb.message)
    _, _, [fb] = _run("also uptrend")
    assert fb.message == "That is already in your scan, so nothing changed."


def test_edits_apply_in_order_and_a_version_is_the_idea_plus_its_first_edits():
    edits = ["drop the volume condition", "rising volume", "drop fno"]
    full, _, feedback = _run(*edits)
    assert full == ['Trend() == "UPTREND"', 'NearZone("demand")@1h', "RSI() >= 35 AND RSI() <= 45", "volume > SMA(volume, 20) * 1.5"]
    assert [f.kind for f in feedback] == ["removed", "added", "removed"]
    assert _run(*edits[:1])[0] == [c for c in START if "volume" not in c]
    assert _run(*edits) == _run(*edits)                                  # replay is deterministic
    conditions, _, feedback = _run("drop fno", "drop trend", "drop zone", "drop volume", "drop rsi")
    assert conditions == [] and "No condition is left" in feedback[-1].message


@pytest.mark.parametrize("edits", [["drop the volume condition"], ["also RSI above 60"], ["downtrend", "also bullish divergence"],
                                   ["use the 1h chart", "also above vwap"], ["change the trend to sideways"], ["also hammer on the weekly chart"]])
def test_every_edited_draft_validates_from_registry_functions_and_the_feedback_is_plain(edits):
    draft, tf, feedback = idea_edits.replay(IDEA.replace("1H ", ""), "1d", edits)
    for part in draft.parts:
        ast, validated = compile_screen(part.condition, base_tf=tf)
        assert ast is not None and validated.ok, (part.condition, [p.message for p in validated.problems])
    for fb in feedback:
        assert fb.changed, fb.message
        assert not re.search(r"\b(?:buy|sell|target|stop)\b", fb.message, re.I)
        assert check(re.sub(r"[\d.]+", "", fb.message), "signal_public") == [], fb.message


def test_at_most_twenty_edits_are_read():
    assert len(_run(*(["drop fno"] * 25))[2]) == idea_edits.MAX_EDITS


# --- the route -----------------------------------------------------------------------------------------------------------------

def _org(tag):
    t = _tenant(f"idea-edit-{tag}-{uuid.uuid4().hex[:6]}@example.com", with_credentials=False)
    for name in ("screener_v2", "screener_copilot", "market_zones"):
        _flag(name, t["tenant_id"])
    return t


def test_the_route_replays_the_edits_and_returns_the_feedback():
    t = _org("on")
    body = client.post(URL, headers=t["headers"], json={"text": IDEA, "edits": ["drop the volume condition", "use the 1h chart", "only banks"]}).json()
    assert body["ok"] and body["base_tf"] == "1h"
    assert body["text"] == 'IsFnO() AND NearZone("demand") AND RSI() >= 35 AND RSI() <= 45'
    dow = next(p for p in body["parts"] if p["condition"] == 'DowTrend()@1d == "UP"')     # gated like a typed screen
    assert not dow["ok"] and dow["problems"][0]["kind"] == "flag_off"
    assert [e["kind"] for e in body["edits"]] == ["removed", "timeframe", "unchanged"]
    assert body["edits"][2]["message"] == "“banks” is not supported yet, so nothing changed." and body["edits"][2]["changed"] is False
    assert all(set(e) == {"edit", "kind", "message", "changed", "removed", "added"} for e in body["edits"])
    plain = client.post(URL, headers=t["headers"], json={"text": IDEA}).json()
    assert plain["edits"] == [] and plain["base_tf"] == "1d"


def test_the_route_limits_the_edits():
    t = _org("limits")
    assert client.post(URL, headers=t["headers"], json={"text": IDEA, "edits": ["drop fno"] * 21}).status_code == 422
    assert client.post(URL, headers=t["headers"], json={"text": IDEA, "edits": ["x" * 301]}).status_code == 422
    assert client.post(URL, headers=t["headers"], json={"text": IDEA, "edits": [""]}).status_code == 422
