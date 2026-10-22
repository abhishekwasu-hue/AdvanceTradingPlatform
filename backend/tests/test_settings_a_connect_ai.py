"""Settings redesign part A: "Connect AI in one paste" - one key for every AI feature (docs/design/SETTINGS_REDESIGN_REVIEW.md
R1-R6 and A1-A6; owner questions SR-1..SR-8 with their provisional defaults).

Every key here is an obviously fake string and every provider a mocked transport: no test reaches a real provider.
"""
import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.ai import byok, key_formats, resolve
from app.ai import providers as prov
from app.ai import settings as ai_settings
from app.ai.metering import AI_METRICS, MeteredProvider
from app.ai.providers import ProviderError, RuleBasedProvider
from app.db.models import AuditLogRecord, LlmCallRecord, Tenant, UsageRecord, User, UserAiKeyRecord
from app.platform import controls
from tests.test_auth_api import _register, _session_factory, client
from tests.test_phase_l_ai import _anthropic_mock

A_KEY = "sk-ant-api03-FAKEFAKE-test-only-aaaaaaaaaaaaaaaaaaaa-a9F2"
B_KEY = "sk-ant-api03-FAKEFAKE-test-only-bbbbbbbbbbbbbbbbbbbb-2222"
TENANT_KEY = "sk-ant-api03-FAKEFAKE-test-only-tenant-tttttttttttt-7777"
OPENAI_KEY = "sk-proj-FAKEFAKE-test-only-cccccccccccccccccccc-3333"
GEMINI_KEY = "AIzaFAKEFAKE-test-only-dddddddddddddddddd-4444"
FIXTURE = Path(__file__).parent / "fixtures" / "key_formats.json"
FRONTEND_FIXTURE = Path(__file__).resolve().parents[2] / "frontend" / "src" / "aiKeys" / "__fixtures__" / "key_formats.json"


def _run(coro):
    return asyncio.run(coro)


async def _set_flag(name, on):
    async with _session_factory() as session:
        current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
        flags = dict(current.get("flags") or {})
        flags[name] = {"on": on, "tenants": []}
        await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
        await session.commit()


def _flags(**values):
    for name, on in values.items():
        _run(_set_flag(name, on))


@pytest.fixture
def one_key():
    _flags(ui_v2=True, ai_one_key=True)
    yield
    _flags(ui_v2=False, ai_one_key=False, vision_byok=False)


def _user(email, plan="pro"):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()

    async def go():
        async with _session_factory() as session:
            (await session.get(Tenant, me["tenant_id"])).plan = plan
            await session.commit()
    _run(go())
    return headers, me


class Mocks:
    """One mock transport per provider: records the key, the model and whether an image came along. `fail` answers with
    the given provider error (its body echoes the key - the worst case for leaking it)."""

    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []

    def _note(self, provider, key, body):
        text = json.dumps(body)
        self.calls.append({"provider": provider, "key": key, "model": body.get("model"),
                           "image": any(marker in text for marker in ('"image"', '"image_url"', '"inlineData"'))})

    def anthropic(self):
        import httpx2

        def handler(request):
            body = json.loads(request.content or b"{}")
            self._note("anthropic", request.headers.get("x-api-key"), body)
            if self.fail == "auth":
                return httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": f"bad {request.headers.get('x-api-key')}"}})
            if self.fail == "no_credit":
                return httpx2.Response(400, json={"type": "error", "error": {"type": "invalid_request_error",
                                                                             "message": "Your credit balance is too low to access the Anthropic API."}})
            return httpx2.Response(200, json={"id": "msg_1", "type": "message", "role": "assistant", "model": body.get("model"),
                                              "stop_sequence": None, "stop_reason": "end_turn", "content": [{"type": "text", "text": "OK"}],
                                              "usage": {"input_tokens": 30, "output_tokens": 2}})
        return _anthropic_mock(handler)

    def openai(self):
        def handler(request):
            body = json.loads(request.content or b"{}")
            key = request.headers.get("authorization", "").removeprefix("Bearer ")
            self._note("openai", key, body)
            if self.fail == "no_credit":
                return httpx.Response(429, json={"error": {"message": "You exceeded your current quota", "type": "insufficient_quota", "code": "insufficient_quota"}})
            if self.fail == "rate":
                return httpx.Response(429, json={"error": {"message": "Rate limit reached", "type": "requests", "code": "rate_limit_exceeded"}})
            return httpx.Response(200, json={"model": body.get("model"), "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                                             "usage": {"prompt_tokens": 30, "completion_tokens": 1}})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def gemini(self):
        def handler(request):
            body = json.loads(request.content or b"{}")
            self._note("gemini", request.headers.get("x-goog-api-key"), {**body, "model": request.url.path.split("/")[-1]})
            if self.fail == "no_credit":
                return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                                           "message": "You exceeded your current quota, please check your plan and billing details."}})
            if self.fail == "no_vision":
                return httpx.Response(400, json={"error": {"code": 400, "message": "This model does not support image input."}})
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "OK"}], "role": "model"}, "finishReason": "STOP"}],
                                             "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 1}})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def install(self, monkeypatch):
        original = prov.build_byok_provider

        def build(name, api_key, model, client=None, **kw):
            return original(name, api_key, model, client=getattr(self, name)(), **kw)
        monkeypatch.setattr(prov, "build_byok_provider", build)
        return self


async def _rows(model, *where):
    async with _session_factory() as session:
        return list(await session.scalars(select(model).where(*where)))


def _connect(headers, key, **extra):
    return client.post("/api/ai/byok/connect", headers=headers, json={"api_key": key, **extra})


# --- the key-format table (R5) ----------------------------------------------------------------------------------------------

def test_the_table_and_the_shared_fixture_agree_and_the_frontend_copy_is_byte_identical():
    data = json.loads(FIXTURE.read_text())
    assert data["table"] == key_formats.table()
    assert data["session_markers"] == list(key_formats.SESSION_MARKERS)
    assert data["may_train_on_free_tier"] == key_formats.MAY_TRAIN_ON_FREE_TIER
    assert FRONTEND_FIXTURE.read_bytes() == FIXTURE.read_bytes()
    for case in data["cases"]:
        found = key_formats.detect_provider(case["key"])
        assert (found.kind, found.provider) == (case["kind"], case["provider"]), case["key"]


def test_openrouter_and_groq_keys_are_never_taken_as_openai_keys():
    for key, name in (("sk-or-v1-FAKEFAKE-test-only-eeeeeeeeeeeeeeeeeeee-5555", "OpenRouter"),
                      ("gsk_FAKEFAKE-test-only-ffffffffffffffffffffffff-6666", "Groq")):
        with pytest.raises(byok.ByokError) as info:
            byok.validate_key_shape("openai", key)
        assert f"cannot use {name} keys yet" in str(info.value) and key not in str(info.value)
    assert byok.validate_key_shape("openai", "sk-svcacct-FAKEFAKE-test-only-cccccccccccccccccc-3333")


def test_session_and_admin_shapes_are_refused_before_any_provider_prefix():
    for key in ("sk-ant-sid01-FAKEFAKE-test-only-session-iiiiiiiiii", "sessionKey=sk-ant-api03-FAKEFAKE0000000000"):
        assert key_formats.detect_provider(key).kind == "session"
    assert key_formats.detect_provider("sk-ant-admin01-FAKEFAKE-test-only-hhhhhhhhhhhhhhhh").kind == "admin"


# --- error kinds (R6) --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("provider,key,fail,kind", [
    ("anthropic", A_KEY, "auth", "auth"),
    ("anthropic", A_KEY, "no_credit", "no_credit"),
    ("openai", OPENAI_KEY, "no_credit", "no_credit"),
    ("openai", OPENAI_KEY, "rate", "rate"),
    ("gemini", GEMINI_KEY, "no_credit", "no_credit"),
    ("gemini", GEMINI_KEY, "no_vision", "no_vision"),
])
def test_every_provider_error_carries_its_kind_from_the_providers_own_code(provider, key, fail, kind):
    mocks = Mocks(fail=fail)
    llm = prov.build_byok_provider(provider, key, "", client=getattr(mocks, provider)(), task="copilot")
    with pytest.raises(ProviderError) as info:
        _run(llm.complete_full("s", "u", max_tokens=8))
    assert info.value.kind == kind
    assert byok.plain_error(kind) in byok.PLAIN_ERRORS.values()


def test_the_plain_sentences_are_the_ones_the_order_names():
    assert byok.PLAIN_ERRORS["auth"] == "This key did not work. Check that you copied all of it."
    assert byok.PLAIN_ERRORS["no_credit"] == "This key has no credit left."
    assert byok.PLAIN_ERRORS["rate"] == "Too many requests. Try again in a minute."
    assert byok.PLAIN_ERRORS["no_vision"] == "This provider cannot read charts. Vision needs Gemini, Claude, OpenAI, OpenRouter, Mistral or Cloudflare Workers AI."


def test_a_user_key_runs_the_tasks_tier_model_and_gemini_has_tiers():
    models = prov.default_models()["gemini"]
    assert set(models) == {"strong", "fast", "cheap"}
    llm = prov.build_byok_provider("gemini", GEMINI_KEY, "pinned-model", task="copilot")
    assert llm.model == models[prov.TASK_TIERS["copilot"]]
    assert prov.build_byok_provider("gemini", GEMINI_KEY, "gemini-2.5-flash").model == "gemini-2.5-flash"   # Vision: as before


# --- flag OFF: exactly today -------------------------------------------------------------------------------------------------

def test_with_the_flag_off_the_connect_routes_answer_404_and_every_call_gets_todays_provider(monkeypatch):
    headers, me = _user("seta-off@example.com")
    for method, path in (("get", "/api/ai/byok/connection"), ("post", "/api/ai/byok/connect"), ("delete", "/api/ai/byok/connection"),
                         ("put", "/api/ai/byok/limits"), ("post", "/api/ai/byok/acknowledge"), ("post", "/api/ai/byok/copy-tenant-key")):
        body = {"api_key": A_KEY} if path.endswith("connect") else {"kind": "consent"} if path.endswith("acknowledge") else {}
        response = getattr(client, method)(path, headers=headers, **({"json": body} if method != "get" and method != "delete" else {}))
        assert response.status_code == 404, path
    sentinel = object()
    seen = []

    async def todays(session, tenant, task="general", user_id=None, **kw):
        seen.append((task, user_id, kw))
        return sentinel

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            tenant = await session.get(Tenant, me["tenant_id"])
            # A key saved on the old Vision path exists - with the flag off it is never used for these calls.
            session.add(UserAiKeyRecord(tenant_id=user.tenant_id, user_id=user.id, provider="anthropic",
                                        encrypted_key=byok.seal_key(A_KEY, tenant_id=user.tenant_id, user_id=user.id, provider="anthropic"),
                                        key_hint=byok.key_hint(A_KEY), model=""))
            await session.commit()
            return [await resolve.provider_for_user(session, user, tenant, task) for task in ("copilot", "knowledge", "screener_nl")]
    monkeypatch.setattr(ai_settings, "provider_for", todays)
    assert _run(go()) == [sentinel, sentinel, sentinel]
    assert seen == [("copilot", me["id"], {}), ("knowledge", me["id"], {}), ("screener_nl", me["id"], {})]
    home = client.get("/api/ai/chat/home", headers=headers)
    assert home.status_code == 404                                          # ui_v2 off: the v2 Copilot is not there


def test_with_ui_v2_on_and_ai_one_key_off_the_copilot_state_is_unchanged():
    _flags(ui_v2=True)
    try:
        headers, _ = _user("seta-off-v2@example.com")
        ai = client.get("/api/ai/chat/home", headers=headers).json()["ai"]
        assert set(ai) == {"ready", "title", "line", "settings", "reason"} and ai["ready"] is False
        assert client.get("/api/ai/byok/connection", headers=headers).status_code == 404
    finally:
        _flags(ui_v2=False)


# --- the connect flow (A2 / A3 / A4) ---------------------------------------------------------------------------------------

def test_connect_needs_the_consent_line_first_then_saves_tests_with_an_image_and_shows_connected(one_key, monkeypatch):
    mocks = Mocks().install(monkeypatch)
    headers, me = _user("seta-connect@example.com")
    assert client.get("/api/ai/byok/connection", headers=headers).json()["connected"] is False
    refused = _connect(headers, f"  {A_KEY}\n")
    assert refused.status_code == 400 and refused.json()["detail"]["code"] == "consent_required"
    assert refused.json()["detail"]["message"] == ("Your questions and chart images are sent to Claude to answer you. "
                                                   "Never your broker login or passwords.")
    assert _run(_rows(UserAiKeyRecord, UserAiKeyRecord.user_id == me["id"])) == [] and mocks.calls == []
    done = _connect(headers, f"  {A_KEY}\n", consent=True)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["connected"] is True and body["label"] == "Claude" and body["key_end"] == "a9F2" and body["ok"] is True
    assert body["consent"]["accepted"] is True and body["error"] is None and A_KEY not in done.text
    assert body["limits"]["line"].startswith("AI this month: ₹") and "limit" in body["limits"]["line"]
    assert [c["image"] for c in mocks.calls] == [True] and mocks.calls[0]["key"] == A_KEY
    # "Connected" is the key row only: the Vision background reviews stay off until the user turns them on.
    allowed, reason = _run(_allowed(me["id"]))
    assert (allowed, reason) == (False, byok.REASON_VISION_OFF)
    audit = [r.event for r in _run(_rows(AuditLogRecord, AuditLogRecord.user_id == me["id"]))]
    assert "ai_byok_consent_accepted" in audit and "byok_key_saved" in audit and "byok_key_tested" in audit
    assert A_KEY not in json.dumps([r.detail for r in _run(_rows(AuditLogRecord, AuditLogRecord.user_id == me["id"]))])
    gone = client.delete("/api/ai/byok/connection", headers=headers).json()
    assert gone["connected"] is False and _run(_rows(UserAiKeyRecord, UserAiKeyRecord.user_id == me["id"])) == []


async def _allowed(user_id):
    async with _session_factory() as session:
        return await byok.byok_allowed(session, user_id)


@pytest.mark.parametrize("fail,provider,key,sentence", [
    ("auth", "anthropic", A_KEY, "This key did not work. Check that you copied all of it."),
    ("no_credit", "openai", OPENAI_KEY, "This key has no credit left."),
    ("rate", "openai", OPENAI_KEY, "Too many requests. Try again in a minute."),
    ("no_vision", "gemini", GEMINI_KEY, "This provider cannot read charts. Vision needs Gemini, Claude, OpenAI, OpenRouter, Mistral or Cloudflare Workers AI."),
])
def test_a_failed_test_keeps_the_key_and_shows_one_plain_sentence(one_key, monkeypatch, fail, provider, key, sentence):
    Mocks(fail=fail).install(monkeypatch)
    headers, me = _user(f"seta-fail-{fail}-{provider}@example.com")
    body = _connect(headers, key, consent=True).json()
    assert body["connected"] is True and body["ok"] is False and body["error"] == sentence and key not in json.dumps(body)
    row = _run(_rows(UserAiKeyRecord, UserAiKeyRecord.user_id == me["id"]))[0]
    assert row.last_test_error == sentence and key not in (row.last_test_error or "")


def test_an_unknown_format_asks_for_the_provider_and_unsupported_keys_get_a_plain_line(one_key, monkeypatch):
    mocks = Mocks().install(monkeypatch)
    headers, _ = _user("seta-unknown@example.com")
    unknown = "FAKEFAKE-test-only-no-known-prefix-llllllllllll"
    asked = _connect(headers, unknown, consent=True)
    assert asked.status_code == 400 and asked.json()["detail"]["code"] == "provider_needed"
    picked = _connect(headers, unknown, consent=True, provider="gemini").json()
    assert picked["connected"] is True and picked["label"] == "Google Gemini" and mocks.calls[0]["provider"] == "gemini"
    # MO2 A: OpenRouter and Groq are taken now; an xAI key still gets the plain line, naming the services that work.
    refused = _connect(headers, "xai-FAKEFAKE-test-only-gggggggggggggggggggggggg-7777", consent=True)
    assert refused.status_code == 400 and "cannot use xAI keys" in refused.json()["detail"]["message"]
    assert "OpenRouter, Groq, Mistral, Z.ai or Cloudflare Workers AI" in refused.json()["detail"]["message"]
    session_token = _connect(headers, "sk-ant-sid01-FAKEFAKE-test-only-session-iiiiiiiiii", consent=True)
    assert session_token.status_code == 400 and "not an API key" in session_token.json()["detail"]["message"]


def test_a_gemini_key_shows_the_free_tier_line_and_chart_analysis_waits_for_the_paid_plan_tick(one_key, monkeypatch):
    mocks = Mocks().install(monkeypatch)
    headers, me = _user("seta-gemini@example.com")
    body = _connect(headers, GEMINI_KEY, consent=True).json()
    assert body["may_train"] is True and body["paid_plan"] is False and body["free_tier_warning"].startswith("Free keys from Google Gemini")
    with pytest.raises(Exception) as info:
        _run(_chart_ask(me))
    assert getattr(info.value, "code", None) == resolve.PAID_PLAN_CODE
    calls_before = len(mocks.calls)
    assert calls_before == 1                                    # only the connect test: no private prompt went out
    confirmed = client.post("/api/ai/byok/acknowledge", headers=headers, json={"kind": "paid_plan"}).json()
    assert confirmed["paid_plan"] is True
    answer = _run(_chart_ask(me))
    assert answer.billed_to_user_key is True and answer.error is None and mocks.calls[-1]["image"] is True


async def _chart_ask(me, settings=None):
    from datetime import datetime, timezone

    from app.chart_analyze import engine
    from app.chart_analyze.provider import Instructions
    async with _session_factory() as session:
        user = await session.get(User, me["id"])
        tenant = await session.get(Tenant, me["tenant_id"])
        out = await engine.ask(session, user, tenant, Instructions("system", "user", "test-v1"), byok.TEST_IMAGE_PNG,
                               settings or {"estimate_input_tokens": 100, "estimate_output_tokens": 50, "max_output_tokens": 64},
                               now=datetime.now(timezone.utc))
        await session.commit()
        return out


# --- one key, every feature (A5) -----------------------------------------------------------------------------------------------

def test_one_key_serves_copilot_vision_and_chart_analysis_and_is_billed_to_the_user(one_key, monkeypatch):
    mocks = Mocks().install(monkeypatch)
    headers, me = _user("seta-onekey@example.com")
    assert _connect(headers, A_KEY, consent=True).json()["ok"] is True

    async def copilot():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            tenant = await session.get(Tenant, me["tenant_id"])
            llm = await resolve.provider_for_user(session, user, tenant, "copilot")
            assert isinstance(llm, resolve.UserKeyProvider) and llm.supports_tools
            await llm.complete_full("system", "a question", max_tokens=16)
            await session.commit()
    _run(copilot())
    answer = _run(_chart_ask(me))
    assert answer.billed_to_user_key is True and answer.provider == "anthropic"
    # Vision keeps its own switch, and reads the same key row.
    _flags(vision_byok=True)
    assert client.put("/api/ai/byok/prefs", headers=headers, json={"enabled": True}).status_code == 200
    assert _run(_allowed(me["id"])) == (True, byok.REASON_OK)
    assert {c["key"] for c in mocks.calls} == {A_KEY}
    assert mocks.calls[1]["model"] == prov.default_models()["anthropic"][prov.TASK_TIERS["copilot"]]
    calls = _run(_rows(LlmCallRecord, LlmCallRecord.user_id == me["id"]))
    assert {c.feature for c in calls} == {"byok_test", "copilot", "chart_analyze"} and all(c.billed_to_user_key for c in calls)
    assert _run(_rows(UsageRecord, UsageRecord.tenant_id == me["tenant_id"], UsageRecord.metric.in_(AI_METRICS))) == []


def test_resolution_order_user_key_then_the_organisations_key_then_the_rules(one_key, monkeypatch):
    Mocks().install(monkeypatch)
    headers, me = _user("seta-order@example.com")

    async def resolved(task="copilot"):
        async with _session_factory() as session:
            return await resolve.provider_for_user(session, await session.get(User, me["id"]), await session.get(Tenant, me["tenant_id"]), task)
    assert isinstance(_run(resolved()), RuleBasedProvider)                     # 3. nothing: the rules
    with pytest.raises(HTTPException) as info:
        _run(_refuse(me, _run(resolved())))
    assert info.value.detail == {"code": "ai_not_connected", "message": resolve.NOT_CONNECTED_MESSAGE}
    _run(_tenant_key(me))
    assert isinstance(_run(resolved()), MeteredProvider)                       # 2. the organisation's key
    _connect(headers, A_KEY, consent=True)
    assert isinstance(_run(resolved()), resolve.UserKeyProvider)               # 1. the user's own key


async def _refuse(me, provider):
    async with _session_factory() as session:
        await resolve.refuse_if_not_connected(session, me["tenant_id"], provider)


async def _tenant_key(me, entered_by=None):
    from app.ai import compliance_terms as terms
    async with _session_factory() as session:
        user = await session.get(User, me["id"])
        await terms.accept(session, user, terms.KIND_DATA, terms.DATA_CONSENT_VERSION)
        record = await ai_settings.save_config(session, user, provider="anthropic", model="", api_key=TENANT_KEY)
        if entered_by is not None:
            record.updated_by = entered_by
            await session.commit()


def test_a_key_without_its_consent_is_not_used(one_key, monkeypatch):
    Mocks().install(monkeypatch)
    _, me = _user("seta-noconsent@example.com")

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            await byok.save_key(session, user, provider="anthropic", api_key=A_KEY)        # the old Vision path: no consent line
            return await resolve.provider_for_user(session, user, await session.get(Tenant, me["tenant_id"]), "copilot")
    assert isinstance(_run(go()), RuleBasedProvider)


def test_another_users_key_is_never_used(one_key, monkeypatch):
    mocks = Mocks().install(monkeypatch)
    a_headers, _ = _user("seta-iso-a@example.com")
    _connect(a_headers, A_KEY, consent=True)
    _, b = _user("seta-iso-b@example.com")

    async def go():
        async with _session_factory() as session:
            return await resolve.provider_for_user(session, await session.get(User, b["id"]), await session.get(Tenant, b["tenant_id"]), "copilot")
    assert isinstance(_run(go()), RuleBasedProvider)
    assert {c["key"] for c in mocks.calls} == {A_KEY} and len(mocks.calls) == 1


def test_at_the_cap_a_plain_hard_stop_never_the_organisations_key(one_key, monkeypatch):
    mocks = Mocks().install(monkeypatch)
    headers, me = _user("seta-cap@example.com")
    _run(_tenant_key(me))
    _connect(headers, A_KEY, consent=True)
    limits = client.put("/api/ai/byok/limits", headers=headers, json={"daily_inr": 0, "monthly_inr": 0}).json()["limits"]
    assert limits["daily_cap_inr"] == 0 and limits["reached"] == "day"

    async def go():
        async with _session_factory() as session:
            return await resolve.provider_for_user(session, await session.get(User, me["id"]), await session.get(Tenant, me["tenant_id"]), "copilot")
    with pytest.raises(HTTPException) as info:
        _run(go())
    assert info.value.status_code == 409 and info.value.detail["code"] == "ai_limit_reached"
    assert info.value.detail["message"].startswith("You reached your AI limit for today")
    assert TENANT_KEY not in {c["key"] for c in mocks.calls}


def test_the_limits_are_typed_in_rupees_and_stored_in_usd(one_key, monkeypatch):
    from app.ai import pricing
    Mocks().install(monkeypatch)
    headers, me = _user("seta-limits@example.com")
    _connect(headers, A_KEY, consent=True)
    out = client.put("/api/ai/byok/limits", headers=headers, json={"daily_inr": 100, "monthly_inr": 500}).json()["limits"]
    assert out["daily_cap_inr"] == pytest.approx(100, abs=0.05) and out["monthly_cap_inr"] == pytest.approx(500, abs=0.05)
    prefs = _run(_prefs(me))
    assert prefs.monthly_cap_usd == pytest.approx(500 / pricing.usd_inr_rate(), rel=1e-4)


async def _prefs(me):
    async with _session_factory() as session:
        return await byok.get_prefs(session, await session.get(User, me["id"]))


# --- "Use this key for your account too" (R1 / SR-8) --------------------------------------------------------------------------

def test_only_the_owner_who_entered_the_organisations_key_may_copy_it_and_the_copy_is_resealed_and_audited(one_key, monkeypatch):
    mocks = Mocks().install(monkeypatch)
    headers, me = _user("seta-copy@example.com")
    _run(_tenant_key(me))
    offer = client.get("/api/ai/byok/connection", headers=headers).json()["copy_tenant_key"]
    assert offer == {"label": "Claude"}
    need = client.post("/api/ai/byok/copy-tenant-key", headers=headers, json={})
    assert need.status_code == 400 and need.json()["detail"]["code"] == "consent_required"
    done = client.post("/api/ai/byok/copy-tenant-key", headers=headers, json={"consent": True})
    assert done.status_code == 200 and done.json()["connected"] is True and TENANT_KEY not in done.text
    row = _run(_rows(UserAiKeyRecord, UserAiKeyRecord.user_id == me["id"]))[0]

    async def opened():
        async with _session_factory() as session:
            return byok.open_key(row, user=await session.get(User, me["id"]))
    assert _run(opened()) == TENANT_KEY and mocks.calls[-1]["key"] == TENANT_KEY
    assert "byok_key_copied_from_tenant" in [r.event for r in _run(_rows(AuditLogRecord, AuditLogRecord.user_id == me["id"]))]
    assert client.get("/api/ai/byok/connection", headers=headers).json()["copy_tenant_key"] is None


def test_an_owner_who_did_not_enter_the_key_and_a_member_cannot_copy_it(one_key, monkeypatch):
    Mocks().install(monkeypatch)
    headers, me = _user("seta-copy-other@example.com")
    _run(_tenant_key(me, entered_by=None))

    async def other_entered():
        async with _session_factory() as session:
            record = await ai_settings.get_config(session, me["tenant_id"])
            record.updated_by = None
            await session.commit()
    _run(other_entered())
    assert client.get("/api/ai/byok/connection", headers=headers).json()["copy_tenant_key"] is None
    assert client.post("/api/ai/byok/copy-tenant-key", headers=headers, json={"consent": True}).status_code == 404

    async def demote():
        async with _session_factory() as session:
            (await session.get(User, me["id"])).role = "USER"
            await session.commit()
    _run(demote())
    assert client.post("/api/ai/byok/copy-tenant-key", headers=headers, json={"consent": True}).status_code == 403
    assert _run(_rows(UserAiKeyRecord, UserAiKeyRecord.user_id == me["id"])) == []


# --- the Copilot on one key -----------------------------------------------------------------------------------------------------

def test_the_copilot_home_says_connected_on_the_users_own_key(one_key, monkeypatch):
    Mocks().install(monkeypatch)
    headers, _ = _user("seta-copilot@example.com")
    before = client.get("/api/ai/chat/home", headers=headers).json()["ai"]
    assert before["ready"] is False and before["code"] == "ai_not_connected" and before["own_key"] is False
    _connect(headers, A_KEY, consent=True)
    after = client.get("/api/ai/chat/home", headers=headers).json()["ai"]
    assert after["ready"] is True and after["own_key"] is True and after["code"] is None


def test_billed_to_user_key_marks_only_own_key_calls(one_key, monkeypatch):
    Mocks().install(monkeypatch)
    headers, me = _user("seta-billing@example.com")
    _connect(headers, A_KEY, consent=True)
    rows = _run(_rows(LlmCallRecord, LlmCallRecord.user_id == me["id"]))
    assert rows and all(r.billed_to_user_key for r in rows)
    assert A_KEY not in json.dumps([[r.system_text, r.user_text, r.response_text, r.status] for r in rows])
