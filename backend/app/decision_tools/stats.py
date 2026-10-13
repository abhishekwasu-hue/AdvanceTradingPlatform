"""N2-4 (P1 spec 1.5): pure statistics over daily closes - log returns, historical volatility, correlation, beta, and
the expected-move cone. Deterministic; no I/O. A model of past prices, never a forecast.

- Returns are the log returns of consecutive closes. Two series are aligned on the dates both have before any pairwise
  figure.
- Historical volatility (HV) is the sample standard deviation of the last `window` daily log returns × √252,
  annualised.
- Beta of a to m is cov(a, m) / var(m). Correlation is Pearson's r.
- Expected move: the 1-sd move over h sessions is close × σ × √(h / 252). The cone gives ±1 sd (about 68 % under a
  lognormal model) and ±2 sd (about 95 %) as prices: close × e^(±k·σ·√(h/252)).
"""
from __future__ import annotations

import math
from datetime import date
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

TRADING_DAYS = 252


def log_returns(closes: Mapping[date, float]) -> Dict[date, float]:
    """{date: ln(close / previous close)} for every date after the first (closes <= 0 are skipped)."""
    days = sorted(d for d, c in closes.items() if c and c > 0)
    return {d: math.log(closes[d] / closes[p]) for p, d in zip(days, days[1:])}


def _mean(xs: Sequence[float]) -> float:
    return math.fsum(xs) / len(xs)


def stdev(xs: Sequence[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = _mean(xs)
    return math.sqrt(math.fsum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def hv(closes: Mapping[date, float], window: int = 20) -> Optional[float]:
    """Annualised historical volatility of the last `window` daily log returns; None with fewer returns."""
    rets = [r for _, r in sorted(log_returns(closes).items())][-window:]
    if len(rets) < window:
        return None
    s = stdev(rets)
    return None if s is None else s * math.sqrt(TRADING_DAYS)


def aligned(a: Mapping[date, float], b: Mapping[date, float]) -> Tuple[List[float], List[float]]:
    days = sorted(set(a) & set(b))
    return [a[d] for d in days], [b[d] for d in days]


def correlation(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Pearson's r; None with fewer than 3 points or a flat series."""
    if len(a) != len(b) or len(a) < 3:
        return None
    ma, mb = _mean(a), _mean(b)
    sab = math.fsum((x - ma) * (y - mb) for x, y in zip(a, b))
    saa = math.fsum((x - ma) ** 2 for x in a)
    sbb = math.fsum((y - mb) ** 2 for y in b)
    if saa <= 0 or sbb <= 0:
        return None
    return max(-1.0, min(1.0, sab / math.sqrt(saa * sbb)))


def beta(a: Sequence[float], m: Sequence[float]) -> Optional[float]:
    """cov(a, m) / var(m); None with fewer than 3 points or a flat market series."""
    if len(a) != len(m) or len(a) < 3:
        return None
    ma, mm = _mean(a), _mean(m)
    var = math.fsum((y - mm) ** 2 for y in m)
    if var <= 0:
        return None
    return math.fsum((x - ma) * (y - mm) for x, y in zip(a, m)) / var


def cone(close: float, sigma: float, horizons: Sequence[int]) -> List[Dict[str, float]]:
    """One row per horizon (sessions): the 1-sd move and the ±1 sd / ±2 sd prices."""
    out = []
    for h in horizons:
        s = sigma * math.sqrt(h / TRADING_DAYS)
        out.append({"sessions": int(h), "move_1sd": round(close * s, 2),
                    "low_1sd": round(close * math.exp(-s), 2), "high_1sd": round(close * math.exp(s), 2),
                    "low_2sd": round(close * math.exp(-2 * s), 2), "high_2sd": round(close * math.exp(2 * s), 2)})
    return out
