"""M97 PR-4 proof fixture: the real detector (app/price_action/sd_zones.py, reference settings) on hand-built bars that
truly form each formation (the bar builders of tests/test_m97_sd_zones.py), dumped as the e2e mocks' candles and
sd-zones answer - so the drawn zone, the highlighted candles and the tooltip numbers are the detector's on the bars shown.

Made-up bars only (no market data). Run from backend/:
    python -m scripts.m97_proof_fixture   ->   ../frontend/e2e/fixtures/sd-zones-proof.json
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.charts import sd_zones_layer as S
from app.price_action import sd_zones as sd
from tests.test_m97_sd_zones import AWAY, BASE, LEG_DOWN, OUT_UP, _frame, _mirror, _rbr

OUT = Path(__file__).resolve().parents[2] / "frontend" / "e2e" / "fixtures" / "sd-zones-proof.json"
# after the leg-out: candles wholly away from the zone (the zone stays fresh)
AFTER = [AWAY, (101.5, 102.3, 101.2, 102.1), (102.1, 102.6, 101.6, 101.8), (101.8, 102.4, 101.4, 102.2), AWAY, (101.5, 102.0, 100.9, 101.2)]
FORMATIONS = {
    "DBR": [LEG_DOWN, *BASE, OUT_UP, *AFTER],
    "RBR": [*_rbr(), *AFTER],
    "RBD": _mirror([LEG_DOWN, *BASE, OUT_UP, *AFTER]),
    "DBD": _mirror([*_rbr(), *AFTER]),
}
# the e2e clock: Monday 19 Oct 2026 11:00 IST; bars are 15 minute NSE session bars ending before it
NOW = pd.Timestamp("2026-10-19 11:00", tz="Asia/Kolkata")


def session_starts(n: int) -> pd.DatetimeIndex:
    """The `n` newest closed 15-minute bar starts (09:15-15:30 IST, weekdays) before NOW, oldest first."""
    out: list[pd.Timestamp] = []
    day = NOW.normalize()
    while len(out) < n:
        if day.weekday() < 5:
            t = day + pd.Timedelta(hours=15, minutes=15)
            while t >= day + pd.Timedelta(hours=9, minutes=15) and len(out) < n:
                if t + pd.Timedelta(minutes=15) <= NOW:
                    out.append(t)
                t -= pd.Timedelta(minutes=15)
        day -= pd.Timedelta(days=1)
    return pd.DatetimeIndex(sorted(out)).tz_convert("UTC")


def scaled(df: pd.DataFrame) -> pd.DataFrame:
    """Prices around 24,000 (x 240): every ratio the detector reads (ranges, bodies, averages) is unchanged."""
    out = df.copy()
    for c in ("open", "high", "low", "close"):
        out[c] = (out[c] * 240).round(2)
    return out


def build() -> dict:
    fixture: dict = {"note": "made-up bars; zones from app/price_action/sd_zones.py with the reference settings", "formations": {}}
    for name, candles in FORMATIONS.items():
        df = scaled(_frame(candles))
        df.index = session_starts(len(df))
        rows = S.zone_rows(df, "15m")
        zones = [z for z in rows["zones"] if z["formation"] == name]
        assert len(zones) == 1, (name, rows["zones"])                    # the detector itself found this formation
        assert [z.formation for z in sd.detect(df)] == [name]
        bars = [{"timestamp": t.isoformat().replace("+00:00", "Z"), "open": float(r.open), "high": float(r.high), "low": float(r.low),
                 "close": float(r.close), "volume": 100000.0} for t, r in zip(df.index, df.itertuples())]
        fixture["formations"][name] = {"bars": bars, "rows": {**rows, "source": "lake:15m", "reason": None}}
    return fixture


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=1) + "\n")
    print(f"wrote {OUT}")
