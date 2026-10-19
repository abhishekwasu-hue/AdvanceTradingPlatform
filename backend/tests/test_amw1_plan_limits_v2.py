"""AMW-1 (docs/design/AMW_PORT.md §4.1): plan limits v2 behind the `plan_limits_v2` flag (off by default).

Behaviour specifications ported from the AMW reference tests (re-written on ATP's plans and fixtures, no AMW code):
- `test_saas_sched.py::test_plan_limits_enforced_in_backend`: 50 symbols per scan pass on the free plan and 51 are
  refused; a 5-minute schedule is refused on the free plan;
- `test_decisions.py::test_q002_plans_are_provisional_and_config_driven`: changing a plan's value changes enforcement
  without code changes in the checks;
- `test_decisions.py::test_q005_defaults_and_plan_aware_cleanup`: report retention days per plan (7 / 90 / 365).
ATP-specific: the flag gates enforcement and the Billing payload; a refusal is the B2 402 with the upgrade hint; usage
counts come from the slice that owns the rows and are the caller's own.
"""
import asyncio
import dataclasses
import uuid

import pytest
from fastapi import HTTPException

from app.db.models import Tenant
from app.plans import limits
from app.plans.registry import PLANS, Plan, get_plan
from app.platform import controls
from tests.test_auth_api import _register, _session_factory, client

FIELDS = ("max_watchlists", "max_screen_symbols", "reports_per_day", "report_retention_days", "report_storage_mb",
          "report_schedules", "min_schedule_interval_min", "lab_strategies", "lab_cpu_seconds_per_day")


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"amw1-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _owner(tag: str):
    headers = {"Authorization": f"Bearer {_register(_email(tag))}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return headers, me


def _flag(tenant_id=None, on: bool = True, globally: bool = False) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get(limits.FLAG_V2) or {"on": False, "tenants": []}
            tenants = set(entry.get("tenants") or [])
            if tenant_id is not None:
                (tenants.add if on else tenants.discard)(tenant_id)
            flags[limits.FLAG_V2] = {"on": on if globally else bool(entry.get("on")), "tenants": sorted(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _with_tenant(tenant_id: int, fn, **fields):
    """Runs `fn(session, tenant)` on the tenant row (fields set on it first, not committed)."""
    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            for k, v in fields.items():
                setattr(tenant, k, v)
            return await fn(session, tenant)
    return _run(go())


def _refused(tenant_id: int, fn, **fields) -> HTTPException:
    with pytest.raises(HTTPException) as err:
        _with_tenant(tenant_id, fn, **fields)
    return err.value


# --- the plan catalogue ----------------------------------------------------------------------------------------------

def test_every_plan_has_every_v2_field_and_higher_plans_never_get_less():
    for plan in PLANS.values():
        for field in FIELDS:
            assert isinstance(getattr(plan, field), int) and getattr(plan, field) > 0, (plan.id, field)
    free, pro, business = PLANS["free"], PLANS["pro"], PLANS["business"]
    for field in FIELDS:
        if field == "min_schedule_interval_min":            # a shorter interval is more
            assert free.min_schedule_interval_min >= pro.min_schedule_interval_min >= business.min_schedule_interval_min
        else:
            assert getattr(free, field) <= getattr(pro, field) <= getattr(business, field), field


def test_defaults_are_the_free_values_so_a_forgotten_field_restricts():
    bare = Plan(id="x", name="X", max_active_deployments=1, live_trading=False, max_custom_strategies=1, max_members=1,
                max_alert_channels=1, description="")
    assert {f: getattr(bare, f) for f in FIELDS} == {f: getattr(PLANS["free"], f) for f in FIELDS}
    assert get_plan("nonsense").max_screen_symbols == PLANS["free"].max_screen_symbols


def test_report_retention_days_per_plan():
    # AMW q005: free 7, pro 90, premium/enterprise 365 -> ATP Free / Pro / Business
    assert [PLANS[p].report_retention_days for p in ("free", "pro", "business")] == [7, 90, 365]
    assert limits.report_retention_days(None) == 7
    assert limits.report_retention_days(Tenant(name="t", plan="pro")) == 90


def test_limits_v2_lists_every_limit_by_key():
    out = limits.limits_v2(PLANS["pro"])
    assert set(out) == {limit.key for limit in limits.LIMITS_V2}
    assert out["watchlists"] == 25 and out["screen_symbols"] == 500 and out["min_schedule_interval_min"] == 15


# --- the flag ---------------------------------------------------------------------------------------------------------

def test_flag_is_registered_and_off_by_default():
    assert limits.FLAG_V2 in controls.FEATURE_FLAGS
    assert limits.FLAG_V2 in controls.DEFAULT_OFF_FLAGS

    async def go():
        async with _session_factory() as session:
            return (await controls.feature_flags(session))[limits.FLAG_V2]["on"]
    assert _run(go()) is False


def test_flag_off_enforces_nothing():
    _, me = _owner("off")

    async def everything(session, tenant):
        await limits.check_screen_symbols(session, tenant, 10_000)
        await limits.check_schedule_interval(session, tenant, 1)
        await limits.check_can_add_watchlist(session, tenant, 1_000)
        await limits.check_can_add_report(session, tenant, 1_000)
        await limits.check_report_storage(session, tenant, 10_000, 10)
        await limits.check_can_add_report_schedule(session, tenant, 1_000)
        await limits.check_can_add_lab_strategy(session, tenant, 1_000)
        await limits.check_lab_cpu(session, tenant, 10_000, 10)
        return True
    assert _with_tenant(me["tenant_id"], everything) is True
    assert _with_tenant(me["tenant_id"], everything, status="suspended") is True


# --- enforcement with the flag on -------------------------------------------------------------------------------------

def test_symbols_per_scan_and_schedule_interval_on_the_free_plan():
    _, me = _owner("scan")
    tid = me["tenant_id"]
    _flag(tid)
    assert _with_tenant(tid, lambda s, t: limits.check_screen_symbols(s, t, 50)) is None
    err = _refused(tid, lambda s, t: limits.check_screen_symbols(s, t, 51))
    assert err.status_code == 402 and "allows 50 symbols in one screener run; this run has 51" in err.detail
    assert "Upgrade the plan" in err.detail
    err = _refused(tid, lambda s, t: limits.check_schedule_interval(s, t, 5))
    assert err.status_code == 402 and "every 60 minutes or longer" in err.detail
    assert _with_tenant(tid, lambda s, t: limits.check_schedule_interval(s, t, 60)) is None
    # Pro allows 500 symbols and 15-minute schedules
    assert _with_tenant(tid, lambda s, t: limits.check_screen_symbols(s, t, 500), plan="pro") is None
    assert _with_tenant(tid, lambda s, t: limits.check_schedule_interval(s, t, 15), plan="pro") is None


def test_counted_limits_refuse_at_the_plan_number_with_the_upgrade_hint():
    _, me = _owner("count")
    tid = me["tenant_id"]
    _flag(tid)
    checks = {
        "watchlists": (limits.check_can_add_watchlist, 5, "5 watchlists; you have 5"),
        "reports_per_day": (limits.check_can_add_report, 10, "10 reports a day; you have 10"),
        "report_schedules": (limits.check_can_add_report_schedule, 1, "1 report schedules; you have 1"),
        "lab_strategies": (limits.check_can_add_lab_strategy, 3, "3 Strategy Lab strategies; you have 3"),
    }
    for key, (check, allowed, words) in checks.items():
        assert _with_tenant(tid, lambda s, t, c=check, n=allowed: c(s, t, n - 1)) is None, key
        err = _refused(tid, lambda s, t, c=check, n=allowed: c(s, t, n))
        assert err.status_code == 402 and words in err.detail and "Upgrade the plan" in err.detail, (key, err.detail)
    # Business: the top plan, so no upgrade hint
    err = _refused(tid, lambda s, t: limits.check_can_add_watchlist(s, t, 100), plan="business")
    assert "100 watchlists" in err.detail and "Upgrade" not in err.detail


def test_amount_limits_count_what_is_used_plus_what_the_request_adds():
    _, me = _owner("amount")
    tid = me["tenant_id"]
    _flag(tid)
    assert _with_tenant(tid, lambda s, t: limits.check_report_storage(s, t, 49.5, 0.5)) is None
    err = _refused(tid, lambda s, t: limits.check_report_storage(s, t, 49.5, 0.6))
    assert err.status_code == 402 and "50 MB of stored reports; you have used 49.5 and this needs 0.6 more" in err.detail
    assert _with_tenant(tid, lambda s, t: limits.check_lab_cpu(s, t, 299)) is None
    err = _refused(tid, lambda s, t: limits.check_lab_cpu(s, t, 300))
    assert "300 Strategy Lab CPU seconds a day; you have used 300." in err.detail


def test_a_suspended_tenant_is_refused_while_the_flag_is_on():
    _, me = _owner("susp")
    _flag(me["tenant_id"])
    err = _refused(me["tenant_id"], lambda s, t: limits.check_can_add_watchlist(s, t, 0), status="suspended")
    assert err.status_code == 403


def test_the_flag_is_per_tenant():
    _, a = _owner("ta")
    _, b = _owner("tb")
    _flag(a["tenant_id"])
    _refused(a["tenant_id"], lambda s, t: limits.check_screen_symbols(s, t, 51))
    assert _with_tenant(b["tenant_id"], lambda s, t: limits.check_screen_symbols(s, t, 51)) is None


def test_plan_values_drive_enforcement(monkeypatch):
    # AMW q002: change the plan's value, not the checks
    _, me = _owner("cfg")
    _flag(me["tenant_id"])
    monkeypatch.setitem(PLANS, "free", dataclasses.replace(PLANS["free"], max_watchlists=1))
    err = _refused(me["tenant_id"], lambda s, t: limits.check_can_add_watchlist(s, t, 1))
    assert "allows 1 watchlists" in err.detail


def test_a_check_must_match_its_limit_kind():
    _, me = _owner("kind")
    _flag(me["tenant_id"])
    with pytest.raises(ValueError):
        _with_tenant(me["tenant_id"], lambda s, t: limits.check_v2_count(s, t, "screen_symbols", 1))
    with pytest.raises(ValueError):
        limits.register_usage_counter("screen_symbols", lambda s, t, u: asyncio.sleep(0, 1))


# --- usage counters and the Billing payload ----------------------------------------------------------------------------

def test_usage_counters_are_the_callers_own_and_none_until_registered(monkeypatch):
    monkeypatch.setattr(limits, "_USAGE_COUNTERS", {})
    seen = []

    async def count(session, tenant_id, user_id):
        seen.append((tenant_id, user_id))
        return 3
    limits.register_usage_counter("watchlists", count)

    async def go():
        async with _session_factory() as session:
            return await limits.usage_v2(session, 7, 11)
    out = _run(go())
    assert out["watchlists"] == 3 and seen == [(7, 11)]
    assert out["reports_per_day"] is None and out["lab_cpu_seconds_per_day"] is None
    assert "screen_symbols" not in out and "report_retention_days" not in out    # per-request limits are not usage


def test_billing_shows_v2_limits_and_usage_only_while_the_flag_is_on(monkeypatch):
    monkeypatch.setattr(limits, "_USAGE_COUNTERS", {})

    async def count(session, tenant_id, user_id):
        return 2
    limits.register_usage_counter("watchlists", count)
    headers, me = _owner("billing")
    other_headers, _ = _owner("billing-other")

    overview = client.get("/api/billing", headers=headers).json()
    assert "limits_v2" not in overview and "usage_v2" not in overview
    assert "counts" not in client.get("/api/billing/usage", headers=headers).json()

    _flag(me["tenant_id"])
    overview = client.get("/api/billing", headers=headers).json()
    assert overview["limits_v2"] == limits.limits_v2(PLANS["free"])
    assert overview["usage_v2"]["watchlists"] == 2 and overview["usage_v2"]["reports_per_day"] is None
    counts = client.get("/api/billing/usage", headers=headers).json()["counts"]
    assert counts["watchlists"] == 2 and counts["members"] == 1          # the B2 counts sit next to the v2 ones
    # another organisation without the flag sees none of it
    assert "limits_v2" not in client.get("/api/billing", headers=other_headers).json()


def test_plan_catalogue_lists_v2_limits_only_when_the_flag_is_on_for_everyone():
    assert all("limits_v2" not in p for p in client.get("/api/billing/plans").json())
    _flag(globally=True)
    try:
        plans = {p["id"]: p for p in client.get("/api/billing/plans").json()}
        assert plans["business"]["limits_v2"]["screen_symbols"] == 2500 and plans["free"]["limits_v2"]["watchlists"] == 5
    finally:
        _flag(on=False, globally=True)
