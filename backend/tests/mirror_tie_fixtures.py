"""TPL-F6: synthetic bars for the mirror tests of the swing structure (DowTrend, SwingBreakType) and the reversal check
(ReversalAt), and the swing labelling as it was before the fix (`legacy_dow`), so a test can show the old rule fails.

Every price is a whole number of ticks, so reflecting the bars through a whole-tick level (price -> axis - price, high and
low swapped) is exact and an equal high or low stays equal on the reflected bars. No market data: random walks
(app/quality/lookahead.synthetic_bars) and series built to hold equal swing highs and equal swing lows (a range whose tops
and bottoms are cut at one price, and the two triangles with one flat edge)."""
from __future__ import annotations

from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.quality.lookahead import synthetic_bars

KINDS: Tuple[str, ...] = ("walk", "range", "flat_top", "flat_bottom")


def whole_ticks(df: pd.DataFrame) -> pd.DataFrame:
    """The synthetic bars move on a 0.05 tick: x 20 makes every price a whole number (exact under reflection)."""
    out = df.copy()
    out[["open", "high", "low", "close"]] = (df[["open", "high", "low", "close"]] * 20).round()
    return out


def mirror(df: pd.DataFrame, axis: Optional[float] = None) -> pd.DataFrame:
    """The bars reflected through `axis` (default: the frame's highest high + lowest low, a whole number)."""
    if axis is None:
        axis = float(df["high"].max() + df["low"].min())
    out = df.copy()
    out["open"], out["close"] = axis - df["open"], axis - df["close"]
    out["high"], out["low"] = axis - df["low"], axis - df["high"]
    return out


def tie_bars(n: int, tf: str, seed: int, kind: str) -> pd.DataFrame:
    """Bars on the random walk's clock and candles whose swing extremes repeat exactly.

    range        price swings between a ceiling and a floor; every high above the ceiling is cut to it and every low
                 below the floor to it, so successive swing highs (lows) are often equal
    flat_top     the floor rises through the frame, the ceiling is flat (equal highs in a rising structure)
    flat_bottom  the ceiling falls through the frame, the floor is flat (equal lows in a falling structure)
    """
    clock = {"1h": "60m", "30m": "30m"}.get(tf, tf)        # synthetic_bars reads intraday clocks in minutes
    base = whole_ticks(synthetic_bars(n, tf=clock, seed=seed))
    if kind == "walk":
        return base
    rng = np.random.default_rng(10_000 + seed)
    t = np.arange(n, dtype=float)
    mid = 2000.0
    period = float(rng.integers(18, 60))
    amp = float(rng.integers(40, 90))
    close = mid + amp * np.sin(2 * np.pi * t / period + rng.uniform(0, 6.3)) + np.cumsum(rng.normal(0.0, 3.0, n)) * 0.2
    close = np.round(close)
    open_ = np.r_[close[0], close[:-1]]
    wick = np.round(np.abs(rng.normal(0.0, 6.0, size=(2, n))))
    high = np.maximum(open_, close) + wick[0]
    low = np.minimum(open_, close) - wick[1]
    ceiling = np.full(n, np.round(mid + 0.7 * amp))
    floor = np.full(n, np.round(mid - 0.7 * amp))
    if kind == "flat_top":
        floor = np.round(floor + (ceiling - floor - 8) * t / n)
    elif kind == "flat_bottom":
        ceiling = np.round(ceiling - (ceiling - floor - 8) * t / n)
    elif kind != "range":
        raise ValueError(f"unknown kind {kind!r}")
    high = np.minimum(high, ceiling)
    low = np.maximum(low, floor)
    open_ = np.clip(open_, low, high)
    close = np.clip(close, low, high)
    high, low = np.maximum.reduce([high, open_, close]), np.minimum.reduce([low, open_, close])
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": base["volume"].to_numpy()},
                        index=base.index)


def series(count: int, n: int, tf: str, seed0: int = 0) -> Iterator[Tuple[str, int, pd.DataFrame]]:
    """`count` deterministic series cycling through KINDS: (kind, seed, bars)."""
    for i in range(count):
        kind = KINDS[i % len(KINDS)]
        seed = seed0 + i
        yield kind, seed, tie_bars(n, tf, seed, kind)


def legacy_dow(bars, degree: int) -> np.ndarray:
    """app/screener/breakouts/screens.Bars.dow as it was before TPL-F6: an equal swing low was labelled LL (a lower low)
    while an equal swing high was labelled LH (a lower high) - a tie read as "lower" on both sides, so it did not mirror."""
    out = np.full(bars.n, None, dtype=object)
    last: Dict[str, Optional[float]] = {"H": None, "L": None}
    label: Dict[str, Optional[str]] = {"H": None, "L": None}
    piv: List = bars.pivots(degree)
    k = 0
    cur: Optional[str] = None
    for t in range(bars.n):
        while k < len(piv) and int(piv[k].confirmed_idx) <= t:
            pv = piv[k]
            prev = last[pv.kind]
            if prev is not None:
                label[pv.kind] = ("HH" if pv.price > prev else "LH") if pv.kind == "H" else ("HL" if pv.price > prev else "LL")
            last[pv.kind] = pv.price
            if label["H"] and label["L"]:
                cur = "UP" if (label["H"], label["L"]) == ("HH", "HL") else "DOWN" if (label["H"], label["L"]) == ("LH", "LL") else "RANGE"
            k += 1
        out[t] = cur
    from app.screener.breakouts.screens import DOW_MIN_BARS
    warm = int(bars.p["dow_min_bars"]) if degree == 0 else DOW_MIN_BARS[degree]
    out[:min(bars.n, warm)] = None
    return out


def legacy_swing_label(f: pd.DataFrame, kind: str, degree: int) -> pd.Series:
    """app/screener/runtime._swing_label as it was before TPL-F6 (an equal swing low read LL)."""
    from app.screener.runtime import _swing_pivots
    k = "H" if kind == "high" else "L"
    out = np.full(len(f), None, dtype=object)
    prev: Optional[float] = None
    for p in _swing_pivots(f, degree):
        if p.kind != k:
            continue
        if prev is not None:
            higher = p.price > prev
            out[int(p.confirmed_idx)] = ("HH" if higher else "LH") if k == "H" else ("HL" if higher else "LL")
        prev = p.price
    return pd.Series(out, index=f.index, dtype=object).ffill()
