"""HB0 / HB1 admin endpoints for the history loads (SUPER_ADMIN with an MFA session).

HB0: GET /api/admin/history-import/progress - what the R2 import has done. Read-only: per dataset the files by status,
per symbol done / pending / refused / failed and the last error. The import itself is run by hand from the CLI
(`python -m app.market_lake.r2_import`), never from a request. Symbols and counts only - no bars are returned here.

HB1: /api/admin/history-backfill/...
- GET  progress           per segment (cm / index / fo): files by status, first / last day done; files needing attention
- POST upload             the manual-upload fallback: one day's file for one segment (the archive blocked it, or a
                          correction) - same checks as the job; `force` skips only the short-file gate
- POST corporate-actions  a corporate-actions CSV (ATP layout or the exchange's export) for the B4 adjusted view
- POST holidays           a PAST holiday list CSV (market_holidays) so the daily gap check can run for that year; dates
                          from today on are refused unless `allow_future`; the form's exchange is the only one loaded
- POST holidays/remove    undo: the same CSV removes exactly the dates it lists
Every upload needs the `history_backfill` flag on, is size-capped and is written to the audit log.

HB2: GET /api/admin/history-intraday/progress - what the owner's intraday capture / backfill has done (per instrument:
days by status, first / last day done, bars stored; days needing attention). Read-only; counts and names only, never
bars. The capture itself runs in the trading worker after the close or from the CLI (`app.market_lake.intraday_history`).
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.log import write_audit_log
from app.auth.dependencies import require_mfa_session, require_role
from app.db.models import User
from app.db.session import get_session
from app.market_lake import bhavcopy_backfill, intraday_history, r2_import
from app.platform.controls import flag_enabled

router = APIRouter(prefix="/api/admin/history-import", tags=["admin"],
                   dependencies=[Depends(require_role()), Depends(require_mfa_session)])
backfill_router = APIRouter(prefix="/api/admin/history-backfill", tags=["admin"],
                            dependencies=[Depends(require_role()), Depends(require_mfa_session)])
intraday_router = APIRouter(prefix="/api/admin/history-intraday", tags=["admin"],
                            dependencies=[Depends(require_role()), Depends(require_mfa_session)])

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_CSV_BYTES = 5 * 1024 * 1024


@router.get("/progress")
async def import_progress(dataset: Optional[str] = Query(None, pattern="^(daily|m1)$"), limit: int = Query(500, ge=1, le=5000),
                          session: AsyncSession = Depends(get_session)) -> dict:
    result = await r2_import.progress(session, dataset=dataset, limit=limit)
    result["flag_on"] = await flag_enabled(session, r2_import.FLAG)
    return result


@backfill_router.get("/progress")
async def backfill_progress(limit: int = Query(200, ge=1, le=5000), session: AsyncSession = Depends(get_session)) -> dict:
    result = await bhavcopy_backfill.progress(session, limit=limit)
    result["flag_on"] = await flag_enabled(session, bhavcopy_backfill.FLAG)
    return result


@intraday_router.get("/progress")
async def intraday_progress(limit: int = Query(200, ge=1, le=5000), session: AsyncSession = Depends(get_session)) -> dict:
    result = await intraday_history.progress(session, limit=limit)
    result["flag_on"] = await intraday_history.enabled(session)
    return result


async def _read(file: UploadFile, cap: int) -> bytes:
    content = await file.read(cap + 1)
    if len(content) > cap:
        raise HTTPException(status_code=413, detail=f"File larger than {cap // (1024 * 1024)} MB")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Empty file")
    return content


async def _flag_on(session: AsyncSession) -> None:
    if not await flag_enabled(session, bhavcopy_backfill.FLAG):
        raise HTTPException(status_code=409, detail=f"The '{bhavcopy_backfill.FLAG}' flag is off - turn it on in Admin > Controls")


@backfill_router.post("/upload")
async def upload_day_file(segment: str = Form(..., pattern="^(cm|index|fo)$"), trade_date: date = Form(...), force: bool = Form(False),
                          file: UploadFile = File(...), user: User = Depends(require_role()),
                          session: AsyncSession = Depends(get_session)) -> dict:
    await _flag_on(session)
    content = await _read(file, MAX_UPLOAD_BYTES)
    await session.commit()                     # the load runs in its own sessions (one transaction per file)
    try:
        result = await bhavcopy_backfill.ingest_upload(async_sessionmaker(session.bind, expire_on_commit=False), segment, trade_date,
                                                       content, file.filename or f"{segment}-{trade_date}.csv", force=force)
    except bhavcopy_backfill.BackfillConfigError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    await write_audit_log(session, user.tenant_id, user.id, "history_backfill_upload",
                          f"{segment} {trade_date} {result.get('status')} sha256={result.get('sha256')} force={force}")
    await session.commit()
    return result


@backfill_router.post("/corporate-actions")
async def upload_corporate_actions(file: UploadFile = File(...), user: User = Depends(require_role()),
                                   session: AsyncSession = Depends(get_session)) -> dict:
    from app.market_lake.corporate_actions import load_csv

    await _flag_on(session)
    report = await load_csv(session, await _read(file, MAX_CSV_BYTES), source="ca_csv:upload", commit=False)
    await write_audit_log(session, user.tenant_id, user.id, "history_corporate_actions_upload",
                          f"{file.filename}: {report.inserted} new, {report.corrected} corrected, {len(report.unreadable)} unreadable")
    await session.commit()
    return report.as_dict()


@backfill_router.post("/holidays")
async def upload_holidays(file: UploadFile = File(...), exchange: str = Form("NSE", min_length=1, max_length=20),
                          allow_future: bool = Form(False), user: User = Depends(require_role()),
                          session: AsyncSession = Depends(get_session)) -> dict:
    from app.market_data.holiday_import import import_holidays

    await _flag_on(session)
    report = await import_holidays(session, await _read(file, MAX_CSV_BYTES), exchange=exchange, commit=False,
                                   allow_future=allow_future)
    await write_audit_log(session, user.tenant_id, user.id, "history_holidays_upload",
                          f"{exchange.upper()} {file.filename}: {report.added} added, years {report.years}, allow_future={allow_future}")
    await session.commit()
    return report.as_dict()


@backfill_router.post("/holidays/remove")
async def remove_uploaded_holidays(file: UploadFile = File(...), exchange: str = Form("NSE", min_length=1, max_length=20),
                                   user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    from app.market_data.holiday_import import remove_holidays

    await _flag_on(session)
    report = await remove_holidays(session, await _read(file, MAX_CSV_BYTES), exchange=exchange, commit=False)
    await write_audit_log(session, user.tenant_id, user.id, "history_holidays_removed",
                          f"{exchange.upper()} {file.filename}: {report.removed} removed, years {report.years}")
    await session.commit()
    return report.as_dict()
