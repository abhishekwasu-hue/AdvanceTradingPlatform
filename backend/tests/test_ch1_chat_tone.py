"""CH-1 (owner 5 Nov, "Default yes"): the Copilot chat answers in an experienced analyst's register by default and, with
the answer language "auto", in the language the question is written in (Marathi / Hindi in Devanagari, else English).
Rule 4 of the planner prompt (never buy / sell / enter / exit, no targets) is untouched by either."""
from app.ai import agent
from app.ai import languages as L
from app.me.prefs import AiPrefs


def test_auto_answers_in_the_questions_language():
    assert L.resolve("auto", "NIFTY \u091a\u0940 \u0930\u091a\u0928\u093e \u0915\u093e\u092f \u0906\u0939\u0947?") == "auto_dev"
    assert L.resolve("auto", "What is the NIFTY structure?") == "en"
    assert L.resolve("auto", "nifty chi rachna kay") == "en"              # romanised Marathi: English answer
    assert L.resolve("mr", "anything") == "mr" and L.resolve("bad", "x") == "en"
    assert L.filter_lang("auto_dev") == "mr" and L.filter_lang("en") == "en"
    dev = L.system_for(agent.SYSTEM, "auto_dev")
    assert "Answer in the language of the user's question - Marathi or Hindi" in dev and L.ENGLISH_LINE not in dev
    assert L.system_for(agent.SYSTEM, "en") == agent.SYSTEM


def test_the_analyst_register_replaces_only_the_plain_line_and_keeps_rule_4():
    analyst = L.with_tone(agent.SYSTEM, "analyst")
    assert "Write like an experienced market analyst" in analyst and "Keep the answer short and plain." not in analyst
    assert "Never tell the user to buy, sell, enter or exit, and never give price targets." in analyst
    assert "Answer in English only" in analyst
    assert L.with_tone(agent.SYSTEM, "plain") == agent.SYSTEM
    both = L.with_tone(L.system_for(agent.SYSTEM, "auto_dev"), "analyst")
    assert "experienced market analyst" in both and "Marathi or Hindi" in both
    for word in (" buy", " sell", "target", "stop-loss"):
        assert word not in L._ANALYST.lower()


def test_prefs_defaults_are_auto_and_analyst():
    p = AiPrefs()
    assert p.language == "auto" and p.tone == "analyst" and p.voice is False
    assert AiPrefs(language="en", tone="plain").tone == "plain"
