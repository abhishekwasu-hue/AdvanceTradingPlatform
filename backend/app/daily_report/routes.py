"""M88-R routes.

Users (any member; behind `ui_v2` + `shared_daily_report`, 404 while either is off) read the STORED report - no AI call:
* `GET /api/daily-report/latest` - the newest stored report (null when none is stored yet);
* `GET /api/daily-report/{report_id}/pdf` - that report as a PDF (rebuilt from the stored report).
* M89: `GET` / `PUT /api/daily-report/telegram` - his own "Daily market report on Telegram" choice (ON by default) and
  whether his Telegram reaches him; `GET` / `POST /api/daily-report/telegram/stop?t=` - the Stop button's signed link
  (no sign-in; the GET page has one button, so a link preview stops nothing).

Admin (SUPER_ADMIN with two-factor):
* `GET /api/admin/daily-report` - the setting, the last runs (status, reason, tokens, cost) and whether his own AI key
  is connected;
* `PUT /api/admin/daily-report/settings` - "Shared report symbols" and "Recent bars" (audited);
* `POST /api/admin/daily-report/run` - Run now for the newest stored close, on the presser's own AI key (runs after the
  answer is sent; the card reads the result).
Read-only data; nothing here places, blocks or changes an order.
"""
from __future__ import annotations

import html
import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.dependencies import get_current_user, require_mfa_session, require_role
from app.core.rate_limit import user_rate_limit
from app.daily_report import FLAG, PAGE_FLAG, build, job
from app.daily_report import settings as S
from app.db.models import SharedDailyReportRecord, User
from app.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/daily-report", tags=["daily-report"])
admin_router = APIRouter(prefix="/api/admin/daily-report", tags=["admin"],
                         dependencies=[Depends(require_role()), Depends(require_mfa_session)])
read_limited = user_rate_limit("daily_report_read", limit=60, window_seconds=60)
pdf_limited = user_rate_limit("daily_report_pdf", limit=10, window_seconds=60)
RUNS_SHOWN = 10


async def _flags(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    for name in (PAGE_FLAG, FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


@router.get("/latest", dependencies=[Depends(read_limited)])
async def latest(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _flags(session, user)
    row = await build.latest(session)
    if row is None or not row.content_json:
        return {"report": None}
    return {"report": {"id": row.id, **json.loads(row.content_json)}}


class TelegramBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    telegram: bool


async def _telegram_state(session: AsyncSession, user: User) -> dict[str, Any]:
    from app.daily_report import telegram
    tenant_id, user_id = int(user.tenant_id), int(user.id)
    return {"telegram": await telegram.wants(session, tenant_id, user_id),
            "connected": await telegram.own_chat(session, tenant_id, user_id) is not None}


@router.get("/telegram", dependencies=[Depends(read_limited)])
async def telegram_get(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await _flags(session, user)
    return await _telegram_state(session, user)


@router.put("/telegram", dependencies=[Depends(read_limited)])
async def telegram_put(body: TelegramBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    from app.daily_report import telegram
    await _flags(session, user)
    await telegram.set_wants(session, int(user.tenant_id), int(user.id), body.telegram)
    return await _telegram_state(session, user)


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>{html.escape(title)}</title>"
                        f"<body style='font-family:system-ui,sans-serif;max-width:32rem;margin:2rem auto;padding:0 1rem'><h1>{html.escape(title)}</h1>{body}</body>")


@router.get("/telegram/stop", response_class=HTMLResponse)
async def telegram_stop_page(t: str = Query("", max_length=200)) -> HTMLResponse:
    from app.daily_report import telegram
    if telegram.read_stop_token(t) is None:
        return _page("Link not valid", "<p>This link is not valid.</p>")
    return _page("Stop the daily market report on Telegram", "<p>Stop getting the daily market report in this Telegram chat? You can turn "
                 "it on again in Settings › Alerts.</p>"
                 f"<form method='post' action='?t={html.escape(t)}'><button type='submit'>Stop</button></form>")


@router.post("/telegram/stop", response_class=HTMLResponse)
async def telegram_stop(t: str = Query("", max_length=200), session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    from app.daily_report import telegram
    who = telegram.read_stop_token(t)
    if who is None:
        return _page("Link not valid", "<p>This link is not valid.</p>")
    user = await session.get(User, who[1])
    if user is not None and int(user.tenant_id) == who[0]:
        await telegram.set_wants(session, who[0], who[1], False)
    return _page("Stopped", "<p>The daily market report will not come to Telegram any more. Turn it on again in Settings › Alerts.</p>")


@router.get("/{report_id}/pdf", dependencies=[Depends(pdf_limited)])
async def report_pdf(report_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Response:
    import asyncio

    from app.daily_report.pdf import report_pdf as render
    await _flags(session, user)
    row = await session.get(SharedDailyReportRecord, report_id)
    if row is None or row.status != "ok" or not row.content_json:
        raise HTTPException(status_code=404, detail="Report not found")
    data = await asyncio.to_thread(render, json.loads(row.content_json))
    name = f"daily-market-report-{row.trade_date.isoformat()}.pdf"
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})


# --- admin ------------------------------------------------------------------------------------------------------------------
class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbols: list[str] = Field(default_factory=list, max_length=S.MAX_ENTRIES)
    recent_bars: int = Field(default=20, ge=S.RECENT_BARS_RANGE[0], le=S.RECENT_BARS_RANGE[1])


async def _key_state(session: AsyncSession, user: User) -> dict[str, Any]:
    from app.ai import byok
    record = await byok.connected_key(session, user)
    ok = record is not None and await byok.consent_accepted(session, user, record.provider)
    return {"connected": bool(ok), "message": None if ok else "Connect your own AI key in Settings › AI to run the report."}


async def admin_status(session: AsyncSession, user: User) -> dict[str, Any]:
    from app.platform.controls import feature_flags
    cfg = await S.read(session)
    runs = list(await session.scalars(select(SharedDailyReportRecord).order_by(SharedDailyReportRecord.trade_date.desc()).limit(RUNS_SHOWN)))
    flag = (await feature_flags(session)).get(FLAG) or {}
    from app.daily_report import telegram
    sends = await telegram.counts(session, [int(r.id) for r in runs])
    return {"settings": {"symbols": cfg["symbols"], "recent_bars": cfg["recent_bars"]}, "flag_on": bool(flag.get("on") or flag.get("tenants")),
            "key": await _key_state(session, user), "runs": [{**build.run_summary(r), "telegram": sends[int(r.id)]} for r in runs],
            "examples": ["NIFTY 50", "NIFTY 50 constituents"]}


@admin_router.get("")
async def admin_get(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return await admin_status(session, user)


@admin_router.put("/settings")
async def admin_settings(body: SettingsBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    try:
        await S.save(session, user, symbols=body.symbols, recent_bars=body.recent_bars)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await admin_status(session, user)


async def _run_now(factory: Any, user_id: int, day: Any) -> None:
    async with factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            return
        try:
            await build.run(session, day, user=user, trigger="run_now", on_stored=job.after_stored)
        except build.NothingToRun as exc:
            logger.info("Daily report Run now did not start: %s", exc)


@admin_router.post("/run")
async def admin_run(tasks: BackgroundTasks, user: User = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    cfg = await S.read(session)
    if not cfg["symbols"]:
        raise HTTPException(status_code=409, detail="The Shared report symbols setting is empty, so there is no report.")
    key = await _key_state(session, user)
    if not key["connected"]:
        raise HTTPException(status_code=409, detail=key["message"])
    day = await job.newest_close_day(session, job._probe_symbol(cfg["symbols"]))  # noqa: SLF001
    if day is None:
        raise HTTPException(status_code=409, detail="No end-of-day close is stored yet for the report's first symbol.")
    if build._LOCK.locked():  # noqa: SLF001
        raise HTTPException(status_code=409, detail="A report run is already going. It finishes in a few minutes.")
    tasks.add_task(_run_now, async_sessionmaker(session.bind, expire_on_commit=False), int(user.id), day)
    return {"started": True, "trade_date": day.isoformat()}
