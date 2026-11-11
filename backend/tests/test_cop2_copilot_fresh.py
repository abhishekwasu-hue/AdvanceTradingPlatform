"""COP-2 (owner 11 Nov, "Default yes"): the Copilot shows only up-to-date answers, "Read chart" for any symbol, one trend
word.

A. `GET /api/ai/chat/history` marks each turn `stale` when its chart answer's close is older than the newest Daily close
   now stored for that symbol (`own_bars.official_close`); the page then opens on a fresh chat (the conversation stays
   in the list and opens normally from there - the frontend tests hold that part).
B. `named_symbol` reads a known instrument written in lower or mixed case ("read the reliance chart"), never an ordinary
   word as a symbol.
C. The answer headline's trend is the structure card's word for every direction the layer can give ("up (weak)" is up,
   weak), "not known" only for none / empty / short history.
"""
import asyncio
import json
from datetime import date

import pytest
from sqlalchemy import delete

from app.ai import chat_history, instrument_names
from app.ai import copilot_chat as chat
from app.ai.tools import ToolResult
from app.charts.price_action_concepts import STATE_DIRECTION
from app.db.models import CopilotChatTurnRecord, MdCandleRecord
from app.market_lake import index_history as dh
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_copilot_v2_chat import recorded_tools, v2_on  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

SYMBOL = "COPTWOX"
KEY = "NSE:COPTWOX"


def _run(coro):
    return asyncio.run(coro)


def _chart_answer(as_of: str) -> dict:
    return {"job": "chart", "summary": [f"{SYMBOL} closed."],
            "headline": {"symbol": SYMBOL, "price": 100.0, "price_label": "Last close", "as_of": as_of, "trend": "up", "weak": False,
                         "timeframe": "Daily", "area": None, "chart": None}}


def _closes(*days: date) -> None:
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == KEY))
            session.add_all([MdCandleRecord(instrument_key=KEY, ts=dh.session_close(d), version=1, open=99, high=101, low=98, close=100,
                                            volume=0, timeframe="day", source="nse_bhav:equity") for d in days])
            await session.commit()
    _run(go())


def _store(me: dict, answers: list[tuple[str, dict]]) -> None:
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(CopilotChatTurnRecord).where(CopilotChatTurnRecord.user_id == me["id"]))
            for question, answer in answers:
                session.add(CopilotChatTurnRecord(tenant_id=me["tenant_id"], user_id=me["id"], question=question, answer_json=json.dumps(answer)))
            await session.commit()
    _run(go())


def _history(headers, **params):
    r = client.get("/api/ai/chat/history", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


# --- A: an answer a newer stored close has overtaken is marked stale -------------------------------------------------

def test_a_chart_answer_is_current_while_no_newer_close_is_stored(v2_on, recorded_tools):  # noqa: F811 - fixtures
    headers, me = _owner("cop2-current@example.com")
    _closes(date(2026, 11, 9), date(2026, 11, 10))
    _store(me, [("What is RSI?", {"job": "teacher", "summary": ["RSI is ..."]}), (f"Read the {SYMBOL} chart", _chart_answer("2026-11-10"))])
    body = _history(headers)
    assert [t["stale"] for t in body["turns"]] == [False, False]


def test_a_newer_stored_close_makes_the_chart_answer_stale(v2_on, recorded_tools):  # noqa: F811 - fixtures
    headers, me = _owner("cop2-stale@example.com")
    _closes(date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11))
    _store(me, [(f"Read the {SYMBOL} chart", _chart_answer("2026-11-09")), (f"Read the {SYMBOL} chart", _chart_answer("2026-11-11"))])
    body = _history(headers)
    assert [t["stale"] for t in body["turns"]] == [True, False]
    # opened from the Conversations list: the same turns come back, as stored (the page shows them)
    again = _history(headers, session_id=body["session"]["id"])
    assert [t["answer"]["headline"]["as_of"] for t in again["turns"]] == ["2026-11-09", "2026-11-11"]
    assert [t["stale"] for t in again["turns"]] == [True, False]


def test_no_stored_close_or_no_chart_answer_is_never_stale(v2_on, recorded_tools):  # noqa: F811 - fixtures
    headers, me = _owner("cop2-none@example.com")
    _closes()
    _store(me, [(f"Read the {SYMBOL} chart", _chart_answer("2026-11-09")), ("hello", {"job": "help", "summary": ["Hi"]})])
    assert [t["stale"] for t in _history(headers)["turns"]] == [False, False]


def test_mark_stale_reads_one_close_per_symbol(monkeypatch):
    from app.market_lake import own_bars
    calls: list[str] = []

    async def close(session, key, scope):  # noqa: ANN001, ANN202 - the official_close signature
        calls.append(key)
        return {"close": 1.0, "date": "2026-11-10", "source": "x"}

    async def scope(session, user, key):  # noqa: ANN001, ANN202
        return None
    monkeypatch.setattr(own_bars, "official_close", close)
    monkeypatch.setattr(own_bars, "scope_for", scope)
    turns = [{"answer": _chart_answer("2026-11-09")}, {"answer": _chart_answer("2026-11-10")}, {"answer": None},
             {"answer": {"headline": {"symbol": SYMBOL, "as_of": None}}}]
    _run(chat_history.mark_stale(None, None, turns))
    assert [t["stale"] for t in turns] == [True, False, False, False] and calls == [KEY]


# --- B: a symbol written in any case ---------------------------------------------------------------------------------

def test_a_lowercase_known_symbol_is_read():
    assert chat.named_symbol("read the reliance chart") == "RELIANCE"
    assert chat.named_symbol("Read the Reliance chart") == "RELIANCE"
    assert chat.named_symbol("Read the RELIANCE chart") == "RELIANCE"
    assert chat.named_symbol("read the bank nifty chart") == "BANKNIFTY"


def test_a_question_without_a_symbol_names_none(monkeypatch):
    for text in ("read the chart", "please read this", "Read the chart", "what does the price show on the daily chart today",
                 "teach me: what is a demand zone?", "how are my open positions?"):
        assert chat.named_symbol(text) is None, text
    assert chat.symbol_in("read the chart") == chat.DEFAULT_SYMBOL
    token = chat.CONTEXT_SYMBOL.set("TCS")
    try:
        assert chat.symbol_in("read the chart") == "TCS"
    finally:
        chat.CONTEXT_SYMBOL.reset(token)


def test_the_master_adds_symbols_in_any_case_but_never_ordinary_words(monkeypatch):
    monkeypatch.setattr(instrument_names, "_SYMBOLS", dict(instrument_names._SYMBOLS))   # noqa: SLF001
    instrument_names.add_symbols([("TATAMOTORS", "Tata Motors Limited"), ("POWER", "Power Ltd"), ("ABC", "Abc")])
    assert chat.named_symbol("read the tata motors chart") == "TATAMOTORS"
    assert chat.named_symbol("read the tatamotors chart") == "TATAMOTORS"
    assert chat.named_symbol("read the power chart") is None          # an ordinary word
    assert chat.named_symbol("read the abc chart") is None            # too short to be read in lower case
    assert chat.named_symbol("read the ABC chart") == "ABC"           # the upper-case ticker rule as before


def test_the_master_is_read_into_the_symbol_index(monkeypatch):
    from app.db.models import SecurityRecord
    monkeypatch.setattr(instrument_names, "_SYMBOLS", dict(instrument_names._SYMBOLS))   # noqa: SLF001
    monkeypatch.setattr(instrument_names, "_LAST_SYMBOLS", {"at": 0.0})                  # noqa: SLF001

    async def go():
        async with _session_factory() as session:
            await session.execute(delete(SecurityRecord).where(SecurityRecord.isin == "INE000COP201"))
            session.add(SecurityRecord(isin="INE000COP201", symbol="COPTWOSTOCK", name="Coptwo Widgets Limited", series="EQ", source="test", checksum="x"))
            await session.commit()
            added = await instrument_names.refresh_symbols_if_stale(session)
            again = await instrument_names.refresh_symbols_if_stale(session)
            await session.execute(delete(SecurityRecord).where(SecurityRecord.isin == "INE000COP201"))
            await session.commit()
        return added, again
    added, again = _run(go())
    assert added >= 2 and again == 0
    assert chat.named_symbol("read the coptwo widgets chart") == "COPTWOSTOCK"
    assert instrument_names.known("coptwo widgets") is False          # the output filter's own index is unchanged


# --- C: one trend word -----------------------------------------------------------------------------------------------

def _res(direction: str | None, short: bool = False) -> ToolResult:
    row = {"tf": "1d", "direction": direction, "short": short, "protected": None}
    return ToolResult(True, {"last_close": 100.0, "official_close": {"close": 100.0, "date": "2026-11-10"},
                             "chart_state": {"direction": direction}, "timeframes": [row], "levels": []}, None, "x")


NO_ZONES = ToolResult(False, None, None, "market_zones", error="not read")
EXPECTED = {"up": ("up", False), "up (weak)": ("up", True), "down": ("down", False), "down (weak)": ("down", True),
            "range": ("sideways", False), "none": (None, False)}


@pytest.mark.parametrize("direction", sorted(set(STATE_DIRECTION.values())))
def test_the_headline_says_the_cards_trend_word(direction):
    res = _res(direction)
    head = chat.chart_headline("NIFTY", "1d", res, NO_ZONES)
    card = chat.structure_card("NIFTY", "1d", res, NO_ZONES)
    row = next(r for r in card["timeframes"] if r["tf"] == "1d")
    assert (head["trend"], head["weak"]) == EXPECTED[direction]
    assert (head["trend"], head["weak"]) == (row["trend"], row["weak"] and row["trend"] is not None)


def test_the_headline_trend_is_not_known_only_for_empty_or_short_history():
    assert chat.chart_headline("NIFTY", "1d", _res(None), NO_ZONES)["trend"] is None
    assert chat.chart_headline("NIFTY", "1d", _res(""), NO_ZONES)["trend"] is None
    short = chat.chart_headline("NIFTY", "1d", _res("up", short=True), NO_ZONES)
    assert short["trend"] is None and short["weak"] is False
