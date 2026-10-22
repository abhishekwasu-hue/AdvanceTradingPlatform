"""Master Order 4 slice 4 (docs/owner-orders/ATP_SESSION_GROWTH_MASTER.md PART 2 items 3, 5, 9, 10, 11): the "ready-made
dish" at the top of Signals, My positions, the Journal, Charts and Find setups - a few plain lines, prepared from what
ATP already holds for this user, the moment the page opens.

Every dish is built on demand from rows that already exist (no migration, no sample figure, nothing stored):

* signals   - his signals inbox (`signals_feed.feed`: what waits for his decision, what arrived today by source) and the
              paper record of each of his own strategies over the last 90 days (closed PAPER trades, legs of one
              structure counted once, as the journal counts them);
* positions - his open PAPER positions: the open P&L at the last marks, the result if each reached the invalidation he
              set for it against his own daily loss limit, positions without an invalidation, those near it, the
              premium kept on written options, and what is coming up for what he holds (events, expiries);
* journal   - the journal's weekly discipline score and its trend over his own past weeks, his best and costliest
              setups over four weeks, the behaviour patterns of the week and the tags that wait for him (only while
              the journal, `discipline_engine`, is on);
* charts    - the chart's story in structure words from the shared daily history (daily and weekly swings, the last
              structure event, where the close sits against the last swings; no level is shown) and what is his on
              that symbol (an open position, his latest setup, his lists);
* screener  - today's matches from his own scans grouped by scan (most matches first), those on his own lists, and how
              many of his scans run on their own.

The rules (CLAUDE.md, the master order): information only - every line passes the daily rhythm's guard (no buy / sell
/ target / stop wording, no advice phrase); a line that describes a setup also carries no number at all
(`words.signal_line`). No reward for activity: no line praises or counts up trading more; the discipline lines are about
following his own plan. A section with nothing says so in plain words. One source failing leaves its lines out.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AlertRuleRecord, JournalTagRecord, MarketEventRecord, TradeRecord
from app.market_data.calendar import IST, family_holidays
from app.rhythm import words
from app.rhythm.build import (Ctx, Me, Section, _day_label, _own_trades, _risk_cfg, _safely, _setup_line, _side,
                              _utc, money, position_state)

PAGES: Tuple[str, ...] = ("signals", "positions", "journal", "charts", "screener")
TITLES: Dict[str, str] = {"signals": "Your signals at a glance", "positions": "Your positions now",
                          "journal": "Your journal at a glance", "charts": "What this chart shows",
                          "screener": "Today's setups from your scans"}
NOTE = ("Information from your own records and the market's data. It describes what the data shows; it does not tell you "
        "what to trade.")
MAX_LINES = 3                 # setup lines per section
RECORD_DAYS = 90              # the strategies' paper record
SETUP_WEEKS = 4               # best / costliest setups
AHEAD_DAYS = 7                # events and expiries "coming up"
HISTORY_DAYS = 400            # daily bars read for the chart's story (a year and a bit: enough weekly swings)
MIN_DAILY_BARS = 30
MIN_WEEKLY_BARS = 10
SOURCE_WORDS: Dict[str, str] = {"autopilot": "your paper bots", "scan": "your scans", "copilot": "the Copilot",
                                "library": "the signal generator"}
# The journal's behaviour tags in words the dish may show (the journal's own labels use "stop" / "target", which the
# signal-language guard keeps off a dish).
BEHAVIOUR_WORDS: Dict[str, str] = {
    "revenge_trade": "a trade soon after a loss (revenge trade)", "fomo_entry": "a late entry after a fast move (FOMO)",
    "averaging_loser": "adding to a losing position", "moved_stop_against": "the invalidation moved against the position",
    "no_stop": "no invalidation level set", "overtrading": "more trades than your own daily norm",
    "early_exit": "an exit before your plan's exit level", "held_through_event": "held through a market event",
}


def _plural(n: int, word: str, many: Optional[str] = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _pretty(name: str) -> str:
    """"IRON_CONDOR" -> "Iron condor"."""
    text = re.sub(r"[_\s]+", " ", str(name or "")).strip()
    return text[:1].upper() + text[1:].lower() if text.isupper() or "_" in str(name or "") else text


async def _ctx(session: AsyncSession, user: Any, now: datetime) -> Ctx:
    from app.platform.controls import features_for_tenant
    holidays = set(await family_holidays(session, "NSE"))
    flags = await features_for_tenant(session, int(user.tenant_id))
    return Ctx(session=session, user=Me(id=int(user.id), tenant_id=int(user.tenant_id), role=str(user.role or "USER")), now=now,
               holidays=holidays, flags=flags)


# --- signals --------------------------------------------------------------------------------------------------------

def _units(trades: Sequence[TradeRecord]) -> List[Any]:
    from app.journal.service import review_trades
    return review_trades(trades)


async def _closed_since(ctx: Ctx, since: datetime) -> List[TradeRecord]:
    q = select(TradeRecord).where(TradeRecord.tenant_id == ctx.user.tenant_id, TradeRecord.user_id == ctx.user.id,
                                  TradeRecord.mode == "PAPER", TradeRecord.exit_time.is_not(None), TradeRecord.exit_time >= since)
    return list(await ctx.session.scalars(q.order_by(TradeRecord.exit_time.desc()).limit(2000)))


def strategy_record_lines(units: Sequence[Any], days: int = RECORD_DAYS) -> List[str]:
    """Per strategy (most closed first, at most three): closed paper trades and how many ended with a gain - a record of
    what happened, never a forecast."""
    from app.signals_feed.service import strategy_name
    groups: Dict[str, List[Any]] = defaultdict(list)
    for u in units:
        if u.closed:
            groups[u.setup or "-"].append(u)
    out: List[str] = []
    for setup, group in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:MAX_LINES]:
        gains = sum(1 for u in group if (u.pnl or 0) > 0)
        name = words.clean(strategy_name(setup)) or "A strategy"
        out.append(f"{name}: {_plural(len(group), 'closed paper trade')} in the last {days} days; {gains} ended with a gain and "
                   f"{len(group) - gains} did not.")
    return out


async def signals_dish(ctx: Ctx) -> Tuple[Optional[str], List[Section]]:
    from app.signals_feed import service as feed
    waiting = Section("waiting", "Waiting for your decision",
                      "Nothing waits for your decision right now. New signals from your paper bots and scans arrive here.")
    page = await _safely(ctx, "waiting signals", feed.feed(ctx.session, ctx.user, tab="waiting", limit=20, now=ctx.now), {})
    n_wait = int(((page.get("counts") or {}).get("waiting")) or 0)
    if n_wait:
        waiting.add(f"{_plural(n_wait, 'signal')} {'waits' if n_wait == 1 else 'wait'} for your decision. Nothing is placed "
                    f"until you approve it.")
    seen = set()
    for item in page.get("items", []):
        line = _setup_line(item)
        if line and item.get("symbol") not in seen:
            seen.add(item.get("symbol"))
            waiting.lines.append(line)
        if len(waiting.lines) > MAX_LINES:
            break

    today = Section("today", "Today so far", "No new signals today yet. They appear here when your paper bots or scans find a "
                    "setup from your own rules.")
    day = await _safely(ctx, "today's signals", feed.feed(ctx.session, ctx.user, tab="today", limit=100, now=ctx.now), {})
    by_kind: Dict[str, int] = defaultdict(int)
    for item in day.get("items", []):
        by_kind[str((item.get("source") or {}).get("kind") or "")] += 1
    parts = [f"{by_kind[k]} from {SOURCE_WORDS[k]}" for k in SOURCE_WORDS if by_kind.get(k)]
    if parts:
        more = int(((day.get("counts") or {}).get("today")) or 0) > sum(by_kind.values()) or bool(day.get("next_cursor"))
        joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
        today.add(joined[:1].upper() + joined[1:] + (" (and more further down the list)" if more else "") + ".")

    record = Section("record", "Your strategies' paper record",
                     f"No closed paper trades from your strategies in the last {RECORD_DAYS} days yet.")
    closed = await _safely(ctx, "paper record", _closed_since(ctx, ctx.now - timedelta(days=RECORD_DAYS)), [])
    for line in strategy_record_lines(_units(closed)):
        record.add(line)
    headline = waiting.lines[0] if n_wait else "Nothing waits for your decision right now."
    return headline, [waiting, today, record]


# --- positions ------------------------------------------------------------------------------------------------------

def _group_key(t: TradeRecord) -> str:
    return f"g:{t.leg_group_id}" if t.leg_group_id else f"t:{t.id}"


def at_invalidation(legs: Sequence[TradeRecord]) -> Optional[float]:
    """One decision's result (rupees, from its entry) if it reached the invalidation he set: a single position from its
    own invalidation, a structure from its maximum loss. None when no invalidation is set."""
    if len(legs) == 1 and not legs[0].leg_group_id:
        t = legs[0]
        if not t.stop_loss:
            return None
        return round(_side(t.direction) * (float(t.stop_loss) - float(t.entry_price)) * float(t.quantity), 2)
    from app.journal.service import _group_risk
    risk = _group_risk(legs)
    return -round(risk, 2) if risk > 0 else None


def premium_kept(legs: Sequence[TradeRecord]) -> Optional[Dict[str, Optional[float]]]:
    """For written options (one sold option, or legs placed together that took in a net premium): what came in and the
    share kept at the last marks (the positions page's own rule). None for anything else."""
    if not legs or any(t.instrument_kind != "OPTION" for t in legs):
        return None
    sign = [(-1 if _side(t.direction) > 0 else 1) for t in legs]          # a sold leg takes premium in
    received = sum(s * float(t.entry_price) * float(t.quantity) for s, t in zip(sign, legs))
    if received <= 0:
        return None
    if any(t.mark_price is None for t in legs):
        return {"received": round(received, 2), "kept": None, "pct": None}
    close_now = sum(s * float(t.mark_price or 0) * float(t.quantity) for s, t in zip(sign, legs))
    kept = received - close_now
    return {"received": round(received, 2), "kept": round(kept, 2), "pct": round(100.0 * kept / received)}


def _unit_label(legs: Sequence[TradeRecord]) -> str:
    first = legs[0]
    if len(legs) > 1 or first.leg_group_id:
        name = _pretty(first.option_strategy or "") or "Option structure"
        return f"{name} on {first.underlying_symbol or first.symbol}"
    return f"{first.symbol} (written)"


async def _events_for(ctx: Ctx, start: date, end: date) -> List[MarketEventRecord]:
    from app.rhythm.build import _events
    return await _events(ctx, start, end)


def _event_text(e: MarketEventRecord) -> Optional[str]:
    from app.rhythm.build import _event_line
    return _event_line(e, with_day=True)


async def positions_dish(ctx: Ctx) -> Tuple[Optional[str], List[Section]]:
    opens = await _safely(ctx, "open positions", _own_trades(ctx, open_only=True), [])
    risk = Section("risk", "Your risk in plain words", "No open paper positions. Your risk shows here once you hold one.")
    near = Section("near", "Near their invalidation", "None of your open positions is near the invalidation you set for it.")
    premium = Section("premium", "Premium kept on written options", "No written options open.")
    ahead = Section("ahead", "Coming up for what you hold",
                    f"Nothing on the calendar for what you hold in the next {AHEAD_DAYS} days.")
    if not opens:
        return "No open paper positions.", [risk]

    units: Dict[str, List[TradeRecord]] = defaultdict(list)
    for t in opens:
        units[_group_key(t)].append(t)
    pnls = [position_state(t)["pnl"] for t in opens]
    known = [p for p in pnls if p is not None]
    if known:
        risk.add(f"Open P&L across {_plural(len(units), 'position')}: {money(sum(known))} at the last prices"
                 + ("" if len(known) == len(pnls) else " (some prices are not in yet)") + ".")
    else:
        risk.add(f"You hold {_plural(len(units), 'open position')}; the open P&L shows once prices are in.")
    results = [at_invalidation(legs) for legs in units.values()]
    unset = sum(1 for r in results if r is None)
    total = round(sum(r for r in results if r is not None), 2)
    if len(results) > unset:
        risk.add(f"If each position reached the invalidation you set for it, the result from entry would be about {money(total)} in total.")
        cfg = await _risk_cfg(ctx)
        limit = float(cfg.capital) * float(cfg.max_daily_loss_pct) / 100.0 if cfg is not None else 0.0
        if total < 0 and limit > 0:
            risk.add(f"That is {round(100.0 * -total / limit)}% of your own daily loss limit.")
    if unset:
        risk.add(f"{_plural(unset, 'position')} {'has' if unset == 1 else 'have'} no invalidation level set.")

    for t in opens:
        if position_state(t)["near"]:
            near.add(f"{t.symbol} ({'short' if _side(t.direction) < 0 else 'long'}): close to the invalidation you set for it.")

    for legs in units.values():
        cap = premium_kept(legs)
        if cap is None:
            continue
        label = _unit_label(legs)
        if cap["pct"] is None:
            premium.add(f"{label}: the premium kept shows once prices are in ({money(float(cap['received'] or 0)).lstrip('+')} came in).")
        else:
            premium.add(f"{label}: {cap['pct']:.0f}% of the premium kept so far ({money(cap['kept'] or 0)} of "
                        f"{money(float(cap['received'] or 0)).lstrip('+')}).")

    underlyings = {str(t.underlying_symbol or t.symbol).upper() for t in opens}
    end = ctx.today + timedelta(days=AHEAD_DAYS)
    for e in await _safely(ctx, "events", _events_for(ctx, ctx.today, end), []):
        scope = str(e.underlying or "*").upper()
        if scope in ("*", "") or scope in underlyings:
            ahead.add(_event_text(e))
    expiries: Dict[Tuple[date, str], int] = defaultdict(int)
    for t in opens:
        if t.expiry is not None and ctx.today <= t.expiry <= end:
            expiries[(t.expiry, str(t.underlying_symbol or t.symbol).upper())] += 1
    for (day, under), count in sorted(expiries.items()):
        ahead.add(f"{_day_label(day)}: your {under} contracts expire ({_plural(count, 'position')}).")

    sections = [risk] + [s for s in (near, premium) if s.lines] + [ahead]
    return risk.lines[0] if risk.lines else None, sections


# --- journal --------------------------------------------------------------------------------------------------------

def setup_extremes(units: Sequence[Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """(best, costliest) closed setup by net P&L: best only with a positive net, costliest only with a negative one."""
    groups: Dict[str, List[Any]] = defaultdict(list)
    for u in units:
        if u.closed:
            groups[u.setup or "-"].append(u)
    rows: List[Dict[str, Any]] = [{"setup": k, "net": round(sum(float(u.pnl or 0) for u in g), 2), "closed": len(g)}
                                  for k, g in groups.items()]
    best = max((r for r in rows if r["net"] > 0), key=lambda r: (r["net"], r["setup"]), default=None)
    worst = min((r for r in rows if r["net"] < 0), key=lambda r: (r["net"], r["setup"]), default=None)
    return best, worst


async def _suggested_tags(ctx: Ctx, start: datetime, end: datetime) -> int:
    q = select(func.count(JournalTagRecord.id)).join(TradeRecord, TradeRecord.id == JournalTagRecord.trade_id).where(
        JournalTagRecord.user_id == ctx.user.id, JournalTagRecord.tenant_id == ctx.user.tenant_id,
        JournalTagRecord.status == "SUGGESTED", TradeRecord.entry_time >= start, TradeRecord.entry_time < end)
    return int(await ctx.session.scalar(q) or 0)


async def journal_dish(ctx: Ctx) -> Tuple[Optional[str], List[Section]]:
    from app.journal import service as journal
    score = Section("score", "Your discipline", "No score yet this week: it starts with your first journaled trade. A week "
                    "without a trade is fine when nothing met your plan.")
    setups = Section("setups", "Your setups over the last four weeks", "No closed trades in the last four weeks yet.")
    habits = Section("habits", "Patterns this week", "No behaviour patterns found in this week's trades.")
    review = await _safely(ctx, "weekly review", journal.weekly_review(ctx.session, ctx.user, None, history_weeks=3, now=ctx.now), None)
    if review:
        value = (review.get("discipline") or {}).get("score")
        past = [h["score"] for h in reversed(review.get("history") or []) if h.get("score") is not None]   # oldest first
        if value is not None:
            score.add(f"Discipline score this week: {value} out of 100.")
            if past:
                prev = past[-1]
                trend = (f"up {value - prev} on last week" if value > prev else f"down {prev - value} on last week"
                         if value < prev else "the same as last week")
                score.add(f"That is {trend}. The weeks before: {', '.join(str(p) for p in past)} (oldest first).")
            score.add("It counts how closely you followed your own plan, not how many trades you took.")
        for b in sorted(review.get("behaviours") or [], key=lambda b: (-int(b.get("trades") or 0), str(b.get("tag"))))[:MAX_LINES]:
            label = BEHAVIOUR_WORDS.get(str(b.get("tag")))
            if not label or not b.get("trades"):
                continue
            text = f"{label[:1].upper() + label[1:]}: {_plural(int(b['trades']), 'trade')}"
            if b.get("closed"):
                text += f", net {money(float(b.get('net_pnl') or 0))}"
            habits.add(text + ".")
        start = _utc(datetime.fromisoformat(review["start"])) if review.get("start") else None
        end = _utc(datetime.fromisoformat(review["end"])) if review.get("end") else None
        if start and end:
            waiting = await _safely(ctx, "suggested tags", _suggested_tags(ctx, start, end), 0)
            if waiting:
                habits.add(f"{_plural(waiting, 'suggested tag')} {'waits' if waiting == 1 else 'wait'} for you to confirm or "
                           f"dismiss on the Week tab.")
            mine = await _safely(ctx, "week trades", journal.own_trades(ctx.session, ctx.user, start, end), [])
            closed = [t for t in mine if t.exit_time is not None and t.mode == "PAPER"]
            bare = sum(1 for t in closed if not (t.notes or "").strip())
            if closed and bare:
                habits.add(f"{bare} of this week's {_plural(len(closed), 'closed trade')} {'has' if bare == 1 else 'have'} no note "
                           f"of yours yet.")
    since = ctx.now - timedelta(weeks=SETUP_WEEKS)
    recent = await _safely(ctx, "recent trades", _closed_since(ctx, since), [])
    best, worst = setup_extremes(_units(recent))
    from app.signals_feed.service import strategy_name
    if best:
        setups.add(f"Best: {words.clean(strategy_name(best['setup'])) or 'a setup'} ({money(best['net'])} over "
                   f"{_plural(best['closed'], 'closed trade')}).")
    if worst:
        setups.add(f"Cost the most: {words.clean(strategy_name(worst['setup'])) or 'a setup'} ({money(worst['net'])} over "
                   f"{_plural(worst['closed'], 'closed trade')}).")
    if recent and not (best or worst):
        setups.add("Your closed setups of the last four weeks ended about even.")
    return (score.lines[0] if score.lines else None), [score, setups, habits]


# --- charts ---------------------------------------------------------------------------------------------------------

def _index_names() -> Dict[str, str]:
    from app.universe.indices import catalogue
    return {str(e["name"]).upper(): str(e["name"]) for e in catalogue()}


def lake_key(symbol: str) -> str:
    """The daily-history key of a chart symbol: an NSE index by its catalogue name, else an NSE cash share."""
    from app.context_panel.service import index_key
    name = _index_names().get(symbol.strip().upper())
    return index_key(name) if name else f"NSE:{symbol.strip().upper()}"


async def _daily_bars(ctx: Ctx, symbol: str) -> Any:
    import pandas as pd

    from app.market_lake.asof import candles_as_of
    lo = datetime.combine(ctx.today - timedelta(days=HISTORY_DAYS), datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    hi = datetime.combine(ctx.today + timedelta(days=1), datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    rows = await candles_as_of(ctx.session, lake_key(symbol), "day", lo, hi)
    days = [(_utc(r.ts) or r.ts).astimezone(IST).date() for r in rows]
    idx = pd.DatetimeIndex([pd.Timestamp(datetime.combine(d, datetime.min.time())) for d in days])
    return pd.DataFrame({"high": [float(r.high) for r in rows], "low": [float(r.low) for r in rows],
                         "close": [float(r.close) for r in rows]}, index=idx)


def where_close(close: float, swings: Sequence[Any]) -> Optional[str]:
    """Where the close sits against the last swing high and the last swing low (in words; no level shown)."""
    high = next((s for s in reversed(swings) if s.kind == "HIGH"), None)
    low = next((s for s in reversed(swings) if s.kind == "LOW"), None)
    if high is None or low is None:
        return None
    if close > float(high.price):
        return "the last close is above the last swing high"
    if close < float(low.price):
        return "the last close is below the last swing low"
    return "the last close is between the last swing low and the last swing high"


def story_lines(bars: Any) -> List[str]:
    """The chart's story from daily bars: daily swings with the last structure event and where the close sits, then the
    weekly swings - structure words only."""
    from app.context_panel.service import weekly
    from app.price_action.market_structure import analyze_market_structure
    if len(bars) < MIN_DAILY_BARS:
        return []
    daily = analyze_market_structure(bars, window=3)
    trend = daily.trend.value if hasattr(daily.trend, "value") else str(daily.trend)
    parts = [f"Daily swings: {words.trend_words(trend)}"]
    if daily.events:
        last = daily.events[-1]
        event = words.event_words({"event": last.event, "direction": last.direction})
        if event:
            parts.append(f"most recently {event}")
    place = where_close(float(bars["close"].iloc[-1]), daily.swings)
    if place:
        parts.append(place)
    out = ["; ".join(parts) + "."]
    weeks = weekly(bars)
    if len(weeks) >= MIN_WEEKLY_BARS:
        wk = analyze_market_structure(weeks, window=2)
        out.append(f"Weekly swings: {words.trend_words(wk.trend.value if hasattr(wk.trend, 'value') else str(wk.trend))}.")
    return out


async def charts_dish(ctx: Ctx, symbol: str, exchange: str = "NSE") -> Tuple[Optional[str], List[Section]]:
    from app.signals_feed import service as feed
    from app.watchlists import service as lists
    sym = symbol.strip().upper()
    story = Section("story", "The chart's story", f"No stored daily history for {sym} yet, so there is no structure read. "
                    "The chart above still shows its candles.")
    if exchange.strip().upper() in ("", "NSE"):
        bars = await _safely(ctx, "daily bars", _daily_bars(ctx, sym), None)
        if bars is not None and len(bars):
            lines = story_lines(bars)
            if lines:
                as_of = bars.index[-1].date()
                for line in lines:
                    story.add(line)
                if as_of != ctx.today:
                    story.add(f"Read from the close of {_day_label(as_of)}.")
            else:
                story.empty = f"Too little stored daily history for {sym} to read its swings yet."
    else:
        story.empty = "The structure read uses NSE's stored daily history, which this exchange does not have."

    yours = Section("yours", f"Yours on {sym}",
                    f"Nothing of yours on {sym} yet: no open position, no recent setup, and it is on none of your lists.")
    opens = await _safely(ctx, "open positions", _own_trades(ctx, open_only=True), [])
    mine = [t for t in opens if sym in (str(t.symbol).upper(), str(t.underlying_symbol or "").upper())]
    if mine:
        known = [p for p in (position_state(t)["pnl"] for t in mine) if p is not None]
        yours.add(f"You hold {_plural(len(mine), 'open paper position')} here"
                  + (f", open P&L {money(sum(known))}" if known else "") + ".")
        if any(position_state(t)["near"] for t in mine):
            yours.add("One of them is close to the invalidation you set for it.")
    page = await _safely(ctx, "setups", feed.feed(ctx.session, ctx.user, tab="history", symbol=sym, limit=5, now=ctx.now), {})
    for item in page.get("items", []):
        setup = _setup_line(item)
        at = _utc(datetime.fromisoformat(item["at"])) if item.get("at") else None
        if setup and at is not None:
            when = "today" if at.astimezone(IST).date() == ctx.today else "in the last thirty days"
            what = setup[len(sym) + 2:] if setup.startswith(sym + ": ") else setup
            yours.add(words.signal_line(f"Your latest setup here, {when}: {what}", [sym]))
            break
    rows = await _safely(ctx, "watchlists", lists.list_own(ctx.session, ctx.user.tenant_id, ctx.user.id), [])
    names = [r.name for r in rows if sym in {s.upper() for s in lists.symbols_of(r)}]
    if names:
        yours.add(f"On your lists: {', '.join(names[:4])}" + (f" and {len(names) - 4} more" if len(names) > 4 else "") + ".")
    # The headline is the daily swings alone (one short line above the chart); the details tell the rest of the story.
    headline = story.lines[0].split(";")[0].rstrip(".") + "." if story.lines else None
    return headline, [story, yours]


# --- find setups ----------------------------------------------------------------------------------------------------

def match_groups(items: Iterable[Dict[str, Any]]) -> List[Tuple[str, List[str]]]:
    """Today's scan matches as (scan name, symbols in order of arrival, newest first), most matches first."""
    groups: Dict[str, List[str]] = {}
    for item in items:
        if (item.get("source") or {}).get("kind") != "scan":
            continue
        name = words.clean((item.get("source") or {}).get("name") or item.get("setup")) or "Your scan"
        sym = str(item.get("symbol") or "").upper()
        if sym and sym not in groups.setdefault(name, []):
            groups[name].append(sym)
    return sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))


async def _active_scans(ctx: Ctx) -> int:
    q = select(func.count(AlertRuleRecord.id)).where(AlertRuleRecord.tenant_id == ctx.user.tenant_id,
                                                     AlertRuleRecord.created_by == ctx.user.id, AlertRuleRecord.status == "active")
    return int(await ctx.session.scalar(q) or 0)


async def screener_dish(ctx: Ctx) -> Tuple[Optional[str], List[Section]]:
    from app.signals_feed import service as feed
    from app.watchlists import service as lists
    today = Section("today", "Today's matches", "No matches from your scans yet today. New matches arrive here during the session.",
                    link={"label": "Open Signals", "to": "/signals"})
    page = await _safely(ctx, "scan matches", feed.feed(ctx.session, ctx.user, tab="today", source="scan", limit=100, now=ctx.now), {})
    groups = match_groups(page.get("items", []))
    total = len({s for _, syms in groups for s in syms})
    for name, syms in groups[:4]:
        shown = ", ".join(syms[:4]) + (f" and {len(syms) - 4} more" if len(syms) > 4 else "")
        today.add(f"\"{name}\": {_plural(len(syms), 'match', 'matches')} - {shown}.")
    if len(groups) > 4:
        today.add(f"{_plural(len(groups) - 4, 'more scan')} matched too; Signals lists every match.")
    rows = await _safely(ctx, "watchlists", lists.list_own(ctx.session, ctx.user.tenant_id, ctx.user.id), [])
    listed = {s.upper() for r in rows for s in lists.symbols_of(r)}
    on_lists = [s for _, syms in groups for s in syms if s in listed]
    on_lists = list(dict.fromkeys(on_lists))
    if on_lists:
        today.add(f"On your own lists: {', '.join(on_lists[:6])}" + (f" and {len(on_lists) - 6} more" if len(on_lists) > 6 else "") + ".")
    scans = Section("scans", "Your scans that run on their own",
                    "None of your scans runs on its own yet. Save a scan with alerts on and its matches show here during the session.")
    active = await _safely(ctx, "active scans", _active_scans(ctx), 0)
    if active:
        scans.add(f"{_plural(active, 'scan')} of yours {'runs on its own' if active == 1 else 'run on their own'}; new matches "
                  f"show here and on Signals.")
    headline = (f"{_plural(total, 'symbol')} matched your scans today." if total else None)
    return headline, [today, scans]


# --- the answer -----------------------------------------------------------------------------------------------------

def _guard(sections: Sequence[Section]) -> None:
    """The last check before anything is shown: every line again through the rhythm's guard (call words, advice)."""
    for s in sections:
        s.lines = [line for line in (words.clean(x) for x in s.lines) if line]


async def build(session: AsyncSession, user: Any, page: str, *, symbol: Optional[str] = None, exchange: str = "NSE",
                now: Optional[datetime] = None) -> Dict[str, Any]:
    """The dish of `page` for `user`: {page, title, headline, available, sections: [{id, title, lines, empty, link}], as_of,
    note}. `available` is false for the journal while the journal is off (the page says so itself)."""
    if page not in PAGES:
        raise ValueError(f"unknown page {page!r}")
    now = _utc(now) or datetime.now(timezone.utc)
    ctx = await _ctx(session, user, now)
    title = TITLES[page]
    headline: Optional[str]
    if page == "signals":
        headline, sections = await signals_dish(ctx)
    elif page == "positions":
        headline, sections = await positions_dish(ctx)
    elif page == "journal":
        if not ctx.flags.get("discipline_engine"):
            return {"page": page, "title": title, "headline": None, "available": False, "sections": [], "as_of": now.isoformat(),
                    "note": NOTE}
        headline, sections = await journal_dish(ctx)
    elif page == "charts":
        if not symbol or not symbol.strip():
            raise ValueError("a symbol is needed")
        headline, sections = await charts_dish(ctx, symbol, exchange)
    else:
        headline, sections = await screener_dish(ctx)
    _guard(sections)
    return {"page": page, "title": title, "headline": words.clean(headline), "available": True,
            "sections": [s.as_dict() for s in sections], "as_of": now.isoformat(), "note": NOTE}


__all__ = ["PAGES", "TITLES", "NOTE", "build", "strategy_record_lines", "at_invalidation", "premium_kept", "setup_extremes",
           "story_lines", "where_close", "match_groups", "lake_key", "BEHAVIOUR_WORDS"]
