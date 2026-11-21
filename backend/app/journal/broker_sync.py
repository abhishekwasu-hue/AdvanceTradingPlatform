"""P2-8 J1-1: trades placed in the trader's own broker app (outside ATP) land in his journal by themselves.

Reads the trade book of each of his OWN signed-in broker sessions (read-only; the same per-credential rate budget as
"From your broker", `app/me/broker_book.py`) and journals the fills ATP has not seen:

* a fill of an order ATP placed for him (his trade's entry / stop / exit order id) is MATCHED: that trade already has
  its journal entry, so nothing new is written;
* a fill already stored (`journal_broker_fills`, unique per user + broker + fill id) is skipped: a re-run, the worker's
  daily run and the "Sync from broker" button never journal a fill twice;
* the rest first close his open broker entries of the same symbol (first in, first out; a partial close splits the
  entry), then pair first in, first out (`importer.pair_fifo`) into new outside-ATP entries marked "From your broker".
  A pair already in the journal (the same trade imported from a file) is not written again.

Journal data only: a synced entry is never a trade row, so no order, position, risk counter or P&L book reads it.
Brokers return only the current day's fills (Upstox: get-trades-for-day), so the worker syncs once a day after the
close (OPEN_QUESTIONS JV2-Q1) and the Journal has a "Sync from broker" button.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    BrokerCredentialRecord,
    JournalBrokerFillRecord,
    JournalManualTradeRecord,
    TradeRecord,
    User,
    UserPrefsRecord,
)
from app.journal import entries, importer

logger = logging.getLogger(__name__)

ORIGIN = "BROKER"
LABEL = "From your broker"
MATCHED, ENTRY = "MATCHED", "ENTRY"
SYNC_AFTER = time(15, 45)            # IST: the worker's daily run (an operational time; the chart is never read by clock)
MARKS = "journal_broker_sync"        # internal user_prefs row: the IST day of the last worker run (no route reads it)
MAX_TRIES = 3                        # a broker that does not answer is asked again on the next runs, at most this often a day
EPS = 1e-9


# --- the pure part ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class BrokerFill:
    broker: str
    fill_id: str
    order_id: str | None
    symbol: str
    side: str                 # BUY / SELL
    quantity: float
    price: float
    at: datetime


@dataclass(frozen=True)
class OpenEntry:
    """One of his open broker-synced journal entries (no exit yet)."""
    id: int
    symbol: str
    direction: str            # LONG / SHORT
    quantity: float
    entry_time: datetime


@dataclass
class Close:
    """Fills that close (part of) an open broker entry."""
    entry_id: int
    quantity: float           # how much of it they close
    exit_price: float         # their quantity-weighted price
    exit_time: datetime
    rest: float               # what stays open (0 = closed in full)
    fills: list[BrokerFill] = field(default_factory=list)


@dataclass
class SyncPlan:
    matched: list[tuple[BrokerFill, int]] = field(default_factory=list)
    closes: list[Close] = field(default_factory=list)
    trades: list[tuple[importer.Trade, list[BrokerFill]]] = field(default_factory=list)
    seen: int = 0


def _closing(direction: str, side: str) -> bool:
    return (direction == "LONG" and side == "SELL") or (direction == "SHORT" and side == "BUY")


def plan_sync(fills: list[BrokerFill], own_orders: dict[str, int], seen: set[tuple[str, str]],
              open_entries: list[OpenEntry]) -> SyncPlan:
    """What a sync writes, from the broker's fills alone (same input -> same output; nothing is read twice)."""
    out = SyncPlan()
    fresh: list[BrokerFill] = []
    taken: set[tuple[str, str]] = set()
    for f in fills:
        key = (f.broker, f.fill_id)
        if key in seen or key in taken:
            out.seen += 1
            continue
        taken.add(key)
        if f.order_id and f.order_id in own_orders:
            out.matched.append((f, own_orders[f.order_id]))
        else:
            fresh.append(f)
    fresh.sort(key=lambda f: (f.at, f.fill_id))
    # 1. close his open broker entries first, oldest first
    left: dict[int, float] = {e.id: float(e.quantity) for e in open_entries}
    by_symbol: dict[str, list[OpenEntry]] = defaultdict(list)
    for e in sorted(open_entries, key=lambda e: (e.entry_time, e.id)):
        by_symbol[e.symbol.upper()].append(e)
    closes: dict[int, Close] = {}
    rest_fills: list[tuple[BrokerFill, float]] = []
    for f in fresh:
        rest = float(f.quantity)
        for e in by_symbol.get(f.symbol.upper(), []):
            if rest <= EPS:
                break
            if left[e.id] <= EPS or not _closing(e.direction, f.side):
                continue
            take = min(rest, left[e.id])
            c = closes.get(e.id)
            if c is None:
                c = closes[e.id] = Close(e.id, 0.0, 0.0, f.at, 0.0)
            c.exit_price = (c.exit_price * c.quantity + f.price * take) / (c.quantity + take)
            c.quantity += take
            c.exit_time = max(c.exit_time, f.at)
            c.fills.append(f)
            left[e.id] -= take
            rest -= take
        if rest > EPS:
            rest_fills.append((f, rest))
    for entry_id, c in closes.items():
        c.rest = round(max(left[entry_id], 0.0), 6)
        c.exit_price = round(c.exit_price, 4)
        c.quantity = round(c.quantity, 6)
        out.closes.append(c)
    # 2. pair the rest into new round trips
    rows: dict[int, BrokerFill] = {}
    pairs: list[importer.Fill] = []
    for i, (f, quantity) in enumerate(rest_fills):
        symbol, exchange, kind = importer.clean_symbol(f.symbol)
        rows[i] = f
        pairs.append(importer.Fill(row=i, symbol=symbol, exchange=exchange, kind=kind, side=f.side, quantity=quantity,
                                   price=f.price, at=f.at, charges=0.0, timed=True))
    for t in importer.pair_fifo(pairs):
        out.trades.append((t, [rows[r] for r in t.rows]))
    return out


def to_fill(broker: str, raw: Any, now: datetime) -> BrokerFill | None:
    """A `BrokerTradeEntry` as a fill (None when it is not a whole fill: no id, no quantity, no price)."""
    try:
        side = str(getattr(raw.transaction_type, "value", raw.transaction_type)).upper()
        quantity, price = float(raw.quantity), float(raw.price)
    except (TypeError, ValueError, AttributeError):
        return None
    fill_id = str(getattr(raw, "trade_id", "") or "").strip()
    if side not in ("BUY", "SELL") or quantity <= 0 or price <= 0 or not fill_id:
        return None
    at = getattr(raw, "timestamp", None)
    if isinstance(at, datetime):
        at = at if at.tzinfo else at.replace(tzinfo=UTC)
    else:
        at = now
    order_id = str(raw.order_id).strip() if getattr(raw, "order_id", None) else None
    return BrokerFill(broker, fill_id[:100], order_id, str(raw.symbol).upper()[:50], side, quantity, price, at)


# --- the database part --------------------------------------------------------------------------------------------------

def build_adapter(record: BrokerCredentialRecord) -> Any:
    """The credential's budgeted adapter (tests replace this)."""
    from app.me import broker_book
    return broker_book._budgeted(record, broker_book.build_adapter(record))


async def _read_book(record: BrokerCredentialRecord, now: datetime) -> tuple[list[BrokerFill], str | None]:
    from app.brokers import catalog
    from app.brokers.token_lifecycle import token_is_usable
    name = catalog.display_name(record.broker_name)
    if not token_is_usable(record):
        return [], f"Sign in to {name} to bring its trades into your journal."
    try:
        raw = await build_adapter(record).get_trade_book()
    except Exception as exc:  # noqa: BLE001 - a sentence, never an exception
        logger.info("journal broker sync: %s trade book not read (%s)", record.broker_name, type(exc).__name__)
        return [], f"{name} did not send its trade list just now. Try again in a minute."
    fills = [f for f in (to_fill(record.broker_name, r, now) for r in raw or []) if f is not None]
    return fills, None


async def _own_orders(session: AsyncSession, user: User, order_ids: set[str]) -> dict[str, int]:
    if not order_ids:
        return {}
    ids = sorted(order_ids)
    out: dict[str, int] = {}
    for t in await session.scalars(select(TradeRecord).where(
            TradeRecord.user_id == user.id, TradeRecord.tenant_id == user.tenant_id,
            or_(TradeRecord.broker_order_id.in_(ids), TradeRecord.exit_order_id.in_(ids), TradeRecord.sl_order_id.in_(ids)))):
        for oid in (t.broker_order_id, t.exit_order_id, t.sl_order_id):
            if oid and oid in order_ids:
                out[oid] = t.id
    return out


async def _seen(session: AsyncSession, user: User, fills: list[BrokerFill]) -> set[tuple[str, str]]:
    if not fills:
        return set()
    rows = await session.execute(select(JournalBrokerFillRecord.broker, JournalBrokerFillRecord.fill_id).where(
        JournalBrokerFillRecord.user_id == user.id, JournalBrokerFillRecord.fill_id.in_(sorted({f.fill_id for f in fills}))))
    return {(b, f) for b, f in rows.all()}


async def _open_entries(session: AsyncSession, user: User) -> list[JournalManualTradeRecord]:
    return list(await session.scalars(select(JournalManualTradeRecord).where(
        JournalManualTradeRecord.user_id == user.id, JournalManualTradeRecord.tenant_id == user.tenant_id,
        JournalManualTradeRecord.origin == ORIGIN, JournalManualTradeRecord.exit_time.is_(None))))


def _fill_row(user: User, f: BrokerFill, status: str, *, trade_id: int | None = None,
              manual_trade_id: int | None = None) -> JournalBrokerFillRecord:
    return JournalBrokerFillRecord(tenant_id=user.tenant_id, user_id=user.id, broker=f.broker, fill_id=f.fill_id, order_id=f.order_id,
                                   symbol=f.symbol, side=f.side, quantity=f.quantity, price=f.price, filled_at=f.at, status=status,
                                   trade_id=trade_id, manual_trade_id=manual_trade_id)


async def sync_user(session: AsyncSession, user: User, *, now: datetime | None = None) -> dict[str, Any]:
    """Reads his own broker sessions' trade books and journals what is new. Returns the counts and plain messages."""
    from app.market_data import entitlement
    now = now or datetime.now(UTC)
    records = await entitlement.own_records(session, user)
    fills: list[BrokerFill] = []
    messages: list[str] = []
    for record in records:
        got, message = await _read_book(record, now)
        fills.extend(got)
        if message:
            messages.append(message)
    summary: dict[str, Any] = {"brokers": len(records), "read": len(fills), "matched": 0, "added": 0, "closed": 0,
                               "already": 0, "messages": messages, "entry_ids": []}
    if not records:
        summary["messages"] = ["Sign in to your broker under Settings to bring its trades into your journal."]
        return summary
    if not fills:
        return summary
    own = await _own_orders(session, user, {f.order_id for f in fills if f.order_id})
    seen = await _seen(session, user, fills)
    open_rows = await _open_entries(session, user)
    plan = plan_sync(fills, own, seen, [OpenEntry(r.id, r.symbol, r.direction, float(r.quantity), entries._utc(r.entry_time) or now)
                                        for r in open_rows])
    by_id = {r.id: r for r in open_rows}
    try:
        for f, trade_id in plan.matched:
            session.add(_fill_row(user, f, MATCHED, trade_id=trade_id))
        for c in plan.closes:
            row = by_id[c.entry_id]
            if c.rest > EPS:                             # a partial close: the open rest becomes its own entry
                session.add(JournalManualTradeRecord(
                    tenant_id=user.tenant_id, user_id=user.id, origin=ORIGIN, symbol=row.symbol, exchange=row.exchange,
                    instrument_kind=row.instrument_kind, direction=row.direction, quantity=c.rest, entry_time=row.entry_time,
                    entry_price=row.entry_price, charges=0.0))
                row.quantity = c.quantity
            row.exit_time, row.exit_price = c.exit_time, c.exit_price
            for f in c.fills:
                session.add(_fill_row(user, f, ENTRY, manual_trade_id=row.id))
        existing = await importer._existing_keys(session, user, [t for t, _ in plan.trades])
        added: list[JournalManualTradeRecord] = []
        for t, used in plan.trades:
            new_row: JournalManualTradeRecord | None = None
            if t.key() not in existing:
                new_row = JournalManualTradeRecord(tenant_id=user.tenant_id, user_id=user.id, origin=ORIGIN, **entries.manual_values(t.body(), now=now + timedelta(minutes=10)))
                session.add(new_row)
                await session.flush()
                added.append(new_row)
            for f in used:
                session.add(_fill_row(user, f, ENTRY, manual_trade_id=new_row.id if new_row is not None else None))
        await session.commit()
    except IntegrityError:
        await session.rollback()                         # another sync stored these fills a moment ago
        summary["messages"] = messages + ["Your broker's trades were just brought in. Nothing new to add."]
        return summary
    summary.update(matched=len(plan.matched), added=len(added), closed=len(plan.closes), already=plan.seen,
                   entry_ids=[r.id for r in added])
    return summary


# --- the worker's daily run ---------------------------------------------------------------------------------------------

async def _flags_on(session: AsyncSession, tenant_id: int, memo: dict[int, bool]) -> bool:
    if tenant_id not in memo:
        from app.journal import FLAG
        from app.platform.controls import flag_enabled
        memo[tenant_id] = await flag_enabled(session, "ui_v2", tenant_id) and await flag_enabled(session, FLAG, tenant_id)
    return memo[tenant_id]


def due_day(now: datetime, holidays: set[Any]) -> str | None:
    """The IST trading day whose sync is due now (after SYNC_AFTER), else None."""
    from app.market_data.calendar import IST, is_trading_day
    ist = now.astimezone(IST)
    if ist.time() < SYNC_AFTER or not is_trading_day(ist.date(), holidays):
        return None
    return ist.date().isoformat()


async def work_waiting(session: AsyncSession, now: datetime | None = None) -> bool:
    """Cheap: is a daily sync due now (after the close on a trading day), is the journal on for anyone, and has anyone
    linked a broker session? The worker starts the background task only then."""
    from app.journal import FLAG
    from app.market_data.calendar import family_holidays
    from app.platform.controls import feature_flags
    now = now or datetime.now(UTC)
    entry = (await feature_flags(session)).get(FLAG) or {}
    if not (entry.get("on") or entry.get("tenants")):
        return False
    if due_day(now, set(await family_holidays(session, "NSE"))) is None:
        return False
    return await session.scalar(select(BrokerCredentialRecord.id).limit(1)) is not None


async def run_due(session: AsyncSession, now: datetime | None = None) -> int:
    """Once a trading day after the close: syncs every user with a linked broker session whose organisation has the
    journal on. Returns how many users were synced. One user's failure never stops the others."""
    from app.market_data.calendar import family_holidays
    now = now or datetime.now(UTC)
    day = due_day(now, set(await family_holidays(session, "NSE")))
    if day is None:
        return 0
    pairs = sorted({(r.tenant_id, r.user_id) for r in await session.scalars(select(BrokerCredentialRecord))})
    marks = {(r.tenant_id, r.user_id): r for r in await session.scalars(select(UserPrefsRecord).where(UserPrefsRecord.namespace == MARKS))}
    memo: dict[int, bool] = {}
    done = 0
    for tenant_id, user_id in pairs:
        mark = marks.get((tenant_id, user_id))
        stored_day, tries = _mark(mark.value_json if mark is not None else None)
        if stored_day == day and (tries == 0 or tries >= MAX_TRIES):
            continue                                     # done today (0 = clean), or tried MAX_TRIES times
        if not await _flags_on(session, tenant_id, memo):
            continue
        user = await session.get(User, user_id)
        if user is None or not user.is_active or user.tenant_id != tenant_id:
            continue
        try:
            summary = await sync_user(session, user, now=now)
            failed = bool(summary["messages"]) and summary["read"] == 0
            value = json.dumps({"day": day, "tries": (tries + 1 if stored_day == day else 1) if failed else 0})
            if mark is None:
                session.add(UserPrefsRecord(tenant_id=tenant_id, user_id=user_id, namespace=MARKS, value_json=value, version=1, updated_at=now))
            else:
                mark.value_json, mark.version, mark.updated_at = value, mark.version + 1, now
            await session.commit()
            done += 1
        except Exception as exc:  # noqa: BLE001 - retried on the next run
            logger.warning("journal broker sync for user %s failed: %s", user_id, type(exc).__name__)
            await session.rollback()
            marks = {(r.tenant_id, r.user_id): r for r in await session.scalars(
                select(UserPrefsRecord).where(UserPrefsRecord.namespace == MARKS))}
    return done


def _mark(raw: str | None) -> tuple[str | None, int]:
    """(the IST day of the last run, how many failed reads that day; 0 = it went through)."""
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return None, 0
    if not isinstance(value, dict) or not value.get("day"):
        return None, 0
    try:
        tries = int(value.get("tries") or 0)
    except (TypeError, ValueError):
        tries = 0
    return str(value["day"]), tries


__all__ = ["ORIGIN", "LABEL", "BrokerFill", "OpenEntry", "plan_sync", "to_fill", "sync_user", "run_due", "due_day", "work_waiting"]
