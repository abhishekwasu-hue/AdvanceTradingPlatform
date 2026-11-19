"""CA-CHAT (owner 18 / 19 Nov): the trader's own conditions checked on the SAME chart of his analysis.

Made-up data only; the API client is a fake (no network, no key). Checks: the chat is the caller's own (404 for anyone
else), his own key and cap, the cost logged with placeholders only, every sentence guarded (no order words), the chat
kept with the analysis but never in its `result` or a shared copy, a thread cap, and clearing.
"""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app.chart_analyze import share
from app.chart_reader import chat, service
from app.db.models import ChartAnalysisRecord, LlmCallRecord
from tests.test_auth_api import _session_factory, client
from tests.test_chart_analyze_api import _run
from tests.test_chart_analyze_api import _upload as _ca_upload
from tests.test_chart_analyze_hybrid import _allow, _isolated  # noqa: F401 - fixtures
from tests.test_m86_chart_reader import BASE, _reader, _user  # noqa: F401 - fixtures

ANSWER = {"conditions": [
    {"condition": "Price is above the last swing low", "status": "met", "evidence": "The last swing low held and price rose.",
     "price_low": 101.5, "price_high": 102.25},
    {"condition": "Volume rises on the up candles", "status": "unclear", "evidence": "Volume is not drawn on this chart.",
     "price_low": None, "price_high": None},
    {"condition": "A strong candle", "status": "not_met", "evidence": "Buy here, target 110.", "price_low": 0, "price_high": 5},
]}


class FakeMessages:
    def __init__(self, text: str) -> None:
        self.text, self.sent = text, []

    def create(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text=self.text)], model="fake-model",
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0))


@pytest.fixture
def fake(monkeypatch):
    msgs = FakeMessages("Here: " + json.dumps(ANSWER))
    monkeypatch.setattr(service, "make_client", lambda key: SimpleNamespace(messages=msgs))
    return msgs


def _analysed_item(who: dict) -> int:
    item_id = _ca_upload(who).json()["id"]

    async def go():
        async with _session_factory() as session:
            await session.execute(update(ChartAnalysisRecord).where(ChartAnalysisRecord.id == item_id)
                                  .values(status="done", result_json=json.dumps({"summary": "Example summary"})))
            await session.commit()
    _run(go())
    return item_id


def _result_json(item_id: int) -> str:
    async def go():
        async with _session_factory() as session:
            return (await session.get(ChartAnalysisRecord, item_id)).result_json
    return _run(go())


def test_parse_answer_guards_every_sentence_and_checks_prices():
    rows = chat.parse_answer("noise " + json.dumps(ANSWER) + " trailing")
    assert [r["status"] for r in rows] == ["met", "unclear", "not_met"]
    assert rows[0]["price_low"] == 101.5 and rows[0]["price_high"] == 102.25
    assert rows[1]["price_low"] is None
    assert rows[2]["evidence"] == chat.WITHHELD                         # order words never reach the trader
    assert rows[2]["price_low"] is None                                 # a zero price is not a price
    assert chat.parse_answer("not json") == []
    assert chat.parse_answer(json.dumps({"conditions": [{"condition": "x", "status": "maybe"}]}))[0]["status"] == "unclear"
    assert "buy" in chat.SYSTEM and "Never use the words buy" in chat.SYSTEM


def test_share_split_never_shows_the_chat_and_a_new_link_keeps_it():
    stored = share.with_chat(json.dumps({"summary": "s"}), [{"question": "q"}])
    assert share.split(stored) == ({"summary": "s"}, {})
    assert share.chat_of(stored) == [{"question": "q"}]
    linked = share.with_state(stored, {"version": 1, "active": True})
    assert share.chat_of(linked) == [{"question": "q"}] and share.split(linked)[1] == {"version": 1, "active": True}
    assert share.chat_of(share.with_chat(linked, [])) == [] and share.split(share.with_chat(linked, []))[1]["version"] == 1


def test_a_message_checks_the_conditions_on_the_owners_chart_and_is_kept(fake):
    owner, other = _user("cac-own"), _user("cac-other")
    item_id = _analysed_item(owner)
    assert client.get(f"{BASE}/items/{item_id}/chat", headers=other["headers"]).status_code == 404
    assert client.post(f"{BASE}/items/{item_id}/chat", headers=other["headers"], json={"text": "x"}).status_code == 404

    got = client.post(f"{BASE}/items/{item_id}/chat", headers=owner["headers"], json={"text": "Price above the swing low; volume rising"})
    assert got.status_code == 200, got.text
    turn = got.json()["turn"]
    assert [a["status"] for a in turn["answers"]] == ["met", "unclear", "not_met"]
    assert turn["cost_usd"] > 0 and got.json()["turns"] == 1
    sent = fake.sent[0]
    assert sent["messages"][0]["content"][0]["type"] == "image"
    assert "Price above the swing low" in sent["messages"][0]["content"][1]["text"]

    thread = client.get(f"{BASE}/items/{item_id}/chat", headers=owner["headers"]).json()
    assert len(thread["turns"]) == 1 and thread["max_turns"] == chat.TURNS_MAX
    item = client.get(f"/api/chart-analyze/items/{item_id}", headers=owner["headers"]).json()
    assert "_chat" not in json.dumps(item.get("result") or {})          # never part of the analysis result

    async def rows():
        async with _session_factory() as session:
            return list((await session.scalars(select(LlmCallRecord).where(LlmCallRecord.user_id == owner["user_id"]))).all())
    logged = _run(rows())
    assert logged and logged[-1].prompt_version == chat.PROMPT_VERSION
    assert "swing low" not in json.dumps([r.__dict__.get("prompt") for r in logged], default=str)

    assert client.delete(f"{BASE}/items/{item_id}/chat", headers=owner["headers"]).json() == {"turns": []}
    assert share.chat_of(_result_json(item_id)) == [] and share.split(_result_json(item_id))[0] == {"summary": "Example summary"}


def test_no_key_no_call_and_an_empty_message_is_refused(fake, _reader):  # noqa: F811 - the fixture by name
    owner = _user("cac-nokey")
    item_id = _analysed_item(owner)
    assert client.post(f"{BASE}/items/{item_id}/chat", headers=owner["headers"], json={"text": "   "}).status_code == 422
    _reader["own"]["on"] = False
    assert client.post(f"{BASE}/items/{item_id}/chat", headers=owner["headers"], json={"text": "x"}).status_code == 503
    assert fake.sent == []


def test_the_thread_is_capped(fake, monkeypatch):
    owner = _user("cac-cap")
    item_id = _analysed_item(owner)
    monkeypatch.setattr(chat, "TURNS_MAX", 1)
    assert client.post(f"{BASE}/items/{item_id}/chat", headers=owner["headers"], json={"text": "one"}).status_code == 200
    capped = client.post(f"{BASE}/items/{item_id}/chat", headers=owner["headers"], json={"text": "two"})
    assert capped.status_code == 409 and len(fake.sent) == 1


def test_an_item_not_analysed_yet_has_no_chat(fake):
    owner = _user("cac-new")
    item_id = _ca_upload(owner).json()["id"]
    got = client.post(f"{BASE}/items/{item_id}/chat", headers=owner["headers"], json={"text": "x"})
    assert got.status_code == 409


def test_a_symbol_picked_analysis_is_checked_on_atps_own_chart(fake, monkeypatch):
    """No uploaded picture (a symbol-picked Advanced analysis): ATP's own chart of the stored reading is sent instead."""
    owner = _user("cac-sym")
    item_id = _analysed_item(owner)
    bars = [{"t": f"2026-10-0{1 + i % 9}T04:{i:02d}:00+00:00", "o": 100 + i, "h": 101 + i, "l": 99 + i, "c": 100.5 + i} for i in range(30)]

    async def go():
        async with _session_factory() as session:
            await session.execute(update(ChartAnalysisRecord).where(ChartAnalysisRecord.id == item_id).values(
                image_path=None, result_json=json.dumps({"reading": {"symbol": "DEMO", "timeframe": "1h", "bars": bars}, "chart": {"width": 960, "height": 540}})))
            await session.commit()
    _run(go())
    got = client.post(f"{BASE}/items/{item_id}/chat", headers=owner["headers"], json={"text": "price rising"})
    assert got.status_code == 200, got.text
    sent = fake.sent[-1]["messages"][0]["content"][0]
    assert sent["source"]["media_type"] == "image/png"
