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
from app.brokers.base import BrokerInterface
from app.brokers.exceptions import is_clear_rejection
from app.core.enums import ExecutionMode, NotificationSeverity, NotificationType, OrderSide
from app.db.models import Tenant, TradeRecord
from app.execution.tagging import LEG_STOP, build_order_tag
from app.market_data.calendar import market_session_status
from app.notifications.service import notify
from app.reconciliation.service import broker_uncertain_reason
from app.core import config
from app.trading.position_monitor import close_position, exchange_for_trade

logger = logging.getLogger(__name__)

# Statuses under which a stop order is still protecting the position.
_STANDING = {"OPEN", "PENDING", "TRIGGER PENDING", "TRIGGER_PENDING", "PUT ORDER REQ RECEIVED", "VALIDATION PENDING", "OPEN PENDING",
             "MODIFY PENDING", "AFTER MARKET ORDER REQ RECEIVED"}
_FILLED = {"COMPLETE", "COMPLETED", "FILLED", "TRADED", "EXECUTED"}
ALERT_COOLDOWN_SECONDS = 1800

_last_failure_alert: Dict[int, float] = {}   # trade id -> monotonic time of the last CRITICAL; absent = never alerted


MAX_EXIT_ATTEMPTS = 3
_exit_attempts: Dict[int, int] = {}          # trade id -> immediate exits tried (LIVE_EXIT_IF_NO_STOP)


def _failure_alert_due(trade_id: int) -> bool:
    last = _last_failure_alert.get(trade_id)
    if last is not None and time.monotonic() - last <= ALERT_COOLDOWN_SECONDS:
        return False
    _last_failure_alert[trade_id] = time.monotonic()
    return True


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
    if _exit_attempts.get(trade.id, 0) >= MAX_EXIT_ATTEMPTS:
        return None
    _exit_attempts[trade.id] = _exit_attempts.get(trade.id, 0) + 1
    try:
        price = float(await broker.get_ltp_for_symbol(trade.symbol, exchange))
    except Exception:  # noqa: BLE001 - the reference price only seeds the booking; the broker fill wins
        price = float(trade.entry_price)
    outcome = await close_position(session, trade, price, f"No protective stop - closed at once ({why})", broker=broker,
                                   stop_dead=True)
    if outcome.closed:
        _exit_attempts.pop(trade.id, None)
        _last_failure_alert.pop(trade.id, None)
    if alert and (outcome.closed or _failure_alert_due(trade.id)):
        left = MAX_EXIT_ATTEMPTS - _exit_attempts.get(trade.id, 0)
        await notify(session, trade.tenant_id, NotificationType.SYSTEM_FAILURE,
                     title=f"{'Closed' if outcome.closed else 'Could NOT close'} {trade.symbol}: no broker-side stop",
                     message=(f"Position #{trade.id}: {why}. " + ("Closed with a market exit (LIVE_EXIT_IF_NO_STOP)." if outcome.closed
                              else "The market exit failed too: " + "; ".join(outcome.warnings)
                              + f" - close it at the broker by hand ({left} automatic attempt(s) left).")),
                     severity=NotificationSeverity.CRITICAL, related_trade_id=trade.id)
    if not outcome.closed:
        logger.error("No-stop exit failed for trade %s: %s", trade.id, "; ".join(outcome.warnings))
    return outcome.closed


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
        if order is not None:
            status = (order.status or "").upper()
            if status in _STANDING:
                counts["standing"] += 1
                continue
            if status in _FILLED:
                counts["filled_pending"] += 1   # the exchange closed us; the monitor books it this cycle
                continue
        # Missing, cancelled or rejected: re-arm.
        side = OrderSide.SELL if trade.direction == "LONG" else OrderSide.BUY
        tag = build_order_tag(strategy_id=trade.strategy_id, leg=LEG_STOP, algo_id=tenant.algo_id)
        previous = trade.sl_order_id
        try:
            response = await broker.place_stop_loss_order(trade.symbol, exchange_for_trade(trade), side, trade.quantity,
                                                          trigger_price=float(trade.stop_loss),
                                                          product=product_for_trade(trade) if (getattr(trade, "holding", None) or "INTRADAY") == "SWING" else product,
                                                          tag=tag)
            trade.sl_order_id = response.order_id
            counts["rearmed"] += 1
            reason = "no stop order on record" if previous is None else f"stop {previous} was {(order.status if order else 'missing at the broker')}"
            await write_audit_log(session, tenant.id, user_id, "protective_stop_rearmed",
                                  f"trade {trade.id} {trade.symbol}: {reason}; new stop {response.order_id} @ {trade.stop_loss} ({source})")
            await notify(session, tenant.id, NotificationType.RISK_REJECTION, title=f"Protective stop re-armed on {trade.symbol}",
                         message=f"Position #{trade.id}: {reason}. A new SL-M at {trade.stop_loss:g} was placed ({response.order_id}).",
                         severity=NotificationSeverity.WARNING, related_trade_id=trade.id)
            logger.warning("Stop guard: re-armed stop for trade %s (%s)", trade.id, reason)
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
    await session.commit()
    return counts
