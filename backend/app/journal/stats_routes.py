"""M62 JN-3: `/api/journal/entries`, `/api/journal/stats` and `/api/journal/export` (behind `discipline_engine`).

The logged-in trader's own entries only (tenant and user): his ATP trades and, unless `include_manual=false`, his
manual (outside-ATP) entries, labelled as such. Numbers only; nothing here places, changes or blocks an order.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.journal import FLAG, stats
from app.journal import export as journal_export
from app.platform.controls import require_flag

router = APIRouter(prefix="/api/journal", tags=["journal"])

OUTCOME_PATTERN = r"^(win|loss|flat|open)$"


def _filters(setup: str | None, tag: str | None, outcome: str | None, date_from: date | None, date_to: date | None,
             include_manual: bool) -> stats.Filters:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="The start date is after the end date.")
    return stats.Filters(setup=(setup or "").strip() or None, tag=(tag or "").strip().lower() or None, outcome=outcome,
                         date_from=date_from, date_to=date_to, include_manual=include_manual)


async def _load(session: AsyncSession, user: User, f: stats.Filters) -> stats.Loaded:
    await require_flag(session, FLAG, user.tenant_id)
    return await stats.load(session, user, f)


def _common(setup: str | None = Query(default=None, max_length=100), tag: str | None = Query(default=None, max_length=30),
            outcome: str | None = Query(default=None, pattern=OUTCOME_PATTERN),
            date_from: date | None = Query(default=None, description="IST calendar day, inclusive"),
            date_to: date | None = Query(default=None, description="IST calendar day, inclusive"),
            include_manual: bool = Query(default=True, description="include the trader's outside-ATP entries (labelled)")) -> stats.Filters:
    return _filters(setup, tag, outcome, date_from, date_to, include_manual)


@router.get("/entries")
async def journal_entries(f: stats.Filters = Depends(_common), limit: int = Query(default=200, ge=1, le=1000),
                          user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """The journal list, newest first: date, symbol, setup, net P&L, R, tags, chart link per entry, with the setup and
    tag choices for the filters."""
    loaded = await _load(session, user, f)
    return {"entries": loaded.rows[:limit], "total": len(loaded.rows), "truncated": loaded.truncated or len(loaded.rows) > limit,
            "setups": loaded.setups, "tags": loaded.tags, "filters": f.to_dict()}


@router.get("/stats")
async def journal_stats(f: stats.Filters = Depends(_common), user: User = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Win rate, profit factor, expectancy, average win / loss, average R and MAE / MFE (where stored) - in total and
    by setup, by timeframe and by weekday (IST). A multi-leg structure counts once. Numbers only."""
    loaded = await _load(session, user, f)
    return {**stats.stats_payload(loaded.decisions), "filters": f.to_dict(), "truncated": loaded.truncated,
            "setups": loaded.setups, "tags": loaded.tags}


@router.get("/export")
async def journal_export_file(format: str = Query(default="csv", pattern=r"^(csv|pdf)$"), f: stats.Filters = Depends(_common),
                              user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Response:
    """The journal (with the same filters) as CSV or PDF, for the trader's own records."""
    loaded = await _load(session, user, f)
    stamp = f"{f.date_from or 'all'}_{f.date_to or 'today'}"
    if format == "pdf":
        body = journal_export.to_pdf(loaded.rows, stats.summary(loaded.decisions), f.to_dict())
        media = "application/pdf"
    else:
        body = journal_export.to_csv(loaded.rows)
        media = "text/csv; charset=utf-8"
    return Response(content=body, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="journal-{stamp}.{format}"', "Cache-Control": "private, no-store",
        "X-Export-Rows": str(len(loaded.rows)), "Access-Control-Expose-Headers": "Content-Disposition, X-Export-Rows"})
