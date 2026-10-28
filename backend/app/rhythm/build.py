"""The four digests of the daily rhythm, built on demand from what ATP already holds.

Sources (all read-only here, except the journal's own refresh when the `discipline_engine` flag is on, which completes the
user's journal exactly as opening the Journal page does):

* market: the Copilot's market memory of the user's organisation (`ai.market_memory.latest`: NIFTY 50 / NIFTY BANK
  structure and areas), the shared daily lake (index closes, breadth, India VIX, sector indices) through the market
  context panel's readers (`context_panel.service`), the free global cues cache, FII / DII when a source is configured;
* calendar: NSE holidays and the market events (platform-wide and the organisation's own);
* the user's own: his setups since the last session (`signals_feed.feed`: his strategies' proposals, his scans' matches,
  his fills), his open PAPER positions with their marks, his day (`ai.briefing.your_day`: P&L, trades against his own
  limits, loss budget), his journal (discipline score, behaviour tags) when the journal is on.

A source that has nothing (or fails) leaves its lines out; a section with no line says so in plain words. Never a sample
figure. Each line passes `words.clean`; a setup line also `words.signal_line` (no number). A digest is one screen: a few
sections of at most a few lines, each with the page it opens.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MarketEventRecord, MarketHolidayRecord, TradeRecord
from app.market_data.calendar import IST, family_holidays, next_trading_day
from app.rhythm import KINDS, schedule, words

logger = logging.getLogger(__name__)

TITLES: Dict[str, str] = {"pre_market": "Before the open", "mid_session": "Mid-session pulse", "closing": "Closing wrap",
                          "weekly": "Your week"}
NOTE = ("Information from the market and your own records. It describes what the data shows; it does not tell you what "
        "to trade.")
MAX_SETUPS = 5
MAX_POSITIONS = 5
MAX_EVENTS = 5
NEAR_INVALIDATION = 0.25        # a position whose mark has used 75 % or more of the room to its own invalidation
INDICES = (("NIFTY 50", "NIFTY 50"), ("NIFTY BANK", "BANK NIFTY"))
# NSE sectoral indices read from the shared daily lake for the leader / laggard line (an index without bars is skipped).
SECTOR_INDICES = (("NIFTY IT", "IT"), ("NIFTY BANK", "Banking"), ("NIFTY AUTO", "Auto"), ("NIFTY PHARMA", "Pharma"),
                  ("NIFTY FMCG", "FMCG"), ("NIFTY METAL", "Metal"), ("NIFTY REALTY", "Realty"), ("NIFTY ENERGY", "Energy"),
                  ("NIFTY MEDIA", "Media"), ("NIFTY PSU BANK", "PSU banks"), ("NIFTY FINANCIAL SERVICES", "Financial services"),
                  ("NIFTY OIL & GAS", "Oil & gas"), ("NIFTY HEALTHCARE INDEX", "Healthcare"), ("NIFTY CONSUMER DURABLES", "Consumer durables"))
EVENT_LABELS: Dict[str, str] = {"BUDGET": "Union Budget", "RBI_POLICY": "RBI policy", "EXPIRY": "Expiry", "RESULTS": "Results",
                                "FED": "US Fed decision", "ELECTION": "Election", "OTHER": "Market event"}


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def money(value: float) -> str:
    return f"{'-' if value < 0 else '+' if value > 0 else ''}₹{abs(value):,.0f}"


def _day_label(day: date) -> str:
    return day.strftime("%a %d %b")


def _pct(value: float) -> str:
    return f"{value:+.2f}%"


@dataclass
class Section:
    id: str
    title: str
    empty: str
    lines: List[str] = field(default_factory=list)
    link: Optional[Dict[str, str]] = None
    # UX-11b C1: what the section is (the page renders by it) and its typed facts (numbers, signs, states). `lines` stay
    # the same sentences for Telegram (rhythm/deliver.py) and for a client that does not know the kind.
    kind: str = "lines"
    rows: List[Dict[str, Any]] = field(default_factory=list)

    def add(self, text: Optional[str]) -> None:
        value = words.clean(text)
        if value and value not in self.lines:
            self.lines.append(value)

    def row(self, type_: str, **facts: Any) -> None:
        self.rows.append({"type": type_, **facts})

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "title": self.title, "lines": list(self.lines), "rows": list(self.rows),
                "empty": None if self.lines or self.rows else self.empty, "link": self.link}


@dataclass(frozen=True)
class Me:
    """The user's plain ids, read once: a reader's rollback expires the ORM user, and the next reader must not lazy-load
    it (the context panel's own rule)."""
    id: int
    tenant_id: int
    role: str = "USER"


@dataclass
class Ctx:
    session: AsyncSession
    user: Any                       # Me (duck-types the User fields the readers use: id, tenant_id, role)
    now: datetime
    holidays: Set[date]
    flags: Dict[str, bool]
    memory: Dict[str, Any] = field(default_factory=dict)

    @property
    def today(self) -> date:
        return self.now.astimezone(IST).date()


# --- readers (each fails on its own: a failure leaves its lines out) -------------------------------------------------

async def _safely(ctx: Ctx, what: str, coro: Any, default: Any) -> Any:
    try:
        return await coro
    except Exception:  # noqa: BLE001 - one source failing never fails the digest
        logger.exception("daily rhythm: %s failed", what)
        try:
            await ctx.session.rollback()
        except Exception:  # noqa: BLE001 - the session is already unusable; the digest goes on without this source
            logger.debug("daily rhythm: rollback after %s failed", what)
        return default


async def _tile(ctx: Ctx, name: str) -> Dict[str, Any]:
    from app.context_panel import service as ctxp
    viewer = ctxp.Viewer(id=int(ctx.user.id), tenant_id=int(ctx.user.tenant_id))
    tile = await _safely(ctx, f"tile {name}", ctxp.BUILDERS[name](ctx.session, viewer, ctx.today, sorted(ctx.holidays)), None)
    return tile if isinstance(tile, dict) and tile.get("status") in ("ok", "stale") else {}


async def _closes(ctx: Ctx, index_name: str, days: int = 20) -> List[tuple]:
    """(date, close) of an index from the shared daily lake, oldest first, up to today."""
    from app.context_panel import service as ctxp
    bars = await _safely(ctx, f"closes {index_name}", ctxp._bars(ctx.session, index_name, days, ctx.today), None)
    if bars is None or len(bars) == 0:
        return []
    return [(ts.date(), float(close)) for ts, close in zip(bars.index, bars["close"])]


async def _holiday_names(ctx: Ctx, start: date, end: date) -> Dict[date, str]:
    rows = (await ctx.session.execute(select(MarketHolidayRecord.holiday_date, MarketHolidayRecord.description).where(
        MarketHolidayRecord.exchange == "NSE", MarketHolidayRecord.holiday_date >= start, MarketHolidayRecord.holiday_date <= end))).all()
    return {d: (desc or "").strip()[:60] for d, desc in rows}


async def _events(ctx: Ctx, start: date, end: date) -> List[MarketEventRecord]:
    return list(await ctx.session.scalars(select(MarketEventRecord).where(
        MarketEventRecord.event_date >= start, MarketEventRecord.event_date <= end,
        or_(MarketEventRecord.tenant_id.is_(None), MarketEventRecord.tenant_id == ctx.user.tenant_id)).order_by(
        MarketEventRecord.event_date, MarketEventRecord.start_time, MarketEventRecord.id).limit(50)))


async def _own_trades(ctx: Ctx, *, open_only: bool = False, since: Optional[datetime] = None) -> List[TradeRecord]:
    q = select(TradeRecord).where(TradeRecord.tenant_id == ctx.user.tenant_id, TradeRecord.user_id == ctx.user.id,
                                  TradeRecord.mode == "PAPER")
    if open_only:
        q = q.where(TradeRecord.exit_time.is_(None))
    if since is not None:
        q = q.where(or_(TradeRecord.entry_time >= since, TradeRecord.exit_time >= since, TradeRecord.exit_time.is_(None)))
    return list(await ctx.session.scalars(q.order_by(TradeRecord.entry_time.desc()).limit(500)))


async def _risk_cfg(ctx: Ctx) -> Any:
    """The organisation's saved risk settings, or None when none are saved (then no limit line is shown: the platform
    defaults are not the user's own limits)."""
    from app.risk_engine.routes import get_tenant_risk_config
    return await _safely(ctx, "risk settings", get_tenant_risk_config(ctx.user.tenant_id, ctx.session), None)


# --- sections --------------------------------------------------------------------------------------------------------

def _memory_symbol(ctx: Ctx, symbol: str) -> Optional[Dict[str, Any]]:
    return next((s for s in ctx.memory.get("symbols", []) if s.get("symbol") == symbol), None)


def _area(area: Any) -> Optional[Dict[str, float]]:
    """A buyer / seller area as a price range {low, high} (the user's OWN brief shows it inside Details - UX-11b)."""
    try:
        return {"low": float(area["low"]), "high": float(area["high"])} if area else None
    except (KeyError, TypeError, ValueError):
        return None


def _structure_snap(ctx: Ctx, symbol: str, *, today_only: bool = False) -> Optional[tuple]:
    snap = _memory_symbol(ctx, symbol)
    if not snap:
        return None
    captured = _utc(datetime.fromisoformat(snap["captured_at"])) if snap.get("captured_at") else None
    if captured is None:
        return None
    read_today = captured.astimezone(IST).date() == ctx.today
    if today_only and not read_today:
        return None
    return snap, captured, read_today


def _structure_row(ctx: Ctx, symbol: str, label: str, *, today_only: bool = False) -> Optional[Dict[str, Any]]:
    found = _structure_snap(ctx, symbol, today_only=today_only)
    if not found:
        return None
    snap, captured, read_today = found
    payload = snap.get("payload") or {}
    structure = payload.get("structure") or {}
    price = payload.get("last_price") or snap.get("last_price")
    return {"type": "index", "symbol": symbol, "label": label, "trend": words.trend_key(snap.get("structure") or structure.get("trend")),
            "event": words.event_key(structure.get("last_event")),
            "location": words.area_state(price, payload.get("support"), payload.get("resistance")),
            "buyer_area": _area(payload.get("support")), "seller_area": _area(payload.get("resistance")),
            "last_read": None if read_today else captured.astimezone(IST).date().isoformat()}


def _structure_line(ctx: Ctx, symbol: str, label: str, *, today_only: bool = False) -> Optional[str]:
    found = _structure_snap(ctx, symbol, today_only=today_only)
    if not found:
        return None
    snap, captured, read_today = found
    payload = snap.get("payload") or {}
    parts = [f"{label}: {words.trend_words(snap.get('structure') or (payload.get('structure') or {}).get('trend'))}"]
    event = words.event_words((payload.get("structure") or {}).get("last_event"))
    if event:
        parts.append(event)
    area = words.area_words(payload.get("last_price") or snap.get("last_price"), payload.get("support"), payload.get("resistance"))
    if area:
        parts.append(area)
    when = "" if read_today else f" (last read {captured.astimezone(IST).strftime('%a %d %b')})"
    return "; ".join(parts) + when + "."


async def structure_section(ctx: Ctx, *, today_only: bool = False, with_dow: bool = True) -> Section:
    sec = Section("structure", "NIFTY 50 and BANK NIFTY", "No structure read of the indices yet. It appears after the market "
                  "data is read during a session.", link={"label": "Open the chart", "to": "/charts/NIFTY%2050"})
    sec.kind = "structure"
    for symbol, label in INDICES:
        sec.add(_structure_line(ctx, symbol, label, today_only=today_only))
        row = _structure_row(ctx, symbol, label, today_only=today_only)
        if row:
            sec.rows.append(row)
    if with_dow:
        regime = await _tile(ctx, "regime")
        if regime:
            daily = (regime.get("daily") or {}).get("trend")
            weekly = (regime.get("weekly") or {}).get("trend")
            if daily and weekly:
                sec.add(f"NIFTY 50 daily swings: {words.trend_words(daily)}; weekly swings: {words.trend_words(weekly)}.")
                # the table shows the daily / weekly swings on the NIFTY 50 row (one row per index)
                nifty = next((r for r in sec.rows if r["symbol"] == INDICES[0][0]), None)
                if nifty is None:
                    nifty = {"type": "index", "symbol": INDICES[0][0], "label": INDICES[0][1], "trend": None, "event": None,
                             "location": None, "buyer_area": None, "seller_area": None, "last_read": None}
                    sec.rows.insert(0, nifty)
                nifty.update(daily=words.trend_key(daily), weekly=words.trend_key(weekly))
    return sec


async def mood_section(ctx: Ctx) -> Section:
    from app.ai import global_cues, market_memory
    sec = Section("mood", "Market mood", "No market read yet. It appears once the day's market data is in.", kind="mood")
    glob = await _tile(ctx, "global")
    rows = [{"symbol": m["key"], "change_pct": m.get("change_pct"), "last_price": m.get("last"), "payload": {"as_of": m.get("as_of")}}
            for m in glob.get("markets", []) if m.get("change_pct") is not None]
    if not rows:                                     # the organisation's market memory keeps the last global read too
        rows = [g for g in ctx.memory.get("globals", []) if g.get("change_pct") is not None]
    cue = global_cues.view("en", rows, ctx.now)[:1] if rows else []
    for line in cue:
        sec.add(line)
    if cue:
        sec.row("cues", mood=global_cues.mood(rows, ctx.now)["label"],
                markets=[{"key": g["symbol"], "name": global_cues.BY_KEY[g["symbol"]].en if g["symbol"] in global_cues.BY_KEY else g["symbol"],
                          "last": g.get("last_price"), "change_pct": float(g["change_pct"]), "stale": not global_cues._fresh(g, ctx.now)}
                         for g in rows])
    vix = await _tile(ctx, "vix")
    if vix.get("close") is not None:
        text = market_memory.vix_text("en", float(vix["close"]))
        pct = vix.get("percentile")
        if pct is not None:
            text += f"; higher than {pct:.0f}% of the past year's closes"
        stale = vix.get("status") == "stale" and bool(vix.get("as_of"))
        if stale:
            text += f" (close of {date.fromisoformat(vix['as_of']).strftime('%a %d %b')})"
        sec.add(text + ".")
        sec.row("vix", value=float(vix["close"]), change_pct=vix.get("change_pct"), level=market_memory.vix_level(float(vix["close"])),
                percentile=pct, as_of=vix.get("as_of") if stale else None)
    flows = await _tile(ctx, "fii_dii")
    if flows.get("net_fii_crore") is not None and flows.get("net_dii_crore") is not None:
        sec.add(f"FII net {float(flows['net_fii_crore']):+,.0f} crore, DII net {float(flows['net_dii_crore']):+,.0f} crore "
                f"(the last published day).")
        sec.row("flows", fii_crore=float(flows["net_fii_crore"]), dii_crore=float(flows["net_dii_crore"]), as_of=flows.get("as_of"))
    return sec


def _event_line(e: MarketEventRecord, *, with_day: bool) -> Optional[str]:
    label = EVENT_LABELS.get(e.kind, "Market event")
    desc = words.clean(e.description)
    if desc is None and (e.kind == "OTHER" or e.kind not in EVENT_LABELS):
        return None                                  # an unnamed "market event" says nothing (its text failed the guard)
    scope = f" ({e.underlying})" if e.underlying and e.underlying != "*" else ""
    when = f"{_day_label(e.event_date)}: " if with_day else ""
    window = f" {e.start_time}-{e.end_time}" if e.start_time and e.end_time else ""
    effect = " New entries pause in its window; exits always work." if e.action == "BLOCK" else ""
    text = f"{when}{label}{scope}{window}"
    if desc and desc.lower() != label.lower():
        text += f" - {desc}"
    return text + "." + effect


def event_row(e: MarketEventRecord) -> Dict[str, Any]:
    """An event line's typed facts (the page shows them as a chip with the day; the window and detail go in Details)."""
    label = EVENT_LABELS.get(e.kind, "Market event")
    desc = words.clean(e.description)
    return {"type": "event", "date": e.event_date.isoformat(), "label": label,
            "scope": e.underlying if e.underlying and e.underlying != "*" else None,
            "window": f"{e.start_time}-{e.end_time}" if e.start_time and e.end_time else None,
            "detail": desc if desc and desc.lower() != label.lower() else None, "pauses_entries": e.action == "BLOCK"}


async def events_section(ctx: Ctx, start: date, end: date, title: str, empty: str, *, with_day: bool = True) -> Section:
    sec = Section("events", title, empty, link={"label": "News & events", "to": "/news-events"}, kind="events")
    rows = await _safely(ctx, "events", _events(ctx, start, end), [])
    for e in rows[:MAX_EVENTS]:
        line = _event_line(e, with_day=with_day)
        sec.add(line)
        if line and words.clean(line):
            sec.rows.append(event_row(e))
    names = await _safely(ctx, "holiday names", _holiday_names(ctx, start, end), {})
    for day in sorted(d for d in ctx.holidays if start <= d <= end and d.weekday() < 5):
        name = words.clean(names.get(day)) or ""
        sec.add(f"{_day_label(day)}: market holiday" + (f" ({name})" if name else "") + ".")
        sec.row("holiday", date=day.isoformat(), name=name or None)
    return sec


def _setup_line(item: Dict[str, Any]) -> Optional[str]:
    symbol = str(item.get("symbol") or "").strip()
    if not symbol:
        return None
    side = words.SIDE_WORDS.get(str(item.get("direction") or "").upper())
    kind = (item.get("source") or {}).get("kind")
    name = words.clean(item.get("setup"))
    name = name if name and words.numberless(name) else None
    if kind == "scan":
        text = f"{symbol}: matched your scan" + (f" \"{name}\"" if name else "")
    else:
        what = f"a {side} setup" if side else "a setup"
        text = f"{symbol}: {what} from your strategy" + (f" \"{name}\"" if name else "")
    reason = next((str(r).strip().rstrip(".") for r in (item.get("reasons") or []) if words.signal_line(str(r), [symbol])), None)
    if reason:
        first = reason.split(" ", 1)[0]
        if reason[:1].isupper() and not (len(first) > 1 and first.isupper()):      # keep an acronym ("VWAP", "RSI")
            reason = reason[0].lower() + reason[1:]
        text += f" - {reason}"
    return words.signal_line(text + ".", [symbol])


def setup_row(item: Dict[str, Any], line: str) -> Dict[str, Any]:
    """A setup line's typed facts: symbol, side (LONG / SHORT / None), source (strategy / scan) and the line's own words
    after the symbol (already numberless) for the row's Details."""
    direction = str(item.get("direction") or "").upper()
    return {"type": "setup", "symbol": str(item.get("symbol") or ""),
            "side": "SHORT" if direction in ("SHORT", "SELL") else "LONG" if direction in ("LONG", "BUY") else None,
            "source": "scan" if (item.get("source") or {}).get("kind") == "scan" else "strategy",
            "detail": line.split(": ", 1)[1] if ": " in line else line}


async def setups_section(ctx: Ctx, since: datetime, title: str = "Setups from your lists") -> Section:
    from app.signals_feed import service as feed_service
    sec = Section("setups", title, "No setups from your strategies or scans since the last session.",
                  link={"label": "Open Signals", "to": "/signals"}, kind="setups")
    page = await _safely(ctx, "setups", feed_service.feed(ctx.session, ctx.user, tab="history", limit=50, now=ctx.now), {})
    seen: Set[str] = set()
    for item in page.get("items", []):
        at = _utc(datetime.fromisoformat(item["at"])) if item.get("at") else None
        if at is None or at < since or (item.get("source") or {}).get("kind") == "autopilot" and item.get("status") == "taken":
            continue
        sym = str(item.get("symbol") or "")
        if sym in seen:
            continue
        line = _setup_line(item)
        if line:
            seen.add(sym)
            sec.lines.append(line)
            sec.rows.append(setup_row(item, line))
        if len(sec.lines) >= MAX_SETUPS:
            break
    return sec


def _side(direction: str) -> int:
    return -1 if str(direction).upper() in ("SHORT", "SELL") else 1


def position_state(t: TradeRecord) -> Dict[str, Any]:
    """Open P&L from the position's own mark and how much of the room to its own invalidation is used (in words)."""
    side = _side(t.direction)
    mark = t.mark_price
    pnl = None if mark is None else round((float(mark) - float(t.entry_price)) * float(t.quantity) * side, 2)
    near = False
    room = (float(t.entry_price) - float(t.stop_loss)) * side if t.stop_loss else 0.0
    if mark is not None and room > 0:
        left = (float(mark) - float(t.stop_loss)) * side
        near = left <= room * NEAR_INVALIDATION
    return {"pnl": pnl, "near": near}


async def positions_section(ctx: Ctx, *, only_near: bool = False) -> Section:
    sec = Section("positions", "Your open positions" if not only_near else "Positions near their invalidation",
                  "No open paper positions." if not only_near else "None of your open positions is near its invalidation.",
                  link={"label": "My positions", "to": "/positions"}, kind="positions")
    rows = await _safely(ctx, "positions", _own_trades(ctx, open_only=True), [])
    for t in rows:
        st = position_state(t)
        if only_near and not st["near"]:
            continue
        side = "short" if _side(t.direction) < 0 else "long"
        text = f"{t.symbol} ({side})"
        if st["pnl"] is not None:
            text += f": open P&L {money(st['pnl'])}"
        if st["near"]:
            text += " - near the invalidation you set for it"
        sec.add(text + ".")
        sec.row("position", symbol=t.symbol, side=side.upper(), pnl=st["pnl"], near=st["near"])
        if len(sec.lines) >= MAX_POSITIONS:
            break
    if not only_near and len(rows) > MAX_POSITIONS:
        sec.add(f"{len(rows) - MAX_POSITIONS} more on My positions.")
        sec.row("more", count=len(rows) - MAX_POSITIONS)
    return sec


async def _your_day(ctx: Ctx) -> Dict[str, Any]:
    from app.ai import briefing
    from app.core.models import RiskConfig
    cfg = await _risk_cfg(ctx)
    day = await _safely(ctx, "your day", briefing.your_day(ctx.session, ctx.user, cfg or RiskConfig(), ctx.now), None)
    return {"cfg": cfg, "paper": (day or {}).get("PAPER")}


async def budget_section(ctx: Ctx) -> Section:
    sec = Section("budget", "Your risk for the day", "Your loss budget appears once your risk settings are read.",
                  link={"label": "Risk limits", "to": "/risk-management"}, kind="budget")
    d = await _your_day(ctx)
    paper = d["paper"]
    if d["cfg"] is None or paper is None or paper.get("loss_limit") is None:      # DEF-0: no daily loss limit set
        sec.empty = "Set your own daily loss limit on the Risk limits page and your loss budget for the day shows here."
    elif paper:
        sec.add(f"Loss budget left today: {money(paper['loss_left']).lstrip('+')} of {money(paper['loss_limit']).lstrip('+')} "
                f"(your own daily loss limit).")
        if paper["consecutive_losses"]:
            sec.add(f"Losses in a row so far: {paper['consecutive_losses']}"
                    + (f" (your pause comes after {paper['max_consecutive_losses']})." if paper.get("max_consecutive_losses") is not None else "."))
        sec.row("budget", left=float(paper["loss_left"]), limit=float(paper["loss_limit"]), used=float(paper.get("loss_used") or 0.0),
                losses_in_row=int(paper["consecutive_losses"] or 0), pause_after=paper.get("max_consecutive_losses"))
    return sec


async def _discipline_today(ctx: Ctx) -> Optional[Dict[str, Any]]:
    """The day's discipline score from the journal (the weekly score's rules over today's decisions)."""
    if not ctx.flags.get("discipline_engine"):
        return None
    from app.journal import service as journal
    from app.journal.score import TagState, weekly_score
    data = await journal.refresh_week(ctx.session, ctx.user, now=ctx.now)
    start = schedule.day_start(ctx.today)
    today = [t for t in data["trades"] if (_utc(t.entry_time) or start) >= start]
    if not today:
        return None
    units = {t.id: (f"g:{t.leg_group_id}" if t.leg_group_id else f"t:{t.id}") for t in today}
    tags = [TagState(r.trade_id, r.tag, r.status) for r in data["tags"] if r.trade_id in units]
    return weekly_score(units, tags, data["settings"], closed=[t.id for t in today if t.exit_time is not None])


async def day_section(ctx: Ctx) -> Section:
    sec = Section("day", "Your day", "No paper trades today. A day without a trade is fine when nothing met your plan.",
                  link={"label": "My positions", "to": "/positions"}, kind="day")
    facts: Dict[str, Any] = {}
    d = await _your_day(ctx)
    paper, cfg = d["paper"], d["cfg"]
    opens = await _safely(ctx, "open positions", _own_trades(ctx, open_only=True), [])
    open_pnl = sum(p for p in (position_state(t)["pnl"] for t in opens) if p is not None)
    if paper and (paper["trades_today"] or paper["realised_pnl"] or opens):
        sec.add(f"Booked P&L today: {money(paper['realised_pnl'])}" + (f"; open positions {money(open_pnl)}." if opens else "."))
        facts.update(booked=float(paper["realised_pnl"]), open_pnl=round(open_pnl, 2) if opens else None)
        if cfg is not None:
            # DEF-0: only the limits he set count (a limit not set is never "gone past")
            inside = ((cfg.max_trades_per_day is None or paper["trades_today"] <= cfg.max_trades_per_day)
                      and (paper["loss_limit"] is None or paper["loss_used"] < paper["loss_limit"]))
            facts["inside_limits"] = bool(inside)
            if inside:
                sec.add("You stayed inside your own daily limits today. That is the habit that counts.")
            else:
                sec.add("Today went past one of your own daily limits (trades or loss). Your Risk limits page shows which.")
    score = await _safely(ctx, "discipline today", _discipline_today(ctx), None)
    if score and score.get("score") is None and score.get("note"):
        sec.add(f"Discipline score for today: {score['note']}.")         # AUD-V4: open trades only - nothing to score yet
    if score and score.get("score") is not None:
        sec.add(f"Discipline score for today: {score['score']} out of 100 {score['note']} (following your own plan, not the number of trades).")
        sec.link = {"label": "Open the Journal", "to": "/journal"}
        facts["discipline"] = score["score"]
    if facts:
        sec.row("day", **{"booked": None, "open_pnl": None, "inside_limits": None, "discipline": None, **facts})
    return sec


async def _index_move_facts(ctx: Ctx, symbol: str, label: str, sessions: int = 1) -> Optional[Dict[str, Any]]:
    closes = await _closes(ctx, symbol, days=20)
    if len(closes) <= sessions:
        return None
    last_day, last = closes[-1]
    _, before = closes[-1 - sessions]
    if not before:
        return None
    return {"type": "index_move", "label": label, "close": round(float(last), 2), "change_pct": round(100.0 * (last / before - 1.0), 2),
            "as_of": None if last_day == ctx.today or sessions > 1 else last_day.isoformat(), "_pct": 100.0 * (last / before - 1.0),
            "_day": last_day}


def _index_move_text(ctx: Ctx, f: Dict[str, Any], sessions: int) -> str:
    when = "" if f["_day"] == ctx.today or sessions > 1 else f" (close of {_day_label(f['_day'])})"
    return f"{f['label']} {_pct(f['_pct'])}{when}"


async def _index_move(ctx: Ctx, symbol: str, label: str, sessions: int = 1) -> Optional[str]:
    f = await _index_move_facts(ctx, symbol, label, sessions)
    return _index_move_text(ctx, f, sessions) if f else None


async def _sector_moves(ctx: Ctx, sessions: int = 1) -> List[tuple]:
    out = []
    for index_name, label in SECTOR_INDICES:
        closes = await _closes(ctx, index_name, days=20)
        if len(closes) > sessions and closes[-1 - sessions][1]:
            out.append((100.0 * (closes[-1][1] / closes[-1 - sessions][1] - 1.0), label, closes[-1][0]))
    return out


async def market_move_section(ctx: Ctx, *, sessions: int = 1) -> Section:
    week = sessions > 1
    sec = Section("market", "The week in the market" if week else "How the market moved",
                  "The day's closing data is not in yet. It is published in the evening." if not week
                  else "The week's closing data is not in yet.", link={"label": "Open the chart", "to": "/charts/NIFTY%2050"},
                  kind="market")
    facts = [f for f in [await _index_move_facts(ctx, sym, label, sessions) for sym, label in INDICES] if f]
    if facts:
        sec.add(("Over the week: " if week else "") + ", ".join(_index_move_text(ctx, f, sessions) for f in facts) + ".")
        for f in facts:
            sec.rows.append({**{k: v for k, v in f.items() if not k.startswith("_")}, "sessions": sessions})
    if not week:
        breadth = await _tile(ctx, "breadth")
        if breadth.get("counted"):
            sec.add(f"Breadth: {breadth['advances']} shares rose and {breadth['declines']} fell"
                    + ("" if breadth.get("as_of") == ctx.today.isoformat() else f" (on {date.fromisoformat(breadth['as_of']).strftime('%a %d %b')})") + ".")
            sec.row("breadth", advances=int(breadth["advances"]), declines=int(breadth["declines"]),
                    as_of=None if breadth.get("as_of") == ctx.today.isoformat() else breadth.get("as_of"))
    sectors = sorted(await _sector_moves(ctx, sessions), key=lambda s: s[0])
    if len(sectors) >= 2:
        lead, lag = sectors[-1], sectors[0]
        sec.add(f"Strongest sector: {lead[1]} ({_pct(lead[0])}); weakest: {lag[1]} ({_pct(lag[0])}).")
        sec.row("sectors", strongest={"label": lead[1], "change_pct": round(lead[0], 2)},
                weakest={"label": lag[1], "change_pct": round(lag[0], 2)})
    return sec


async def tomorrow_section(ctx: Ctx) -> Section:
    nxt = next_trading_day(ctx.today, ctx.holidays)
    sec = await events_section(ctx, ctx.today + timedelta(days=1), nxt, "What to watch next session",
                               "No events on the calendar for the next session.")
    sec.id, sec.kind = "next", "next"
    opens = await _safely(ctx, "open positions", _own_trades(ctx, open_only=True), [])
    sec.lines.insert(0, f"Next session: {_day_label(nxt)}.")
    sec.rows.insert(0, {"type": "next_session", "date": nxt.isoformat()})
    if opens:
        sec.add(f"You carry {len(opens)} open paper position{'s' if len(opens) != 1 else ''} into it.")
        sec.row("carry", count=len(opens))
    return sec


async def week_you_section(ctx: Ctx, monday: date) -> Section:
    sec = Section("you", "Your week", "No paper trades this week. A quiet week is fine when nothing met your plan.",
                  link={"label": "My positions", "to": "/positions"}, kind="week")
    start = schedule.day_start(monday).astimezone(timezone.utc)
    end = start + timedelta(days=7)
    if ctx.flags.get("discipline_engine"):
        from app.journal import service as journal
        review = await _safely(ctx, "weekly review", journal.weekly_review(ctx.session, ctx.user, None, history_weeks=1, now=ctx.now), None)
        if review and review["totals"]["trades"]:
            t = review["totals"]
            sec.add(f"{t['closed']} closed, {t['wins']} won and {t['losses']} lost; net {money(t['net_pnl'])} after charges.")
            sec.row("record", closed=t["closed"], won=t["wins"], lost=t["losses"], net=float(t["net_pnl"]), after_charges=True)
            score = (review.get("discipline") or {}).get("score")
            if score is not None:
                prev = next((h for h in review.get("history", []) if h.get("score") is not None), None)
                sec.row("discipline", score=score, previous=prev["score"] if prev else None)
                change = "" if not prev else (f", up {score - prev['score']} on last week" if score > prev["score"]
                                              else f", down {prev['score'] - score} on last week" if score < prev["score"] else ", the same as last week")
                note = (review.get("discipline") or {}).get("note")
                sec.add(f"Discipline score: {score} out of 100{' ' + note if note else ''}{change}.")
            elif (review.get("discipline") or {}).get("note"):
                sec.add(f"Discipline score: {review['discipline']['note']}.")
            cost = review.get("costliest")
            if cost:
                sec.add(f"The pattern that cost the most: {cost['label']} ({money(-abs(cost['net_pnl']))} over {cost['closed']} closed trade"
                        f"{'s' if cost['closed'] != 1 else ''}).")
                sec.row("costliest", label=cost["label"], net=-abs(float(cost["net_pnl"])), closed=cost["closed"])
            sec.link = {"label": "Open the Journal", "to": "/journal"}
            return sec
    trades = await _safely(ctx, "week trades", _own_trades(ctx, since=start), [])
    closed = [t for t in trades if t.exit_time is not None and start <= (_utc(t.exit_time) or start) < end and t.pnl is not None]
    if closed:
        wins = sum(1 for t in closed if (t.pnl or 0) > 0)
        net = round(sum(float(t.pnl or 0) for t in closed), 2)
        sec.add(f"{len(closed)} closed, {wins} won and {len(closed) - wins} did not; net {money(net)}.")
        sec.row("record", closed=len(closed), won=wins, lost=len(closed) - wins, net=net, after_charges=False)
    cfg = await _risk_cfg(ctx)
    days = [d for d in schedule.week_trading_days(monday, ctx.holidays) if d <= ctx.today]
    if trades and days and cfg is not None and cfg.daily_loss_amount() is not None and cfg.max_trades_per_day is not None:
        limit = float(cfg.daily_loss_amount() or 0.0)
        inside = 0
        for d in days:
            s, e = schedule.day_start(d).astimezone(timezone.utc), schedule.day_start(d + timedelta(days=1)).astimezone(timezone.utc)
            entered = sum(1 for t in trades if s <= (_utc(t.entry_time) or s) < e)
            lost = -sum(float(t.pnl or 0) for t in trades if t.exit_time is not None and s <= (_utc(t.exit_time) or s) < e)
            inside += entered <= cfg.max_trades_per_day and lost < limit
        sec.add(f"Days inside your own daily limits: {inside} of {len(days)}.")
        sec.row("days_inside", inside=int(inside), days=len(days))
    return sec


# --- the digests -----------------------------------------------------------------------------------------------------

async def _ctx(session: AsyncSession, user: Me, now: datetime) -> Ctx:
    from app.ai import market_memory
    from app.platform.controls import features_for_tenant
    holidays = set(await family_holidays(session, "NSE"))
    flags = await features_for_tenant(session, user.tenant_id)
    ctx = Ctx(session=session, user=user, now=now, holidays=holidays, flags=flags)
    ctx.memory = await _safely(ctx, "market memory", market_memory.latest(session, user.tenant_id, now=now), {}) or {}
    return ctx


def _since_last_session(ctx: Ctx) -> datetime:
    prev = schedule.previous_session(ctx.today, ctx.holidays)
    return schedule.session_start(prev).astimezone(timezone.utc)


async def pre_market(ctx: Ctx) -> List[Section]:
    nxt = schedule.next_session(ctx.now, ctx.holidays)
    return [await mood_section(ctx),
            await events_section(ctx, nxt, nxt, "Events today" if nxt == ctx.today else f"Events on {_day_label(nxt)}",
                                 "No events on the calendar for the session.", with_day=False),
            await structure_section(ctx),
            await setups_section(ctx, _since_last_session(ctx)),
            await positions_section(ctx),
            await budget_section(ctx)]


async def mid_session(ctx: Ctx) -> List[Section]:
    opened = schedule.session_start(ctx.today).astimezone(timezone.utc)
    structure = await structure_section(ctx, today_only=True, with_dow=False)
    structure.title, structure.empty = "What changed in the indices", "No new read of the indices since the open yet."
    return [structure,
            await positions_section(ctx, only_near=True),
            await setups_section(ctx, opened, title="New setups since the open"),
            await day_section(ctx)]


async def closing(ctx: Ctx) -> List[Section]:
    return [await market_move_section(ctx), await day_section(ctx), await tomorrow_section(ctx)]


async def weekly(ctx: Ctx) -> List[Section]:
    monday = ctx.today - timedelta(days=ctx.today.weekday())
    structure = await structure_section(ctx)
    structure.id, structure.title = "story", "The structure now"
    next_monday = monday + timedelta(days=7)
    ahead = await events_section(ctx, next_monday, next_monday + timedelta(days=4), "The coming week",
                                 "No events on the calendar for the coming week.")
    ahead.id = "ahead"
    return [await market_move_section(ctx, sessions=5), structure, await week_you_section(ctx, monday), ahead]


BUILDERS = {"pre_market": pre_market, "mid_session": mid_session, "closing": closing, "weekly": weekly}


def subtitle(kind: str, ctx: Ctx, names: Optional[Dict[date, str]] = None) -> str:
    today = _day_label(ctx.today)
    if kind == "weekly":
        monday = ctx.today - timedelta(days=ctx.today.weekday())
        return f"Week of {_day_label(monday)}"
    if ctx.today in ctx.holidays or ctx.today.weekday() >= 5:
        name = words.clean((names or {}).get(ctx.today)) or ""
        nxt = schedule.next_session(ctx.now, ctx.holidays)
        return f"{today} · market closed" + (f" ({name})" if name else "") + f" · next session {_day_label(nxt)}"
    if kind == "pre_market":
        return f"{today} · the market opens at 09:15"
    if kind == "mid_session":
        return f"{today} · market open"
    return f"{today} · after the close"


async def build(session: AsyncSession, user: Any, kind: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """One digest for `user`: {kind, title, subtitle, as_of, sections: [{id, kind, title, lines, rows, empty, link}], has_content,
    note}. `rows` are the typed facts the page renders by `kind`; `lines` the same facts as sentences (Telegram)."""
    if kind not in KINDS:
        raise ValueError(f"unknown digest {kind!r}")
    now = _utc(now) or datetime.now(timezone.utc)
    ctx = await _ctx(session, Me(id=int(user.id), tenant_id=int(user.tenant_id), role=str(user.role or "USER")), now)
    sections = await BUILDERS[kind](ctx)
    for s in sections:                                # a typed row passes the same guard as a line
        s.rows = [r for r in s.rows if words.clean_row(r)]
    names = await _safely(ctx, "holiday names", _holiday_names(ctx, ctx.today, ctx.today), {})
    return {"kind": kind, "title": TITLES[kind], "subtitle": subtitle(kind, ctx, names), "as_of": now.isoformat(),
            "sections": [s.as_dict() for s in sections],
            # "next" always names the next session; the digest has content when any other section has a line.
            "has_content": any(s.lines for s in sections if s.id != "next") or any(len(s.lines) > 1 for s in sections if s.id == "next"),
            "note": NOTE}


async def holidays_for(session: AsyncSession) -> Sequence[date]:
    return sorted(await family_holidays(session, "NSE"))


__all__ = ["build", "BUILDERS", "setup_row", "event_row", "TITLES", "NOTE", "position_state", "money", "holidays_for"]
