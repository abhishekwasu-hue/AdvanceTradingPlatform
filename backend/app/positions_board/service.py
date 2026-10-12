"""N2-6 (P1 spec 2.4, docs/design/N2_P1FGH.md): the positions board - the caller's own open positions with the figures a
manual trader watches:

- P&L in ₹ and in R;
- the stop and target status;
- time in trade;
- the plan against the current stop (a widened stop is flagged);
- how the position exits.

Read-only. Prices are the position monitor's last marks (`trades.mark_price` / `mark_time`); nothing is quoted here,
so no broker session is read. A position without a mark is valued at entry and says so.

- **R** is the initial risk: |entry − initial stop| × quantity. The initial stop is `trades.initial_stop_loss` when
  stored, else the plan's stop (P1-d ticket), else the current stop (the basis is named).
- **Stop status:**
  - "through" when the mark is at or beyond the stop (the monitor exits it on its next cycle);
  - "near" within `NEAR_R` of the stop;
  - otherwise "ok".
- **Target status:** "reached" at or beyond target 1; otherwise "near" within `NEAR_R` of it.
- **Drift:**
  - "widened" when the current stop is farther from the entry than the plan's / initial stop;
  - "trailed" when it is closer or in profit;
  - otherwise "unchanged".
- **Exit:** "one_click" for a PAPER position the caller holds (or as the organisation owner) while "Exit now" exists
  (`approval.exit_now_enabled`); "broker_app" for LIVE; "automatic" otherwise (stops, targets and time exits run on
  the worker).

  It never depends on a lockout, a pause or this board's flag: exits are never gated (ADR-0004, tested). The daily
  loss lockout is shown only as a fact: new entries stopped, exits still working.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TradePlanRecord, TradeRecord, User

FLAG = "positions_board"
NEAR_R = 0.25
EXITS_NOTE = "Exits are never locked: stops, targets, time exits and Exit now always work."


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))


def _r(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def row(trade: TradeRecord, plan: Optional[TradePlanRecord], *, now: datetime, exit_how: str) -> Dict[str, Any]:
    """One position's figures (pure: the trade, its plan and how it exits)."""
    sign = 1.0 if trade.direction == "LONG" else -1.0
    qty = abs(float(trade.quantity))
    entry = float(trade.entry_price)
    mark = float(trade.mark_price) if trade.mark_price is not None else None
    price = mark if mark is not None else entry
    pnl = sign * (price - entry) * qty
    stop = float(trade.stop_loss) if trade.stop_loss is not None and float(trade.stop_loss) > 0 else None
    if trade.initial_stop_loss is not None and float(trade.initial_stop_loss) > 0:
        first_stop, basis = float(trade.initial_stop_loss), "initial stop"
    elif plan is not None and plan.stop_loss:
        first_stop, basis = float(plan.stop_loss), "plan stop"
    elif stop is not None:
        first_stop, basis = stop, "current stop"
    else:
        first_stop, basis = None, "no stop"
    risk_per_unit = abs(entry - first_stop) if first_stop is not None else None
    initial_risk = risk_per_unit * qty if risk_per_unit else None
    r_mult = pnl / initial_risk if initial_risk else None

    stop_status = None
    to_stop_r = None
    if stop is not None:
        room = sign * (price - stop)                         # > 0 while the stop is still away from the price
        to_stop_r = room / risk_per_unit if risk_per_unit else None
        stop_status = "through" if room <= 0 else ("near" if to_stop_r is not None and to_stop_r <= NEAR_R else "ok")
    target = float(trade.target1) if trade.target1 is not None and float(trade.target1) > 0 else None
    target_status = None
    to_target_r = None
    if target is not None:
        left = sign * (target - price)
        to_target_r = left / risk_per_unit if risk_per_unit else None
        target_status = "reached" if left <= 0 else ("near" if to_target_r is not None and to_target_r <= NEAR_R else "ok")

    drift = None
    if stop is not None and first_stop is not None and basis != "current stop":
        moved = sign * (stop - first_stop)                   # > 0: moved towards / past the entry (tighter)
        drift = "unchanged" if abs(moved) < 1e-9 else ("trailed" if moved > 0 else "widened")
    entered = _utc(trade.entry_time)
    marked = _utc(trade.mark_time)
    return {
        "trade_id": trade.id, "symbol": trade.symbol, "mode": trade.mode, "direction": trade.direction, "quantity": qty,
        "instrument_kind": trade.instrument_kind, "underlying": trade.underlying_symbol, "leg_group_id": trade.leg_group_id,
        "entry_price": entry, "entry_time": entered.isoformat() if entered else None,
        "time_in_trade_minutes": int((now - entered).total_seconds() // 60) if entered else None,
        "mark_price": mark, "mark_time": marked.isoformat() if marked else None,
        "mark_age_minutes": int((now - marked).total_seconds() // 60) if marked else None,
        "price_basis": "mark" if mark is not None else "entry",
        "pnl": _r(pnl), "r_multiple": _r(r_mult), "initial_risk": _r(initial_risk), "risk_basis": basis,
        "stop": stop, "stop_status": stop_status, "to_stop_r": _r(to_stop_r),
        "target": target, "target_status": target_status, "to_target_r": _r(to_target_r),
        "plan": None if plan is None else {"plan_id": plan.id, "setup": plan.setup, "entry": plan.entry, "stop_loss": plan.stop_loss,
                                           "target": plan.target, "reward_risk": plan.reward_risk},
        "original_stop": first_stop if basis in ("initial stop", "plan stop") else None, "stop_drift": drift,
        "exit": exit_how,
    }


async def may_use_exit_now(session: AsyncSession, user: User) -> bool:
    """Review: whether this user passes Exit now's own gate (`require_trader`: a trading role, the `trading:write` scope
    and an active organisation). A user who does not is shown "automatic", not a button that would answer 403."""
    from app.auth.dependencies import TRADING_ROLES
    from app.auth.scopes import has_scope
    from app.core.enums import UserRole
    from app.plans.limits import tenant_is_active
    if user.role == UserRole.SUPER_ADMIN.value:
        return True
    if user.role not in {r.value for r in TRADING_ROLES} or not has_scope(user, "trading:write"):
        return False
    from app.db.models import Tenant
    tenant = await session.get(Tenant, user.tenant_id)
    return tenant is not None and tenant_is_active(tenant)


async def board(session: AsyncSession, user: User, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The caller's own open positions (tenant + user), with totals and the lockout as a fact."""
    from app.execution import approval
    from app.risk_engine import by_default
    now = now or datetime.now(timezone.utc)
    trades = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.exit_time.is_(None)).order_by(TradeRecord.entry_time, TradeRecord.id)))
    plans: Dict[int, TradePlanRecord] = {}
    if trades:
        for p in await session.scalars(select(TradePlanRecord).where(
                TradePlanRecord.tenant_id == user.tenant_id, TradePlanRecord.user_id == user.id,
                TradePlanRecord.trade_id.in_([t.id for t in trades]))):
            if p.trade_id is not None:
                plans[int(p.trade_id)] = p
    one_click = await approval.exit_now_enabled(session, user.tenant_id) and await may_use_exit_now(session, user)

    def how(t: TradeRecord) -> str:
        if t.mode == "LIVE":
            return "broker_app"
        return "one_click" if one_click and approval.may_act_on_trade(user, t) else "automatic"
    rows: List[Dict[str, Any]] = [row(t, plans.get(t.id), now=now, exit_how=how(t)) for t in trades]
    locked = None
    if await by_default.enabled(session, user.tenant_id):
        locked = await by_default.lockout(session, user, now, record=False)
    risks = [r["initial_risk"] for r in rows if r["initial_risk"]]
    # What the stops would give back from the current marks: Σ max(0, distance from the price to the stop) × quantity.
    def give_back(r: Dict[str, Any]) -> float:
        if r["stop"] is None:
            return 0.0
        price = r["mark_price"] if r["mark_price"] is not None else r["entry_price"]
        sign = 1.0 if r["direction"] == "LONG" else -1.0
        return max(0.0, sign * (price - r["stop"])) * r["quantity"]
    open_risk = sum(give_back(r) for r in rows)
    return {
        "positions": rows,
        "totals": {"positions": len(rows), "pnl": _r(sum(r["pnl"] or 0.0 for r in rows)),
                   "r": _r(sum(r["r_multiple"] or 0.0 for r in rows)), "initial_risk": _r(sum(risks)) if risks else None,
                   "to_stops_from_marks": _r(open_risk), "without_stop": sum(1 for r in rows if r["stop"] is None),
                   "widened_stops": sum(1 for r in rows if r["stop_drift"] == "widened"),
                   "near_stop": sum(1 for r in rows if r["stop_status"] in ("near", "through")),
                   "unmarked": sum(1 for r in rows if r["price_basis"] == "entry")},
        "lockout": None if locked is None else {"reason": locked.get("reason"), "until": locked.get("until")},
        "exits": EXITS_NOTE, "near_r": NEAR_R, "as_of": now.isoformat(),
        "note": "P&L at the position monitor's last marks (entry where there is none), before charges. Greeks: the Positions "
                "page's Greeks table.",
    }
