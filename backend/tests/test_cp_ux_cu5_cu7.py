"""CP-UX CU5-CU7 (owner order 30 Oct): the no-key state said once, the Copilot conversation kept across reloads (per
user, server-side, bounded by a setting, deletable in Settings -> My data, part of the export and erased with the
account), and jobs behind an OFF switch hidden instead of shown as ready."""
from pathlib import Path

from sqlalchemy import func, select

from app.ai import chat_history
from app.ai import copilot_chat as chat
from app.core import config
from app.db.models import CopilotChatTurnRecord, OrderRecord
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_copilot_v2_chat import _flag, _run, recorded_tools, v2_on  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

BACKEND = Path(__file__).resolve().parents[1]


# --- CU5 ------------------------------------------------------------------------------------------------------------------

def test_no_key_state_is_one_line_not_fixed_sentences():
    assert chat.NO_KEY_TITLE == "AI is off — answers are from rules"
    assert "fixed sentences" not in chat.NO_KEY_LINE and "free AI key" not in chat.NO_KEY_TITLE


# --- CU7 ------------------------------------------------------------------------------------------------------------------

def test_jobs_behind_an_off_switch_are_hidden():
    all_off = {f: False for f in chat.CHAT_FLAGS}
    ids = [j.id for j in chat.visible_jobs(all_off)]
    assert "screen" not in ids and "views" not in ids and len(ids) == len(chat.JOBS) - 2
    assert [j.id for j in chat.visible_jobs({**all_off, "screener_copilot": True})][-1] == "screen"
    assert len(chat.visible_jobs({f: True for f in chat.CHAT_FLAGS})) == len(chat.JOBS)
    assert len(chat.visible_jobs(None)) == len(chat.JOBS)                       # the rule tests: all on
    # ai_agent gates two tools inside jobs that answer without them: no job is hidden by it alone
    assert len(chat.visible_jobs({"ai_agent": False, "screener_copilot": True, "ai_multiview": True})) == len(chat.JOBS)


def test_help_answer_lists_only_the_visible_jobs():
    out = _run(chat.answer("hello there", lambda *_: None, flags={f: False for f in chat.CHAT_FLAGS}))
    lines = out.sections[0]["lines"]
    assert not any(line.startswith(("Find setups", "Full view of a symbol")) for line in lines)
    assert "Find stocks in a daily uptrend near a demand zone" not in out.starters


def test_home_lists_a_job_once_its_switch_is_on(v2_on, recorded_tools):  # noqa: F811 - fixtures
    headers, _ = _owner("cu7-home@example.com")
    assert "screen" not in [j["id"] for j in client.get("/api/ai/chat/home", headers=headers).json()["jobs"]]
    _run(_flag("screener_copilot", True))
    try:
        body = client.get("/api/ai/chat/home", headers=headers).json()
    finally:
        _run(_flag("screener_copilot", False))
    assert "screen" in [j["id"] for j in body["jobs"]] and "views" not in [j["id"] for j in body["jobs"]]
    assert body["keep"] == config.COPILOT_CHAT_KEEP


# --- CU6 ------------------------------------------------------------------------------------------------------------------

def test_the_conversation_survives_a_reload_and_is_per_user(v2_on, recorded_tools):  # noqa: F811 - fixtures
    headers, _ = _owner("cu6-a@example.com")
    other, _ = _owner("cu6-b@example.com")
    assert client.get("/api/ai/chat/history", headers=headers).json() == {"turns": [], "keep": config.COPILOT_CHAT_KEEP}
    answer = client.post("/api/ai/chat", headers=headers, json={"message": "Read the NIFTY chart"}).json()
    client.post("/api/ai/chat", headers=headers, json={"message": "What is RSI?"})
    turns = client.get("/api/ai/chat/history", headers=headers).json()["turns"]
    assert [t["question"] for t in turns] == ["Read the NIFTY chart", "What is RSI?"]           # oldest first
    assert turns[0]["answer"]["job"] == "chart" and turns[0]["answer"]["summary"] == answer["summary"] and turns[0]["at"]
    assert client.get("/api/ai/chat/history", headers=other).json()["turns"] == []           # another person sees nothing


def test_admin_diagnostics_are_never_stored(v2_on, recorded_tools):  # noqa: F811 - fixtures
    from app.core.enums import UserRole
    from app.db.models import User
    headers, me = _owner("cu6-admin@example.com")

    async def promote():
        async with _session_factory() as session:
            (await session.get(User, me["id"])).role = UserRole.SUPER_ADMIN.value
            await session.commit()
    _run(promote())
    body = client.post("/api/ai/chat", headers=headers, json={"message": "Read the NIFTY chart"}).json()
    assert "diagnostics" in body
    stored = client.get("/api/ai/chat/history", headers=headers).json()["turns"][-1]["answer"]
    assert "diagnostics" not in stored


def test_only_the_newest_turns_are_kept(v2_on, recorded_tools, monkeypatch):  # noqa: F811 - fixtures
    monkeypatch.setattr(config, "COPILOT_CHAT_KEEP", 3)
    headers, _ = _owner("cu6-keep@example.com")
    for q in ("What is RSI?", "Teach me: what is a demand zone?", "hello there", "Read the NIFTY chart", "What is RSI?"):
        assert client.post("/api/ai/chat", headers=headers, json={"message": q}).status_code == 200
    body = client.get("/api/ai/chat/history", headers=headers).json()
    assert body["keep"] == 3 and [t["question"] for t in body["turns"]] == ["hello there", "Read the NIFTY chart", "What is RSI?"]

    async def rows():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(CopilotChatTurnRecord))
    assert _run(rows()) >= 3                                         # other tests' people keep theirs


def test_delete_my_conversation_removes_only_mine_and_works_with_the_flags_off(v2_on, recorded_tools):  # noqa: F811 - fixtures
    headers, _ = _owner("cu6-del@example.com")
    other, _ = _owner("cu6-keepme@example.com")
    client.post("/api/ai/chat", headers=headers, json={"message": "What is RSI?"})
    client.post("/api/ai/chat", headers=other, json={"message": "What is RSI?"})
    _run(_flag("ui_v2", False))
    try:
        r = client.delete("/api/ai/chat/history", headers=headers)
    finally:
        _run(_flag("ui_v2", True))
    assert r.status_code == 200 and r.json() == {"deleted": 1}
    assert client.get("/api/ai/chat/history", headers=headers).json()["turns"] == []
    assert len(client.get("/api/ai/chat/history", headers=other).json()["turns"]) == 1


def test_history_read_is_off_with_ui_v2_off():
    headers, _ = _owner("cu6-off@example.com")
    assert client.get("/api/ai/chat/history", headers=headers).status_code == 404


def test_storing_a_turn_writes_no_order(v2_on, recorded_tools):  # noqa: F811 - fixtures
    headers, _ = _owner("cu6-noorder@example.com")

    async def orders():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(OrderRecord))
    before = _run(orders())
    client.post("/api/ai/chat", headers=headers, json={"message": "Place a buy order for 2 lots of NIFTY"})
    assert _run(orders()) == before


def test_export_and_erasure_carry_the_conversation():
    from app.me import account
    assert ("copilot_conversation", CopilotChatTurnRecord) in account._DATASETS
    src = (BACKEND / "app/retention/service.py").read_text()
    assert "forget_copilot_conversation(session, user.tenant_id, user.id)" in src


def test_a_broken_stored_answer_reads_as_none():
    _, me = _owner("cu6-broken@example.com")

    async def go():
        async with _session_factory() as session:
            session.add(CopilotChatTurnRecord(tenant_id=me["tenant_id"], user_id=me["id"], question="q", answer_json="not json"))
            await session.commit()
            return await chat_history.recent(session, me["tenant_id"], me["id"])
    out = _run(go())
    assert out[-1]["question"] == "q" and out[-1]["answer"] is None


def test_keep_is_a_setting_and_the_migration_chains_after_the_previous_head():
    assert isinstance(config.COPILOT_CHAT_KEEP, int) and config.COPILOT_CHAT_KEEP >= 1
    text = (BACKEND / "alembic/versions/e1a2c3d4f506_cp_ux_cu6_copilot_chat_turns.py").read_text()
    assert "revision: str = 'e1a2c3d4f506'" in text and "down_revision: Union[str, Sequence[str], None] = 'd0f0a1b2g204'" in text
    assert "ROLLBACK_SAFE = True" in text and "drop_table('copilot_chat_turns')" in text
    from scripts.migration_rollback_safety import risky_operations
    assert risky_operations(text) == []
    src = (BACKEND / "app/ai/chat_history.py").read_text()
    assert "return int(config.COPILOT_CHAT_KEEP)" in src
