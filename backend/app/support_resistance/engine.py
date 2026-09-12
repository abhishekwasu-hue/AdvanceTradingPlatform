from typing import List

import pandas as pd

from app.price_action.swings import alternate_swings, find_swings
from app.support_resistance.levels import (
    fibonacci_levels,
    opening_range_zone,
    pivot_levels,
    previous_day_levels,
    previous_week_levels,
    vwap_zone,
)
from app.support_resistance.models import SRZone
from app.support_resistance.zones import build_swing_zones


class SupportResistanceEngine:
    """Combines every zone source the brief calls for: swing-point clusters, previous day/week
    high-low, the opening range, session VWAP, standard pivot points, and Fibonacci retracement
    of the most recent swing leg. Each zone keeps its own `source` tag rather than being merged
    across sources, since scoring a true confluence merge needs price-cluster logic beyond this
    pass - a zone confirmed by more than one source is visible by inspecting overlapping ranges.
    """

    def __init__(self, swing_window: int = 3, tolerance_pct: float = 0.15, opening_range_minutes: int = 15) -> None:
        self.swing_window = swing_window
        self.tolerance_pct = tolerance_pct
        self.opening_range_minutes = opening_range_minutes

    def build_zones(self, df: pd.DataFrame, timeframe: str) -> List[SRZone]:
        raw_swings = find_swings(df, self.swing_window)
        alternating = alternate_swings(raw_swings)

        zones: List[SRZone] = []
        zones += build_swing_zones(df, raw_swings, timeframe, self.tolerance_pct)
        zones += previous_day_levels(df)
        zones += previous_week_levels(df)
        zones += opening_range_zone(df, self.opening_range_minutes)
        zones += pivot_levels(df)
        zones += fibonacci_levels(df, alternating)
        zones += vwap_zone(df)

        return sorted(zones, key=lambda z: z.mid)
