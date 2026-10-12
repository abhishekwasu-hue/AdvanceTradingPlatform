"""The market-zones chart layer (Z7a-3, CH3 / U8 minimal style) - pure functions over the rows of one stored snapshot.

No database, no clock, no chart library: the same code feeds the chart read API (`routes.py`), the ScreenQL zone
functions (`app/screener/zones.py`) and, next, the U8 evidence charts drawn server-side (Telegram images). The browser
has a line-for-line port (`frontend/src/charting/marketZones.ts`) so a chart can re-pick its bands when the reference
price moves; both are checked against the same cases (`tests/fixtures/zones/chart_bands_cases.json`).

  * `role_at(row, price)`: "demand" when the zone's mid is at or below the price, else "supply" - Trade's
    compute_current_role (whatever the zone's origin, its role comes from where price is now).
  * `distance_pct(row, price)`: 0 inside the band, else the gap to the nearer edge as % of the price.
  * `zone_strength(row)`: a 0-100 score from the stored fields only (family, timeframe, lifecycle state, touches,
    remembered) - provisional weights, docs/OPEN_QUESTIONS.md Z7a3-2.
  * `candidates(rows)`: the live rows (any state but invalidated) as chart candidates with their strength and the
    price texts (every number a chart shows comes from here, never from pixels).
  * `select_bands(cands, price)` (`bands_from_rows(rows, price)` from snapshot rows): at most 6 bands - per role, the strongest of each overlapping cluster, then the
    nearest to the price: 3 demand + 3 supply, a short side's slots going to the other side. Top to bottom by price.
  * labels: "demand 512.30 (fresh)", "supply 530.00 (tested)" and the touch count ("2 touches").
  * `nearest_zone(...)`: the live zone nearest to a price within a tolerance - what NearZone / ZoneRole / ZoneState /
    ZoneStrength read on each bar.
"""
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from app.price_action.zones.snapshot import family_of, is_line

DEMAND, SUPPLY = "demand", "supply"
ROLES = (DEMAND, SUPPLY)
LIVE_STATES = ("fresh", "tested", "flipped")
FAMILIES = ("all", "zones", "levels")             # zones: order blocks, demand / supply bases, gaps; levels: S/R lines

MAX_BANDS = 6
PER_ROLE = 3
MAX_DISTANCE_PCT = 10.0                           # provisional (OPEN_QUESTIONS Z7a3-1): further bands squash the price axis

# zone_strength weights (provisional, OPEN_QUESTIONS Z7a3-2)
FAMILY_BASE: Dict[str, int] = {"DEMAND_ZONE": 50, "SUPPLY_ZONE": 50, "BULLISH_OB": 45, "BEARISH_OB": 45, "UP_GAP": 35, "DOWN_GAP": 35,
                               "SR": 40, "DYNAMIC_SR": 35}
TF_BONUS: Dict[str, int] = {"1m": 0, "3m": 0, "5m": 2, "15m": 5, "30m": 7, "1h": 10, "4h": 12, "1d": 15, "1w": 20}
STATE_BONUS: Dict[str, int] = {"fresh": 15, "tested": 0, "flipped": 10}
BAND_TOUCH_PENALTY, BAND_PENALTY_CAP = 5, 20      # a band absorbs orders each time price comes back
LINE_SWING_POINTS, LINE_SWING_CAP = 4, 5          # a line found on more swings is a stronger level
LINE_TOUCH_POINTS, LINE_TOUCH_CAP = 2, 5          # ... and one respected again since it was stored
REMEMBERED_BONUS = 5                              # carried across days by level memory


def price_text(x: float) -> str:
    return f"{float(x):.2f}"


def mid(row: Mapping[str, Any]) -> float:
    return (float(row["zone_low"]) + float(row["zone_high"])) / 2


def role_at(row: Mapping[str, Any], price: float) -> str:
    """Role by the price (Trade compute_current_role): mid above the price -> supply, otherwise demand."""
    return SUPPLY if mid(row) > price else DEMAND


def distance_pct(row: Mapping[str, Any], price: float) -> float:
    """0 when the price is inside the band (or on the line), else the gap to the nearer edge as % of the price."""
    low, high = float(row["zone_low"]), float(row["zone_high"])
    if low <= price <= high:
        return 0.0
    gap = low - price if price < low else price - high
    return gap / price * 100.0


def is_live(row: Mapping[str, Any]) -> bool:
    return str(row.get("state")) in LIVE_STATES


def in_family(row: Mapping[str, Any], family: str) -> bool:
    if family == "all":
        return True
    line = is_line(str(row["zone_type"]))
    return line if family == "levels" else not line


def zone_strength(row: Mapping[str, Any]) -> int:
    """0-100 from the stored fields only: family base + timeframe + lifecycle state, then touches (a band weakens with
    each visit, a line strengthens with the swings it was found on and the visits it held) and level memory."""
    zt = str(row["zone_type"])
    fam = family_of(zt)
    score = FAMILY_BASE.get(fam, 30) + TF_BONUS.get(str(row.get("timeframe")), 0) + STATE_BONUS.get(str(row.get("state")), 0)
    touches = int(row.get("touches") or 0)
    if is_line(zt):
        swings = int(row.get("strength") or 0)
        score += LINE_SWING_POINTS * min(swings, LINE_SWING_CAP) + LINE_TOUCH_POINTS * min(touches, LINE_TOUCH_CAP)
    else:
        score -= min(BAND_TOUCH_PENALTY * max(0, touches - 1), BAND_PENALTY_CAP)
    if row.get("remembered"):
        score += REMEMBERED_BONUS
    return int(max(0, min(100, score)))


def touch_text(touches: int) -> str:
    return "no touches" if touches <= 0 else ("1 touch" if touches == 1 else f"{touches} touches")


def candidate(row: Mapping[str, Any]) -> Dict[str, Any]:
    """One live row as a chart candidate: bounds, state, touches, strength and the price texts the labels use."""
    low, high = float(row["zone_low"]), float(row["zone_high"])
    return {"zone_key": str(row["zone_key"]), "zone_type": str(row["zone_type"]), "family": family_of(str(row["zone_type"])),
            "timeframe": str(row["timeframe"]), "kind": "line" if is_line(str(row["zone_type"])) else "band",
            "low": low, "high": high, "low_text": price_text(low), "high_text": price_text(high), "state": str(row["state"]),
            "touches": int(row.get("touches") or 0), "strength": zone_strength(row)}


def candidates(rows: Iterable[Mapping[str, Any]], family: str = "all") -> List[Dict[str, Any]]:
    """Live rows of the family as chart candidates, in a stable order (zone_key)."""
    return sorted((candidate(r) for r in rows if is_live(r) and in_family(r, family)), key=lambda c: c["zone_key"])


def _cand_row(c: Mapping[str, Any]) -> Dict[str, Any]:
    return {"zone_low": c["low"], "zone_high": c["high"]}


def label(c: Mapping[str, Any], role: str) -> str:
    """"demand 512.30 (fresh)" / "supply 530.00 (tested)": the edge price meets first - the top of a demand band, the
    bottom of a supply band (a line has one price)."""
    text = c["high_text"] if role == DEMAND else c["low_text"]
    return f"{role} {text} ({c['state']})"


def _overlaps(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return float(a["low"]) <= float(b["high"]) and float(b["low"]) <= float(a["high"])


def _by_proximity(item: Dict[str, Any]) -> tuple:
    return (item["distance_pct"], -item["strength"], item["zone_key"])


def _by_strength(item: Dict[str, Any]) -> tuple:
    return (-item["strength"], item["distance_pct"], item["zone_key"])


def select_bands(cands: Sequence[Mapping[str, Any]], price: float, max_bands: int = MAX_BANDS, per_role: int = PER_ROLE,
                 max_distance_pct: Optional[float] = MAX_DISTANCE_PCT) -> List[Dict[str, Any]]:
    """At most `max_bands` bands for a chart at `price`:
      1. role by the price, distance to the price; candidates beyond `max_distance_pct` are left out;
      2. per role, overlapping candidates are one cluster and only its strongest is kept (ties: nearer, then zone_key);
      3. per role, the nearest `per_role` (ties: stronger, then zone_key); slots a role cannot fill go to the other
         role's next nearest, up to `max_bands`;
      4. top to bottom: by the band's high, then low, descending (ties: zone_key).
    Each band: the candidate fields plus role, distance_pct, label, touch_text."""
    if price is None or not price > 0 or max_bands <= 0:
        return []
    items: List[Dict[str, Any]] = []
    for c in cands:
        d = distance_pct(_cand_row(c), price)
        if max_distance_pct is not None and d > max_distance_pct:
            continue
        role = role_at(_cand_row(c), price)
        items.append({**c, "role": role, "distance_pct": d})
    kept: Dict[str, List[Dict[str, Any]]] = {DEMAND: [], SUPPLY: []}
    for item in sorted(items, key=_by_strength):
        same = kept[item["role"]]
        if not any(_overlaps(item, k) for k in same):
            same.append(item)
    near = {role: sorted(kept[role], key=_by_proximity) for role in ROLES}
    take = {role: near[role][:per_role] for role in ROLES}
    spare = sorted(near[DEMAND][per_role:] + near[SUPPLY][per_role:], key=_by_proximity)
    chosen = take[DEMAND] + take[SUPPLY]
    chosen = sorted(chosen, key=_by_proximity)[:max_bands]
    for item in spare:
        if len(chosen) >= max_bands:
            break
        chosen.append(item)
    out = []
    for item in sorted(chosen, key=lambda i: (-i["high"], -i["low"], i["zone_key"])):
        out.append({**item, "label": label(item, item["role"]), "touch_text": touch_text(int(item["touches"]))})
    return out


def bands_from_rows(rows: Iterable[Mapping[str, Any]], price: float, family: str = "all", max_bands: int = MAX_BANDS) -> List[Dict[str, Any]]:
    """Snapshot rows straight to the bands a chart draws at `price` (what a server-side chart image calls)."""
    return select_bands(candidates(rows, family), price, max_bands=max_bands)


def nearest_zone(rows: Iterable[Mapping[str, Any]], price: float, role: str = "any", tolerance_pct: float = 0.5,
                 family: str = "all") -> Optional[Dict[str, Any]]:
    """The live zone nearest to `price` (role by that price) within `tolerance_pct` % of it, or None. Ties: the
    stronger, then zone_key. Returned as a candidate with role and distance_pct."""
    if price is None or not price > 0 or not tolerance_pct >= 0:
        return None
    best: Optional[Dict[str, Any]] = None
    for r in rows:
        if not is_live(r) or not in_family(r, family):
            continue
        d = distance_pct(r, price)
        if d > tolerance_pct:
            continue
        rl = role_at(r, price)
        if role != "any" and rl != role:
            continue
        item = {**candidate(r), "role": rl, "distance_pct": d}
        if best is None or _by_proximity(item) < _by_proximity(best):
            best = item
    return best


__all__ = ["DEMAND", "SUPPLY", "FAMILIES", "MAX_BANDS", "PER_ROLE", "MAX_DISTANCE_PCT", "role_at", "distance_pct", "zone_strength",
           "candidate", "candidates", "select_bands", "bands_from_rows", "nearest_zone", "label", "touch_text", "price_text", "is_live"]
