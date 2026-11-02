"""TM-2 / TM-23 / TT-6 (owner, 23-24 Oct): the ONE trigger engine - it watches every resting order of the resting book
(app/execution/resting_book.py) and fires the ones the price reaches.

Where it runs (TT-6): inside the trading worker only - on live ticks from the data hub when the organisation streams
(`tick_pass`, every TRIGGER_TICK_SECONDS, touch triggers), and once per worker cycle on the cycle's own prices
(`run_exits` before every entry gate, `run_entries` after them) for everything, including candle-close triggers. M55 S3
(A12): ONE exit cadence - PAPER and LIVE resting exits fire on the same tick pass and the same cycle; a LIVE exit goes out
at the session of the broker account that holds the position (`_exit_session`). No API route calls it.

What it decides (TT-7, the shared rules of app/trading/trigger_rules.py):
* touch: a tick reaches the level; close: a CLOSED candle of the order's own timeframe closes at / beyond it (no
  look-ahead, never a candle that closed before the order was armed);
* stops first: in one pass the reached STOPs are acted on before TARGETs, TARGETs before waiting entries; when an exit
  closes a position its other armed exits are cancelled;
* the source (T-D2): the option's own price (PREMIUM) or the underlying (SPOT) - the order's own setting;
* combined exits (CX / SX, app/execution/group_exits.py) are judged FIRST in every exit pass, on the same one reading per
  symbol: the group's ₹ MTM, its net premium or the spot level; a reached one closes all its legs as one group (sold
  legs first, then the hedges) through `position_monitor.close_group`, and every later pass closes what is still open.

What it does at fire time (TM-23):
* an ENTRY is placed through the order ticket's own `place` (app/paper/fno_ticket.py) - the ordinary pipeline, so the
  kill switches, the order chokepoint (`phase0_lock.assert_paper`), the risk checks and approval-only run AGAIN now,
  and a refused entry is REJECTED with its plain reasons (and a notification). Same fill and cost model as an order
  placed now (the PaperExecutionAdapter). M55 S3 (A17): a reached LIVE entry meets the same entry chokepoint as the
  ticket (`phase0_lock.assert_paper`): the owner's LIVE switches decide, and while they are off it is refused with
  their plain reason; nothing LIVE is placed until the LIVE manual path (TT-1) exists. M55 A18: while Redis (the
  worker's replica lock) is unreachable no entry fires or is re-sent - each stays waiting and fires on the first pass
  after Redis is back;
* an EXIT closes the user's own position through `position_monitor.close_position` - the one exit path (a PAPER fill
  through the PaperExecutionAdapter; a LIVE position squares off at its own broker session, handed in by the worker).
  An exit is NEVER blocked by a kill switch, a limit or the PAPER lock (ADR-0004);
* a triggered stop with a limit (SL) whose limit the price is already past keeps waiting at its limit, as at an
  exchange; a target limit the price moved away from keeps waiting.

Heartbeat (TT-6 / TT-26): every pass writes its last-run time (`worker_heartbeats`, name `trigger_engine`, read by
GET /api/system/worker-status for the status bar). `check_stall` (each worker cycle) tells every user with waiting
orders - in the app and on Telegram, through the ordinary notification path - when the engine has not run for
TRIGGER_STALL_SECONDS during market hours. The AI never calls this (ADR-0006).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time as monotonic
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface
from app.core.config import TRIGGER_ACK_TIMEOUT_SECONDS, TRIGGER_STALL_SECONDS, TRIGGER_TICK_MAX_AGE_SECONDS, TRIGGER_TICK_SECONDS
from app.db.models import GroupExitRecord, PaperRestingOrderRecord, TradeRecord, User, WorkerHeartbeatRecord
from app.execution import group_exits as gx
from app.execution import resting_book as book
from app.trading import trigger_rules
from app.trading.trigger_rules import CLOSE

logger = logging.getLogger(__name__)

ENGINE_NAME = "trigger_engine"
PriceFn = Callable[[str, str], Awaitable[float]]
CandleFn = Callable[[str, str], Awaitable[Sequence[Any]]]
HEARTBEAT_EVERY_SECONDS = 5.0
STALL_TITLE = "Your waiting orders are not being watched right now"
# M55 S3 (A17): what a reached LIVE resting entry says while the owner's PAPER-only lock holds (plain words, no flag name).
LIVE_ENTRY_SWITCHED_OFF = "Live trading is switched off for your account, so this live order was not sent: nothing was placed"

# M55 A18: while Redis - and with it the worker's replica lock - cannot be reached, a reached entry waits (one WARNING per
# outage, not one per tick); exits never look at this (ADR-0004).
ENTRIES_PAUSED_NOTE = "Redis is unreachable, so the worker's replica lock is off: waiting entries are held (exits continue)"
_entries_paused_warned = False

_last_run: Optional[datetime] = None
_last_beat = 0.0
_stall_alerted: Set[int] = set()       # users told about the current stall (cleared when the engine runs again)


@dataclass
class EngineReport:
    checked: int = 0
    filled: int = 0
    rejected: int = 0
    expired: int = 0
    cancelled: int = 0
    waiting: int = 0
    errors: List[str] = field(default_factory=list)


def last_run() -> Optional[datetime]:
    """The last time a pass of the engine completed in this process."""
    return _last_run


def fire_order_type(order_type: str) -> str:
    """What a reached order is sent as: a stop without a limit becomes a market order, a stop with a limit becomes its
    limit, a limit stays a limit - the exchange's own behaviour once a trigger is hit."""
    kind = (order_type or "MARKET").upper()
    if kind in ("SL-M", "MARKET"):
        return "MARKET"
    return "LIMIT"


def _rank(row: PaperRestingOrderRecord) -> Tuple[int, int, int]:
    order = trigger_rules.STOP_FIRST
    return (0 if row.purpose == book.EXIT else 1, order.index(row.kind) if row.kind in order else len(order), row.id)


async def tenants_with_resting(session: AsyncSession, *, paper_only: bool = False) -> Set[int]:
    """Organisations with a waiting order or an armed / exiting combined exit."""
    query = select(PaperRestingOrderRecord.tenant_id).where(PaperRestingOrderRecord.status.in_((book.RESTING, book.FIRING)))
    groups = select(GroupExitRecord.tenant_id).where(GroupExitRecord.status.in_(gx.LIVE_STATUSES))
    if paper_only:
        query = query.where(PaperRestingOrderRecord.mode == "PAPER")
        groups = groups.where(GroupExitRecord.mode == "PAPER")
    return set(await session.scalars(query.distinct())) | set(await session.scalars(groups.distinct()))


async def _resting(session: AsyncSession, tenant_id: int, purpose: str, paper_only: bool) -> List[PaperRestingOrderRecord]:
    query = select(PaperRestingOrderRecord).where(PaperRestingOrderRecord.tenant_id == tenant_id,
                                                  PaperRestingOrderRecord.status == book.RESTING,
                                                  PaperRestingOrderRecord.purpose == purpose)
    if paper_only:
        query = query.where(PaperRestingOrderRecord.mode == "PAPER")
    return sorted(await session.scalars(query), key=_rank)


class _Prices:
    """One reading per symbol per pass (every order on the same symbol judged on the same price); never a made-up one."""

    def __init__(self, fn: PriceFn) -> None:
        self.fn = fn
        self.seen: Dict[Tuple[str, str], Optional[float]] = {}

    async def get(self, symbol: str, exchange: str) -> Optional[float]:
        key = (symbol.upper(), exchange.upper())
        if key not in self.seen:
            try:
                value: Optional[float] = float(await self.fn(symbol, exchange))
            except Exception as exc:  # noqa: BLE001 - no price, no decision this pass (never a made-up price)
                logger.info("Trigger engine: no price for %s:%s (%s)", exchange, symbol, type(exc).__name__)
                value = None
            self.seen[key] = value if value is not None and value > 0 else None
        return self.seen[key]


async def _reached(row: PaperRestingOrderRecord, prices: _Prices, candles: Optional[CandleFn], now: datetime) -> Optional[bool]:
    """True: the trigger is reached now; False: not yet; None: no reading this pass (the order keeps waiting)."""
    level, side = float(row.trigger_level), row.trigger_side
    if row.trigger_mode == CLOSE:
        if candles is None or not row.timeframe_minutes:
            return None
        try:
            bars = await candles(row.trigger_symbol, row.trigger_exchange)
        except Exception as exc:  # noqa: BLE001 - no candles, no decision this pass
            logger.info("Trigger engine: no candles for %s (%s)", row.trigger_symbol, type(exc).__name__)
            return None
        closed = trigger_rules.closed_candles(bars, int(row.timeframe_minutes), now)
        if closed:
            row.last_price, row.last_checked_at = closed[-1].close, now
        return trigger_rules.close_trigger(level, side, bars, int(row.timeframe_minutes), now, armed_at=row.created_at) is not None
    price = await prices.get(row.trigger_symbol, row.trigger_exchange)
    if price is None:
        return None
    row.last_price, row.last_checked_at = price, now
    return trigger_rules.touched(level, side, price, price)


async def _claim(session: AsyncSession, row: PaperRestingOrderRecord, now: datetime) -> bool:
    """RESTING -> FIRING exactly once (the tick pass and the cycle never fire the same order twice)."""
    claimed = await session.execute(update(PaperRestingOrderRecord).where(
        PaperRestingOrderRecord.id == row.id, PaperRestingOrderRecord.status == book.RESTING).values(
        status=book.FIRING, fired_at=now, updated_at=now).execution_options(synchronize_session=False))
    if getattr(claimed, "rowcount", 0) != 1:
        await session.rollback()
        return False
    await session.commit()
    await session.refresh(row)
    return True


async def _finish(session: AsyncSession, row: PaperRestingOrderRecord, status: str, note: str, now: datetime) -> None:
    row.status, row.status_note, row.updated_at = status, note, now
    book.add_trail(row, status, note, now)
    await session.commit()


async def _back_to_waiting(session: AsyncSession, row: PaperRestingOrderRecord, note: str, now: datetime) -> None:
    row.status, row.status_note, row.fired_at, row.updated_at = book.RESTING, note, None, now
    book.add_trail(row, book.RESTING, note, now)
    await session.commit()


async def _keep_as_limit(session: AsyncSession, row: PaperRestingOrderRecord, now: datetime) -> None:
    """A reached order whose limit the price is past: it waits at its limit, touched by any print (exchange-true)."""
    assert row.price is not None
    row.order_type, row.trigger_source, row.trigger_symbol, row.trigger_exchange = "LIMIT", book.PREMIUM, row.symbol, row.exchange
    row.trigger_level, row.trigger_side = float(row.price), trigger_rules.order_trigger_side(row.side, "LIMIT")
    row.trigger_mode, row.timeframe_minutes = trigger_rules.TOUCH, None
    await _back_to_waiting(session, row, f"Triggered: waiting at your limit {float(row.price):,.2f}", now)


async def _notify(session: AsyncSession, row: PaperRestingOrderRecord, title: str, message: str, *, warning: bool) -> None:
    from app.core.enums import NotificationSeverity, NotificationType
    from app.notifications.service import notify
    try:
        await notify(session, row.tenant_id, NotificationType.REJECTION, title=title, message=message,
                     severity=NotificationSeverity.WARNING if warning else NotificationSeverity.INFO, user_id=row.user_id,
                     related_trade_id=row.trade_id, metadata_json=json.dumps({"resting_order": row.id}))
    except Exception:  # noqa: BLE001 - a notification never undoes the order's own state
        logger.exception("Trigger engine: notification for resting order %s failed", row.id)
        await session.rollback()


async def _expire(session: AsyncSession, rows: List[PaperRestingOrderRecord], now: datetime, report: EngineReport) -> List[PaperRestingOrderRecord]:
    alive: List[PaperRestingOrderRecord] = []
    for row in rows:
        expires = row.expires_at if row.expires_at is None or row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
        if expires is not None and now >= expires:
            await _finish(session, row, book.EXPIRED, "Expired at the end of the day: nothing was placed", now)
            await _notify(session, row, f"Waiting order expired: {row.symbol}", "It was not reached today; nothing was placed.",
                          warning=False)
            report.expired += 1
        else:
            alive.append(row)
    return alive


async def run_exits(session: AsyncSession, tenant_id: int, prices: PriceFn, *, broker: Optional[BrokerInterface] = None,
                    candles: Optional[CandleFn] = None, now: Optional[datetime] = None, paper_only: bool = False) -> EngineReport:
    """Every armed exit of the organisation, stops first. Never blocked (ADR-0004)."""
    now = now or datetime.now(timezone.utc)
    report = EngineReport()
    reader = _Prices(prices)
    await run_group_exits(session, tenant_id, reader, candles=candles, broker=broker, now=now, report=report,
                          paper_only=paper_only)
    await _resolve_firing(session, tenant_id, book.EXIT, reader, broker, now, report, paper_only)
    for row in await _resting(session, tenant_id, book.EXIT, paper_only):
        report.checked += 1
        try:
            await _evaluate_exit(session, row, reader, candles, broker, now, report)
        except Exception as exc:  # noqa: BLE001 - one order never stops the others; it is tried again next pass
            logger.exception("Trigger engine: exit order %s failed", row.id)
            report.errors.append(f"exit {row.id}: {type(exc).__name__}")
            await session.rollback()
            await _restore(session, row, now)
    await _mark_run(session, now, report)
    return report


async def entries_paused() -> bool:
    """M55 A18: True while Redis is unreachable - the worker's replica lock (trading_worker.LOCK_KEY, read the way the H-1
    renewal reads it) is then off, and a second worker could fire the same entry, so entries fail closed like the
    worker's own (#749). One WARNING when an outage starts, one INFO when Redis is back. Entries only - never an exit."""
    global _entries_paused_warned
    from app.cache.client import cache_lock_holder
    from app.workers.trading_worker import LOCK_KEY
    _, reachable = await cache_lock_holder(LOCK_KEY)
    if not reachable and not _entries_paused_warned:
        logger.warning("Trigger engine: %s", ENTRIES_PAUSED_NOTE)
    elif reachable and _entries_paused_warned:
        logger.info("Trigger engine: Redis is back - waiting entries fire again when reached")
    _entries_paused_warned = not reachable
    return not reachable


async def run_entries(session: AsyncSession, tenant_id: int, prices: PriceFn, *, candles: Optional[CandleFn] = None,
                      now: Optional[datetime] = None, paused: bool | None = None) -> EngineReport:
    """Every waiting entry of the organisation, each placed through the ordinary pipeline when reached. M55 A18: while
    the replica lock is off (`paused`; None = read it now, `entries_paused`) nothing is fired or re-sent: every entry
    stays waiting - the expiry rules still apply - and fires on the first pass after Redis is back."""
    now = now or datetime.now(timezone.utc)
    report = EngineReport()
    reader = _Prices(prices)
    if paused is None:
        paused = await entries_paused()
    if not paused:
        await _resolve_firing(session, tenant_id, book.ENTRY, reader, None, now, report, False)
    rows = await _expire(session, await _resting(session, tenant_id, book.ENTRY, False), now, report)
    if paused:
        report.waiting += len(rows)
        await _mark_run(session, now, report)
        return report
    for row in rows:
        report.checked += 1
        try:
            await _evaluate_entry(session, row, reader, candles, now, report)
        except Exception as exc:  # noqa: BLE001 - one order never stops the others
            logger.exception("Trigger engine: entry order %s failed", row.id)
            report.errors.append(f"entry {row.id}: {type(exc).__name__}")
            await session.rollback()
            await _restore(session, row, now)
    await _mark_run(session, now, report)
    return report


async def _restore(session: AsyncSession, row: PaperRestingOrderRecord, now: datetime) -> None:
    """After an unexpected error: an order left FIRING by this pass waits again (tried on the next pass)."""
    try:
        await session.refresh(row)
        if row.status == book.FIRING:
            await _back_to_waiting(session, row, "Not sent this time (a temporary problem): tried again on the next price", now)
    except Exception:  # noqa: BLE001
        logger.exception("Trigger engine: could not restore resting order %s", row.id)
        await session.rollback()


async def _evaluate_exit(session: AsyncSession, row: PaperRestingOrderRecord, prices: _Prices, candles: Optional[CandleFn],
                         broker: Optional[BrokerInterface], now: datetime, report: EngineReport) -> None:
    trade = await session.get(TradeRecord, row.trade_id) if row.trade_id is not None else None
    if trade is None or trade.exit_time is not None:
        await _finish(session, row, book.CANCELLED, "The position was already closed", now)
        report.cancelled += 1
        return
    broker = await _exit_session(session, trade, broker)

    reached = await _reached(row, prices, candles, now)
    if not reached:
        return                             # its last price is saved with the pass (one commit)
    if trade.mode != "PAPER" and broker is None:
        report.waiting += 1                # no usable session for the account that holds it: waits, the cycle says why
        return
    contract_price = await prices.get(row.symbol, row.exchange)
    if contract_price is None:
        report.waiting += 1
        return
    from app.paper.fno_ticket import paper_fill
    if fire_order_type(row.order_type) == "LIMIT" and paper_fill("LIMIT", row.side, contract_price, row.price, None)[0] is None:
        await _keep_as_limit(session, row, now)
        report.waiting += 1
        return
    if not await _claim(session, row, now):
        return
    await _send_exit(session, row, trade, contract_price, broker, now, report)


async def exit_broker_for(session: AsyncSession, trade: TradeRecord,
                          handed_in: BrokerInterface | None) -> BrokerInterface | None:
    """M55 S5: the broker session that exits this LIVE position - resolved per position from the broker account that holds
    it, exactly as the position monitor resolves it: the session the cycle hands in (the worker hands one in only when
    the organisation has a single account - that account's), else `position_monitor.broker_for_trade` for this position
    (several accounts: each position its own account's session). A LIVE exit never waits for a session nobody hands in.
    PAPER: unchanged."""
    if trade.mode == "PAPER" or handed_in is not None:
        return handed_in
    from app.trading.position_monitor import broker_for_trade
    return await broker_for_trade(session, trade)


def _exit_reason(row: PaperRestingOrderRecord) -> str:
    return f"{'Stop' if row.kind == book.STOP else 'Target'} (your waiting order)"


async def _send_exit(session: AsyncSession, row: PaperRestingOrderRecord, trade: TradeRecord, contract_price: float,
                     broker: Optional[BrokerInterface], now: datetime, report: EngineReport) -> None:
    """Sends a claimed (FIRING) exit: the position closes through the one exit path, with this order's client id."""
    from app.paper.fno_ticket import paper_fill
    from app.trading.position_monitor import close_position
    fire_type = fire_order_type(row.order_type)
    if fire_type == "LIMIT" and paper_fill("LIMIT", row.side, contract_price, row.price, None)[0] is None:
        await _keep_as_limit(session, row, now)
        report.waiting += 1
        return
    underlying = None
    if row.trigger_source == book.SPOT:
        underlying = row.last_price
    exit_price = contract_price
    if trade.mode == "PAPER":
        exit_price = await _paper_fill(session, row, fire_type, contract_price)
    outcome = await close_position(session, trade, exit_price, _exit_reason(row), broker=broker, user_id=row.user_id, now=now,
                                   underlying_price=underlying)
    await session.refresh(row)
    if outcome.closed:
        row.fill_price = outcome.exit_price
        await _finish(session, row, book.FILLED, f"Closed at {float(outcome.exit_price or exit_price):,.2f}", now)
        if row.trade_id is not None and await book.cancel_for_trade(session, row.trade_id, "The position closed (another exit filled)", now):
            await session.commit()
        report.filled += 1
        return
    if trade.exit_time is not None or "already closed" in " ".join(outcome.warnings).lower():
        await _finish(session, row, book.CANCELLED, "The position was already closed", now)
        report.cancelled += 1
        return
    await _back_to_waiting(session, row, "; ".join(outcome.warnings) or "The exit is waiting for the broker; tried again next pass", now)
    report.waiting += 1


async def _paper_fill(session: AsyncSession, row: PaperRestingOrderRecord, fire_type: str, price: float) -> float:
    """The PAPER fill of a reached exit on the paper exchange (TM-1's adapter: the same slippage and limit cap), sent
    with the order's own client id (a second send of the same id returns the first, TM2-4)."""
    from app.brokers.models import BrokerOrderRequest
    from app.core.enums import OrderSide
    from app.execution.paper_adapter import PaperExecutionAdapter
    from app.paper import paper_settings  # M68 PR-7: the order owner's own slippage (Not set: none)
    own = await paper_settings.charges_of_ids(session, row.tenant_id, row.user_id)
    adapter = PaperExecutionAdapter(own.broker(), book=durable_book(session, row))
    adapter.mark(row.symbol, price, row.exchange)
    request = BrokerOrderRequest(symbol=row.symbol, exchange=row.exchange, transaction_type=OrderSide(row.side),
                                 quantity=float(row.quantity), order_type=fire_type, price=row.price if fire_type == "LIMIT" else None,
                                 client_ref=row.client_ref)
    response = await adapter.place_order(request)
    status = adapter.order(response.order_id)
    if status is not None and status.average_price is not None:
        return float(status.average_price)
    return price


async def _evaluate_entry(session: AsyncSession, row: PaperRestingOrderRecord, prices: _Prices, candles: Optional[CandleFn],
                          now: datetime, report: EngineReport) -> None:
    from app.paper import fno_ticket
    reached = await _reached(row, prices, candles, now)
    if not reached:
        return
    contract_price = await prices.get(row.symbol, row.exchange)
    if contract_price is None:
        report.waiting += 1
        return
    if fire_order_type(row.order_type) == "LIMIT" and fno_ticket.paper_fill("LIMIT", row.side, contract_price, row.price, None)[0] is None:
        await _keep_as_limit(session, row, now)
        report.waiting += 1
        return
    if not await _claim(session, row, now):
        return
    await _send_entry(session, row, contract_price, now, report)


async def _send_entry(session: AsyncSession, row: PaperRestingOrderRecord, contract_price: float, now: datetime,
                      report: EngineReport) -> None:
    """Sends a claimed (FIRING) entry through the order ticket's own pipeline, with the ticket's own client id."""
    from app.execution.approval import ORIGIN_AUTOMATIC
    from app.paper import fno_ticket
    if row.mode != "PAPER":
        # TM-23 / owner principle / M55 S3 (A17): the same entry chokepoint as the order ticket - the owner's LIVE switches
        # (the Phase 0 lock) decide and say why. Until the LIVE manual path (TT-1) exists nothing LIVE is sent, whatever
        # the switches say. Never an exit: exits never come here (ADR-0004).
        reason = await _live_entry_refusal(session, row)
        await _finish(session, row, book.REJECTED, reason, now)
        await _notify(session, row, f"Waiting order not placed: {row.symbol}", f"Your price was reached, but: {reason}", warning=True)
        report.rejected += 1
        return
    fire_type = fire_order_type(row.order_type)
    if fire_type == "LIMIT" and fno_ticket.paper_fill("LIMIT", row.side, contract_price, row.price, None)[0] is None:
        await _keep_as_limit(session, row, now)
        report.waiting += 1
        return
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active or user.tenant_id != row.tenant_id:
        await _finish(session, row, book.CANCELLED, "The account is no longer active: nothing was placed", now)
        report.cancelled += 1
        return
    payload = json.loads(row.payload_json or "{}")
    ask = fno_ticket.ask_from_payload(payload["ask"])
    ask.rest_if_waiting = False
    ask.order_type = fire_type
    ask.trigger_price = None
    if fire_type == "MARKET":
        ask.price = None

    async def lookup(symbol: str, exchange: str = "") -> float:
        return float(contract_price)

    result = await fno_ticket.place(session, user, ask, price_lookup=lookup, now=now, origin=str(payload.get("origin") or ORIGIN_AUTOMATIC))
    await session.refresh(row)
    if result.get("status") == "PLACED":
        row.order_id = result.get("order_id")
        row.fill_price = (result.get("filled") or {}).get("price")
        await _finish(session, row, book.FILLED, f"Placed at {float(row.fill_price or contract_price):,.2f}", now)
        report.filled += 1
        return
    reasons = "; ".join(str(r) for r in (result.get("reasons") or [])) or "Not placed"
    row.order_id = result.get("order_id")
    await _finish(session, row, book.REJECTED, reasons, now)
    await _notify(session, row, f"Waiting order not placed: {row.symbol}", f"Your price was reached, but: {reasons}", warning=True)
    report.rejected += 1


# --- TM2-4: an order left "Sending" (FIRING) - settled from its broker by its own client id --------------------------------

def client_id(row: PaperRestingOrderRecord) -> Optional[str]:
    """The id the order goes out with: an entry's ticket idempotency key (the pipeline's order), an exit's client_ref."""
    if row.purpose == book.EXIT:
        return row.client_ref
    try:
        ref = (json.loads(row.payload_json or "{}").get("ask") or {}).get("client_ref")
    except (TypeError, ValueError):
        ref = None
    return f"fno:{row.user_id}:{ref}" if ref else None


def durable_book(session: AsyncSession, row: PaperRestingOrderRecord) -> Callable[[str], Awaitable[Optional[Any]]]:
    """The paper exchange's order book by client id for this order, read from the order / trade rows - so a lookup
    after a restart sees what the stopped worker placed."""
    async def durable(wanted: str) -> Optional[Any]:
        return await _paper_book(session, row, wanted)
    return durable


def paper_venue(session: AsyncSession, row: PaperRestingOrderRecord) -> Any:
    """The paper exchange for this order: TM-1's adapter with the durable order book by client id."""
    from app.execution.paper_adapter import PaperExecutionAdapter
    return PaperExecutionAdapter(book=durable_book(session, row))


async def _paper_book(session: AsyncSession, row: PaperRestingOrderRecord, wanted: str) -> Optional[Any]:
    from app.brokers.models import BrokerOrderStatus
    from app.core.enums import OrderSide
    from app.db.models import OrderRecord
    if wanted != client_id(row):
        return None
    side, kind = OrderSide(row.side), fire_order_type(row.order_type)
    if row.purpose == book.ENTRY:
        order = await session.scalar(select(OrderRecord).where(OrderRecord.tenant_id == row.tenant_id,
                                                               OrderRecord.idempotency_key == wanted))
        if order is None:
            return None
        trade = await session.get(TradeRecord, order.trade_id) if order.trade_id is not None else None
        filled = trade is not None
        return BrokerOrderStatus(order_id=str(order.id), symbol=row.symbol, transaction_type=side, quantity=float(row.quantity),
                                 filled_quantity=float(row.quantity) if filled else 0.0, order_type=kind,
                                 status="COMPLETE" if filled else "REJECTED", average_price=trade.entry_price if trade else None,
                                 tag=wanted)
    trade = await session.get(TradeRecord, row.trade_id) if row.trade_id is not None else None
    if trade is None or trade.exit_time is None or trade.exit_reason != _exit_reason(row):
        return None
    return BrokerOrderStatus(order_id=f"EXIT-{trade.id}", symbol=row.symbol, transaction_type=side, quantity=float(row.quantity),
                             filled_quantity=float(row.quantity), order_type=kind, status="COMPLETE",
                             average_price=trade.exit_price, tag=wanted)


async def _take_over(session: AsyncSession, row: PaperRestingOrderRecord, now: datetime) -> bool:
    """FIRING as last seen -> FIRING owned by this pass, exactly once (two passes never settle the same order)."""
    seen = row.fired_at
    same = PaperRestingOrderRecord.fired_at.is_(None) if seen is None else PaperRestingOrderRecord.fired_at == seen
    taken = await session.execute(update(PaperRestingOrderRecord).where(
        PaperRestingOrderRecord.id == row.id, PaperRestingOrderRecord.status == book.FIRING, same).values(
        fired_at=now, updated_at=now).execution_options(synchronize_session=False))
    if getattr(taken, "rowcount", 0) != 1:
        await session.rollback()
        return False
    await session.commit()
    await session.refresh(row)
    return True


async def _send_later(session: AsyncSession, row: PaperRestingOrderRecord, note: str, now: datetime) -> None:
    """Not at its broker: sent once, with the same id, on the next pass that has its price (and, for a LIVE exit, its
    broker session). It stays "Sending" (FIRING, no claim time) and comes first in that pass."""
    row.fired_at, row.status_note, row.updated_at = None, note, now
    book.add_trail(row, book.FIRING, note, now)
    await session.commit()


async def _reconcile(session: AsyncSession, row: PaperRestingOrderRecord, prices: Optional[_Prices],
                     broker: Optional[BrokerInterface], now: datetime, report: EngineReport) -> None:
    """One FIRING order this pass took over: looked up at its broker by its client id - found: its state is adopted;
    not found: sent once with the same id (the PAPER adapter and the live adapters answer the same lookup)."""
    again = "Not at the broker: sent once with the same id"
    if row.purpose == book.ENTRY and row.mode != "PAPER":
        await _finish(session, row, book.REJECTED, "Live manual orders are not switched on: nothing was placed", now)
        report.rejected += 1
        return
    if row.purpose == book.EXIT:
        trade = await session.get(TradeRecord, row.trade_id) if row.trade_id is not None else None
        if trade is None:
            await _finish(session, row, book.CANCELLED, "The position was already closed", now)
            report.cancelled += 1
            return
        if trade.mode != "PAPER":
            # LIVE: the one exit path looks the exit up in the broker's order book by its tag and adopts it, or sends it
            # once with the same tag (position_monitor's exit ledger and re-exit guard).
            broker = await exit_broker_for(session, trade, broker)
            price = await prices.get(row.symbol, row.exchange) if prices is not None else None
            if broker is None or price is None:
                await _send_later(session, row, "Checked at the broker on the next pass with its session", now)
                return
            await _send_exit(session, row, trade, price, broker, now, report)
            return
        found = await paper_venue(session, row).find_order_by_client_id(row.client_ref)
        if found is not None and found.status == "COMPLETE":
            row.fill_price = found.average_price
            await _finish(session, row, book.FILLED, f"Closed at {float(found.average_price or 0):,.2f} (found after a restart)", now)
            if row.trade_id is not None and await book.cancel_for_trade(session, row.trade_id, "The position closed (another exit filled)", now):
                await session.commit()
            report.filled += 1
            return
        if trade.exit_time is not None:
            await _finish(session, row, book.CANCELLED, "The position was already closed", now)
            report.cancelled += 1
            return
        price = await prices.get(row.symbol, row.exchange) if prices is not None else None
        if price is None:
            await _send_later(session, row, again + " at the next price", now)
            return
        await _send_exit(session, row, trade, price, None, now, report)
        return
    wanted = client_id(row)
    found = await paper_venue(session, row).find_order_by_client_id(wanted) if wanted else None
    if found is not None:
        row.order_id = int(found.order_id)
        if found.status == "COMPLETE":
            row.fill_price = found.average_price
            await _finish(session, row, book.FILLED, f"Placed at {float(found.average_price or 0):,.2f} (found after a restart)", now)
            report.filled += 1
        else:
            await _finish(session, row, book.REJECTED, "Not placed (its order was refused before the restart)", now)
            report.rejected += 1
        return
    price = await prices.get(row.symbol, row.exchange) if prices is not None else None
    if price is None:
        await _send_later(session, row, again + " at the next price", now)
        return
    await _send_entry(session, row, price, now, report)


def _overdue(row: PaperRestingOrderRecord, now: datetime, timeout: float) -> bool:
    if row.fired_at is None:
        return True                        # settled at start-up as "not at the broker": sent on this pass
    fired = row.fired_at if row.fired_at.tzinfo else row.fired_at.replace(tzinfo=timezone.utc)
    return (now - fired).total_seconds() >= timeout


async def _resolve_firing(session: AsyncSession, tenant_id: Optional[int], purpose: Optional[str], prices: Optional[_Prices],
                          broker: Optional[BrokerInterface], now: datetime, report: EngineReport, paper_only: bool,
                          ack_timeout: Optional[float] = None) -> None:
    """Every FIRING order (of the organisation and purpose; None = all) still not acknowledged after the ack timeout
    (`ack_timeout` 0: every one, at start-up) - exits first - settled through `_reconcile`, before any new trigger."""
    timeout = TRIGGER_ACK_TIMEOUT_SECONDS if ack_timeout is None else ack_timeout
    query = select(PaperRestingOrderRecord).where(PaperRestingOrderRecord.status == book.FIRING)
    if tenant_id is not None:
        query = query.where(PaperRestingOrderRecord.tenant_id == tenant_id)
    if purpose is not None:
        query = query.where(PaperRestingOrderRecord.purpose == purpose)
    if paper_only:
        query = query.where(PaperRestingOrderRecord.mode == "PAPER")
    for row in sorted(await session.scalars(query), key=_rank):
        if not _overdue(row, now, timeout) or not await _take_over(session, row, now):
            continue
        try:
            await _reconcile(session, row, prices, broker, now, report)
        except Exception as exc:  # noqa: BLE001 - it stays FIRING and is settled again after the ack timeout
            logger.exception("Trigger engine: settling order %s failed", row.id)
            report.errors.append(f"settle {row.id}: {type(exc).__name__}")
            await session.rollback()


async def reconcile_on_start(session: AsyncSession, now: Optional[datetime] = None) -> EngineReport:
    """TM2-4: on worker start, before the first cycle and the tick loop, every FIRING order of every organisation is
    looked up at its broker by its client id, exits first: found -> its state; not found -> sent once with the same id
    on the first pass that has its price (and, for a LIVE exit, the position's broker session)."""
    now = now or datetime.now(timezone.utc)
    report = EngineReport()
    await _resolve_firing(session, None, None, None, None, now, report, False, ack_timeout=0.0)
    return report

# --- combined exits (CX / SX, app/execution/group_exits.py) -------------------------------------------------------------

async def _group_reading(row: GroupExitRecord, legs: List[Dict[str, Any]], reader: _Prices, candles: Optional[CandleFn],
                         now: datetime, exits: Optional[Dict[int, float]] = None,
                         last_traded: Optional[Dict[int, Tuple[float, Optional[datetime]]]] = None) -> Optional[str]:
    """SL / TARGET when the group's measure reached it on this reading; None: not reached or no reading this pass.

    CX-F1 (owner, 25 Oct): a ₹ / premium stop never waits silently on one leg without a fresh price - that leg counts at
    its last traded price (`last_traded`: the monitor's last price, else its entry), marked stale on the exit
    (`stale_json`, shown on the chip); a notice goes out once the leg stays stale for QUOTE_MAX_STALE_SECONDS. A spot
    level never needs leg prices."""
    if row.check_mode == gx.CLOSE:
        if candles is None or not row.timeframe_minutes or not row.spot_symbol or not row.spot_exchange:
            return None
        try:
            bars = await candles(row.spot_symbol, row.spot_exchange)
        except Exception as exc:  # noqa: BLE001 - no candles, no decision this pass
            logger.info("Trigger engine: no candles for %s (%s)", row.spot_symbol, type(exc).__name__)
            return None
        tf = int(row.timeframe_minutes)
        closed = trigger_rules.closed_candles(bars, tf, now)
        if closed:
            row.last_value, row.last_checked_at = closed[-1].close, now
        if gx.trailing_on(row):
            return _trailed_close_reading(row, closed, now)
        sl = row.current_sl if row.current_sl is not None else row.sl_value
        for kind, level, side in ((gx.SL, sl, row.sl_side), (gx.TARGET, row.target_value, row.target_side)):
            if level is not None and side and trigger_rules.close_trigger(float(level), side, bars, tf, now,
                                                                          armed_at=row.created_at) is not None:
                return kind
        return None
    if row.measure == gx.SPOT:
        if not row.spot_symbol or not row.spot_exchange:
            return None
        value = await reader.get(row.spot_symbol, row.spot_exchange)
    else:
        booked = {int(k): float(v) for k, v in (exits or {}).items()}
        open_legs = [leg for leg in legs if int(leg["trade_id"]) not in booked]
        prices = {int(leg["trade_id"]): p for leg in open_legs
                  if (p := await reader.get(str(leg["symbol"]), str(leg["exchange"]))) is not None}
        stale: List[Dict[str, Any]] = []
        for leg in open_legs:
            tid = int(leg["trade_id"])
            if tid not in prices and last_traded and tid in last_traded:
                price, seen = last_traded[tid]
                prices[tid] = price
                stale.append({"trade_id": tid, "symbol": leg["symbol"], "price": price,
                              "seen_at": seen.isoformat() if seen else None})
        gx.mark_stale(row, stale, now)
        if row.measure == gx.MTM_INR:                  # a leg closed by hand counts at its booked exit price (CXQ-2)
            value = gx.mtm(legs, {**prices, **booked})
        else:
            value = gx.net_premium(open_legs, prices)
    if value is None:
        return None
    row.last_value, row.last_checked_at = value, now
    gx.apply_trail(row, value, now)                    # CX2: the stop may move in the trade's favour, never back
    return gx.decide(row, value)


def _trailed_close_reading(row: GroupExitRecord, closed: Sequence[trigger_rules.Candle], now: datetime) -> Optional[str]:
    """CX2 on a closed-candle check: each candle closed since the exit was set (or since the last one read, `seen`) is
    judged on the stop in force BEFORE it, then may move the stop - in order, so no candle is judged on a stop a later
    candle moved (no look-ahead), and none is read twice after a restart."""
    since = [at for at in (row.created_at, gx.trail_seen(row)) if at is not None]
    start = max((at if at.tzinfo else at.replace(tzinfo=timezone.utc)) for at in since) if since else None
    for candle in closed:
        if start is not None and candle.end <= start:
            continue
        gx.mark_seen(row, candle.end)
        stop, side = gx.stop_now(row), gx.stop_side(row)
        if stop is not None and side and trigger_rules.closed_beyond(stop, side, candle.close):
            return gx.SL
        if row.target_value is not None and row.target_side and trigger_rules.closed_beyond(
                float(row.target_value), row.target_side, candle.close):
            return gx.TARGET
        gx.apply_trail(row, candle.close, now)
    return None


async def _save_trail(session: AsyncSession, row: GroupExitRecord, before: Tuple[Any, Any, Any]) -> None:
    """Stores what the trailing changed on this reading with conditional UPDATEs, so two passes (the tick pass and the
    cycle) can never move the stored stop back: the best reading and the stop are written only when more favourable
    than what is stored, `locked_at` only once, and only while the exit is still ARMED."""
    from sqlalchemy.orm.attributes import set_committed_value
    fav = gx.favour(row)
    col_rows = GroupExitRecord.__table__.c
    moved = {name: getattr(row, name) for name, old in zip(("best_value", "current_sl"), before)
             if getattr(row, name) is not None and getattr(row, name) != old}
    locked = row.locked_at if row.locked_at is not None and before[2] is None else None
    # Nothing of these is written by the ORM's own flush (an autoflush would write a stale value unconditionally).
    for name, old in zip(("best_value", "current_sl", "locked_at"), before):
        if name in moved or (name == "locked_at" and locked is not None):
            set_committed_value(row, name, old)
    for name, new in moved.items():
        col = col_rows[name]
        ahead = col < new if fav > 0 else col > new
        # F2 (owner): only on the version this pass read - an edit in between (new kind, new numbers, the trailing
        # started again) never gets a stop worked out from the old trailing.
        await session.execute(update(GroupExitRecord).where(
            GroupExitRecord.id == row.id, GroupExitRecord.status == gx.ARMED, GroupExitRecord.version == row.version,
            (col.is_(None)) | ahead).values(
            **{name: new}).execution_options(synchronize_session=False))
    if locked is not None:
        await session.execute(update(GroupExitRecord).where(
            GroupExitRecord.id == row.id, GroupExitRecord.version == row.version, GroupExitRecord.locked_at.is_(None)).values(
            locked_at=locked).execution_options(synchronize_session=False))
    for name, new in [*moved.items(), *([("locked_at", locked)] if locked is not None else [])]:
        set_committed_value(row, name, new)


async def _claim_group(session: AsyncSession, row: GroupExitRecord, kind: str, now: datetime) -> bool:
    """ARMED -> FIRING exactly once (the tick pass and the cycle never fire the same combined exit twice)."""
    claimed = await session.execute(update(GroupExitRecord).where(
        GroupExitRecord.id == row.id, GroupExitRecord.status == gx.ARMED, GroupExitRecord.version == row.version).values(
        status=gx.FIRING, fired_kind=kind, fired_at=now, fired_value=row.last_value, passes_firing=0,
        updated_at=now).execution_options(synchronize_session=False))
    if getattr(claimed, "rowcount", 0) != 1:
        await session.rollback()
        return False
    await session.commit()
    await session.refresh(row)
    gx.add_log(row, gx.FIRING, f"{'Stop-loss' if kind == gx.SL else 'Target'} reached at {row.last_value} - closing the legs", now)
    await session.commit()
    return True


async def _group_notify(session: AsyncSession, row: GroupExitRecord, title: str, message: str, *, critical: bool = False,
                        warning: bool = False, action: Optional[Dict[str, Any]] = None) -> None:
    from app.core.enums import NotificationSeverity, NotificationType
    from app.notifications.service import notify
    try:
        severity = (NotificationSeverity.CRITICAL if critical else
                    NotificationSeverity.WARNING if warning else NotificationSeverity.INFO)
        meta: Dict[str, Any] = {"group_exit": row.id}
        if action:
            meta["action"] = action
        await notify(session, row.tenant_id, NotificationType.SYSTEM_FAILURE if (critical or warning) else NotificationType.EXIT,
                     title=title, message=message, user_id=row.user_id, severity=severity,
                     related_trade_id=(action or {}).get("trade_id"), metadata_json=json.dumps(meta))
    except Exception:  # noqa: BLE001 - a notification never undoes the exit
        logger.exception("Trigger engine: notification for combined exit %s failed", row.id)
        await session.rollback()


async def _close_group_legs(session: AsyncSession, row: GroupExitRecord, reader: _Prices, broker: Optional[BrokerInterface],
                            now: datetime, report: EngineReport) -> None:
    """Close every leg of a FIRING combined exit that is still open - sold legs first, then the hedges (a short that does
    not close keeps its hedges open; close_group alerts). Legs closed already (this pass, an earlier pass, or before a
    restart) are skipped; each leg's own close claims it once, so nothing is closed twice."""
    from app.trading.position_monitor import close_group, venue_for
    trades = [t for t in [await session.get(TradeRecord, i) for i in gx.trade_ids(row)] if t is not None]
    still_open = [t for t in trades if t.exit_time is None]
    if still_open:
        if row.passes_firing == 0:                        # first pass: the legs' own waiting exits make way (SX4)
            for t in still_open:
                await book.cancel_for_trade(session, t.id, "Cancelled: your combined exit is closing this position", now)
            await session.commit()
        prices = {t.id: p for t in still_open if (p := await reader.get(t.symbol, await venue_for(session, t))) is not None}
        for t in still_open:                           # CX-F1: no fresh quote -> the leg's real last traded price (never its entry)
            if t.id not in prices and t.mark_price is not None:
                prices[t.id] = float(t.mark_price)
        spot: Optional[float] = None
        if row.spot_symbol and row.spot_exchange:
            spot = await reader.get(row.spot_symbol, row.spot_exchange)
        reason = "Combined stop-loss" if row.fired_kind == gx.SL else "Combined target"
        outcomes = await close_group(session, still_open, prices, reason, broker=broker, user_id=row.user_id,
                                     underlying_price=spot)
        report.filled += sum(1 for o in outcomes if o.closed)
        for t in still_open:
            await session.refresh(t)
        still_open = [t for t in still_open if t.exit_time is None]
    if not still_open:
        row.status = gx.DONE
        gx.add_log(row, gx.DONE, f"All {len(trades)} legs closed", now)
        await session.commit()
        what = "Combined stop-loss" if row.fired_kind == gx.SL else "Combined target"
        await _group_notify(session, row, f"{what}: {len(trades)} legs closed",
                            f"{gx.describe(row)} It fired at {row.fired_value}; every leg is now closed.")
        return
    row.passes_firing = int(row.passes_firing or 0) + 1
    names = ", ".join(t.symbol for t in still_open)
    gx.add_log(row, gx.FIRING, f"Still open: {names} - tried again next pass", now)
    await session.commit()
    if row.passes_firing == gx.STUCK_ALERT_PASSES:
        short = next((t for t in still_open if gx.is_short(t)), None)
        action = gx.exit_short_action(short) if short is not None else None
        link = f" Exit the short now: {action['url']}" if action else ""
        await _group_notify(session, row, "Combined exit: legs still open",
                            f"Your combined exit fired, but these legs are still open after {row.passes_firing} tries: {names}. "
                            f"They are tried again on every pass (sold legs first). Check the position at the broker.{link}",
                            critical=True, action=action)


async def run_group_exits(session: AsyncSession, tenant_id: int, reader: _Prices, *, candles: Optional[CandleFn] = None,
                          broker: Optional[BrokerInterface] = None, now: Optional[datetime] = None,
                          report: Optional[EngineReport] = None, paper_only: bool = False) -> EngineReport:
    """Every combined exit of the organisation: the exiting ones first (their open legs closed again), then every armed
    one judged on this pass's reading. Never blocked (ADR-0004): no kill switch, limit, approval mode or PAPER lock is
    read here or on the close path."""
    now = now or datetime.now(timezone.utc)
    report = report if report is not None else EngineReport()
    query = select(GroupExitRecord).where(GroupExitRecord.tenant_id == tenant_id, GroupExitRecord.status.in_(gx.LIVE_STATUSES))
    if paper_only:
        query = query.where(GroupExitRecord.mode == "PAPER")
    rows = sorted(await session.scalars(query), key=lambda r: (0 if r.status == gx.FIRING else 1, r.id))
    for row in rows:
        report.checked += 1
        try:
            if row.status == gx.ARMED:
                trades = [t for t in [await session.get(TradeRecord, i) for i in gx.trade_ids(row)] if t is not None]
                if not any(t.exit_time is None for t in trades):
                    row.status = gx.DONE                   # every leg was closed some other way: nothing left to watch
                    gx.add_log(row, gx.DONE, "Every leg is already closed - nothing left to watch", now)
                    await session.commit()
                    continue
                exits = {t.id: float(t.exit_price) for t in trades if t.exit_time is not None and t.exit_price is not None}
                last_traded = {t.id: gx.last_traded(t) for t in trades if t.exit_time is None}
                before = (row.best_value, row.current_sl, row.locked_at)
                kind = await _group_reading(row, gx.legs_of(row), reader, candles, now, exits, last_traded)
                if gx.trailing_on(row):
                    await _save_trail(session, row, before)
                if gx.stale_notice_due(row, now):
                    await _group_notify(session, row, "Combined exit: a leg has no fresh price",
                                        gx.stale_words(row) + " Your combined exit keeps watching on its last traded price.",
                                        warning=True)
                await session.commit()                     # the reading the exit was judged on
                if kind is None or not await _claim_group(session, row, kind, now):
                    continue
            await _close_group_legs(session, row, reader, broker, now, report)
        except Exception as exc:  # noqa: BLE001 - one exit never stops the others; a FIRING one is tried again next pass
            logger.exception("Trigger engine: combined exit %s failed", row.id)
            report.errors.append(f"group {row.id}: {type(exc).__name__}")
            await session.rollback()
    return report

# --- heartbeat and stall alert (TT-6 / TT-26) ----------------------------------------------------------------------------

async def _mark_run(session: AsyncSession, now: datetime, report: EngineReport) -> None:
    global _last_run, _last_beat
    await session.commit()                 # the last prices the waiting orders were judged on
    _last_run = now
    _stall_alerted.clear()
    if report.errors or monotonic.monotonic() - _last_beat >= HEARTBEAT_EVERY_SECONDS:
        try:
            await beat(session, now, error="; ".join(report.errors)[:2000] if report.errors else None)
            _last_beat = monotonic.monotonic()
        except Exception:  # noqa: BLE001 - the heartbeat never stops a pass
            logger.exception("Trigger engine heartbeat failed")
            await session.rollback()


async def beat(session: AsyncSession, now: datetime, *, error: Optional[str] = None) -> None:
    record = await session.scalar(select(WorkerHeartbeatRecord).where(WorkerHeartbeatRecord.worker_name == ENGINE_NAME))
    if record is None:
        record = WorkerHeartbeatRecord(worker_name=ENGINE_NAME, started_at=now, cycle_count=0)
        session.add(record)
    record.last_seen_at = now
    record.cycle_count = (record.cycle_count or 0) + 1
    record.last_error = error
    await session.commit()


async def heartbeat(session: AsyncSession) -> Optional[datetime]:
    """The engine's last-run time from its heartbeat row (any process), for the status bar."""
    record = await session.scalar(select(WorkerHeartbeatRecord).where(WorkerHeartbeatRecord.worker_name == ENGINE_NAME))
    if record is None or record.last_seen_at is None:
        return None
    seen = record.last_seen_at
    return seen if seen.tzinfo else seen.replace(tzinfo=timezone.utc)


async def check_stall(session: AsyncSession, now: Optional[datetime] = None, *, market_open: bool = True) -> int:
    """During market hours, with waiting orders and no engine run for TRIGGER_STALL_SECONDS: every user with waiting
    orders is told once per stall (in the app and on Telegram). Returns how many users were told."""
    from app.core.enums import NotificationSeverity, NotificationType
    from app.notifications.service import notify
    now = now or datetime.now(timezone.utc)
    if not market_open:
        return 0
    seen = await heartbeat(session)
    if seen is not None and (now - seen).total_seconds() <= TRIGGER_STALL_SECONDS:
        return 0
    owners = list(await session.execute(select(PaperRestingOrderRecord.tenant_id, PaperRestingOrderRecord.user_id).where(
        PaperRestingOrderRecord.status == book.RESTING).distinct()))
    told = 0
    since = f" since {seen.astimezone(book.IST).strftime('%H:%M')} IST" if seen is not None else ""
    for tenant_id, user_id in owners:
        if user_id in _stall_alerted:
            continue
        await notify(session, tenant_id, NotificationType.SYSTEM_FAILURE, title=STALL_TITLE,
                     message=(f"The engine that places your waiting orders has not run{since}. They are kept and are checked "
                              "again as soon as it runs. To act now, use Exit on the position or cancel the order."),
                     severity=NotificationSeverity.CRITICAL, user_id=user_id)
        _stall_alerted.add(user_id)
        told += 1
    return told


# --- M55 S3: the LIVE side of the one path (exit session, entry chokepoint) ------------------------------------------------

async def _exit_session(session: AsyncSession, trade: TradeRecord,
                        handed_in: BrokerInterface | None) -> BrokerInterface | None:
    """M55 S3 (A12): the broker session a LIVE resting exit of `trade` goes out at, on the tick pass and on the cycle alike:
    the session handed in, else the session of the broker account that holds the position
    (`position_monitor.broker_for_trade`, the resolution the position monitor and the combined exits use). PAPER: the
    handed-in value, unchanged. None: no usable session - the exit waits and is tried again on the next pass (never
    blocked, ADR-0004). The rule is M55 S5's `exit_broker_for`; this adds only that a failed lookup waits for the next pass.
    """
    try:
        return await exit_broker_for(session, trade, handed_in)
    except Exception:  # noqa: BLE001 - no session this pass: the exit waits and is tried again (never dropped)
        logger.exception("Trigger engine: no broker session for LIVE position %s this pass", trade.id)
        return None


async def _live_entry_refusal(session: AsyncSession, row: PaperRestingOrderRecord) -> str:
    """M55 S3 (A17): why a reached LIVE resting entry is not placed - the order ticket's own chokepoint first (the owner's
    Phase 0 lock, read fresh), else the plain fact that the LIVE manual path is not built yet. Reads, never changes, a
    switch."""
    from app.platform import phase0_lock
    try:
        await phase0_lock.is_on(session, row.tenant_id)
        phase0_lock.assert_paper(row.mode, row.tenant_id)
    except phase0_lock.LiveOrderRefused:
        return LIVE_ENTRY_SWITCHED_OFF
    return "Live manual orders are not switched on: nothing was placed"


# --- the tick pass inside the worker (TT-6) ---------------------------------------------------------------------------

def _tick_prices(ticks: Dict[str, list], now: datetime) -> PriceFn:
    async def lookup(symbol: str, exchange: str) -> float:
        found = ticks.get(f"{exchange.strip().upper()}:{symbol.strip().upper()}")
        if not found:
            raise LookupError("no tick")
        at = datetime.fromisoformat(str(found[1]))
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        if (now - at) > timedelta(seconds=TRIGGER_TICK_MAX_AGE_SECONDS):
            raise LookupError("old tick")
        return float(found[0])
    return lookup


async def tick_pass(session: AsyncSession, now: Optional[datetime] = None) -> EngineReport:
    """One pass on the organisations' live ticks in this process: the touch triggers of every resting order, PAPER and
    LIVE alike (M55 S3, A12: one exit cadence). A LIVE exit goes out at the session of the broker account holding the
    position (`_exit_session`); with no usable session it waits, as on the cycle. Candle-close triggers wait for the
    worker cycle (it holds the candles)."""
    from app.market_data import hub
    now = now or datetime.now(timezone.utc)
    total = EngineReport()
    paused: bool | None = None             # M55 A18: Redis read once per pass, not once per organisation
    for tenant_id in sorted(await tenants_with_resting(session)):
        ticks = hub.live_ticks(hub.org_namespace(tenant_id))
        if not ticks:
            continue
        prices = _tick_prices(ticks, now)
        exits = await run_exits(session, tenant_id, prices, now=now)      # exits first, never paused (M55 A18)
        if paused is None:
            paused = await entries_paused()
        for report in (exits, await run_entries(session, tenant_id, prices, now=now, paused=paused)):
            total.checked += report.checked
            total.filled += report.filled
            total.rejected += report.rejected
            total.errors.extend(report.errors)
    return total


async def tick_loop(session_factory: Callable[[], Any], stop: asyncio.Event, seconds: float = TRIGGER_TICK_SECONDS) -> None:
    """The worker's background tick pass (TRIGGER_TICK_SECONDS; 0 turns it off - the worker cycle still runs the engine)."""
    if seconds <= 0:
        return
    while not stop.is_set():
        try:
            async with session_factory() as session:
                await tick_pass(session)
        except Exception:  # noqa: BLE001 - the loop survives anything; the cycle pass still runs
            logger.exception("Trigger engine tick pass failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
