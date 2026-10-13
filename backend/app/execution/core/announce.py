"""N1 X2: the audit row and the owner's notification of one Auto PAPER entry - written exactly once per intent.

The dispatcher writes them with the intent's DONE. If the worker died between the PAPER fill and that write, the retry,
the expiry or the stale sweep links the order and calls `placed_once`, which writes them if they are not there yet
(review of X2, L3). Facts only, never advice; nothing here places, changes or closes an order.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import AuditLogRecord, ExecutionIntentRecord

EVENT = "scan_auto_paper_entry"


def _money(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:,.2f}"


async def already(session: AsyncSession, tenant_id: int, intent_id: int) -> bool:
    return await session.scalar(select(AuditLogRecord.id).where(AuditLogRecord.tenant_id == tenant_id, AuditLogRecord.event == EVENT,
                                                                AuditLogRecord.detail.like(f"intent #{intent_id} %")).limit(1)) is not None


async def write(session: AsyncSession, *, tenant_id: int, owner_id: int, intent_id: int, direction: str, symbol: str, strategy_id: str,
                source_ref: str, order_id: int, price: float, stop_loss: Optional[float], target1: Optional[float]) -> None:
    """The audit row and the notification (the caller commits)."""
    from app.audit.log import write_audit_log
    from app.notifications.service import notify
    await write_audit_log(session, tenant_id, owner_id, EVENT,
                          f"intent #{intent_id} {direction} {symbol} at {price:g}: PAPER order #{order_id} ({source_ref})"[:500])
    await notify(
        session, tenant_id, NotificationType.SCREEN_ALERT, title=f"Auto PAPER entry: {direction} {symbol} (PAPER)",
        message=(f"Auto PAPER entry placed: {direction} {symbol} at {_money(price)} (stop {_money(stop_loss)}, target {_money(target1)}) "
                 f"from {strategy_id}. PAPER only. Exits run as usual; Exit now is on the Positions page."),
        severity=NotificationSeverity.WARNING, user_id=owner_id,
        metadata_json=json.dumps({"execution_intent_id": intent_id, "source_ref": source_ref, "order_id": order_id}))


async def placed_once(session: AsyncSession, intent_id: int, order: Any) -> None:
    """For a linked order that filled: the audit row and the notification, unless the dispatcher wrote them already."""
    row = await session.get(ExecutionIntentRecord, intent_id)
    if row is None or row.user_id is None or await already(session, row.tenant_id, intent_id):
        return
    try:
        signal = json.loads(row.signal_json or "{}")
    except ValueError:
        signal = {}
    price = float(signal.get("entry") or 0.0)
    if getattr(order, "trade_id", None) is not None:
        from app.db.models import TradeRecord
        trade = await session.get(TradeRecord, order.trade_id)
        if trade is not None:
            price = float(trade.entry_price)
    await write(session, tenant_id=row.tenant_id, owner_id=row.user_id, intent_id=intent_id, direction=row.direction, symbol=row.symbol,
                strategy_id=row.strategy_id, source_ref=row.source_ref, order_id=int(order.id), price=price,
                stop_loss=signal.get("stop_loss"), target1=signal.get("target1"))
