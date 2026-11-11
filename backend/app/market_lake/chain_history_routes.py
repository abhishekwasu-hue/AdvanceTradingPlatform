"""TM 9.4: the chain history's read API (tenant-scoped) and the admin status card.

Reads - `/api/chain-history/*`, any member of the organisation, only the organisation's OWN recording (TM-7) and, within
it, only the rows read through the caller's own broker session (BT1b, `chain_store`): another organisation always gets
an empty answer, never a refusal that says something exists. Behind the `chain_history` flag of the caller's
organisation (404 while off). For Part 9.3 (IV / OI history), Part 10 (replay) and backtests later.
- GET coverage                      which days hold chains, per underlying (counts and slot times only)
- GET day?underlying=&day=&expiry=  one IST trading day's chains, oldest first
- GET range?underlying=&start=&end=&expiry=   up to CHAIN_HISTORY_READ_MAX_DAYS days, at most
                                    CHAIN_HISTORY_READ_MAX_CHAINS chains (counted first; 422 above it)

M96 N1 (the option chain's OI view; the caller's own rows only, the same scope as above; also open with the
bring-your-own-broker data tier on, whose own 5-minute reads live in the same store):
- GET oi-range?underlying=&expiry=&start=&end=   per strike call / put OI at the two times and the change (start / end
                                    are IST date-times; the reading used is the newest read at or before each, same day)
- GET oi-replay?underlying=&expiry=&day=&around=  every stored chain of that day and expiry, OI per strike per frame
- GET expiries?underlying=&at=      per stored expiry at `at` (default now): total OI, OI change since the day's first
                                    reading, PCR (OI), max pain, ATM IV

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
from app.market_lake import chain_range, chain_recorder, chain_store

router = APIRouter(prefix="/api/chain-history", tags=["chain-history"])
admin_router = APIRouter(prefix="/api/admin/chain-history", tags=["admin"],
                         dependencies=[Depends(require_role()), Depends(require_mfa_session)])

UNDERLYING = r"^[A-Za-z0-9 &_-]{1,30}$"


async def _flag(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, chain_recorder.FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": chain_recorder.FLAG})


async def _own_history(session: AsyncSession, user: User) -> None:
    """M96 N1: the recorder's flag, or the own-broker data tier (whose own reads are stored in the same table)."""
    from app.market_data import live_access
    from app.platform.controls import flag_enabled
    if await flag_enabled(session, chain_recorder.FLAG, user.tenant_id) or await live_access.byob_on(session, user.tenant_id):
        return
    raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": chain_recorder.FLAG})


def _ist(moment: datetime) -> datetime:
    """A date-time without a zone is IST (the pickers' clock)."""
    return moment.replace(tzinfo=IST) if moment.tzinfo is None else moment


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


@router.get("/oi-range")
async def oi_range(underlying: str = Query(..., pattern=UNDERLYING), expiry: date = Query(...), start: datetime = Query(...),
                   end: datetime = Query(...), user: User = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _own_history(session, user)
    start, end = _ist(start), _ist(end)
    if end <= start:
        raise HTTPException(status_code=422, detail="The 'To' time must be after the 'From' time.")
    if (end.astimezone(IST).date() - start.astimezone(IST).date()).days + 1 > config.CHAIN_HISTORY_READ_MAX_DAYS:
        raise HTTPException(status_code=422, detail=f"At most {config.CHAIN_HISTORY_READ_MAX_DAYS} days per request.")
    name = _name(underlying)
    first = await chain_range.chain_at(session, user.tenant_id, user.id, name, expiry, start)
    last = await chain_range.chain_at(session, user.tenant_id, user.id, name, expiry, end)
    head = {"underlying": name, "expiry": expiry.isoformat(), "start": start.isoformat(), "end": end.isoformat()}
    if first is None or last is None or first.captured_at >= last.captured_at:
        missing = "both" if first is None and last is None else "start" if first is None else "end" if last is None else "same"
        return {**head, "missing": missing, "from": chain_range.meta(first) if first else None,
                "to": chain_range.meta(last) if last else None, "atm": None, "rows": [], "totals": None}
    return {**head, "missing": None, **chain_range.oi_diff(first, last)}


@router.get("/oi-replay")
async def oi_replay(underlying: str = Query(..., pattern=UNDERLYING), expiry: date = Query(...), day: date = Query(...),
                    around: int = Query(default=0, ge=0, le=50), user: User = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _own_history(session, user)
    name = _name(underlying)
    try:
        chains = await chain_store.read_day(session, user.tenant_id, name, day, expiry=expiry, reader_user_id=user.id,
                                            max_chains=config.CHAIN_HISTORY_READ_MAX_CHAINS)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"underlying": name, "expiry": expiry.isoformat(), "day": day.isoformat(), "around": around,
            **chain_range.replay_frames(chains, around)}


@router.get("/expiries")
async def expiries(underlying: str = Query(..., pattern=UNDERLYING), at: Optional[datetime] = Query(default=None),
                   user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _own_history(session, user)
    from app.market_data.calendar import family_holidays
    from app.option_chain import chain_view
    name = _name(underlying)
    moment = _ist(at) if at is not None else datetime.now(timezone.utc)
    day = moment.astimezone(IST).date()
    holidays = await family_holidays(session, chain_view.spot_instrument(name)[1])
    rate = await chain_view.risk_free_rate_for(session, user)
    items = []
    for expiry in await chain_range.day_expiries(session, user.tenant_id, user.id, name, day):
        chain = await chain_range.chain_at(session, user.tenant_id, user.id, name, expiry, moment)
        if chain is None:
            continue
        first = await chain_range.first_of_day(session, user.tenant_id, user.id, name, expiry, day, before=moment)
        items.append(chain_range.expiry_summary(chain, first if first is not None and first.captured_at < chain.captured_at else None,
                                                holidays=holidays, rate=rate))
    return {"underlying": name, "day": day.isoformat(), "at": moment.astimezone(IST).isoformat(), "expiries": items}


@admin_router.get("/status")
async def status(session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    return await chain_recorder.status(session)
