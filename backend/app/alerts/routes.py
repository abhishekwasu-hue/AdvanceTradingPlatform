from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.channels import decrypt_raw, encrypt_config, masked_summary, merge_secrets, parse_config
from app.alerts.dispatcher import send_via_channel
from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_trader
from app.core.enums import AlertChannelType, NotificationSeverity, NotificationType
from app.db.models import AlertChannelRecord, AlertDeliveryRecord, NotificationRecord, User
from app.db.session import get_session

router = APIRouter(prefix="/api/alert-channels", tags=["alerts"])

can_manage = require_trader


class AlertChannelResponse(BaseModel):
    channel_type: str
    enabled: bool
    min_severity: str
    config: Dict[str, Any]
    last_delivered_at: Optional[str]
    last_error: Optional[str]
    updated_at: str

    @classmethod
    def from_record(cls, record: AlertChannelRecord) -> "AlertChannelResponse":
        return cls(
            channel_type=record.channel_type, enabled=record.enabled, min_severity=record.min_severity,
            config=masked_summary(record),
            last_delivered_at=record.last_delivered_at.isoformat() if record.last_delivered_at else None,
            last_error=record.last_error, updated_at=record.updated_at.isoformat(),
        )


class AlertChannelUpsertRequest(BaseModel):
    enabled: bool = True
    min_severity: NotificationSeverity = NotificationSeverity.WARNING
    config: Dict[str, Any] = Field(default_factory=dict)


class AlertDeliveryResponse(BaseModel):
    id: int
    channel_type: str
    notification_id: int
    title: str
    severity: str
    status: str
    attempts: int
    last_error: Optional[str]
    created_at: str
    sent_at: Optional[str]


class TestSendResponse(BaseModel):
    ok: bool
    detail: str


def _channel_type_or_404(raw: str) -> str:
    try:
        return AlertChannelType(raw.upper()).value
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown channel type '{raw}'. Use TELEGRAM or EMAIL.") from exc


async def _get_channel(session: AsyncSession, tenant_id: int, channel_type: str) -> Optional[AlertChannelRecord]:
    return await session.scalar(
        select(AlertChannelRecord).where(
            AlertChannelRecord.tenant_id == tenant_id, AlertChannelRecord.channel_type == channel_type
        )
    )


@router.get("", response_model=List[AlertChannelResponse])
async def list_alert_channels(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> List[AlertChannelResponse]:
    rows = await session.scalars(
        select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == user.tenant_id).order_by(AlertChannelRecord.channel_type)
    )
    return [AlertChannelResponse.from_record(r) for r in rows]


@router.put("/{channel_type}", response_model=AlertChannelResponse)
async def upsert_alert_channel(
    channel_type: str, request: AlertChannelUpsertRequest,
    user: User = Depends(can_manage), session: AsyncSession = Depends(get_session),
) -> AlertChannelResponse:
    """Creates or updates the tenant's channel of this type. Secrets left blank on an update keep
    their stored value; the API never returns them."""
    kind = _channel_type_or_404(channel_type)
    record = await _get_channel(session, user.tenant_id, kind)
    existing_raw = decrypt_raw(record) if record is not None else None
    try:
        config = parse_config(kind, merge_secrets(kind, request.config, existing_raw))
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if record is None:
        record = AlertChannelRecord(tenant_id=user.tenant_id, channel_type=kind, created_by=user.id, encrypted_config=encrypt_config(config))
        session.add(record)
        event = "alert_channel_created"
    else:
        record.encrypted_config = encrypt_config(config)
        event = "alert_channel_updated"
    record.enabled = request.enabled
    record.min_severity = request.min_severity.value
    record.last_error = None
    await session.flush()
    await write_audit_log(session, user.tenant_id, user.id, event, f"{kind} min_severity={record.min_severity} enabled={record.enabled}")
    await session.commit()
    await session.refresh(record)
    return AlertChannelResponse.from_record(record)


@router.delete("/{channel_type}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert_channel(
    channel_type: str, user: User = Depends(can_manage), session: AsyncSession = Depends(get_session),
) -> None:
    kind = _channel_type_or_404(channel_type)
    record = await _get_channel(session, user.tenant_id, kind)
    if record is None:
        return
    await session.delete(record)
    await write_audit_log(session, user.tenant_id, user.id, "alert_channel_deleted", kind)
    await session.commit()


@router.post("/{channel_type}/test", response_model=TestSendResponse)
async def test_alert_channel(
    channel_type: str, user: User = Depends(can_manage), session: AsyncSession = Depends(get_session),
) -> TestSendResponse:
    """Sends a test message through the stored channel right now (not via the outbox) and reports
    the broker-style truth: the exact error if it failed."""
    kind = _channel_type_or_404(channel_type)
    record = await _get_channel(session, user.tenant_id, kind)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No {kind} channel configured")
    probe = NotificationRecord(
        tenant_id=user.tenant_id, user_id=user.id, event_type=NotificationType.SYSTEM_FAILURE.value,
        severity=NotificationSeverity.INFO.value, title="Test alert from Advance Trading Platform",
        message=f"If you can read this, {kind.lower()} alerts for your account are working. Sent by {user.email}.",
        created_at=datetime.now(timezone.utc),
    )
    try:
        await send_via_channel(record, probe)
    except Exception as exc:  # noqa: BLE001 - the whole point is to surface the error
        record.last_error = str(exc)[:500]
        await session.commit()
        return TestSendResponse(ok=False, detail=str(exc)[:500])
    record.last_delivered_at = datetime.now(timezone.utc)
    record.last_error = None
    await session.commit()
    return TestSendResponse(ok=True, detail=f"Test message sent via {kind}")


@router.get("/deliveries", response_model=List[AlertDeliveryResponse])
async def list_alert_deliveries(
    limit: int = 50, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> List[AlertDeliveryResponse]:
    """The outbox: was that CRITICAL alert actually delivered, and if not, why."""
    rows = await session.execute(
        select(AlertDeliveryRecord, AlertChannelRecord.channel_type, NotificationRecord.title, NotificationRecord.severity)
        .join(AlertChannelRecord, AlertChannelRecord.id == AlertDeliveryRecord.channel_id)
        .join(NotificationRecord, NotificationRecord.id == AlertDeliveryRecord.notification_id)
        .where(AlertDeliveryRecord.tenant_id == user.tenant_id)
        .order_by(AlertDeliveryRecord.id.desc()).limit(limit)
    )
    return [
        AlertDeliveryResponse(
            id=d.id, channel_type=channel_type, notification_id=d.notification_id, title=title, severity=severity,
            status=d.status, attempts=d.attempts, last_error=d.last_error, created_at=d.created_at.isoformat(),
            sent_at=d.sent_at.isoformat() if d.sent_at else None,
        )
        for d, channel_type, title, severity in rows
    ]
