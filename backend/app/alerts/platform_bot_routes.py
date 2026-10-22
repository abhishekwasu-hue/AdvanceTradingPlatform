"""UX2 fix 5: "Connect Telegram" with the platform's bot (app/alerts/platform_bot.py).

* `GET /api/alerts/telegram/connect` - can this server connect Telegram, and is this organisation connected? While the
  user has a code waiting and the server has no webhook, this also reads the bot's new messages once, so the Connect
  screen can ask every few seconds and show "Connected" as soon as Start was pressed.
* `POST /api/alerts/telegram/connect` - a one-time code and the `t.me` link that carries it (a trader; scope
  `alerts:write`). Disconnecting and "Send a test" are the alert channel's own routes (`DELETE` / `POST .../test` on
  `/api/alert-channels/telegram`).
* `POST /api/telegram/platform/webhook` - Telegram's posts when the webhook is registered; the secret header is the
  credential.
* `GET` / `POST /api/admin/telegram/platform[/register]` - the platform admin's view and the webhook registration
  (SUPER_ADMIN with an MFA session).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import platform_bot
from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_mfa_session, require_role, require_trader
from app.auth.scopes import require_scope
from app.db.models import User
from app.db.session import get_session
from app.plans.limits import check_can_add_alert_channel, load_tenant

router = APIRouter(tags=["alerts"])

NOT_SET_UP = "Telegram alerts are not set up on this server yet."
OWN_BOT = "Your organisation sends Telegram alerts with its own bot. Change that under Settings > Advanced > Your own Telegram bot."
NO_TELEGRAM = "Telegram could not be reached just now. Try again in a minute."


async def _state(session: AsyncSession, user: User) -> Dict[str, Any]:
    record = await platform_bot.telegram_channel(session, user.tenant_id)
    platform = platform_bot.is_platform(record)
    return {
        "available": platform_bot.configured(),
        "connected": bool(record is not None and platform and record.enabled),
        "own_bot": bool(record is not None and not platform),
        "working": bool(record is not None and platform and record.last_delivered_at is not None and not record.last_error),
    }


@router.get("/api/alerts/telegram/connect")
async def connect_state(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    if platform_bot.configured() and not platform_bot.webhook_mode() and await platform_bot.pending_code(session, user.tenant_id, user.id):
        await platform_bot.poll_once(session)
    return await _state(session, user)


@router.post("/api/alerts/telegram/connect", dependencies=[Depends(require_scope("alerts:write"))])
async def connect_start(user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    if not platform_bot.configured():
        raise HTTPException(status_code=409, detail=NOT_SET_UP)
    record = await platform_bot.telegram_channel(session, user.tenant_id)
    if record is not None and not platform_bot.is_platform(record):
        raise HTTPException(status_code=409, detail=OWN_BOT)
    if record is None:
        await check_can_add_alert_channel(session, await load_tenant(session, user.tenant_id))
    username = await platform_bot.bot_username()
    if username is None:
        raise HTTPException(status_code=503, detail=NO_TELEGRAM)
    code, expires = await platform_bot.new_code(session, user.tenant_id, user.id)
    await write_audit_log(session, user.tenant_id, user.id, "telegram_connect_started", "one-time code for the platform bot")
    await session.commit()
    return {"url": platform_bot.connect_url(username, code), "expires_at": expires.isoformat()}


@router.post(platform_bot.WEBHOOK_PATH)
async def platform_webhook(request: Request, session: AsyncSession = Depends(get_session),
                           x_telegram_bot_api_secret_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    if not platform_bot.configured() or not platform_bot.webhook_secret_ok(x_telegram_bot_api_secret_token):
        raise HTTPException(status_code=401, detail="Bad secret")
    try:
        update = await request.json()
    except ValueError:
        return {"ok": True, "handled": "ignored"}
    if not isinstance(update, dict):
        return {"ok": True, "handled": "ignored"}
    return {"ok": True, **await platform_bot.handle_update(session, update)}


admin = [Depends(require_role()), Depends(require_mfa_session)]


@router.get("/api/admin/telegram/platform", dependencies=admin)
async def platform_status() -> Dict[str, Any]:
    username = await platform_bot.bot_username() if platform_bot.configured() else None
    return {"configured": platform_bot.configured(), "username": username, "webhook_mode": platform_bot.webhook_mode(),
            "webhook_url": platform_bot.webhook_url()}


@router.post("/api/admin/telegram/platform/register")
async def platform_register(user: User = Depends(require_role()), _mfa: Any = Depends(require_mfa_session),
                            session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    try:
        result = await platform_bot.register_webhook()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await write_audit_log(session, user.tenant_id, user.id, "telegram_platform_webhook_registered",
                          f"ok={result['ok']} {str(result.get('description') or '')[:120]}")
    await session.commit()
    return result


__all__ = ["router"]
