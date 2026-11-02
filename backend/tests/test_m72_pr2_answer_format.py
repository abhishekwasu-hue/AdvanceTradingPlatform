"""M72 PR-2 (CP-2, finding F7): the planner's answer format - a checked one-line headline, short lines, cards that code
draws from the tool results the model names (never a model-written table) and fixed follow-up chips.
No test calls a real provider (scripted models only)."""
import ast
import json
from pathlib import Path

import pytest

from app.ai import agent, answer_cards, grounding
from tests.test_auth_api import client  # noqa: F401 - fixture
from tests.test_m72_pr1a_safety import _org
from tests.test_m72_pr1c_planner import ORDER_FUNCTIONS, ORDER_MODULES, Scripted, _agent, _flag, _provider, _run, _seed_own_data, planner_on  # noqa: F401
from tests.test_phase_l_ai import _owner


def _final(text, headline="", cards=(), chips=(), claims=()):
    return {"text": json.dumps({"headline": headline, "text": text, "claims": list(claims), "cards": list(cards), "chips": list(chips),
                                "disclaimers": []})}


def _on(tenant_id):
    _run(_flag("copilot_agent", False, [tenant_id]))


# --- the card builder -----------------------------------------------------------------------------------------------------

def test_a_card_is_drawn_from_its_own_calls_result_only():
    calls = [{"id": "c1", "name": "get_my_orders", "ok": True, "arguments": {}},
             {"id": "c2", "name": "get_news", "ok": True, "arguments": {}},
             {"id": "c3", "name": "get_quote", "ok": False, "arguments": {"symbol": "SBIN"}}]
    results = {"c1": {"waiting": [{"id": 7, "symbol": "AAA", "price": 101.5, "quantity": 10, "client_ref": "r-1"},
                                  {"id": 8, "symbol": "BBB", "price": 99.25, "quantity": 5, "client_ref": "r-2"}], "count": 2},
               "c2": {"items": [{"title": "headline 123"}]}}
    cards = answer_cards.build_cards([{"call": "c1", "title": "Your orders"}, {"call": "c2"}, {"call": "c3"}, {"call": "c9"}, {"call": "c1"}],
                                     calls, results, first_party={"c1"})
    assert len(cards) == 1                          # news (third-party), a failed call, an unknown id and a repeat: no card
    table = cards[0]["table"]
    assert table["title"] == "Your orders"
    keys = [c["key"] for c in table["columns"]]
    assert "id" not in keys and keys[:3] == ["symbol", "price", "quantity"]
    allowed = grounding.numbers_in_values(results["c1"])
    for number in answer_cards.card_numbers(cards[0]):
        assert grounding.numbers_in_values([number]) & allowed, number
    assert [c["label"] for c in table["columns"]][:2] == ["Symbol", "Price"]
    kinds = {c["key"]: c["kind"] for c in table["columns"]}
    assert kinds["price"] == "ratio" and kinds["quantity"] == "number" and kinds["symbol"] == "text"   # 101.5 is never shown as 102


def test_a_title_with_a_figure_is_replaced_and_fields_become_pairs():
    calls = [{"id": "c1", "name": "get_risk_limits", "ok": True, "arguments": {}}]
    cards = answer_cards.build_cards([{"call": "c1", "title": "Risk 2% of 500000"}], calls,
                                     {"c1": {"risk_per_trade_pct": 1.0, "max_open_positions": 3, "user_id": 4, "nested": {"a": 1}}}, {"c1"})
    table = cards[0]["table"]
    assert table["title"] == "Your risk settings"
    assert table["rows"] == [{"item": "Risk per trade %", "value": 1.0}, {"item": "Max open positions", "value": 3}]


def test_at_most_three_cards_and_bounded_tables():
    calls = [{"id": f"c{i}", "name": "get_my_orders", "ok": True, "arguments": {}} for i in range(5)]
    rows = [{f"k{j}": j for j in range(10)} for _ in range(30)]
    cards = answer_cards.build_cards([{"call": c["id"]} for c in calls], calls, {c["id"]: {"rows": rows} for c in calls}, {c["id"] for c in calls})
    assert len(cards) == answer_cards.MAX_CARDS
    assert len(cards[0]["table"]["rows"]) == answer_cards.MAX_ROWS and len(cards[0]["table"]["columns"]) == answer_cards.MAX_COLUMNS


def test_chips_are_fixed_actions_and_need_a_symbol_a_tool_read():
    read = [{"id": "c1", "name": "get_chart_structure", "ok": True, "arguments": {"symbol": "nifty"}}]
    chips = answer_cards.build_chips(["chart", "terminal", "alert", "backtest", "chart", "buy_now"], read)
    assert [c["label"] for c in chips] == ["Show on chart", "Open in Terminal", "Make this an alert", "Backtest this"]
    assert chips[0]["symbol"] == "NIFTY" and chips[2] == {"kind": "ask", "label": "Make this an alert", "question": "Make an alert for NIFTY"}
    assert answer_cards.build_chips(["chart", "alert"], [{"id": "c1", "name": "get_my_orders", "ok": True, "arguments": {}}]) == []


# --- the planner loop -----------------------------------------------------------------------------------------------------

def test_the_planner_answer_carries_a_checked_headline_cards_and_chips():
    tenant_id, a, _b = _org("pr2-loop")
    _seed_own_data(tenant_id, a, "EEE")
    _on(tenant_id)
    try:
        good = Scripted([{"calls": [{"tool": "get_my_orders"}]},
                         _final("It is a limit order.", headline="EEESTOCK waits at 101.5", cards=[{"call": "c1_0", "title": "Waiting"}],
                                chips=["terminal"])])
        ans = _agent(a, good, "Which orders are waiting?")
        assert ans.source == "ai" and ans.headline == "EEESTOCK waits at 101.5"
        assert len(ans.cards) == 1 and ans.cards[0]["table"]["title"] == "Waiting"
        assert ans.chips == []                      # no tool read a symbol argument: no symbol chip
        # a headline figure no tool returned: one rewrite, then the data summary
        bad = Scripted([{"calls": [{"tool": "get_my_orders"}]}, _final("Limit order.", headline="EEESTOCK waits 7.5 below"),
                        _final("Limit order.", headline="EEESTOCK waits 7.5 below")])
        ans = _agent(a, bad, "Which orders are waiting?")
        assert ans.source == "summary" and ans.stopped == "not_grounded"
    finally:
        _run(_flag("copilot_agent", False))


def test_the_prompt_asks_for_cards_not_tables():
    assert '"cards"' in agent.SYSTEM and '"headline"' in agent.SYSTEM and '"chips"' in agent.SYSTEM
    assert "the platform draws its table" in agent.SYSTEM
    p = agent.parse_answer(json.dumps({"text": "x", "headline": "h", "cards": [{"call": "c1"}, "junk"], "chips": ["chart", 3]}))
    assert (p.headline, p.cards, p.chips) == ("h", [{"call": "c1", "title": ""}], ["chart"])
    plain = agent.parse_answer("just words")
    assert plain.contract == "plain" and plain.cards == [] and plain.chips == []


# --- the chat route -------------------------------------------------------------------------------------------------------

@pytest.mark.usefixtures("planner_on")
def test_the_chat_shows_headline_first_cards_as_tables_and_chips(monkeypatch):
    headers, me = _owner("pr2-chat@example.com")
    _seed_own_data(me["tenant_id"], me["id"], "FFF")
    model = Scripted([{"calls": [{"tool": "get_my_orders"}]},
                      _final("It waits as a limit order.\nNothing else is waiting.", headline="FFFSTOCK waits at 101.5",
                             cards=[{"call": "c1_0", "title": "Waiting orders"}], chips=["chart"])])
    _provider(monkeypatch, model)
    body = client.post("/api/ai/chat", headers=headers, json={"message": "Which orders are waiting?"}).json()
    assert body["source"] == "ai" and body["summary"] == ["FFFSTOCK waits at 101.5", "It waits as a limit order.", "Nothing else is waiting."]
    assert body["headline_line"] == "FFFSTOCK waits at 101.5"
    assert [b["type"] for b in body["blocks"]] == ["table"] and body["blocks"][0]["table"]["title"] == "Waiting orders"
    assert "call" not in body["blocks"][0]                       # the call id never reaches the page
    assert body["chips"] == []
    text = json.dumps(body["blocks"])
    assert "client_ref" not in text and '"id"' not in text


def test_the_card_module_imports_no_order_function():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "app" / "ai" / "answer_cards.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(ORDER_MODULES) and not {a.name for a in node.names} & ORDER_FUNCTIONS
