"""N2-1 (P1 spec 1.5, docs/design/N2_P1FGH.md): figures from a user's own closed trades - pure and deterministic.

Input: the net P&L of each closed trade, after charges, in rupees. Output: plain numbers with a status code; nothing
here sizes, places or recommends a trade. N2-3's calculators reuse these with typed inputs.

- Expectancy is net P&L per trade. Profit factor is gross wins / gross losses.
- Payoff ratio is average win / average loss. A flat trade (exactly 0) is neither a win nor a loss.
- Kelly reference: the fraction f of capital lost on a losing trade that maximises the mean log growth of the past
  outcomes. Maximising p·log(1 + f·b) + q·log(1 − f) gives f = (p·b − q) / (b·(p + q)), with p and q the win and loss
  shares and b the payoff ratio. With no flat trade, this is the textbook p − q / b.
- Risk of ruin: Lundberg's bound for a random walk whose steps are drawn from the past P&Ls. The chance of ever falling
  D rupees below the start is at most e^(−R·D), where R > 0 solves mean(e^(−R·x)) = 1. For ±1 steps this equals the
  gambler's-ruin (q/p)^D. A non-positive expectancy makes ruin certain in this model. With no loss there is nothing to
  bound.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence

KELLY_FORMULA = "f = (p·b − q) / (b·(p + q))"
RUIN_METHOD = "lundberg_upper_bound"
_R_CAP = 700.0          # scaled coefficient cap: e^700 is still a float; a larger root only makes the bound smaller


def _round(value: Optional[float], places: int = 2) -> Optional[float]:
    return None if value is None else round(value, places)


def trade_stats(pnls: Sequence[float]) -> Dict[str, Any]:
    """Counts and per-trade figures. Ratios are None when undefined (no trade, no win, no loss)."""
    n = len(pnls)
    wins = [float(x) for x in pnls if x > 0]
    losses = [-float(x) for x in pnls if x < 0]
    gross_win, gross_loss = math.fsum(wins), math.fsum(losses)
    avg_win = gross_win / len(wins) if wins else None
    avg_loss = gross_loss / len(losses) if losses else None
    return {
        "trades": n, "wins": len(wins), "losses": len(losses), "flat": n - len(wins) - len(losses),
        "net": math.fsum(float(x) for x in pnls),
        "win_rate": len(wins) / n if n else None,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "payoff_ratio": (avg_win / avg_loss) if avg_win is not None and avg_loss else None,
        "expectancy": math.fsum(float(x) for x in pnls) / n if n else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
    }


def kelly_fraction(wins: int, losses: int, payoff_ratio: Optional[float]) -> Optional[float]:
    """f = (p·b − q) / (b·(p + q)); None without both a win and a loss. Negative = the past outcomes lost money."""
    if wins <= 0 or losses <= 0 or not payoff_ratio or payoff_ratio <= 0:
        return None
    total = wins + losses
    p, q, b = wins / total, losses / total, payoff_ratio
    return (p * b - q) / b


def adjustment_coefficient(pnls: Sequence[float]) -> Optional[float]:
    """Lundberg's R > 0 with mean(e^(−R·x)) = 1, by bisection on the P&Ls scaled to |x| <= 1. None when there is no root
    (no losing trade, or a non-positive mean). Capped at _R_CAP / scale: the bound it gives is then still valid."""
    xs = [float(x) for x in pnls]
    if not xs or not any(x < 0 for x in xs) or math.fsum(xs) <= 0:
        return None
    scale = max(abs(x) for x in xs)
    ys = [x / scale for x in xs]

    def excess(r: float) -> float:
        return math.fsum(math.exp(-r * y) for y in ys) / len(ys) - 1.0

    hi = 1.0
    while excess(hi) <= 0.0:
        if hi >= _R_CAP:
            return _R_CAP / scale
        hi = min(hi * 2.0, _R_CAP)
    lo = 0.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if excess(mid) <= 0.0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0 / scale


def ruin_bound(pnls: Sequence[float], distance: float) -> Dict[str, Any]:
    """{status, probability}: "ok" with the bound e^(−R·D), "certain" (1.0) for a non-positive expectancy, "no_losses"
    (None) when no trade lost."""
    xs = [float(x) for x in pnls]
    if xs and not any(x < 0 for x in xs):
        return {"status": "no_losses", "probability": None}
    if not xs or math.fsum(xs) <= 0:
        return {"status": "certain", "probability": 1.0}
    r = adjustment_coefficient(xs)
    assert r is not None
    return {"status": "ok", "probability": min(1.0, max(0.0, math.exp(-r * max(0.0, distance))))}


def evidence(pnls: Sequence[float], starting_capital: float, *, min_trades: int, ruin_pct: float) -> Dict[str, Any]:
    """The ladder card's "your figures" block. Below `min_trades` closed trades only the counts are given."""
    stats = trade_stats(pnls)
    distance = max(0.0, float(starting_capital)) * float(ruin_pct) / 100.0
    out: Dict[str, Any] = {
        "trades": stats["trades"], "wins": stats["wins"], "losses": stats["losses"], "flat": stats["flat"],
        "min_trades": int(min_trades), "enough": stats["trades"] >= min_trades,
        "basis": "Your closed PAPER trades priced from platform quotes, net P&L after charges",
    }
    if not out["enough"]:
        out.update(status="too_few", note=f"{stats['trades']} of {int(min_trades)} closed trades: no figures yet")
        return out
    kelly = kelly_fraction(stats["wins"], stats["losses"], stats["payoff_ratio"])
    kelly_status = "ok" if kelly is not None and kelly > 0 else ("negative" if kelly is not None else
                                                                  ("no_losses" if stats["losses"] == 0 else "no_wins"))
    ruin = ruin_bound(pnls, distance)
    out.update(
        status="ok",
        net=_round(stats["net"]), win_rate=_round(stats["win_rate"], 4), avg_win=_round(stats["avg_win"]), avg_loss=_round(stats["avg_loss"]),
        payoff_ratio=_round(stats["payoff_ratio"], 3), expectancy=_round(stats["expectancy"]), profit_factor=_round(stats["profit_factor"], 3),
        kelly={"fraction": _round(kelly, 4), "status": kelly_status, "formula": KELLY_FORMULA,
               "note": "A formula's reading of past outcomes, not a position size; sizing stays with your risk cap"},
        ruin={"probability": None if ruin["probability"] is None else float(f"{ruin['probability']:.4g}"), "status": ruin["status"],
              "ruin_pct": float(ruin_pct),
              "distance": _round(distance), "method": RUIN_METHOD,
              "note": f"Upper bound on ever falling {float(ruin_pct):g}% below the starting capital if trades kept "
                      "coming from your past results at the same rupee sizes"},
    )
    return out
