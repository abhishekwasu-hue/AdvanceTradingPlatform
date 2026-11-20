"""CP-CHAT (owner 20 Nov, "AI connected astana proper chating hone apeskhit aahe"): with an AI key the Copilot replies as a
chat - the question answered first in short paragraphs, no fixed Summary / What I need / Next step sections, one short
question when something is missing - and a starter that needs his idea fills the box instead of being sent. Every safety
rule of the planner prompt stays word for word."""
from app.ai import agent
from app.ai import copilot_chat as chat
from app.ai import languages as L
from app.ai.chat_routes import chat_parts

SAFETY_LINES = (
    "1. Every number you state must appear in a tool result or in the user's message.",
    "Never tell the user to buy, sell, enter or exit, and never give price targets.",
    "A Trade setting shown as \"Not set\" has no value: say it is not set and ask the user; never assume or suggest one.",
    "Describe an area of interest by its side and\n   price range and its state; no stop, target or invalidation words.",
    "A propose_* tool, when offered, only creates a proposal that a person must approve.",
    "Each claim's numbers must appear in the result of the tool call it names.",
    "never compute it yourself.",
)


def test_the_chat_register_has_no_fixed_sections():
    chat_prompt = L.with_tone(agent.SYSTEM, "analyst")
    assert "Organise the text into short sections" not in chat_prompt
    assert "'## <title>'" not in chat_prompt and "Summary first" not in chat_prompt
    assert "answer the question first" in chat_prompt
    assert "No section headings" in chat_prompt
    assert "ask for it in one short question on its own line" in chat_prompt
    assert "short paragraphs separated by a blank line" in chat_prompt and L._TEXT_FIELD not in chat_prompt


def test_every_safety_rule_stays_in_both_registers_and_languages():
    for lang in ("en", "mr", "hi", "auto_dev"):
        for tone in ("analyst", "plain"):
            prompt = L.with_tone(L.system_for(agent.SYSTEM, lang), tone)
            for line in SAFETY_LINES:
                assert line in prompt, (lang, tone, line)
    for word in (" buy", " sell", "target", "stop-loss", "entry", "exit"):
        assert word not in L._ANALYST.lower()


def test_the_plain_register_is_unchanged():
    assert L.with_tone(agent.SYSTEM, "plain") == agent.SYSTEM


def test_a_reply_in_paragraphs_keeps_its_paragraphs_and_its_list():
    text = "NIFTY reads up on the daily chart.\nIt holds above the last swing low.\n\n- Demand area below\n- Supply area above\n\nWhich timeframe do you trade?"
    assert chat_parts(text) == ["NIFTY reads up on the daily chart.\nIt holds above the last swing low.", "- Demand area below\n- Supply area above",
                                "Which timeframe do you trade?"]


def test_a_reply_without_blank_lines_keeps_one_part_per_line():
    assert chat_parts("one\ntwo") == ["one", "two"]
    assert chat_parts("") == []


def test_only_the_jobs_that_need_his_idea_fill_the_box():
    filled = {j.id: j.prefill for j in chat.JOBS if j.prefill}
    assert filled == {"trade_check": "Check my trade idea: ", "strategy": "Turn my idea into a strategy and test it: "}
    for job in chat.JOBS:
        if job.prefill:
            assert job.prefill.rstrip(": ") == job.starter        # the box starts with the button's own words


def test_a_prefill_completed_by_him_still_reaches_its_job():
    assert chat.route("Check my trade idea: NIFTY 24500 CE, 2 lots, my risk 3000") == "trade_check"
    assert chat.route("Turn my idea into a strategy and test it: on the 15-minute chart, 20 EMA above 50 EMA") == "strategy"
