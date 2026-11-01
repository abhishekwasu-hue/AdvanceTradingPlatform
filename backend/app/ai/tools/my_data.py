"""M72 PR-1c (CP-1, findings F6 / F18): the read tools the Copilot planner was missing, and its calculators.

Every tool here reads only the logged-in trader's OWN rows (`tenant_id` AND `user_id`, from the session, never from the
model), is typed (pydantic input, extra keys refused), carries its read time (`as_of`) and is offered only while the
flag `copilot_agent` is on for the organisation. None writes anything; none places, blocks or changes an order
(ADR-0006) - the orders tool only READS the order book.

* `get_my_orders`         - his waiting (resting) orders and his latest order attempts with their status.
* `get_my_trade_settings` - his own Settings › Trade settings: every field he can see, its value or "Not set" (M68: a
                            Not set value is never filled in by the Copilot - the answer asks for it).
* `get_journal_entries`   - his latest journalled trades: the levels, the result, his own checklist and labels.
* `get_watchlist`         - his own watchlists and their symbols.
* `calc_difference`, `calc_percent_change`, `calc_sum` (F6) - deterministic arithmetic over numbers that already came
  from a tool result or his own message (the agent loop refuses any other input), so a derived figure (points to his
  stop, a combined P&L) is computed by code and recorded as a tool call - never by the model.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.ai.tools import Tool, ToolContext, ToolResult, register
from app.db.models import JournalEntryRecord, OrderRecord, PaperRestingOrderRecord, TradeRecord

FLAG = "copilot_agent"
CALC_TOOLS = ("calc_difference", "calc_percent_change", "calc_sum")
NOT_SET = "Not set"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


def _num(value: Any) -> float | None:
    try:
        return None if value is None else round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _json(raw: str | None, default: Any) -> Any:
    try:
        return json.loads(raw) if raw else default
    except ValueError:
        return default


class _Orders(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=10, ge=1, le=30, description="How many of the latest order attempts to list")


class _Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section: str | None = Field(default=None, max_length=40, description="One section id (order_defaults, position_size, exits, options, "
                                                                         "risk_limits, automation, paper_account); omit for all")


class _Journal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=10, ge=1, le=30, description="How many of the latest journalled trades")
    symbol: str | None = Field(default=None, max_length=40)


class _Watchlist(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=60, description="One list's name; omit for all of his lists")


class _Two(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: float = Field(description="The first number, exactly as it appears in a tool result or the user's message", allow_inf_nan=False)
    b: float = Field(description="The second number, exactly as it appears in a tool result or the user's message", allow_inf_nan=False)
    label: str | None = Field(default=None, max_length=80, description="What the result means, in a few words")


class _Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float = Field(description="The starting value (from a tool result or the user's message)", allow_inf_nan=False)
    end: float = Field(description="The new value (from a tool result or the user's message)", allow_inf_nan=False)
    label: str | None = Field(default=None, max_length=80)


class _Sum(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: list[float] = Field(min_length=1, max_length=20, description="Numbers from tool results or the user's message")
    label: str | None = Field(default=None, max_length=80)


async def get_my_orders(ctx: ToolContext, args: _Orders) -> ToolResult:
    """His waiting orders (resting: not yet triggered) and his latest order attempts. Read only."""
    resting = list(await ctx.session.scalars(select(PaperRestingOrderRecord).where(
        PaperRestingOrderRecord.tenant_id == ctx.tenant_id, PaperRestingOrderRecord.user_id == ctx.user.id,
        PaperRestingOrderRecord.status.in_(("RESTING", "FIRING"))).order_by(PaperRestingOrderRecord.created_at.desc()).limit(30)))
    recent = list(await ctx.session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == ctx.tenant_id, OrderRecord.user_id == ctx.user.id)
                                            .order_by(OrderRecord.created_at.desc(), OrderRecord.id.desc()).limit(args.limit)))
    data = {
        "waiting": [{"id": r.id, "symbol": r.symbol, "mode": r.mode, "purpose": r.purpose, "kind": r.kind, "side": r.side, "quantity": _num(r.quantity),
                     "order_type": r.order_type, "price": _num(r.price), "trigger_level": _num(r.trigger_level), "trigger_side": r.trigger_side,
                     "status": r.status, "placed_at": _iso(r.created_at), "expires_at": _iso(r.expires_at)} for r in resting],
        "recent": [{"id": o.id, "symbol": o.symbol, "mode": o.mode, "direction": o.direction, "quantity": _num(o.quantity), "status": o.status,
                    "at": _iso(o.created_at)} for o in recent],
    }
    return ToolResult(True, data, _iso(ctx.now), "orders", {"read_at": _iso(ctx.now)})


async def get_my_trade_settings(ctx: ToolContext, args: _Settings) -> ToolResult:
    """His own Trade settings as the Settings page shows them to him; a field he never saved is "Not set"."""
    from app.me import prefs, trade_settings
    value, _version = await prefs.read_one(trade_settings.NAMESPACE, ctx.user, ctx.session)
    values = trade_settings.clean(value.get("values"))
    from app.platform.controls import risk_ceilings
    # As the Settings page shows them (trade_settings.read): an organisation-scope field shows the ceiling in force.
    shown = trade_settings.view(values, False, await risk_ceilings(ctx.session, ctx.tenant_id),
                                off=await trade_settings.flags_off(ctx.session, ctx.tenant_id))
    sections = []
    for s in shown["sections"]:
        if args.section and s["id"] != args.section:
            continue
        fields = [{"key": f["key"], "label": f["label"], "unit": f["unit"] or None,
                   "value": f["value"] if f["saved"] or f["fact"] else NOT_SET} for f in s["fields"]]
        if fields:
            sections.append({"id": s["id"], "title": s["title"], "fields": fields})
    if args.section and not sections:
        return ToolResult(False, None, None, "trade_settings", error=f"no Trade settings section {args.section!r}")
    return ToolResult(True, {"sections": sections, "not_set_means": "he has not chosen a value; ask him, never assume one"},
                      _iso(ctx.now), "trade_settings", {"read_at": _iso(ctx.now)})


async def get_journal_entries(ctx: ToolContext, args: _Journal) -> ToolResult:
    """His latest journalled trades (newest first): levels, result and his own checklist / labels."""
    query = (select(JournalEntryRecord, TradeRecord).join(TradeRecord, TradeRecord.id == JournalEntryRecord.trade_id)
             .where(JournalEntryRecord.tenant_id == ctx.tenant_id, JournalEntryRecord.user_id == ctx.user.id,
                    TradeRecord.tenant_id == ctx.tenant_id, TradeRecord.user_id == ctx.user.id))
    if args.symbol:
        query = query.where(TradeRecord.symbol == args.symbol.strip().upper())
    rows = (await ctx.session.execute(query.order_by(TradeRecord.entry_time.desc()).limit(args.limit))).all()
    data = [{"trade_id": t.id, "symbol": t.symbol, "mode": t.mode, "direction": t.direction, "quantity": _num(t.quantity),
             "entry_price": _num(t.entry_price), "stop": _num(t.stop_loss), "exit_price": _num(t.exit_price), "pnl": _num(t.pnl),
             "entered_at": _iso(t.entry_time), "exited_at": _iso(t.exit_time), "exit_reason": t.exit_reason,
             "checklist": _json(j.checklist_json, None), "labels": _json(j.user_tags_json, None)} for j, t in rows]
    return ToolResult(True, data, _iso(ctx.now), "journal", {"read_at": _iso(ctx.now)})


async def get_watchlist(ctx: ToolContext, args: _Watchlist) -> ToolResult:
    """His own watchlists (names and symbols)."""
    from app.watchlists import service
    lists = await service.list_own(ctx.session, ctx.tenant_id, int(ctx.user.id))
    data = [{"name": w.name, "symbols": service.symbols_of(w)} for w in lists if not args.name or w.name.lower() == args.name.strip().lower()]
    if args.name and not data:
        return ToolResult(False, None, None, "watchlists", error=f"no watchlist named {args.name!r}")
    return ToolResult(True, data, _iso(ctx.now), "watchlists", {"read_at": _iso(ctx.now)})


def _r(value: float) -> float:
    return round(value, 4)


async def calc_difference(ctx: ToolContext, args: _Two) -> ToolResult:
    diff = args.a - args.b
    data = {"a": args.a, "b": args.b, "a_minus_b": _r(diff), "absolute": _r(abs(diff)), "label": args.label}
    return ToolResult(True, data, _iso(ctx.now), "calc", {"computed_at": _iso(ctx.now)})


async def calc_percent_change(ctx: ToolContext, args: _Change) -> ToolResult:
    if args.start == 0:
        return ToolResult(False, None, None, "calc", error="the starting value is 0, so a percent change has no meaning")
    change = args.end - args.start
    data = {"start": args.start, "end": args.end, "change": _r(change), "percent_change": _r(change / abs(args.start) * 100), "label": args.label}
    return ToolResult(True, data, _iso(ctx.now), "calc", {"computed_at": _iso(ctx.now)})


async def calc_sum(ctx: ToolContext, args: _Sum) -> ToolResult:
    data = {"values": args.values, "sum": _r(sum(args.values)), "count": len(args.values), "label": args.label}
    return ToolResult(True, data, _iso(ctx.now), "calc", {"computed_at": _iso(ctx.now)})


for _tool in (
    Tool("get_my_orders", "Your own waiting orders (not yet triggered) and your latest order attempts with their status. Read only.", _Orders,
         get_my_orders, flag=FLAG),
    Tool("get_my_trade_settings", "Your own Trade settings (order defaults, position size, exits, options, risk limits, automation): each value "
         "or 'Not set'. A Not set value must be asked for, never assumed.", _Settings, get_my_trade_settings, flag=FLAG),
    Tool("get_journal_entries", "Your own latest journalled trades: levels, result, your checklist and labels.", _Journal, get_journal_entries, flag=FLAG),
    Tool("get_watchlist", "Your own watchlists and their symbols.", _Watchlist, get_watchlist, flag=FLAG),
    Tool("calc_difference", "Computes a - b (and its absolute value) from two numbers that appear in tool results or the user's message. "
         "Use it for any distance or gap you state.", _Two, calc_difference, flag=FLAG, timeout_seconds=2.0),
    Tool("calc_percent_change", "Computes the change and the percent change from start to end (numbers from tool results or the user's message).",
         _Change, calc_percent_change, flag=FLAG, timeout_seconds=2.0),
    Tool("calc_sum", "Adds up to 20 numbers from tool results or the user's message (for a combined total).", _Sum, calc_sum, flag=FLAG,
         timeout_seconds=2.0),
):
    register(_tool)
