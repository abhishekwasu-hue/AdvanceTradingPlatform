"""P1-b: the strategy before expiry - T+0 / any-date value curves, IV and time scenarios, probability of profit, expected
move and probability-weighted P&L, all from the platform's Black-Scholes (app/option_chain/greeks.py).

A leg here is the builder's leg (direction, option_type, strike, premium, lots, lot_size) plus `expiry` (ISO date) and
`iv` (annualised, e.g. 0.14 - the leg's own, from the chain or solved from its premium; never invented).

- `value_curve`: the strategy's P&L at every price on a chosen date (`days_forward` from `as_of`) with every leg's IV
  shifted by `iv_shift` (absolute, 0.02 = +2 vol points). A leg is expired from 15:30 IST on its expiry day (P1-b
  review): `as_of` may be an aware datetime (exact - an expiry-day option at 10:00 still has 5.5 hours), today's date
  (now), or another plain date (that day's close: on its expiry date a leg is worth its intrinsic value), so a curve
  on the last expiry equals the expiry payoff. A FUT leg is worth spot x e^(r t) to its own expiry (the carry).
- `pop_at_expiry`: the probability the expiry payoff is above zero, under a lognormal price with one volatility. The
  expiry payoff is piecewise linear in the price, so the profitable intervals are found exactly between its kinks (no
  grid) and their probability summed.
- `expected_pnl_at_expiry`: the probability-weighted expiry P&L under the same distribution, in closed form.
- These and `payoff_extremes` need every leg to expire together; they refuse legs with different expiries. For a
  calendar or diagonal, `summary` integrates numerically over the price at the near expiry, with the later legs
  valued by Black-Scholes (P1-b review: their intrinsic value there would be nonsense).
- `expected_move`: the one-standard-deviation move, spot x IV x sqrt(t).

These are models: the UI labels them as estimates, never as a forecast or a recommendation.
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np

from app.core.config import RISK_FREE_RATE
from app.option_chain.greeks import IST, BSInputs, black_scholes, time_to_expiry_years, today_ist
from app.option_chain.models import OptionType
from app.options_builder.payoff import compute_leg_payoff

Leg = Mapping[str, Any]
When = Union[date, datetime]
EXPIRY_CLOSE = time(15, 30)          # a contract's last trade, IST (as in time_to_expiry_years)


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _sign_qty(leg: Leg) -> float:
    return (1.0 if leg["direction"] == "BUY" else -1.0) * float(leg["lots"]) * float(leg["lot_size"])


def _expiry(leg: Leg) -> date:
    e = leg.get("expiry")
    if not e:
        raise ValueError(f"leg {leg.get('option_type')} {leg.get('strike')} has no expiry - the model needs one")
    return e if isinstance(e, date) and not isinstance(e, datetime) else date.fromisoformat(str(e)[:10])


def years_left(expiry: date, on: When) -> Optional[float]:
    """Years of life left on `on`, or None once expired (15:30 IST on the expiry day). An aware datetime is exact;
    today's date means now; any other plain date means that day's close."""
    if isinstance(on, datetime):
        instant = on if on.tzinfo is not None else on.replace(tzinfo=timezone.utc)
        if instant >= datetime.combine(expiry, EXPIRY_CLOSE, tzinfo=IST):
            return None
        return time_to_expiry_years(expiry, instant)
    if on == today_ist():
        return years_left(expiry, datetime.now(timezone.utc))
    if on >= expiry:
        return None
    return time_to_expiry_years(expiry, on)


def _iv(leg: Leg, iv_shift: float) -> float:
    iv = leg.get("iv")
    if iv is None:
        raise ValueError(f"leg {leg.get('option_type')} {leg.get('strike')} has no IV - take it from the chain or solve it from the premium")
    return max(1e-4, float(iv) + iv_shift)


def _intrinsic(leg: Leg, spot: float) -> float:
    k = float(leg["strike"])
    return max(spot - k, 0.0) if leg["option_type"] == "CE" else max(k - spot, 0.0)


def leg_theoretical(leg: Leg, spot: float, on: When, *, iv_shift: float = 0.0, r: float = RISK_FREE_RATE) -> float:
    """One unit of the leg, valued at `on`: Black-Scholes while alive, intrinsic once expired. An underlying leg (FUT)
    is worth spot x e^(r t) to its own expiry (spot when it has none, or at its expiry)."""
    if leg["option_type"] == "FUT":
        t_fut = years_left(_expiry(leg), on) if leg.get("expiry") else None
        return spot * math.exp(r * t_fut) if t_fut else spot
    t = years_left(_expiry(leg), on)
    if t is None:
        return _intrinsic(leg, spot)
    ot = OptionType.CALL if leg["option_type"] == "CE" else OptionType.PUT
    return black_scholes(BSInputs(spot, float(leg["strike"]), t, r, _iv(leg, iv_shift), ot)).theoretical_price


def _shift(as_of: When, days_forward: float) -> When:
    return as_of + timedelta(days=int(round(days_forward)))


def pnl_on(legs: Sequence[Leg], prices: Sequence[float], on: When, *, iv_shift: float = 0.0, r: float = RISK_FREE_RATE) -> List[float]:
    """The strategy's P&L at each price, valued at `on`."""
    return [sum((leg_theoretical(leg, p, on, iv_shift=iv_shift, r=r) - float(leg["premium"])) * _sign_qty(leg) for leg in legs)
            for p in prices]


def value_curve(legs: Sequence[Leg], price_range: Sequence[float], *, as_of: When, days_forward: float = 0.0,
                iv_shift: float = 0.0, r: float = RISK_FREE_RATE) -> List[float]:
    """The strategy's P&L at every price on `as_of + days_forward` (whole days), with IVs shifted by `iv_shift`."""
    return pnl_on(legs, price_range, _shift(as_of, days_forward), iv_shift=iv_shift, r=r)


def expiries(legs: Sequence[Leg]) -> List[date]:
    """The distinct expiries of the legs that carry one, nearest first."""
    return sorted({_expiry(leg) for leg in legs if leg.get("expiry")})


def _one_expiry(legs: Sequence[Leg], what: str) -> None:
    if len(expiries(legs)) > 1:
        raise ValueError(f"{what} needs every leg to expire together; these legs expire on "
                         f"{', '.join(e.isoformat() for e in expiries(legs))} - use summary() or value_curve()")


def _tolerance(legs: Sequence[Leg]) -> float:
    """What counts as zero P&L: a payoff that is mathematically zero comes out as +-1e-11 or so (P1-b review)."""
    scale = sum(abs(_sign_qty(leg)) * max(1.0, abs(float(leg["strike"])), abs(float(leg.get("premium") or 0.0))) for leg in legs)
    return 1e-9 * max(1.0, scale)


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
    segment (and the tail above the last strike, by its slope) is solved for its zero. Values within the tolerance of
    zero count as zero."""
    _one_expiry(legs, "profitable_intervals")
    eps = _tolerance(legs)
    kinks = sorted({float(leg["strike"]) for leg in legs if leg["option_type"] != "FUT"})   # an underlying leg is linear
    points = [0.0] + kinks
    out: List[Tuple[float, float]] = []

    def val(p: float) -> float:
        v = _payoff(legs, p)
        return 0.0 if abs(v) <= eps else v

    def add(a: float, b: float) -> None:
        if b <= a:
            return
        if out and abs(out[-1][1] - a) < 1e-9:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))

    for a, b in zip(points, points[1:]):
        fa, fb = val(a), val(b)
        if fa > 0 and fb > 0:
            add(a, b)
        elif fa > 0 or fb > 0:
            z = a + (b - a) * (0 - fa) / (fb - fa)
            add(a, z) if fa > 0 else add(z, b)
    last = points[-1]
    f_last = val(last)
    slope = _payoff(legs, last + 1.0) - _payoff(legs, last)
    slope = 0.0 if abs(slope) <= eps else slope
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
    _one_expiry(legs, "payoff_extremes")
    eps = _tolerance(legs)
    kinks = sorted({float(leg["strike"]) for leg in legs if leg["option_type"] != "FUT"})
    points = [0.0] + kinks
    values = [_payoff(legs, p) for p in points]
    last = points[-1]
    slope = _payoff(legs, last + 1.0) - _payoff(legs, last)
    up, down = slope > eps, slope < -eps
    return {
        "max_profit": math.inf if up else max(values), "max_loss": -math.inf if down else min(values),
        "unbounded_profit": up, "unbounded_loss": down,
        "max_profit_at": None if up else points[values.index(max(values))],
        "max_loss_at": None if down else points[values.index(min(values))],
    }


def pop_at_expiry(legs: Sequence[Leg], spot: float, sigma: float, t_years: float, *, r: float = RISK_FREE_RATE) -> float:
    """Probability (0-1) that the expiry payoff is above zero, for a lognormal price at volatility `sigma`."""
    _one_expiry(legs, "pop_at_expiry")
    if sigma <= 0 or t_years <= 0:
        return 1.0 if _payoff(legs, spot * math.exp(r * max(t_years, 0.0))) > _tolerance(legs) else 0.0
    return sum(_lognormal_cdf(b, spot, sigma, t_years, r) - _lognormal_cdf(a, spot, sigma, t_years, r) for a, b in profitable_intervals(legs))


def expected_pnl_at_expiry(legs: Sequence[Leg], spot: float, sigma: float, t_years: float, *, r: float = RISK_FREE_RATE) -> float:
    """The probability-weighted expiry P&L (risk-neutral lognormal, not discounted): each leg's expected intrinsic value
    in closed form, minus (or plus) its premium."""
    _one_expiry(legs, "expected_pnl_at_expiry")
    fwd = spot * math.exp(r * max(t_years, 0.0))
    if sigma <= 0 or t_years <= 0:
        return _payoff(legs, fwd)                    # no volatility: the price ends at the forward (P1-b review)
    total = 0.0
    s = sigma * math.sqrt(t_years)
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


def leg_rho(leg: Leg, spot: float, on: When, *, iv_shift: float = 0.0, r: float = RISK_FREE_RATE) -> float:
    """Rho per unit, per 1 percentage point of the rate (0 once expired), at the leg's IV shifted by `iv_shift`."""
    t = years_left(_expiry(leg), on)
    if t is None:
        return 0.0
    k, sigma = float(leg["strike"]), _iv(leg, iv_shift)
    d2 = (math.log(spot / k) + (r - 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    disc = k * t * math.exp(-r * t) / 100.0
    return disc * _ncdf(d2) if leg["option_type"] == "CE" else -disc * _ncdf(-d2)


def net_greeks(legs: Sequence[Leg], spot: float, on: When, *, iv_shift: float = 0.0, r: float = RISK_FREE_RATE) -> Dict[str, float]:
    """Net delta, gamma, theta (per day), vega (per IV point) and rho (per rate point) of the position at `on`. A FUT
    leg has delta e^(r t) and rho spot x t x e^(r t) / 100 to its expiry."""
    out = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    for leg in legs:
        q = _sign_qty(leg)
        if leg["option_type"] == "FUT":
            t_fut = (years_left(_expiry(leg), on) if leg.get("expiry") else None) or 0.0
            out["delta"] += q * math.exp(r * t_fut)
            out["rho"] += q * spot * t_fut * math.exp(r * t_fut) / 100.0
            continue
        t = years_left(_expiry(leg), on)
        if t is None:
            k = float(leg["strike"])
            itm = spot > k if leg["option_type"] == "CE" else spot < k
            out["delta"] += q * ((1.0 if leg["option_type"] == "CE" else -1.0) if itm else 0.0)
            continue
        ot = OptionType.CALL if leg["option_type"] == "CE" else OptionType.PUT
        g = black_scholes(BSInputs(spot, float(leg["strike"]), t, r, _iv(leg, iv_shift), ot))
        out["delta"] += q * g.delta
        out["gamma"] += q * g.gamma
        out["theta"] += q * g.theta
        out["vega"] += q * g.vega
        out["rho"] += q * leg_rho(leg, spot, on, iv_shift=iv_shift, r=r)
    return out


HORIZON_GRID = 4001       # points of the lognormal grid for a calendar's numerical PoP and expected P&L
HORIZON_SD = 8.0          # +- standard deviations it covers


def horizon_stats(legs: Sequence[Leg], spot: float, sigma: float, t_years: float, horizon: When, *,
                  r: float = RISK_FREE_RATE) -> Dict[str, float]:
    """PoP and probability-weighted P&L at `horizon` (t_years away) for legs that need not expire together: the price
    at the horizon is lognormal at `sigma`; every leg is valued there by `pnl_on` (later legs by Black-Scholes at their
    own IV); the expectation is a sum over a fine grid (an estimate: accurate to about 1e-3 in PoP)."""
    if sigma <= 0 or t_years <= 0:
        p = pnl_on(legs, [spot * math.exp(r * max(t_years, 0.0))], horizon, r=r)[0]
        return {"pop": 1.0 if p > _tolerance(legs) else 0.0, "expected_pnl": p}
    z = np.linspace(-HORIZON_SD, HORIZON_SD, HORIZON_GRID)
    prices = spot * np.exp((r - 0.5 * sigma * sigma) * t_years + sigma * math.sqrt(t_years) * z)
    w = np.exp(-0.5 * z * z)
    w /= w.sum()
    pnl = np.asarray(pnl_on(legs, prices.tolist(), horizon, r=r))
    return {"pop": float(w[pnl > _tolerance(legs)].sum()), "expected_pnl": float((w * pnl).sum())}


def summary(legs: Sequence[Leg], spot: float, *, as_of: When, sigma: Optional[float] = None, r: float = RISK_FREE_RATE) -> Dict[str, Any]:
    """The numbers the builder's metrics card shows, at the nearest expiry among the legs: PoP, expected move,
    probability-weighted P&L, net Greeks now. `sigma` defaults to the mean IV of the option legs. When the legs expire
    on different dates (calendars, diagonals) PoP and P&L come from `horizon_stats` and `method` says so."""
    dates = expiries(legs)
    if not dates:
        raise ValueError("no leg has an expiry - the model needs one")
    expiry = dates[0]
    t = years_left(expiry, as_of) or 0.0
    options = [leg for leg in legs if leg["option_type"] != "FUT"]
    if sigma is None and not options:
        raise ValueError("no option legs to take a volatility from - pass sigma")
    vol = sigma if sigma is not None else sum(_iv(leg, 0.0) for leg in options) / len(options)
    if len(dates) > 1:
        horizon = datetime.combine(expiry, EXPIRY_CLOSE, tzinfo=IST)
        stats = horizon_stats(legs, spot, vol, t, horizon, r=r)
        method = "numerical: legs expire on different dates; later legs valued by Black-Scholes at the near expiry"
    else:
        stats = {"pop": pop_at_expiry(legs, spot, vol, t, r=r), "expected_pnl": expected_pnl_at_expiry(legs, spot, vol, t, r=r)}
        method = "exact: every leg expires together"
    return {
        "expiry": expiry.isoformat(), "t_years": t, "sigma": vol, "pop": stats["pop"], "expected_move": expected_move(spot, vol, t),
        "expected_pnl": stats["expected_pnl"], "greeks": net_greeks(legs, spot, as_of, r=r), "method": method,
        "model": "Black-Scholes, lognormal price, one volatility (an estimate, not a forecast)",
    }
