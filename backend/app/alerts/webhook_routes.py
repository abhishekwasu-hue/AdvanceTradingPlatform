"""`/api/alert-webhooks` - AUD-G2.5: each member's own signed webhooks (Settings › Alerts › Webhooks; behind `ui_v2` +
`alert_webhooks`, 404 while either is off, X-Feature-Disabled names the flag).

* `GET    /api/alert-webhooks`            - his own webhooks (never a secret) and how many more he may add;
* `POST   /api/alert-webhooks`            - add one; the answer carries the secret ONCE (stored encrypted, never shown again);
* `DELETE /api/alert-webhooks/{id}`       - remove one of his own;
* `POST   /api/alert-webhooks/{id}/test`  - a signed test POST right now, with the receiver's answer.

Per member: another member's webhook answers 404 here, exactly like one that does not exist. Nothing here touches an
order.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import signed_webhooks
from app.alerts.channels import decrypt_config, encrypt_config
from app.alerts.signed_webhooks import SignedWebhook, SignedWebhookConfig, WebhookRefused
from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_trader
from app.auth.scopes import require_scope
from app.core.enums import AlertChannelType, NotificationSeverity, NotificationType
from app.core.validation_errors import safe_validation_message
from app.db.models import AlertChannelRecord, NotificationRecord, User
from app.db.session import get_session
from app.plans.limits import check_can_add_alert_channel, load_tenant

router = APIRouter(prefix="/api/alert-webhooks", tags=["alerts"])
KIND = AlertChannelType.SIGNED_WEBHOOK.value


async def require_flags(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> None:
    from app.platform.controls import flag_enabled
    for name in (signed_webhooks.PAGE_FLAG, signed_webhooks.FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


class WebhookCreate(BaseModel):
    url: str = Field(min_length=1, max_length=signed_webhooks.MAX_URL_LENGTH)
    label: str = Field(default="", max_length=60)
    event_types: List[str] = Field(default_factory=list, max_length=30)
    min_severity: NotificationSeverity = NotificationSeverity.INFO


def _cap() -> int:
    from app.core import config
    return int(config.ALERT_WEBHOOKS_MAX_PER_USER)


async def _channel(session: AsyncSession, tenant_id: int, *, lock: bool = False) -> Optional[AlertChannelRecord]:
    query = select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant_id, AlertChannelRecord.channel_type == KIND)
    return await session.scalar(query.with_for_update() if lock else query)


def _config(record: Optional[AlertChannelRecord]) -> SignedWebhookConfig:
    if record is None:
        return SignedWebhookConfig()
    try:
        return decrypt_config(record)  # type: ignore[return-value]
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Your webhooks cannot be read right now; ask the platform admin") from exc


def _own(config: SignedWebhookConfig, user: User) -> List[SignedWebhook]:
    return [e for e in config.endpoints if e.user_id == user.id]


def _find(config: SignedWebhookConfig, user: User, webhook_id: str) -> SignedWebhook:
    hook = next((e for e in _own(config, user) if e.id == webhook_id), None)
    if hook is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return hook


def _listing(config: SignedWebhookConfig, user: User) -> Dict[str, Any]:
    own = _own(config, user)
    return {"webhooks": [signed_webhooks.summary(e) for e in own], "max": _cap(), "left": max(0, _cap() - len(own))}


@router.get("", dependencies=[Depends(require_flags)])
async def list_webhooks(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    return _listing(_config(await _channel(session, user.tenant_id)), user)


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_flags), Depends(require_scope("alerts:write"))])
async def add_webhook(body: WebhookCreate, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    try:
        url = signed_webhooks.checked_url(body.url)
        await signed_webhooks.check_resolved(url)               # the name must resolve to public addresses now, too
    except WebhookRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record = await _channel(session, user.tenant_id, lock=True)
    config = _config(record)
    if len(_own(config, user)) >= _cap():
        raise HTTPException(status_code=409, detail=f"You can add up to {_cap()} webhooks; remove one first")
    secret = signed_webhooks.new_secret()
    try:
        hook = SignedWebhook(id=signed_webhooks.new_id(), user_id=user.id, label=" ".join(body.label.split()), url=url, secret=secret,
                             event_types=[str(t).strip().upper() for t in body.event_types if str(t).strip()],
                             min_severity=body.min_severity.value, created_at=datetime.now(timezone.utc).isoformat())
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=safe_validation_message(exc)) from exc
    known = {t.value for t in NotificationType}
    unknown = [t for t in hook.event_types if t not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown notification type: {', '.join(unknown[:3])}")
    config = SignedWebhookConfig(endpoints=[*config.endpoints, hook])
    if record is None:
        await check_can_add_alert_channel(session, await load_tenant(session, user.tenant_id))
        record = AlertChannelRecord(tenant_id=user.tenant_id, channel_type=KIND, created_by=user.id, enabled=True,
                                    min_severity=NotificationSeverity.INFO.value,   # each webhook has its own floor
                                    encrypted_config=encrypt_config(config, user.tenant_id))  # type: ignore[arg-type]
        session.add(record)
    else:
        record.encrypted_config = encrypt_config(config, user.tenant_id)  # type: ignore[arg-type]
    await session.flush()
    host = (urlsplit(url).hostname or "")[:120]
    await write_audit_log(session, user.tenant_id, user.id, "alert_webhook_added", f"webhook {hook.id} to {host}")
    await session.commit()
    # The ONLY time the secret leaves the server.
    return {**signed_webhooks.summary(hook), "secret": secret}


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_flags), Depends(require_scope("alerts:write"))])
async def remove_webhook(webhook_id: str, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> None:
    record = await _channel(session, user.tenant_id, lock=True)
    config = _config(record)
    hook = _find(config, user, webhook_id)
    remaining = [e for e in config.endpoints if e.id != hook.id]
    assert record is not None
    record.encrypted_config = encrypt_config(SignedWebhookConfig(endpoints=remaining), user.tenant_id)  # type: ignore[arg-type]
    await write_audit_log(session, user.tenant_id, user.id, "alert_webhook_removed", f"webhook {hook.id}")
    await session.commit()


@router.post("/{webhook_id}/test", dependencies=[Depends(require_flags), Depends(require_scope("alerts:write"))])
async def test_webhook(webhook_id: str, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """A signed test POST right now (not through the outbox), with the receiver's answer or the plain reason it failed."""
    hook = _find(_config(await _channel(session, user.tenant_id)), user, webhook_id)
    probe = NotificationRecord(tenant_id=user.tenant_id, user_id=user.id, event_type=NotificationType.SYSTEM_FAILURE.value,
                               severity=NotificationSeverity.INFO.value, title="Test from AiAlgoDesk",
                               message="If your receiver checks this signature, your webhook is working.",
                               created_at=datetime.now(timezone.utc))
    try:
        code = await signed_webhooks.post(hook, probe, event_id=signed_webhooks.event_id_for(None, None, hook.id), test=True)
    except (WebhookRefused, RuntimeError) as exc:
        return {"ok": False, "detail": str(exc)[:300]}
    return {"ok": True, "detail": f"Your receiver answered HTTP {code}"}
