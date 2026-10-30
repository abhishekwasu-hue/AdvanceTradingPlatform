"""B8: the caller's own phones for notifications from the AiAlgoDesk Android app (flag `mobile_push`, OFF by default).

No new table (readiness B8: no migration): the phones live in the organisation's FCM alert channel, inside its
encrypted config (`app/alerts/channels.FcmConfig`), the same way browser subscriptions live in the PUSH channel. The
channel is made here when the first phone registers; the organisation never sets it up by hand, and it does not count
against the plan's alert-channel limit.

Rules:
* every route answers 404 with `X-Feature-Disabled: mobile_push` while the flag is off for the caller's organisation;
* a member sees and changes only their own phones; another member's or organisation's device id answers 404;
* the FCM token is never returned (the API shows an opaque `id` derived from it);
* the same token registered by another member (a phone changing hands) is moved to the caller - in another
  organisation it is removed there first, never shared;
* registering needs the server's FCM key: without it the answer is 409 "not set up on this server yet".
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import fcm
from app.alerts.channels import FcmConfig, FcmDevice, decrypt_raw, encrypt_config
from app.alerts.push_kinds import PHONE_KINDS
from app.alerts.push_kinds import device_id as make_device_id
from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user
from app.core.enums import AlertChannelType, NotificationSeverity, NotificationType
from app.core.validation_errors import safe_validation_message
from app.db.models import AlertChannelRecord, NotificationRecord, User
from app.db.session import get_session
from app.secrets_store.envelope import ensure_tenant_key

logger = logging.getLogger(__name__)

FLAG = "mobile_push"
MAX_PHONES_PER_MEMBER = 5
MAX_PHONES_PER_ORGANISATION = 50
FCM = AlertChannelType.FCM.value

router = APIRouter(prefix="/api/mobile", tags=["mobile"])


async def require_mobile_push(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> User:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": FLAG})
    return user


class DeviceRegisterRequest(BaseModel):
    token: str = Field(min_length=20, max_length=4096)
    platform: Literal["android", "ios"] = "android"
    label: str = Field(default="Phone", max_length=60)
    kinds: Optional[List[str]] = Field(default=None, max_length=4)
    app_version: str = Field(default="", max_length=40)


class DeviceUpdateRequest(BaseModel):
    label: Optional[str] = Field(default=None, max_length=60)
    kinds: Optional[List[str]] = Field(default=None, max_length=4)


class DeviceView(BaseModel):
    id: str
    label: str
    platform: str
    kinds: List[str]
    app_version: str
    registered_at: str
    last_seen_at: str


class DevicesResponse(BaseModel):
    configured: bool
    kinds: List[Dict[str, str]]
    devices: List[DeviceView]


class TestResponse(BaseModel):
    ok: bool
    detail: str


def _view(device: FcmDevice) -> DeviceView:
    return DeviceView(id=device.device_id, label=device.label, platform=device.platform,
                      kinds=list(device.kinds) if device.kinds is not None else list(PHONE_KINDS),
                      app_version=device.app_version, registered_at=device.registered_at, last_seen_at=device.last_seen_at)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def _channel(session: AsyncSession, tenant_id: int, *, lock: bool = False) -> Optional[AlertChannelRecord]:
    query = select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant_id, AlertChannelRecord.channel_type == FCM)
    if lock:
        query = query.with_for_update()               # two phones registering at once never lose one another
    return await session.scalar(query)


async def _devices(session: AsyncSession, record: Optional[AlertChannelRecord]) -> List[FcmDevice]:
    if record is None:
        return []
    await ensure_tenant_key(session, record.tenant_id)
    try:
        return list(FcmConfig.model_validate(decrypt_raw(record)).devices)
    except (ValueError, ValidationError):
        logger.warning("The phone list of tenant %s cannot be read; it starts empty", record.tenant_id)
        return []


def _store(record: AlertChannelRecord, devices: List[FcmDevice]) -> None:
    record.encrypted_config = encrypt_config(FcmConfig(devices=devices), record.tenant_id)


def _own(devices: List[FcmDevice], user: User, device_id: str) -> FcmDevice:
    for device in devices:
        if device.device_id == device_id and device.user_id == user.id:
            return device
    raise HTTPException(status_code=404, detail="Phone not found")


def _check_kinds(kinds: Optional[List[str]]) -> Optional[List[str]]:
    if kinds is None:
        return None
    unknown = [k for k in kinds if k not in PHONE_KINDS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown notification kinds: {', '.join(unknown)}")
    return sorted(set(kinds))


async def _remove_elsewhere(session: AsyncSession, tenant_id: int, device_id: str) -> None:
    """A phone that changed hands to another organisation: its old registration there is removed first."""
    others = await session.scalars(select(AlertChannelRecord).where(AlertChannelRecord.channel_type == FCM,
                                                                    AlertChannelRecord.tenant_id != tenant_id))
    for record in others:
        devices = await _devices(session, record)
        kept = [d for d in devices if d.device_id != device_id]
        if len(kept) != len(devices):
            _store(record, kept)
            await write_audit_log(session, record.tenant_id, None, "mobile_device_moved_away", "a phone registered elsewhere")


@router.get("/devices", response_model=DevicesResponse)
async def list_devices(user: User = Depends(require_mobile_push), session: AsyncSession = Depends(get_session)) -> DevicesResponse:
    """The caller's own phones, and whether this server can send to phones at all."""
    devices = await _devices(session, await _channel(session, user.tenant_id))
    return DevicesResponse(configured=fcm.configured(), kinds=[{"id": k, "label": v} for k, v in PHONE_KINDS.items()],
                           devices=[_view(d) for d in devices if d.user_id == user.id])


@router.post("/devices", response_model=DeviceView)
async def register_device(request: DeviceRegisterRequest, user: User = Depends(require_mobile_push),
                          session: AsyncSession = Depends(get_session)) -> DeviceView:
    """Registers (or refreshes) the caller's phone. The app calls this on every start, so `last_seen_at` stays fresh."""
    if not fcm.configured():
        raise HTTPException(status_code=409, detail=fcm.NOT_SET_UP)          # a 4xx: nothing is wrong with the server
    kinds = _check_kinds(request.kinds)
    did = make_device_id(request.token)
    await ensure_tenant_key(session, user.tenant_id)
    await _remove_elsewhere(session, user.tenant_id, did)
    record = await _channel(session, user.tenant_id, lock=True)
    devices = await _devices(session, record)
    previous = next((d for d in devices if d.device_id == did), None)
    now = _now()
    try:
        device = FcmDevice(device_id=did, token=request.token, user_id=user.id, platform=request.platform,
                           label=(request.label or "Phone").strip() or "Phone",
                           kinds=kinds if request.kinds is not None else (previous.kinds if previous and previous.user_id == user.id else None),
                           app_version=request.app_version.strip(),
                           registered_at=previous.registered_at if previous and previous.user_id == user.id else now, last_seen_at=now)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=safe_validation_message(exc)) from exc
    devices = [d for d in devices if d.device_id != did]
    mine = sorted((d for d in devices if d.user_id == user.id), key=lambda d: d.last_seen_at or d.registered_at)
    while len(mine) >= MAX_PHONES_PER_MEMBER:          # the member's oldest phone makes room
        oldest = mine.pop(0)
        devices = [d for d in devices if d.device_id != oldest.device_id]
    if len(devices) >= MAX_PHONES_PER_ORGANISATION:
        raise HTTPException(status_code=409, detail=f"Your organisation already has {MAX_PHONES_PER_ORGANISATION} phones registered. "
                                                     "Remove one under Settings, then try again.")
    devices.append(device)
    if record is None:
        record = AlertChannelRecord(tenant_id=user.tenant_id, channel_type=FCM, created_by=user.id, enabled=True,
                                    min_severity=NotificationSeverity.INFO.value, encrypted_config="")
        session.add(record)
    _store(record, devices)
    try:
        await session.flush()
    except IntegrityError as exc:                      # another request made the channel at the same moment
        await session.rollback()
        raise HTTPException(status_code=409, detail="Please try again in a moment.") from exc
    if previous is None or previous.user_id != user.id:
        await write_audit_log(session, user.tenant_id, user.id, "mobile_device_registered", f"{request.platform} phone")
    await session.commit()
    return _view(device)


@router.patch("/devices/{device_id}", response_model=DeviceView)
async def update_device(device_id: str, request: DeviceUpdateRequest, user: User = Depends(require_mobile_push),
                        session: AsyncSession = Depends(get_session)) -> DeviceView:
    """The phone's name and which kinds it gets (signals, approvals, exits, renew reminders)."""
    record = await _channel(session, user.tenant_id, lock=True)
    devices = await _devices(session, record)
    device = _own(devices, user, device_id)
    changes: Dict[str, Any] = {}
    if request.label is not None:
        changes["label"] = request.label.strip() or "Phone"
    if "kinds" in request.model_fields_set:
        changes["kinds"] = _check_kinds(request.kinds)
    updated = device.model_copy(update=changes)
    if record is None:                                 # _own found a device, so the channel exists
        raise HTTPException(status_code=404, detail="Phone not found")
    _store(record, [updated if d.device_id == device_id else d for d in devices])
    await session.commit()
    return _view(updated)


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_device(device_id: str, user: User = Depends(require_mobile_push), session: AsyncSession = Depends(get_session)) -> Response:
    """Stops notifications to this phone (the app calls it on sign-out; Settings has a Remove button)."""
    record = await _channel(session, user.tenant_id, lock=True)
    devices = await _devices(session, record)
    _own(devices, user, device_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Phone not found")
    _store(record, [d for d in devices if d.device_id != device_id])
    await write_audit_log(session, user.tenant_id, user.id, "mobile_device_removed", "phone")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/devices/{device_id}/test", response_model=TestResponse)
async def test_device(device_id: str, user: User = Depends(require_mobile_push), session: AsyncSession = Depends(get_session)) -> TestResponse:
    """Sends a test notification to this phone now (not through the outbox) and says plainly whether it worked."""
    from app.alerts.dispatcher import DeliverySkipped, send_fcm
    record = await _channel(session, user.tenant_id)
    devices = await _devices(session, record)
    device = _own(devices, user, device_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Phone not found")
    probe = NotificationRecord(tenant_id=user.tenant_id, user_id=user.id, event_type=NotificationType.SYSTEM_FAILURE.value,
                               severity=NotificationSeverity.INFO.value, title="Test", message="", created_at=datetime.now(timezone.utc))
    try:
        await send_fcm(record, FcmConfig(devices=devices), probe, address=device.device_id, session=session)
    except DeliverySkipped as skipped:
        await session.commit()                         # a phone FCM reported gone was removed from the list
        return TestResponse(ok=False, detail=skipped.reason)
    except Exception as exc:  # noqa: BLE001 - the point is to show the reason (our own message, never a token)
        return TestResponse(ok=False, detail=str(exc)[:300] or "The test could not be sent. Try again in a moment.")
    return TestResponse(ok=True, detail="Sent. It should arrive on this phone in a few seconds.")


__all__ = ["router", "FLAG", "require_mobile_push"]
