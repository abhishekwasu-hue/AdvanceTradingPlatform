"""S3b-2 (ADR-0022): per-user Telegram linking and the email unsubscribe page.

* `POST /api/alerts/telegram/link-code` - a one-time code to send to the organisation's bot as `/start <code>`
  (needs the Telegram channel with inbound on, so the bot can hear it);
* `GET` / `DELETE /api/alerts/telegram/link` - this user's link, and unlinking;
* `GET /api/alerts/unsubscribe?t=` - a page with one button (no login; the signed token is the authority);
  `POST` the same URL unsubscribes - also the RFC 8058 one-click target;
* `GET /api/alerts/email-opt-outs`, `DELETE .../{id}` - a trader sees and removes opt-outs.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import links
from app.alerts.channels import TelegramConfig, decrypt_config
from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_trader
from app.core.enums import AlertChannelType
from app.db.models import AlertChannelRecord, EmailOptOutRecord, User
from app.db.session import get_session
from app.secrets_store.envelope import ensure_tenant_key

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


async def _inbound_bot(session: AsyncSession, tenant_id: int) -> TelegramConfig:
    channel = await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant_id,
                                                                    AlertChannelRecord.channel_type == AlertChannelType.TELEGRAM.value))
    if channel is None or not channel.enabled:
        raise HTTPException(status_code=409, detail="Set up the organisation's Telegram channel first (Settings > Alerts).")
    await ensure_tenant_key(session, tenant_id)
    cfg = decrypt_config(channel)
    if not isinstance(cfg, TelegramConfig) or not cfg.inbound_enabled:
        raise HTTPException(status_code=409, detail="Turn on Telegram inbound so the bot can receive the link code.")
    return cfg


@router.post("/telegram/link-code")
async def telegram_link_code(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _inbound_bot(session, user.tenant_id)
    code, expires = await links.new_link_code(session, user.tenant_id, user.id)
    await session.commit()
    return {"code": code, "command": f"/start {code}", "expires_at": expires.isoformat(),
            "how": "Open a private chat with the organisation's bot and send the command. The code works once."}


@router.get("/telegram/link")
async def telegram_link(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    row = await links.linked_chat(session, user.tenant_id, user.id)
    return {"linked": row is not None, "linked_at": row.linked_at.isoformat() if row and row.linked_at else None}


@router.delete("/telegram/link")
async def telegram_unlink(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    n = await links.unlink(session, user.tenant_id, user.id)
    if n:
        await write_audit_log(session, user.tenant_id, user.id, "telegram_unlinked", "screen alerts no longer go to the linked chat")
    await session.commit()
    return {"linked": False}


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>{html.escape(title)}</title>"
                        f"<body style='font-family:system-ui;max-width:32rem;margin:3rem auto;padding:0 1rem'><h1>{html.escape(title)}</h1>{body}</body>")


@router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_page(t: str = "") -> HTMLResponse:
    who = links.read_unsubscribe_token(t)
    if who is None:
        return _page("Link not valid", "<p>This unsubscribe link is not valid.</p>")
    return _page("Stop screen-alert emails", f"<p>Stop screen-alert emails to <b>{html.escape(who[1])}</b>? Risk and account emails are not affected.</p>"
                 f"<form method='post' action='?t={html.escape(t)}'><button type='submit'>Unsubscribe</button></form>")


@router.post("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(t: str = "", session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    who = links.read_unsubscribe_token(t)
    if who is None:
        raise HTTPException(status_code=400, detail="This unsubscribe link is not valid.")
    tenant_id, address = who
    if await links.opt_out(session, tenant_id, address):
        await write_audit_log(session, tenant_id, None, "email_unsubscribed", f"{address} stopped screen-alert emails")
    await session.commit()
    return _page("Unsubscribed", f"<p>{html.escape(address)} will no longer get screen-alert emails from this organisation.</p>")


@router.get("/email-opt-outs")
async def email_opt_outs(user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    rows = await session.scalars(select(EmailOptOutRecord).where(EmailOptOutRecord.tenant_id == user.tenant_id).order_by(EmailOptOutRecord.id.desc()))
    return [{"id": r.id, "address": r.address, "scope": r.scope, "created_at": r.created_at.isoformat()} for r in rows]


@router.delete("/email-opt-outs/{opt_out_id}")
async def remove_opt_out(opt_out_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    result = await session.execute(delete(EmailOptOutRecord).where(EmailOptOutRecord.id == opt_out_id, EmailOptOutRecord.tenant_id == user.tenant_id))
    if not getattr(result, "rowcount", 0):
        raise HTTPException(status_code=404, detail="Opt-out not found")
    await write_audit_log(session, user.tenant_id, user.id, "email_resubscribed", f"opt-out {opt_out_id} removed")
    await session.commit()
    return {"removed": opt_out_id, "at": datetime.now(timezone.utc).isoformat()}


__all__ = ["router"]
