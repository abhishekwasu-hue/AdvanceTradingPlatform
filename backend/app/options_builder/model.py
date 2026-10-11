"""P1-b: the strategy before expiry - T+0 / any-date value curves, IV and time scenarios, probability of profit, expected
move and probability-weighted P&L, all from the platform's Black-Scholes (app/option_chain/greeks.py).

A leg here is the builder's leg (direction, option_type, strike, premium, lots, lot_size) plus `expiry` (ISO date) and
`iv` (annualised, e.g. 0.14 - the leg's own, from the chain or solved from its premium; never invented).

- `value_curve`: the strategy's P&L at every price on a chosen date (`days_forward` from `as_of`) with every leg's IV
  shifted by `iv_shift` (absolute, 0.02 = +2 vol points). On or after a leg's expiry the leg is worth its intrinsic
  value, so a curve on the last expiry equals the expiry payoff.
- `pop_at_expiry`: the probability the expiry payoff is above zero, under a lognormal price with one volatility. The
  expiry payoff is piecewise linear in the price, so the profitable intervals are found exactly between its kinks (no
  grid) and their probability summed.
- `expected_pnl_at_expiry`: the probability-weighted expiry P&L under the same distribution, in closed form.
- `expected_move`: the one-standard-deviation move, spot x IV x sqrt(t).

These are models: the UI labels them as estimates, never as a forecast or a recommendation.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.core.config import RISK_FREE_RATE
from app.option_chain.greeks import BSInputs, black_scholes, time_to_expiry_years
from app.option_chain.models import OptionType
from app.options_builder.payoff import compute_leg_payoff

Leg = Mapping[str, Any]


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _sign_qty(leg: Leg) -> float:
    return (1.0 if leg["direction"] == "BUY" else -1.0) * float(leg["lots"]) * float(leg["lot_size"])


def _expiry(leg: Leg) -> date:
    e = leg.get("expiry")
    if not e:
        raise ValueError(f"leg {leg.get('option_type')} {leg.get('strike')} has no expiry - the model needs one")
    return e if isinstance(e, date) else date.fromisoformat(str(e)[:10])


def _iv(leg: Leg, iv_shift: float) -> float:
    iv = leg.get("iv")
    if iv is None:
        raise ValueError(f"leg {leg.get('option_type')} {leg.get('strike')} has no IV - take it from the chain or solve it from the premium")
    return max(1e-4, float(iv) + iv_shift)


def leg_theoretical(leg: Leg, spot: float, on: date, *, iv_shift: float = 0.0, r: float = RISK_FREE_RATE) -> float:
    """One unit of the leg's option, valued on date `on` (intrinsic on or after its expiry). An underlying leg (FUT) is
    worth the spot (no carry modelled)."""
    if leg["option_type"] == "FUT":
        return spot
    k = float(leg["strike"])
    if on >= _expiry(leg):
        return max(spot - k, 0.0) if leg["option_type"] == "CE" else max(k - spot, 0.0)
    t = time_to_expiry_years(_expiry(leg), on)
    ot = OptionType.CALL if leg["option_type"] == "CE" else OptionType.PUT
    return black_scholes(BSInputs(spot, k, t, r, _iv(leg, iv_shift), ot)).theoretical_price


def value_curve(legs: Sequence[Leg], price_range: Sequence[float], *, as_of: date, days_forward: float = 0.0,
                iv_shift: float = 0.0, r: float = RISK_FREE_RATE) -> List[float]:
    """The strategy's P&L at every price on `as_of + days_forward` (whole days), with IVs shifted by `iv_shift`."""
    on = as_of + timedelta(days=int(round(days_forward)))
    return [sum((leg_theoretical(leg, p, on, iv_shift=iv_shift, r=r) - float(leg["premium"])) * _sign_qty(leg) for leg in legs)
            for p in price_range]


def expected_move(spot: float, iv: float, t_years: float) -> float:
    """The one-standard-deviation move by `t_years` at volatility `iv` (spot x iv x sqrt(t))."""
    return spot * iv * math.sqrt(max(t_years, 0.0))


def _lognormal_cdf(x: float, spot: float, sigma: float, t: float, r: float) -> float:
    """P(S_T <= x) for a risk-neutral lognormal price."""
    if x <= 0:
        return 0.0
    if math.isinf(x):
        return 1.0
    s = sigma * math.sqrt(t)
    return _ncdf((math.log(x / spot) - (r - 0.5 * sigma * sigma) * t) / s)


def _payoff(legs: Sequence[Leg], price: float) -> float:
    return sum(compute_leg_payoff(price, **{k: leg[k] for k in ("direction", "option_type", "strike", "premium", "lots", "lot_size")}) for leg in legs)


def profitable_intervals(legs: Sequence[Leg]) -> List[Tuple[float, float]]:
    """The price intervals where the expiry payoff is above zero, exactly: the payoff is linear between strikes, so each
    segment (and the two tails, by their slopes) is solved for its zero."""
    kinks = sorted({float(leg["strike"]) for leg in legs if leg["option_type"] != "FUT"})   # an underlying leg is linear
    points = [0.0] + kinks
    out: List[Tuple[float, float]] = []

    def add(a: float, b: float) -> None:
        if b <= a:
            return
        if out and abs(out[-1][1] - a) < 1e-12:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))

    for a, b in zip(points, points[1:]):
        fa, fb = _payoff(legs, a), _payoff(legs, b)
        if fa > 0 and fb > 0:
            add(a, b)
        elif fa > 0 or fb > 0:
            z = a + (b - a) * (0 - fa) / (fb - fa)
            add(a, z) if fa > 0 else add(z, b)
    last = points[-1]
    f_last = _payoff(legs, last)
    slope = _payoff(legs, last + 1.0) - f_last
    if f_last > 0 and slope >= 0:
        add(last, math.inf)
    elif f_last > 0 and slope < 0:
        add(last, last - f_last / slope)
    elif f_last <= 0 and slope > 0:
        add(last - f_last / slope, math.inf)
    return out


def payoff_extremes(legs: Sequence[Leg]) -> Dict[str, Any]:
    """The true best and worst expiry P&L over every price from 0 up (OB-4: the grid-based max loss depends on the grid
    width). The payoff is linear between strikes, so its extremes are at a strike, at a price of 0, or unbounded above
    the last strike when the slope there is not zero. `max_loss` is the lowest P&L (negative for a loss); -inf / inf
    mark undefined risk / reward, and sizing must never use a finite stand-in for them. Works with unequal lots
    (ratios, OB-3)."""
    kinks = sorted({float(leg["strike"]) for leg in legs if leg["option_type"] != "FUT"})
    points = [0.0] + kinks
    values = [_payoff(legs, p) for p in points]
    last = points[-1]
    slope = _payoff(legs, last + 1.0) - _payoff(legs, last)
    up, down = slope > 1e-9, slope < -1e-9
    return {
        "max_profit": math.inf if up else max(values), "max_loss": -math.inf if down else min(values),
        "unbounded_profit": up, "unbounded_loss": down,
        "max_profit_at": None if up else points[values.index(max(values))],
        "max_loss_at": None if down else points[values.index(min(values))],
    }


def pop_at_expiry(legs: Sequence[Leg], spot: float, sigma: float, t_years: float, *, r: float = RISK_FREE_RATE) -> float:
    """Probability (0-1) that the expiry payoff is above zero, for a lognormal price at volatility `sigma`."""
    if sigma <= 0 or t_years <= 0:
        return 1.0 if _payoff(legs, spot) > 0 else 0.0
    return sum(_lognormal_cdf(b, spot, sigma, t_years, r) - _lognormal_cdf(a, spot, sigma, t_years, r) for a, b in profitable_intervals(legs))


def expected_pnl_at_expiry(legs: Sequence[Leg], spot: float, sigma: float, t_years: float, *, r: float = RISK_FREE_RATE) -> float:
    """The probability-weighted expiry P&L (risk-neutral lognormal, not discounted): each leg's expected intrinsic value
    in closed form, minus (or plus) its premium."""
    total = 0.0
    fwd = spot * math.exp(r * t_years)
    s = sigma * math.sqrt(max(t_years, 1e-12))
    for leg in legs:
        if leg["option_type"] == "FUT":
            total += (fwd - float(leg["premium"])) * _sign_qty(leg)
            continue
        k = float(leg["strike"])
        d1 = (math.log(fwd / k) + 0.5 * s * s) / s
        d2 = d1 - s
        call = fwd * _ncdf(d1) - k * _ncdf(d2)
        put = k * _ncdf(-d2) - fwd * _ncdf(-d1)
        intrinsic = call if leg["option_type"] == "CE" else put
        total += (intrinsic - float(leg["premium"])) * _sign_qty(leg)
    return total


def leg_rho(leg: Leg, spot: float, on: date, *, r: float = RISK_FREE_RATE) -> float:
    """Rho per unit, per 1 percentage point of the rate (0 on or after expiry)."""
    if on >= _expiry(leg):
        return 0.0
    k, t, sigma = float(leg["strike"]), time_to_expiry_years(_expiry(leg), on), _iv(leg, 0.0)
    d2 = (math.log(spot / k) + (r - 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    disc = k * t * math.exp(-r * t) / 100.0
    return disc * _ncdf(d2) if leg["option_type"] == "CE" else -disc * _ncdf(-d2)


def net_greeks(legs: Sequence[Leg], spot: float, on: date, *, iv_shift: float = 0.0, r: float = RISK_FREE_RATE) -> Dict[str, float]:
    """Net delta, gamma, theta (per day), vega (per IV point) and rho (per rate point) of the position on date `on`."""
    out = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    for leg in legs:
        q = _sign_qty(leg)
        if leg["option_type"] == "FUT":
            out["delta"] += q
            continue
        if on >= _expiry(leg):
            k = float(leg["strike"])
            itm = spot > k if leg["option_type"] == "CE" else spot < k
            out["delta"] += q * ((1.0 if leg["option_type"] == "CE" else -1.0) if itm else 0.0)
            continue
        t = time_to_expiry_years(_expiry(leg), on)
        ot = OptionType.CALL if leg["option_type"] == "CE" else OptionType.PUT
        g = black_scholes(BSInputs(spot, float(leg["strike"]), t, r, _iv(leg, iv_shift), ot))
        out["delta"] += q * g.delta
        out["gamma"] += q * g.gamma
        out["theta"] += q * g.theta
        out["vega"] += q * g.vega
        out["rho"] += q * leg_rho(leg, spot, on, r=r)
    return out


def summary(legs: Sequence[Leg], spot: float, *, as_of: date, sigma: Optional[float] = None, r: float = RISK_FREE_RATE) -> Dict[str, Any]:
    """The numbers the builder's metrics card shows, for the nearest expiry among the legs: PoP, expected move,
    probability-weighted P&L, net Greeks today. `sigma` defaults to the mean IV of the legs."""
    expiry = min(_expiry(leg) for leg in legs)
    t = time_to_expiry_years(expiry, as_of) if as_of < expiry else 0.0
    options = [leg for leg in legs if leg["option_type"] != "FUT"]
    if sigma is None and not options:
        raise ValueError("no option legs to take a volatility from - pass sigma")
    vol = sigma if sigma is not None else sum(_iv(leg, 0.0) for leg in options) / len(options)
    return {
        "expiry": expiry.isoformat(), "t_years": t, "sigma": vol,
        "pop": pop_at_expiry(legs, spot, vol, t, r=r), "expected_move": expected_move(spot, vol, t),
        "expected_pnl": expected_pnl_at_expiry(legs, spot, vol, t, r=r), "greeks": net_greeks(legs, spot, as_of, r=r),
        "model": "Black-Scholes, lognormal price, one volatility (an estimate, not a forecast)",
    }
