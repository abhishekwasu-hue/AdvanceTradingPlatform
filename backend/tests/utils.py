from typing import List

import numpy as np
import pandas as pd


def make_series(prices: List[float], start: str = "2024-01-02 09:15", freq: str = "1min") -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=len(prices), freq=freq)
    closes = pd.Series(prices, index=idx, dtype=float)
    opens = closes.shift(1).fillna(closes.iloc[0])
    highs = pd.concat([opens, closes], axis=1).max(axis=1) + 0.05
    lows = pd.concat([opens, closes], axis=1).min(axis=1) - 0.05
    volume = pd.Series(1000.0, index=idx)
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume})


def decline_then_rally(decline_len: int = 40, rally_len: int = 20, start: float = 100.0) -> List[float]:
    decline = list(np.linspace(start, start * 0.94, decline_len))
    rally = list(np.linspace(start * 0.94, start * 1.25, rally_len))
    return decline + rally


def rally_then_decline(rally_len: int = 40, decline_len: int = 20, start: float = 100.0) -> List[float]:
    rally = list(np.linspace(start, start * 1.06, rally_len))
    decline = list(np.linspace(start * 1.06, start * 0.75, decline_len))
    return rally + decline


def noisy_uptrend(
    n: int = 400, start: float = 100.0, seed: int = 7, amplitude: float = 1.5, period: int = 40
) -> List[float]:
    """A rising trend with a periodic oscillation layered on top, so price repeatedly pulls back
    toward its short-term moving average (as it would around support in a real pullback trend)
    instead of climbing in one smooth, un-tradeable line.
    """
    rng = np.random.default_rng(seed)
    idx = np.arange(n)
    drift = np.linspace(0, 25, n)
    wave = amplitude * np.sin(2 * np.pi * idx / period)
    noise = rng.normal(0, 0.15, n)
    return list(start + drift + wave + noise)
