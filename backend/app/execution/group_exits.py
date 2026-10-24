"""CX / SX (owner, 24 Oct): combined exits - one stop-loss and / or target over a group of the user's own open positions.

What a combined exit is (`GroupExitRecord`):
* the legs: any set of the user's open positions (CX1: all open, a ticked set, or one spread group), frozen when it is set;
* the measure (CX2): the group's ₹ MTM (a loss and / or a profit in rupees), its net premium (sold premium minus bought
  premium, per unit of the smallest leg), or the underlying's spot level (T-D2, the default for spreads). A level's side
  (ABOVE / BELOW) is read from the value when the exit is set, so the user only types the number;
* the check (CX3): TOUCH (every reading) or CLOSE (a closed candle of a chosen timeframe; the spot level today).

Firing (app/execution/trigger_engine.py): ARMED -> FIRING exactly once, then every leg still open is closed through
`position_monitor.close_group` - short legs first, then the hedges; a short that does not close keeps its hedges open
and is tried first again on the next pass. A FIRING exit is never given up: each pass closes what is still open until
nothing is (DONE). Never blocked by a limit, approval mode or the kill switch (ADR-0004); AI never sets or changes one
(ADR-0006); the numbers are the user's own (no suggested value). PAPER and LIVE use the same row, the same values and the
same close path; a LIVE leg still needs the owner's LIVE switches and the position's own broker session.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GroupExitRecord, TradeRecord, User

logger = logging.getLogger(__name__)

ARMED, FIRING, DONE, CANCELLED = "ARMED", "FIRING", "DONE", "CANCELLED"
LIVE_STATUSES = (ARMED, FIRING)
MTM_INR, NET_PREMIUM, SPOT = "MTM_INR", "NET_PREMIUM", "SPOT"
MEASURES = (MTM_INR, NET_PREMIUM, SPOT)
TOUCH, CLOSE = "TOUCH", "CLOSE"
ABOVE, BELOW = "ABOVE", "BELOW"
SL, TARGET = "SL", "TARGET"
SCOPES = ("SELECTED", "ALL_OPEN", "GROUP")
STUCK_ALERT_PASSES = 5          # a short still open after this many passes while FIRING: one CRITICAL notice (it keeps trying)


class GroupExitError(ValueError):
    """A combined exit that cannot be set, in plain words for the user."""


@dataclass
class GroupExitAsk:
    """What the user asked for (the routes / the ticket build it; nothing here suggests a number)."""
    client_ref: str
    trade_ids: List[int]
    measure: str
    sl_value: Optional[float] = None
    target_value: Optional[float] = None
    spot_symbol: Optional[str] = None
    spot_exchange: Optional[str] = None
    check_mode: str = TOUCH
    timeframe_minutes: Optional[int] = None
    scope: str = "SELECTED"
    origin: str = "CX"
    basket_ref: Optional[str] = None
    errors: List[str] = field(default_factory=list)


def _now(now: Optional[datetime]) -> datetime:
    """Stored times are UTC (a naive read-back never shifts an IST time by 5:30)."""
    at = now or datetime.now(timezone.utc)
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def is_short(leg: Any) -> bool:
    """A sold leg: its structure role when it has one, else its own direction (a hand-picked position)."""
    role = getattr(leg, "leg_role", None) or getattr(leg, "role", None)
    if role:
        return str(role).upper() == "SHORT"
    return str(getattr(leg, "direction", None) or getattr(leg, "side", "")).upper() == "SHORT"


def leg_view(trade: TradeRecord) -> Dict[str, Any]:
    """One frozen leg: what the measure needs, read once when the exit is set."""
    return {"trade_id": trade.id, "symbol": trade.symbol, "exchange": trade.exchange, "direction": trade.direction,
            "role": "SHORT" if is_short(trade) else "LONG", "quantity": float(trade.quantity),
            "entry_price": float(trade.entry_price), "mode": trade.mode}


def legs_of(row: GroupExitRecord) -> List[Dict[str, Any]]:
    try:
        legs = json.loads(row.legs_json or "[]")
    except ValueError:
        return []
    return [leg for leg in legs if isinstance(leg, dict) and "trade_id" in leg]


def trade_ids(row: GroupExitRecord) -> List[int]:
    return [int(leg["trade_id"]) for leg in legs_of(row)]


def mtm(legs: Sequence[Dict[str, Any]], prices: Dict[int, float]) -> Optional[float]:
    """The group's ₹ MTM before charges: Σ (price - entry) × quantity, negative for a sold leg. Any leg without a price:
    None (no decision on part of a group, never a made-up value)."""
    total = 0.0
    for leg in legs:
        price = prices.get(int(leg["trade_id"]))
        if price is None:
            return None
        sign = -1.0 if leg.get("role") == "SHORT" else 1.0
        total += sign * (float(price) - float(leg["entry_price"])) * float(leg["quantity"])
    return round(total, 2)


def net_premium(legs: Sequence[Dict[str, Any]], prices: Dict[int, float]) -> Optional[float]:
    """Sold premium minus bought premium, per unit of the smallest leg (a 1:1 spread: short price - long price). Any leg
    without a price: None."""
    if not legs:
        return None
    unit = min(float(leg["quantity"]) for leg in legs) or 1.0
    total = 0.0
    for leg in legs:
        price = prices.get(int(leg["trade_id"]))
        if price is None:
            return None
        sign = 1.0 if leg.get("role") == "SHORT" else -1.0
        total += sign * float(price) * float(leg["quantity"])
    return round(total / unit, 2)


def side_for(level: float, now_value: float) -> str:
    """Which way the value must go to reach `level` from where it is when the exit is set."""
    return ABOVE if level > now_value else BELOW


def reached(level: Optional[float], side: Optional[str], value: float) -> bool:
    if level is None or side is None:
        return False
    return value >= level if side == ABOVE else value <= level


def decide(row: GroupExitRecord, value: float) -> Optional[str]:
    """SL / TARGET / None for one reading of the group's measure. The stop is judged before the target (TT-7)."""
    if row.measure == MTM_INR:
        if row.sl_value is not None and value <= -abs(float(row.sl_value)):
            return SL
        if row.target_value is not None and value >= abs(float(row.target_value)):
            return TARGET
        return None
    if reached(_float(row.current_sl if row.current_sl is not None else row.sl_value), row.sl_side, value):
        return SL
    if reached(_float(row.target_value), row.target_side, value):
        return TARGET
    return None


def _float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def add_log(row: GroupExitRecord, status: str, note: str, now: Optional[datetime] = None) -> None:
    log = trail(row)
    log.append({"at": _now(now).isoformat(), "status": status, "note": note[:300]})
    row.trail_log = json.dumps(log[-50:])
    row.status_note = note[:500]


def trail(row: GroupExitRecord) -> List[Dict[str, Any]]:
    try:
        log = json.loads(row.trail_log or "[]")
    except ValueError:
        return []
    return log if isinstance(log, list) else []


def describe(row: GroupExitRecord) -> str:
    """The exit in plain words (CX6 / SX5): numbers are the user's own."""
    n = len(legs_of(row))
    legs = f"{n} leg{'s' if n != 1 else ''}"
    order = " - sold legs first" if any(leg.get("role") == "SHORT" for leg in legs_of(row)) else ""
    parts: List[str] = []
    if row.measure == MTM_INR:
        if row.sl_value is not None:
            parts.append(f"combined loss reaches ₹{abs(float(row.sl_value)):,.0f}")
        if row.target_value is not None:
            parts.append(f"combined profit reaches ₹{abs(float(row.target_value)):,.0f}")
    else:
        what = f"{row.spot_symbol} spot" if row.measure == SPOT else "net premium"
        word = {ABOVE: "rises to", BELOW: "falls to"}
        if row.sl_value is not None:
            parts.append(f"{what} {word.get(row.sl_side or '', 'reaches')} {float(row.sl_value):,.2f} (stop-loss)")
        if row.target_value is not None:
            parts.append(f"{what} {word.get(row.target_side or '', 'reaches')} {float(row.target_value):,.2f} (target)")
    when = " on a closed candle" if row.check_mode == CLOSE else ""
    return f"If {' or '.join(parts)}{when}, these {legs} exit{order}."


def view(row: GroupExitRecord) -> Dict[str, Any]:
    return {"id": row.id, "client_ref": row.client_ref, "mode": row.mode, "origin": row.origin, "scope": row.scope,
            "measure": row.measure, "sl_value": _float(row.sl_value), "target_value": _float(row.target_value),
            "sl_side": row.sl_side, "target_side": row.target_side, "spot_symbol": row.spot_symbol,
            "check_mode": row.check_mode, "timeframe_minutes": row.timeframe_minutes, "status": row.status,
            "fired_kind": row.fired_kind, "last_value": _float(row.last_value), "version": row.version,
            "legs": legs_of(row), "words": describe(row), "note": row.status_note, "trail": trail(row)}


def ask_errors(ask: GroupExitAsk, trades: Sequence[TradeRecord]) -> List[str]:
    """Plain-word reasons the exit cannot be set (nothing is stored when there is one)."""
    errors: List[str] = []
    if ask.measure not in MEASURES:
        errors.append("Choose what the exit watches: combined ₹ profit / loss, net premium, or the spot level")
    if ask.sl_value is None and ask.target_value is None:
        errors.append("Enter a stop-loss, a target, or both")
    for name, value in (("stop-loss", ask.sl_value), ("target", ask.target_value)):
        if value is not None and float(value) <= 0:
            errors.append(f"The {name} must be more than zero")
    if not ask.trade_ids:
        errors.append("Tick at least one open position")
    if len(trades) != len(set(ask.trade_ids)):
        errors.append("Some of the ticked positions are not your open positions any more")
    if any(t.exit_time is not None for t in trades):
        errors.append("A ticked position is already closed")
    if len({t.mode for t in trades}) > 1:
        errors.append("PAPER and LIVE positions cannot share one combined exit")
    if ask.measure == SPOT and not (ask.spot_symbol and ask.spot_exchange):
        errors.append("Choose the underlying whose spot level the exit watches")
    if ask.measure in (SPOT, NET_PREMIUM) and len({(t.underlying_symbol or "") for t in trades}) > 1:
        errors.append("A spot or net-premium exit needs positions on one underlying; use the ₹ profit / loss for a mix")
    if ask.check_mode not in (TOUCH, CLOSE):
        errors.append("Check on every price or on a closed candle")
    if ask.check_mode == CLOSE:
        if ask.measure != SPOT:
            errors.append("A closed-candle check works on the spot level today; use every price for ₹ or premium")
        if not ask.timeframe_minutes or int(ask.timeframe_minutes) <= 0:
            errors.append("Choose the candle timeframe for the closed-candle check")
    if ask.scope not in SCOPES:
        errors.append("Choose all open positions, a ticked set, or one spread")
    return errors


async def by_ref(session: AsyncSession, tenant_id: int, user_id: int, client_ref: str) -> Optional[GroupExitRecord]:
    return (await session.scalars(select(GroupExitRecord).where(
        GroupExitRecord.tenant_id == tenant_id, GroupExitRecord.user_id == user_id,
        GroupExitRecord.client_ref == client_ref))).first()


async def own_open_trades(session: AsyncSession, user: User, ids: Iterable[int]) -> List[TradeRecord]:
    wanted = sorted({int(i) for i in ids})
    if not wanted:
        return []
    rows = await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.id.in_(wanted)))
    return sorted(rows, key=lambda t: t.id)


async def create(session: AsyncSession, user: User, ask: GroupExitAsk, *, now_value: Optional[float] = None,
                 now: Optional[datetime] = None) -> GroupExitRecord:
    """Store one combined exit (idempotent on `client_ref`: the same tap never sets two). `now_value` is the group's
    measure when it is set (a level's side is read from it); without it a level exit cannot be set."""
    existing = await by_ref(session, user.tenant_id, user.id, ask.client_ref)
    if existing is not None:
        return existing
    trades = await own_open_trades(session, user, ask.trade_ids)
    errors = ask_errors(ask, trades)
    if ask.measure in (SPOT, NET_PREMIUM) and now_value is None and not errors:
        errors.append("No live price for the group right now - try again in a moment")
    if ask.measure in (SPOT, NET_PREMIUM) and now_value is not None:
        for name, level in (("stop-loss", ask.sl_value), ("target", ask.target_value)):
            if level is not None and float(level) == float(now_value):
                errors.append(f"The {name} equals the value now - it would fire at once")
    if errors:
        raise GroupExitError("; ".join(errors))
    at = _now(now)
    row = GroupExitRecord(
        tenant_id=user.tenant_id, user_id=user.id, client_ref=ask.client_ref, mode=trades[0].mode, origin=ask.origin,
        scope=ask.scope, basket_ref=ask.basket_ref, legs_json=json.dumps([leg_view(t) for t in trades]),
        measure=ask.measure, spot_symbol=(ask.spot_symbol or None), spot_exchange=(ask.spot_exchange or None),
        sl_value=ask.sl_value, target_value=ask.target_value, basis_value=now_value, check_mode=ask.check_mode,
        timeframe_minutes=ask.timeframe_minutes if ask.check_mode == CLOSE else None, status=ARMED, version=1,
        passes_firing=0, trail_kind="NONE", created_at=at, updated_at=at)
    if ask.measure != MTM_INR and now_value is not None:
        row.sl_side = side_for(float(ask.sl_value), now_value) if ask.sl_value is not None else None
        row.target_side = side_for(float(ask.target_value), now_value) if ask.target_value is not None else None
    add_log(row, ARMED, describe(row), at)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_for_user(session: AsyncSession, user: User, *, include_final: bool = False) -> List[GroupExitRecord]:
    query = select(GroupExitRecord).where(GroupExitRecord.tenant_id == user.tenant_id, GroupExitRecord.user_id == user.id)
    if not include_final:
        query = query.where(GroupExitRecord.status.in_(LIVE_STATUSES))
    return list(await session.scalars(query.order_by(GroupExitRecord.id.desc()).limit(100)))


async def cancel(session: AsyncSession, user: User, row_id: int, now: Optional[datetime] = None) -> GroupExitRecord:
    """The user removes his own armed exit at any time; one already exiting cannot be cancelled (it is closing the legs)."""
    row = await session.get(GroupExitRecord, row_id)
    if row is None or row.tenant_id != user.tenant_id or row.user_id != user.id:
        raise GroupExitError("Combined exit not found")
    if row.status == FIRING:
        raise GroupExitError("Already exiting - the legs are being closed")
    if row.status != ARMED:
        return row
    at = _now(now)
    done = await session.execute(update(GroupExitRecord).where(
        GroupExitRecord.id == row.id, GroupExitRecord.status == ARMED).values(
        status=CANCELLED, updated_at=at).execution_options(synchronize_session=False))
    if getattr(done, "rowcount", 0) != 1:
        await session.rollback()
        await session.refresh(row)
        raise GroupExitError("Already exiting - the legs are being closed")
    await session.refresh(row)
    add_log(row, CANCELLED, "Removed by you", at)
    await session.commit()
    return row


__all__ = ["ARMED", "FIRING", "DONE", "CANCELLED", "MTM_INR", "NET_PREMIUM", "SPOT", "TOUCH", "CLOSE", "ABOVE", "BELOW",
           "SL", "TARGET", "GroupExitAsk", "GroupExitError", "is_short", "leg_view", "legs_of", "trade_ids", "mtm",
           "net_premium", "side_for", "reached", "decide", "describe", "view", "ask_errors", "create", "cancel",
           "list_for_user", "by_ref", "add_log", "trail", "STUCK_ALERT_PASSES"]
