"""BYOK-V1 (docs/BYOK.md): "Vision with your own AI key" - per-user encrypted keys, key test, caps, settings.

Every key here is an obviously fake string and every provider is a mocked transport: no test reaches a real provider.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.ai import byok
from app.ai import providers as prov
from app.db.models import AuditLogRecord, LlmCallRecord, UsageRecord, User, UserAiKeyRecord, UserVisionPrefsRecord
from app.platform import controls
from tests.test_auth_api import _register, _session_factory, client
from tests.test_phase_l_ai import _anthropic_mock

A_ANTHROPIC = "sk-ant-api03-FAKEFAKE-test-only-aaaaaaaaaaaaaaaaaaaa-1111"
B_ANTHROPIC = "sk-ant-api03-FAKEFAKE-test-only-bbbbbbbbbbbbbbbbbbbb-2222"
OPENAI_KEY = "sk-proj-FAKEFAKE-test-only-cccccccccccccccccccc-3333"
GEMINI_KEY = "AIzaFAKEFAKE-test-only-dddddddddddddddddd-4444"
KEYS = {"anthropic": A_ANTHROPIC, "openai": OPENAI_KEY, "gemini": GEMINI_KEY}


def _run(coro):
    return asyncio.run(coro)


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _flag(on: bool) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags["vision_byok"] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


@pytest.fixture
def flag_on():
    _flag(True)
    yield
    _flag(False)


def _user(email):
    headers = _headers(_register(email))
    me = client.get("/api/auth/me", headers=headers).json()
    return headers, me


# --- mocked providers ---------------------------------------------------------------------------------------------------

class Mocks:
    """One mock transport per provider; records which key each request carried. `fail` makes a provider answer with an
    error whose body echoes the key back (the worst case for leaking it into stored errors or logs)."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.seen = {"anthropic": [], "openai": [], "gemini": []}
        self.urls: list = []

    def anthropic(self):
        import httpx2

        def handler(request):
            self.seen["anthropic"].append(request.headers.get("x-api-key"))
            if self.fail:
                return httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error",
                                                                             "message": f"invalid x-api-key {request.headers.get('x-api-key')}"}})
            return httpx2.Response(200, json={"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                                              "stop_sequence": None, "stop_reason": "end_turn", "content": [{"type": "text", "text": "OK"}],
                                              "usage": {"input_tokens": 30, "output_tokens": 2}})
        return _anthropic_mock(handler)

    def openai(self):
        def handler(request):
            key = request.headers.get("authorization", "").removeprefix("Bearer ")
            self.seen["openai"].append(key)
            if self.fail:
                return httpx.Response(400, json={"error": {"message": f"Incorrect API key provided: {key}"}})
            return httpx.Response(200, json={"model": "gpt-4.1-mini", "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                                             "usage": {"prompt_tokens": 30, "completion_tokens": 1}})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def gemini(self):
        def handler(request):
            self.urls.append(str(request.url))
            key = request.headers.get("x-goog-api-key")
            self.seen["gemini"].append(key)
            if self.fail:
                return httpx.Response(500, json={"error": {"message": f"backend error for key={key} ({key})"}})
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "OK"}], "role": "model"}, "finishReason": "STOP"}],
                                             "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 1, "thoughtsTokenCount": 4},
                                             "modelVersion": "gemini-2.5-flash"})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def install(self, monkeypatch):
        original = prov.build_byok_provider

        def build(name, api_key, model, client=None):
            return original(name, api_key, model, client=getattr(self, name)())
        monkeypatch.setattr(prov, "build_byok_provider", build)
        return self


async def _rows(model, *where):
    async with _session_factory() as session:
        return list(await session.scalars(select(model).where(*where)))


# --- flag -----------------------------------------------------------------------------------------------------------------

def test_flag_is_default_off_and_every_route_answers_404_while_off():
    assert "vision_byok" in controls.FEATURE_FLAGS and "vision_byok" in controls.DEFAULT_OFF_FLAGS

    async def default():
        async with _session_factory() as session:
            return (await controls.feature_flags(session))["vision_byok"]["on"]
    assert _run(default()) is False
    headers, _ = _user("byok-flag-off@example.com")
    calls = [client.get("/api/ai/byok/status", headers=headers),
             client.put("/api/ai/byok/key", headers=headers, json={"provider": "anthropic", "api_key": A_ANTHROPIC}),
             client.delete("/api/ai/byok/key/anthropic", headers=headers),
             client.post("/api/ai/byok/test/anthropic", headers=headers),
             client.put("/api/ai/byok/prefs", headers=headers, json={"enabled": True})]
    for response in calls:
        assert response.status_code == 404, response.text
        assert response.headers.get("X-Feature-Disabled") == "vision_byok"
    assert _run(_rows(UserAiKeyRecord, UserAiKeyRecord.user_id == client.get("/api/auth/me", headers=headers).json()["id"])) == []


def test_without_a_key_the_vision_line_says_add_your_api_key(flag_on):
    headers, _ = _user("byok-nokey@example.com")
    status = client.get("/api/ai/byok/status", headers=headers).json()
    assert status["vision_line"] == "Vision off - connect your AI key in Settings > AI"
    assert status["active"] is False and status["enabled"] is False
    assert [p["provider"] for p in status["providers"]] == ["anthropic", "openai", "gemini"]
    assert all(p["key_set"] is False and p["key_hint"] is None for p in status["providers"])
    consoles = {p["provider"]: p["console_url"] for p in status["providers"]}
    assert consoles == {"anthropic": "https://console.anthropic.com/settings/keys", "openai": "https://platform.openai.com/api-keys",
                        "gemini": "https://aistudio.google.com/app/apikey"}
    # The default model of each provider is the configured cost-efficient one, shown by id.
    assert {p["provider"]: p["model"] for p in status["providers"]} == byok.config.BYOK_VISION_DEFAULT_MODELS
    assert "API keys only" in status["notes"]["subscription"]


# --- storage ------------------------------------------------------------------------------------------------------------

def test_key_is_stored_encrypted_and_only_the_hint_ever_comes_back(flag_on):
    headers, me = _user("byok-store@example.com")
    saved = client.put("/api/ai/byok/key", headers=headers, json={"provider": "anthropic", "api_key": f"  {A_ANTHROPIC}  "})
    assert saved.status_code == 200, saved.text
    assert A_ANTHROPIC not in saved.text and "aaaaaaaaaaaa" not in saved.text
    row = next(p for p in saved.json()["providers"] if p["provider"] == "anthropic")
    assert row["key_set"] is True and row["key_hint"] == "sk-a...1111" == byok.key_hint(A_ANTHROPIC)
    status = client.get("/api/ai/byok/status", headers=headers)
    assert A_ANTHROPIC not in status.text and "aaaaaaaaaaaa" not in status.text

    rows = _run(_rows(UserAiKeyRecord, UserAiKeyRecord.user_id == me["id"]))
    assert len(rows) == 1
    record = rows[0]
    assert record.tenant_id == me["tenant_id"] and record.provider == "anthropic" and record.key_hint == "sk-a...1111"
    assert A_ANTHROPIC not in record.encrypted_key and "aaaaaaaaaaaa" not in record.encrypted_key
    assert record.encrypted_key.startswith(("t1:", "t2:"))           # the tenant's data key (envelope encryption)

    async def opened():
        async with _session_factory() as session:
            return byok.open_key(record, user=await session.get(User, me["id"]))
    assert _run(opened()) == A_ANTHROPIC

    # Replacing keeps one row per (user, provider).
    again = client.put("/api/ai/byok/key", headers=headers, json={"provider": "anthropic", "api_key": B_ANTHROPIC})
    assert again.status_code == 200 and B_ANTHROPIC not in again.text
    assert [r.key_hint for r in _run(_rows(UserAiKeyRecord, UserAiKeyRecord.user_id == me["id"]))] == ["sk-a...2222"]


def test_a_ciphertext_copied_to_another_user_or_provider_cannot_be_opened_and_is_never_used(flag_on, monkeypatch):
    mocks = Mocks().install(monkeypatch)
    a_headers, a = _user("byok-copy-a@example.com")
    b_headers, b = _user("byok-copy-b@example.com")
    assert client.put("/api/ai/byok/key", headers=a_headers, json={"provider": "anthropic", "api_key": A_ANTHROPIC}).status_code == 200
    assert client.put("/api/ai/byok/key", headers=b_headers, json={"provider": "anthropic", "api_key": B_ANTHROPIC}).status_code == 200

    async def copy_a_into_b():
        async with _session_factory() as session:
            a_row = await session.scalar(select(UserAiKeyRecord).where(UserAiKeyRecord.user_id == a["id"]))
            b_row = await session.scalar(select(UserAiKeyRecord).where(UserAiKeyRecord.user_id == b["id"]))
            b_row.encrypted_key = a_row.encrypted_key
            await session.commit()
            return a_row.encrypted_key
    a_cipher = _run(copy_a_into_b())
    tested = client.post("/api/ai/byok/test/anthropic", headers=b_headers)
    assert tested.status_code == 200, tested.text
    result = tested.json()["result"]
    assert result["ok"] is False and "could not be opened" in result["error"]
    assert mocks.seen["anthropic"] == []                           # nothing was sent - least of all A's key
    assert A_ANTHROPIC not in tested.text

    # Same organisation, other user: the sealed plaintext names its user, so the copy is refused too.
    async def same_tenant_copy():
        async with _session_factory() as session:
            owner = await session.get(User, a["id"])
            other = User(id=a["id"] + 100000, tenant_id=a["tenant_id"], email="x@example.com", hashed_password="!", is_active=True)
            copied = UserAiKeyRecord(tenant_id=a["tenant_id"], user_id=other.id, provider="anthropic", encrypted_key=a_cipher, key_hint="x")
            with pytest.raises(byok.KeyBindingError):
                byok.open_key(copied, user=other)
            # A row of another user is never opened for the caller, whatever it holds.
            with pytest.raises(byok.KeyBindingError):
                byok.open_key(copied, user=owner)
            # Another provider's row of the same user: refused (bound to the provider).
            moved = UserAiKeyRecord(tenant_id=a["tenant_id"], user_id=a["id"], provider="openai", encrypted_key=a_cipher, key_hint="x")
            with pytest.raises(byok.KeyBindingError):
                byok.open_key(moved, user=owner)
    _run(same_tenant_copy())


def test_user_b_can_neither_see_nor_test_nor_delete_user_a_key(flag_on, monkeypatch):
    mocks = Mocks().install(monkeypatch)
    a_headers, a = _user("byok-iso-a@example.com")
    b_headers, b = _user("byok-iso-b@example.com")
    assert client.put("/api/ai/byok/key", headers=a_headers, json={"provider": "anthropic", "api_key": A_ANTHROPIC}).status_code == 200

    status_b = client.get("/api/ai/byok/status", headers=b_headers)
    assert status_b.status_code == 200
    assert all(p["key_set"] is False and p["key_hint"] is None for p in status_b.json()["providers"])
    assert "sk-a...1111" not in status_b.text and A_ANTHROPIC not in status_b.text
    assert client.post("/api/ai/byok/test/anthropic", headers=b_headers).status_code == 404
    assert client.delete("/api/ai/byok/key/anthropic", headers=b_headers).status_code == 404
    assert mocks.seen["anthropic"] == []
    # No route takes a user id: an extra field is refused, not obeyed.
    assert client.put("/api/ai/byok/prefs", headers=b_headers, json={"user_id": a["id"], "enabled": True}).status_code == 422
    assert client.put("/api/ai/byok/key", headers=b_headers,
                      json={"provider": "anthropic", "api_key": B_ANTHROPIC, "user_id": a["id"]}).status_code == 422
    assert len(_run(_rows(UserAiKeyRecord, UserAiKeyRecord.user_id == a["id"]))) == 1
    assert _run(_rows(UserAiKeyRecord, UserAiKeyRecord.user_id == b["id"])) == []

    # B's own key is the only one B's test ever sends.
    assert client.put("/api/ai/byok/key", headers=b_headers, json={"provider": "anthropic", "api_key": B_ANTHROPIC}).status_code == 200
    assert client.post("/api/ai/byok/test/anthropic", headers=b_headers).json()["result"]["ok"] is True
    assert client.post("/api/ai/byok/test/anthropic", headers=a_headers).json()["result"]["ok"] is True
    assert mocks.seen["anthropic"] == [B_ANTHROPIC, A_ANTHROPIC]
    # B removes B's key; A's stays.
    assert client.delete("/api/ai/byok/key/anthropic", headers=b_headers).status_code == 200
    assert len(_run(_rows(UserAiKeyRecord, UserAiKeyRecord.user_id == a["id"]))) == 1


# --- key test through the provider seam -----------------------------------------------------------------------------------

@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_the_key_test_goes_through_the_provider_seam_and_is_metered_to_the_user_key(flag_on, monkeypatch, provider):
    mocks = Mocks().install(monkeypatch)
    headers, me = _user(f"byok-test-ok-{provider}@example.com")
    key = KEYS[provider]
    assert client.put("/api/ai/byok/key", headers=headers, json={"provider": provider, "api_key": key}).status_code == 200
    tested = client.post(f"/api/ai/byok/test/{provider}", headers=headers)
    assert tested.status_code == 200, tested.text
    body = tested.json()
    assert body["result"]["ok"] is True and body["result"]["error"] is None and key not in tested.text
    assert body["result"]["model"] == byok.default_model(provider)
    assert mocks.seen[provider] == [key]
    if provider == "gemini":
        assert all(key not in url for url in mocks.urls)        # the key travels in a header, never in the URL
    row = next(p for p in body["status"]["providers"] if p["provider"] == provider)
    assert row["last_test_ok"] is True and row["last_used_at"] and row["month_spend_usd"] > 0

    calls = _run(_rows(LlmCallRecord, LlmCallRecord.user_id == me["id"]))
    assert len(calls) == 1 and calls[0].billed_to_user_key is True and calls[0].feature == "byok_test"
    assert calls[0].provider == provider and calls[0].tenant_id == me["tenant_id"] and calls[0].cost_usd > 0
    # The user pays the provider: nothing lands in the plan's usage rows.
    assert _run(_rows(UsageRecord, UsageRecord.tenant_id == me["tenant_id"], UsageRecord.metric.like("ai_%"))) == []

    async def spend():
        async with _session_factory() as session:
            return (await byok.byok_spend(session, me["id"], provider, byok.day_start()),
                    await byok.byok_spend(session, me["id"], "openai" if provider != "openai" else "gemini", None))
    mine, other_provider = _run(spend())
    assert mine == pytest.approx(calls[0].cost_usd) and other_provider == 0.0


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_a_failed_key_test_records_a_sanitised_error_that_never_contains_the_key(flag_on, monkeypatch, caplog, provider):
    Mocks(fail=True).install(monkeypatch)
    headers, me = _user(f"byok-test-fail-{provider}@example.com")
    key = KEYS[provider]
    assert client.put("/api/ai/byok/key", headers=headers, json={"provider": provider, "api_key": key}).status_code == 200
    with caplog.at_level(logging.DEBUG):
        tested = client.post(f"/api/ai/byok/test/{provider}", headers=headers)
    assert tested.status_code == 200, tested.text
    result = tested.json()["result"]
    assert result["ok"] is False and result["error"]
    assert key not in tested.text and key[8:-4] not in tested.text
    record = _run(_rows(UserAiKeyRecord, UserAiKeyRecord.user_id == me["id"]))[0]
    assert record.last_test_ok is False and key not in (record.last_test_error or "") and len(record.last_test_error or "") <= 200
    for call in _run(_rows(LlmCallRecord, LlmCallRecord.user_id == me["id"])):
        assert key not in call.status and key not in (call.response_text or "") and call.billed_to_user_key is True
    assert all(key not in r.getMessage() for r in caplog.records)


def test_sanitize_error_removes_keys_and_tokens():
    text = f"boom {A_ANTHROPIC} Bearer abc.def key={GEMINI_KEY} {OPENAI_KEY} ya29.a0AfH6 risk-free"
    clean = byok.sanitize_error(text, [A_ANTHROPIC])
    for secret in (A_ANTHROPIC, GEMINI_KEY, OPENAI_KEY, "abc.def", "ya29.a0AfH6"):
        assert secret not in clean
    assert "risk-free" in clean and len(byok.sanitize_error("x" * 1000)) == 200


# --- caps ---------------------------------------------------------------------------------------------------------------

def test_byok_allowed_hard_stops_at_the_daily_and_the_monthly_cap(flag_on):
    headers, me = _user("byok-caps@example.com")
    other_headers, other = _user("byok-caps-other@example.com")
    now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

    async def allowed():
        async with _session_factory() as session:
            return await byok.byok_allowed(session, me["id"], now=now)
    assert _run(allowed()) == (False, "vision off")                    # never switched on
    assert client.put("/api/ai/byok/prefs", headers=headers, json={"enabled": True, "daily_cap_usd": 1.0, "monthly_cap_usd": 5.0}).status_code == 200
    assert _run(allowed()) == (False, "no key")
    assert client.put("/api/ai/byok/key", headers=headers, json={"provider": "openai", "api_key": OPENAI_KEY}).status_code == 200
    assert _run(allowed()) == (True, "ok")

    async def seed(user_id, tenant_id, cost, when, billed=True):
        async with _session_factory() as session:
            session.add(LlmCallRecord(tenant_id=tenant_id, user_id=user_id, feature="vision_audit", provider="openai", model="gpt-4.1-mini",
                                      system_sha256="0" * 64, user_sha256="0" * 64, system_text="", user_text="", status="ok",
                                      cost_usd=cost, created_at=when, billed_to_user_key=billed))
            await session.commit()
    # Spend that must not count: another user's own-key calls, this user's plan-billed calls, last month's calls.
    _run(seed(other["id"], other["tenant_id"], 50.0, now - timedelta(hours=1)))
    _run(seed(me["id"], me["tenant_id"], 50.0, now - timedelta(hours=1), billed=False))
    _run(seed(me["id"], me["tenant_id"], 50.0, datetime(2026, 9, 30, 23, 0, tzinfo=timezone.utc)))
    assert _run(allowed()) == (True, "ok")

    _run(seed(me["id"], me["tenant_id"], 0.6, now - timedelta(hours=2)))
    assert _run(allowed()) == (True, "ok")

    async def with_estimate(estimate):
        async with _session_factory() as session:
            return await byok.byok_allowed(session, me["id"], now=now, estimate_usd=estimate)
    assert _run(with_estimate(0.5)) == (False, "daily cap reached")    # the next call would cross the cap
    _run(seed(me["id"], me["tenant_id"], 0.4, now - timedelta(hours=1)))
    assert _run(allowed()) == (False, "daily cap reached")            # exactly at the cap: hard stop
    # The next day the daily cap is fresh, but the month has 1.0 of 5.0 spent; earlier days fill it.
    tomorrow = now + timedelta(days=1)

    async def allowed_at(when):
        async with _session_factory() as session:
            return await byok.byok_allowed(session, me["id"], now=when)
    assert _run(allowed_at(tomorrow)) == (True, "ok")
    for day in (3, 5, 7, 9):
        _run(seed(me["id"], me["tenant_id"], 1.0, datetime(2026, 10, day, 9, 0, tzinfo=timezone.utc)))     # 1.0 + 4.0 = the 5.0 cap
    assert _run(allowed_at(tomorrow)) == (False, "monthly cap reached")
    assert _run(allowed_at(datetime(2026, 11, 1, 0, 30, tzinfo=timezone.utc))) == (True, "ok")    # a new month

    # Raising the cap is the user's choice; the gate follows it.
    assert client.put("/api/ai/byok/prefs", headers=headers, json={"monthly_cap_usd": 20.0}).status_code == 200
    assert _run(allowed_at(tomorrow)) == (True, "ok")
    # The operator flag off -> vision off, whatever the user set.
    _flag(False)
    assert _run(allowed_at(tomorrow)) == (False, "vision off")
    _flag(True)


def test_the_key_test_is_refused_at_the_cap_with_a_notice(flag_on, monkeypatch):
    mocks = Mocks().install(monkeypatch)
    headers, me = _user("byok-cap-test@example.com")
    assert client.put("/api/ai/byok/key", headers=headers, json={"provider": "gemini", "api_key": GEMINI_KEY}).status_code == 200
    assert client.put("/api/ai/byok/prefs", headers=headers, json={"daily_cap_usd": 0.0}).status_code == 200
    refused = client.post("/api/ai/byok/test/gemini", headers=headers)
    assert refused.status_code == 409 and "daily cap reached" in refused.json()["detail"]
    assert mocks.seen["gemini"] == []
    status = client.get("/api/ai/byok/status", headers=headers).json()
    assert status["caps"]["daily_cap_usd"] == 0.0


# --- validation ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("provider,token", [
    ("anthropic", "sk-ant-oat01-FAKEFAKEsubscriptionoauthtoken0000"),       # Claude subscription OAuth access token
    ("anthropic", "sk-ant-ort01-FAKEFAKEsubscriptionrefreshtoken000"),
    ("anthropic", "sk-ant-sid01-FAKEFAKEclaudeaisessionkey00000000"),      # claude.ai browser session key
    ("anthropic", "sessionKey=sk-ant-sid01-FAKEFAKEcookie000000000000"),
    ("anthropic", "Bearer sk-ant-api03-FAKEFAKE000000000000000000"),
    ("openai", "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJGQUtFIn0.FAKEFAKEsignature"),  # a JWT
    ("openai", "sess-FAKEFAKEchatgptbrowsersession000000000"),
    ("gemini", "ya29.a0FAKEFAKEgoogleoauthaccesstoken00000000"),
    ("gemini", "__Secure-next-auth.session-token=FAKEFAKE00000000000"),
])
def test_session_cookies_oauth_and_subscription_tokens_are_refused(flag_on, provider, token):
    headers, me = _user(f"byok-sub-{abs(hash(token)) % 10**8}@example.com")
    refused = client.put("/api/ai/byok/key", headers=headers, json={"provider": provider, "api_key": token})
    assert refused.status_code == 400, refused.text
    assert "not an API key" in refused.json()["detail"] and token not in refused.text
    assert _run(_rows(UserAiKeyRecord, UserAiKeyRecord.user_id == me["id"])) == []


@pytest.mark.parametrize("provider,key,message", [
    ("anthropic", OPENAI_KEY, "starts with 'sk-ant-'"),
    ("openai", A_ANTHROPIC, "starts with 'sk-'"),
    ("gemini", OPENAI_KEY, "not a Google Gemini key"),
    ("openai", "sk-short", "20 to 256"),
    ("openai", "sk-" + "x" * 300, "20 to 256"),
    ("openai", "sk-FAKE key with spaces 000000000", "only letters"),
    ("anthropic", "sk-ant-admin01-FAKEFAKE000000000000000000", "admin key"),
    ("gemini", "   ", "Paste an API key"),
])
def test_key_shape_is_validated_per_provider(provider, key, message):
    with pytest.raises(byok.ByokError) as info:
        byok.validate_key_shape(provider, key)
    assert message in str(info.value) and (key.strip() == "" or key not in str(info.value))
    for name, good in KEYS.items():
        assert byok.validate_key_shape(name, good) == good


def test_an_over_long_key_is_refused_without_being_echoed(flag_on):
    headers, _ = _user("byok-long@example.com")
    long_key = "sk-ant-api03-" + "FAKE" * 200
    refused = client.put("/api/ai/byok/key", headers=headers, json={"provider": "anthropic", "api_key": long_key})
    assert refused.status_code == 422 and "FAKEFAKEFAKE" not in refused.text


def test_prefs_validate_caps_models_and_providers(flag_on):
    headers, me = _user("byok-prefs@example.com")
    assert client.put("/api/ai/byok/prefs", headers=headers, json={"daily_cap_usd": 20, "monthly_cap_usd": 10}).status_code == 400
    assert client.put("/api/ai/byok/prefs", headers=headers, json={"monthly_cap_usd": 100000}).status_code == 400
    assert client.put("/api/ai/byok/prefs", headers=headers, json={"daily_cap_usd": -1}).status_code == 422
    assert client.put("/api/ai/byok/prefs", headers=headers, json={"provider": "openai"}).status_code == 400          # no key yet
    assert client.put("/api/ai/byok/prefs", headers=headers, json={"models": {"openai": "gpt-4o-mini"}}).status_code == 400
    assert client.put("/api/ai/byok/key", headers=headers, json={"provider": "openai", "api_key": OPENAI_KEY}).status_code == 200
    assert client.put("/api/ai/byok/prefs", headers=headers, json={"models": {"openai": "some-expensive-model"}}).status_code == 400
    saved = client.put("/api/ai/byok/prefs", headers=headers, json={
        "enabled": True, "provider": "openai", "daily_cap_usd": 0.5, "monthly_cap_usd": 4,
        "audit": {"proposals": True, "scan_alerts": True, "daily_review": False}, "channels": {"telegram": True, "in_app": False},
        "models": {"openai": "gpt-4o-mini"}})
    assert saved.status_code == 200, saved.text
    status = saved.json()
    assert status["enabled"] is True and status["provider"] == "openai" and status["active"] is True
    assert status["caps"]["daily_cap_usd"] == 0.5 and status["caps"]["monthly_cap_usd"] == 4
    assert status["audit"] == {"proposals": True, "scan_alerts": True, "daily_review": False}
    assert status["channels"] == {"telegram": True, "in_app": False}
    openai_row = next(p for p in status["providers"] if p["provider"] == "openai")
    assert openai_row["model"] == "gpt-4o-mini" and openai_row["model_pinned"] is True
    assert status["vision_line"] == "Vision on - OpenAI, model gpt-4o-mini"
    # Back to the default model, and removing the chosen provider's key falls back to "auto".
    assert client.put("/api/ai/byok/prefs", headers=headers, json={"models": {"openai": ""}}).json()["providers"][1]["model_pinned"] is False
    after = client.delete("/api/ai/byok/key/openai", headers=headers).json()
    assert after["provider"] is None and after["vision_line"] == "Vision off - connect your AI key in Settings > AI"
    prefs = _run(_rows(UserVisionPrefsRecord, UserVisionPrefsRecord.user_id == me["id"]))
    assert len(prefs) == 1 and prefs[0].tenant_id == me["tenant_id"]


# --- audit ----------------------------------------------------------------------------------------------------------------

def test_audit_log_records_add_test_remove_without_any_key_material(flag_on, monkeypatch):
    Mocks().install(monkeypatch)
    headers, me = _user("byok-audit@example.com")
    for provider, key in KEYS.items():
        assert client.put("/api/ai/byok/key", headers=headers, json={"provider": provider, "api_key": key}).status_code == 200
        assert client.post(f"/api/ai/byok/test/{provider}", headers=headers).status_code == 200
        assert client.delete(f"/api/ai/byok/key/{provider}", headers=headers).status_code == 200
    rows = _run(_rows(AuditLogRecord, AuditLogRecord.tenant_id == me["tenant_id"], AuditLogRecord.event.like("byok_%")))
    events = [r.event for r in rows]
    assert events.count("byok_key_saved") == 3 and events.count("byok_key_tested") == 3 and events.count("byok_key_removed") == 3
    for row in rows:
        for provider, key in KEYS.items():
            assert key not in row.detail and key[4:-4] not in row.detail and byok.key_hint(key) not in row.detail and key[-4:] not in row.detail


def test_reencrypt_and_erasure_cover_byok_keys(flag_on):
    from app.retention.service import erase_user
    from app.secrets_store import envelope
    headers, me = _user("byok-erase@example.com")
    assert client.put("/api/ai/byok/key", headers=headers, json={"provider": "anthropic", "api_key": A_ANTHROPIC}).status_code == 200
    assert client.put("/api/ai/byok/prefs", headers=headers, json={"enabled": True}).status_code == 200

    async def go():
        async with _session_factory() as session:
            counts = await envelope.reencrypt_tenant(session, me["tenant_id"])
            assert counts["byok_ai_keys"] == 0                      # already in the configured format
            user = await session.get(User, me["id"])
            user.is_active = False
            await erase_user(session, user, actor_id=None)
            await session.commit()
            keys = await session.scalar(select(func.count()).select_from(UserAiKeyRecord).where(UserAiKeyRecord.user_id == me["id"]))
            prefs = await session.scalar(select(func.count()).select_from(UserVisionPrefsRecord).where(UserVisionPrefsRecord.user_id == me["id"]))
            return keys, prefs
    assert _run(go()) == (0, 0)


def test_gemini_provider_parses_usage_and_refuses_a_bad_model_id():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "thinking", "thought": True}, {"text": "OK"}]},
                                                         "finishReason": "STOP"}],
                                         "usageMetadata": {"promptTokenCount": 100, "cachedContentTokenCount": 40, "candidatesTokenCount": 3,
                                                           "thoughtsTokenCount": 7}})
    provider = prov.GeminiProvider(GEMINI_KEY, model="gemini-2.5-flash", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = _run(provider.complete_full("sys", "hi", max_tokens=16))
    assert out.text == "OK" and out.input_tokens == 60 and out.cache_read_tokens == 40 and out.output_tokens == 10
    assert seen[0]["systemInstruction"] == {"parts": [{"text": "sys"}]} and GEMINI_KEY not in json.dumps(seen[0])
    for bad in ("../../v1/evil?x=", "..", ".hidden", ""):
        with pytest.raises(prov.ProviderError):
            _run(prov.GeminiProvider(GEMINI_KEY, model=bad).complete_full("s", "u"))
