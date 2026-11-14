"""Z10-c (Phase 0 exit criterion "no API order path reachable"): the PAPER-only lock.

LIVE-1 (owner order, binding: ONE live switch): the lock is no longer a switch of its own. It is DERIVED from the
`live_trading` flag - `controls.feature_flags` answers `phase0_paper_only` = NOT `live_trading` for everyone (a stored
`phase0_paper_only` entry is ignored, and Admin -> Controls refuses to set it). So: `live_trading` OFF (the production
default) = locked, no new LIVE entry is sent anywhere; `live_trading` ON = unlocked, each user picks PAPER or LIVE.
The shared decision is app/platform/live_switch.py (`live_allowed`); this module keeps the layers that enforce it.
Before LIVE-1 the lock was the flag `phase0_paper_only` (M55 S1: ON by default, the owner alone could turn it off).

While it is on (live_trading OFF), no new LIVE entry is sent anywhere. Two layers:
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
from app.core import runtime_mode

FLAG = "phase0_paper_only"
REFUSAL = "phase0_paper_only"
# LIVE-1: one plain sentence (the switch, not a flag name) - the same words as live_switch.LIVE_OFF.
ENTRY_REASON = "Live trading is switched off by the admin: new LIVE entries are refused - exits still run"
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
    """Reads the flag fresh (and refreshes the process state the broker chokepoint reads). Runner S4c: in the user's
    runner process the lock follows ATP's signed LIVE state for his organisation (`core/runtime_mode.live_open`), never
    the runner's local store (it holds no switch of ATP's)."""
    if runtime_mode.is_runner():
        return locked(tenant_id)
    from app.platform.controls import feature_flags
    entry = (await feature_flags(session)).get(FLAG) or {}
    remember(bool(entry.get("on")), entry.get("tenants") or ())
    return locked(tenant_id)


def locked(tenant_id: Optional[int]) -> bool:
    """From the last read: on for everyone, or for this tenant through the allow-list. With no tenant known the lock
    holds when it is on for anyone (refusing an entry is always safe; exits never come here). Runner S4c: in the runner
    process, locked unless ATP's fresh signed answer opened LIVE for this organisation (`runtime_mode.live_open`)."""
    if runtime_mode.is_runner():
        return tenant_id is None or int(tenant_id) not in runtime_mode.live_open()
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
    """FNO-1 / LIVE-1: the assertion an order path that picks its own mode makes at its chokepoint - the manual order
    ticket (app/paper/fno_ticket.py) calls it right before the order pipeline. While the lock holds (live_trading OFF),
    only PAPER passes; a LIVE request raises `LiveOrderRefused` and nothing is
    placed. Reads the state the last flag read left (`is_on` refreshes it). Entries only: no exit path calls this, and
    exits of an existing position are never refused (ADR-0004). Returns the mode it allowed."""
    value = str(mode or "").upper()
    if value != "PAPER" and locked(tenant_id):
        raise LiveOrderRefused(f"{REFUSAL}: {ENTRY_REASON} - nothing was placed")
    return value
