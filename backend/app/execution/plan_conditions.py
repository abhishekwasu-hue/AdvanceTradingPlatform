"""M75 W-9 (owner order M75 CW-6; DECISIONS 1 Nov: M75Q-4 default confirmed): the method conditions of a trigger plan.

Two conditions beside W-8's price level, both his own choice (kind, side, timeframe - nothing pre-set, no 15M preset):

* AREA - the price inside HIS chosen area (a buyer or a seller area of the chart's reading, its edges as he picked them)
  and a CLOSED candle of HIS timeframe with the chosen reader's rejection there: the engine's commitment reading (its
  signal on that area's side, on his timeframe) when the engine reads this chart for him, else ATP's ported level gate
  commitment candle (`read_chart.area_commitment`, unchanged; the other stored areas as its neighbours, exactly as the
  chart's reading reads each area). The candle must trade inside his area.
* STRUCTURE - a break of structure (BOS) or a change of character (CHoCH), up or down, on HIS timeframe: the engine's
  structure events when it reads this chart, else ATP's structure tracker (`price_action_layer.compute_layer`, unchanged).

ONE source per reading, the same as the workspace's (M75 W-3, `workspace_reading.engine_result`): the engine when flag
`trade_engine` is on and it reads this chart for him, else ATP's readers - never both, and never a fallback from one to
the other inside a reading. A source that does not give a part (an engine without structure events) answers "not given":
the plan waits, it is never read from the other source. Every hit says which source met it.

No look-ahead: only bars that CLOSED by now are read, and only a candle / event whose bar closed AFTER the plan was armed
counts (the armed-after rule of `trigger_rules.close_trigger`). The structure tracker is fed bar by bar and the gate is
replayed bar by bar, so a replay of the same closed bars one at a time (a backtest) meets the condition on the same bar.
Read-only: nothing here places, blocks or changes an order; the plan fires through `trigger_plans.fire` as before.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

PRICE, AREA, STRUCTURE = "PRICE", "AREA", "STRUCTURE"
KINDS = (PRICE, AREA, STRUCTURE)
METHOD_KINDS = (AREA, STRUCTURE)
AREA_SIDES = ("buyer", "seller")
EVENTS = ("BOS", "CHOCH")
DIRECTIONS = ("up", "down")
SOURCE_ATP, SOURCE_ENGINE = "atp", "engine"
SOURCE_WORDS = {SOURCE_ATP: "ATP's chart readers", SOURCE_ENGINE: "the signal engine"}
EVENT_NAMES = {"BOS": "break of structure", "CHOCH": "change of character"}
# his timeframe in minutes -> the chart reading's timeframe (the ones the reading is offered on, `read_chart.TIMEFRAMES`)
READ_TIMEFRAMES = {1: "1m", 3: "3m", 5: "5m", 15: "15m", 30: "30m", 60: "1h", 1440: "1d"}
TF_WORDS = {"1m": "1-minute", "3m": "3-minute", "5m": "5-minute", "15m": "15-minute", "30m": "30-minute", "1h": "1-hour", "1d": "daily"}
ROLE_OF_SIDE = {"buyer": "demand", "seller": "supply"}
RETRY_SECONDS = 60                 # no reading this pass (no bars, busy): look again after this (operational only)
NOT_GIVEN = "The chart's reading source does not give this part for this chart: the plan waits."


@dataclass(frozen=True)
class Hit:
    """The condition met: by which source, on the bar that closed at `at`, at that bar's close (None: not given)."""
    source: str
    at: datetime
    price: float | None
    words: str


@dataclass(frozen=True)
class Reading:
    """One pass: the source that read (None = no reading now), the hit if any, the last closed bar and its close."""
    source: str | None
    hit: Hit | None = None
    last_end: datetime | None = None
    last_close: float | None = None
    note: str | None = None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _ts(value: Any) -> datetime:
    t = pd.Timestamp(value)
    return (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).to_pydatetime()


def read_tf(minutes: int | None) -> str | None:
    return READ_TIMEFRAMES.get(int(minutes)) if minutes else None


def overlaps(low: float, high: float, a_low: float, a_high: float) -> bool:
    """A candle (or a band) trading inside the area: the two ranges share a price."""
    lo, hi = (a_low, a_high) if a_low <= a_high else (a_high, a_low)
    return low <= hi and high >= lo


def _area_words(c: Any) -> str:
    return f"the {c.area_side} area {c.area_low:g} - {c.area_high:g}"


# --- pure: ATP's readers --------------------------------------------------------------------------------------------

def atp_structure_hit(layer: Mapping[str, Any], frame: pd.DataFrame, c: Any, armed_at: datetime) -> Hit | None:
    """The first BOS / CHoCH of his kind and direction that ATP's structure tracker marks on a bar that closed after
    `armed_at` (each event sits on the bar that confirmed it; that bar's close time decides)."""
    tf = read_tf(c.timeframe_minutes) or ""
    ends = {int(pd.Timestamp(t).timestamp()): (_ts(e), float(cl))
            for t, e, cl in zip(pd.to_datetime(frame["timestamp"], utc=True), frame["bar_end"], frame["close"], strict=True)}
    since = _utc(armed_at)
    for e in layer.get("structure") or []:
        if e.get("type") != c.event or e.get("direction") != c.direction:
            continue
        bar = ends.get(int(e.get("time") or 0))
        if bar is None or bar[0] <= since:
            continue
        return Hit(SOURCE_ATP, bar[0], bar[1], f"Met by {SOURCE_WORDS[SOURCE_ATP]}: {EVENT_NAMES[c.event]} {c.direction} "
                                               f"on the {TF_WORDS.get(tf, tf)} chart.")
    return None


def atp_area_hit(frame: pd.DataFrame, areas: Sequence[Mapping[str, Any]], index: int, ctx: Mapping[str, Any] | None, c: Any,
                 armed_at: datetime) -> Hit | None:
    """ATP's ported level gate commitment candle at his area (`areas[index]`), on a bar that closed after `armed_at`, on
    his side, trading inside his area."""
    from app.charts.read_chart import area_commitment
    found = area_commitment(frame, areas, index, ctx, _utc(armed_at))
    if found is None or found.get("role") != ROLE_OF_SIDE.get(c.area_side):
        return None
    if not overlaps(float(found["low"]), float(found["high"]), float(c.area_low), float(c.area_high)):
        return None
    tf = read_tf(c.timeframe_minutes) or ""
    return Hit(SOURCE_ATP, _ts(found["bar_end"]), float(found["close"]),
               f"Met by {SOURCE_WORDS[SOURCE_ATP]}: commitment candle ({found.get('form') or 'commitment'}) at {_area_words(c)} "
               f"on the {TF_WORDS.get(tf, tf)} chart.")


def plan_areas(bands: Sequence[Mapping[str, Any]], c: Any) -> tuple[list[dict[str, Any]], int]:
    """The stored areas in the reading's form with HIS area among them (index): the stored area with his edges and side
    when it is still stored, else his edges added (his choice stays what he picked; the others are its neighbours)."""
    from app.charts.read_chart import area_part
    areas = [area_part(b) for b in bands]
    role = ROLE_OF_SIDE.get(c.area_side)
    for i, a in enumerate(areas):
        if a.get("role") == role and a.get("low") is not None and a.get("high") is not None \
                and abs(float(a["low"]) - float(c.area_low)) < 1e-6 and abs(float(a["high"]) - float(c.area_high)) < 1e-6:
            return areas, i
    mine = {"key": None, "role": role, "low": float(c.area_low), "high": float(c.area_high), "low_text": f"{c.area_low:g}",
            "high_text": f"{c.area_high:g}"}
    return [mine, *areas], 0


# --- pure: the signal engine ----------------------------------------------------------------------------------------

def engine_structure_hit(result: Any, c: Any, armed_at: datetime, last_close: float | None) -> Hit | None:
    from app.charts.workspace_reading import engine_tf
    tf = read_tf(c.timeframe_minutes) or ""
    since = _utc(armed_at)
    for e in sorted(getattr(result, "events", ()) or (), key=lambda e: e.at):
        if e.kind == c.event and e.direction == c.direction and engine_tf(str(e.timeframe)) == tf and _utc(e.at) > since:
            return Hit(SOURCE_ENGINE, _utc(e.at), last_close, f"Met by {SOURCE_WORDS[SOURCE_ENGINE]}: {EVENT_NAMES[c.event]} "
                                                            f"{c.direction} on the {TF_WORDS.get(tf, tf)} chart.")
    return None


def engine_area_hit(result: Any, c: Any, armed_at: datetime, last_close: float | None) -> Hit | None:
    """The engine's commitment reading at his area: a signal on his area's side and his timeframe, on a bar that closed
    after `armed_at`, whose area (or entry band) lies inside his area."""
    from app.charts.workspace_reading import engine_tf
    tf = read_tf(c.timeframe_minutes) or ""
    since = _utc(armed_at)
    bands = {a.area_id: a.band for a in getattr(result, "areas", ()) or ()}
    for s in sorted(getattr(result, "signals", ()) or (), key=lambda s: s.bar_end):
        if s.side != c.area_side or engine_tf(str(s.timeframe)) != tf or _utc(s.bar_end) <= since:
            continue
        band = bands.get(s.area_id) if s.area_id else None
        band = band or s.entry_zone
        if overlaps(float(band.low), float(band.high), float(c.area_low), float(c.area_high)):
            return Hit(SOURCE_ENGINE, _utc(s.bar_end), last_close,
                       f"Met by {SOURCE_WORDS[SOURCE_ENGINE]}: its commitment reading at {_area_words(c)} on the {TF_WORDS.get(tf, tf)} chart.")
    return None


def engine_gives(result: Any, kind: str) -> bool:
    """Whether the engine's result carries the part this condition reads (interface 1's optional parts may be empty)."""
    return bool(getattr(result, "events", ())) if kind == STRUCTURE else True


# --- the pass (worker) ----------------------------------------------------------------------------------------------

def instrument_key(symbol: str, exchange: str) -> str | None:
    from app.price_action.zones.store import normalise_key
    try:
        key = normalise_key(f"{exchange}:{symbol}")
    except ValueError:
        return None
    return None if key == "*" else key


async def read(session: Any, user: Any, symbol: str, exchange: str, c: Any, armed_at: datetime, now: datetime) -> Reading:
    """One reading of his method condition as of `now` (closed bars only), from ONE source (see the module text)."""
    from app.charts import price_action_layer as pal
    from app.charts import read_chart as RC
    from app.charts import workspace_reading as WR
    from app.market_lake import own_bars
    tf = read_tf(c.timeframe_minutes)
    key = instrument_key(symbol, exchange)
    if tf is None or key is None:
        return Reading(None, note="This chart cannot be read on that timeframe.")
    engine = await WR.engine_result(session, user, key, now)
    if engine is not None:
        result, _signal_tf, last, as_of = engine
        if not engine_gives(result, c.kind):
            return Reading(SOURCE_ENGINE, last_end=_ts(as_of), last_close=last, note=NOT_GIVEN)
        hit = engine_structure_hit(result, c, armed_at, last) if c.kind == STRUCTURE else engine_area_hit(result, c, armed_at, last)
        return Reading(SOURCE_ENGINE, hit=hit, last_end=_ts(as_of), last_close=last)
    private = await own_bars.scope_for(session, user, key)
    try:
        frame, _src = await pal.load_frame(session, key, tf, now - timedelta(days=pal.DEFAULT_DAYS[tf]), now, private=private)
    except pal.WindowTooLarge:
        return Reading(None, note="This chart cannot be read on that timeframe.")
    frame = RC.closed_only(frame, now)
    if not len(frame):
        return Reading(None, note=RC.NO_BARS)
    last_end = _ts(frame["bar_end"].iloc[-1])
    last_close = float(frame["close"].iloc[-1])
    if c.kind == STRUCTURE:
        layer = await asyncio.to_thread(pal.compute_layer, frame, tf)
        return Reading(SOURCE_ATP, hit=atp_structure_hit(layer, frame, c, armed_at), last_end=last_end, last_close=last_close)
    zones = await RC._areas(session, user, key, last_end, last_close)  # noqa: SLF001 - the reading's own stored-areas read
    ctx = await RC._context(session, key, last_end)                    # noqa: SLF001 - the reading's own Daily context
    if not ctx:
        return Reading(SOURCE_ATP, last_end=last_end, last_close=last_close, note="The Daily trend is not available for this symbol yet: the plan waits.")
    areas, index = plan_areas(zones.get("bands") or [], c)
    hit = await asyncio.to_thread(atp_area_hit, frame, areas, index, ctx, c, armed_at)
    return Reading(SOURCE_ATP, hit=hit, last_end=last_end, last_close=last_close)


# The next time a plan's reading can change: a new candle of his timeframe cannot close before the last one + his
# timeframe (in-process only; a restart reads again at once - harmless, the fire is still once).
_NEXT: dict[tuple[int, str, str], datetime] = {}


def due(plan_id: int, condition_json: str, armed_at: datetime, now: datetime) -> bool:
    nxt = _NEXT.get((plan_id, condition_json, _utc(armed_at).isoformat()))
    return nxt is None or now >= nxt


def seen(plan_id: int, condition_json: str, armed_at: datetime, reading: Reading, minutes: int | None, now: datetime) -> None:
    k = (plan_id, condition_json, _utc(armed_at).isoformat())
    if reading.last_end is not None and minutes:
        _NEXT[k] = max(_utc(reading.last_end) + timedelta(minutes=int(minutes)), now + timedelta(seconds=1))
    else:
        _NEXT[k] = now + timedelta(seconds=RETRY_SECONDS)
    if len(_NEXT) > 4096:
        _NEXT.pop(next(iter(_NEXT)))


def clear() -> None:
    _NEXT.clear()


__all__ = ["PRICE", "AREA", "STRUCTURE", "KINDS", "METHOD_KINDS", "Hit", "Reading", "read", "atp_structure_hit", "atp_area_hit",
           "engine_structure_hit", "engine_area_hit", "plan_areas", "read_tf", "overlaps", "due", "seen", "clear"]
