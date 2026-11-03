"""AUD-T5 (owner audit 28 Oct, "Structure reading as a service"): "Read this chart" - the readers ATP already has, called
unchanged on the chart the user has open, as of its last CLOSED bar, and put into plain structure words.

GET /api/charts/read?instrument=NSE:BANKNIFTY&tf=15m

What is read (no new reading logic here - this module only calls the existing functions and words their output):
  * structure - `price_action_layer.compute_layer` (N4 CH3: confirmed swings, real breaks, the structure tracker's state,
    the protected level) on the chart's own closed bars;
  * areas     - `zones_layer.market_zones_layer` (Z7a-3: the organisation's stored zones as of the last closed bar, at most
    six near the price, each with its own timeframe - never merged) with the zone lifecycle state (fresh / tested /
    flipped; invalidated areas are not live and are not shown);
  * pullback  - the ported Daily trend fold (N1 L2, `screener.then.trend.daily_context` -> `level_gate.gate
    .context_from_state`): the Daily trend, its phase and the counter move (pullback / correction);
  * commitment candle - the ported level gate (N1 L1, `level_gate.gate.safe_evaluate`) replayed per area over the
    chart's closed bars since the areas were stored (an area is never read on bars before it was known): the newest
    commitment candle it marked. Only the candle, its form and its area are returned - the gate's own levels and ratios
    are never part of this answer.
No look-ahead: the window is bars whose END is at or before the request time (a forming bar is never read), and the
pure part `read_closed` cuts again at `when`. The Trade reader port (P5-1) is not in ATP yet: its DECISIONS 76-77
lifecycle words and its owner answer key are therefore not shown (see docs/ROADMAP.md P5-1).
Behind `ui_v2` + `read_chart` (404 while either is off). Self-scoped: the caller's organisation's zones and his own
private bars only. Read-only: nothing here places, changes or blocks an order.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional, Sequence

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.charts import price_action_layer as pal
from app.charts import zones_layer as zl
from app.core import config
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.price_action.level_gate import gate as G

router = APIRouter(prefix="/api/charts", tags=["charts"])
FLAG = "read_chart"
rate_limited = user_rate_limit("read_chart", limit=config.READ_CHART_PER_MINUTE, window_seconds=60)
TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "1d")
NOTE = "A reading of past prices in structure words - reference, not advice."
NO_BARS = "No closed bars for this chart yet."

TF_WORDS = {"1m": "1-minute", "3m": "3-minute", "5m": "5-minute", "15m": "15-minute", "30m": "30-minute", "1h": "1-hour",
            "60m": "1-hour", "1d": "daily", "day": "daily", "1w": "weekly", "week": "weekly"}
DIRECTION_WORDS = {"up": "up", "up (weak)": "up, weakening", "down": "down", "down (weak)": "down, weakening", "range": "a range",
                   "none": "not formed yet"}
EVENT_WORDS = {"BOS": "a break of structure", "CHOCH": "a change of character", "REVERSAL_CONFIRMED": "a confirmed reversal",
               "RECOVERY": "a recovery", "RANGE_START": "a range forming", "RANGE_EXIT_UP": "a move out of the range",
               "RANGE_EXIT_DOWN": "a move out of the range", "INIT_TREND": "the first trend", "INIT_RANGE": "the first range"}
STATE_WORDS = {"fresh": "untouched since it formed", "tested": "price has come back to it",
               "flipped": "price closed through it and held beyond; it now acts on the other side"}
TREND_WORDS = {"UP": "up", "DOWN": "down", "RANGE": "a range", "NEUTRAL": "not clear", "UNKNOWN": "not clear"}


def _words_of(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------------------------------------------------------------
# Pure part: closed bars + the stored areas + the Daily context -> the reading
# ---------------------------------------------------------------------------------------------------------------------
def closed_only(frame: pd.DataFrame, when: datetime) -> pd.DataFrame:
    """Only bars whose END is at or before `when` - a forming bar is never read."""
    if not len(frame):
        return frame
    keep = pd.to_datetime(frame["bar_end"], utc=True) <= pd.Timestamp(when)
    return frame[keep.to_numpy()].reset_index(drop=True)


def gate_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """The chart frame in the gate's columns (timestamp = bar start in IST wall time, as `gate.closed_bars` gives)."""
    ts = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    out = pd.DataFrame({"timestamp": ts.to_numpy()})
    for col in ("open", "high", "low", "close"):
        out[col] = pd.to_numeric(frame[col], errors="coerce").to_numpy(dtype=float)
    if "volume" in frame.columns:
        out["volume"] = pd.to_numeric(frame["volume"], errors="coerce").to_numpy(dtype=float)
    return out


def structure_part(layer: Mapping[str, Any], tf: str) -> Dict[str, Any]:
    state = layer.get("structure_state") or {}
    direction = str(state.get("direction") or "none")
    last = state.get("last_event") or None
    words = f"Structure on the {TF_WORDS.get(tf, tf)} chart: {DIRECTION_WORDS.get(direction, direction)}"
    if last:
        words += f"; the last event was {EVENT_WORDS.get(str(last.get('type')), 'a structure event')}"
        if last.get("direction") in ("up", "down"):
            words += f" {last['direction']}"
    prot = state.get("protected")
    side = "low" if direction.startswith("up") else "high" if direction.startswith("down") else None
    return {"direction": direction, "words": _words_of(words + "."),
            "last_event": None if not last else {"type": last.get("type"), "direction": last.get("direction"), "time": last.get("time"),
                                                 "words": EVENT_WORDS.get(str(last.get("type")), "a structure event")},
            "protected": None if prot is None or side is None else {"price": prot, "side": side,
                                                                    "words": f"protected swing {side} of this structure"}}


def area_part(band: Mapping[str, Any]) -> Dict[str, Any]:
    role = str(band.get("role") or "area")
    state = str(band.get("state") or "")
    tf = str(band.get("timeframe") or "")
    touches = int(band.get("touches") or 0)
    words = (f"{role.capitalize()} area {band.get('low_text')} - {band.get('high_text')} from the {TF_WORDS.get(tf, tf)} chart: "
             f"{STATE_WORDS.get(state, state)}" + ("" if touches <= 0 else f" ({band.get('touch_text') or touches})") + ".")
    return {"key": band.get("zone_key"), "role": role, "low": band.get("low"), "high": band.get("high"), "low_text": band.get("low_text"),
            "high_text": band.get("high_text"), "timeframe": tf, "state": state, "state_words": STATE_WORDS.get(state, state),
            "touches": touches, "words": _words_of(words)}


def pullback_part(ctx: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The Daily trend, its phase and the counter move, as the ported fold states them (known at the previous close)."""
    if not ctx or ctx.get("pending") or str(ctx.get("trend") or "UNKNOWN") == "UNKNOWN":
        return {"available": False, "trend": None, "phase": None, "counter": None, "as_of": None,
                "words": "The Daily trend is not available for this symbol yet."}
    trend, phase, counter = str(ctx.get("trend")), ctx.get("phase"), ctx.get("counter")
    words = f"Daily trend: {TREND_WORDS.get(trend, trend.lower())}"
    if trend in ("UP", "DOWN"):
        if phase == "origin_broken":
            words += "; a close went through the origin of the trend"
        elif counter == "correction":
            words += "; a larger correction is running"
        elif counter == "candidate":
            words += "; a correction of the last leg is running"
        elif counter == "pullback":
            words += "; a pullback inside the running leg"
        elif phase == "impulse":
            words += "; the leg is running, no pullback yet"
    return {"available": True, "trend": trend, "phase": phase, "counter": counter, "as_of": ctx.get("asof"), "words": _words_of(words + ".")}


def _start_after(frame: pd.DataFrame, known: Optional[datetime]) -> int:
    """The first bar that closed after the areas were known (an area is never read on bars before it existed)."""
    if known is None or not len(frame):
        return len(frame)
    ends = pd.to_datetime(frame["bar_end"], utc=True)
    later = (ends > pd.Timestamp(known)).to_numpy().nonzero()[0]
    return int(later[0]) if len(later) else len(frame)


def commitment_part(frame: pd.DataFrame, areas: Sequence[Mapping[str, Any]], ctx: Optional[Mapping[str, Any]],
                    areas_known: Optional[datetime]) -> Optional[Dict[str, Any]]:
    """The newest commitment candle the ported level gate marks at one of the areas, replayed over the closed bars since the
    areas were stored (`gate.safe_evaluate`, unchanged). None when it marks none."""
    if not len(frame) or not areas or not ctx:
        return None
    start = _start_after(frame, areas_known)
    if start >= len(frame):
        return None
    bars = gate_bars(frame)
    bounds = [(float(a["low"]), float(a["high"])) for a in areas]
    best: Optional[Dict[str, Any]] = None
    for i in range(len(bounds)):
        found = _area_commitment(frame, bars, areas, bounds, i, ctx, start)
        if found is not None and (best is None or found["bar"] > best["bar"]):
            best = found
    if best is not None:
        best.pop("bar")
    return best


def _area_commitment(frame: pd.DataFrame, bars: pd.DataFrame, areas: Sequence[Mapping[str, Any]], bounds: Sequence[tuple[float, float]],
                     i: int, ctx: Mapping[str, Any], start: int) -> Optional[Dict[str, Any]]:
    """The commitment candle the ported level gate marks at area `i` (the other areas as its neighbours), or None."""
    area = bounds[i]
    others = [b for j, b in enumerate(bounds) if j != i]
    r = G.safe_evaluate(bars, area, ctx, others, start)
    at = r.get("confirm_bar")
    if at is None or not (start <= int(at) < len(frame)):
        return None
    side = r.get("side")
    role = "demand" if side == G.BULL else "supply"
    form = G.form_name(r.get("form"), side) or "commitment"
    return {"bar": int(at), "time": pal._epoch(frame["timestamp"].iloc[int(at)]), "bar_end": pal._iso(frame["bar_end"].iloc[int(at)]),
            "role": role, "form": form, "area": {"low": area[0], "high": area[1], "key": areas[i].get("key")},
            "words": _words_of(f"Commitment candle ({form}) at the {role} area {areas[i].get('low_text')} - {areas[i].get('high_text')}.")}


def area_commitment(frame: pd.DataFrame, areas: Sequence[Mapping[str, Any]], index: int, ctx: Optional[Mapping[str, Any]],
                    known: Optional[datetime]) -> Optional[Dict[str, Any]]:
    """M75 W-9: the commitment candle the ported level gate (unchanged) marks at ONE area - `areas[index]`, the others as
    its neighbours, exactly as `commitment_part` reads each area - replayed over the closed bars that ended after `known`
    (a trigger plan's armed time: never a candle from before). It carries `bar` (the frame row) and the candle's `low`,
    `high` and `close`. None when the gate marks none there (or there is no Daily context)."""
    if not len(frame) or not ctx or not (0 <= index < len(areas)):
        return None
    start = _start_after(frame, known)
    if start >= len(frame):
        return None
    bounds = [(float(a["low"]), float(a["high"])) for a in areas]
    found = _area_commitment(frame, gate_bars(frame), areas, bounds, index, ctx, start)
    if found is not None:
        row = frame.iloc[found["bar"]]
        found.update(low=float(row["low"]), high=float(row["high"]), close=float(row["close"]))
    return found


def read_closed(frame: pd.DataFrame, tf: str, when: datetime, *, bands: Sequence[Mapping[str, Any]] = (),
                areas_known: Optional[datetime] = None, ctx: Optional[Mapping[str, Any]] = None,
                mtf: Sequence[Mapping[str, Any]] | None = None) -> Dict[str, Any]:
    """The reading of `frame` (closed bars, ascending) as of `when`: structure, areas, pullback state, commitment candle
    and the plain lines. Pure: the caller's bars, the stored areas and the Daily context are its only inputs.
    M75 W-3: `mtf` (the top-down rows as of the same bar; given while `copilot_workspace` is on) adds `annotations` -
    ATP's annotation set for the Copilot's chart workspace (app/charts/workspace_reading.py)."""
    frame = closed_only(frame.reset_index(drop=True), when)
    n = len(frame)
    out: Dict[str, Any] = {"tf": tf, "bars": n, "as_of": pal._iso(frame["bar_end"].iloc[-1]) if n else None,
                           "last_bar": pal._epoch(frame["timestamp"].iloc[-1]) if n else None,
                           "structure": None, "areas": [], "pullback": pullback_part(ctx), "commitment": None, "lines": []}
    if n == 0:
        out["lines"] = [NO_BARS]
        return out
    layer = pal.compute_layer(frame, tf)
    out["structure"] = structure_part(layer, tf)
    out["areas"] = [area_part(b) for b in bands]
    out["commitment"] = commitment_part(frame, out["areas"], ctx, areas_known)
    lines = [out["structure"]["words"], out["pullback"]["words"]]
    lines += [a["words"] for a in out["areas"]] if out["areas"] else ["No stored areas near the price on this chart."]
    lines.append(out["commitment"]["words"] if out["commitment"] else "No commitment candle at an area since the areas were stored.")
    out["lines"] = lines
    if mtf is not None:
        from app.charts import workspace_reading as WR
        out["annotations"] = WR.atp_annotations(frame, layer, tf, out["areas"], mtf)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------------------------------------------------
async def _flags(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    for name in ("ui_v2", FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


async def _areas(session: AsyncSession, user: User, key: str, at: datetime, price: Optional[float]) -> Dict[str, Any]:
    """The stored areas as of the last closed bar through the existing market-zones layer (its scope rule unchanged)."""
    try:
        return await zl.market_zones_layer(instrument=key, at=at, price=price, family="all", max_bands=zl.chart.MAX_BANDS,
                                           user=user, session=session)
    except HTTPException:
        return {"bands": [], "as_of": None, "reason": "Areas are not available on this platform yet."}


async def _context(session: AsyncSession, key: str, last_end: datetime) -> Optional[Dict[str, Any]]:
    from app.market_data.calendar import IST
    from app.screener.then import trend
    try:
        return await trend.daily_context(session, key, last_end.astimezone(IST).date())
    except Exception:  # noqa: BLE001 - the reading goes on without the Daily context, and says so
        return None


async def workspace_annotations(session: AsyncSession, user: User, key: str, tf: str, now: datetime) -> dict[str, Any] | None:
    """M76 MP-3: the workspace's annotations for `key` on `tf` as of the last closed bar - the same pieces the route below
    reads (the signal engine's when it reads this chart for him, else ATP's readers on the same bars, stored areas and
    top-down rows), for the ticket's check card. None when there are no closed bars or the reading cannot be built now
    (busy, no data): the card then has no structure line. Read-only."""
    from app.charts import workspace_reading as WR
    from app.market_lake import own_bars
    if tf not in pal.DEFAULT_DAYS:
        return None
    private = await own_bars.scope_for(session, user, key)
    try:
        frame, _source = await pal.load_frame(session, key, tf, now - timedelta(days=pal.DEFAULT_DAYS[tf]), now, private=private)
    except pal.WindowTooLarge:
        return None
    frame = closed_only(frame, now)
    if not len(frame):
        return None
    engine_part = await WR.engine_reading(session, user, key, tf, now)
    if engine_part is not None:
        return engine_part
    last_end = pd.Timestamp(frame["bar_end"].iloc[-1]).to_pydatetime()
    last_end = last_end if last_end.tzinfo else last_end.replace(tzinfo=timezone.utc)
    zones = await _areas(session, user, key, last_end, float(frame["close"].iloc[-1]))
    mtf = await pal.mtf_rows(session, key, last_end, owner=user.id, private=private)
    bands = zones.get("bands") or []

    def build() -> dict[str, Any]:
        return WR.atp_annotations(frame, pal.compute_layer(frame, tf), tf, [area_part(b) for b in bands], mtf)
    try:
        result: dict[str, Any] = await pal._in_slot(session, user.id, build)  # noqa: SLF001 - the route's own build slot
    except HTTPException:
        return None                                       # busy: no structure line now; Place never waits for it
    return result


@router.get("/read", dependencies=[Depends(rate_limited)])
async def read_chart(instrument: str = Query(..., min_length=1, max_length=100), tf: pal.Timeframe = "15m",
                     user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The reading of the user's open chart as of its last closed bar (see the module docstring)."""
    await _flags(session, user)
    from app.market_lake import own_bars
    from app.price_action.zones.store import normalise_key
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    head: Dict[str, Any] = {"instrument": instrument, "note": NOTE, "answer_key": None}
    try:
        key: Optional[str] = normalise_key(instrument)
    except ValueError:
        key = None
    if key is None or key == "*":
        return {**head, **read_closed(pd.DataFrame(columns=pal.FRAME_COLS), tf, now)}
    head["instrument"] = key
    private = await own_bars.scope_for(session, user, key)          # DATA-H: his own private bars, only his
    try:                                                            # owner 3 Nov: and his provisional Daily bar
        frame, _source = await pal.load_frame(session, key, tf, now - timedelta(days=pal.DEFAULT_DAYS[tf]), now, private=private,
                                              provisional=True)
    except pal.WindowTooLarge as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    frame = closed_only(frame, now)
    if not len(frame):
        return {**head, **read_closed(frame, tf, now)}
    last_end = pd.Timestamp(frame["bar_end"].iloc[-1]).to_pydatetime()
    last_end = last_end if last_end.tzinfo else last_end.replace(tzinfo=timezone.utc)
    zones = await _areas(session, user, key, last_end, float(frame["close"].iloc[-1]))
    known = zones.get("as_of")
    known_at = datetime.fromisoformat(known) if isinstance(known, str) else None
    ctx = await _context(session, key, last_end)
    # M75 W-3: the workspace's annotations - the signal engine's when it reads this chart for him, else ATP's (never mixed)
    from app.charts import workspace_reading as WR
    from app.platform.controls import flag_enabled
    mtf: list[dict[str, Any]] | None = None
    engine_part: dict[str, Any] | None = None
    if await flag_enabled(session, WR.FLAG, user.tenant_id):
        engine_part = await WR.engine_reading(session, user, key, tf, now)
        if engine_part is None:
            mtf = await pal.mtf_rows(session, key, last_end, owner=user.id, private=private, provisional=True)
    content = await pal._in_slot(session, user.id, lambda: read_closed(frame, tf, now, bands=zones.get("bands") or [],
                                                                          areas_known=known_at, ctx=ctx, mtf=mtf))
    if engine_part is not None:
        content["annotations"] = engine_part
    return {**head, **content}


__all__ = ["router", "FLAG", "read_closed", "closed_only", "commitment_part", "area_commitment", "pullback_part", "structure_part", "area_part"]
