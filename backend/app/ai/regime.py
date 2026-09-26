"""Phase L3: the market regime engine (V4.7).

A deterministic classifier over the base-timeframe frame the worker already holds: ADX for
trend strength, the EMA(20)/EMA(50) relation and slope for direction, ATR as a share of price
against its own recent median for volatility. Five regimes: TRENDING_UP, TRENDING_DOWN, RANGING,
VOLATILE, QUIET, each with a 0-1 confidence and the numbers behind the call, so a deployment
that skips a signal says exactly why. Regimes are a *filter* on entries - they never place or
close anything - and a deployment with no `regime_filter` is unaffected.
"""
from dataclasses import asdict, dataclass, field
from typing import List, Optional

import pandas as pd

from app.indicators.directional import adx as adx_frame
from app.indicators.trend import ema
from app.indicators.volatility import atr

REGIMES = ("TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE", "QUIET")
MIN_BARS = 60


@dataclass
class Regime:
    kind: str
    confidence: float
    adx: Optional[float] = None
    ema_fast: Optional[float] = None
    ema_slow: Optional[float] = None
    ema_slope_pct: Optional[float] = None
    atr_pct: Optional[float] = None
    atr_ratio: Optional[float] = None
    bars: int = 0
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def classify_regime(df: pd.DataFrame, *, adx_period: int = 14, fast: int = 20, slow: int = 50, atr_period: int = 14,
                    trend_adx: float = 25.0, range_adx: float = 20.0, volatile_ratio: float = 1.6, quiet_ratio: float = 0.6) -> Regime:
    n = len(df)
    if n < MIN_BARS:
        return Regime("UNKNOWN", 0.0, bars=n, reasons=[f"need {MIN_BARS} bars, have {n}"])
    close = df["close"].astype(float)
    adx_val = float(adx_frame(df, adx_period)["adx"].iloc[-1])
    e_fast, e_slow = ema(close, fast), ema(close, slow)
    ef, es = float(e_fast.iloc[-1]), float(e_slow.iloc[-1])
    lookback = min(10, n - 1)
    slope_pct = (ef - float(e_fast.iloc[-1 - lookback])) / max(abs(float(e_fast.iloc[-1 - lookback])), 1e-9) * 100.0
    atr_series = atr(df, atr_period)
    atr_now = float(atr_series.iloc[-1])
    atr_pct = atr_now / max(float(close.iloc[-1]), 1e-9) * 100.0
    window = atr_series.iloc[-min(n, 200):]
    atr_median = float(window.median()) if len(window) else atr_now
    atr_ratio = atr_now / atr_median if atr_median > 0 else 1.0
    reasons: List[str] = [f"ADX {adx_val:.1f}", f"EMA{fast} {'above' if ef > es else 'below'} EMA{slow}", f"EMA{fast} slope {slope_pct:+.2f}% / {lookback} bars",
                          f"ATR {atr_pct:.2f}% of price, {atr_ratio:.2f}x its median"]

    if atr_ratio >= volatile_ratio:
        kind, conf = "VOLATILE", min(1.0, 0.5 + (atr_ratio - volatile_ratio) / volatile_ratio)
        reasons.append("volatility expansion dominates - directional reads unreliable")
    elif adx_val >= trend_adx and ef > es and slope_pct > 0:
        kind, conf = "TRENDING_UP", min(1.0, 0.5 + (adx_val - trend_adx) / 30.0)
    elif adx_val >= trend_adx and ef < es and slope_pct < 0:
        kind, conf = "TRENDING_DOWN", min(1.0, 0.5 + (adx_val - trend_adx) / 30.0)
    elif atr_ratio <= quiet_ratio and adx_val < range_adx:
        kind, conf = "QUIET", min(1.0, 0.5 + (quiet_ratio - atr_ratio))
        reasons.append("compressed range - breakout strategies wait, mean reversion has little room")
    elif adx_val < range_adx:
        kind, conf = "RANGING", min(1.0, 0.5 + (range_adx - adx_val) / range_adx)
    elif adx_val < trend_adx and ((ef > es and slope_pct > 0) or (ef < es and slope_pct < 0)):
        # ADX between the range and trend thresholds with the EMAs agreeing: a weak trend.
        kind, conf = ("TRENDING_UP" if ef > es else "TRENDING_DOWN"), 0.35
        reasons.append("weak/transitional trend - low confidence")
    else:
        # Strong ADX without EMA/slope agreement (a reversal in progress) or a transitional zone
        # with mixed reads: no directional call.
        kind, conf = "RANGING", 0.3
        reasons.append("mixed directional reads - no trend call")
    return Regime(kind, round(conf, 2), adx=round(adx_val, 2), ema_fast=round(ef, 2), ema_slow=round(es, 2), ema_slope_pct=round(slope_pct, 3),
                  atr_pct=round(atr_pct, 3), atr_ratio=round(atr_ratio, 3), bars=n, reasons=reasons)


def parse_filter(raw: Optional[str]) -> List[str]:
    return [r.strip().upper() for r in (raw or "").split(",") if r.strip()]


def validate_filter(values: List[str]) -> List[str]:
    bad = [v for v in values if v not in REGIMES]
    if bad:
        raise ValueError(f"Unknown regimes {bad}; valid: {list(REGIMES)}")
    return sorted(set(values))


def regime_blocks(regime: Regime, allowed: List[str]) -> Optional[str]:
    """None when the deployment may enter; otherwise the human-readable reason it may not."""
    if not allowed:
        return None
    if regime.kind == "UNKNOWN":
        return f"Regime unknown ({'; '.join(regime.reasons)}) - entries wait for {', '.join(allowed)}"
    if regime.kind in allowed:
        return None
    return f"Regime {regime.kind} (confidence {regime.confidence:.2f}: {'; '.join(regime.reasons[:2])}) not in {', '.join(allowed)} - entry skipped"
