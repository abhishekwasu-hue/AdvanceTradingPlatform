"""H-C1 c: the server-side advice/guarantee filter. ATP answers in English: AI text with Devanagari is withheld outright,
and the word lists are English only (the owner's ruling removed the Marathi / Hindi lists). Devanagari test text is
written with \\u escapes (no Devanagari in the source - scripts/check_english_only.py)."""
import asyncio
import json

import pytest

from app.ai import copilot, knowledge, output_filter, thesis


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("text, word", [
    ("This setup is guaranteed to work.", "guaranteed"),
    ("A sure-shot breakout above the range.", "sure-shot"),
    ("Risk-free money on expiry day.", "risk-free"),
    ("I recommend buying NIFTY here.", "i recommend"),
    ("You should buy the dip.", "you should buy"),
])
def test_guarantee_and_advice_words_are_blocked(text, word):
    s = output_filter.screen(text, "en")
    assert not s.ok and s.blocked == [word]


@pytest.mark.parametrize("text", [
    "\u0939\u0940 strategy \u0916\u093e\u0924\u094d\u0930\u0940\u0936\u0940\u0930.",      # Marathi: "this strategy is guaranteed"
    "\u092a\u0915\u094d\u0915\u093e \u092e\u0941\u0928\u093e\u092b\u093e.",               # Hindi: "sure profit"
    "\u0939\u093e \u0938\u0932\u094d\u0932\u093e \u0928\u093e\u0939\u0940.",              # Marathi: "this is not advice"
])
def test_no_marathi_or_hindi_lists_and_ai_output_in_devanagari_is_withheld(text):
    assert output_filter.blocked_terms(text) == []                   # no per-language word lists any more
    ok, shown, why = output_filter.check(text, "mr")                 # AI output: English only, fail closed
    assert not ok and shown is None and why == output_filter.NOT_ENGLISH_REASON


def test_the_terms_file_is_english_plus_the_fixed_romanised_list():
    data = json.loads(output_filter.DEFAULT_FILE.read_text(encoding="utf-8"))
    for kind in ("guarantee", "advice"):
        assert set(data[kind]) == {"en", "romanised"}, kind               # no per-language (mr / hi) lists
    assert set(data["framing"]) == {"en"}
    assert set(data["calls"]["verbs"]) == {"en", "romanised"} and set(data["calls"]["negated_sayings"]) == {"en"} and set(data["negations"]) == {"before"}
    assert data["calls"]["light_verbs"] == ["le lo", "ghya"] and "pakka" in data["calls"]["certainty"] and "pakka" in data["calls"]["certainty_lead"]
    for phrase in ("pakka profit", "paisa double", "abhi kharido", "kharid lo", "bech do", "ye share lo", "best share hai"):
        assert output_filter.blocked_terms(f"NIFTY 50: {phrase}.") == [phrase], phrase       # read always, whatever the language
    assert {"kharido", "le lo", "ghya", "vika"} <= set(data["calls"]["verbs"]["romanised"])
    assert not any("\u0900" <= ch <= "\u097f" for ch in output_filter.DEFAULT_FILE.read_text(encoding="utf-8"))
    assert output_filter.framing("mr") == output_filter.framing("en") == data["framing"]["en"]


@pytest.mark.parametrize("text", [
    "This is not a recommendation to buy anything.",
    "Nothing here is guaranteed.",
    "It is never guaranteed and a loss can happen.",
    "NIFTY 50 trend is up; support near 24850.",
    "Bulls hold 25200.00; room to 25550.00, wrong below 24850.00.",
    "Shorting is risky.",
    "A sell-off below 24850 would weaken the read.",
    "Guaranteeing nothing, the read is mixed.",
])
def test_neutral_and_negated_text_passes_unchanged(text):
    s = output_filter.screen(text, "en")
    assert s.ok and not s.framed and s.text == text


def test_a_specific_call_is_kept_with_educational_framing():
    # (an imperative call aimed at an instrument - "Buy NIFTY 25000 CE above 120." - is now blocked, tests/test_ai_call_filter.py)
    en = output_filter.screen("One could buy the 25000 CE above 120.", "en")
    assert en.ok and en.framed and en.text.startswith("One could buy the 25000 CE") and "not investment advice" in en.text
    assert not output_filter.screen("25200 \u091a\u094d\u092f\u093e \u0935\u0930 \u0916\u0930\u0947\u0926\u0940.", "mr").ok     # Devanagari: withheld
    again = output_filter.screen(en.text, "en")                                   # framing is added once
    assert again.text.count("not investment advice") == 1


def test_the_word_list_is_data_and_can_be_replaced(tmp_path, monkeypatch):
    data = json.loads(output_filter.DEFAULT_FILE.read_text(encoding="utf-8"))
    data["guarantee"]["en"].append("moonshot")
    path = tmp_path / "terms.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("AI_ADVICE_TERMS_FILE", str(path))
    assert output_filter.blocked_terms("A moonshot setup.") == ["moonshot"]
    monkeypatch.delenv("AI_ADVICE_TERMS_FILE")
    assert output_filter.blocked_terms("A moonshot setup.") == []


class _Provider:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    async def complete(self, system, user, *, max_tokens=700):
        self.calls += 1
        self.last_user = user
        return self.answers.pop(0)


def test_copilot_retries_once_then_keeps_the_rule_text():
    # app.ai.wording (ATP review 11) runs first and already refuses guarantee / recommend / sure-shot; these inputs
    # use phrases only the H-C1 filter carries, so they reach it
    facts = ["NIFTY 50 last 25200", "trend up"]
    retry = _Provider(["You should buy now near 25200.", "NIFTY 50 is at 25200 and the trend is up."])
    text, why = _run(copilot.narrate(retry, "en", "market", "how is nifty?", facts))
    assert text == "NIFTY 50 is at 25200 and the trend is up." and retry.calls == 2 and "not allowed" in retry.last_user
    stubborn = _Provider(["25200 \u0935\u0930 \u0939\u092e\u0940.", "Buy now at 25200."])
    text, why = _run(copilot.narrate(stubborn, "mr", "market", "nifty?", facts))
    assert text is None and "advice/guarantee" in why and "English only" in stubborn.last_user
    marathi = _Provider(["NIFTY 50 25200 \u0935\u0930 \u0906\u0939\u0947.", "NIFTY 50 25200 \u0935\u0930 \u0906\u0939\u0947."])
    text, why = _run(copilot.narrate(marathi, "mr", "market", "nifty?", facts))
    assert text is None and why == output_filter.NOT_ENGLISH_REASON


def test_knowledge_answer_falls_back_to_the_library_on_blocked_words():
    out = _run(knowledge.ai_answer(_Provider(["With RSI you should buy now."]), "what is rsi", "en", None, None))
    assert out.get("source") != "ai" and "advice/guarantee" in out["note"]
    ok = _run(knowledge.ai_answer(_Provider(["RSI measures momentum."]), "what is rsi", "en", None, None))
    assert ok["source"] == "ai" and ok["answer"] == "RSI measures momentum."


def test_thesis_narrative_is_filtered():
    from tests.test_phase_bd_thesis import NOW, _snapshot
    th = thesis.compose("NIFTY 50", _snapshot(), {"sentiment": None, "globals": [], "cues": []}, [], [], "en", NOW)
    text, why = _run(thesis.narrate(_Provider(["A jackpot rally from 25200.00.", "Buy now above 25200.00."]), th, "en"))
    assert text is None and "advice/guarantee" in why


@pytest.mark.parametrize("reply", ["RELIANCE abhi kharido, pakka profit hai.", "kharid lo, 2900 se upar jayega pakka"])
def test_hinglish_advice_in_latin_script_is_withheld(reply):
    """Review of #273 (B1): romanised Hinglish advice is ASCII, so the script check does not catch it - the fixed
    romanised list does. Through the Copilot, the reply is withheld (both attempts) and the rule text is kept."""
    facts = ["RELIANCE last 2900", "trend up"]
    provider = _Provider([reply, reply])
    text, why = _run(copilot.narrate(provider, "en", "brief", "RELIANCE?", facts))
    assert text is None and "advice/guarantee" in why and provider.calls == 2
    assert not output_filter.check(reply, "en")[0]
