"""M72 PR-1b (CP-1, findings F1 / F2, owner decision M72Q-1): the platform AI key and its caps.

- `ai_platform_key` OFF: every provider is exactly today's.
- ON: the user's own key first, then the organisation's own key, then the platform key - only inside the plan's monthly
  platform budget and the per-user daily cap, both Not set until the owner sets them (ATP chooses no number; the Free plan
  stays rules-only until it has a budget). A cap reached is a hard stop, then the rules.
- The key lives on the host only and is never logged, stored or returned.
Every key here is an obviously fake string and every provider a mocked transport: no test reaches a real provider.
"""
import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.ai import platform_key, resolve
from app.ai import providers as prov
from app.ai.metering import METRIC_COST_USD, MeteredProvider, budget_exhausted
from app.ai.providers import RuleBasedProvider
from app.db.models import LlmCallRecord, Tenant, UsageRecord, User
from app.plans import registry
from tests.test_admin_api import _admin
from tests.test_auth_api import _session_factory, client
from tests.test_settings_a_connect_ai import Mocks, _flags, _run, _tenant_key, _user

PLATFORM_KEY = "sk-ant-api03-FAKEFAKE-test-only-platform-pppppppp-5151"


@pytest.fixture
def platform(monkeypatch):
    """The flag on, a platform key on the 'host', every provider built on a mocked transport (calls recorded)."""
    _flags(ui_v2=True, ai_platform_key=True)
    monkeypatch.setenv(platform_key.ENV_PROVIDER, "anthropic")
    monkeypatch.setenv(platform_key.ENV_KEY, PLATFORM_KEY)
    for plan in registry.PLANS:
        monkeypatch.delenv(f"AI_PLATFORM_BUDGET_INR_{plan.upper()}", raising=False)
    monkeypatch.delenv(platform_key.ENV_USER_DAILY_CAP, raising=False)
    mocks = Mocks()
    original = prov.build_provider

    def build(name, api_key, model, client=None, **kw):
        return original(name, api_key, model, client=mocks.anthropic(), **kw)
    monkeypatch.setattr(prov, "build_provider", build)
    _run(_clear_limits())
    yield mocks
    _run(_clear_limits())
    _flags(ui_v2=False, ai_platform_key=False, ai_one_key=False)


async def _clear_limits():
    from app.platform import controls
    async with _session_factory() as session:
        await controls._set(session, platform_key.CONTROL_KEY, {}, None)
        await session.commit()


async def _set_limits(budgets=None, cap=None):
    async with _session_factory() as session:
        admin = await session.scalar(select(User).limit(1))
        await platform_key.set_limits(session, admin, plan_budget_inr=budgets or {}, user_daily_cap_inr=cap)


async def _consent(me):
    from app.ai import compliance_terms as terms
    async with _session_factory() as session:
        await terms.accept(session, await session.get(User, me["id"]), terms.KIND_DATA, terms.DATA_CONSENT_VERSION)
        await session.commit()


async def _resolve(me, task="copilot", need_vision=False):
    async with _session_factory() as session:
        user = await session.get(User, me["id"])
        tenant = await session.get(Tenant, me["tenant_id"])
        return await resolve.provider_for_user(session, user, tenant, task, need_vision=need_vision)


async def _ask(me, text="Say OK"):
    async with _session_factory() as session:
        user = await session.get(User, me["id"])
        tenant = await session.get(Tenant, me["tenant_id"])
        provider = await resolve.provider_for_user(session, user, tenant, "copilot")
        out = await provider.complete("You are terse.", text, max_tokens=50)
        await session.commit()
        return provider, out


async def _rows(model, *where):
    async with _session_factory() as session:
        return list(await session.scalars(select(model).where(*where)))


async def _spend(me, inr, *, days_ago=0, user_id=None):
    """A platform cost row as if calls had already spent `inr` (today, or `days_ago`)."""
    from app.ai import pricing
    start = (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(hour=0, minute=0, second=0, microsecond=0)
    async with _session_factory() as session:
        session.add(UsageRecord(tenant_id=me["tenant_id"], metric=platform_key.METRIC_COST_USD, quantity=inr / pricing.usd_inr_rate(),
                                period_start=start, period_end=start + timedelta(days=1), source=f"platform:u{user_id or me['id']}"))
        await session.commit()


# --- flag off / Not set ---------------------------------------------------------------------------------------------------

def test_flag_off_is_exactly_todays_provider(platform):
    _, me = _user("m72b-off@example.com")
    _run(_consent(me))
    _run(_set_limits({"pro": 1000.0}, 50.0))
    _flags(ai_platform_key=False)
    provider = _run(_resolve(me))
    assert type(provider) is RuleBasedProvider and not platform.calls


def test_no_number_in_code_every_limit_starts_not_set(platform):
    assert all(registry.platform_ai_budget_inr(p) is None for p in registry.PLANS)
    assert _run(_limits()) == {"plan_budget_inr": {p: None for p in registry.PLANS}, "user_daily_cap_inr": None}
    _, me = _user("m72b-notset@example.com")
    _run(_consent(me))
    provider = _run(_resolve(me))
    assert isinstance(provider, platform_key.PlatformRules) and provider.platform_reason == platform_key.REASON_BUDGET_NOT_SET
    _run(_set_limits({"pro": 1000.0}))                                       # a budget but no daily cap: still the rules
    assert _run(_resolve(me)).platform_reason == platform_key.REASON_CAP_NOT_SET
    assert not platform.calls


async def _limits():
    async with _session_factory() as session:
        return await platform_key.limits(session)


def test_free_plan_stays_rules_only_until_it_has_its_own_budget(platform):
    _, me = _user("m72b-free@example.com", plan="free")
    _run(_consent(me))
    _run(_set_limits({"pro": 1000.0, "business": 5000.0}, 50.0))
    assert _run(_resolve(me)).platform_reason == platform_key.REASON_BUDGET_NOT_SET
    _run(_set_limits({"free": 100.0}))
    assert isinstance(_run(_resolve(me)), platform_key.PlatformKeyProvider)


def test_host_values_apply_when_admin_has_none_and_admin_wins(platform, monkeypatch):
    monkeypatch.setenv("AI_PLATFORM_BUDGET_INR_PRO", "800")
    monkeypatch.setenv(platform_key.ENV_USER_DAILY_CAP, "20")
    for bad in ("", "abc", "-5", "nan", "inf"):
        monkeypatch.setenv("AI_PLATFORM_BUDGET_INR_FREE", bad)
        assert registry.platform_ai_budget_inr("free") is None, bad
    values = _run(_limits())
    assert values["plan_budget_inr"]["pro"] == 800.0 and values["user_daily_cap_inr"] == 20.0
    _run(_set_limits({"pro": 300.0}, 5.0))
    values = _run(_limits())
    assert values["plan_budget_inr"]["pro"] == 300.0 and values["user_daily_cap_inr"] == 5.0


# --- the order of sources -------------------------------------------------------------------------------------------------

def test_platform_answers_metered_on_its_own_metrics_and_logged(platform):
    _, me = _user("m72b-on@example.com")
    _run(_consent(me))
    _run(_set_limits({"pro": 1000.0}, 50.0))
    provider, out = _run(_ask(me))
    assert out == "OK" and isinstance(provider, platform_key.PlatformKeyProvider) and isinstance(provider, MeteredProvider)
    assert platform.calls and platform.calls[0]["key"] == PLATFORM_KEY
    rows = _run(_rows(UsageRecord, UsageRecord.tenant_id == me["tenant_id"]))
    assert {r.metric for r in rows} == set(platform_key.PLATFORM_METRICS)
    assert {r.source for r in rows} == {f"platform:u{me['id']}"}
    assert not [r for r in rows if r.metric == METRIC_COST_USD]               # the organisation's own-key budget never counts it
    calls = _run(_rows(LlmCallRecord, LlmCallRecord.tenant_id == me["tenant_id"]))
    assert len(calls) == 1 and calls[0].user_id == me["id"] and calls[0].status == "ok" and not calls[0].billed_to_user_key
    assert resolve.is_platform_key(provider) and not resolve.is_user_key(provider)
    from app.ai.metering import spent_by
    assert spent_by(provider)["calls"] == 1                                    # the per-answer cost chip


def test_the_organisations_own_key_comes_before_the_platform_key(platform, monkeypatch):
    _, me = _user("m72b-org@example.com")
    _run(_set_limits({"pro": 1000.0}, 50.0))
    _run(_tenant_key(me))
    provider = _run(_resolve(me))
    assert isinstance(provider, MeteredProvider) and not isinstance(provider, platform_key.PlatformKeyProvider)


def test_an_organisation_key_with_a_spent_budget_never_falls_over_to_the_platform_key(platform, monkeypatch):
    _, me = _user("m72b-orgspent@example.com")
    _run(_set_limits({"pro": 1000.0}, 50.0))
    _run(_tenant_key(me))

    async def spent(session, tenant):
        return True
    monkeypatch.setattr("app.ai.metering.budget_exhausted", spent)
    provider = _run(_resolve(me))
    assert type(provider) is RuleBasedProvider and "budget" in provider.reason


def test_the_users_own_key_comes_first(platform, monkeypatch):
    _, me = _user("m72b-own@example.com")
    _run(_consent(me))
    _run(_set_limits({"pro": 1000.0}, 50.0))
    _flags(ai_one_key=True)
    own = object()

    async def own_key(session, user, task, **kw):
        return own
    monkeypatch.setattr(resolve, "user_key_provider", own_key)
    assert _run(_resolve(me)) is own


def test_without_the_data_consent_nothing_leaves(platform):
    _, me = _user("m72b-consent@example.com")
    _run(_set_limits({"pro": 1000.0}, 50.0))
    assert _run(_resolve(me)).platform_reason == platform_key.REASON_CONSENT
    assert not platform.calls


def test_no_key_on_the_host_or_an_unknown_provider_keeps_todays_answer(platform, monkeypatch):
    _, me = _user("m72b-nokey@example.com")
    _run(_consent(me))
    _run(_set_limits({"pro": 1000.0}, 50.0))
    monkeypatch.delenv(platform_key.ENV_KEY)
    assert type(_run(_resolve(me))) is RuleBasedProvider
    monkeypatch.setenv(platform_key.ENV_KEY, PLATFORM_KEY)
    monkeypatch.setenv(platform_key.ENV_PROVIDER, "somebody")
    assert type(_run(_resolve(me))) is RuleBasedProvider


def test_the_key_can_come_from_a_file_on_the_host(platform, monkeypatch, tmp_path):
    path = tmp_path / "platform.key"
    path.write_text(PLATFORM_KEY + "\n")
    monkeypatch.delenv(platform_key.ENV_KEY)
    monkeypatch.setenv(platform_key.ENV_KEY_FILE, str(path))
    assert platform_key.configured_provider() == "anthropic" and platform_key._secret() == PLATFORM_KEY
    monkeypatch.setenv(platform_key.ENV_KEY_FILE, str(tmp_path / "missing"))
    assert platform_key.configured_provider() is None


def test_a_support_view_never_uses_the_platform_key(platform, monkeypatch):
    _, me = _user("m72b-viewas@example.com")
    _run(_consent(me))
    _run(_set_limits({"pro": 1000.0}, 50.0))
    from app.auth import view_as
    monkeypatch.setattr(view_as, "active", lambda: True)
    assert not isinstance(_run(_resolve(me)), platform_key.PlatformKeyProvider)


# --- caps -----------------------------------------------------------------------------------------------------------------

def test_daily_cap_reached_is_a_hard_stop_then_rules(platform):
    _, me = _user("m72b-cap@example.com")
    _run(_consent(me))
    _run(_set_limits({"pro": 1000.0}, 10.0))
    _run(_spend(me, 9.0))
    assert isinstance(_run(_resolve(me)), platform_key.PlatformKeyProvider)
    _run(_spend(me, 1.0))
    provider = _run(_resolve(me))
    assert provider.platform_reason == platform_key.REASON_DAILY_CAP and "limit for today" in provider.reason
    assert not platform.calls


def test_yesterdays_spend_and_another_users_spend_do_not_count_today(platform):
    _, me = _user("m72b-cap2@example.com")
    _run(_consent(me))
    _run(_set_limits({"pro": 100000.0}, 10.0))
    _run(_spend(me, 50.0, days_ago=1))
    _run(_spend(me, 50.0, user_id=me["id"] + 100000))
    assert isinstance(_run(_resolve(me)), platform_key.PlatformKeyProvider)


def test_cap_is_checked_before_every_call_of_one_request(platform):
    _, me = _user("m72b-midcap@example.com")
    _run(_consent(me))
    _run(_set_limits({"pro": 1000.0}, 10.0))

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            tenant = await session.get(Tenant, me["tenant_id"])
            provider = await resolve.provider_for_user(session, user, tenant, "copilot")
            assert isinstance(provider, platform_key.PlatformKeyProvider)
            await _spend(me, 10.0)                                       # spent elsewhere between two calls
            with pytest.raises(platform_key.PlatformKeyLimit) as raised:
                await provider.complete("s", "u")
            assert raised.value.reason == platform_key.REASON_DAILY_CAP
            with pytest.raises(platform_key.PlatformKeyLimit):
                await provider.complete_tools("s", [{"role": "user", "content": "u"}], [])
    _run(go())
    assert not platform.calls


def test_the_plans_monthly_budget_spent_stops_every_member(platform):
    _, me = _user("m72b-month@example.com")
    _run(_consent(me))
    _run(_set_limits({"pro": 20.0}, 1000.0))
    _run(_spend(me, 20.0, user_id=me["id"] + 200000))             # another member of the organisation spent it
    assert _run(_resolve(me)).platform_reason == platform_key.REASON_BUDGET_SPENT


def test_the_users_own_cap_can_only_lower_the_operators(platform):
    headers, me = _user("m72b-owncap@example.com")
    _run(_consent(me))
    _run(_set_limits({"pro": 1000.0}, 10.0))
    _run(_spend(me, 3.0))
    assert client.put("/api/me/prefs/ai_usage", headers=headers, json={"daily_cap_inr": 2.0}).status_code == 200
    assert _run(_resolve(me)).platform_reason == platform_key.REASON_DAILY_CAP
    assert client.put("/api/me/prefs/ai_usage", headers=headers, json={"daily_cap_inr": 500.0}).status_code == 200
    assert isinstance(_run(_resolve(me)), platform_key.PlatformKeyProvider)           # the operator's 10 still applies
    _run(_spend(me, 7.0))
    assert _run(_resolve(me)).platform_reason == platform_key.REASON_DAILY_CAP
    for bad in ({"daily_cap_inr": 0}, {"daily_cap_inr": -1}, {"daily_cap_inr": "x"}, {"other": 1}):
        assert client.put("/api/me/prefs/ai_usage", headers=headers, json=bad).status_code == 422, bad
    assert platform_key.effective_cap(None, 5.0) is None and platform_key.effective_cap(10.0, None) == 10.0


# --- the key is never logged ----------------------------------------------------------------------------------------------

def test_the_key_never_reaches_a_log_row_or_an_error(platform, monkeypatch, caplog):
    import httpx2

    from tests.test_phase_l_ai import _anthropic_mock

    def handler(request):
        key = request.headers.get("x-api-key")
        return httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": f"bad key {key}"}})
    monkeypatch.setattr(prov, "build_provider", lambda name, api_key, model, client=None, **kw: prov.AnthropicProvider(
        api_key=api_key, model="claude-test", http_client=_anthropic_mock(handler)))
    _, me = _user("m72b-leak@example.com")
    _run(_consent(me))
    _run(_set_limits({"pro": 1000.0}, 50.0))
    caplog.set_level(logging.DEBUG)

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            tenant = await session.get(Tenant, me["tenant_id"])
            provider = await resolve.provider_for_user(session, user, tenant, "copilot")
            with pytest.raises(prov.ProviderError) as raised:
                await provider.complete("s", "u")
            await session.commit()
            return str(raised.value)
    message = _run(go())
    assert PLATFORM_KEY not in message
    rows = _run(_rows(LlmCallRecord, LlmCallRecord.tenant_id == me["tenant_id"]))
    assert rows and all(PLATFORM_KEY not in (r.status + (r.response_text or "") + r.system_text + r.user_text) for r in rows)
    assert PLATFORM_KEY not in caplog.text


# --- routes ---------------------------------------------------------------------------------------------------------------

def test_admin_sets_the_limits_and_never_sees_the_key(platform):
    headers, _ = _admin("m72b-admin@example.com")
    got = client.get("/api/admin/controls/ai-platform", headers=headers)
    assert got.status_code == 200 and got.json()["key_configured"] is True and got.json()["provider"] == "anthropic"
    assert PLATFORM_KEY not in got.text and got.json()["user_daily_cap_inr"] is None
    saved = client.put("/api/admin/controls/ai-platform", headers=headers, json={"plan_budget_inr": {"pro": 1500, "free": 0}, "user_daily_cap_inr": 25})
    assert saved.status_code == 200 and saved.json()["plan_budget_inr"]["pro"] == 1500 and saved.json()["plan_budget_inr"]["free"] == 0
    assert saved.json()["plan_budget_inr"]["business"] is None and saved.json()["user_daily_cap_inr"] == 25
    cleared = client.put("/api/admin/controls/ai-platform", headers=headers, json={"plan_budget_inr": {"pro": None}, "clear_cap": True})
    assert cleared.json()["plan_budget_inr"]["pro"] is None and cleared.json()["user_daily_cap_inr"] is None
    assert cleared.json()["plan_budget_inr"]["free"] == 0
    for bad in ({"plan_budget_inr": {"gold": 5}}, {"plan_budget_inr": {"pro": -1}}, {"user_daily_cap_inr": -2}):
        assert client.put("/api/admin/controls/ai-platform", headers=headers, json=bad).status_code == 400, bad


def test_a_trader_cannot_read_or_set_the_limits(platform):
    headers, _ = _user("m72b-trader@example.com")
    assert client.get("/api/admin/controls/ai-platform", headers=headers).status_code == 403
    assert client.put("/api/admin/controls/ai-platform", headers=headers, json={"user_daily_cap_inr": 5}).status_code == 403


def test_my_usage_route_shows_only_my_own_numbers(platform):
    headers, me = _user("m72b-usage@example.com")
    _run(_consent(me))
    _run(_set_limits({"pro": 1000.0}, 10.0))
    _run(_spend(me, 4.0))
    _run(_spend(me, 99.0, user_id=me["id"] + 300000))
    body = client.get("/api/ai/platform/usage", headers=headers).json()
    assert body["in_use"] and body["available"] and body["today_inr"] == 4.0 and body["cap_inr"] == 10.0
    assert body["own_cap_inr"] is None and body["reason"] is None and PLATFORM_KEY not in str(body)
    _run(_spend(me, 6.0))
    body = client.get("/api/ai/platform/usage", headers=headers).json()
    assert body["available"] is False and body["reason"] == platform_key.REASON_DAILY_CAP and body["message"]
    _flags(ai_platform_key=False)
    assert client.get("/api/ai/platform/usage", headers=headers).status_code == 404


def test_the_chat_says_ai_limit_reached_on_a_platform_cap():
    from app.ai.chat_routes import _ai_state
    state = _ai_state(platform_key.rules(platform_key.REASON_DAILY_CAP), one_key=True)
    assert state["title"] == "AI limit reached" and state["code"] == resolve.LIMIT_CODE and state["platform"] is True
    not_set = _ai_state(platform_key.rules(platform_key.REASON_BUDGET_NOT_SET))
    assert not_set["ready"] is False and not_set.get("platform") is None


def test_flag_is_off_by_default_owner_only_and_not_in_the_trader_pack():
    from app.platform import controls
    from app.platform.flag_catalog import FLAG_INFO
    from app.platform.trader_pack import PACK
    assert "ai_platform_key" in controls.DEFAULT_OFF_FLAGS and "ai_platform_key" not in PACK
    assert FLAG_INFO["ai_platform_key"].depends_on == ("ui_v2",)
    assert "ai_one_key" in controls.FEATURE_FLAGS                    # ai_one_key keeps its meaning (OKT-9)


def test_platform_key_module_imports_no_order_path():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(platform_key))
    names = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not {m for m in names if m.startswith(("app.execution", "app.brokers", "app.trading", "app.workers"))}


def test_budget_exhausted_for_the_organisation_ignores_platform_spend(platform):
    _, me = _user("m72b-orgbudget@example.com")
    _run(_spend(me, 1e7))

    async def go():
        async with _session_factory() as session:
            return await budget_exhausted(session, await session.get(Tenant, me["tenant_id"]))
    assert _run(go()) is False

