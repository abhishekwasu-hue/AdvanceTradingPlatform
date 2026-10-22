"""FNO-1 (owner order ATP_SESSION_FNO_WORKSPACE.md, sections 3 and 6): the ONE order ticket for an option contract the
user picked himself on the chain - PAPER only (Phase 0).

The ticket is the user's own manual order: side, quantity in LOTS (units = lots x the instrument master's lot size),
Market / Limit / SL / SL-M, MIS / NRML, and a stop-loss for the position. Every number the confirm step shows comes from
here, before anything is placed:

* **the contract** - from the instrument master (trading symbol, lot size, tick size); a contract the master does not
  hold is not traded (the master syncs daily);
* **the price** - the live price on the user's own broker session (`price_lookup_for_user`, never a teammate's); a
  preview without one uses the price the chain showed, labelled so. PAPER fills at once only what the price reaches
  now: a market order at the live price, a limit order that is marketable, an SL / SL-M order whose trigger the price
  has passed. A resting order is not kept (it is refused with plain words) - open question FNO-1-3;
* **the margin** - the Options Builder's labelled estimate (`options_builder.metrics.margin_estimate`): a bought option
  needs its premium; a sold one needs the broker's SPAN margin, which PAPER does not have (said so, never invented);
* **the max loss** - a bought option: the premium (or down to its stop); a sold option: up to its stop, which is
  required (without one the loss has no limit);
* **the risk checks** - the same ones every entry passes (kill switches, platform controls, the PAPER pause, the daily
  loss lockout and entries-per-day limit, approval-only mode, experience gates, the F&O ban, the Risk Guardian, the
  risk engine's size within the risk per trade), in plain words. The order pipeline runs them again when it places.

Placing sends ONE PAPER order through the ordinary pipeline (`execute_signal_for_user`, mode PAPER, the order state
machine and trail) with the asked lots as the ceiling. `phase0_lock.assert_paper` is asserted right before it. Nothing
here can reach a broker's order API, the AI never calls it (ADR-0006), and nothing here touches an exit (ADR-0004):
exits go through `POST /api/positions/{id}/exit-now`.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AssetClass, SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.db.models import InstrumentRecord, Tenant, TradeRecord, User
from app.instruments import master
from app.instruments.models import ContractSpec
from app.market_data.calendar import to_ist

logger = logging.getLogger(__name__)
PriceLookup = Callable[..., Awaitable[float]]

MODE = "PAPER"
STRATEGY_ID = "manual_fno"
SIDES = ("BUY", "SELL")
RIGHTS = ("CE", "PE")
ORDER_TYPES = ("MARKET", "LIMIT", "SL", "SL-M")
PRODUCTS = ("MIS", "NRML")
MAX_LOTS = 500           # a ticket's own upper bound; the exchange's freeze quantity is not in the master yet (FNO-1-2)
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
PRACTICE = "Practice mode (PAPER): no real order is sent and no money moves."


@dataclass
class OrderAsk:
    underlying: str
    expiry: date
    strike: float
    right: str                       # CE | PE
    side: str                        # BUY | SELL
    lots: int
    order_type: str = "MARKET"       # MARKET | LIMIT | SL | SL-M
    product: str = "MIS"             # MIS (intraday) | NRML (carry forward)
    price: Optional[float] = None    # LIMIT and SL: the limit price
    trigger_price: Optional[float] = None   # SL and SL-M
    stop_loss: Optional[float] = None       # the position's stop (premium); required for a SELL
    chain_ltp: Optional[float] = None       # what the chain showed: a preview's price when no live price can be read
    client_ref: Optional[str] = None        # the ticket's own id: a second tap on Place never places twice


@dataclass
class Contract:
    underlying: str
    tradingsymbol: str
    exchange: str
    instrument_key: str
    expiry: date
    strike: float
    right: str
    lot_size: int
    tick_size: float

    @property
    def label(self) -> str:
        return contract_label(self.underlying, self.expiry, self.strike, self.right)

    def as_dict(self) -> Dict[str, Any]:
        return {"underlying": self.underlying, "tradingsymbol": self.tradingsymbol, "exchange": self.exchange,
                "instrument_key": self.instrument_key, "expiry": self.expiry.isoformat(), "strike": self.strike,
                "right": self.right, "lot_size": self.lot_size, "tick_size": self.tick_size, "label": self.label,
                "freeze_quantity": None}


def contract_label(underlying: str, expiry: date, strike: float, right: str) -> str:
    """The contract as traders write it: "NIFTY 28 OCT 22800 CE"."""
    k = int(strike) if float(strike).is_integer() else round(float(strike), 2)
    return f"{underlying} {expiry.day:02d} {MONTHS[expiry.month - 1]} {k} {right}"


class ContractNotFound(LookupError):
    """The instrument master holds no such contract (not synced yet, or no such strike / expiry)."""


async def find_contract(session: AsyncSession, underlying: str, expiry: date, strike: float, right: str) -> Contract:
    """The contract from the instrument master: Upstox's master first (ATP's own data connection), else any broker's."""
    name = master.underlying_of(underlying)
    row = await master.find_option(session, name, expiry, strike, right)
    if row is None:
        row = await session.scalar(select(InstrumentRecord).where(
            InstrumentRecord.underlying == name, InstrumentRecord.instrument_type == right.upper(), InstrumentRecord.expiry == expiry,
            func.abs(InstrumentRecord.strike - strike) < 1e-6,
        ).order_by(InstrumentRecord.broker).limit(1))
    if row is None or row.expiry is None:
        raise ContractNotFound(f"{contract_label(name, expiry, strike, right)} is not in the instrument list yet. It updates every "
                               "morning; try again later.")
    return Contract(underlying=name, tradingsymbol=row.tradingsymbol, exchange=row.exchange or master.derivatives_exchange(name),
                    instrument_key=row.instrument_key, expiry=row.expiry, strike=float(row.strike or strike), right=right.upper(),
                    lot_size=int(row.lot_size or 1), tick_size=float(row.tick_size or 0.05))


# --- pure parts (tested directly) ---------------------------------------------------------------------------------------

def on_tick(value: float, tick: float) -> bool:
    return tick > 0 and abs(value / tick - round(value / tick)) < 1e-6


def paper_fill(order_type: str, side: str, ltp: float, price: Optional[float], trigger: Optional[float]) -> Tuple[Optional[float], str]:
    """Where a PAPER order fills now at the live price `ltp`, or (None, why not). PAPER keeps no resting order."""
    buy = side == "BUY"
    if order_type == "MARKET":
        return ltp, f"Fills at the live price {ltp:,.2f}"
    if order_type == "LIMIT":
        assert price is not None
        if (buy and ltp <= price) or (not buy and ltp >= price):
            return ltp, f"Your limit {price:,.2f} is reached now: fills at {ltp:,.2f}"
        return None, (f"The price ({ltp:,.2f}) has not reached your limit {price:,.2f}. Practice mode fills an order only when "
                      "the price reaches it now; it does not keep a waiting order yet.")
    assert trigger is not None
    triggered = (buy and ltp >= trigger) or (not buy and ltp <= trigger)
    if not triggered:
        return None, (f"The price ({ltp:,.2f}) has not reached your trigger {trigger:,.2f}. Practice mode does not keep a waiting "
                      "stop order yet.")
    if order_type == "SL":
        assert price is not None
        if (buy and ltp > price) or (not buy and ltp < price):
            return None, f"Triggered, but the price ({ltp:,.2f}) is past your limit {price:,.2f}: it would not fill now."
        return ltp, f"Triggered at {trigger:,.2f}: fills at {ltp:,.2f}"
    return ltp, f"Triggered at {trigger:,.2f}: fills at {ltp:,.2f}"


def ask_errors(ask: OrderAsk, tick: float) -> List[str]:
    """What is wrong with the ticket itself, in plain words (nothing about the market or the user's limits)."""
    errors: List[str] = []
    if ask.side not in SIDES:
        errors.append("Choose Buy or Sell")
    if ask.order_type not in ORDER_TYPES:
        errors.append("Choose Market, Limit, SL or SL-M")
    if ask.product not in PRODUCTS:
        errors.append("Choose MIS (intraday) or NRML (carry forward)")
    if not 1 <= int(ask.lots) <= MAX_LOTS:
        errors.append(f"Lots must be between 1 and {MAX_LOTS}")
    if ask.order_type in ("LIMIT", "SL") and not ask.price:
        errors.append("Enter the limit price")
    if ask.order_type in ("SL", "SL-M") and not ask.trigger_price:
        errors.append("Enter the trigger price")
    for name, value in (("Limit price", ask.price), ("Trigger price", ask.trigger_price), ("Stop-loss", ask.stop_loss)):
        if value is not None and not on_tick(float(value), tick):
            errors.append(f"{name} {value:g} is not a multiple of the tick size {tick:g}")
    if ask.order_type == "SL" and ask.price and ask.trigger_price:
        if ask.side == "BUY" and ask.price < ask.trigger_price:
            errors.append("For a buy SL order the limit price must be at or above the trigger")
        if ask.side == "SELL" and ask.price > ask.trigger_price:
            errors.append("For a sell SL order the limit price must be at or below the trigger")
    if ask.side == "SELL" and ask.stop_loss is None:
        errors.append("A sold option needs a stop-loss: without one the loss has no limit")
    return errors


def stop_errors(side: str, fill: float, stop: Optional[float]) -> List[str]:
    if stop is None:
        return []
    if side == "BUY" and not stop < fill:
        return [f"The stop-loss ({stop:,.2f}) must be below the price ({fill:,.2f}) for a bought option"]
    if side == "SELL" and not stop > fill:
        return [f"The stop-loss ({stop:,.2f}) must be above the price ({fill:,.2f}) for a sold option"]
    return []


def position_stop(side: str, stop: Optional[float], tick: float) -> float:
    """The stop stored on the position: the user's own, or - a bought option without one - one tick (the whole premium
    is at risk, which is what the risk engine sizes against)."""
    if stop is not None:
        return float(stop)
    assert side == "BUY"
    return float(tick)


def max_loss(side: str, fill: float, stop: Optional[float], units: float) -> Dict[str, Any]:
    if side == "BUY":
        if stop is None:
            return {"value": round(fill * units, 2), "basis": "The premium you pay, if the option expires worthless."}
        return {"value": round((fill - stop) * units, 2), "basis": f"Down to your stop-loss {stop:,.2f}. At most the premium you pay."}
    assert stop is not None
    return {"value": round((stop - fill) * units, 2), "basis": f"Up to your stop-loss {stop:,.2f}. A gap past the stop can lose more."}


def margin(side: str, right: str, strike: float, fill: float, lots: int, lot_size: int, expiry: date) -> Dict[str, Any]:
    """The Options Builder's labelled estimate for this one leg."""
    from app.options_builder.metrics import margin_estimate
    leg = {"direction": side, "option_type": right, "strike": strike, "premium": fill, "lots": lots, "lot_size": lot_size,
           "expiry": expiry.isoformat()}
    units = lots * lot_size
    extremes = {"unbounded_loss": False, "max_loss": -fill * units} if side == "BUY" else {"unbounded_loss": right == "CE",
                                                                                           "max_loss": None}
    out = margin_estimate([leg], extremes)
    if side == "SELL":
        out["basis"] = "A sold option needs your broker's SPAN and exposure margin, which practice mode does not have."
    return out


PLAIN = (
    (re.compile(r"^Max trades per day reached \((\d+)\)"), "You have reached your limit of {0} trades for today."),
    (re.compile(r"^Max open positions reached \((\d+)\)"), "You already hold {0} positions, your limit. Exit one before a new entry."),
    (re.compile(r"^Max consecutive losses reached \((\d+)\)"), "{0} losses in a row: new entries pause for a cool-down."),
    (re.compile(r"^Daily loss limit breached"), "Today's loss limit is reached: no new entries today. Exits still work."),
    (re.compile(r"^Computed position size is below one lot"), "One lot risks more than your risk per trade allows. Set a closer "
                                                               "stop-loss or a smaller position."),
    (re.compile(r"^Approval-only mode"), "Your organisation approves every entry first (approval-only mode). Orders from the chain "
                                         "cannot be sent for approval yet."),
)


def plain(reason: str) -> str:
    """A risk check's reason in plain words (the reasons already written for people pass unchanged)."""
    for pattern, text in PLAIN:
        m = pattern.match(reason)
        if m:
            return text.format(*m.groups())
    return reason


# --- the ticket ----------------------------------------------------------------------------------------------------

def _signal(contract: Contract, side: str, fill: float, stop: float, now: datetime, note: str) -> Signal:
    return Signal(symbol=contract.tradingsymbol, strategy_id=STRATEGY_ID, strategy_name="Manual order (option chain)",
                  direction=SignalDirection.LONG if side == "BUY" else SignalDirection.SHORT, timestamp=now, entry=fill, stop_loss=stop,
                  target1=None, risk_reward=None, score=0, grade=SignalGrade.VALID, reasons=[f"Manual order: {side} {contract.label}", note],
                  timeframe_combo="manual")


def _spec(contract: Contract) -> ContractSpec:
    return ContractSpec(symbol=contract.tradingsymbol, exchange=contract.exchange, asset_class=AssetClass.INDEX_OPTION,
                        description=f"{contract.label} ({contract.exchange})", lot_size=float(contract.lot_size),
                        tick_size=contract.tick_size)


def _meta(contract: Contract, side: str) -> Dict[str, Any]:
    from app.option_chain.chain_view import spot_instrument
    return {"instrument_kind": "OPTION", "exchange": contract.exchange, "instrument_key": contract.instrument_key,
            "lot_size": contract.lot_size, "expiry": contract.expiry, "option_position": "BUY" if side == "BUY" else "WRITE",
            "underlying_symbol": spot_instrument(contract.underlying)[0]}


async def _effective_config(session: AsyncSession, user: User, direction: str, underlying: str, now: datetime) -> Tuple[RiskConfig, List[str], List[str]]:
    """The risk config the pipeline sizes with (organisation, clamped, the user's own cap, the guardian's cut), and the
    guardian's refusals and notes - the same steps as `execute_signal_for_user`, read only."""
    from app.platform.controls import clamp_config, risk_ceilings
    from app.risk_engine import by_default
    from app.risk_engine.guardian import apply_multiplier, guard_entry
    from app.risk_engine.routes import get_tenant_risk_config
    cfg = await get_tenant_risk_config(user.tenant_id, session) or RiskConfig()
    cfg, notes = clamp_config(cfg, await risk_ceilings(session))
    if await by_default.enabled(session, user.tenant_id):
        cfg, cap_notes = await by_default.apply_user_cap(session, user, cfg)
        notes = list(notes) + list(cap_notes)
    guard = await guard_entry(session, user.tenant_id, mode=MODE, underlying=underlying, cfg=cfg, direction=direction, user_id=user.id)
    return apply_multiplier(cfg, guard.size_multiplier), list(guard.reasons), list(notes) + list(guard.notes)


async def _price(lookup: Optional[PriceLookup], contract: Contract) -> Tuple[Optional[float], Optional[str]]:
    """The live price on the user's own session: by trading symbol, then by instrument key (Upstox)."""
    if lookup is None:
        return None, "No live price: connect and log in to your broker"
    problem = "No live price for this contract on your broker session"
    for symbol in dict.fromkeys((contract.tradingsymbol, contract.instrument_key)):
        if not symbol:
            continue
        try:
            value = float(await lookup(symbol, contract.exchange))
        except Exception as exc:  # noqa: BLE001 - no quote: try the next identifier, then say so (never a made-up price)
            logger.info("FNO ticket: no price for %s on %s (%s)", symbol, contract.exchange, type(exc).__name__)
            continue
        if math.isfinite(value) and value > 0:
            return value, None
    return None, problem


async def assess(session: AsyncSession, user: User, ask: OrderAsk, *, price_lookup: Optional[PriceLookup],
                 live_only: bool = False, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Everything the confirm step shows, checked. Reads only (no entry slot is taken, nothing is stored)."""
    from app.execution import approval
    from app.execution.signal_execution import entry_refusals
    from app.paper import experience
    from app.risk_engine.risk_manager import RiskManager
    from app.trading.persistence import build_trading_day_state
    now = now or datetime.now(timezone.utc)
    errors: List[str] = []
    refusals: List[str] = []
    warnings: List[str] = []
    side, right = ask.side.upper(), ask.right.upper()
    ask.side, ask.right, ask.order_type, ask.product = side, right, ask.order_type.upper(), ask.product.upper()
    try:
        contract: Optional[Contract] = await find_contract(session, ask.underlying, ask.expiry, ask.strike, right)
    except ContractNotFound as exc:
        contract = None
        errors.append(str(exc))
    today = to_ist(now).date()
    if ask.expiry < today:
        errors.append("This expiry has passed. Pick a current expiry.")
    elif ask.expiry == today:
        warnings.append("This contract expires today.")
    tick = contract.tick_size if contract else 0.05
    errors.extend(ask_errors(ask, tick))
    lot_size = contract.lot_size if contract else None
    units = float(ask.lots * lot_size) if lot_size else None

    price, price_source = None, None
    if contract is not None:
        price, problem = await _price(price_lookup, contract)
        price_source = "live" if price is not None else None
        if price is None and not live_only and ask.chain_ltp and ask.chain_ltp > 0:
            price, price_source = float(ask.chain_ltp), "chain"
            warnings.append("No live price just now: these figures use the price the chain showed. Placing uses the live price.")
        elif price is None:
            errors.append(f"{problem}. Nothing can be placed without it.")

    fill, fill_note = None, ""
    if price is not None and not errors:
        fill, fill_note = paper_fill(ask.order_type, side, price, ask.price, ask.trigger_price)
        if fill is None:
            refusals.append(fill_note)
    reference = fill if fill is not None else (ask.price or price)
    if reference is not None and not errors:
        errors.extend(stop_errors(side, float(reference), ask.stop_loss))

    tenant = await session.get(Tenant, user.tenant_id)
    refusals.extend(plain(r) for r in await entry_refusals(session, tenant, user.tenant_id, MODE, STRATEGY_ID, user=user, reserve=False))
    gate = await approval.gate_refusal(session, user.tenant_id, deployment_id=None, origin=approval.ORIGIN_AUTOMATIC, approval_action_id=None)
    if gate:
        refusals.append(plain(gate))
    direction = SignalDirection.LONG if side == "BUY" else SignalDirection.SHORT
    if contract is not None:
        refusals.extend(await experience.symbol_entry_refusals(session, user, contract.tradingsymbol, direction))
        from app.compliance import fo_limits
        ban, ban_note = await fo_limits.entry_refusal(session, _meta(contract, side)["underlying_symbol"], now)
        if ban:
            refusals.append(ban)
        elif ban_note:
            warnings.append(ban_note)

    risk: Dict[str, Any] = {}
    if contract is not None and fill is not None and not errors and units:
        cfg, guard_reasons, notes = await _effective_config(session, user, direction.value, contract.tradingsymbol, now)
        refusals.extend(plain(r) for r in guard_reasons)
        warnings.extend(n for n in notes if n)
        stop = position_stop(side, ask.stop_loss, tick)
        decision = RiskManager(cfg).validate_and_size(_signal(contract, side, fill, stop, now, fill_note), await build_trading_day_state(session, user),
                                                      contract_spec=_spec(contract))
        cap_amount = round(cfg.capital * cfg.risk_per_trade_pct / 100.0, 2)
        risk = {"risk_per_trade": cap_amount, "allowed_lots": int(decision.quantity // contract.lot_size) if decision.approved else 0}
        if not decision.approved:
            refusals.extend(plain(r) for r in decision.reasons)
        elif decision.quantity + 1e-9 < units:
            refusals.append(f"Your risk per trade (₹{cap_amount:,.0f}) allows {risk['allowed_lots']} lot(s) at this stop-loss. "
                            "Lower the lots or set a closer stop-loss.")

    loss = max_loss(side, fill, ask.stop_loss, units) if fill is not None and units and not errors else None
    margin_line = margin(side, right, contract.strike, fill, ask.lots, contract.lot_size, contract.expiry) if (
        contract is not None and fill is not None and not errors) else None
    charges = None
    if fill is not None and units and not errors:
        from app.execution.india_costs import leg_cost
        cost = leg_cost(today, "buy" if side == "BUY" else "sell", fill, units, "OPTION")
        charges = {"total": round(cost["total"], 2), "estimated": True}
    can_place = not errors and not refusals and fill is not None
    label = contract.label if contract else contract_label(master.underlying_of(ask.underlying), ask.expiry, ask.strike, right)
    return {
        "mode": MODE, "practice": PRACTICE, "contract": contract.as_dict() if contract else None, "label": label,
        "side": side, "lots": ask.lots, "lot_size": lot_size, "units": units, "order_type": ask.order_type, "product": ask.product,
        "price": price, "price_source": price_source, "fill_price": fill, "fill_note": fill_note or None,
        "stop_loss": ask.stop_loss, "margin": margin_line, "max_loss": loss, "charges": charges, "risk": risk,
        "errors": errors, "refusals": refusals, "warnings": warnings, "can_place": can_place,
        "confirm_label": f"Place PAPER order: {side.title()} {ask.lots} lot{'s' if ask.lots != 1 else ''} ({units:g} qty)" if can_place and units else None,
        "as_of": now.isoformat(),
    }


async def place(session: AsyncSession, user: User, ask: OrderAsk, *, price_lookup: Optional[PriceLookup],
                now: Optional[datetime] = None) -> Dict[str, Any]:
    """Checks the ticket again at the live price and places ONE PAPER order through the ordinary pipeline, or says why
    not. Never a broker order: the mode is PAPER, asserted at the chokepoint under the Phase 0 lock."""
    from app.execution.signal_execution import execute_signal_for_user
    from app.platform import phase0_lock
    now = now or datetime.now(timezone.utc)
    view = await assess(session, user, ask, price_lookup=price_lookup, live_only=True, now=now)
    if not view["can_place"]:
        await session.commit()
        return {**view, "status": "REFUSED", "reasons": view["errors"] + view["refusals"], "order_id": None, "trade_id": None, "filled": None}
    await phase0_lock.is_on(session, user.tenant_id)            # refreshes the lock state the assertion reads
    mode = phase0_lock.assert_paper(MODE, user.tenant_id)
    contract = await find_contract(session, ask.underlying, ask.expiry, ask.strike, ask.right)
    fill = float(view["fill_price"])
    stop = position_stop(ask.side, ask.stop_loss, contract.tick_size)
    key = f"fno:{user.id}:{ask.client_ref}" if ask.client_ref else None
    try:
        result, order = await execute_signal_for_user(
            session, user, mode=mode, strategy_id=STRATEGY_ID, signal=_signal(contract, ask.side, fill, stop, now, view["fill_note"] or ""),
            idempotency_key=key, holding="SWING" if ask.product == "NRML" else "INTRADAY", max_quantity=float(view["units"]),
            journal_source="MANUAL", manual_contract_spec=_spec(contract), manual_contract_meta=_meta(contract, ask.side))
    except Exception as exc:  # noqa: BLE001 - shown to the person; the order trail holds what the pipeline recorded
        logger.exception("FNO ticket order failed for user %s", user.id)
        await session.rollback()
        return {**view, "status": "FAILED", "reasons": [f"Not placed: {type(exc).__name__}"], "order_id": None, "trade_id": None, "filled": None}
    filled = None
    if result.executed and result.trade is not None:
        filled = {"price": float(result.trade.entry_price), "quantity": float(result.trade.quantity),
                  "lots": round(float(result.trade.quantity) / contract.lot_size, 4)}
    reasons = [plain(r) for r in (result.reasons or [])]
    return {**view, "status": "PLACED" if result.executed else "REFUSED", "reasons": reasons if not result.executed else [],
            "order_id": order.id, "trade_id": order.trade_id, "filled": filled, "mode": order.mode}


async def held(session: AsyncSession, user: User, underlying: str, expiry: date) -> List[Dict[str, Any]]:
    """The caller's own open PAPER option positions on this underlying and expiry, by strike and right (the chain's "P"
    marker). Tenant and user scoped: another member's positions never show."""
    name = master.underlying_of(underlying)
    trades = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.mode == MODE,
        TradeRecord.exit_time.is_(None), TradeRecord.instrument_kind == "OPTION", TradeRecord.expiry == expiry)))
    if not trades:
        return []
    symbols = sorted({t.symbol.upper() for t in trades})
    rows = list(await session.scalars(select(InstrumentRecord).where(
        InstrumentRecord.tradingsymbol.in_(symbols), InstrumentRecord.underlying == name, InstrumentRecord.expiry == expiry,
        InstrumentRecord.instrument_type.in_(RIGHTS))))
    by_symbol = {r.tradingsymbol.upper(): r for r in rows}
    out = []
    for t in trades:
        row = by_symbol.get(t.symbol.upper())
        if row is None or row.strike is None:
            continue
        lot = int(t.lot_size or row.lot_size or 1)
        sign = 1.0 if t.direction == "LONG" else -1.0
        pnl = round(sign * (float(t.mark_price) - float(t.entry_price)) * float(t.quantity), 2) if t.mark_price is not None else None
        out.append({"trade_id": t.id, "tradingsymbol": t.symbol, "strike": float(row.strike), "right": row.instrument_type,
                    "side": "BUY" if t.direction == "LONG" else "SELL", "quantity": float(t.quantity), "lots": round(float(t.quantity) / lot, 4),
                    "lot_size": lot, "entry_price": float(t.entry_price), "mark_price": t.mark_price, "pnl": pnl,
                    "stop_loss": float(t.stop_loss) if t.stop_loss is not None else None})
    return out
