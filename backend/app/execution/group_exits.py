"""CX / SX (owner, 24 Oct): combined exits - one stop-loss and / or target over a group of the user's own open positions.

What a combined exit is (`GroupExitRecord`):
* the legs: any set of the user's open positions (CX1: all open, a ticked set, or one spread group), frozen when it is set;
* the measure (CX2): the group's ₹ MTM (a loss and / or a profit in rupees), its net premium (sold premium minus bought
  premium, per unit of the smallest leg), or the underlying's spot level (T-D2, the default for spreads). A level's side
  (ABOVE / BELOW) is read from the value when the exit is set, so the user only types the number;
* the check (CX3): TOUCH (every reading) or CLOSE (a closed candle of a chosen timeframe; the spot level today);
* optional trailing (CX2 / SX2, owner): LOCK - once the gain reaches A, the stop moves to keep B; TRAIL - every X of
  favourable move, the stop moves by Y. Every number is the user's own (no default). The stop only ever moves in the
  trade's favour; `best_value`, `current_sl` and `locked_at` are stored on the row, so a restart continues from them.

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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

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
TRAIL_NONE, TRAIL, LOCK = "NONE", "TRAIL", "LOCK"
TRAIL_KINDS = (TRAIL_NONE, TRAIL, LOCK)
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
    trail_kind: str = TRAIL_NONE
    trail: Optional[Dict[str, float]] = None     # LOCK {after: A, lock: B}; TRAIL {step: X, by: Y} - the user's numbers
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


def leg_view(trade: TradeRecord, venue: Optional[str] = None) -> Dict[str, Any]:
    """One frozen leg: what the measure needs, read once when the exit is set (`venue`: the exit paths' own
    `position_monitor.venue_for`, so a legacy row without an exchange is priced where it trades)."""
    return {"trade_id": trade.id, "symbol": trade.symbol, "exchange": venue or trade.exchange or "",
            "direction": trade.direction,
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
    stop = stop_now(row)
    if row.measure == MTM_INR:
        if stop is not None and value <= stop:
            return SL
        if row.target_value is not None and value >= abs(float(row.target_value)):
            return TARGET
        return None
    if reached(stop, stop_side(row) if stop is not None else None, value):
        return SL
    if reached(_float(row.target_value), row.target_side, value):
        return TARGET
    return None


def _float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


# --- trailing (CX2 / SX2): the stop only ever moves in the trade's favour ---------------------------------------------

def trail_spec(row: GroupExitRecord) -> Dict[str, Any]:
    """The user's trailing numbers (LOCK {after, lock}; TRAIL {step, by}) plus the candle-close bookmark `seen`."""
    try:
        spec = json.loads(row.trail_json or "{}")
    except ValueError:
        return {}
    return spec if isinstance(spec, dict) else {}


def trailing_on(row: GroupExitRecord) -> bool:
    return (row.trail_kind or TRAIL_NONE) in (TRAIL, LOCK)


def favour(row: GroupExitRecord) -> float:
    """+1 when the trade gains as the measure rises, -1 when it gains as it falls. ₹ MTM: always +1. A level: away from
    the stop (a stop BELOW: up), else towards the target."""
    if row.measure == MTM_INR:
        return 1.0
    if row.sl_side:
        return 1.0 if row.sl_side == BELOW else -1.0
    return 1.0 if row.target_side == ABOVE else -1.0


def stop_side(row: GroupExitRecord) -> Optional[str]:
    """Which way a level measure must go to reach the stop (the side read when it was set; a lock-only exit: against
    the trade)."""
    if row.measure == MTM_INR:
        return None
    return row.sl_side or (BELOW if favour(row) > 0 else ABOVE)


def first_stop(row: GroupExitRecord) -> Optional[float]:
    """The stop as the user set it: ₹ MTM as a signed MTM (a loss of ₹X -> -X), a level as typed."""
    if row.sl_value is None:
        return None
    return -abs(float(row.sl_value)) if row.measure == MTM_INR else float(row.sl_value)


def stop_now(row: GroupExitRecord) -> Optional[float]:
    """The stop in force: the trailed one once it moved, else the user's own (₹ MTM signed)."""
    return _float(row.current_sl) if row.current_sl is not None else first_stop(row)


def reference(row: GroupExitRecord) -> Optional[float]:
    """Where the gain is counted from: ₹ MTM - zero (the combined profit itself); a level - its reading when set."""
    return 0.0 if row.measure == MTM_INR else _float(row.basis_value)


def better(a: Optional[float], b: Optional[float], fav: float) -> Optional[float]:
    """The more favourable of two stops (None = no stop)."""
    if a is None or b is None:
        return b if a is None else a
    return max(a, b) if fav > 0 else min(a, b)


def trailed_stop(row: GroupExitRecord, best: float) -> Optional[float]:
    """The stop the trailing asks for after the best reading `best` (None: not yet). Pure: LOCK - once the gain from the
    reference reaches A, the stop keeps B; TRAIL - every full X of gain moves the user's own stop by Y."""
    ref = reference(row)
    spec = trail_spec(row)
    if ref is None or not trailing_on(row):
        return None
    fav = favour(row)
    gain = (best - ref) * fav
    if row.trail_kind == LOCK:
        after, keep = _float(spec.get("after")), _float(spec.get("lock"))
        if after is None or keep is None or gain < after:
            return None
        return round(ref + fav * keep, 4)
    step, by, start = _float(spec.get("step")), _float(spec.get("by")), first_stop(row)
    if step is None or by is None or step <= 0 or start is None or gain < step:
        return None
    return round(start + fav * int(gain // step) * by, 4)


def apply_trail(row: GroupExitRecord, value: float, now: Optional[datetime] = None) -> bool:
    """One reading of the measure: keeps the best reading and moves the stop when the trailing asks - only ever in the
    trade's favour (never back). A LOCK is logged once (`locked_at`). True when the stop moved."""
    if not trailing_on(row):
        return False
    fav = favour(row)
    best = better(_float(row.best_value), float(value), fav)
    row.best_value = best
    wanted = trailed_stop(row, float(best)) if best is not None else None
    held = stop_now(row)
    if wanted is None or (held is not None and better(held, wanted, fav) == held):
        return False
    row.current_sl = wanted
    if row.trail_kind == LOCK:
        if row.locked_at is None:
            row.locked_at = _now(now)
            add_log(row, ARMED, f"Gain reached - the stop moved to {_level_words(row, wanted)}", now)
    else:
        add_log(row, ARMED, f"The stop moved to {_level_words(row, wanted)}", now)
    return True


def _level_words(row: GroupExitRecord, level: float) -> str:
    if row.measure == MTM_INR:
        return f"a combined profit of ₹{level:,.0f}" if level >= 0 else f"a combined loss of ₹{abs(level):,.0f}"
    return f"{level:,.2f}"


def trail_words(row: GroupExitRecord) -> str:
    """The trailing in plain words (empty when there is none); the numbers are the user's own."""
    if not trailing_on(row):
        return ""
    spec = trail_spec(row)
    if row.measure == MTM_INR:
        if row.trail_kind == LOCK:
            return (f"Once the combined profit reaches ₹{float(spec.get('after', 0)):,.0f}, the stop moves to keep "
                    f"₹{float(spec.get('lock', 0)):,.0f}.")
        return (f"For every ₹{float(spec.get('step', 0)):,.0f} the combined profit rises, the stop moves up "
                f"₹{float(spec.get('by', 0)):,.0f}.")
    what = f"{row.spot_symbol} spot" if row.measure == SPOT else "net premium"
    base = f" from {float(row.basis_value):,.2f}" if row.basis_value is not None else ""
    if row.trail_kind == LOCK:
        return (f"Once the {what} moves {float(spec.get('after', 0)):,.2f} points your way{base}, the stop moves to keep "
                f"{float(spec.get('lock', 0)):,.2f} points.")
    return (f"For every {float(spec.get('step', 0)):,.2f} points the {what} moves your way, the stop moves "
            f"{float(spec.get('by', 0)):,.2f} points with it.")


def trail_errors(kind: str, spec: Optional[Dict[str, Any]], sl_value: Optional[float]) -> List[str]:
    """Plain-word reasons the trailing cannot be set. Only the user's numbers; nothing is filled in for him."""
    if kind == TRAIL_NONE:
        return []
    if kind not in TRAIL_KINDS:
        return ["Choose how the stop trails: lock a gain, or trail step by step"]
    spec = spec or {}
    errors: List[str] = []
    if kind == LOCK:
        after, keep = _float(spec.get("after")), _float(spec.get("lock"))
        if after is None or after <= 0:
            errors.append("Enter the gain after which the stop moves (more than zero)")
        if keep is None or keep < 0:
            errors.append("Enter how much of the gain to keep (zero or more)")
        if after is not None and keep is not None and after > 0 and keep >= after:
            errors.append("The gain kept must be less than the gain that moves the stop")
        return errors
    step, by = _float(spec.get("step")), _float(spec.get("by"))
    if sl_value is None:
        errors.append("A trailing stop needs a stop-loss to start from")
    if step is None or step <= 0:
        errors.append("Enter the move that shifts the stop (more than zero)")
    if by is None or by <= 0:
        errors.append("Enter how far the stop shifts each time (more than zero)")
    if step is not None and by is not None and step > 0 and by > step:
        errors.append("The stop cannot shift further than the move itself")
    return errors


def trail_json_of(kind: str, spec: Optional[Dict[str, Any]]) -> Optional[str]:
    """The stored trailing numbers (only the keys of its kind)."""
    spec = spec or {}
    if kind == LOCK:
        return json.dumps({"after": float(spec["after"]), "lock": float(spec["lock"])})
    if kind == TRAIL:
        return json.dumps({"step": float(spec["step"]), "by": float(spec["by"])})
    return None


def trail_seen(row: GroupExitRecord) -> Optional[datetime]:
    """CLOSE check: the end of the last closed candle the trailing has read (a restart never reads one twice)."""
    raw = trail_spec(row).get("seen")
    if not raw:
        return None
    at = datetime.fromisoformat(str(raw))
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def mark_seen(row: GroupExitRecord, end: datetime) -> None:
    spec = trail_spec(row)
    spec["seen"] = _now(end).isoformat()
    row.trail_json = json.dumps(spec)


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


def last_traded(trade: TradeRecord) -> Tuple[float, Optional[datetime]]:
    """CX-F1: a leg's last traded price when no fresh quote came this pass - the monitor's last price (`mark_price`),
    else its entry price - with when it was seen (None for the entry)."""
    if trade.mark_price is not None:
        return float(trade.mark_price), trade.mark_time
    return float(trade.entry_price), None


def stale_state(row: GroupExitRecord) -> Dict[str, Any]:
    try:
        state = json.loads(row.stale_json or "{}")
    except ValueError:
        return {}
    return state if isinstance(state, dict) else {}


def mark_stale(row: GroupExitRecord, legs: List[Dict[str, Any]], now: datetime) -> None:
    """Record which legs were read at their last traded price this pass (cleared when every leg is fresh again)."""
    if not legs:
        row.stale_json = None
        return
    state = stale_state(row)
    since = state.get("since") or _now(now).isoformat()
    row.stale_json = json.dumps({"since": since, "legs": legs, "notified": bool(state.get("notified"))})


def stale_notice_due(row: GroupExitRecord, now: datetime) -> bool:
    """True once per stale spell: a leg has stayed on its last traded price for QUOTE_MAX_STALE_SECONDS (marks it told)."""
    from app.core.config import QUOTE_MAX_STALE_SECONDS
    state = stale_state(row)
    if not state.get("legs") or state.get("notified"):
        return False
    since = datetime.fromisoformat(str(state["since"]))
    since = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    if (_now(now) - since).total_seconds() < QUOTE_MAX_STALE_SECONDS:
        return False
    state["notified"] = True
    row.stale_json = json.dumps(state)
    return True


def stale_words(row: GroupExitRecord) -> str:
    legs = stale_state(row).get("legs") or []
    parts = [f"{leg['symbol']} (last traded {float(leg['price']):,.2f})" for leg in legs]
    return ("No fresh price for " + ", ".join(parts) + ".") if parts else ""


def exit_short_action(trade: TradeRecord) -> Dict[str, Any]:
    """CX-F2: the one-tap "Exit short now" the CRITICAL notice carries - it opens the position's own exit with its
    confirm step in the app (`POST /api/paper/fno/positions/{id}/exit-leg`, never blocked); the link is for Telegram."""
    from app.core.config import FRONTEND_URL
    base = FRONTEND_URL.rstrip("/") if FRONTEND_URL.startswith("http") else ""
    return {"kind": "exit_short", "label": "Exit short now", "trade_id": trade.id, "symbol": trade.symbol,
            "confirm": True, "path": f"/positions?exit={trade.id}", "url": f"{base}/positions?exit={trade.id}"}


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
    if not parts and trailing_on(row):
        parts.append("the trailing stop is reached")
    when = " on a closed candle" if row.check_mode == CLOSE else ""
    extra = trail_words(row)
    tail = f" {extra} The stop only moves your way." if extra else ""
    return f"If {' or '.join(parts)}{when}, these {legs} exit{order}.{tail}"


def distance(row: GroupExitRecord, value: Optional[float] = None) -> Dict[str, Optional[float]]:
    """How far the group's measure still has to move to reach each level, from `value` (default: the last reading);
    never negative (0 = reached). For ₹ MTM in rupees; for a level in its own unit (points of premium or spot)."""
    now_v = _float(value if value is not None else row.last_value)
    out: Dict[str, Optional[float]] = {"sl": None, "target": None}
    if now_v is None:
        return out
    if row.measure == MTM_INR:
        stop = stop_now(row)
        if stop is not None:
            out["sl"] = round(max(0.0, now_v - stop), 2)
        if row.target_value is not None:
            out["target"] = round(max(0.0, abs(float(row.target_value)) - now_v), 2)
        return out
    sl = stop_now(row)
    for key, level, side in (("sl", sl, stop_side(row) if sl is not None else None),
                             ("target", row.target_value, row.target_side)):
        if level is not None and side:
            gap = float(level) - now_v if side == ABOVE else now_v - float(level)
            out[key] = round(max(0.0, gap), 2)
    return out


def view(row: GroupExitRecord) -> Dict[str, Any]:
    return {"id": row.id, "client_ref": row.client_ref, "mode": row.mode, "origin": row.origin, "scope": row.scope,
            "measure": row.measure, "sl_value": _float(row.sl_value), "target_value": _float(row.target_value),
            "sl_side": row.sl_side, "target_side": row.target_side, "spot_symbol": row.spot_symbol,
            "check_mode": row.check_mode, "timeframe_minutes": row.timeframe_minutes, "status": row.status,
            "fired_kind": row.fired_kind, "last_value": _float(row.last_value), "distance": distance(row),
            "version": row.version, "legs": legs_of(row), "words": describe(row), "note": row.status_note,
            "stale": stale_state(row).get("legs") or [], "trail": trail(row),
            "trail_kind": row.trail_kind or TRAIL_NONE,
            "trailing": {k: v for k, v in trail_spec(row).items() if k != "seen"} if trailing_on(row) else None,
            "stop_now": stop_now(row), "stop_moved": row.current_sl is not None, "best_value": _float(row.best_value),
            "locked": row.locked_at is not None}


def ask_errors(ask: GroupExitAsk, trades: Sequence[TradeRecord]) -> List[str]:
    """Plain-word reasons the exit cannot be set (nothing is stored when there is one)."""
    errors: List[str] = []
    if ask.measure not in MEASURES:
        errors.append("Choose what the exit watches: combined ₹ profit / loss, net premium, or the spot level")
    if ask.sl_value is None and ask.target_value is None and ask.trail_kind != LOCK:
        errors.append("Enter a stop-loss, a target, or both")
    errors.extend(trail_errors(ask.trail_kind, ask.trail, ask.sl_value))
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
    from app.trading.position_monitor import venue_for
    at = _now(now)
    row = GroupExitRecord(
        tenant_id=user.tenant_id, user_id=user.id, client_ref=ask.client_ref, mode=trades[0].mode, origin=ask.origin,
        scope=ask.scope, basket_ref=ask.basket_ref, legs_json=json.dumps([leg_view(t, await venue_for(session, t)) for t in trades]),
        measure=ask.measure, spot_symbol=(ask.spot_symbol or None), spot_exchange=(ask.spot_exchange or None),
        sl_value=ask.sl_value, target_value=ask.target_value, basis_value=now_value, check_mode=ask.check_mode,
        timeframe_minutes=ask.timeframe_minutes if ask.check_mode == CLOSE else None, status=ARMED, version=1,
        passes_firing=0, trail_kind=ask.trail_kind, trail_json=trail_json_of(ask.trail_kind, ask.trail),
        created_at=at, updated_at=at)
    if ask.measure != MTM_INR and now_value is not None:
        row.sl_side = side_for(float(ask.sl_value), now_value) if ask.sl_value is not None else None
        row.target_side = side_for(float(ask.target_value), now_value) if ask.target_value is not None else None
    add_log(row, ARMED, describe(row), at)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def all_open_ids(session: AsyncSession, user: User) -> List[int]:
    """CX1 "all open positions": the user's own open PAPER positions now (frozen into the exit when it is set; a position
    opened later is not covered until the exit is set again - CXQ-4)."""
    rows = await session.scalars(select(TradeRecord.id).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.exit_time.is_(None),
        TradeRecord.mode == "PAPER"))
    return sorted(int(i) for i in rows)


async def current_value(session: AsyncSession, measure: str, trades: Sequence[TradeRecord], lookup: Any, *,
                        spot_symbol: Optional[str] = None, spot_exchange: Optional[str] = None) -> Optional[float]:
    """The group's measure now, read the way "Exit now" prices a leg (`lookup`: the person's own live price, from the
    route's `price_lookup_for_user`; else the recent last price; never a typed one) - for the preview, the level's side
    and the distance. None when any leg has no price."""
    from app.execution.approval import exit_price
    if measure == SPOT:
        if lookup is None or not spot_symbol or not spot_exchange:
            return None
        try:
            return float(await lookup(spot_symbol, spot_exchange))
        except Exception:  # noqa: BLE001 - no spot price now: no preview value
            return None
    prices: Dict[int, float] = {}
    for trade in trades:
        try:
            prices[trade.id], _ = await exit_price(session, trade, lookup)
        except ValueError:
            return None
    legs = [leg_view(t) for t in trades]
    return mtm(legs, prices) if measure == MTM_INR else net_premium(legs, prices)


async def edit(session: AsyncSession, user: User, row_id: int, *, version: int, sl_value: Optional[float],
               target_value: Optional[float], check_mode: Optional[str] = None, timeframe_minutes: Optional[int] = None,
               now_value: Optional[float] = None, now: Optional[datetime] = None,
               trailing: Optional[Tuple[str, Optional[Dict[str, Any]]]] = None) -> GroupExitRecord:
    """The user changes his own armed exit's levels (CX5 / SX6): only while ARMED and only on the version he saw (an edit
    from another device in between is refused with plain words). The legs stay as set; to change them, remove the exit
    and set a new one. A level's side is read again from the value now. `trailing` (kind, numbers) replaces the trailing;
    None keeps it. An edit starts the trailing again from the reading now (the user's own new levels)."""
    row = await session.get(GroupExitRecord, row_id)
    if row is None or row.tenant_id != user.tenant_id or row.user_id != user.id:
        raise GroupExitError("Combined exit not found")
    if row.status == FIRING:
        raise GroupExitError("Already exiting - the legs are being closed")
    if row.status != ARMED:
        raise GroupExitError("This combined exit has ended - set a new one")
    if int(row.version) != int(version):
        raise GroupExitError("Changed on another screen - reload and try again")
    mode = check_mode or row.check_mode
    tf = timeframe_minutes if timeframe_minutes is not None else row.timeframe_minutes
    kind, spec = trailing if trailing is not None else (row.trail_kind or TRAIL_NONE, trail_spec(row))
    probe = GroupExitAsk(client_ref=row.client_ref, trade_ids=trade_ids(row), measure=row.measure, sl_value=sl_value,
                         target_value=target_value, spot_symbol=row.spot_symbol, spot_exchange=row.spot_exchange,
                         check_mode=mode, timeframe_minutes=tf, scope=row.scope, trail_kind=kind, trail=spec)
    errors = [e for e in ask_errors(probe, await own_open_trades(session, user, trade_ids(row)))
              if not e.startswith(("Some of the ticked", "A ticked position is already closed"))]
    if row.measure != MTM_INR:
        if now_value is None:
            errors.append("No live price for the group right now - try again in a moment")
        else:
            for name, level in (("stop-loss", sl_value), ("target", target_value)):
                if level is not None and float(level) == float(now_value):
                    errors.append(f"The {name} equals the value now - it would fire at once")
    if errors:
        raise GroupExitError("; ".join(errors))
    at = _now(now)
    values: Dict[str, Any] = {"sl_value": sl_value, "target_value": target_value, "check_mode": mode,
                              "timeframe_minutes": tf if mode == CLOSE else None, "current_sl": None,
                              "best_value": None, "locked_at": None, "trail_kind": kind,
                              "trail_json": trail_json_of(kind, spec), "version": int(row.version) + 1, "updated_at": at}
    if kind in (TRAIL, LOCK):                          # the closed-candle check starts after the edit (no candle before it)
        values["trail_json"] = json.dumps({**json.loads(values["trail_json"]), "seen": at.isoformat()})
    if row.measure != MTM_INR and now_value is not None:
        values["basis_value"] = now_value
    if row.measure != MTM_INR and now_value is not None:
        values["sl_side"] = side_for(float(sl_value), now_value) if sl_value is not None else None
        values["target_side"] = side_for(float(target_value), now_value) if target_value is not None else None
    done = await session.execute(update(GroupExitRecord).where(
        GroupExitRecord.id == row.id, GroupExitRecord.status == ARMED, GroupExitRecord.version == row.version).values(
        **values).execution_options(synchronize_session=False))
    if getattr(done, "rowcount", 0) != 1:
        await session.rollback()
        raise GroupExitError("Changed or fired meanwhile - reload and try again")
    await session.refresh(row)
    add_log(row, ARMED, f"Changed by you: {describe(row)}", at)
    await session.commit()
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
           "list_for_user", "by_ref", "add_log", "trail", "STUCK_ALERT_PASSES", "distance", "all_open_ids",
           "current_value", "edit", "last_traded", "mark_stale", "stale_notice_due", "stale_words",
           "exit_short_action", "stale_state", "TRAIL_NONE", "TRAIL", "LOCK", "TRAIL_KINDS", "trail_spec", "trailing_on",
           "favour", "stop_side", "first_stop", "stop_now", "trailed_stop", "apply_trail", "trail_words", "trail_errors",
           "trail_json_of", "trail_seen", "mark_seen", "better"]
