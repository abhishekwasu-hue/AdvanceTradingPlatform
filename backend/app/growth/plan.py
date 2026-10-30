"""The user's plan page: what his plan includes, in plain words, and how much of it he used (a usage meter).

Every number comes from existing limits and counters - nothing new is stored and nothing is enforced here:

* the plan (`app/plans/registry.py`): what it includes; the AMW-1 limits (`max_watchlists`, `max_screen_symbols`) are
  shown as limits only while `plan_limits_v2` enforces them, else the meter says there is no limit on the plan yet;
* his own rows: watchlists (`watchlists.user_id`), scans this month (`screen_runs.user_id`), chart analyses today and
  this month (Analyze a chart's own counter and limits, only while `chart_analyze` is on);
* his AI requests this month from the AI call log (`llm_calls.user_id`; calls on his own AI key included);
* the organisation's rows (a team shares them; the meter says so when the team has more than one member): trading bots
  running (`strategy_deployments`, the B2 limit that is always enforced) and backtests this month (`usage_records`,
  metered at the call site).

The month starts on the 1st at 00:00 UTC, as the AI budget and the chart-analysis counter already do. No price is
shown here and nothing starts a trial, changes a plan or sends a reminder (owner decision pending, OPEN_QUESTIONS
MO4-21..). A running trial's end date is shown because the subscription already holds it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.metering import month_start
from app.db.models import LlmCallRecord, ScreenRunRecord, Tenant, UsageRecord, User
from app.plans import limits as plan_limits
from app.plans.registry import Plan, get_plan

TEAM_NOTE = "Shared by everyone in your team."


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _n(value: float) -> str:
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,.1f}"


def includes(plan: Plan, *, v2_enforced: bool) -> List[Dict[str, str]]:
    """What the plan gives, one plain line each (no price, no LIVE: Phase 0 is paper only)."""
    rows = [
        {"key": "paper_bots", "text": f"Up to {_n(plan.max_active_deployments)} trading bots running at once"},
        {"key": "custom_strategies", "text": f"{_n(plan.max_custom_strategies)} strategies of your own"},
        {"key": "backtests", "text": f"{_n(plan.max_backtests_per_month)} backtests a month"},
        {"key": "watchlists", "text": f"{_n(plan.max_watchlists)} watchlists" if v2_enforced else "Watchlists"},
        {"key": "scan_size", "text": f"Up to {_n(plan.max_screen_symbols)} stocks in one scan" if v2_enforced else "Scans of your own lists"},
        {"key": "brokers", "text": f"{_n(plan.max_accounts)} broker account{'s' if plan.max_accounts != 1 else ''}"},
        {"key": "alerts", "text": f"{_n(plan.max_alert_channels)} alert channel{'s' if plan.max_alert_channels != 1 else ''} (for example Telegram)"},
        {"key": "options", "text": "Options tools: spreads and multi-leg plans" if plan.option_features else "Options tools: single options only"},
        {"key": "ai", "text": "AI tools on ATP's AI service" if plan.ai_features else "AI tools with your own AI key"},
        {"key": "team", "text": f"Team of up to {_n(plan.max_members)}" if plan.max_members > 1 else "Just you (no team members)"},
    ]
    return rows


def _meter(key: str, label: str, used: Optional[float], limit: Optional[float], period: str, note: Optional[str] = None) -> Dict[str, Any]:
    """One meter row. `limit` None = the plan sets no limit on it (the row then shows the count only)."""
    full = used is not None and limit is not None and limit > 0 and used >= limit
    near = used is not None and limit is not None and limit > 0 and not full and used >= 0.8 * limit
    if used is None:
        text = "Not counted yet"
    elif limit is None:
        text = f"{_n(used)} {period}".strip()
    else:
        text = f"{_n(used)} of {_n(limit)} {period}".strip()
    return {"key": key, "label": label, "used": used, "limit": limit, "period": period, "text": text,
            "state": "full" if full else "near" if near else "ok", "note": note}


async def _tenant_metric(session: AsyncSession, tenant_id: int, metric: str, since: datetime) -> float:
    return float(await session.scalar(select(func.coalesce(func.sum(UsageRecord.quantity), 0.0)).where(
        UsageRecord.tenant_id == tenant_id, UsageRecord.metric == metric, UsageRecord.period_start >= since)) or 0.0)


async def _scans(session: AsyncSession, user: User, since: datetime) -> int:
    return int(await session.scalar(select(func.count()).select_from(ScreenRunRecord).where(
        ScreenRunRecord.tenant_id == user.tenant_id, ScreenRunRecord.user_id == user.id, ScreenRunRecord.created_at >= since)) or 0)


async def _ai_requests(session: AsyncSession, user: User, since: datetime) -> int:
    return int(await session.scalar(select(func.count()).select_from(LlmCallRecord).where(
        LlmCallRecord.tenant_id == user.tenant_id, LlmCallRecord.user_id == user.id, LlmCallRecord.created_at >= since)) or 0)


async def _analyses(session: AsyncSession, user: User, tenant: Tenant, now: datetime) -> Optional[Dict[str, Any]]:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, "chart_analyze", tenant.id):
        return None
    from app.chart_analyze import limits as ca_limits
    from app.chart_analyze import settings as ca_settings
    return await ca_limits.usage(session, user, tenant, await ca_settings.load(session), now)


async def trial(session: AsyncSession, tenant: Tenant, now: datetime) -> Optional[Dict[str, Any]]:
    """A running trial (the subscription already holds its end): the plan and the days left. None otherwise."""
    from app.billing.service import STATUS_TRIALING, get_subscription
    sub = await get_subscription(session, tenant.id)
    end = _aware(sub.trial_end) if sub is not None else None
    if sub is None or sub.status != STATUS_TRIALING or end is None:
        return None
    seconds = (end - now).total_seconds()
    days = max(0, int(seconds // 86400) + (1 if seconds % 86400 else 0))
    return {"plan_name": get_plan(sub.plan_id).name, "ends_on": end.date().isoformat(), "days_left": days}


async def plan_page(session: AsyncSession, user: User, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    tenant = await plan_limits.load_tenant(session, user.tenant_id)
    plan = get_plan(tenant.plan)
    v2 = await plan_limits.v2_enforced(session, tenant.id)
    since = month_start(now)
    members = await plan_limits.count_members(session, tenant.id)
    shared = TEAM_NOTE if members > 1 else None

    from app.watchlists.service import count_own
    meters: List[Dict[str, Any]] = [
        _meter("watchlists", "Watchlists", await count_own(session, tenant.id, user.id), plan.max_watchlists if v2 else None, "",
               None if v2 else "No limit on your plan right now."),
        _meter("scans", "Scans", await _scans(session, user, since), None, "this month",
               f"Up to {_n(plan.max_screen_symbols)} stocks in one scan." if v2 else None),
        _meter("ai", "AI requests", await _ai_requests(session, user, since), None, "this month",
               "Requests on your own AI key are counted too."),
    ]
    analyses = await _analyses(session, user, tenant, now)
    if analyses is not None:
        meters.append(_meter("analyses_today", "Chart analyses today", analyses["daily_used"], analyses["daily_limit"] or None, "today"))
        meters.append(_meter("analyses_month", "Chart analyses", analyses["monthly_used"], analyses["monthly_limit"] or None, "this month"))
    meters += [
        _meter("backtests", "Backtests (Strategy Lab)", await _tenant_metric(session, tenant.id, "backtest", since),
               plan.max_backtests_per_month, "this month", shared),
        _meter("paper_bots", "Trading bots running", await plan_limits.count_active_deployments(session, tenant.id),
               plan.max_active_deployments, "", shared),
    ]
    return {
        "plan": {"id": plan.id, "name": plan.name, "status": (tenant.status or "active")},
        "trial": await trial(session, tenant, now),
        "includes": includes(plan, v2_enforced=v2),
        "meters": meters,
        "month_starts": since.date().isoformat(),
        "resets": "Monthly counts start again on the 1st of each month.",
    }
