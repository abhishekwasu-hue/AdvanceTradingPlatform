"""M77 SX-2: the study SNAPSHOT (`study/1`) - every number of a saved study, built by the server from its own reads.

The browser sends only references and the member's own inputs (the chart's instrument key, the timeframes studied, the
visible range per timeframe, the sections he ticked, his title, his notes and his checklist answers). Every number -
bars, areas, structure, the last price, distances - is read or computed here; a number in the request is never read
(the request model has no field for one, and unknown fields are dropped).

Two parts:
* `assemble(StudyInputs)` - pure: the inputs read from the stores -> the snapshot dict. The same inputs give the same
  snapshot, and `canonical()` gives the same bytes (sorted keys, no spaces, no NaN), so the PDF / PNG / CSV / JSON of a
  stored study are rebuilt with identical bytes (M77Q-3).
* `gather(...)` - the reads (the chart's closed bars per timeframe, his organisation's stored areas through the
  market-zones layer's own scope rule, the Daily trend fold, his live price when his broker session gives one, his own
  drawings on the symbol and his own checklist rules). Only the caller's own rows, inside his organisation.

What is read is the code the Charts page already reads (app/charts/read_chart.py, price_action_layer, zones_layer),
unchanged; nothing here is new chart-reading logic. Reading source: "ATP structure readers" (the method engine's reading
arrives with M75 W-3; its name, version and settings are never written here - M77Q-2). The sections whose sources are
not built yet (trade plan, pre-trade check, scenarios, similar setups) are stored as "not available" with a plain
reason, never blank; SX-5 adds the AI card and the option context, read when the study is saved (app/study/sections.py)
or "not available" with their own reason. No look-ahead: a bar whose END is after the study time is never read.
Read-only: nothing here places, changes or blocks an order.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.charts import price_action_layer as pal
from app.charts import read_chart as R
from app.core import config
from app.db.models import ChartDrawingRecord, InstrumentRecord, User
from app.price_action.zones import chart as zone_chart

SCHEMA = "study/1"
INFO_LINE = "Information and education only, not advice. Every trading decision is yours."
READING_SOURCE = "atp"
READING_SOURCE_WORDS = {"atp": "ATP structure readers", "engine": "method engine"}
TIMEFRAMES: tuple[str, ...] = R.TIMEFRAMES               # 1m 3m 5m 15m 30m 1h 1d, smallest first
MAX_TIMEFRAMES = 4

# The sections a member may tick (PD-1: all on by default). The footer is on every page and is not a choice (PD-2.12).
SECTIONS: tuple[str, ...] = ("cover", "charts", "structure", "areas", "ai_card", "option_context", "trade_plan",
                             "pre_trade_check", "scenarios", "similar_setups", "notes")
SECTION_TITLES = {"cover": "Study", "charts": "Charts", "structure": "Structure", "areas": "Areas", "ai_card": "AI explanation",
                  "option_context": "Option context", "trade_plan": "Trade plan", "pre_trade_check": "Pre-trade check",
                  "scenarios": "Scenarios", "similar_setups": "Similar past setups", "notes": "Notes and checklist"}
# Sections whose source is not part of a saved study yet: the plain reason the study prints ("Not available: ...").
NOT_BUILT = {
    "ai_card": "the AI explanation card is not saved with a study yet",
    "option_context": "the option chain is not saved with a study yet",
    "trade_plan": "no trade plan is saved with a study yet",
    "pre_trade_check": "no pre-trade check card is saved with a study yet",
    "scenarios": "no trade plan is saved with a study yet, so there are no scenarios",
    "similar_setups": "similar past setups are not saved with a study yet",
}
KIND_WORDS = {"EQ": "Equity", "INDEX": "Index", "FUT": "Future", "CE": "Option", "PE": "Option"}
SOURCE_WORDS = {"lake": "market data store", "own": "your own price history", "shared": "your organisation's index history"}
NO_AREAS = "no stored areas for this instrument"
AREAS_OFF = "areas are not available on this platform yet"
NO_QUOTE = "no live price from your broker session"


class StudyTooLarge(ValueError):
    """The snapshot is over STUDY_SNAPSHOT_MAX_BYTES; the message is the plain sentence the member sees."""


@dataclass
class ZoneRead:
    """The stored areas as of the study time (zones_layer.snapshot_for), or why there are none."""
    rows: list[dict[str, Any]] = field(default_factory=list)
    as_of: datetime | None = None
    fallback: bool = False
    reason: str | None = None


@dataclass
class StudyInputs:
    created_at: datetime                                   # UTC, whole seconds: the study time
    key: str                                               # "NSE:NIFTY BANK"
    instrument_kind: str | None                         # EQ / INDEX / FUT / CE / PE from the instrument master, else None
    timeframes: list[str]                                  # largest first
    sections: list[str]
    frames: dict[str, tuple[pd.DataFrame, str | None]]  # tf -> (bars, source label of load_frame)
    zones: ZoneRead
    daily_context: Mapping[str, Any] | None = None
    quote: Mapping[str, Any] | None = None              # {"value", "as_of"} from his broker session, else None
    drawings: list[dict[str, Any]] = field(default_factory=list)
    visible: dict[str, tuple[datetime | None, datetime | None]] = field(default_factory=dict)
    title: str | None = None
    notes: str | None = None
    checklist_rules: list[str] = field(default_factory=list)
    checklist_answers: list[bool | None] = field(default_factory=list)
    app_version: str = config.APP_VERSION
    reading_source: str = READING_SOURCE
    max_bars: int = config.STUDY_CHART_MAX_BARS
    ai_card: Mapping[str, Any] | None = None             # SX-5: app/study/sections.py ai_card_read (a section, or {"reason"})
    option_context: Mapping[str, Any] | None = None      # SX-5: app/study/sections.py option_context_read


# ---------------------------------------------------------------------------------------------------------------------
# Canonical bytes
# ---------------------------------------------------------------------------------------------------------------------
def canonical(snapshot: Mapping[str, Any]) -> bytes:
    """The one byte form of a snapshot: sorted keys, no spaces, UTF-8, NaN refused (a missing number is None)."""
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_size(data: bytes, limit: int = 0) -> None:
    limit = limit or config.STUDY_SNAPSHOT_MAX_BYTES
    if len(data) > limit:
        raise StudyTooLarge(f"This study is too large to save ({math.ceil(len(data) / 1024)} KB; the limit is "
                            f"{limit // 1024} KB). Choose fewer timeframes or a shorter visible range, then save again.")


# ---------------------------------------------------------------------------------------------------------------------
# Pure part
# ---------------------------------------------------------------------------------------------------------------------
def tf_order(tfs: Sequence[str]) -> list[str]:
    """The timeframes largest first (Daily, 1-hour, 15-minute, 5-minute), each once."""
    return sorted(dict.fromkeys(tfs), key=lambda tf: -TIMEFRAMES.index(tf))


def _num(x: Any) -> float | None:
    try:
        value = float(x)
    except (TypeError, ValueError):
        return None
    return pal._num(value) if math.isfinite(value) else None


def _iso(t: Any) -> str | None:
    if t is None:
        return None
    try:
        return pal._iso(t)
    except (TypeError, ValueError):
        return None


def _source_words(label: str | None) -> str | None:
    if not label:
        return None
    return SOURCE_WORDS.get(label.split(":", 1)[0], "market data store")


def _cut(frame: pd.DataFrame, rng: tuple[datetime | None, datetime | None], max_bars: int) -> pd.DataFrame:
    """The bars a chart of the study draws: inside his visible range (when he sent one), the newest `max_bars`."""
    start, end = rng
    if len(frame) and start is not None:
        frame = frame[(pd.to_datetime(frame["timestamp"], utc=True) >= pd.Timestamp(start)).to_numpy()]
    if len(frame) and end is not None:
        frame = frame[(pd.to_datetime(frame["bar_end"], utc=True) <= pd.Timestamp(end)).to_numpy()]
    return frame.tail(max_bars).reset_index(drop=True)


def _bar_rows(frame: pd.DataFrame) -> list[list[Any]]:
    vol = frame["volume"] if "volume" in frame.columns else pd.Series([None] * len(frame))
    return [[pal._epoch(t), _num(o), _num(h), _num(lo), _num(c), _num(v)]
            for t, o, h, lo, c, v in zip(frame["timestamp"], frame["open"], frame["high"], frame["low"], frame["close"], vol)]


def _areas(zones: ZoneRead, price: float | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(the study's area rows with S1 / B1 labels - numbered from the nearest, never the stored zone key -, the bands as
    read_chart reads them - for the commitment replay).
    The bands the Charts page draws at `price` (zone_chart.select_bands, unchanged); each keeps its own timeframe."""
    if price is None or not zones.rows:
        return [], []
    by_key = {str(r.get("zone_key")): r for r in zones.rows}
    bands = zone_chart.bands_from_rows(zones.rows, price)
    rows: list[dict[str, Any]] = []
    for band in bands:                                    # top to bottom, as the chart draws them
        role = str(band["role"])
        raw = by_key.get(str(band["zone_key"]), {})
        low, high = float(band["low"]), float(band["high"])
        distance = 0.0 if low <= price <= high else (low - price if low > price else price - high)
        last = raw.get("last_event") or {}
        rows.append({"label": "", "timeframe": str(band["timeframe"]), "tf_words": R.TF_WORDS.get(str(band["timeframe"]), str(band["timeframe"])),
                     "role": role, "kind": band.get("kind"), "low": _num(low), "high": _num(high), "state": str(band["state"]),
                     "state_words": R.STATE_WORDS.get(str(band["state"]), str(band["state"])), "touches": int(band.get("touches") or 0),
                     "formed_at": _iso(raw.get("formed_at")), "last_visit": _iso(last.get("time")) if isinstance(last, Mapping) else None,
                     "side": "above" if low > price else "below" if high < price else "at",
                     "distance_points": _num(distance), "_key": str(band["zone_key"])})
    for role, letter in ((zone_chart.SUPPLY, "S"), (zone_chart.DEMAND, "B")):   # S1 / B1 = the nearest seller / buyer area
        for i, row in enumerate(sorted((r for r in rows if r["role"] == role), key=lambda r: (r["distance_points"], r["_key"])), 1):
            row["label"] = f"{letter}{i}"
    return rows, bands


def _nearest(areas: Sequence[Mapping[str, Any]], tf: str) -> dict[str, Any]:
    """The nearest area above and below the reference price among the areas OF THIS TIMEFRAME (never merged)."""
    own = [a for a in areas if a["timeframe"] == tf]
    out: dict[str, Any] = {}
    for side in ("above", "below"):
        near = sorted((a for a in own if a["side"] in (side, "at")), key=lambda a: (a["distance_points"], a["label"]))
        out[side] = None if not near else {"label": near[0]["label"], "role": near[0]["role"], "low": near[0]["low"],
                                           "high": near[0]["high"], "distance_points": near[0]["distance_points"]}
    return out


def _tf_read(tf: str, frame: pd.DataFrame, source: str | None, inputs: StudyInputs, areas: list[dict[str, Any]],
             bands: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(chart, structure) of one timeframe from its closed bars - the readers read_chart calls, unchanged."""
    words = R.TF_WORDS.get(tf, tf)
    if not len(frame):
        reason = R.NO_BARS
        return ({"tf": tf, "tf_words": words, "available": False, "reason": reason, "bars": [], "swings": [], "events": [],
                 "as_of": None, "source": _source_words(source)},
                {"tf": tf, "tf_words": words, "available": False, "reason": reason})
    layer = pal.compute_layer(frame, tf)
    structure = R.structure_part(layer, tf)
    state = layer.get("structure_state") or {}
    last = state.get("last_event") or {}
    known = inputs.zones.as_of
    commitment = R.commitment_part(frame, [R.area_part(b) for b in bands], inputs.daily_context, known) if bands else None
    label_of = {a["_key"]: a["label"] for a in areas}
    if commitment is not None:
        area = commitment.get("area") or {}
        commitment = {"time": commitment.get("time"), "bar_end": commitment.get("bar_end"), "role": commitment.get("role"),
                      "form": commitment.get("form"), "area": label_of.get(str(area.get("key"))), "words": commitment.get("words")}
    shown = _cut(frame, inputs.visible.get(tf, (None, None)), inputs.max_bars)
    first = pal._epoch(shown["timestamp"].iloc[0]) if len(shown) else None
    swings = [{"label": s["label"], "kind": s["kind"], "price": s["price"], "time": s["time"]}
              for s in layer.get("swings") or [] if s.get("status") == "confirmed" and first is not None and s["time"] >= first]
    events = [{"type": e["type"], "name": e["name"], "direction": e["direction"], "price": e["price"], "time": e["time"]}
              for e in layer.get("structure") or [] if first is not None and e["time"] >= first]
    as_of = _iso(frame["bar_end"].iloc[-1])
    chart = {"tf": tf, "tf_words": words, "available": True, "reason": None, "bars": _bar_rows(shown), "swings": swings,
             "events": events, "as_of": as_of, "source": _source_words(source), "last_close": _num(frame["close"].iloc[-1])}
    prot = structure.get("protected")
    row = {"tf": tf, "tf_words": words, "available": True, "reason": None, "as_of": as_of,
           "direction": structure["direction"], "words": structure["words"],
           "last_event": None if not structure.get("last_event") else {
               "type": structure["last_event"]["type"], "direction": structure["last_event"]["direction"],
               "words": structure["last_event"]["words"], "time": structure["last_event"].get("time"),
               "level": _num(last.get("price")) if last else None},
           "protected": None if not prot else {"price": _num(prot["price"]), "side": prot["side"], "words": prot["words"]},
           "nearest": _nearest(areas, tf), "commitment": commitment}
    return chart, row


def _reference(inputs: StudyInputs, closed: Mapping[str, pd.DataFrame]) -> dict[str, Any]:
    """The price distances are measured from: his live price when his broker session gave one, else the last close of the
    smallest timeframe studied (labelled so)."""
    quote = inputs.quote or {}
    value = _num(quote.get("value"))
    if value is not None and value > 0:
        return {"value": value, "source": "live price", "as_of": quote.get("as_of")}
    for tf in sorted(closed, key=TIMEFRAMES.index):
        frame = closed[tf]
        if len(frame):
            return {"value": _num(frame["close"].iloc[-1]), "source": f"last close ({R.TF_WORDS.get(tf, tf)})",
                    "as_of": _iso(frame["bar_end"].iloc[-1])}
    return {"value": None, "source": None, "as_of": None}


def _drawing(d: Mapping[str, Any]) -> dict[str, Any]:
    """His drawing as stored (`drawing/1`: time / price anchors, style, text), without its row id or version."""
    body = d.get("drawing") or {}
    return {"kind": d.get("kind"), "timeframe": d.get("timeframe"), "anchors": body.get("anchors") or [],
            "style": body.get("style") or {}, "text": body.get("text"), "levels": body.get("levels")}


def assemble(inputs: StudyInputs) -> dict[str, Any]:
    """The snapshot of a study (pure; see the module docstring)."""
    when = inputs.created_at
    tfs = tf_order(inputs.timeframes)
    ticked = [s for s in SECTIONS if s in set(inputs.sections)]
    closed = {tf: R.closed_only(inputs.frames.get(tf, (pd.DataFrame(columns=pal.FRAME_COLS), None))[0].reset_index(drop=True), when)
              for tf in tfs}
    ref = _reference(inputs, closed)
    areas, bands = _areas(inputs.zones, ref["value"])
    charts, structure = [], []
    for tf in tfs:
        chart, row = _tf_read(tf, closed[tf], inputs.frames.get(tf, (None, None))[1], inputs, areas, bands)
        charts.append(chart)
        structure.append(row)
    exchange, _, symbol = inputs.key.partition(":")
    quote = inputs.quote or {}
    live = _num(quote.get("value"))
    sources: list[dict[str, Any]] = [{"name": f"{c['tf_words']} bars", "words": c["source"] or "not available", "as_of": c["as_of"]}
                                     for c in charts]
    sources.append({"name": "Areas", "words": inputs.zones.reason or ("platform default settings" if inputs.zones.fallback else
                                                                      "your organisation's stored areas"),
                    "as_of": _iso(inputs.zones.as_of)})
    pull = R.pullback_part(inputs.daily_context)
    sources.append({"name": "Daily trend", "words": "the Daily trend fold" if pull["available"] else "not available", "as_of": pull["as_of"]})
    sources.append({"name": "Live price", "words": "your broker session" if live is not None else NO_QUOTE,
                    "as_of": quote.get("as_of") if live is not None else None})
    data: dict[str, Any] = {}
    if "cover" in ticked:
        data["cover"] = {"live_price": {"value": live, "as_of": quote.get("as_of") if live is not None else None,
                                        "reason": None if live is not None else NO_QUOTE},
                         "reference_price": ref}
    if "charts" in ticked:
        data["charts"] = charts
    if "structure" in ticked:
        data["structure"] = {"timeframes": structure, "daily_trend": pull, "reference_price": ref}
    if ticked and {"charts", "structure", "areas"} & set(ticked):
        data["areas"] = {"rows": [{k: v for k, v in a.items() if not k.startswith("_")} for a in areas],
                         "as_of": _iso(inputs.zones.as_of), "reference_price": ref,
                         "reason": None if areas else (inputs.zones.reason or NO_AREAS),
                         "settings_note": zone_chart_note(inputs.zones)}
    if "notes" in ticked:
        answers = list(inputs.checklist_answers or [])
        data["notes"] = {"text": inputs.notes if inputs.notes else None,
                         "checklist": [{"rule": rule, "answer": ("yes" if answers[i] else "no") if i < len(answers) and answers[i] is not None
                                        else "not answered"} for i, rule in enumerate(inputs.checklist_rules)]}
    if "charts" in ticked:
        data["drawings"] = [_drawing(d) for d in inputs.drawings]
    not_available = {s: NOT_BUILT[s] for s in ticked if s in NOT_BUILT}
    for name, part in (("ai_card", inputs.ai_card), ("option_context", inputs.option_context)):   # SX-5: read when saved
        if name in ticked and part is not None:
            if part.get("reason"):
                not_available[name] = str(part["reason"])
            else:
                data[name] = dict(part)
                not_available.pop(name, None)
    return {"schema": SCHEMA, "created_at": _iso(when), "title": inputs.title or None,
            "instrument": {"key": inputs.key, "symbol": symbol, "exchange": exchange,
                           "kind": KIND_WORDS.get(str(inputs.instrument_kind or "").upper())},
            "timeframes": tfs, "sections": ticked, "section_titles": {s: SECTION_TITLES[s] for s in ticked},
            "app_version": inputs.app_version, "reading_source": READING_SOURCE_WORDS.get(inputs.reading_source, "ATP structure readers"),
            "data": data, "not_available": not_available,
            "footer": {"text": INFO_LINE, "sources": sources}}


def zone_chart_note(zones: ZoneRead) -> str | None:
    return "platform default settings (none stored with your organisation's zone settings yet)" if zones.fallback else None


# ---------------------------------------------------------------------------------------------------------------------
# The reads (the caller's own data only)
# ---------------------------------------------------------------------------------------------------------------------
async def instrument_kind(session: AsyncSession, exchange: str, symbol: str) -> str | None:
    """EQ / INDEX / FUT / CE / PE from the instrument master (platform-wide reference data), else None."""
    names = (exchange, f"{exchange}_INDEX")
    row = await session.scalar(select(InstrumentRecord.instrument_type).where(
        InstrumentRecord.exchange.in_(names), InstrumentRecord.tradingsymbol == symbol).order_by(InstrumentRecord.id).limit(1))
    if row is None:
        row = await session.scalar(select(InstrumentRecord.instrument_type).where(
            InstrumentRecord.exchange.in_(names), InstrumentRecord.name == symbol).order_by(InstrumentRecord.id).limit(1))
    return None if row is None else str(row)


async def own_drawings(session: AsyncSession, user: User, exchange: str, symbol: str) -> list[dict[str, Any]]:
    """His own drawings on the symbol (not deleted), oldest first - never another member's."""
    rows = await session.scalars(select(ChartDrawingRecord).where(
        ChartDrawingRecord.user_id == user.id, ChartDrawingRecord.tenant_id == user.tenant_id, ChartDrawingRecord.symbol == symbol,
        ChartDrawingRecord.exchange == exchange, ChartDrawingRecord.deleted_at.is_(None)).order_by(ChartDrawingRecord.id))
    out = []
    for r in rows:
        try:
            body = json.loads(r.drawing_json)
        except (TypeError, ValueError):
            continue
        out.append({"kind": r.kind, "timeframe": r.timeframe, "drawing": body if isinstance(body, dict) else {}})
    return out


async def live_quote(session: AsyncSession, user: User, exchange: str, symbol: str) -> dict[str, Any] | None:
    """His live price through the chart's own last-price read (his own broker session / his organisation's feed only);
    None when there is none - the study says "not available", never a guess."""
    from app.market_data.candles_routes import ltp
    try:
        body = await ltp(symbol=symbol, exchange=exchange, broker=None, account_label="primary", user=user, session=session)
    except Exception:  # noqa: BLE001 - no broker session, no entitlement, a broker error: the price is simply not available
        return None
    value = _num((body or {}).get("ltp"))
    if value is None or value <= 0:
        return None
    return {"value": value, "as_of": body.get("timestamp")}


async def zones_at(session: AsyncSession, user: User, key: str, when: datetime) -> ZoneRead:
    from app.charts import zones_layer as zl
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, zl.FLAG, user.tenant_id):
        return ZoneRead(reason=AREAS_OFF)
    snap = await zl.snapshot_for(session, key, when, user.tenant_id, user.id)
    if snap is None:
        return ZoneRead(reason=NO_AREAS)
    return ZoneRead(rows=list(snap.rows), as_of=snap.as_of, fallback=bool(snap.fallback))


async def frames_for(session: AsyncSession, user: User, key: str, tfs: Sequence[str], now: datetime
                     ) -> dict[str, tuple[pd.DataFrame, str | None]]:
    from app.market_lake import own_bars
    private = await own_bars.scope_for(session, user, key)          # DATA-H: his own private bars, only his
    out: dict[str, tuple[pd.DataFrame, str | None]] = {}
    for tf in tfs:
        try:
            frame, source = await pal.load_frame(session, key, tf, now - timedelta(days=pal.DEFAULT_DAYS[tf]), now, private=private)
        except pal.WindowTooLarge:
            frame, source = pd.DataFrame(columns=pal.FRAME_COLS), None
        out[tf] = (R.closed_only(frame, now), source)
    return out


def last_end(frames: Mapping[str, tuple[pd.DataFrame, str | None]], now: datetime) -> datetime | None:
    ends = [pd.Timestamp(f["bar_end"].iloc[-1]).to_pydatetime() for f, _ in frames.values() if len(f)]
    if not ends:
        return None
    end = max(e if e.tzinfo else e.replace(tzinfo=UTC) for e in ends)
    return min(end, now)


__all__ = ["SCHEMA", "INFO_LINE", "SECTIONS", "SECTION_TITLES", "NOT_BUILT", "MAX_TIMEFRAMES", "StudyInputs", "ZoneRead",
           "StudyTooLarge", "assemble", "canonical", "fingerprint", "check_size", "tf_order", "instrument_kind", "own_drawings",
           "live_quote", "zones_at", "frames_for", "last_end"]
