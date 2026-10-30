"""Z5 / P1-d (P1 spec 1.2 and 2.2, Phase 0): the plan-before-trade PAPER ticket.

A discretionary PAPER entry starts from a plan: the setup (from the method library), the direction, entry, stop and
target, the reward:risk (at least the user's minimum), an optional time stop and an invalidation note, and the
pre-trade checklist. The ticket is built from the plan and checked on the server - every number the page shows comes
from here, the page computes nothing:

* **sizing** - the quantity from the user's risk cap and the stop distance (`app/risk_engine/by_default.py`), in whole
  lots, within the organisation's own limit and after the guardian's size cuts (drawdown ladder, event days, India VIX
  gate). A larger quantity needs a deliberate override with a reason, logged and counted, up to
  `RISK_OVERRIDE_MAX_MULTIPLE` x the cap and never past the organisation's limit;
* **refusals** - the daily loss lockout, the entries-per-day limit, the PAPER pause, kill switches and platform blocks,
  the guardian's rules (cool-down, drawdown pause, event blackout, VIX spike), a reward:risk under the minimum, levels on
  the wrong side of the entry;
* **the cost line** - brokerage, STT, exchange, SEBI, stamp duty, GST, a slippage estimate, the break-even move and the
  reward:risk after costs;
* **the checklist** - the user's ticks plus two the server fills (reward:risk and size within the cap). Skipping is
  allowed and counted as a discipline miss (stored on the plan; the journal reads it in P1-e).

Placing re-prices the entry at the user's own live price (as an approved proposal does, Z3-a): beyond the re-quote limit
nothing is placed and the plan must be updated. Then one PAPER order goes through the ordinary pipeline (kill switches,
platform controls, risk engine, order state machine) with the plan's quantity as its ceiling - or, under approval-only
mode (no entry without an approved proposal), an ENTRY proposal carrying those limits is raised and Approve places it.
The plan is stored with the order and the position (or the proposal), or with the reasons it was refused. PAPER only: nothing here can reach a
broker's order API, and nothing here ever stands in the way of an exit (ADR-0004).
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.core import config as app_config
from app.core.enums import SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.db.models import AiActionRecord, InstrumentRecord, Tenant, TradePlanRecord, TradeRecord, User
from app.execution import approval
from app.instruments.registry import get_contract_spec
from app.market_data.calendar import to_ist
from app.risk_engine import by_default

logger = logging.getLogger(__name__)
PriceLookup = Callable[..., Awaitable[float]]

SETUPS: Dict[str, str] = {
    "PULLBACK_AT_LEVEL": "Pullback at a level", "SR_FLIP": "Support/resistance flip", "BREAKOUT_RETEST": "Breakout retest",
    "RANGE_EDGE": "Range edge", "OI_IV": "OI / IV based", "OTHER": "Other",
}
CHECKLIST: Dict[str, str] = {
    "trend_aligned": "Trend aligned on the higher timeframe", "level_identified": "Level identified", "pullback_confirmed": "Pullback confirmed",
    "commitment_candle": "Commitment candle", "no_event": "No event within the hold window", "liquidity_ok": "Liquidity OK",
}
AUTO_CHECKS: Dict[str, str] = {"rr_ok": "Reward:risk at or above your minimum", "size_within_cap": "Size within your risk cap"}
TIME_STOP = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DERIVATIVE = re.compile(r"(\d(CE|PE)|FUT)$")          # an option or future trading symbol (NIFTY24OCT25000CE, ...FUT)
SESSION_OPEN, SESSION_CLOSE = "09:15", "15:30"


def wall_clock() -> datetime:
    """The time a time stop is checked against (one place, so a test can hold it)."""
    return datetime.now(timezone.utc)


@dataclass
class Plan:
    symbol: str
    direction: str                    # LONG | SHORT
    setup: str
    entry: float
    stop_loss: float
    target: float
    time_stop: Optional[str] = None   # "HH:MM" IST
    invalidation: Optional[str] = None
    checklist: Dict[str, bool] = field(default_factory=dict)
    quantity: Optional[float] = None  # None = the size the cap allows
    override: bool = False
    override_reason: Optional[str] = None


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(float(value), digits)


async def _org_config(session: AsyncSession, tenant_id: int, capital: Optional[float] = None) -> RiskConfig:
    """The organisation's limits (DEF-0: each not set = off); a % reads its own capital, else `capital` (PAPER)."""
    from app.platform.controls import clamp_config, risk_ceilings
    from app.risk_engine.routes import get_tenant_risk_config
    cfg = (await get_tenant_risk_config(tenant_id, session) or RiskConfig()).with_capital(capital)
    cfg, _ = clamp_config(cfg, await risk_ceilings(session))
    return cfg


CASH_TYPES = ("EQ", "INDEX")
CASH_EXCHANGES = ("NSE", "BSE")


async def is_cash_underlying(session: AsyncSession, symbol: str) -> bool:
    """Whether the ticket may take `symbol`: by the instrument master when it knows the symbol (an NSE/BSE EQ or INDEX
    row, any broker's master), else - a master not synced yet - not a registry contract (MCX, crypto) and not shaped
    like an option or future trading symbol."""
    rows = list(await session.execute(select(InstrumentRecord.exchange, InstrumentRecord.instrument_type)
                                      .where(InstrumentRecord.tradingsymbol == symbol.upper()).limit(50)))
    if rows:
        return any((ex or "").upper() in CASH_EXCHANGES and (kind or "").upper() in CASH_TYPES for ex, kind in rows)
    return get_contract_spec(symbol) is None and not DERIVATIVE.search(symbol.upper())


def _finite(value: float) -> Optional[float]:
    """DEF-0: a cap not set is infinite in the arithmetic and None (not set) in what the ticket shows and stores."""
    return value if math.isfinite(value) else None


async def assess(session: AsyncSession, user: User, plan: Plan, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Everything the ticket shows for this plan, checked: sizing, refusals, warnings, checklist, cost line. Reads only
    (a daily loss lockout seen for the first time is recorded, as the order pipeline would)."""
    from app.execution.signal_execution import entry_refusals
    from app.platform.controls import risk_ceilings
    from app.risk_engine.guardian import guard_entry
    asked_at = now
    now = now or datetime.now(timezone.utc)
    symbol, direction = plan.symbol.strip().upper(), plan.direction.upper()
    errors: List[str] = []
    refusals: List[str] = []
    warnings: List[str] = []
    if plan.setup not in SETUPS:
        errors.append(f"Setup must be one of: {', '.join(SETUPS.values())}")
    if direction not in ("LONG", "SHORT"):
        errors.append("Direction must be long or short")
    errors.extend(by_default.level_errors(direction, plan.entry, plan.stop_loss, plan.target))
    if plan.time_stop and (not TIME_STOP.match(plan.time_stop) or not SESSION_OPEN < plan.time_stop <= SESSION_CLOSE):
        errors.append(f"Time stop must be a time between {SESSION_OPEN} and {SESSION_CLOSE} IST (HH:MM)")
    elif plan.time_stop:
        earliest = (to_ist(asked_at or wall_clock()) + timedelta(minutes=app_config.TICKET_TIME_STOP_MIN_LEAD_MINUTES)).strftime("%H:%M")
        if plan.time_stop <= earliest:
            errors.append(f"Time stop {plan.time_stop} IST is already past (it must be after {earliest} IST): the position would close at once")
    if not await is_cash_underlying(session, symbol):
        errors.append("The PAPER ticket covers NSE/BSE cash underlyings in Phase 0 (no futures, options, commodities or crypto)")

    ceilings = await risk_ceilings(session)
    settings, setting_notes = by_default.effective_settings(await by_default.user_settings(session, user, now), ceilings)
    warnings.extend(setting_notes)
    basis = await by_default.capital_basis(session, user, now)
    cap_info = by_default.risk_cap(basis, settings)
    cfg = await _org_config(session, user.tenant_id, basis)
    # DEF-0: the organisation's risk per trade only when it set one (else no organisation cap: inf).
    org_amount = cfg.risk_amount()
    org_cap = round(org_amount, 2) if org_amount is not None else math.inf
    guard = await guard_entry(session, user.tenant_id, mode=by_default.MODE, underlying=symbol, cfg=cfg, now=asked_at, direction=direction,
                              user_id=user.id)
    refusals.extend(guard.reasons)
    warnings.extend(guard.notes)
    multiplier = max(0.0, float(guard.size_multiplier))
    # TT-12: no risk per trade of his own = no cap of his own; the organisation's risk per trade still sizes the entry.
    user_cap = float(cap_info["amount"]) if cap_info["amount"] is not None else math.inf
    base_cap = min(user_cap, org_cap)
    binding = "your cap" if user_cap <= org_cap else "the organisation's risk per trade"
    cap = round(base_cap * multiplier, 2) if math.isfinite(base_cap) else math.inf
    # DEF-0 D5: the override multiple exists only over a cap the trader set; never above the organisation's own limit.
    override_base = min(user_cap * app_config.RISK_OVERRIDE_MAX_MULTIPLE, org_cap)
    override_cap = round(override_base * multiplier, 2) if math.isfinite(override_base) else math.inf

    lot = float(cfg.lot_size or 1)            # cash underlyings only (checked above): the organisation's unit
    if math.isfinite(cap):
        suggested = by_default.size(plan.entry, plan.stop_loss, cap, lot)
    else:                                     # DEF-0: no cap set anywhere - one lot is the start, the trader sizes it
        one = by_default.size(plan.entry, plan.stop_loss, 0.0, lot)   # its risk per lot only (no cap: no whole-lot count)
        suggested = {**one, "lots": 1, "quantity": lot, "risk_amount": one["risk_per_lot"]}
    quantity = float(plan.quantity) if plan.quantity is not None else float(suggested["quantity"])
    per_unit = abs(float(plan.entry) - float(plan.stop_loss))
    risk_amount = round(quantity * per_unit, 2)
    rr = by_default.reward_risk(plan.entry, plan.stop_loss, plan.target)
    min_rr = settings.min_rr if settings.min_rr > 0 else None          # TT-12: no minimum unless he set one
    rr_ok = rr is not None and (min_rr is None or rr >= min_rr)
    within = risk_amount <= cap + 0.005
    if not errors:
        if not rr_ok and min_rr is not None:
            refusals.append(f"Reward:risk {rr if rr is not None else '-'} is below your minimum {settings.min_rr:g}")
        if plan.quantity is not None and (quantity <= 0 or abs(quantity / lot - round(quantity / lot)) > 1e-9):
            errors.append(f"Quantity must be a whole number of lots of {lot:g}")
        elif quantity <= 0:
            refusals.append(f"One lot ({lot:g}) risks {suggested['risk_per_lot']:,.2f} at the stop - more than the cap {cap:,.2f} ({binding}). "
                            "Move the stop closer or skip the trade")
        elif not within:
            reason = (plan.override_reason or "").strip()
            if not plan.override:
                refusals.append(f"{quantity:g} risks {risk_amount:,.2f} at the stop - above the cap {cap:,.2f} ({binding}). "
                                f"Up to {suggested['quantity']:g} fits; going above needs an override with a reason")
            elif base_cap < user_cap or risk_amount > override_cap + 0.005:
                refusals.append(f"Even with an override an entry may risk at most {override_cap:,.2f} "
                                + (f"({app_config.RISK_OVERRIDE_MAX_MULTIPLE:g} x your cap, within the organisation's limit); "
                                   if math.isfinite(user_cap) else "(the organisation's limit); ")
                                + f"{quantity:g} risks {risk_amount:,.2f}")
            elif len(reason) < app_config.RISK_OVERRIDE_MIN_REASON_CHARS:
                refusals.append(f"An override needs a reason of at least {app_config.RISK_OVERRIDE_MIN_REASON_CHARS} characters")
            else:
                warnings.append(f"Override: {quantity:g} risks {risk_amount:,.2f}, above your cap {cap:,.2f}. It is logged and counted")
    refusals.extend(await entry_refusals(session, await session.get(Tenant, user.tenant_id), user.tenant_id, by_default.MODE,
                                         f"plan_{plan.setup.lower()}", user=user, reserve=False))

    ticks = {key: bool(plan.checklist.get(key)) for key in CHECKLIST}
    auto = {"rr_ok": rr_ok, "size_within_cap": within and quantity > 0}
    complete = all(ticks.values()) and all(auto.values())
    if not complete:
        warnings.append("Checklist not complete: you may still place the trade; it is counted as a discipline miss")
    costs = by_default.cost_line(direction=direction, entry=plan.entry, stop=plan.stop_loss, target=plan.target, quantity=quantity,
                                 day=to_ist(now).date()) if quantity > 0 and not errors else None
    side = "BUY" if direction == "LONG" else "SELL"
    can_place = not errors and not refusals and quantity > 0
    return {
        "symbol": symbol, "direction": direction, "setup": plan.setup, "setup_label": SETUPS.get(plan.setup), "mode": by_default.MODE,
        "entry": plan.entry, "stop_loss": plan.stop_loss, "target": plan.target, "time_stop": plan.time_stop, "invalidation": plan.invalidation,
        "reward_risk": rr, "min_reward_risk": min_rr,
        "sizing": {"capital_basis": basis, "risk_per_trade_pct": settings.risk_per_trade_pct, "risk_per_trade_amount": settings.risk_per_trade_amount,
                   "user_cap": _round(user_cap) if math.isfinite(user_cap) else None, "org_cap": _finite(org_cap), "binding": binding if math.isfinite(base_cap) else None,
                   "size_multiplier": round(multiplier, 4), "cap": _finite(cap), "override_cap": _finite(override_cap), "lot_size": lot, "risk_per_unit": round(per_unit, 4),
                   "suggested_quantity": suggested["quantity"], "suggested_lots": suggested["lots"], "quantity": quantity,
                   "lots": round(quantity / lot, 4) if lot else None, "risk_amount": risk_amount,
                   "risk_pct_of_capital": round(risk_amount / basis * 100.0, 3) if basis > 0 else None,
                   "gauge": round(risk_amount / cap, 4) if math.isfinite(cap) and cap > 0 else None, "within_cap": within, "override": bool(plan.override),
                   "gauge_zone": "ok" if within else ("override" if risk_amount <= override_cap + 0.005 and base_cap >= user_cap else "over")},
        "checklist": [{"key": k, "label": v, "checked": ticks[k], "auto": False} for k, v in CHECKLIST.items()]
                     + [{"key": k, "label": v, "checked": auto[k], "auto": True} for k, v in AUTO_CHECKS.items()],
        "checklist_complete": complete, "costs": costs, "errors": errors, "refusals": refusals, "warnings": warnings, "can_place": can_place,
        "confirm_label": f"Place PAPER: {side} {quantity:g} {symbol}" if can_place else None,
        "as_of": now.isoformat(),
    }


def _plan_row(user: User, plan: Plan, view: Dict[str, Any]) -> TradePlanRecord:
    s = view["sizing"]
    return TradePlanRecord(tenant_id=user.tenant_id, user_id=user.id, mode=by_default.MODE, symbol=view["symbol"], direction=view["direction"],
                           setup=plan.setup, entry=plan.entry, stop_loss=plan.stop_loss, target=plan.target, reward_risk=view["reward_risk"],
                           time_stop=plan.time_stop, invalidation=(plan.invalidation or "").strip()[:300] or None,
                           checklist_json=json.dumps({c["key"]: c["checked"] for c in view["checklist"]}, sort_keys=True),
                           checklist_complete=view["checklist_complete"], quantity=s["quantity"], risk_amount=s["risk_amount"], risk_cap=s["cap"],
                           override=bool(plan.override and not s["within_cap"]),
                           override_reason=(plan.override_reason or "").strip()[:300] or None if plan.override else None,
                           costs_json=json.dumps(view["costs"] or {}, sort_keys=True), status="PENDING", reasons_json="[]")


async def place(session: AsyncSession, user: User, plan: Plan, *, price_lookup: Optional[PriceLookup],
                now: Optional[datetime] = None) -> Dict[str, Any]:
    """Checks the plan again, stores it, re-prices the entry at the live price (the user's own entitlement) and either
    places one PAPER order through the ordinary pipeline or - under approval-only mode, where no entry goes through
    without an approved proposal (Z3-a) - raises an ENTRY proposal carrying the plan's limits; Approve places it.
    Returns the stored plan: PLACED (order and position), PROPOSED (the proposal) or REFUSED (the reasons)."""
    from app.execution.signal_execution import execute_signal_for_user
    from app.trading.exit_rules import ExitRules
    asked_at = now
    now = now or datetime.now(timezone.utc)
    view = await assess(session, user, plan, asked_at)
    row = _plan_row(user, plan, view)
    session.add(row)
    await session.flush()
    reasons: List[str] = list(view["errors"]) + list(view["refusals"])
    price: Optional[float] = None
    if not reasons:
        if price_lookup is None:
            reasons.append(f"No live price for {view['symbol']}: a PAPER ticket is placed at the live price (link your broker session) - nothing was placed")
        else:
            try:
                price = float(await price_lookup(view["symbol"]))
            except Exception as exc:  # noqa: BLE001 - no quote, no order (NoLivePrice included)
                reasons.append(f"No live price for {view['symbol']} on your own broker session ({type(exc).__name__}) - nothing was placed")
    if not reasons and price is not None:
        verdict, why = approval.requote_check(view["direction"], float(plan.entry), price, stop_loss=plan.stop_loss, target1=plan.target,
                                              max_drift_pct=app_config.APPROVAL_REQUOTE_MAX_DRIFT_PCT,
                                              max_drift_ticks=app_config.APPROVAL_REQUOTE_MAX_DRIFT_TICKS, tick_size=approval.tick_size_for(view["symbol"]))
        if why == approval.NO_VALID_PRICE:
            reasons.append(f"The live price for {view['symbol']} is not usable - nothing was placed")
        elif verdict != "ok":
            reasons.append(f"{why} - update the plan to the current price; nothing was placed")
    if reasons:
        row.status, row.reasons_json = "REFUSED", json.dumps(reasons)
        await session.commit()
        return await plan_view(session, row)

    assert price is not None
    s = view["sizing"]
    multiplier = s["size_multiplier"] or 1.0
    # The checked cap before the guardian's cut (the pipeline applies the cut again), with the override's room if used.
    # TT-12: none of his own - the organisation's; DEF-0: neither set - no cap (None), the plan's own quantity sizes it.
    caps = [c for c in (s["user_cap"], s["org_cap"]) if c is not None]
    cap_amount = round(max(min(caps), s["risk_amount"] / multiplier if multiplier > 0 else 0.0), 2) if caps else None
    risk = abs(price - plan.stop_loss)
    signal = Signal(symbol=view["symbol"], strategy_id=f"plan_{plan.setup.lower()}", strategy_name=f"PAPER ticket: {SETUPS[plan.setup]}",
                    direction=SignalDirection(view["direction"]), timestamp=now, entry=price, stop_loss=plan.stop_loss, target1=plan.target,
                    risk_reward=round(abs(plan.target - price) / risk, 2) if risk > 0 else None, score=0, grade=SignalGrade.VALID,
                    reasons=[f"Plan #{row.id}: {SETUPS[plan.setup]}" + (f"; invalidation: {row.invalidation}" if row.invalidation else ""),
                             f"From the PAPER ticket at the live price {price:g}"], timeframe_combo="plan")
    exit_rules = ExitRules(time_exit_at=plan.time_stop).to_json() if plan.time_stop else None
    if row.override:
        await write_audit_log(session, user.tenant_id, user.id, "risk_cap_override",
                              f"plan #{row.id} {view['symbol']} qty {s['quantity']:g} risks {s['risk_amount']:,.2f} > cap {s['cap'] or 0:,.2f}: {row.override_reason}"[:500])
    await session.commit()
    plan_id = row.id
    try:
        if await approval.required(session, user.tenant_id):
            problems: List[str] = []
            try:
                action = await approval.propose_entry(session, user.tenant_id, signal=signal, strategy_id=signal.strategy_id, source="ticket",
                                                      idempotency_key=f"ticket:{plan_id}", exit_rules=exit_rules, user_id=user.id, now=now,
                                                      ticket={"plan_id": plan_id, "max_quantity": s["quantity"], "risk_cap_amount": cap_amount})
            except approval.EntriesPaused as exc:
                action, problems = None, [str(exc)]
            if action is None and not problems:
                problems = ["This plan was already proposed - nothing new was raised"]
            row = await session.get(TradePlanRecord, plan_id) or row
            row.proposal_id = action.id if action is not None else None
            row.status = "PROPOSED" if action is not None else "REFUSED"
            row.reasons_json = json.dumps([f"Sent for approval: proposal #{action.id} - Approve places the PAPER order"] if action is not None else problems)
            await session.commit()
            return await plan_view(session, row)
        result, order = await execute_signal_for_user(
            session, user, mode=by_default.MODE, strategy_id=signal.strategy_id, signal=signal, idempotency_key=f"ticket:{plan_id}",
            exit_rules=exit_rules, holding="INTRADAY", max_quantity=s["quantity"], risk_cap_amount=cap_amount)
        row = await session.get(TradePlanRecord, plan_id) or row
        row.order_id, row.trade_id = order.id, order.trade_id
        row.status = "PLACED" if result.executed else "REFUSED"
        row.reasons_json = json.dumps(list(result.reasons) or ([] if result.executed else [f"Order {order.status}"]))
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - the plan never stays PENDING without a reason
        logger.exception("PAPER ticket plan %s failed", plan_id)
        await session.rollback()
        row = await session.get(TradePlanRecord, plan_id) or row
        row.status, row.reasons_json = "FAILED", json.dumps([f"Not placed: {type(exc).__name__}: {exc}"[:300]])
        await session.commit()
    return await plan_view(session, row)


async def settle_from_proposal(session: AsyncSession, tenant_id: int, plan_id: int, values: Dict[str, Any], order: Any) -> None:
    """The ticket's plan after its proposal was executed (Z3-a): PLACED with the order and position, or FAILED with
    why. Called by `approval.execute_entry`; never raises (the approval's own outcome is already recorded)."""
    try:
        row = await session.get(TradePlanRecord, plan_id)
        if row is None or row.tenant_id != tenant_id:
            return
        if values.get("status") == "EXECUTED":
            row.status, row.trade_id = "PLACED", values.get("trade_id")
            row.order_id = getattr(order, "id", None)
            row.reasons_json = json.dumps([str(values.get("result") or "Placed after approval")])
        else:
            row.status, row.reasons_json = "FAILED", json.dumps([str(values.get("result") or "The approved proposal failed")])
        await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Settling plan %s from its proposal failed", plan_id)
        await session.rollback()


async def plan_view(session: AsyncSession, row: TradePlanRecord) -> Dict[str, Any]:
    """The stored plan and what became of it: the fill (the live price, and the quantity the pipeline placed - smaller
    than planned when the price moved away from the stop, so the risk stays within the cap)."""
    filled, proposal = None, None
    trade_id = row.trade_id
    if row.proposal_id is not None:
        action = await session.get(AiActionRecord, row.proposal_id)
        if action is not None and action.tenant_id == row.tenant_id:
            proposal = {"id": action.id, "status": action.status, "result": action.result}
            trade_id = trade_id or action.trade_id
    if trade_id is not None:
        trade = await session.get(TradeRecord, trade_id)
        if trade is not None and trade.tenant_id == row.tenant_id:
            filled = {"quantity": float(trade.quantity), "price": float(trade.entry_price),
                      "risk_amount": round(abs(float(trade.entry_price) - float(trade.stop_loss)) * float(trade.quantity), 2)}

    def loads(text: Optional[str], default: Any) -> Any:
        try:
            return json.loads(text) if text else default
        except ValueError:
            return default
    return {"id": row.id, "status": row.status, "symbol": row.symbol, "direction": row.direction, "setup": row.setup,
            "setup_label": SETUPS.get(row.setup, row.setup), "entry": row.entry, "stop_loss": row.stop_loss, "target": row.target,
            "reward_risk": row.reward_risk, "time_stop": row.time_stop, "invalidation": row.invalidation, "checklist": loads(row.checklist_json, {}),
            "checklist_complete": row.checklist_complete, "quantity": row.quantity, "risk_amount": row.risk_amount, "risk_cap": row.risk_cap,
            "override": row.override, "override_reason": row.override_reason, "costs": loads(row.costs_json, {}),
            "reasons": loads(row.reasons_json, []), "order_id": row.order_id, "trade_id": trade_id, "filled": filled, "proposal": proposal,
            "created_at": (row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)).isoformat()}


async def recent_plans(session: AsyncSession, user: User, limit: int = 20) -> List[Dict[str, Any]]:
    rows = await session.scalars(select(TradePlanRecord).where(TradePlanRecord.tenant_id == user.tenant_id, TradePlanRecord.user_id == user.id)
                                 .order_by(TradePlanRecord.id.desc()).limit(limit))
    return [await plan_view(session, row) for row in rows]


async def risk_status(session: AsyncSession, user: User, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The user's risk-by-default state for the ticket's status strip: settings, capital basis and cap, today's P&L
    against the daily limit, the lockout (why and until when), entries today, overrides, and the India VIX gate."""
    from app.platform.controls import risk_ceilings
    from app.risk_engine import vix_gate
    asked_at = now
    now = now or datetime.now(timezone.utc)
    ceilings = await risk_ceilings(session)
    stored = await by_default.user_settings(session, user, now)
    account_row = await by_default._account(session, user)
    pending = by_default.pending_of(account_row.risk_settings_json if account_row is not None else None)
    if pending is not None and str(pending["from"]) <= to_ist(now).date().isoformat():
        pending = None
    settings, notes = by_default.effective_settings(stored, ceilings)
    basis = await by_default.capital_basis(session, user, now)
    act = await by_default.day_activity(session, user, now)
    day = by_default.daily_loss_breached(basis, settings, act["closed_pnls"], act["open_marks"])
    locked = await by_default.lockout(session, user, now)
    await session.commit()
    return {
        "mode": by_default.MODE, "settings": stored.as_dict(), "effective": settings.as_dict(), "notes": notes, "pending": pending,
        # TT-12: the keys he set himself; every other limit is off ("Not set"). `defaults` are all off.
        "set": by_default.set_on(account_row.risk_settings_json if account_row is not None else None, to_ist(now).date()),
        "defaults": by_default.RiskSettings.defaults().as_dict(), "bounds": {k: list(v) for k, v in by_default.BOUNDS.items()},
        "labels": by_default.LABELS, "ceilings": {"risk_per_trade_pct": ceilings.get("risk_per_trade_pct"), "daily_loss_pct": ceilings.get("max_daily_loss_pct")},
        "capital_basis": basis, "risk_cap": by_default.risk_cap(basis, settings),
        "today": {"realised": day["realised"], "open_marks": day["open_marks"], "pnl": day["now"], "worst": day["worst"], "limit": day["limit"],
                  "used": round(max(0.0, -day["worst"]) / day["limit"], 4) if day["limit"] > 0 else None,
                  "entries": act["entries_today"], "max_entries": settings.max_trades_per_day},
        "lockout": locked, "overrides": await by_default.override_counts(session, user, now),
        "override_max_multiple": app_config.RISK_OVERRIDE_MAX_MULTIPLE, "override_min_reason_chars": app_config.RISK_OVERRIDE_MIN_REASON_CHARS,
        "vix_gate": await vix_gate.status(session, user.tenant_id, asked_at, viewer_id=user.id),
        "setups": SETUPS, "checklist": CHECKLIST, "exits": "Exits are never locked: stops, targets, time stops and Exit now always work.",
        "as_of": now.isoformat(),
    }


async def save_settings(session: AsyncSession, user: User, values: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Saves the user's own settings: a tightening applies at once, a loosening from the next IST day (so a limit
    cannot be widened in the middle of the day it protects). Returns what was applied and what waits."""
    from app.paper.service import get_account
    from app.platform.controls import risk_ceilings
    now = now or datetime.now(timezone.utc)
    errors = by_default.validate_settings(values, await risk_ceilings(session))
    if errors:
        raise ValueError("; ".join(errors))
    account = await get_account(session, user)
    text, applied, deferred, start = by_default.plan_change(account.risk_settings_json, values, to_ist(now).date())
    account.risk_settings_json = text
    def said(v: Any) -> str:
        return "not set" if v is None else f"{v:g}"
    detail = (", ".join(f"{k} -> {said(v)} now" for k, v in applied.items()) + ("; " if applied and deferred else "")
              + ", ".join(f"{k} -> {said(v)} from {start}" for k, v in deferred.items())) or "no change"
    await write_audit_log(session, user.tenant_id, user.id, "paper_risk_settings_set", detail[:500])
    await session.commit()
    return {"applied": applied, "deferred": deferred, "from": start if deferred else None}
