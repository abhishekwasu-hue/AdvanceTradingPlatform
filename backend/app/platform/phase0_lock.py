"""Z10-c (Phase 0 exit criterion "no API order path reachable"): the PAPER-only lock, feature flag `phase0_paper_only`.

OFF by default, like every flag. The one exception is the explicit Phase 0 deployment mode (`ATP_DEPLOYMENT_PHASE=phase0`,
set by the Z11 bundle for the Hostinger host): it seeds this flag ON (and `live_trading` OFF) where no entry is stored yet,
by the owner's decision of 2026-10-12 - the restrictive direction only (app/platform/deployment_phase.py,
docs/OPERATIONS.md 1.6aj, OPEN_QUESTIONS Z10-3). No other code, migration or script turns it on.
The tenant allow-list works as for every flag: OFF with tenants listed locks those tenants only.

While it is on, no new LIVE entry is sent anywhere. Two layers:
1. `platform.controls.entry_blocks` refuses every new LIVE entry (single-leg and multi-leg pipelines alike) before any
   sizing or routing, with a reason on the order trail;
2. the broker-order chokepoint (`brokers.rate_budget.RateLimitedBroker.place_order`, the wrapper every worker LIVE
   order goes through) refuses an order whose stated intent is ENTRY before it reaches the broker - a REJECTED
   response the router records as a refusal.
Exits of an existing LIVE position, protective stops, modifies and cancels always pass (ADR-0004). PAPER is untouched.
On top of the two layers, the trading worker does not evaluate a LIVE deployment while the lock holds for its
organisation (`WORKER_NOT_EVALUATED`), so no LIVE entry is even built.
The lock never changes the `live_trading` flag or a plan; it only adds a refusal.

The chokepoint has no database session, so it reads the state the last flag read in this process left behind
(`remember`). Every entry passes layer 1 first, which reads the flag fresh and refreshes that state, so layer 2 sees
the value read moments before the order was built.
"""
from typing import Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.models import ORDER_INTENT_ENTRY, BrokerOrderRequest, BrokerOrderResponse

FLAG = "phase0_paper_only"
REFUSAL = "phase0_paper_only"
ENTRY_REASON = "Phase 0 PAPER-only lock (phase0_paper_only) is on: new LIVE entries are refused - exits still run"
# What a LIVE deployment says while the lock is on: the trading worker does not evaluate it (no LIVE entry path is started).
WORKER_NOT_EVALUATED = ENTRY_REASON + " (LIVE deployment not evaluated)"

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
    """An order the platform built as a new entry: `intent == "ENTRY"`, set where entries are built (OrderRouter,
    the multi-leg executor). Nothing is inferred from the tag (Z10-c review M1: a long algo id could make an exit's
    tag look like an entry's). Exits, stops and any order with no stated intent always pass - an exit must never be
    refused here."""
    return order.intent == ORDER_INTENT_ENTRY


def refused_response() -> BrokerOrderResponse:
    return BrokerOrderResponse(order_id="", status="REJECTED", message=f"{REFUSAL}: {ENTRY_REASON} - order not sent")


class LiveOrderRefused(RuntimeError):
    """FNO-1: an order path asked for a LIVE order while the lock holds. Raised before anything is built or sent."""


def assert_paper(mode: str, tenant_id: Optional[int]) -> str:
    """FNO-1 (owner order "every order is PAPER under Phase 0"): the assertion an order path that picks its own mode
    makes at its chokepoint - the manual order ticket (app/paper/fno_ticket.py) calls it right before the order pipeline.
    While the lock holds for the organisation, only PAPER passes; a LIVE request raises `LiveOrderRefused` and nothing is
    placed. Reads the state the last flag read left (`is_on` refreshes it). Entries only: no exit path calls this, and
    exits of an existing position are never refused (ADR-0004). Returns the mode it allowed."""
    value = str(mode or "").upper()
    if value != "PAPER" and locked(tenant_id):
        raise LiveOrderRefused(f"{REFUSAL}: {ENTRY_REASON} - nothing was placed")
    return value
