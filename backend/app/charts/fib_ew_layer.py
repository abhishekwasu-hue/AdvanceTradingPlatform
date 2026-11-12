"""M99 P5: the "Fibonacci + Elliott" chart layer - on one symbol's chart: the Fibonacci tool on the latest confirmed swings
(retracements, extensions, golden-section projections), the price clusters ahead of the open swing, the current wave count
with its rule checklist and invalidation lines, a contracting triangle's thrust projection, and every reading as a mark
with its "Why?".

GET /api/charts/layers/fib-ew?symbol=RELIANCE&exchange=NSE&timeframe=1h
  * The bars are the screener's own (`screener.routes.load_universe`: store first, the user's broker after) on the
    timeframe asked, closed bars only - the same bars a "Fibonacci + Elliott" scan of that symbol reads.
  * The readings are `app/price_action/fib_ew.py`'s `evaluate` with the reference settings - the ONE evaluation the
    scanners (ScreenQL `FibEw(...)`) and the L templates read, so a mark on the chart is a match of the same scanner on the
    bars cut at that bar (tested).
  * `count`: the main reading as known at the last closed bar - its wave ends (the last one still running), its type and
    clarity, the checklist of its rules (pass / open / broken) and its invalidation lines. Always labelled "Count is an
    interpretation".
  * `thrust`: a PROJECTION (the minimum estimate after a contracting triangle) - never a target; drawn dashed and labelled so.
Behind the `fib_ew_pack` flag (off by default -> 503). Signals only: it places, proposes and changes no order.
"""
from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session, release_connection
from app.platform.controls import require_flag
from app.price_action import elliott_rules as er
from app.price_action import fib_ew
from app.price_action.waves import counter as wc

router = APIRouter(prefix="/api/charts/layers", tags=["charts"])
FLAG = "fib_ew_pack"
SCHEMA = "fib-ew-layer/1"
MAX_BARS = 300                       # the newest bars whose marks are drawn (a display limit; the evaluation reads all history)
NOTE = f"{fib_ew.COUNT_NOTE}. Fibonacci levels and wave counts describe past bars - not advice, not a forecast."
THRUST_WORDS = "Thrust projection after the triangle - a minimum estimate, not a target"
NO_BARS = "No closed bars for this symbol and timeframe yet."
TIMEFRAMES: tuple[str, ...] = ("1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w")
rate_limited = user_rate_limit("chart_fib_ew_layer", limit=60, window_seconds=60)
TYPE_WORDS: dict[str, str] = {wc.IMPULSE: "Impulse", wc.DIAGONAL: "Diagonal", wc.ZIGZAG: "Zigzag", wc.FLAT: "Flat",
                              wc.EXPANDED_FLAT: "Expanded flat", wc.TRIANGLE: "Triangle", wc.DOUBLE: "Double three",
                              wc.UNCLEAR: "Not clear yet"}


def _iso(t: Any) -> str:
    import pandas as pd
    ts = pd.Timestamp(t)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.isoformat().replace("+00:00", "Z")


def screen_text() -> str:
    """The screen whose bars the layer reads (only its data needs are used; the readings are fib_ew's)."""
    return 'FibEw("cluster_touch")'


def _labels(r: wc.Reading) -> list[str]:
    if r.type == wc.DOUBLE:
        names = ["W", "X", "Y"]
    elif r.family == "impulse":
        names = ["1", "2", "3", "4", "5"]
    else:
        names = ["A", "B", "C", "D", "E"]
    return ["0"] + names[:len(r.points) - 1]


def _shape(r: wc.Reading, kind: str) -> str:
    """The shape the count was read as (on its own points when it was counted): contracting when those keep the
    contracting rules, else expanding."""
    return er.CONTRACTING if er.validate(r.points, kind, shape=er.CONTRACTING).valid else er.EXPANDING


def _checks(r: wc.Reading, points: list[float]) -> tuple[str, list[er.Check]]:
    """The rule checklist of the main reading with its latest running end (the last wave not complete): a rule the
    running wave has broken since the count was read shows as broken (the count is then void)."""
    if r.type == wc.IMPULSE:
        return er.IMPULSE, list(er.validate(points, er.IMPULSE).checks)
    if r.type in (wc.DIAGONAL, wc.TRIANGLE):
        kind = er.DIAGONAL if r.type == wc.DIAGONAL else er.TRIANGLE
        return kind, list(er.validate(points, kind, shape=_shape(r, kind)).checks)
    if r.type == wc.ZIGZAG:
        return er.ZIGZAG, list(er.validate(points, er.ZIGZAG).checks)
    return "", []


def _clarity(frame: Any, s: fib_ew.FibEwSettings, t: int) -> str:
    """The count's clarity on bar t (CLEAR / ALT_AGREES / ALT_DISAGREES / UNCLEAR), from the same cached count."""
    from app.price_action.waves.series import cached_wave_series
    value = cached_wave_series(frame, int(s.degree)).clarity[t]
    return str(value) if value is not None else wc.UNCLEAR


def count_of(frame: Any, res: fib_ew.FibEwResult) -> dict[str, Any] | None:
    """The main reading at the last bar, its checklist, its invalidation lines and the thrust projection (if any)."""
    L = res.waves
    if L is None or res.n == 0:
        return None
    t = res.n - 1
    r = L.main[t]
    if r is None:
        return None
    points = [float(p) for p in r.points[:-1]] + [float(L.running[t])]
    bars = [int(b) for b in r.bars[:-1]] + [int(L.running_bar[t])]
    kind, checks = _checks(r, points)
    lines: list[dict[str, Any]] = []
    own = float(res.levels["invalidation"][t])
    if math.isfinite(own):
        lines.append({"price": own, "words": "Count void beyond this level (the count's own rule)", "kind": "count"})
    for c in checks:
        if c.status == er.OPEN and c.level is not None and c.kind in (er.HARD, er.STRUCTURE) and math.isfinite(c.level):
            if all(abs(c.level - x["price"]) > 1e-9 for x in lines):
                lines.append({"price": float(c.level), "words": f"Rule line: {c.words.lower()}", "kind": "rule"})
    thrust = float(res.levels["thrust"][t])
    return {
        "type": r.type, "type_words": TYPE_WORDS.get(r.type, r.type), "state": r.label, "clarity": _clarity(frame, res.settings, t),
        "direction": r.direction, "labels": _labels(r),
        "points": [{"time": _iso(frame.index[b]), "price": p} for b, p in zip(bars, points)],
        "running": True, "kind": kind,
        "checks": [{"rule": c.rule, "words": c.words, "status": c.status, "kind": c.kind} for c in checks],
        "lines": lines,
        "thrust": ({"price": thrust, "from_time": _iso(frame.index[bars[-1]]), "words": THRUST_WORDS} if math.isfinite(thrust) else None),
        "note": fib_ew.COUNT_NOTE,
    }


def layer_of(frame: Any, settings: fib_ew.FibEwSettings, max_bars: int = MAX_BARS) -> dict[str, Any]:
    """The Fibonacci tool, clusters, count and marks of the newest `max_bars` bars of a closed-bar frame (pure; the
    route's core)."""
    res = fib_ew.evaluate(frame, settings)
    n = res.n
    if n == 0:
        return {"available": False, "reason": NO_BARS, "marks": [], "fib": [], "clusters": [], "count": None, "bars": 0}
    first = max(0, n - max_bars)
    marks = [{"time": _iso(frame.index[e.bar]), "origin_time": _iso(frame.index[e.origin]), "name": e.name, "side": e.side,
              "short": e.label, "label": e.title, "text": e.why} for e in res.events if e.bar >= first]
    known = [p for p in res.pivots if p.confirmed_idx is not None and int(p.confirmed_idx) <= n - 1]
    fib: list[dict[str, Any]] = []
    if len(known) >= 2:
        start = known[-3] if len(known) >= 3 else known[-2]
        for lv in fib_ew.fib_tool(known, settings):
            fib.append({**lv, "from_time": _iso(frame.index[int(start.bar_idx)]), "anchor_time": _iso(frame.index[int(known[-1].bar_idx)])})
    clusters: list[dict[str, Any]] = []
    levels, direction = fib_ew.cluster_levels(known, settings)
    atr = float(res.atr[-1]) if res.atr is not None and len(res.atr) else float("nan")
    if levels and (settings.band_mode == "pct" or (math.isfinite(atr) and atr > 0)):
        for c in fib_ew.qualifying(levels, settings, atr if settings.band_mode == "atr" else None):
            clusters.append({"low": c.low, "high": c.high, "count": c.count, "labels": list(c.labels), "sources": list(c.sources),
                             "side": fib_ew.UP if direction < 0 else fib_ew.DOWN,
                             "from_time": _iso(frame.index[int(known[-1].bar_idx)])})
    return {"available": True, "reason": None, "marks": marks, "fib": fib, "clusters": clusters, "count": count_of(frame, res),
            "bars": n, "as_of": _iso(frame.index[-1])}


@router.get("/fib-ew", dependencies=[Depends(rate_limited)])
async def fib_ew_layer(symbol: str = Query(..., min_length=1, max_length=60), exchange: str = Query("NSE", max_length=10),
                       timeframe: str = Query("1h", max_length=4),
                       user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=422, detail=f"unknown timeframe {timeframe}; use {', '.join(TIMEFRAMES)}")
    from app.screener import parse
    from app.screener import routes as R
    from app.screener.runtime import closed_only
    from app.screener.validator import validate
    s = fib_ew.FibEwSettings()
    ast = parse(screen_text())
    validated = validate(ast, base_tf=timeframe, params={})
    sym = symbol.strip().upper()
    head = {"schema": SCHEMA, "symbol": sym, "exchange": exchange, "base_tf": timeframe, "note": NOTE,
            "settings": {name: getattr(s, name) for name in fib_ew.SETTING_NAMES}, "settings_note": fib_ew.REFERENCE_LABEL}
    store = await R.store_first(session, user.tenant_id)
    universe, problems, data_source, _ = await R.load_universe(session, user, ast, validated, {}, [sym], exchange, timeframe, {},
                                                               release=True, budget={}, store=store, pending=[])
    await release_connection(session)
    data = next((d for d in universe if d.symbol == sym), None)
    if data is None or data.frames.get(timeframe) is None or not len(data.frames[timeframe]):
        why = problems.get(sym)
        return {**head, "available": False, "reason": str(why) if why else NO_BARS, "marks": [], "fib": [], "clusters": [],
                "count": None, "data_source": data_source}
    frame = closed_only(data.frames[timeframe], timeframe, datetime.now(UTC), exchange)      # closed bars only
    got = await asyncio.to_thread(layer_of, frame, s)
    return {**head, **got, "data_source": data_source}


__all__ = ["router", "FLAG", "SCHEMA", "MAX_BARS", "TIMEFRAMES", "NOTE", "THRUST_WORDS", "layer_of", "count_of", "screen_text"]
