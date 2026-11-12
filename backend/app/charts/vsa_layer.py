"""M99 P3: the "Volume spread" chart layer - the volume spread readings on one symbol's chart, each mark with its "Why?".

GET /api/charts/layers/vsa?symbol=RELIANCE&exchange=NSE&timeframe=1d&background=true&confirm=false
  * The bars are the screener's own (`screener.routes.load_universe`: store first, the user's broker after; an index takes
    its near-month future's volume, and its 0 volume is no volume data, M93-Q3) on the screen timeframe asked, closed bars
    only - the same bars a "Volume spread" scan of that symbol reads.
  * The readings are `app/price_action/vsa.py`'s `evaluate` with the reference settings and the two switches asked
    (background, next-bar confirmation) - the ONE evaluation the scanners (ScreenQL `VSA(...)`) and the J templates
    read, so a mark on the chart is a match of the same scanner on the bars cut at that bar (tested).
  * `marks`: the newest MAX_BARS bars' readings, each with the bar time (its start), the reading, its side (strength /
    weakness, a description), its short label and the "Why?" text in structure words (spread, volume, close position,
    the structure before, the background) - no instruction and no single price.
  * No volume data on the bars: `available` false and the reason "no volume data" - never a reading from a guessed
    volume.
Behind the `vsa_pack` flag (off by default -> 503). Signals only: it places, proposes and changes no order.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session, release_connection
from app.platform.controls import require_flag
from app.price_action import vsa

router = APIRouter(prefix="/api/charts/layers", tags=["charts"])
FLAG = "vsa_pack"
SCHEMA = "vsa-layer/1"
MAX_BARS = 300                       # the newest bars marked (a display limit; the evaluation reads the whole history)
NOTE = "Readings of spread, volume and close against the bars before - a description of past bars, not advice."
NO_BARS = "No closed bars for this symbol and timeframe yet."
TIMEFRAMES: tuple[str, ...] = ("1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w")
rate_limited = user_rate_limit("chart_vsa_layer", limit=60, window_seconds=60)


def _iso(t: Any) -> str:
    import pandas as pd
    ts = pd.Timestamp(t)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.isoformat().replace("+00:00", "Z")


def screen_text(names: tuple[str, ...] = vsa.DETECTORS) -> str:
    """The screen whose bars the layer reads: every reading OR-ed (only its data needs are used; the marks are vsa's)."""
    return " OR ".join(f'VSA("{name}")' for name in names)


def marks_of(frame: Any, settings: vsa.VsaSettings, max_bars: int = MAX_BARS) -> dict[str, Any]:
    """The readings of the newest `max_bars` bars of a closed-bar frame, each with its Why? (pure; the route's core)."""
    sig = vsa.evaluate(frame, settings)
    if not sig.has_volume:
        return {"available": False, "reason": vsa.NO_VOLUME, "marks": [], "bars": sig.n}
    first = max(0, sig.n - max_bars)
    marks = []
    for i, name in sig.rows():
        if i < first:
            continue
        w = vsa.why(sig, name, i)
        marks.append({"time": _iso(frame.index[i]), "origin_time": _iso(frame.index[w["origin"]]), **w})
    unknown = int(sum(1 for i in range(first, sig.n) if not any(sig.known[k][i] for k in vsa.DETECTORS)))
    return {"available": True, "reason": None, "marks": marks, "bars": sig.n, "unknown_bars": unknown,
            "as_of": _iso(frame.index[-1]) if sig.n else None}


@router.get("/vsa", dependencies=[Depends(rate_limited)])
async def vsa_layer(symbol: str = Query(..., min_length=1, max_length=60), exchange: str = Query("NSE", max_length=10),
                    timeframe: str = Query("1d", max_length=4), background: bool = True, confirm: bool = False,
                    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=422, detail=f"unknown timeframe {timeframe}; use {', '.join(TIMEFRAMES)}")
    from app.screener import parse
    from app.screener import routes as R
    from app.screener.runtime import closed_only, no_index_volume
    from app.screener.validator import validate
    s = vsa.settings_from({"background": background, "confirm": confirm})
    ast = parse(screen_text())
    validated = validate(ast, base_tf=timeframe, params={})
    sym = symbol.strip().upper()
    head = {"schema": SCHEMA, "symbol": sym, "exchange": exchange, "base_tf": timeframe, "note": NOTE,
            "settings": {name: getattr(s, name) for name, *_ in vsa.SETTINGS_TABLE}, "settings_note": vsa.REFERENCE_LABEL,
            "names": vsa.WHY_NAMES, "short": vsa.SHORT}
    store = await R.store_first(session, user.tenant_id)
    universe, problems, data_source, _ = await R.load_universe(session, user, ast, validated, {}, [sym], exchange, timeframe, {},
                                                               release=True, budget={}, store=store, pending=[])
    await release_connection(session)
    data = next((d for d in universe if d.symbol == sym), None)
    if data is None or data.frames.get(timeframe) is None or not len(data.frames[timeframe]):
        why = problems.get(sym)
        return {**head, "available": False, "reason": str(why) if why else NO_BARS, "marks": [], "data_source": data_source}
    no_index_volume([data])                          # M93-Q3: an index's 0 volume is no volume data (as run_screen)
    frame = closed_only(data.frames[timeframe], timeframe, datetime.now(UTC), exchange)      # closed bars only
    got = await asyncio.to_thread(marks_of, frame, s)
    return {**head, **got, "data_source": data_source}


__all__ = ["router", "FLAG", "SCHEMA", "MAX_BARS", "TIMEFRAMES", "marks_of", "screen_text"]
