"""H-C3b API: /api/ai/research is behind `ai_research` (off by default), needs a real AI provider (the rules cannot
draft), runs on server bars from the market tools, records every draft, stores the out-of-sample check so a later GET
shows it, and the LLM proposer parses only a JSON object (anything else is an invalid draft or the end)."""
import asyncio
import json

import pytest

from app.ai import research_loop
from tests.test_auth_api import _session_factory, client
from tests.test_hc3b_research_loop import _draft, _frame
from tests.test_phase_l_ai import _owner


def _run(coro):
    return asyncio.run(coro)


def _flags(**on):
    async def go():
        from app.platform import controls
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            for name, value in on.items():
                flags[name] = {"on": value, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    async def complete(self, system, user, *, max_tokens=2000):
        self.prompts.append(user)
        return self.replies.pop(0) if self.replies else "null"


@pytest.fixture
def no_ack_gate():
    """The first-use acknowledgement has its own tests; here it is replaced by a plain login."""
    from app.ai.compliance_terms import require_ai_acknowledged
    from app.auth.dependencies import get_current_user
    from app.main import app
    app.dependency_overrides[require_ai_acknowledged] = get_current_user
    yield
    app.dependency_overrides.pop(require_ai_acknowledged, None)


@pytest.fixture
def research_on():
    _flags(ai_research=True)
    yield
    _flags(ai_research=False)


def test_flag_off_by_default_and_the_rules_cannot_draft(monkeypatch, no_ack_gate):
    headers, _ = _owner("hc3b-api-off@example.com")
    body = {"idea": "EMA trend", "symbol": "NIFTY 50", "timeframe": "15min"}
    assert client.post("/api/ai/research", headers=headers, json=body).status_code == 503        # ai_research is off by default
    _flags(ai_research=True)
    try:
        from app.ai.providers import RuleBasedProvider
        from app.ai import settings as ai_settings

        async def rules(*args, **kwargs):
            return RuleBasedProvider()
        monkeypatch.setattr(ai_settings, "provider_for", rules)
        refused = client.post("/api/ai/research", headers=headers, json=body)
        assert refused.status_code == 409 and "AI provider" in refused.json()["detail"]
    finally:
        _flags(ai_research=False)


def test_the_llm_proposer_accepts_only_a_json_object():
    provider = FakeProvider(['Sure! Here is the draft: {"name": "x", "long_conditions": []} thanks', "[1, 2]", "null"])
    propose = research_loop.llm_proposer(provider)
    first = _run(propose("idea", []))
    assert first == {"name": "x", "long_conditions": []}
    assert _run(propose("idea", [{"seq": 1, "status": "invalid"}])) == {"raw": "[1, 2]"}        # not an object: an invalid draft
    assert _run(propose("idea", [])) is None                                                    # the model says it is done
    assert '"earlier_trials"' in provider.prompts[1] and "invalid" in provider.prompts[1]


def test_a_study_through_the_api_stores_trials_and_the_oos_check(monkeypatch, research_on, no_ack_gate):
    headers, me = _owner("hc3b-api@example.com")
    from app.ai import settings as ai_settings
    from app.ai.tools import market
    provider = FakeProvider([json.dumps(_draft("a", 9, 21)), json.dumps({"name": "broken"}), json.dumps(_draft("b", 5, 34)), "null"])

    async def provider_for(*args, **kwargs):
        return provider
    monkeypatch.setattr(ai_settings, "provider_for", provider_for)

    async def frame(ctx, symbol, exchange, timeframe, days):
        assert ctx.tenant_id == me["tenant_id"]
        return _frame(), "broker:fake"
    monkeypatch.setattr(market, "_frame", frame)
    out = client.post("/api/ai/research", headers=headers, json={"idea": "EMA trend", "symbol": "NIFTY 50", "timeframe": "15min"})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["data_source"] == "broker:fake" and body["report"]["backtested"] == 2 and body["report"]["invalid"] == 1
    assert body["report"]["out_of_sample"]["run"] is True
    later = client.get(f"/api/ai/research/{body['study_id']}", headers=headers).json()
    assert [t["status"] for t in later["trials"]] == ["ok", "invalid", "ok"]
    assert later["report"]["out_of_sample"]["run"] is True and later["report"]["trials"] == 3          # the OOS row is not a trial
    other, _ = _owner("hc3b-api-other@example.com")
    assert client.get(f"/api/ai/research/{body['study_id']}", headers=other).status_code == 404
