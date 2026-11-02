"""M72 PR-3 (CP-3, finding F8): "Ask AI about this" - the chat's context. The server re-reads the object for the asking
user (another person's id answers 404), takes no number about it from the page, hands the planner what it read (its
numbers count as read), and the rules answer reads the context's symbol when the question names none.
No test calls a real provider (scripted models only)."""
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.ai import ask_context
from app.ai import copilot_chat as chat
from app.db.models import SignalHistoryRecord, TradeRecord
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixture
from tests.test_m72_pr1a_safety import _org
from tests.test_m72_pr1c_planner import Scripted, _agent, _flag, _provider, _run, _seed_own_data, planner_on  # noqa: F401
from tests.test_phase_l_ai import _owner

NOW = datetime.now(UTC)


def _final(text, cards=(), claims=()):
    return {"text": json.dumps({"headline": "", "text": text, "claims": list(claims), "cards": list(cards), "chips": [], "disclaimers": []})}


def _ids(tenant_id, user_id):
    async def go():
        async with _session_factory() as session:
            trade = await session.scalar(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.user_id == user_id))
            sig = SignalHistoryRecord(tenant_id=tenant_id, user_id=user_id, strategy_id="s", symbol="SIGSTOCK", direction="LONG",
                                      signal_time=NOW - timedelta(hours=1), entry=111.0, stop_loss=101.0, target1=131.0, grade="A",
                                      reasons_json="[]", timeframe_combo="15min+1h")
            session.add(sig)
            await session.commit()
            return trade.id, sig.id
    return _run(go())


def _resolve(tenant_id, user_id, body):
    async def go():
        async with _session_factory() as session:
            return await ask_context.resolve(session, tenant_id, user_id, ask_context.ChatContext.model_validate(body))
    return _run(go())


def test_the_context_is_read_for_the_asker_only():
    tenant_id, a, b = _org("pr3-own")
    _seed_own_data(tenant_id, a, "GGG")
    trade_id, signal_id = _ids(tenant_id, a)
    got = _resolve(tenant_id, a, {"page": "positions", "symbol": "nifty", "object": {"kind": "trade", "id": trade_id}})
    assert got["object"]["symbol"] == "GGGSTOCK" and got["symbol"] == "GGGSTOCK"          # the object's own symbol wins
    assert got["object"]["entry_price"] == 100.0 and got["object"]["open"] is False and got["object"]["pnl"] == 20.0
    journal = _resolve(tenant_id, a, {"object": {"kind": "journal", "id": trade_id}})
    assert journal["object"]["your_tags"] == ["patient"] and journal["object"]["trade"]["symbol"] == "GGGSTOCK"
    sig = _resolve(tenant_id, a, {"object": {"kind": "signal", "id": signal_id}})["object"]
    assert sig == {"kind": "signal", "symbol": "SIGSTOCK", "timeframes": "15min+1h", "grade": "A", "signal_time": sig["signal_time"]}
    assert "111" not in json.dumps(sig) and "LONG" not in json.dumps(sig)                  # no price, no direction word
    from fastapi import HTTPException
    for kind, oid in (("trade", trade_id), ("journal", trade_id), ("signal", signal_id), ("position", trade_id)):
        with pytest.raises(HTTPException) as err:
            _resolve(tenant_id, b, {"object": {"kind": kind, "id": oid}})                  # member B: not found
        assert err.value.status_code == 404
    assert _resolve(tenant_id, a, {"symbol": "BANKNIFTY", "timeframe": "15min"}) == {"symbol": "BANKNIFTY", "timeframe": "15min"}
    assert _resolve(tenant_id, a, {}) is None


def test_the_context_takes_no_numbers_or_extra_fields_from_the_page():
    from pydantic import ValidationError
    for bad in ({"object": {"kind": "trade", "id": 1, "price": 99}}, {"entry": 5}, {"symbol": "NIFTY; DROP"}, {"object": {"kind": "wallet", "id": 1}},
                {"page": "x" * 41}, {"object": {"kind": "trade", "id": 0}}):
        with pytest.raises(ValidationError):
            ask_context.ChatContext.model_validate(bad)


def test_the_planner_gets_the_context_as_data_and_its_numbers_count_as_read():
    tenant_id, a, _b = _org("pr3-plan")
    _seed_own_data(tenant_id, a, "HHH")
    trade_id, _sig = _ids(tenant_id, a)
    context = _resolve(tenant_id, a, {"object": {"kind": "trade", "id": trade_id}})
    _run(_flag("copilot_agent", False, [tenant_id]))
    try:
        model = Scripted([_final("This HHHSTOCK trade entered at 100 and closed at 104.", claims=[{"statement": "Entry 100", "source": "context"}])])

        async def go():
            from app.ai import agent
            from app.ai.tools import ToolContext
            from app.db.models import User
            async with _session_factory() as session:
                user = await session.get(User, a)
                return await agent.run_agent(model, ToolContext(session, tenant_id, user, NOW), "How did this trade go?", context=context)
        ans = _run(go())
        assert ans.source == "ai" and "104" in ans.text, (ans.stopped, ans.text)
        assert model.turns == 1
        # without the context the same figures are not read: one rewrite, then the data summary
        bare = Scripted([_final("This HHHSTOCK trade entered at 100 and closed at 104."), _final("This HHHSTOCK trade entered at 100 and closed at 104.")])
        assert _agent(a, bare, "How did this trade go?").source == "summary"
    finally:
        _run(_flag("copilot_agent", False))


def test_the_rules_answer_reads_the_context_symbol_when_the_question_names_none():
    token = chat.CONTEXT_SYMBOL.set("BANKNIFTY")
    try:
        assert chat.symbol_in("Read the chart") == "BANKNIFTY"
        assert chat.symbol_in("Read the NIFTY chart") == "NIFTY"
    finally:
        chat.CONTEXT_SYMBOL.reset(token)
    assert chat.symbol_in("Read the chart") == "NIFTY"


@pytest.mark.usefixtures("planner_on")
def test_the_chat_route_resolves_the_context_and_refuses_another_persons_object(monkeypatch):
    headers, me = _owner("pr3-chat@example.com")
    _seed_own_data(me["tenant_id"], me["id"], "JJJ")
    trade_id, _sig = _ids(me["tenant_id"], me["id"])
    seen = {}

    async def spy(provider, ctx, question, **kw):
        seen.update(kw)
        from app.ai.agent import AgentAnswer
        return AgentAnswer("x", "summary", "steps")
    from app.ai import agent
    monkeypatch.setattr(agent, "run_agent", spy)
    _provider(monkeypatch, Scripted([]))
    body = client.post("/api/ai/chat", headers=headers, json={"message": "How did this go?", "context": {"page": "orders", "object": {"kind": "trade", "id": trade_id}}}).json()
    assert seen["context"]["object"]["symbol"] == "JJJSTOCK" and body["context"] == {"symbol": "JJJSTOCK", "kind": "trade"}
    other_headers, _other = _owner("pr3-other@example.com")
    r = client.post("/api/ai/chat", headers=other_headers, json={"message": "How did this go?", "context": {"object": {"kind": "trade", "id": trade_id}}})
    assert r.status_code == 404
    bad = client.post("/api/ai/chat", headers=headers, json={"message": "How did this go?", "context": {"object": {"kind": "trade", "id": trade_id, "pnl": 9}}})
    assert bad.status_code == 422
