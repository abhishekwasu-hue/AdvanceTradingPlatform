"""Z5 / P1-c: the money lines of the builder's metrics card and the basket the page shows before "Place as basket".

Pure functions over the builder's legs (direction, option_type, strike, premium, lots, lot_size, expiry) and the exact
extremes of `model.payoff_extremes`, so the page draws numbers from the engine and computes none itself:

- `net_premium`: option premium received (positive) or paid (negative), in money; a FUT leg's "premium" is its entry
  price, not money changing hands, so it is left out.
- `reward_to_risk`: max profit over max loss, only when both are finite and there is a loss to risk.
- `margin_estimate`: an ESTIMATE, labelled so. A defined-risk structure needs at least its max loss (long-only: the
  premium paid). Undefined risk and calendars need the broker's SPAN + exposure number, which arrives with the order
  ticket (P1-d); the builder never shows a made-up figure for them.
- `costs`: the statutory charges and brokerage to open the strategy (and to close it at today's premiums), from the
  platform's one table of Indian charges by date (`app.execution.india_costs`, pure: rates only, no order path).
- `basket`: the legs in placement order (hedge first), each with its quantity (lots x lot size) and builder key. It
  names contracts for review; it is never sent to a broker from here.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.execution.india_costs import leg_cost
from app.options_builder.chain import builder_key
from app.options_builder.templates import hedge_first

Leg = Mapping[str, Any]

MARGIN_NOTE = "Estimate. The broker's SPAN and exposure margin comes with the order ticket."


def _qty(leg: Leg) -> float:
    return float(leg["lots"]) * float(leg["lot_size"])


def net_premium(legs: Sequence[Leg]) -> float:
    """Positive: a net credit received; negative: a net debit paid. Futures legs are left out."""
    return float(sum((1.0 if leg["direction"] == "SELL" else -1.0) * float(leg["premium"]) * _qty(leg)
                     for leg in legs if leg["option_type"] != "FUT"))


def reward_to_risk(max_profit: Optional[float], max_loss: Optional[float]) -> Optional[float]:
    """Max profit per unit of max loss. None when either is unbounded (None or infinite), there is no loss to risk, or
    no price makes a profit."""
    if max_profit is None or max_loss is None or not (math.isfinite(max_profit) and math.isfinite(max_loss)):
        return None
    if max_loss >= 0 or max_profit <= 0:
        return None
    return max_profit / abs(max_loss)


def margin_estimate(legs: Sequence[Leg], extremes: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The margin line of the metrics card: `value` in money or None, `basis` saying how it was made (or why there is
    no estimate), and `estimated: True` always - nothing here is the broker's number."""
    out: Dict[str, Any] = {"value": None, "estimated": True, "note": MARGIN_NOTE}
    if extremes is None:
        out["basis"] = "Legs expire on different dates: needs the broker's margin."
        return out
    if extremes.get("unbounded_loss"):
        out["basis"] = "Undefined risk: needs the broker's margin."
        return out
    if any(leg["option_type"] == "FUT" for leg in legs):
        out["basis"] = "Includes a futures leg: needs the broker's margin."
        return out
    max_loss = extremes.get("max_loss")
    if max_loss is None or not math.isfinite(max_loss):
        out["basis"] = "No finite max loss: needs the broker's margin."
        return out
    if all(leg["direction"] == "BUY" for leg in legs):
        out["value"] = round(-net_premium(legs), 2)
        out["basis"] = "Long options only: the premium paid."
    else:
        out["value"] = round(max(0.0, -float(max_loss)), 2)
        out["basis"] = "Defined risk: at least the max loss."
    return out


def costs(legs: Sequence[Leg], day: date, *, brokerage_per_order: float) -> Dict[str, Any]:
    """Charges to open every leg at its premium on `day` (one order per leg), and to open and close at today's premiums
    (each leg closed by the opposite order at the same price - an estimate; an option left to expire has no closing
    order). Every component is summed across legs."""
    keys = ("stt", "exch", "sebi", "stamp", "brokerage", "tax", "total")
    open_sum = dict.fromkeys(keys, 0.0)
    close_sum = dict.fromkeys(keys, 0.0)
    for leg in legs:
        segment = "FUTURE" if leg["option_type"] == "FUT" else "OPTION"
        side = "buy" if leg["direction"] == "BUY" else "sell"
        other = "sell" if side == "buy" else "buy"
        a = leg_cost(day, side, float(leg["premium"]), _qty(leg), segment, brokerage_per_order)
        b = leg_cost(day, other, float(leg["premium"]), _qty(leg), segment, brokerage_per_order)
        for k in keys:
            open_sum[k] += a[k]
            close_sum[k] += b[k]
    return {"to_open": {k: round(v, 2) for k, v in open_sum.items()},
            "round_trip": round(open_sum["total"] + close_sum["total"], 2),
            "brokerage_per_order": brokerage_per_order, "orders": len(legs), "as_of": day.isoformat(),
            "note": "Estimate: statutory charges as on the day plus the brokerage per order you set; slippage not included."}


def basket(underlying: str, legs: Sequence[Leg]) -> List[Dict[str, Any]]:
    """The legs in placement order (every BUY before every SELL, order kept within each), numbered from 1, each with
    its quantity and builder key. `leg` is the leg's position in the request, so the page can match rows."""
    indexed = [{**leg, "_i": i} for i, leg in enumerate(legs)]
    out: List[Dict[str, Any]] = []
    for seq, leg in enumerate(hedge_first(indexed), start=1):
        kind = leg["option_type"]
        key = (f"{underlying}|{leg['expiry']}|FUT" if kind == "FUT"
               else builder_key(underlying, str(leg["expiry"]), float(leg["strike"]), kind))
        out.append({"seq": seq, "leg": leg["_i"], "side": leg["direction"], "option_type": kind,
                    "strike": None if kind == "FUT" else float(leg["strike"]), "expiry": str(leg["expiry"]),
                    "lots": int(leg["lots"]), "lot_size": int(leg["lot_size"]), "quantity": int(_qty(leg)),
                    "price": float(leg["premium"]), "key": key})
    return out
