"""SE-7 (owner, 23 Oct): an INCOMPLETE multi-leg order - what it holds, the user's answer, and his "if I do not answer" rule.

When the engine stops a structure at a leg that did not fill (app/execution/multileg_smart.py, TM-11), the filled legs are
booked as the group's positions and every leg's `group_meta` carries an `incomplete` block (JSON on the existing column,
no migration):

    state         INCOMPLETE (waiting for his answer) -> COMPLETING (he chose "Complete the rest") -> COMPLETING_SENT
                  (the worker claimed it and sends the rest) -> COMPLETED; or -> CLOSING ("Close the filled legs", by his
                  tap or by his rule) -> CLOSED; or -> KEPT (his rule says keep, or he tapped Keep them)
    deadline      the moment his own rule applies: `answer_minutes` after the failure (prefs `smart_execution`)
    on_no_answer  "close_filled" (the default) or "keep" - his own setting, as it was when the order stopped
    pending       the legs (and part-fill remainders) still to place, with what booking them needs

His choices (the routes in incomplete_routes.py, the trading worker):
* "Complete the rest" is an ENTRY: recorded by the route, sent by the trading worker on its next cycle (in Phase 0 only
  the worker reaches an entry order, docs/specs/ATP_PHASE0_FREEZE.md), through every entry refusal (kill switches, the
  Phase 0 lock, the broker-uncertain flag, plans) and the engine again (hedges first; nothing undone by itself).
* "Close the filled legs" is an EXIT (ADR-0004): sold legs first, then the hedges, through `close_group` - the path every
  exit takes (its naked-short guard holds a hedge while its short is open). No kill switch, lockout, risk check or the
  Phase 0 lock stands in its way; a leg with no price now is retried on the next cycle. PAPER legs close at once from the
  route; LIVE legs are closed by the worker (the LIVE exit path in Phase 0).
* No answer by the deadline: the worker applies his rule - close (the exit above) or keep - audited and notified (in
  the app, and on Telegram where his alert channel takes the severity).

While a group is not COMPLETED its structure exit rules are not judged (they are written for the whole structure; on a
part of it they would read a false target or stop): the legs stay closable at any moment with Exit now, the emergency
exit, the intraday square-off and this module. The AI never calls any of this (ADR-0006).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TradeRecord, User

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
KEY = "incomplete"
INCOMPLETE, COMPLETING, COMPLETING_SENT, COMPLETED = "INCOMPLETE", "COMPLETING", "COMPLETING_SENT", "COMPLETED"
CLOSING, CLOSED, KEPT = "CLOSING", "CLOSED", "KEPT"
CLOSE_FILLED, KEEP = "close_filled", "keep"
ACTION_COMPLETE_REST, ACTION_CLOSE_FILLED, ACTION_KEEP = "complete_rest", "close_filled", "keep"
ACTIONS = (ACTION_COMPLETE_REST, ACTION_CLOSE_FILLED, ACTION_KEEP)
# States in which the structure's own exit rules wait (the group is not whole).
PAUSED = frozenset({INCOMPLETE, COMPLETING, COMPLETING_SENT, CLOSING, KEPT})
PriceLookup = Callable[[str, str], Awaitable[float]]


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat()


def _parse(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    when = datetime.fromisoformat(text)
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def clock_text(when: datetime) -> str:
    return when.astimezone(IST).strftime("%H:%M")


def rule_text(on_no_answer: str, deadline: datetime) -> str:
    if on_no_answer == KEEP:
        return f"If you do not answer by {clock_text(deadline)}, the filled legs stay open (your setting)."
    return f"If you do not answer by {clock_text(deadline)}, the filled legs are closed, sold legs first (your setting)."


def new_block(*, user_id: int, mode: str, strategy_id: str, deployment_id: Optional[int], account_id: Optional[int],
              idempotency_key: str, pending: Sequence[Any], leg_meta: Dict[str, Dict[str, Any]], failure: str, settings: Any,
              premiums: Dict[str, Optional[float]], debit: bool, stop_credit_pct: Optional[float], filled: int = 0,
              total: int = 0, round_no: int = 1, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The `incomplete` block an INCOMPLETE group carries. The deadline and the choice are his own settings now."""
    now = now or datetime.now(timezone.utc)
    deadline = now + timedelta(minutes=int(settings.answer_minutes))
    choice = CLOSE_FILLED if settings.on_no_answer != KEEP else KEEP
    return {"state": INCOMPLETE, "since": _iso(now), "deadline": _iso(deadline), "answer_minutes": int(settings.answer_minutes),
            "on_no_answer": choice, "user_id": user_id, "mode": mode, "strategy_id": strategy_id, "deployment_id": deployment_id,
            "account_id": account_id, "idempotency_key": idempotency_key, "round": round_no,
            "pending": [p.as_dict() for p in pending], "leg_meta": leg_meta, "premiums": premiums, "debit": debit,
            "stop_credit_pct": stop_credit_pct, "failure": failure,
            "message": ("Incomplete: a leg did not fill, so nothing more was sent. Complete the rest, or close the filled legs. "
                        + rule_text(choice, deadline)),
            "history": []}


def block_of(trade: TradeRecord) -> Optional[Dict[str, Any]]:
    try:
        meta = json.loads(trade.group_meta or "{}")
    except (TypeError, ValueError):
        return None
    block = meta.get(KEY) if isinstance(meta, dict) else None
    return block if isinstance(block, dict) else None


def rules_paused(legs: Sequence[TradeRecord]) -> bool:
    """True while the group is not whole: its structure exit rules wait (every leg stays closable by hand)."""
    block = block_of(legs[0]) if legs else None
    return block is not None and block.get("state") in PAUSED


async def _group(session: AsyncSession, tenant_id: int, group_id: str, *, open_only: bool = False) -> List[TradeRecord]:
    query = select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.leg_group_id == group_id)
    if open_only:
        query = query.where(TradeRecord.exit_time.is_(None))
    return list((await session.execute(query.order_by(TradeRecord.id).execution_options(populate_existing=True))).scalars())


async def _groups(session: AsyncSession, tenant_id: Optional[int], states: Sequence[str]) -> Dict[str, List[TradeRecord]]:
    """Open groups whose block is in `states` (open legs only)."""
    query = select(TradeRecord).where(TradeRecord.exit_time.is_(None), TradeRecord.leg_group_id.is_not(None),
                                      TradeRecord.group_meta.contains(f'"{KEY}"'))
    if tenant_id is not None:
        query = query.where(TradeRecord.tenant_id == tenant_id)
    out: Dict[str, List[TradeRecord]] = {}
    for trade in (await session.execute(query.order_by(TradeRecord.id).execution_options(populate_existing=True))).scalars():
        block = block_of(trade)
        if block is not None and block.get("state") in states and trade.leg_group_id:
            out.setdefault(trade.leg_group_id, []).append(trade)
    return out


async def _transition(session: AsyncSession, legs: Sequence[TradeRecord], expect: Sequence[str], change: Dict[str, Any],
                      *, note: str) -> Optional[Dict[str, Any]]:
    """Compare-and-set on the group's block: only one of a tap, a second tap and the worker's timer moves it (the first
    leg's row is the claim). Returns the new block, or None when the state was no longer one of `expect`."""
    if not legs:
        return None
    every = await _group(session, legs[0].tenant_id, legs[0].leg_group_id or "")
    first = every[0]
    block = block_of(first)
    if block is None or block.get("state") not in expect:
        return None
    old_text = first.group_meta
    new_block_ = {**block, **change}
    new_block_["history"] = list(block.get("history") or []) + [{"at": _iso(datetime.now(timezone.utc)), "state": new_block_["state"], "note": note}]
    meta = json.loads(old_text or "{}")
    meta[KEY] = new_block_
    claimed = await session.execute(update(TradeRecord).where(TradeRecord.id == first.id, TradeRecord.group_meta == old_text)
                                    .values(group_meta=json.dumps(meta)).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        await session.rollback()
        return None
    for leg in every[1:]:
        leg_meta = json.loads(leg.group_meta or "{}")
        leg_meta[KEY] = new_block_
        leg.group_meta = json.dumps(leg_meta)
    await session.commit()
    for leg in every:
        await session.refresh(leg)
    return new_block_


async def _say(session: AsyncSession, legs: Sequence[TradeRecord], user_id: Optional[int], title: str, message: str, *,
               live: bool, audit_event: str) -> None:
    from app.audit.log import write_audit_log
    from app.core.enums import NotificationSeverity, NotificationType
    from app.notifications.service import notify
    first = legs[0]
    await write_audit_log(session, first.tenant_id, user_id, audit_event,
                          f"group {first.leg_group_id}: {message} (legs {[leg.id for leg in legs]})"[:1000])
    await session.commit()
    await notify(session, first.tenant_id, NotificationType.STRUCTURE_INCOMPLETE, title=title, message=message[:1000],
                 severity=NotificationSeverity.CRITICAL if live else NotificationSeverity.WARNING, user_id=user_id,
                 related_trade_id=first.id)


def _name(legs: Sequence[TradeRecord]) -> str:
    return (legs[0].underlying_symbol or legs[0].symbol) if legs else ""


async def announce(session: AsyncSession, trades: Sequence[TradeRecord], block: Dict[str, Any], underlying: str, *,
                   user_id: Optional[int]) -> None:
    """The INCOMPLETE state is told at once: audit row, in-app, Telegram where his channel takes the severity."""
    if not trades:
        return
    await _say(session, trades, user_id, f"Incomplete order: {underlying}",
               f"{block['message']} What stopped it: {block['failure']}", live=block.get("mode") == "LIVE",
               audit_event="structure_incomplete")


# --- what the user sees ----------------------------------------------------------------------------------------------

def _leg_row(trade: TradeRecord) -> Dict[str, Any]:
    return {"trade_id": trade.id, "symbol": trade.symbol, "side": "BUY" if trade.direction == "LONG" else "SELL",
            "role": "hedge" if trade.leg_role != "SHORT" else "sold", "quantity": trade.quantity, "entry_price": trade.entry_price,
            "open": trade.exit_time is None}


def _item(legs: Sequence[TradeRecord], block: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    deadline = _parse(block.get("deadline"))
    seconds = max(0, int((deadline - now).total_seconds())) if deadline is not None else None
    state = block.get("state")
    actions = list(ACTIONS) if state == INCOMPLETE else []
    pending = [{"symbol": p["key"], "side": p["side"], "role": "hedge" if p["side"] == "BUY" else "sold", "quantity": p["quantity"]}
               for p in block.get("pending") or []]
    return {"group_id": legs[0].leg_group_id, "underlying": _name(legs), "strategy": (legs[0].option_strategy or "").replace("_", " ").lower(),
            "mode": block.get("mode"), "state": state, "message": block.get("message"), "failure": block.get("failure"),
            "deadline": block.get("deadline"), "seconds_left": seconds if state == INCOMPLETE else None,
            "on_no_answer": block.get("on_no_answer"), "rule": rule_text(block.get("on_no_answer") or CLOSE_FILLED, deadline) if deadline else None,
            "filled": [_leg_row(t) for t in legs if t.exit_time is None], "pending": pending, "actions": actions}


async def view_for_user(session: AsyncSession, user: User, now: Optional[datetime] = None) -> Dict[str, Any]:
    """His own incomplete orders (his own legs only, never another member's - BT1b) still waiting or being carried out."""
    now = now or datetime.now(timezone.utc)
    groups = await _groups(session, user.tenant_id, [INCOMPLETE, COMPLETING, COMPLETING_SENT, CLOSING])
    items = []
    for legs in groups.values():
        if not all(leg.user_id == user.id for leg in legs):
            continue
        block = block_of(legs[0]) or {}
        items.append(_item(legs, block, now))
    return {"items": items, "as_of": _iso(now)}


# --- his answer --------------------------------------------------------------------------------------------------------

class AnswerError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


async def answer(session: AsyncSession, user: User, group_id: str, action: str, *,
                 price_lookup: Optional[PriceLookup] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """His tap: "complete_rest" (recorded; the worker sends it), "close_filled" (an exit: PAPER legs close now, LIVE legs
    on the worker's next cycle) or "keep". A second tap answers the state it is in now; nothing is done twice."""
    from app.execution.approval import may_act_on_trade
    if action not in ACTIONS:
        raise AnswerError(422, "Unknown choice")
    now = now or datetime.now(timezone.utc)
    legs = await _group(session, user.tenant_id, group_id, open_only=True)
    if not legs or block_of(legs[0]) is None:
        raise AnswerError(404, "Unknown order")
    if not all(may_act_on_trade(user, leg) for leg in legs):
        raise AnswerError(403, "This order belongs to another member; only they or the organisation owner can answer it")
    target = {ACTION_COMPLETE_REST: COMPLETING, ACTION_CLOSE_FILLED: CLOSING, ACTION_KEEP: KEPT}[action]
    words = {ACTION_COMPLETE_REST: "Complete the rest", ACTION_CLOSE_FILLED: "Close the filled legs", ACTION_KEEP: "Keep them"}[action]
    block = await _transition(session, legs, [INCOMPLETE], {"state": target, "answered_by": "user", "answer": action,
                                                             "answered_at": _iso(now)}, note=f"his tap: {words}")
    if block is None:
        current = block_of((await _group(session, user.tenant_id, group_id))[0]) or {}
        return {"state": current.get("state"), "message": "Already answered: nothing more was done.", "group_id": group_id}
    live = block.get("mode") == "LIVE"
    await _say(session, legs, user.id, f"You chose {words}: {_name(legs)}",
               {ACTION_COMPLETE_REST: "The rest of the order is sent on the next check, hedge legs first.",
                ACTION_CLOSE_FILLED: "The filled legs are being closed, sold legs first.",
                ACTION_KEEP: "The filled legs stay open. Close each one with Exit now when you want."}[action],
               live=live, audit_event="structure_incomplete_answer")
    result: Dict[str, Any] = {"state": target, "group_id": group_id, "message": {
        ACTION_COMPLETE_REST: "Sending the rest of the order on the next check (within a minute), hedge legs first.",
        ACTION_CLOSE_FILLED: "Closing the filled legs, sold legs first.",
        ACTION_KEEP: "The filled legs stay open."}[action]}
    if action == ACTION_CLOSE_FILLED and not live:
        result.update(await close_filled_now(session, legs, price_lookup=price_lookup, user_id=user.id,
                                             reason="Incomplete order: you chose Close the filled legs"))
    return result


# --- closing the filled legs (an exit, never blocked) ---------------------------------------------------------------------

async def close_filled_now(session: AsyncSession, legs: Sequence[TradeRecord], *, price_lookup: Optional[PriceLookup],
                           user_id: Optional[int], reason: str, broker: Any = None) -> Dict[str, Any]:
    """The group's open legs through `close_group` (sold legs first; a hedge is held while its short is open). The price
    of each leg: `price_lookup`, else the monitor's last fresh price; a leg with neither is left open and retried."""
    from app.execution.approval import recent_mark
    from app.trading.position_monitor import close_group, venue_for
    open_legs = [leg for leg in legs if leg.exit_time is None]
    prices: Dict[int, float] = {}
    for leg in open_legs:
        price: Optional[float] = None
        if price_lookup is not None:
            try:
                price = float(await price_lookup(leg.symbol, await venue_for(session, leg)))
            except Exception as exc:  # noqa: BLE001 - the last fresh price below, else retried next cycle
                logger.warning("Incomplete order: no price for %s: %s", leg.symbol, type(exc).__name__)
        if (price is None or price <= 0) and leg.mark_price and recent_mark(leg):
            price = float(leg.mark_price)
        if price is not None and price > 0:
            prices[leg.id] = price
    outcomes = await close_group(session, list(open_legs), prices, reason, broker=broker, user_id=user_id) if open_legs else []
    left = await _group(session, legs[0].tenant_id, legs[0].leg_group_id or "", open_only=True) if legs else []
    rows = [{"trade_id": o.trade_id, "closed": o.closed, "exit_price": o.exit_price, "reasons": list(o.warnings)} for o in outcomes]
    if not left and legs:
        every = await _group(session, legs[0].tenant_id, legs[0].leg_group_id or "")
        await _transition(session, every, [CLOSING], {"state": CLOSED}, note="every filled leg closed")
        return {"state": CLOSED, "closed": rows, "message": "The filled legs are closed."}
    return {"state": CLOSING, "closed": rows,
            "message": "Not every leg could be closed yet: it is tried again on the next check. Each leg can also be closed with Exit now."}


# --- the trading worker: his rule at the deadline, the closes, the completions -----------------------------------------

async def run_exits(session: AsyncSession, tenant_id: int, price_lookup: Optional[PriceLookup], *, broker: Any = None,
                    user_id: Optional[int] = None, now: Optional[datetime] = None) -> int:
    """Every cycle, in the exits part (before any entry gate): the groups past their deadline get his rule (close or keep),
    then every CLOSING group is closed (sold legs first). Returns the legs closed. Never raises for one group."""
    now = now or datetime.now(timezone.utc)
    closed = 0
    for group_id, legs in (await _groups(session, tenant_id, [INCOMPLETE])).items():
        block = block_of(legs[0]) or {}
        deadline = _parse(block.get("deadline"))
        if deadline is None or now < deadline:
            continue
        keep = block.get("on_no_answer") == KEEP
        moved = await _transition(session, legs, [INCOMPLETE], {"state": KEPT if keep else CLOSING, "answered_by": "timer",
                                                                 "answer": KEEP if keep else CLOSE_FILLED, "answered_at": _iso(now)},
                                  note=f"no answer in {block.get('answer_minutes')} min: his rule")
        if moved is None:
            continue
        owner = block.get("user_id")
        await _say(session, legs, owner, f"No answer: {_name(legs)}",
                   ("You did not answer in time, so the filled legs stay open (your setting). Close each one with Exit now when you want."
                    if keep else "You did not answer in time, so the filled legs are being closed, sold legs first (your setting)."),
                   live=block.get("mode") == "LIVE", audit_event="structure_incomplete_timer")
    for group_id, legs in (await _groups(session, tenant_id, [CLOSING])).items():
        block = block_of(legs[0]) or {}
        try:
            result = await close_filled_now(session, legs, price_lookup=price_lookup, user_id=block.get("user_id") or user_id,
                                            broker=broker, reason=("Incomplete order: no answer, your rule closed the filled legs"
                                                                   if block.get("answered_by") == "timer" else
                                                                   "Incomplete order: you chose Close the filled legs"))
            closed += sum(1 for row in result["closed"] if row["closed"])
        except Exception as exc:  # noqa: BLE001 - one group never stops the sweep; retried next cycle (ADR-0004)
            logger.error("Incomplete order %s: closing the filled legs failed: %s", group_id, exc)
            await session.rollback()
    return closed


async def pending_completions(session: AsyncSession, tenant_id: int) -> Dict[str, List[TradeRecord]]:
    return await _groups(session, tenant_id, [COMPLETING])


async def complete_rest(session: AsyncSession, user: User, legs: Sequence[TradeRecord], *, broker: Any,
                        price_lookup: Optional[PriceLookup], now: Optional[datetime] = None,
                        refusal: Optional[str] = None) -> Dict[str, Any]:
    """"Complete the rest" (the trading worker, after his tap): the legs still to place, as ENTRY orders through every
    entry refusal and the engine (hedges first; the first that does not fill stops it). Claimed first (COMPLETING ->
    COMPLETING_SENT), so a second cycle never sends it again. COMPLETED when every leg filled; else INCOMPLETE again with
    a new deadline from his settings."""
    from app.core.enums import ExecutionMode, OrderStatus
    from app.core.models import Signal, Trade
    from app.db.models import OrderRecord, Tenant
    from app.execution import smart_execution
    from app.execution.multileg import leg_stop
    from app.execution.multileg_smart import LegSpec, place_legs
    from app.execution.order_persistence import create_order, transition_order
    from app.execution.paper_adapter import PaperExecutionAdapter
    from app.execution.paper_broker import PaperBroker
    from app.execution.signal_execution import entry_refusals
    from app.trading.persistence import persist_trade
    now = now or datetime.now(timezone.utc)
    block = block_of(legs[0]) or {}
    mode = block.get("mode") or ExecutionMode.PAPER.value
    live = mode == ExecutionMode.LIVE.value
    claimed = await _transition(session, legs, [COMPLETING], {"state": COMPLETING_SENT}, note="the worker sends the rest")
    if claimed is None:
        return {"state": None, "message": "Already handled"}
    tenant = await session.get(Tenant, user.tenant_id)
    settings = await smart_execution.settings_for(session, user)

    async def back(why: str) -> Dict[str, Any]:
        deadline = now + timedelta(minutes=int(settings.answer_minutes))
        choice = CLOSE_FILLED if settings.on_no_answer != KEEP else KEEP
        message = f"Not completed: {why}. Complete the rest, or close the filled legs. " + rule_text(choice, deadline)
        current = await _group(session, user.tenant_id, legs[0].leg_group_id or "", open_only=True)
        await _transition(session, current, [COMPLETING_SENT], {"state": INCOMPLETE, "deadline": _iso(deadline), "on_no_answer": choice,
                                                                "answer_minutes": int(settings.answer_minutes), "message": message,
                                                                "failure": why}, note=f"not completed: {why}")
        await _say(session, current, user.id, f"Incomplete order: {_name(current)}", message, live=live, audit_event="structure_incomplete")
        return {"state": INCOMPLETE, "message": message}

    strategy_id = str(block.get("strategy_id") or legs[0].strategy_id)
    refusals = await entry_refusals(session, tenant, user.tenant_id, mode, strategy_id, user=user,
                                    broker_name=getattr(broker, "name", None) if broker is not None else None, reserve=False)
    if refusal:
        refusals.insert(0, refusal)
    if refusals:
        return await back("; ".join(refusals))
    if live and broker is None:
        return await back("no usable broker session - log in again on the Broker page")
    specs = [LegSpec.from_dict(p) for p in block.get("pending") or []]
    if not specs:
        return await back("nothing left to place")
    prices: Dict[str, float] = {}
    for spec in specs:
        if price_lookup is None:
            break
        try:
            prices[spec.key] = float(await price_lookup(spec.key, spec.exchange))
        except Exception as exc:  # noqa: BLE001 - no price: a market order needs none; a practice order is then refused (no fill)
            logger.info("Complete the rest: no price for %s (%s)", spec.key, type(exc).__name__)
    venue = broker
    if not live:
        practice = PaperExecutionAdapter(PaperBroker())
        for key, price in prices.items():
            practice.mark(key, price)
        venue = practice
    round_no = int(block.get("round") or 1) + 1
    key_base = str(block.get("idempotency_key") or legs[0].leg_group_id)
    orders: Dict[str, OrderRecord] = {}
    for spec in specs:
        signal = None
        original = (await session.execute(select(OrderRecord).where(OrderRecord.tenant_id == user.tenant_id,
                                                                     OrderRecord.idempotency_key == f"{key_base}:L{spec.index}"))).scalar_one_or_none()
        if original is not None and original.signal_json:
            signal = Signal.model_validate_json(original.signal_json)
        if signal is None:
            from app.core.enums import SignalDirection
            signal = Signal(symbol=spec.key, strategy_id=strategy_id, strategy_name=strategy_id, timestamp=now,
                            direction=SignalDirection.LONG if spec.side == "BUY" else SignalDirection.SHORT)
        order, created = await create_order(session, user, mode=mode, strategy_id=strategy_id, signal=signal,
                                            idempotency_key=f"{key_base}:L{spec.index}:R{round_no}")
        if not created:
            return {"state": COMPLETING_SENT, "message": "Already sent"}
        order = await transition_order(session, order, OrderStatus.VALIDATING, detail="Complete the rest: leg received")
        order = await transition_order(session, order, OrderStatus.RISK_CHECK, detail="Entry checks passed")
        order.quantity = spec.quantity
        orders[spec.key] = order
    await session.commit()
    run = await place_legs(venue, specs, strategy_id=strategy_id, tenant=tenant, band_pct=settings.protection_band_pct,
                           prices=prices, live=live)
    if tenant is not None and run.uncertain:
        from app.reconciliation.service import mark_broker_uncertain
        await mark_broker_uncertain(session, tenant, f"completing a structure: {run.uncertain}"[:500], user_id=user.id)
        await session.commit()
    group_id = legs[0].leg_group_id or ""
    meta_now = json.loads(legs[0].group_meta or "{}")
    leg_meta = block.get("leg_meta") or {}
    booked: List[TradeRecord] = []
    for spec in specs:
        order = orders[spec.key]
        fill = run.fills.get(spec.key)
        if fill is None:
            why = run.leg_reasons.get(spec.key) or [run.failure or "not filled"]
            order.reasons_json = json.dumps(why)
            await transition_order(session, order, OrderStatus.FAILED if why[0] == run.failure else OrderStatus.REJECTED, detail="; ".join(why)[:500])
            continue
        if live and fill.order_id:
            order.broker_order_id = fill.order_id
        venue_name = getattr(broker, "name", "broker") if live else "paper broker"
        order = await transition_order(session, order, OrderStatus.SUBMITTED, detail=f"Submitted to {venue_name}")
        order = await transition_order(session, order, OrderStatus.PENDING, detail="Awaiting fill")
        order = await transition_order(session, order, OrderStatus.FILLED, detail=f"{'Live' if live else 'Paper'} fill {fill.order_id if live else ''}".strip())
        lm = dict(leg_meta.get(spec.key) or {})
        direction = lm.pop("direction", "LONG" if spec.side == "BUY" else "SHORT")
        if isinstance(lm.get("expiry"), str):
            from datetime import date
            lm["expiry"] = date.fromisoformat(lm["expiry"])
        lm.update({"leg_group_id": group_id, "group_meta": json.dumps(meta_now)})
        trade = Trade(symbol=spec.key, strategy_id=strategy_id, direction=direction, entry_time=now, entry_price=fill.price,
                      quantity=fill.quantity, stop_loss=leg_stop(fill.price, spec.role, debit=bool(block.get("debit")),
                                                                 stop_credit_pct=block.get("stop_credit_pct")),
                      expected_price=(block.get("premiums") or {}).get(spec.key) or prices.get(spec.key))
        record = await persist_trade(session, user, trade, mode=mode, broker_order_id=fill.order_id if live else None,
                                     deployment_id=block.get("deployment_id"), contract_meta=lm, account_id=block.get("account_id"))
        order.trade_id = record.id
        order.reasons_json = json.dumps(["Complete the rest"] + run.notes)
        await transition_order(session, order, OrderStatus.POSITION_OPEN, detail=f"Position opened (trade #{record.id}, group {group_id[:8]})")
        booked.append(record)
    await session.commit()
    every = await _group(session, user.tenant_id, group_id, open_only=True)
    if run.state == smart_execution.COMPLETE:
        await _transition(session, every, [COMPLETING_SENT], {"state": COMPLETED, "pending": []}, note="every leg filled")
        await _say(session, every, user.id, f"Order completed: {_name(every)}",
                   "Every leg is filled now. The structure's own exit rules apply again.", live=live, audit_event="structure_completed")
        return {"state": COMPLETED, "message": "Every leg is filled now."}
    await _transition(session, every, [COMPLETING_SENT], {"pending": [p.as_dict() for p in run.pending], "round": round_no}, note="partly completed")
    return await back(run.failure or "a leg did not fill")


__all__ = ["new_block", "block_of", "rules_paused", "announce", "view_for_user", "answer", "AnswerError", "close_filled_now",
           "run_exits", "pending_completions", "complete_rest", "ACTIONS", "PAUSED"]

