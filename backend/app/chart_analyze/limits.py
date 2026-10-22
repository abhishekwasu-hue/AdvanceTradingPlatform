"""Analyze a chart: plan limits and the AI cost cap (numbers from the admin settings, `settings.DEFAULTS`).

- **Tier**: `trial` while the organisation's subscription is TRIALING, else the plan id (free / pro / business; an
  unknown plan reads as free, so a typo can only restrict). A suspended organisation gets nothing new.
- **Analyses a day / a month**: every chart a user adds (upload or link) is one analysis; deleted and expired rows still
  count until `counter_keep_days`, so deleting cannot reset a limit. The day and the month start at 00:00 UTC and on
  the 1st (UTC), like the BYOK caps. 0 = not included in the plan (402); used up = 429 with when it resets.
- **AI cost cap**: the USD this user's analyses spent this month on the organisation's AI (not on their own key),
  plus the next call's estimate, must stay within the tier's `cost_cap_usd` (429 when it would not).
Counting before the insert is serialised per user by a transaction-scoped advisory lock (Postgres), taken only for the
short count-and-insert, so two requests at the same moment cannot both take the last analysis. The cost cap reserves one
estimate per running analysis and counts spend by the month the analysis ran.
"""
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.metering import month_start
from app.db.models import ChartAnalysisRecord, Tenant, User
from app.plans.registry import DEFAULT_PLAN_ID, PLANS


class LimitReached(HTTPException):
    def __init__(self, detail: str, *, code: int = status.HTTP_429_TOO_MANY_REQUESTS) -> None:
        super().__init__(status_code=code, detail={"message": detail, "code": "limit_reached" if code == 429 else "not_in_plan"})


def day_start(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


async def tier_for(session: AsyncSession, tenant: Tenant) -> str:
    from app.billing.service import STATUS_TRIALING, get_subscription
    sub = await get_subscription(session, tenant.id)
    if sub is not None and sub.status == STATUS_TRIALING:
        return "trial"
    plan = (tenant.plan or "").lower()
    return plan if plan in PLANS else DEFAULT_PLAN_ID


async def _count(session: AsyncSession, user: User, since: datetime) -> int:
    return int(await session.scalar(select(func.count()).select_from(ChartAnalysisRecord).where(
        ChartAnalysisRecord.tenant_id == user.tenant_id, ChartAnalysisRecord.user_id == user.id,
        ChartAnalysisRecord.created_at >= since)) or 0)


async def platform_cost(session: AsyncSession, user: User, since: datetime) -> float:
    """USD spent on the organisation's AI by this user's analyses that RAN since `since` (by analysed_at: an item added
    last month and analysed this month counts this month)."""
    return float(await session.scalar(select(func.coalesce(func.sum(ChartAnalysisRecord.cost_usd), 0.0)).where(
        ChartAnalysisRecord.tenant_id == user.tenant_id, ChartAnalysisRecord.user_id == user.id,
        ChartAnalysisRecord.billed_to_user_key.is_(False), ChartAnalysisRecord.analysed_at >= since)) or 0.0)


async def running_count(session: AsyncSession, user: User, settings: Dict[str, Any], now: datetime) -> int:
    """This user's analyses running right now (their cost is not known yet: each reserves one estimate). A stale
    "running" row (past running_timeout_minutes: its run is gone) reserves nothing."""
    from app.chart_analyze.service import RUNNING, stale_before
    return int(await session.scalar(select(func.count()).select_from(ChartAnalysisRecord).where(
        ChartAnalysisRecord.tenant_id == user.tenant_id, ChartAnalysisRecord.user_id == user.id,
        ChartAnalysisRecord.status == RUNNING, ChartAnalysisRecord.started_at >= stale_before(settings, now))) or 0)


async def usage(session: AsyncSession, user: User, tenant: Tenant, settings: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    tier = await tier_for(session, tenant)
    row = settings["limits"][tier]
    return {"tier": tier, "daily_limit": int(row["daily"]), "monthly_limit": int(row["monthly"]), "cost_cap_usd": float(row["cost_cap_usd"]),
            "daily_used": await _count(session, user, day_start(now)), "monthly_used": await _count(session, user, month_start(now)),
            "cost_used_usd": round(await platform_cost(session, user, month_start(now)), 6),
            "resets": "daily at 00:00 UTC, monthly on the 1st (UTC)"}


LOCK_NAMESPACE = "chart_analyze_add"


async def lock_user(session: AsyncSession, user: User) -> None:
    """Serialises one user's count-then-insert until the commit: a transaction-scoped advisory lock keyed by the user
    (it blocks only another add of the same user - never a row of `users` or an insert that references it). A no-op on
    SQLite (tests), which serialises writes anyway."""
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:ns), :uid)"), {"ns": LOCK_NAMESPACE, "uid": int(user.id)})


async def check_can_add(session: AsyncSession, user: User, tenant: Tenant, settings: Dict[str, Any], now: Optional[datetime] = None, *,
                        lock: bool = False) -> None:
    """The plan limits for one more chart. `lock=False` (the early check before the upload is read or the link fetched)
    takes no lock; the add itself checks again with `lock=True` right before its insert."""
    from app.plans.limits import ensure_tenant_active
    ensure_tenant_active(tenant)
    if lock:
        await lock_user(session, user)
    used = await usage(session, user, tenant, settings, now)
    if used["monthly_limit"] <= 0 or used["daily_limit"] <= 0:
        raise LimitReached("Analyze a chart is not included in your plan. Upgrade the plan to use it.", code=status.HTTP_402_PAYMENT_REQUIRED)
    if used["daily_used"] >= used["daily_limit"]:
        raise LimitReached(f"Limit reached: your plan allows {used['daily_limit']} chart analyses a day and you have used them. "
                           "It resets at 00:00 UTC.")
    if used["monthly_used"] >= used["monthly_limit"]:
        raise LimitReached(f"Limit reached: your plan allows {used['monthly_limit']} chart analyses a month and you have used them. "
                           "It resets on the 1st (UTC).")


async def check_cost(session: AsyncSession, user: User, tenant: Tenant, settings: Dict[str, Any], estimate_usd: float,
                     now: Optional[datetime] = None) -> None:
    """The organisation's AI only (a BYOK call is capped by the user's own BYOK caps instead)."""
    now = now or datetime.now(timezone.utc)
    used = await usage(session, user, tenant, settings, now)
    cap = used["cost_cap_usd"]
    # Every running analysis (this one included: it is claimed before the call) reserves one estimate, so parallel runs
    # cannot each pass the cap on the same remaining budget.
    reserved = max(1, await running_count(session, user, settings, now)) * max(0.0, estimate_usd)
    if cap <= 0 or used["cost_used_usd"] >= cap or used["cost_used_usd"] + reserved > cap:
        raise LimitReached("Limit reached: this month's AI budget for chart analyses on your plan is used up. "
                           "It resets on the 1st (UTC), or add your own AI key under Settings > AI.")
