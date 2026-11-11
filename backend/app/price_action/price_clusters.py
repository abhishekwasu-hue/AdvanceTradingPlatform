"""M99 B1-b: a generic price-cluster finder over labelled levels from any source (owner order M99, flag `m99_primitives`).

Input: `PriceLevel`s - a price, a plain label ("retrace 0.618 of last swing", "swing high") and the source it came from
(fib, swings, zones, anything). Output: `Cluster`s - centre (the members' mean), low / high (the outermost members),
member count, member labels and the distinct sources.

The joining rule is the one of `app.price_action.zones.levels.cluster_prices` (sorted chain clustering): in price order,
a level joins the current cluster when its gap to the cluster's LAST member is within the band; otherwise it starts a new
cluster. The band is a parameter:

* mode "pct": the gap is at most `band` % of that last member's price - exactly `cluster_prices` (pinned by a test);
* mode "atr": the gap is at most `band` x the ATR the caller passes (the ATR known at the bar).

`cluster_prices` itself is unchanged for its callers (S/R levels); this module adds the labels, the low / high and the
sources it does not keep. A cluster needs `min_members` levels (default 2: one level alone is a level, not a cluster).
Pure functions; causality is the caller's - the levels fed in must be known at the bar. Evidence only, no orders.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

PCT = "pct"
ATR = "atr"
BAND_MODES: Tuple[str, ...] = (PCT, ATR)


@dataclass(frozen=True)
class PriceLevel:
    price: float
    label: str
    source: str = ""


@dataclass(frozen=True)
class Cluster:
    centre: float
    low: float
    high: float
    count: int
    labels: Tuple[str, ...]
    sources: Tuple[str, ...]      # distinct, in order of first appearance (low to high)


def _joins(price: float, last: float, band: float, mode: str, atr: Optional[float]) -> bool:
    if mode == PCT:
        if last == 0:
            return price == last
        return abs(price - last) / abs(last) * 100 <= band
    return abs(price - last) <= band * float(atr)                         # type: ignore[arg-type]


def find_clusters(levels: Iterable[PriceLevel], band: float, mode: str = PCT, atr: Optional[float] = None,
                  min_members: int = 2) -> List[Cluster]:
    """Clusters of `levels` (low to high). Levels with a non-finite price are skipped. In "atr" mode a missing or
    non-positive ATR gives no cluster (nothing to measure the band in)."""
    if mode not in BAND_MODES:
        raise ValueError(f"band mode {mode!r} is not one of {', '.join(BAND_MODES)}")
    band = float(band)
    if not math.isfinite(band) or band < 0:
        raise ValueError(f"band {band!r} must be a finite number at or above 0")
    if min_members < 1:
        raise ValueError("min_members must be at least 1")
    if mode == ATR and (atr is None or not math.isfinite(float(atr)) or float(atr) <= 0):
        return []
    ordered = sorted((lv for lv in levels if math.isfinite(float(lv.price))), key=lambda lv: (float(lv.price), lv.label, lv.source))
    if not ordered:
        return []
    groups: List[List[PriceLevel]] = [[ordered[0]]]
    for lv in ordered[1:]:
        if _joins(float(lv.price), float(groups[-1][-1].price), band, mode, atr):
            groups[-1].append(lv)
        else:
            groups.append([lv])
    out: List[Cluster] = []
    for g in groups:
        if len(g) < min_members:
            continue
        prices = [float(lv.price) for lv in g]
        sources: List[str] = []
        for lv in g:
            if lv.source not in sources:
                sources.append(lv.source)
        out.append(Cluster(sum(prices) / len(prices), prices[0], prices[-1], len(g), tuple(lv.label for lv in g), tuple(sources)))
    return out


def nearest_cluster(clusters: Iterable[Cluster], price: float) -> Optional[Cluster]:
    """The cluster whose centre is nearest `price` (a tie: the lower centre); None when there is none."""
    best: Optional[Cluster] = None
    for c in clusters:
        if best is None or abs(c.centre - price) < abs(best.centre - price):
            best = c
    return best


__all__ = ["PriceLevel", "Cluster", "BAND_MODES", "PCT", "ATR", "find_clusters", "nearest_cluster"]
