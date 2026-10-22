"""Handover 11j "idea -> scan": a plain idea -> a ScreenQL draft from registry functions only (app/screener/idea.py,
POST /api/screener/copilot/idea behind `screener_copilot`).

- every rule builds a condition the Screener's own validator accepts, from registry names only;
- unknown words come back as "not supported yet" with the user's words; trade wording is never a condition;
- a part whose function is behind a flag that is off, or that reads a finer timeframe, comes back with the validator's
  fixed reason and is left out of the screen;
- the draft opens as editable blocks (the shape of POST /stages); no AI provider is needed; nothing is stored or run.
All ideas here are made up for the test."""
import re
import uuid

import pytest
from sqlalchemy import func, select

from app.db.models import AuditLogRecord, ScreenRecord, ScreenRunRecord
from app.screener import compile_screen, idea, nodes
from app.screener.registry import FIELDS, FUNCTIONS
from tests.test_auth_api import _session_factory, client
from tests.test_n1_e1_execution_core import _flag
from tests.test_trading_worker import _tenant

URL = "/api/screener/copilot/idea"
EXAMPLE = "F&O stocks in a Daily uptrend, pulling back into a 1H demand zone on falling volume, RSI near 40"

IDEAS = [
    "uptrend", "downtrend", "sideways range", "at a demand zone", "near a supply area", "bullish divergence", "bearish divergence",
    "RSI divergence", "RSI above 60", "RSI below 35", "RSI near 40", "oversold", "overbought", "falling volume", "heavy volume",
    "F&O stocks", "above the 200 EMA", "below 50 SMA", "above the 20 day moving average", "bullish change of character",
    "break of structure", "hammer", "bearish engulfing", "inside bar", "bullish candle", "52-week high", "new lows",
    "breakout", "breakdown", "retest after breakout", "breakdown retest", "higher lows", "lower highs", "strong trend",
    "ADX above 30", "above vwap", "supertrend green", "weekly uptrend", "hammer on the weekly chart",
]


def _names(text: str):
    return set(re.findall(r"\b([A-Za-z]\w*)\s*\(", text)) - {"IN"} | set(re.findall(r"\b(open|high|low|close|volume|oi)\b", text))


@pytest.mark.parametrize("text", IDEAS)
def test_every_rule_builds_a_valid_condition_from_registry_names_only(text):
    draft = idea.read_idea(text, "1d")
    assert draft.parts, text
    for part in draft.parts:
        assert _names(part.condition) <= set(FUNCTIONS) | set(FIELDS), part.condition
        ast, validated = compile_screen(part.condition, base_tf="1d")
        assert ast is not None and validated.ok, (part.condition, [p.message for p in validated.problems])
        assert part.reads and not re.search(r"\b(?:buy|sell|target|stop)\b", part.reads, re.I)


def test_the_owners_example_reads_part_by_part_and_says_what_it_does_not_know():
    draft = idea.read_idea(EXAMPLE, "1d")
    assert [p.condition for p in draft.parts] == ["IsFnO()", 'Trend() == "UPTREND"', 'NearZone("demand")@1h', "volume@1h < SMA(volume, 20)@1h",
                                                 "RSI() >= 35 AND RSI() <= 45"]
    assert [p.phrase for p in draft.parts] == ["F&O", "uptrend", "demand zone", "falling volume", "RSI near 40"]
    assert draft.unsupported == [{"phrase": "pulling back", "why": idea.NOT_SUPPORTED}]
    assert any("within 5 points" in a for a in draft.parts[-1].assumptions)


def test_trade_wording_is_never_a_condition():
    draft = idea.read_idea("Buy when RSI below 30, stop loss 2%, target 10%", "1d")
    assert [p.condition for p in draft.parts] == ["RSI() < 30"]
    assert {u["why"] for u in draft.unsupported} == {idea.TRADE_WORDS_WHY}
    assert idea.read_idea("banks with promoter buying", "1d").unsupported == [
        {"phrase": "banks", "why": idea.NOT_SUPPORTED}, {"phrase": "promoter buying", "why": idea.NOT_SUPPORTED}]


def test_english_only_and_nothing_guessed():
    assert idea.read_idea("\u092e\u093e\u0917\u0923\u0940 \u0915\u094d\u0937\u0947\u0924\u094d\u0930", "1d").parts == []
    assert idea.read_idea("stocks that will go up tomorrow", "1d").parts == []
    assert idea.read_idea("RSI above 300", "1d").parts == []                 # out of range: no condition, no guess
    assert idea.joined(["A()", "(B() OR C())"]) == "A() AND (B() OR C())"


def test_a_timeframe_equal_to_the_scan_is_not_repeated():
    assert [p.condition for p in idea.read_idea("daily uptrend at a daily demand zone", "1d").parts] == ['Trend() == "UPTREND"', 'NearZone("demand")']
    assert [p.condition for p in idea.read_idea("weekly uptrend", "1d").parts] == ['DowTrend()@1w == "UP"']


# --- the route -----------------------------------------------------------------------------------------------------------------

def _org(tag, flags=("screener_v2", "screener_copilot")):
    t = _tenant(f"idea-{tag}-{uuid.uuid4().hex[:6]}@example.com", with_credentials=False)
    for name in flags:
        _flag(name, t["tenant_id"])
    return t


async def _count(model, tenant_id):
    async with _session_factory() as session:
        return await session.scalar(select(func.count()).select_from(model).where(model.tenant_id == tenant_id))


def test_the_route_needs_the_flags_and_a_login():
    off = _org("off", flags=("screener_v2",))
    r = client.post(URL, headers=off["headers"], json={"text": "uptrend"})
    assert r.status_code in (403, 404, 503) and "screener_copilot" in r.text
    assert client.post(URL, json={"text": "uptrend"}).status_code == 401


def test_the_route_returns_editable_blocks_and_stores_nothing():
    import asyncio
    t = _org("on", flags=("screener_v2", "screener_copilot", "market_zones"))
    out = client.post(URL, headers=t["headers"], json={"text": "F&O stocks in a Daily uptrend at a demand zone on falling volume, RSI near 40, pulling back"})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["ok"] and body["base_tf"] == "1d" and body["disclaimer"] == idea.DISCLAIMER
    assert body["text"] == 'IsFnO() AND Trend() == "UPTREND" AND NearZone("demand") AND volume < SMA(volume, 20) AND RSI() >= 35 AND RSI() <= 45'
    assert [s["text"] for s in body["stages"]] == ["IsFnO()", 'Trend() == "UPTREND"', 'NearZone("demand")', "volume < SMA(volume, 20)",
                                                   "RSI() >= 35", "RSI() <= 45"]
    assert all({"text", "wrapped", "ast"} <= set(s) for s in body["stages"])
    assert all(p["ok"] and p["problems"] == [] for p in body["parts"])
    assert body["not_supported"] == [{"phrase": "pulling back", "why": idea.NOT_SUPPORTED}]
    assert nodes.to_text(compile_screen(body["text"], base_tf="1d")[0]) == body["text"]
    assert asyncio.run(_count(ScreenRecord, t["tenant_id"])) == 0 and asyncio.run(_count(ScreenRunRecord, t["tenant_id"])) == 0
    assert asyncio.run(_count(AuditLogRecord, t["tenant_id"])) >= 1


def test_a_part_behind_an_off_flag_or_a_finer_timeframe_is_left_out_with_the_fixed_reason():
    t = _org("gate")                                          # market_zones is off here
    body = client.post(URL, headers=t["headers"], json={"text": "uptrend at a demand zone, RSI above 50 on the 1h"}).json()
    by_rule = {p["rule"]: p for p in body["parts"]}
    assert by_rule["trend_up"]["ok"]
    assert not by_rule["demand_zone"]["ok"] and by_rule["demand_zone"]["problems"][0]["kind"] == "flag_off"
    assert not by_rule["rsi_level"]["ok"] and by_rule["rsi_level"]["problems"][0]["kind"] == "timeframe_finer"
    assert body["text"] == 'Trend() == "UPTREND"' and [s["text"] for s in body["stages"]] == ['Trend() == "UPTREND"']


def test_an_idea_with_nothing_known_is_a_plain_answer_not_an_error():
    t = _org("none")
    body = client.post(URL, headers=t["headers"], json={"text": "stocks my friend likes"}).json()
    assert body["ok"] is False and body["text"] is None and body["stages"] == [] and body["parts"] == []
    assert body["not_supported"] == [{"phrase": "friend likes", "why": idea.NOT_SUPPORTED}]
