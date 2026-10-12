import warnings
from typing import List, Tuple

import numpy as np
import pandas as pd

from app.price_action.models import SwingPoint


def fractal_positions(df: pd.DataFrame, window: int = 3) -> List[Tuple[int, str]]:
    """The positional core of `find_swings`: (bar index, "HIGH" / "LOW") in bar order, a swing high before a swing low
    on the same bar. Bar i is a swing high / low if it is the extreme of the 2*window+1 bars centred on it (ties
    allowed). A NaN in the window is skipped (pandas max / min) and a NaN bar is never a swing, so the swing list and
    the market-structure events (which use this too) always agree."""
    highs, lows = df["high"].to_numpy(float), df["low"].to_numpy(float)
    out: List[Tuple[int, str]] = []
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)                  # an all-NaN window: no swing
        for i in range(window, len(df) - window):
            if highs[i] == np.nanmax(highs[i - window:i + window + 1]):
                out.append((i, "HIGH"))
            if lows[i] == np.nanmin(lows[i - window:i + window + 1]):
                out.append((i, "LOW"))
    return out


def find_swings(df: pd.DataFrame, window: int = 3) -> List[SwingPoint]:
    """Fractal swing detection: bar i is a swing high/low if it's the extreme of the 2*window+1
    bars centered on it (ties allowed - `alternate_swings` collapses any resulting plateau down
    to one point). A bar can register as both kinds at once (rare, e.g. a doji at a sharp turn).
    """
    highs, lows = df["high"], df["low"]
    swings = [SwingPoint(timestamp=df.index[i], price=float((highs if kind == "HIGH" else lows).iloc[i]), kind=kind)  # type: ignore[arg-type]
              for i, kind in fractal_positions(df, window)]
    swings.sort(key=lambda s: s.timestamp)
    return swings


def alternate_swings(swings: List[SwingPoint]) -> List[SwingPoint]:
    """Collapses consecutive same-kind swings down to the most extreme one, so the
    resulting sequence strictly alternates HIGH/LOW as real market structure requires.
    """
    if not swings:
        return []

    result = [swings[0]]
    for swing in swings[1:]:
        if swing.kind == result[-1].kind:
            is_more_extreme = (
                swing.kind == "HIGH" and swing.price > result[-1].price
            ) or (swing.kind == "LOW" and swing.price < result[-1].price)
            if is_more_extreme:
                result[-1] = swing
        else:
            result.append(swing)
    return result


def swing_indices(df: pd.DataFrame, window: int = 3) -> Tuple[List[int], List[int]]:
    """The positional form of `find_swings` (Trade signals.find_swings): (swing-high bar indices, swing-low bar indices).
    Bar i is a swing high / low if it is the max / min of the 2*window+1 bars centred on it (ties allowed). A bar can
    be both. Fewer than 2*window+1 bars -> no swings."""
    n = len(df)
    if n < window * 2 + 1:
        return [], []
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    swing_high_idx: List[int] = []
    swing_low_idx: List[int] = []
    for i in range(window, n - window):
        if highs[i] == highs[i - window:i + window + 1].max():
            swing_high_idx.append(i)
        if lows[i] == lows[i - window:i + window + 1].min():
            swing_low_idx.append(i)
    return swing_high_idx, swing_low_idx
