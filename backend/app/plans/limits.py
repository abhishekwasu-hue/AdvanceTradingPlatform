"""Where plan limits and tenant status are enforced. Every check raises a 402 (plan) or 403
(suspended) with a message that says which limit, what the plan allows, and what to do - the
Team tab shows the same numbers as usage-vs-limit so nobody hits these blind."""
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import DeploymentStatus
from app.db.models import (
    AlertChannelRecord,
    CustomStrategyRecord,
    StrategyDeploymentRecord,
    Tenant,
    TenantInviteRecord,
    User,
)
from app.plans.registry import Plan, get_plan

TENANT_ACTIVE = "active"
TENANT_SUSPENDED = "suspended"


class PlanLimitExceeded(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=detail)


class TenantSuspended(HTTPException):
    def __init__(self, tenant: Tenant) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Organisation '{tenant.name}' is {tenant.status}: trading and configuration are disabled. "
                   "Contact support.",
        )


async def load_tenant(session: AsyncSession, tenant_id: int) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Unknown organisation")
    return tenant


def tenant_is_active(tenant: Tenant) -> bool:
    return (tenant.status or TENANT_ACTIVE).lower() == TENANT_ACTIVE


def ensure_tenant_active(tenant: Tenant) -> None:
    if not tenant_is_active(tenant):
        raise TenantSuspended(tenant)


async def count_active_deployments(session: AsyncSession, tenant_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(StrategyDeploymentRecord).where(
            StrategyDeploymentRecord.tenant_id == tenant_id,
            StrategyDeploymentRecord.status != DeploymentStatus.STOPPED.value,
        )
    ) or 0


async def count_custom_strategies(session: AsyncSession, tenant_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(CustomStrategyRecord).where(CustomStrategyRecord.tenant_id == tenant_id)
    ) or 0


async def count_members(session: AsyncSession, tenant_id: int) -> int:
    from datetime import datetime, timezone
    users = await session.scalar(
        select(func.count()).select_from(User).where(User.tenant_id == tenant_id, User.is_active.is_(True))
    ) or 0
    invites = await session.scalar(
        select(func.count()).select_from(TenantInviteRecord).where(
            TenantInviteRecord.tenant_id == tenant_id, TenantInviteRecord.accepted_at.is_(None),
            TenantInviteRecord.expires_at > datetime.now(timezone.utc),
        )
    ) or 0
    return users + invites


async def count_alert_channels(session: AsyncSession, tenant_id: int) -> int:
    return await session.scalar(
        # B8: the phone-notification channel (FCM) is made by the app when a phone registers; it is not a channel the
        # organisation sets up, so it never counts against the plan's alert-channel limit.
        select(func.count()).select_from(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant_id,
                                                                   AlertChannelRecord.channel_type != "FCM")
    ) or 0


async def usage(session: AsyncSession, tenant_id: int) -> Dict[str, int]:
    return {
        "active_deployments": await count_active_deployments(session, tenant_id),
        "custom_strategies": await count_custom_strategies(session, tenant_id),
        "members": await count_members(session, tenant_id),
        "alert_channels": await count_alert_channels(session, tenant_id),
    }


def limits(plan: Plan) -> Dict[str, object]:
    return {
        "active_deployments": plan.max_active_deployments,
        "live_trading": plan.live_trading,
        "custom_strategies": plan.max_custom_strategies,
        "members": plan.max_members,
        "alert_channels": plan.max_alert_channels,
        # Phase K commercial fields
        "price_monthly": plan.price_monthly, "price_yearly": plan.price_yearly, "currency": plan.currency,
        "live_strategies": plan.max_live_strategies, "backtests_per_month": plan.max_backtests_per_month,
        "api_calls_per_day": plan.max_api_calls_per_day, "accounts": plan.max_accounts, "brokers": plan.max_brokers,
        "option_features": plan.option_features, "ai_features": plan.ai_features, "marketplace_access": plan.marketplace_access,
        "support_level": plan.support_level, "trial_days": plan.trial_days,
    }


def feature_allowed(tenant: Optional[Tenant], feature: str) -> bool:
    """Plan-gated features (Phase K): option_features, ai_features, marketplace_access, public_api."""
    if tenant is None or not tenant_is_active(tenant):
        return False
    plan = get_plan(tenant.plan)
    if feature == "public_api":
        return plan.max_api_calls_per_day > 0
    return bool(getattr(plan, feature, False))


def require_feature(tenant: Tenant, feature: str, label: str) -> None:
    if not feature_allowed(tenant, feature):
        raise PlanLimitExceeded(f"{label} needs the Pro or Business plan (current: {get_plan(tenant.plan).name}).")


def _upgrade_hint(plan: Plan) -> str:
    return "" if plan.id == "business" else " Upgrade the plan to add more."


async def check_can_add_deployment(session: AsyncSession, tenant: Tenant, *, live: bool, adding: bool = True) -> None:
    ensure_tenant_active(tenant)
    plan = get_plan(tenant.plan)
    if live and not plan.live_trading:
        raise PlanLimitExceeded(f"The {plan.name} plan is paper trading only - LIVE deployments need Pro or Business.")
    if adding:
        current = await count_active_deployments(session, tenant.id)
        if current >= plan.max_active_deployments:
            raise PlanLimitExceeded(
                f"The {plan.name} plan allows {plan.max_active_deployments} active deployment(s); you have {current}. "
                f"Stop one first.{_upgrade_hint(plan)}"
            )


async def check_can_add_custom_strategy(session: AsyncSession, tenant: Tenant) -> None:
    ensure_tenant_active(tenant)
    plan = get_plan(tenant.plan)
    current = await count_custom_strategies(session, tenant.id)
    if current >= plan.max_custom_strategies:
        raise PlanLimitExceeded(
            f"The {plan.name} plan allows {plan.max_custom_strategies} custom strategies; you have {current}.{_upgrade_hint(plan)}"
        )


async def check_can_add_member(session: AsyncSession, tenant: Tenant) -> None:
    ensure_tenant_active(tenant)
    plan = get_plan(tenant.plan)
    current = await count_members(session, tenant.id)
    if current >= plan.max_members:
        raise PlanLimitExceeded(
            f"The {plan.name} plan allows {plan.max_members} team member(s) including open invites; you have {current}.{_upgrade_hint(plan)}"
        )


async def check_can_add_alert_channel(session: AsyncSession, tenant: Tenant) -> None:
    ensure_tenant_active(tenant)
    plan = get_plan(tenant.plan)
    current = await count_alert_channels(session, tenant.id)
    if current >= plan.max_alert_channels:
        raise PlanLimitExceeded(
            f"The {plan.name} plan allows {plan.max_alert_channels} alert channel(s); you have {current}.{_upgrade_hint(plan)}"
        )


def live_allowed(tenant: Optional[Tenant]) -> bool:
    return tenant is not None and tenant_is_active(tenant) and get_plan(tenant.plan).live_trading


# --- AMW-1: plan limits v2 (flag `plan_limits_v2`, off by default) ---------------------------------------------------
# The limits of the features the AMW port adds (docs/design/AMW_PORT.md §4.1). Each check is a no-op while the flag is
# off for the tenant; when it is on, a request over the limit gets the same 402 and upgrade hint as the B2 limits above.
# The slices that own the resources pass their own counts in (they own the tables), and register a usage counter so
# `GET /api/billing` and `GET /api/billing/usage` can show usage against the limit.
FLAG_V2 = "plan_limits_v2"

COUNT = "count"            # a number of things the user has; adding one more must stay within the limit
AMOUNT = "amount"          # a quantity used over a window (MB stored, CPU seconds today); used + adding within the limit
MAX_REQUEST = "max"        # the size of one request (symbols in one scan)
MIN_REQUEST = "min"        # a lower bound on one request (a schedule's interval)
RETENTION = "retention"    # read by a retention job; nothing to refuse at request time


@dataclass(frozen=True)
class LimitV2:
    key: str        # the name in `limits_v2` / `usage_v2`
    field: str      # the Plan attribute
    kind: str
    label: str      # how a refusal names it ("allows 5 {label}")


LIMITS_V2: Tuple[LimitV2, ...] = (
    LimitV2("watchlists", "max_watchlists", COUNT, "watchlists"),
    LimitV2("screen_symbols", "max_screen_symbols", MAX_REQUEST, "symbols in one screener run"),
    LimitV2("reports_per_day", "reports_per_day", COUNT, "reports a day"),
    LimitV2("report_retention_days", "report_retention_days", RETENTION, "days of report retention"),
    LimitV2("report_storage_mb", "report_storage_mb", AMOUNT, "MB of stored reports"),
    LimitV2("report_schedules", "report_schedules", COUNT, "report schedules"),
    LimitV2("min_schedule_interval_min", "min_schedule_interval_min", MIN_REQUEST, "minutes between scheduled runs"),
    LimitV2("lab_strategies", "lab_strategies", COUNT, "Strategy Lab strategies"),
    LimitV2("lab_cpu_seconds_per_day", "lab_cpu_seconds_per_day", AMOUNT, "Strategy Lab CPU seconds a day"),
)
_BY_KEY: Dict[str, LimitV2] = {limit.key: limit for limit in LIMITS_V2}

# (session, tenant_id, user_id) -> the caller's current usage of one limit. Registered by the slice that owns the rows.
UsageCounter = Callable[[AsyncSession, int, int], Awaitable[float]]
_USAGE_COUNTERS: Dict[str, UsageCounter] = {}


def register_usage_counter(key: str, counter: UsageCounter) -> None:
    """Called once by the slice that owns a limit's rows (watchlists, reports, Lab...), at import time."""
    limit = _BY_KEY.get(key)
    if limit is None or limit.kind not in (COUNT, AMOUNT):
        raise ValueError(f"'{key}' is not a counted plan limit - counted: {sorted(k for k, v in _BY_KEY.items() if v.kind in (COUNT, AMOUNT))}")
    _USAGE_COUNTERS[key] = counter


def limits_v2(plan: Plan) -> Dict[str, int]:
    return {limit.key: int(getattr(plan, limit.field)) for limit in LIMITS_V2}


async def v2_enforced(session: AsyncSession, tenant_id: Optional[int]) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG_V2, tenant_id)


async def usage_v2(session: AsyncSession, tenant_id: int, user_id: int) -> Dict[str, Optional[float]]:
    """The caller's usage of every counted limit; None where no slice counts it yet (the feature is not built)."""
    out: Dict[str, Optional[float]] = {}
    for limit in LIMITS_V2:
        if limit.kind not in (COUNT, AMOUNT):
            continue
        counter = _USAGE_COUNTERS.get(limit.key)
        out[limit.key] = None if counter is None else await counter(session, tenant_id, user_id)
    return out


def _fmt(value: float) -> str:
    return f"{value:g}"


async def _plan_if_enforced(session: AsyncSession, tenant: Tenant, key: str, kind: str) -> Optional[Tuple[Plan, int]]:
    limit = _BY_KEY[key]
    if limit.kind != kind:
        raise ValueError(f"plan limit '{key}' is a {limit.kind} limit, not {kind}")
    if not await v2_enforced(session, tenant.id):
        return None
    ensure_tenant_active(tenant)
    plan = get_plan(tenant.plan)
    return plan, int(getattr(plan, limit.field))


async def check_v2_count(session: AsyncSession, tenant: Tenant, key: str, current: int) -> None:
    """Adding one more: refused when the user already has the plan's number.

    Not race-safe by itself: `current` is a count the caller took, and two requests counting at the same time both pass.
    The caller must serialise per user - e.g. `SELECT users.id ... FOR UPDATE` on the caller's row (held to the commit)
    before counting, as AMW-2's watchlist create does - or re-count after the insert and undo the one over the limit.
    The same holds for `check_v2_amount`."""
    found = await _plan_if_enforced(session, tenant, key, COUNT)
    if found is None:
        return
    plan, allowed = found
    if current >= allowed:
        raise PlanLimitExceeded(f"The {plan.name} plan allows {allowed} {_BY_KEY[key].label}; you have {current}.{_upgrade_hint(plan)}")


async def check_v2_amount(session: AsyncSession, tenant: Tenant, key: str, used: float, adding: float = 0.0) -> None:
    """Using `adding` more on top of `used`: refused when the total would go over (or `used` already reached) the limit."""
    found = await _plan_if_enforced(session, tenant, key, AMOUNT)
    if found is None:
        return
    plan, allowed = found
    if used >= allowed or used + adding > allowed:
        raise PlanLimitExceeded(f"The {plan.name} plan allows {allowed} {_BY_KEY[key].label}; you have used {_fmt(used)}"
                                f"{f' and this needs {_fmt(adding)} more' if adding else ''}.{_upgrade_hint(plan)}")


async def check_screen_symbols(session: AsyncSession, tenant: Tenant, requested: int) -> None:
    found = await _plan_if_enforced(session, tenant, "screen_symbols", MAX_REQUEST)
    if found is None:
        return
    plan, allowed = found
    if requested > allowed:
        raise PlanLimitExceeded(f"The {plan.name} plan allows {allowed} symbols in one screener run; this run has {requested}. "
                                f"Split the list.{_upgrade_hint(plan)}")


async def check_schedule_interval(session: AsyncSession, tenant: Tenant, minutes: float) -> None:
    found = await _plan_if_enforced(session, tenant, "min_schedule_interval_min", MIN_REQUEST)
    if found is None:
        return
    plan, allowed = found
    if minutes < allowed:
        raise PlanLimitExceeded(f"The {plan.name} plan allows schedules every {allowed} minutes or longer; this one is every "
                                f"{_fmt(minutes)}.{_upgrade_hint(plan)}")


def report_retention_days(tenant: Optional[Tenant]) -> int:
    """For the report retention job (AMW-14). Not a refusal, so not behind the flag: the job decides when it runs."""
    return get_plan(tenant.plan if tenant is not None else "").report_retention_days


async def check_can_add_watchlist(session: AsyncSession, tenant: Tenant, current: int) -> None:
    await check_v2_count(session, tenant, "watchlists", current)


async def check_can_add_report(session: AsyncSession, tenant: Tenant, reports_today: int) -> None:
    await check_v2_count(session, tenant, "reports_per_day", reports_today)


async def check_report_storage(session: AsyncSession, tenant: Tenant, used_mb: float, adding_mb: float) -> None:
    await check_v2_amount(session, tenant, "report_storage_mb", used_mb, adding_mb)


async def check_can_add_report_schedule(session: AsyncSession, tenant: Tenant, current: int) -> None:
    await check_v2_count(session, tenant, "report_schedules", current)


async def check_can_add_lab_strategy(session: AsyncSession, tenant: Tenant, current: int) -> None:
    await check_v2_count(session, tenant, "lab_strategies", current)


async def check_lab_cpu(session: AsyncSession, tenant: Tenant, used_seconds: float, adding_seconds: float = 0.0) -> None:
    await check_v2_amount(session, tenant, "lab_cpu_seconds_per_day", used_seconds, adding_seconds)
