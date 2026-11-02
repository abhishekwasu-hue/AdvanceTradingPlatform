"""M75 W-8 (owner order M75 CW-6 / CW-7; DECISIONS 1 Nov: M75Q-1, M75Q-2 defaults confirmed; TM-23): trigger plans -
"set and wait". A plan is his own condition -> his own order(s) -> his own exits, kept until his own validity ends.

* Condition (W-8 core): the underlying's price crossing HIS level - a touch, or a CLOSED candle of HIS timeframe closing
  at / beyond it (the shared rules of app/trading/trigger_rules.py, never a candle that closed before the plan was armed:
  no look-ahead), optionally only inside HIS own time window (an operational setting, C.4).
* Method conditions (M75 W-9, app/execution/plan_conditions.py): the price inside HIS chosen area + a closed candle of
  HIS timeframe with the reader's rejection there, or a BOS / CHoCH (up / down) on HIS timeframe - from the same ONE
  source as the workspace's reading (the engine when it reads this chart for him, else ATP's readers; never both); the
  trigger note says which source met it. Same armed-after rule, same fire.
* Orders: each one his own contract, side, lots, order type, product and price - nothing is pre-chosen (M68). At fire
  time each order goes through the order ticket's OWN `place` (app/paper/fno_ticket.py): the kill switches, the order
  chokepoint (`phase0_lock.assert_paper`), the risk checks and approval-only run AGAIN then (TM-23). PAPER = LIVE: the
  one path, no mode of its own (W13). Journal source PLAN.
* Exits: the stop goes on the order as the ticket's own position stop; the target is armed on the filled position in
  the resting book (`resting_book.arm_exit`, a TARGET limit) - ordinary exits, never behind an approval or a kill switch
  (ADR-0004).
* Approval (M75Q-2): the plan's own choice "place at once" (AUTO) or "ask me first" (APPROVAL); Not set = his Trade
  settings > Automation > "When a plan triggers"; with neither the plan cannot be armed. The organisation's approval-only
  flag (and his own "Ask me first" for new entries) turn a fire into the existing ENTRY proposal (in-app + Telegram
  buttons, the existing waiting time); only his Approve places the order (`execute_approved`).
* Fires ONCE and is restart-safe: ARMED -> TRIGGERED is a conditional update (one winner); every order carries the
  client reference `plan-<id>-<n>` (the ticket's idempotency key) and every proposal the key `plan:<id>:<n>`, so a second
  pass, a second worker or a restart while TRIGGERED finishes what is left and never places twice. Results per order are
  kept on the row; the plan is DONE when every order is settled.
* Broker GTT (W-10, M75Q-1): not here; flag `broker_gtt` (OFF). PAPER plans always use this engine.

Worker-only for firing (`run`, called by the trigger engine's entry pass while the flag `trigger_plans` is on). The API
(app/execution/trigger_plan_routes.py) only creates, lists, edits, pauses and cancels his own plans. The AI never writes a
plan row or presses anything here (ADR-0006).
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AiActionRecord, TradeRecord, TriggerPlanRecord, User
from app.trading import trigger_rules
from app.trading.trigger_rules import ABOVE, BELOW, CLOSE, TOUCH

logger = logging.getLogger(__name__)

FLAG = "trigger_plans"
PRICE = "PRICE"                                   # W-8's price condition (W-9's kinds: app/execution/plan_conditions.py)
ARMED, PAUSED, TRIGGERED, DONE, EXPIRED, CANCELLED = "ARMED", "PAUSED", "TRIGGERED", "DONE", "EXPIRED", "CANCELLED"
WATCHED = (ARMED, TRIGGERED)                      # the rows the engine visits
EDITABLE = (ARMED, PAUSED)
FINAL = (DONE, EXPIRED, CANCELLED)
AUTO, APPROVAL = "AUTO", "APPROVAL"
APPROVALS = (AUTO, APPROVAL)
ORDER_TYPES = ("MARKET", "LIMIT")
PRODUCTS = ("MIS", "NRML")
RIGHTS = ("CE", "PE", "FUT")
MAX_ORDERS = 12                                   # the basket's own limit (app/paper/fno_basket.py MAX_LEGS)
MAX_TIMEFRAME = 24 * 60
SOURCE = "plan"
STRATEGY_ID = "trigger_plan"
IST = timezone(timedelta(hours=5, minutes=30))
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
STATUS_WORDS = {ARMED: "Armed", PAUSED: "Paused", TRIGGERED: "Triggered", DONE: "Done", EXPIRED: "Expired", CANCELLED: "Cancelled"}

PriceFn = Callable[[str, str], Awaitable[float]]
CandleFn = Callable[[str, str], Awaitable[Sequence[object]]]


class PlanError(ValueError):
    """A plan that cannot be kept, in plain words."""


@dataclass
class Condition:
    """His own condition: the underlying crossing `level` from `side`, by touch or by a closed candle of his timeframe
    (kind PRICE, W-8); or (W-9) his area + the reader's rejection candle (AREA), or a BOS / CHoCH (STRUCTURE)."""
    level: float = 0.0
    side: str = ""                                # ABOVE | BELOW (PRICE)
    mode: str = ""                                # TOUCH | CLOSE (PRICE)
    timeframe_minutes: int | None = None
    window_start: str | None = None               # "HH:MM" IST, his own (operational); None = any time
    window_end: str | None = None
    kind: str = "PRICE"                           # PRICE | AREA | STRUCTURE (a W-8 row has no kind: PRICE)
    area_side: str | None = None                  # AREA: buyer | seller - the area he picked from the reading
    area_low: float | None = None
    area_high: float | None = None
    area_timeframe: str | None = None             # AREA: the area's own timeframe (shown only; never merged)
    event: str | None = None                      # STRUCTURE: BOS | CHOCH
    direction: str | None = None                  # STRUCTURE: up | down


@dataclass
class PlanOrder:
    """One order of the plan: every field his own."""
    expiry: str                                   # YYYY-MM-DD
    strike: float | None                          # None for a future
    right: str                                    # CE | PE | FUT
    side: str                                     # BUY | SELL
    lots: int
    order_type: str                               # MARKET | LIMIT
    product: str                                  # MIS | NRML
    price: float | None = None                    # LIMIT only
    stop_loss: float | None = None                # the position's stop (premium), the ticket's own
    target: float | None = None                   # the target (premium), armed on the filled position


def condition_errors(c: Condition) -> list[str]:
    from app.execution import plan_conditions as PC
    if c.kind not in PC.KINDS:
        return ["Choose what the plan watches."]
    if c.kind != PC.PRICE:
        return _method_errors(c) + _window_errors(c)
    errors: list[str] = []
    if not (c.level > 0):
        errors.append("Set the level the plan watches.")
    if c.side not in (ABOVE, BELOW):
        errors.append("Say whether the price must go above or below your level.")
    if c.mode not in (TOUCH, CLOSE):
        errors.append("Choose whether the plan fires on a touch or on a candle close.")
    if c.mode == CLOSE and not (c.timeframe_minutes and 1 <= c.timeframe_minutes <= MAX_TIMEFRAME):
        errors.append("A candle-close plan needs its timeframe in minutes.")
    return errors + _window_errors(c)


def _method_errors(c: Condition) -> list[str]:
    """W-9: his area (side + edges from the reading) or his BOS / CHoCH + direction, and his timeframe of the reading."""
    from app.execution import plan_conditions as PC
    errors: list[str] = []
    if PC.read_tf(c.timeframe_minutes) is None:
        errors.append("Choose the candle timeframe the reading uses: 1, 3, 5, 15, 30 or 60 minutes, or daily.")
    if c.kind == PC.AREA:
        if c.area_side not in PC.AREA_SIDES:
            errors.append("Pick the buyer or seller area the plan watches.")
        if not (c.area_low and c.area_high and 0 < c.area_low <= c.area_high):
            errors.append("Pick the area from the chart's reading.")
    else:
        if c.event not in PC.EVENTS:
            errors.append("Choose a break of structure (BOS) or a change of character (CHoCH).")
        if c.direction not in PC.DIRECTIONS:
            errors.append("Choose up or down.")
    return errors


def _window_errors(c: Condition) -> list[str]:
    errors: list[str] = []
    if (c.window_start is None) != (c.window_end is None):
        errors.append("A time window needs both its start and its end.")
    for t in (c.window_start, c.window_end):
        if t is not None and not _HHMM.match(t):
            errors.append("Write a window time as HH:MM.")
    if c.window_start and c.window_end and _HHMM.match(c.window_start) and _HHMM.match(c.window_end) and c.window_start >= c.window_end:
        errors.append("The window's start must be before its end.")
    return errors


def order_errors(o: PlanOrder, n: int) -> list[str]:
    errors: list[str] = []
    what = f"Order {n + 1}:"
    try:
        date.fromisoformat(o.expiry)
    except ValueError:
        errors.append(f"{what} choose the expiry.")
    if o.right not in RIGHTS:
        errors.append(f"{what} choose call, put or future.")
    if o.right == "FUT" and o.strike is not None:
        errors.append(f"{what} a future has no strike.")
    if o.right in ("CE", "PE") and not (o.strike and o.strike > 0):
        errors.append(f"{what} choose the strike.")
    if o.side not in ("BUY", "SELL"):
        errors.append(f"{what} choose buy or sell.")
    if not (isinstance(o.lots, int) and o.lots >= 1):
        errors.append(f"{what} enter the lots.")
    if o.order_type not in ORDER_TYPES:
        errors.append(f"{what} choose market or limit.")
    if o.order_type == "LIMIT" and not (o.price and o.price > 0):
        errors.append(f"{what} a limit order needs its price.")
    if o.product not in PRODUCTS:
        errors.append(f"{what} choose intraday (MIS) or carry forward (NRML).")
    for name, value in (("stop-loss", o.stop_loss), ("target", o.target)):
        if value is not None and not value > 0:
            errors.append(f"{what} the {name} must be above zero.")
    return errors


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _now(now: datetime | None) -> datetime:
    return _utc(now or datetime.now(UTC))


def condition_of(row: TriggerPlanRecord) -> Condition:
    return Condition(**json.loads(row.condition_json))


def orders_of(row: TriggerPlanRecord) -> list[PlanOrder]:
    return [PlanOrder(**o) for o in json.loads(row.orders_json)]


def results_of(row: TriggerPlanRecord) -> dict[str, dict[str, Any]]:
    return json.loads(row.results_json or "{}")


async def plan_approval_setting(session: AsyncSession, user: User) -> str | None:
    """His Trade settings > Automation > "When a plan triggers" (None = Not set)."""
    from app.execution import automation
    return (await automation.load(session, user)).plan_trigger_action


async def _check(session: AsyncSession, user: User, condition: Condition, orders: list[PlanOrder], approval: str | None,
                 valid_until: datetime, now: datetime) -> str:
    errors = condition_errors(condition)
    if not orders:
        errors.append("Add at least one order.")
    if len(orders) > MAX_ORDERS:
        errors.append(f"A plan holds at most {MAX_ORDERS} orders.")
    for n, o in enumerate(orders):
        errors.extend(order_errors(o, n))
    chosen = approval or await plan_approval_setting(session, user)
    if chosen not in APPROVALS:
        errors.append("Choose what happens when the plan triggers: place at once, or ask me first.")
    if _utc(valid_until) <= now:
        errors.append("The plan's validity must end in the future.")
    if errors:
        raise PlanError(" ".join(errors))
    return str(chosen)


async def by_ref(session: AsyncSession, user: User, client_ref: str) -> TriggerPlanRecord | None:
    return (await session.execute(select(TriggerPlanRecord).where(
        TriggerPlanRecord.tenant_id == user.tenant_id, TriggerPlanRecord.user_id == user.id,
        TriggerPlanRecord.client_ref == client_ref))).scalar_one_or_none()


async def create(session: AsyncSession, user: User, *, client_ref: str, name: str | None, underlying: str, watch_symbol: str,
                 watch_exchange: str, condition: Condition, orders: list[PlanOrder], approval: str | None, valid_until: datetime,
                 now: datetime | None = None) -> TriggerPlanRecord:
    """Arms a new plan (or returns the one already made with this client reference - a second tap makes no second plan)."""
    now = _now(now)
    found = await by_ref(session, user, client_ref)
    if found is not None:
        return found
    chosen = await _check(session, user, condition, orders, approval, valid_until, now)
    row = TriggerPlanRecord(tenant_id=user.tenant_id, user_id=user.id, client_ref=client_ref, name=(name or None),
                            underlying=underlying, watch_symbol=watch_symbol, watch_exchange=watch_exchange.upper(),
                            condition_json=json.dumps(asdict(condition)), orders_json=json.dumps([asdict(o) for o in orders]),
                            approval=chosen, status=ARMED, valid_until=_utc(valid_until), armed_at=now, created_at=now, updated_at=now)
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        again = await by_ref(session, user, client_ref)
        if again is None:
            raise
        return again
    return row


async def list_for_user(session: AsyncSession, user: User, *, include_final: bool = True) -> list[TriggerPlanRecord]:
    query = select(TriggerPlanRecord).where(TriggerPlanRecord.tenant_id == user.tenant_id, TriggerPlanRecord.user_id == user.id)
    if not include_final:
        query = query.where(TriggerPlanRecord.status.not_in(FINAL))
    return list(await session.scalars(query.order_by(TriggerPlanRecord.id.desc()).limit(200)))


async def own(session: AsyncSession, user: User, plan_id: int) -> TriggerPlanRecord:
    row = await session.get(TriggerPlanRecord, plan_id)
    if row is None or row.tenant_id != user.tenant_id or row.user_id != user.id:
        raise LookupError("Not found")
    return row


async def _move(session: AsyncSession, row: TriggerPlanRecord, frm: Sequence[str], to: str, now: datetime, **values: Any) -> bool:
    """A conditional state change (one winner): False when the row is no longer in `frm`."""
    done = await session.execute(update(TriggerPlanRecord).where(TriggerPlanRecord.id == row.id, TriggerPlanRecord.status.in_(tuple(frm)))
                                 .values(status=to, updated_at=now, **values))
    await session.refresh(row)
    return bool(done.rowcount)


async def pause(session: AsyncSession, user: User, plan_id: int, now: datetime | None = None) -> TriggerPlanRecord:
    row = await own(session, user, plan_id)
    await _gtt_down(session, row, _now(now))                 # W-10: the broker GTT goes first (no fire after his tap)
    if not await _move(session, row, (ARMED,), PAUSED, _now(now)):
        raise PlanError(f"This plan is {STATUS_WORDS[row.status].lower()}: only an armed plan can be paused.")
    return row


async def resume(session: AsyncSession, user: User, plan_id: int, now: datetime | None = None) -> TriggerPlanRecord:
    """Arms a paused plan again. No candle that closed while it was paused counts (armed again now: no look-ahead)."""
    now = _now(now)
    row = await own(session, user, plan_id)
    if _utc(row.valid_until) <= now:
        raise PlanError("This plan's validity has ended: edit its validity first.")
    if not await _move(session, row, (PAUSED,), ARMED, now, armed_at=now):
        raise PlanError(f"This plan is {STATUS_WORDS[row.status].lower()}: only a paused plan can be resumed.")
    return row


async def cancel(session: AsyncSession, user: User, plan_id: int, now: datetime | None = None) -> TriggerPlanRecord:
    """Cancels an armed or paused plan. A triggered plan's orders are already on their way; its exits are never touched."""
    row = await own(session, user, plan_id)
    await _gtt_down(session, row, _now(now))                 # W-10: the broker GTT goes first (no fire after his tap)
    if not await _move(session, row, EDITABLE, CANCELLED, _now(now), finished_at=_now(now), note="Cancelled by you"):
        raise PlanError(f"This plan is {STATUS_WORDS[row.status].lower()}: it can no longer be cancelled.")
    return row


async def edit(session: AsyncSession, user: User, plan_id: int, *, condition: Condition, orders: list[PlanOrder], approval: str | None,
               valid_until: datetime, name: str | None, now: datetime | None = None) -> TriggerPlanRecord:
    """Changes an armed or paused plan (all of it, his own values again); it is armed afresh from now."""
    now = _now(now)
    row = await own(session, user, plan_id)
    if row.status not in EDITABLE:
        raise PlanError(f"This plan is {STATUS_WORDS[row.status].lower()}: it can no longer be edited.")
    chosen = await _check(session, user, condition, orders, approval, valid_until, now)
    await _gtt_down(session, row, now)                       # W-10: the old GTT goes first (never a GTT at the old level)
    if not await _move(session, row, EDITABLE, row.status, now, condition_json=json.dumps(asdict(condition)),
                       orders_json=json.dumps([asdict(o) for o in orders]), approval=chosen, valid_until=_utc(valid_until),
                       name=name or None, armed_at=now):
        raise PlanError("This plan changed meanwhile: open it again.")
    from app.execution import broker_gtt
    if not broker_gtt.active(row):
        row.results_json = None                   # armed afresh: an earlier broker answer does not carry over
    return row


async def _gtt_down(session: AsyncSession, row: TriggerPlanRecord, now: datetime) -> None:
    """W-10: a paused, edited or cancelled plan's broker GTT is cancelled at his broker (the worker re-arms an armed plan).
    The change goes ahead only once no GTT waits at his broker: a GTT that already triggered there moves the plan to
    TRIGGERED (its order was sent; never placed again), and a cancel his broker did not confirm leaves the plan as it is."""
    from app.execution import broker_gtt
    if broker_gtt.active(row):
        await broker_gtt.on_plan_change(session, row, now)
        await session.flush()
    if await broker_gtt.took_over(session, row, now):
        raise PlanError("Your broker's GTT already triggered: its order was sent, so the plan can no longer be changed.")
    if broker_gtt.active(row):
        raise PlanError("Your broker did not confirm the GTT's cancel, so the plan is unchanged. Try again.")


def condition_words(underlying: str, c: Condition) -> str:
    """His condition in plain words for the plans list."""
    from app.execution import plan_conditions as PC
    if c.kind == PC.AREA:
        tf = PC.TF_WORDS.get(PC.read_tf(c.timeframe_minutes) or "", "his")
        return (f"{underlying} inside the {c.area_side} area {c.area_low or 0:g} - {c.area_high or 0:g} and a {tf} candle "
                "with the reading's rejection there")
    if c.kind == PC.STRUCTURE:
        tf = PC.TF_WORDS.get(PC.read_tf(c.timeframe_minutes) or "", "his")
        return f"{underlying}: {PC.EVENT_NAMES.get(c.event or '', 'structure event')} {c.direction} on the {tf} chart"
    how = "on a touch" if c.mode == TOUCH else f"on a {c.timeframe_minutes}-minute candle close"
    return f"{underlying} {'above' if c.side == ABOVE else 'below'} {c.level:g} ({how})"


def view(row: TriggerPlanRecord) -> dict[str, Any]:
    c = condition_of(row)
    return {"id": row.id, "name": row.name, "underlying": row.underlying, "watch_symbol": row.watch_symbol,
            "watch_exchange": row.watch_exchange, "condition": asdict(c),
            "condition_words": condition_words(row.underlying, c), "orders": [asdict(o) for o in orders_of(row)],
            "approval": row.approval, "status": row.status, "status_words": STATUS_WORDS.get(row.status, row.status),
            "valid_until": _utc(row.valid_until).isoformat(), "armed_at": _utc(row.armed_at).isoformat(),
            "triggered_at": _utc(row.triggered_at).isoformat() if row.triggered_at else None,
            "finished_at": _utc(row.finished_at).isoformat() if row.finished_at else None,
            "trigger_price": row.trigger_price, "last_price": row.last_price, "results": results_of(row), "note": row.note,
            "mode": "PAPER"}


# --- the engine (worker only) --------------------------------------------------------------------------------------------

async def enabled(session: AsyncSession, tenant_id: int) -> bool:
    from app.platform.controls import flag_enabled
    return bool(await flag_enabled(session, FLAG, tenant_id))


async def tenants_with_plans(session: AsyncSession) -> set[int]:
    return set(await session.scalars(select(TriggerPlanRecord.tenant_id).where(TriggerPlanRecord.status.in_(WATCHED)).distinct()))


def in_window(c: Condition, now: datetime) -> bool:
    """His own time window (IST), when he set one; operational only - the reading never looks at the clock."""
    if not c.window_start or not c.window_end:
        return True
    hhmm = now.astimezone(IST).strftime("%H:%M")
    return c.window_start <= hhmm < c.window_end


async def method_reached(session: AsyncSession, row: TriggerPlanRecord, c: Condition, now: datetime) -> bool | None:
    """W-9: his area / structure condition from the chart's ONE reading source (app/execution/plan_conditions.py). Read
    again only once a new candle of his timeframe can have closed. The trigger note names the source that met it."""
    from app.execution import plan_conditions as PC
    armed = _utc(row.armed_at)
    if not PC.due(row.id, row.condition_json, armed, now):
        return False
    user = await session.get(User, row.user_id)
    if user is None:
        return None
    try:
        reading = await PC.read(session, user, row.watch_symbol, row.watch_exchange, c, armed, now)
    except Exception as exc:  # noqa: BLE001 - no reading, no decision this pass
        logger.info("Trigger plans: no method reading for %s (%s)", row.watch_symbol, type(exc).__name__)
        reading = PC.Reading(None)
    PC.seen(row.id, row.condition_json, armed, reading, c.timeframe_minutes, now)
    if reading.last_close is not None:
        row.last_price, row.last_checked_at = reading.last_close, now
    if reading.hit is None:
        return None if reading.source is None else False
    row.trigger_price = reading.hit.price if reading.hit.price is not None else reading.last_close
    row.note = reading.hit.words[:300]
    return True


async def reached(row: TriggerPlanRecord, c: Condition, prices: PriceFn, candles: CandleFn | None, now: datetime) -> bool | None:
    """True: his condition is met now; False: not yet; None: no reading this pass (never a made-up price)."""
    if c.mode == CLOSE:
        if candles is None or not c.timeframe_minutes:
            return None
        try:
            bars = await candles(row.watch_symbol, row.watch_exchange)
        except Exception as exc:  # noqa: BLE001 - no candles, no decision this pass
            logger.info("Trigger plans: no candles for %s (%s)", row.watch_symbol, type(exc).__name__)
            return None
        candle = trigger_rules.close_trigger(c.level, c.side, bars, int(c.timeframe_minutes), now, armed_at=_utc(row.armed_at))
        closed = trigger_rules.closed_candles(bars, int(c.timeframe_minutes), now)
        if closed:
            row.last_price, row.last_checked_at = closed[-1].close, now
        if candle is not None:
            row.trigger_price = candle.close
        return candle is not None
    try:
        price = float(await prices(row.watch_symbol, row.watch_exchange))
    except Exception as exc:  # noqa: BLE001 - no price, no decision this pass
        logger.info("Trigger plans: no price for %s (%s)", row.watch_symbol, type(exc).__name__)
        return None
    if not price > 0:
        return None
    row.last_price, row.last_checked_at = price, now
    hit = trigger_rules.touched(c.level, c.side, price, price)
    if hit:
        row.trigger_price = price
    return hit


async def _notify(session: AsyncSession, row: TriggerPlanRecord, title: str, message: str, *, warning: bool = False) -> None:
    from app.core.enums import NotificationSeverity, NotificationType
    from app.notifications.service import notify
    await notify(session, row.tenant_id, NotificationType.ENTRY if not warning else NotificationType.REJECTION, title=title,
                 message=message, severity=NotificationSeverity.WARNING if warning else NotificationSeverity.INFO, user_id=row.user_id)


def _label(row: TriggerPlanRecord) -> str:
    return row.name or f"{row.underlying} plan"


async def run(session: AsyncSession, tenant_id: int, prices: PriceFn, *, candles: CandleFn | None = None,
              now: datetime | None = None) -> dict[str, int]:
    """One pass over the organisation's plans: expire the ended ones, finish every TRIGGERED plan (a restart or an earlier
    pass left it), then fire each ARMED plan whose condition is met - once. W-10: a plan waiting at his broker's GTT is
    read from the broker instead (app/execution/broker_gtt.py; flag `broker_gtt`, OFF)."""
    from app.execution import broker_gtt
    now = _now(now)
    report = {"checked": 0, "triggered": 0, "expired": 0, "done": 0}
    if not await enabled(session, tenant_id):
        return report
    ids = list(await session.scalars(select(TriggerPlanRecord.id).where(TriggerPlanRecord.tenant_id == tenant_id,
                                                                     TriggerPlanRecord.status.in_((ARMED, PAUSED, TRIGGERED)))
                                     .order_by(TriggerPlanRecord.id)))
    for row_id in ids:
        # each plan read afresh: the plan before it committed (the ticket, a proposal), which expires what was loaded
        row = await session.get(TriggerPlanRecord, row_id, populate_existing=True)
        if row is None:
            continue
        try:
            if row.status in (ARMED, PAUSED) and _utc(row.valid_until) <= now:
                await broker_gtt.on_plan_change(session, row, now)        # W-10: its broker GTT goes first
                if await broker_gtt.took_over(session, row, now) or broker_gtt.active(row):
                    await session.commit()                                # fired at the broker / not cancelled yet: never
                    continue                                              # expired while a GTT can still send its order
                if await _move(session, row, (ARMED, PAUSED), EXPIRED, now, finished_at=now, note="Its validity ended before it triggered"):
                    report["expired"] += 1
                    await _notify(session, row, f"Plan expired: {_label(row)}", "Its validity ended before the condition was met. Nothing was placed.")
                    await session.commit()
                continue
            if row.status == TRIGGERED:
                if broker_gtt.fired(row) or broker_gtt.active(row):        # W-10: the broker fired it; ATP never places it
                    await broker_gtt.handle(session, row, prices, now)
                    report["done"] += int(row.status == DONE)
                elif await fire(session, row, prices, now):
                    report["done"] += 1
                continue
            if row.status != ARMED:
                continue
            report["checked"] += 1
            if await broker_gtt.handle(session, row, prices, now):     # W-10: waiting at (or fired by) his broker's GTT
                report["triggered"] += int(row.status != ARMED)
                continue
            c = condition_of(row)
            if not in_window(c, now):
                continue
            if c.kind == PRICE:
                hit = await reached(row, c, prices, candles, now)
            else:
                hit = await method_reached(session, row, c, now)
            if not hit:
                await session.commit()          # the last reading only
                continue
            if not await _move(session, row, (ARMED,), TRIGGERED, now, triggered_at=now, trigger_price=row.trigger_price,
                               **({"note": row.note} if c.kind != PRICE else {})):
                continue                        # another pass / worker won: it fires, this one does not
            await session.commit()
            report["triggered"] += 1
            if await fire(session, row, prices, now):
                report["done"] += 1
        except Exception:  # noqa: BLE001 - one plan never stops the others; a TRIGGERED plan is finished next pass
            logger.exception("Trigger plans: plan %s failed", row_id)
            await session.rollback()
    return report


async def needs_approval(session: AsyncSession, row: TriggerPlanRecord) -> bool:
    """His "ask me first", or the organisation's approval-only mode / his own "Ask me first" for new entries (they win)."""
    from app.execution import approval
    return row.approval == APPROVAL or await approval.required(session, row.tenant_id, None, row.user_id)


def ask_of(row: TriggerPlanRecord, o: PlanOrder, n: int) -> Any:
    from app.paper import fno_ticket
    return fno_ticket.OrderAsk(underlying=row.underlying, expiry=date.fromisoformat(o.expiry), strike=o.strike, right=o.right,
                               side=o.side, lots=o.lots, order_type=o.order_type, product=o.product,
                               price=o.price if o.order_type == "LIMIT" else None, stop_loss=o.stop_loss, client_ref=f"plan-{row.id}-{n}")


async def fire(session: AsyncSession, row: TriggerPlanRecord, prices: PriceFn, now: datetime) -> bool:
    """Places (or proposes) every order of a TRIGGERED plan that is not settled yet, arms each filled order's target, and
    ends the plan DONE when all are settled. Safe to run again: every step is keyed (True when the plan ended DONE)."""
    from app.execution import approval
    from app.paper import fno_ticket
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active or user.tenant_id != row.tenant_id:
        await _move(session, row, (TRIGGERED,), DONE, now, finished_at=now, note="The account is no longer active: nothing was placed")
        await session.commit()
        return True
    ask_first = await needs_approval(session, row)
    results = results_of(row)
    plan_orders = orders_of(row)
    for n, o in enumerate(plan_orders):
        # every step below commits (the ticket, a proposal, the plan's results): read the plan and the user again first
        await session.refresh(row)
        await session.refresh(user)
        r = dict(results.get(str(n)) or {})
        if not r.get("settled"):
            if ask_first:
                r = await _ask(session, row, o, n, r, now)
            else:
                res = await fno_ticket.place(session, user, ask_of(row, o, n), price_lookup=prices, now=now,
                                             origin=approval.ORIGIN_AUTOMATIC, journal_source="PLAN")
                r = {"status": res.get("status"), "trade_id": res.get("trade_id"), "order_id": res.get("order_id"),
                     "reasons": [str(x) for x in (res.get("reasons") or [])][:5], "settled": True}
            await session.refresh(row)
            await session.refresh(user)
        if r.get("trade_id") and o.target and not r.get("target_armed"):
            r.update(await _arm_target(session, user, row, o, n, int(r["trade_id"]), now))
        results[str(n)] = r
        row.results_json = json.dumps(results)
        row.updated_at = now
        await session.commit()
    await session.refresh(row)
    if all((results.get(str(n)) or {}).get("settled") for n in range(len(plan_orders))):
        placed = sum(1 for r in results.values() if r.get("trade_id"))
        note = f"{placed} of {len(results)} order(s) placed"
        if row.note:                             # W-9: what met his method condition, and by which source
            note = f"{row.note} {note}"[:300]
        if await _move(session, row, (TRIGGERED,), DONE, now, finished_at=now, note=note):
            await _notify(session, row, f"Plan triggered: {_label(row)}", f"{note}. Its exits are on the position(s).",
                          warning=placed < len(results))
        await session.commit()
        return True
    return False


async def _arm_target(session: AsyncSession, user: User, row: TriggerPlanRecord, o: PlanOrder, n: int, trade_id: int,
                      now: datetime) -> dict[str, Any]:
    """His target on the filled position: a TARGET limit in the resting book (keyed: armed once)."""
    from app.execution import resting_book
    trade = await session.get(TradeRecord, trade_id)
    if trade is None or trade.exit_time is not None:
        return {"target_armed": True, "target_note": "The position was already closed"}
    try:
        await resting_book.arm_exit(session, user, trade, client_ref=f"exit:plan-{row.id}-{n}-target", kind=resting_book.TARGET,
                                    order_type="LIMIT", level=float(o.target or 0), now=now)
    except resting_book.RestingOrderError as exc:
        return {"target_armed": True, "target_note": str(exc)[:200]}
    return {"target_armed": True}


async def _ask(session: AsyncSession, row: TriggerPlanRecord, o: PlanOrder, n: int, r: dict[str, Any], now: datetime) -> dict[str, Any]:
    """"Ask me first": the existing ENTRY proposal (in-app + Telegram buttons, his waiting time) - one per order, keyed;
    settled when his answer is final. Nothing is placed here."""
    from app.core.enums import SignalDirection, SignalGrade
    from app.core.models import Signal
    from app.execution import approval
    key = f"plan:{row.id}:{n}"
    action_id = r.get("proposal_id")
    if action_id is None:
        contract = f"{row.underlying} {o.expiry} {'' if o.strike is None else f'{o.strike:g} '}{o.right}".replace("  ", " ")
        signal = Signal(symbol=row.watch_symbol, strategy_id=STRATEGY_ID, strategy_name="Trigger plan",
                        direction=SignalDirection.LONG if o.side == "BUY" else SignalDirection.SHORT, timestamp=now,
                        entry=float(row.trigger_price or row.last_price or 0) or None, grade=SignalGrade.VALID,
                        reasons=[f"Your plan {_label(row)}: {o.side} {o.lots} lot(s) {contract}"], timeframe_combo="plan")
        try:
            action = await approval.propose_entry(session, row.tenant_id, signal=signal, strategy_id=STRATEGY_ID, source=SOURCE,
                                                  idempotency_key=key, user_id=row.user_id, exchange=row.watch_exchange, now=now)
        except Exception as exc:  # noqa: BLE001 - a refused proposal (paused, locked out, no price) settles the order with why
            return {"status": "NOT_PROPOSED", "reasons": [str(exc)[:200]], "settled": True}
        await session.refresh(row)
        if action is None:
            action = (await session.execute(select(AiActionRecord).where(AiActionRecord.tenant_id == row.tenant_id,
                                                                         AiActionRecord.idempotency_key == key))).scalar_one_or_none()
        if action is None:
            return {"status": "NOT_PROPOSED", "reasons": ["The request could not be raised"], "settled": True}
        evidence = json.loads(action.evidence_json or "{}")
        if "trigger_plan" not in evidence:
            evidence["trigger_plan"] = {"plan_id": row.id, "order": n}
            action.evidence_json = json.dumps(evidence)
        await session.commit()
        return {"status": "PROPOSED", "proposal_id": action.id}
    action = await session.get(AiActionRecord, int(action_id))
    if action is None:
        return {**r, "status": "NOT_PLACED", "settled": True}
    if action.status == "EXECUTED":
        return {**r, "status": "PLACED", "trade_id": action.trade_id, "settled": True}
    if action.status in ("REJECTED", "EXPIRED", "FAILED"):
        return {**r, "status": action.status, "reasons": [str(action.result or "")[:200]], "settled": True}
    return r                                       # still waiting for his answer


async def execute_approved(session: AsyncSession, action: AiActionRecord, owner: User, evidence: dict[str, Any], price_lookup: Any,
                           now: datetime) -> dict[str, Any]:
    """His Approve on a plan's ENTRY proposal: that ONE order through the ticket's own `place`, with the approved proposal
    (origin "approval") - every check again now. Raises ValueError (nothing placed) when it cannot."""
    from app.execution import approval
    from app.paper import fno_ticket
    ref = evidence.get("trigger_plan") or {}
    row = await session.get(TriggerPlanRecord, int(ref.get("plan_id") or 0))
    if row is None or row.tenant_id != action.tenant_id or row.user_id != owner.id:
        raise ValueError("The plan of this request is gone - nothing was placed")
    n = int(ref.get("order") or 0)
    plan_orders = orders_of(row)
    if not 0 <= n < len(plan_orders):
        raise ValueError("The plan's order is gone - nothing was placed")
    res = await fno_ticket.place(session, owner, ask_of(row, plan_orders[n], n), price_lookup=price_lookup, now=now,
                                 origin=approval.ORIGIN_APPROVAL, approval_action_id=action.id, journal_source="PLAN")
    if res.get("status") != "PLACED":
        raise ValueError("PAPER order not placed: " + ("; ".join(str(x) for x in (res.get("reasons") or [])) or "refused"))
    return {"status": "EXECUTED", "executed_at": now, "trade_id": res.get("trade_id"),
            "result": f"PAPER order #{res.get('order_id')} placed from your plan" + (f"; position #{res.get('trade_id')}" if res.get("trade_id") else "")}
