"""M99 P4: the "Wyckoff" chart layer - the trading ranges on one symbol's chart: each range as a box (its two edges, from
the climax to its last bar), the phase labels A-E where each phase began, and every event as a mark with its "Why?".

GET /api/charts/layers/wyckoff?symbol=RELIANCE&exchange=NSE&timeframe=1h
  * The bars are the screener's own (`screener.routes.load_universe`: store first, the user's broker after; an index takes
    its near-month future's volume, and its 0 volume is no volume data, M93-Q3) on the timeframe asked, closed bars only -
    the same bars a "Wyckoff" scan of that symbol reads.
  * The readings are `app/price_action/wyckoff.py`'s `evaluate` with the reference settings - the ONE evaluation the
    scanners (ScreenQL `Wyckoff(...)`) and the K templates read, so a mark on the chart is a match of the same scanner on
    the bars cut at that bar (tested).
  * `ranges`: the boxes whose bars reach into the newest MAX_BARS bars; `phases`: where each phase began; `marks`: the
    events in those bars, each on the bar it was known (`origin_time`: the bar it is about), with its side, short label
    and "Why?" in structure words - no instruction and no single price.
  * No volume data on the bars: `available` false and the reason "no volume data" - never a reading from a guessed volume.
Behind the `wyckoff_pack` flag (off by default -> 503). Signals only: it places, proposes and changes no order.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session, release_connection
from app.platform.controls import require_flag
from app.price_action import wyckoff

router = APIRouter(prefix="/api/charts/layers", tags=["charts"])
FLAG = "wyckoff_pack"
SCHEMA = "wyckoff-layer/1"
MAX_BARS = 300                       # the newest bars drawn (a display limit; the evaluation reads the whole history)
NOTE = "Trading ranges read in phases from price, volume and swings - a description of past bars, not advice."
NO_BARS = "No closed bars for this symbol and timeframe yet."
TIMEFRAMES: tuple[str, ...] = ("1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w")
rate_limited = user_rate_limit("chart_wyckoff_layer", limit=60, window_seconds=60)
PHASE_WORDS: dict[str, str] = {"A": "Phase A - the old move stops", "B": "Phase B - the range is built",
                               "C": "Phase C - the shake and its test", "D": "Phase D - the range is left",
                               "E": "Phase E - the move out of the range"}


def _iso(t: Any) -> str:
    import pandas as pd
    ts = pd.Timestamp(t)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.isoformat().replace("+00:00", "Z")


def screen_text() -> str:
    """The screen whose bars the layer reads (only its data needs are used; the readings are wyckoff's)."""
    return 'Wyckoff("in_range")'


def layer_of(frame: Any, settings: wyckoff.WyckoffSettings, tf: str = "1h", max_bars: int = MAX_BARS) -> dict[str, Any]:
    """The ranges, phases and events of the newest `max_bars` bars of a closed-bar frame (pure; the route's core)."""
    r = wyckoff.evaluate(frame, settings, tf)
    if not r.has_volume:
        return {"available": False, "reason": wyckoff.NO_VOLUME, "marks": [], "ranges": [], "phases": [], "bars": r.n}
    first = max(0, r.n - max_bars)
    marks = []
    for e in r.events:
        if e.bar < first:
            continue
        w = wyckoff.why(r, e)
        marks.append({"time": _iso(frame.index[e.bar]), "origin_time": _iso(frame.index[e.origin]), **w})
    ranges: list[dict[str, Any]] = []
    phases: list[dict[str, Any]] = []
    i = 0
    while i < r.n:
        start = int(r.range_start[i])
        if start < 0:
            i += 1
            continue
        j = i
        while j + 1 < r.n and int(r.range_start[j + 1]) == start:
            j += 1
        if j >= first:
            tops = r.top[i:j + 1]
            ok = np.isfinite(tops)
            if ok.any():
                k = i + int(np.argmax(ok))
                ranges.append({"start_time": _iso(frame.index[start]), "end_time": _iso(frame.index[j]),
                               "known_time": _iso(frame.index[k]), "top": float(r.top[k]), "bottom": float(r.bottom[k]),
                               "after": str(r.after[k]), "last_phase": str(r.phase[j]), "lean": str(r.lean[j]),
                               "open": j == r.n - 1})
            for q in range(i, j + 1):
                if q >= first and (q == i or r.phase[q] != r.phase[q - 1]):
                    phases.append({"time": _iso(frame.index[q]), "phase": str(r.phase[q]), "words": PHASE_WORDS[str(r.phase[q])],
                                   "lean": str(r.lean[q])})
        i = j + 1
    last = r.n - 1
    bias = str(r.bias[last]) if r.n and r.bias_known[last] else ""
    return {"available": True, "reason": None, "marks": marks, "ranges": ranges, "phases": phases, "bars": r.n,
            "bias": bias, "as_of": _iso(frame.index[-1]) if r.n else None}


@router.get("/wyckoff", dependencies=[Depends(rate_limited)])
async def wyckoff_layer(symbol: str = Query(..., min_length=1, max_length=60), exchange: str = Query("NSE", max_length=10),
                        timeframe: str = Query("1h", max_length=4),
                        user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=422, detail=f"unknown timeframe {timeframe}; use {', '.join(TIMEFRAMES)}")
    from app.screener import parse
    from app.screener import routes as R
    from app.screener.runtime import closed_only, no_index_volume
    from app.screener.validator import validate
    s = wyckoff.WyckoffSettings()
    ast = parse(screen_text())
    validated = validate(ast, base_tf=timeframe, params={})
    sym = symbol.strip().upper()
    head = {"schema": SCHEMA, "symbol": sym, "exchange": exchange, "base_tf": timeframe, "note": NOTE,
            "settings": {name: getattr(s, name) for name, *_ in wyckoff.SETTINGS_TABLE}, "settings_note": wyckoff.REFERENCE_LABEL,
            "phase_words": PHASE_WORDS}
    store = await R.store_first(session, user.tenant_id)
    universe, problems, data_source, _ = await R.load_universe(session, user, ast, validated, {}, [sym], exchange, timeframe, {},
                                                               release=True, budget={}, store=store, pending=[])
    await release_connection(session)
    data = next((d for d in universe if d.symbol == sym), None)
    if data is None or data.frames.get(timeframe) is None or not len(data.frames[timeframe]):
        why = problems.get(sym)
        return {**head, "available": False, "reason": str(why) if why else NO_BARS, "marks": [], "ranges": [], "phases": [],
                "data_source": data_source}
    no_index_volume([data])                          # M93-Q3: an index's 0 volume is no volume data (as run_screen)
    frame = closed_only(data.frames[timeframe], timeframe, datetime.now(UTC), exchange)      # closed bars only
    got = await asyncio.to_thread(layer_of, frame, s, timeframe)
    return {**head, **got, "data_source": data_source}


__all__ = ["router", "FLAG", "SCHEMA", "MAX_BARS", "TIMEFRAMES", "PHASE_WORDS", "layer_of", "screen_text"]
