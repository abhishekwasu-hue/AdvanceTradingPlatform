"""Z10-c (Phase 0 exit criterion "no API order path reachable"): the PAPER-only lock.

LIVE-1 (owner order, binding: ONE live switch): the lock is no longer a switch of its own. It is DERIVED from the
`live_trading` flag - `controls.feature_flags` answers `phase0_paper_only` = NOT `live_trading` for everyone (a stored
`phase0_paper_only` entry is ignored, and Admin -> Controls refuses to set it). So: `live_trading` OFF (the production
default) = locked, no new LIVE entry is sent anywhere; `live_trading` ON = unlocked, each user picks PAPER or LIVE.
The shared decision is app/platform/live_switch.py (`live_allowed`); this module keeps the layers that enforce it.
Before LIVE-1 the lock was the flag `phase0_paper_only` (M55 S1: ON by default, the owner alone could turn it off).

B1 (owner decision 14 Nov, LIVE-1 amended): LIVE orders through ATP's server are for the owner's organisation only - the
organisation of the owner account (`LIVE_OWNER_USER_ID`, resolved as `live_owner.is_owner` does). So the lock holds for an
organisation when `live_trading` is OFF (for everyone, as before) OR the organisation is not the owner's (also while the
switch is ON). Other organisations trade PAPER here and use "Trade in your broker app". The switch stays ONE switch.
No organisation known, or no owner account resolved, = locked (refusing an entry is always safe). Exits never come here.

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

FLAG = "phase0_paper_only"
REFUSAL = "phase0_paper_only"
# LIVE-1: one plain sentence (the switch, not a flag name) - the same words as live_switch.LIVE_OFF.
ENTRY_REASON = "Live trading is switched off by the admin: new LIVE entries are refused - exits still run"
# B1 (owner 14 Nov): the plain sentence when the switch is ON but the organisation is not the owner's.
NOT_OWNER_REASON = "Live orders from ATP are for the platform owner only. Use Trade in your broker app."
# What a LIVE deployment says while the lock is on: the trading worker does not evaluate it (no LIVE entry path is started).
WORKER_NOT_EVALUATED = ENTRY_REASON + " (LIVE deployment not evaluated)"
WORKER_NOT_OWNER = NOT_OWNER_REASON + " (LIVE deployment not evaluated)"

# Last read of the flag in this process: the global switch, the tenant allow-list and (B1) the owner's organisation.
_state: Dict[str, object] = {"on": False, "tenants": frozenset(), "owner_tenant": None}


def remember(on: bool, tenants) -> None:
    _state["on"] = bool(on)
    _state["tenants"] = frozenset(int(t) for t in tenants or ())


def remember_owner(tenant_id: int | None) -> None:
    """B1: the owner's organisation from the last read (None = not resolved: every organisation locked)."""
    _state["owner_tenant"] = int(tenant_id) if tenant_id is not None else None


def reset_cache() -> None:
    remember(False, ())
    remember_owner(None)


async def owner_tenant_id(session: AsyncSession) -> int | None:
    """B1: the organisation of the owner account (`live_owner.owner_user_id`), only while that account is the owner as
    `live_owner.is_owner` defines it (active platform administrator). None otherwise."""
    from app.db.models import User
    from app.platform import live_owner
    user = await session.get(User, live_owner.owner_user_id())
    if not live_owner.is_owner(user) or user is None or user.tenant_id is None:
        return None
    return int(user.tenant_id)


async def is_on(session: AsyncSession, tenant_id: Optional[int]) -> bool:
    """Reads the flag and the owner's organisation fresh (and refreshes the process state the broker chokepoint reads)."""
    from app.platform.controls import feature_flags
    entry = (await feature_flags(session)).get(FLAG) or {}
    remember(bool(entry.get("on")), entry.get("tenants") or ())
    remember_owner(await owner_tenant_id(session))
    return locked(tenant_id)


def is_owner_org(tenant_id: int | None) -> bool:
    """B1: True when `tenant_id` is the owner's organisation, from the last read (`is_on`). None / unresolved = False."""
    owner = _state["owner_tenant"]
    return tenant_id is not None and owner is not None and int(tenant_id) == owner


def switch_locked() -> bool:
    """From the last read: the global lock alone (`live_trading` OFF) - for logs and the platform status, not for an
    organisation's entry (that is `locked`)."""
    return bool(_state["on"])


def locked(tenant_id: Optional[int]) -> bool:
    """From the last read: on for everyone (`live_trading` OFF), or (B1) for every organisation that is not the owner's,
    or for this tenant through the allow-list. With no tenant known, or no owner resolved, the lock holds (refusing an
    entry is always safe; exits never come here)."""
    if _state["on"]:
        return True
    if tenant_id is None or not is_owner_org(tenant_id):
        return True
    tenants = _state["tenants"]
    assert isinstance(tenants, frozenset)
    return int(tenant_id) in tenants


def reason(tenant_id: int | None) -> str:
    """The plain sentence for a refused LIVE entry, from the last read: the switch is off (`ENTRY_REASON`), else the
    organisation is not the owner's (`NOT_OWNER_REASON`)."""
    return ENTRY_REASON if _state["on"] else NOT_OWNER_REASON


def is_entry_order(order: BrokerOrderRequest) -> bool:
    """An order the platform built as a new entry: `intent == "ENTRY"`, set where entries are built (OrderRouter,
    the multi-leg executor). Nothing is inferred from the tag (Z10-c review M1: a long algo id could make an exit's
    tag look like an entry's). Exits, stops and any order with no stated intent always pass - an exit must never be
    refused here."""
    return order.intent == ORDER_INTENT_ENTRY


def refused_response(tenant_id: Optional[int] = None) -> BrokerOrderResponse:
    return BrokerOrderResponse(order_id="", status="REJECTED", message=f"{REFUSAL}: {reason(tenant_id)} - order not sent")


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
        raise LiveOrderRefused(f"{REFUSAL}: {reason(tenant_id)} - nothing was placed")
    return value
