"""Item 5 (owner's handover, AI Copilot as a trader's brain), second slice: the read tools behind the chat-first Copilot's
jobs 4 (position watch) and 5 (post-trade coach). Job 3 (pre-trade check) reuses the first slice's tools.

Each tool reads only the logged-in trader's OWN rows (`user_id` and `tenant_id`), like the journal (Z6):

* `get_my_positions`   - the trader's open trades (PAPER and LIVE), the legs of one multi-leg structure as one position:
                         entry, the stop now and as opened, the last price the position monitor saw and when, the
                         marked P&L, the loss if the stop is hit, how far the price has moved from the entry towards the
                         stop; and the trader's realised P&L today (IST). No flag: the Positions page shows the same rows.
* `get_my_last_trade`  - the trader's most recent closed trade: the levels as planned and as executed, the result in
                         money and in R, how it was closed, how long it was held, the stop changes, the furthest move
                         for and against it (journal MAE / MFE) and the discipline engine's tags on it. No flag.
* `get_weekly_review`  - the discipline engine's weekly review (`app/journal/service.weekly_review`, the same as
                         `GET /api/journal/review`: every number from the trader's own journal, descriptive lines) plus
                         the behaviour tags that repeat across the trader's last weeks. Flag `discipline_engine`.

All three are `read` tools: none places, blocks or changes an order (ADR-0006). The weekly review refreshes the
journal's AUTO tags exactly as its own page does on every read; it never touches a trade or an order.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.ai.tools import Tool, ToolContext, ToolResult, register
from app.db.models import JournalEntryRecord, JournalTagRecord, TradeRecord

IST = timezone(timedelta(hours=5, minutes=30))
MAX_POSITIONS = 20
PATTERN_WEEKS = 4                 # this week and the three before it


class _NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _WeekArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    week: Optional[str] = Field(default=None, pattern=r"^\d{4}-W\d{2}$", description="An ISO week like 2026-W43; omit for this week")


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _num(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _r2(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 2)


def _side(trade: TradeRecord) -> str:
    from app.portfolio.risk import direction_of
    return direction_of(trade).lower()          # bullish / bearish / neutral on the underlying


# --- job 4: the trader's own open positions --------------------------------------------------------------------------------

def position_row(t: TradeRecord) -> Dict[str, Any]:
    """One open trade, with the numbers the watch reads (every derived number is computed here, from the row)."""
    sign = 1.0 if (t.direction or "").upper() == "LONG" else -1.0
    entry, stop, qty = float(t.entry_price), _num(t.stop_loss), abs(float(t.quantity))
    initial = _num(t.initial_stop_loss)
    mark = _num(t.mark_price)
    marked = round(sign * (mark - entry) * qty, 2) if mark is not None else None
    loss_if_stop = round(sign * (entry - stop) * qty, 2) if stop else None           # > 0: a loss; <= 0: the stop locks a gain
    moved_pct = None
    if mark is not None and stop and entry != stop and sign * (mark - entry) < 0 and sign * (entry - stop) > 0:
        moved_pct = round(100.0 * abs(mark - entry) / abs(entry - stop), 1)
    widened = bool(stop and initial and abs(entry - stop) > abs(entry - initial) + 1e-9 and sign * (entry - stop) > 0)
    return {"id": t.id, "symbol": t.symbol, "underlying": t.underlying_symbol or t.symbol, "mode": t.mode, "side": _side(t),
            "kind": t.instrument_kind or "UNDERLYING", "quantity": qty, "entry_price": entry, "stop_now": stop, "initial_stop": initial,
            "stop_widened": widened, "mark_price": mark, "mark_time": _iso(t.mark_time), "marked_pnl": marked,
            "loss_if_stop_hit": loss_if_stop, "moved_to_stop_pct": moved_pct, "expiry": t.expiry.isoformat() if t.expiry else None,
            "setup": t.strategy_id, "entry_time": _iso(t.entry_time), "group": t.leg_group_id}


def group_rows(trades: List[TradeRecord]) -> List[Dict[str, Any]]:
    """Singles as they are; the legs of one structure as one position at the structure's risk (the portfolio model's rule)."""
    from app.portfolio.risk import positions_of
    rows: List[Dict[str, Any]] = []
    groups: Dict[str, List[TradeRecord]] = defaultdict(list)
    for t in trades:
        if t.leg_group_id:
            groups[t.leg_group_id].append(t)
        else:
            rows.append(position_row(t))
    for gid, legs in groups.items():
        pos = positions_of(legs)[0]
        legs_rows = [position_row(t) for t in legs]
        marks = [r["marked_pnl"] for r in legs_rows]
        rows.append({"id": legs[0].id, "symbol": pos.underlying, "underlying": pos.underlying, "mode": legs[0].mode, "side": pos.direction.lower(),
                     "kind": "STRUCTURE", "legs": len(legs), "quantity": None, "entry_price": None, "stop_now": None, "initial_stop": None,
                     "stop_widened": False, "mark_price": None,
                     "mark_time": min((r["mark_time"] for r in legs_rows if r["mark_time"]), default=None),
                     "marked_pnl": round(sum(m for m in marks if m is not None), 2) if all(m is not None for m in marks) else None,
                     "loss_if_stop_hit": round(pos.risk, 2), "moved_to_stop_pct": None,
                     "expiry": min((r["expiry"] for r in legs_rows if r["expiry"]), default=None), "setup": legs[0].strategy_id,
                     "entry_time": legs_rows[0]["entry_time"], "group": gid})
    return rows


async def get_my_positions(ctx: ToolContext, args: _NoArgs) -> ToolResult:
    """Your own open positions (PAPER and LIVE): entry, the stop now and as opened, the last price seen and when, the
    marked P&L, the loss if the stop is hit and how far the price has moved towards the stop; and your realised P&L today."""
    rows = list(await ctx.session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == ctx.tenant_id, TradeRecord.user_id == ctx.user.id, TradeRecord.exit_time.is_(None))
        .order_by(TradeRecord.entry_time.desc(), TradeRecord.id.desc()).limit(MAX_POSITIONS * 4)))
    positions = group_rows(rows)[:MAX_POSITIONS]
    start = datetime.combine(ctx.now.astimezone(IST).date(), time(), IST).astimezone(timezone.utc)
    closed = list(await ctx.session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == ctx.tenant_id, TradeRecord.user_id == ctx.user.id, TradeRecord.exit_time >= start)))
    realised = round(sum(float(t.pnl or 0) for t in closed), 2)
    risks = [p["loss_if_stop_hit"] for p in positions if p["loss_if_stop_hit"] is not None and p["loss_if_stop_hit"] > 0]
    marks = [p["marked_pnl"] for p in positions if p["marked_pnl"] is not None]
    sides: Dict[str, int] = defaultdict(int)
    for p in positions:
        sides[p["side"]] += 1
    data = {"count": len(positions), "positions": positions, "total_loss_if_stops_hit": round(sum(risks), 2),
            "total_marked_pnl": round(sum(marks), 2) if marks else None, "unpriced": sum(1 for p in positions if p["marked_pnl"] is None),
            "bullish": sides["bullish"], "bearish": sides["bearish"], "neutral": sides["neutral"],
            "realised_today": realised, "closed_today": len(closed), "read_at": _iso(ctx.now)}
    newest = max((p["mark_time"] for p in positions if p["mark_time"]), default=None)
    return ToolResult(True, data, _iso(ctx.now), "trades", {"read_at": _iso(ctx.now), "newest_mark": newest})


# --- job 5: the last closed trade and the weekly review ----------------------------------------------------------------------

EXIT_WORDS = (("trail", "the trailing stop closed it"), ("breakeven", "the break-even stop closed it"), ("stop", "the stop was hit"),
              ("target", "your planned exit level was reached"), ("manual", "you closed it by hand"), ("by hand", "you closed it by hand"),
              ("exit now", "you closed it by hand"), ("ai action", "you closed it from an approved Copilot proposal"),
              ("eod", "the end-of-day exit closed it"), ("square", "the square-off closed it"), ("time", "the time exit closed it"),
              ("expir", "it was closed at expiry"), ("kill", "the kill switch closed it"), ("invalidat", "the invalidation rule closed it"))


def exit_words(reason: Optional[str]) -> str:
    text = (reason or "").lower()
    return next((words for marker, words in EXIT_WORDS if marker in text), "it was closed")


async def get_my_last_trade(ctx: ToolContext, args: _NoArgs) -> ToolResult:
    """Your most recent closed trade: planned and executed levels, the result in money and in R, how it was closed, how
    long it was held, the stop changes, the furthest move for and against it, and the discipline engine's tags on it."""
    from app.journal import capture
    from app.journal.review import TAG_LABELS
    from app.journal.service import excursions
    trade = await ctx.session.scalar(select(TradeRecord).where(
        TradeRecord.tenant_id == ctx.tenant_id, TradeRecord.user_id == ctx.user.id, TradeRecord.exit_time.is_not(None))
        .order_by(TradeRecord.exit_time.desc(), TradeRecord.id.desc()).limit(1))
    if trade is None:
        return ToolResult(True, {"trade": None, "reason": "you have no closed trade yet"}, _iso(ctx.now), "trades", {"read_at": _iso(ctx.now)})
    entry = await ctx.session.scalar(select(JournalEntryRecord).where(JournalEntryRecord.trade_id == trade.id))
    tags = list(await ctx.session.scalars(select(JournalTagRecord).where(
        JournalTagRecord.user_id == ctx.user.id, JournalTagRecord.trade_id == trade.id, JournalTagRecord.status != "DISMISSED")
        .order_by(JournalTagRecord.tag)))
    plan = capture.plan_of(trade)
    stop0 = _num(plan.get("stop"))
    qty, fill = abs(float(trade.quantity)), float(trade.entry_price)
    risk = round(abs(fill - stop0) * qty, 2) if stop0 else 0.0
    pnl = _num(trade.pnl)
    r_mult = round(pnl / risk, 2) if pnl is not None and risk > 0 else None
    stop_history = capture._loads(entry.stop_history_json, []) if entry is not None else []
    exc = excursions(trade, entry)
    entry_time, exit_time = trade.entry_time, trade.exit_time
    held = None
    if entry_time and exit_time:
        a = entry_time if entry_time.tzinfo else entry_time.replace(tzinfo=timezone.utc)
        b = exit_time if exit_time.tzinfo else exit_time.replace(tzinfo=timezone.utc)
        held = round((b - a).total_seconds() / 60.0)
    data = {"trade": {
        "id": trade.id, "symbol": trade.symbol, "mode": trade.mode, "side": _side(trade), "setup": trade.strategy_id,
        "entry_date": (trade.entry_time.astimezone(IST).date().isoformat() if trade.entry_time and trade.entry_time.tzinfo
                       else trade.entry_time.date().isoformat() if trade.entry_time else None),
        "exit_date": (exit_time.astimezone(IST).date().isoformat() if exit_time and exit_time.tzinfo
                      else exit_time.date().isoformat() if exit_time else None),
        "planned_entry": _num(plan.get("entry")), "entry_price": fill, "exit_price": _num(trade.exit_price), "quantity": qty,
        "initial_stop": stop0, "stop_at_exit": _num(trade.stop_loss), "planned_rr": _num(plan.get("rr")),
        "risk_planned": risk, "pnl": pnl, "charges": _r2(_num(trade.charges)), "r_multiple": r_mult,
        "exit_reason": trade.exit_reason, "exit_words": exit_words(trade.exit_reason), "held_minutes": held,
        "stop_changes": len(stop_history) if isinstance(stop_history, list) else 0,
        "mfe_r": exc.get("mfe_r"), "mae_r": exc.get("mae_r"),
        "tags": [{"tag": t.tag, "label": TAG_LABELS.get(t.tag, t.tag.replace("_", " ")), "status": t.status, "source": t.source} for t in tags]}}
    return ToolResult(True, data, _iso(trade.exit_time), "trades", {"exit_time": _iso(trade.exit_time)})


async def get_weekly_review(ctx: ToolContext, args: _WeekArgs) -> ToolResult:
    """The discipline engine's weekly review of your own journal (totals, setups, behaviour costs, the discipline score,
    your previous weeks, descriptive lines) and the behaviour tags that repeat across your last four weeks."""
    from app.journal import service
    from app.journal.review import TAG_LABELS
    from app.journal.score import TagState, counted
    try:
        review = await service.weekly_review(ctx.session, ctx.user, args.week, history_weeks=PATTERN_WEEKS - 1, now=ctx.now)
    except service.WeekError as exc:
        return ToolResult(False, None, None, "journal", error=str(exc))
    weeks = [review["week"], *(h["week"] for h in review["history"])]
    start = service.week_bounds(weeks[-1], now=ctx.now)[1]
    end = service.week_bounds(weeks[0], now=ctx.now)[2]
    rows = (await ctx.session.execute(select(JournalTagRecord.trade_id, JournalTagRecord.tag, JournalTagRecord.status, TradeRecord.entry_time)
                                      .join(TradeRecord, TradeRecord.id == JournalTagRecord.trade_id)
                                      .where(JournalTagRecord.user_id == ctx.user.id, TradeRecord.user_id == ctx.user.id,
                                             TradeRecord.tenant_id == ctx.tenant_id, TradeRecord.entry_time >= start,
                                             TradeRecord.entry_time < end))).all()
    from app.journal import settings as journal_settings
    s = await journal_settings.load(ctx.session, ctx.user)
    live = {(x.trade_id, x.tag) for x in counted([TagState(r[0], r[1], r[2]) for r in rows], s)}
    per_tag: Dict[str, Dict[str, Any]] = {}
    for trade_id, tag, _status, entry_time in rows:
        if (trade_id, tag) not in live:
            continue
        week = service.week_bounds(None, now=entry_time if entry_time.tzinfo else entry_time.replace(tzinfo=timezone.utc))[0]
        item = per_tag.setdefault(tag, {"tag": tag, "label": TAG_LABELS.get(tag, tag), "weeks": set(), "trades": set()})
        item["weeks"].add(week)
        item["trades"].add(trade_id)
    patterns = sorted(({"tag": v["tag"], "label": v["label"], "weeks": len(v["weeks"]), "of_weeks": len(weeks), "trades": len(v["trades"])}
                       for v in per_tag.values() if len(v["weeks"]) >= 2), key=lambda p: (-p["weeks"], -p["trades"], p["tag"]))
    data = {"week": review["week"], "totals": review["totals"], "behaviours": review["behaviours"], "costliest": review["costliest"],
            "discipline": {"score": review["discipline"].get("score")}, "history": review["history"], "lines": review["lines"],
            "what_worked": review["what_worked"], "what_lost": review["what_lost"], "patterns": patterns, "weeks_read": len(weeks)}
    return ToolResult(True, json.loads(json.dumps(data, default=str)), review.get("end"), "journal", {"week": review["week"]})


for _tool in (
    Tool("get_my_positions", get_my_positions.__doc__ or "", _NoArgs, get_my_positions, cost_units=1, timeout_seconds=10),
    Tool("get_my_last_trade", get_my_last_trade.__doc__ or "", _NoArgs, get_my_last_trade, cost_units=1, timeout_seconds=10),
    Tool("get_weekly_review", get_weekly_review.__doc__ or "", _WeekArgs, get_weekly_review, cost_units=2, timeout_seconds=20,
         flag="discipline_engine"),
):
    register(_tool)

__all__ = ["get_my_positions", "get_my_last_trade", "get_weekly_review", "position_row", "group_rows", "exit_words"]
