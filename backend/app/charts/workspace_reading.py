"""M75 W-3 (owner order M75 CW-2; docs/design/M75_REVIEW.md findings W4-W7, rows M75Q-3 / M75Q-4 - defaults confirmed by
the owner on 1 Nov): the annotations of the Copilot's chart workspace, drawn by code from ONE source per reading.

One reading = the areas (side, state, tests, timeframe, edges), the swings HH / HL / LH / LL, the trend per timeframe,
the BOS / CHoCH events, the next area above and below the last close, an RSI divergence when one is present, and the
structure level. Every value comes from a reader ATP already has, or from the signal engine; nothing here is a new rule:

* source "atp" - ATP's own readers, called unchanged on the chart's closed bars: the price-action layer
  (`price_action_layer.compute_layer`: confirmed swings, the structure tracker's BOS / CHoCH and its protected level),
  the top-down rows (`price_action_layer.mtf_rows`: the structure per timeframe), the stored areas with ATP's own zone
  states and touch counts (`zones_layer`, already in the reading) and the screener's RSI divergence on the chart's swings
  (`screener.runtime._divergence_swings`, its own RSI period, regular kind - unchanged);
* source "engine" - the signal engine (flag `trade_engine`, the private package installed), read exactly as the
  Copilot's engine reading does (`ai.tools.engine_read`: the runner's own user, his listed symbols, his signal timeframe,
  the runner's own request builder - the same request the worker builds for the same bar). Interface 1 gives areas,
  swings and the higher timeframe's side; the optional parts (an area's tests, the trend per timeframe, BOS / CHoCH,
  divergences) appear only when the package fills them. The two sources are never mixed in one reading.
States (M75Q-4): only states with a public phrase from their own source - ATP's zone states, or the engine's tokens
that `chart_analyze.contract.AREA_STATES` names; any other token is dropped, never mapped onto another set.
Structure level (M75Q-3): only ATP's public protected swing, worded "structure changes beyond"; the engine's internal
invalidation reference is never read here. Never in a response: a model id, an instance id, an area id, evidence or
data-problem tokens, the engine's name / version or its settings.
No look-ahead: everything is read from closed bars only (the caller cuts them); the engine read is cached per
(user, instrument, signal timeframe, last closed bar). Read-only: nothing here places, blocks or changes an order.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import pandas as pd

from app.charts import price_action_layer as pal

FLAG = "copilot_workspace"
ENGINE_FLAG = "trade_engine"
SOURCE_ATP = "atp"
SOURCE_ENGINE = "engine"
SOURCE_WORDS = {SOURCE_ATP: "Drawn from ATP's chart readers on this chart's closed bars.",
                SOURCE_ENGINE: "Drawn from your signal engine's reading of its signal timeframe, as of its last closed bar."}
MAX_SWINGS = 10              # display cap: the newest confirmed swings drawn on the chart
MAX_EVENTS = 4               # display cap: the newest BOS / CHoCH events drawn on the chart
EVENT_TYPES = ("BOS", "CHOCH")
EVENT_NAMES = {"BOS": "BOS", "CHOCH": "CHoCH"}
EVENT_WORDS = {"BOS": "break of structure", "CHOCH": "change of character"}
SWING_LABELS = ("HH", "HL", "LH", "LL")
ROLE_OF_SIDE = {"buyer": "demand", "seller": "supply"}
SIDE_OF_ROLE = {"demand": "buyer", "supply": "seller"}
ATP_STATE_WORDS = {"fresh": "untouched since it formed", "tested": "price has come back to it",
                   "flipped": "price closed through it and held beyond; it now acts on the other side"}
ENGINE_STATE_WORDS = {"fresh": "untouched since it formed", "tested": "price has come back to it",
                      "broken": "price closed through it", "flipped": "price closed through it and held beyond"}
TREND_WORDS = {"up": "up", "up (weak)": "up, weakening", "down": "down", "down (weak)": "down, weakening", "range": "a range",
               "none": "not formed yet", "sideways": "sideways", "unclear": "not clear"}
TF_WORDS = {"1m": "1-minute", "3m": "3-minute", "5m": "5-minute", "15m": "15-minute", "30m": "30-minute", "1h": "1-hour",
            "60m": "1-hour", "1d": "daily", "day": "daily", "1w": "weekly", "week": "weekly"}
LEVEL_WORDS = "structure changes beyond"
NOT_GIVEN = "Not given by this source."
ENGINE_CACHE_SECONDS = pal.MTF_CACHE_SECONDS
ENGINE_CACHE_SIZE = 256


def _tf_words(tf: str) -> str:
    return TF_WORDS.get(tf, tf)


# ---------------------------------------------------------------------------------------------------------------------
# Pure parts
# ---------------------------------------------------------------------------------------------------------------------
def swings_part(layer: Mapping[str, Any], tf: str) -> list[dict[str, Any]]:
    """The newest confirmed swings of the chart's degree with their Dow label (HH / HL / LH / LL)."""
    rows = [s for s in layer.get("swings") or [] if s.get("status") == "confirmed" and s.get("label") in SWING_LABELS]
    return [{"label": s["label"], "kind": s["kind"], "time": s["time"], "price": s["price"], "price_text": s["price_text"], "timeframe": tf}
            for s in rows[-MAX_SWINGS:]]


def events_part(layer: Mapping[str, Any], tf: str) -> list[dict[str, Any]]:
    """The newest BOS / CHoCH events of the structure tracker (each on the bar that confirmed it)."""
    rows = [e for e in layer.get("structure") or [] if e.get("type") in EVENT_TYPES]
    return [{"type": e["type"], "name": EVENT_NAMES[e["type"]], "direction": e.get("direction"), "time": e["time"],
             "swing_time": e.get("swing_time"), "price": e["price"], "price_text": e["price_text"], "timeframe": tf,
             "words": f"{EVENT_WORDS[e['type']]} {e.get('direction') or ''} on the {_tf_words(tf)} chart".replace("  ", " ")}
            for e in rows[-MAX_EVENTS:]]


def trend_part(rows: Sequence[Mapping[str, Any]], tf: str, state: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The structure per timeframe: the top-down rows (week, day, hour, 15 minutes) and the chart's own timeframe."""
    out: list[dict[str, Any]] = []
    for r in rows:
        if not r.get("bars"):
            continue
        direction = str(r.get("direction") or "none")
        out.append({"timeframe": str(r.get("tf")), "direction": direction,
                    "words": f"{_tf_words(str(r.get('tf')))}: {TREND_WORDS.get(direction, direction)}"})
    if state is not None and all(t["timeframe"] != tf for t in out):
        direction = str(state.get("direction") or "none")
        out.append({"timeframe": tf, "direction": direction, "words": f"{_tf_words(tf)}: {TREND_WORDS.get(direction, direction)}"})
    return out


def _area_row(*, role: str, low: float, high: float, timeframe: str, state: str | None, state_words: str | None,
              tests: int | None) -> dict[str, Any]:
    low, high = (low, high) if low <= high else (high, low)
    side = SIDE_OF_ROLE.get(role, "area")
    words = f"{side.capitalize()} area {pal.price_text(low)} - {pal.price_text(high)} ({_tf_words(timeframe)})"
    if state_words:
        words += f": {state_words}"
    if tests:
        words += f"; tested {tests} time{'s' if tests != 1 else ''}"
    return {"role": role, "side": side, "low": pal._num(low), "high": pal._num(high), "low_text": pal.price_text(low),
            "high_text": pal.price_text(high), "timeframe": timeframe, "state": state, "state_words": state_words, "tests": tests,
            "words": words + "."}


def atp_areas(areas: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The reading's stored areas (ATP's zone states and touch counts, unchanged) in the annotation form."""
    out = []
    for a in areas:
        state = str(a.get("state") or "") or None
        out.append(_area_row(role=str(a.get("role") or ""), low=float(a["low"]), high=float(a["high"]), timeframe=str(a.get("timeframe") or ""),
                             state=state if state in ATP_STATE_WORDS else None, state_words=ATP_STATE_WORDS.get(state or ""),
                             tests=int(a.get("touches") or 0)))
    return out


def next_areas(areas: Sequence[Mapping[str, Any]], last_close: float | None) -> dict[str, Any]:
    """The nearest area above the last close, the nearest below it, and the one the price is inside (if any)."""
    if last_close is None:
        return {"above": None, "below": None, "inside": None}
    above = sorted((a for a in areas if a["low"] > last_close), key=lambda a: a["low"])
    below = sorted((a for a in areas if a["high"] < last_close), key=lambda a: -a["high"])
    inside = [a for a in areas if a["low"] <= last_close <= a["high"]]
    return {"above": above[0] if above else None, "below": below[0] if below else None, "inside": inside[0] if inside else None}


def divergence_part(frame: pd.DataFrame, tf: str, degree: int = pal.DEFAULT_DEGREE) -> dict[str, Any] | None:
    """The newest regular RSI divergence on the chart's swings, if the latest confirmed swing of its kind diverges from the
    one before (the screener's own reader, unchanged): bullish = a lower swing low with a higher RSI, bearish = a higher
    swing high with a lower RSI. None when the latest swing of neither kind diverges."""
    from app.screener.runtime import RSI_DIVERGENCE_PERIOD, _divergence_swings
    if len(frame) < 3:
        return None
    f = frame.set_index(pd.to_datetime(frame["timestamp"], utc=True))
    ts = frame["timestamp"].to_numpy()
    best: dict[str, Any] | None = None
    for side in ("bullish", "bearish"):
        swings = _divergence_swings(f, side, degree, RSI_DIVERGENCE_PERIOD)
        if len(swings) < 2 or not swings[-1].diverges:
            continue
        prev, last = swings[-2], swings[-1]
        row = {"side": side, "role": "demand" if side == "bullish" else "supply", "timeframe": tf, "confirmed": last.confirmed,
               "first": {"time": pal._epoch(ts[prev.bar]), "price": pal._num(prev.price)},
               "second": {"time": pal._epoch(ts[last.bar]), "price": pal._num(last.price)},
               "words": (f"RSI divergence on the {_tf_words(tf)} chart: "
                         + ("a lower swing low with a higher RSI." if side == "bullish" else "a higher swing high with a lower RSI."))}
        if best is None or row["confirmed"] > best["confirmed"]:
            best = row
    if best is not None:
        best.pop("confirmed")
    return best


def level_part(state: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """ATP's public structure level - the structure tracker's protected swing (M75Q-3), in structure words."""
    if not state or state.get("protected") is None:
        return None
    direction = str(state.get("direction") or "")
    side = "below" if direction.startswith("up") else "above" if direction.startswith("down") else None
    if side is None:
        return None
    price = float(state["protected"])
    return {"price": pal._num(price), "price_text": pal.price_text(price), "side": side,
            "words": f"The {'up' if side == 'below' else 'down'} structure changes beyond {pal.price_text(price)}."}


def _summary(trend: Sequence[Mapping[str, Any]], nxt: Mapping[str, Any], divergence: Mapping[str, Any] | None,
             events: Sequence[Mapping[str, Any]]) -> list[str]:
    lines: list[str] = []
    if trend:
        lines.append("Trend - " + "; ".join(t["words"] for t in trend) + ".")
    if events:
        e = events[-1]
        lines.append(f"Last structure event: {e['words']}.")
    if nxt.get("inside"):
        lines.append(f"Price is inside the {nxt['inside']['words'][0].lower()}{nxt['inside']['words'][1:]}")
    for where in ("above", "below"):
        if nxt.get(where):
            lines.append(f"Next area {where}: {nxt[where]['words'][0].lower()}{nxt[where]['words'][1:]}")
    if divergence:
        lines.append(divergence["words"])
    return lines


def atp_annotations(frame: pd.DataFrame, layer: Mapping[str, Any], tf: str, areas: Sequence[Mapping[str, Any]],
                    mtf: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The annotations from ATP's readers (source "atp"). Pure: the closed bars, the layer computed on them, the reading's
    stored areas and the top-down rows are its only inputs."""
    state = layer.get("structure_state")
    last_close = layer.get("last_close")
    rows = atp_areas(areas)
    trend = trend_part(mtf, tf, state)
    events = events_part(layer, tf)
    divergence = divergence_part(frame, tf, int(layer.get("degree", pal.DEFAULT_DEGREE)))
    nxt = next_areas(rows, last_close)
    return {"source": SOURCE_ATP, "source_words": SOURCE_WORDS[SOURCE_ATP], "timeframe": tf, "as_of": layer.get("as_of"),
            "last_close": last_close, "areas": rows, "swings": swings_part(layer, tf), "events": events, "trend": trend,
            "next_areas": nxt, "divergence": divergence, "level": level_part(state), "summary": _summary(trend, nxt, divergence, events),
            "not_given": []}


def _engine_tf(tf: str) -> str:
    return "1h" if tf == "60m" else "1d" if tf == "day" else tf


engine_tf = _engine_tf


def engine_annotations(result: Any, *, tf: str, signal_tf: str, last_close: float | None, as_of: str | None) -> dict[str, Any]:
    """The signal engine's result in the annotation form, public parts only (see the module text). Pure; `result` is an
    `EngineResult`. Swings are drawn only on the chart of their own timeframe; the others are listed by label only."""
    from app.chart_analyze.contract import AREA_STATES, TREND_SIDES
    rows = []
    for a in result.areas:
        state = a.state if a.state in AREA_STATES else None
        tests = getattr(a, "tests", None)
        rows.append(_area_row(role=ROLE_OF_SIDE.get(a.side, ""), low=float(a.band.low), high=float(a.band.high), timeframe=str(a.timeframe),
                              state=state, state_words=ENGINE_STATE_WORDS.get(state or ""), tests=None if tests is None else int(tests)))
    swings = [{"label": s.label, "kind": "H" if s.label in ("HH", "LH") else "L", "time": pal._epoch(s.at), "price": pal._num(s.price),
               "price_text": pal.price_text(s.price), "timeframe": str(s.timeframe)}
              for s in sorted(result.swings, key=lambda s: s.at) if s.label in SWING_LABELS and _engine_tf(str(s.timeframe)) == tf][-MAX_SWINGS:]
    trend: list[dict[str, Any]] = []
    for t in getattr(result, "trends", ()) or ():
        if t.side in TREND_SIDES:
            trend.append({"timeframe": str(t.timeframe), "direction": t.side, "words": f"{_tf_words(str(t.timeframe))}: {TREND_WORDS[t.side]}"})
    higher = result.higher_side
    if higher is not None and higher.side in TREND_SIDES and all(t["timeframe"] != str(higher.timeframe) for t in trend):
        trend.insert(0, {"timeframe": str(higher.timeframe), "direction": higher.side,
                         "words": f"{_tf_words(str(higher.timeframe))}: {TREND_WORDS[higher.side]}"})
    events = [{"type": e.kind, "name": EVENT_NAMES[e.kind], "direction": e.direction, "time": pal._epoch(e.at), "swing_time": None,
               "price": pal._num(e.level), "price_text": pal.price_text(e.level), "timeframe": str(e.timeframe),
               "words": f"{EVENT_WORDS[e.kind]} {e.direction} on the {_tf_words(str(e.timeframe))} chart"}
              for e in sorted(getattr(result, "events", ()) or (), key=lambda e: e.at)
              if e.kind in EVENT_TYPES and _engine_tf(str(e.timeframe)) == tf][-MAX_EVENTS:]
    divergence = None
    for d in sorted(getattr(result, "divergences", ()) or (), key=lambda d: d.second_at):
        if _engine_tf(str(d.timeframe)) != tf:
            continue
        side = "bullish" if d.side == "buyer" else "bearish"
        divergence = {"side": side, "role": ROLE_OF_SIDE[d.side], "timeframe": str(d.timeframe),
                      "first": {"time": pal._epoch(d.first_at), "price": pal._num(d.first_price)},
                      "second": {"time": pal._epoch(d.second_at), "price": pal._num(d.second_price)},
                      "words": f"RSI divergence on the {_tf_words(str(d.timeframe))} chart ({side})."}
    nxt = next_areas(rows, last_close)
    not_given = [part for part, present in (("events", bool(events) or bool(getattr(result, "events", ()))),
                                            ("divergence", divergence is not None or bool(getattr(result, "divergences", ()))),
                                            ("level", False)) if not present]
    return {"source": SOURCE_ENGINE, "source_words": SOURCE_WORDS[SOURCE_ENGINE] + f" Signal timeframe: {_tf_words(signal_tf)}.",
            "timeframe": tf, "as_of": as_of, "last_close": None if last_close is None else pal._num(last_close), "areas": rows,
            "swings": swings, "events": events, "trend": trend, "next_areas": nxt, "divergence": divergence, "level": None,
            "summary": _summary(trend, nxt, divergence, events), "not_given": not_given}


# ---------------------------------------------------------------------------------------------------------------------
# The engine read (request time, cached per closed bar)
# ---------------------------------------------------------------------------------------------------------------------
_ENGINE_CACHE: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()


def cache_clear() -> None:
    _ENGINE_CACHE.clear()


async def engine_reading(session: Any, user: Any, key: str, tf: str, now: datetime) -> dict[str, Any] | None:
    """The engine's annotations for `key` on a chart of `tf`, or None when the engine does not read this chart for this
    user (not installed, flag off, not the runner's user, not one of his symbols, incomplete data, a failing read) - the
    caller then draws ATP's. The request is the runner's own (`engine_read.build_request`); the engine runs once per
    (user, instrument, signal timeframe, last closed bar)."""
    read = await engine_result(session, user, key, now)
    if read is None:
        return None
    result, signal_tf, last, as_of = read
    return engine_annotations(result, tf=tf, signal_tf=signal_tf, last_close=last, as_of=as_of)


async def engine_result(session: Any, user: Any, key: str, now: datetime) -> tuple[Any, str, float | None, str] | None:
    """The engine's own result for `key` (the `EngineResult`, his signal timeframe, the last close, the as-of time), read
    and cached exactly as `engine_reading` describes; None when the engine does not read this chart for him. M75 W-9:
    a trigger plan's method condition reads the same result (never ATP's readers beside it)."""
    from app.ai.tools import ToolContext, engine_read
    from app.engine_provider import loader
    from app.engine_provider.contract import INTERFACE_VERSION, EngineResult
    from app.engine_provider.frames import merge_problems
    from app.platform.controls import flag_enabled
    engine = loader.load()
    if engine is None or not await flag_enabled(session, ENGINE_FLAG, user.tenant_id):
        return None
    ctx = ToolContext(session, user.tenant_id, user, now)
    cfg = await engine_read._settings_for(ctx)
    if cfg is None or not cfg.symbols or cfg.signal_timeframe not in engine.timeframes:
        return None
    watch = next((w for w in cfg.symbols if w.key == key), None)
    if watch is None:
        return None
    try:
        request, complete, last = await engine_read.build_request(ctx, cfg, watch, engine)
    except Exception:  # noqa: BLE001 - no engine reading now; ATP's readers answer
        return None
    if request is None or not complete:
        return None
    as_of = request.as_of.isoformat()
    ck = (user.tenant_id, user.id, key, cfg.signal_timeframe, as_of)
    hit = _ENGINE_CACHE.get(ck)
    if hit is not None and hit[0] >= time.monotonic():
        _ENGINE_CACHE.move_to_end(ck)
        result: Any = hit[1]
    else:
        try:
            result = await asyncio.wait_for(asyncio.to_thread(engine.read, request), timeout=cfg.read_timeout_seconds)
        except Exception:  # noqa: BLE001 - a failing or slow read is "no engine reading now"
            return None
        if not isinstance(result, EngineResult) or result.interface != INTERFACE_VERSION or merge_problems(result, ()).data_problems:
            return None
        _ENGINE_CACHE[ck] = (time.monotonic() + ENGINE_CACHE_SECONDS, result)
        _ENGINE_CACHE.move_to_end(ck)
        while len(_ENGINE_CACHE) > ENGINE_CACHE_SIZE:
            _ENGINE_CACHE.popitem(last=False)
    return result, str(cfg.signal_timeframe), last, as_of


__all__ = ["FLAG", "atp_annotations", "engine_annotations", "engine_reading", "engine_result", "engine_tf", "divergence_part", "next_areas", "level_part",
           "trend_part", "swings_part", "events_part", "cache_clear"]
