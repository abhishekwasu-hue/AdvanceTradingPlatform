from typing import List

import pandas as pd

from app.price_action.models import SwingPoint


def find_swings(df: pd.DataFrame, window: int = 3) -> List[SwingPoint]:
    """Fractal swing detection: bar i is a swing high/low if it's the extreme of the 2*window+1
    bars centered on it (ties allowed - `alternate_swings` collapses any resulting plateau down
    to one point). A bar can register as both kinds at once (rare, e.g. a doji at a sharp turn).
    """
    highs, lows = df["high"], df["low"]
    swings: List[SwingPoint] = []
    n = len(df)

    for i in range(window, n - window):
        high_window = highs.iloc[i - window : i + window + 1]
        if highs.iloc[i] == high_window.max():
            swings.append(SwingPoint(timestamp=df.index[i], price=float(highs.iloc[i]), kind="HIGH"))

        low_window = lows.iloc[i - window : i + window + 1]
        if lows.iloc[i] == low_window.min():
            swings.append(SwingPoint(timestamp=df.index[i], price=float(lows.iloc[i]), kind="LOW"))

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
