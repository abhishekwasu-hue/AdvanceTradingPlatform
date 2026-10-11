"""OI Banner O2: the history API (read-only; numbers carry their data timestamps).

* `GET /api/option-chain/{underlying}/banner`   the latest slot's banner, with stale / market-closed flags
* `GET /api/option-chain/{underlying}/history`  the day's banner timeline (`interval` 5, 10 or 15 minutes, newest first)
* `GET /api/option-chain/{underlying}/strikes`  per-strike call/put OI through the day (the window of the latest slot)
* `GET /api/option-chain/{underlying}/settings` and `PUT` (owner) - the tenant's settings for the underlying; "*" is
  the tenant default for every underlying. `enabled` asks the collector to follow the underlying.

Every reading is recomputed from the stored strikes with the caller's tenant settings (app/option_chain/snapshots.py).
Nothing here places, changes or suggests an order (ADR-0006).
"""
import json
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_role
from app.core.enums import UserRole
from app.db.models import OIBannerSettingRecord, User
from app.db.session import get_session
from app.market_data.calendar import market_session_status
from app.option_chain import oi_regime, snapshots
from app.option_chain.snapshots import IST

router = APIRouter(prefix="/api/option-chain", tags=["option-chain"])


def _underlying(underlying: str) -> str:
    try:
        return snapshots.normalise_underlying(underlying)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _exchange(session: AsyncSession, tenant_id: int, underlying: str) -> str:
    _, own = await snapshots.settings_rows(session, tenant_id, underlying)
    return own.exchange if own is not None else "NSE"


async def _day_view(session: AsyncSession, user: User, underlying: str, day: Optional[date]):
    now = datetime.now(timezone.utc)
    settings = await snapshots.settings_for(session, user.tenant_id, underlying)
    day = day or now.astimezone(IST).date()
    slots = await snapshots.day_chains(session, underlying, day)
    status = await market_session_status(session, now, await _exchange(session, user.tenant_id, underlying))
    flags = snapshots.staleness(await snapshots.latest_capture(session, underlying), now, settings, status.is_open and day == now.astimezone(IST).date())
    return settings, day, slots, flags


@router.get("/{underlying}/banner")
async def banner(underlying: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    underlying = _underlying(underlying)
    settings, day, slots, flags = await _day_view(session, user, underlying, None)
    timeline = snapshots.replay(underlying, slots, settings, day)
    if not timeline:
        return {"underlying": underlying, "date": day.isoformat(), "banner": None, **flags,
                "message": oi_regime.INSUFFICIENT_HISTORY_MESSAGE}
    return {"underlying": underlying, "date": day.isoformat(), "banner": snapshots.entry_json(timeline[-1]), **flags}


@router.get("/{underlying}/history")
async def history(underlying: str, day: Optional[date] = Query(default=None, alias="date"), interval: int = Query(default=5),
                  user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    if interval not in (5, 10, 15):
        raise HTTPException(status_code=422, detail="interval must be 5, 10 or 15")
    underlying = _underlying(underlying)
    settings, day, slots, flags = await _day_view(session, user, underlying, day)
    timeline = snapshots.replay(underlying, slots, settings, day)
    rows = [snapshots.entry_json(e) for _, e in oi_regime.aggregate_history(timeline, interval, lambda e: e.slot_start.astimezone(IST))]
    return {"underlying": underlying, "date": day.isoformat(), "interval": interval, "rows": rows, **flags}


@router.get("/{underlying}/strikes")
async def strikes(underlying: str, day: Optional[date] = Query(default=None, alias="date"),
                  user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    underlying = _underlying(underlying)
    settings, day, slots, flags = await _day_view(session, user, underlying, day)
    base = await snapshots.baselines(session, underlying, day)
    series = snapshots.strike_series(slots, settings)
    for s in series:
        call0, put0 = base.get(s["strike"], (None, None))
        s["baseline_call_oi"], s["baseline_put_oi"] = call0, put0
    return {"underlying": underlying, "date": day.isoformat(), "strikes": series,
            "data_as_of": slots[-1].captured_at.isoformat() if slots else None, **flags}


class SettingsBody(BaseModel):
    enabled: Optional[bool] = None
    exchange: Optional[str] = Field(default=None, min_length=2, max_length=10)
    overrides: Optional[Dict[str, Any]] = None


def _settings_json(row: Optional[OIBannerSettingRecord], effective: oi_regime.OIRegimeSettings) -> Dict[str, Any]:
    return {"enabled": bool(row.enabled) if row else False, "exchange": row.exchange if row else "NSE",
            "overrides": json.loads(row.overrides or "{}") if row else {}, "effective": effective.model_dump(),
            "updated_at": row.updated_at.isoformat() if row and row.updated_at else None}


@router.get("/{underlying}/settings")
async def get_settings(underlying: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    key = snapshots.DEFAULT_KEY if underlying == snapshots.DEFAULT_KEY else _underlying(underlying)
    default_row, own = await snapshots.settings_rows(session, user.tenant_id, key)
    row = default_row if key == snapshots.DEFAULT_KEY else own
    return {"underlying": key, **_settings_json(row, await snapshots.settings_for(session, user.tenant_id, key))}


@router.put("/{underlying}/settings")
async def put_settings(underlying: str, body: SettingsBody, user: User = Depends(require_role(UserRole.OWNER)),
                       session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    key = snapshots.DEFAULT_KEY if underlying == snapshots.DEFAULT_KEY else _underlying(underlying)
    default_row, own = await snapshots.settings_rows(session, user.tenant_id, key)
    row = default_row if key == snapshots.DEFAULT_KEY else own
    overrides = body.overrides if body.overrides is not None else (json.loads(row.overrides or "{}") if row else {})
    layers = [snapshots.platform_layer()] + ([] if key == snapshots.DEFAULT_KEY else [json.loads(default_row.overrides or "{}") if default_row else {}])
    try:
        oi_regime.resolve_settings(*layers, overrides)                    # refuse an invalid override before storing it
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row is None:
        row = OIBannerSettingRecord(tenant_id=user.tenant_id, underlying=key)
        session.add(row)
    if body.enabled is not None:
        row.enabled = body.enabled and key != snapshots.DEFAULT_KEY      # the collector follows named underlyings only
    if body.exchange is not None:
        row.exchange = body.exchange.upper()
    row.overrides = json.dumps(overrides, sort_keys=True)
    row.updated_by, row.updated_at = user.id, datetime.now(timezone.utc)
    await session.commit()
    return {"underlying": key, **_settings_json(row, await snapshots.settings_for(session, user.tenant_id, key))}
