"""One close path for every open position, paper or live, whoever triggers it.

Before this module there were three separate hand-written "close the trade" blocks (the manual
mark-price endpoint, the emergency-exit endpoint, and the backtest simulator) that each computed
P&L and charges on their own. Now the autonomous worker needs a fourth - and LIVE positions need
real exit orders at the broker, not just a row update. So this is the single implementation:

* `close_position` - marks a TradeRecord closed at an exit price with P&L/charges and an EXIT
  notification. For a LIVE trade it first squares off at the broker: on a target exit it cancels
  the protective SL-M and places a market exit; on a stop-loss exit it checks whether the broker-
  side stop already fired (then there is nothing to place - placing another order would open a
  reverse position) and only sends a market exit if the stop is still open.
* `monitor_open_positions` - the per-cycle sweep the worker runs: LTP per open position ->
  `check_exit` -> `close_position`. One position's failure never stops the sweep.
* `broker_for_trade` - which broker session squares off a given trade (via its deployment).

Charges on LIVE trades are the same NSE-intraday estimate paper trading uses; the broker's own
contract note is the authoritative figure and reconciliation is where that gets trued up.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface
from app.brokers.models import BrokerOrderRequest, BrokerOrderStatus
from app.brokers.token_lifecycle import build_adapter, get_credential_record, token_is_usable
from app.core.enums import ExecutionMode, NotificationSeverity, NotificationType, OrderSide
from app.db.models import BrokerCredentialRecord, StrategyDeploymentRecord, Tenant, TradeRecord
from app.execution.tagging import LEG_EXIT, build_order_tag
from app.execution.paper_broker import PaperBroker
from app.instruments.registry import get_contract_spec
from app.notifications.service import notify
from app.trading.exit_logic import check_exit

logger = logging.getLogger(__name__)

PriceLookup = Callable[[str, str], Awaitable[float]]

# Broker order-book statuses that mean "this order has filled" across Kite/Upstox/Noren wording.
_FILLED_STATUSES = {"COMPLETE", "COMPLETED", "FILLED", "TRADED", "EXECUTED"}

FILL_POLL_ATTEMPTS = 3
FILL_POLL_DELAY_SECONDS = 0.5


@dataclass
class CloseOutcome:
    trade_id: int
    closed: bool
    exit_reason: Optional[str] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    broker_exit_order_id: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


def exchange_for_symbol(symbol: str) -> str:
    spec = get_contract_spec(symbol)
    return spec.exchange if spec else "NSE"


def exchange_for_trade(trade: TradeRecord) -> str:
    """Derived-contract trades (Phase F3) carry their own exchange (NFO/BFO); older rows fall
    back to the symbol registry."""
    return trade.exchange or exchange_for_symbol(trade.symbol)


async def broker_for_trade(session: AsyncSession, trade: TradeRecord) -> Optional[BrokerInterface]:
    """The authenticated adapter that can square off this LIVE trade: the broker its deployment
    trades through, or - for a trade entered by hand - the tenant's single stored broker. None
    when there is no usable (VALID, unexpired) token, so callers never fire an exit order that the
    broker would reject anyway."""
    if trade.mode != ExecutionMode.LIVE.value:
        return None
    record: Optional[BrokerCredentialRecord] = None
    if trade.deployment_id is not None:
        deployment = await session.get(StrategyDeploymentRecord, trade.deployment_id)
        if deployment is not None and deployment.broker_name:
            record = await get_credential_record(session, trade.tenant_id, deployment.broker_name)
    if record is None:
        records = list(await session.scalars(
            select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == trade.tenant_id)
        ))
        record = records[0] if len(records) == 1 else None
    if record is None or not token_is_usable(record):
        return None
    return build_adapter(record)


async def _find_order(broker: BrokerInterface, order_id: str) -> Optional[BrokerOrderStatus]:
    try:
        book = await broker.get_order_book()
    except Exception as exc:  # noqa: BLE001 - informational, callers decide the fallback
        logger.debug("Order book unavailable while resolving %s: %s", order_id, exc)
        return None
    return next((o for o in book if o.order_id == order_id), None)


async def _fill_price(broker: BrokerInterface, order_id: str, fallback: float) -> float:
    for attempt in range(FILL_POLL_ATTEMPTS):
        order = await _find_order(broker, order_id)
        if order is not None and order.average_price and order.filled_quantity > 0:
            return float(order.average_price)
        if attempt < FILL_POLL_ATTEMPTS - 1:
            await asyncio.sleep(FILL_POLL_DELAY_SECONDS)
    return fallback


def _stop_already_filled(trade: TradeRecord, sl_order: BrokerOrderStatus, reason: str, outcome: CloseOutcome) -> float:
    """The broker-side stop did the exit for us: record it as a Stop Loss at the stop's own fill,
    whatever level the software check happened to see first."""
    outcome.broker_exit_order_id = trade.sl_order_id
    outcome.exit_reason = "Stop Loss"
    if reason != "Stop Loss":
        outcome.warnings.append(f"Broker stop {trade.sl_order_id} had already filled; recorded as Stop Loss")
    return float(sl_order.average_price) if sl_order.average_price else trade.stop_loss


async def _square_off_live(
    trade: TradeRecord, broker: BrokerInterface, reason: str, reference_price: float, outcome: CloseOutcome,
    algo_id: Optional[str] = None,
) -> Optional[float]:
    """Places whatever the broker needs to flatten this position and returns the realised exit
    price, or None when the position could not be flattened (the trade must then stay open)."""
    exchange = exchange_for_trade(trade)
    exit_side = OrderSide.SELL if trade.direction == "LONG" else OrderSide.BUY

    if trade.sl_order_id:
        sl_order = await _find_order(broker, trade.sl_order_id)
        if sl_order is not None and sl_order.status.upper() in _FILLED_STATUSES:
            # The exchange already closed us at the stop. Nothing to place - a second exit order
            # here would open a fresh position in the opposite direction.
            return _stop_already_filled(trade, sl_order, reason, outcome)
        try:
            await broker.cancel_order(trade.sl_order_id)
        except Exception as exc:  # noqa: BLE001
            # Cancel can legitimately fail because the stop filled between our book read and now.
            recheck = await _find_order(broker, trade.sl_order_id)
            if recheck is not None and recheck.status.upper() in _FILLED_STATUSES:
                return _stop_already_filled(trade, recheck, reason, outcome)
            outcome.warnings.append(f"Could not cancel protective stop {trade.sl_order_id}: {exc}")
            logger.error("Cancel of SL %s failed for trade %s: %s", trade.sl_order_id, trade.id, exc)
            return None

    try:
        response = await broker.place_order(BrokerOrderRequest(
            symbol=trade.symbol, exchange=exchange, transaction_type=exit_side, quantity=trade.quantity,
            order_type="MARKET", product="MIS",
            tag=build_order_tag(strategy_id=trade.strategy_id, leg=LEG_EXIT, algo_id=algo_id,
                                max_length=getattr(broker, "max_tag_length", None) or 20),
        ))
    except Exception as exc:  # noqa: BLE001
        outcome.warnings.append(f"Exit order failed at {broker.name}: {exc}")
        logger.error("Exit order failed for trade %s: %s", trade.id, exc)
        return None
    if response.status.upper() in ("REJECTED", "CANCELLED"):
        outcome.warnings.append(f"Exit order rejected by {broker.name}: {response.message or response.status}")
        return None
    outcome.broker_exit_order_id = response.order_id
    return await _fill_price(broker, response.order_id, fallback=reference_price)


async def close_position(
    session: AsyncSession, trade: TradeRecord, exit_price: float, reason: str, *,
    broker: Optional[BrokerInterface] = None, user_id: Optional[int] = None, now: Optional[datetime] = None,
) -> CloseOutcome:
    """Closes one open position. PAPER: books the exit at `exit_price`. LIVE: squares off at the
    broker first (see module docstring) and books the realised fill; if the broker leg fails the
    trade stays open and the outcome says why - a LIVE row is never marked closed while the
    position may still exist at the exchange. Commits on success and raises an EXIT notification."""
    outcome = CloseOutcome(trade_id=trade.id, closed=False)
    if trade.exit_time is not None:
        outcome.warnings.append("Position is already closed")
        return outcome

    realised_price = exit_price
    if trade.mode == ExecutionMode.LIVE.value:
        if broker is None:
            outcome.warnings.append("LIVE position needs an authenticated broker session to exit - left open")
            return outcome
        tenant = await session.get(Tenant, trade.tenant_id)
        realised = await _square_off_live(
            trade, broker, reason, exit_price, outcome, algo_id=tenant.algo_id if tenant is not None else None,
        )
        if realised is None:
            return outcome
        realised_price = realised
        reason = outcome.exit_reason or reason

    direction_sign = 1 if trade.direction == "LONG" else -1
    gross_pnl = direction_sign * (realised_price - trade.entry_price) * trade.quantity
    charges = PaperBroker().estimate_round_trip_costs(trade.entry_price, realised_price, trade.quantity)
    trade.exit_price = round(realised_price, 2)
    trade.exit_time = now or datetime.now(timezone.utc)
    trade.exit_reason = reason
    trade.charges = charges
    trade.charges_source = "ESTIMATED"
    trade.pnl = round(gross_pnl - charges, 2)
    if outcome.broker_exit_order_id:
        trade.exit_order_id = outcome.broker_exit_order_id
    await session.commit()

    outcome.closed = True
    outcome.exit_reason = reason
    outcome.exit_price = trade.exit_price
    outcome.pnl = trade.pnl
    logger.info("Trade %s closed: %s at %s, P&L %s (%s)", trade.id, reason, trade.exit_price, trade.pnl, trade.mode)

    detail = f"{reason} at {trade.exit_price}, P&L {trade.pnl}"
    if outcome.warnings:
        detail += " | " + "; ".join(outcome.warnings)
    await notify(
        session, trade.tenant_id, NotificationType.EXIT,
        title=f"{trade.direction} position closed: {trade.symbol}", message=detail,
        severity=NotificationSeverity.WARNING if trade.pnl is not None and trade.pnl < 0 else NotificationSeverity.INFO,
        user_id=user_id, related_trade_id=trade.id,
    )
    return outcome


async def monitor_open_positions(
    session: AsyncSession, tenant_id: int, price_lookup: PriceLookup, *,
    broker: Optional[BrokerInterface] = None, user_id: Optional[int] = None,
) -> List[CloseOutcome]:
    """The worker's per-cycle sweep for one tenant: current price for every open position, close
    the ones whose stop/target has been crossed. `price_lookup(symbol, exchange)` is normally
    MarketDataService.get_ltp. A failure on one position (quote unavailable, exit order error) is
    recorded and the sweep continues - the other positions still need watching."""
    open_trades = list(await session.scalars(
        select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))
        .order_by(TradeRecord.id)
    ))
    outcomes: List[CloseOutcome] = []
    for trade in open_trades:
        try:
            price = await price_lookup(trade.symbol, exchange_for_trade(trade))
        except Exception as exc:  # noqa: BLE001 - one bad quote must not stop the sweep
            logger.warning("No price for %s while monitoring trade %s: %s", trade.symbol, trade.id, exc)
            outcomes.append(CloseOutcome(trade_id=trade.id, closed=False, warnings=[f"Price unavailable: {exc}"]))
            continue
        hit = check_exit(trade, price)
        if hit is None:
            continue
        reason, exit_price = hit
        trade_broker = broker if trade.mode == ExecutionMode.LIVE.value else None
        if trade.mode == ExecutionMode.LIVE.value and trade_broker is None:
            trade_broker = await broker_for_trade(session, trade)
        outcomes.append(await close_position(session, trade, exit_price, reason, broker=trade_broker, user_id=user_id))
    return outcomes
