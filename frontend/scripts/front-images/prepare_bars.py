"""M95 front-door images: index daily bars for the capture, from a local store of real bars.

Reads daily bar CSVs (timestamp, bar_end, open, high, low, close; .csv or .csv.gz), keeps the bars up to the as-of
date (at least three months old, rule M95-Q1) and writes one JSON file OUTSIDE the repository for the capture script
(FRONT_BARS=<that file>). The raw bars are never committed; the images hide every price figure and price axis.

  python3 scripts/front-images/prepare_bars.py --asof 2026-07-31 --out /tmp/front-bars.json \
      --bars "NIFTY 50=<daily CSV>" --bars "NIFTY BANK=<daily CSV>"
"""
import argparse
import csv
import datetime as dt
import gzip
import json
import pathlib


def read(path: pathlib.Path, asof: str, keep: int) -> list:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["timestamp"][:10] <= asof]
    return [{"timestamp": f"{r['timestamp'][:10]}T03:45:00Z", "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"]), "volume": 0} for r in rows[-keep:]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", required=True, help="the last bar's date, YYYY-MM-DD")
    ap.add_argument("--bars", action="append", required=True, help="SYMBOL=path to a daily bar CSV")
    ap.add_argument("--keep", type=int, default=160, help="bars kept per symbol")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if dt.date.fromisoformat(a.asof) > dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=92):
        raise SystemExit("the as-of date must be at least three months old (M95-Q1)")
    out = {"asof": a.asof, "symbols": {}}
    for item in a.bars:
        symbol, path = item.split("=", 1)
        bars = read(pathlib.Path(path), a.asof, a.keep)
        if not bars or bars[-1]["timestamp"][:10] != a.asof:
            raise SystemExit(f"{symbol}: no bar on {a.asof}")
        out["symbols"][symbol] = bars
    pathlib.Path(a.out).write_text(json.dumps(out))
    print(f"wrote {a.out}: " + ", ".join(f"{s} {len(b)} bars" for s, b in out["symbols"].items()))


if __name__ == "__main__":
    main()
