"""SX (owner, 24 Oct, ATP_ORDER_CX_SX.md SX1-SX8): strategy-level stop-loss / target / trailing attached at entry.

The basket ticket (and the spread builder, which opens it) carries "Exits for this strategy": an overall stop-loss and
/ or target over the whole strategy - combined ₹ MTM, % of the net premium, or the underlying's spot level - and an
optional trailing (group_exits: LOCK, TRAIL, LOCK_TRAIL, BREAK_EVEN). Every number is the user's own; nothing is filled
in here (the ticket pre-fills only from his own saved defaults).

When the basket's legs fill, the exit becomes ONE combined exit (`GroupExitRecord`, origin SX, scope GROUP, the basket's
`basket_ref`) on the legs that filled - the same engine, row and close path as CX (app/execution/group_exits.py and the
trigger engine): stop-loss and target on one row (OCO by construction), sold legs closed first, never blocked, survives
a restart, AI never sets or changes it. A basket sent without exits gets none. Setting it never undoes the entry: when it
cannot be set (no price for a spot level, say) the result and a notice say so in plain words and the legs stay open
with their own stops.

% of the net premium: converted once, at the fill, to the combined ₹ MTM on the filled legs (the net premium in ₹ =
Σ sold premium × qty - Σ bought premium × qty, at the fill prices), so the stored exit is an ordinary ₹ MTM exit. SX7:
no re-entry - nothing here or in the engine opens a position.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Sequence, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TradeRecord, User
from app.execution import group_exits as gx

logger = logging.getLogger(__name__)

PREMIUM_PCT = "PREMIUM_PCT"
MEASURES = (gx.MTM_INR, PREMIUM_PCT, gx.SPOT)
ORIGIN = "SX"
ARMED_STATUS, NOT_SET = "ARMED", "NOT_SET"


@dataclass
class StrategyExitAsk:
    """What the ticket sent: the user's own numbers (% of the net premium for PREMIUM_PCT, points for SPOT, ₹ for MTM)."""
    measure: str
    sl_value: Optional[float] = None
    target_value: Optional[float] = None
    trail_kind: str = gx.TRAIL_NONE
    trail: Optional[Dict[str, float]] = None


def client_ref(basket_ref: str) -> str:
    """One strategy exit per basket run: a repeated execute never sets two."""
    return f"sx:{basket_ref}"


def premium_inr(trades: Sequence[TradeRecord]) -> float:
    """The strategy's net premium in ₹ at the fill prices: sold premium in minus bought premium out (its size, either
    way: a credit or a debit)."""
    total = 0.0
    for t in trades:
        sign = 1.0 if gx.is_short(t) else -1.0
        total += sign * float(t.entry_price) * float(t.quantity)
    return round(abs(total), 2)


def in_rupees(ask: StrategyExitAsk, basis: float) -> Tuple[Optional[float], Optional[float], Optional[Dict[str, float]]]:
    """PREMIUM_PCT -> ₹ MTM on the filled legs: every number (stop-loss, target and the trailing's) is `pct` % of the
    net premium in ₹. Pure."""
    def conv(v: Optional[float]) -> Optional[float]:
        return None if v is None else round(float(v) * basis / 100.0, 2)
    spec = {k: float(conv(v) or 0.0) for k, v in (ask.trail or {}).items()} if ask.trail else None
    return conv(ask.sl_value), conv(ask.target_value), spec


def spot_of(trades: Sequence[TradeRecord]) -> Tuple[Optional[str], Optional[str]]:
    """The underlying's own spot instrument (T-D2: the default trigger for a spread)."""
    from app.option_chain.chain_view import spot_instrument
    names = {(t.underlying_symbol or "").upper() for t in trades}
    if len(names) != 1 or "" in names:
        return None, None
    return spot_instrument(names.pop())


async def arm(session: AsyncSession, user_id: int, basket_ref: str, trade_ids: Sequence[int], ask: StrategyExitAsk,
              price_lookup: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    """After the basket ran: the strategy exit over the legs that filled (SX3). Never raises - the entry stands either
    way; the answer says ARMED (with the exit's words) or NOT_SET (with the reason, also sent as a notice)."""
    ids = sorted({int(i) for i in trade_ids if i})
    user = await session.get(User, user_id)
    if user is None:
        return {"status": NOT_SET, "reason": "Your account could not be read"}
    if not ids:
        return {"status": NOT_SET, "reason": "No leg filled, so there is nothing for the strategy exit to watch"}
    try:
        trades = await gx.own_open_trades(session, user, ids)
        if ask.measure not in MEASURES:
            raise gx.GroupExitError("Choose what the exit watches: combined ₹ profit / loss, % of the net premium, "
                                    "or the spot level")
        measure, sl, target, trail = ask.measure, ask.sl_value, ask.target_value, ask.trail
        note = ""
        if measure == PREMIUM_PCT:
            basis = premium_inr(trades)
            if basis <= 0:
                raise gx.GroupExitError("The net premium of the filled legs is zero, so a % of it cannot be set")
            sl, target, trail = in_rupees(ask, basis)
            measure = gx.MTM_INR
            note = f" Set from % of the net premium (₹{basis:,.0f} at the fill)."
        spot_symbol = spot_exchange = None
        if measure == gx.SPOT:
            spot_symbol, spot_exchange = spot_of(trades)
        group_ask = gx.GroupExitAsk(client_ref=client_ref(basket_ref), trade_ids=ids, measure=measure, sl_value=sl,
                                    target_value=target, spot_symbol=spot_symbol, spot_exchange=spot_exchange,
                                    scope="GROUP", origin=ORIGIN, basket_ref=basket_ref, trail_kind=ask.trail_kind,
                                    trail=trail, check_mode=gx.TOUCH)   # M68 PR-3: a strategy exit has no candle check (its only mode)
        value = None
        if measure != gx.MTM_INR:
            value = await gx.current_value(session, measure, trades, price_lookup, spot_symbol=spot_symbol,
                                           spot_exchange=spot_exchange)
        row = await gx.create(session, user, group_ask, now_value=value, now=now)
        if note:
            gx.add_log(row, gx.ARMED, note.strip(), now)
            await session.commit()
        # F3: the stop in force and the target as numbers too (₹ MTM signed), for the shared signed value on the ticket.
        return {"status": ARMED_STATUS, "id": row.id, "words": gx.describe(row) + note, "legs": len(ids),
                "measure": row.measure, "stop_now": gx.stop_now(row), "target_value": None if row.target_value is None else float(row.target_value)}
    except gx.GroupExitError as exc:
        await session.rollback()
        reason = str(exc)
    except Exception as exc:  # noqa: BLE001 - the entry is placed; the exit's failure is told, never raised
        logger.exception("SX: strategy exit for basket %s not set", basket_ref)
        await session.rollback()
        reason = f"It could not be set ({type(exc).__name__})"
    await _tell(session, user_id, reason)
    return {"status": NOT_SET, "reason": reason}


async def _tell(session: AsyncSession, user_id: int, reason: str) -> None:
    from app.core.enums import NotificationSeverity, NotificationType
    from app.notifications.service import notify
    try:
        user = await session.get(User, user_id)
        if user is None:
            return
        await notify(session, user.tenant_id, NotificationType.SYSTEM_FAILURE, title="Strategy exit not set",
                     message=(f"Your basket's legs are placed, but the exits for this strategy were not set: {reason}. "
                              "The legs keep their own stops. Set a combined exit on Positions if you want one."),
                     user_id=user_id, severity=NotificationSeverity.WARNING)
    except Exception:  # noqa: BLE001 - a notice never undoes the entry
        logger.exception("SX: the notice for user %s failed", user_id)
        await session.rollback()


__all__ = ["PREMIUM_PCT", "MEASURES", "ORIGIN", "StrategyExitAsk", "client_ref", "premium_inr", "in_rupees", "spot_of",
           "arm"]
