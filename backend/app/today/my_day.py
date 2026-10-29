"""AUD-T1: the "My day" read - one entry per open position of the caller, plus what changed since the previous session.

Every fact comes from what ATP already holds; a source with nothing says so in plain words, never a made-up value:

* positions: the caller's own open trades (tenant AND user; PAPER and LIVE alike), read only;
* areas: the stored zone snapshot of the underlying (`price_action.zones.read.read_as_of`, the chart layer's own read:
  the organisation's scope, else the platform's; the member's own holder scope when newer), live areas only. Measured
  from the held strike of an option, else from the position's own stop. No snapshot -> one plain sentence;
* the area's change: ONLY the lifecycle event the zone store already recorded on that area (`last_event`: touched,
  gapped past, closed through) when it happened in the previous session or later - no new reading logic here;
* expiry: trading sessions to the expiry on the exchange calendar (`calendar.trading_days_until`, holidays included);
* gap: the underlying's newest stored Daily bar's open against the close before it (the shared daily history);
* events: today's events-calendar rows (platform-wide and the organisation's own) for these underlyings or the market;
* chip: ONLY from the user's own stop - the share of the room between his entry and his stop still left at the
  position's last mark, against MY_DAY_WATCH_ROOM_LEFT_PCT / MY_DAY_NEAR_ROOM_LEFT_PCT (settings). No stop -> no chip.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import MarketEventRecord, TradeRecord
from app.market_data.calendar import IST, family_holidays, previous_session_close, to_ist, trading_days_until

logger = logging.getLogger(__name__)

NOTE = "Information from your own positions and the market data ATP holds. It does not tell you what to trade."
NO_STOP = "No stop set"
NO_MARK = "No recent price for this position yet"
STOP_PAST_ENTRY = "Your stop is at or past your entry; room not measured"
CHIP_LABELS = {"safe": "Safe", "watch": "Watch", "near": "Near invalidation"}
# The zone store's own lifecycle events (price_action.zones.lifecycle.ZoneEvent) in plain words.
ZONE_EVENT_WORDS = {"TOUCH": "was tested", "GAP_THROUGH": "was gapped past", "FLIP": "was closed through and held beyond",
                    "STRUCTURAL_BREAK": "was broken"}
MAX_POSITIONS = 200            # one read bounds the card; an account holds far fewer open positions
DAILY_TIMEFRAME = "day"        # the shared daily history's timeframe (context_panel.service.TIMEFRAME)
READ_FAILED: List[Dict[str, Any]] = []   # sentinel: a zone read that failed (said so, never "none stored")
GAP_LOOKBACK_DAYS = 10         # calendar days read back for the two newest Daily bars (covers long holiday runs)


def side_of(direction: str) -> int:
    return -1 if str(direction or "").upper() in ("SHORT", "SELL") else 1


def money_text(x: float) -> str:
    return f"{float(x):,.2f}"


def thresholds() -> Dict[str, float]:
    return {"watch_room_left_pct": float(config.MY_DAY_WATCH_ROOM_LEFT_PCT), "near_room_left_pct": float(config.MY_DAY_NEAR_ROOM_LEFT_PCT)}


def chip_for(direction: str, entry: Optional[float], stop: Optional[float], mark: Optional[float]) -> Dict[str, Any]:
    """The state chip from the user's own stop only. {chip, label, room_left_pct, text}: chip None with the plain reason
    when there is no stop, no mark, or the room cannot be measured (stop at or past the entry)."""
    if stop is None or not float(stop) > 0:
        return {"chip": None, "label": None, "room_left_pct": None, "text": NO_STOP}
    if mark is None or entry is None:
        return {"chip": None, "label": None, "room_left_pct": None, "text": NO_MARK}
    side = side_of(direction)
    room = (float(entry) - float(stop)) * side
    left = (float(mark) - float(stop)) * side
    if left <= 0:                                      # the mark has reached the user's own stop
        return {"chip": "near", "label": CHIP_LABELS["near"], "room_left_pct": 0.0, "text": None}
    if room <= 0:
        return {"chip": None, "label": None, "room_left_pct": None, "text": STOP_PAST_ENTRY}
    pct = round(min(left / room, 1.0) * 100.0, 1)
    t = thresholds()
    chip = "near" if pct <= t["near_room_left_pct"] else "watch" if pct <= t["watch_room_left_pct"] else "safe"
    return {"chip": chip, "label": CHIP_LABELS[chip], "room_left_pct": pct, "text": None}


def expiry_text(sessions: int) -> str:
    if sessions < 0:
        return "Expiry date has passed"
    if sessions == 0:
        return "Expires today"
    if sessions == 1:
        return "Expires next session"
    return f"Expires in {sessions} sessions"


def strike_of(symbol: str) -> Optional[float]:
    from app.trading.routes import _strike_of
    return _strike_of(symbol)


def underlying_name(t: TradeRecord) -> str:
    kind = str(t.instrument_kind or "UNDERLYING").upper()
    return str(t.underlying_symbol if kind != "UNDERLYING" and t.underlying_symbol else t.symbol).strip().upper()


def lake_key_of(name: str, exchange: Optional[str]) -> Optional[str]:
    """The shared history / zone key of an underlying ("NIFTY" -> NSE_INDEX:NIFTY50); None off the Indian exchanges."""
    if (exchange or "NSE").upper() not in ("NSE", "NFO", "BSE", "BFO", "NSE_INDEX", "BSE_INDEX", "NSE_EQ", "NSE_FO", "BSE_EQ", "BSE_FO"):
        return None
    from app.ai.tools.copilot import lake_key
    from app.instruments.master import underlying_of
    from app.price_action.zones.store import normalise_key
    try:
        return normalise_key(lake_key(underlying_of(name)))
    except ValueError:
        return None


def reference_of(t: TradeRecord) -> Tuple[Optional[float], Optional[str]]:
    """What the nearest area is measured from: the held strike of an option, else the position's own stop on the
    underlying (an option's underlying stop, or the position's stop when it is the underlying itself)."""
    if str(t.instrument_kind or "").upper() == "OPTION":
        strike = strike_of(t.symbol)
        if strike is not None:
            return strike, "strike"
        if t.underlying_stop_loss:
            return float(t.underlying_stop_loss), "stop"
        return None, None
    if str(t.instrument_kind or "UNDERLYING").upper() == "UNDERLYING":
        return (float(t.stop_loss), "stop") if t.stop_loss and float(t.stop_loss) > 0 else (None, None)
    return (float(t.underlying_stop_loss), "stop") if t.underlying_stop_loss else (None, None)


def _event_time(row: Dict[str, Any]) -> Optional[datetime]:
    ev = row.get("last_event") or {}
    t = ev.get("time") if isinstance(ev, dict) else None
    if isinstance(t, str):
        try:
            t = datetime.fromisoformat(t)
        except ValueError:
            return None
    if not isinstance(t, datetime):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def nearest_area(rows: Sequence[Dict[str, Any]], ref: float, since: Optional[date]) -> Optional[Dict[str, Any]]:
    """The live area nearest to `ref` (0 inside it; ties: the stronger, then zone_key) with where it sits from `ref`, and
    its own stored lifecycle event when that happened on or after `since` (IST date). Pure."""
    from app.price_action.zones.chart import distance_pct, is_live, zone_strength
    best: Optional[Tuple[Tuple[float, float, str], Dict[str, Any]]] = None
    for r in rows:
        if not is_live(r):
            continue
        key = (distance_pct(r, ref), -zone_strength(r), str(r.get("zone_key")))
        if best is None or key < best[0]:
            best = (key, r)
    if best is None:
        return None
    r = best[1]
    low, high = float(r["zone_low"]), float(r["zone_high"])
    where = "inside" if low <= ref <= high else ("above" if low > ref else "below")
    change = None
    ev = (r.get("last_event") or {}).get("event") if isinstance(r.get("last_event"), dict) else None
    at = _event_time(r)
    if ev in ZONE_EVENT_WORDS and at is not None and since is not None and at.astimezone(IST).date() >= since:
        change = {"event": ev, "time": at.astimezone(timezone.utc).isoformat(), "words": ZONE_EVENT_WORDS[ev]}
    return {"low": low, "high": high, "state": str(r.get("state")), "where": where, "distance_pct": round(best[0][0], 2),
            "range_text": f"{money_text(low)}-{money_text(high)}" if low != high else money_text(low), "change": change}


def area_text(area: Dict[str, Any], frm: str) -> str:
    what = "your strike" if frm == "strike" else "your stop"
    if area["where"] == "inside":
        return f"Your {frm} sits inside the nearest area."
    return f"Nearest area is {area['distance_pct']:.2f}% {area['where']} {what}."


def gap_of(bars: Sequence[Tuple[date, float, float]]) -> Optional[Dict[str, Any]]:
    """The newest Daily bar's open against the close of the bar before it: {session, gap_pct}. Bars are
    (session date, open, close), oldest first. None with fewer than two bars. Pure."""
    if len(bars) < 2:
        return None
    (day, opened, _), (_, _, prev_close) = bars[-1], bars[-2]
    if not prev_close:
        return None
    return {"session": day.isoformat(), "gap_pct": round((float(opened) / float(prev_close) - 1.0) * 100.0, 2)}


def gap_text(name: str, gap: Dict[str, Any], today: date) -> str:
    pct = float(gap["gap_pct"])
    day = date.fromisoformat(gap["session"])
    when = "today" if day == today else f"on {day.strftime('%a %d %b')}"
    if pct == 0:
        return f"{name} opened flat {when}."
    return f"{name} opened {abs(pct):.2f}% {'above' if pct > 0 else 'below'} the previous close {when}."


def event_text(e: MarketEventRecord) -> str:
    from app.rhythm.build import EVENT_LABELS
    from app.rhythm import words
    label = EVENT_LABELS.get(e.kind, "Market event")
    desc = words.clean(e.description)
    scope = f" ({e.underlying})" if e.underlying and e.underlying != "*" else ""
    window = f" {e.start_time}-{e.end_time}" if e.start_time and e.end_time else ""
    text = f"Today: {label}{scope}{window}"
    if desc and desc.lower() != label.lower() and len((text + desc).split()) <= 15:
        text += f" - {desc}"
    return text + "."


# --- the async readers (each fails on its own: a failure leaves its part out, said in plain words) ----------------------

@dataclass
class Viewer:
    id: int
    tenant_id: int


async def _safely(session: AsyncSession, what: str, coro: Any, default: Any) -> Any:
    try:
        return await coro
    except Exception:  # noqa: BLE001 - one source failing never fails the card
        logger.exception("my day: %s failed", what)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            logger.debug("my day: rollback after %s failed", what)
        return default


async def own_open_positions(session: AsyncSession, me: Viewer) -> List[TradeRecord]:
    return list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == me.tenant_id, TradeRecord.user_id == me.id, TradeRecord.exit_time.is_(None)).order_by(
        TradeRecord.entry_time.desc(), TradeRecord.id.desc()).limit(MAX_POSITIONS)))


async def _zone_rows(session: AsyncSession, me: Viewer, key: str, now: datetime) -> Optional[List[Dict[str, Any]]]:
    """The live rows of the newest stored snapshot (the chart layer's read); None when no snapshot exists."""
    from app.price_action.zones import read
    snap = await read.read_as_of(session, key, now, me.tenant_id, allow_platform_fallback=True)
    own = await read.read_holder(session, key, now, me.tenant_id, me.id)
    if own is not None and (snap is None or own.as_of >= snap.as_of):
        snap = own
    return None if snap is None else list(snap.rows)


async def _daily_bars(session: AsyncSession, key: str, today: date) -> List[Tuple[date, float, float]]:
    from app.market_lake.asof import candles_as_of
    lo = datetime.combine(today - timedelta(days=GAP_LOOKBACK_DAYS), datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    hi = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    rows = await candles_as_of(session, key, DAILY_TIMEFRAME, lo, hi)
    out = []
    for r in rows:
        ts = r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)
        out.append((ts.astimezone(IST).date(), float(r.open), float(r.close)))
    return out


async def _events_today(session: AsyncSession, me: Viewer, today: date, names: Set[str]) -> List[MarketEventRecord]:
    rows = list(await session.scalars(select(MarketEventRecord).where(
        MarketEventRecord.event_date == today,
        or_(MarketEventRecord.tenant_id.is_(None), MarketEventRecord.tenant_id == me.tenant_id)).order_by(
        MarketEventRecord.start_time, MarketEventRecord.id).limit(50)))
    from app.instruments.master import underlying_of
    wanted = {underlying_of(n) for n in names}
    return [e for e in rows if not e.underlying or e.underlying == "*" or underlying_of(e.underlying) in wanted]


async def build(session: AsyncSession, me: Viewer, now: datetime) -> Dict[str, Any]:
    from app.platform.controls import flag_enabled
    today = to_ist(now).date()
    holidays = await _safely(session, "holidays", family_holidays(session, "NSE"), set())
    prev = previous_session_close(now, holidays)
    since = prev.date() if prev is not None else None
    trades = await own_open_positions(session, me)
    for t in trades:                       # detached: a failing reader's rollback must not expire them (no lazy loads)
        session.expunge(t)
    zones_on = bool(await _safely(session, "zones flag", flag_enabled(session, "market_zones", me.tenant_id), False))

    zones: Dict[str, Optional[List[Dict[str, Any]]]] = {}
    positions: List[Dict[str, Any]] = []
    changes: List[str] = []
    names: Dict[str, Optional[str]] = {}
    for t in trades:
        name = underlying_name(t)
        key = lake_key_of(name, t.exchange)
        names.setdefault(name, key)
        chip = chip_for(t.direction, t.entry_price, t.stop_loss, t.mark_price)
        sessions = trading_days_until(today, t.expiry, holidays) if t.expiry else None
        ref, frm = reference_of(t)
        area: Optional[Dict[str, Any]] = None
        if not zones_on or key is None:
            area_line = "Structure areas are not available for this position."
        elif ref is None:
            area_line = "No strike or stop to measure the nearest area from."
        else:
            if key not in zones:
                zones[key] = await _safely(session, f"zones {key}", _zone_rows(session, me, key, now), READ_FAILED)
            rows = zones[key]
            area = nearest_area(rows, ref, since) if rows and rows is not READ_FAILED else None
            area_line = (area_text(area, frm) if area else "Structure areas could not be read just now." if rows is READ_FAILED
                         else f"No structure areas stored for {name} yet.")
            if area and area["change"]:
                line = f"{name} area {area['range_text']} {area['change']['words']} since the last session."
                if line not in changes:                    # several positions on one underlying: one line
                    changes.append(line)
        positions.append({
            "id": t.id, "symbol": t.symbol, "mode": t.mode, "side": "SHORT" if side_of(t.direction) < 0 else "LONG",
            "kind": str(t.instrument_kind or "UNDERLYING").upper(), "underlying": name, "deployment_id": t.deployment_id,
            "chip": chip["chip"], "chip_label": chip["label"], "room_left_pct": chip["room_left_pct"], "chip_text": chip["text"],
            "expiry": t.expiry.isoformat() if t.expiry else None, "expiry_sessions": sessions,
            "expiry_text": expiry_text(sessions) if sessions is not None else None,
            "area": None if area is None else {**area, "from": frm}, "area_text": area_line,
        })

    gaps: List[Dict[str, Any]] = []
    for name, key in names.items():
        if key is None:
            continue
        bars = await _safely(session, f"daily {key}", _daily_bars(session, key, today), [])
        gap = gap_of(bars)
        if gap is not None:
            gaps.append({"underlying": name, **gap, "text": gap_text(name, gap, today)})
    events = await _safely(session, "events", _events_today(session, me, today, set(names)), []) if trades else []
    event_lines = list(dict.fromkeys(event_text(e) for e in events))
    return {
        "as_of": now.astimezone(timezone.utc).isoformat(), "today": today.isoformat(),
        "previous_session": since.isoformat() if since else None, "note": NOTE, "thresholds": thresholds(),
        "positions": positions,
        "since_last_session": {"gaps": gaps, "events": event_lines, "areas": changes,
                               "empty": None if (gaps or event_lines or changes) else "Nothing new on the record since the last session."},
    }


__all__ = ["build", "chip_for", "nearest_area", "gap_of", "gap_text", "expiry_text", "reference_of", "Viewer", "NOTE", "NO_STOP"]
