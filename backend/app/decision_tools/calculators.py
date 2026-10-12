"""N2-3 (P1 spec 1.5, docs/design/N2_P1FGH.md): the risk-of-ruin and Kelly calculator on typed inputs - pure.

Inputs:
- win rate p;
- payoff ratio b (average win / average loss);
- risk per trade r, as % of the starting capital (a fixed rupee risk);
- ruin level D, as % of the starting capital.

The page prefills them from the user's own figures (N2-1 `evidence`), and the user may change any of them. Each trade
is +b·r % with chance p or −r % with chance q = 1 − p.

Outputs:
- Expectancy per trade in R (risked units): p·b − q.
- Kelly reference: f = p − q / b, the fraction of capital lost on a losing trade that maximises the long-run log growth.
- Growth per trade, if each trade risked r % of the *current* capital: p·ln(1 + b·r) + q·ln(1 − r). It is negative past
  roughly 2·f.
- Risk of ruin (upper bound): Lundberg's e^(−R·D), with R solving p·e^(−R·b·r) + q·e^(R·r) = 1. It is certain with a
  non-positive expectancy. For b = 1 it equals the gambler's-ruin (q/p)^(D/r).

A comparison row is given for each risk level in `compare`, so the effect of the risk per trade can be read off. These
are models of the inputs, never advice; nothing here sizes or places an order.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from app.decision_tools.own_stats import KELLY_FORMULA, weighted_adjustment_coefficient

DEFAULT_COMPARE = (0.5, 1.0, 2.0, 5.0)
MAX_COMPARE = 8


class CalculatorError(ValueError):
    pass


def _check(p: float, b: float, risk_pct: float, ruin_pct: float) -> None:
    problems = []
    if not 0.0 < p < 1.0:
        problems.append("win rate must be between 0 and 1 (exclusive)")
    if not b > 0.0:
        problems.append("payoff ratio must be above 0")
    if not 0.0 < risk_pct < 100.0:
        problems.append("risk per trade must be between 0 and 100 % of capital")
    if not 0.0 < ruin_pct <= 100.0:
        problems.append("ruin level must be between 0 and 100 % of capital")
    if problems:
        raise CalculatorError("; ".join(problems))


def ruin_probability(p: float, b: float, risk_pct: float, ruin_pct: float) -> Dict[str, Any]:
    """{status, probability}: "ok" with the Lundberg bound, "certain" (1.0) with a non-positive expectancy."""
    if p * b - (1.0 - p) <= 0.0:
        return {"status": "certain", "probability": 1.0}
    r = weighted_adjustment_coefficient([(b * risk_pct, p), (-risk_pct, 1.0 - p)])
    assert r is not None
    return {"status": "ok", "probability": min(1.0, max(0.0, math.exp(-r * ruin_pct)))}


def growth_per_trade(p: float, b: float, risk_pct: float) -> Optional[float]:
    """Mean log growth per trade when each trade risks `risk_pct` % of the current capital; None when a loss would take
    everything (risk at 100 %)."""
    f = risk_pct / 100.0
    if f >= 1.0:
        return None
    return p * math.log(1.0 + b * f) + (1.0 - p) * math.log(1.0 - f)


def _row(p: float, b: float, risk_pct: float, ruin_pct: float) -> Dict[str, Any]:
    ruin = ruin_probability(p, b, risk_pct, ruin_pct)
    g = growth_per_trade(p, b, risk_pct)
    prob = ruin["probability"]
    return {"risk_pct": risk_pct, "ruin_probability": float(f"{prob:.4g}"), "ruin_status": ruin["status"],
            "losses_to_ruin": math.ceil(ruin_pct / risk_pct - 1e-9),
            "growth_per_trade_pct": None if g is None else round(g * 100.0, 4)}


def calculate(win_rate: float, payoff_ratio: float, risk_pct: float, ruin_pct: float,
              compare: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    """The calculator's answer: the inputs, expectancy, the Kelly reference and one row per risk level (the chosen one
    marked)."""
    p, b = float(win_rate), float(payoff_ratio)
    _check(p, b, float(risk_pct), float(ruin_pct))
    levels: List[float] = []
    for x in [*(compare if compare is not None else DEFAULT_COMPARE), float(risk_pct)]:
        x = round(float(x), 4)
        if 0.0 < x < 100.0 and x not in levels:
            levels.append(x)
    levels = sorted(levels)[: MAX_COMPARE + 1]
    if round(float(risk_pct), 4) not in levels:
        levels.append(round(float(risk_pct), 4))
        levels.sort()
    q = 1.0 - p
    kelly = p - q / b
    rows = [{**_row(p, b, x, float(ruin_pct)), "chosen": x == round(float(risk_pct), 4)} for x in levels]
    return {
        "inputs": {"win_rate": p, "payoff_ratio": b, "risk_pct": float(risk_pct), "ruin_pct": float(ruin_pct)},
        "expectancy_r": round(p * b - q, 4),
        "kelly": {"fraction": round(kelly, 4), "status": "ok" if kelly > 0 else "negative", "formula": KELLY_FORMULA},
        "rows": rows,
        "method": "lundberg_upper_bound",
        "note": "Each trade risks a fixed share of the starting capital and wins b times that with the given chance. A model of "
                "these inputs, not a forecast; nothing here sizes or places a trade",
    }
