"""TT-9 (owner, 24 Oct: the review's default) / TM-17's method: ONE split function for a partial close of a position.

A trade row holds one open position. Closing N of its M lots splits the row: the original row keeps the rest (and its id,
so its orders, armed exits and journal stay linked), and a NEW row of the same position holds the N lots that close. The
closed part books its own exit, P&L, charges and journal line through the one exit path (`close_position`); no column is
added (no migration). Partial exit and Reduce use it today; the profit ladder (TM-17) will use the same function.

The split is claimed atomically (a conditional UPDATE on the quantity the caller saw): two partial exits racing on the
same position never take more lots than are open. Nothing here prices, sends or blocks anything - it only divides a row.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PaperRestingOrderRecord, TradeRecord

# Columns never copied to the split row: its own identity and the exit fields (it is open until the caller closes it).
_NOT_COPIED = {"id", "created_at", "exit_time", "exit_price", "exit_reason", "pnl", "charges", "exit_order_id", "exit_sent_at",
               "reconciled_open_at", "exit_filled_quantity", "exit_filled_price", "contract_note_id"}
SPLIT_FROM = "split_from"


def not_a_split():
    """SQL filter: rows that are not the closed part of a partial exit (a split is not a new trade - trades-per-day
    counts it once, on the original row)."""
    return or_(TradeRecord.group_meta.is_(None), ~TradeRecord.group_meta.contains(f'"{SPLIT_FROM}"'))


class SplitError(ValueError):
    """The split cannot be made, in plain words (nothing changed)."""


def _meta(trade: TradeRecord) -> Dict[str, Any]:
    try:
        meta = json.loads(trade.group_meta or "{}")
    except ValueError:
        return {}
    return meta if isinstance(meta, dict) else {}


async def split_off(session: AsyncSession, trade: TradeRecord, quantity: float, *, marks: Dict[str, Any] | None = None) -> TradeRecord:
    """Takes `quantity` units off the open `trade` into a new open row and returns it (flushed, not committed; the caller
    commits and then closes it). `marks` are extra `group_meta` keys for the new row (e.g. the caller's idempotency tag).
    Raises SplitError when the position is closed, the quantity is not below the open quantity, or another change won."""
    if trade.exit_time is not None:
        raise SplitError("This position is already closed.")
    held = float(trade.quantity)
    if not 0 < quantity < held:
        raise SplitError("Only part of the open quantity can be split off.")
    rest = held - float(quantity)
    claimed = await session.execute(update(TradeRecord).where(
        TradeRecord.id == trade.id, TradeRecord.exit_time.is_(None), TradeRecord.quantity == held,
    ).values(quantity=rest).execution_options(synchronize_session=False))
    if getattr(claimed, "rowcount", 0) != 1:
        await session.rollback()
        raise SplitError("The position changed at the same moment (another exit or fill). Nothing was closed - refresh and try again.")
    trade.quantity = rest
    values = {c.key: getattr(trade, c.key) for c in TradeRecord.__table__.columns if c.key not in _NOT_COPIED}
    values["quantity"] = float(quantity)
    meta = _meta(trade)
    meta[SPLIT_FROM] = trade.id
    meta.update(marks or {})
    values["group_meta"] = json.dumps(meta)
    part = TradeRecord(**values)
    part.charges = 0.0
    session.add(part)
    # An armed exit of this position closes what is left: its quantity follows the position (it still closes all of it).
    await session.execute(update(PaperRestingOrderRecord).where(
        PaperRestingOrderRecord.trade_id == trade.id, PaperRestingOrderRecord.status == "RESTING",
    ).values(quantity=rest).execution_options(synchronize_session=False))
    await session.flush()
    return part
