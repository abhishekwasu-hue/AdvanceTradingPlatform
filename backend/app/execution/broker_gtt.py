"""M75 W-10 (owner order M75 CW-6; DECISIONS 23 Oct TM-23 and 1 Nov M75Q-1 default confirmed): the broker GTT path of
trigger plans - flag `broker_gtt` (OFF), LIVE only, tested on a fake broker.

TM-23 stays the rule: ATP's own trigger engine by default (the kill switch, the risk checks, the PAPER lock and "ask me
first" all run at fire time). A plan waits at the broker as a GTT ("good till triggered") only when ALL of these hold
(`route_reasons` names each one that does not, in plain words):

* the flag `broker_gtt` is on for his organisation;
* his own Trade settings > Automation > "Simple price plans" is "Use my broker's GTT" (Not set = ATP's engine);
* the plan is a SIMPLE price plan: a touch of his level, no time window (a broker GTT has neither a candle close nor a
  window), "place at once" - a plan that asks him first never goes to a GTT, and neither does any plan while his
  organisation's approval-only mode (or his own "Ask me first" for new entries) is on;
* the broker can watch what the plan watches: a GTT triggers on the ORDERED contract's own price, so the plan must watch
  that contract (M75Q-8; a plan on the underlying stays with ATP's engine);
* a filled GTT can be recorded as his position with the plan's exits (`FILL_RECORDING`, M75Q-9): that needs the LIVE
  manual path (TT-1), which is not built - until then no GTT is created, so a broker fill can never be a position
  without ATP's exits;
* his own broker session offers GTT (`BrokerInterface.supports_gtt`).

Creating a GTT is a new LIVE entry, so it meets the order chokepoints first: the Phase 0 PAPER-only lock refuses it
(`phase0_lock.is_on`, and again in `RateLimitedBroker.place_gtt`) and any engaged kill switch refuses it. PAPER plans
never come here (PAPER = ATP's engine with the same plan states). Each GTT carries the plan order's own key
`plan-<id>-<n>`; its id is kept on the plan's results (no new column).

Kill switch: ANY kill switch that engages cancels the GTTs in its scope FIRST (`cancel_all`, called by
`kill_switch.checks.engage` before the switch is stored) - a cancel always passes every chokepoint. Pausing, editing,
cancelling or expiring a plan cancels its GTTs too; the plan then waits in ATP's engine again (or ends).
Reconciliation (`sync`, worker): a GTT the broker TRIGGERED moves the plan ARMED -> TRIGGERED once (the same
conditional move as ATP's engine) and records the broker's order (by the GTT's order id, from the broker's own book);
a GTT the broker cancelled, rejected or expired returns the plan to ATP's engine, saying so. Exits are never blocked:
nothing here touches an exit. The AI never reaches this module (ADR-0006).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TriggerPlanRecord, User

logger = logging.getLogger(__name__)

FLAG = "broker_gtt"
BROKER, ATP = "BROKER", "ATP"
ACTIVE, TRIGGERED, CANCELLED, REJECTED, EXPIRED = "ACTIVE", "TRIGGERED", "CANCELLED", "REJECTED", "EXPIRED"
ENDED = (CANCELLED, REJECTED, EXPIRED)
FNO_EXCHANGES = ("NFO", "BFO", "MCX")
# M75Q-9: a filled GTT becomes his position with the plan's exits only through the LIVE manual path (TT-1, not built).
FILL_RECORDING = False
CANCEL_TIMEOUT_SECONDS = 10.0          # operational: a kill switch never waits longer than this for a broker's answer

AdapterFn = Callable[[AsyncSession, User], Awaitable[Any]]


def _results(row: TriggerPlanRecord) -> dict[str, dict[str, Any]]:
    return json.loads(row.results_json or "{}")


def gtts(row: TriggerPlanRecord) -> dict[int, dict[str, Any]]:
    """The plan's GTTs by order number (id, status, broker) - empty when the plan waits in ATP's engine."""
    return {int(n): r["gtt"] for n, r in _results(row).items() if isinstance(r, dict) and r.get("gtt")}


def active(row: TriggerPlanRecord) -> dict[int, dict[str, Any]]:
    return {n: g for n, g in gtts(row).items() if g.get("status") == ACTIVE and g.get("id")}


def settled(row: TriggerPlanRecord) -> bool:
    """Every order of the plan has its broker answer recorded (only then may the plan end DONE)."""
    from app.execution import trigger_plans
    results = _results(row)
    return all((results.get(str(n)) or {}).get("settled") for n in range(len(trigger_plans.orders_of(row))))


def _store(row: TriggerPlanRecord, n: int, gtt: dict[str, Any] | None, now: datetime, **extra: Any) -> None:
    results = _results(row)
    r = dict(results.get(str(n)) or {})
    if gtt is None:
        r.pop("gtt", None)
    else:
        r["gtt"] = gtt
    r.update(extra)
    results[str(n)] = r
    row.results_json = json.dumps(results)
    row.updated_at = now


async def enabled(session: AsyncSession, tenant_id: int) -> bool:
    from app.platform.controls import flag_enabled
    return bool(await flag_enabled(session, FLAG, tenant_id))


async def his_choice(session: AsyncSession, user: User) -> str | None:
    """His Trade settings > Automation > "Simple price plans" (None = Not set = ATP's engine)."""
    from app.execution import automation
    return (await automation.load(session, user)).plan_broker_gtt


async def route_reasons(session: AsyncSession, user: User, row: TriggerPlanRecord) -> list[str]:
    """Why this plan waits in ATP's engine and not at his broker (empty = it may go to his broker's GTT)."""
    from app.execution import approval, trigger_plans
    from app.trading.trigger_rules import TOUCH
    reasons: list[str] = []
    if not await enabled(session, row.tenant_id):
        reasons.append("Broker GTT is not switched on for your account.")
    if await his_choice(session, user) != BROKER:
        reasons.append("Your Trade settings keep simple price plans in ATP's trigger engine.")
    c = trigger_plans.condition_of(row)
    if c.kind != trigger_plans.PRICE or c.mode != TOUCH or c.window_start or c.window_end:
        reasons.append("Only a touch of a price level with no time window can wait at the broker.")
    if row.approval != trigger_plans.AUTO or await approval.required(session, row.tenant_id, None, row.user_id):
        reasons.append("A plan that asks you first always stays with ATP.")
    if row.watch_exchange not in FNO_EXCHANGES:
        reasons.append(f"Your broker's GTT watches the ordered contract's own price; this plan watches {row.watch_symbol}.")
    if not FILL_RECORDING:
        reasons.append("A filled broker GTT cannot be recorded as your position with its exits yet.")
    return reasons


async def own_adapter(session: AsyncSession, user: User) -> Any:
    """His own broker session that offers GTT, behind the order chokepoint wrapper (the Phase 0 lock, the rate budget);
    None when he has none."""
    from app.brokers.rate_budget import RateBudget, RateLimitedBroker, limits_for
    from app.brokers.token_lifecycle import build_adapter, token_is_usable
    from app.db.models import BrokerCredentialRecord
    for record in await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == user.tenant_id,
                                                                             BrokerCredentialRecord.user_id == user.id)):
        if not token_is_usable(record):
            continue
        try:
            adapter = build_adapter(record)
        except Exception:  # noqa: BLE001 - another session may serve
            continue
        if getattr(adapter, "supports_gtt", False):
            return RateLimitedBroker(adapter, RateBudget(limits_for(adapter.name)), tenant_id=user.tenant_id)
    return None


def _request(row: TriggerPlanRecord, o: Any, n: int, contract: Any, last_price: float) -> Any:
    from app.brokers.models import ORDER_INTENT_ENTRY, BrokerOrderRequest, GttRequest
    from app.core.enums import OrderSide
    from app.execution import trigger_plans
    c = trigger_plans.condition_of(row)
    order = BrokerOrderRequest(symbol=contract.tradingsymbol, exchange=contract.exchange,
                               transaction_type=OrderSide.BUY if o.side == "BUY" else OrderSide.SELL,
                               quantity=float(o.lots * contract.lot_size), order_type=o.order_type, product=o.product,
                               price=o.price if o.order_type == "LIMIT" else None, intent=ORDER_INTENT_ENTRY,
                               client_ref=f"plan-{row.id}-{n}")
    return GttRequest(symbol=row.watch_symbol, exchange=row.watch_exchange, trigger_price=float(c.level), last_price=last_price,
                      order=order, client_ref=f"plan-{row.id}-{n}")


async def arm(session: AsyncSession, row: TriggerPlanRecord, user: User, adapter: Any, last_price: float, now: datetime) -> bool:
    """Creates the plan's GTT(s) at his broker (once: an order that already has a GTT is skipped). True when every order
    of the plan now waits at the broker; False leaves the plan in ATP's engine (the reason is on the plan)."""
    from app.execution import trigger_plans
    from app.kill_switch.checks import active_kill_switch_reasons
    from app.paper import fno_ticket
    from app.platform import phase0_lock
    if await phase0_lock.is_on(session, row.tenant_id):
        row.note = "Live orders are switched off for your account, so the plan waits in ATP's trigger engine."
        return False
    stopped = await active_kill_switch_reasons(session, row.tenant_id, trigger_plans.STRATEGY_ID)
    if stopped:
        row.note = "A kill switch is on, so no broker GTT was created."
        return False
    current = gtts(row)
    for n, o in enumerate(trigger_plans.orders_of(row)):
        if n in current and current[n].get("status") in (ACTIVE, TRIGGERED):
            continue
        if n in current and current[n].get("status") == REJECTED:
            return False                                        # his broker refused it once: ATP's engine watches
        try:
            contract = await fno_ticket.find_contract(session, row.underlying, datetime.fromisoformat(o.expiry).date(), o.strike, o.right)
            status = await adapter.place_gtt(_request(row, o, n, contract, last_price))
        except Exception as exc:  # noqa: BLE001 - not created: ATP's engine keeps watching
            _store(row, n, {"id": None, "status": REJECTED, "broker": getattr(adapter, "name", ""), "message": str(exc)[:200]}, now)
            row.note = "Your broker did not take the GTT, so the plan waits in ATP's trigger engine."
            return False
        state = (status.status or "").upper()
        _store(row, n, {"id": status.gtt_id or None, "status": ACTIVE if state == ACTIVE and status.gtt_id else REJECTED,
                        "broker": getattr(adapter, "name", ""), "message": (status.message or "")[:200] or None}, now)
        if state != ACTIVE or not status.gtt_id:
            row.note = "Your broker did not take the GTT, so the plan waits in ATP's trigger engine."
            await cancel(session, row, adapter, now, note=None)       # never half at the broker, half in ATP
            return False
    row.note = "Waiting at your broker as a GTT."
    return True


async def cancel(session: AsyncSession, row: TriggerPlanRecord, adapter: Any, now: datetime, *, note: str | None = None) -> int:
    """Cancels every ACTIVE GTT of the plan at his broker (a cancel always passes). Returns how many were cancelled."""
    done = 0
    for n, g in active(row).items():
        try:
            status = await asyncio.wait_for(adapter.cancel_gtt(str(g["id"])), timeout=CANCEL_TIMEOUT_SECONDS)
            state = (status.status or "").upper()
        except Exception as exc:  # noqa: BLE001 - kept ACTIVE: the next pass / switch tries again
            logger.warning("Broker GTT: cancel of plan %s order %s failed (%s)", row.id, n, type(exc).__name__)
            continue
        if state in (CANCELLED, EXPIRED, REJECTED):
            _store(row, n, {**g, "status": CANCELLED}, now)
            done += 1
        elif state == TRIGGERED:
            _store(row, n, {**g, "status": TRIGGERED, "order_id": status.order_id}, now)
    if note:
        row.note = note
    return done


async def sync(session: AsyncSession, row: TriggerPlanRecord, adapter: Any, now: datetime) -> str:
    """Reads the plan's GTTs at the broker. "TRIGGERED": the broker sent an order (the plan moved ARMED -> TRIGGERED once
    and the broker's order is recorded); "ENDED": the broker ended a GTT (the plan waits in ATP's engine again);
    "WAITING": still at the broker; "UNKNOWN": no answer this pass."""
    from app.execution import trigger_plans
    seen: dict[int, Any] = {}
    for n, g in active(row).items():
        try:
            seen[n] = await adapter.get_gtt(str(g["id"]))
        except Exception:  # noqa: BLE001 - no answer: no decision this pass
            return "UNKNOWN"
    fired = {n: s for n, s in seen.items() if (s.status or "").upper() == TRIGGERED}
    ended = {n: s for n, s in seen.items() if (s.status or "").upper() in ENDED}
    if fired:
        if row.status == trigger_plans.ARMED:
            if not await trigger_plans._move(session, row, (trigger_plans.ARMED,), trigger_plans.TRIGGERED, now,  # noqa: SLF001
                                             triggered_at=now, trigger_price=row.last_price,
                                             note="Your broker's GTT triggered."):
                return "UNKNOWN"
        for n, s in fired.items():
            g = gtts(row)[n]
            book = await _broker_order(adapter, s.order_id)
            _store(row, n, {**g, "status": TRIGGERED, "order_id": s.order_id}, now, status="SENT_BY_BROKER",
                   broker_order_id=s.order_id, broker_status=book.get("status"), filled_quantity=book.get("filled_quantity"),
                   average_price=book.get("average_price"), settled=True)
        await session.commit()
        return TRIGGERED
    if ended:
        for n, s in ended.items():
            _store(row, n, None, now)
        await cancel(session, row, adapter, now)                  # the rest too: the plan is one unit
        row.note = "Your broker ended the GTT; the plan waits in ATP's trigger engine again."
        await session.commit()
        return "ENDED"
    return "WAITING"


async def _broker_order(adapter: Any, order_id: str | None) -> dict[str, Any]:
    """The broker's own record of the order the GTT sent (status, filled quantity, average price); {} when not read."""
    if not order_id:
        return {}
    try:
        for o in await adapter.get_order_book():
            if o.order_id == order_id:
                return {"status": o.status, "filled_quantity": o.filled_quantity, "average_price": o.average_price}
    except Exception:  # noqa: BLE001 - recorded without the book; the next read fills it
        return {}
    return {}


async def handle(session: AsyncSession, row: TriggerPlanRecord, prices: Any, now: datetime,
                 adapter_for: AdapterFn | None = None) -> bool:
    """The worker's step for one ARMED plan before ATP's engine reads it. True: the broker handles it this pass (waiting
    or just triggered - ATP's engine does not read it); False: ATP's engine reads it as usual."""
    from app.execution import trigger_plans
    user = await session.get(User, row.user_id)
    if user is None:
        return False
    waiting = active(row)
    triggered = row.status == trigger_plans.TRIGGERED
    if not waiting and not triggered and not await enabled(session, row.tenant_id):
        return False                                             # the flag is off: ATP's engine, nothing else read
    reasons = [] if triggered else await route_reasons(session, user, row)
    if not waiting and reasons:
        return False
    adapter = await (adapter_for or own_adapter)(session, user)
    if adapter is None:
        return bool(waiting) or triggered                         # his session is gone: the GTT still waits at his broker
    if waiting:
        if reasons:                                              # no longer his choice / the flag went off: back to ATP
            await cancel(session, row, adapter, now, note="The plan moved back to ATP's trigger engine.")
            await session.commit()
            return bool(active(row))
        state = await sync(session, row, adapter, now)
        if row.status == trigger_plans.TRIGGERED and settled(row):
            await trigger_plans.fire(session, row, prices, now)  # every order settled by the broker: the plan ends DONE
        return state in (TRIGGERED, "WAITING", "UNKNOWN") or row.status == trigger_plans.TRIGGERED
    if triggered:
        if settled(row):
            await trigger_plans.fire(session, row, prices, now)
        return True                                              # a broker plan is never fired by ATP's engine
    try:
        last = float(await prices(row.watch_symbol, row.watch_exchange))
    except Exception:  # noqa: BLE001 - no price: not created this pass
        return False
    ok = await arm(session, row, user, adapter, last, now)
    await session.commit()
    return ok


async def on_plan_change(session: AsyncSession, row: TriggerPlanRecord, now: datetime, adapter_for: AdapterFn | None = None) -> None:
    """A plan he paused, edited or cancelled (or that expired): its GTTs are cancelled at his broker."""
    if not active(row):
        return
    user = await session.get(User, row.user_id)
    adapter = None if user is None else await (adapter_for or own_adapter)(session, user)
    if adapter is not None:
        await cancel(session, row, adapter, now)


async def cancel_all(session: AsyncSession, tenant_id: int | None, strategy_id: str | None = None,
                     adapter_for: AdapterFn | None = None, now: datetime | None = None) -> int:
    """A kill switch engages: every ACTIVE GTT in its scope is cancelled FIRST (GLOBAL: every organisation; a STRATEGY
    switch only when it is the trigger plans' own). Never raises; returns how many were cancelled."""
    from datetime import UTC

    from app.execution import trigger_plans
    if strategy_id is not None and strategy_id != trigger_plans.STRATEGY_ID:
        return 0
    now = now or datetime.now(UTC)
    query = select(TriggerPlanRecord).where(TriggerPlanRecord.status.in_((trigger_plans.ARMED, trigger_plans.PAUSED)),
                                            TriggerPlanRecord.results_json.like('%"gtt"%'))
    if tenant_id is not None:
        query = query.where(TriggerPlanRecord.tenant_id == tenant_id)
    done = 0
    for row in list(await session.scalars(query)):
        if not active(row):
            continue
        try:
            user = await session.get(User, row.user_id)
            adapter = None if user is None else await (adapter_for or own_adapter)(session, user)
            if adapter is not None:
                done += await cancel(session, row, adapter, now, note="A kill switch cancelled the plan's broker GTT.")
        except Exception:  # noqa: BLE001 - one plan never stops the switch
            logger.exception("Broker GTT: kill-switch cancel failed for plan %s", row.id)
    return done


__all__ = ["FLAG", "FILL_RECORDING", "route_reasons", "arm", "cancel", "sync", "handle", "on_plan_change", "cancel_all", "gtts", "active"]
