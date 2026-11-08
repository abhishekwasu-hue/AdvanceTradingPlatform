"""M88-R routes.

Users (any member; behind `ui_v2` + `shared_daily_report`, 404 while either is off) read the STORED report - no AI call:
* `GET /api/daily-report/latest` - the newest stored report (null when none is stored yet);
* `GET /api/daily-report/{report_id}/pdf` - that report as a PDF (rebuilt from the stored report).

Admin (SUPER_ADMIN with two-factor):
* `GET /api/admin/daily-report` - the setting, the last runs (status, reason, tokens, cost) and whether his own AI key
  is connected;
* `PUT /api/admin/daily-report/settings` - "Shared report symbols" and "Recent bars" (audited);
* `POST /api/admin/daily-report/run` - Run now for the newest stored close, on the presser's own AI key (runs after the
  answer is sent; the card reads the result).
Read-only data; nothing here places, blocks or changes an order.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import Response
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
    return {"settings": {"symbols": cfg["symbols"], "recent_bars": cfg["recent_bars"]}, "flag_on": bool(flag.get("on") or flag.get("tenants")),
            "key": await _key_state(session, user), "runs": [build.run_summary(r) for r in runs],
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
