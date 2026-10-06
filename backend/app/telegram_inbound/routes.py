"""Phase BE: Telegram inbound API. The webhook needs no login (Telegram cannot carry one): the
per-tenant token in the path plus the secret header Telegram echoes back are the credential; the
settings endpoints are the owner's."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.channels import TelegramConfig, decrypt_config
from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_owner
from app.db.models import Tenant, User
from app.db.session import get_session
from app.platform.controls import require_flag
from app.telegram_inbound import service
from app.webhooks.routes import resolve_tenant_by_webhook_token

router = APIRouter(prefix="/api/telegram", tags=["telegram"])
FLAG = "telegram_inbound"


class InboundBody(BaseModel):
    enabled: bool
    allowed_chat_ids: List[str] = Field(default_factory=list, max_length=10)


@router.post("/webhook/{webhook_token}")
async def telegram_webhook(webhook_token: str, request: Request, session: AsyncSession = Depends(get_session),
                           x_telegram_bot_api_secret_token: Optional[str] = Header(default=None)) -> dict:
    # P0.3 / S13: the path names the organisation (stored hash, the plaintext token, or a legacy plaintext URL);
    # the X-Telegram-Bot-Api-Secret-Token header is the credential.
    tenant = await resolve_tenant_by_webhook_token(session, webhook_token, accept_hash=True)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Unknown webhook token")
    record = await service.telegram_channel(session, tenant.id)
    if record is None or not record.enabled:
        raise HTTPException(status_code=403, detail="Telegram inbound is not configured")
    cfg: TelegramConfig = decrypt_config(record)  # type: ignore[assignment]
    if not cfg.inbound_enabled or not await _flag_on(session, tenant.id):
        raise HTTPException(status_code=403, detail="Telegram inbound is off for this organisation")
    if not service.secret_ok(x_telegram_bot_api_secret_token, cfg):
        await write_audit_log(session, tenant.id, None, "telegram_inbound_rejected", "secret header missing or wrong")
        await session.commit()
        raise HTTPException(status_code=401, detail="Bad secret")
    try:
        update = await request.json()
    except ValueError:
        return {"ok": True, "handled": "ignored", "reason": "not json"}
    if not isinstance(update, dict):
        return {"ok": True, "handled": "ignored", "reason": "not an update"}
    result = await service.handle_update(session, tenant, cfg, update)
    return {"ok": True, **result}


@router.get("/inbound/status")
async def inbound_status(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    tenant = await session.get(Tenant, user.tenant_id)
    record = await service.telegram_channel(session, tenant.id)
    return {**service.status_of(record, tenant), "flag_enabled": await _flag_on(session, user.tenant_id)}


async def _flag_on(session: AsyncSession, tenant_id: int) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


@router.put("/inbound")
async def configure_inbound(body: InboundBody, user: User = Depends(require_owner), session: AsyncSession = Depends(get_session)) -> dict:
    await require_flag(session, FLAG, user.tenant_id)
    tenant = await session.get(Tenant, user.tenant_id)
    try:
        return await service.configure(session, tenant, user, enabled=body.enabled, allowed_chat_ids=body.allowed_chat_ids)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/inbound/register")
async def register_inbound(user: User = Depends(require_owner), session: AsyncSession = Depends(get_session)) -> dict:
    await require_flag(session, FLAG, user.tenant_id)
    tenant = await session.get(Tenant, user.tenant_id)
    try:
        return await service.register_webhook(session, tenant, user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
