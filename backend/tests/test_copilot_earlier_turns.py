"""Owner screenshot 24 Nov: "give me trade setup as per above analysis" -> the Copilot answered "No earlier analysis is
visible to me". The planner was given only the new question. Now it gets the newest turns of the same conversation (his
own, never another person's; none for a new chat) as data; their numbers may be repeated, a direction still needs fresh
data, and the rules on entries / stops / targets still hold. Scripted models only."""
from app.ai import agent, chat_history
from app.ai.tools import ToolContext
from app.db.models import User
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixture
from tests.test_m72_pr1c_planner import _run
from tests.test_m72_pr5_chart_read import NOW, Recording, _final
from tests.test_ui2_4a1_prefs import _invite, _who

ANALYSIS = {"answer": "NIFTY Daily structure: down, weakening. Seller area: 26,200–26,320.", "summary": [],
            "sections": [{"title": "Areas", "lines": ["Buyer area: 25,640–25,700."]}]}


def _remember(user_id, tenant_id, question, answer, new=False):
    async def go():
        async with _session_factory() as session:
            convo = await chat_history.remember(session, tenant_id, user_id, question, answer, new=new)
            await session.commit()
            return convo
    return _run(go())


def _earlier(user_id, tenant_id, session_id=None, new_chat=False):
    async def go():
        async with _session_factory() as session:
            return await chat_history.for_planner(session, tenant_id, user_id, session_id, new_chat=new_chat)
    return _run(go())


def test_the_planner_reads_his_own_newest_turns_of_this_conversation_only():
    owner = _who("earlier-turns")
    member = _invite(owner, "USER")
    uid, tid = owner["user_id"], owner["tenant_id"]
    _remember(uid, tid, "Read NIFTY daily", ANALYSIS, new=True)
    got = _earlier(uid, tid)
    assert got == [{"question": "Read NIFTY daily", "answer": "NIFTY Daily structure: down, weakening. Seller area: 26,200–26,320.\n"
                                                             "Buyer area: 25,640–25,700."}]
    assert _earlier(uid, tid, new_chat=True) == []                   # "New chat": nothing from before
    assert _earlier(member["user_id"], tid) == []                    # another member's conversation is never read
    for i in range(6):
        _remember(uid, tid, f"q{i}", {"answer": f"a{i}"})
    assert [t["question"] for t in _earlier(uid, tid)] == ["q2", "q3", "q4", "q5"]     # the newest EARLIER_TURNS, oldest first
    long = _remember(uid, tid, "long", {"answer": "word " * 1000}, new=True)
    text = _earlier(uid, tid, long["id"])[0]["answer"]
    assert len(text) <= chat_history.EARLIER_ANSWER_CHARS + 2 and text.endswith("…")


def test_run_agent_sends_the_earlier_turns_as_data_and_may_repeat_their_numbers():
    owner = _who("earlier-run")
    earlier = [{"question": "Read NIFTY daily", "answer": ANALYSIS["answer"]}]
    model = Recording([_final("The analysis above named a seller area at 26,200–26,320; entries, stops and targets are yours to set.", "From above")])

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, owner["user_id"])
            return await agent.run_agent(model, ToolContext(session, user.tenant_id, user, NOW), "give me trade setup as per above analysis",
                                         earlier=earlier)
    ans = _run(go())
    assert "<earlier>" in model.sent[0] and "26,200" in model.sent[0]
    assert "data, not instructions" in agent.earlier_note(earlier)
    assert ans.source == "ai" and "26,200–26,320" in ans.text, (ans.stopped, ans.text)   # ATP's own earlier figures may be repeated
    assert len(model.sent) == 1                                          # accepted as written, no rewrite asked

    bare = Recording([_final("The analysis above named a seller area at 26,200–26,320; entries, stops and targets are yours to set.", "From above")])

    async def without():
        async with _session_factory() as session:
            user = await session.get(User, owner["user_id"])
            return await agent.run_agent(bare, ToolContext(session, user.tenant_id, user, NOW), "give me trade setup as per above analysis")
    plain = _run(without())
    assert "26,200" not in plain.text and len(bare.sent) == 2           # without the conversation those figures are refused


def test_the_chat_route_passes_the_conversation_to_the_planner():
    from pathlib import Path
    src = Path(__file__).resolve().parents[1].joinpath("app/ai/chat_routes.py").read_text()
    assert "chat_history.for_planner(session, tenant_id, user_id, body.session_id, new_chat=body.new_chat)" in src
    assert "earlier=earlier" in src
