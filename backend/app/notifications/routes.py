from datetime import datetime, timezone
from typing import List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import NotificationReadRecord, NotificationRecord, User
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
    def from_record(cls, record: NotificationRecord, read: bool) -> "NotificationResponse":
        return cls(
            id=record.id, event_type=record.event_type, severity=record.severity,
            title=record.title, message=record.message,
            related_trade_id=record.related_trade_id, related_order_id=record.related_order_id,
            read=read, created_at=record.created_at.isoformat(),
        )


def private_to_addressee():
    """BT1b: the notification kinds that carry one member's own broker data (a screen alert's live values) or concern
    only their own data session (the "Live data: log in to ..." reminder). Everything else - exits, emergency exits,
    system failures, risk rejections, loss limits - stays organisation-wide even when addressed to the acting user."""
    from app.brokers.login_reminder import DATA_TITLE_PREFIX
    from app.core.enums import NotificationType
    return or_(NotificationRecord.event_type == NotificationType.SCREEN_ALERT.value,
               and_(NotificationRecord.event_type == NotificationType.TOKEN_EXPIRED.value, NotificationRecord.title.startswith(DATA_TITLE_PREFIX)))


async def _visible(session: AsyncSession, user: User, query):
    """BT1b: with `byob_data_tier` on, a data-bearing notification addressed to one member is theirs only. Flag off: the
    organisation-wide feed, as before."""
    from app.platform.controls import flag_enabled
    if await flag_enabled(session, "byob_data_tier", user.tenant_id):
        return query.where(or_(NotificationRecord.user_id.is_(None), NotificationRecord.user_id == user.id, not_(private_to_addressee())))
    return query


async def _read_ids(session: AsyncSession, user_id: int) -> Set[int]:
    rows = await session.scalars(select(NotificationReadRecord.notification_id).where(NotificationReadRecord.user_id == user_id))
    return set(rows)


@router.get("", response_model=List[NotificationResponse])
async def list_notifications(
    unread_only: bool = False, limit: int = 200,
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> List[NotificationResponse]:
    """This tenant's full notification feed, most recent first. Read state is per user: one
    teammate reading an alert does not clear it for the others."""
    read_ids = await _read_ids(session, user.id)
    query = await _visible(session, user, select(NotificationRecord).where(NotificationRecord.tenant_id == user.tenant_id))
    if unread_only and read_ids:
        query = query.where(NotificationRecord.id.notin_(read_ids))
    rows = await session.scalars(query.order_by(NotificationRecord.created_at.desc()).limit(limit))
    return [NotificationResponse.from_record(r, r.id in read_ids) for r in rows]


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> NotificationResponse:
    record = await session.scalar(await _visible(session, user, select(NotificationRecord).where(
        NotificationRecord.id == notification_id, NotificationRecord.tenant_id == user.tenant_id)))
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown notification")
    existing = await session.scalar(select(NotificationReadRecord).where(
        NotificationReadRecord.notification_id == record.id, NotificationReadRecord.user_id == user.id,
    ))
    if existing is None:
        session.add(NotificationReadRecord(notification_id=record.id, user_id=user.id, read_at=datetime.now(timezone.utc)))
        await session.commit()
    return NotificationResponse.from_record(record, True)


class MarkAllReadResponse(BaseModel):
    marked_read: int


@router.post("/read-all", response_model=MarkAllReadResponse)
async def mark_all_notifications_read(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> MarkAllReadResponse:
    read_ids = await _read_ids(session, user.id)
    query = await _visible(session, user, select(NotificationRecord.id).where(NotificationRecord.tenant_id == user.tenant_id))
    if read_ids:
        query = query.where(NotificationRecord.id.notin_(read_ids))
    unread_ids = list(await session.scalars(query))
    now = datetime.now(timezone.utc)
    for notification_id in unread_ids:
        session.add(NotificationReadRecord(notification_id=notification_id, user_id=user.id, read_at=now))
    await session.commit()
    return MarkAllReadResponse(marked_read=len(unread_ids))
