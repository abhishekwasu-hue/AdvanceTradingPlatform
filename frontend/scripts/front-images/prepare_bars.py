"""M95 front-door images: index daily bars for the capture, from a local store of real bars.

Not used since the owner's review of 14 Nov: the images now carry made-up data and placeholder names only
(front-images.e2e.ts needs no bars). Kept for reference.

Reads daily bar CSVs (timestamp, bar_end, open, high, low, close; .csv or .csv.gz), keeps the bars up to the as-of
date (at least three months old, rule M95-Q1) and writes one JSON file OUTSIDE the repository for the capture script
(FRONT_BARS=<that file>). The raw bars are never committed; the images hide every price figure and price axis.
The Daily and Weekly structure words are the app's OWN reader on those bars - the daily report's
`app.daily_report.inputs.read_structure` over its own history window (`HISTORY_DAYS`), with backend/ on sys.path -
never a rule of this script.

  python3 scripts/front-images/prepare_bars.py --asof 2026-07-31 --out /tmp/front-bars.json \
      --bars "NIFTY 50=<daily CSV>" --bars "NIFTY BANK=<daily CSV>"
"""
import argparse
import csv
import datetime as dt
import gzip
import json
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[3] / "backend"


def rows_upto(path: pathlib.Path, asof: str) -> list:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", newline="") as fh:
        return [r for r in csv.DictReader(fh) if r["timestamp"][:10] <= asof]


def chart_bars(rows: list, keep: int) -> list:
    return [{"timestamp": f"{r['timestamp'][:10]}T03:45:00Z", "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"]), "volume": 0} for r in rows[-keep:]]


def app_structure(rows: list, asof: str) -> dict:
    """The daily report's own structure reader (Daily + Weekly) on the same history window it reads."""
    sys.path.insert(0, str(BACKEND))
    import pandas as pd
    from app.daily_report import inputs

    first = (dt.date.fromisoformat(asof) - dt.timedelta(days=inputs.HISTORY_DAYS)).isoformat()
    window = [r for r in rows if r["timestamp"][:10] >= first]
    ends = pd.to_datetime([f"{r['timestamp'][:10]} 15:30:00" for r in window]).tz_localize("Asia/Kolkata").tz_convert("UTC")
    frame = pd.DataFrame({"timestamp": ends - pd.to_timedelta("6h15min"), "bar_end": ends,
                          "open": [float(r["open"]) for r in window], "high": [float(r["high"]) for r in window],
                          "low": [float(r["low"]) for r in window], "close": [float(r["close"]) for r in window],
                          "volume": [0.0] * len(window)})
    read = inputs.read_structure(frame)
    return {"d": read["d"]["direction"], "w": read["w"]["direction"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", required=True, help="the last bar's date, YYYY-MM-DD")
    ap.add_argument("--bars", action="append", required=True, help="SYMBOL=path to a daily bar CSV")
    ap.add_argument("--keep", type=int, default=160, help="bars kept per symbol")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if dt.date.fromisoformat(a.asof) > dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=92):
        raise SystemExit("the as-of date must be at least three months old (M95-Q1)")
    out: dict = {"asof": a.asof, "symbols": {}, "structure": {}}
    for item in a.bars:
        symbol, path = item.split("=", 1)
        rows = rows_upto(pathlib.Path(path), a.asof)
        if not rows or rows[-1]["timestamp"][:10] != a.asof:
            raise SystemExit(f"{symbol}: no bar on {a.asof}")
        out["symbols"][symbol] = chart_bars(rows, a.keep)
        out["structure"][symbol] = app_structure(rows, a.asof)
    pathlib.Path(a.out).write_text(json.dumps(out))
    print(f"wrote {a.out}: " + ", ".join(f"{s} {len(b)} bars, structure {out['structure'][s]}" for s, b in out["symbols"].items()))


if __name__ == "__main__":
    main()
