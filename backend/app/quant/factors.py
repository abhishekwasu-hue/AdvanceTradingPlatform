"""Phase Z: cross-sectional factor scores.

Every factor is a plain, documented number computed from a symbol's own candles (and, when the
caller supplies them, a handful of fundamental ratios), standardised across the universe the
caller sends (z-scores, winsorised at +-3) and blended with explicit weights into a composite.
Nothing is fitted, nothing is learned: the same universe and the same weights always give the
same table, and every row says which factors it had data for. A factor without enough bars is
None for that symbol and simply carries no weight in its composite (the remaining weights are
renormalised), so a short history lowers coverage rather than inventing a score.

Factors (higher = more attractive in the usual long-only sense):
* momentum         - return over `momentum_lookback` bars, skipping the last `skip_recent`
                     (the classic 12-1 shape at whatever bar size the caller uses)
* reversal         - minus the return over the last `reversal_lookback` bars (short-term losers
                     tend to bounce; sign flipped so a high score means "oversold")
* low_volatility   - minus the annualised standard deviation of log returns
* trend            - ADX(14) signed by EMA20 vs EMA50 (strength in the direction of the trend)
* liquidity        - log of the average traded value (close x volume) over `liquidity_lookback`
* value            - earnings yield (1/PE) and book yield (1/PB), whichever are supplied
* quality          - ROE % plus half the earnings growth %, minus 10 x debt-to-equity

Fundamentals never come from the platform's own tables here: the caller passes them per symbol
(`pe`, `pb`, `roe_pct`, `debt_to_equity`, `earnings_growth_pct`), so this module stays pure.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from app.indicators.directional import adx as adx_frame
from app.indicators.trend import ema

FACTORS = ("momentum", "reversal", "low_volatility", "trend", "liquidity", "value", "quality")
DEFAULT_WEIGHTS: Dict[str, float] = {"momentum": 0.30, "reversal": 0.10, "low_volatility": 0.20, "trend": 0.20, "liquidity": 0.10,
                                     "value": 0.05, "quality": 0.05}
WINSOR = 3.0
BUCKET_FRACTION = 0.2      # top/bottom quintile of the universe by composite


@dataclass
class FactorInputs:
    symbol: str
    df: pd.DataFrame
    fundamentals: Optional[Dict[str, float]] = None


@dataclass
class FactorRow:
    symbol: str
    close: float
    bars: int
    raw: Dict[str, Optional[float]]
    z: Dict[str, Optional[float]] = field(default_factory=dict)
    composite: Optional[float] = None
    rank: Optional[int] = None
    bucket: str = "NEUTRAL"       # LONG / SHORT / NEUTRAL

    @property
    def coverage(self) -> int:
        return sum(1 for v in self.raw.values() if v is not None)

    def as_dict(self) -> dict:
        return {"symbol": self.symbol, "close": self.close, "bars": self.bars, "raw": self.raw, "z": self.z, "composite": self.composite,
                "rank": self.rank, "bucket": self.bucket, "coverage": self.coverage}


@dataclass
class FactorTable:
    rows: List[FactorRow]
    weights: Dict[str, float]
    bars_per_year: float
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"rows": [r.as_dict() for r in self.rows], "weights": self.weights, "bars_per_year": self.bars_per_year,
                "universe": len(self.rows), "factors": list(FACTORS), "warnings": self.warnings}

    def z_of(self, symbol: str) -> Dict[str, Optional[float]]:
        return next((r.z for r in self.rows if r.symbol == symbol), {})


def bars_per_year_of(df: pd.DataFrame) -> float:
    """Annualisation from the bar spacing: 250 sessions of 375 minutes for intraday bars, 250
    for daily. Falls back to daily when the spacing cannot be read."""
    if len(df.index) < 3:
        return 250.0
    try:
        deltas = pd.Series(df.index[1:] - df.index[:-1]).dt.total_seconds()
        minutes = float(deltas.median()) / 60.0
    except (AttributeError, TypeError):
        return 250.0
    if not minutes or minutes <= 0 or minutes >= 6 * 60:
        return 250.0
    return 250.0 * 375.0 / minutes


def _pct_change(close: pd.Series, start: int, end: int) -> Optional[float]:
    """Return between close[-1-end] and close[-1-start] (start < end, counted from the last bar)."""
    if len(close) <= end:
        return None
    a, b = float(close.iloc[-1 - end]), float(close.iloc[-1 - start])
    return (b / a - 1.0) if a > 0 else None


def raw_factors(df: pd.DataFrame, fundamentals: Optional[Dict[str, float]] = None, *, momentum_lookback: int = 120, skip_recent: int = 5,
                reversal_lookback: int = 5, vol_lookback: int = 60, trend_period: int = 14, liquidity_lookback: int = 20,
                bars_per_year: Optional[float] = None) -> Dict[str, Optional[float]]:
    close = df["close"].astype(float)
    n = len(close)
    out: Dict[str, Optional[float]] = {f: None for f in FACTORS}
    out["momentum"] = _pct_change(close, skip_recent, skip_recent + momentum_lookback)
    rev = _pct_change(close, 0, reversal_lookback)
    out["reversal"] = -rev if rev is not None else None
    if n > vol_lookback:
        rets = np.log(close.iloc[-vol_lookback - 1:].values[1:] / close.iloc[-vol_lookback - 1:].values[:-1])
        rets = rets[np.isfinite(rets)]
        if len(rets) >= 5:
            out["low_volatility"] = -float(np.std(rets, ddof=1) * math.sqrt(bars_per_year or bars_per_year_of(df)))
    if n >= max(60, trend_period * 3):
        try:
            strength = float(adx_frame(df, trend_period)["adx"].iloc[-1])
            direction = 1.0 if float(ema(close, 20).iloc[-1]) >= float(ema(close, 50).iloc[-1]) else -1.0
            if math.isfinite(strength):
                out["trend"] = strength * direction
        except (ValueError, KeyError, IndexError):
            out["trend"] = None
    if "volume" in df and n >= liquidity_lookback:
        value = (close.iloc[-liquidity_lookback:] * df["volume"].astype(float).iloc[-liquidity_lookback:]).mean()
        out["liquidity"] = math.log(float(value)) if value and value > 0 else None
    fund = fundamentals or {}
    yields = []
    if (pe := fund.get("pe")) and pe > 0:
        yields.append(1.0 / float(pe))
    if (pb := fund.get("pb")) and pb > 0:
        yields.append(1.0 / float(pb))
    out["value"] = float(np.mean(yields)) if yields else None
    if any(k in fund and fund[k] is not None for k in ("roe_pct", "earnings_growth_pct", "debt_to_equity")):
        out["quality"] = float(fund.get("roe_pct") or 0.0) + 0.5 * float(fund.get("earnings_growth_pct") or 0.0) - 10.0 * float(fund.get("debt_to_equity") or 0.0)
    return out


def zscores(values: Dict[str, Optional[float]], winsor: float = WINSOR) -> Dict[str, Optional[float]]:
    """Cross-sectional z-scores; None stays None; a universe with no spread scores everyone 0."""
    present = {k: float(v) for k, v in values.items() if v is not None and math.isfinite(float(v))}
    if len(present) < 2:
        return {k: (0.0 if k in present else None) for k in values}
    arr = np.array(list(present.values()))
    mean, std = float(arr.mean()), float(arr.std(ddof=1))
    out: Dict[str, Optional[float]] = {}
    for k in values:
        if k not in present:
            out[k] = None
        elif std <= 1e-12:
            out[k] = 0.0
        else:
            out[k] = float(max(-winsor, min(winsor, (present[k] - mean) / std)))
    return out


def normalise_weights(weights: Optional[Dict[str, float]]) -> Dict[str, float]:
    chosen = {f: float(w) for f, w in (weights or DEFAULT_WEIGHTS).items() if f in FACTORS and w is not None and float(w) >= 0}
    total = sum(chosen.values())
    if total <= 0:
        raise ValueError("factor weights must include at least one positive weight")
    return {f: round(w / total, 6) for f, w in chosen.items() if w > 0}


def score_universe(inputs: Sequence[FactorInputs], weights: Optional[Dict[str, float]] = None, **lookbacks) -> FactorTable:
    weights_used = normalise_weights(weights)
    warnings: List[str] = []
    rows: List[FactorRow] = []
    bpy = max((bars_per_year_of(i.df) for i in inputs if len(i.df) >= 3), default=250.0)
    for item in inputs:
        if item.df is None or item.df.empty:
            warnings.append(f"{item.symbol}: no candles")
            continue
        raw = raw_factors(item.df, item.fundamentals, bars_per_year=bpy, **lookbacks)
        rows.append(FactorRow(symbol=item.symbol, close=float(item.df["close"].iloc[-1]), bars=len(item.df), raw=raw))
    if not rows:
        return FactorTable(rows=[], weights=weights_used, bars_per_year=bpy, warnings=warnings + ["empty universe"])
    for factor in FACTORS:
        z = zscores({r.symbol: r.raw[factor] for r in rows})
        for r in rows:
            r.z[factor] = z[r.symbol]
    missing_everywhere = [f for f in weights_used if all(r.raw[f] is None for r in rows)]
    if missing_everywhere:
        warnings.append("no data for " + ", ".join(missing_everywhere) + " - those weights were redistributed")
    for r in rows:
        available = {f: w for f, w in weights_used.items() if r.z.get(f) is not None}
        total = sum(available.values())
        r.composite = round(sum(w * r.z[f] for f, w in available.items()) / total, 4) if total > 0 else None
    ranked = sorted([r for r in rows if r.composite is not None], key=lambda r: -r.composite)
    for i, r in enumerate(ranked, start=1):
        r.rank = i
    k = max(1, int(round(len(ranked) * BUCKET_FRACTION))) if len(ranked) >= 3 else 0
    for r in ranked[:k]:
        r.bucket = "LONG"
    for r in ranked[len(ranked) - k:] if k else []:
        r.bucket = "SHORT" if r.bucket != "LONG" else r.bucket
    if len(ranked) < 3:
        warnings.append("fewer than three symbols scored - no long/short buckets")
    rows.sort(key=lambda r: (r.rank is None, r.rank or 0))
    return FactorTable(rows=rows, weights=weights_used, bars_per_year=bpy, warnings=warnings)


def exposure(weights: Dict[str, float], table: FactorTable) -> Dict[str, Optional[float]]:
    """Weighted factor tilt of a book: sum of weight x z per factor over the symbols that have
    a score. Weights are signed (short = negative) and taken as given."""
    out: Dict[str, Optional[float]] = {}
    for factor in FACTORS:
        total, seen = 0.0, False
        for symbol, w in weights.items():
            z = table.z_of(symbol).get(factor)
            if z is not None:
                total += float(w) * z
                seen = True
        out[factor] = round(total, 4) if seen else None
    return out
