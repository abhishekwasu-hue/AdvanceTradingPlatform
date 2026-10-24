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
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Awaitable, Callable, List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.execution import approval as approval_policy
from app.execution.products import product_for_trade
from app.brokers.base import BrokerInterface, round_stop_trigger
from app.brokers.exceptions import is_clear_rejection
from app.brokers.models import BrokerOrderRequest, BrokerOrderStatus
from app.brokers.timestamps import parse_broker_timestamp
from app.brokers.token_lifecycle import build_adapter, get_credential_record, token_is_usable
from app.core import config
from app.core.enums import ExecutionMode, NotificationSeverity, NotificationType, OrderSide
from app.db.models import BrokerCredentialRecord, StrategyDeploymentRecord, Tenant, TradeRecord
from app.db.savepoint import guarded
from app.compliance.algo_id import audit_order, order_algo_id
from app.execution.tagging import LEG_EXIT, build_order_tag
from app.execution.paper_broker import PaperBroker
from app.instruments.registry import get_contract_spec
from app.notifications.service import notify
from app.journal import capture as journal_capture
from app.trading import stop_state
from app.trading.exit_rules import ExitRules, apply_exit_rules
from app.trading.exit_logic import check_contract_exit
from app.instruments.master import INDEX_EXCHANGE, underlying_of

logger = logging.getLogger(__name__)

PriceLookup = Callable[[str, str], Awaitable[float]]

# Broker order-book statuses that mean "this order has filled" across Kite/Upstox/Noren wording.
_FILLED_STATUSES = {"COMPLETE", "COMPLETED", "FILLED", "TRADED", "EXECUTED"}

FILL_POLL_ATTEMPTS = 3
FILL_POLL_DELAY_SECONDS = 0.5

# Re-exit guard (ADR-0004: exits are never blocked; only a true duplicate exit is skipped).
# `exit_order_id` holds this marker while an exit is being placed: claimed, the broker's id not known yet.
EXIT_PENDING = "pending-exit"
# A claim younger than this is another exit being placed right now (wait for it); an older one never recorded a broker
# order (a crash, or a send that errored without a clear answer) and is handled like an exit missing from the book.
CLAIM_INFLIGHT_SECONDS = 120
# An order placed this long before the exit's claim can still be its exit (broker and platform clocks differ).
ADOPT_CLOCK_SKEW_SECONDS = 5
# Checks in a row that find an earlier exit still working (or its book unreadable) before a CRITICAL alert, and the
# alert's cooldown.
EXIT_WAIT_ALERT_CHECKS = 5
ALERT_COOLDOWN_SECONDS = 1800
# What the monitor books when it finishes an exit sent earlier without its own rule having to fire again.
RESUMED_EXIT_REASON = "Earlier exit order completed"


@dataclass
class CloseOutcome:
    trade_id: int
    closed: bool
    exit_reason: Optional[str] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    broker_exit_order_id: Optional[str] = None
    exit_tag: Optional[str] = None       # D1: the algo tag the exit order carried (audited)
    warnings: List[str] = field(default_factory=list)
    # G-LIVE: what the broker holds is unknown (an exit order errored without a clear answer, or a stop's fill could
    # not be read before netting) - close_position flags the tenant broker-uncertain so reconciliation decides.
    broker_uncertain: Optional[str] = None
    # Re-exit guard: an earlier exit order of this position that died (rejected / cancelled, nothing filled) and was replaced
    # by a new one now - both ids are kept (the audit trail records the replacement).
    previous_exit_order_id: Optional[str] = None
    previous_exit_status: Optional[str] = None
    # Re-exit guard: an earlier exit is still working (or could not be checked) - nothing new was sent; waiting for it.
    exit_waiting: bool = False


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


def trade_venue(trade, deployment_exchange: Optional[str] = None) -> str:
    """BK2-8: the exchange whose session clock a position follows (monitoring, square-off, its quote). The trade's own
    exchange - stamped at entry since BK2-8; for an older row without one, the symbol registry's (MCX / crypto
    contracts), else its deployment's exchange when that is another family than NSE (a currency deployment's trade is
    CDS), else NSE - as before for every equity / F&O row."""
    if trade.exchange:
        return trade.exchange
    spec = get_contract_spec(trade.symbol)
    if spec is not None:
        return spec.exchange
    return venue_beyond_nse(deployment_exchange) or "NSE"


def venue_beyond_nse(exchange: Optional[str]) -> Optional[str]:
    """`exchange` (upper case) when its session family is not NSE's, else None: an NSE / BSE / NFO name keeps meaning the
    NSE venue the platform has always used for an equity row; a CDS / MCX / crypto one is a different clock."""
    from app.market_data.calendar import session_family
    if exchange and session_family(exchange) != "NSE":
        return exchange.upper()
    return None


async def venue_for(session: AsyncSession, trade) -> str:
    """`trade_venue` for one trade, reading its deployment's exchange only when it is needed (a legacy row without an
    exchange and not in the registry). Every exit path uses it - the clock, the exit order, the stop and its tick - so a
    legacy CDS row is closed with the CDS venue at the CDS time (BK2-8 review LOW-2)."""
    if trade.exchange or get_contract_spec(trade.symbol) is not None or trade.deployment_id is None:
        return trade_venue(trade)
    dep_exchange = await session.scalar(select(StrategyDeploymentRecord.exchange).where(StrategyDeploymentRecord.id == trade.deployment_id))
    return trade_venue(trade, dep_exchange)


async def deployment_exchanges(session: AsyncSession, trades) -> Dict[int, str]:
    """{deployment id: exchange} for the trades whose venue falls back to their deployment (one query)."""
    ids = {t.deployment_id for t in trades if not t.exchange and t.deployment_id is not None and get_contract_spec(t.symbol) is None}
    if not ids:
        return {}
    rows = await session.execute(select(StrategyDeploymentRecord.id, StrategyDeploymentRecord.exchange)
                                 .where(StrategyDeploymentRecord.id.in_(ids)))
    return {dep_id: exchange for dep_id, exchange in rows.all() if exchange}


async def trade_venues(session: AsyncSession, trades) -> Dict[int, str]:
    """{trade id: venue} (`trade_venue`) for a batch of trades."""
    deps = await deployment_exchanges(session, trades)
    return {t.id: trade_venue(t, deps.get(t.deployment_id)) for t in trades}


async def open_trade_families_by_tenant(session: AsyncSession, tenant_ids: Optional[set] = None) -> Dict[int, set]:
    """{tenant id: the session families of its open positions} for `tenant_ids` (None: every tenant), from one select of
    the open trades. The worker reads it once per cycle with no tenant filter: the families decide which clocks it keeps
    and the tenants which organisations get the exits part of the cycle - with or without an ACTIVE / PAUSED deployment
    (#221: a PAPER ticket on MCX, CDS or crypto, or a deployment stopped while its position stayed open)."""
    from types import SimpleNamespace

    from app.market_data.calendar import session_family
    query = select(TradeRecord.id, TradeRecord.tenant_id, TradeRecord.symbol, TradeRecord.exchange, TradeRecord.deployment_id).where(
        TradeRecord.exit_time.is_(None))
    if tenant_ids is not None:
        if not tenant_ids:
            return {}
        query = query.where(TradeRecord.tenant_id.in_(tenant_ids))
    rows = (await session.execute(query)).all()
    trades = [SimpleNamespace(id=i, tenant_id=tenant, symbol=sym, exchange=ex, deployment_id=dep) for i, tenant, sym, ex, dep in rows]
    venues = await trade_venues(session, trades)
    out: Dict[int, set] = {}
    for trade in trades:
        out.setdefault(trade.tenant_id, set()).add(session_family(venues[trade.id]))
    return out


async def open_trade_families(session: AsyncSession, tenant_ids: Optional[set] = None) -> set:
    """BK2-8: the session families of the open positions of `tenant_ids` (None: every tenant) - the worker always keeps
    these clocks, so a position is monitored and squared off on its own venue's session even when no ACTIVE / PAUSED
    deployment trades there any more (ADR-0004: an exit is never skipped because a clock was not looked at)."""
    by_tenant = await open_trade_families_by_tenant(session, tenant_ids)
    return set().union(*by_tenant.values()) if by_tenant else set()


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


async def _read_book(broker: BrokerInterface) -> Optional[List[BrokerOrderStatus]]:
    """The broker's order book, or None when it cannot be read. MED-3: "the book is unreadable" is not "the order is gone"."""
    try:
        return list(await broker.get_order_book())
    except Exception as exc:  # noqa: BLE001 - informational, callers decide the fallback
        logger.warning("Order book unavailable: %s", exc)
        return None


async def _read_order(broker: BrokerInterface, order_id: str) -> Tuple[bool, Optional[BrokerOrderStatus]]:
    """(the book could be read, the order in it or None)."""
    book = await _read_book(broker)
    if book is None:
        return False, None
    return True, next((o for o in book if o.order_id == order_id), None)


async def _find_order(broker: BrokerInterface, order_id: str) -> Optional[BrokerOrderStatus]:
    """The order, or None when it is not in the book or the book cannot be read (callers that must tell those apart use
    `_read_order`)."""
    return (await _read_order(broker, order_id))[1]


def _fully_filled(order: BrokerOrderStatus, quantity: float) -> bool:
    """The exit filled in full: a filled status, or every unit it asked for filled."""
    return (order.status or "").upper() in _FILLED_STATUSES or float(order.filled_quantity or 0) >= float(quantity) - 1e-9


async def _exit_fill(broker: BrokerInterface, order_id: str, quantity: float,
                     fallback: float) -> Tuple[float, Optional[BrokerOrderStatus]]:
    """The exit's fill price (else `fallback`) and the order as last read - polled until it has filled in full or ended.
    Round 4: a part fill while the order still works is not an end - the caller sees it working and books nothing."""
    order: Optional[BrokerOrderStatus] = None
    for attempt in range(FILL_POLL_ATTEMPTS):
        order = await _find_order(broker, order_id)
        if order is not None and _fully_filled(order, quantity):
            return (float(order.average_price) if order.average_price else fallback), order
        if order is not None and _stop_is_dead(order.status):
            return fallback, order
        if attempt < FILL_POLL_ATTEMPTS - 1:
            await asyncio.sleep(FILL_POLL_DELAY_SECONDS)
    return fallback, order


def _stop_already_filled(trade: TradeRecord, sl_order: BrokerOrderStatus, reason: str, outcome: CloseOutcome) -> float:
    """The broker-side stop did the exit for us: record it as a Stop Loss at the stop's own fill,
    whatever level the software check happened to see first."""
    outcome.broker_exit_order_id = trade.sl_order_id
    outcome.exit_reason = "Stop Loss"
    if reason != "Stop Loss":
        outcome.warnings.append(f"Broker stop {trade.sl_order_id} had already filled; recorded as Stop Loss")
    return float(sl_order.average_price) if sl_order.average_price else trade.stop_loss


# A stop the broker no longer works: nothing to cancel, nothing more will fill. Explicit statuses only - a
# "CANCEL PENDING" stop can still fill.
_DEAD_STOP_STATUSES = ("REJECTED", "CANCELLED", "CANCELED", "CANCELLED AMO", "EXPIRED", "LAPSED")


def _stop_is_dead(status: str) -> bool:
    return (status or "").upper() in _DEAD_STOP_STATUSES


async def _settled_stop(broker: BrokerInterface, order_id: str) -> Optional[BrokerOrderStatus]:
    """The stop once it is terminal (dead or filled), polled like a fill; None when the book cannot say."""
    for attempt in range(FILL_POLL_ATTEMPTS):
        order = await _find_order(broker, order_id)
        if order is not None and (_stop_is_dead(order.status) or order.status.upper() in _FILLED_STATUSES):
            return order
        if attempt < FILL_POLL_ATTEMPTS - 1:
            await asyncio.sleep(FILL_POLL_DELAY_SECONDS)
    return None


def _partial_aware() -> bool:
    """G-LIVE: with either stop switch on, an exit sends only what a stop-limit did not already fill."""
    return bool(config.LIVE_UPSTOX_OPTION_STOP_LIMIT or config.LIVE_EXIT_IF_NO_STOP)


def is_pending_exit(order_id: Optional[str]) -> bool:
    """True when `order_id` is the claim marker of an exit being placed (no broker id recorded yet)."""
    return order_id == EXIT_PENDING


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def claim_in_flight(trade, now: Optional[datetime] = None) -> bool:
    """MED-2: True while `trade`'s exit claim is young enough to be another exit being placed right now."""
    sent = _utc(getattr(trade, "exit_sent_at", None))
    now = now or datetime.now(timezone.utc)
    return is_pending_exit(trade.exit_order_id) and sent is not None and (now - sent).total_seconds() < CLAIM_INFLIGHT_SECONDS


def absent_exit_dead_reason(trade) -> Optional[str]:
    """Why an exit of `trade` that the broker's book does not list (or a claim that never recorded a broker order) is
    over, so a fresh exit may go out - or None: wait (the organisation is flagged so reconciliation runs every cycle).

    Only one proof counts: a reconciliation that read the broker's positions after the exit was sent found this symbol
    still open with matching quantities and no order working on it (`reconciled_open_at`, stamped per position by
    reconciliation). "Sent on an earlier day" is not proof - a day order that FILLED yesterday is just as absent from
    today's book, and a second exit on that flat position would open one the other way. The tenant-wide flag does not
    decide either: a mismatch on another symbol says nothing about this one."""
    sent = _utc(getattr(trade, "exit_sent_at", None))
    seen = _utc(getattr(trade, "reconciled_open_at", None))
    if sent is None or seen is None or seen <= sent:
        return None
    return (f"a reconciliation at {seen.isoformat(timespec='seconds')} found {trade.symbol} still open at the broker with no "
            "order working on it")


def expected_exit_tag(trade, broker: BrokerInterface, algo_id: Optional[str]) -> str:
    """The tag every exit of `trade` carries at this broker (built exactly as `_square_off_live` builds it)."""
    return build_order_tag(strategy_id=trade.strategy_id, leg=LEG_EXIT, algo_id=algo_id,
                           max_length=getattr(broker, "max_tag_length", None) or 20, broker=broker.name)


async def exit_ids_elsewhere(session: AsyncSession, trade) -> frozenset:
    """Order ids other positions of the organisation record on this symbol (never adopted as `trade`'s exit)."""
    rows = await session.execute(select(TradeRecord.broker_order_id, TradeRecord.sl_order_id, TradeRecord.exit_order_id)
                                 .where(TradeRecord.tenant_id == trade.tenant_id, TradeRecord.symbol == trade.symbol,
                                        TradeRecord.id != trade.id))
    return frozenset(i for row in rows.all() for i in row if i)


# `_exit_lookalike` verdicts.
_ADOPT, _FOREIGN, _UNVERIFIED = "adopt", "foreign", "unverified"


def _exit_lookalike(trade, order: BrokerOrderStatus, exclude: frozenset, tag: Optional[str],
                    exchange: Optional[str]) -> Tuple[Optional[str], str]:
    """(verdict, why) for one book order against `trade`'s exit. None: not even shaped like the exit (another symbol or
    side, not MARKET, larger than the position, dead with nothing filled, or an id a position records). Else:

    - `_ADOPT`: this platform placed it (round 4) - its tag is the exit tag, or, when the broker echoes no tag, its product
      and exchange are the position's - and the broker took it inside the exit's claim window, from
      `exit_sent_at - ADOPT_CLOCK_SKEW_SECONDS` to `exit_sent_at + CLAIM_INFLIGHT_SECONDS` (`placed_at`, aware UTC);
    - `_FOREIGN`: provably not this exit (another tag, product or exchange, or placed outside the window) - a manual
      order on the same symbol;
    - `_UNVERIFIED`: may be this exit but cannot be shown to be (no `placed_at`, or no tag and no product / exchange, or
      no send time on record) - never adopted, never ignored while it works (see `_judge_earlier_exit`)."""
    side = OrderSide.SELL if trade.direction == "LONG" else OrderSide.BUY
    own = {trade.broker_order_id, trade.sl_order_id, trade.exit_order_id}
    if order.order_id in exclude or order.order_id in own:
        return None, ""
    if order.symbol != trade.symbol or order.transaction_type != side or (order.order_type or "").upper() != "MARKET":
        return None, ""
    if _stop_is_dead(order.status) and float(order.filled_quantity or 0) <= 0:
        return None, ""
    if float(order.quantity or 0) > float(trade.quantity) + 1e-9:
        return None, ""
    order_tag = (order.tag or "").strip().upper()
    if order_tag:
        if not tag or order_tag != tag.strip().upper():
            return _FOREIGN, f"tag {order.tag!r} is not this position's exit tag"
    else:
        product, venue = (order.product or "").upper(), (order.exchange or "").upper()
        if not product or not venue:
            return _UNVERIFIED, "the broker gives no tag, product or exchange to tell whose order it is"
        if product != product_for_trade(trade).upper() or venue != (exchange or exchange_for_trade(trade)).upper():
            return _FOREIGN, f"{order.product} on {order.exchange} is not this position's product / exchange"
    sent = _utc(getattr(trade, "exit_sent_at", None))
    if sent is None:
        return _UNVERIFIED, "this position has no exit send time to compare with"
    if order.placed_at is None:
        return _UNVERIFIED, "the broker gives no time for it"
    placed = parse_broker_timestamp(order.placed_at)       # adapters normalise; a naive time is IST, never UTC
    if placed is None:
        return _UNVERIFIED, "the broker gives no time for it"
    if placed < sent - timedelta(seconds=ADOPT_CLOCK_SKEW_SECONDS) or placed > sent + timedelta(seconds=CLAIM_INFLIGHT_SECONDS):
        return _FOREIGN, f"placed at {placed.isoformat(timespec='seconds')}, outside this exit's claim window"
    return _ADOPT, ""


def adoptable_exit(trade, orders: List[BrokerOrderStatus], exclude: frozenset = frozenset(), *, tag: Optional[str] = None,
                   exchange: Optional[str] = None) -> Optional[BrokerOrderStatus]:
    """P2: an order in the book that is this position's exit although its id was never recorded (a send that errored
    without a clear answer, but reached the broker). Adopting it instead of sending again is what keeps a second exit off a
    position that is already being closed - and only an order this platform placed for it inside the claim window is
    adopted (`_exit_lookalike`): a manual order on the same symbol is never booked as this position's exit. `tag`: the
    exit tag (`expected_exit_tag`); `exchange`: the position's venue."""
    for order in orders:
        if _exit_lookalike(trade, order, exclude, tag, exchange)[0] == _ADOPT:
            return order
    return None


def unverified_working_exits(trade, orders: List[BrokerOrderStatus], exclude: frozenset = frozenset(), *,
                             tag: Optional[str] = None, exchange: Optional[str] = None) -> List[Tuple[BrokerOrderStatus, str]]:
    """Orders still working that may be this position's exit but cannot be shown to be (`_UNVERIFIED`), with why."""
    out = []
    for order in orders:
        verdict, why = _exit_lookalike(trade, order, exclude, tag, exchange)
        status = (order.status or "").upper()
        if verdict == _UNVERIFIED and not _stop_is_dead(status) and status not in _FILLED_STATUSES:
            out.append((order, why))
    return out


class _ExitLedger:
    """MED-2: close_position's record of one LIVE exit - the claim before anything is sent (a conditional UPDATE, committed
    first: a second closer of the same position finds it taken and sends nothing), the broker's id committed the moment the
    broker takes the order (before any audit row or fill polling), and the claim's release when nothing went out."""

    def __init__(self, session: AsyncSession, trade: TradeRecord, tenant: Optional[Tenant], broker_name: str,
                 user_id: Optional[int], reason: str, now: datetime):
        self.session, self.trade, self.tenant = session, trade, tenant
        self.broker_name, self.user_id, self.reason, self.now = broker_name, user_id, reason, now
        self.previous_id: Optional[str] = trade.exit_order_id
        self.previous_sent: Optional[datetime] = getattr(trade, "exit_sent_at", None)
        self.claimed = False
        self.keep_claim = False

    async def ids_elsewhere(self) -> frozenset:
        """Order ids other positions of the organisation record on this symbol (never adopted as this one's exit)."""
        return await exit_ids_elsewhere(self.session, self.trade)

    async def adopt(self, order_id: str, earlier_id: str) -> None:
        """P2: an order found in the book is this position's exit - recorded (kept on the audit trail) instead of sending again."""
        from app.audit.log import write_audit_log
        trade = self.trade
        trade.exit_order_id = order_id
        await self.session.commit()
        self.previous_id = order_id
        await write_audit_log(self.session, trade.tenant_id, self.user_id, "exit_order_adopted",
                              f"trade {trade.id} {trade.symbol}: order {order_id} in the broker's book taken as its exit "
                              f"(recorded: {earlier_id}) - no second exit")
        await self.session.commit()

    async def cleared(self, earlier_id: str, why: str) -> None:
        """HIGH-1: the earlier exit is over without trace - its id leaves the position (kept on the audit trail)."""
        from app.audit.log import write_audit_log
        await write_audit_log(self.session, self.trade.tenant_id, self.user_id, "exit_order_cleared",
                              f"trade {self.trade.id} {self.trade.symbol}: earlier exit {earlier_id} is not in the broker's book "
                              f"and is over ({why}) - cleared; a fresh exit goes out")
        self.previous_id, self.previous_sent = None, None

    async def claim(self) -> bool:
        trade = self.trade
        expected = trade.exit_order_id
        sent_at = datetime.now(timezone.utc)
        same = TradeRecord.exit_order_id.is_(None) if expected is None else TradeRecord.exit_order_id == expected
        result = await self.session.execute(
            update(TradeRecord).where(TradeRecord.id == trade.id, TradeRecord.exit_time.is_(None), same)
            .values(exit_order_id=EXIT_PENDING, exit_sent_at=sent_at).execution_options(synchronize_session=False))
        if getattr(result, "rowcount", 0) != 1:
            return False
        await self.session.commit()
        set_committed_value(trade, "exit_order_id", EXIT_PENDING)
        set_committed_value(trade, "exit_sent_at", sent_at)
        self.claimed = True
        return True

    async def sent(self, order_id: str, outcome: CloseOutcome, prior_fills: List[Tuple[float, float]]) -> None:
        """The broker took the exit. P3: its id (and what earlier exits filled) is committed first, in its own commit -
        the claim can never be released from here on - then the audit rows. A failing audit write leaves the id recorded."""
        trade = self.trade
        self.keep_claim = True
        trade.exit_order_id = order_id
        filled_before = sum(q for q, _ in prior_fills)
        trade.exit_filled_quantity = filled_before or None
        trade.exit_filled_price = (sum(q * p for q, p in prior_fills) / filled_before) if filled_before else None
        await self.session.commit()
        self.claimed = False
        await audit_order(self.session, trade.tenant_id, self.user_id, leg="EXIT", broker_name=self.broker_name, symbol=trade.symbol,
                          tag=outcome.exit_tag, broker_order_id=order_id, ref=f"trade {trade.id}: {self.reason}")
        if outcome.previous_exit_order_id:
            # Re-exit guard: the earlier exit id is never lost - the replacement is on the audit trail with both ids.
            from app.audit.log import write_audit_log
            await write_audit_log(self.session, trade.tenant_id, self.user_id, "exit_order_replaced",
                                  f"trade {trade.id} {trade.symbol}: earlier exit {outcome.previous_exit_order_id} "
                                  f"({outcome.previous_exit_status}) replaced by {order_id}")
        await self.session.commit()

    async def release(self) -> None:
        """Nothing went out (the broker clearly refused, or no order was needed): the claim is undone."""
        if not self.claimed or self.keep_claim:
            return
        self.claimed = False
        trade = self.trade
        await self.session.execute(
            update(TradeRecord).where(TradeRecord.id == trade.id, TradeRecord.exit_order_id == EXIT_PENDING)
            .values(exit_order_id=self.previous_id, exit_sent_at=self.previous_sent).execution_options(synchronize_session=False))
        await self.session.commit()
        set_committed_value(trade, "exit_order_id", self.previous_id)
        set_committed_value(trade, "exit_sent_at", self.previous_sent)


@dataclass
class _Earlier:
    """The re-exit guard's verdict on a position's earlier exit."""
    proceed: bool                                   # a new exit goes out (for `remainder` when set)
    price: Optional[float] = None                   # not proceeding: the booked price (it filled), None = wait / flagged
    remainder: Optional[float] = None               # HIGH-2: only this much is left to exit
    prior_fills: List[Tuple[float, float]] = field(default_factory=list)   # (quantity, price) already filled before it


def _filled_before(trade: TradeRecord, order: BrokerOrderStatus, book: List[BrokerOrderStatus]) -> List[Tuple[float, float]]:
    """What filled before `order` (the position's recorded exit) went out: it was sized for what was left then. Earlier
    exits' fills are recorded on the trade (`exit_filled_quantity` / `_price`); the rest of that gap is a stop-limit's
    part fill before the first exit, at the stop's own average price."""
    whole = float(trade.quantity)
    ordered = min(float(order.quantity or 0) or whole, whole)
    gap = whole - ordered
    prior: List[Tuple[float, float]] = []
    if gap <= 0:
        return prior
    known = min(float(trade.exit_filled_quantity or 0), gap)
    if known > 0:
        prior.append((known, float(trade.exit_filled_price) if trade.exit_filled_price else float(trade.stop_loss)))
    if gap - known > 0:
        stop = next((o for o in book if o.order_id == trade.sl_order_id), None) if trade.sl_order_id else None
        prior.append((gap - known, float(stop.average_price) if stop is not None and stop.average_price else float(trade.stop_loss)))
    return prior


async def _still_open_now(trade: TradeRecord, broker: BrokerInterface, ledger: Optional[_ExitLedger], outcome: CloseOutcome,
                          what: str) -> bool:
    """Round 5: the last check before a replacement for a missing exit goes out - the broker's positions, read now, still
    hold exactly what the platform has open on the symbol (`reconciliation.service.exit_peers`, the same comparison
    reconciliation makes). A reconciliation stamp can be older than a close made since (in the broker app, or by an order
    that could not be adopted); sending on it would open a position the other way. Anything else - another quantity, flat,
    or the positions cannot be read - waits, flagged, with the stamp cleared: reconciliation runs every cycle while the
    tenant is flagged, and once it shows the position open again (stamped anew) this check passes and the exit goes out
    (ADR-0004)."""
    try:
        positions = list(await broker.get_positions())
    except Exception as exc:  # noqa: BLE001 - unreadable is not "still open"
        reason = f"the broker's positions could not be read ({exc})"
    else:
        if ledger is not None:
            from app.reconciliation.service import exit_peers   # local: reconciliation imports this module
            peers = await exit_peers(ledger.session, trade, broker.name)
        else:
            peers = [trade]
        platform = sum(float(t.quantity) if t.direction == "LONG" else -float(t.quantity) for t in peers)
        held = sum(float(p.quantity) for p in positions if p.symbol == trade.symbol)
        if abs(platform - held) <= 1e-9:
            return True
        reason = f"the broker now holds {held:g} of {trade.symbol} where the platform has {platform:g} open"
    outcome.exit_waiting = True
    outcome.broker_uncertain = (f"trade {trade.id}: {what} and a reconciliation had found it open, but {reason} - no "
                                "replacement exit until a reconciliation shows it open again")
    outcome.warnings.append(outcome.broker_uncertain)
    if getattr(trade, "reconciled_open_at", None) is not None and ledger is not None:
        trade.reconciled_open_at = None          # the proof no longer holds (the stop guard reads it too)
        await ledger.session.commit()
    return False


async def _judge_earlier_exit(trade: TradeRecord, earlier_id: str, broker: BrokerInterface, reference_price: float,
                              outcome: CloseOutcome, ledger: Optional[_ExitLedger], resume_only: bool,
                              exit_tag: Optional[str] = None, exchange: Optional[str] = None) -> _Earlier:
    """Re-exit guard: this position already claimed or sent an exit (the booking did not happen). A second exit on top of
    a live or filled one would open a position the other way - so the earlier one decides:

    - filled: booked at its fill (blended with what filled before it), nothing new (flagged when its stop filled too);
    - still working, a claim being placed right now, or the book unreadable this cycle (MED-3): wait;
    - ended (rejected / cancelled ...) after a part fill (HIGH-2): an exit for exactly the rest, every fill blended;
    - ended with nothing filled: a new exit;
    - not in the book, or a claim that never recorded an order: first an order in the book that is it is adopted
      (P2, `adoptable_exit`: this platform's exit tag - or its product and exchange - placed inside the claim window);
      else a new exit once a reconciliation since it was sent shows the position still open (`absent_exit_dead_reason`);
      until then the tenant is flagged so reconciliation runs, and the wait is counted towards the "exit not completed"
      alert. Round 5: the replacement goes out only if the broker's positions, re-read right then, still hold what the
      platform has open on the symbol (`_still_open_now`); else it waits, flagged. An order still working that may be the exit but cannot be shown to be (round 4: the broker gives no time
      for it, or no tag / product / exchange) is never adopted - a manual order would be booked as the close - and never
      sent over either: the position waits, flagged, and the wait raises the CRITICAL alert, until that order ends (or
      is cancelled at the broker); then the reconciliation proof above lets the exit out (ADR-0004).

    `resume_only` (the monitor finishing an exit whose rule no longer fires): book a fill or send a part fill's rest, but
    never a whole new exit."""
    pending = is_pending_exit(earlier_id)
    if pending and claim_in_flight(trade, ledger.now if ledger else None):
        outcome.exit_waiting = True
        outcome.warnings.append("Another exit of this position is being placed right now - no second exit")
        return _Earlier(False)
    book = await _read_book(broker)
    if book is None:
        outcome.exit_waiting = True
        outcome.warnings.append(f"The broker's order book could not be read - earlier exit order {earlier_id} is unknown; "
                                "waiting this cycle (no second exit)")
        return _Earlier(False)
    earlier = None if pending else next((o for o in book if o.order_id == earlier_id), None)
    unverified: List[Tuple[BrokerOrderStatus, str]] = []
    if earlier is None:
        exclude = await ledger.ids_elsewhere() if ledger is not None else frozenset()
        adopted = adoptable_exit(trade, book, exclude, tag=exit_tag, exchange=exchange)
        if adopted is None:
            unverified = unverified_working_exits(trade, book, exclude, tag=exit_tag, exchange=exchange)
        if adopted is not None:
            outcome.warnings.append(f"Order {adopted.order_id} in the broker's book is this position's exit ({adopted.status}) - "
                                    "adopted, no second exit")
            if ledger is not None:
                await ledger.adopt(adopted.order_id, earlier_id)
            earlier_id, earlier = adopted.order_id, adopted
    if earlier is None:
        what = ("its exit claim never recorded a broker order" if pending
                else f"its earlier exit order {earlier_id} is not in the broker's book")
        dead = absent_exit_dead_reason(trade)
        if unverified:
            outcome.exit_waiting = True
            ids = ", ".join(f"{o.order_id} ({o.status}; {why})" for o, why in unverified)
            outcome.broker_uncertain = (f"trade {trade.id}: {what}; order(s) {ids} on {trade.symbol} may be its exit and are "
                                        "still working - not adopted and no second exit until they end (or are cancelled at "
                                        "the broker)")
            outcome.warnings.append(outcome.broker_uncertain)
            return _Earlier(False)
        if dead is None:
            outcome.exit_waiting = True
            outcome.broker_uncertain = (f"trade {trade.id}: {what} - no second exit until a reconciliation shows {trade.symbol} "
                                        "still open at the broker")
            outcome.warnings.append(outcome.broker_uncertain)
            return _Earlier(False)
        if resume_only:
            outcome.warnings.append(f"Trade {trade.id}: {what} and it is over ({dead}); a new exit goes out when its exit "
                                    "rule fires again (the stop guard re-arms its stop meanwhile)")
            return _Earlier(False)
        held = await _still_open_now(trade, broker, ledger, outcome, what)
        if not held:
            return _Earlier(False)
        if ledger is not None:
            await ledger.cleared(earlier_id, dead)
        outcome.previous_exit_order_id, outcome.previous_exit_status = earlier_id, f"gone: {dead}"
        outcome.warnings.append(f"Earlier exit {earlier_id} is over ({dead}); a new exit was sent")
        return _Earlier(True)

    whole = float(trade.quantity)
    prior = _filled_before(trade, earlier, book)
    ordered = whole - sum(q for q, _ in prior)
    status = (earlier.status or "").upper()
    if status in _FILLED_STATUSES:
        outcome.broker_exit_order_id = earlier_id
        outcome.warnings.append(f"Earlier exit order {earlier_id} had filled; booked at its fill")
        if trade.sl_order_id:
            # LOW: the stop may have filled too (it raced the exit) - that is an opposite position: reconciliation decides.
            stop = next((o for o in book if o.order_id == trade.sl_order_id), None)
            if stop is not None and (stop.status or "").upper() in _FILLED_STATUSES and float(stop.filled_quantity or 0) > 0:
                outcome.broker_uncertain = (f"trade {trade.id}: exit {earlier_id} and stop {trade.sl_order_id} both filled - "
                                            "the account may hold a position the other way")
                outcome.warnings.append(outcome.broker_uncertain)
        fill = float(earlier.average_price) if earlier.average_price else reference_price
        return _Earlier(False, price=(sum(q * p for q, p in prior) + ordered * fill) / whole)
    if not _stop_is_dead(status):
        outcome.exit_waiting = True
        outcome.warnings.append(f"Earlier exit order {earlier_id} is still working ({earlier.status}) - waiting for it")
        return _Earlier(False)

    # Terminal: rejected / cancelled / lapsed. What it did not fill is what is left to exit.
    filled = min(float(earlier.filled_quantity or 0), ordered)
    if filled > 0:
        prior.append((filled, float(earlier.average_price) if earlier.average_price else reference_price))
    remainder = ordered - filled
    if remainder <= 0:
        outcome.broker_exit_order_id = earlier_id
        outcome.warnings.append(f"Earlier exit order {earlier_id} ended {earlier.status} with all {filled:g} filled; booked")
        return _Earlier(False, price=sum(q * p for q, p in prior) / whole)
    if filled == 0 and resume_only:
        outcome.warnings.append(f"Earlier exit order {earlier_id} ended {earlier.status} unfilled; a new exit goes out when the "
                                "exit rule fires again (the stop guard re-arms the stop meanwhile)")
        return _Earlier(False)
    outcome.previous_exit_order_id = earlier_id
    outcome.previous_exit_status = earlier.status if filled == 0 else f"{earlier.status} after filling {filled:g}"
    if filled > 0:
        outcome.warnings.append(f"Earlier exit order {earlier_id} ended {earlier.status} after filling {filled:g} of "
                                f"{ordered:g}; an exit for the remaining {remainder:g} was sent")
    else:
        outcome.warnings.append(f"Earlier exit order {earlier_id} ended {earlier.status}; a new exit was sent")
    return _Earlier(True, remainder=remainder if prior else None, prior_fills=prior)


async def _square_off_live(
    trade: TradeRecord, broker: BrokerInterface, reason: str, reference_price: float, outcome: CloseOutcome,
    algo_id: Optional[str] = None, stop_dead: bool = False, exchange: Optional[str] = None,
    ledger: Optional[_ExitLedger] = None, resume_only: bool = False,
) -> Optional[float]:
    """Places whatever the broker needs to flatten this position and returns the realised exit
    price, or None when the position could not be flattened (the trade must then stay open).

    `stop_dead` (G-LIVE, LIVE_EXIT_IF_NO_STOP): the caller has just seen the stop missing / rejected and the broker
    clearly refuse a new one - there is no stop to cancel, so the exit must not wait on a cancel.
    `ledger` (close_position's, MED-2): claims the exit before anything is sent and records the broker's id as soon as
    the broker takes it. `resume_only`: only finish an exit sent earlier (see `_judge_earlier_exit`)."""
    exchange = exchange or exchange_for_trade(trade)       # the caller passes venue_for(): the trade's session venue
    exit_side = OrderSide.SELL if trade.direction == "LONG" else OrderSide.BUY
    stop_after: Optional[BrokerOrderStatus] = None
    cancelled_stop = False
    sl_order_id = trade.sl_order_id
    earlier = _Earlier(True)
    earlier_id = trade.exit_order_id
    if earlier_id:
        earlier = await _judge_earlier_exit(trade, earlier_id, broker, reference_price, outcome, ledger, resume_only,
                                            exit_tag=expected_exit_tag(trade, broker, algo_id), exchange=exchange)
        if not earlier.proceed:
            return earlier.price
        stop_dead = True              # its stop was handled by that earlier attempt (a fresh read still decides)
    elif resume_only:
        return None
    if ledger is not None and not await ledger.claim():
        outcome.exit_waiting = True
        outcome.warnings.append("Another exit of this position claimed it first - no second exit")
        return None
    try:
        if sl_order_id:
            sl_order = await _find_order(broker, sl_order_id)
            if sl_order is not None and sl_order.status.upper() in _FILLED_STATUSES:
                # The exchange already closed us at the stop. Nothing to place - a second exit order
                # here would open a fresh position in the opposite direction.
                if earlier.prior_fills:
                    outcome.broker_uncertain = (f"trade {trade.id}: stop {sl_order_id} filled after an earlier exit had part-filled - "
                                                "the account may hold a position the other way")
                    outcome.warnings.append(outcome.broker_uncertain)
                return _stop_already_filled(trade, sl_order, reason, outcome)
            dead = sl_order is not None and _stop_is_dead(sl_order.status)
            # G-LIVE (LIVE_EXIT_IF_NO_STOP): a stop the broker rejected or cancelled has nothing to cancel - asking would
            # fail and block the exit (ADR-0004: exits are never blocked). Default off = today's cancel attempt. A caller's
            # `stop_dead` is trusted only when this fresh read agrees (missing or dead): a stop the book still shows
            # working is cancelled first, whatever its status string.
            # A REJECTED stop never worked, whatever the switches: a broker refuses to cancel it, and asking blocked the exit
            # on every cycle (with the stop guard no longer re-arming it, for good) - ADR-0004, exits are never blocked.
            rejected = sl_order is not None and (sl_order.status or "").upper() == "REJECTED"
            if (stop_dead and (sl_order is None or dead)) or (dead and (config.LIVE_EXIT_IF_NO_STOP or rejected)):
                stop_after = sl_order
            else:
                try:
                    await broker.cancel_order(sl_order_id)
                except Exception as exc:  # noqa: BLE001
                    # Cancel can legitimately fail because the stop filled between our book read and now.
                    recheck = await _find_order(broker, sl_order_id)
                    if recheck is not None and recheck.status.upper() in _FILLED_STATUSES:
                        return _stop_already_filled(trade, recheck, reason, outcome)
                    outcome.warnings.append(f"Could not cancel protective stop {sl_order_id}: {exc}")
                    logger.error("Cancel of SL %s failed for trade %s: %s", sl_order_id, trade.id, exc)
                    return None
                # The exit goes out at once (ADR-0004: no polling for the cancel to settle). One immediate read of the
                # stop (no wait) sizes it from what the stop had filled by then; anything filled after that read is
                # netted in the booking below and handed to reconciliation, since the exit then sent more than was left.
                stop_after = sl_order
                if _partial_aware():
                    fresh = await _find_order(broker, sl_order_id)
                    if fresh is not None and float(fresh.filled_quantity or 0) >= float((sl_order.filled_quantity if sl_order else 0) or 0):
                        stop_after = fresh
                cancelled_stop = True

        # G-LIVE: a stop-limit can fill in part before it is cancelled (or before it lapsed); exiting the full quantity
        # would then open a fresh position the other way. Only the rest goes out, and the booking blends both fills.
        # HIGH-2: an earlier exit that part-filled already sized that; only its unfilled rest goes out.
        stop_filled = 0.0
        if earlier.remainder is None and _partial_aware() and stop_after is not None:
            stop_filled = min(float(stop_after.filled_quantity or 0), float(trade.quantity))
            if stop_filled >= float(trade.quantity):
                return _stop_already_filled(trade, stop_after, reason, outcome)
        if earlier.remainder is not None:
            exit_quantity = type(trade.quantity)(earlier.remainder)
        else:
            exit_quantity = trade.quantity if stop_filled == 0 else type(trade.quantity)(float(trade.quantity) - stop_filled)

        exit_tag = expected_exit_tag(trade, broker, algo_id)
        outcome.exit_tag = exit_tag
        try:
            response = await broker.place_order(BrokerOrderRequest(
                symbol=trade.symbol, exchange=exchange, transaction_type=exit_side, quantity=exit_quantity,
                order_type="MARKET", product=product_for_trade(trade),   # Phase AS: the entry's own product
                tag=exit_tag,
            ))
        except Exception as exc:  # noqa: BLE001
            outcome.warnings.append(f"Exit order failed at {broker.name}: {exc}")
            logger.error("Exit order failed for trade %s: %s", trade.id, exc)
            if not is_clear_rejection(exc):
                # MED-2: the order may stand at the broker - whatever the stop switches, the tenant is flagged and the claim
                # stays, so no second exit goes out until reconciliation shows the position still open.
                outcome.broker_uncertain = f"exit order for trade {trade.id} errored without a clear answer: {exc}"
                if ledger is not None:
                    ledger.keep_claim = True
            return None
        if response.status.upper() in ("REJECTED", "CANCELLED"):
            outcome.warnings.append(f"Exit order rejected by {broker.name}: {response.message or response.status}")
            return None
        outcome.broker_exit_order_id = response.order_id
        if ledger is not None:
            ledger.keep_claim = True          # P3: the broker took it - nothing from here on may release the claim
            await ledger.sent(response.order_id, outcome, earlier.prior_fills)
        price, final = await _exit_fill(broker, response.order_id, float(exit_quantity), fallback=reference_price)
        if final is not None and not _fully_filled(final, float(exit_quantity)) and not _stop_is_dead(final.status):
            # Round 4: still working (part-filled or not yet filled) when polling stopped - never booked as closed on a
            # part fill. Its id is recorded; the next check finishes it (`_judge_earlier_exit`): still working -> wait,
            # filled -> booked, ended after a part fill -> the rest goes out.
            outcome.exit_waiting = True
            outcome.warnings.append(f"Exit order {response.order_id} is still working ({final.status}, "
                                    f"{float(final.filled_quantity or 0):g} of {float(exit_quantity):g} filled) - not booked yet")
            return None
        if final is not None and _stop_is_dead(final.status) and float(final.filled_quantity or 0) < float(exit_quantity) - 1e-9:
            # The broker took the exit and then ended it (an RMS reject, a cancel) with some or none of it filled: the
            # position is not flat - never booked as closed. Its id is recorded; the next check sends the rest of a part
            # fill (the monitor), or a new exit when the exit rule fires again (the stop guard re-arms the stop meanwhile).
            outcome.warnings.append(f"Exit order {response.order_id} ended {final.status} after filling "
                                    f"{float(final.filled_quantity or 0):g} of {float(exit_quantity):g} - not booked")
            logger.error("Exit order %s for trade %s ended %s part-filled - left open", response.order_id, trade.id, final.status)
            return None
        if cancelled_stop and _partial_aware() and sl_order_id:
            # The exit is already out; now read where the cancelled stop finally settled.
            settled = await _settled_stop(broker, sl_order_id)
            if settled is None:
                outcome.broker_uncertain = (f"stop {sl_order_id} on trade {trade.id}: its final fill could not be read after "
                                            f"the cancel - the exit sent {float(exit_quantity):g}")
                outcome.warnings.append(outcome.broker_uncertain)
            else:
                settled_filled = min(float(settled.filled_quantity or 0), float(trade.quantity))
                if settled_filled > stop_filled:
                    outcome.broker_uncertain = (f"stop {sl_order_id} on trade {trade.id} filled {settled_filled - stop_filled:g} "
                                                f"more after it was read; the exit sent {float(exit_quantity):g} - the excess is "
                                                "left for reconciliation")
                    outcome.warnings.append(outcome.broker_uncertain)
                    stop_filled, stop_after = settled_filled, settled
                    if stop_filled >= float(trade.quantity):
                        # The stop closed all of it; the market exit sent after the read is the excess. Its id stays on
                        # the outcome (and the trade) so reconciliation can find the order that opened it.
                        _stop_already_filled(trade, settled, reason, outcome)
                        outcome.broker_exit_order_id = response.order_id
                        outcome.warnings.append(f"Exit order {response.order_id} went out after stop {sl_order_id} had "
                                                f"filled in full - its {float(exit_quantity):g} are an opposite position")
                        return float(settled.average_price) if settled.average_price else float(trade.stop_loss)
        if earlier.remainder is not None:
            # HIGH-2: what the earlier exit (and a stop before it) filled, blended with this exit's fill.
            filled_before = sum(q for q, _ in earlier.prior_fills)
            total = sum(q * p for q, p in earlier.prior_fills) + float(exit_quantity) * price
            return total / (filled_before + float(exit_quantity))
        if stop_filled > 0 and stop_after is not None:
            stop_price = float(stop_after.average_price) if stop_after.average_price else float(trade.stop_loss)
            rest = float(trade.quantity) - stop_filled
            outcome.warnings.append(f"Stop {sl_order_id} had filled {stop_filled:g} of {float(trade.quantity):g}; "
                                    f"the exit sent the remaining {float(exit_quantity):g}")
            return (stop_filled * stop_price + rest * price) / float(trade.quantity)
        return price
    finally:
        if ledger is not None:
            try:
                await ledger.release()    # a no-op once the broker took the order, or when the send's answer was unclear
            except Exception:  # noqa: BLE001 - a claim left behind is handled like a stale one (reconciliation decides)
                logger.exception("Could not release the exit claim of trade %s", trade.id)


async def _note_exit_wait(session: AsyncSession, trade: TradeRecord, outcome: CloseOutcome, user_id: Optional[int]) -> None:
    """LOW: an earlier exit still working check after check is raised once EXIT_WAIT_ALERT_CHECKS checks in a row found it
    so (then at most once per ALERT_COOLDOWN_SECONDS) - nothing new is sent while it may still fill."""
    if not outcome.exit_waiting:
        stop_state.exit_waits.pop(trade.id, None)
        return
    waits = stop_state.exit_waits[trade.id] = stop_state.exit_waits.get(trade.id, 0) + 1
    if waits < EXIT_WAIT_ALERT_CHECKS:
        return
    last = stop_state.exit_wait_alert.get(trade.id)
    if last is not None and time.monotonic() - last <= ALERT_COOLDOWN_SECONDS:
        return
    stop_state.exit_wait_alert[trade.id] = time.monotonic()
    await notify(session, trade.tenant_id, NotificationType.SYSTEM_FAILURE, title=f"Exit not completed: {trade.symbol}",
                 message=(f"Position #{trade.id}: its exit has not completed after {waits} checks in a row "
                          f"({'; '.join(outcome.warnings)[:300]}). No second exit is sent while the first may still fill - "
                          "check the order at the broker (cancel it there and a new exit goes out)."),
                 severity=NotificationSeverity.CRITICAL, user_id=user_id, related_trade_id=trade.id)


async def close_position(
    session: AsyncSession, trade: TradeRecord, exit_price: float, reason: str, *,
    broker: Optional[BrokerInterface] = None, user_id: Optional[int] = None, now: Optional[datetime] = None,
    stop_dead: bool = False, underlying_price: Optional[float] = None, resume_only: bool = False,
) -> CloseOutcome:
    """Closes one open position. PAPER: books the exit at `exit_price`. LIVE: squares off at the
    broker first (see module docstring) and books the realised fill; if the broker leg fails the
    trade stays open and the outcome says why - a LIVE row is never marked closed while the
    position may still exist at the exchange. Commits on success and raises an EXIT notification.

    `resume_only` (LIVE, the monitor): only finish an exit this position already sent - book its fill, or send the rest
    of one that part-filled - never a whole new exit."""
    outcome = CloseOutcome(trade_id=trade.id, closed=False)
    if trade.exit_time is not None:
        outcome.warnings.append("Position is already closed")
        return outcome

    realised_price = exit_price
    if resume_only and trade.mode != ExecutionMode.LIVE.value:
        return outcome
    if trade.mode == ExecutionMode.LIVE.value:
        if broker is None:
            outcome.warnings.append("LIVE position needs an authenticated broker session to exit - left open")
            return outcome
        tenant = await session.get(Tenant, trade.tenant_id)
        if trade.exit_order_id and trade.exit_sent_at is None:
            # An exit recorded before its send time was kept: the first time it is seen stands in (later, so stricter).
            trade.exit_sent_at = datetime.now(timezone.utc)
        ledger = _ExitLedger(session, trade, tenant, broker.name, user_id, reason, now or datetime.now(timezone.utc))
        realised = await _square_off_live(
            trade, broker, reason, exit_price, outcome,
            algo_id=order_algo_id(tenant.algo_id if tenant is not None else None, broker.name), stop_dead=stop_dead,
            exchange=await venue_for(session, trade), ledger=ledger, resume_only=resume_only,
        )
        if outcome.broker_exit_order_id and outcome.broker_exit_order_id != trade.exit_order_id:
            # No new order went out (the broker-side stop had filled): its id is the exit's - audited and committed now.
            await audit_order(session, trade.tenant_id, user_id, leg="EXIT", broker_name=broker.name, symbol=trade.symbol,
                              tag=outcome.exit_tag, broker_order_id=outcome.broker_exit_order_id, ref=f"trade {trade.id}: {reason}")
            # Review LOW-1: the broker took the exit - its id and the audit row are committed now, so no later error
            # (the booking below, a caller's savepoint) can roll back the record that the order went out.
            trade.exit_order_id = outcome.broker_exit_order_id
            await session.commit()
        if outcome.broker_uncertain and tenant is not None:
            from app.reconciliation.service import mark_broker_uncertain   # local: reconciliation imports this module
            await mark_broker_uncertain(session, tenant, outcome.broker_uncertain)
            await session.commit()
        await _note_exit_wait(session, trade, outcome, user_id)
        if realised is None:
            return outcome
        realised_price = realised
        reason = outcome.exit_reason or reason

    # Z3 (review M3): claim the close atomically - "Exit now" (web / Telegram), an approved exit and the worker's own sweep
    # can reach the same open position at the same moment; only the one whose conditional UPDATE lands books it (one
    # P&L, one EXIT notification). On Postgres the UPDATE row-locks until commit, so a concurrent claim re-checks and finds
    # the position closed. The loser books nothing (a LIVE loser had already sent an exit: the books are flagged).
    exit_time = now or datetime.now(timezone.utc)
    claimed = await session.execute(update(TradeRecord).where(TradeRecord.id == trade.id, TradeRecord.exit_time.is_(None))
                                    .values(exit_time=exit_time).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        await session.refresh(trade)
        outcome.warnings.append("Position was closed at the same moment by another exit - this close was not booked")
        if trade.mode == ExecutionMode.LIVE.value and outcome.broker_exit_order_id:
            from app.reconciliation.service import mark_broker_uncertain
            tenant = await session.get(Tenant, trade.tenant_id)
            if tenant is not None:
                await mark_broker_uncertain(session, tenant, f"exit order {outcome.broker_exit_order_id} for trade {trade.id} went out after another close")
                await session.commit()
        return outcome

    direction_sign = 1 if trade.direction == "LONG" else -1
    gross_pnl = direction_sign * (realised_price - trade.entry_price) * trade.quantity
    charges = PaperBroker().estimate_round_trip_costs(trade.entry_price, realised_price, trade.quantity, trade.instrument_kind or "UNDERLYING",
                                                      sold_first=trade.direction == "SHORT", entry_date=trade.entry_time,
                                                      exit_date=now or datetime.now(timezone.utc),
                                                      delivery=(getattr(trade, "holding", None) or "") == "SWING")
    trade.exit_price = round(realised_price, 2)
    trade.exit_time = exit_time
    trade.exit_reason = reason
    trade.charges = charges
    trade.charges_source = "ESTIMATED"
    trade.pnl = round(gross_pnl - charges, 2)
    if outcome.broker_exit_order_id:
        trade.exit_order_id = outcome.broker_exit_order_id
    await session.commit()
    # Z6: flag read on this session's connection (cached), the capture queued off the exit path; never raises or waits.
    # `underlying_price` (the quote an option exit was judged on) gives the journal IV at exit.
    await journal_capture.on_exit(session, trade, underlying_price=underlying_price)
    stop_state.forget(trade.id)   # the stop guard's counters and alert cooldowns die with the position

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
    # Z3: an "Exit now?" question still open on this position is void now (best effort, after the close is booked).
    await approval_policy.retire_proposals_for_closed(session, trade, reason)
    # Z5 / P1-d: a PAPER close that reaches the user's daily loss limit records the lockout now (after the exit, never raises).
    try:
        from app.risk_engine import by_default
        await by_default.after_close(session, trade)
    except Exception:  # noqa: BLE001 - belt and braces: the exit is booked; nothing after it may turn it into an error
        logger.exception("Daily loss check after closing trade %s failed", trade.id)
        await session.rollback()
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
            prices[leg.id] = await price_lookup(leg.symbol, await venue_for(session, leg))
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
    from app.execution.incomplete_structures import rules_paused
    if rules_paused(legs):
        # SE-7: an INCOMPLETE structure is not whole - its exit rules (written for every leg) would read a false target or
        # stop on a part of it. They wait for his answer or his rule; every leg stays closable at once (Exit now, the
        # emergency exit, the square-off, "Close the filled legs") - ADR-0004.
        return []
    reason = group_exit(legs, prices, underlying_price)
    if reason is None:
        return []
    return await close_group(session, legs, prices, reason, broker=broker, user_id=user_id, underlying_price=underlying_price)


async def close_group(
    session: AsyncSession, legs: List[TradeRecord], prices: Dict[int, float], reason: str, *,
    broker: Optional[BrokerInterface], user_id: Optional[int], underlying_price: Optional[float] = None,
) -> List[CloseOutcome]:
    """Shorts first (risk off), then the wings. Each leg books its own P&L. `underlying_price`: the quote the exit was
    judged on (journal IV at exit only). A leg missing from `prices` is not closed this time (no quote).

    Naked-short guard (LIVE): when a LIVE SHORT leg did not close, the wings are NOT closed - selling the protection while
    the short is still open would leave a naked short. The wings wait (recorded, CRITICAL notification - at most once per
    ALERT_COOLDOWN_SECONDS per structure); the next pass tries the short first again, then the wings. Every path that
    closes a structure (the monitor, the intraday square-off, the emergency exit) goes through here."""
    outcomes: List[CloseOutcome] = []
    stuck_short: Optional[TradeRecord] = None
    for leg in sorted(legs, key=lambda l: (0 if l.leg_role == "SHORT" else 1, l.id)):
        if stuck_short is not None and leg.leg_role != "SHORT":
            outcomes.append(CloseOutcome(trade_id=leg.id, closed=False, warnings=[
                f"Held: short leg #{stuck_short.id} ({stuck_short.symbol}) did not close - closing this wing now would leave a "
                "naked short; the short is tried first again next cycle"]))
            continue
        if leg.id not in prices:
            outcome = CloseOutcome(trade_id=leg.id, closed=False, warnings=[f"Price unavailable for {leg.symbol} - left open"])
        else:
            leg_broker = broker if leg.mode == ExecutionMode.LIVE.value else None
            if leg.mode == ExecutionMode.LIVE.value and leg_broker is None:
                leg_broker = await broker_for_trade(session, leg)
            outcome = await close_position(session, leg, prices[leg.id], reason, broker=leg_broker, user_id=user_id,
                                           underlying_price=underlying_price)
        outcomes.append(outcome)
        if (leg.leg_role == "SHORT" and leg.mode == ExecutionMode.LIVE.value and not outcome.closed and leg.exit_time is None
                and stuck_short is None):
            stuck_short = leg
    group_id = legs[0].leg_group_id if legs else None
    if stuck_short is None:
        if group_id:
            stop_state.wings_held_alert.pop(group_id, None)
        return outcomes
    held = [o.trade_id for o in outcomes if not o.closed and o.trade_id != stuck_short.id]
    why = "; ".join(next((o.warnings for o in outcomes if o.trade_id == stuck_short.id), []))[:300] or "no reason given"
    logger.error("Group %s: short #%s did not close (%s); wings %s held", group_id, stuck_short.id, why, held)
    last = stop_state.wings_held_alert.get(group_id or "") if group_id else None
    if last is not None and time.monotonic() - last <= ALERT_COOLDOWN_SECONDS:
        return outcomes                      # LOW: recorded and raised already - not again every cycle
    if group_id:
        stop_state.wings_held_alert[group_id] = time.monotonic()
    from app.audit.log import write_audit_log
    await write_audit_log(session, stuck_short.tenant_id, user_id, "group_wings_held",
                          f"group {stuck_short.leg_group_id}: short #{stuck_short.id} {stuck_short.symbol} did not close ({why}); "
                          f"wings {held} held to avoid a naked short")
    await notify(session, stuck_short.tenant_id, NotificationType.SYSTEM_FAILURE,
                 title=f"Short leg not closed: {stuck_short.symbol}",
                 message=(f"Exit of the structure ({reason}): the short leg #{stuck_short.id} could not be closed ({why}). The "
                          "protective wings were kept open so the short is never naked; the short is retried first on the "
                          "next cycle. Check the position at the broker."),
                 severity=NotificationSeverity.CRITICAL, user_id=user_id, related_trade_id=stuck_short.id)
    return outcomes


class _SameTrigger(Exception):
    """The trailed stop rounds to the trigger already standing - nothing to send."""


async def _apply_trade_exit_rules(session: AsyncSession, trade: TradeRecord, price: float, *, broker: Optional[BrokerInterface],
                                  now: Optional[datetime] = None) -> Optional[str]:
    """Run the trade's ExitRules against the current price. Persists a tightened stop and the
    best price; LIVE, moves the broker-side SL-M trigger too (a failed modify keeps the software
    stop and says so). Returns a time-exit reason when one fired. `now` is the sweep's clock (the worker cycle's time),
    so a time exit ("flat at 15:00", "N minutes in trade") is judged at the same instant as everything else in the cycle,
    never against a different wall clock."""
    rules = ExitRules.from_json(trade.exit_rules)
    if not rules.active:
        return None
    update = apply_exit_rules(
        rules, direction=trade.direction, entry_price=trade.entry_price,
        initial_stop=trade.initial_stop_loss if trade.initial_stop_loss is not None else trade.stop_loss,
        current_stop=trade.stop_loss, best_price=trade.best_price, high=price, low=price,
        entry_time=trade.entry_time, now=now or datetime.now(timezone.utc),
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
                # P0.5 / T2: a stop placed as SL (limit) moves its limit with the trigger; SL-M has no limit. The exchange
                # takes only whole ticks: the trigger moves to the tick farther from the market.
                stop_side = OrderSide.SELL if trade.direction == "LONG" else OrderSide.BUY
                venue = await venue_for(session, trade)
                trigger = round_stop_trigger(update.stop_loss, stop_side, symbol=trade.symbol, exchange=venue)
                if trigger == round_stop_trigger(old_stop, stop_side, symbol=trade.symbol, exchange=venue):
                    raise _SameTrigger()           # on the tick it has not moved: a no-op modify some brokers reject
                stop_type, stop_limit = live_broker.stop_order_params(trade.symbol, stop_side, trigger,
                                                                      is_option=(trade.instrument_kind or "UNDERLYING") == "OPTION")
                if stop_type == "SL" and config.LIVE_UPSTOX_OPTION_STOP_LIMIT and live_broker.name == "upstox":
                    # G-LIVE: a stop placed as SL-M before the switch went on stays SL-M - a modify keeps its type.
                    standing = await _find_order(live_broker, trade.sl_order_id)
                    if standing is not None and (standing.order_type or "").upper() == "SL-M":
                        stop_type, stop_limit = "SL-M", None
                await live_broker.modify_order(trade.sl_order_id, trigger_price=trigger,
                                               price=stop_limit if stop_type == "SL" else None)
                # Review LOW-1: the broker moved the stop - the new level is committed now, so no later error rolls the
                # record back to a stop that no longer stands at the broker.
                await session.commit()
            except _SameTrigger:
                pass
            except Exception as exc:  # noqa: BLE001 - the software stop still applies
                logger.warning("Could not move broker stop %s for trade %s: %s", trade.sl_order_id, trade.id, exc)
    if changed:
        await session.commit()
        if update.stop_changed:   # Z6: flag on this session's connection (cached), capture queued; never waits
            await journal_capture.on_stop_change(session, trade, float(old_stop), float(update.stop_loss),
                                                 source=f"exit rule: {update.stop_reason or 'stop moved'}")
    return update.time_exit_reason


async def monitor_open_positions(
    session: AsyncSession, tenant_id: int, price_lookup: PriceLookup, *,
    broker: Optional[BrokerInterface] = None, user_id: Optional[int] = None, families: Optional[set] = None,
    exit_approval: Optional[Callable[[TradeRecord], bool]] = None, now: Optional[datetime] = None,
) -> List[CloseOutcome]:
    """The worker's per-cycle sweep for one tenant: current price for every open position, close
    the ones whose stop/target has been crossed. `price_lookup(symbol, exchange)` is normally
    MarketDataService.get_ltp. A failure on one position (quote unavailable, exit order error) is
    recorded and the sweep continues - the other positions still need watching.

    Z3 / BT2 (`exit_approval`, None = unchanged): for a position it returns True for, reaching a *target* asks "Exit
    now?" (an EXIT_POSITION proposal) instead of closing. Every protective exit - the stop, a premium floor/ceiling,
    a time exit, a structure rule - still closes here at once, before and regardless of any proposal or snooze
    (ADR-0004); `determine_exit_price` checks the stop first, so a pending question never shadows it.

    `now`: the cycle's clock (default: the wall clock) - time exits are judged against it."""
    open_trades = list(await session.scalars(
        select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))
        .order_by(TradeRecord.id)
    ))
    venues = await trade_venues(session, open_trades)       # BK2-8: a row without an exchange follows its deployment
    if families is not None:
        # Phase O2: only venues whose session is open right now; a closed venue's quote is stale anyway.
        from app.market_data.calendar import session_family
        open_trades = [t for t in open_trades if session_family(venues[t.id]) in families]
    outcomes: List[CloseOutcome] = []
    asks: List[tuple] = []          # Z3: (trade, target reason, price) - "Exit now?" questions, raised after every exit
    # Phase H2: legs of one structure are judged together, never one by one.
    groups: Dict[str, List[TradeRecord]] = {}
    for trade in open_trades:
        if trade.leg_group_id:
            groups.setdefault(trade.leg_group_id, []).append(trade)
    marked = False
    for group_id, legs in groups.items():
        leg_ids = [leg.id for leg in legs]
        try:
            async with guarded(session, keep=tuple(legs)):
                outcomes.extend(await _monitor_group(session, legs, price_lookup, broker=broker, user_id=user_id))
        except Exception as exc:  # noqa: BLE001 - one structure's failure never ends the sweep (ADR-0004)
            logger.error("Monitoring group %s failed: %s", group_id, exc)
            outcomes.extend(CloseOutcome(trade_id=i, closed=False, warnings=[f"Monitoring failed: {exc}"]) for i in leg_ids)
        marked = marked or any(leg.mark_time is not None for leg in legs)
    for trade in open_trades:
        if trade.leg_group_id:
            continue
        try:
            price = await price_lookup(trade.symbol, venues[trade.id])
        except Exception as exc:  # noqa: BLE001 - one bad quote must not stop the sweep
            logger.warning("No price for %s while monitoring trade %s: %s", trade.symbol, trade.id, exc)
            outcomes.append(CloseOutcome(trade_id=trade.id, closed=False, warnings=[f"Price unavailable: {exc}"]))
            continue
        _mark(trade, price)
        marked = True
        trade_id = trade.id
        try:
            # Review LOW-2 (ADR-0004): one savepoint per position - a close that fails on this one (a SQL error, a
            # broker answer the booking cannot take) never ends the sweep for the positions after it.
            async with guarded(session, keep=(trade,)):
                # Phase J1: dynamic exits - tighten the stop (trailing / break-even) or close on time,
                # with the same rule code the backtest engine runs.
                if trade.exit_rules:
                    time_reason = await _apply_trade_exit_rules(session, trade, price, broker=broker, now=now)
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
                    if trade.mode == ExecutionMode.LIVE.value and trade.exit_order_id:
                        # Re-exit guard: an exit this position already sent is finished whether or not its rule still
                        # fires - its fill is booked, the rest of a part fill goes out (HIGH-2), a long wait is raised.
                        trade_broker = broker or await broker_for_trade(session, trade)
                        outcomes.append(await close_position(session, trade, price, RESUMED_EXIT_REASON, broker=trade_broker,
                                                             user_id=user_id, resume_only=True))
                    continue
                reason, exit_price = hit
                if exit_approval is not None and not approval_policy.is_protective(reason) and exit_approval(trade):
                    asks.append((trade, reason, price))          # asked after the sweep: no question ever delays another exit
                    outcomes.append(CloseOutcome(trade_id=trade.id, closed=False, warnings=[f"{reason}: waiting for \"Exit now?\" approval"]))
                    continue
                trade_broker = broker if trade.mode == ExecutionMode.LIVE.value else None
                if trade.mode == ExecutionMode.LIVE.value and trade_broker is None:
                    trade_broker = await broker_for_trade(session, trade)
                outcomes.append(await close_position(session, trade, exit_price, reason, broker=trade_broker, user_id=user_id,
                                                     underlying_price=underlying_price))
        except Exception as exc:  # noqa: BLE001 - this position stays open and is judged again next cycle
            logger.error("Monitoring trade %s failed: %s", trade_id, exc)
            outcomes.append(CloseOutcome(trade_id=trade_id, closed=False, warnings=[f"Monitoring failed: {exc}"]))
    if marked:
        # P0.5 / T6: persist the marks of positions that stayed open (close_position committed its own).
        try:
            await session.commit()
        except Exception as exc:  # noqa: BLE001 - a failed mark write must not fail the sweep
            logger.warning("Could not persist position marks: %s", exc)
            await session.rollback()
        else:
            # Z6: the price range each open position has seen (MAE / MFE); queued off the sweep, never raises or waits.
            await journal_capture.on_marks(session, tenant_id, {t.id: float(t.mark_price) for t in open_trades
                                                                 if t.exit_time is None and t.mark_price is not None})
    for trade, reason, price in asks:
        await approval_policy.propose_exit(session, trade, reason, price)
    return outcomes


def _mark(trade: TradeRecord, price: float) -> None:
    """P0.5 / T6: remember the last price seen for an open position (the daily-loss limit reads it)."""
    trade.mark_price = float(price)
    trade.mark_time = datetime.now(timezone.utc)
