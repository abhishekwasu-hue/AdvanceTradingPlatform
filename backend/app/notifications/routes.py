from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import NotificationRecord, User
from app.db.session import get_session

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class NotificationResponse(BaseModel):
    id: int
    event_type: str
    severity: str
    title: str
    message: str
    related_trade_id: Optional[int]
    related_order_id: Optional[int]
    read: bool
    created_at: str

    @classmethod
    def from_record(cls, record: NotificationRecord) -> "NotificationResponse":
        return cls(
            id=record.id, event_type=record.event_type, severity=record.severity,
            title=record.title, message=record.message,
            related_trade_id=record.related_trade_id, related_order_id=record.related_order_id,
            read=record.read_at is not None, created_at=record.created_at.isoformat(),
        )


@router.get("", response_model=List[NotificationResponse])
async def list_notifications(
    unread_only: bool = False, limit: int = 200,
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> List[NotificationResponse]:
    """This tenant's full notification feed, most recent first."""
    query = select(NotificationRecord).where(NotificationRecord.tenant_id == user.tenant_id)
    if unread_only:
        query = query.where(NotificationRecord.read_at.is_(None))
    rows = await session.scalars(query.order_by(NotificationRecord.created_at.desc()).limit(limit))
    return [NotificationResponse.from_record(r) for r in rows]


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> NotificationResponse:
    record = await session.get(NotificationRecord, notification_id)
    if record is None or record.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Unknown notification")
    if record.read_at is None:
        record.read_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(record)
    return NotificationResponse.from_record(record)


class MarkAllReadResponse(BaseModel):
    marked_read: int


@router.post("/read-all", response_model=MarkAllReadResponse)
async def mark_all_notifications_read(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> MarkAllReadResponse:
    rows = list(
        await session.scalars(
            select(NotificationRecord).where(
                NotificationRecord.tenant_id == user.tenant_id, NotificationRecord.read_at.is_(None)
            )
        )
    )
    now = datetime.now(timezone.utc)
    for record in rows:
        record.read_at = now
    await session.commit()
    return MarkAllReadResponse(marked_read=len(rows))
