"""N4 CH3 golden: the price-action layer on the frozen synthetic 15m fixture (tests/fixtures/zones/session_15m.csv - seeded,
BANKNIFTY-like, no real market data and nothing from the sealed NIFTY holdout).

`frame()` is the fixture as closed bars; `golden()` the layer content at a fixed bar of the fixture (the default view in
full, the debug view by fingerprint). tests/fixtures/charts/pa_layer_golden.json holds it, and frontend/src/charting/__fixtures__/pa_layer_golden.json
is a byte-identical copy (the frontend Docker build sees only frontend/) used by priceAction.test.ts.
Regenerate both - only after a deliberate engine or schema change - from backend/:  python -m tests.ch3_pa_cases --write
Then pin the new content (public repo guard, ALLOWED_TEST_DATA in scripts/check_public_repo.py):  python3 ../scripts/check_public_repo.py --hash
    tests/fixtures/charts/pa_layer_golden.json ../frontend/src/charting/__fixtures__/pa_layer_golden.json
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from app.charts import price_action_layer as L
from tests import zones_golden as G

PATH = Path(__file__).parent / "fixtures" / "charts" / "pa_layer_golden.json"
FRONTEND_PATH = Path(__file__).resolve().parents[2] / "frontend" / "src" / "charting" / "__fixtures__" / "pa_layer_golden.json"
IST = "Asia/Kolkata"
GOLDEN_SESSIONS = 12            # the layer as of the close of the fixture's 12th session


def frame() -> pd.DataFrame:
    df = G.load("session_15m")
    start = df["timestamp"].dt.tz_localize(IST).dt.tz_convert("UTC")
    return pd.DataFrame({"timestamp": start, "bar_end": start + pd.Timedelta(minutes=15), "open": df["open"], "high": df["high"],
                         "low": df["low"], "close": df["close"]})


def golden_cut() -> pd.Timestamp:
    f = frame()
    days = sorted(f["timestamp"].dt.tz_convert(IST).dt.date.unique())
    last = f[f["timestamp"].dt.tz_convert(IST).dt.date == days[GOLDEN_SESSIONS - 1]]
    return pd.Timestamp(last["bar_end"].iloc[-1])


def golden() -> Dict[str, Any]:
    f = frame()
    cut = f[f["bar_end"] <= golden_cut()]
    debug = L.build(cut, "15m", L.DEFAULT_DEGREE, True)
    # the debug view in full is ~3,500 lines; its fingerprint (SHA-256 of the canonical content) pins it byte for byte
    return {"default": L.build(cut, "15m", L.DEFAULT_DEGREE, False),
            "debug": {"fingerprint": debug["fingerprint"], "counts": {k: len(v) for k, v in debug.items() if isinstance(v, list)},
                      "debug_info": debug["debug_info"]}}


def dumps(content: Dict[str, Any]) -> str:
    return json.dumps(content, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    if "--write" in sys.argv:
        text = dumps(golden())
        for p in (PATH, FRONTEND_PATH):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        print(f"wrote {PATH} and {FRONTEND_PATH} ({text.count(chr(10))} lines)")
