"""OC5: writes option_chain_charts.json - the made-up answer of GET /api/option-chain/charts that the Charts view's
tests and screenshots use. It is the backend's own output (app/option_chain/charts.py) for the hand-made chain in
option_chain_v2.json (round made-up numbers, no real market data) plus 8 made-up stored readings from 09:50 to 10:25
IST and two earlier sessions, so the shapes cannot drift apart.

    cd backend && python ../frontend/e2e/fixtures/make_option_chain_charts.py
"""
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "backend"))

from app.brokers.models import OptionChain, OptionChainRow  # noqa: E402
from app.option_chain import charts  # noqa: E402

NOW = datetime(2026, 10, 27, 5, 0, tzinfo=timezone.utc)          # 10:30 IST, the e2e clock
EXPIRY = "2026-11-03"


def main() -> None:
    live = json.loads((HERE / "option_chain_v2.json").read_text())["live"]
    now_rows = [OptionChainRow(strike=r["strike"], call_oi=r["call"]["oi"], put_oi=r["put"]["oi"], call_ltp=r["call"]["ltp"],
                               put_ltp=r["put"]["ltp"], call_iv=r["call"]["iv"], put_iv=r["put"]["iv"]) for r in live["rows"]]
    spot = live["summary"]["spot"]
    current = OptionChain(underlying="NIFTY", expiry=EXPIRY, underlying_ltp=spot, rows=now_rows)
    readings = []
    for i in range(8):                                           # 09:50 .. 10:25 IST
        at = datetime(2026, 10, 27, 4, 20, tzinfo=timezone.utc) + timedelta(minutes=5 * i)
        share = 0.9 + 0.1 * i / 8                                # OI builds through the morning
        s_spot = round(spot - 30 + 4 * i + 6 * math.sin(i / 2), 2)
        rows = [OptionChainRow(strike=r.strike, call_oi=round(r.call_oi * (share + (0.03 if r.strike > spot else 0) * i / 8)),
                               put_oi=round(r.put_oi * share), call_ltp=round(r.call_ltp * (1.02 - 0.0025 * i), 2),
                               put_ltp=round(r.put_ltp * (1.03 - 0.004 * i), 2), call_iv=round(r.call_iv + 0.6 - 0.04 * i, 2),
                               put_iv=round(r.put_iv + 0.8 - 0.05 * i, 2)) for r in now_rows]
        chain = OptionChain(underlying="NIFTY", expiry=EXPIRY, underlying_ltp=s_spot, rows=rows)
        readings.append(charts.Reading(at, chain, round(s_spot + 51.5, 2), 11_500_000 + 40_000 * i))
    first = readings[0].chain
    baselines = {r.strike: (r.call_oi, r.put_oi) for r in first.rows}
    earlier = []
    for day, iv, prices in [(date(2026, 10, 23), 11.2, (24400.0, 24460.0, 24380.0, 24420.0)),
                                           (date(2026, 10, 26), 11.6, (24420.0, 24500.0, 24410.0, 24450.0))]:
        rows = [OptionChainRow(strike=r.strike, call_oi=r.call_oi, put_oi=r.put_oi, call_iv=iv, put_iv=iv) for r in now_rows]
        at = datetime(day.year, day.month, day.day, 9, 55, tzinfo=timezone.utc)
        earlier.append(charts.EarlierSession(day, at, OptionChain(underlying="NIFTY", expiry=EXPIRY, underlying_ltp=prices[-1], rows=rows), prices))
    body = charts.compute(current, readings, NOW, each_side=10, interval=5, baselines=baselines, earlier=earlier)
    out = {"symbol": "NIFTY", "expiry": EXPIRY, "generated_at": NOW.isoformat(), "strikes": 10, "interval": 5, "state": "ok", "message": None,
           "spot": spot, "data_as_of": NOW.isoformat(), "source": {"broker": "upstox", "account_label": "primary"}, **body}
    (HERE / "option_chain_charts.json").write_text(json.dumps(out, sort_keys=True, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
