"""The plan catalogue. Plans live in code, not the DB: their limits change with releases and
need review like any other business rule, and there are three of them. `tenants.plan` holds the
id; a SUPER_ADMIN changes it (Phase B3). An unknown id falls back to `free` so a typo in the DB
can only ever *restrict* a tenant, never unlock live trading by accident.
"""
from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    max_active_deployments: int      # ACTIVE + PAUSED (STOPPED ones don't count)
    live_trading: bool               # may create/resume LIVE deployments and the worker will fire them
    max_custom_strategies: int
    max_members: int                 # active users + open invites
    max_alert_channels: int
    description: str


PLANS: Dict[str, Plan] = {
    "free": Plan(
        id="free", name="Free", max_active_deployments=2, live_trading=False, max_custom_strategies=3,
        max_members=1, max_alert_channels=1,
        description="Paper trading only. Try strategies on live market data with one account.",
    ),
    "pro": Plan(
        id="pro", name="Pro", max_active_deployments=10, live_trading=True, max_custom_strategies=25,
        max_members=5, max_alert_channels=2,
        description="Live trading for a small desk: up to 10 concurrent deployments and 5 team members.",
    ),
    "business": Plan(
        id="business", name="Business", max_active_deployments=50, live_trading=True, max_custom_strategies=200,
        max_members=25, max_alert_channels=2,
        description="For prop desks and advisories: 50 concurrent deployments, 25 members, priority support.",
    ),
}

DEFAULT_PLAN_ID = "free"


def get_plan(plan_id: str) -> Plan:
    return PLANS.get((plan_id or "").lower(), PLANS[DEFAULT_PLAN_ID])
