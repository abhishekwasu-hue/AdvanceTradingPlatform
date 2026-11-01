"""M72 PR-1c (CP-1, findings F6 / F10 / F18 / F20 / F22): the AI planner in the Copilot chat.

- Flag `copilot_agent` (needs `ai_copilot` + `ui_v2`): with an AI that has tool use, the chat asks the planner first;
  ATP's rules answer when the AI is down (provider error) or its answer fails the checks. OFF: exactly today's chat.
- New user-scoped read tools (orders, Trade settings, journal, watchlists) and calc tools; a calc tool computes only
  over numbers already read in the request; a Trade setting never saved reads "Not set".
- Every tool is `read` or `proposal`; no module of the Copilot chat path imports an order-placing function.
- The planner's limits come from Admin (each inside its bounds).
No test calls a real provider: every model here is a scripted object (no network, no spend).
"""
import ast
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.ai import agent, chat_routes, tools
from app.ai import copilot_chat as chat
from app.ai.providers import Completion, ProviderError, ToolCall, ToolTurn
from app.ai.tools import ToolContext, run_tool
from app.ai.tools.my_data import CALC_TOOLS, NOT_SET
from app.db.models import AgentRunRecord, AiActionRecord, JournalEntryRecord, OrderRecord, PaperRestingOrderRecord, TradeRecord, User, WatchlistRecord
from tests.test_admin_api import _admin
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_m72_pr1a_safety import _org
from tests.test_phase_l_ai import _owner

NOW = datetime.now(UTC)
NEW_TOOLS = ("get_my_orders", "get_my_trade_settings", "get_journal_entries", "get_watchlist", *CALC_TOOLS)


def _run(coro):
    return asyncio.run(coro)


async def _flag(name, on, tenants=()):
    from app.platform import controls
    async with _session_factory() as session:
        current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
        flags = dict(current.get("flags") or {})
        flags[name] = {"on": on, "tenants": list(tenants)}
        await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
        await session.commit()


@pytest.fixture
def planner_on():
    _run(_flag("ui_v2", True))
    _run(_flag("copilot_agent", True))
    yield
    _run(_flag("copilot_agent", False))
    _run(_flag("ui_v2", False))


class Scripted:
    """A recorded model with tool use: each step is {"calls": [...]} or {"text": ...}; `fail` raises like a provider outage."""
    name, model, supports_tools = "anthropic", "scripted", True

    def __init__(self, steps, fail=False):
        self.steps, self.fail, self.turns, self.prompt_version = list(steps), fail, 0, None
        self.systems = []

    async def complete_tools(self, system, messages, tools_, *, max_tokens=4000):
        self.turns += 1
        self.systems.append(system)
        if self.fail:
            raise ProviderError("service unavailable", kind="unavailable")
        step = self.steps.pop(0) if self.steps else {"text": json.dumps({"text": "Done.", "claims": []})}
        if "calls" in step:
            calls = [ToolCall(f"c{self.turns}_{i}", c["tool"], c.get("args", {})) for i, c in enumerate(step["calls"])]
            return ToolTurn("", calls, [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in calls], Completion(""), "tool_use")
        return ToolTurn(step["text"], [], [{"type": "text", "text": step["text"]}], Completion(step["text"]), "end_turn")

    async def complete(self, system, user, *, max_tokens=800):
        raise AssertionError("the planner path never asks for a second wording call")


def _answer(text, claims=()):
    return {"text": json.dumps({"text": text, "claims": list(claims), "disclaimers": []})}


def _call(user_id, name, args=None):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            return await run_tool(name, args or {}, ToolContext(session, user.tenant_id, user, NOW))
    return _run(go())


def _agent(user_id, provider, question):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            return await agent.run_agent(provider, ToolContext(session, user.tenant_id, user, NOW), question)
    return _run(go())


def _seed_own_data(tenant_id, user_id, tag):
    async def go():
        async with _session_factory() as session:
            session.add(PaperRestingOrderRecord(tenant_id=tenant_id, user_id=user_id, client_ref=f"r-{tag}", mode="PAPER", purpose="ENTRY", kind="LIMIT",
                                                symbol=f"{tag}STOCK", exchange="NSE", side="BUY", quantity=10, order_type="LIMIT", price=101.5,
                                                trigger_symbol=f"{tag}STOCK", trigger_exchange="NSE", trigger_level=101.5, trigger_side="BELOW"))
            session.add(OrderRecord(tenant_id=tenant_id, user_id=user_id, mode="PAPER", strategy_id="manual", symbol=f"{tag}STOCK", direction="LONG",
                                    quantity=10, status="FILLED"))
            session.add(WatchlistRecord(tenant_id=tenant_id, user_id=user_id, name=f"{tag} list", symbols_json=json.dumps([f"{tag}STOCK"])))
            trade = TradeRecord(tenant_id=tenant_id, user_id=user_id, mode="PAPER", symbol=f"{tag}STOCK", strategy_id="s", direction="LONG",
                                entry_time=NOW - timedelta(days=1), entry_price=100.0, quantity=5, stop_loss=95.0, exit_time=NOW - timedelta(hours=2),
                                exit_price=104.0, pnl=20.0)
            session.add(trade)
            await session.flush()
            session.add(JournalEntryRecord(tenant_id=tenant_id, user_id=user_id, trade_id=trade.id, user_tags_json=json.dumps(["patient"])))
            await session.commit()
    _run(go())


# --- the registry and the tools -------------------------------------------------------------------------------------------

def test_every_tool_is_read_or_proposal_and_the_new_tools_are_flagged_read_tools():
    reg = tools.registry()
    assert {t.kind for t in reg.values()} <= {"read", "proposal"}
    for name in NEW_TOOLS:
        assert reg[name].kind == "read" and reg[name].flag == "copilot_agent" and reg[name].agent, name
        assert reg[name].schema()["additionalProperties"] is False


def test_new_tools_read_only_the_askers_own_rows():
    tenant_id, a, b = _org("pr1c-own")
    _seed_own_data(tenant_id, a, "AAA")
    _seed_own_data(tenant_id, b, "BBB")
    _run(_flag("copilot_agent", False, [tenant_id]))
    try:
        for user_id, mine, theirs in ((a, "AAA", "BBB"), (b, "BBB", "AAA")):
            for name in ("get_my_orders", "get_journal_entries", "get_watchlist"):
                res = _call(user_id, name)
                text = json.dumps(res.data)
                assert res.ok and mine in text and theirs not in text, (name, text)
                assert res.as_of
    finally:
        _run(_flag("copilot_agent", False))
    orders = _call(a, "get_my_orders")
    assert not orders.ok and "not enabled" in orders.error                 # flag off: never run


def test_trade_settings_read_not_set_until_he_saves_one():
    headers, me = _owner("pr1c-settings@example.com")
    _run(_flag("copilot_agent", False, [me["tenant_id"]]))
    try:
        res = _call(me["id"], "get_my_trade_settings")
        fields = [f for s in res.data["sections"] for f in s["fields"]]
        assert res.ok and fields
        assert all(f["value"] == NOT_SET for f in fields if f["key"] == "order_defaults.product")
        assert "ask him" in res.data["not_set_means"]
        bad = _call(me["id"], "get_my_trade_settings", {"section": "nope"})
        assert not bad.ok
    finally:
        _run(_flag("copilot_agent", False))


def test_calc_tools_are_plain_arithmetic():
    tenant_id, a, _b = _org("pr1c-calc")
    _run(_flag("copilot_agent", False, [tenant_id]))
    try:
        diff = _call(a, "calc_difference", {"a": 104, "b": 95.5}).data
        assert diff["a_minus_b"] == 8.5 and diff["absolute"] == 8.5
        change = _call(a, "calc_percent_change", {"start": 200, "end": 210}).data
        assert change["change"] == 10 and change["percent_change"] == 5
        assert _call(a, "calc_sum", {"values": [1.5, 2.25, -1]}).data["sum"] == 2.75
        assert not _call(a, "calc_percent_change", {"start": 0, "end": 5}).ok
        assert not _call(a, "calc_sum", {"values": []}).ok
    finally:
        _run(_flag("copilot_agent", False))


# --- the planner loop -----------------------------------------------------------------------------------------------------

def test_a_derived_number_needs_a_calc_tool_on_grounded_inputs():
    tenant_id, a, _b = _org("pr1c-derive")
    _seed_own_data(tenant_id, a, "CCC")
    _run(_flag("copilot_agent", False, [tenant_id]))
    try:
        # 1. the model computes 2.5 itself (101.5 - 99): not in any tool result -> not shown, the data summary instead
        no_calc = Scripted([{"calls": [{"tool": "get_my_orders"}]}, _answer("Your CCCSTOCK order waits at 101.5, 2.5 above 99."),
                            _answer("Your CCCSTOCK order waits at 101.5, 2.5 above 99.")])
        ans = _agent(a, no_calc, "How far is my waiting CCCSTOCK order from 99?")
        assert ans.source == "summary" and ans.stopped == "not_grounded"
        # 2. a calc on a number nobody read is refused
        made_up = Scripted([{"calls": [{"tool": "calc_difference", "args": {"a": 123.4, "b": 99}}]}, _answer("Not available.")])
        ans = _agent(a, made_up, "How far is my waiting CCCSTOCK order from 99?")
        assert ans.tool_calls[0]["ok"] is False and "calc refused" in ans.tool_calls[0]["error"]
        # 3. read, then calc on the read number and the asker's own 99: the derived 2.5 is grounded
        good = Scripted([{"calls": [{"tool": "get_my_orders"}]}, {"calls": [{"tool": "calc_difference", "args": {"a": 101.5, "b": 99}}]},
                         _answer("Your CCCSTOCK order waits at 101.5, 2.5 above 99.")])
        ans = _agent(a, good, "How far is my waiting CCCSTOCK order from 99?")
        assert ans.source == "ai" and ans.stopped == "answered" and "2.5" in ans.text
        assert [c["name"] for c in ans.tool_calls] == ["get_my_orders", "calc_difference"]
    finally:
        _run(_flag("copilot_agent", False))


def test_the_planner_prompt_names_calc_tools_and_not_set():
    assert "calc_*" in agent.SYSTEM and '"Not set"' in agent.SYSTEM and "never assume" in agent.SYSTEM
    assert "English only" in agent.SYSTEM


# --- the chat route -------------------------------------------------------------------------------------------------------

def _provider(monkeypatch, model):
    async def provider(session, tenant_id, user_id):
        return model
    monkeypatch.setattr(chat_routes, "_provider", provider)


def test_planner_first_when_the_flag_is_on_and_an_ai_with_tools_is_ready(planner_on, monkeypatch):
    headers, me = _owner("pr1c-chat@example.com")
    _seed_own_data(me["tenant_id"], me["id"], "DDD")
    model = Scripted([{"calls": [{"tool": "get_my_orders"}, {"tool": "get_watchlist"}]}, _answer("Your waiting order is DDDSTOCK at 101.5.")])
    _provider(monkeypatch, model)
    r = client.post("/api/ai/chat", headers=headers, json={"message": "What orders are waiting?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "ai" and body["job"] == "agent" and body["answer"] == "Your waiting order is DDDSTOCK at 101.5."
    assert body["tool_calls"] == ["get_my_orders", "get_watchlist"] and body["worked"][0]["label"] == "Your orders"
    assert body["disclaimer"] == chat.DISCLAIMER and body["ai"]["ready"] is True and "diagnostics" not in body and "agent" not in body
    assert model.turns == 2 and "calc_*" in model.systems[0]

    async def counts():
        async with _session_factory() as session:
            return (await session.scalar(select(func.count()).select_from(OrderRecord).where(OrderRecord.tenant_id == me["tenant_id"])),
                    await session.scalar(select(func.count()).select_from(AiActionRecord).where(AiActionRecord.tenant_id == me["tenant_id"])),
                    await session.scalar(select(func.count()).select_from(AgentRunRecord).where(AgentRunRecord.tenant_id == me["tenant_id"])))
    assert _run(counts()) == (1, 0, 1)                                        # the seeded order only; no proposal; one audited run
    history = client.get("/api/ai/chat/history", headers=headers).json()["turns"]
    assert history and history[-1]["question"] == "What orders are waiting?"


def test_rules_answer_when_the_ai_is_down(planner_on, monkeypatch):
    headers, _ = _owner("pr1c-down@example.com")
    model = Scripted([], fail=True)
    _provider(monkeypatch, model)
    body = client.post("/api/ai/chat", headers=headers, json={"message": "What is my risk per trade?"}).json()
    assert body["source"] == "rules" and body["job"] != "agent" and body["note"] == chat_routes.AGENT_FALLBACK_NOTE
    assert model.turns == 1                                                   # no second call for wording


def test_rules_answer_when_the_ai_answer_fails_the_checks(planner_on, monkeypatch):
    headers, _ = _owner("pr1c-bad@example.com")
    model = Scripted([_answer("Buy NIFTY now, it will hit 26,500."), _answer("Buy NIFTY now, it will hit 26,500.")])
    _provider(monkeypatch, model)
    body = client.post("/api/ai/chat", headers=headers, json={"message": "What is my risk per trade?"}).json()
    assert body["source"] == "rules" and "26,500" not in json.dumps(body) and body["note"] == chat_routes.AGENT_FALLBACK_NOTE


def test_a_refusal_never_reaches_the_planner(planner_on, monkeypatch):
    headers, _ = _owner("pr1c-refuse@example.com")
    model = Scripted([])
    _provider(monkeypatch, model)
    body = client.post("/api/ai/chat", headers=headers, json={"message": "Place a buy order for 2 lots of NIFTY"}).json()
    assert body["refused"] == "order" and model.turns == 0


def test_flag_off_or_no_tool_use_keeps_todays_chat(monkeypatch):
    _run(_flag("ui_v2", True))
    try:
        headers, _ = _owner("pr1c-off@example.com")
        model = Scripted([])
        _provider(monkeypatch, model)
        body = client.post("/api/ai/chat", headers=headers, json={"message": "What is my risk per trade?"}).json()
        assert model.turns == 0 and body["job"] != "agent" and body.get("note") != chat_routes.AGENT_FALLBACK_NOTE
        _run(_flag("copilot_agent", True))

        class NoTools:
            name, model, prompt_version = "anthropic", "words-only", None
            supports_tools = False

            async def complete(self, system, user, *, max_tokens=800):
                return ""
        _provider(monkeypatch, NoTools())
        body = client.post("/api/ai/chat", headers=headers, json={"message": "What is my risk per trade?"}).json()
        assert body["job"] != "agent" and body.get("note") != chat_routes.AGENT_FALLBACK_NOTE
    finally:
        _run(_flag("copilot_agent", False))
        _run(_flag("ui_v2", False))


def test_admin_sees_the_planner_run(planner_on, monkeypatch):
    headers, _ = _admin("pr1c-admin@example.com")
    _provider(monkeypatch, Scripted([_answer("No figures were needed for this.")]))
    body = client.post("/api/ai/chat", headers=headers, json={"message": "What can you read for me?"}).json()
    assert body["source"] == "ai" and body["diagnostics"]["agent"]["stopped"] == "answered"


# --- limits from Admin ----------------------------------------------------------------------------------------------------

def test_admin_sets_the_planner_limits_inside_their_bounds(planner_on, monkeypatch):
    headers, _ = _admin("pr1c-limits@example.com")
    got = client.get("/api/admin/controls/copilot-agent-limits", headers=headers).json()
    assert got["limits"] == got["defaults"] and got["bounds"]["max_steps"] == {"low": 1, "high": 12}
    saved = client.put("/api/admin/controls/copilot-agent-limits", headers=headers, json={"values": {"max_steps": 4, "max_seconds": 30}})
    assert saved.status_code == 200 and saved.json()["limits"]["max_steps"] == 4 and saved.json()["limits"]["max_seconds"] == 30
    for bad in ({"max_steps": 99}, {"max_proposals": 3}, {"max_seconds": 1}):
        assert client.put("/api/admin/controls/copilot-agent-limits", headers=headers, json={"values": bad}).status_code == 400, bad
    seen = {}
    real = agent.run_agent

    async def spy(provider, ctx, question, *, lang="en", limits=None):
        seen["limits"] = limits
        return await real(provider, ctx, question, lang=lang, limits=limits)
    monkeypatch.setattr(agent, "run_agent", spy)
    _provider(monkeypatch, Scripted([_answer("No figures were needed for this.")]))
    client.post("/api/ai/chat", headers=headers, json={"message": "What can you read for me?"})
    assert seen["limits"].max_steps == 4 and seen["limits"].max_proposals == 1
    cleared = client.put("/api/admin/controls/copilot-agent-limits", headers=headers, json={"values": {"max_steps": None, "max_seconds": None}})
    assert cleared.json()["limits"] == cleared.json()["defaults"]
    trader, _ = _owner("pr1c-limits-trader@example.com")
    assert client.get("/api/admin/controls/copilot-agent-limits", headers=trader).status_code == 403


# --- static: no order path ------------------------------------------------------------------------------------------------

ORDER_FUNCTIONS = {"place_order", "execute_entry", "close_position", "cancel_order", "modify_order", "submit_order", "execute"}
ORDER_MODULES = ("app.execution", "app.trading.position_monitor", "app.brokers.base")


def test_no_copilot_chat_module_imports_an_order_placing_function():
    root = Path(__file__).resolve().parents[1] / "app" / "ai"
    files = [root / "agent.py", root / "chat_routes.py", root / "copilot_chat.py", root / "platform_key.py", root / "resolve.py",
             *sorted((root / "tools").glob("*.py"))]
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(ORDER_MODULES), f"{path.name} imports {node.module}"
                assert not {a.name for a in node.names} & ORDER_FUNCTIONS, f"{path.name} imports {[a.name for a in node.names]}"
            if isinstance(node, ast.Import):
                assert not any(a.name.startswith(ORDER_MODULES) for a in node.names), path.name


def test_flag_is_off_by_default_and_needs_the_copilot():
    from app.platform import controls
    from app.platform.flag_catalog import FLAG_INFO
    assert "copilot_agent" in controls.DEFAULT_OFF_FLAGS
    assert set(FLAG_INFO["copilot_agent"].depends_on) == {"ai_copilot", "ui_v2"}
