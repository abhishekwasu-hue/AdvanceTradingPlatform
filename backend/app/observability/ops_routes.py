"""Production readiness A2: Admin > System > Alerts (`/api/admin/ops-alerts`, SUPER_ADMIN with a two-factor session).

* `GET ""`            - the last server check (every check with its state and a plain sentence), the operator channels
                        and whether the server's schedule is reporting. While an operator code waits and the server has
                        no Telegram webhook, this also reads the bot's new messages once (so "Connected" shows).
* `POST "/run"`       - one check now, from the API (host facts: the last ones the server reported). Same dedup as the
                        schedule, so it never sends twice what the schedule already sent.
* `POST "/test"`      - one test message to every operator channel.
* `POST "/telegram"`  - a one-time code and the `t.me` link that links the operator's private chat to the platform bot.
* `DELETE "/telegram"` - forget the linked chat (OPS_ALERT_TELEGRAM_CHAT_ID, when set, still applies).

Nothing here reads or changes an order, a flag or a LIVE switch, and nothing returns a chat id, an address or a token.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import platform_bot
from app.audit.log import write_audit_log
from app.auth.dependencies import require_mfa_session, require_role
from app.core import config
from app.db.models import User
from app.db.session import get_session
from app.observability import ops_alerts

router = APIRouter(prefix="/api/admin/ops-alerts", tags=["admin"], dependencies=[Depends(require_role()), Depends(require_mfa_session)])

TEST_SUBJECT = "AiAlgoDesk server check: test message"
TEST_BODY = ("AiAlgoDesk server check: test message.\n\nServer alerts reach this channel: backups, disk, the trading worker, "
             "the data feed, errors and the HTTPS certificate.")


@router.get("")
async def ops_view(session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    if platform_bot.configured() and not platform_bot.webhook_mode() and await ops_alerts.link_pending(session):
        await platform_bot.poll_once(session)
    return await ops_alerts.view(session)


@router.post("/run")
async def ops_run(admin: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    api_metrics = ops_alerts.local_api_metrics()
    worker_metrics = await ops_alerts.fetch_metrics(config.OPS_WORKER_METRICS_URL)
    await ops_alerts.run(session, source="admin", api_metrics=api_metrics, worker_metrics=worker_metrics)
    await write_audit_log(session, None, admin.id, "ops_alert_check_run", "server checks run from the admin console")
    await session.commit()
    return await ops_alerts.view(session)


@router.post("/test")
async def ops_test(admin: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    result = await ops_alerts.deliver(session, TEST_SUBJECT, TEST_BODY)
    if result["telegram"] is None and result["email"] is None:
        raise HTTPException(status_code=409, detail="No alert channel is set up yet. Connect Telegram, or set up e-mail (SMTP) on the server.")
    await write_audit_log(session, None, admin.id, "ops_alert_test", f"telegram={result['telegram']} email={result['email']}")
    await session.commit()
    return result


@router.post("/telegram")
async def ops_telegram_link(admin: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    if not platform_bot.configured():
        raise HTTPException(status_code=409, detail="The platform Telegram bot is not set up on this server yet (ATP_TELEGRAM_BOT_TOKEN).")
    username = await platform_bot.bot_username()
    if username is None:
        raise HTTPException(status_code=503, detail="Telegram could not be reached just now. Try again in a minute.")
    code, expires = await ops_alerts.new_link_code(session, admin.tenant_id, admin.id)
    await write_audit_log(session, None, admin.id, "ops_alert_telegram_link_started", "one-time code for the operator alert chat")
    await session.commit()
    return {"url": platform_bot.connect_url(username, code), "expires_at": expires.isoformat()}


@router.delete("/telegram")
async def ops_telegram_unlink(admin: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await ops_alerts.unlink(session, admin.id)
    return await ops_alerts.view(session)
