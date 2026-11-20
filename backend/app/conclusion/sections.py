"""TERM-CON C0: the panel's sections, built from what the existing reads return (pure: no database, no clock).

Every line is `{text, tone, kind, source, as_of}`:
* kind `signal` - a market reading in structure words; it must pass `signals.language.check(..., "signal_own")` or it
  is left out (never rewritten). The area may be shown as a price range on his own Terminal (CLAUDE.md 3).
* kind `data` - a market figure the chain panel already shows (PCR, max pain, the days left).
* kind `record` - what happened to his own positions and orders, as the Positions / Orders panels show it.
tone: `up` / `down` / `neutral` / `warn` (the token colours on the panel; never the only carrier of the meaning).

Each section: `{key, title, status, source, as_of, lines, note}`; status `ok` / `stale` / `na` (not available now, with a
plain note) / `off` (its feature is off - the panel hides it). A reading direction per source (`readings`) is taken AS IS
from that source's own output: no threshold, no weighting is added here (DECISIONS 20 Nov, TERM-CON).
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.signals import language as L

Line = dict[str, Any]
Section = dict[str, Any]

SOURCE_CHART = "Chart reading (closed bars)"
SOURCE_OI = "OI banner (stored slots)"
SOURCE_POSITIONS = "Your positions"
SOURCE_ORDERS = "Your orders today"
SOURCE_BROKER = "Broker check"

STRUCTURE_WORDS = {"up": "up", "up (weak)": "up, weakening", "down": "down", "down (weak)": "down, weakening",
                   "range": "a range", "none": "not formed yet"}
EVENT_WORDS = {"BOS": "a break of structure", "CHOCH": "a change of character", "REVERSAL_CONFIRMED": "a confirmed reversal",
               "RECOVERY": "a recovery", "RANGE_START": "a range forming", "RANGE_EXIT_UP": "a move out of the range",
               "RANGE_EXIT_DOWN": "a move out of the range", "INIT_TREND": "the first trend", "INIT_RANGE": "the first range"}
AREA_SIDE = {"demand": "buyer", "supply": "seller", "buyer": "buyer", "seller": "seller"}
OI_DIRECTION = {"BULLISH": "up", "BEARISH": "down", "MIXED": "mixed", "SIDEWAYS": "range", "NEUTRAL": "none"}
SIDE_DIRECTION = {"bullish": "up", "bearish": "down"}
SIDE_WORDS = {"bullish": "gains if the price rises", "bearish": "gains if the price falls", "neutral": "no single direction"}

FILLED = {"FILLED", "POSITION_OPEN"}
WORKING = {"CREATED", "VALIDATING", "RISK_CHECK", "SUBMITTED", "PENDING", "PARTIAL_FILL"}
REFUSED = {"REJECTED", "FAILED"}

NA_NOTES = {
    "chart": "The chart reading is not available for this symbol right now.",
    "oi": "No OI reading is stored for this symbol today.",
}


def line(text: str, *, tone: str = "neutral", kind: str = "record", source: str, as_of: str | None) -> Line:
    return {"text": text, "tone": tone, "kind": kind, "source": source, "as_of": as_of}


def section(key: str, title: str, source: str, *, status: str = "ok", as_of: str | None = None,
            lines: Sequence[Line] = (), note: str | None = None) -> Section:
    return {"key": key, "title": title, "status": status, "source": source, "as_of": as_of, "lines": list(lines), "note": note}


def off(key: str, title: str, source: str) -> Section:
    return section(key, title, source, status="off")


def na(key: str, title: str, source: str, note: str | None = None) -> Section:
    return section(key, title, source, status="na", note=note or NA_NOTES.get(key, "Not available right now."))


def signal_lines(lines: Iterable[Line], symbol: str) -> list[Line]:
    """The signal lines that pass the signal language for his own symbol; the others are left out (never rewritten)."""
    own = [symbol] if symbol else []
    return [x for x in lines if x["kind"] != "signal" or not L.check(x["text"], "signal_own", own_symbols=own)]


def _tf_words(tf: str) -> str:
    return L.TIMEFRAMES.get(tf, tf)


def structure_direction(value: object) -> str:
    """up / down / range / none from the chart reading's structure direction ("up (weak)" is up)."""
    text = str(value or "none")
    return "up" if text.startswith("up") else "down" if text.startswith("down") else "range" if text == "range" else "none"


def _tone(direction: str) -> str:
    return {"up": "up", "down": "down"}.get(direction, "neutral")


# --- chart -----------------------------------------------------------------------------------------------------------

def chart_section(read: Mapping[str, Any], symbol: str, tf: str) -> Section:
    """From GET /api/charts/read (app/charts/read_chart.read_closed): structure, the Daily trend, the stored areas and
    the commitment candle - as of the last CLOSED bar only."""
    as_of = read.get("as_of")
    if not read.get("bars"):
        return na("chart", "Chart", SOURCE_CHART, "No closed bars for this chart yet.")
    out: list[Line] = []
    st = read.get("structure") or {}
    direction = str(st.get("direction") or "none")
    text = f"{_tf_words(tf)} structure: {STRUCTURE_WORDS.get(direction, 'not formed yet')}"
    last = st.get("last_event") or {}
    if last.get("type") in EVENT_WORDS:
        text += f"; the last event was {EVENT_WORDS[last['type']]}"
        if last.get("direction") in ("up", "down"):
            text += f" {last['direction']}"
    out.append(line(text + ".", tone=_tone(structure_direction(direction)), kind="signal", source=SOURCE_CHART, as_of=as_of))
    pull = read.get("pullback") or {}
    if pull.get("available") and pull.get("words"):
        trend = str(pull.get("trend") or "")
        out.append(line(str(pull["words"]), tone=_tone({"UP": "up", "DOWN": "down"}.get(trend, "none")), kind="signal",
                        source="Daily trend (previous close)", as_of=pull.get("as_of")))
    for area in read.get("areas") or []:
        side = AREA_SIDE.get(str(area.get("role") or "").lower())
        if side is None or area.get("low") is None or area.get("high") is None:
            continue
        words = L.area_label(side, float(area["low"]), float(area["high"]))
        if area.get("state_words"):
            words += f" ({area['state_words']})"
        out.append(line(words + ".", tone="up" if side == "buyer" else "down", kind="signal", source="Stored areas", as_of=as_of))
    commit = read.get("commitment") or None
    area = (commit or {}).get("area") or {}
    if commit and area.get("low") is not None and area.get("high") is not None:
        side = AREA_SIDE.get(str(commit.get("role") or "").lower())
        if side is not None:
            out.append(line(f"Commitment candle at the {L.area_label(side, float(area['low']), float(area['high']))}.",
                            tone="up" if side == "buyer" else "down", kind="signal", source=SOURCE_CHART,
                            as_of=commit.get("bar_end") or as_of))
    return section("chart", "Chart", SOURCE_CHART, as_of=as_of, lines=signal_lines(out, symbol))


def chart_reading(read: Mapping[str, Any], tf: str) -> dict[str, Any] | None:
    if not read.get("bars"):
        return None
    direction = structure_direction((read.get("structure") or {}).get("direction"))
    return {"source": "chart", "label": f"{_tf_words(tf)} structure", "tf": tf, "direction": direction, "as_of": read.get("as_of")}


# --- OI --------------------------------------------------------------------------------------------------------------

def _figure(value: Any) -> str | None:
    try:
        return L.group_in(float(value))
    except (TypeError, ValueError):
        return None


def oi_section(resp: Mapping[str, Any], expiry: str | None) -> Section:
    """From GET /api/option-chain/{u}/banner: the latest stored slot's banner in neutral fact words (oi_words)."""
    from app.option_chain import oi_words
    b = resp.get("banner")
    if not b:
        return na("oi", "OI", SOURCE_OI, str(resp.get("message") or NA_NOTES["oi"]))
    as_of = b.get("slot")
    direction = OI_DIRECTION.get(str(b.get("direction") or "NEUTRAL"), "none")
    out = [line(f"OI: {oi_words.direction_words(b.get('direction'))}.", tone=_tone(direction), kind="data", source=SOURCE_OI, as_of=as_of)]
    if b.get("fact_message"):
        out.append(line(str(b["fact_message"]), kind="data", source=SOURCE_OI, as_of=as_of))
    pcr = _figure(b.get("pcr"))
    if pcr is not None:
        out.append(line(f"PCR {pcr} ({oi_words.pcr_band_words(b.get('pcr_band'))}).", kind="data", source=SOURCE_OI, as_of=as_of))
    pain = _figure(b.get("max_pain"))
    if pain is not None:
        out.append(line(f"Max pain strike {pain}.", kind="data", source=SOURCE_OI, as_of=as_of))
    if b.get("dte") is not None:
        out.append(line(f"Sessions to expiry: {int(b['dte'])}.", kind="data", source=SOURCE_OI, as_of=as_of))
    note = None
    if expiry and b.get("expiry") and str(b["expiry"]) != str(expiry):
        note = f"The OI banner reads the {b['expiry']} expiry; the Terminal shows {expiry}."
    status = "stale" if resp.get("stale") else "ok"
    if status == "stale" and resp.get("age_minutes") is not None:
        note = ((note + " ") if note else "") + f"Last OI reading {resp['age_minutes']} minutes ago."
    return section("oi", "OI", SOURCE_OI, status=status, as_of=as_of, lines=out, note=note)


def oi_reading(resp: Mapping[str, Any]) -> dict[str, Any] | None:
    b = resp.get("banner")
    if not b:
        return None
    return {"source": "oi", "label": "OI", "tf": None, "direction": OI_DIRECTION.get(str(b.get("direction") or "NEUTRAL"), "none"),
            "as_of": b.get("slot"), "stale": bool(resp.get("stale"))}


# --- his own positions -----------------------------------------------------------------------------------------------

def matches(symbol: str, name: str | None) -> bool:
    """A row belongs to the Terminal's symbol: the symbol itself or one of its contracts (NIFTY -> NIFTY25NOV25800CE),
    never a longer name that starts the same (NIFTY is not NIFTYNXT50, BANKNIFTY is not NIFTY)."""
    sym, text = (symbol or "").strip().upper(), (name or "").strip().upper()
    return bool(sym) and (text == sym or re.match(rf"^{re.escape(sym)}\d", text) is not None)


def _money(value: float | None) -> str:
    sign = "+" if value is not None and value > 0 else ""
    return sign + L.group_in(float(value or 0))


def positions_section(rows: Sequence[Mapping[str, Any]], symbol: str, as_of: str | None) -> Section:
    """From the Copilot's position rows (ai/tools/copilot_trades.group_rows, PAPER and LIVE alike) for this symbol."""
    mine = [r for r in rows if matches(symbol, r.get("underlying")) or matches(symbol, r.get("symbol"))]
    if not mine:
        return section("positions", "My positions", SOURCE_POSITIONS, as_of=as_of,
                       lines=[line(f"No open position on {symbol}.", source=SOURCE_POSITIONS, as_of=as_of)])
    out: list[Line] = []
    for r in mine:
        what = f"{r.get('legs')} legs" if r.get("kind") == "STRUCTURE" else str(r.get("symbol"))
        text = f"{r.get('mode')} · {what} · {SIDE_WORDS.get(str(r.get('side')), 'no single direction')}"
        pnl = r.get("marked_pnl")
        text += f" · P&L {_money(pnl)}" if pnl is not None else " · not priced yet"
        if r.get("kind") != "STRUCTURE":
            text += (f" · loss if your stop is hit {L.group_in(float(r['loss_if_stop_hit']))}"
                     if r.get("loss_if_stop_hit") is not None and float(r["loss_if_stop_hit"]) > 0
                     else "" if r.get("stop_now") else " · no stop in your settings")
        tone = "up" if pnl is not None and pnl > 0 else "down" if pnl is not None and pnl < 0 else "neutral"
        out.append(line(text + ".", tone=tone, source=SOURCE_POSITIONS, as_of=r.get("mark_time") or as_of))
    risks = [float(r["loss_if_stop_hit"]) for r in mine if r.get("loss_if_stop_hit") is not None and float(r["loss_if_stop_hit"]) > 0]
    if risks:
        out.append(line(f"Loss if all your stops are hit: {L.group_in(sum(risks))}.", tone="warn", source=SOURCE_POSITIONS, as_of=as_of))
    return section("positions", "My positions", SOURCE_POSITIONS, as_of=as_of, lines=out)


def positions_reading(rows: Sequence[Mapping[str, Any]], symbol: str, as_of: str | None) -> dict[str, Any] | None:
    mine = [r for r in rows if matches(symbol, r.get("underlying")) or matches(symbol, r.get("symbol"))]
    if not mine:
        return None
    up = sum(1 for r in mine if r.get("side") == "bullish")
    down = sum(1 for r in mine if r.get("side") == "bearish")
    direction = "up" if up > down else "down" if down > up else ("mixed" if up else "none")
    return {"source": "positions", "label": "Positions", "tf": None, "direction": direction, "as_of": as_of}


# --- his orders today ------------------------------------------------------------------------------------------------

def orders_section(orders: Sequence[Mapping[str, Any]], symbol: str, as_of: str | None) -> Section:
    """From GET /api/orders?mine=true&today=true, this symbol's orders: counts by outcome, and each refused one with
    the reason the order record gives."""
    mine = [o for o in orders if matches(symbol, o.get("symbol"))]
    if not mine:
        return section("orders", "My orders", SOURCE_ORDERS, as_of=as_of,
                       lines=[line(f"No order on {symbol} today.", source=SOURCE_ORDERS, as_of=as_of)])
    filled = sum(1 for o in mine if o.get("status") in FILLED)
    working = sum(1 for o in mine if o.get("status") in WORKING)
    cancelled = sum(1 for o in mine if o.get("status") == "CANCELLED")
    parts = [f"{filled} filled" if filled else "", f"{working} working" if working else "", f"{cancelled} cancelled" if cancelled else ""]
    out: list[Line] = []
    if any(parts):
        out.append(line("Today: " + " · ".join(p for p in parts if p) + ".", source=SOURCE_ORDERS, as_of=as_of))
    for o in mine:
        if o.get("status") in REFUSED:
            reasons = [str(r) for r in (o.get("reasons") or []) if str(r).strip()]
            why = f": {reasons[-1]}" if reasons else ""
            out.append(line(f"{o.get('mode')} order {o.get('symbol')} {str(o.get('status')).lower()}{why}", tone="warn",
                            source=SOURCE_ORDERS, as_of=o.get("updated_at") or o.get("created_at")))
    return section("orders", "My orders", SOURCE_ORDERS, as_of=as_of, lines=out)


def refused_orders(orders: Sequence[Mapping[str, Any]], symbol: str) -> list[Mapping[str, Any]]:
    return [o for o in orders if matches(symbol, o.get("symbol")) and o.get("status") in REFUSED]


# --- his own signals today ------------------------------------------------------------------------------------------

SOURCE_SIGNALS = "Your signals today"
SIGNAL_SIDE = {"LONG": "an upside", "SHORT": "a downside"}
SIGNAL_DIRECTION = {"LONG": "up", "SHORT": "down"}
STATUS_WORDS = {"waiting": "waiting for you", "taken": "taken", "skipped": "skipped", "expired": "expired",
                "not_placed": "not placed", "snoozed": "snoozed"}


def signals_section(items: Sequence[Mapping[str, Any]], symbol: str, as_of: str | None) -> Section:
    """From GET /api/signals/feed (today): his own proposals, scan setups and fills on this symbol, newest first, in the
    signal language (the side of the market, which of his sources, its state; the feed's "why" lines that pass)."""
    mine = [i for i in items if matches(symbol, i.get("symbol"))]
    if not mine:
        return section("signals", "My signals", SOURCE_SIGNALS, as_of=as_of,
                       lines=[line(f"No signal on {symbol} today.", source=SOURCE_SIGNALS, as_of=as_of)])
    out: list[Line] = []
    for i in mine[:5]:
        name = str((i.get("source") or {}).get("name") or "").strip()
        whose = name if name and not L.check(name, "signal_own", own_symbols=[symbol]) else "Your strategy"
        side = SIGNAL_SIDE.get(str(i.get("direction") or ""), "a")
        state = STATUS_WORDS.get(str(i.get("status") or ""), "")
        text = f"{whose}: {side} setup on {symbol}" + (f", {state}" if state else "") + "."
        tone = {"LONG": "up", "SHORT": "down"}.get(str(i.get("direction") or ""), "neutral")
        out.append(line(text, tone=tone, kind="signal", source=SOURCE_SIGNALS, as_of=i.get("at")))
        for why in L.shown_lines([str(r) for r in (i.get("reasons") or [])], "signal_own", own_symbols=[symbol])[:2]:
            out.append(line(why, tone=tone, kind="signal", source=SOURCE_SIGNALS, as_of=i.get("at")))
    return section("signals", "My signals", SOURCE_SIGNALS, as_of=as_of, lines=signal_lines(out, symbol))


def signals_reading(items: Sequence[Mapping[str, Any]], symbol: str) -> dict[str, Any] | None:
    """The newest of his signals on this symbol that has a side."""
    for i in items:
        if matches(symbol, i.get("symbol")) and str(i.get("direction") or "") in SIGNAL_DIRECTION:
            return {"source": "signals", "label": "Your signals", "tf": None,
                    "direction": SIGNAL_DIRECTION[str(i["direction"])], "as_of": i.get("at")}
    return None


# --- risk and events (C1b) ------------------------------------------------------------------------------------------

SOURCE_RISK = "Kill switch and your risk settings"
SOURCE_EVENTS = "Market events (next 7 days)"
GUARD_STATE = {"paused": "new entries paused by your drawdown settings", "reduced": "entry size reduced by your drawdown settings"}
EVENT_ACTION = {"BLOCK": "new entries refused in its window (your setting)", "SIZE_CUT": "entry size cut in its window (your setting)"}


def risk_section(kill: Mapping[str, Any], guardian: Mapping[str, Any] | None, symbol: str, as_of: str | None) -> Section:
    """The kill switch (exits always run, ADR-0004) and only what HIS risk settings already do now: a drawdown pause or
    size cut per mode, a cool-down on this symbol. Nothing is added when he has set nothing (DEF-0)."""
    out = [line(str(kill.get("message") or "New orders are allowed."), tone="warn" if kill.get("engaged") else "neutral",
                source="Kill switch", as_of=as_of)]
    for mode, row in ((guardian or {}).get("modes") or {}).items():
        state = str((row or {}).get("state") or "normal")
        if state in GUARD_STATE:
            out.append(line(f"{mode}: {GUARD_STATE[state]}.", tone="warn", source=SOURCE_RISK, as_of=(guardian or {}).get("as_of")))
        for c in (row or {}).get("cooldowns") or []:
            if matches(symbol, c.get("underlying")):
                out.append(line(f"{mode}: cool-down on {symbol}, {int(c.get('minutes_left') or 0)} minutes left (your setting).",
                                tone="warn", source=SOURCE_RISK, as_of=(guardian or {}).get("as_of")))
    return section("risk", "Risk", SOURCE_RISK, as_of=as_of, lines=out)


def events_section(events: Sequence[Mapping[str, Any]], symbol: str, as_of: str | None) -> Section:
    """Market events for every symbol or for this one (global and his organisation's), as the Risk page lists them."""
    mine = [e for e in events if str(e.get("underlying") or "*") == "*" or matches(symbol, e.get("underlying"))]
    if not mine:
        return section("events", "Events", SOURCE_EVENTS, as_of=as_of,
                       lines=[line("No market event in the next 7 days.", kind="data", source=SOURCE_EVENTS, as_of=as_of)])
    out: list[Line] = []
    for e in mine[:5]:
        text = f"{e.get('event_date')}" + (f" {e['start_time']}" if e.get("start_time") else "") + f" · {e.get('description') or e.get('kind')}"
        action = EVENT_ACTION.get(str(e.get("action") or ""))
        out.append(line(text + (f" · {action}" if action else "") + ".", tone="warn" if action else "neutral", kind="data",
                        source=SOURCE_EVENTS, as_of=e.get("created_at")))
    return section("events", "Events", SOURCE_EVENTS, as_of=as_of, lines=out)


# --- the broker ------------------------------------------------------------------------------------------------------

def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


def broker_section(status: Mapping[str, Any]) -> Section:
    """From GET /api/reconciliation/status: whether LIVE entries wait for a broker check (Phase G1). Exits always run."""
    since, last = _iso(status.get("broker_uncertain_since")), _iso(status.get("last_reconciled_at"))
    if status.get("broker_uncertain"):
        text = "Broker state uncertain" + (f" since {since}" if since else "") + "; new LIVE entries wait for a broker check. Exits keep running."
        return section("broker", "Broker", SOURCE_BROKER, as_of=since,
                       lines=[line(text, tone="warn", source=SOURCE_BROKER, as_of=since)], note=status.get("broker_uncertain_reason"))
    text = "Books agree with your broker" + (f" (last check {last})." if last else ".")
    return section("broker", "Broker", SOURCE_BROKER, as_of=last, lines=[line(text, source=SOURCE_BROKER, as_of=last)])


def needs_you(orders: Sequence[Mapping[str, Any]], broker: Mapping[str, Any] | None, symbol: str, *,
              kill: Mapping[str, Any] | None = None, positions: Sequence[Mapping[str, Any]] = (),
              today: str | None = None) -> list[dict[str, Any]]:
    """Facts that need him: a refused order today on this symbol, the broker check pending, new orders stopped, a
    position on this symbol that expires today. No rule is added."""
    out: list[dict[str, Any]] = []
    refused = refused_orders(orders, symbol)
    if refused:
        n = len(refused)
        out.append({"key": "orders", "text": f"{n} order{'s' if n > 1 else ''} not placed today", "action": "orders"})
    if broker and broker.get("broker_uncertain"):
        out.append({"key": "broker", "text": "Broker check pending", "action": "broker_check"})
    if kill and kill.get("engaged"):
        out.append({"key": "kill_switch", "text": "New orders stopped (exits still work)", "action": None})
    if today:
        expiring = [p for p in positions if (matches(symbol, p.get("underlying")) or matches(symbol, p.get("symbol")))
                    and str(p.get("expiry") or "")[:10] == today]
        if expiring:
            n = len(expiring)
            out.append({"key": "expiry", "text": f"{n} position{'s' if n > 1 else ''} expire{'' if n > 1 else 's'} today",
                        "action": "positions"})
    return out


__all__ = ["chart_section", "chart_reading", "oi_section", "oi_reading", "positions_section", "positions_reading",
           "orders_section", "signals_section", "signals_reading", "risk_section", "events_section", "broker_section", "needs_you", "matches", "signal_lines", "structure_direction", "off", "na"]
