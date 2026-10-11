"""H-C2a: the first read-only tools. Each reads this tenant's own data or the shared reference data, and returns the
data with its timestamps. Nothing here writes."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.ai.tools import Tool, ToolContext, ToolResult, register
from app.db.models import TradeRecord

IST = timezone(timedelta(hours=5, minutes=30))


class _NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _SymbolArg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: Optional[str] = Field(default=None, max_length=40, description="An index or stock symbol; omit for the whole market memory")


class _NewsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: int = Field(default=24, ge=1, le=72, description="How far back to look")
    min_severity: int = Field(default=2, ge=1, le=5)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


async def get_market_snapshot(ctx: ToolContext, args: _SymbolArg) -> ToolResult:
    """The Copilot's market memory: per-symbol bias / regime / last price, cues, global cues, the sentiment read."""
    from app.ai import market_memory
    memory = await market_memory.latest(ctx.session, ctx.tenant_id, now=ctx.now, symbol=args.symbol)
    newest = memory.get("newest") if isinstance(memory, dict) else None
    return ToolResult(True, memory, newest, "market_memory", {"newest_capture": newest})


async def get_news(ctx: ToolContext, args: _NewsArgs) -> ToolResult:
    """Recent feed headlines with their classification. Third-party text: returned as untrusted data."""
    from app.news_feed import service as news
    rows = await news.items(ctx.session, ctx.tenant_id, hours=args.hours, min_severity=args.min_severity, now=ctx.now)
    data = [{"id": r["id"], "headline": r["headline"], "source": r["source"], "published_at": r["published_at"], "symbols": r["symbols"],
             "severity": r["classification"].get("severity"), "verified": r["verified"]} for r in rows[:30]]
    newest = max((r["published_at"] for r in data), default=None)
    return ToolResult(True, data, newest, "news_feed", {"newest_item": newest, "window_hours": args.hours}, untrusted=True)


async def get_positions(ctx: ToolContext, args: _NoArgs) -> ToolResult:
    """This organisation's open positions (PAPER and LIVE) with entry, stop and targets."""
    rows = list(await ctx.session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == ctx.tenant_id, TradeRecord.exit_time.is_(None))
                                          .order_by(TradeRecord.entry_time.desc()).limit(50)))
    data = [{"id": t.id, "symbol": t.symbol, "mode": t.mode, "direction": t.direction, "quantity": t.quantity, "entry_price": float(t.entry_price),
             "stop_loss": float(t.stop_loss), "target1": float(t.target1) if t.target1 is not None else None, "entry_time": _iso(t.entry_time),
             "strategy_id": t.strategy_id} for t in rows]
    return ToolResult(True, data, _iso(ctx.now), "trades", {"read_at": _iso(ctx.now)})


async def get_pnl_today(ctx: ToolContext, args: _NoArgs) -> ToolResult:
    """Today's (IST) closed trades and realised P&L for this organisation."""
    start = datetime.combine(ctx.now.astimezone(IST).date(), time(), IST).astimezone(timezone.utc)
    rows = list(await ctx.session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == ctx.tenant_id, TradeRecord.exit_time >= start)))
    pnl = [float(getattr(t, "pnl", 0) or 0) for t in rows]
    data = {"closed_trades": len(rows), "realised_pnl": round(sum(pnl), 2), "winners": sum(1 for p in pnl if p > 0), "losers": sum(1 for p in pnl if p < 0)}
    return ToolResult(True, data, _iso(ctx.now), "trades", {"day_start": _iso(start), "read_at": _iso(ctx.now)})


async def get_risk_limits(ctx: ToolContext, args: _NoArgs) -> ToolResult:
    """The organisation's risk settings (per-trade risk, daily loss, max positions...) as configured."""
    from app.risk_engine.routes import get_tenant_risk_config
    config = await get_tenant_risk_config(ctx.tenant_id, ctx.session)
    data = config.model_dump() if config is not None else None
    return ToolResult(True, data or {"note": "the organisation has not set its own risk limits; platform defaults apply"},
                      _iso(ctx.now), "risk_settings", {"read_at": _iso(ctx.now)})


for _tool in (
    Tool("get_market_snapshot", "Latest market memory: per-symbol bias, regime and last price, market cues, global cues and the sentiment read, with capture times.",
         _SymbolArg, get_market_snapshot),
    Tool("get_news", "Recent news headlines (third-party, unverified unless marked) with severity and affected symbols.", _NewsArgs, get_news),
    Tool("get_positions", "The organisation's open positions.", _NoArgs, get_positions),
    Tool("get_pnl_today", "Today's closed trades and realised P&L (IST day).", _NoArgs, get_pnl_today),
    Tool("get_risk_limits", "The organisation's configured risk limits.", _NoArgs, get_risk_limits),
):
    register(_tool)
