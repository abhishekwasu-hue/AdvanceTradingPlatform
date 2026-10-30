"""Owner order CP-UX, CU1: the Copilot reads the symbol the trader names - SENSEX and BANKEX too (BSE indices) - in every
job and in the strategy builder, and every answer names the symbol it read (said plainly when the question named none)."""
import asyncio

import pytest

from app.ai import copilot_chat as chat
from app.ai import strategy_builder as sb
from evals import runner
from tests.test_auth_api import client  # noqa: F401 - fixtures
from tests.test_copilot_v2_chat import recorded_tools, v2_on  # noqa: F401 - fixtures
from tests.test_copilot_v2_job6 import FULL
from tests.test_phase_l_ai import _owner

FIX = runner.copilot_fixtures()


def _run(coro):
    return asyncio.run(coro)


def _answer(question, fixture="default"):
    calls = []
    out = _run(chat.answer(question, runner.recorded_runner(FIX[fixture], calls)))
    return out, calls


# --- reading the symbol ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("question, symbol", [
    ("Read the SENSEX chart", "SENSEX"), ("read the sensex chart", "SENSEX"), ("What does the BSE Sensex option chain show?", "SENSEX"),
    ("Read the BANKEX chart", "BANKEX"), ("full view of bankex", "BANKEX"), ("Read the NIFTY chart", "NIFTY"),
    ("Read the Bank Nifty chart", "BANKNIFTY"), ("Read the RELIANCE chart", "RELIANCE"), ("Read the BSE:SENSEX chart", "SENSEX"),
])
def test_the_index_words_and_symbols_are_read(question, symbol):
    assert chat.symbol_in(question) == symbol and chat.named_symbol(question) == symbol


def test_sensex_and_bankex_are_symbols_not_common_words():
    assert "SENSEX" not in chat._NOT_SYMBOLS and "BANKEX" not in chat._NOT_SYMBOLS
    assert {"SENSEX", "BANKEX"} <= {name for name, _ in chat._INDEX_WORDS}
    assert chat.named_symbol("Read the chart") is None and chat.symbol_in("Read the chart") == chat.DEFAULT_SYMBOL


def test_the_builder_reads_sensex_and_bankex():
    assert chat.builder_symbol("Turn my idea into a strategy: go long on SENSEX when RSI 14 crosses above 50") == "SENSEX"
    assert chat.builder_symbol("make a strategy on bankex: long when the 20 EMA crosses above the 50 EMA") == "BANKEX"
    assert chat.builder_symbol("Turn my idea into a strategy: go long when RSI 14 crosses above 50") is None


def test_bse_indices_trade_on_bse():
    assert (chat.exchange_of("SENSEX"), chat.exchange_of("bankex"), chat.exchange_of("NIFTY"), chat.exchange_of("RELIANCE")) == (
        "BSE", "BSE", "NSE", "NSE")


# --- every job reads the named index ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("question, job", [
    ("Read the SENSEX chart", "chart"), ("What does the SENSEX option chain show?", "option_chain"),
    ("Check my trade idea: bullish SENSEX from the demand area, 2 lots", "trade_check"), ("Full view of SENSEX", "views"),
    ("Teach me: what is a demand zone on the SENSEX chart?", "teacher"), ("Read the BANKEX chart", "chart"),
])
def test_every_symbol_job_reads_the_named_bse_index_and_names_it(question, job):
    symbol = chat.named_symbol(question)
    out, calls = _answer(question)
    assert out.job == job, question
    read = [c["args"]["symbol"] for c in calls if "symbol" in c["args"]]
    assert read and set(read) == {symbol}, (question, read)                       # never the NIFTY default
    assert out.symbols == [symbol] and out.symbol_default is False
    assert symbol in "\n".join(out.summary), out.summary
    assert out.as_dict()["symbols"] == [symbol]


def test_no_symbol_named_says_the_default_was_read():
    for question in ("Read the chart", "What does the option chain show?"):
        out, calls = _answer(question)
        assert {c["args"]["symbol"] for c in calls if "symbol" in c["args"]} == {chat.DEFAULT_SYMBOL}
        assert out.symbol_default is True and out.as_dict()["symbol_default"] is True
        assert f"You did not name a symbol, so I read {chat.DEFAULT_SYMBOL}." in out.summary


def test_the_brief_names_both_indices_it_read():
    out, _ = _answer("Today's market brief")
    assert out.symbols[:2] == ["NIFTY", "BANKNIFTY"] and out.symbol_default is False
    text = "\n".join(out.summary)
    assert all(s in text for s in out.symbols)


def test_an_answer_that_reads_no_symbol_adds_no_line():
    out, _ = _answer("Teach me: what is position sizing?")
    assert out.symbols == [] and chat.symbol_line(out, "anything") is None
    refused = _run(chat.answer("Place a buy order for 2 lots of SENSEX", runner.recorded_runner(FIX["default"], [])))
    assert refused.symbols == [] and not any("read" in x.lower() and "SENSEX" in x for x in refused.summary)


def test_the_symbol_line_only_when_the_text_does_not_name_it():
    out = chat.Answer("chart", "Chart reader", symbols=["NIFTY"])
    assert chat.symbol_line(out, "NIFTY last closed at 24,850.") is None
    assert chat.symbol_line(out, "BANKNIFTY last closed at 52,000.") == "Symbol read: NIFTY."     # BANKNIFTY is not NIFTY
    two = chat.Answer("brief", "Pre-market brief", symbols=["NIFTY", "BANKNIFTY"])
    assert chat.symbol_line(two, "NIFTY is up.") == "Symbols read: NIFTY, BANKNIFTY."
    default = chat.Answer("chart", "Chart reader", symbols=["NIFTY"], symbol_default=True)
    assert chat.symbol_line(default, "NIFTY last closed.") == "You did not name a symbol, so I read NIFTY."


# --- the builder and its test on past data ------------------------------------------------------------------------------------

def test_the_builders_draft_on_sensex_is_tested_on_bse():
    idea = FULL.replace("on NIFTY", "on SENSEX") + " Test it on past data."
    out, calls = _answer(idea)
    assert out.job == "strategy" and out.draft["symbol"] == "SENSEX" and out.symbols == ["SENSEX"]
    sent = next(c["args"] for c in calls if c["tool"] == "backtest_draft")
    assert sent["symbol"] == "SENSEX" and sent["exchange"] == "BSE"
    nifty = next(c["args"] for c in _answer(FULL + " Test it on past data.")[1] if c["tool"] == "backtest_draft")
    assert "exchange" not in nifty                                                # NSE stays the tool's default
    assert sb.build(idea, "SENSEX").symbol == "SENSEX"


# --- the route: the AI wording names the symbol too ---------------------------------------------------------------------------

def test_the_ai_wording_gets_the_symbol_line_when_it_leaves_the_symbol_out(v2_on, recorded_tools, monkeypatch):  # noqa: F811
    headers, _ = _owner("cu1-ai@example.com")
    from app.ai import chat_routes

    class Writer(runner.ScriptedWriter):
        name = "anthropic"

    async def provider(session, tenant_id, user_id):
        return Writer("The daily protected level is 24,310.")
    monkeypatch.setattr(chat_routes, "_provider", provider)
    body = client.post("/api/ai/chat", headers=headers, json={"message": "Read the chart"}).json()
    assert body["source"] == "ai" and body["symbols"] == ["NIFTY"] and body["symbol_default"] is True
    assert body["summary"] == ["The daily protected level is 24,310.", "You did not name a symbol, so I read NIFTY."]
