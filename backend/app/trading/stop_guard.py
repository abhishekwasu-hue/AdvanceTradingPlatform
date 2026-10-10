"""Phase P1 / section 52: the broker-side protective stop is the backstop that survives a platform
outage - so it must actually exist for every open LIVE position, all day.

At entry the router places an SL-M on the opposite side (Phase A4). That order can be missing
(placement failed at entry: `sl_order_id` is NULL), cancelled by hand at the broker, rejected on
margin, or lost across a broker session. `verify_protective_stops` reads the order book once per
tenant and re-arms whatever is not standing, then tells the user what it did. It never touches a
position whose stop is OPEN/TRIGGER PENDING or already filled (the position monitor books that
exit), and it skips multi-leg structures (their legs are protected as a group by the monitor).

Runs on worker start-up (after reconciliation) and once per cycle per tenant with a LIVE broker.
"""
import logging
import time
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.products import product_for_trade
from app.audit.log import write_audit_log
from app.brokers.base import BrokerInterface, round_stop_trigger
from app.brokers.exceptions import is_clear_rejection
from app.core.enums import ExecutionMode, NotificationSeverity, NotificationType, OrderSide
from app.db.models import Tenant, TradeRecord
from app.execution.tagging import LEG_STOP, build_order_tag
from app.market_data.calendar import market_session_status
from app.notifications.service import notify
from app.reconciliation.service import broker_uncertain_reason
from app.core import config
from app.trading import stop_state
from app.trading.position_monitor import _find_order, close_position, exchange_for_trade

logger = logging.getLogger(__name__)

# Statuses under which a stop order is still protecting the position.
_STANDING = {"OPEN", "PENDING", "TRIGGER PENDING", "TRIGGER_PENDING", "PUT ORDER REQ RECEIVED", "VALIDATION PENDING", "OPEN PENDING",
             "MODIFY PENDING", "AFTER MARKET ORDER REQ RECEIVED"}
_FILLED = {"COMPLETE", "COMPLETED", "FILLED", "TRADED", "EXECUTED"}
_REJECTED = {"REJECTED"}
ALERT_COOLDOWN_SECONDS = 1800
MAX_EXIT_ATTEMPTS = 3


def _failure_alert_due(trade_id: int, table: Optional[Dict[int, float]] = None) -> bool:
    table = stop_state.last_failure_alert if table is None else table
    last = table.get(trade_id)
    if last is not None and time.monotonic() - last <= ALERT_COOLDOWN_SECONDS:
        return False
    table[trade_id] = time.monotonic()
    return True


def _note_rearm_rejected(trade_id: int, order_id: Optional[str]) -> int:
    """A stop the guard re-armed was accepted and then REJECTED by the broker: one more in a row for this trade.
    Only the guard's own latest stop counts (the entry stop's rejection is the first re-arm's reason, not a repeat)."""
    if order_id is None or stop_state.rearmed_order.get(trade_id) != order_id:
        return stop_state.rearm_rejects.get(trade_id, 0)
    stop_state.rearmed_order.pop(trade_id, None)
    stop_state.rearm_rejects[trade_id] = stop_state.rearm_rejects.get(trade_id, 0) + 1
    return stop_state.rearm_rejects[trade_id]


async def exit_unprotected(session: AsyncSession, trade: TradeRecord, broker: BrokerInterface, why: str, *,
                           rejected: bool, broker_uncertain: bool = False, alert: bool = True) -> Optional[bool]:
    """G-LIVE (LIVE_EXIT_IF_NO_STOP): a LIVE position the broker clearly refused a protective stop for is closed at once
    with a market exit (exits are never blocked, ADR-0004). True = closed; False = the exit was tried and failed;
    None = not tried, the software stop keeps watching (and the caller's own alert stands):

    - the broker did not clearly reject the stop (timeout, 5xx, rate limit): it may be standing at the exchange, and a
      market exit next to a live stop could later open a position the other way;
    - the tenant is broker-uncertain (what the broker holds is unknown until reconciliation passes);
    - the exchange session is closed (a market order would only be queued or refused);
    - MAX_EXIT_ATTEMPTS immediate exits already failed for this position (a clear refusal each time - an exit that
      errored without a clear answer flags the tenant broker-uncertain, which stops further tries until
      reconciliation passes). The count lives in the worker's memory: a restart starts it again."""
    if not rejected:
        logger.warning("No-stop exit skipped for trade %s: the stop was not clearly rejected (%s)", trade.id, why)
        return None
    tenant = await session.get(Tenant, trade.tenant_id)
    if broker_uncertain or (tenant is not None and broker_uncertain_reason(tenant)):
        logger.warning("No-stop exit skipped for trade %s: tenant is broker-uncertain (%s)", trade.id, why)
        return None
    exchange = exchange_for_trade(trade)
    market = await market_session_status(session, exchange=exchange)
    if not market.is_open:
        logger.warning("No-stop exit skipped for trade %s: %s", trade.id, market.reason)
        return None
    if stop_state.exit_attempts.get(trade.id, 0) >= MAX_EXIT_ATTEMPTS:
        return None
    stop_state.exit_attempts[trade.id] = stop_state.exit_attempts.get(trade.id, 0) + 1
    try:
        price = float(await broker.get_ltp_for_symbol(trade.symbol, exchange))
    except Exception:  # noqa: BLE001 - the reference price only seeds the booking; the broker fill wins
        price = float(trade.entry_price)
    outcome = await close_position(session, trade, price, f"No protective stop - closed at once ({why})", broker=broker,
                                   stop_dead=True)
    if alert and (outcome.closed or _failure_alert_due(trade.id)):
        left = MAX_EXIT_ATTEMPTS - stop_state.exit_attempts.get(trade.id, 0)
        await notify(session, trade.tenant_id, NotificationType.SYSTEM_FAILURE,
                     title=f"{'Closed' if outcome.closed else 'Could NOT close'} {trade.symbol}: no broker-side stop",
                     message=(f"Position #{trade.id}: {why}. " + ("Closed with a market exit (LIVE_EXIT_IF_NO_STOP)." if outcome.closed
                              else "The market exit failed too: " + "; ".join(outcome.warnings)
                              + f" - close it at the broker by hand ({left} automatic attempt(s) left).")),
                     severity=NotificationSeverity.CRITICAL, related_trade_id=trade.id)
    if not outcome.closed:
        logger.error("No-stop exit failed for trade %s: %s", trade.id, "; ".join(outcome.warnings))
    return outcome.closed


async def _give_up_rearming(session: AsyncSession, tenant: Tenant, trade: TradeRecord, broker: BrokerInterface, streak: int,
                            counts: Dict[str, int]) -> bool:
    """STOP_REARM_MAX_REJECTS re-armed stops in a row were accepted and then rejected: re-arming again would only repeat
    it forever. With LIVE_EXIT_IF_NO_STOP the position is closed (a clear rejection); otherwise - or when that exit is
    not tried - the guard stops re-arming, the software stop keeps watching (its exit skips the rejected stop) and the
    user gets ONE CRITICAL for the position.
    True = the exit path handled it (closed, or tried and alerted)."""
    why = f"the broker rejected {streak} re-armed stops in a row (last {trade.sl_order_id})"
    if config.LIVE_EXIT_IF_NO_STOP:
        try:
            done = await exit_unprotected(session, trade, broker, why, rejected=True)
        except Exception as exit_exc:  # noqa: BLE001 - never lose the rest of the guard pass; the alert below follows
            logger.error("No-stop exit errored for trade %s: %s", trade.id, exit_exc)
            done = None
        if done:
            counts["closed"] += 1
        if done is not None:
            return True
    if trade.id not in stop_state.gave_up_alert:         # once per position: the guard will not try again
        stop_state.gave_up_alert[trade.id] = time.monotonic()
        await notify(session, tenant.id, NotificationType.SYSTEM_FAILURE, title=f"Stop keeps being rejected on {trade.symbol}",
                     message=f"Position #{trade.id}: {why}. The guard has stopped re-arming it; the software stop still monitors "
                             "it every cycle. Close it at the broker by hand or fix the rejection reason (margin, price band).",
                     severity=NotificationSeverity.CRITICAL, related_trade_id=trade.id)
    return False


async def verify_protective_stops(
    session: AsyncSession, tenant: Tenant, broker: BrokerInterface, *, user_id: Optional[int] = None, source: str = "worker",
    product: str = "MIS", account_id: Optional[int] = None, include_unassigned: bool = True,
) -> Dict[str, int]:
    """Re-arms missing/cancelled/rejected stops for the tenant's open LIVE single-leg trades.
    Returns counts: checked, standing, rearmed, filled_pending, failed (+ closed with LIVE_EXIT_IF_NO_STOP).

    Phase T: `account_id` scopes the check to the trades that sit in that broker account (the
    `broker` must be that account's session); `include_unassigned` also takes trades recorded
    before accounts were tracked. A stop must never be re-armed in a different account from
    the position it protects."""
    counts = {"checked": 0, "standing": 0, "rearmed": 0, "filled_pending": 0, "failed": 0}
    if config.LIVE_EXIT_IF_NO_STOP:
        counts["closed"] = 0
    query = select(TradeRecord).where(
        TradeRecord.tenant_id == tenant.id, TradeRecord.exit_time.is_(None), TradeRecord.mode == ExecutionMode.LIVE.value,
        TradeRecord.leg_group_id.is_(None))
    if account_id is not None:
        from sqlalchemy import or_
        scope = TradeRecord.broker_account_id == account_id
        if include_unassigned:
            scope = or_(scope, TradeRecord.broker_account_id.is_(None))
        query = query.where(scope)
    trades: List[TradeRecord] = list(await session.scalars(query))
    if not trades:
        return counts
    try:
        book = {o.order_id: o for o in await broker.get_order_book()}
    except Exception as exc:  # noqa: BLE001 - cannot judge without the book; try again next cycle
        logger.warning("Stop guard: order book unavailable for tenant %s: %s", tenant.id, exc)
        return counts
    for trade in trades:
        counts["checked"] += 1
        order = book.get(trade.sl_order_id) if trade.sl_order_id else None
        status = (order.status or "").upper() if order is not None else ""
        if status in _STANDING:
            counts["standing"] += 1
            if stop_state.rearmed_order.get(trade.id) == trade.sl_order_id:
                # The guard's own stop survived a whole cycle: it was accepted, the streak is over.
                stop_state.rearm_rejects.pop(trade.id, None)
                stop_state.rearmed_order.pop(trade.id, None)
            continue
        if status in _FILLED:
            counts["filled_pending"] += 1   # the exchange closed us; the monitor books it this cycle
            continue
        # Missing, cancelled or rejected. A re-armed stop the broker accepted and then rejected counts against the trade;
        # any other reason (cancelled by hand, lost across a session) breaks the streak.
        if status in _REJECTED:
            streak = _note_rearm_rejected(trade.id, trade.sl_order_id)
        else:
            stop_state.rearm_rejects.pop(trade.id, None)
            stop_state.rearmed_order.pop(trade.id, None)
            streak = 0
        if streak >= config.STOP_REARM_MAX_REJECTS:
            counts["failed"] += 1
            await _give_up_rearming(session, tenant, trade, broker, streak, counts)
            continue
        # Re-arm.
        side = OrderSide.SELL if trade.direction == "LONG" else OrderSide.BUY
        tag = build_order_tag(strategy_id=trade.strategy_id, leg=LEG_STOP, algo_id=tenant.algo_id)
        previous = trade.sl_order_id
        trigger = round_stop_trigger(float(trade.stop_loss), side, symbol=trade.symbol, exchange=exchange_for_trade(trade))   # on the tick, away from the market
        try:
            response = await broker.place_stop_loss_order(trade.symbol, exchange_for_trade(trade), side, trade.quantity,
                                                          trigger_price=trigger,
                                                          product=product_for_trade(trade) if (getattr(trade, "holding", None) or "INTRADAY") == "SWING" else product,
                                                          tag=tag)
        except Exception as exc:  # noqa: BLE001 - alert, keep the software stop, try again next cycle
            counts["failed"] += 1
            logger.error("Stop guard: could not re-arm stop for trade %s: %s", trade.id, exc)
            if config.LIVE_EXIT_IF_NO_STOP:
                try:
                    done = await exit_unprotected(session, trade, broker, f"stop re-arm failed: {exc}",
                                                  rejected=is_clear_rejection(exc))
                except Exception as exit_exc:  # noqa: BLE001 - never lose the rest of the guard pass; today's alert follows
                    logger.error("No-stop exit errored for trade %s: %s", trade.id, exit_exc)
                    done = None
                if done:
                    counts["closed"] += 1
                if done is not None:      # tried: exit_unprotected already alerted (or is inside its cooldown)
                    continue
            if _failure_alert_due(trade.id):
                await notify(session, tenant.id, NotificationType.SYSTEM_FAILURE, title=f"No broker-side stop on {trade.symbol}",
                             message=f"Position #{trade.id} has no standing protective stop and re-placing it failed: {exc}. "
                                     "The software stop still monitors it every cycle; consider closing it by hand.",
                             severity=NotificationSeverity.CRITICAL, related_trade_id=trade.id)
            continue
        trade.sl_order_id = response.order_id
        stop_state.rearmed_order[trade.id] = response.order_id
        reason = "no stop order on record" if previous is None else f"stop {previous} was {(order.status if order else 'missing at the broker')}"
        await write_audit_log(session, tenant.id, user_id, "protective_stop_rearmed",
                              f"trade {trade.id} {trade.symbol}: {reason}; new stop {response.order_id} @ {trigger:g} ({source})")
        # One look at the new stop: a broker can take it and reject it a moment later (RMS / margin).
        placed = await _find_order(broker, response.order_id)
        if placed is not None and (placed.status or "").upper() in _REJECTED:
            counts["failed"] += 1
            streak = _note_rearm_rejected(trade.id, response.order_id)
            logger.error("Stop guard: re-armed stop %s for trade %s was rejected (%s in a row): %s", response.order_id, trade.id,
                         streak, placed.status)
            if streak >= config.STOP_REARM_MAX_REJECTS:
                await _give_up_rearming(session, tenant, trade, broker, streak, counts)
            elif _failure_alert_due(trade.id):
                await notify(session, tenant.id, NotificationType.SYSTEM_FAILURE, title=f"Re-armed stop rejected on {trade.symbol}",
                             message=f"Position #{trade.id}: the broker accepted the new stop {response.order_id} and then rejected it "
                                     f"({placed.status}). Rejection {streak} of {config.STOP_REARM_MAX_REJECTS} before "
                                     "the guard stops re-arming; the software stop still monitors it.",
                             severity=NotificationSeverity.CRITICAL, related_trade_id=trade.id)
            continue
        counts["rearmed"] += 1
        await notify(session, tenant.id, NotificationType.RISK_REJECTION, title=f"Protective stop re-armed on {trade.symbol}",
                     message=f"Position #{trade.id}: {reason}. A new SL-M at {trigger:g} was placed ({response.order_id}).",
                     severity=NotificationSeverity.WARNING, related_trade_id=trade.id)
        logger.warning("Stop guard: re-armed stop for trade %s (%s)", trade.id, reason)
    await session.commit()
    return counts
