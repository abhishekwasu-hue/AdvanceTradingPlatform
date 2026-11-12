"""M99 P6: the "Price patterns" chart layer - the price-action pattern readings on one symbol's chart, each mark with its
"Why?", the optional indicator evidence rows and the nearest support / resistance information.

GET /api/charts/layers/price-patterns?symbol=RELIANCE&exchange=NSE&timeframe=1d&evidence=adx,macd
  * The bars are the screener's own (`screener.routes.load_universe`: store first, the user's broker after; an index takes
    its near-month future's volume) on the screen timeframe asked, closed bars only - the same bars a "Price patterns"
    scan of that symbol reads.
  * The readings are `app/price_action/pa_pack.py`'s `evaluate` with the reference settings - the ONE evaluation the
    scanners (ScreenQL `PricePattern(...)`) and the M templates read, so a mark on the chart is a match of the same
    scanner on the bars cut at that bar (tested).
  * `marks`: the newest MAX_BARS bars' readings, each with the bar time (its start), the reading, its side (up / down /
    none, a description), its short label and the "Why?" text in structure words - no instruction and no single price.
    The evidence layers asked (all off by default) add rows to each mark's Why?; they never add or remove a mark.
  * `levels`: the nearest swing areas below and above the last close with their touches and the existing level
    strength score - information only, never a signal (the user's own chart: an area as a price range).
Behind the `pa_pack` flag (off by default -> 503). Signals only: it places, proposes and changes no order.
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
from app.price_action import pa_pack as pa

router = APIRouter(prefix="/api/charts/layers", tags=["charts"])
FLAG = "pa_pack"
SCHEMA = "price-patterns-layer/1"
MAX_BARS = 300                       # the newest bars marked (a display limit; the evaluation reads the whole history)
NOTE = "Readings of the bars and swings - a description of past bars, not advice."
NO_BARS = "No closed bars for this symbol and timeframe yet."
TIMEFRAMES: tuple[str, ...] = ("1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w")
rate_limited = user_rate_limit("chart_pa_layer", limit=60, window_seconds=60)


def _iso(t: Any) -> str:
    import pandas as pd
    ts = pd.Timestamp(t)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.isoformat().replace("+00:00", "Z")


def screen_text() -> str:
    """The screen whose bars the layer reads (only its data needs are used; the marks are pa_pack's)."""
    return 'PricePattern("fault")'


def title_of(e: pa.Event) -> str:
    if e.name in pa.MIRROR_TITLES and ((e.name == pa.HAMMER and e.side == pa.DOWN) or (e.name == pa.HEAD_SHOULDERS and e.side == pa.UP)
                                       or (e.name == pa.CUP and e.side == pa.DOWN)):
        return pa.MIRROR_TITLES[e.name]
    return pa.TITLES[e.name]


def marks_of(frame: Any, settings: pa.PaSettings, evidence: pa.EvidenceSettings | None = None,
             max_bars: int = MAX_BARS) -> dict[str, Any]:
    """The readings of the newest `max_bars` bars of a closed-bar frame, each with its Why? and evidence rows (pure; the
    route's core)."""
    res = pa.evaluate(frame, settings)
    first = max(0, res.n - max_bars)
    rows = [e for e in res.rows() if e.bar >= first]
    ev = pa.evidence_rows(frame, res, evidence, rows) if evidence is not None else {}
    marks = []
    for k, e in enumerate(rows):
        marks.append({"time": _iso(frame.index[e.bar]), "origin_time": _iso(frame.index[e.origin]), "bar": e.bar, "origin": e.origin,
                      "name": e.name, "label": title_of(e), "short": pa.SHORT[e.name], "side": e.side, "variant": e.variant,
                      "text": e.why, "evidence": ev.get(k, [])})
    return {"available": True, "reason": None, "marks": marks, "bars": res.n, "levels": pa.sr_information(frame, res),
            "as_of": _iso(frame.index[-1]) if res.n else None}


def parse_evidence(text: str) -> tuple[str, ...]:
    names = tuple(x.strip() for x in (text or "").split(",") if x.strip())
    bad = [x for x in names if x not in pa.EVIDENCE_LAYERS]
    if bad:
        raise HTTPException(status_code=422, detail=f"unknown evidence layer(s): {', '.join(bad)}; use {', '.join(pa.EVIDENCE_LAYERS)}")
    return tuple(dict.fromkeys(names))


@router.get("/price-patterns", dependencies=[Depends(rate_limited)])
async def pa_layer(symbol: str = Query(..., min_length=1, max_length=60), exchange: str = Query("NSE", max_length=10),
                   timeframe: str = Query("1d", max_length=4), evidence: str = Query("", max_length=200),
                   user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=422, detail=f"unknown timeframe {timeframe}; use {', '.join(TIMEFRAMES)}")
    layers = parse_evidence(evidence)
    from app.screener import parse
    from app.screener import routes as R
    from app.screener.runtime import closed_only, no_index_volume
    from app.screener.validator import validate
    s = pa.PaSettings()
    ev = pa.evidence_from(layers)
    ast = parse(screen_text())
    validated = validate(ast, base_tf=timeframe, params={})
    sym = symbol.strip().upper()
    head = {"schema": SCHEMA, "symbol": sym, "exchange": exchange, "base_tf": timeframe, "note": NOTE,
            "settings": {name: getattr(s, name) for name in pa.SETTING_NAMES}, "settings_note": pa.REFERENCE_LABEL,
            "names": pa.TITLES, "short": pa.SHORT, "evidence_layers": list(layers), "evidence_titles": pa.EVIDENCE_TITLES}
    store = await R.store_first(session, user.tenant_id)
    universe, problems, data_source, _ = await R.load_universe(session, user, ast, validated, {}, [sym], exchange, timeframe, {},
                                                               release=True, budget={}, store=store, pending=[])
    await release_connection(session)
    data = next((d for d in universe if d.symbol == sym), None)
    if data is None or data.frames.get(timeframe) is None or not len(data.frames[timeframe]):
        why = problems.get(sym)
        return {**head, "available": False, "reason": str(why) if why else NO_BARS, "marks": [], "levels": [], "data_source": data_source}
    no_index_volume([data])                          # M93-Q3: an index's 0 volume is no volume data (as run_screen)
    frame = closed_only(data.frames[timeframe], timeframe, datetime.now(UTC), exchange)      # closed bars only
    got = await asyncio.to_thread(marks_of, frame, s, ev)
    return {**head, **got, "data_source": data_source}


__all__ = ["router", "FLAG", "SCHEMA", "MAX_BARS", "TIMEFRAMES", "marks_of", "screen_text", "parse_evidence", "title_of"]
