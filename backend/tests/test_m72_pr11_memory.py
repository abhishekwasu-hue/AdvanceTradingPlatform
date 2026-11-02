"""M72 PR-11 (CP-11): what the Copilot remembers - saved by the user himself (suggested from his own lists, trades and
questions), shown, edited and deleted by him, deleted with "Delete my Copilot conversation"; given to the planner only for
his own questions while `copilot_memory` is on; never read across users or by an order path / bot. Scripted models only."""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.ai import memory
from app.db.models import CopilotChatTurnRecord, TradeRecord, User, WatchlistRecord
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixture
from tests.test_m72_pr1c_planner import _flag, _run
from tests.test_m72_pr5_chart_read import Recording, _agent, _final
from tests.test_ui2_4a1_prefs import _invite, _who

NOW = datetime.now(UTC)
GOOD = {"instruments": ["NIFTY", "RELIANCE"], "timeframes": ["15m", "1d"], "style": "swing"}


def _add(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(list(rows))
            await session.commit()
    _run(go())


def _on(tenant_id):
    _run(_flag("ui_v2", True))
    _run(_flag("copilot_memory", False, [tenant_id]))


def test_he_saves_edits_and_deletes_his_own_memory():
    who = _who("pr11-crud")
    h = who["headers"]
    _run(_flag("copilot_memory", False))
    assert client.get("/api/copilot-memory", headers=h).status_code == 404
    _on(who["tenant_id"])
    try:
        got = client.get("/api/copilot-memory", headers=h).json()
        assert got["memory"] == {"instruments": [], "timeframes": [], "style": None}           # nothing until he saves
        assert client.put("/api/copilot-memory", headers=h, json=GOOD).json()["memory"] == GOOD
        for bad in ({"timeframes": ["4h"]}, {"instruments": [f"S{i}" for i in range(13)]}, {"style": "scalper"}, {"instruments": ["<b>"]}):
            assert client.put("/api/copilot-memory", headers=h, json=bad).status_code == 422, bad
        _run(_flag("copilot_memory", False))
        assert client.delete("/api/copilot-memory", headers=h).json() == {"deleted": 1}        # deleting works whatever the flags
    finally:
        _run(_flag("copilot_memory", False))


def test_suggestions_come_from_his_own_rows_only():
    owner = _who("pr11-sugg")
    member = _invite(owner, "USER")
    t = owner["tenant_id"]
    _add(WatchlistRecord(tenant_id=t, user_id=owner["user_id"], name="Mine", symbols_json=json.dumps(["TCS", "INFY"])),
         WatchlistRecord(tenant_id=t, user_id=member["user_id"], name="Theirs", symbols_json=json.dumps(["SECRETCO"])),
         TradeRecord(tenant_id=t, user_id=owner["user_id"], mode="PAPER", symbol="NIFTY26NOV25000CE", underlying_symbol="NIFTY",
                     instrument_kind="OPTION", strategy_id="s", direction="SHORT", entry_time=NOW - timedelta(days=1), entry_price=80.0,
                     quantity=75, stop_loss=120.0),
         CopilotChatTurnRecord(tenant_id=t, user_id=owner["user_id"], question="TCS on the 15 min chart?", answer_json="{}"),
         CopilotChatTurnRecord(tenant_id=t, user_id=member["user_id"], question="OTHERCO weekly chart?", answer_json="{}"))

    async def go():
        async with _session_factory() as session:
            return await memory.suggest(session, await session.get(User, owner["user_id"]))
    s = _run(go())
    syms = {i["symbol"]: i for i in s["instruments"]}
    assert set(syms) == {"TCS", "INFY", "NIFTY"}                                              # never the member's
    assert syms["TCS"]["count"] == 2 and syms["TCS"]["from"] == ["your Copilot questions", "your watchlists"]
    assert syms["NIFTY"]["from"] == ["your trades"]
    assert s["timeframes"] == [{"timeframe": "15m", "count": 1, "from": ["your Copilot questions"]}]


def test_the_planner_gets_his_memory_only_with_the_flag_and_delete_my_conversation_clears_it():
    owner = _who("pr11-plan")
    member = _invite(owner, "USER")
    _on(owner["tenant_id"])
    try:
        assert client.put("/api/copilot-memory", headers=owner["headers"], json=GOOD).status_code == 200
        model = Recording([_final("I have no figures for that yet.", "Answer")])
        assert _agent(owner["user_id"], model, "How is my market?").source == "ai"
        assert "<memory>" not in model.sent[0]                     # run_agent alone: the chat route passes it

        async def mine(uid):
            async with _session_factory() as session:
                return await memory.for_planner(session, await session.get(User, uid))
        assert _run(mine(owner["user_id"])) == {"instruments": ["NIFTY", "RELIANCE"], "timeframes": ["15m", "1d"], "style": "Swing"}
        assert _run(mine(member["user_id"])) is None                # another member's memory is never read
        assert "never sets a strike" in memory.note(GOOD)

        async def ask():
            from app.ai import agent
            from app.ai.tools import ToolContext
            m = Recording([_final("RELIANCE and NIFTY are in your saved list.", "Your list")])
            async with _session_factory() as session:
                user = await session.get(User, owner["user_id"])
                ans = await agent.run_agent(m, ToolContext(session, user.tenant_id, user), "What do I follow?", memory=await memory.for_planner(session, user))
            return m, ans
        m, ans = _run(ask())
        assert "<memory>" in m.sent[0] and ans.source == "ai", ans.note       # its symbols count as his own data
        _run(_flag("copilot_memory", False))
        assert _run(mine(owner["user_id"])) is None                 # flag off: not read
        _on(owner["tenant_id"])
        assert client.delete("/api/ai/chat/history", headers=owner["headers"]).status_code == 200
        assert client.get("/api/copilot-memory", headers=owner["headers"]).json()["memory"]["instruments"] == []
    finally:
        _run(_flag("copilot_memory", False))


def test_no_order_path_worker_or_bot_reads_the_memory():
    root = Path(__file__).resolve().parents[1] / "app"
    readers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if ("ai_memory" in p.read_text(encoding="utf-8") or "ai.memory" in p.read_text(encoding="utf-8")
                         or "import memory" in p.read_text(encoding="utf-8")))
    assert readers == ["ai/agent.py", "ai/chat_history.py", "ai/chat_routes.py", "ai/memory.py", "ai/memory_routes.py", "main.py", "me/prefs.py"], readers
