"""Z7a-3 (CH3 / U8 minimal style): the market-zones chart layer - the stored zones of an instrument as of a time, for the
caller's organisation, with at most six bands picked by role and proximity.

GET /api/charts/layers/market-zones?instrument=NSE:BANKNIFTY&at=<ISO time>&price=<reference price>
  * `at` (timezone-aware; default now): the snapshot `zones_as_of` returns then - the latest stored with as_of <= at, in
    the organisation's scope (its own settings' snapshot, else the platform's), never one computed from later bars and
    never another organisation's. A time in the future reads as now.
  * an organisation whose own zone settings have no snapshot yet gets the platform's (default settings), with
    `fallback: true` and `settings_note` - the chart says so on every band. ScreenQL never falls back.
  * `price` (default: the snapshot's own last close): the price roles and distances are measured from - a chart passes
    its last close so "demand" / "supply" follow the price on screen.
  * The response carries every number the chart draws (bounds, label prices, touches, strength, distances) and the
    labels themselves ("demand 512.30 (fresh)", "2 touches"); `zones` are all live candidates, for a chart that re-picks
    its bands as the price moves (the same rule, frontend/src/charting/marketZones.ts).
Behind the `market_zones` flag (off by default -> 503). Reference data: nothing here places or proposes an order.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_session
from app.platform.controls import require_flag
from app.price_action.zones import chart, read
from app.price_action.zones.store import normalise_key

router = APIRouter(prefix="/api/charts/layers", tags=["charts"])
FLAG = "market_zones"
NOTE = "Zones are reference levels from past prices, not signals or recommendations."
NO_ZONES = "no zones for this instrument"
FAR_ZONES = f"no zones within {chart.MAX_DISTANCE_PCT:g} % of the price"
FALLBACK_NOTE = "platform default settings (none stored with your organisation's zone settings yet)"


def _iso(t: Optional[datetime]) -> Optional[str]:
    return None if t is None else t.astimezone(timezone.utc).isoformat()


@router.get("/market-zones")
async def market_zones_layer(instrument: str = Query(..., min_length=1, max_length=100), at: Optional[datetime] = None,
                             price: Optional[float] = Query(default=None, gt=0),
                             family: Literal["all", "zones", "levels"] = "all",
                             max_bands: int = Query(default=chart.MAX_BANDS, ge=1, le=chart.MAX_BANDS),
                             user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    now = datetime.now(timezone.utc)
    try:
        key: Optional[str] = normalise_key(instrument)
    except ValueError:
        key = None
    if key is None or key == "*":
        # not an instrument the lake keys (an option contract, a symbol with characters keys never hold): the chart just
        # has no zones - never an error on the chart
        return {"instrument": instrument, "at": _iso(now), "as_of": None, "scope": None, "fallback": False, "settings_note": None,
                "price": price, "price_source": None, "bands": [], "zones": [], "live_count": 0, "max_bands": max_bands,
                "reason": NO_ZONES, "note": NOTE}
    if at is not None and at.tzinfo is None:
        raise HTTPException(status_code=422, detail="`at` must carry its time zone (for example 2026-10-09T15:30:00+05:30)")
    when = now if at is None or at > now else at.astimezone(timezone.utc)
    if price is not None and not math.isfinite(price):
        raise HTTPException(status_code=422, detail="price must be a finite number")
    snap = await read.read_as_of(session, key, when, user.tenant_id, allow_platform_fallback=True)
    if snap is None:
        return {"instrument": key, "at": _iso(when), "as_of": None, "scope": None, "fallback": False, "settings_note": None, "price": price,
                "price_source": None, "bands": [], "zones": [], "live_count": 0, "max_bands": max_bands, "reason": NO_ZONES, "note": NOTE}
    ref, source = (price, "request") if price is not None else (snap.price, "snapshot")
    cands = chart.candidates(snap.rows, family)
    bands = chart.select_bands(cands, ref, max_bands=max_bands) if ref is not None else []
    scope = "platform" if snap.scope == "platform" else ("organisation" if snap.scope else None)
    return {"instrument": key, "at": _iso(when), "as_of": _iso(snap.as_of), "scope": scope, "fallback": snap.fallback,
            "settings_note": FALLBACK_NOTE if snap.fallback else None, "price": ref, "price_source": source,
            "price_text": None if ref is None else chart.price_text(ref), "bands": bands, "zones": cands, "live_count": len(cands),
            "max_bands": max_bands, "reason": None if bands else (NO_ZONES if not cands else FAR_ZONES), "note": NOTE}


__all__ = ["router", "FLAG"]
