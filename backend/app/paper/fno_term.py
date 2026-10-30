"""TERM-2 step 4 (owner order docs/owner-orders/ATP_ORDER_TERM2.md, "Beyond the best"): the server side of R1, R2 and R4,
each behind its own flag (OFF by default, PAPER first). Read only: nothing here places, changes, blocks or proposes an
order; no order path imports this module, and the AI never calls it (ADR-0006).

* **R1 Roll** (`term_roll`) - `roll_preview`: for the caller's own open PAPER F&O legs, the exit of each leg (at its live
  price, else its last marked price) and the same strike and right on the NEXT listed expiry after it (the instrument
  master's own list, `fno_contracts.roll_target`; never guessed), same side and lots, with the new legs' numbers from the
  ONE basket calculation (`fno_basket.evaluate`) and the roll's net premium. The exit then runs through the existing Exit
  now / group exit (never blocked, ADR-0004) and the new legs go into the one basket for the user to review and place:
  two confirmed steps, said so in `steps` - the roll is not atomic. A sold leg's stop is left blank (DEF-0: the user's own).
* **R2 What-if** (`term_whatif`) - `whatif`: the combined payoff of the caller's own open PAPER F&O legs on the basket's
  underlying and expiries plus the basket's new legs, from the same engine in one call. The open legs are valued at their
  entry price, so the combined curve is the book's own result at expiry.
* **R4 Margin pair** (`term_margin_pair`) - `margin_pair`: required (the broker's basket margin through the user's OWN
  session where the adapter has the call, else ATP's labelled estimate - `smart_execution.margin_view`, the confirm step's
  own rule) and available (the Terminal status bar's funds, `terminal.status.funds_of`).
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TradeRecord, User
from app.instruments import master
from app.paper import fno_basket, fno_ticket

logger = logging.getLogger(__name__)

ROLL_FLAG = "term_roll"
WHATIF_FLAG = "term_whatif"
STRIKE_AREA_FLAG = "term_strike_area"
MARGIN_PAIR_FLAG = "term_margin_pair"
FLAGS = (ROLL_FLAG, WHATIF_FLAG, STRIKE_AREA_FLAG, MARGIN_PAIR_FLAG)
PAGE_FLAGS = ("ui_v2", "terminal")
MAX_ROLL_LEGS = fno_basket.MAX_LEGS

ROLL_STEPS = ("Step 1 closes these legs through Exit now. Step 2 puts the new legs in your basket: you review and place them. "
              "Between the two steps these strikes are not held.")


async def enabled(session: AsyncSession, tenant_id: int, flag: str) -> bool:
    """The flag and the Terminal's own flags (ui_v2 + terminal) are all on for the organisation."""
    from app.platform.controls import flag_enabled
    for name in (*PAGE_FLAGS, flag):
        if not await flag_enabled(session, name, tenant_id):
            return False
    return True


def _side(trade: TradeRecord) -> str:
    return "SELL" if (trade.direction or "").upper() in ("SHORT", "SELL") else "BUY"


def _lots(trade: TradeRecord, lot_size: int | None) -> int:
    unit = float(lot_size or trade.lot_size or 1)
    return max(1, int(round(float(trade.quantity) / unit)))


async def _facts(session: AsyncSession, trades: list[TradeRecord]) -> dict[int, dict[str, Any]]:
    """Each trade's contract as the instrument master (or its own symbol) says: only known F&O contracts."""
    from app.paper import fno_contracts
    if not trades:
        return {}
    rows = await fno_contracts.lookup(session, [((t.exchange or "NFO"), t.symbol) for t in trades])
    out: dict[int, dict[str, Any]] = {}
    for t, row in zip(trades, rows):
        if row.get("known") and row.get("expiry") and row.get("right") in ("CE", "PE", "FUT"):
            out[t.id] = row
    return out


async def _open_fno(session: AsyncSession, user: User) -> list[TradeRecord]:
    return list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.mode == fno_ticket.MODE,
        TradeRecord.exit_time.is_(None), TradeRecord.instrument_kind != "UNDERLYING").order_by(TradeRecord.id).limit(200)))


# --- R2 what-if ----------------------------------------------------------------------------------------------------------

async def open_legs(session: AsyncSession, user: User, underlying: str, expiries: set[date]) -> list[tuple[TradeRecord, fno_basket.LegAsk, str]]:
    """The caller's own open PAPER F&O legs on `underlying` and one of `expiries`, as basket legs at their entry price."""
    name = master.underlying_of(underlying)
    trades = [t for t in await _open_fno(session, user) if t.expiry is None or t.expiry in expiries]
    facts = await _facts(session, trades)
    out: list[tuple[TradeRecord, fno_basket.LegAsk, str]] = []
    for t in trades:
        f = facts.get(t.id)
        if f is None or master.underlying_of(str(f["underlying"])) != name:
            continue
        expiry = date.fromisoformat(str(f["expiry"]))
        if expiry not in expiries or not t.entry_price or float(t.entry_price) <= 0:
            continue
        strike = None if f["right"] == fno_ticket.FUTURE else f.get("strike")
        leg = fno_basket.LegAsk(underlying=name, expiry=expiry, strike=strike, right=str(f["right"]), side=_side(t),
                                lots=_lots(t, f.get("lot_size")), price=float(t.entry_price))
        out.append((t, leg, str(f.get("label") or t.symbol)))
    return out


async def whatif(session: AsyncSession, user: User, legs: list[fno_basket.LegAsk], spot: float) -> dict[str, Any]:
    """The combined numbers of the open legs (same underlying, same expiries) plus the basket's new legs - one call of
    the basket's own engine. {"ok": False, "open": []} when nothing is open there (the basket's own payoff is the answer)."""
    if not legs:
        return {"ok": False, "open": [], "errors": []}
    held = await open_legs(session, user, legs[0].underlying, {leg.expiry for leg in legs})
    if not held:
        return {"ok": False, "open": [], "errors": []}
    combined = [leg for _, leg, _ in held] + list(legs)
    if len(combined) > fno_basket.MAX_LEGS * 2:
        return {"ok": False, "open": [], "errors": ["Too many legs together for one payoff."]}
    out = await fno_basket.evaluate(session, combined, spot)
    opened = [{"trade_id": t.id, "label": label, "side": leg.side, "lots": leg.lots, "price": leg.price} for t, leg, label in held]
    if not out.get("ok"):
        return {"ok": False, "open": opened, "errors": list(out.get("errors") or [])}
    sizes = out.get("lot_sizes") or []
    out["open"] = opened
    # The open legs as the payoff chart draws them (the basket's own legs follow, in the basket's order).
    out["open_legs"] = [{"id": f"open-{t.id}", "direction": leg.side, "option_type": leg.right,
                         "strike": float(leg.price or 0) if leg.right == fno_ticket.FUTURE else float(leg.strike or 0),
                         "premium": float(leg.price or 0), "lots": leg.lots, "lot_size": int(sizes[i] or 1) if i < len(sizes) else 1,
                         "expiry": leg.expiry.isoformat(), "iv": None} for i, (t, leg, _) in enumerate(held)]
    for key in ("order", "labels", "lot_sizes"):
        out.pop(key, None)                   # the payoff only: the basket's own figures (and its margin pair) stay the basket's
    return out


# --- R4 margin pair ------------------------------------------------------------------------------------------------------

async def margin_pair(session: AsyncSession, user: User, legs: list[fno_basket.LegAsk], numbers: dict[str, Any], product: str) -> dict[str, Any]:
    """Required (the broker's basket margin through his own session where the adapter has it, else ATP's labelled
    estimate) and available (the Terminal status funds), with what is left. Reads only; a failing broker read falls
    back to the estimate and says so."""
    from app.brokers.models import BrokerOrderRequest
    from app.core.enums import OrderSide
    from app.execution import smart_execution
    from app.terminal.status import funds_of
    estimate = ((numbers.get("margin") or {}).get("hedge")) or {}
    broker_margin: dict[str, Any] | None = None
    broker_name: str | None = None
    broker = await fno_basket.own_broker(session, user)
    if broker is not None:
        orders = []
        for leg in legs:
            try:
                c = await fno_ticket.find_contract(session, leg.underlying, leg.expiry, leg.strike, leg.right)
            except fno_ticket.ContractNotFound:
                orders = []
                break
            orders.append(BrokerOrderRequest(symbol=c.tradingsymbol, exchange=c.exchange,
                                             transaction_type=OrderSide.BUY if leg.side == "BUY" else OrderSide.SELL,
                                             quantity=float(leg.lots * c.lot_size), product=product))
        if orders:
            broker_name = getattr(broker, "name", None)
            broker_margin = await smart_execution.broker_basket_margin(broker, orders)
    funds = (await funds_of(session, user))["funds"]
    view = smart_execution.margin_view(broker_margin, estimate, funds["available"], free_source="practice", broker_name=broker_name)
    out = view.as_dict()
    out["available"] = funds["available"]
    out["available_note"] = funds["note"]
    out["estimate"] = out["source"] != "broker"
    return out


# --- R1 roll -------------------------------------------------------------------------------------------------------------

async def _price(lookup: fno_ticket.PriceLookup | None, symbol: str, exchange: str) -> float | None:
    if lookup is None:
        return None
    try:
        value = float(await lookup(symbol, exchange))
    except Exception:  # noqa: BLE001 - no live price: the preview says so, nothing is made up
        return None
    return value if value > 0 else None


async def _spot(lookup: fno_ticket.PriceLookup | None, underlying: str) -> float | None:
    from app.option_chain.chain_view import spot_instrument
    try:
        symbol, exchange = spot_instrument(underlying)
    except ValueError:
        return None
    return await _price(lookup, symbol, exchange)


async def roll_preview(session: AsyncSession, user: User, trade_ids: list[int], *, price_lookup: fno_ticket.PriceLookup | None,
                       spot: float | None = None, now: datetime | None = None) -> dict[str, Any]:
    """The roll of the caller's own open PAPER F&O legs to the next listed expiry: what closes, what opens, the new legs'
    numbers and the roll's net premium. Reads only."""
    from fastapi import HTTPException

    from app.execution import approval
    from app.paper import fno_contracts
    now = now or datetime.now(UTC)
    trades: list[TradeRecord] = []
    for trade_id in dict.fromkeys(trade_ids):
        trade = await session.get(TradeRecord, trade_id)
        if trade is None or trade.tenant_id != user.tenant_id:
            raise HTTPException(status_code=404, detail="Unknown position")
        if not approval.may_act_on_trade(user, trade):
            raise HTTPException(status_code=403, detail="This position belongs to another member; only they or the organisation owner can roll it")
        trades.append(trade)
    errors: list[str] = []
    for t in trades:
        if t.exit_time is not None:
            errors.append(f"{t.symbol} is already closed.")
        elif t.mode != fno_ticket.MODE:
            errors.append(f"{t.symbol} is a live position: roll it in your broker's app.")
    facts = await _facts(session, [t for t in trades if t.exit_time is None and t.mode == fno_ticket.MODE])
    exits: list[dict[str, Any]] = []
    new_legs: list[fno_basket.LegAsk] = []
    labels: list[str] = []
    expiries: set[str] = set()
    underlyings: set[str] = set()
    for t in trades:
        f = facts.get(t.id)
        if t.exit_time is not None or t.mode != fno_ticket.MODE:
            continue
        if f is None:
            errors.append(f"{t.symbol} is not an option or future ATP can read.")
            continue
        underlying = master.underlying_of(str(f["underlying"]))
        underlyings.add(underlying)
        expiry = date.fromisoformat(str(f["expiry"]))
        strike = None if f["right"] == fno_ticket.FUTURE else f.get("strike")
        lots = _lots(t, f.get("lot_size"))
        last = await _price(price_lookup, t.symbol, t.exchange or "NFO")
        exits.append({"trade_id": t.id, "label": f.get("label") or t.symbol, "side": _side(t), "lots": lots,
                      "units": float(t.quantity), "price": last if last is not None else (float(t.mark_price) if t.mark_price else None),
                      "price_source": "live" if last is not None else ("last" if t.mark_price else None)})
        target = await fno_contracts.roll_target(session, underlying, str(f["right"]), strike, after=expiry, monthly=False)
        if target is None or target.expiry is None:
            errors.append(f"The next expiry of {f.get('label') or t.symbol} is not in the instrument list yet. It updates every morning.")
            continue
        price = await _price(price_lookup, target.tradingsymbol, target.exchange or master.derivatives_exchange(underlying))
        leg = fno_basket.LegAsk(underlying=underlying, expiry=target.expiry, strike=strike, right=str(f["right"]), side=_side(t),
                                lots=lots, price=price, stop_loss=None)
        new_legs.append(leg)
        labels.append(fno_ticket.contract_label(underlying, target.expiry, strike, leg.right))
        expiries.add(target.expiry.isoformat())
    if len(underlyings) > 1:
        errors.append("These legs are on more than one underlying: roll each one on its own.")
    legs_out = [{"underlying": leg.underlying, "expiry": leg.expiry.isoformat(), "strike": leg.strike, "right": leg.right, "side": leg.side,
                 "lots": leg.lots, "price": leg.price, "stop_loss": None, "label": labels[i]} for i, leg in enumerate(new_legs)]
    numbers: dict[str, Any] | None = None
    numbers_errors: list[str] = []
    underlying = next(iter(underlyings), None)
    spot_now = (await _spot(price_lookup, underlying) if underlying else None) or spot
    if new_legs and not errors:
        if spot_now:
            out = await fno_basket.evaluate(session, new_legs, spot_now, as_of=now)
            if out.get("ok"):
                numbers = {"net_premium": out["net_premium"], "extremes": out["extremes"], "breakevens": out["breakevens"],
                           "margin": out["margin"], "lot_sizes": out["lot_sizes"]}
            else:
                numbers_errors = list(out.get("errors") or [])
        else:
            numbers_errors = ["The underlying's price is not known just now: the new legs' numbers show in the basket."]
    exit_value: float | None = None
    if exits and all(e["price"] is not None for e in exits):
        # Closing a sold leg pays its price; closing a bought leg receives it.
        exit_value = round(sum((e["price"] or 0.0) * e["units"] * (-1 if e["side"] == "SELL" else 1) for e in exits), 2)
    net = None
    if exit_value is not None and numbers is not None:
        net = round(exit_value + float(numbers["net_premium"]), 2)
    return {"mode": fno_ticket.MODE, "ok": not errors and bool(new_legs), "errors": errors, "exit": exits, "legs": legs_out,
            "expiry": min(expiries) if expiries else None, "underlying": underlying, "spot": spot_now,
            "numbers": numbers, "numbers_errors": numbers_errors, "exit_value": exit_value, "net": net,
            "atomic": False, "steps": ROLL_STEPS, "as_of": now.isoformat()}


__all__ = ["FLAGS", "ROLL_FLAG", "WHATIF_FLAG", "STRIKE_AREA_FLAG", "MARGIN_PAIR_FLAG", "enabled", "open_legs", "whatif", "margin_pair",
           "roll_preview", "ROLL_STEPS"]
