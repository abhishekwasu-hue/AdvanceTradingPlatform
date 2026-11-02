"""M76 MP-3 (owner order M76 W-1 + amendment 1-5; docs/design/M76_REVIEW.md sections 1 W-1, 9; OPEN_QUESTIONS M76Q-7): the
check card's structure lines - the ticket's order set beside the SAME reading the Copilot's chart workspace shows (M75 W-3,
app/charts/workspace_reading.py): the signal engine's when it reads this chart for him (flag `trade_engine`), else ATP's
own chart readers. Each line says which of the two it comes from; the two are never mixed in one line.

Which timeframes (M76Q-7 default): the workspace's open timeframe when the ticket opens while the workspace shows the same
underlying; otherwise his signal timeframe (only when he has saved one, as the engine runner's user) and the daily. One
reading per timeframe, and every line names its timeframe: a line compares the order with ONE timeframe's own trend, its
own areas (an area whose own timeframe is that one) and its own last closed bar - timeframes are never merged.

The order's direction is a plain fact of the ticket: buy CE / sell PE / buy FUT = bullish, buy PE / sell CE / sell FUT =
bearish. Line types (each its own switch, ON as information - never an extra click unless "ask me" is on):
  trend_conflict  that timeframe's trend runs against the order's direction;
  counter_trend   the same, with the newest BOS / CHoCH of that chart when it is not a CHoCH the order's way (it takes the
                  trend line's place for that timeframe, so one fact is one line); not given by the source: no line;
  area_conflict   the last close sits inside an area of the other side;
  strike_in_area  a sold option's strike lies inside an area;
  room            the nearest area of the other side in the order's direction, in points from the last close.
A false-break state has no public phrase in either source today (M75Q-4: ATP's states fresh / tested / flipped, the
engine's public tokens fresh / tested / broken / flipped), so that type does not exist yet (amendment 1).

No advice: one line of facts with numbers, no judgement word. No look-ahead: closed bars only, as the workspace reads them.
Never in a line: a model id, an area id, the engine's name / version / settings or its internal invalidation reference.
Read-only: nothing here places, refuses, changes or delays an order or an exit.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
from typing import Any

FLAG = "copilot_workspace"
STRUCTURE_IDS: tuple[str, ...] = ("counter_trend", "trend_conflict", "area_conflict", "strike_in_area", "room")
DAILY = "1d"
SOURCE_LABEL = {"atp": "ATP chart readers", "engine": "your signal engine"}
SETUP_OF: dict[tuple[str, str], str] = {("BUY", "CE"): "bullish", ("SELL", "PE"): "bullish", ("BUY", "FUT"): "bullish",
                                        ("SELL", "CE"): "bearish", ("BUY", "PE"): "bearish", ("SELL", "FUT"): "bearish"}
_TF_ALIASES = {"60m": "1h", "day": "1d", "week": "1w"}


def setup_of(side: str, right: str) -> str | None:
    """The order's own direction (a fact of the ticket), or None."""
    return SETUP_OF.get((side.upper(), right.upper()))


def norm_tf(tf: str) -> str:
    return _TF_ALIASES.get(str(tf), str(tf))


def _tf_words(tf: str) -> str:
    from app.charts.workspace_reading import TF_WORDS
    return TF_WORDS.get(tf, tf)


def _trend_side(direction: str) -> str | None:
    d = str(direction or "")
    return "bullish" if d.startswith("up") else "bearish" if d.startswith("down") else None


def _area_words(a: Mapping[str, Any], tf: str) -> str:
    return f"{a['side']} area ({_tf_words(tf)}, {a['low_text']} - {a['high_text']})"


def _line(wid: str, text: str, source: str, tf: str, **facts: Any) -> dict[str, Any]:
    return {"id": wid, "severity": "fact", "text": f"{text} · {SOURCE_LABEL.get(source, SOURCE_LABEL['atp'])}",
            "facts": {"timeframe": tf, "source": source, **facts}}


def lines_for(reading: Mapping[str, Any], tf: str, setup: str, *, side: str, right: str, strike: float | None,
              counter_on: bool = True) -> list[dict[str, Any]]:
    """The structure lines of ONE timeframe's reading (the workspace's annotation form). Pure.
    `counter_on`: his counter-trend switch - on, that line takes the trend line's place for this timeframe."""
    from app.charts import price_action_layer as pal
    from app.charts.workspace_reading import TREND_WORDS
    source = str(reading.get("source") or "atp")
    tf = norm_tf(tf)
    tfw = _tf_words(tf)
    out: list[dict[str, Any]] = []
    trend = next((t for t in reading.get("trend") or [] if norm_tf(t.get("timeframe")) == tf), None)
    direction = str(trend.get("direction") or "") if trend else ""
    if trend and _trend_side(direction) not in (None, setup):
        head = f"{tfw[0].upper()}{tfw[1:]} trend: {TREND_WORDS.get(direction, direction)}"
        events = [e for e in reading.get("events") or [] if norm_tf(e.get("timeframe")) == tf]
        known = "events" not in (reading.get("not_given") or [])
        toward = "up" if setup == "bullish" else "down"
        last = events[-1] if events else None
        if counter_on and known and not (last and last.get("type") == "CHOCH" and last.get("direction") == toward):
            event = (f"last structure event: {last['name']} {last.get('direction') or ''}".rstrip()
                     + (f" at {last['price_text']}" if last.get("price_text") else "")) if last else "no BOS or CHoCH read on this chart"
            out.append(_line("counter_trend", f"{head} · {event} · your setup: {setup}", source, tf, trend=direction,
                             last_event=(last or {}).get("type")))
        else:
            out.append(_line("trend_conflict", f"{head} · your setup: {setup}", source, tf, trend=direction))
    close = reading.get("last_close")
    areas = [a for a in reading.get("areas") or [] if norm_tf(a.get("timeframe")) == tf and a.get("side") in ("buyer", "seller")]
    if close is not None:
        close = float(close)
        other = "seller" if setup == "bullish" else "buyer"
        inside = [a for a in areas if a["side"] == other and float(a["low"]) <= close <= float(a["high"])]
        if inside:
            a = inside[0]
            out.append(_line("area_conflict", f"Last close {pal.price_text(close)} inside a {_area_words(a, tf)} · your setup: {setup}",
                             source, tf, low=a["low"], high=a["high"], last_close=close))
        if setup == "bullish":
            ahead = sorted((a for a in areas if a["side"] == "seller" and float(a["low"]) > close), key=lambda a: float(a["low"]))
            gap = (float(ahead[0]["low"]) - close) if ahead else None
            where = "above"
        else:
            ahead = sorted((a for a in areas if a["side"] == "buyer" and float(a["high"]) < close), key=lambda a: -float(a["high"]))
            gap = (close - float(ahead[0]["high"])) if ahead else None
            where = "below"
        if ahead and gap is not None:
            a = ahead[0]
            out.append(_line("room", f"Next {_area_words(a, tf)}: {pal.price_text(gap)} points {where} the last close {pal.price_text(close)}",
                             source, tf, points=round(gap, 2), low=a["low"], high=a["high"], last_close=close))
    if side.upper() == "SELL" and right.upper() in ("CE", "PE") and strike is not None:
        hit = [a for a in areas if float(a["low"]) <= float(strike) <= float(a["high"])]
        if hit:
            a = hit[0]
            out.append(_line("strike_in_area", f"Short {pal.price_text(float(strike))} {right.upper()} inside a {_area_words(a, tf)}",
                             source, tf, strike=float(strike), low=a["low"], high=a["high"]))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Which timeframes, and the readings (request time)
# ---------------------------------------------------------------------------------------------------------------------
async def signal_timeframe(session: Any, user: Any) -> str | None:
    """His own signal timeframe: only one he saved (the engine runner's settings, when he is its user); else None."""
    from app.charts import price_action_layer as pal
    from app.workers import engine_runner as R
    raw = (await R._stored_settings(session)).get(str(user.tenant_id))   # noqa: SLF001 - the runner's own reader
    if not isinstance(raw, Mapping) or raw.get("user_id") != user.id or not raw.get("signal_timeframe"):
        return None
    tf = norm_tf(str(raw["signal_timeframe"]))
    return tf if tf in pal.DEFAULT_DAYS else None


def chart_key(underlying: str) -> str | None:
    """The lake key the workspace reads for this underlying (the Copilot's own mapping; spot for structure)."""
    from app.ai.tools.copilot import lake_key
    from app.price_action.zones.store import normalise_key
    try:
        return normalise_key(lake_key(underlying))
    except ValueError:
        return None


async def timeframes_for(session: Any, user: Any, key: str, chart_instrument: str | None, chart_tf: str | None) -> list[str]:
    """M76Q-7 default: the workspace's open timeframe (same underlying), else his signal timeframe and the daily."""
    from app.charts import price_action_layer as pal
    from app.price_action.zones.store import normalise_key
    if chart_instrument and chart_tf and norm_tf(chart_tf) in pal.DEFAULT_DAYS:
        try:
            if normalise_key(chart_instrument) == key:
                return [norm_tf(chart_tf)]
        except ValueError:
            pass
    own = await signal_timeframe(session, user)
    return list(dict.fromkeys([t for t in (own, DAILY) if t]))


async def findings(factory: Any, user: Any, d: Any, s: Any, now: datetime) -> list[dict[str, Any]]:
    """Every structure line for the draft `d` (a pretrade_checks.Draft) under his switches `s`. Needs `copilot_workspace`
    (the workspace's reading); off, no structure line exists."""
    from app.charts import read_chart
    from app.platform.controls import flag_enabled
    setup = setup_of(d.side, d.right)
    key = chart_key(d.underlying)
    if setup is None or key is None:
        return []
    async with factory() as session:
        if not await flag_enabled(session, FLAG, user.tenant_id):
            return []
        tfs = await timeframes_for(session, user, key, getattr(d, "chart_instrument", None), getattr(d, "chart_tf", None))

    async def one(tf: str) -> list[dict[str, Any]]:
        async with factory() as session:
            reading = await read_chart.workspace_annotations(session, user, key, tf, now)
        if not reading:
            return []
        return lines_for(reading, tf, setup, side=d.side, right=d.right, strike=d.strike, counter_on=s.enabled("counter_trend"))

    parts = await asyncio.gather(*(one(tf) for tf in tfs), return_exceptions=True)
    out: dict[str, dict[str, Any]] = {}                # one line per fact (the first timeframe that gives it)
    for part in parts:
        if isinstance(part, BaseException):
            continue                                   # a reading that fails says nothing; it never blocks
        for f in part:
            out.setdefault(f["text"], f)
    return list(out.values())

