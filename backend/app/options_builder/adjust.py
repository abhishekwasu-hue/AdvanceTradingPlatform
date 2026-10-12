"""N2-7 (P1 spec 3.2, docs/design/N2_P1FGH.md): builder adjustments - pure functions over the builder's legs.

Each adjustment returns the legs after it and the changes it makes. Models only: nothing here places, stages or
proposes an order. In Phase 0, acting on an adjustment means building the new legs again in the PAPER ticket / basket
flow.

| Kind | What it does |
|---|---|
| `roll` | closes one leg and opens the same leg `steps` strikes up (+) or down (−), and/or at another expiry |
| `add_hedge` | buys the same option `width_steps` strikes further out of the money for one sold leg (CE above, PE below), same lots and expiry |
| `iron_condor` | for sold calls and puts of one expiry with no bought wing (a short strangle or straddle), buys a call above the highest sold call and a put below the lowest sold put, `width_steps` away |
| `close_side` | closes every CE or every PE leg |

**Prices:**
- A new leg is priced by the model (Black-Scholes) now, at an IV the caller gives or else the IV of the leg it comes
  from.
- A closed leg is valued at the model now, at its own IV.
- `realised` is the P&L a close locks in against that leg's entry premium.
- `cash` is the premium the adjustment pays (−) or receives (+) today.

**Before and after** are then evaluated the same way:
- the after P&L is the remaining legs (entry premiums), plus the new legs (model premiums), plus `realised`, so both
  lines are the P&L since the original entry;
- each side has the curve at expiry (one expiry) or on the nearest expiry (calendars), max profit/loss, breakevens,
  PoP and the net Greeks now.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from app.options_builder import model
from app.options_builder.payoff import compute_strategy_payoff_curve

When = Union[date, datetime]
KINDS = ("roll", "add_hedge", "iron_condor", "close_side")


class AdjustError(ValueError):
    pass


def _sign(leg: Dict[str, Any]) -> float:
    return (1.0 if leg["direction"] == "BUY" else -1.0) * float(leg["lots"]) * float(leg["lot_size"])


def _price_now(leg: Dict[str, Any], spot: float, as_of: When, r: float) -> float:
    return round(model.leg_theoretical(leg, spot, as_of, r=r), 2)


def _new_leg(template: Dict[str, Any], *, direction: str, strike: float, expiry: Optional[str] = None, iv: Optional[float] = None,
             spot: float, as_of: When, r: float) -> Dict[str, Any]:
    if strike <= 0:
        raise AdjustError(f"the new strike {strike:g} is not above 0")
    leg = {"direction": direction, "option_type": template["option_type"], "strike": float(strike), "lots": template["lots"],
           "lot_size": template["lot_size"], "expiry": expiry or template["expiry"], "iv": float(iv if iv is not None else template["iv"]),
           "iv_source": "given" if iv is not None else "from the leg it replaces"}
    leg["premium"] = _price_now(leg, spot, as_of, r)
    return leg


def _close(leg: Dict[str, Any], spot: float, as_of: When, r: float) -> Tuple[Dict[str, Any], float, float]:
    """(change record, realised P&L, cash today) for closing `leg` at the model's price now."""
    price = _price_now(leg, spot, as_of, r)
    q = _sign(leg)
    realised = (price - float(leg["premium"])) * q
    cash = price * q                                       # selling a bought leg brings its value in; buying back a sold one pays it
    return ({"action": "close", "leg": _public(leg), "price": price, "realised": round(realised, 2)}, realised, cash)


def _open(leg: Dict[str, Any]) -> Tuple[Dict[str, Any], float]:
    return {"action": "open", "leg": _public(leg), "price": leg["premium"]}, -float(leg["premium"]) * _sign(leg)


def _public(leg: Dict[str, Any]) -> Dict[str, Any]:
    return {k: leg.get(k) for k in ("direction", "option_type", "strike", "premium", "lots", "lot_size", "expiry", "iv")}


def _options(legs: Sequence[Dict[str, Any]]) -> None:
    for leg in legs:
        if leg["option_type"] not in ("CE", "PE"):
            raise AdjustError("adjustments work on option legs; a FUT leg is left as it is - remove it first")


def adjust(legs: List[Dict[str, Any]], kind: str, *, spot: float, as_of: When, strike_step: float, r: float,
           index: Optional[int] = None, steps: int = 0, expiry: Optional[str] = None, width_steps: int = 2,
           side: Optional[str] = None, iv: Optional[float] = None) -> Dict[str, Any]:
    """{after: legs, changes, realised, cash, description} for one adjustment of `legs` (each with an `iv`)."""
    if kind not in KINDS:
        raise AdjustError(f"unknown adjustment {kind!r} - one of {', '.join(KINDS)}")
    if not strike_step > 0:
        raise AdjustError("the strike step must be above 0")
    for leg in legs:                                        # review: lots are whole numbers (the API's LegIn says so too)
        lots = leg.get("lots")
        if isinstance(lots, bool) or not isinstance(lots, (int, float)) or not float(lots).is_integer() or lots < 1:
            raise AdjustError(f"lots must be a whole number of at least 1 (got {lots!r})")
    changes: List[Dict[str, Any]] = []
    realised = cash = 0.0
    after = [{**leg, "lots": int(leg["lots"])} for leg in legs]

    def pick(i: Optional[int]) -> Dict[str, Any]:
        if i is None or not 0 <= i < len(after):
            raise AdjustError("choose a leg (its position in the list)")
        return after[i]

    if kind == "roll":
        leg = pick(index)
        _options([leg])
        if steps == 0 and not expiry:
            raise AdjustError("a roll needs strike steps (up +, down −) or another expiry")
        if expiry and expiry < str(leg["expiry"]):
            raise AdjustError("a roll moves out to the same or a later expiry")
        new = _new_leg(leg, direction=leg["direction"], strike=float(leg["strike"]) + steps * strike_step, expiry=expiry, iv=iv,
                       spot=spot, as_of=as_of, r=r)
        rec, rz, c = _close(leg, spot, as_of, r)
        orec, oc = _open(new)
        changes += [rec, orec]
        realised, cash = rz, c + oc
        after[index] = new                                  # type: ignore[index]
        moved = (f"{abs(steps)} strike{'s' if abs(steps) != 1 else ''} {'up' if steps > 0 else 'down'}" if steps else "")
        description = f"Roll {leg['direction'].lower()} {leg['option_type']} {float(leg['strike']):g} " + \
            " and ".join(p for p in (moved, f"out to {expiry}" if expiry and expiry != leg["expiry"] else "") if p)
    elif kind == "add_hedge":
        leg = pick(index)
        _options([leg])
        if leg["direction"] != "SELL":
            raise AdjustError("a hedge is added to a sold leg")
        if width_steps < 1:
            raise AdjustError("the hedge is at least one strike away")
        offset = width_steps * strike_step * (1 if leg["option_type"] == "CE" else -1)
        new = _new_leg(leg, direction="BUY", strike=float(leg["strike"]) + offset, iv=iv, spot=spot, as_of=as_of, r=r)
        orec, cash = _open(new)
        changes.append(orec)
        after.append(new)
        description = f"Buy {new['option_type']} {new['strike']:g} to hedge the sold {leg['option_type']} {float(leg['strike']):g}"
    elif kind == "iron_condor":
        _options(after)
        expiries = {leg["expiry"] for leg in after}
        sold_ce = [leg for leg in after if leg["direction"] == "SELL" and leg["option_type"] == "CE"]
        sold_pe = [leg for leg in after if leg["direction"] == "SELL" and leg["option_type"] == "PE"]
        bought = [leg for leg in after if leg["direction"] == "BUY"]
        if len(expiries) != 1 or not sold_ce or not sold_pe or bought:
            raise AdjustError("an iron condor is made from sold calls and sold puts of one expiry with no bought leg yet "
                              "(a short strangle or straddle)")
        if width_steps < 1:
            raise AdjustError("the wings are at least one strike away")
        top = max(sold_ce, key=lambda leg: float(leg["strike"]))
        bottom = min(sold_pe, key=lambda leg: float(leg["strike"]))
        lots_ce = sum(leg["lots"] for leg in sold_ce)        # validated whole numbers above
        lots_pe = sum(leg["lots"] for leg in sold_pe)
        wing_ce = _new_leg({**top, "lots": lots_ce}, direction="BUY", strike=float(top["strike"]) + width_steps * strike_step, iv=iv,
                           spot=spot, as_of=as_of, r=r)
        wing_pe = _new_leg({**bottom, "lots": lots_pe}, direction="BUY", strike=float(bottom["strike"]) - width_steps * strike_step, iv=iv,
                           spot=spot, as_of=as_of, r=r)
        for new in (wing_ce, wing_pe):
            orec, oc = _open(new)
            changes.append(orec)
            cash += oc
            after.append(new)
        description = f"Buy wings at CE {wing_ce['strike']:g} and PE {wing_pe['strike']:g}: the short strangle becomes an iron condor"
    else:                                                   # close_side
        if side not in ("CE", "PE"):
            raise AdjustError("close_side needs side CE or PE")
        closing = [leg for leg in after if leg["option_type"] == side]
        if not closing:
            raise AdjustError(f"there is no {side} leg to close")
        if len(closing) == len(after):
            raise AdjustError(f"every leg is a {side}: closing that side closes the whole strategy")
        for leg in closing:
            rec, rz, c = _close(leg, spot, as_of, r)
            changes.append(rec)
            realised += rz
            cash += c
        after = [leg for leg in after if leg["option_type"] != side]
        description = f"Close the {side} side ({len(closing)} leg{'s' if len(closing) != 1 else ''})"
    return {"after": after, "changes": changes, "realised": round(realised, 2), "cash": round(cash, 2), "description": description}


def _finite(x: Optional[float]) -> Optional[float]:
    return None if x is None or not math.isfinite(x) else round(float(x), 2)


def side_view(legs: Sequence[Dict[str, Any]], prices: Sequence[float], *, spot: float, as_of: When, r: float, shift: float = 0.0) -> Dict[str, Any]:
    """One side of the comparison: the curve at expiry (or on the nearest expiry for calendars), plus `shift` (P&L already
    locked in), extremes, breakevens, PoP and net Greeks now."""
    single = len(model.expiries(legs)) == 1
    if single:
        curve = [v + shift for v in compute_strategy_payoff_curve(legs, prices)]
        ext = model.payoff_extremes(legs)
        intervals = model.profitable_intervals(legs) if abs(shift) < 1e-9 else None
        extremes = {"max_profit": _finite(ext["max_profit"] + shift if ext["max_profit"] is not None else None),
                    "max_loss": _finite(ext["max_loss"] + shift if ext["max_loss"] is not None else None),   # a loss is negative
                    "unbounded_profit": ext["unbounded_profit"], "unbounded_loss": ext["unbounded_loss"]}
    else:
        near = model.expiries(legs)[0]
        curve = [v + shift for v in model.pnl_on(legs, prices, near)]
        extremes, intervals = None, None
    if intervals is None:                                   # breakevens of the shifted curve: where it crosses zero
        breakevens = [round(prices[i] + (prices[i + 1] - prices[i]) * (0 - curve[i]) / (curve[i + 1] - curve[i]), 2)
                      for i in range(len(curve) - 1) if (curve[i] < 0) != (curve[i + 1] < 0) and curve[i + 1] != curve[i]]
    else:
        breakevens = sorted({round(x, 2) for a, b in intervals for x in (a, b) if 0 < x < math.inf})
    try:
        summary = model.summary(list(legs), spot, as_of=as_of, r=r)
        pop = summary.get("pop")
    except ValueError:
        pop = None
    greeks = {k: round(v, 4) for k, v in model.net_greeks(list(legs), spot, as_of, r=r).items()}
    return {"legs": [_public(leg) for leg in legs], "at_expiry": [round(v, 2) for v in curve], "single_expiry": single, "extremes": extremes,
            "breakevens": breakevens, "pop": None if pop is None else round(float(pop), 4),
            "pop_basis": "the legs held, at their own entry premiums (P&L locked in by a close is not in it)", "greeks": greeks,
            "net_premium": round(-sum(float(leg["premium"]) * _sign(leg) for leg in legs), 2)}


def compare(before: List[Dict[str, Any]], result: Dict[str, Any], prices: Sequence[float], *, spot: float, as_of: When, r: float) -> Dict[str, Any]:
    after = result["after"]
    b = side_view(before, prices, spot=spot, as_of=as_of, r=r)
    a = side_view(after, prices, spot=spot, as_of=as_of, r=r, shift=result["realised"])
    delta = {k: round(a["greeks"][k] - b["greeks"][k], 4) for k in b["greeks"]}
    return {"before": b, "after": a, "greeks_change": delta}
