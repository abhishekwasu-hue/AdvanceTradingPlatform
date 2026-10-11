"""H-C2b-2 (ADR-0019 §4, §6): the JSON answer contract (claims cite a tool call of the request, and their numbers must
come from that very call) and the OpenAI function-tools adapter behind the same loop."""
import asyncio
import json

import httpx
import pytest

from app.ai import agent
from app.ai.providers import OpenAIProvider, ProviderError
from tests.test_hc2a_agent import _Scripted, _agent, _trade
from tests.test_phase_l_ai import _owner


def _run(coro):
    return asyncio.run(coro)


def test_parse_answer_json_fenced_and_plain():
    j = agent.parse_answer('{"text": "ALPHA: 5 open.", "claims": [{"statement": "5 units", "source": "t1_0"}], "disclaimers": ["paper data"]}')
    assert (j.contract, j.text, j.claims, j.disclaimers) == ("json", "ALPHA: 5 open.", [{"statement": "5 units", "source": "t1_0"}], ["paper data"])
    fenced = agent.parse_answer('```json\n{"text": "x", "claims": []}\n```')
    assert fenced.contract == "json" and fenced.text == "x"
    for raw in ("plain words", '{"text": 5}', '{"broken": ', '{"claims": []}'):
        p = agent.parse_answer(raw)
        assert p.contract == "plain" and p.text == raw and p.claims == []


def _answer(text, claims=(), disclaimers=()):
    return ("text", json.dumps({"text": text, "claims": [{"statement": s, "source": src} for s, src in claims], "disclaimers": list(disclaimers)}))


def test_claims_cite_a_call_of_this_request_and_carry_its_numbers():
    _, me = _owner("hc2b2-claims@example.com")
    _run(_trade(me["tenant_id"], me["id"], "ALPHA"))
    ok = _Scripted([("call", [("get_positions", {}), ("get_risk_limits", {})]),
                    _answer("ALPHA: 5 units from 100, stop 95.", [("5 units of ALPHA from 100", "t1_0")], ["Paper positions only."])])
    ans = _run(_agent(me["id"], ok, "What is open?"))
    assert ans.contract == "json" and ans.claims == [{"statement": "5 units of ALPHA from 100", "source": "t1_0"}]
    assert ans.disclaimers == ["Paper positions only."] and [c["id"] for c in ans.tool_calls] == ["t1_0", "t1_1"]
    # a claim citing an id that is not a call of this request -> one rewrite, then accepted
    unknown = _Scripted([("call", [("get_positions", {})]), _answer("ALPHA: 5 open.", [("5 open", "toolu_made_up")]),
                         _answer("ALPHA: 5 open.", [("5 open", "t1_0")])])
    fixed = _run(_agent(me["id"], unknown, "What is open?"))
    assert fixed.stopped == "answered" and fixed.claims[0]["source"] == "t1_0"
    assert "toolu_made_up" in unknown.seen[2][-1]["content"] and "not a successful tool call" in unknown.seen[2][-1]["content"]
    # the claim's number is in another call's result, not in the cited one -> refused twice -> data summary
    wrong = _Scripted([("call", [("get_positions", {}), ("get_pnl_today", {})]),
                       _answer("ALPHA: 5 open.", [("5 units from 100", "t1_1")]), _answer("ALPHA: 5 open.", [("5 units from 100", "t1_1")])])
    bad = _run(_agent(me["id"], wrong, "What is open?"))
    assert bad.stopped == "not_grounded" and bad.source == "summary" and bad.claims == [] and bad.contract is None
    assert "not in the result of t1_1" in wrong.seen[2][-1]["content"]


def test_advice_inside_a_claim_is_caught_too():
    _, me = _owner("hc2b2-advice@example.com")
    _run(_trade(me["tenant_id"], me["id"], "ALPHA"))
    fake = _Scripted([("call", [("get_positions", {})]), _answer("ALPHA: 5 open.", [("Guaranteed profit, buy more ALPHA now", "t1_0")]),
                      _answer("ALPHA: 5 open.", [("5 units open", "t1_0")])])
    ans = _run(_agent(me["id"], fake, "How is ALPHA?"))
    assert ans.claims == [{"statement": "5 units open", "source": "t1_0"}]


def test_a_plain_text_answer_is_still_accepted_without_claims():
    _, me = _owner("hc2b2-plain@example.com")
    _run(_trade(me["tenant_id"], me["id"], "ALPHA"))
    ans = _run(_agent(me["id"], _Scripted([("call", [("get_positions", {})]), ("text", "ALPHA: 5 units open from 100.")]), "What is open?"))
    assert ans.contract == "plain" and ans.claims == [] and ans.text == "ALPHA: 5 units open from 100."


def _openai(handler):
    return OpenAIProvider("sk-openai", model="gpt-4.1", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_openai_tool_step_payload_and_parsing():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"model": "gpt-4.1", "usage": {"prompt_tokens": 300, "completion_tokens": 20},
                                         "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                                             {"id": "call_1", "type": "function", "function": {"name": "get_positions", "arguments": "{}"}},
                                             {"id": "call_2", "type": "function", "function": {"name": "get_news", "arguments": "{not json"}}]}}]})
    from app.ai import tools
    messages = [{"role": "user", "content": "open?"},
                {"role": "assistant", "content": [{"type": "text", "text": "Checking."}, {"type": "tool_use", "id": "call_0", "name": "get_pnl_today", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_0", "content": "{\"data\": 1}"}]}]
    turn = _run(_openai(handler).complete_tools("sys", messages, tools.specs(["get_positions", "get_news"])))
    body = seen[0]
    assert body["tools"][0] == {"type": "function", "function": {"name": "get_news", "description": tools.registry()["get_news"].description,
                                                                 "parameters": tools.registry()["get_news"].schema()}}
    assert body["messages"][0] == {"role": "system", "content": "sys"} and body["messages"][1] == {"role": "user", "content": "open?"}
    assert body["messages"][2]["tool_calls"][0]["function"] == {"name": "get_pnl_today", "arguments": "{}"} and body["messages"][2]["content"] == "Checking."
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "call_0", "content": "{\"data\": 1}"}
    assert "max_completion_tokens" in body and "tool_choice" not in body
    assert [(c.id, c.name) for c in turn.calls] == [("call_1", "get_positions"), ("call_2", "get_news")] and turn.stop_reason == "tool_use"
    assert "__invalid_json__" in turn.calls[1].arguments and turn.usage.input_tokens == 300
    assert turn.assistant_content[0]["type"] == "tool_use"                                   # Anthropic-shaped: the loop stays neutral


def test_openai_tool_step_errors():
    with pytest.raises(ProviderError, match="cut off"):
        _run(_openai(lambda r: httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "par"}}]}))
             .complete_tools("s", [{"role": "user", "content": "q"}], []))
    with pytest.raises(ProviderError, match="429"):
        _run(_openai(lambda r: httpx.Response(429, json={})).complete_tools("s", [{"role": "user", "content": "q"}], []))


def test_agent_runs_on_openai_end_to_end():
    _, me = _owner("hc2b2-openai@example.com")
    _run(_trade(me["tenant_id"], me["id"], "ALPHA"))
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                {"id": "call_a", "type": "function", "function": {"name": "get_positions", "arguments": "{}"}},
                {"id": "call_b", "type": "function", "function": {"name": "get_news", "arguments": "{\"hours\": 999}"}}]}}]})
        answer = {"text": "ALPHA: 5 units open from 100.", "claims": [{"statement": "ALPHA 5 units", "source": "call_a"}], "disclaimers": []}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]})
    ans = _run(_agent(me["id"], _openai(handler), "What is open?"))
    assert ans.stopped == "answered" and ans.contract == "json" and ans.claims[0]["source"] == "call_a"
    tool_msgs = [m for m in bodies[1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["call_a", "call_b"] and "invalid arguments" in tool_msgs[1]["content"]
    assert [c["ok"] for c in ans.tool_calls] == [True, False]
