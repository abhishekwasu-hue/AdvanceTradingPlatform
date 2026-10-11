"""Z10-c (Phase 0 exit criterion "no API order path reachable"): the PAPER-only lock, feature flag `phase0_paper_only`.

OFF by default, like every flag, and nothing in code, migrations, scripts or deploy tooling turns it on: the owner turns
it on in *Admin Console -> Feature flags* when installing the beta host (docs/OPERATIONS.md 1.6aj, OPEN_QUESTIONS Z10-3).
The tenant allow-list works as for every flag: OFF with tenants listed locks those tenants only.

While it is on, no new LIVE entry is sent anywhere. Two layers:
1. `platform.controls.entry_blocks` refuses every new LIVE entry (single-leg and multi-leg pipelines alike) before any
   sizing or routing, with a reason on the order trail;
2. the broker-order chokepoint (`brokers.rate_budget.RateLimitedBroker.place_order`, the wrapper every worker LIVE
   order goes through) refuses an order tagged as an entry before it reaches the broker - a REJECTED response the
   router records as a refusal.
Exits of an existing LIVE position, protective stops, modifies and cancels always pass (ADR-0004). PAPER is untouched.
The lock never changes the `live_trading` flag or a plan; it only adds a refusal.

The chokepoint has no database session, so it reads the state the last flag read in this process left behind
(`remember`). Every entry passes layer 1 first, which reads the flag fresh and refreshes that state, so layer 2 sees
the value read moments before the order was built.
"""
from typing import Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.models import BrokerOrderRequest, BrokerOrderResponse
from app.execution.tagging import LEG_ENTRY

FLAG = "phase0_paper_only"
REFUSAL = "phase0_paper_only"
ENTRY_REASON = "Phase 0 PAPER-only lock (phase0_paper_only) is on: new LIVE entries are refused - exits still run"

# Last read of the flag in this process: the global switch and the tenant allow-list.
_state: Dict[str, object] = {"on": False, "tenants": frozenset()}


def remember(on: bool, tenants) -> None:
    _state["on"] = bool(on)
    _state["tenants"] = frozenset(int(t) for t in tenants or ())


def reset_cache() -> None:
    remember(False, ())


async def is_on(session: AsyncSession, tenant_id: Optional[int]) -> bool:
    """Reads the flag fresh (and refreshes the process state the broker chokepoint reads)."""
    from app.platform.controls import feature_flags
    entry = (await feature_flags(session)).get(FLAG) or {}
    remember(bool(entry.get("on")), entry.get("tenants") or ())
    return locked(tenant_id)


def locked(tenant_id: Optional[int]) -> bool:
    """From the last read: on for everyone, or for this tenant through the allow-list. With no tenant known the lock
    holds when it is on for anyone (refusing an entry is always safe; exits never come here)."""
    if _state["on"]:
        return True
    tenants = _state["tenants"]
    assert isinstance(tenants, frozenset)
    if tenant_id is None:
        return bool(tenants)
    return int(tenant_id) in tenants


def is_entry_order(order: BrokerOrderRequest) -> bool:
    """An order the platform built as a new entry. Every entry carries the entry leg last in its tag
    (`execution.tagging.build_order_tag`: `<algo>-<strategy>-ENT`, or without separators for an alphanumeric-only
    broker). Exits (`EXIT`), stops (`SL`) and untagged orders are never treated as entries: an exit must never be
    refused here."""
    tag = (order.tag or "").upper()
    return tag.endswith(LEG_ENTRY)


def refused_response() -> BrokerOrderResponse:
    return BrokerOrderResponse(order_id="", status="REJECTED", message=f"{REFUSAL}: {ENTRY_REASON} - order not sent")
