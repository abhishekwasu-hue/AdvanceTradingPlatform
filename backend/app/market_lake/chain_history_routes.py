"""TM 9.4: the chain history's read API (tenant-scoped) and the admin status card.

Reads - `/api/chain-history/*`, any member of the organisation, only the organisation's OWN recording (TM-7) and, within
it, only the rows read through the caller's own broker session (BT1b, `chain_store`): another organisation always gets
an empty answer, never a refusal that says something exists. Behind the `chain_history` flag of the caller's
organisation (404 while off). For Part 9.3 (IV / OI history), Part 10 (replay) and backtests later.
- GET coverage                      which days hold chains, per underlying (counts and slot times only)
- GET day?underlying=&day=&expiry=  one IST trading day's chains, oldest first
- GET range?underlying=&start=&end=&expiry=   up to CHAIN_HISTORY_READ_MAX_DAYS days, at most
                                    CHAIN_HISTORY_READ_MAX_CHAINS chains (counted first; 422 above it)

Admin - GET /api/admin/chain-history/status (SUPER_ADMIN with two-factor): what is being recorded, today's chains per
underlying, the last snapshot, today's gaps. Counts and times only - no prices.
"""
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_mfa_session, require_role
from app.core import config
from app.db.models import User
from app.db.session import get_session
from app.market_data.calendar import IST
from app.market_lake import chain_recorder, chain_store

router = APIRouter(prefix="/api/chain-history", tags=["chain-history"])
admin_router = APIRouter(prefix="/api/admin/chain-history", tags=["admin"],
                         dependencies=[Depends(require_role()), Depends(require_mfa_session)])

UNDERLYING = r"^[A-Za-z0-9 &_-]{1,30}$"


async def _flag(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, chain_recorder.FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": chain_recorder.FLAG})


def _name(underlying: str) -> str:
    return " ".join(underlying.upper().split())


@router.get("/coverage")
async def coverage(underlying: Optional[str] = Query(default=None, pattern=UNDERLYING), user: User = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flag(session, user)
    days = await chain_store.coverage(session, user.tenant_id, underlying=_name(underlying) if underlying else None, reader_user_id=user.id)
    return {"days": days, "slot_minutes": config.CHAIN_HISTORY_SLOT_MINUTES, "fields": list(chain_store.FIELD_NAMES)}


@router.get("/day")
async def day(underlying: str = Query(..., pattern=UNDERLYING), day: Optional[date] = Query(default=None),
              expiry: Optional[date] = Query(default=None), user: User = Depends(get_current_user),
              session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flag(session, user)
    name = _name(underlying)
    wanted = day or datetime.now(timezone.utc).astimezone(IST).date()
    try:
        chains = await chain_store.read_day(session, user.tenant_id, name, wanted, expiry=expiry, reader_user_id=user.id,
                                            max_chains=config.CHAIN_HISTORY_READ_MAX_CHAINS)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"underlying": name, "day": wanted.isoformat(), "expiry": expiry.isoformat() if expiry else None,
            "fields": list(chain_store.FIELD_NAMES), "chains": [c.as_json() for c in chains]}


@router.get("/range")
async def range_(underlying: str = Query(..., pattern=UNDERLYING), start: date = Query(...), end: date = Query(...),
                 expiry: Optional[date] = Query(default=None), user: User = Depends(get_current_user),
                 session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flag(session, user)
    if end < start:
        raise HTTPException(status_code=422, detail="The end day is before the start day.")
    if (end - start).days + 1 > config.CHAIN_HISTORY_READ_MAX_DAYS:
        raise HTTPException(status_code=422, detail=f"At most {config.CHAIN_HISTORY_READ_MAX_DAYS} days per request.")
    name = _name(underlying)
    try:
        chains = await chain_store.read_range(session, user.tenant_id, name, start, end, expiry=expiry, reader_user_id=user.id,
                                              max_chains=config.CHAIN_HISTORY_READ_MAX_CHAINS)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"underlying": name, "start": start.isoformat(), "end": end.isoformat(), "expiry": expiry.isoformat() if expiry else None,
            "fields": list(chain_store.FIELD_NAMES), "chains": [c.as_json() for c in chains]}


@admin_router.get("/status")
async def status(session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    return await chain_recorder.status(session)
