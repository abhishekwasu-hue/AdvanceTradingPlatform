"""CH-2 (owner 5 Nov, "Ho finish all job"): Claude-like Copilot chat sessions - "New chat", the list of earlier
conversations titled by their first question, open / rename / delete; only the caller's own; turns from before CH-2
adopted into "Earlier conversation". Made-up users and the recorded tools only."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from app.ai import chat_history
from app.core import config
from app.db.models import CopilotChatSessionRecord, CopilotChatTurnRecord
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_copilot_v2_chat import _flag, _run, recorded_tools, v2_on  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

BACKEND = Path(__file__).resolve().parents[1]


def _ask(headers, message, **extra):
    r = client.post("/api/ai/chat", headers=headers, json={"message": message, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_title_is_the_first_question_on_one_line():
    assert chat_history.title_of("  What   is\nRSI? ") == "What is RSI?"
    long = "Read the NIFTY chart and tell me about the structure of the trend on the daily and the hourly frames"
    title = chat_history.title_of(long)
    assert len(title) <= chat_history.TITLE_MAX + 1 and title.endswith("…") and long.startswith(title[:-1])
    assert chat_history.title_of("") == "New chat"


def test_new_chat_starts_a_conversation_and_the_old_one_stays(v2_on, recorded_tools):  # noqa: F811 - fixtures
    headers, _ = _owner("ch2-a@example.com")
    first = _ask(headers, "What is RSI?")
    _ask(headers, "Read the NIFTY chart")                                   # no id: the most recently used conversation
    a = first["conversation"]
    assert a["title"] == "What is RSI?"
    b = _ask(headers, "hello there", new_chat=True)["conversation"]
    assert b["id"] != a["id"] and b["title"] == "hello there"
    listed = client.get("/api/ai/chat/sessions", headers=headers).json()
    assert [(s["id"], s["turns"]) for s in listed["sessions"]] == [(b["id"], 1), (a["id"], 2)]       # most recent first
    assert listed["keep"] == config.COPILOT_CHAT_SESSIONS_KEEP
    old = client.get(f"/api/ai/chat/history?session_id={a['id']}", headers=headers).json()
    assert [t["question"] for t in old["turns"]] == ["What is RSI?", "Read the NIFTY chart"] and old["session"] == a
    _ask(headers, "What is RSI?", session_id=a["id"])                     # continue the older one
    assert client.get("/api/ai/chat/sessions", headers=headers).json()["sessions"][0]["id"] == a["id"]
    assert client.get("/api/ai/chat/history", headers=headers).json()["session"]["id"] == a["id"]


def test_another_persons_conversation_is_not_found(v2_on, recorded_tools):  # noqa: F811 - fixtures
    mine, _ = _owner("ch2-mine@example.com")
    other, _ = _owner("ch2-other@example.com")
    sid = _ask(mine, "What is RSI?")["conversation"]["id"]
    assert client.get(f"/api/ai/chat/history?session_id={sid}", headers=other).status_code == 404
    assert client.post("/api/ai/chat", headers=other, json={"message": "What is RSI?", "session_id": sid}).status_code == 404
    assert client.patch(f"/api/ai/chat/sessions/{sid}", headers=other, json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/ai/chat/sessions/{sid}", headers=other).status_code == 404
    assert client.get("/api/ai/chat/sessions", headers=other).json()["sessions"] == []
    assert len(client.get(f"/api/ai/chat/history?session_id={sid}", headers=mine).json()["turns"]) == 1


def test_rename_and_delete_one_conversation(v2_on, recorded_tools):  # noqa: F811 - fixtures
    headers, _ = _owner("ch2-edit@example.com")
    a = _ask(headers, "What is RSI?")["conversation"]["id"]
    b = _ask(headers, "hello there", new_chat=True)["conversation"]["id"]
    r = client.patch(f"/api/ai/chat/sessions/{a}", headers=headers, json={"title": "  RSI   notes "})
    assert r.status_code == 200 and r.json() == {"id": a, "title": "RSI notes"}
    assert client.delete(f"/api/ai/chat/sessions/{b}", headers=headers).json() == {"deleted": 1}
    assert [s["id"] for s in client.get("/api/ai/chat/sessions", headers=headers).json()["sessions"]] == [a]
    assert client.get(f"/api/ai/chat/history?session_id={b}", headers=headers).status_code == 404


def test_turns_from_before_ch2_become_earlier_conversation(v2_on, recorded_tools):  # noqa: F811 - fixtures
    headers, me = _owner("ch2-legacy@example.com")

    async def legacy():
        async with _session_factory() as session:
            for q in ("old one", "old two"):
                session.add(CopilotChatTurnRecord(tenant_id=me["tenant_id"], user_id=me["id"], question=q, answer_json="{}"))
            await session.commit()
    _run(legacy())
    listed = client.get("/api/ai/chat/sessions", headers=headers).json()["sessions"]
    assert [(s["title"], s["turns"]) for s in listed] == [(chat_history.EARLIER, 2)]
    body = client.get("/api/ai/chat/history", headers=headers).json()
    assert [t["question"] for t in body["turns"]] == ["old one", "old two"] and body["session"]["title"] == chat_history.EARLIER


def test_only_the_newest_conversations_are_kept(v2_on, recorded_tools, monkeypatch):  # noqa: F811 - fixtures
    monkeypatch.setattr(config, "COPILOT_CHAT_SESSIONS_KEEP", 2)
    headers, _ = _owner("ch2-keep@example.com")
    for q in ("What is RSI?", "hello there", "Read the NIFTY chart"):
        _ask(headers, q, new_chat=True)
    assert [s["title"] for s in client.get("/api/ai/chat/sessions", headers=headers).json()["sessions"]] == ["Read the NIFTY chart", "hello there"]


def test_delete_my_conversation_removes_every_session(v2_on, recorded_tools):  # noqa: F811 - fixtures
    headers, me = _owner("ch2-forget@example.com")
    _ask(headers, "What is RSI?")
    _ask(headers, "hello there", new_chat=True)
    assert client.delete("/api/ai/chat/history", headers=headers).json() == {"deleted": 2}

    async def left():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(CopilotChatSessionRecord).where(CopilotChatSessionRecord.user_id == me["id"]))
    assert _run(left()) == 0


def test_export_carries_the_conversations_and_the_migration_is_additive():
    from app.me import account
    assert ("copilot_conversations", CopilotChatSessionRecord) in account._DATASETS
    text = (BACKEND / "alembic/versions/f2c4a6e8b012_ch2_copilot_chat_sessions.py").read_text()
    assert "revision: str = 'f2c4a6e8b012'" in text and "down_revision: Union[str, Sequence[str], None] = 'd5f1b7c3e9a2'" in text
    assert "ROLLBACK_SAFE = True" in text
    from scripts.migration_rollback_safety import risky_operations
    assert risky_operations(text) == []
