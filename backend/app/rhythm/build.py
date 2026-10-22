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

    def add(self, text: Optional[str]) -> None:
        value = words.clean(text)
        if value and value not in self.lines:
            self.lines.append(value)

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "title": self.title, "lines": list(self.lines), "empty": None if self.lines else self.empty,
                "link": self.link}


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


def _structure_line(ctx: Ctx, symbol: str, label: str, *, today_only: bool = False) -> Optional[str]:
    snap = _memory_symbol(ctx, symbol)
    if not snap:
        return None
    captured = _utc(datetime.fromisoformat(snap["captured_at"])) if snap.get("captured_at") else None
    if captured is None:
        return None
    read_today = captured.astimezone(IST).date() == ctx.today
    if today_only and not read_today:
        return None
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
    for symbol, label in INDICES:
        sec.add(_structure_line(ctx, symbol, label, today_only=today_only))
    if with_dow:
        regime = await _tile(ctx, "regime")
        if regime:
            daily = (regime.get("daily") or {}).get("trend")
            weekly = (regime.get("weekly") or {}).get("trend")
            if daily and weekly:
                sec.add(f"NIFTY 50 daily swings: {words.trend_words(daily)}; weekly swings: {words.trend_words(weekly)}.")
    return sec


async def mood_section(ctx: Ctx) -> Section:
    from app.ai import global_cues, market_memory
    sec = Section("mood", "Market mood", "No market read yet. It appears once the day's market data is in.")
    glob = await _tile(ctx, "global")
    rows = [{"symbol": m["key"], "change_pct": m.get("change_pct"), "payload": {"as_of": m.get("as_of")}}
            for m in glob.get("markets", []) if m.get("change_pct") is not None]
    if not rows:                                     # the organisation's market memory keeps the last global read too
        rows = [g for g in ctx.memory.get("globals", []) if g.get("change_pct") is not None]
    cue = global_cues.view("en", rows, ctx.now)[:1] if rows else []
    for line in cue:
        sec.add(line)
    vix = await _tile(ctx, "vix")
    if vix.get("close") is not None:
        text = market_memory.vix_text("en", float(vix["close"]))
        pct = vix.get("percentile")
        if pct is not None:
            text += f"; higher than {pct:.0f}% of the past year's closes"
        if vix.get("status") == "stale" and vix.get("as_of"):
            text += f" (close of {date.fromisoformat(vix['as_of']).strftime('%a %d %b')})"
        sec.add(text + ".")
    flows = await _tile(ctx, "fii_dii")
    if flows.get("net_fii_crore") is not None and flows.get("net_dii_crore") is not None:
        sec.add(f"FII net {float(flows['net_fii_crore']):+,.0f} crore, DII net {float(flows['net_dii_crore']):+,.0f} crore "
                f"(the last published day).")
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


async def events_section(ctx: Ctx, start: date, end: date, title: str, empty: str, *, with_day: bool = True) -> Section:
    sec = Section("events", title, empty, link={"label": "News & events", "to": "/news-events"})
    rows = await _safely(ctx, "events", _events(ctx, start, end), [])
    for e in rows[:MAX_EVENTS]:
        sec.add(_event_line(e, with_day=with_day))
    names = await _safely(ctx, "holiday names", _holiday_names(ctx, start, end), {})
    for day in sorted(d for d in ctx.holidays if start <= d <= end and d.weekday() < 5):
        name = words.clean(names.get(day)) or ""
        sec.add(f"{_day_label(day)}: market holiday" + (f" ({name})" if name else "") + ".")
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


async def setups_section(ctx: Ctx, since: datetime, title: str = "Setups from your lists") -> Section:
    from app.signals_feed import service as feed_service
    sec = Section("setups", title, "No setups from your strategies or scans since the last session.",
                  link={"label": "Open Signals", "to": "/signals"})
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
                  link={"label": "My positions", "to": "/positions"})
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
        if len(sec.lines) >= MAX_POSITIONS:
            break
    if not only_near and len(rows) > MAX_POSITIONS:
        sec.add(f"{len(rows) - MAX_POSITIONS} more on My positions.")
    return sec


async def _your_day(ctx: Ctx) -> Dict[str, Any]:
    from app.ai import briefing
    from app.core.models import RiskConfig
    cfg = await _risk_cfg(ctx)
    day = await _safely(ctx, "your day", briefing.your_day(ctx.session, ctx.user, cfg or RiskConfig(), ctx.now), None)
    return {"cfg": cfg, "paper": (day or {}).get("PAPER")}


async def budget_section(ctx: Ctx) -> Section:
    sec = Section("budget", "Your risk for the day", "Your loss budget appears once your risk settings are read.",
                  link={"label": "Risk limits", "to": "/risk-management"})
    d = await _your_day(ctx)
    paper = d["paper"]
    if d["cfg"] is None:
        sec.empty = "Set your own daily loss limit on the Risk limits page and your loss budget for the day shows here."
    elif paper:
        sec.add(f"Loss budget left today: {money(paper['loss_left']).lstrip('+')} of {money(paper['loss_limit']).lstrip('+')} "
                f"(your own daily loss limit).")
        if paper["consecutive_losses"]:
            sec.add(f"Losses in a row so far: {paper['consecutive_losses']} (your pause comes after {paper['max_consecutive_losses']}).")
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
    return weekly_score(units, tags, data["settings"])


async def day_section(ctx: Ctx) -> Section:
    sec = Section("day", "Your day", "No paper trades today. A day without a trade is fine when nothing met your plan.",
                  link={"label": "My positions", "to": "/positions"})
    d = await _your_day(ctx)
    paper, cfg = d["paper"], d["cfg"]
    opens = await _safely(ctx, "open positions", _own_trades(ctx, open_only=True), [])
    open_pnl = sum(p for p in (position_state(t)["pnl"] for t in opens) if p is not None)
    if paper and (paper["trades_today"] or paper["realised_pnl"] or opens):
        sec.add(f"Booked P&L today: {money(paper['realised_pnl'])}" + (f"; open positions {money(open_pnl)}." if opens else "."))
        if cfg is not None:
            if paper["trades_today"] <= cfg.max_trades_per_day and paper["loss_used"] < paper["loss_limit"]:
                sec.add("You stayed inside your own daily limits today. That is the habit that counts.")
            else:
                sec.add("Today went past one of your own daily limits (trades or loss). Your Risk limits page shows which.")
    score = await _safely(ctx, "discipline today", _discipline_today(ctx), None)
    if score and score.get("score") is not None:
        sec.add(f"Discipline score for today: {score['score']} out of 100 (following your own plan, not the number of trades).")
        sec.link = {"label": "Open the Journal", "to": "/journal"}
    return sec


async def _index_move(ctx: Ctx, symbol: str, label: str, sessions: int = 1) -> Optional[str]:
    closes = await _closes(ctx, symbol, days=20)
    if len(closes) <= sessions:
        return None
    last_day, last = closes[-1]
    _, before = closes[-1 - sessions]
    if not before:
        return None
    when = "" if last_day == ctx.today or sessions > 1 else f" (close of {_day_label(last_day)})"
    return f"{label} {_pct(100.0 * (last / before - 1.0))}{when}"


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
                  else "The week's closing data is not in yet.", link={"label": "Open the chart", "to": "/charts/NIFTY%2050"})
    moves = [m for m in [await _index_move(ctx, sym, label, sessions) for sym, label in INDICES] if m]
    if moves:
        sec.add(("Over the week: " if week else "") + ", ".join(moves) + ".")
    if not week:
        breadth = await _tile(ctx, "breadth")
        if breadth.get("counted"):
            sec.add(f"Breadth: {breadth['advances']} shares rose and {breadth['declines']} fell"
                    + ("" if breadth.get("as_of") == ctx.today.isoformat() else f" (on {date.fromisoformat(breadth['as_of']).strftime('%a %d %b')})") + ".")
    sectors = sorted(await _sector_moves(ctx, sessions), key=lambda s: s[0])
    if len(sectors) >= 2:
        lead, lag = sectors[-1], sectors[0]
        sec.add(f"Strongest sector: {lead[1]} ({_pct(lead[0])}); weakest: {lag[1]} ({_pct(lag[0])}).")
    return sec


async def tomorrow_section(ctx: Ctx) -> Section:
    nxt = next_trading_day(ctx.today, ctx.holidays)
    sec = await events_section(ctx, ctx.today + timedelta(days=1), nxt, "What to watch next session",
                               "No events on the calendar for the next session.")
    sec.id = "next"
    opens = await _safely(ctx, "open positions", _own_trades(ctx, open_only=True), [])
    sec.lines.insert(0, f"Next session: {_day_label(nxt)}.")
    if opens:
        sec.add(f"You carry {len(opens)} open paper position{'s' if len(opens) != 1 else ''} into it.")
    return sec


async def week_you_section(ctx: Ctx, monday: date) -> Section:
    sec = Section("you", "Your week", "No paper trades this week. A quiet week is fine when nothing met your plan.",
                  link={"label": "My positions", "to": "/positions"})
    start = schedule.day_start(monday).astimezone(timezone.utc)
    end = start + timedelta(days=7)
    if ctx.flags.get("discipline_engine"):
        from app.journal import service as journal
        review = await _safely(ctx, "weekly review", journal.weekly_review(ctx.session, ctx.user, None, history_weeks=1, now=ctx.now), None)
        if review and review["totals"]["trades"]:
            t = review["totals"]
            sec.add(f"{t['closed']} closed, {t['wins']} won and {t['losses']} lost; net {money(t['net_pnl'])} after charges.")
            score = (review.get("discipline") or {}).get("score")
            if score is not None:
                prev = next((h for h in review.get("history", []) if h.get("score") is not None), None)
                change = "" if not prev else (f", up {score - prev['score']} on last week" if score > prev["score"]
                                              else f", down {prev['score'] - score} on last week" if score < prev["score"] else ", the same as last week")
                sec.add(f"Discipline score: {score} out of 100{change}.")
            cost = review.get("costliest")
            if cost:
                sec.add(f"The pattern that cost the most: {cost['label']} ({money(-abs(cost['net_pnl']))} over {cost['closed']} closed trade"
                        f"{'s' if cost['closed'] != 1 else ''}).")
            sec.link = {"label": "Open the Journal", "to": "/journal"}
            return sec
    trades = await _safely(ctx, "week trades", _own_trades(ctx, since=start), [])
    closed = [t for t in trades if t.exit_time is not None and start <= (_utc(t.exit_time) or start) < end and t.pnl is not None]
    if closed:
        wins = sum(1 for t in closed if (t.pnl or 0) > 0)
        net = round(sum(float(t.pnl or 0) for t in closed), 2)
        sec.add(f"{len(closed)} closed, {wins} won and {len(closed) - wins} did not; net {money(net)}.")
    cfg = await _risk_cfg(ctx)
    days = [d for d in schedule.week_trading_days(monday, ctx.holidays) if d <= ctx.today]
    if trades and days and cfg is not None:
        limit = cfg.capital * cfg.max_daily_loss_pct / 100.0
        inside = 0
        for d in days:
            s, e = schedule.day_start(d).astimezone(timezone.utc), schedule.day_start(d + timedelta(days=1)).astimezone(timezone.utc)
            entered = sum(1 for t in trades if s <= (_utc(t.entry_time) or s) < e)
            lost = -sum(float(t.pnl or 0) for t in trades if t.exit_time is not None and s <= (_utc(t.exit_time) or s) < e)
            inside += entered <= cfg.max_trades_per_day and lost < limit
        sec.add(f"Days inside your own daily limits: {inside} of {len(days)}.")
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
    """One digest for `user`: {kind, title, subtitle, as_of, sections: [{id, title, lines, empty, link}], has_content, note}."""
    if kind not in KINDS:
        raise ValueError(f"unknown digest {kind!r}")
    now = _utc(now) or datetime.now(timezone.utc)
    ctx = await _ctx(session, Me(id=int(user.id), tenant_id=int(user.tenant_id), role=str(user.role or "USER")), now)
    sections = await BUILDERS[kind](ctx)
    names = await _safely(ctx, "holiday names", _holiday_names(ctx, ctx.today, ctx.today), {})
    return {"kind": kind, "title": TITLES[kind], "subtitle": subtitle(kind, ctx, names), "as_of": now.isoformat(),
            "sections": [s.as_dict() for s in sections],
            # "next" always names the next session; the digest has content when any other section has a line.
            "has_content": any(s.lines for s in sections if s.id != "next") or any(len(s.lines) > 1 for s in sections if s.id == "next"),
            "note": NOTE}


async def holidays_for(session: AsyncSession) -> Sequence[date]:
    return sorted(await family_holidays(session, "NSE"))


__all__ = ["build", "BUILDERS", "TITLES", "NOTE", "position_state", "money", "holidays_for"]
