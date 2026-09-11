from typing import List

import pandas as pd

from app.price_action.models import SwingPoint
from app.support_resistance.models import SRZone


def build_swing_zones(
    df: pd.DataFrame, swings: List[SwingPoint], timeframe: str, tolerance_pct: float = 0.15
) -> List[SRZone]:
    """Clusters nearby swing prices into zones rather than treating each swing as an exact
    price, per the platform's support/resistance-as-a-zone requirement. Strength combines
    touch count, whether volume near the zone runs above the series average, and how many
    times price pierced the zone intrabar but closed back outside it (a rejection).
    """
    avg_volume = df["volume"].mean()

    def cluster_and_score(prices: List[float], kind: str) -> List[SRZone]:
        prices = sorted(prices)
        clusters: List[List[float]] = []
        current: List[float] = []
        for price in prices:
            if not current or (price - current[-1]) / current[-1] * 100 <= tolerance_pct:
                current.append(price)
            else:
                clusters.append(current)
                current = [price]
        if current:
            clusters.append(current)

        zones = []
        for cluster in clusters:
            lower, upper = min(cluster), max(cluster)
            mid = (lower + upper) / 2
            touches = len(cluster)

            near = df[(df["low"] <= upper * 1.001) & (df["high"] >= lower * 0.999)]
            volume_confirmation = bool(len(near) and near["volume"].mean() > avg_volume)

            if kind == "SUPPORT":
                pierced = df[(df["low"] < upper) & (df["close"] > upper)]
            else:
                pierced = df[(df["high"] > lower) & (df["close"] < lower)]
            rejection_count = int(len(pierced))

            score = min(100, touches * 20 + (25 if volume_confirmation else 0) + rejection_count * 10)
            zones.append(SRZone(
                kind=kind, lower=lower, upper=upper, mid=mid, strength_score=score,
                touches=touches, volume_confirmation=volume_confirmation, rejection_count=rejection_count,
                timeframe=timeframe, source="swing",
            ))
        return zones

    lows = [s.price for s in swings if s.kind == "LOW"]
    highs = [s.price for s in swings if s.kind == "HIGH"]
    return cluster_and_score(lows, "SUPPORT") + cluster_and_score(highs, "RESISTANCE")
