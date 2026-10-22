"""Master Order 2 part A: AI provider choice with free options - OpenRouter, Groq, Mistral and Z.ai GLM next to Google
Gemini, Claude and OpenAI (behind `ui_v2` + `ai_one_key`; the Vision card without `ai_one_key` is unchanged).

Every key here is an obviously fake string and every provider a mocked transport: no test reaches a real provider.
"""
import json
from pathlib import Path

import httpx
import pytest

from app.ai import byok, key_formats, pricing, resolve
from app.ai import providers as prov
from app.ai.providers import ProviderError
from app.db.models import LlmCallRecord, Tenant, User, UserAiKeyRecord, UserVisionPrefsRecord
from tests.test_auth_api import _session_factory, client
from tests.test_settings_a_connect_ai import _chart_ask, _connect, _flags, _rows, _run, _user

FIXTURE = Path(__file__).parent / "fixtures" / "key_formats.json"
OPENROUTER_KEY = "sk-or-v1-FAKEFAKE-test-only-eeeeeeeeeeeeeeeeeeee-5555"
GROQ_KEY = "gsk_FAKEFAKE-test-only-ffffffffffffffffffffffff-6666"
MISTRAL_KEY = "FAKEFAKEfaketestonlymistral0000"
ZAI_KEY = "FAKEFAKEfaketestonlyzai00000000.FAKEFAKEtestonly"
KEYS = {"openrouter": OPENROUTER_KEY, "groq": GROQ_KEY, "mistral": MISTRAL_KEY, "zai": ZAI_KEY}
NEW = ("openrouter", "groq", "mistral", "zai")


@pytest.fixture
def one_key():
    _flags(ui_v2=True, ai_one_key=True)
    yield
    _flags(ui_v2=False, ai_one_key=False, vision_byok=False)


class Compat:
    """One mock transport for the OpenAI-compatible providers: records every request; `status` / `body` answer instead
    of the success reply (the error bodies echo the key - the worst case for leaking it)."""

    def __init__(self, status=200, body=None):
        self.status, self.body, self.requests = status, body, []

    def client(self):
        def handler(request):
            payload = json.loads(request.content or b"{}")
            self.requests.append({"url": str(request.url), "auth": request.headers.get("authorization", ""), "body": payload})
            if self.status != 200:
                return httpx.Response(self.status, json=self.body or {})
            return httpx.Response(200, json={"model": "some-routed-model", "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                                             "usage": {"prompt_tokens": 30, "completion_tokens": 1}})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def install(self, monkeypatch):
        original = prov.build_byok_provider

        def build(name, api_key, model, client=None, **kw):
            return original(name, api_key, model, client=self.client(), **kw)
        monkeypatch.setattr(prov, "build_byok_provider", build)
        return self


# --- the one table and the shared fixture ------------------------------------------------------------------------------------

def test_the_shared_fixture_holds_the_same_provider_facts_and_orders_as_the_server():
    data = json.loads(FIXTURE.read_text())
    assert data["table"] == key_formats.table()
    assert data["provider_facts"] == key_formats.facts()
    assert data["may_train_on_free_tier"] == key_formats.MAY_TRAIN_ON_FREE_TIER
    assert data["connect_order"] == list(key_formats.CONNECT_ORDER) == list(prov.CONNECT_PROVIDERS)
    assert data["picker_order"] == list(key_formats.PICKER_ORDER)
    assert key_formats.CONNECT_ORDER[0] == "gemini"                         # "Start with free Google Gemini" stays first
    assert set(key_formats.PROVIDER_FACTS) == set(prov.CONNECT_PROVIDERS)
    for provider, facts in key_formats.PROVIDER_FACTS.items():
        assert facts["vision"] == (provider in prov.VISION_PROVIDERS), provider
        assert facts["privacy"] and len(facts["privacy"]) < 160 and facts["name"]


def test_openrouter_and_groq_keys_have_their_own_rows_before_plain_openai():
    kinds = [(row["prefix"], row["provider"]) for row in key_formats.table()]
    assert kinds.index(("sk-or-", "openrouter")) < kinds.index(("sk-", "openai"))
    assert key_formats.detect_provider(OPENROUTER_KEY).provider == "openrouter"
    assert key_formats.detect_provider(GROQ_KEY).provider == "groq"
    # Mistral and Z.ai keys have no prefix: the picker asks.
    assert key_formats.detect_provider(MISTRAL_KEY).kind == "unknown" and key_formats.detect_provider(ZAI_KEY).kind == "unknown"
    assert {"mistral", "zai"} <= set(key_formats.PICKER_ORDER)


def test_the_key_shape_checks_of_the_new_providers():
    for provider, key in KEYS.items():
        assert byok.validate_key_shape(provider, key, one_key=True) == key
    with pytest.raises(byok.ByokError, match="starts with 'sk-or-'"):
        byok.validate_key_shape("openrouter", GROQ_KEY, one_key=True)
    with pytest.raises(byok.ByokError, match="starts with 'gsk_'"):
        byok.validate_key_shape("groq", MISTRAL_KEY, one_key=True)
    with pytest.raises(byok.ByokError, match="^This looks like an OpenRouter key, not a Mistral key$"):
        byok.validate_key_shape("mistral", OPENROUTER_KEY, one_key=True)
    with pytest.raises(byok.ByokError, match="^This looks like a Groq key, not a Z.ai GLM key$"):
        byok.validate_key_shape("zai", GROQ_KEY, one_key=True)
    with pytest.raises(byok.ByokError, match="only letters"):                  # only a Z.ai key may carry its one dot
        byok.validate_key_shape("mistral", ZAI_KEY, one_key=True)
    with pytest.raises(byok.ByokError):                                        # two dots: a token, never a key
        byok.validate_key_shape("zai", "FAKEFAKE.faketestonly.zai00000000", one_key=True)


def test_without_ai_one_key_the_vision_card_refuses_the_new_providers_exactly_as_before():
    for key, name in ((OPENROUTER_KEY, "OpenRouter"), (GROQ_KEY, "Groq")):
        with pytest.raises(byok.ByokError) as info:
            byok.validate_key_shape("openai", key)
        assert str(info.value) == f"This looks like {'an' if name == 'OpenRouter' else 'a'} {name} key. AiAlgoDesk cannot use {name} keys yet. Use a Claude, OpenAI or Google Gemini key."
    for provider in NEW:
        with pytest.raises(byok.ByokError, match="Unknown provider"):
            byok.validate_key_shape(provider, KEYS[provider])
    assert prov.BYOK_PROVIDERS == ("anthropic", "openai", "gemini")


# --- each provider's request shape (mocked HTTP) -------------------------------------------------------------------------------

@pytest.mark.parametrize("provider,url,field", [
    ("openrouter", "https://openrouter.ai/api/v1/chat/completions", "max_completion_tokens"),
    ("groq", "https://api.groq.com/openai/v1/chat/completions", "max_completion_tokens"),
    ("mistral", "https://api.mistral.ai/v1/chat/completions", "max_tokens"),
    ("zai", "https://api.z.ai/api/paas/v4/chat/completions", "max_tokens"),
])
def test_each_provider_gets_its_documented_url_auth_budget_field_and_tier_model(provider, url, field):
    mock = Compat()
    for task in ("strategy_generation", "copilot", "support"):
        llm = prov.build_byok_provider(provider, KEYS[provider], "", client=mock.client(), task=task)
        _run(llm.complete_full("system text", "user text", max_tokens=32))
    tiers = prov.default_models()[provider]
    assert [r["url"] for r in mock.requests] == [url] * 3
    assert {r["auth"] for r in mock.requests} == {f"Bearer {KEYS[provider]}"}
    assert [r["body"]["model"] for r in mock.requests] == [tiers["strong"], tiers["fast"], tiers["cheap"]]
    for r in mock.requests:
        other = "max_tokens" if field == "max_completion_tokens" else "max_completion_tokens"
        assert r["body"][field] >= 32 and other not in r["body"]
        assert r["body"]["messages"][0] == {"role": "system", "content": "system text"}
    assert set(tiers) == {"strong", "fast", "cheap"}


def test_the_free_options_are_the_default_where_a_provider_has_one_and_cost_nothing():
    assert set(prov.default_models()["openrouter"].values()) == {"openrouter/free"}
    assert prov.default_models()["zai"]["fast"] == "glm-4.5-flash"
    for provider, model in (("openrouter", "openrouter/free"), ("openrouter", "meta-llama/some-model:free"), ("zai", "glm-4.5-flash")):
        cost = pricing.cost_usd(provider, model, input_tokens=100_000, output_tokens=100_000)
        assert cost.amount == 0.0 and cost.estimated is False
    # A free router answer is recorded under the model asked for, so it stays free whatever model the router picked.
    mock = Compat()
    llm = prov.build_byok_provider("openrouter", OPENROUTER_KEY, "", client=mock.client(), task="copilot")
    assert _run(llm.complete_full("s", "u", max_tokens=8)).model == "openrouter/free"
    # A paid model of a new provider is never under-counted: it gets a row, or its provider's most expensive one.
    assert pricing.cost_usd("mistral", "mistral-large-latest", input_tokens=1_000_000, output_tokens=0).amount > 0
    assert pricing.price_for("groq", "openai/gpt-oss-999")[1] is True


def test_a_chart_goes_as_each_provider_documents_and_text_only_providers_are_refused_before_any_call():
    mock = Compat()
    png = byok.TEST_IMAGE_PNG
    mistral = prov.build_byok_provider("mistral", MISTRAL_KEY, "", client=mock.client(), task="copilot", vision=True)
    _run(mistral.complete_full("s", "u", max_tokens=8, image_png=png))
    openrouter = prov.build_byok_provider("openrouter", OPENROUTER_KEY, "", client=mock.client(), task="copilot", vision=True)
    _run(openrouter.complete_full("s", "u", max_tokens=8, image_png=png))
    mistral_part, openrouter_part = (r["body"]["messages"][1]["content"][1] for r in mock.requests)
    assert mistral_part["type"] == "image_url" and mistral_part["image_url"].startswith("data:image/png;base64,")
    assert openrouter_part["image_url"]["url"].startswith("data:image/png;base64,")
    assert mock.requests[0]["body"]["model"] in prov.VISION_MODELS["mistral"]
    for provider in ("groq", "zai"):
        with pytest.raises(ProviderError) as info:
            prov.build_byok_provider(provider, KEYS[provider], "", client=mock.client(), task="copilot", vision=True)
        assert info.value.kind == "no_vision"
    assert len(mock.requests) == 2
    assert prov.vision_model("mistral", "some-text-model") == prov.VISION_MODELS["mistral"][0]


# --- plain-word errors per provider (ProviderError.kind) ----------------------------------------------------------------------

@pytest.mark.parametrize("provider,status,body,kind", [
    ("openrouter", 401, {"error": {"code": 401, "message": f"No auth credentials found {OPENROUTER_KEY}"}}, "auth"),
    ("openrouter", 402, {"error": {"code": 402, "message": "This request requires more credits"}}, "no_credit"),
    ("openrouter", 429, {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-day"}}, "rate"),
    ("openrouter", 404, {"error": {"code": 404, "message": "No endpoints found"}}, "model"),
    ("groq", 401, {"error": {"message": "Invalid API Key", "type": "invalid_request_error", "code": "invalid_api_key"}}, "auth"),
    ("groq", 429, {"error": {"message": "Rate limit reached for model", "type": "tokens", "code": "rate_limit_exceeded"}}, "rate"),
    ("groq", 400, {"error": {"message": "The model has been decommissioned", "type": "invalid_request_error", "code": "model_decommissioned"}}, "model"),
    ("mistral", 401, {"message": "Unauthorized", "request_id": "x"}, "auth"),
    ("mistral", 429, {"message": "Requests rate limit exceeded"}, "rate"),
    ("zai", 429, {"error": {"code": "1113", "message": "Insufficient balance or no resource package. Please recharge."}}, "no_credit"),
    ("zai", 429, {"error": {"code": "1302", "message": "Rate limit reached for requests"}}, "rate"),
    ("zai", 401, {"error": {"code": "1000", "message": "Authentication failed"}}, "auth"),
])
def test_every_new_provider_error_maps_to_one_plain_sentence(provider, status, body, kind):
    mock = Compat(status=status, body=body)
    llm = prov.build_byok_provider(provider, KEYS[provider], "", client=mock.client(), task="copilot")
    with pytest.raises(ProviderError) as info:
        _run(llm.complete_full("s", "u", max_tokens=8))
    assert info.value.kind == kind
    assert KEYS[provider] not in str(info.value)
    assert byok.plain_error(kind) in byok.PLAIN_ERRORS.values()


def test_the_no_vision_sentence_names_every_provider_that_reads_charts():
    sentence = byok.PLAIN_ERRORS["no_vision"]
    for provider in prov.VISION_PROVIDERS:
        name = {"anthropic": "Claude", "gemini": "Gemini"}.get(provider, key_formats.PROVIDER_NAMES[provider])
        assert name in sentence, provider
    assert "Groq" not in sentence and "Z.ai" not in sentence


# --- the connect panel with the new providers ---------------------------------------------------------------------------------

def test_connect_takes_openrouter_and_groq_by_prefix_and_mistral_and_zai_from_the_picker(one_key, monkeypatch):
    mock = Compat().install(monkeypatch)
    for provider in NEW:
        headers, me = _user(f"mo2a-connect-{provider}@example.com")
        extra = {"provider": provider} if provider in ("mistral", "zai") else {}
        if extra:
            asked = _connect(headers, KEYS[provider], consent=True)
            assert asked.status_code == 400 and asked.json()["detail"]["code"] == "provider_needed"
        done = _connect(headers, KEYS[provider], consent=True, **extra)
        assert done.status_code == 200, done.text
        body = done.json()
        facts = key_formats.PROVIDER_FACTS[provider]
        assert body["connected"] is True and body["ok"] is True and body["provider"] == provider and body["label"] == facts["name"]
        assert body["vision"] is facts["vision"] and body["privacy"] == facts["privacy"] and body["may_train"] is facts["may_train"]
        assert (body["free_tier_warning"] is not None) is facts["may_train"]
        assert KEYS[provider] not in done.text
        row = _run(_rows(UserAiKeyRecord, UserAiKeyRecord.user_id == me["id"]))[0]
        assert row.provider == provider and len(row.provider) <= 20 and KEYS[provider] not in row.encrypted_key
    # The connect test sends the tiny image only to the providers that read charts.
    images = ["image_url" in json.dumps(r["body"]) for r in mock.requests]
    assert images == [True, False, True, False]


def test_a_may_train_provider_blocks_chart_analysis_until_a_paid_plan_is_confirmed(one_key, monkeypatch):
    mock = Compat().install(monkeypatch)
    headers, me = _user("mo2a-paid-plan@example.com")
    body = _connect(headers, MISTRAL_KEY, consent=True, provider="mistral").json()
    assert body["may_train"] is True and body["paid_plan"] is False
    calls = len(mock.requests)
    with pytest.raises(Exception) as info:
        _run(_chart_ask(me))
    assert getattr(info.value, "code", None) == resolve.PAID_PLAN_CODE and len(mock.requests) == calls   # nothing was sent
    assert client.post("/api/ai/byok/acknowledge", headers=headers, json={"kind": "paid_plan"}).json()["paid_plan"] is True
    answer = _run(_chart_ask(me))
    assert answer.billed_to_user_key is True and answer.provider == "mistral"
    assert "image_url" in json.dumps(mock.requests[-1]["body"])


def test_a_text_only_provider_is_refused_by_the_vision_features_with_the_plain_sentence(one_key, monkeypatch):
    mock = Compat().install(monkeypatch)
    headers, me = _user("mo2a-no-vision@example.com")
    body = _connect(headers, GROQ_KEY, consent=True).json()
    assert body["vision"] is False and body["may_train"] is False and body["paid_plan"] is True
    calls = len(mock.requests)

    async def vision_call():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            tenant = await session.get(Tenant, me["tenant_id"])
            return await resolve.provider_for_user(session, user, tenant, "chart_analyze", need_vision=True)
    with pytest.raises(ProviderError) as info:
        _run(vision_call())
    assert info.value.kind == "no_vision" and str(info.value) == byok.PLAIN_ERRORS["no_vision"]
    assert len(mock.requests) == calls

    async def copilot():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            tenant = await session.get(Tenant, me["tenant_id"])
            llm = await resolve.provider_for_user(session, user, tenant, "copilot")
            await llm.complete_full("system", "a question", max_tokens=16)
            await session.commit()
            return llm
    assert _run(copilot()).name == "groq"                                    # text features use it as usual
    logged = _run(_rows(LlmCallRecord, LlmCallRecord.user_id == me["id"]))
    assert {c.provider for c in logged} == {"groq"} and all(c.billed_to_user_key for c in logged)


def test_the_vision_reviews_never_pick_a_provider_added_for_the_connect_panel(one_key, monkeypatch):
    Compat().install(monkeypatch)
    headers, me = _user("mo2a-vision-card@example.com")
    _connect(headers, OPENROUTER_KEY, consent=True)

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            prefs = await byok.get_prefs(session, user)
            keys = await byok.list_keys(session, user)
            return prefs.provider, byok.active_provider(prefs, keys), (await byok.connected_key(session, user)).provider
    assert _run(go()) == ("openrouter", None, "openrouter")
    _flags(vision_byok=True)
    status = client.get("/api/ai/byok/status", headers=headers)
    assert status.status_code == 200                                         # the Vision card lists its three, as before
    assert [p["provider"] for p in status.json()["providers"]] == ["anthropic", "openai", "gemini"]
    assert status.json()["active_provider"] is None


def test_the_picker_route_accepts_only_known_provider_ids(one_key):
    headers, _ = _user("mo2a-picker@example.com")
    bad = _connect(headers, MISTRAL_KEY, consent=True, provider="cloudflare")
    assert bad.status_code == 422


def test_no_migration_is_needed_every_provider_id_fits_the_column():
    assert all(len(p) <= UserAiKeyRecord.__table__.c.provider.type.length for p in prov.CONNECT_PROVIDERS)
    assert UserVisionPrefsRecord.__table__.c.provider.type.length >= max(len(p) for p in prov.CONNECT_PROVIDERS)


def test_a_paid_openrouter_key_never_runs_on_the_free_models(one_key, monkeypatch):
    mock = Compat().install(monkeypatch)
    headers, me = _user("mo2a-openrouter-paid@example.com")
    body = _connect(headers, OPENROUTER_KEY, consent=True).json()
    assert body["may_train"] is True and body["vision"] is True
    assert mock.requests[-1]["body"]["model"] == "openrouter/free"            # the connect test runs on the free router
    with pytest.raises(Exception) as info:
        _run(_chart_ask(me))
    assert getattr(info.value, "code", None) == resolve.PAID_PLAN_CODE
    client.post("/api/ai/byok/acknowledge", headers=headers, json={"kind": "paid_plan"})
    answer = _run(_chart_ask(me))
    assert answer.provider == "openrouter" and mock.requests[-1]["body"]["model"] == prov.paid_model("openrouter") == "openrouter/auto"
    assert "image_url" in json.dumps(mock.requests[-1]["body"])
    # A paid model the price table does not know is counted at the most expensive row (never under-counted).
    assert pricing.price_for("openrouter", "openrouter/auto")[1] is True
