"""BYOK (Bring Your Own Key) tests. Provider HTTP is MOCKED - no paid API calls, no network.

Verifies: per-user keys, no shared/company key, tenant isolation, encryption at rest, redaction in logs/API responses,
provider selection policies, Dual Review authorization, safe failures, estimate-vs-billing labelling, bounded retries,
budget stops, agent loop bounds, key deletion, and that deterministic features work without any key."""
import logging
import os
import threading

import pytest
from sqlalchemy import select

from src.ai import crypto
from src.ai.agent import draft_strategy
from src.ai.gateway import NOTICE, AIGateway, AuthorizationRequired, BudgetExceeded, GatewayLLM, SessionKeys
from src.ai.providers import ProviderError, looks_like_key
from src.ai.redact import RedactingFilter, mask, redact
from src.services.analyst import analyse
from src.storage.db import Store, ai_credentials

OA1 = "sk-proj-FAKEFAKEpenaiKEY000000000001"
OA2 = "sk-proj-FAKEFAKEpenaiKEY000000000002"
AN1 = "sk-ant-api03-FAKEFAKEnthropicKEY0001"
EVIL_ENV = "sk-FAKEFAKEEYshouldNEVERbeUSED00000"


class MockProviders:
    """Mock transport for OpenAI + Anthropic. Records every request (url + auth header) for assertions."""

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.fail = {}            # provider -> list of HTTP statuses to return before succeeding
        self.valid = {OA1, OA2, AN1}
        self.reply = "Close is 123.45 and the trend is up."
        self.models = {"openai": ["gpt-test-mini", "gpt-test"], "anthropic": ["claude-test"]}

    def __call__(self, method, url, headers, body, timeout):
        prov = "anthropic" if "anthropic.com" in url else "openai"
        key = headers.get("x-api-key") or headers.get("Authorization", "").removeprefix("Bearer ")
        with self.lock:
            self.calls.append({"provider": prov, "url": url, "key": key, "body": body})
        if key not in self.valid:
            return 401, {"error": {"type": "authentication_error", "message": f"Incorrect API key provided: {key}"}}, ""
        q = self.fail.get(prov)
        if q:
            st = q.pop(0)
            if st == 429 and prov == "openai" and getattr(self, "quota", False):
                return 429, {"error": {"code": "insufficient_quota", "message": "You exceeded your current quota"}}, ""
            return st, {"error": {"message": f"status {st}"}}, ""
        if method == "GET":
            return 200, {"data": [{"id": m} for m in self.models[prov]]}, ""
        if prov == "openai":
            return 200, {"id": "chatcmpl-1", "model": body["model"], "choices": [{"message": {"content": self.reply}}],
                         "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "prompt_tokens_details": {"cached_tokens": 400}}}, ""
        return 200, {"id": "msg_1", "model": body["model"], "content": [{"type": "text", "text": self.reply}],
                     "usage": {"input_tokens": 900, "output_tokens": 150, "cache_read_input_tokens": 0}}, ""

    def chat_calls(self, prov=None):
        return [c for c in self.calls if c["body"] is not None and (prov is None or c["provider"] == prov)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AMW_SECRET_KEY", crypto.generate_master_key())
    monkeypatch.setenv("OPENAI_API_KEY", EVIL_ENV)          # a company/env key must NEVER be used
    monkeypatch.setenv("ANTHROPIC_API_KEY", EVIL_ENV)
    store = Store(f"sqlite:///{tmp_path}/ai.db")
    a, _ = store.create_user("alice")
    b, _ = store.create_user("bob")
    mock = MockProviders()
    gw = AIGateway(store, transport=mock, session_keys=SessionKeys(), sleeper=lambda s: None)
    return store, gw, mock, a, b


def task(gw, uid, **kw):
    return gw.new_task(uid, "chart_explanation", **kw)


# ---------------------------------------------------------------- keys, encryption, isolation
def test_key_tested_before_saving_and_encrypted(env):
    store, gw, mock, a, b = env
    with pytest.raises(ProviderError) as e:
        gw.save_key(a, "openai", "sk-proj-FAKEFAKEDKEY00000000000", None)
    assert e.value.code == "invalid_key" and gw.configured(a) == {}
    models = gw.save_key(a, "openai", OA1, "gpt-test-mini")
    assert "gpt-test" in models
    with store.engine.connect() as c:
        row = c.execute(select(ai_credentials)).first()
    assert OA1.encode() not in row.ciphertext and row.masked == mask(OA1) and OA1 not in row.masked
    assert store.ai_credentials(a)[0]["masked"].endswith(OA1[-4:])
    assert "ciphertext" not in store.ai_credentials(a)[0]


def test_format_checks_and_cross_provider_keys(env):
    store, gw, mock, a, b = env
    assert looks_like_key("openai", AN1) and looks_like_key("anthropic", OA1) and looks_like_key("openai", "short")
    with pytest.raises(ProviderError):
        gw.save_key(a, "openai", AN1, None)                  # Anthropic key never sent to OpenAI
    assert not mock.calls


def test_ciphertext_bound_to_user_and_provider(env):
    store, gw, mock, a, b = env
    gw.save_key(a, "openai", OA1, "gpt-test")
    with store.engine.begin() as c:
        row = c.execute(select(ai_credentials)).first()
        c.execute(ai_credentials.insert().values(user_id=b, provider="openai", ciphertext=row.ciphertext,
                                                 master_key_id=row.master_key_id, masked="x", model="gpt-test"))
    with pytest.raises(crypto.CryptoError):                  # row copied to another user cannot be decrypted
        store.ai_get_key(b, "openai")
    r = gw.complete(b, task(gw, b), "s", "u")
    assert not r.ok and r.error_code == "missing_key" and not mock.chat_calls()


def test_wrong_master_key_fails_closed(env, monkeypatch):
    store, gw, mock, a, b = env
    gw.save_key(a, "openai", OA1, "gpt-test")
    monkeypatch.setenv("AMW_SECRET_KEY", crypto.generate_master_key())
    r = gw.complete(a, task(gw, a), "s", "u")
    assert not r.ok and r.error_code == "missing_key" and "re-enter" in r.message


def test_each_request_uses_the_callers_own_key(env):
    store, gw, mock, a, b = env
    gw.save_key(a, "openai", OA1, "gpt-test")
    gw.save_key(b, "openai", OA2, "gpt-test")
    assert gw.complete(a, task(gw, a), "s", "u").ok and gw.complete(b, task(gw, b), "s", "u").ok
    keys = [c["key"] for c in mock.chat_calls()]
    assert keys == [OA1, OA2] and EVIL_ENV not in [c["key"] for c in mock.calls]


def test_no_key_means_no_call_and_no_company_fallback(env):
    store, gw, mock, a, b = env
    r = gw.complete(a, task(gw, a), "s", "u")
    assert not r.ok and r.error_code == "missing_key" and "Continue without AI (deterministic analysis)" in r.options
    assert mock.calls == []
    assert store.ai_usage(a)[0]["status"] == "blocked"


def test_delete_key_disables_future_use(env):
    store, gw, mock, a, b = env
    gw.save_key(a, "openai", OA1, "gpt-test")
    assert gw.delete_key(a, "openai")
    n = len(mock.calls)
    assert gw.complete(a, task(gw, a), "s", "u").error_code == "missing_key" and len(mock.calls) == n


def test_session_only_keys_not_persisted(env):
    store, gw, mock, a, b = env
    gw.save_key(a, "anthropic", AN1, "claude-test", session_only=True)
    assert store.ai_credentials(a) == [] and gw.configured(a)["anthropic"]["storage"] == "session-only"
    assert gw.complete(a, task(gw, a), "s", "u").ok
    fresh = AIGateway(store, transport=mock, session_keys=SessionKeys())          # restart
    assert fresh.configured(a) == {}


# ---------------------------------------------------------------- redaction
def test_redaction_everywhere(env, caplog):
    store, gw, mock, a, b = env
    assert redact(f"bad key {OA1} and {AN1} Authorization: Bearer {OA1}").count("[REDACTED]") >= 3
    assert OA1 not in redact(f"x-api-key: {OA1}")
    with pytest.raises(ProviderError) as e:
        gw.save_key(a, "openai", "sk-proj-FAKEFAKEngWRONG00000000099", None)   # provider echoes the key in its error
    assert "WRONGwrong" not in str(e.value)
    rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "leak %s", (OA1,), None)
    RedactingFilter().filter(rec)
    assert OA1 not in rec.getMessage()
    assert OA1 not in repr(__import__("src.ai.providers", fromlist=["x"]).OpenAIClient(OA1))


def test_api_never_returns_keys_and_isolates_users(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    from src.data.fixture_provider import FixtureProvider
    from src.services.app_services import Services
    monkeypatch.setenv("AMW_SECRET_KEY", crypto.generate_master_key())
    monkeypatch.setenv("AMW_AUTH_MODE", "token")
    mock = MockProviders()
    svc = Services(db_url=f"sqlite:///{tmp_path}/api.db", provider=FixtureProvider(end="2026-10-16"), ai_transport=mock)
    _, ta = svc.store.create_user("alice")
    _, tb = svc.store.create_user("bob")
    c = TestClient(create_app(svc))
    A, B = {"Authorization": f"Bearer {ta}"}, {"Authorization": f"Bearer {tb}"}
    r = c.put("/api/v1/ai/keys/openai", json={"api_key": OA1, "model": "gpt-test"}, headers=A)
    assert r.status_code == 200 and OA1 not in r.text and r.json()["masked"].endswith(OA1[-4:])
    bad = c.put("/api/v1/ai/keys/openai", json={"api_key": "sk-proj-FAKEFAKEngWRONG00000000099"}, headers=B)
    assert bad.status_code == 400 and "WRONGwrong" not in bad.text
    for path in ("/api/v1/ai/providers", "/api/v1/ai/usage"):
        assert OA1 not in c.get(path, headers=A).text
    assert c.get("/api/v1/ai/providers", headers=B).json()["configured"] == {}          # bob cannot see alice's key
    r = c.post("/api/v1/analyst", json={"ticker": "A.NS", "timeframes": ["1D"], "use_llm": True}, headers=B)
    assert r.status_code == 200 and r.json()["report"] and r.json()["llm"] is None      # bob has no key: deterministic only
    assert all(x["key"] == OA1 for x in mock.chat_calls())
    r = c.post("/api/v1/analyst", json={"ticker": "A.NS", "timeframes": ["1D"], "use_llm": True}, headers=A)
    assert r.status_code == 200 and r.json()["ai"]["provider"] == "openai"
    assert "billed by your provider" in r.json()["ai"]["billing"]
    usage = c.get("/api/v1/ai/usage", headers=A).json()
    assert "estimate" in usage["note"] and "authoritative" in usage["note"]
    assert c.get("/api/v1/ai/usage", headers=B).json()["rows"] == [] or all(
        x["status"] == "blocked" for x in c.get("/api/v1/ai/usage", headers=B).json()["rows"])
    assert c.delete("/api/v1/ai/keys/openai", headers=B).json()["deleted"] is False      # cannot delete alice's key
    assert c.delete("/api/v1/ai/keys/openai", headers=A).json()["deleted"] is True


def test_no_shared_key_code_paths():
    """Static guard: AI code must not read provider keys from the environment."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    for p in list((root / "src").rglob("*.py")) + list((root / "pages").glob("*.py")):
        t = p.read_text(encoding="utf-8")
        assert "OPENAI_API_KEY" not in t and "ANTHROPIC_API_KEY" not in t, p


# ---------------------------------------------------------------- provider selection
def test_selection_policies(env):
    store, gw, mock, a, b = env
    gw.save_key(a, "openai", OA1, "gpt-test")
    gw.save_key(a, "anthropic", AN1, "claude-test")
    with pytest.raises(ProviderError):                           # both configured, no default
        gw.resolve_provider(a, "chart_explanation")
    gw.save_policy(a, {"default_provider": "anthropic"})
    assert gw.resolve_provider(a, "chart_explanation") == "anthropic"
    assert gw.resolve_provider(a, "chart_explanation", "openai") == "openai"
    gw.save_policy(a, {"selection": "manual"})
    with pytest.raises(ProviderError):
        gw.resolve_provider(a, "chart_explanation")
    gw.save_policy(a, {"selection": "task_routing", "default_provider": "openai", "task_routes": {"strategy_draft": "anthropic"}})
    assert gw.resolve_provider(a, "strategy_draft") == "anthropic" and gw.resolve_provider(a, "chart_explanation") == "openai"
    with pytest.raises(ValueError):
        gw.save_policy(a, {"fallback": "company"})


def test_single_provider_user_cannot_route_to_unconfigured(env):
    store, gw, mock, a, b = env
    gw.save_key(a, "openai", OA1, "gpt-test")
    assert gw.resolve_provider(a, "chart_explanation") == "openai"
    with pytest.raises(ProviderError):
        gw.resolve_provider(a, "chart_explanation", "anthropic")


def test_fallback_follows_policy_only(env):
    store, gw, mock, a, b = env
    gw.save_key(a, "openai", OA1, "gpt-test")
    gw.save_key(a, "anthropic", AN1, "claude-test")
    gw.save_policy(a, {"default_provider": "openai", "fallback": "none", "limits": {"max_retries": 0}})
    mock.fail["openai"] = [503]
    r = gw.complete(a, task(gw, a), "s", "u")
    assert not r.ok and not mock.chat_calls("anthropic") and "Use your Anthropic Claude key instead" in r.options
    gw.save_policy(a, {"fallback": "auto"})
    mock.fail["openai"] = [503]
    r = gw.complete(a, task(gw, a), "s", "u")
    assert r.ok and r.provider == "anthropic" and mock.chat_calls("anthropic")[-1]["key"] == AN1
    assert any(u["fallback_from"] == "openai" for u in store.ai_usage(a))


def test_dual_review_requires_both_keys_and_authorization(env):
    store, gw, mock, a, b = env
    gw.save_key(a, "openai", OA1, "gpt-test")
    with pytest.raises(AuthorizationRequired):
        gw.dual_review(a, "s", "u", authorized=True)           # disabled by default
    gw.save_policy(a, {"dual_review_enabled": True})
    with pytest.raises(ProviderError):
        gw.dual_review(a, "s", "u", authorized=True)           # missing anthropic key
    gw.save_key(a, "anthropic", AN1, "claude-test")
    n = len(mock.chat_calls())
    with pytest.raises(AuthorizationRequired) as e:
        gw.dual_review(a, "s", "u", authorized=False)
    assert "BOTH" in str(e.value) and len(mock.chat_calls()) == n
    out = gw.dual_review(a, "s", "u", authorized=True)
    assert all(r.ok for r in out["results"].values()) and out["calls"] == 2
    assert [c["key"] for c in mock.chat_calls()[-2:]] == [OA1, AN1]


# ---------------------------------------------------------------- errors, retries, budgets
@pytest.mark.parametrize("status,code", [(401, "invalid_key"), (403, "permission"), (404, "model_not_found"),
                                         (429, "rate_limited"), (503, "provider_unavailable"), (400, "bad_request")])
def test_error_classification(env, status, code):
    store, gw, mock, a, b = env
    gw.save_key(a, "openai", OA1, "gpt-test")
    gw.save_policy(a, {"limits": {"max_retries": 0}})
    mock.fail["openai"] = [status]
    if status == 401:
        mock.valid.discard(OA1)
    r = gw.complete(a, task(gw, a), "s", "u")
    assert not r.ok and r.error_code == code and OA1 not in (r.message or "")


def test_insufficient_credit_and_network(env):
    import requests
    store, gw, mock, a, b = env
    gw.save_key(a, "openai", OA1, "gpt-test")
    mock.quota = True
    mock.fail["openai"] = [429]
    assert gw.complete(a, task(gw, a), "s", "u").error_code == "insufficient_credit"

    def down(*a_, **k):
        raise requests.ConnectionError("boom")
    gw.transport = down
    r = gw.complete(a, task(gw, a), "s", "u")
    assert r.error_code == "network" and "Retry (within your retry limit)" in r.options


def test_retries_are_bounded(env):
    store, gw, mock, a, b = env
    gw.save_key(a, "openai", OA1, "gpt-test")
    gw.save_policy(a, {"limits": {"max_retries": 2, "max_calls_per_task": 10}})
    mock.fail["openai"] = [503] * 50
    r = gw.complete(a, task(gw, a), "s", "u")
    assert not r.ok and len(mock.chat_calls()) == 3            # 1 + 2 retries, never more


def test_calls_per_task_and_authorization(env):
    store, gw, mock, a, b = env
    gw.save_key(a, "openai", OA1, "gpt-test")
    gw.save_policy(a, {"limits": {"max_calls_per_task": 2, "confirm_calls_above": 1}})
    with pytest.raises(BudgetExceeded):
        gw.new_task(a, "strategy_draft", expected_calls=3)
    with pytest.raises(AuthorizationRequired):
        gw.new_task(a, "strategy_draft", expected_calls=2)
    b_ = gw.new_task(a, "strategy_draft", expected_calls=2, authorized=True)
    assert gw.complete(a, b_, "s", "u").ok and gw.complete(a, b_, "s", "u").ok
    r = gw.complete(a, b_, "s", "u")
    assert r.error_code == "budget" and len(mock.chat_calls()) == 2


def test_cost_thresholds_stop_execution_and_estimates_are_labelled(env):
    store, gw, mock, a, b = env
    gw.save_key(a, "openai", OA1, "gpt-test")
    prices = {"openai:gpt-test": {"input_per_m": 10.0, "output_per_m": 30.0, "cached_input_per_m": 5.0}}
    gw.save_policy(a, {"prices": prices, "limits": {"max_cost_per_task_usd": 0.02, "max_calls_per_task": 10, "max_output_tokens": 200}})
    b_ = task(gw, a)
    r1 = gw.complete(a, b_, "s", "u")
    # 600 uncached*10 + 400 cached*5 + 200*30 = 6000+2000+6000 = 14000 / 1e6 = 0.014
    assert r1.ok and r1.est_cost_usd == pytest.approx(0.014)
    r2 = gw.complete(a, b_, "s", "u")
    assert not r2.ok and r2.error_code == "budget" and len(mock.chat_calls()) == 1
    row = [u for u in store.ai_usage(a) if u["status"] == "ok"][0]
    assert row["usage_source"] == "provider" and row["input_tokens"] == 1000 and row["cached_input_tokens"] == 400
    assert "user-entered prices" in row["price_basis"]
    gw.save_policy(a, {"prices": {}, "limits": {"max_cost_per_task_usd": 0.02}})
    assert gw.complete(a, task(gw, a), "s", "u").error_code == "budget"   # limit set but no price -> fail safe
    assert "billed separately" in NOTICE and "not included in your AMW subscription" in NOTICE


def test_daily_request_threshold(env):
    store, gw, mock, a, b = env
    gw.save_key(a, "openai", OA1, "gpt-test")
    gw.save_policy(a, {"limits": {"daily_requests": 2}})
    assert gw.complete(a, task(gw, a), "s", "u").ok and gw.complete(a, task(gw, a), "s", "u").ok
    with pytest.raises(BudgetExceeded):
        task(gw, a)
    assert len(mock.chat_calls()) == 2


def test_cancellation(env):
    store, gw, mock, a, b = env
    gw.save_key(a, "openai", OA1, "gpt-test")
    ev = threading.Event()
    ev.set()
    r = gw.complete(a, task(gw, a), "s", "u", cancel=ev)
    assert r.error_code == "cancelled" and not mock.chat_calls()


# ---------------------------------------------------------------- agent
def test_agent_repair_loop_bounded_and_draft_only(env):
    store, gw, mock, a, b = env
    gw.save_key(a, "openai", OA1, "gpt-test")
    gw.save_policy(a, {"limits": {"max_repair_attempts": 2, "max_calls_per_task": 5, "confirm_calls_above": 1}})
    mock.reply = "```\nthis is not pine\n```"
    with pytest.raises(AuthorizationRequired):
        draft_strategy(gw, a, "close above ema 50", "pine")
    r = draft_strategy(gw, a, "close above ema 50", "pine", authorized=True)
    assert not r.ok and r.attempts == 3 and len(mock.chat_calls()) == 3
    mock.reply = "```\n//@version=5\nindicator('x')\nlong_signal = close > ta.ema(close, 50)\n```"
    r = draft_strategy(gw, a, "close above ema 50", "pine", authorized=True)
    assert r.ok and r.attempts == 1 and "draft" in r.message
    assert store.lab_list(a) == []                               # nothing saved/published automatically
    with pytest.raises(BudgetExceeded):
        draft_strategy(gw, a, "x" * 20, "pine", authorized=True, depth=5)


# ---------------------------------------------------------------- analyst integration + deterministic fallback
def test_analyst_works_without_key_and_with_key(env):
    store, gw, mock, a, b = env
    ev = {"symbol": "A", "generated_utc": "t", "regime": {}, "signals": [],
          "timeframes": {"1D": {"close": 123.45, "data_status": "OK", "last_bar": "d", "trend": "Bullish", "trend_score": 0.8,
                                "structure_trend": "up", "rsi14": 61.0, "adx": 25.0, "rvol": 1.2, "volatility": "normal",
                                "structure_events": [], "patterns": []}}}
    r = analyse(ev, None, None)
    assert r.deterministic and r.llm_status == "not requested"
    r = analyse(ev, None, GatewayLLM(gw, a, task(gw, a)), retries=0)            # no key -> deterministic + clear status
    assert r.deterministic and r.llm_text is None and "unavailable" in r.llm_status
    gw.save_key(a, "openai", OA1, "gpt-test")
    r = analyse(ev, None, GatewayLLM(gw, a, task(gw, a)), retries=0)
    assert r.llm_text == mock.reply and "verified" in r.llm_status
    mock.reply = "Target 999.99"
    r = analyse(ev, None, GatewayLLM(gw, a, task(gw, a)), retries=0)
    assert r.llm_text is None and "rejected" in r.llm_status


def test_master_key_file_permissions(tmp_path, monkeypatch):
    monkeypatch.delenv("AMW_SECRET_KEY", raising=False)
    monkeypatch.setattr(crypto, "_key_file", lambda: tmp_path / "k" / ".amw_master.key")
    k1 = crypto.load_master_key()
    assert crypto.load_master_key() == k1 and len(k1) == 32
    if os.name == "posix":
        assert oct((tmp_path / "k" / ".amw_master.key").stat().st_mode)[-3:] == "600"
