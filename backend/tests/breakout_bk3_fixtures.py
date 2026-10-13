"""U6 BK3 fixtures: deterministic daily bars (no randomness) for the VCP, squeeze, moving-average and regime breakouts.

Every series is built from straight legs (a close path) with a fixed half-range around each close, so the swings, the
ATR and the indicators are exactly known. Index: weekdays from 2025-01-01, 03:45 UTC (09:15 IST), like the other
breakout fixtures. The leading uptrend is a zigzag (up 20 bars, down 12) so the weekly Dow trend reads UP."""
from typing import List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

HALF = 0.5                                # high = close + HALF, low = close - HALF (plus the gap to the previous close)


def path(start: float, legs: Sequence[Tuple[int, float]]) -> List[float]:
    """Closes along straight legs: (bars, change over the leg)."""
    out = [start]
    for bars, change in legs:
        step = change / bars
        out += [out[-1] + step * (i + 1) for i in range(bars)]
    return out


def frame(closes: Sequence[float], volumes: Optional[Sequence[float]] = None, half: float = HALF) -> pd.DataFrame:
    c = np.asarray(closes, dtype=float)
    o = np.r_[c[0], c[:-1]]
    v = np.full(len(c), 1000.0) if volumes is None else np.asarray(volumes, dtype=float)
    idx = pd.bdate_range("2025-01-01", periods=len(c), tz="UTC") + pd.Timedelta(hours=3, minutes=45)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + half, "low": np.minimum(o, c) - half, "close": c, "volume": v}, index=idx)


def uptrend(start: float = 30.0, cycles: int = 6) -> List[Tuple[int, float]]:
    """Zigzag advance: +30 over 20 bars, -18 over 12 (net +12 a cycle), then a final push of 20 bars."""
    return [leg for _ in range(cycles) for leg in ((20, 30.0), (12, -18.0))] + [(20, 30.0)]


def vcp(contractions: Sequence[float] = (16.0, 8.0, 4.0), break_bar: bool = True, break_volume: float = 3000.0,
        dry: bool = True, trend: bool = True, wick_only: bool = False) -> pd.DataFrame:
    """A trend (or, trend=False, a sideways stretch), then a base of contractions (each a % drop from its high, rallies back to just under the previous high),
    then the last bar: a close 2 above the pivot on `break_volume` (or, wick_only, a high above it with the close under)."""
    legs = uptrend() if trend else [(20, 6.0), (12, -6.0)] * 6 + [(20, 6.0)]       # no trend: a sideways zigzag
    start = 30.0 if trend else 100.0
    closes = path(start, legs)
    vols = [1000.0] * len(closes)
    top = closes[-1]
    level_vol = [1200.0, 800.0, 500.0, 400.0, 300.0] if dry else [500.0, 800.0, 1200.0, 1400.0, 1500.0]
    for i, depth in enumerate(contractions):
        low = top * (1 - depth / 100.0)
        down = max(5, int(round(depth)))
        closes = closes + path(closes[-1], [(down, low - closes[-1])])[1:]
        vols += [level_vol[min(i, len(level_vol) - 1)]] * down
        nxt = top - 0.5 if i < len(contractions) - 1 else top - 0.6
        up = max(5, int(round(depth)))
        closes = closes + path(closes[-1], [(up, nxt - closes[-1])])[1:]
        vols += [level_vol[min(i, len(level_vol) - 1)] * 0.9] * up
        top = nxt
    if break_bar:
        pivot_high = max(closes[-up - 1:]) + HALF if contractions else top
        f = frame(closes + [top + 2.5 if not wick_only else top - 0.2], vols + [break_volume])
        if wick_only:
            f.iloc[-1, f.columns.get_loc("high")] = pivot_high + 3.0
        return f
    return frame(closes, vols)
