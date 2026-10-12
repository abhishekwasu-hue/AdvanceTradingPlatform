"""Z7a-3: the band-selection cases shared by the backend (app/price_action/zones/chart.py) and the browser port
(frontend/src/charting/marketZones.ts, which reads its byte-identical copy frontend/src/charting/__fixtures__/). Seeded,
synthetic zones only.

The expected bands in tests/fixtures/zones/chart_bands_cases.json come from the Python rule; tests/test_zones_chart_screenql.py
checks Python against the file and marketZones.test.ts checks TypeScript against it, so the two can never drift.

Rebuild after a deliberate rule change (from backend/): python -m tests.zones_chart_cases --write
"""
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List

from app.price_action.zones import chart
from app.price_action.zones.snapshot import is_line

PATH = Path(__file__).parent / "fixtures" / "zones" / "chart_bands_cases.json"
# The browser's copy, inside the frontend build context (the frontend image type-checks its tests and cannot reach
# backend/). Byte-identical to PATH - tests/test_zones_chart_screenql.py checks it; --write writes both.
FRONTEND_PATH = Path(__file__).resolve().parents[2] / "frontend" / "src" / "charting" / "__fixtures__" / "chart_bands_cases.json"
TYPES = ["DEMAND_ZONE", "SUPPLY_ZONE", "BULLISH_OB", "BEARISH_OB", "UP_GAP", "DOWN_GAP", "SUPPORT", "RESISTANCE",
         "DYNAMIC_SR_SUPPORT", "DYNAMIC_SR_RESISTANCE"]
TFS = ["5m", "15m", "30m", "1h", "1d"]
STATES = ["fresh", "tested", "flipped"]


def _row(key: str, zone_type: str, tf: str, low: float, high: float, state: str, touches: int, strength: Any = None,
         remembered: bool = False) -> Dict[str, Any]:
    return {"zone_key": key, "zone_type": zone_type, "timeframe": tf, "zone_low": low, "zone_high": high, "state": state,
            "touches": touches, "strength": strength, "remembered": remembered}


def random_rows(rng: random.Random, price: float, n: int) -> List[Dict[str, Any]]:
    rows = []
    for i in range(n):
        zt = rng.choice(TYPES)
        line = is_line(zt)
        center = round(price * (1 + rng.uniform(-0.12, 0.12)), 2)
        width = 0.0 if line else round(price * rng.uniform(0.001, 0.012), 2)
        low = round(center - width / 2, 2)
        high = round(low + width, 2)
        rows.append(_row(f"z{i:03d}|{zt}|{rng.choice(TFS)}", zt, rng.choice(TFS), low, high, rng.choice(STATES), rng.randint(0, 6),
                         rng.randint(1, 7) if line else None, rng.random() < 0.3))
    return rows


def hand_cases() -> List[Dict[str, Any]]:
    """Edge shapes: overlapping clusters, one side short, ties, price inside a band, everything too far."""
    p = 1000.0
    overlap = [_row("a", "DEMAND_ZONE", "1h", 990.0, 995.0, "fresh", 0), _row("b", "BULLISH_OB", "15m", 992.0, 997.0, "tested", 3),
               _row("c", "SUPPORT", "1h", 994.0, 994.0, "fresh", 1, 4), _row("d", "SUPPLY_ZONE", "1h", 1004.0, 1008.0, "fresh", 0),
               _row("e", "BEARISH_OB", "1d", 1006.0, 1010.0, "flipped", 2)]
    one_side = [_row(f"s{i}", "SUPPLY_ZONE", "1h", 1001.0 + 5 * i, 1003.0 + 5 * i, "fresh", 0) for i in range(8)] + \
               [_row("d0", "DEMAND_ZONE", "1h", 980.0, 985.0, "tested", 1)]
    ties = [_row(k, "DYNAMIC_SR_SUPPORT", "5m", 990.0, 990.0, "fresh", 0, 2) for k in ("t2", "t1", "t3")] + \
           [_row(k, "DYNAMIC_SR_RESISTANCE", "5m", 1010.0, 1010.0, "fresh", 0, 2) for k in ("u1", "u2")]
    inside = [_row("in", "DEMAND_ZONE", "1h", 995.0, 1003.0, "fresh", 0), _row("in2", "SUPPLY_ZONE", "1h", 999.0, 1009.0, "tested", 2)]
    far = [_row("far1", "DEMAND_ZONE", "1d", 700.0, 720.0, "fresh", 0), _row("far2", "SUPPLY_ZONE", "1d", 1300.0, 1320.0, "fresh", 0)]
    return [{"name": "overlapping clusters", "price": p, "rows": overlap},
            {"name": "one side short", "price": p, "rows": one_side},
            {"name": "ties broken by zone_key", "price": p, "rows": ties},
            {"name": "price inside bands", "price": p, "rows": inside},
            {"name": "all beyond the distance cap", "price": p, "rows": far},
            {"name": "two bands at most", "price": p, "rows": one_side, "max_bands": 2},
            {"name": "no distance cap", "price": p, "rows": far, "max_distance_pct": None}]


def build() -> Dict[str, Any]:
    rng = random.Random(20261011)
    cases = hand_cases()
    for i in range(16):
        price = round(rng.uniform(80, 52000), 2)
        cases.append({"name": f"random {i}", "price": price, "rows": random_rows(rng, price, rng.randint(0, 24)),
                      **({"max_bands": rng.randint(1, 6)} if i % 5 == 4 else {})})
    out = []
    for case in cases:
        cands = chart.candidates(case["rows"])
        kwargs = {"max_bands": case.get("max_bands", chart.MAX_BANDS)}
        if "max_distance_pct" in case:
            kwargs["max_distance_pct"] = case["max_distance_pct"]
        bands = chart.select_bands(cands, case["price"], **kwargs)
        out.append({"name": case["name"], "price": case["price"], "max_bands": kwargs["max_bands"],
                    "max_distance_pct": kwargs.get("max_distance_pct", chart.MAX_DISTANCE_PCT), "candidates": cands,
                    "expected": [{"zone_key": b["zone_key"], "role": b["role"], "label": b["label"], "touch_text": b["touch_text"]} for b in bands]})
    return {"rule": "app/price_action/zones/chart.py select_bands", "per_role": chart.PER_ROLE, "cases": out}


def dump(data: Dict[str, Any]) -> str:
    """One case per line (a small, reviewable file - not a 10,000-line golden)."""
    head = {k: v for k, v in data.items() if k != "cases"}
    lines = [json.dumps(c, sort_keys=True, separators=(",", ":")) for c in data["cases"]]
    return json.dumps(head, sort_keys=True)[:-1] + ', "cases": [\n' + ",\n".join(lines) + "\n]}\n"


if __name__ == "__main__":
    if "--write" in sys.argv:
        text = dump(build())
        for path in (PATH, FRONTEND_PATH):
            path.write_text(text)
            print(f"wrote {path}")
    else:
        print(dump(build())[:2000])
