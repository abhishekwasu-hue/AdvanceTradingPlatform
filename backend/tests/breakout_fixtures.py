"""U6 BK1: synthetic bar fixtures for the breakout screens (spec §7). Written out bar by bar - no randomness - so each
test says exactly what the market did: a trend to set ATR and swings, a base, then the break (or the fake)."""
from typing import List, Sequence, Tuple

import numpy as np
import pandas as pd

Bar = Tuple[float, float, float, float, float]          # open, high, low, close, volume


def zigzag(points: Sequence[float], per_leg: int, spread: float = 0.5, volume: float = 1000.0) -> List[Bar]:
    """Bars walking straight between the pivot prices, `per_leg` bars a leg; each bar opens at the last close."""
    closes: List[float] = [float(points[0])]
    for a, b in zip(points, points[1:]):
        closes += list(np.linspace(a, b, per_leg + 1)[1:])
    out: List[Bar] = []
    prev = closes[0]
    for c in closes:
        out.append((prev, max(prev, c) + spread, min(prev, c) - spread, c, volume))
        prev = c
    return out


def box(n: int, low: float, high: float, volume: float = 1000.0) -> List[Bar]:
    """A tight range: closes alternate a quarter in from each edge; every high is `high`, every low `low`."""
    q = (high - low) / 4.0
    out: List[Bar] = []
    for i in range(n):
        o, c = (low + q, high - q) if i % 2 == 0 else (high - q, low + q)
        out.append((o, high, low, c, volume))
    return out


def frame(bars: Sequence[Bar], start: str = "2025-01-06 03:45", freq: str = "B") -> pd.DataFrame:
    """Bars on business days (the default) or any pandas frequency, UTC-stamped at the bar start."""
    idx = pd.date_range(start, periods=len(bars), freq=freq, tz="UTC")
    return pd.DataFrame(list(bars), columns=["open", "high", "low", "close", "volume"], index=idx).astype(float)


UPTREND = [60, 75, 66, 82, 73, 90, 81, 98, 90, 101]       # HH / HL: the daily Dow trend reads UP at the end


def base_then(break_bars: Sequence[Bar], box_n: int = 25) -> pd.DataFrame:
    """An uptrend into a 100-102 box of `box_n` bars, then `break_bars`."""
    return frame(zigzag(UPTREND, 5) + box(box_n, 100.0, 102.0) + list(break_bars))


TRUE_BREAK: Bar = (101.5, 104.0, 101.2, 103.8, 3000.0)    # closes 1.8 above the 102 box top (about 0.8 ATR) on 3x volume
WICK_ONLY: Bar = (101.5, 104.0, 101.2, 101.6, 3000.0)     # same wick, close back inside the box
LOW_VOLUME_BREAK: Bar = (101.5, 104.0, 101.2, 103.8, 1000.0)


def context_frame(points: Sequence[float], end_before: pd.Timestamp, freq: str, per_leg: int = 4) -> pd.DataFrame:
    """A higher-timeframe zigzag (weekly 'W-MON' or monthly 'MS' bars) that closes before `end_before`."""
    bars = zigzag(points, per_leg, spread=1.0)
    idx = pd.date_range(end=end_before - pd.Timedelta(days=62), periods=len(bars), freq=freq, tz="UTC")
    return pd.DataFrame(bars, columns=["open", "high", "low", "close", "volume"], index=idx).astype(float)


HTF_DOWN = [200, 180, 190, 170, 180, 160, 170, 150, 160, 140, 150, 130, 140]
HTF_UP = [100, 120, 110, 130, 120, 140, 130, 150, 140, 160, 150, 170, 160]
