"""Trading Terminal T1: the status bar's figures for the signed-in user (docs/owner-orders/ATP_SESSION_TRADING_TERMINAL.md
T1, review docs/design/TRADING_TERMINAL_REVIEW.md TT-15, TT-19).

Read only. Every row read here belongs to the caller: trades filter on tenant AND user (TT-15: the Terminal never shows a
teammate's positions), the hub status is the caller's own price connection, the kill switch is the organisation's (and the
platform's) state as the existing kill-switch status reads it. Nothing here places, changes or blocks an order, and no
order path imports this module (tests/test_z10c_order_paths.py stays unchanged).

- `today`: what was booked today (closed PAPER trades since midnight IST, after the estimated charges) plus the open
  positions' profit or loss at their last marked price. A position carried from an earlier day counts from its entry
  price (ATP keeps no previous close per position): said in `note`.
- `open`: the open positions' profit or loss at the last marked price, how many have a mark, and when the newest was taken.
- `funds`: the practice balance (starting capital plus the net result of closed PAPER trades, as the paper account shows)
  minus the margin estimate below.
- `margin`: ATP's labelled estimate (TT-19), never the broker's number: legs placed together (one basket, one structure)
  with the Options Builder's rule and the hedge benefit, a bought option its premium, a cash position its full value. A
  sold option or a future on its own needs the broker's SPAN and exposure margin, which practice mode does not have, so it
  is counted in `not_estimated` and left out of the sum - nothing is made up.
- `mode`: the mode the Terminal's orders use (PAPER in Phase 0) - shown, never changed (T9).
- `feed`: the caller's price connection in plain words (the data hub), or why there is none.
- `kill_switch`: whether new orders are stopped for the organisation or the platform. Exits always run (ADR-0004).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import KillSwitchScope
from app.db.models import PaperAccountRecord, TradeRecord, User
from app.market_data.calendar import IST

FLAG = "terminal"
PAGE_FLAG = "ui_v2"
MARGIN_NOTE = ("ATP's estimate, not your broker's number. Sold options and futures on their own need your broker's margin, "
               "which practice mode does not have.")
TODAY_NOTE = "Booked today plus your open positions' profit or loss since entry, at their last price."


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _counted(trade: TradeRecord) -> bool:
    """As the paper account counts trades: one priced from the user's own inputs (`price_source`) is left out."""
    return not trade.price_source


def open_pnl(trade: TradeRecord) -> Optional[float]:
    """An open position's profit or loss at its last marked price; None before the first mark."""
    if trade.mark_price is None:
        return None
    sign = 1.0 if trade.direction == "LONG" else -1.0
    return sign * (float(trade.mark_price) - float(trade.entry_price)) * float(trade.quantity)


def _builder_leg(trade: TradeRecord) -> Optional[Dict[str, Any]]:
    """An open option position as the Options Builder reads a leg; None when the contract cannot be read from its name."""
    from app.paper.fno_contracts import parse_symbol
    parsed = parse_symbol(trade.symbol)
    if parsed is None or parsed.right not in ("CE", "PE") or parsed.strike is None or parsed.expiry is None:
        return None
    units = max(1, int(round(float(trade.quantity))))
    return {"direction": "BUY" if trade.direction == "LONG" else "SELL", "option_type": parsed.right, "strike": float(parsed.strike),
            "premium": float(trade.entry_price), "lots": 1, "lot_size": units, "expiry": parsed.expiry.isoformat()}


def single_margin(trade: TradeRecord) -> Optional[float]:
    """One position on its own: a bought option its premium, a cash position its full value; None = needs the broker."""
    value = float(trade.entry_price) * float(trade.quantity)
    if trade.instrument_kind == "UNDERLYING":
        return round(value, 2)
    if trade.instrument_kind == "OPTION" and trade.direction == "LONG":
        return round(value, 2)
    return None


def group_margin(trades: Sequence[TradeRecord]) -> Optional[float]:
    """Legs placed together: the builder's estimate with the hedge; None when it needs the broker (undefined risk, a
    future, legs on several expiries, a contract that cannot be read)."""
    from app.paper.fno_basket import hedge_margin
    legs = [_builder_leg(t) for t in trades]
    if any(leg is None for leg in legs):
        return None
    value = hedge_margin([leg for leg in legs if leg is not None])["with_hedge"].get("value")
    return None if value is None else round(float(value), 2)


def margin_estimate(open_trades: Sequence[TradeRecord], groups: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure (given the groups): the labelled estimate of the margin his open positions use."""
    by_id = {t.id: t for t in open_trades}
    used = 0.0
    not_estimated = 0
    grouped: set = set()
    for group in groups:
        members = [by_id[i] for i in group.get("trade_ids", []) if i in by_id]
        if len(members) < 2:
            continue
        grouped.update(t.id for t in members)
        value = group_margin(members)
        if value is None:
            not_estimated += 1
        else:
            used += value
    for trade in open_trades:
        if trade.id in grouped:
            continue
        value = single_margin(trade)
        if value is None:
            not_estimated += 1
        else:
            used += value
    return {"used": round(used, 2), "estimated": True, "not_estimated": not_estimated, "note": MARGIN_NOTE}


async def _own_trades(session: AsyncSession, user: User) -> List[TradeRecord]:
    rows = await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id,
                                                           TradeRecord.mode == "PAPER").order_by(TradeRecord.id))
    return [t for t in rows if _counted(t)]


async def _starting_capital(session: AsyncSession, user: User) -> float:
    """The paper account's starting capital, read without creating the account (a GET never writes)."""
    from app.core import config
    row = await session.scalar(select(PaperAccountRecord).where(PaperAccountRecord.user_id == user.id,
                                                               PaperAccountRecord.tenant_id == user.tenant_id))
    return float(row.starting_capital) if row is not None else float(config.PAPER_STARTING_CAPITAL)


async def _feed(session: AsyncSession, user: User) -> Dict[str, Any]:
    from app.market_data import hub
    if not await hub.hub_on(session, user.tenant_id):
        return {"state": "off", "message": "Prices refresh every few seconds."}
    conn = await hub.user_connection(session, user)
    if conn is None:
        return {"state": "none", "message": "Connect your broker to see live prices."}
    status = (await hub.connection_status(conn)).public()
    return {"state": status["state"], "message": status["message"], "broker_name": status["broker_name"]}


async def _kill_switch(session: AsyncSession, user: User) -> Dict[str, Any]:
    from app.kill_switch import checks
    platform = await checks.get_switch(session, KillSwitchScope.GLOBAL, None)
    account = await checks.get_switch(session, KillSwitchScope.TENANT, user.tenant_id)
    if platform is not None and platform.engaged:
        return {"engaged": True, "scope": "platform", "message": "New orders are stopped for everyone by ATP. Exits still work."}
    if account is not None and account.engaged:
        return {"engaged": True, "scope": "account", "message": "New orders are stopped for your account. Exits still work."}
    return {"engaged": False, "scope": None, "message": "New orders are allowed."}


async def _mode(session: AsyncSession, user: User) -> Dict[str, Any]:
    """The Terminal places PAPER orders (the one ticket and basket are PAPER-only today). Shown, never changed."""
    # M55 S1b: LIVE is allowed for this organisation only with the switch on, the lock off and the owner's grant.
    from app.platform import live_owner
    return {"mode": "PAPER", "live_allowed": await live_owner.org_live_open(session, user.tenant_id),
            "message": "Practice mode: no real order is sent and no money moves."}


async def funds_of(session: AsyncSession, user: User, trades: Sequence[TradeRecord] | None = None) -> dict[str, Any]:
    """The status bar's `funds` and `margin` (TERM-2 R4: the basket's "available" reads the same figure). Read only."""
    from app.paper.fno_contracts import position_groups
    trades = list(trades) if trades is not None else await _own_trades(session, user)
    closed = [t for t in trades if t.exit_time is not None]
    open_trades = [t for t in trades if t.exit_time is None]
    balance = await _starting_capital(session, user) + sum(float(t.pnl or 0.0) for t in closed)
    groups = await position_groups(session, user) if len(open_trades) > 1 else []
    margin = margin_estimate(open_trades, groups)
    return {"funds": {"balance": round(balance, 2), "available": round(balance - margin["used"], 2), "source": "practice",
                      "note": "Your practice balance minus the margin estimate."}, "margin": margin}


async def terminal_status(session: AsyncSession, user: User, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(IST).date()
    trades = await _own_trades(session, user)
    closed = [t for t in trades if t.exit_time is not None]
    open_trades = [t for t in trades if t.exit_time is None]
    booked_today = sum(float(t.pnl or 0.0) for t in closed if t.exit_time is not None and _utc(t.exit_time).astimezone(IST).date() == today)
    marks = [(open_pnl(t), t.mark_time) for t in open_trades]
    priced = [p for p, _ in marks if p is not None]
    open_mtm: Optional[float] = round(sum(priced), 2) if priced else (0.0 if not open_trades else None)
    newest = max((_utc(m) for p, m in marks if p is not None and m is not None), default=None)
    held = await funds_of(session, user, trades)
    margin = held["margin"]
    today_mtm = round(booked_today + (open_mtm or 0.0), 2)
    return {
        "today": {"mtm": today_mtm, "booked": round(booked_today, 2), "note": TODAY_NOTE},
        "open": {"count": len(open_trades), "priced": len(priced), "mtm": open_mtm, "as_of": newest.isoformat() if newest else None},
        "funds": held["funds"],
        "margin": margin,
        "mode": await _mode(session, user),
        "feed": await _feed(session, user),
        "kill_switch": await _kill_switch(session, user),
        "as_of": now.isoformat(),
    }


__all__ = ["FLAG", "PAGE_FLAG", "terminal_status", "funds_of", "margin_estimate", "single_margin", "group_margin", "open_pnl"]
