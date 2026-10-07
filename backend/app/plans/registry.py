"""The plan catalogue. Plans live in code, not the DB: their limits change with releases and
need review like any other business rule, and there are three of them. `tenants.plan` holds the
id; a SUPER_ADMIN changes it (Phase B3). An unknown id falls back to `free` so a typo in the DB
can only ever *restrict* a tenant, never unlock live trading by accident.
"""
import os
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
    # Phase K (master prompt V3.14 rule 5): the commercial fields of the tier.
    price_monthly: float = 0.0        # in `currency`, per billing cycle MONTHLY
    price_yearly: float = 0.0
    currency: str = "INR"
    max_live_strategies: int = 0      # LIVE deployments at once (subset of max_active_deployments)
    max_backtests_per_month: int = 50
    max_api_calls_per_day: int = 0    # public API (0 = no public API access)
    max_accounts: int = 1             # broker accounts
    max_brokers: int = 1
    option_features: bool = False     # multi-leg structures, strike filters
    ai_features: bool = False         # conversational strategy builder, AI analytics
    marketplace_access: bool = False  # subscribe to / publish marketplace strategies
    support_level: str = "community"
    trial_days: int = 0
    # P0.8-C: monthly spend on external AI providers (INR) before the tenant falls back to the rule-based provider;
    # 0 = no cap. The operator sets `AI_BUDGET_INR_PRO` / `AI_BUDGET_INR_BUSINESS` to change the defaults.
    ai_monthly_budget_inr: float = 0.0


def _budget(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, str(default))))
    except ValueError:
        return default


PLANS: Dict[str, Plan] = {
    "free": Plan(
        id="free", name="Free", max_active_deployments=2, live_trading=False, max_custom_strategies=3,
        max_members=1, max_alert_channels=1,
        description="Paper trading only. Try strategies on live market data with one account.",
        max_backtests_per_month=50, max_accounts=1, max_brokers=1, support_level="community",
    ),
    "pro": Plan(
        id="pro", name="Pro", max_active_deployments=10, live_trading=True, max_custom_strategies=25,
        max_members=5, max_alert_channels=5,
        description="Live trading for a small desk: up to 10 concurrent deployments and 5 team members.",
        price_monthly=2999.0, price_yearly=29990.0, max_live_strategies=5, max_backtests_per_month=500,
        max_api_calls_per_day=5000, max_accounts=3, max_brokers=2, option_features=True, ai_features=True,
        marketplace_access=True, support_level="email", trial_days=14, ai_monthly_budget_inr=_budget("AI_BUDGET_INR_PRO", 1500.0),
    ),
    "business": Plan(
        id="business", name="Business", max_active_deployments=50, live_trading=True, max_custom_strategies=200,
        max_members=25, max_alert_channels=5,
        description="For prop desks and advisories: 50 concurrent deployments, 25 members, priority support.",
        price_monthly=14999.0, price_yearly=149990.0, max_live_strategies=50, max_backtests_per_month=5000,
        max_api_calls_per_day=100000, max_accounts=20, max_brokers=5, option_features=True, ai_features=True,
        marketplace_access=True, support_level="priority", trial_days=14, ai_monthly_budget_inr=_budget("AI_BUDGET_INR_BUSINESS", 10000.0),
    ),
}

DEFAULT_PLAN_ID = "free"


def get_plan(plan_id: str) -> Plan:
    return PLANS.get((plan_id or "").lower(), PLANS[DEFAULT_PLAN_ID])
