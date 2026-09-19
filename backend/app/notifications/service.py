from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import NotificationRecord


async def notify(
    session: AsyncSession, tenant_id: int, event_type: NotificationType, title: str, message: str = "",
    severity: NotificationSeverity = NotificationSeverity.INFO, user_id: Optional[int] = None,
    related_trade_id: Optional[int] = None, related_order_id: Optional[int] = None,
) -> NotificationRecord:
    """The single choke point every other engine emits an in-app notification through - visible
    to the whole tenant, like every other tenant-shared resource. Never a fire-and-forget log
    line: every call to this persists a row the frontend feed and GET /api/notifications read.
    """
    record = NotificationRecord(
        tenant_id=tenant_id, user_id=user_id, event_type=event_type.value, severity=severity.value,
        title=title, message=message, related_trade_id=related_trade_id, related_order_id=related_order_id,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record
