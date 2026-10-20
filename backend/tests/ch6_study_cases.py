"""N4 CH6 parity cases: the screener runtime's SMA / EMA / RSI on the frozen synthetic 15m fixture
(tests/fixtures/zones/session_15m.csv - seeded, no real data, nothing from the sealed NIFTY holdout), for the browser's
own study math (frontend/src/charting/studyMath.ts) to match. tests/fixtures/charts/study_parity.json holds them and
frontend/src/charting/__fixtures__/study_parity.json is a byte-identical copy (the frontend build sees only frontend/).
Regenerate both - only after a deliberate change to the runtime's indicators - from backend/:
    python -m tests.ch6_study_cases --write
    # Then pin the new content (public repo guard, ALLOWED_TEST_DATA in scripts/check_public_repo.py):
    python3 ../scripts/check_public_repo.py --hash tests/fixtures/charts/study_parity.json ../frontend/src/charting/__fixtures__/study_parity.json
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict

from app.charts import studies as S
from tests.ch3_pa_cases import frame

PATH = Path(__file__).parent / "fixtures" / "charts" / "study_parity.json"
FRONTEND_PATH = Path(__file__).resolve().parents[2] / "frontend" / "src" / "charting" / "__fixtures__" / "study_parity.json"
BARS = 300
CASES = {"sma": ("SMA(close, 20)", 20), "ema": ("EMA(close, 20)", 20), "rsi": ("RSI(14)", 14)}


def cases() -> Dict[str, Any]:
    f = frame().head(BARS).assign(volume=0.0)
    checked = S.check([expr for expr, _ in CASES.values()], "15m")
    out = S.compute(f, "15m", checked, None)
    by = {row["expr"]: row for row in out["studies"]}
    return {"bars": [[int(t), float(c)] for t, c in zip(out["times"], f["close"])],
            "studies": {name: {"expr": expr, "period": period, "values": by[expr]["values"]} for name, (expr, period) in CASES.items()}}


def dumps(content: Dict[str, Any]) -> str:
    return json.dumps(content, sort_keys=True, separators=(",", ":")) + "\n"


if __name__ == "__main__":
    if "--write" in sys.argv:
        text = dumps(cases())
        for p in (PATH, FRONTEND_PATH):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        print(f"wrote {PATH} and {FRONTEND_PATH} ({len(text)} bytes)")
