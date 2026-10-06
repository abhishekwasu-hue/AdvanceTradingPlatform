"""Phase T: broker-selection rules (master prompt V3.1-3.5).

Phase I2 gave a tenant several broker accounts and let a deployment name one. That is
*explicit* routing: the account is chosen when the deployment is created. V3 asks for rules -
"capital/risk-based routing across brokers" - so the account is chosen at signal time from
what the accounts look like right now:

* **EXPLICIT** (default, unchanged): the deployment's `broker_account_id`, else the broker's
  default account.
* **MOST_MARGIN**: the ACTIVE candidate with the largest synced `available_balance`.
* **LEAST_UTILISED**: the ACTIVE candidate with the smallest margin utilisation
  (`used_margin / (used_margin + available_balance)`), so capital is spread evenly.
* **FEWEST_POSITIONS**: the ACTIVE candidate carrying the fewest open LIVE positions, so no
  single account concentrates the day's risk.

Candidates are the tenant's ACTIVE accounts at the deployment's broker, or at *every* broker
the tenant has a usable session for when the deployment says `route_across_brokers`. A
capital-based policy only trusts a balance synced within `ROUTING_MAX_SYNC_AGE_SECONDS`; when
no candidate has a fresh balance the choice falls back to the default account and the note says
so - a stale number never decides where real money goes. The worker refreshes every account's
balance from its broker session once per `ACCOUNT_REFRESH_SECONDS` so the numbers are fresh
without a REST call per cycle.

The decision is recorded on the deployment (`last_route`) and appears on the Autopilot card:
"account #7 (upstox/second) by MOST_MARGIN: 2,10,000 available". Everything after the choice -
the adapter, the LIVE gates, the risk hierarchy's ACCOUNT scope - is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Iterable, List, Optional

from app.db.models import BrokerAccountRecord

ROUTING_MAX_SYNC_AGE_SECONDS = 15 * 60     # a balance older than this cannot decide a capital policy
ACCOUNT_REFRESH_SECONDS = 5 * 60           # the worker's per-account balance refresh interval


class RoutingPolicy(str, Enum):
    EXPLICIT = "EXPLICIT"
    MOST_MARGIN = "MOST_MARGIN"
    LEAST_UTILISED = "LEAST_UTILISED"
    FEWEST_POSITIONS = "FEWEST_POSITIONS"


CAPITAL_POLICIES = frozenset({RoutingPolicy.MOST_MARGIN, RoutingPolicy.LEAST_UTILISED})


@dataclass(frozen=True)
class RouteChoice:
    account: Optional[BrokerAccountRecord]
    note: str
    policy: RoutingPolicy
    fell_back: bool = False

    @property
    def label(self) -> str:
        return self.account.account_label if self.account is not None else "primary"


def _aware(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def balance_is_fresh(account: BrokerAccountRecord, now: datetime, max_age: int = ROUTING_MAX_SYNC_AGE_SECONDS) -> bool:
    synced = _aware(account.last_sync_at)
    if synced is None or account.available_balance is None:
        return False
    return (now - synced).total_seconds() <= max_age


def utilisation(account: BrokerAccountRecord) -> float:
    used = float(account.used_margin or 0.0)
    available = float(account.available_balance or 0.0)
    total = used + available
    return used / total if total > 0 else 1.0


def _describe(account: BrokerAccountRecord) -> str:
    return f"account #{account.id} ({account.broker_name}/{account.account_label})"


def choose_account(
    candidates: Iterable[BrokerAccountRecord], policy: RoutingPolicy, *, now: datetime,
    open_positions: Optional[Dict[int, int]] = None, explicit: Optional[BrokerAccountRecord] = None,
    default: Optional[BrokerAccountRecord] = None, max_sync_age: int = ROUTING_MAX_SYNC_AGE_SECONDS,
) -> RouteChoice:
    """Pure selection. `candidates` are the accounts the policy may pick from (the caller has
    already restricted them to the right broker(s)); DISABLED ones are ignored here too.
    `explicit` is the deployment's own account, `default` the broker's default; either is the
    fallback when the policy cannot decide."""
    active: List[BrokerAccountRecord] = [a for a in candidates if a.status == "ACTIVE"]
    fallback = explicit or default or (active[0] if active else None)

    if policy == RoutingPolicy.EXPLICIT or not active:
        if fallback is None:
            return RouteChoice(None, "no broker account: primary credential", policy)
        return RouteChoice(fallback, f"{_describe(fallback)} by {policy.value}", policy)

    if len(active) == 1:
        only = active[0]
        return RouteChoice(only, f"{_describe(only)} by {policy.value}: only active candidate", policy)

    if policy in CAPITAL_POLICIES:
        fresh = [a for a in active if balance_is_fresh(a, now, max_sync_age)]
        if not fresh:
            target = fallback if fallback is not None else active[0]
            return RouteChoice(target, f"{_describe(target)}: {policy.value} needs a balance synced within "
                                       f"{max_sync_age // 60} min, none is - default account used", policy, fell_back=True)
        if policy == RoutingPolicy.MOST_MARGIN:
            best = max(fresh, key=lambda a: (float(a.available_balance or 0.0), -a.id))
            return RouteChoice(best, f"{_describe(best)} by MOST_MARGIN: {float(best.available_balance or 0):,.0f} available", policy)
        best = min(fresh, key=lambda a: (utilisation(a), a.id))
        return RouteChoice(best, f"{_describe(best)} by LEAST_UTILISED: {utilisation(best) * 100:.0f}% margin used", policy)

    # FEWEST_POSITIONS: ties broken by the freshest larger balance, then id.
    counts = open_positions or {}
    best = min(active, key=lambda a: (counts.get(a.id, 0), -float(a.available_balance or 0.0), a.id))
    return RouteChoice(best, f"{_describe(best)} by FEWEST_POSITIONS: {counts.get(best.id, 0)} open", policy)


def policy_for(deployment_policy: Optional[str], tenant_default: Optional[str]) -> RoutingPolicy:
    """The deployment's own policy, else the tenant's default, else EXPLICIT."""
    for raw in (deployment_policy, tenant_default):
        if raw:
            try:
                return RoutingPolicy(raw)
            except ValueError:
                continue
    return RoutingPolicy.EXPLICIT
