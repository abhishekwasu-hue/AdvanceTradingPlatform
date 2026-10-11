"""Part B5: GET /api/market-data/history - lake candles as known at `as_of`, adjusted or not.

The point-in-time read every research tool and (next) every backtest goes through: bars labelled by their END,
`as_of` hides rows ingested later (and corrections made later), `adjusted` applies splits/bonuses known at `as_of`
(equity only, B4). The response says what it is - window, as_of, adjusted, sources used, and how many data-quality
events (B3) fall inside the window - so a chart or a backtest never presents patched data as clean.

HB0: `store=shared` (default) reads the shared lake that every tenant may see; `store=tenant` reads ONLY the caller's
own tenant-private store (the owner's licensed 1-minute history, resampled to 3/5/15/30/60 on read). The tenant is
always the authenticated user's - never a parameter - so no request can name another tenant's store, and the shared
read never includes private rows. Quality-event counts follow the same split.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core import config
from app.db.models import DataQualityEventRecord, User
from app.db.session import get_session
from app.market_lake import adjust, tenant_store

router = APIRouter(prefix="/api/market-data", tags=["market-data"])

DAILY_TIMEFRAMES = frozenset({"1d", "day", "d", "1day"})


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@router.get("/history")
async def history(
    instrument_key: str = Query(min_length=1, max_length=64), timeframe: str = Query(min_length=1, max_length=8),
    start: datetime = Query(...), end: datetime = Query(...), as_of: Optional[datetime] = None, adjusted: bool = True,
    kind: str = Query("EQ", max_length=12), symbol: Optional[str] = Query(None, max_length=40), exchange: str = Query("NSE", max_length=20),
    sources: Optional[str] = Query(None, max_length=200, description="comma-separated preference order"),
    store: str = Query("shared", pattern="^(shared|tenant)$", description="shared lake, or your own tenant's private store"),
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> dict:
    start, end, as_of = _utc(start), _utc(end), _utc(as_of)
    if end <= start:
        raise HTTPException(status_code=422, detail="end must be after start")
    preferred = [s.strip() for s in sources.split(",") if s.strip()] if sources else None
    sym = symbol or instrument_key.split(":")[-1]
    if store == "shared" and timeframe.strip().lower() not in DAILY_TIMEFRAMES:
        from app.market_data import live_access
        if await live_access.byob_on(session, user.tenant_id):
            # BT1b: intraday bars in the shared lake were recorded from other organisations' broker streams / backfills.
            raise live_access.LiveDataRefused("Intraday bars in the shared history were recorded through other sessions' broker feeds, so "
                                              "they are not shown while the bring-your-own-broker data tier is on; daily (EOD) bars are.")
    if store == "tenant":
        try:
            bars = await tenant_store.candles(session, user.tenant_id, instrument_key, timeframe, start, end, symbol=sym,
                                              exchange=exchange, kind=kind, adjusted=adjusted, as_of=as_of, sources=preferred)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        owner = DataQualityEventRecord.tenant_id == user.tenant_id
    else:
        bars = await adjust.candles(session, instrument_key, timeframe, start, end, symbol=sym,
                                    exchange=exchange, kind=kind, adjusted=adjusted, as_of=as_of, sources=preferred)
        owner = DataQualityEventRecord.tenant_id.is_(None)
    if len(bars) > config.LAKE_HISTORY_MAX_BARS:
        raise HTTPException(status_code=422, detail=f"{len(bars)} bars in the window; at most {config.LAKE_HISTORY_MAX_BARS} per request")
    quality = int(await session.scalar(select(func.count()).select_from(DataQualityEventRecord).where(
        DataQualityEventRecord.instrument_key == instrument_key, DataQualityEventRecord.ts > start,
        DataQualityEventRecord.ts <= end, owner)) or 0)
    return {
        "instrument_key": instrument_key, "timeframe": timeframe, "start": start.isoformat(), "end": end.isoformat(),
        "store": store,
        "as_of": as_of.isoformat() if as_of else None, "adjusted": bool(adjusted and kind.upper() in adjust.EQUITY_KINDS),
        "bar_label": "end", "quality_events": quality,
        "candles": [{"ts": _utc(b.ts).isoformat(), "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                     "volume": b.volume, "oi": b.oi} for b in bars],
    }
