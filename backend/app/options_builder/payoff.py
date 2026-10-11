"""Payoff at expiry, combined Greeks, breakevens and max profit / loss for a multi-leg options strategy.

Leg format (a mapping): direction "BUY" / "SELL", option_type "CE" / "PE", strike, premium, lots, lot_size. Extra keys
(instrument_key, Greeks, expiry...) are ignored by the payoff. Ported from Trade `strategy_payoff.py`; same results.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Tuple

GREEK_KEYS = ("delta", "gamma", "theta", "vega")


def compute_leg_payoff(underlying_price: float, direction: str, option_type: str, strike: float, premium: float,
                       lots: float, lot_size: float, **_extra: Any) -> float:
    """One leg's P&L at expiry for an underlying price. Other keys on the leg are accepted and ignored.
    P1-b: an underlying leg (option_type "FUT") is linear - its `premium` is the entry price (its `strike` is ignored)."""
    if option_type == "FUT":
        move = underlying_price - premium
        return (move if direction == "BUY" else -move) * lots * lot_size
    intrinsic = max(underlying_price - strike, 0) if option_type == "CE" else max(strike - underlying_price, 0)
    if direction == "BUY":
        return (intrinsic - premium) * lots * lot_size
    return (premium - intrinsic) * lots * lot_size


def compute_strategy_payoff_curve(legs: Sequence[Mapping[str, Any]], price_range: Sequence[float]) -> List[float]:
    """The whole strategy's P&L at expiry at every price of `price_range` (same length and order)."""
    return [sum(compute_leg_payoff(p, **leg) for leg in legs) for p in price_range]


def compute_combined_greeks(legs_with_greeks: Sequence[Mapping[str, Any]]) -> Dict[str, float]:
    """Net Greeks of the strategy: each leg's per-unit Greeks signed by direction (BUY +, SELL -) and scaled by
    lots x lot_size. A leg without a Greek counts it as 0."""
    total = {g: 0.0 for g in GREEK_KEYS}
    for leg in legs_with_greeks:
        sign = 1 if leg["direction"] == "BUY" else -1
        qty = leg["lots"] * leg["lot_size"]
        for g in total:
            total[g] += sign * leg.get(g, 0.0) * qty
    return total


def find_breakeven_points(price_range: Sequence[float], payoff_curve: Sequence[float]) -> List[float]:
    """Prices where the payoff crosses zero, linearly interpolated between grid points (rounded to 2 decimals).

    Kept exactly as the Trade source (golden parity), including its edges: every grid point where the payoff is
    exactly 0 is listed (a flat zero run lists each point, and a touch without a sign change is listed too), except
    the last grid point. P1-b's `model.profitable_intervals` gives exact intervals for the builder's numbers."""
    breakevens: List[float] = []
    for i in range(1, len(payoff_curve)):
        p1, p2 = payoff_curve[i - 1], payoff_curve[i]
        if p1 == 0:
            breakevens.append(round(price_range[i - 1], 2))
        elif (p1 < 0 < p2) or (p1 > 0 > p2):
            x1, x2 = price_range[i - 1], price_range[i]
            breakeven = x1 + (x2 - x1) * (0 - p1) / (p2 - p1)
            breakevens.append(round(breakeven, 2))
    return breakevens


def compute_max_profit_loss(payoff_curve: Sequence[float]) -> Tuple[float, float]:
    """(max profit, max loss) over the given price range - beyond it a naked leg can be unbounded."""
    return max(payoff_curve), min(payoff_curve)


def build_default_price_range(underlying_price: float, num_points: int = 100, range_pct: float = 5.0) -> List[float]:
    """An evenly spaced price grid of ±range_pct % around the current price, for the payoff chart."""
    lo = underlying_price * (1 - range_pct / 100)
    hi = underlying_price * (1 + range_pct / 100)
    step = (hi - lo) / (num_points - 1)
    return [round(lo + i * step, 2) for i in range(num_points)]
