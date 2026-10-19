"""N4 CH6: ScreenQL studies on charts - computed server-side by the screener's own runtime (`app.screener.runtime`,
the one computation library behind screens, alert rules and the screener backtest), as of a time, from the shared
lake bars the price-action layer reads.

GET /api/charts/studies?instrument=NSE:BANKNIFTY&tf=15m&at=<ISO time>&start=<ISO time>&expr=SMA(close, 20)&expr=RSI(14)
  * Up to MAX_STUDIES `expr` per request, each a ScreenQL expression that is a number (drawn as a line) or a condition
    (drawn as marks where it turns true). Checked like a screen (types, units, timeframes, look-ahead, cost) plus: no
    cross-sectional function, no last-bar-only function (`validator.validate_study`). A study that fails says why; the
    others are still drawn.
  * As of `at`: only bars whose END is <= `at` are read (CH3 loader: shared-licensed sources, preferred source then
    version, corrections point-in-time, row caps). The window is (`start`, `at`]; the history before `start` that the
    studies need to warm up (their look-back) is read too, so the first drawn value is already a full one.
  * Every value is the runtime's own; `times` (bar starts, epoch s - the chart's candle times) once, each line study's
    `values` aligned to them, NaN -> null; a condition study's `fires` (the bars it turns true). `fingerprint` = SHA-256 of
    the computed part (canonical JSON): the same bars give the same bytes.
  * A price-unit study draws on the price pane (`pane: "price"`), anything else in its own pane.
  * Per study, also refused with the reason while the others still draw:
    - a function whose screener flag is off for the organisation (`gate`: the U6 breakout and market-zone flags);
    - a warm-up that cannot be read (the 1-minute rebuild cap, the row cap: `_load`);
    - a study over the compute budget ("too heavy for this window": `admit`, CALL_WEIGHT).
  * Computed inside the CH3b build semaphore, and cached for 30 s with the layer's byte-bounded cache. 60 requests
    per minute per user.
Behind the `chart_studies` flag (off by default -> 503). Reference only - nothing here places or proposes an order.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.charts import price_action_layer as PAL
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.platform.controls import require_flag

router = APIRouter(prefix="/api/charts", tags=["charts"])
FLAG = "chart_studies"
SCHEMA = "study/1"
NOTE = "Studies computed by the screener's own engine from past prices only - reference, not signals or recommendations."
MAX_STUDIES = 6
MAX_EXPR_CHARS = 300
STUDY_MAX_BARS = 50_000                # bars drawn per study (the chart's 50k-bar target), warm-up included
STUDY_MAX_DAYS = {"1m": 180, "3m": 365, "5m": 730, "15m": 1825, "30m": 3650, "1h": 3650, "1d": 36500}
MAX_WARMUP_BARS = 2_000                # history read before `start` for the studies' look-back (at most)
rate_limited = user_rate_limit("chart_studies", limit=60, window_seconds=60)
# Compute budget (CH6a review HIGH). A request is estimated as the sum, over its studies' function calls, of each call's
# weight x the bars read (warm-up included). Weights are ms per 1,000 bars, about twice what was measured for the
# functions that run a per-bar Python loop (swing / structure / zone and the breakout engines). Every other function
# is vectorised and weighs DEFAULT_WEIGHT. Studies are admitted in order while the request stays within REQUEST_BUDGET.
# A study with a function of HEAVY_WEIGHT or more is refused outright above HEAVY_MAX_BARS bars. A refused study says
# "too heavy for this window" and the rest still draw.
CALL_WEIGHT: Dict[str, float] = {
    "SwingZoneStrength": 250.0, "SwingZoneDistance": 250.0, "Trend": 150.0, "StructureEvent": 130.0, "ReversalAt": 130.0,
    "NearSupport": 100.0, "NearResistance": 100.0, "RealBreak": 60.0, "DowTrend": 40.0, "DayExtremeBreak": 40.0,
    "SwingBreakType": 40.0, "NRBreakout": 30.0, "SwingBreakout": 30.0, "RangeBreakout": 30.0, "VolumeBreakout": 25.0,
    "SwingHigh": 20.0, "SwingLow": 20.0, "SwingDirection": 20.0, "DayLevelBreak": 15.0, "RangeExpansion": 15.0,
    "ORBreakout": 10.0, "SessionVWAP": 10.0, "ATRPercentile": 10.0, "RelVolumeTOD": 10.0, "VWAP": 10.0, "Supertrend": 5.0,
}
DEFAULT_WEIGHT = 1.0
HEAVY_WEIGHT = 50.0
HEAVY_MAX_BARS = 20_000
REQUEST_BUDGET = 8_000.0               # estimated ms of computation per request (about 4 s measured)
STUDY_CACHE_TAG = "studies"            # answers share the CH3 layer's byte-bounded 30 s cache under this tag


def canonical(content: Dict[str, Any]) -> str:
    return json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(content: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical(content).encode()).hexdigest()


def _pane(unit: Optional[str], vals: np.ndarray, closes: np.ndarray) -> str:
    """A price-unit line that lives where the price is (a moving average, a band, a level) shares the price pane; a
    price-unit distance (ATR, a range) or any other unit gets its own pane: the median |value / close - 1| < 25 %."""
    if unit != "price":
        return "own"
    ok = np.isfinite(vals) & np.isfinite(closes) & (closes != 0)
    return "price" if ok.any() and float(np.median(np.abs(vals[ok] / closes[ok] - 1.0))) < 0.25 else "own"


def _json_values(arr: np.ndarray) -> List[Optional[float]]:
    """Floats for JSON, NaN / inf -> None (vectorised: the 50k-bar target)."""
    out: List[Optional[float]] = arr.tolist()
    for i in np.flatnonzero(~np.isfinite(arr)).tolist():
        out[i] = None
    return out


def _runtime_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """CH3 loader frame -> the screener runtime's frame: index = bar start (UTC), open / high / low / close / volume."""
    df = frame.reset_index(drop=True)
    out = pd.DataFrame({k: df[k].astype(float) for k in ("open", "high", "low", "close")})
    out["volume"] = df["volume"].astype(float) if "volume" in df.columns else 0.0
    out.index = pd.DatetimeIndex(pd.to_datetime(df["timestamp"], utc=True), name="timestamp")
    return out


def check(exprs: Sequence[str], tf: str, functions: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Each expression parsed and checked as a study: [{expr, ok, problems, kind, unit, lookback, ast}]. `functions`: the
    registry view to check against (AMW-3 review: the flag-off view while `screenql_amw_functions` is off)."""
    from app.screener import nodes
    from app.screener.parser import ScreenQLSyntaxError, parse
    from app.screener.routes import missing_data
    from app.screener.validator import validate_study
    out: List[Dict[str, Any]] = []
    for expr in exprs:
        row: Dict[str, Any] = {"expr": expr, "ok": False, "problems": [], "kind": None, "unit": None, "lookback": 0, "lookback_by_tf": {},
                               "ast": None, "text": expr}
        try:
            ast = parse(expr)
        except ScreenQLSyntaxError as exc:
            row["problems"] = [{"message": exc.message, "pos": exc.pos}]
            out.append(row)
            continue
        except ValueError as exc:
            row["problems"] = [{"message": str(exc), "pos": None}]
            out.append(row)
            continue
        sc = validate_study(ast, base_tf=tf, functions=functions)
        problems = [p.as_dict() for p in sc.validated.problems]
        problems += [{"message": f"{m}() needs data a chart study does not load (sector / index / F&O lists or the option chain)", "pos": None}
                     for m in missing_data(ast)]
        row.update(ok=not problems, problems=problems, kind=sc.kind, unit=sc.unit, ast=ast, text=nodes.to_text(ast),
                   lookback=max([int(v) for v in sc.validated.lookback.values()] or [0]),
                   lookback_by_tf={k: int(v) for k, v in sc.validated.lookback.items()})
        out.append(row)
    return out


def weight(ast: Any) -> float:
    """ms per 1,000 bars of one study: its function calls' weights summed (a nested call counts on its own)."""
    from app.screener import nodes
    return sum(CALL_WEIGHT.get(node.name, DEFAULT_WEIGHT) for node in nodes.walk(ast) if isinstance(node, nodes.Call))


def heavy_calls(ast: Any) -> List[str]:
    from app.screener import nodes
    out: List[str] = []
    for node in nodes.walk(ast):
        if isinstance(node, nodes.Call) and CALL_WEIGHT.get(node.name, DEFAULT_WEIGHT) >= HEAVY_WEIGHT and node.name not in out:
            out.append(node.name)
    return out


def _refuse(st: Dict[str, Any], message: str) -> Dict[str, Any]:
    return {**st, "ok": False, "problems": [*st["problems"], {"message": message, "pos": None}]}


def admit(studies: Sequence[Dict[str, Any]], bars: int) -> List[Dict[str, Any]]:
    """The compute budget over `bars` bars (see CALL_WEIGHT): each checked study is admitted in order, or refused with
    "too heavy for this window" and why. The others still draw."""
    out: List[Dict[str, Any]] = []
    spent = 0.0
    for st in studies:
        if not st["ok"] or st["ast"] is None:
            out.append(st)
            continue
        heavy = heavy_calls(st["ast"])
        est = weight(st["ast"]) * bars / 1000.0
        if heavy and bars > HEAVY_MAX_BARS:
            out.append(_refuse(st, f"too heavy for this window: {', '.join(h + '()' for h in heavy)} is computed bar by bar, on at most "
                                   f"{HEAVY_MAX_BARS:,} bars ({bars:,} here, warm-up included); choose a later `start`"))
        elif spent + est > REQUEST_BUDGET:
            out.append(_refuse(st, f"too heavy for this window: with the studies before it, this request would need about {(spent + est) / 1000:.1f} s "
                                   f"of computation (the budget is {REQUEST_BUDGET / 1000:.0f} s); choose a later `start` or fewer studies"))
        else:
            spent += est
            out.append(st)
    return out


async def gate(session: AsyncSession, tenant_id: Any, studies: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The screener's function flags, per study (as `screener.routes._gate` does for a screen): a study using a U6
    breakout function while `screener_breakouts` is off, a market-zone function while `market_zones` is off, or an AMW-3
    function / v2 candle / confirmation status while `screenql_amw_functions` is off, is refused with the flag's reason.
    The others still draw."""
    from app.screener import amw_functions as amw_gate
    from app.screener import zones as zone_gate
    from app.screener.breakouts import gate as breakout_gate
    out: List[Dict[str, Any]] = []
    for st in studies:
        if st["ok"] and st["ast"] is not None:
            for g in (breakout_gate, zone_gate):
                blocked = await g.blocked_calls(session, tenant_id, st["ast"])
                if blocked:
                    st = _refuse(st, g.refusal(blocked))
            amw_blocked = await amw_gate.blocked_calls(session, tenant_id, st["ast"], {})   # studies take no $parameters
            if amw_blocked:
                st = _refuse(st, amw_gate.refusal(amw_blocked))
        out.append(st)
    return out


def _need(studies: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    need: Dict[str, int] = {}
    for st in studies:
        for tf_i, bars in (st.get("lookback_by_tf") or {}).items() if st["ok"] else ():
            need[tf_i] = max(need.get(tf_i, 0), bars)
    return need


async def _load(session: AsyncSession, key: str, tf: str, begin: datetime, when: datetime,
                studies: List[Dict[str, Any]]) -> Tuple[pd.DataFrame, Optional[str], datetime, List[Dict[str, Any]]]:
    """The bars for the window plus the warm-up the studies need. When that read is refused (the 1-minute rebuild cap,
    the row cap), the study whose own warm-up reaches furthest back is refused with the reason, and the read is tried
    again without it. Only a window that is too large on its own refuses the request (422)."""
    while True:
        read_from = warmup_start(begin, _need(studies), key)
        try:
            frame, source = await PAL.load_frame(session, key, tf, read_from, when, max_bars=STUDY_MAX_BARS + MAX_WARMUP_BARS)
            return frame, source, read_from, studies
        except PAL.WindowTooLarge as exc:
            starts = [warmup_start(begin, st.get("lookback_by_tf") or {}, key) for st in studies]
            earliest = min([t for t, st in zip(starts, studies) if st["ok"]] or [begin])
            if earliest >= begin:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            studies = [_refuse(st, f"its warm-up needs history from {earliest.date().isoformat()}, which cannot be read here: {exc}")
                       if st["ok"] and t == earliest else st for t, st in zip(starts, studies)]


def compute(frame: pd.DataFrame, tf: str, studies: Sequence[Dict[str, Any]], begin: Optional[datetime], exchange: str = "NSE") -> Dict[str, Any]:
    """The checked studies over the closed bars (CH3 loader columns; nothing after `at`), the points from `begin` on.
    A pure function of the bars it is given - the caller cuts them at `at`."""
    from app.screener.runtime import ScreenRuntimeError, SymbolData, evaluate
    df = _runtime_frame(frame) if len(frame) else pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    times: List[int] = (pd.DatetimeIndex(df.index).asi8 // 10**9).tolist() if len(df) else []
    first = 0
    if begin is not None and len(df):
        ends = pd.DatetimeIndex(pd.to_datetime(frame["bar_end"], utc=True))
        b = pd.Timestamp(begin)
        first = int(ends.searchsorted(b.tz_localize("UTC") if b.tzinfo is None else b.tz_convert("UTC"), side="right"))
    shown = times[first:]
    out: Dict[str, Any] = {"schema": SCHEMA, "tf": tf, "bars": len(shown), "warmup_bars": first, "times": shown,
                           "as_of": None if not len(frame) else pd.Timestamp(frame["bar_end"].iloc[-1]).tz_convert("UTC").isoformat(),
                           "first_time": shown[0] if shown else None, "last_time": shown[-1] if shown else None, "studies": []}
    data = SymbolData("study", {tf: df}, exchange=exchange) if len(df) else None
    for i, st in enumerate(studies):
        row: Dict[str, Any] = {"id": i, "expr": st["expr"], "text": st["text"], "ok": st["ok"], "problems": list(st["problems"]), "kind": st["kind"],
                               "unit": st["unit"], "pane": "price" if st["unit"] == "price" else "own", "lookback": st["lookback"]}
        if st["ok"] and data is not None:
            try:
                value = evaluate(st["ast"], data, base_tf=tf)
            except ScreenRuntimeError as exc:
                row.update(ok=False, problems=[{"message": str(exc), "pos": None}])
                value = None
            if row["ok"] and not isinstance(value, pd.Series):
                row.update(ok=False, problems=[{"message": "the expression has one value, not a series - nothing to draw", "pos": None}])
            elif row["ok"]:
                series = value.reindex(df.index)
                if st["kind"] == "bool":
                    raw = series.to_numpy()
                    flags = [None if v is None or (isinstance(v, float) and math.isnan(v)) else bool(v) for v in raw]
                    # a fire = the bar the condition turns true (false or unknown on the bar before)
                    fires = [times[k] for k in range(first, len(flags)) if flags[k] and not (k and flags[k - 1])]
                    row.update(fires=fires, true_bars=sum(1 for f in flags[first:] if f), last=flags[-1] if flags else None)
                else:
                    arr = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)[first:]
                    vals = _json_values(arr)
                    last = vals[-1] if vals else None
                    row.update(values=vals, last=last, last_text=None if last is None else f"{last:.2f}",
                               pane=_pane(st["unit"], arr, df["close"].to_numpy(dtype=float)[first:]))
        out["studies"].append(row)
    return out


def build(frame: pd.DataFrame, tf: str, studies: Sequence[Dict[str, Any]], begin: Optional[datetime], exchange: str = "NSE") -> Dict[str, Any]:
    content = compute(frame, tf, studies, begin, exchange)
    content["fingerprint"] = fingerprint(content)
    return content


def warmup_start(begin: datetime, lookback: Dict[str, int], key: str) -> datetime:
    """The read start that holds each timeframe's look-back (bars of that timeframe, `@1d` in a 15m study included)
    before `begin` - weekends and the session included, at most MAX_WARMUP_BARS bars of any one timeframe."""
    from app.price_action.zones import job
    sess = job.exchange_session(key)
    def days_for(tf_i: str, bars: int) -> int:
        bars = min(int(bars), MAX_WARMUP_BARS)
        return bars * 31 + 31 if tf_i == "1M" else job.warmup_days(tf_i, bars, sess)
    days = max([days_for(tf_i, bars) for tf_i, bars in lookback.items() if bars > 0] or [0])
    return begin - timedelta(days=days)


@router.get("/studies", dependencies=[Depends(rate_limited)])
async def chart_studies(instrument: str = Query(..., min_length=1, max_length=100), tf: PAL.Timeframe = "15m",
                        at: Optional[datetime] = None, start: Optional[datetime] = None,
                        expr: List[str] = Query(..., min_length=1, max_length=MAX_STUDIES),
                        user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    owner, tenant_id = user.id, user.tenant_id         # read now: the session is closed before the build
    from app.price_action.zones.store import normalise_key
    if any(len(e) > MAX_EXPR_CHARS or not e.strip() for e in expr):
        raise HTTPException(status_code=422, detail=f"each study is 1-{MAX_EXPR_CHARS} characters")
    now = datetime.now(timezone.utc)
    at_utc, start_utc = PAL._aware(at, "at"), PAL._aware(start, "start")
    when = now.replace(second=0, microsecond=0) if at_utc is None or at_utc > now else at_utc
    begin = start_utc if start_utc is not None else when - timedelta(days=PAL.DEFAULT_DAYS[tf])
    if begin >= when:
        raise HTTPException(status_code=422, detail="`start` must be before `at`")
    if when - begin > timedelta(days=STUDY_MAX_DAYS[tf]):
        raise HTTPException(status_code=422, detail=f"the window may span at most {STUDY_MAX_DAYS[tf]} days on {tf}; choose a later `start`")
    from app.screener import amw_functions as amw_gate
    flag_off_view = None if await amw_gate.enabled(session, tenant_id) else amw_gate.FLAG_OFF_FUNCTIONS
    studies = check([e.strip() for e in expr], tf, flag_off_view)
    head = {"instrument": instrument, "at": when.isoformat(), "start": begin.isoformat(), "source": None, "note": NOTE}
    try:
        key: Optional[str] = normalise_key(instrument)
    except ValueError:
        key = None
    if key is None or key == "*":
        return {**head, **build(pd.DataFrame(columns=PAL.FRAME_COLS), tf, studies, begin), "reason": PAL.NO_BARS}
    studies = await gate(session, tenant_id, studies)
    # a short cache (the CH3 layer's, 30 s, byte-bounded): the answer is a function of the bars read (instrument,
    # timeframe, read start, window, `at`) and of which studies run - each study's refusal messages (an organisation's
    # flags) are in the key, word for word; the budget and the read refusals follow from the same bars
    cache_key = (STUDY_CACHE_TAG, key, tf, warmup_start(begin, _need(studies), key), begin, when,
                 tuple((st["text"], st["ok"], tuple(p["message"] for p in st["problems"])) for st in studies))
    hit = PAL._cached(cache_key)
    if hit is None:
        frame, source, _read_from, studies = await _load(session, key, tf, begin, when, studies)
        if len(frame) > STUDY_MAX_BARS + MAX_WARMUP_BARS:
            raise HTTPException(status_code=422, detail=f"the window holds {len(frame)} bars, more than {STUDY_MAX_BARS}; choose a later `start`")
        studies = admit(studies, len(frame))
        # every bar is read: the session goes back to the pool before the bounded wait for a build slot (CH6a re-review)
        content = await PAL._in_slot(session, owner, build, frame, tf, studies, begin, key.split(":", 1)[0])
        hit = {"source": source, **content, "reason": None if len(frame) else PAL.NO_BARS}
        PAL._remember(cache_key, hit, len(canonical(hit)))
    return {**head, "instrument": key, **hit}

__all__ = ["router", "FLAG", "check", "gate", "admit", "weight", "heavy_calls", "compute", "build", "canonical", "fingerprint"]
