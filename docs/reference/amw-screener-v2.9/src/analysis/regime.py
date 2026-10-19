"""Market regime classification from several features (never a single indicator).

Rules (all on completed bars of the analysed frame):
  trend votes : EMA alignment sign, EMA50 slope over 10 bars (> +/-0.5 ATR), structure trend (last close-based break)
  strength    : ADX14
  volatility  : ATR14/ATR50 ratio and 20-bar realised volatility percentile over the available history
Classification:
  * trending_bullish / trending_bearish : >=2 of 3 trend votes agree AND ADX >= 20
  * range_bound                          : ADX < 20 AND |EMA50 slope| < 0.5 ATR
  * transition                           : otherwise
Volatility state (reported separately): expansion if ATR ratio > 1/compression_ratio or realised-vol pct >= 80,
contraction if ATR ratio < compression_ratio or pct <= 20, else normal.
"""
from __future__ import annotations

import numpy as np

from .context import TFAnalysis, ema_alignment


def classify_regime(t: TFAnalysis | None, compression_ratio: float = 0.75) -> dict:
    if t is None or t.n < 60:
        return {"regime": "uncertain", "volatility": "NA", "evidence": ["insufficient data"], "rules": __doc__}
    df, last = t.df, t.df.iloc[-1]
    ev, votes = [], []
    ea = ema_alignment(last)
    if ea is not None:
        votes.append(np.sign(ea) if abs(ea) >= 0.5 else 0)
        ev.append(f"EMA alignment {ea:+.2f}")
    slope = (df.ema50.iloc[-1] - df.ema50.iloc[-11]) if df.ema50.iloc[-11:].notna().all() else np.nan
    A = t.atr or np.nan
    if np.isfinite(slope) and np.isfinite(A):
        votes.append(np.sign(slope) if abs(slope) > 0.5 * A else 0)
        ev.append(f"EMA50 10-bar slope {slope / A:+.2f} ATR")
    if t.structure_trend:
        votes.append(1 if t.structure_trend == "up" else -1)
        ev.append(f"structure {t.structure_trend}")
    adx = last.adx
    ev.append(f"ADX {adx:.1f}" if np.isfinite(adx) else "ADX NA")
    bull, bear = sum(v > 0 for v in votes), sum(v < 0 for v in votes)
    if np.isfinite(adx) and adx >= 20 and bull >= 2:
        regime = "trending_bullish"
    elif np.isfinite(adx) and adx >= 20 and bear >= 2:
        regime = "trending_bearish"
    elif np.isfinite(adx) and adx < 20 and np.isfinite(slope) and np.isfinite(A) and abs(slope) < 0.5 * A:
        regime = "range_bound"
    else:
        regime = "transition"
    ratio = last.atr14 / last.atr50 if np.isfinite(last.atr50) and last.atr50 > 0 else np.nan
    rv = np.log(df.Close).diff().rolling(20).std()
    pct = float((rv.dropna() <= rv.iloc[-1]).mean() * 100) if rv.notna().sum() > 40 and np.isfinite(rv.iloc[-1]) else np.nan
    if (np.isfinite(ratio) and ratio > 1 / compression_ratio) or (np.isfinite(pct) and pct >= 80):
        vol = "expansion"
    elif (np.isfinite(ratio) and ratio < compression_ratio) or (np.isfinite(pct) and pct <= 20):
        vol = "contraction"
    else:
        vol = "normal" if np.isfinite(ratio) or np.isfinite(pct) else "NA"
    ev.append(f"ATR14/ATR50 {ratio:.2f}" if np.isfinite(ratio) else "ATR ratio NA")
    ev.append(f"realised-vol percentile {pct:.0f}" if np.isfinite(pct) else "realised-vol pct NA")
    return {"regime": regime, "volatility": vol, "evidence": ev, "as_of": t.time(), "rules": __doc__}
