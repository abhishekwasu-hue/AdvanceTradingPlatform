"""Phase P2: `GET /api/tax/years`, `GET /api/tax/report?fy=2026-27&mode=LIVE|PAPER|ALL`, `GET /api/tax/report.csv`.

Every route reads only the caller's own trades (fix, BT1b): a member never sees another member's trades, and the
organisation owner sees their own.

Both report routes take an optional `turnover_method` (ABS_PNL | ABS_PNL_PLUS_PREMIUM); omitted = the configured
`TAX_FNO_TURNOVER_METHOD`. The report names the method it used."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.market_data.calendar import IST
from app.tax import report as tax

router = APIRouter(prefix="/api/tax", tags=["tax"])


def _mode(value: str) -> Optional[str]:
    value = (value or "LIVE").upper()
    if value not in ("LIVE", "PAPER", "ALL"):
        raise HTTPException(status_code=400, detail="mode must be LIVE, PAPER or ALL")
    return None if value == "ALL" else value


async def _build(session: AsyncSession, user: User, fy: Optional[str], mode: str, turnover_method: Optional[str] = None) -> dict:
    fy = fy or tax.financial_year(datetime.now(IST).date())
    try:
        tax.fy_bounds(fy)
        method = tax.turnover_method(turnover_method)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    selected = _mode(mode)
    trades = await tax.closed_trades(session, user.tenant_id, user.id, fy, mode=selected)
    return tax.build_report(trades, fy, mode=selected, method=method)


@router.get("/years")
async def years(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    return {"years": await tax.available_years(session, user.tenant_id, user.id), "current": tax.financial_year(datetime.now(IST).date())}


@router.get("/report")
async def report(fy: Optional[str] = Query(default=None), mode: str = Query(default="LIVE"),
                 turnover_method: Optional[str] = Query(default=None),
                 user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    return await _build(session, user, fy, mode, turnover_method)


@router.get("/report.csv", response_class=PlainTextResponse)
async def report_csv(fy: Optional[str] = Query(default=None), mode: str = Query(default="LIVE"),
                     turnover_method: Optional[str] = Query(default=None),
                     user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> PlainTextResponse:
    body = await _build(session, user, fy, mode, turnover_method)
    return PlainTextResponse(tax.to_csv(body), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="tax-report-{body["financial_year"]}-{body["mode"]}.csv"'})
