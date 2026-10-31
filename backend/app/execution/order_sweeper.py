"""M55 S4: the start-up order sweeper - every `orders` row a stopped worker (or API process) left in a non-terminal state
is settled against its venue before the first cycle.

An order row moves CREATED -> VALIDATING -> RISK_CHECK -> SUBMITTED -> PENDING -> FILLED -> POSITION_OPEN inside one call
(app/execution/signal_execution.py, app/execution/multileg.py). A process that stops inside that call leaves the row in
between, and nothing else ever looks at it again. The sweeper takes every such row of every organisation (not only those
with an open LIVE position), once it has not moved for SWEEP_MIN_AGE_SECONDS (an order being placed right now by another
process is never touched), and asks its venue what really happened:

* its position is recorded (`trade_id`): the order filled - POSITION_OPEN;
* CREATED / VALIDATING: nothing can have been sent (orders go out only after the risk check) - CANCELLED;
* PAPER: the paper venue's record is the position table - no position means nothing filled - CANCELLED;
* LIVE: looked up in the broker's order book of every usable session of the organisation, by the client order id its
  tag carries (app/execution/tagging.py; the tag is on the row before anything is sent), else by the broker's order id:
  - ended with nothing filled at the broker: REJECTED / CANCELLED, as the broker says;
  - filled (in full or in part) with no position recorded, or still working: flagged - the organisation's LIVE entries
    pause until reconciliation (`mark_broker_uncertain`), a CRITICAL notification says which order and why;
  - not in any readable book: CANCELLED when it was looked up by its own client order id and is young enough to be in
    the book (SWEEP_BOOK_HOURS), else
    FAILED ("could not be confirmed at the broker") with a notification;
  - no usable session, or a book that cannot be read: left as it is (unknown is never "not there") and checked again
    on the next start.

The sweeper never places, modifies or cancels a broker order: it only reads books and records what it finds.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface
from app.brokers.models import BrokerOrderStatus
from app.core.enums import ExecutionMode, NotificationSeverity, NotificationType, OrderStatus
from app.db.models import BrokerCredentialRecord, OrderRecord, Tenant, TradeRecord
from app.execution.order_persistence import transition_order
from app.execution.order_state_machine import _TRANSITIONS, TERMINAL_STATUSES
from app.execution.tagging import client_order_id, has_client_id

logger = logging.getLogger(__name__)

SWEEP_MIN_AGE_SECONDS = 300
SWEEP_BOOK_HOURS = 12
NON_TERMINAL = tuple(s.value for s in OrderStatus if s not in TERMINAL_STATUSES)
_FILLED = {"COMPLETE", "COMPLETED", "FILLED", "TRADED", "EXECUTED"}
_DEAD = {"REJECTED", "CANCELLED", "CANCELED", "CANCELLED AMO", "EXPIRED", "LAPSED"}


@dataclass
class SweepReport:
    settled: dict[str, int] = field(default_factory=dict)        # terminal status -> rows moved there
    flagged: list[int] = field(default_factory=list)            # order ids flagged for the owner
    unchecked: list[int] = field(default_factory=list)          # order ids left for the next start
    tenants: set[int] = field(default_factory=set)

    def count(self, status: str) -> None:
        self.settled[status] = self.settled.get(status, 0) + 1


def _path(start: OrderStatus, goal: OrderStatus) -> list[OrderStatus]:
    """The shortest legal chain of transitions from `start` to `goal` (empty: none, or already there)."""
    seen, queue = {start}, deque([(start, [])])
    while queue:
        status, path = queue.popleft()
        if status == goal:
            return path
        for nxt in _TRANSITIONS.get(status, frozenset()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, path + [nxt]))
    return []


async def _move(session: AsyncSession, order: OrderRecord, goal: OrderStatus, detail: str, report: SweepReport) -> None:
    if order.status == goal.value:
        return
    path = _path(OrderStatus(order.status), goal)
    if not path:                                       # e.g. FILLED with no position: it cannot be CANCELLED - flagged
        await _flag(session, order, f"it is {order.status} and could not be settled automatically", report, pause=False)
        return
    for status in path:
        order = await transition_order(session, order, status, detail=detail if status == goal else "Start-up order check")
    if goal in TERMINAL_STATUSES:
        report.count(goal.value)


def tag_client_id(order: OrderRecord) -> str | None:
    """The client order id the row's tag carries (an entry: derived from the row id), or None (a tag from before it)."""
    wanted = client_order_id("E", order.id)
    return wanted if has_client_id(order.algo_tag, wanted) else None


class _Books:
    """Each usable broker session's order book, read once per sweep (None: it could not be read)."""

    def __init__(self) -> None:
        self._books: dict[int, list[BrokerOrderStatus] | None] = {}

    async def read(self, key: int, broker: BrokerInterface) -> list[BrokerOrderStatus] | None:
        if key not in self._books:
            try:
                self._books[key] = list(await broker.get_order_book())
            except Exception as exc:  # noqa: BLE001 - unknown is never "not there"
                logger.warning("Order sweeper: the %s order book could not be read: %s", getattr(broker, "name", "?"), exc)
                self._books[key] = None
        return self._books[key]


async def _sessions(session: AsyncSession, order: OrderRecord) -> list[tuple[int, BrokerInterface]]:
    """The broker sessions the order can be at: its position's account when it has one, else every usable session of the
    organisation (one adapter per credential, the cache `broker_for_trade` keeps)."""
    from app.brokers.token_lifecycle import build_adapter, token_is_usable
    records = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == order.tenant_id)))
    cache: dict[int, BrokerInterface] = session.info.setdefault("trade_brokers", {})
    out = []
    for record in records:
        if not token_is_usable(record):
            continue
        adapter = cache.get(record.id)
        if adapter is None:
            try:
                adapter = cache[record.id] = build_adapter(record)
            except Exception as exc:  # noqa: BLE001 - that session is skipped; the others are still asked
                logger.warning("Order sweeper: no adapter for credential %s: %s", record.id, exc)
                continue
        out.append((record.id, adapter))
    return out


def _match(book: list[BrokerOrderStatus], order: OrderRecord, cid: str | None) -> BrokerOrderStatus | None:
    if cid and order.algo_tag:
        wanted = order.algo_tag.strip().upper()
        hits = [o for o in book if (o.tag or "").strip().upper() == wanted]
        if hits:
            return hits[-1]
    if order.broker_order_id:
        return next((o for o in book if o.order_id == order.broker_order_id), None)
    return None


async def _flag(session: AsyncSession, order: OrderRecord, why: str, report: SweepReport, *, pause: bool) -> None:
    from app.notifications.service import notify
    from app.reconciliation.service import mark_broker_uncertain
    tenant = await session.get(Tenant, order.tenant_id)
    if pause and tenant is not None:
        await mark_broker_uncertain(session, tenant, f"order {order.id} ({order.symbol}): {why}", user_id=order.user_id)
    await notify(session, order.tenant_id, NotificationType.SYSTEM_FAILURE, title=f"Order check needed: {order.symbol}",
                 message=f"After a restart, order #{order.id} on {order.symbol}: {why}. Please check it at your broker.",
                 severity=NotificationSeverity.CRITICAL if pause else NotificationSeverity.WARNING,
                 user_id=order.user_id, related_order_id=order.id)
    await session.commit()
    report.flagged.append(order.id)


async def _settle_live(session: AsyncSession, order: OrderRecord, books: _Books, age: timedelta, report: SweepReport) -> None:
    cid = tag_client_id(order)
    if cid is None and not order.broker_order_id:
        await _move(session, order, OrderStatus.FAILED, "Could not be confirmed at the broker (no order id to look it up by)", report)
        await _flag(session, order, "it could not be looked up at the broker, so it is marked failed", report, pause=False)
        return
    sessions = await _sessions(session, order)
    found, all_read = None, bool(sessions)
    for key, broker in sessions:
        book = await books.read(key, broker)
        if book is None:
            all_read = False
            continue
        found = _match(book, order, cid)
        if found is not None:
            break
    if found is None:
        if not all_read:
            report.unchecked.append(order.id)          # no usable session, or a book unreadable: checked on the next start
            return
        if cid is not None and age <= timedelta(hours=SWEEP_BOOK_HOURS):      # looked up by its own id: not there = never placed
            await _move(session, order, OrderStatus.CANCELLED, "Not at the broker: it was never placed", report)
        else:
            await _move(session, order, OrderStatus.FAILED, "Could not be confirmed at the broker (no longer in its order book)", report)
            await _flag(session, order, "it is no longer in the broker's order book, so it is marked failed", report, pause=False)
        return
    status = (found.status or "").upper()
    filled = float(found.filled_quantity or 0)
    if found.order_id and not order.broker_order_id:
        order.broker_order_id = found.order_id
    if status in _DEAD and filled <= 0:
        goal = OrderStatus.REJECTED if status == "REJECTED" else OrderStatus.CANCELLED
        await _move(session, order, goal, f"Ended {status} at the broker (order {found.order_id}), nothing filled", report)
        return
    if status in _FILLED or filled > 0:
        await _move(session, order, OrderStatus.FILLED, f"Filled at the broker (order {found.order_id}) - no position recorded", report)
        await _flag(session, order, f"it filled at the broker (order {found.order_id}) but no position was recorded", report, pause=True)
        return
    await _flag(session, order, f"it is still working at the broker (order {found.order_id}, {found.status})", report, pause=True)


async def sweep_orders(session: AsyncSession, now: datetime | None = None, *, min_age_seconds: float = SWEEP_MIN_AGE_SECONDS,
                       tenant_ids: set[int] | None = None) -> SweepReport:
    """Settles every non-terminal order row of every organisation (`tenant_ids`: only those) that has not moved for
    `min_age_seconds`."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(seconds=min_age_seconds)
    query = select(OrderRecord).where(OrderRecord.status.in_(NON_TERMINAL))
    if tenant_ids is not None:
        query = query.where(OrderRecord.tenant_id.in_(tenant_ids))
    rows = list(await session.scalars(query.order_by(OrderRecord.id)))
    report = SweepReport()
    books = _Books()
    for order in rows:
        updated = order.updated_at or order.created_at or now
        updated = updated if updated.tzinfo else updated.replace(tzinfo=UTC)
        if updated > cutoff:
            continue                                   # may be in the middle of being placed right now
        report.tenants.add(order.tenant_id)
        try:
            trade: Any = await session.get(TradeRecord, order.trade_id) if order.trade_id is not None else None
            if trade is not None:
                await _move(session, order, OrderStatus.POSITION_OPEN, "Its position is recorded", report)
            elif order.status in (OrderStatus.CREATED.value, OrderStatus.VALIDATING.value):
                await _move(session, order, OrderStatus.CANCELLED, "Stopped before it was checked: nothing was placed", report)
            elif order.mode != ExecutionMode.LIVE.value:
                await _move(session, order, OrderStatus.CANCELLED, "Stopped before it filled: nothing was placed", report)
            else:
                await _settle_live(session, order, books, now - updated, report)
        except Exception as exc:  # noqa: BLE001 - one row never ends the sweep for the others
            logger.exception("Order sweeper: order %s failed: %s", order.id, exc)
            report.unchecked.append(order.id)
            await session.rollback()
    if rows:
        logger.info("Order sweeper: %s settled, %s flagged, %s left for the next start", report.settled, len(report.flagged),
                    len(report.unchecked))
    return report
