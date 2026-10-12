"""CH5-a review (ADR-0004): broker calls made by the API process for charts (`/api/market-data/ltp`) draw from a
per-session RateBudget, so a page that polls - a multi-chart workspace in an open tab - can never spend the broker
allowance the trading worker's exits need.

The worker keeps its own budget per (tenant, broker@account) in its own process (`TradingWorker._budget_for`), at the
conservative limits of `rate_budget.limits_for` (about a third of each broker's published allowance). The API process
cannot share that object, so it gets its own, at API_SHARE of those limits: the two together stay well inside the
broker's allowance. A request that cannot get a token within TOKEN_WAIT_SECONDS is answered 429 (the chart keeps its
last price) instead of queueing. Tokens are taken with `acquire_spare`, which never holds the budget's lock while
waiting. Nothing here places an order.
"""
from typing import Dict, Tuple

from app.brokers.rate_budget import RateBudget, RateLimits, limits_for

API_SHARE = 0.5                   # of the conservative per-broker limits (the worker's own budget is the other part)
TOKEN_WAIT_SECONDS = 2.0

_budgets: Dict[Tuple[int, str], RateBudget] = {}


def budget_for(tenant_id: int, broker: str, account_label: str = "primary") -> RateBudget:
    """The API process's one budget for a broker session (tenant + broker + account), created on first use."""
    key = (int(tenant_id), f"{broker}@{account_label or 'primary'}")
    budget = _budgets.get(key)
    if budget is None:
        base = limits_for(broker)
        budget = _budgets[key] = RateBudget(RateLimits(per_second=max(0.5, base.per_second * API_SHARE), burst=max(1, int(base.burst * API_SHARE)),
                                                       per_minute=max(1, int(base.per_minute * API_SHARE))))
    return budget


async def take(budget: RateBudget, wait_seconds: float = TOKEN_WAIT_SECONDS) -> bool:
    """One token, or False when none comes within `wait_seconds` (the caller answers 429 rather than queue)."""
    return await budget.acquire_spare(0.0, deadline=budget.now() + max(0.0, wait_seconds))


def reset() -> None:
    """Tests: forget every budget."""
    _budgets.clear()
