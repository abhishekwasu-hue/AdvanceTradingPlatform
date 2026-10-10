"""H-C1 b: the AI endpoints are rate limited per user and per organisation, with plan-wise limits and heavy jobs
costing more units. Limits come from config; the window resets."""
import asyncio

import pytest

from app.ai import rate_limit as ai_rl
from app.core import config
from app.core import rate_limit as core_rl
from app.db.models import Tenant
from tests.test_auth_api import _register, _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner as _owner_acknowledged


def _owner(email, plan="pro"):
    """The helper accepts the AI acknowledgement (one AI call): start each test's windows empty."""
    out = _owner_acknowledged(email, plan=plan)
    core_rl.reset("ai_u")
    core_rl.reset("ai_t")
    return out


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _limit_enforced():
    """The suite disables the AI limit (tests/test_auth_api.py); these tests turn it back on."""
    from app.main import app
    saved = app.dependency_overrides.pop(ai_rl.ai_rate_limit, None)
    core_rl.reset("ai_u")
    core_rl.reset("ai_t")
    yield
    if saved is not None:
        app.dependency_overrides[ai_rl.ai_rate_limit] = saved


def _tight(monkeypatch, *, user=3, tenant=100, weights=None):
    monkeypatch.setattr(config, "AI_RATE_LIMITS", {"free": {"user": user, "tenant": tenant}, "pro": {"user": user, "tenant": tenant}})
    monkeypatch.setattr(config, "AI_RATE_WEIGHTS", weights or {})


def test_user_limit_is_enforced_and_resets(monkeypatch):
    _tight(monkeypatch, user=3)
    headers, _ = _owner("hc1b-user@example.com")
    codes = [client.get("/api/ai/drafts", headers=headers).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    refused = client.get("/api/ai/drafts", headers=headers)
    assert refused.status_code == 429 and refused.headers.get("retry-after") == str(config.AI_RATE_WINDOW_SECONDS)
    assert "Too many AI requests" in refused.json()["detail"]
    # Another user has their own window; non-AI routes are not counted.
    other, _ = _owner("hc1b-user2@example.com")
    assert client.get("/api/ai/drafts", headers=other).status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    # The window resets (in-process windows forgotten = the minute has passed).
    core_rl.reset("ai_u")
    core_rl.reset("ai_t")
    assert client.get("/api/ai/drafts", headers=headers).status_code == 200


def test_organisation_limit_is_shared_by_its_members(monkeypatch):
    _tight(monkeypatch, user=100, tenant=2)
    headers, me = _owner("hc1b-org@example.com")
    assert [client.get("/api/ai/drafts", headers=headers).status_code for _ in range(3)] == [200, 200, 429]
    # The organisation is checked first: the refused call did not spend the user's own units.
    assert core_rl._WINDOWS[("ai_u", f"u{me['id']}")].__len__() == 2


def test_heavy_jobs_cost_more_units(monkeypatch):
    _tight(monkeypatch, user=10, weights={"POST strategist/parse": 6})
    headers, _ = _owner("hc1b-heavy@example.com")
    body = {"request": "intraday nifty", "language": "en"}
    first = client.post("/api/ai/strategist/parse", headers=headers, json=body)
    assert first.status_code != 429
    assert client.post("/api/ai/strategist/parse", headers=headers, json=body).status_code == 429     # 6 + 6 > 10
    assert client.get("/api/ai/drafts", headers=headers).status_code == 200                        # 6 + 1 fits


def test_plan_wise_limits_apply(monkeypatch):
    monkeypatch.setattr(config, "AI_RATE_LIMITS", {"free": {"user": 1, "tenant": 100}, "pro": {"user": 5, "tenant": 100}})
    monkeypatch.setattr(config, "AI_RATE_WEIGHTS", {})
    free_headers = {"Authorization": f"Bearer {_register('hc1b-free@example.com')}"}
    pro_headers, _ = _owner("hc1b-pro@example.com", plan="pro")
    assert [client.get("/api/ai/drafts", headers=free_headers).status_code for _ in range(2)] == [200, 429]
    assert all(client.get("/api/ai/drafts", headers=pro_headers).status_code == 200 for _ in range(5))
    # An unknown plan gets the default (free) plan's limits; a missing table row means no limit configured.
    assert ai_rl.limits_for("platinum") == {"user": 1, "tenant": 100}
    monkeypatch.setattr(config, "AI_RATE_LIMITS", {})
    assert ai_rl.limits_for("pro") == {"user": 0, "tenant": 0}


def test_defaults_cover_every_plan_and_weigh_the_heavy_jobs():
    from app.plans.registry import PLANS
    for plan_id in PLANS:
        limits = ai_rl.limits_for(plan_id)
        assert limits["user"] > 0 and limits["tenant"] >= limits["user"]
    for heavy in ("POST strategist/build", "POST interview/plan", "POST drafts/{draft_id}/backtest"):
        assert config.AI_RATE_WEIGHTS.get(heavy, 1) > 1


def test_units_are_counted_in_redis_with_incrby(monkeypatch):
    calls = []

    class FakeRedis:
        async def eval(self, script, nkeys, key, window, units):
            calls.append((key, int(window), int(units)))
            return sum(c[2] for c in calls if c[0] == key)
    monkeypatch.setattr(core_rl, "redis_backed", lambda: True)
    import app.cache.client as cache_client
    monkeypatch.setattr(cache_client, "_get_client", lambda: FakeRedis())
    assert _run(core_rl.allow("t", "k", 10, 60, units=6)) is True
    assert _run(core_rl.allow("t", "k", 10, 60, units=6)) is False
    assert calls == [("rl:t:k", 60, 6), ("rl:t:k", 60, 6)]


def test_anonymous_calls_are_left_to_the_routes_own_auth(monkeypatch):
    _tight(monkeypatch, user=1, tenant=1)
    assert client.get("/api/ai/regimes").status_code == 200
    assert client.get("/api/ai/regimes").status_code == 200
    assert client.get("/api/ai/drafts").status_code in (401, 403)


def test_tenant_row_missing_plan_falls_back(monkeypatch):
    _tight(monkeypatch, user=2)
    headers, me = _owner("hc1b-plan@example.com")

    async def odd_plan():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            tenant.plan = "legacy-plan"
            await session.commit()
    _run(odd_plan())
    monkeypatch.setattr(config, "AI_RATE_LIMITS", {"free": {"user": 2, "tenant": 100}})
    assert [client.get("/api/ai/drafts", headers=headers).status_code for _ in range(3)] == [200, 200, 429]
