"""N1 X1 (OPEN_QUESTIONS N1-4): the gate's candidate areas - the organisation's live Z7a market zones for the symbol, as
known at the time (`zones.read.read_as_of`: the tenant's scope rule, never another tenant's, no platform fallback - the
ScreenQL path). A zone is only an AREA (low-high), never a signal. A line zone (a level, no width) is widened to
`LEVEL_WIDTH` of its price, Trade's replay width (level +/- 0.05 % / 2). No snapshot (the zones flag off, or not yet
computed) -> no areas -> no entry."""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

LEVEL_WIDTH = 0.0005                  # a level's area: 0.05 % of its price, centred on it
MAX_AREAS = 40                        # the strongest first (the snapshot's own order), nearest-first is the gate's job

Area = Tuple[float, float]


def area_of(low: float, high: float) -> Optional[Area]:
    lo, hi = (float(low), float(high)) if low <= high else (float(high), float(low))
    if not (hi > 0 and lo > 0):
        return None
    mid = (lo + hi) / 2.0
    if hi - lo < mid * LEVEL_WIDTH:
        half = mid * LEVEL_WIDTH / 2.0
        lo, hi = mid - half, mid + half
    return round(lo, 2), round(hi, 2)


async def areas_for(session: AsyncSession, key: Optional[str], tenant_id: int, at: Any) -> List[Area]:
    """The live zones of `key` as known at `at` (timezone-aware) for the organisation, as distinct areas."""
    if not key:
        return []
    from app.price_action.zones import read
    snap = await read.read_as_of(session, key, at, tenant_id)
    if snap is None:
        return []
    from app.price_action.zones.chart import zone_strength
    rows = sorted(snap.rows, key=lambda r: (-zone_strength(r), str(r.get("zone_key"))))
    out: List[Area] = []
    for row in rows:
        area = area_of(row["zone_low"], row["zone_high"])
        if area is not None and area not in out:
            out.append(area)
        if len(out) >= MAX_AREAS:
            break
    return out
