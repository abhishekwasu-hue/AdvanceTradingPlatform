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
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Awaitable, Callable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.products import product_for_trade
from app.brokers.base import BrokerInterface
from app.brokers.models import BrokerOrderRequest, BrokerOrderStatus
from app.brokers.token_lifecycle import build_adapter, get_credential_record, token_is_usable
from app.core.enums import ExecutionMode, NotificationSeverity, NotificationType, OrderSide
from app.db.models import BrokerCredentialRecord, StrategyDeploymentRecord, Tenant, TradeRecord
from app.execution.tagging import LEG_EXIT, build_order_tag
from app.execution.paper_broker import PaperBroker
from app.instruments.registry import get_contract_spec
from app.notifications.service import notify
from app.trading.exit_rules import ExitRules, apply_exit_rules
from app.trading.exit_logic import check_contract_exit
from app.instruments.master import INDEX_EXCHANGE, underlying_of

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


def underlying_exchange(symbol: str) -> str:
    """Where an underlying's own quote comes from: NSE for stocks and NIFTY indices, BSE for
    SENSEX/BANKEX."""
    return INDEX_EXCHANGE.get(underlying_of(symbol), "NSE")


def exchange_for_trade(trade: TradeRecord) -> str:
    """Derived-contract trades (Phase F3) carry their own exchange (NFO/BFO); older rows fall
    back to the symbol registry."""
    return trade.exchange or exchange_for_symbol(trade.symbol)


async def broker_for_trade(session: AsyncSession, trade: TradeRecord) -> Optional[BrokerInterface]:
    """The authenticated adapter that can square off this LIVE trade: the broker account the
    position sits in (Phase T), else the broker its deployment trades through, else - for a trade
    entered by hand - the tenant's single stored broker. None
    when there is no usable (VALID, unexpired) token, so callers never fire an exit order that the
    broker would reject anyway."""
    if trade.mode != ExecutionMode.LIVE.value:
        return None
    record: Optional[BrokerCredentialRecord] = None
    if getattr(trade, "broker_account_id", None) is not None:
        # Phase T: the position sits in a specific broker account; its exit goes there, never to
        # whichever session the tenant happens to hold first.
        from app.accounts.service import credential_for_account, get_account
        account = await get_account(session, trade.tenant_id, trade.broker_account_id)
        if account is not None:
            record = await credential_for_account(session, account)
    if record is None and trade.deployment_id is not None:
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
    # One adapter per credential per session (the worker's cycle): the monitor, the square-off
    # and the group exits all resolve through here, so a tenant with many positions in one
    # account does not build a client per position.
    cache: Dict[int, BrokerInterface] = session.info.setdefault("trade_brokers", {})
    adapter = cache.get(record.id)
    if adapter is None:
        adapter = cache[record.id] = build_adapter(record)
    return adapter


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
            order_type="MARKET", product=product_for_trade(trade),   # Phase AS: the entry's own product
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
    charges = PaperBroker().estimate_round_trip_costs(trade.entry_price, realised_price, trade.quantity, trade.instrument_kind or "UNDERLYING")
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


def group_exit(legs: List[TradeRecord], prices: Dict[int, float], underlying_price: Optional[float]) -> Optional[str]:
    """Why a multi-leg group should close now, or None. `prices` maps trade id -> leg price.
    The group's value is what it costs to close it: buy the shorts back, sell the wings."""
    meta = json.loads(legs[0].group_meta or "{}")
    # Phase U: legs may carry ratios (1:2); each is weighted by its quantity against the 1x leg's.
    base = float(meta.get("quantity") or 0.0)

    def weight(leg: TradeRecord) -> float:
        return (float(leg.quantity) / base) if base > 0 and leg.quantity else 1.0

    value = sum((prices[l.id] if l.leg_role == "SHORT" else -prices[l.id]) * weight(l) for l in legs)
    return structure_exit_reason(meta, value, underlying_price)


def structure_exit_reason(meta: dict, value: float, underlying_price: Optional[float]) -> Optional[str]:
    """The group exit rule on its own (Phase W shares it with the option backtest engine).
    `value` is the per-unit cost of closing the structure: short legs' prices minus long legs',
    each weighted by its ratio."""
    stop_value = meta.get("stop_value")
    target_value = meta.get("target_value")
    if meta.get("pnl_stop") is not None:
        # Phase U: payoff-priced structures are judged on mark-to-market P&L per unit - what was
        # received (or paid) at entry against what closing costs now.
        pnl = float(meta.get("net_credit") or 0.0) - value
        if pnl <= meta["pnl_stop"]:
            return f"Structure stop (P&L {pnl:.2f}/unit <= {meta['pnl_stop']:g})"
        if meta.get("pnl_target") is not None and pnl >= meta["pnl_target"]:
            return f"Structure target (P&L {pnl:.2f}/unit >= {meta['pnl_target']:g})"
    elif meta.get("debit"):
        # Phase R: a debit structure is judged on what selling it brings (longs minus shorts).
        worth = -value
        if stop_value is not None and worth <= stop_value:
            return f"Structure stop (worth {worth:.2f} <= {stop_value:g})"
        if target_value is not None and worth >= target_value:
            return f"Structure target (worth {worth:.2f} >= {target_value:g})"
        return None
    else:
        if stop_value is not None and value >= stop_value:
            return f"Spread stop (value {value:.2f} >= {stop_value:g})"
        if target_value is not None and value <= target_value:
            return f"Spread target (value {value:.2f} <= {target_value:g})"
    return underlying_exit_reason(meta, underlying_price)


def underlying_exit_reason(meta: dict, underlying_price: Optional[float]) -> Optional[str]:
    """The underlying-level part of the group exit: breakeven levels of ATM-short and
    payoff-priced structures, short-strike breaches of the winged ones."""
    if underlying_price is None:
        return None
    exits = meta.get("underlying_exits") or {}
    if "below" in exits and underlying_price <= exits["below"]:
        return f"Lower breakeven {exits['below']:g} breached (underlying {underlying_price:.2f})"
    if "above" in exits and underlying_price >= exits["above"]:
        return f"Upper breakeven {exits['above']:g} breached (underlying {underlying_price:.2f})"
    for right, strike in (meta.get("short_strikes") or {}).items():
        if right == "PE" and underlying_price <= strike:
            return f"Short {int(strike)} PE breached (underlying {underlying_price:.2f})"
        if right == "CE" and underlying_price >= strike:
            return f"Short {int(strike)} CE breached (underlying {underlying_price:.2f})"
    return None


async def _monitor_group(
    session: AsyncSession, legs: List[TradeRecord], price_lookup: PriceLookup, *,
    broker: Optional[BrokerInterface], user_id: Optional[int],
) -> List[CloseOutcome]:
    prices: Dict[int, float] = {}
    for leg in legs:
        try:
            prices[leg.id] = await price_lookup(leg.symbol, exchange_for_trade(leg))
            _mark(leg, prices[leg.id])
        except Exception as exc:  # noqa: BLE001 - no decision on a group with a missing leg price
            logger.warning("No price for leg %s of group %s: %s", leg.symbol, leg.leg_group_id, exc)
            return [CloseOutcome(trade_id=l.id, closed=False, warnings=[f"Price unavailable for {leg.symbol}: {exc}"]) for l in legs]
    underlying_price = None
    if legs[0].underlying_symbol:
        try:
            underlying_price = await price_lookup(legs[0].underlying_symbol, underlying_exchange(legs[0].underlying_symbol))
        except Exception as exc:  # noqa: BLE001
            logger.warning("No underlying price for group %s: %s - value checks only", legs[0].leg_group_id, exc)
    reason = group_exit(legs, prices, underlying_price)
    if reason is None:
        return []
    return await close_group(session, legs, prices, reason, broker=broker, user_id=user_id)


async def close_group(
    session: AsyncSession, legs: List[TradeRecord], prices: Dict[int, float], reason: str, *,
    broker: Optional[BrokerInterface], user_id: Optional[int],
) -> List[CloseOutcome]:
    """Shorts first (risk off), then the wings. Each leg books its own P&L."""
    outcomes: List[CloseOutcome] = []
    for leg in sorted(legs, key=lambda l: 0 if l.leg_role == "SHORT" else 1):
        leg_broker = broker if leg.mode == ExecutionMode.LIVE.value else None
        if leg.mode == ExecutionMode.LIVE.value and leg_broker is None:
            leg_broker = await broker_for_trade(session, leg)
        outcomes.append(await close_position(session, leg, prices[leg.id], reason, broker=leg_broker, user_id=user_id))
    return outcomes


async def _apply_trade_exit_rules(session: AsyncSession, trade: TradeRecord, price: float, *, broker: Optional[BrokerInterface]) -> Optional[str]:
    """Run the trade's ExitRules against the current price. Persists a tightened stop and the
    best price; LIVE, moves the broker-side SL-M trigger too (a failed modify keeps the software
    stop and says so). Returns a time-exit reason when one fired."""
    rules = ExitRules.from_json(trade.exit_rules)
    if not rules.active:
        return None
    update = apply_exit_rules(
        rules, direction=trade.direction, entry_price=trade.entry_price,
        initial_stop=trade.initial_stop_loss if trade.initial_stop_loss is not None else trade.stop_loss,
        current_stop=trade.stop_loss, best_price=trade.best_price, high=price, low=price,
        entry_time=trade.entry_time, now=datetime.now(timezone.utc),
    )
    changed = update.best_price != trade.best_price or update.stop_changed
    trade.best_price = update.best_price
    if update.stop_changed:
        old_stop = trade.stop_loss
        trade.stop_loss = update.stop_loss
        logger.info("Trade %s stop %s -> %s (%s)", trade.id, old_stop, update.stop_loss, update.stop_reason)
        live_broker = broker
        if trade.mode == ExecutionMode.LIVE.value and live_broker is None:
            live_broker = await broker_for_trade(session, trade)
        if trade.mode == ExecutionMode.LIVE.value and trade.sl_order_id and live_broker is not None:
            try:
                # P0.5 / T2: a stop placed as SL (limit) moves its limit with the trigger; SL-M has no limit.
                stop_side = OrderSide.SELL if trade.direction == "LONG" else OrderSide.BUY
                stop_type, stop_limit = live_broker.stop_order_params(trade.symbol, stop_side, update.stop_loss,
                                                                      is_option=(trade.instrument_kind or "UNDERLYING") == "OPTION")
                await live_broker.modify_order(trade.sl_order_id, trigger_price=update.stop_loss,
                                               price=stop_limit if stop_type == "SL" else None)
            except Exception as exc:  # noqa: BLE001 - the software stop still applies
                logger.warning("Could not move broker stop %s for trade %s: %s", trade.sl_order_id, trade.id, exc)
    if changed:
        await session.commit()
    return update.time_exit_reason


async def monitor_open_positions(
    session: AsyncSession, tenant_id: int, price_lookup: PriceLookup, *,
    broker: Optional[BrokerInterface] = None, user_id: Optional[int] = None, families: Optional[set] = None,
) -> List[CloseOutcome]:
    """The worker's per-cycle sweep for one tenant: current price for every open position, close
    the ones whose stop/target has been crossed. `price_lookup(symbol, exchange)` is normally
    MarketDataService.get_ltp. A failure on one position (quote unavailable, exit order error) is
    recorded and the sweep continues - the other positions still need watching."""
    open_trades = list(await session.scalars(
        select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))
        .order_by(TradeRecord.id)
    ))
    if families is not None:
        # Phase O2: only venues whose session is open right now; a closed venue's quote is stale anyway.
        from app.market_data.calendar import session_family
        open_trades = [t for t in open_trades if session_family(exchange_for_trade(t)) in families]
    outcomes: List[CloseOutcome] = []
    # Phase H2: legs of one structure are judged together, never one by one.
    groups: Dict[str, List[TradeRecord]] = {}
    for trade in open_trades:
        if trade.leg_group_id:
            groups.setdefault(trade.leg_group_id, []).append(trade)
    marked = False
    for group_id, legs in groups.items():
        outcomes.extend(await _monitor_group(session, legs, price_lookup, broker=broker, user_id=user_id))
        marked = marked or any(leg.mark_time is not None for leg in legs)
    for trade in open_trades:
        if trade.leg_group_id:
            continue
        try:
            price = await price_lookup(trade.symbol, exchange_for_trade(trade))
        except Exception as exc:  # noqa: BLE001 - one bad quote must not stop the sweep
            logger.warning("No price for %s while monitoring trade %s: %s", trade.symbol, trade.id, exc)
            outcomes.append(CloseOutcome(trade_id=trade.id, closed=False, warnings=[f"Price unavailable: {exc}"]))
            continue
        _mark(trade, price)
        marked = True
        # Phase J1: dynamic exits - tighten the stop (trailing / break-even) or close on time,
        # with the same rule code the backtest engine runs.
        if trade.exit_rules:
            time_reason = await _apply_trade_exit_rules(session, trade, price, broker=broker)
            if time_reason is not None:
                trade_broker = broker if trade.mode == ExecutionMode.LIVE.value else None
                if trade.mode == ExecutionMode.LIVE.value and trade_broker is None:
                    trade_broker = await broker_for_trade(session, trade)
                outcomes.append(await close_position(session, trade, price, time_reason, broker=trade_broker, user_id=user_id))
                continue
        underlying_price = None
        if (trade.instrument_kind or "UNDERLYING") == "OPTION" and trade.underlying_symbol:
            # The strategy's levels live on the underlying (Phase F4); without its quote the
            # premium floor/ceiling still protects the position this cycle.
            try:
                underlying_price = await price_lookup(trade.underlying_symbol, underlying_exchange(trade.underlying_symbol))
            except Exception as exc:  # noqa: BLE001
                logger.warning("No underlying price for %s (trade %s): %s - premium check only", trade.underlying_symbol, trade.id, exc)
        hit = check_contract_exit(trade, price, underlying_price)
        if hit is None:
            continue
        reason, exit_price = hit
        trade_broker = broker if trade.mode == ExecutionMode.LIVE.value else None
        if trade.mode == ExecutionMode.LIVE.value and trade_broker is None:
            trade_broker = await broker_for_trade(session, trade)
        outcomes.append(await close_position(session, trade, exit_price, reason, broker=trade_broker, user_id=user_id))
    if marked:
        # P0.5 / T6: persist the marks of positions that stayed open (close_position committed its own).
        try:
            await session.commit()
        except Exception as exc:  # noqa: BLE001 - a failed mark write must not fail the sweep
            logger.warning("Could not persist position marks: %s", exc)
            await session.rollback()
    return outcomes


def _mark(trade: TradeRecord, price: float) -> None:
    """P0.5 / T6: remember the last price seen for an open position (the daily-loss limit reads it)."""
    trade.mark_price = float(price)
    trade.mark_time = datetime.now(timezone.utc)
