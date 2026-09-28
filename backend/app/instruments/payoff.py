"""Phase U: expiry payoff of an arbitrary set of same-expiry option legs.

The Phase H2/R structures each carried hand-written economics (a bull put's max loss is the
width minus the credit, a straddle's breakevens are strike +/- credit ...). Ratio spreads, long
butterflies and the free-form leg builder cannot: their max loss, max profit and breakevens
depend on how the legs combine. This module computes them from the payoff itself.

The P&L per unit at expiry, as a function of the underlying S, is

    pnl(S) = net_credit + sum over legs of  sign * ratio * intrinsic(S)

with sign +1 for a bought leg, -1 for a sold one, and intrinsic max(S-K, 0) for a CE or
max(K-S, 0) for a PE. It is piecewise linear with kinks at the strikes, so:

* the extreme values lie at the strikes (or at S = 0); evaluating there gives the finite
  `peak` and `trough`;
* the slope beyond the highest strike (sum of the CE legs' signed ratios) says whether the
  upside is bounded: a negative slope means the loss grows without limit as S rises
  (`upside_defined = False`) - a naked short call, a call ratio spread;
* the slope below the lowest strike (sum of the PE legs' signed ratios) says the same for the
  downside: a positive slope means the loss grows as S falls towards zero
  (`downside_defined = False`) - a naked short put, a put ratio spread;
* the breakevens are the roots of the piecewise-linear function, found segment by segment.

`max_loss` is None when either side is undefined and `max_profit` is None when the upside is
unbounded (S can rise without limit; the downside profit is bounded by S = 0 and is reported).
Everything is per unit (one share of the lot); the executor multiplies by lot size and lots.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence


@dataclass(frozen=True)
class PayoffLeg:
    right: str        # "CE" / "PE"
    role: str         # "SHORT" (sold) / "LONG" (bought)
    strike: float
    premium: float    # per unit, as quoted
    ratio: int = 1    # lots of this leg per lot of the structure

    @property
    def sign(self) -> int:
        """+1 for a bought leg (owns the intrinsic value), -1 for a sold one."""
        return 1 if self.role == "LONG" else -1

    def intrinsic(self, spot: float) -> float:
        if self.right == "CE":
            return max(spot - self.strike, 0.0)
        return max(self.strike - spot, 0.0)


@dataclass
class PayoffAnalysis:
    net_credit: float                  # positive = credit received, negative = debit paid
    peak: float                        # best P&L at any strike or at S = 0 (always finite)
    trough: float                      # worst P&L at any strike or at S = 0
    max_profit: Optional[float]        # None when the upside is unbounded
    max_loss: Optional[float]          # None when either side's loss is unbounded; else >= 0
    breakevens: List[float]
    upside_defined: bool
    downside_defined: bool
    slope_up: int                      # dP&L/dS above the highest strike
    slope_down: int                    # dP&L/dS below the lowest strike
    strikes: List[float] = field(default_factory=list)
    pnl_at: Callable[[float], float] = field(default=lambda s: 0.0, repr=False, compare=False)

    @property
    def defined_risk(self) -> bool:
        return self.upside_defined and self.downside_defined

    def as_dict(self) -> dict:
        return {"net_credit": self.net_credit, "peak": self.peak, "trough": self.trough, "max_profit": self.max_profit,
                "max_loss": self.max_loss, "breakevens": self.breakevens, "upside_defined": self.upside_defined,
                "downside_defined": self.downside_defined}


def _round(value: float) -> float:
    return round(value + 0.0, 2)


def analyse(legs: Sequence[PayoffLeg]) -> PayoffAnalysis:
    """Expiry economics of `legs`, which must share one expiry (a calendar has no static payoff)."""
    if not legs:
        raise ValueError("a structure needs at least one leg")
    net_credit = sum(-leg.sign * leg.ratio * leg.premium for leg in legs)

    def pnl(spot: float) -> float:
        return net_credit + sum(leg.sign * leg.ratio * leg.intrinsic(spot) for leg in legs)

    strikes = sorted({float(leg.strike) for leg in legs})
    slope_up = sum(leg.sign * leg.ratio for leg in legs if leg.right == "CE")
    slope_down = sum(-leg.sign * leg.ratio for leg in legs if leg.right == "PE")   # dP&L/dS for S below every strike
    upside_defined = slope_up >= 0
    downside_defined = slope_down <= 0

    points = [0.0] + strikes
    values = [pnl(p) for p in points]
    peak, trough = max(values), min(values)
    max_profit = None if slope_up > 0 else _round(peak)
    max_loss = None if not (upside_defined and downside_defined) else _round(max(0.0, -trough))

    breakevens: List[float] = []
    for (s0, v0), (s1, v1) in zip(zip(points, values), zip(points[1:], values[1:])):
        if v0 == 0.0 and s0 > 0:
            breakevens.append(s0)
        if (v0 < 0 < v1) or (v1 < 0 < v0):
            breakevens.append(s0 + (s1 - s0) * (-v0) / (v1 - v0))
    last_s, last_v = points[-1], values[-1]
    if last_v == 0.0:
        breakevens.append(last_s)
    elif slope_up != 0 and (last_v > 0) != (slope_up > 0):
        breakevens.append(last_s - last_v / slope_up)
    breakevens = sorted({_round(b) for b in breakevens if b > 0})

    return PayoffAnalysis(
        net_credit=_round(net_credit), peak=_round(peak), trough=_round(trough), max_profit=max_profit, max_loss=max_loss,
        breakevens=breakevens, upside_defined=upside_defined, downside_defined=downside_defined,
        slope_up=slope_up, slope_down=slope_down, strikes=strikes, pnl_at=pnl,
    )
