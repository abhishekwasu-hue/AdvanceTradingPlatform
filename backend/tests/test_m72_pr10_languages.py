"""M72 PR-10 (CP-10, F4 / F16 / F17; M72Q-2): the Copilot planner answers in the user's own language (Marathi / Hindi) -
numbers and symbols unchanged, the Marathi / Hindi advice and order phrases refused, other scripts still withheld; the
choice is read only while `copilot_languages` is on; Marathi / Hindi phrases may ask for a proposal only in that language
and never from third-party text. English stays the default and its prompt version is unchanged. Scripted models only."""
from app.ai import agent, grounding, languages, output_filter
from app.ai.tools import proposals
from app.db.models import User
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixture
from tests.test_m72_pr1c_planner import _flag, _run
from tests.test_m72_pr5_chart_read import Recording, _agent, _final
from tests.test_ui2_4a1_prefs import _who

MR = "NIFTY 50 \u091a\u093e \u0936\u0947\u0935\u091f\u091a\u093e \u092d\u093e\u0935 24850.5 \u0906\u0939\u0947 \u0906\u0923\u093f \u0924\u094b \u0906\u0927\u0940\u091a\u094d\u092f\u093e \u0926\u093f\u0935\u0938\u093e\u092a\u0947\u0915\u094d\u0937\u093e 118.4 \u0928\u0947 \u0935\u0930 \u0906\u0939\u0947."
HI = "NIFTY 50 \u0915\u093e \u0906\u0916\u093f\u0930\u0940 \u092d\u093e\u0935 24850.5 \u0939\u0948\u0964"


def test_the_prompt_per_language_and_english_unchanged():
    assert languages.system_for(agent.SYSTEM, "en") == agent.SYSTEM
    mr = languages.system_for(agent.SYSTEM, "mr")
    assert "Answer in Marathi (Devanagari script)" in mr and "digits 0-9" in mr and "English only" not in mr
    assert languages.system_for(agent.SYSTEM, "xx") == agent.SYSTEM
    assert agent.PROMPT_VERSION == "agent-f93c1d6b"                      # the English planner prompt (and its baseline) unchanged


def test_the_answer_check_per_language():
    def check(text, lang):
        return output_filter.check(text, lang, answer_language=True)
    assert check(MR, "mr")[:2] == (True, MR) and check(HI, "hi")[0]
    assert check(MR, "en")[0] is False                                   # English answers stay English only
    assert output_filter.check(MR, "mr")[0] is False                    # every other caller: English only, as before
    ok, _shown, why = check("NIFTY 50 \u091a\u093e \u092d\u093e\u0935 \u0968\u096a\u096e\u096b\u0966 \u0906\u0939\u0947.", "mr")
    assert not ok and why == output_filter.DIGITS_REASON                 # a Devanagari digit: refused (F16)
    assert check("NIFTY 50 \u091a\u093e \u092d\u093e\u0935 24850 \u0906\u0939\u0947. Buy RELIANCE now.", "mr")[0] is False
    assert check("NIFTY 50 \u0447a \u092d\u093e\u0935 24850 \u0906\u0939\u0947.", "mr")[2] == output_filter.NOT_ENGLISH_REASON         # Cyrillic: withheld
    assert check("NIFTY 50 \u091a\u093e \u092d\u093e\u0935 24850 \u0906\u0939\u0947.\u200b", "mr")[0] is False                                    # a hidden character
    nums = grounding.allowed_from_text("NIFTY 50 24850.5 118.4")
    assert grounding.answer_problem(MR, numbers=nums, trusted_text="NIFTY 50", direction_text="") is None
    assert "advice" in (grounding.answer_problem("RELIANCE \u0935\u093f\u0915\u0924 \u0918\u094d\u092f\u093e.", numbers=set(), trusted_text="RELIANCE", direction_text="") or "")
    assert grounding.answer_problem("NIFTY 50 \u091a\u093e \u092d\u093e\u0935 2 \u0932\u093e\u0916 \u0906\u0939\u0947.", numbers=nums, trusted_text="NIFTY 50", direction_text="")   # 2 not read


def test_indic_proposal_phrases_only_in_that_language_and_only_his_own_words():
    q = "NIFTY 50 24000 \u091a\u094d\u092f\u093e \u0916\u093e\u0932\u0940 \u0917\u0947\u0932\u093e \u0924\u0930 \u092e\u0932\u093e \u0915\u0933\u0935"
    assert proposals.allowed_for(q, "en") == [] and proposals.allowed_for(q, "mr") == ["propose_alert"]
    assert proposals.allowed_for("Alert me when NIFTY falls", "mr") == ["propose_alert"]        # English still works
    allowed = proposals.allowed_for(q, "mr")
    assert proposals.guard("propose_alert", {"quote": "\u092e\u0932\u093e \u0915\u0933\u0935", "reason": "x"}, q, allowed, [], 0, 1) is None
    assert proposals.guard("propose_alert", {"quote": "\u092e\u0932\u093e \u0915\u0933\u0935", "reason": "x"}, q, allowed, ["news: \u092e\u0932\u093e \u0915\u0933\u0935"], 0, 1)   # injection
    assert proposals.guard("propose_alert", {"quote": "\u092a\u093e\u0939\u093f\u091c\u0947", "reason": "x"}, q, allowed, [], 0, 1)                 # not his words


def _lang(who):
    async def go():
        async with _session_factory() as session:
            return await languages.answer_language(session, await session.get(User, who["user_id"]))
    return _run(go())


def test_his_choice_counts_only_with_the_flag_and_the_planner_answers_in_it():
    who = _who("pr10-lang")
    assert client.put("/api/me/prefs/ai", headers=who["headers"], json={"language": "mr", "voice": True}).status_code == 200
    assert client.put("/api/me/prefs/ai", headers=who["headers"], json={"language": "fr"}).status_code == 422
    _run(_flag("copilot_languages", False))
    assert _lang(who) == "en"
    _run(_flag("copilot_languages", False, [who["tenant_id"]]))
    try:
        assert _lang(who) == "mr"
        model = Recording([_final("NIFTY \u091a\u093e \u092d\u093e\u0935 \u0968\u096a\u096e\u096b\u0966 \u0906\u0939\u0947.", "NIFTY"), _final("\u092e\u093e\u091d\u094d\u092f\u093e\u0915\u0921\u0947 \u0906\u0924\u094d\u0924\u093e \u092f\u093e \u092a\u094d\u0930\u0936\u094d\u0928\u093e\u0938\u093e\u0920\u0940 \u0906\u0915\u0921\u0947 \u0928\u093e\u0939\u0940\u0924.", "\u0909\u0924\u094d\u0924\u0930")])

        async def go():
            from app.ai.tools import ToolContext
            async with _session_factory() as session:
                user = await session.get(User, who["user_id"])
                return await agent.run_agent(model, ToolContext(session, user.tenant_id, user), "NIFTY \u092c\u0926\u094d\u0926\u0932 \u0938\u093e\u0902\u0917", lang="mr")
        ans = _run(go())
        assert ans.source == "ai", ans.note
        assert "Answer in Marathi" in model.sent[0] and "Your answer cannot be shown" in model.sent[-1]       # Devanagari digits: refused
        assert "\u0968" not in ans.text
    finally:
        _run(_flag("copilot_languages", False))


def test_english_planner_unchanged_when_off():
    who = _who("pr10-en")
    model = Recording([_final("NIFTY 50 \u091a\u093e \u092d\u093e\u0935 24850 \u0906\u0939\u0947.", "NIFTY 50"), _final("I have no numbers for this question right now.", "Answer")])
    ans = _agent(who["user_id"], model, "Tell me about NIFTY")
    assert ans.source == "ai" and ans.text.startswith("I have no numbers") and "English only" in model.sent[0]
