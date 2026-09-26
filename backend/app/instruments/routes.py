"""Phase F1: instrument master API (platform-wide, read for any logged-in user).

GET  /api/instrument-master/status                      what is loaded, per broker/exchange, and when
GET  /api/instrument-master/search?q=&exchange=&type=   contracts matching a symbol/underlying fragment
GET  /api/instrument-master/expiries?underlying=&type=  upcoming expiries for an underlying (CE/PE/FUT)
GET  /api/instrument-master/strikes?underlying=&expiry= strikes available on one expiry
POST /api/instrument-master/sync                        SUPER_ADMIN: pull Upstox's public master now
"""
from datetime import date
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_mfa_session, require_role
from app.core import config
from app.db.models import User
from app.db.session import get_session
from app.instruments import master

router = APIRouter(prefix="/api/instrument-master", tags=["instruments"])


@router.get("/status")
async def status(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, object]:
    return {"sources": await master.master_status(session), "sync_exchanges": config.INSTRUMENT_SYNC_EXCHANGES}


@router.get("/search")
async def search(
    q: str = Query("", max_length=50), exchange: Optional[str] = None, type: Optional[str] = Query(None, alias="type"),
    broker: str = master.UPSTOX_BROKER, limit: int = Query(50, ge=1, le=500),
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> List[Dict[str, object]]:
    rows = await master.search_instruments(session, q, broker=broker, exchange=exchange, instrument_type=type, limit=limit)
    return [master.as_dict(r) for r in rows]


@router.get("/expiries")
async def list_expiries(
    underlying: str = Query(..., min_length=1, max_length=50), type: str = Query("CE", alias="type"),
    broker: str = master.UPSTOX_BROKER, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> Dict[str, object]:
    found = await master.expiries(session, underlying, broker=broker, instrument_type=type, on_or_after=date.today())
    return {"underlying": master.underlying_of(underlying), "type": type.upper(), "expiries": [e.isoformat() for e in found]}


@router.get("/strikes")
async def list_strikes(
    underlying: str = Query(..., min_length=1, max_length=50), expiry: date = Query(...), type: str = Query("CE", alias="type"),
    broker: str = master.UPSTOX_BROKER, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> Dict[str, object]:
    found = await master.strikes(session, underlying, expiry, broker=broker, instrument_type=type)
    lot = None
    if found:
        record = await master.find_option(session, underlying, expiry, found[0], type, broker=broker)
        lot = record.lot_size if record else None
    return {"underlying": master.underlying_of(underlying), "expiry": expiry.isoformat(), "type": type.upper(),
            "strikes": found, "lot_size": lot}


@router.post("/sync", dependencies=[Depends(require_mfa_session)])
async def sync_now(user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> Dict[str, object]:
    try:
        counts = await master.sync_upstox(session, config.INSTRUMENT_SYNC_EXCHANGES)
    except Exception as exc:  # noqa: BLE001 - surface the download error, do not 500
        raise HTTPException(status_code=502, detail=f"Instrument master download failed: {exc}") from exc
    await write_audit_log(session, user.tenant_id, user.id, "instrument_master_synced", f"upstox {counts}")
    await session.commit()
    return {"synced": counts, "sources": await master.master_status(session)}
