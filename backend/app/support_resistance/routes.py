"""`POST /api/support-resistance/zones`: support / resistance zones from the candles the request sends.

M55 Q1: moved from app/main.py (which now only includes routers) as it was - only the annotations are in built-in
generics; included at the end of the router list in the same order as before, so every path, method, dependency
and the match order are unchanged (tests/test_m55_q1_route_table.py).
"""
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.backtest.server_candles import DataChoice
from app.cache.client import cache_get, cache_set
from app.core.models import OHLCVBar, bars_to_dataframe
from app.db.models import User
from app.db.session import get_session
from app.price_action.routes import CandlesRequest
from app.support_resistance.engine import SupportResistanceEngine
from app.support_resistance.models import SRZone

router = APIRouter()


class SRZonesRequest(CandlesRequest, DataChoice):
    candles: list[OHLCVBar] = Field(default_factory=list)      # M55 T3: no candles = the server loads them
    tolerance_pct: float = 0.15
    opening_range_minutes: int = 15


def _cache_key(prefix: str, payload: str) -> str:
    return f"{prefix}:{hashlib.sha256(payload.encode()).hexdigest()}"


@router.post("/api/support-resistance/zones", response_model=list[SRZone])
async def support_resistance_zones(request: SRZonesRequest, response: Response, user: User = Depends(get_current_user),
                                   session: AsyncSession = Depends(get_session)) -> list[SRZone]:
    """Pure function of its input candles, so short-lived results are cached in Redis (when
    reachable - this fails open to a plain recompute otherwise) to avoid rebuilding the same
    swing-cluster/pivot/Fibonacci zones on every identical repeated call.
    M55 T3: no candles = the server loads them for symbol / timeframe; the `X-Data-Source` header says where they came
    from ("uploaded" for pasted candles).
    """
    from app.backtest import server_candles
    if server_candles.mode_of(request, request.candles) == server_candles.PASTE:
        if not request.candles:
            raise HTTPException(status_code=422, detail=server_candles.PASTE_NEEDS_CANDLES)
        source = server_candles.UPLOADED
    else:
        request.candles, source, _line = await server_candles.load(session, user, request.symbol, request.timeframe, request)
    response.headers["X-Data-Source"] = source
    key = _cache_key("sr_zones", request.model_dump_json(include={"symbol", "candles", "timeframe", "swing_window", "tolerance_pct",
                                                                   "opening_range_minutes"}))
    cached = await cache_get(key)
    if cached is not None:
        return [SRZone.model_validate(z) for z in json.loads(cached)]

    df = bars_to_dataframe(request.candles)
    engine = SupportResistanceEngine(
        swing_window=request.swing_window,
        tolerance_pct=request.tolerance_pct,
        opening_range_minutes=request.opening_range_minutes,
    )
    zones = await run_in_threadpool(engine.build_zones, df, request.timeframe)
    await cache_set(key, "[" + ",".join(z.model_dump_json() for z in zones) + "]", ttl_seconds=5)
    return zones
