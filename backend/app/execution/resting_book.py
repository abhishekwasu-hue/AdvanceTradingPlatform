"""TM-2 (owner, 23 Oct): the durable resting order book - the orders that wait for their price, owned by one user of one
organisation (`paper_resting_orders`). The one trigger engine (app/execution/trigger_engine.py) fires them; this module
only stores, validates, lists and cancels. Nothing here sends an order or reaches a broker.

* an ENTRY rests from the order ticket (`app/paper/fno_ticket.place` with "wait for my price"): every entry check ran
  when it was placed and runs AGAIN at fire time (kill switch, the order chokepoint, the risk checks - TM-23);
* an EXIT is armed on the user's own open position (`arm_exit`): a stop or a target on the premium or on the
  underlying (T-D2: the source is the order's own setting, never pre-filled), touch or close of his timeframe (TT-7).
  An exit is never refused by a kill switch or a limit (ADR-0004). When the position closes, its other armed exits are
  cancelled (one position, one close).

Idempotency: `client_ref` is unique per tenant and user - a second tap returns the first row, never a second order.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PaperRestingOrderRecord, TradeRecord, User
from app.trading import trigger_rules
from app.trading.trigger_rules import ABOVE, BELOW, CLOSE, TOUCH

RESTING, FIRING, FILLED, REJECTED, CANCELLED, EXPIRED = "RESTING", "FIRING", "FILLED", "REJECTED", "CANCELLED", "EXPIRED"
FINAL = (FILLED, REJECTED, CANCELLED, EXPIRED)
ENTRY, EXIT = "ENTRY", "EXIT"
LIMIT, STOP, TARGET = "LIMIT", "STOP", "TARGET"
PREMIUM, SPOT = "PREMIUM", "SPOT"
WAITING_TYPES = ("LIMIT", "SL", "SL-M")
EXIT_TYPES = ("LIMIT", "SL-M", "MARKET")
MAX_TIMEFRAME = 24 * 60
MAX_TRAIL = 50
IST = timezone(timedelta(hours=5, minutes=30))
DAY_END = time(15, 30)       # an entry rests for the day it was placed (the exchange's DAY validity)
STATUS_WORDS = {
    RESTING: "Waiting for your price", FIRING: "Sending", FILLED: "Filled", REJECTED: "Not placed",
    CANCELLED: "Cancelled", EXPIRED: "Expired at the end of the day",
}


class RestingOrderError(ValueError):
    """A resting order that cannot be kept, in plain words."""


@dataclass
class Trigger:
    """The trigger settings of one order (T-D2 / TT-7), all the user's own."""
    source: str = PREMIUM            # PREMIUM | SPOT
    mode: str = TOUCH                # TOUCH | CLOSE
    timeframe_minutes: Optional[int] = None
    spot_level: Optional[float] = None
    spot_side: Optional[str] = None  # ABOVE | BELOW (SPOT only: which way the underlying must go)


def trigger_errors(trigger: Trigger) -> List[str]:
    errors: List[str] = []
    if trigger.source not in (PREMIUM, SPOT):
        errors.append("Choose whether the order watches the option's price or the underlying.")
    if trigger.mode not in (TOUCH, CLOSE):
        errors.append("Choose whether the order fires on a touch or on a candle close.")
    if trigger.mode == CLOSE and not (trigger.timeframe_minutes and 1 <= trigger.timeframe_minutes <= MAX_TIMEFRAME):
        errors.append("A candle-close order needs its timeframe in minutes.")
    if trigger.source == SPOT:
        if trigger.spot_level is None or trigger.spot_level <= 0:
            errors.append("Set the underlying's level this order watches.")
        if trigger.spot_side not in (ABOVE, BELOW):
            errors.append("Say whether the underlying must go above or below your level.")
    return errors


def day_end(now: datetime) -> datetime:
    """The end of the IST day's session the order was placed in (a DAY order)."""
    ist = now.astimezone(IST)
    end = datetime.combine(ist.date(), DAY_END, tzinfo=IST)
    if ist >= end:
        end = end + timedelta(days=1)
    return end.astimezone(timezone.utc)


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(timezone.utc)


def add_trail(row: PaperRestingOrderRecord, status: str, note: str, now: Optional[datetime] = None) -> None:
    """One line of the order trail (oldest first, the last MAX_TRAIL kept)."""
    try:
        trail = json.loads(row.trail_json) if row.trail_json else []
    except (TypeError, ValueError):
        trail = []
    trail.append({"at": _now(now).isoformat(), "status": status, "note": note})
    row.trail_json = json.dumps(trail[-MAX_TRAIL:], separators=(",", ":"))


def trail(row: PaperRestingOrderRecord) -> List[Dict[str, Any]]:
    try:
        loaded = json.loads(row.trail_json) if row.trail_json else []
    except (TypeError, ValueError):
        return []
    return loaded if isinstance(loaded, list) else []


def view(row: PaperRestingOrderRecord) -> Dict[str, Any]:
    """What the user sees of his own resting order (no internal fields)."""
    return {
        "id": row.id, "client_ref": row.client_ref, "mode": row.mode, "purpose": row.purpose, "kind": row.kind,
        "trade_id": row.trade_id, "symbol": row.symbol, "side": row.side, "quantity": row.quantity,
        "order_type": row.order_type, "price": row.price, "trigger_price": row.trigger_price,
        "trigger": {"source": row.trigger_source, "symbol": row.trigger_symbol, "level": row.trigger_level,
                    "side": row.trigger_side, "mode": row.trigger_mode, "timeframe_minutes": row.timeframe_minutes},
        "status": row.status, "status_words": STATUS_WORDS.get(row.status, row.status), "note": row.status_note,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "last_price": row.last_price, "fired_at": row.fired_at.isoformat() if row.fired_at else None,
        "fill_price": row.fill_price, "order_id": row.order_id, "trail": trail(row),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def by_ref(session: AsyncSession, tenant_id: int, user_id: int, client_ref: str) -> Optional[PaperRestingOrderRecord]:
    return await session.scalar(select(PaperRestingOrderRecord).where(
        PaperRestingOrderRecord.tenant_id == tenant_id, PaperRestingOrderRecord.user_id == user_id,
        PaperRestingOrderRecord.client_ref == client_ref))


def _level_and_side(side: str, order_type: str, price: Optional[float], trigger_price: Optional[float],
                    trigger: Trigger) -> tuple[float, str]:
    if trigger.source == SPOT:
        assert trigger.spot_level is not None and trigger.spot_side is not None
        return float(trigger.spot_level), trigger.spot_side
    level = price if order_type == "LIMIT" else trigger_price
    if level is None:
        raise RestingOrderError("A waiting order needs its price.")
    return float(level), trigger_rules.order_trigger_side(side, order_type)


async def rest_entry(session: AsyncSession, user: User, *, client_ref: str, symbol: str, exchange: str, side: str,
                     quantity: float, order_type: str, price: Optional[float], trigger_price: Optional[float],
                     ask: Dict[str, Any], origin: str, mode: str = "PAPER", trigger: Optional[Trigger] = None,
                     now: Optional[datetime] = None) -> PaperRestingOrderRecord:
    """Keeps one ENTRY that waits for its price (the ticket checked it first). Same `client_ref` -> the first row."""
    trigger = trigger or Trigger()
    found = await by_ref(session, user.tenant_id, user.id, client_ref)
    if found is not None:
        return found
    problems = trigger_errors(trigger)
    if trigger.source == SPOT:
        problems.append("An entry waits on its own price in this version; an underlying level is for exits.")
    if order_type not in WAITING_TYPES:
        problems.append("Only a limit or a stop order waits for its price.")
    if problems:
        raise RestingOrderError(" ".join(problems))
    level, level_side = _level_and_side(side, order_type, price, trigger_price, trigger)
    now = _now(now)
    row = PaperRestingOrderRecord(
        tenant_id=user.tenant_id, user_id=user.id, client_ref=client_ref, mode=mode, purpose=ENTRY, kind=LIMIT,
        symbol=symbol, exchange=exchange, side=side, quantity=float(quantity), order_type=order_type, price=price,
        trigger_price=trigger_price, trigger_source=PREMIUM, trigger_symbol=symbol, trigger_exchange=exchange,
        trigger_level=level, trigger_side=level_side, trigger_mode=trigger.mode,
        timeframe_minutes=trigger.timeframe_minutes if trigger.mode == CLOSE else None,
        payload_json=json.dumps({"ask": ask, "origin": origin}, default=str, separators=(",", ":")),
        status=RESTING, expires_at=day_end(now), created_at=now, updated_at=now)
    add_trail(row, RESTING, f"Waiting for your price {level:,.2f}", now)
    session.add(row)
    await session.flush()
    return row


async def arm_exit(session: AsyncSession, user: User, trade: TradeRecord, *, client_ref: str, kind: str, order_type: str,
                   level: float, trigger: Optional[Trigger] = None, now: Optional[datetime] = None) -> PaperRestingOrderRecord:
    """Arms a STOP or a TARGET on the user's own open position: the whole position closes when it fires (a partial exit
    is TT-9). `level` is the premium level (PREMIUM) or ignored for SPOT, whose level is the trigger's own."""
    from app.trading.position_monitor import exchange_for_trade, underlying_exchange
    trigger = trigger or Trigger()
    if trade.tenant_id != user.tenant_id or trade.user_id != user.id:
        raise RestingOrderError("This is not your position.")
    if trade.exit_time is not None:
        raise RestingOrderError("This position is already closed.")
    found = await by_ref(session, user.tenant_id, user.id, client_ref)
    if found is not None:
        return found
    problems = trigger_errors(trigger)
    if kind not in (STOP, TARGET):
        problems.append("Choose a stop or a target.")
    if order_type not in EXIT_TYPES:
        problems.append("An exit waits as a stop (SL-M) or a limit target.")
    if trigger.source == PREMIUM:
        if order_type == "MARKET":
            problems.append("A market exit on the option's own price is placed now with Exit, not kept waiting.")
        if kind == STOP and order_type != "SL-M":
            problems.append("A stop on the option's price waits as SL-M (it always fills when reached).")
        if kind == TARGET and order_type != "LIMIT":
            problems.append("A target on the option's price waits as a limit.")
        if level is None or level <= 0:
            problems.append("Set the price of this exit.")
    if problems:
        raise RestingOrderError(" ".join(problems))
    exit_side = "SELL" if trade.direction == "LONG" else "BUY"
    exchange = exchange_for_trade(trade)
    now = _now(now)
    if trigger.source == SPOT:
        underlying = trade.underlying_symbol
        if not underlying:
            raise RestingOrderError("This position has no underlying to watch.")
        assert trigger.spot_level is not None and trigger.spot_side is not None
        t_symbol, t_exchange, t_level, t_side = underlying, underlying_exchange(underlying), float(trigger.spot_level), trigger.spot_side
        order_type, price, trigger_price = "MARKET", None, None
    else:
        t_symbol, t_exchange = trade.symbol, exchange
        price = float(level) if order_type == "LIMIT" else None
        trigger_price = float(level) if order_type == "SL-M" else None
        t_level, t_side = float(level), trigger_rules.order_trigger_side(exit_side, order_type)
    row = PaperRestingOrderRecord(
        tenant_id=user.tenant_id, user_id=user.id, client_ref=client_ref, mode=trade.mode, purpose=EXIT, kind=kind,
        trade_id=trade.id, symbol=trade.symbol, exchange=exchange, side=exit_side, quantity=float(trade.quantity),
        order_type=order_type, price=price, trigger_price=trigger_price, trigger_source=trigger.source,
        trigger_symbol=t_symbol, trigger_exchange=t_exchange, trigger_level=t_level, trigger_side=t_side,
        trigger_mode=trigger.mode, timeframe_minutes=trigger.timeframe_minutes if trigger.mode == CLOSE else None,
        status=RESTING, created_at=now, updated_at=now)
    what = "underlying" if trigger.source == SPOT else "price"
    add_trail(row, RESTING, f"{kind.title()} armed: {what} {'at or above' if t_side == ABOVE else 'at or below'} {t_level:,.2f}"
              + (f" on a {trigger.timeframe_minutes}-minute close" if trigger.mode == CLOSE else ""), now)
    session.add(row)
    await session.flush()
    return row


async def list_for_user(session: AsyncSession, user: User, *, include_final: bool = False, limit: int = 100) -> List[PaperRestingOrderRecord]:
    query = select(PaperRestingOrderRecord).where(PaperRestingOrderRecord.tenant_id == user.tenant_id,
                                                  PaperRestingOrderRecord.user_id == user.id)
    if not include_final:
        query = query.where(PaperRestingOrderRecord.status.in_((RESTING, FIRING)))
    return list(await session.scalars(query.order_by(PaperRestingOrderRecord.id.desc()).limit(limit)))


async def cancel(session: AsyncSession, user: User, row_id: int, now: Optional[datetime] = None) -> PaperRestingOrderRecord:
    """Cancels the user's own waiting order. One already sent or done is left as it is (said so)."""
    row = await session.get(PaperRestingOrderRecord, row_id)
    if row is None or row.tenant_id != user.tenant_id or row.user_id != user.id:
        raise LookupError("No such waiting order")
    now = _now(now)
    done = await session.execute(update(PaperRestingOrderRecord).where(
        PaperRestingOrderRecord.id == row.id, PaperRestingOrderRecord.status == RESTING).values(
        status=CANCELLED, status_note="Cancelled by you", updated_at=now).execution_options(synchronize_session=False))
    await session.refresh(row)
    if done.rowcount == 1:
        add_trail(row, CANCELLED, "Cancelled by you", now)
    await session.commit()
    return row


async def cancel_for_trade(session: AsyncSession, trade_id: int, note: str, now: Optional[datetime] = None) -> int:
    """The other armed exits of a position that has closed (one position, one close)."""
    rows = list(await session.scalars(select(PaperRestingOrderRecord).where(
        PaperRestingOrderRecord.trade_id == trade_id, PaperRestingOrderRecord.status == RESTING)))
    for row in rows:
        row.status, row.status_note = CANCELLED, note
        add_trail(row, CANCELLED, note, now)
    return len(rows)
