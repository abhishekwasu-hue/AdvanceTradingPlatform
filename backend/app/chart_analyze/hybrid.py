"""Analyze a chart, hybrid mode (M59 CR-4, owner order ATP_ORDER_M59): "Advanced analysis (code + AI)".

The user uploads (or links) his chart; its symbol and timeframe are read from what is printed on it (owner order M84,
app/chart_analyze/detect.py), and the user's own values, when he gives them, win. No price is ever read from the picture: its
price axis is not known to ATP, so nothing is drawn on it. Instead the server reads ATP's OWN structure reading of the
stored candles for that symbol / timeframe and code draws it on a server-rendered chart of the same candles, shown
beside the user's picture ("Your chart" | "ATP's reading of <SYMBOL> <TF>"):

- the structure per timeframe (the price-action layer, app/charts/price_action_layer.py): trend, protected level and
  the last break of structure, as a table (Timeframe | Trend | Protected level | Last break);
- the areas of interest nearest the price (the market-zones layer, app/charts/zones_layer.py), above and below, with
  the distance in points - drawn as bands;
- the protected level and the nearest unbroken swing levels - drawn as lines;
- the trigger candle: the candle of the last break of structure on the picked timeframe - marked;
- a checklist of these facts (present / absent, never scored).

Then, when the private engine has the hybrid entry point (provider.HybridProvider), it receives only these generic
facts (and ATP's own rendered chart) and returns the generic output contract; without it the result is "Advanced
analysis (code)" without AI wording. Every word here is ATP's fixed wording; no method text is in this module.
Reference only: nothing here places, proposes or changes an order; no buy / sell / target / stop wording.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pandas as pd
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.chart_analyze import HYBRID_FLAG, contract, hybrid_depth
from app.db.models import User

TIMEFRAMES = ("5m", "15m", "1h", "1d")
TF_WORDS = {"1w": "Weekly", "1d": "Daily", "1h": "1-hour", "15m": "15-minute", "5m": "5-minute"}
SYMBOL = re.compile(r"^[A-Z0-9 &._:-]{1,40}$")
LABEL_CODE = "Advanced analysis (code)"
LABEL_CODE_AI = "Advanced analysis (code + AI)"
LABEL_AI_ONLY = "AI only"
METHOD_CODE, METHOD_CODE_AI = "code", "code_ai"
OFF_MESSAGE = "Advanced analysis is not switched on for your organisation yet. Use AI only for now."
ADMIN_WHERE = "Admin > Feature flags > Charts and tools"
ADMIN_NEEDS = ("chart_analyze", "chart_price_action", "market_zones (for the areas of interest)")
# Owner 4 Nov (ADV-2): a positive sentence - what ATP does, never what it cannot.
SIDE_BY_SIDE = ("ATP draws its reading on its own chart of this symbol and timeframe, beside your chart; your picture stays "
                "exactly as you added it.")
NOT_SHAREABLE = "Only an AI only analysis can be shared for now."
STRUCTURE_OFF = "ATP's structure reading is not switched on for your organisation yet. Use AI only for now."
ZONES_OFF = "Areas of interest are not switched on for your organisation yet."
BUSY = "ATP's structure reading is busy right now. Try again in a minute."
DRAWN_BARS = 120
MAX_AREAS = 6
MAX_LEVELS = 6
MIN_BARS = 30                     # the bars a timeframe's structure needs before it is a reading (as the Copilot's card)
PICK_KEY = "_pick"                # the user's own values stored with a hybrid row until its result replaces it (no new column)
READ_KEY = "_read"                # M84: what the chart showed (as printed and as mapped), once the picture was read
NEEDS_KEY = "_needs"              # M84: the values still to ask for, each with its exact reason
SOURCES = ("stored", "broker", "stored+broker")   # ADV-2 A: where a reading's candles came from (`reading.source`)
BREAK_WORDS = {"BOS": "Swing break", "CHOCH": "Break against the trend", "MSS": "Break against the trend",
               "REVERSAL_CONFIRMED": "Trend turned", "RECOVERY": "Trend recovered"}


class PickError(ValueError):
    """The symbol / timeframe the user picked is missing or not one ATP reads."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ReadingUnavailable(RuntimeError):
    """ATP's reading of this symbol / timeframe cannot be made (no stored candles, the layer is off or busy)."""


def check_pick(symbol: str | None, timeframe: str | None) -> dict[str, str]:
    """The user's pick, normalised, or PickError. The pick is the ONLY source of the symbol and timeframe."""
    sym = " ".join(str(symbol or "").strip().upper().split())
    if not sym:
        raise PickError("symbol_required", "Pick the chart's symbol (for example NIFTY or RELIANCE).")
    if not SYMBOL.fullmatch(sym):
        raise PickError("bad_symbol", "Use the symbol's name only: letters, digits and . & - (at most 40).")
    tf = str(timeframe or "").strip()
    if not tf:
        raise PickError("timeframe_required", "Pick the chart's timeframe.")
    if tf not in TIMEFRAMES:
        raise PickError("bad_timeframe", "Pick one of the listed timeframes: " + ", ".join(TF_WORDS[t] for t in TIMEFRAMES) + ".")
    return {"symbol": sym, "timeframe": tf}


def check_override(symbol: str | None, timeframe: str | None) -> dict[str, str | None]:
    """M84: the user's own values, each OPTIONAL (an empty one is read from the chart); a value given is checked as a pick."""
    sym = " ".join(str(symbol or "").strip().upper().split()) or None
    tf = str(timeframe or "").strip() or None
    if sym is not None and not SYMBOL.fullmatch(sym):
        raise PickError("bad_symbol", "Use the symbol's name only: letters, digits and . & - (at most 40).")
    if tf is not None and tf not in TIMEFRAMES:
        raise PickError("bad_timeframe", "Pick one of the listed timeframes: " + ", ".join(TF_WORDS[t] for t in TIMEFRAMES) + ".")
    return {"symbol": sym, "timeframe": tf}


def off_detail(*, admin: bool) -> dict[str, Any]:
    """The flag-off answer: one plain line for a user; the admin also reads the flag's name and where to switch it."""
    out: dict[str, Any] = {"message": OFF_MESSAGE, "code": "hybrid_off"}
    if admin:
        out["admin"] = {"flag": HYBRID_FLAG, "where": ADMIN_WHERE, "needs": list(ADMIN_NEEDS)}
    return out


def tf_label(tf: str) -> str:
    return TF_WORDS.get(tf, tf)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return round(number, 2) if number == number and abs(number) != float("inf") else None


def _trend(direction: Any) -> tuple[str | None, bool]:
    """(up / down / sideways / None, weak) from the layer's direction words."""
    text = str(direction or "none")
    weak = "weak" in text
    if text.startswith("up"):
        return "up", weak
    if text.startswith("down"):
        return "down", weak
    if text == "range":
        return "sideways", False
    return None, False


def _break(event: Any) -> dict[str, Any] | None:
    if not isinstance(event, dict):
        return None
    price = _num(event.get("price"))
    kind = str(event.get("type") or event.get("name") or "").upper()
    if price is None or not kind:
        return None
    return {"label": BREAK_WORDS.get(kind, "Structure break"), "price": price,
            "direction": event.get("direction") if event.get("direction") in ("up", "down") else None,
            "time": int(event["time"]) if isinstance(event.get("time"), (int, float)) else None}


def _row(tf: str, direction: Any, protected: Any, last: Any, bars: int) -> dict[str, Any]:
    trend, weak = _trend(direction)
    ready = bars >= MIN_BARS
    return {"tf": tf, "label": tf_label(tf), "ready": ready, "trend": trend if ready else None, "weak": weak and ready,
            "protected": _num(protected) if ready else None, "last_break": _break(last) if ready else None}


def _bars(frame: pd.DataFrame) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in frame.tail(DRAWN_BARS).itertuples():
        values = [_num(getattr(r, k)) for k in ("open", "high", "low", "close")]
        if any(v is None or v <= 0 for v in values):
            continue
        o, h, lo, c = (float(v or 0) for v in values)
        out.append({"t": int(pd.Timestamp(r.timestamp).timestamp()), "o": o, "h": h, "l": lo, "c": c})
    return out


def _areas(bands: list[dict[str, Any]], price: float | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in bands:
        low, high = _num(b.get("low")), _num(b.get("high"))
        if low is None or high is None:
            continue
        low, high = min(low, high), max(low, high)
        if price is None:
            where, distance = ("above" if b.get("role") == "supply" else "below"), None
        elif low <= price <= high:
            where, distance = "inside", 0.0
        elif low > price:
            where, distance = "above", round(low - price, 2)
        else:
            where, distance = "below", round(price - high, 2)
        out.append({"where": where, "low": low, "high": high, "distance": distance, "distance_pct": hybrid_depth.pct(distance, price),
                    "state": str(b.get("state") or "") or None, "timeframe": str(b.get("timeframe") or "") or None})
    out.sort(key=lambda a: (a["distance"] is None, a["distance"] or 0.0))
    return out[:MAX_AREAS]


def _levels(layer: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for lv in layer.get("levels") or []:
        price = _num(lv.get("price")) if isinstance(lv, dict) else None
        if price is None or lv.get("status") not in (None, "live"):
            continue
        out.append({"kind": "swing high" if lv.get("kind") == "resistance" else "swing low", "price": price})
    return out[:MAX_LEVELS]


def _checklist(tf: str, chart_row: dict[str, Any], rows: list[dict[str, Any]], areas: list[dict[str, Any]],
               trigger: dict[str, Any] | None, zones_note: str | None, price: float | None) -> list[dict[str, Any]]:
    """ATP's facts, each present or absent with its plain reason - listed, never scored or weighed. ADV-2 (owner 4 Nov): a
    reason is always a fact; a check with no fact to give (nothing read for it) is left out of the list, never shown as
    a negative placeholder. `zones_note` stays in the signature for the callers; it is no longer a reason."""
    word = tf_label(tf)
    trend = chart_row["trend"]
    brk = chart_row["last_break"]
    above = sum(1 for a in areas if a["where"] == "above")
    below = sum(1 for a in areas if a["where"] == "below")
    inside = [a for a in areas if a["where"] == "inside"]
    others = [r for r in rows if r["tf"] != tf]
    ready = [r["label"] for r in others if r["ready"]]
    out: list[dict[str, Any]] = []
    if trend in ("up", "down", "sideways"):
        out.append({"name": f"Trend on the {word} chart", "present": trend in ("up", "down"),
                    "reason": {"up": "Up", "down": "Down", "sideways": "Sideways"}[trend] + (" (weak)" if chart_row["weak"] else "")})
    if chart_row["protected"] is not None:
        out.append({"name": "Protected level", "present": True, "reason": f"{chart_row['protected']:,.2f}"})
    if brk is not None:
        out.append({"name": "Last break of structure", "present": True, "reason": f"{brk['label']} at {brk['price']:,.2f}"})
        out.append({"name": "Trigger candle marked", "present": trigger is not None,
                    "reason": "The candle of the last break is marked on ATP's chart" if trigger else "The last break is older than the candles drawn"})
    if areas:
        out.append({"name": "Area of interest nearby", "present": True,
                    "reason": f"{above} above, {below} below" + (f", {len(inside)} at the price" if inside else "")})
        if inside:
            out.append({"name": "Price inside an area of interest", "present": True, "reason": f"{inside[0]['low']:,.2f} – {inside[0]['high']:,.2f}"})
        elif price is not None:
            out.append({"name": "Price inside an area of interest", "present": False, "reason": "Between areas"})
    if ready:
        out.append({"name": "Higher and lower timeframes read", "present": len(ready) == len(others), "reason": ", ".join(ready) + " read"})
    return out


def build_reading(pick: dict[str, str], layer: dict[str, Any], bands: list[dict[str, Any]], frame: pd.DataFrame,
                  zones_note: str | None = None, source: str = "stored") -> dict[str, Any]:
    """The reading (pure): the layer and the zones of the candles read -> what the page and the chart show. ADV-2:
    `source` ("stored" | "broker" | "stored+broker": where the candles came from), `bars_read` (the picked timeframe's)
    and the depth fields (app/chart_analyze/hybrid_depth.py); every older field is kept."""
    tf = pick["timeframe"]
    state = layer.get("structure_state") or {}
    price = _num(layer.get("last_close"))
    chart_row = _row(tf, state.get("direction"), state.get("protected"), state.get("last_event"), int(layer.get("bars") or 0))
    rows: list[dict[str, Any]] = []
    for r in layer.get("mtf") or []:
        if isinstance(r, dict) and r.get("tf"):
            rows.append(chart_row if r["tf"] == tf else _row(str(r["tf"]), r.get("direction"), r.get("protected"), r.get("last_event"),
                                                             int(r.get("bars") or 0)))
    if not any(r["tf"] == tf for r in rows):
        rows.insert(0, chart_row)
    order = {t: i for i, t in enumerate(("1w", "1d", "1h", "15m", "5m"))}
    rows.sort(key=lambda r: order.get(r["tf"], 99))
    bars = _bars(frame)
    trigger = None
    brk = chart_row["last_break"]
    if brk and brk["time"] is not None:
        for i, b in enumerate(bars):
            if b["t"] == brk["time"]:
                trigger = {"index": i, **b}
                break
    areas = _areas(bands, price)
    deep = hybrid_depth.depth(layer if chart_row["ready"] else {}, tf_label(tf), chart_row["protected"], areas, price)
    return {"symbol": pick["symbol"], "timeframe": tf, "timeframe_label": tf_label(tf), "instrument": layer.get("instrument"),
            "as_of": layer.get("as_of"), "last_close": price, "source": source if source in SOURCES else "stored",
            "bars_read": int(layer.get("bars") or 0), "bars": bars,
            "table": rows, "areas": areas, "levels": _levels(layer), "protected": chart_row["protected"], "last_break": brk,
            "trigger_candle": trigger, "zones_note": zones_note,
            "checklist": _checklist(tf, chart_row, rows, areas, trigger, zones_note, price), **deep}


def facts(reading: dict[str, Any], *, width: int, height: int) -> dict[str, Any]:
    """The generic facts the private engine's hybrid entry point receives: ATP's reading only (no user picture, no
    method word). JSON-able."""
    return {"symbol": reading["symbol"], "timeframe": reading["timeframe"], "as_of": reading["as_of"], "last_close": reading["last_close"],
            "timeframes": [{k: r[k] for k in ("tf", "ready", "trend", "weak", "protected", "last_break")} for r in reading["table"]],
            "areas": reading["areas"], "levels": reading["levels"], "protected": reading["protected"], "last_break": reading["last_break"],
            "trigger_candle": reading["trigger_candle"], "bars": reading["bars"], "chart": {"width": width, "height": height},
            "coordinate_space": "time_price"}


def _ai_block(validated: dict[str, Any] | None) -> dict[str, Any] | None:
    """The engine's validated contract without its drawing (code draws in hybrid) and without the image-only fields."""
    if validated is None:
        return None
    keep = ("story", "trend", "current_state", "rsi", "setup", "evidence", "scenarios", "what_to_wait_for", "htf_context", "key_levels")
    return {k: validated[k] for k in keep if k in validated}


def result(pick: dict[str, str], reading: dict[str, Any], *, width: int, height: int, ai: dict[str, Any] | None,
           notes: list[str], ai_version: dict[str, str] | None = None, read_from_chart: dict[str, Any] | None = None,
           pick_source: dict[str, str | None] | None = None) -> dict[str, Any]:
    """The stored hybrid result. M84: `read_from_chart` is what the chart showed (None when the user gave both values),
    `pick_source` where each value of the pick came from ("chart" or "you")."""
    method = METHOD_CODE_AI if ai is not None else METHOD_CODE
    return {"contract_version": contract.CONTRACT_VERSION, "mode": "hybrid", "method": method,
            "label": LABEL_CODE_AI if ai is not None else LABEL_CODE, "disclaimer": contract.DISCLAIMER,
            "approximate_prices": False, "coordinate_space": "time_price", "pick": dict(pick),
            "side_by_side_note": SIDE_BY_SIDE, "reading": reading, "chart": {"width": width, "height": height},
            "ai": _ai_block(ai), "ai_version": ai_version, "notes": [n for n in notes if n][:4], "read_from_chart": read_from_chart,
            "pick_source": dict(pick_source or {"symbol": "you", "timeframe": "you"})}


def stored_pick(result_json: str | None) -> dict[str, str] | None:
    """The pick stored with a hybrid row: before its analysis under `_pick`, after it inside the result."""
    if not result_json:
        return None
    try:
        data = json.loads(result_json)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get(PICK_KEY) or data.get("pick")
    try:
        return check_pick(raw.get("symbol"), raw.get("timeframe")) if isinstance(raw, dict) else None
    except PickError:
        return None


def _stored(result_json: str | None) -> dict[str, Any]:
    if not result_json:
        return {}
    try:
        data = json.loads(result_json)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def stored_override(result_json: str | None) -> dict[str, str | None]:
    """M84: the user's own values stored with a hybrid row (either may be None: read from the chart); after the analysis,
    the pick it used."""
    data = _stored(result_json)
    raw = data.get(PICK_KEY) or data.get("pick")
    if not isinstance(raw, dict):
        return {"symbol": None, "timeframe": None}
    try:
        return check_override(raw.get("symbol"), raw.get("timeframe"))
    except PickError:
        return {"symbol": None, "timeframe": None}


def stored_read(result_json: str | None) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """M84: (what the chart showed, the values still to ask for) stored with a hybrid row before its result."""
    data = _stored(result_json)
    read = data.get(READ_KEY)
    needs = data.get(NEEDS_KEY)
    return (read if isinstance(read, dict) else None,
            [n for n in needs if isinstance(n, dict)] if isinstance(needs, list) else [])


def summary(parsed: dict[str, Any]) -> dict[str, Any]:
    reading = parsed.get("reading") or {}
    table = reading.get("table") or []
    chart_row: dict[str, Any] = next((r for r in table if r.get("tf") == reading.get("timeframe")), {})
    setup = ((parsed.get("ai") or {}).get("setup") or {}).get("status")
    return {"setup_status": setup if setup in ("formed", "forming", "none") else None, "trend": chart_row.get("trend") or "unclear",
            "symbol": reading.get("symbol") or (parsed.get("pick") or {}).get("symbol")}


# --- the reads (I/O) ------------------------------------------------------------------------------------------------------

def _plain(exc: HTTPException) -> str:
    if exc.status_code == 503 and (exc.headers or {}).get("X-Feature-Disabled"):
        return STRUCTURE_OFF
    if exc.status_code == 503:
        return BUSY
    return "ATP's structure reading could not be made for this symbol and timeframe. Try another timeframe, or use AI only."


def unavailable_message(symbol: str, tf: str, have: list[str]) -> str:
    """Owner 3 Nov: the pick's timeframe has no stored candles - say what ATP DOES hold for the symbol, so the user picks
    that timeframe (and uploads a chart of it), never only what is missing."""
    if have:
        words = [tf_label(t) for t in have]
        listed = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
        return (f"Advanced analysis for {symbol} works on {listed} candles. Pick {'that timeframe' if len(words) == 1 else 'one of these'} "
                f"and upload a chart of the same timeframe - or read this {tf_label(tf).lower()} chart with AI only.")
    return f"ATP does not hold candles for {symbol} yet, so this chart can be read with AI only."


async def _stored_timeframes(session: AsyncSession, user: User, key: str, when: datetime, *, skip: str) -> list[str]:
    """The pick timeframes (TIMEFRAMES order) with stored candles for `key` in their usual window, `skip` left out."""
    from app.charts import price_action_layer as pal
    from app.market_lake import own_bars
    private = await own_bars.scope_for(session, user, key)
    out: list[str] = []
    for t in TIMEFRAMES:
        if t == skip:
            continue
        try:
            frame, _src = await pal.load_frame(session, key, t, when - timedelta(days=pal.DEFAULT_DAYS[t]), when, private=private)
        except Exception:  # noqa: BLE001 - a probe that cannot read a timeframe just does not list it
            continue
        if len(frame):
            out.append(t)
    return out


def no_session_message(symbol: str, tf: str, have: list[str]) -> str:
    """ADV-2 A: too few stored candles for the pick and no broker session of the user's own - ONE positive sentence: what
    works now, and what makes this timeframe work (the style of `unavailable_message`)."""
    word = tf_label(tf)
    if have:
        words = [tf_label(t) for t in have]
        listed = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
        return (f"Advanced analysis for {symbol} works on {listed} candles now, and on {word} candles too once you log in to your "
                f"broker on the Broker page - or read this chart with AI only.")
    return (f"Advanced analysis for {symbol} on {word} candles works once you log in to your broker on the Broker page - until then, "
            f"read this chart with AI only.")


def source_of(kinds: list[str]) -> str:
    """"stored" | "broker" | "stored+broker" from the kinds of the timeframes read."""
    seen = set(kinds) & {"stored", "broker"}
    return "stored+broker" if len(seen) == 2 else (seen.pop() if seen else "stored")


async def read(session: AsyncSession, user: User, pick: dict[str, str], *, now: datetime | None = None) -> dict[str, Any]:
    """ATP's reading of the candles for the pick, as of the current minute (closed candles only). The session is
    released while the layer builds (its build slot), so nothing read before may be used lazily after.
    ADV-2 A: per timeframe (the picked one and the top-down rows) the stored candles when they hold at least MIN_BARS,
    else the user's own broker candles of the same window (app/chart_analyze/hybrid_sources.py, read only). Too few
    stored candles for the pick and no broker session of his own: ReadingUnavailable with one positive sentence.
    ADV-2 B: no bands from the zones job (or zones off) -> the bands of this read, computed on the spot."""
    from app.ai.tools.copilot import lake_key
    from app.chart_analyze import hybrid_sources as hs
    from app.charts import price_action_concepts as PC
    from app.charts import price_action_layer as pal
    from app.charts import zones_layer as zl
    from app.market_lake import own_bars
    from app.price_action.zones import chart as zchart
    key = lake_key(pick["symbol"])
    tf = pick["timeframe"]
    when = (now or datetime.now(UTC)).replace(second=0, microsecond=0)
    owner = user.id                                   # read now: the build slots close the session
    try:
        layer = await pal.price_action_layer(instrument=key, tf=cast(pal.Timeframe, tf), at=when, start=None, degree=pal.DEFAULT_DEGREE, debug=False, mtf=True,
                                             user=user, session=session)
    except HTTPException as exc:
        raise ReadingUnavailable(_plain(exc)) from None
    private = await own_bars.scope_for(session, user, key)
    begin = when - timedelta(days=pal.DEFAULT_DAYS[tf])
    try:
        frame, _src = await pal.load_frame(session, key, tf, begin, when, private=private)
    except pal.WindowTooLarge:
        frame = pd.DataFrame(columns=pal.FRAME_COLS)
    own = hs.OwnBrokerBars(session, user, pick["symbol"], key, when)
    stored_bars = int(layer.get("bars") or 0)
    kinds: list[str] = []
    chart_frame: pd.DataFrame | None = None
    if stored_bars < MIN_BARS:
        try:
            broker = await own.frame(tf, begin)
        except hs.NoSession:
            have = await _stored_timeframes(session, user, key, when, skip=tf)
            raise ReadingUnavailable(no_session_message(pick["symbol"], tf, have)) from None
        if len(broker) > stored_bars:
            chart_frame = frame = broker
        elif not stored_bars:
            have = await _stored_timeframes(session, user, key, when, skip=tf)
            raise ReadingUnavailable(unavailable_message(pick["symbol"], tf, have))
    kinds.append("broker" if chart_frame is not None else "stored")
    rows = [r for r in layer.get("mtf") or [] if isinstance(r, dict)]
    redo: dict[int, tuple[str, pd.DataFrame]] = {}
    for i, r in enumerate(rows):
        t, bars = str(r.get("tf") or ""), int(r.get("bars") or 0)
        if t == tf or t not in pal.MTF_DAYS:
            continue                                  # the picked timeframe's row is the chart's own (build_reading)
        if bars < MIN_BARS and not own.no_session:
            try:
                got = await own.frame(t, when - timedelta(days=pal.MTF_DAYS[t]))
            except hs.NoSession:
                got = pd.DataFrame(columns=pal.FRAME_COLS)
            if len(got) > bars:
                redo[i] = (t, got)
                kinds.append("broker")
                continue
        if bars:
            kinds.append("stored")
    if chart_frame is not None or redo:
        def build_rows() -> tuple[dict[str, Any] | None, dict[int, dict[str, Any]]]:
            content = pal.build(chart_frame, tf, pal.DEFAULT_DEGREE, False, None) if chart_frame is not None else None
            return content, {i: PC.mtf_row(f, t, own.source) for i, (t, f) in redo.items()}
        try:
            content, rebuilt = await pal._in_slot(session, owner, build_rows)
        except HTTPException as exc:
            raise ReadingUnavailable(_plain(exc)) from None
        rows = [rebuilt.get(i, r) for i, r in enumerate(rows)]
        layer = {**(layer if content is None else {**content, "instrument": layer.get("instrument") or key, "source": own.source}), "mtf": rows}
    bands: list[dict[str, Any]] = []
    zones_note: str | None = None
    zones_off = False
    try:
        zones = await zl.market_zones_layer(instrument=key, at=when, price=layer.get("last_close"), family="all", max_bands=zchart.MAX_BANDS,
                                            user=user, session=session)
        bands = [b for b in zones.get("bands") or [] if isinstance(b, dict)]
    except HTTPException:
        zones_off = True
    if not bands:
        bands = await hs.read_time_bands(session, user, key, when, layer.get("last_close"), private=private, own=own, min_bars=MIN_BARS)
        zones_note = ZONES_OFF if zones_off and not bands else None
    return build_reading(pick, layer, bands, frame, zones_note, source_of(kinds))


__all__ = ["TIMEFRAMES", "LABEL_CODE", "LABEL_CODE_AI", "LABEL_AI_ONLY", "OFF_MESSAGE", "PickError", "ReadingUnavailable", "check_pick",
           "check_override", "stored_override", "unavailable_message", "no_session_message", "source_of", "SOURCES", "stored_read", "READ_KEY", "NEEDS_KEY", "off_detail", "build_reading", "facts", "result", "stored_pick", "summary", "read", "PICK_KEY"]
