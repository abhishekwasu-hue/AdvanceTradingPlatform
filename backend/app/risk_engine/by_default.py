"""Z5 / P1-d (P1 spec 1.1 and 1.2, Phase 0): risk by default for each user's PAPER entries.

Behind the flag `risk_by_default` (off by default). With it off nothing here runs. With it on, for a user's PAPER entries:

* **Risk cap and sizing.** The most an entry may lose at its stop is the stricter of `risk_per_trade_pct` % of the user's
  PAPER capital basis (starting capital + realised net P&L before today) and `risk_per_trade_amount` (0 = no rupee cap).
  The organisation's own risk per trade (after the platform ceilings) still applies: strictest wins (ADR-0003). The
  ticket computes the quantity from that cap and the stop distance, in whole lots. Going above the user's cap needs a
  deliberate override with a reason: it is written to the audit log, stored on the plan and counted; it may reach at
  most `RISK_OVERRIDE_MAX_MULTIPLE` x the cap and never the organisation's limit.
* **Daily loss lockout.** When the user's PAPER net P&L of the IST day (closed trades, net of charges, in the order they
  closed, plus today's open marks) reaches minus `daily_loss_pct` % of the capital basis, new PAPER entries and entry
  proposals of that user are locked for the rest of the day. The lockout is recorded the first time it is seen and kept
  even if the P&L recovers. Exits are never locked: stops, targets, time exits, "Exit now" and the kill switch's exits
  do not pass through here (ADR-0004).
* **Trades per day.** At most `max_trades_per_day` new PAPER entries per IST day (0 = no limit).
* **Minimum reward:risk.** A plan's reward:risk must be at least `min_rr` (0 = no minimum).
* **Cost line.** Every ticket shows the round trip's estimated charges (brokerage, STT, exchange, SEBI, stamp duty, GST
  from ATP's dated India cost model), a slippage estimate, the break-even move and the reward:risk after costs.

TT-12 (owner decision, 24 Oct): every limit starts OFF for every user until he sets his own value - no risk per trade
cap, no daily loss lockout, no entries-per-day cap and no minimum reward:risk (a minimum nobody stated is a hidden
trading rule, CLAUDE.md section 3). A value counts only when the user set it himself: the stored JSON lists those keys
under `_set`; a row saved before TT-12 (no `_set`) counts a stored value only when it differs from the old platform
default (`LEGACY_DEFAULTS`, the RISK_DEFAULT_* values in `app/core/config.py`), since a save then wrote every key. The
user may change his own settings within the bounds here and the platform's risk ceilings, or clear one (back to off).
A change that loosens a limit he set (a higher risk per trade or daily loss limit, more or unlimited entries per day,
a lower minimum reward:risk, or clearing it) takes effect from the next IST day; a tightening - and setting a limit
that was off - applies at once, so a limit cannot be widened in the middle of the day it protects. The cost line uses
the paper broker's own brokerage and slippage. Scope: every read filters on tenant and user. Nothing in
this module places, changes or closes an order.
"""
from __future__ import annotations

import functools
import json
import math
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, ParamSpec, Sequence, Set, Tuple, TypeVar

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config as app_config
from app.core.models import RiskConfig
from app.db.models import PaperAccountRecord, RiskLockoutRecord, TradePlanRecord, TradeRecord, User
from app.execution import india_costs
from app.market_data.calendar import IST, to_ist, trading_day_start

FLAG = "risk_by_default"
MODE = "PAPER"
KIND_DAILY_LOSS = "DAILY_LOSS"

# The bounds a user may set their own values within (the platform ceilings clamp further at runtime).
BOUNDS: Dict[str, Tuple[float, float]] = {
    "risk_per_trade_pct": (0.05, 5.0),
    "risk_per_trade_amount": (0.0, 10_000_000.0),
    "daily_loss_pct": (0.1, 20.0),
    "max_trades_per_day": (0, 500),
    "min_rr": (0.0, 10.0),
    # N1 F2 (flag portfolio_risk_caps): the user's own portfolio caps on new PAPER entries; 0 = no limit.
    "max_sector_pct": (0.0, 500.0),
    "max_correlated_risk_pct": (0.0, 50.0),
    "max_open_positions": (0, 200),
}
LABELS = {
    "risk_per_trade_pct": "Risk per trade, % of capital",
    "risk_per_trade_amount": "Risk per trade, rupees (0 = no rupee cap)",
    "daily_loss_pct": "Daily loss limit, % of capital",
    "max_trades_per_day": "New PAPER entries per day (0 = no limit)",
    "min_rr": "Minimum reward:risk",
    "max_sector_pct": "Exposure in one sector bucket, % of capital (0 = no limit)",
    "max_correlated_risk_pct": "Open risk at the stops in one bucket, % of capital (0 = no limit)",
    "max_open_positions": "Open PAPER positions (0 = no limit)",
}
INT_KEYS = ("max_trades_per_day", "max_open_positions")


@dataclass(frozen=True)
class RiskSettings:
    risk_per_trade_pct: float
    risk_per_trade_amount: float
    daily_loss_pct: float
    max_trades_per_day: int
    min_rr: float
    max_sector_pct: float = 0.0               # N1 F2 portfolio caps (0 = no limit)
    max_correlated_risk_pct: float = 0.0
    max_open_positions: int = 0

    @classmethod
    def defaults(cls) -> "RiskSettings":
        """TT-12: nothing set = every limit off (0 = no limit / no minimum)."""
        return cls(risk_per_trade_pct=0.0, risk_per_trade_amount=0.0, daily_loss_pct=0.0, max_trades_per_day=0, min_rr=0.0)

    @classmethod
    def from_values(cls, stored: Dict[str, Any], explicit: "Set[str]") -> "RiskSettings":
        """The values the user set (`explicit`) over "off"; a value outside its bounds (or unreadable) stays off."""
        base = asdict(cls.defaults())
        for key, (low, high) in BOUNDS.items():
            value = stored.get(key)
            if key in explicit and _valid(value, low, high):
                base[key] = int(value) if key in INT_KEYS else float(value)
        return cls(**base)

    @classmethod
    def from_json(cls, text: Optional[str]) -> "RiskSettings":
        """The stored values the user set himself, every other limit off (no deferred change folded in)."""
        stored = _stored(text)
        return cls.from_values(stored, explicit_keys(stored))

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


PENDING_KEY = "_pending"
SET_KEY = "_set"                                                     # TT-12: the keys the user set himself
NO_LIMIT_ZERO = ("risk_per_trade_amount", "max_trades_per_day", "max_sector_pct", "max_correlated_risk_pct",
                 "max_open_positions")                               # 0 means "no limit" for these


def legacy_defaults() -> Dict[str, Any]:
    """The platform defaults every user started with before TT-12 (24 Oct). Used only to read a row saved before then:
    a save wrote every key, so a stored value equal to one of these may never have been chosen by the user."""
    return {"risk_per_trade_pct": app_config.RISK_DEFAULT_RISK_PER_TRADE_PCT, "risk_per_trade_amount": app_config.RISK_DEFAULT_RISK_PER_TRADE_AMOUNT,
            "daily_loss_pct": app_config.RISK_DEFAULT_DAILY_LOSS_PCT, "max_trades_per_day": app_config.RISK_DEFAULT_MAX_TRADES_PER_DAY,
            "min_rr": app_config.RISK_DEFAULT_MIN_RR, "max_sector_pct": 0.0, "max_correlated_risk_pct": 0.0, "max_open_positions": 0}


def _valid(value: Any, low: float, high: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and low <= value <= high


def explicit_keys(stored: Dict[str, Any]) -> Set[str]:
    """The keys of a stored settings dict the user set himself (TT-12). A row with `_set` lists them; a row saved before
    TT-12 counts a value only when it differs from the old platform default (a value equal to it counts as unset)."""
    marker = stored.get(SET_KEY)
    if isinstance(marker, list):
        return {k for k in marker if isinstance(k, str) and k in BOUNDS}
    old = legacy_defaults()
    return {k for k, (low, high) in BOUNDS.items() if _valid(stored.get(k), low, high) and float(stored[k]) != float(old[k])}


def reset_by_tt12(text: Optional[str]) -> bool:
    """TT12-1 notice: whether this stored row is one whose limits TT-12 turned into "Not set" - saved before TT-12 (no
    `_set`), holding at least one limit that was on at the old platform default, and no value the user chose himself
    (a stored or deferred value that differs from the old default). A new user (no row) or one who saved since TT-12
    never qualifies. Pure; reads only."""
    stored = _stored(text)
    if not stored or isinstance(stored.get(SET_KEY), list) or explicit_keys(stored):
        return False
    old = legacy_defaults()
    pending = stored.get(PENDING_KEY)
    waiting = pending.get("values") if isinstance(pending, dict) else None
    if isinstance(waiting, dict) and any(k in BOUNDS and v is not None and (not isinstance(v, (int, float)) or float(v) != float(old[k]))
                                         for k, v in waiting.items()):
        return False                                     # a deferred change he chose himself is his own value
    return any(is_on(k, float(old[k])) and _valid(stored.get(k), low, high) and float(stored[k]) == float(old[k])
               for k, (low, high) in BOUNDS.items())


def is_on(key: str, value: float) -> bool:
    """Whether a limit restricts anything: every key reads 0 as off (no limit / no minimum)."""
    return float(value) > 0


def loosens(key: str, old: float, new: float) -> bool:
    """Whether changing `key` from `old` to `new` relaxes the limit (0 = no limit for the rupee cap and entries per day)."""
    if key in NO_LIMIT_ZERO:
        if new == old:
            return False
        if new == 0:
            return True
        return old != 0 and new > old
    if key == "min_rr":
        return new < old
    return new > old


def _stored(text: Optional[str]) -> Dict[str, Any]:
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def pending_of(text: Optional[str]) -> Optional[Dict[str, Any]]:
    pending = _stored(text).get(PENDING_KEY)
    if not isinstance(pending, dict) or not isinstance(pending.get("values"), dict) or not pending.get("from"):
        return None
    return pending


def _on(text: Optional[str], day: date) -> Tuple[Dict[str, Any], Set[str]]:
    """(stored values, the keys the user set) in force on `day`: a deferred change is folded in once its day has come
    (a deferred value is one he chose; a deferred None clears the key)."""
    data = _stored(text)
    explicit = explicit_keys(data)
    pending = pending_of(text)
    if pending is not None and str(pending["from"]) <= day.isoformat():
        for key, value in pending["values"].items():
            if key not in BOUNDS:
                continue
            if value is None:
                explicit.discard(key)
                data.pop(key, None)
            else:
                data[key] = value
                explicit.add(key)
    return {k: v for k, v in data.items() if k in BOUNDS}, explicit


def settings_on(text: Optional[str], day: date) -> RiskSettings:
    """The settings in force on `day`: the ones the user set (a deferred loosening applied once its day has come), every
    other limit off."""
    data, explicit = _on(text, day)
    return RiskSettings.from_values(data, explicit)


def set_on(text: Optional[str], day: date) -> List[str]:
    """The keys the user set himself, in force on `day` (the rest show as "Not set")."""
    data, explicit = _on(text, day)
    return sorted(k for k in explicit if _valid(data.get(k), *BOUNDS[k]))


def plan_change(text: Optional[str], values: Dict[str, Any], today: date) -> Tuple[str, Dict[str, Any], Dict[str, Any], str]:
    """(new stored text, applied now, deferred, the day the deferred ones start). A value None clears the key (off).
    Setting a limit that was off, or tightening one, applies at once; loosening one the user set (clearing it included)
    waits for the next IST day. A due deferred change is folded in first. Pure."""
    data, explicit = _on(text, today)
    current = RiskSettings.from_values(data, explicit).as_dict()
    explicit = {k for k in explicit if _valid(data.get(k), *BOUNDS[k])}
    pending = pending_of(text)
    deferred: Dict[str, Any] = dict(pending["values"]) if pending is not None and str(pending["from"]) > today.isoformat() else {}
    applied: Dict[str, Any] = {}
    for key, raw in values.items():
        if raw is None:
            if key in explicit and is_on(key, current[key]):
                deferred[key] = None                 # clearing a limit he set is a loosening: from the next day
            else:
                explicit.discard(key)
                current[key] = 0
                applied[key] = None
                deferred.pop(key, None)
            continue
        new = int(raw) if key in INT_KEYS else float(raw)
        if key in explicit and loosens(key, float(current[key]), float(new)):
            deferred[key] = new
        else:                                        # a tightening, or a limit that was off (TT-12): at once
            current[key] = new
            explicit.add(key)
            applied[key] = new
            deferred.pop(key, None)                  # a tightening also cancels a loosening still waiting
    start = (today + timedelta(days=1)).isoformat()
    stored: Dict[str, Any] = {k: current[k] for k in sorted(explicit)}
    stored[SET_KEY] = sorted(explicit)
    if deferred:
        stored[PENDING_KEY] = {"values": deferred, "from": start}
    return json.dumps(stored, sort_keys=True), applied, deferred, start


def validate_settings(values: Dict[str, Any], ceilings: Dict[str, float]) -> List[str]:
    """What is wrong with a user's new settings (bounds, then the platform ceilings), or []."""
    errors = []
    for key, value in values.items():
        if key not in BOUNDS:
            errors.append(f"unknown setting {key!r}")
            continue
        if value is None:                            # TT-12: clear it (off)
            continue
        low, high = BOUNDS[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not low <= value <= high:
            errors.append(f"{LABELS[key]} must be between {low:g} and {high:g}")
    if isinstance(values.get("risk_per_trade_pct"), (int, float)) and values["risk_per_trade_pct"] > ceilings.get("risk_per_trade_pct", math.inf):
        errors.append(f"{LABELS['risk_per_trade_pct']} is above the platform ceiling {ceilings['risk_per_trade_pct']:g}")
    if isinstance(values.get("daily_loss_pct"), (int, float)) and values["daily_loss_pct"] > ceilings.get("max_daily_loss_pct", math.inf):
        errors.append(f"{LABELS['daily_loss_pct']} is above the platform ceiling {ceilings['max_daily_loss_pct']:g}")
    return errors


def effective_settings(settings: RiskSettings, ceilings: Dict[str, float]) -> Tuple[RiskSettings, List[str]]:
    """The settings within the platform ceilings (runtime safety net, as `controls.clamp_config`)."""
    notes = []
    pct, daily = settings.risk_per_trade_pct, settings.daily_loss_pct
    if pct > ceilings.get("risk_per_trade_pct", math.inf):
        pct = ceilings["risk_per_trade_pct"]
        notes.append(f"Your risk per trade is capped at the platform ceiling {pct:g}%")
    if daily > ceilings.get("max_daily_loss_pct", math.inf):
        daily = ceilings["max_daily_loss_pct"]
        notes.append(f"Your daily loss limit is capped at the platform ceiling {daily:g}%")
    from dataclasses import replace
    return replace(settings, risk_per_trade_pct=pct, daily_loss_pct=daily), notes


# --- pure rules --------------------------------------------------------------------------------------------------------

def risk_cap(capital_basis: float, settings: RiskSettings) -> Dict[str, Any]:
    """The user's cap per trade: the stricter of the % of capital and the rupee amount; either 0 = not set. Neither set
    (TT-12): no cap of his own - `amount` None (the organisation's risk per trade still sizes the entry)."""
    by_pct = max(0.0, float(capital_basis)) * settings.risk_per_trade_pct / 100.0 if settings.risk_per_trade_pct > 0 else None
    amount = settings.risk_per_trade_amount if settings.risk_per_trade_amount > 0 else None
    if amount is not None and (by_pct is None or amount < by_pct):
        return {"amount": round(amount, 2), "binding": "amount", "by_pct": round(by_pct, 2) if by_pct is not None else None}
    if by_pct is not None:
        return {"amount": round(by_pct, 2), "binding": "pct", "by_pct": round(by_pct, 2)}
    return {"amount": None, "binding": None, "by_pct": None}


def reward_risk(entry: float, stop: float, target: float) -> Optional[float]:
    risk = abs(float(entry) - float(stop))
    return round(abs(float(target) - float(entry)) / risk, 2) if risk > 0 else None


def level_errors(direction: str, entry: float, stop: float, target: float) -> List[str]:
    """Long: stop below the entry, target above; short: the reverse."""
    errors = []
    if direction == "LONG":
        if not stop < entry:
            errors.append(f"Long: the stop ({stop:g}) must be below the entry ({entry:g})")
        if not target > entry:
            errors.append(f"Long: the target ({target:g}) must be above the entry ({entry:g})")
    else:
        if not stop > entry:
            errors.append(f"Short: the stop ({stop:g}) must be above the entry ({entry:g})")
        if not target < entry:
            errors.append(f"Short: the target ({target:g}) must be below the entry ({entry:g})")
    return errors


def size(entry: float, stop: float, cap: float, lot_size: float) -> Dict[str, Any]:
    """Whole lots whose loss at the stop stays within `cap`: lots = floor(cap / (|entry - stop| x lot size))."""
    per_unit = abs(float(entry) - float(stop))
    lot = float(lot_size) if lot_size and lot_size > 0 else 1.0
    per_lot = per_unit * lot
    lots = int(math.floor(cap / per_lot + 1e-9)) if per_lot > 0 and cap > 0 else 0
    return {"risk_per_unit": round(per_unit, 4), "risk_per_lot": round(per_lot, 2), "lot_size": lot, "lots": lots,
            "quantity": lots * lot, "risk_amount": round(lots * per_lot, 2)}


def _leg_items(cost: Dict[str, float]) -> Dict[str, float]:
    return {"brokerage": cost["brokerage"], "stt": cost["stt"], "exchange": cost["exch"], "sebi": cost["sebi"], "stamp": cost["stamp"], "gst": cost["tax"]}


def cost_line(*, direction: str, entry: float, stop: float, target: float, quantity: float, day: date, instrument_kind: str = "UNDERLYING",
              delivery: bool = False, slippage_pct: Optional[float] = None, brokerage_per_order: Optional[float] = None) -> Dict[str, Any]:
    """Estimated costs of the round trip (ATP's dated India cost model, the rates of `day`) and what they do to the plan:
    the charges and slippage if it exits at the target (the line shown), the same at the stop, the break-even move
    (the price move that pays the round trip's costs), and the reward:risk after costs. Pure."""
    from app.execution.paper_broker import PaperBroker
    paper = PaperBroker()                         # the PAPER fill's own brokerage and slippage: the line cannot drift from it
    slip_pct = float(paper.slippage_pct) if slippage_pct is None else float(slippage_pct)
    brokerage = float(paper.brokerage_per_order) if brokerage_per_order is None else float(brokerage_per_order)
    qty = float(quantity)
    segment = india_costs.segment_for(instrument_kind, delivery=delivery)
    short = direction == "SHORT"

    def trip(exit_price: float) -> Dict[str, float]:
        cost = india_costs.round_trip(day, day, entry, exit_price, qty, segment, sold_first=short, brokerage_per_order=brokerage)
        items = _leg_items(cost)
        items["slippage"] = (float(entry) + float(exit_price)) * qty * slip_pct / 100.0
        return {k: round(v, 2) for k, v in items.items()} | {"total": round(sum(items.values()), 2)}

    at_target, at_stop, flat = trip(target), trip(stop), trip(entry)
    reward = abs(float(target) - float(entry)) * qty
    risk = abs(float(entry) - float(stop)) * qty
    net_reward, net_risk = reward - at_target["total"], risk + at_stop["total"]
    be_move = flat["total"] / qty if qty > 0 else None
    return {
        "items": {k: v for k, v in at_target.items() if k != "total"}, "total": at_target["total"], "at_stop_total": at_stop["total"],
        "break_even_move": round(be_move, 4) if be_move is not None else None,
        "break_even_pct": round(be_move / float(entry) * 100.0, 4) if be_move is not None and entry > 0 else None,
        "gross_reward": round(reward, 2), "gross_risk": round(risk, 2), "net_reward": round(net_reward, 2), "net_risk": round(net_risk, 2),
        "net_reward_risk": round(net_reward / net_risk, 2) if net_risk > 0 else None,
        "segment": segment, "rates_of": day.isoformat(), "slippage_pct_per_side": slip_pct, "brokerage_per_order": brokerage,
        "basis": (f"Estimate: round trip exiting at the target, {segment.replace('_', ' ').lower()} charges at the rates of {day.isoformat()}, "
                  f"brokerage {brokerage:g} per order, slippage {slip_pct:g}% per side. A contract note replaces it."),
    }


def daily_loss_breached(capital_basis: float, settings: RiskSettings, closed_pnls_in_order: Sequence[float], open_marks: float) -> Dict[str, Any]:
    """Whether the day's PAPER P&L reached the limit at any point seen: after any close (in close order), or now with the
    open marks. Pure."""
    limit = max(0.0, float(capital_basis)) * settings.daily_loss_pct / 100.0
    running, worst = 0.0, 0.0
    for pnl in closed_pnls_in_order:
        running += float(pnl or 0.0)
        worst = min(worst, running)
    now = running + float(open_marks or 0.0)
    worst = min(worst, now)
    return {"limit": round(limit, 2), "realised": round(running, 2), "open_marks": round(float(open_marks or 0.0), 2), "now": round(now, 2),
            "worst": round(worst, 2), "breached": limit > 0 and worst <= -limit + 1e-9}


# --- the user's numbers -------------------------------------------------------------------------------------------------

async def enabled(session: AsyncSession, tenant_id: int) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def _account(session: AsyncSession, user: User) -> Optional[PaperAccountRecord]:
    return await session.scalar(select(PaperAccountRecord).where(PaperAccountRecord.tenant_id == user.tenant_id, PaperAccountRecord.user_id == user.id))


async def user_settings(session: AsyncSession, user: User, now: Optional[datetime] = None) -> RiskSettings:
    """The user's settings in force today (a loosening waits for the next IST day); a limit he did not set is off
    (TT-12). Reads only (no account is created)."""
    row = await _account(session, user)
    return settings_on(row.risk_settings_json if row is not None else None, to_ist(now).date())


async def capital_basis(session: AsyncSession, user: User, now: Optional[datetime] = None) -> float:
    """The PAPER starting capital plus the realised net P&L of this user's PAPER trades closed before today (IST)."""
    row = await _account(session, user)
    starting = float(row.starting_capital) if row is not None else float(app_config.PAPER_STARTING_CAPITAL)
    before = await session.scalar(select(func.coalesce(func.sum(TradeRecord.pnl), 0.0)).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.mode == MODE,
        TradeRecord.exit_time.is_not(None), TradeRecord.exit_time < trading_day_start(now)))
    return round(starting + float(before or 0.0), 2)


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def day_activity(session: AsyncSession, user: User, now: Optional[datetime] = None) -> Dict[str, Any]:
    """This user's PAPER day so far: net P&L of the trades closed today in close order, the open marks of positions
    opened and marked today (as the organisation's daily limit counts them), and the entries made today (a structure
    counts once)."""
    start = trading_day_start(now)
    trades = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.mode == MODE,
        (TradeRecord.exit_time >= start) | TradeRecord.exit_time.is_(None) | (TradeRecord.entry_time >= start))))
    closed = sorted((t for t in trades if t.exit_time is not None and _utc(t.exit_time) >= start),
                    key=lambda t: (_utc(t.exit_time or start), t.id))
    marks = 0.0
    for t in trades:
        if t.exit_time is None and t.mark_price is not None and t.mark_time is not None and _utc(t.entry_time) >= start and _utc(t.mark_time) >= start:
            marks += (1.0 if t.direction == "LONG" else -1.0) * (float(t.mark_price) - float(t.entry_price)) * float(t.quantity)
    entries = {(t.leg_group_id or f"t{t.id}") for t in trades if _utc(t.entry_time) >= start}
    return {"closed_pnls": [float(t.pnl or 0.0) for t in closed], "open_marks": round(marks, 2), "entries_today": len(entries)}


def _until(now: Optional[datetime]) -> datetime:
    """The end of the IST day the lockout belongs to (00:00 IST next day), as an aware instant."""
    ist = to_ist(now)
    return (ist.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(timezone.utc)


def lockout_text(pnl: float, limit: float, pct: float, basis: float, until: datetime) -> str:
    return (f"Daily loss limit reached: today's PAPER P&L {pnl:,.2f} is at or below -{limit:,.2f} ({pct:g}% of {basis:,.0f}). "
            f"New PAPER entries are locked until {until.astimezone(IST).strftime('%d %b %H:%M')} IST; exits stay available")


async def lockout(session: AsyncSession, user: User, now: Optional[datetime] = None, *, record: bool = True) -> Optional[Dict[str, Any]]:
    """Today's daily loss lockout of this user, or None. Recorded (once, with a notification) the first time the limit
    is seen reached; a recorded lockout stays for the day."""
    now = now or datetime.now(timezone.utc)
    day = to_ist(now).date()
    until = _until(now)
    row = await session.scalar(select(RiskLockoutRecord).where(
        RiskLockoutRecord.tenant_id == user.tenant_id, RiskLockoutRecord.user_id == user.id, RiskLockoutRecord.mode == MODE,
        RiskLockoutRecord.trading_day == day, RiskLockoutRecord.kind == KIND_DAILY_LOSS))
    if row is not None:
        return {"kind": KIND_DAILY_LOSS, "reason": row.reason, "pnl": float(row.pnl), "limit": float(row.limit_amount),
                "locked_at": _utc(row.locked_at).isoformat(), "until": until.isoformat()}
    from app.platform.controls import risk_ceilings
    settings, _ = effective_settings(await user_settings(session, user, now), await risk_ceilings(session))
    basis = await capital_basis(session, user, now)
    act = await day_activity(session, user, now)
    check = daily_loss_breached(basis, settings, act["closed_pnls"], act["open_marks"])
    if not check["breached"]:
        return None
    reason = lockout_text(check["worst"], check["limit"], settings.daily_loss_pct, basis, until)
    if record:
        lock = RiskLockoutRecord(tenant_id=user.tenant_id, user_id=user.id, mode=MODE, trading_day=day, kind=KIND_DAILY_LOSS,
                                 pnl=check["worst"], limit_amount=check["limit"], reason=reason[:300], locked_at=now)
        try:
            async with session.begin_nested():
                session.add(lock)
                await session.flush()
        except IntegrityError:          # recorded concurrently: that one stands
            return await lockout(session, user, now, record=False)
        from app.audit.log import write_audit_log
        from app.core.enums import NotificationSeverity, NotificationType
        from app.notifications.service import notify
        await write_audit_log(session, user.tenant_id, user.id, "paper_daily_loss_lockout", reason[:300])
        await notify(session, user.tenant_id, NotificationType.DAILY_LOSS_LIMIT, title="PAPER daily loss limit reached: new entries locked",
                     message=reason, severity=NotificationSeverity.WARNING, user_id=user.id)
    return {"kind": KIND_DAILY_LOSS, "reason": reason, "pnl": check["worst"], "limit": check["limit"],
            "locked_at": now.isoformat(), "until": until.isoformat()}


def _per_day_text(count: int, limit: int) -> str:
    return f"PAPER entries per day: {count} of {limit} made today - no new PAPER entries today; exits stay available"


async def entry_refusals(session: AsyncSession, user: User, now: Optional[datetime] = None, *, reserve: bool = False) -> List[str]:
    """Why this user's new PAPER entry is refused: the daily loss lockout, the entries-per-day limit. Entries only.
    `reserve` (the order pipelines): the entry takes one of the day's slots atomically (`reserve_entry`); a preview
    only reads the count."""
    reasons = []
    locked = await lockout(session, user, now)
    if locked:
        reasons.append(locked["reason"])
    settings = await user_settings(session, user, now)
    if settings.max_trades_per_day > 0:
        if reserve and not reasons:
            refused = await reserve_entry(session, user, settings.max_trades_per_day, now)
            if refused:
                reasons.append(refused)
        elif not reserve:
            act = await day_activity(session, user, now)
            used = max(act["entries_today"], await _slots_used(session, user, now))
            if used >= settings.max_trades_per_day:
                reasons.append(_per_day_text(used, settings.max_trades_per_day))
    return reasons


# The entry slot reserved in this task (the order pipelines release it when the entry does not go through).
RESERVED: ContextVar[Optional[int]] = ContextVar("risk_by_default_reserved_entry", default=None)


async def _slots_used(session: AsyncSession, user: User, now: Optional[datetime]) -> int:
    row = await _account(session, user)
    if row is None or row.entries_day != to_ist(now).date():
        return 0
    return int(row.entries_count or 0)


async def reserve_entry(session: AsyncSession, user: User, limit: int, now: Optional[datetime] = None) -> Optional[str]:
    """Takes one of the user's PAPER entry slots for the IST day, atomically: a conditional UPDATE on the user's PAPER
    account row (the row lock serialises concurrent entries of one user; the WHERE is re-checked after the wait), so two
    entries at once cannot both take the last slot. Returns why not, or None (and marks the slot as this task's)."""
    from app.paper.service import get_account
    day = to_ist(now).date()
    if await _account(session, user) is None:
        await get_account(session, user)                      # created on first use, as the PAPER account always is
    act = await day_activity(session, user, now)
    base = PaperAccountRecord.tenant_id == user.tenant_id, PaperAccountRecord.user_id == user.id
    # A new day starts from the entries already made today (e.g. before the flag was turned on).
    await session.execute(update(PaperAccountRecord).where(*base, (PaperAccountRecord.entries_day.is_(None)) | (PaperAccountRecord.entries_day != day))
                          .values(entries_day=day, entries_count=act["entries_today"]).execution_options(synchronize_session=False))
    done = await session.execute(update(PaperAccountRecord).where(*base, PaperAccountRecord.entries_day == day, PaperAccountRecord.entries_count < limit)
                                 .values(entries_count=PaperAccountRecord.entries_count + 1).execution_options(synchronize_session=False))
    if int(getattr(done, "rowcount", 0) or 0) == 0:
        used = int(await session.scalar(select(PaperAccountRecord.entries_count).where(*base)) or limit)
        return _per_day_text(used, limit)
    RESERVED.set(user.id)
    return None


async def release_entry(session: AsyncSession, user: User, now: Optional[datetime] = None) -> None:
    """Gives back the slot of an entry that did not go through (refused later in the pipeline). Never raises."""
    try:
        await session.execute(update(PaperAccountRecord).where(
            PaperAccountRecord.tenant_id == user.tenant_id, PaperAccountRecord.user_id == user.id,
            PaperAccountRecord.entries_day == to_ist(now).date(), PaperAccountRecord.entries_count > 0)
            .values(entries_count=PaperAccountRecord.entries_count - 1).execution_options(synchronize_session=False))
        await session.commit()
    except Exception:  # noqa: BLE001 - a slot not given back only makes the limit stricter
        await session.rollback()


P = ParamSpec("P")
R = TypeVar("R")


def releases_unused_entry_slot(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """For the two order pipelines: an entry slot taken during the call is given back when the entry was not placed."""
    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        token = RESERVED.set(None)
        try:
            result = await func(*args, **kwargs)
            outcome = result[0] if isinstance(result, tuple) else result
            # SE-7: an INCOMPLETE structure opened real positions (its filled legs) - the slot stays taken.
            if RESERVED.get() is not None and not getattr(outcome, "executed", False) and not getattr(outcome, "keeps_entry_slot", False):
                session, user = (kwargs.get("session") or args[0]), (kwargs.get("user") or args[1])
                await release_entry(session, user)  # type: ignore[arg-type]
            return result
        finally:
            RESERVED.reset(token)
    return wrapper


async def proposal_refusal(session: AsyncSession, tenant_id: int, user_id: Optional[int], now: Optional[datetime] = None) -> Optional[str]:
    """Why no entry proposal is raised for this user now (flag on and locked out, or at the day's entry limit), or None."""
    if user_id is None or not await enabled(session, tenant_id):
        return None
    user = await session.get(User, user_id)
    if user is None or user.tenant_id != tenant_id:
        return None
    reasons = await entry_refusals(session, user, now)
    return "; ".join(reasons) if reasons else None


def cap_config(cfg: RiskConfig, amount: float) -> Tuple[RiskConfig, Optional[str]]:
    """The organisation's config with its risk per trade lowered to `amount` when that is stricter (ADR-0003). The risk
    per trade is a percentage of the organisation's capital, so the amount is expressed as one (a hair above, so the
    floor in the sizer never loses a lot to rounding)."""
    if cfg.capital is None or cfg.capital <= 0:
        return cfg, None
    current = cfg.risk_amount()             # DEF-0: None = the organisation set no risk per trade - the user's cap alone
    if current is not None and amount >= current:
        return cfg, None
    pct = max(0.0, amount) * (1.0 + 1e-9) / cfg.capital * 100.0
    return cfg.model_copy(update={"risk_per_trade_pct": pct}), f"Risk by default: risk per trade capped at {amount:,.2f} (your PAPER cap)"


async def apply_user_cap(session: AsyncSession, user: User, cfg: RiskConfig, *, amount: Optional[float] = None,
                         now: Optional[datetime] = None) -> Tuple[RiskConfig, List[str]]:
    """Under the flag, a user's PAPER entry is sized within their own cap (or `amount`, a ticket's checked cap - an
    override included) as well as the organisation's limit, whichever is stricter."""
    if amount is None:
        from app.platform.controls import risk_ceilings
        settings, _ = effective_settings(await user_settings(session, user, now), await risk_ceilings(session))
        amount = risk_cap(await capital_basis(session, user, now), settings)["amount"]
        if amount is None:                            # TT-12: no cap of his own - the organisation's alone sizes it
            return cfg, []
    capped, note = cap_config(cfg, float(amount))
    return capped, [note] if note else []


async def override_counts(session: AsyncSession, user: User, now: Optional[datetime] = None) -> Dict[str, int]:
    """Entries this user placed above their cap (a deliberate override), today and in all."""
    async def count(*conditions) -> int:
        return int(await session.scalar(select(func.count()).select_from(TradePlanRecord).where(
            TradePlanRecord.tenant_id == user.tenant_id, TradePlanRecord.user_id == user.id, TradePlanRecord.override.is_(True),
            TradePlanRecord.status == "PLACED", *conditions)) or 0)
    return {"today": await count(TradePlanRecord.created_at >= trading_day_start(now)), "total": await count()}


async def after_close(session: AsyncSession, trade: TradeRecord) -> None:
    """Called after a PAPER close is booked (and committed): records the user's daily loss lockout at once if this close
    reached the limit, so it holds even if nothing reads it before the P&L recovers. Never raises; the exit is done."""
    try:
        if trade.mode != MODE or trade.user_id is None or not await enabled(session, trade.tenant_id):
            return
        user = await session.get(User, trade.user_id)
        if user is not None and user.tenant_id == trade.tenant_id:
            await lockout(session, user)
            await session.commit()
    except Exception:  # noqa: BLE001 - bookkeeping after an exit; never in its way (ADR-0004)
        import logging
        logging.getLogger(__name__).exception("Daily loss check after closing trade %s failed", trade.id)
        await session.rollback()


async def sweep(session: AsyncSession, now: Optional[datetime] = None) -> int:
    """The worker's pass: every user with PAPER activity today (a close today, or an open position) in an organisation
    with the flag on is checked, so a breach through open marks is recorded too. Returns the lockouts recorded."""
    now = now or datetime.now(timezone.utc)
    start = trading_day_start(now)
    pairs = list(await session.execute(select(TradeRecord.tenant_id, TradeRecord.user_id).where(
        TradeRecord.mode == MODE, TradeRecord.user_id.is_not(None),
        (TradeRecord.exit_time >= start) | TradeRecord.exit_time.is_(None)).distinct()))
    flags: Dict[int, bool] = {}
    recorded = 0
    for tenant_id, user_id in pairs:
        if tenant_id not in flags:
            flags[tenant_id] = await enabled(session, tenant_id)
        if not flags[tenant_id]:
            continue
        user = await session.get(User, user_id)
        if user is None or user.tenant_id != tenant_id:
            continue
        before = await session.scalar(select(RiskLockoutRecord.id).where(
            RiskLockoutRecord.user_id == user_id, RiskLockoutRecord.mode == MODE, RiskLockoutRecord.trading_day == to_ist(now).date(),
            RiskLockoutRecord.kind == KIND_DAILY_LOSS))
        if before is None and await lockout(session, user, now) is not None:
            recorded += 1
        await session.commit()
    return recorded
