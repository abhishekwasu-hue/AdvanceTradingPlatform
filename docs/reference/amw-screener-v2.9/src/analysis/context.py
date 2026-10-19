"""Per-timeframe analysis context consumed by all strategies (no strategy logic here)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .candles import detect_candles
from .indicators import add_indicators
from .patterns import detect_patterns
from .structure import (Swing, compression_state, find_swings, gaps, liquidity, sr_levels, structure_bias,
                        structure_events, zones)


@dataclass
class TFAnalysis:
    tf: str
    df: pd.DataFrame
    trend_score: float | None
    trend_label: str
    structure_trend: str | None
    structure_bias: float | None
    swings: list[Swing]
    events: list
    candles: list
    patterns: list
    zones: list
    sr: list
    liquidity: dict
    compression: str | None
    last: dict = field(default_factory=dict)
    gaps: list = field(default_factory=list)

    @property
    def atr(self) -> float | None:
        v = self.df.atr14.iloc[-1]
        return float(v) if np.isfinite(v) and v > 0 else None

    @property
    def close(self) -> float:
        return float(self.df.Close.iloc[-1])

    @property
    def n(self) -> int:
        return len(self.df)

    def time(self, i: int = -1) -> str:
        return str(self.df.index[i])[:16]

    def highs(self) -> list[Swing]:
        return [s for s in self.swings if s.kind == "H"]

    def lows(self) -> list[Swing]:
        return [s for s in self.swings if s.kind == "L"]


def ema_alignment(row: pd.Series) -> float | None:
    c, e20, e50, e200 = row.Close, row.ema20, row.ema50, row.ema200
    if not (np.isfinite(e20) and np.isfinite(e50)):
        return None
    parts = [np.sign(c - e20), np.sign(e20 - e50)]
    if np.isfinite(e200):
        parts.append(np.sign(e50 - e200))
    return float(np.mean(parts))


def analyze_tf(raw: pd.DataFrame, tf: str, cfg: dict) -> TFAnalysis:
    a = cfg["analysis"]
    df = add_indicators(raw, tf)
    n = len(df)
    sw = [s for s in find_swings(df, int(a["swing_k"])) if s.confirmed_at <= n - 1]
    ev, st_trend = structure_events(df, sw)
    last = df.iloc[-1]
    atr_last = float(last.atr14) if np.isfinite(last.atr14) else float("nan")
    ea = ema_alignment(last)
    sb = structure_bias(sw, st_trend) if sw else None
    parts = [x for x in (ea, sb) if x is not None]
    ts = float(np.clip(np.mean(parts), -1, 1)) if parts else None
    label = "NA" if ts is None else "Bullish" if ts >= 0.5 else "Bearish" if ts <= -0.5 else "Mixed"
    t = TFAnalysis(tf=tf, df=df, trend_score=ts, trend_label=label, structure_trend=st_trend, structure_bias=sb,
                   swings=sw, events=ev, candles=detect_candles(df),
                   patterns=detect_patterns(df, sw, atr_last, a["pattern_tolerance_atr"]),
                   zones=zones(df, df.atr14, a["zone_departure_atr"]),
                   sr=sr_levels(sw, atr_last, float(last.Close), a["sr_cluster_atr"], df),
                   liquidity=liquidity(df, sw, atr_last, a["equal_level_atr"]),
                   compression=compression_state(df, a["compression_ratio"]), gaps=gaps(df))
    keys = ("Close", "supertrend", "st_dir", "rsi14", "rvol", "atr14", "atr_pct", "adx", "plus_di", "minus_di", "macd_hist", "ema20", "ema50",
            "ema200", "bb_width", "vwap", "Volume")
    t.last = {k: (float(last[k]) if pd.notna(last[k]) else None) for k in keys}
    t.last["time"] = t.time()
    return t


def rsi_divergence(t: TFAnalysis, direction: str, max_age: int = 15) -> str | None:
    """Regular divergence between the last two CONFIRMED swing lows (bullish) / highs (bearish)."""
    r = t.df.rsi14
    sw = (t.lows() if direction == "bullish" else t.highs())[-2:]
    if len(sw) < 2 or sw[1].i < t.n - max_age or not (np.isfinite(r.iloc[sw[0].i]) and np.isfinite(r.iloc[sw[1].i])):
        return None
    if direction == "bullish" and sw[1].price < sw[0].price and r.iloc[sw[1].i] > r.iloc[sw[0].i]:
        return f"bullish RSI divergence: low {sw[0].price:.2f}->{sw[1].price:.2f}, RSI {r.iloc[sw[0].i]:.1f}->{r.iloc[sw[1].i]:.1f}"
    if direction == "bearish" and sw[1].price > sw[0].price and r.iloc[sw[1].i] < r.iloc[sw[0].i]:
        return f"bearish RSI divergence: high {sw[0].price:.2f}->{sw[1].price:.2f}, RSI {r.iloc[sw[0].i]:.1f}->{r.iloc[sw[1].i]:.1f}"
    return None
