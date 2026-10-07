"""P0.8-C (ATP_AI_COPILOT_FIX_PROMPT section C): the provider layer.

C1 thinking vs small max_tokens: the Anthropic call adds thinking headroom to the text budget, a `max_tokens` stop is
   retried once with a bigger budget and, if still cut off, raises - a partial answer is never returned as complete.
C2 model names come from the environment per tier (fast / strong), with the tenant's Settings override for generation.
C3 OpenAI: `max_completion_tokens`, no temperature on reasoning models, the 400 body is logged (without the key),
   `finish_reason == "length"` is handled like a truncation.
C4 one SDK client per key is reused; the static system prompt carries a cache_control block; the timeout grows with
   the token budget.
C5 every call is metered (tokens and USD/INR per tenant, feature and model); a plan's monthly budget, once spent,
   switches the tenant to the rule-based provider until the month ends; `/api/ai/provider` shows the usage.
"""
import asyncio
import json
import logging

import httpx
import pytest
from sqlalchemy import select

from app.ai import metering, pricing
from app.ai import providers as prov
from app.ai import settings as ai_settings
from app.ai.providers import AnthropicProvider, OpenAIProvider, ProviderError, RuleBasedProvider
from app.db.models import Tenant, UsageRecord
from tests.test_auth_api import _register, _session_factory, client
from tests.test_phase_l_ai import _anthropic_mock
from tests.test_trading_worker import _upgrade_plan


def _run(coro):
    return asyncio.run(coro)


def _msg(text, stop="end_turn", usage=None, model="claude-x"):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": model, "stop_sequence": None, "stop_reason": stop,
            "content": [{"type": "thinking", "thinking": "", "signature": "x"}, {"type": "text", "text": text}],
            "usage": usage or {"input_tokens": 1200, "output_tokens": 300, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 0}}


# --- C1 + C4 ------------------------------------------------------------------------------------------------------------------
def test_anthropic_adds_thinking_headroom_retries_a_cut_off_answer_once_and_never_returns_a_partial_one():
    import httpx2
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            return httpx2.Response(200, json=_msg("The index is at 25,0", stop="max_tokens"))
        return httpx2.Response(200, json=_msg("The index is at 25,000 and the plan is to wait."))
    provider = AnthropicProvider("sk-ant-test", http_client=_anthropic_mock(handler), tier="fast")
    assert _run(provider.complete("sys", "narrate", max_tokens=700)) == "The index is at 25,000 and the plan is to wait."
    assert len(seen) == 2
    # The fast tier thinks at low effort; the request budget is the text budget plus the thinking headroom, doubled on the retry.
    assert seen[0]["output_config"] == {"effort": "low"} and seen[0]["thinking"] == {"type": "adaptive"}
    assert seen[0]["max_tokens"] == 700 + prov.thinking_headroom("low") and seen[1]["max_tokens"] == 2 * seen[0]["max_tokens"]
    # The static system prompt is a cacheable block; the user text stays uncached (it changes every call).
    assert seen[0]["system"] == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
    assert seen[0]["messages"] == [{"role": "user", "content": "narrate"}]
    assert set(seen[0]) == {"model", "max_tokens", "system", "messages", "thinking", "output_config", "fallbacks"}

    calls = []

    def always_cut(request):
        calls.append(1)
        return httpx2.Response(200, json=_msg("half an ans", stop="max_tokens"))
    with pytest.raises(ProviderError) as info:
        _run(AnthropicProvider("sk-ant-test", http_client=_anthropic_mock(always_cut)).complete("sys", "u", max_tokens=500))
    assert "cut off" in str(info.value) and len(calls) == 2
    # The tokens of the failed attempts are still known to the caller (they are billed).
    assert info.value.usage is not None and info.value.usage.output_tokens == 600


def test_anthropic_reuses_one_client_per_key_and_scales_the_timeout_with_the_budget(monkeypatch):
    prov._ANTHROPIC_CLIENTS.clear()
    a = AnthropicProvider("sk-ant-one")
    b = AnthropicProvider("sk-ant-one", model="claude-other")
    c = AnthropicProvider("sk-ant-two")
    assert a._client() is b._client() and a._client() is not c._client()
    # Strong-tier thinking has more headroom; the timeout grows with the budget instead of a flat 45 s.
    assert prov.thinking_headroom("medium") > prov.thinking_headroom("low")
    assert prov.timeout_for(16000 + prov.thinking_headroom("medium")) > prov.timeout_for(700 + prov.thinking_headroom("low")) >= prov.TIMEOUT
    # Older models without the effort/thinking controls get a plain request.
    plain = AnthropicProvider("k", model="claude-haiku-4-5")._request("s", "u", 900)
    assert "thinking" not in plain and "output_config" not in plain and plain["max_tokens"] == 900
    assert not prov._supports_effort("claude-sonnet-4-20250514") and not prov._supports_effort("claude-3-5-haiku-20241022")
    assert prov._supports_effort("claude-opus-4-6") and prov._supports_effort("claude-haiku-5") and prov._supports_effort("claude-sonnet-5-5-20260901")
    assert "api_key" not in repr(AnthropicProvider("sk-secret-xyz")) and "sk-secret-xyz" not in repr(OpenAIProvider("sk-secret-xyz"))


# --- C2 ---------------------------------------------------------------------------------------------------------------------
def test_models_come_from_the_environment_per_tier_and_the_tenant_override_wins_for_generation(monkeypatch):
    monkeypatch.setenv("AI_ANTHROPIC_STRONG_MODEL", "claude-strong-env")
    monkeypatch.setenv("AI_ANTHROPIC_FAST_MODEL", "claude-fast-env")
    monkeypatch.setenv("AI_OPENAI_FAST_MODEL", "gpt-fast-env")
    models = prov.default_models()
    assert models["anthropic"] == {"strong": "claude-strong-env", "fast": "claude-fast-env"} and models["openai"]["fast"] == "gpt-fast-env"
    assert prov.TASK_TIERS["strategy_generation"] == "strong" and prov.TASK_TIERS["narration"] == "fast" and prov.TASK_TIERS["classification"] == "fast"
    # No Settings override: both tiers from the environment. With one: it drives generation, cheap tasks keep the fast model.
    assert prov.model_for("anthropic", None, "strategy_generation") == "claude-strong-env"
    assert prov.model_for("anthropic", None, "narration") == "claude-fast-env"
    assert prov.model_for("anthropic", "claude-mine", "strategy_generation") == "claude-mine"
    assert prov.model_for("anthropic", "claude-mine", "narration") == "claude-fast-env"
    built = prov.build_provider("anthropic", "k", None, task="narration")
    assert built.model == "claude-fast-env" and built.effort == "low" and built.tier == "fast"
    assert prov.build_provider("anthropic", "k", "claude-mine", task="strategy_generation").effort == "medium"
    # Pro and o-series models carry their own prices; a prefix never maps them onto the cheaper base model.
    assert pricing.cost_usd("openai", "o3-pro", input_tokens=1_000_000, output_tokens=0).amount == pytest.approx(20.0)
    assert pricing.cost_usd("openai", "gpt-5-pro-2026", input_tokens=1_000_000, output_tokens=0).amount == pytest.approx(15.0)


# --- C3 ---------------------------------------------------------------------------------------------------------------------
def test_openai_uses_max_completion_tokens_skips_temperature_for_reasoning_models_and_logs_the_400_body(caplog):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if body["model"] == "bad-model":
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'temperature'", "type": "invalid_request_error"}})
        if body["model"] == "cut":
            return httpx.Response(200, json={"choices": [{"message": {"content": "half"}, "finish_reason": "length"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello from gpt"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 50}}})
    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert _run(OpenAIProvider("sk-openai", model="gpt-4.1-mini", client=mock).complete("s", "u", max_tokens=300)) == "hello from gpt"
    assert seen[-1]["max_completion_tokens"] == 300 and "max_tokens" not in seen[-1] and seen[-1]["temperature"] == 0.2
    result = _run(OpenAIProvider("sk-openai", model="o4-mini", client=mock, tier="fast").complete_full("s", "u", max_tokens=300))
    assert "temperature" not in seen[-1] and result.input_tokens == 100 and result.output_tokens == 20 and result.cache_read_tokens == 50
    # C1 on OpenAI too: a reasoning model's cap covers its reasoning, so the fast tier gets low effort plus the headroom.
    assert seen[-1]["reasoning_effort"] == "low" and seen[-1]["max_completion_tokens"] == 300 + prov.thinking_headroom("low")
    assert prov.is_reasoning_model("gpt-5-mini") and prov.is_reasoning_model("o3") and not prov.is_reasoning_model("gpt-4.1")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ProviderError) as info:
            _run(OpenAIProvider("sk-openai-secret", model="bad-model", client=mock).complete("s", "u"))
    assert "400" in str(info.value) and "Unsupported parameter" in str(info.value)
    assert any("Unsupported parameter" in r.getMessage() for r in caplog.records) and not any("sk-openai-secret" in r.getMessage() for r in caplog.records)
    with pytest.raises(ProviderError) as cut:
        _run(OpenAIProvider("sk-openai", model="cut", client=mock).complete("s", "u", max_tokens=100))
    assert "cut off" in str(cut.value) and seen[-1]["max_completion_tokens"] == 200       # retried once with double the budget


# --- C5 ---------------------------------------------------------------------------------------------------------------------
def test_pricing_knows_the_current_models_and_estimates_unknown_ones_conservatively(monkeypatch):
    usd = pricing.cost_usd("anthropic", "claude-sonnet-5-5", input_tokens=1_000_000, output_tokens=0)
    assert usd.amount == pytest.approx(2.0) and usd.estimated is False
    cached = pricing.cost_usd("anthropic", "claude-sonnet-5-5", input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000)
    assert cached.amount < usd.amount
    assert pricing.cost_usd("openai", "gpt-4.1-mini", input_tokens=1_000_000, output_tokens=1_000_000).amount == pytest.approx(0.4 + 1.6)
    unknown = pricing.cost_usd("anthropic", "claude-future-9", input_tokens=1_000_000, output_tokens=0)
    assert unknown.estimated is True and unknown.amount >= usd.amount
    monkeypatch.setenv("AI_MODEL_PRICES_JSON", json.dumps({"claude-future-9": [1.0, 2.0, 0.1]}))
    pricing.reload()
    assert pricing.cost_usd("anthropic", "claude-future-9", input_tokens=1_000_000, output_tokens=0).amount == pytest.approx(1.0)
    monkeypatch.delenv("AI_MODEL_PRICES_JSON")
    pricing.reload()
    monkeypatch.setenv("AI_USD_INR_RATE", "80")
    assert pricing.usd_to_inr(2.0) == pytest.approx(160.0)
    assert pricing.cost_usd("rule_based", "nlu-parser-v1", input_tokens=10, output_tokens=10).amount == 0.0


def test_every_call_is_metered_and_the_plan_budget_switches_the_tenant_to_rules(monkeypatch):
    import httpx2
    headers = {"Authorization": f"Bearer {_register('p08c-meter@example.com')}"}
    me = client.get("/api/auth/me", headers=headers).json()
    _upgrade_plan(me["tenant_id"], "pro")
    monkeypatch.setenv("AI_ANTHROPIC_FAST_MODEL", "claude-fast-env")
    monkeypatch.setenv("AI_ANTHROPIC_STRONG_MODEL", "claude-strong-env")
    # Typing the operator's current default (or nothing) pins no model: the row keeps "" and follows the environment.
    saved = client.put("/api/ai/provider", headers=headers, json={"provider": "anthropic", "api_key": "sk-ant-meter-key-1234", "model": "claude-strong-env"}).json()
    assert saved["model"] == "" and saved["models"] == {"strong": "claude-strong-env", "fast": "claude-fast-env"}
    saved = client.put("/api/ai/provider", headers=headers, json={"provider": "anthropic", "model": "claude-mine"}).json()
    assert saved["model"] == "claude-mine" and saved["models"]["strong"] == "claude-mine"

    def handler(request):
        # The response names the model that served the call (a server-side fallback may differ from the request): that is what is priced.
        return httpx2.Response(200, json=_msg("Grounded words.", model=json.loads(request.content)["model"],
                                               usage={"input_tokens": 3000, "output_tokens": 500, "cache_read_input_tokens": 2000, "cache_creation_input_tokens": 0}))

    async def call(task):
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            provider = await ai_settings.provider_for(session, tenant, client=_anthropic_mock(handler), task=task)
            text = await provider.complete("sys", "u", max_tokens=500)
            await session.commit()                 # the usage rows ride on the caller's transaction (every call site commits)
            return provider, text
    provider, text = _run(call("narration"))
    assert text == "Grounded words." and isinstance(provider, metering.MeteredProvider)
    assert provider.model == "claude-fast-env" and provider.name == "anthropic" and provider.feature == "narration"
    provider, _ = _run(call("strategy_generation"))
    assert provider.model == "claude-mine"

    async def rows():
        async with _session_factory() as session:
            return list(await session.scalars(select(UsageRecord).where(UsageRecord.tenant_id == me["tenant_id"], UsageRecord.metric.like("ai_%"))))
    recorded = _run(rows())
    by_metric = {}
    for r in recorded:
        by_metric.setdefault(r.metric, 0.0)
        by_metric[r.metric] += r.quantity
    # tokens_input counts every prompt token - fresh and cached (cached ones are billed, at the lower cache rate)
    assert by_metric["ai_calls"] == 2 and by_metric["ai_tokens_input"] == 10000 and by_metric["ai_tokens_output"] == 1000 and by_metric["ai_cost_usd"] > 0
    meta = json.loads(next(r for r in recorded if r.metric == "ai_cost_usd").metadata_json)
    assert meta["feature"] in ("narration", "strategy_generation") and meta["provider"] == "anthropic" and meta["model"] in ("claude-fast-env", "claude-mine")

    # The Settings card shows the month's usage against the plan's budget.
    shown = client.get("/api/ai/provider", headers=headers).json()
    usage = shown["usage"]
    assert usage["calls"] == 2 and usage["tokens_input"] == 10000 and usage["spent_usd"] > 0 and usage["spent_inr"] > usage["spent_usd"]
    assert usage["budget_inr"] > 0 and usage["exhausted"] is False and set(usage["by_feature"]) == {"narration", "strategy_generation"}
    assert shown["models"] == {"strong": "claude-mine", "fast": "claude-fast-env"}

    # Spend past the plan's monthly budget: the tenant gets the rule-based provider (with the reason) until the month turns.
    async def spend():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            budget = metering.budget_inr(tenant)
            await metering.record(session, tenant.id, "narration", "anthropic", "claude-fast-env",
                                  prov.Completion(text="x", input_tokens=1, output_tokens=1, cost_usd_override=pricing.inr_to_usd(budget)))
    _run(spend())
    provider, text = _run(call("narration"))
    assert isinstance(provider, RuleBasedProvider) and "budget" in provider.reason
    shown = client.get("/api/ai/provider", headers=headers).json()
    assert shown["usage"]["exhausted"] is True and "budget" in shown["usage"]["note"]
    # A plan without a budget line (0) is uncapped; the Free plan never calls out anyway.
    assert metering.budget_inr(Tenant(name="x", plan="free", status="ACTIVE")) == 0.0
    # Metering never breaks the AI call: a failing recorder is logged, the answer still comes back.
    async def broken(*_a, **_k):
        raise RuntimeError("db down")
    monkeypatch.setattr(metering, "record", broken)
    _upgrade_plan(me["tenant_id"], "business")           # a fresh (bigger) budget
    provider, text = _run(call("narration"))
    assert text == "Grounded words."


def test_truncated_answers_are_metered_and_fall_back_to_the_rules(monkeypatch):
    import httpx2
    from app.ai import copilot
    headers = {"Authorization": f"Bearer {_register('p08c-trunc@example.com')}"}
    me = client.get("/api/auth/me", headers=headers).json()
    _upgrade_plan(me["tenant_id"], "pro")
    assert client.put("/api/ai/provider", headers=headers, json={"provider": "anthropic", "api_key": "sk-ant-trunc-key-1234"}).status_code == 200

    def cut(request):
        return httpx2.Response(200, json=_msg("Index at 25,0", stop="max_tokens", usage={"input_tokens": 10, "output_tokens": 50}))

    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            provider = await ai_settings.provider_for(session, tenant, client=_anthropic_mock(cut), task="copilot")
            text, why = await copilot.narrate(provider, "en", "market", "how is the index?", ["Index at 25,000"])
            n = await session.scalar(select(UsageRecord.quantity).where(UsageRecord.tenant_id == tenant.id, UsageRecord.metric == "ai_tokens_output"))
            return text, why, n
    text, why, n = _run(go())
    assert text is None and "cut off" in why and n == 100          # two attempts, both billed, nothing partial shown
