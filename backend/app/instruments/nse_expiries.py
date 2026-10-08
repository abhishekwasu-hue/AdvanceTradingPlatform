"""Index F&O expiry dates read from NSE's own daily F&O bhavcopies - never from a weekday rule.

Every bhavcopy lists each live contract with its expiry date: `EXPIRY_DT` (e.g. 25-Jan-2024) in the legacy file
(`fo<DD><MON><YYYY>bhav.csv`, until 5 Jul 2024) and `XpryDt` (2024-07-10) in the UDiFF file
(`BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv`, from 8 Jul 2024). The build:

1. reads one bhavcopy per calendar week (the first trading day the archive has) from `start` to `end`, plus the last
   file on or before `end`, and collects every index contract (OPTIDX / FUTIDX, IDO / IDF) with the day it was first
   seen;
2. confirms each expiry with the bhavcopy OF that day: an expiry counts only if contracts expiring that day are in
   that day's file. A date that fails (a late holiday or a change of weekday moved the contract) is dropped and
   reported, and the files of the six days either side are read for the date it actually expired on (the day a future
   of the underlying expired when there is one - NIFTY Thu 25 -> Tue 30 Sep 2025, not the weekly of the 23rd; else
   the nearest day, earlier on a tie) - so a contract moved too late to show in a weekly sample (BANKNIFTY 29 -> 28 Jun 2023) is
   still found, and a re-dated contract (BANKNIFTY Jan 2025: Wed 29 -> Thu 30) keeps its first listing date;
3. an expiry with a futures contract is "monthly", the rest "weekly" - read from the data, not inferred. A contract
   listed too far ahead to have a future yet is "monthly" when it is the last listed expiry of its month.

Guards (the build fails, nothing is written): a calendar week with no file at all (NSE never closes a whole week -
the archive refused or was cut), an expiry of a STRICT underlying (BANKNIFTY) with no file on its day that no move
explains, and - against the files already in `--out` - an earlier coverage end or fewer expired rows for any
underlying. A 403 is retried, then counted as "no file" (the guards catch a block).

Output (committed, read by `app.instruments.expiry_data`): `data/nse_index_expiries.csv`
(symbol, expiry, kind, first_seen, status) and `data/nse_index_expiries.meta.json` (coverage, files read, drops).
`first_seen` has weekly precision (the sampled day). Contracts expiring after `end` are kept as status "listed".

Stdlib only, so it runs without the backend's dependencies:
    python backend/app/instruments/nse_expiries.py --start 2016-01-01 --end 2026-10-08 --out backend/app/instruments/data
The GitHub workflow `.github/workflows/nse-expiries.yml` runs it (the archive is not reachable from every network).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import sys
import time
import urllib.error
import urllib.request
import zipfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

INDEX_SYMBOLS = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50")
STRICT = ("BANKNIFTY",)          # underlyings whose backtests read this file: every expiry must be explained
UDIFF_FROM = dt.date(2024, 7, 8)
ARCHIVE = "https://nsearchives.nseindia.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "*/*", "Referer": "https://www.nseindia.com/",
}

# (symbol, expiry, is_future)
Contract = Tuple[str, dt.date, bool]


def legacy_url(day: dt.date) -> str:
    mon = day.strftime("%b").upper()
    return f"{ARCHIVE}/content/historical/DERIVATIVES/{day.year}/{mon}/fo{day:%d}{mon}{day.year}bhav.csv.zip"


def udiff_url(day: dt.date) -> str:
    return f"{ARCHIVE}/content/fo/BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"


def parse_bhavcopy(text: str, symbols: Iterable[str] = INDEX_SYMBOLS) -> Set[Contract]:
    """Index contracts in one F&O bhavcopy (legacy or UDiFF layout)."""
    wanted = set(symbols)
    reader = csv.DictReader(io.StringIO(text))
    fields = {(f or "").strip() for f in (reader.fieldnames or [])}
    out: Set[Contract] = set()
    if "EXPIRY_DT" in fields:
        for row in reader:
            row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            inst = row.get("INSTRUMENT", "")
            if inst in ("OPTIDX", "FUTIDX") and row.get("SYMBOL") in wanted:
                exp = dt.datetime.strptime(row["EXPIRY_DT"], "%d-%b-%Y").date()
                out.add((row["SYMBOL"], exp, inst == "FUTIDX"))
    elif "XpryDt" in fields:
        for row in reader:
            row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            tp = row.get("FinInstrmTp", "")
            if tp in ("IDO", "IDF") and row.get("TckrSymb") in wanted:
                out.add((row["TckrSymb"], dt.date.fromisoformat(row["XpryDt"][:10]), tp == "IDF"))
    else:
        raise ValueError(f"not an F&O bhavcopy (columns: {sorted(fields)[:8]}...)")
    return out


def _unzip(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        return z.read(name).decode("utf-8", errors="replace")


class Archive:
    """Downloads (and caches) one day's bhavcopy; None when the exchange has no file for that day (holiday)."""

    def __init__(self, cache: Optional[Path] = None, pause: float = 0.35, retries: int = 4) -> None:
        self.cache, self.pause, self.retries = cache, pause, retries
        self.files_read = 0
        self.forbidden = 0
        self._lock = threading.Lock()
        if cache:
            cache.mkdir(parents=True, exist_ok=True)

    def _get(self, url: str) -> Optional[bytes]:
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=60) as r:  # nosec B310
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code == 403 and attempt == self.retries - 1:
                    with self._lock:
                        self.forbidden += 1
                    return None
                time.sleep(2 ** attempt)
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(2 ** attempt)
        raise RuntimeError(f"giving up on {url}")

    def day(self, day: dt.date) -> Optional[str]:
        if day.weekday() >= 5:
            return None
        hit = self.cache / f"{day:%Y%m%d}.csv" if self.cache else None
        miss = self.cache / f"{day:%Y%m%d}.none" if self.cache else None
        if hit and hit.exists():
            return hit.read_text()
        if miss and miss.exists():
            return None
        urls = [udiff_url(day), legacy_url(day)] if day >= UDIFF_FROM else [legacy_url(day), udiff_url(day)]
        text = None
        for url in urls:
            time.sleep(self.pause)
            blob = self._get(url)
            if blob:
                text = _unzip(blob)
                break
        if text is None:
            if miss:
                miss.write_text("")
            return None
        with self._lock:
            self.files_read += 1
        if hit:
            hit.write_text(text)
        return text


class BuildRefused(RuntimeError):
    """A guard failed: the result would have holes, so nothing is written."""


def build(start: dt.date, end: dt.date, fetch: Callable[[dt.date], Optional[str]], workers: int = 3,
          log: Callable[[str], None] = lambda m: None, strict: Iterable[str] = STRICT) -> Tuple[List[Dict[str, str]], Dict]:
    """(rows, meta) - see the module docstring. Raises BuildRefused when a guard fails."""
    # 1. one bhavcopy per calendar week
    monday = start - dt.timedelta(days=start.weekday())
    weeks = []
    while monday <= end:
        weeks.append(monday)
        monday += dt.timedelta(days=7)

    def first_day_of_week(m: dt.date) -> Tuple[Optional[dt.date], Set[Contract]]:
        for i in range(5):
            d = m + dt.timedelta(days=i)
            if start <= d <= end:
                text = fetch(d)
                if text is not None:
                    return d, parse_bhavcopy(text)
        return None, set()

    def last_day() -> Tuple[Optional[dt.date], Set[Contract]]:
        for i in range(7):
            d = end - dt.timedelta(days=i)
            text = fetch(d) if d >= start else None
            if text is not None:
                return d, parse_bhavcopy(text)
        return None, set()

    seen: Dict[Tuple[str, dt.date], Dict] = {}
    sampled: List[dt.date] = []
    holes: List[str] = []
    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(first_day_of_week, weeks))
    results.append(last_day())
    for n, ((d, contracts), monday) in enumerate(zip(results, weeks + [end]), 1):
        if d is None:
            # a whole Mon-Fri inside the range with no file (the extra last-day look is not a week)
            if n <= len(weeks) and monday >= start and monday + dt.timedelta(days=4) <= end:
                holes.append(monday.isoformat())
            continue
        sampled.append(d)
        for sym, exp, fut in contracts:
            s = seen.setdefault((sym, exp), {"first_seen": d, "future": False})
            s["first_seen"] = min(s["first_seen"], d)
            s["future"] = s["future"] or fut
        if n % 50 == 0:
            log(f"sampled {n}/{len(weeks)} weeks")
    if not sampled:
        raise BuildRefused("no bhavcopy could be read in the range")
    if holes:
        raise BuildRefused(f"no file at all in {len(holes)} week(s), e.g. {holes[:5]} - archive blocked or cut?")
    last = max(sampled)

    # 2. confirm every expiry on/before the last file read with that day's own bhavcopy
    days = sorted({exp for (_, exp) in seen if exp <= last})

    def expiring(d: dt.date) -> Tuple[dt.date, Optional[Set[Tuple[str, bool]]]]:
        text = fetch(d)
        if text is None:
            return d, None
        return d, {(s, f) for s, e, f in parse_bhavcopy(text) if e == d}

    confirmed: Dict[dt.date, Optional[Set[Tuple[str, bool]]]] = {}
    with ThreadPoolExecutor(workers) as pool:
        for n, (d, got) in enumerate(pool.map(expiring, days), 1):
            confirmed[d] = got
            if n % 100 == 0:
                log(f"confirmed {n}/{len(days)} expiry days")

    rows: List[Dict[str, str]] = []
    dropped: List[Dict[str, str]] = []
    moved: List[Dict[str, str]] = []
    kept = {k for k in seen if k[1] > last or (confirmed.get(k[1]) is not None and any(x == k[0] for x, _ in confirmed[k[1]]))}
    for (sym, exp), s in sorted(seen.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        if exp > last or (sym, exp) in kept:
            continue
        # where did it go? To the day a future of this underlying expired when there is one within six days (a
        # re-dated monthly or long-dated contract is that month's monthly - merging its early first_seen into a weekly
        # would list the weekly years early); else to the nearest day (earlier first) with an expiry of the underlying.
        candidates = []
        for off in (o for k in range(1, 7) for o in (-k, k)):
            d = exp + dt.timedelta(days=off)
            text = fetch(d) if start <= d <= end else None
            if text is None:
                continue
            here = {(x, f) for x, e, f in parse_bhavcopy(text) if e == d and x == sym}
            if here:
                candidates.append((d, here))
        candidates = [c for c in candidates if (sym, True) in c[1]] or candidates
        if candidates:
            d, here = candidates[0]
            moved.append({"symbol": sym, "from": exp.isoformat(), "to": d.isoformat()})
            # the same contract under its new date: listed since the old one was first seen; its kind comes from the
            # new day's own file (a future expiring that day), never carried over
            t = seen.setdefault((sym, d), {"first_seen": s["first_seen"], "future": False})
            t["first_seen"] = min(t["first_seen"], s["first_seen"])
            if (sym, d) not in kept:
                kept.add((sym, d))
                confirmed[d] = (confirmed.get(d) or set()) | here
    for (sym, exp), s in sorted(seen.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        future = s["future"]
        if exp <= last:
            got = confirmed.get(exp)
            on_day = got is not None and any(x == sym for x, _ in got)
            if not on_day:
                why = "no bhavcopy that day" if got is None else "not in that day's bhavcopy"
                dropped.append({"symbol": sym, "expiry": exp.isoformat(), "reason": why})
                continue
            future = future or (sym, True) in got
            status = "expired" if exp < last else "listed"
        else:
            status = "listed"
        rows.append({"symbol": sym, "expiry": exp.isoformat(), "kind": "monthly" if future else "weekly",
                     "first_seen": s["first_seen"].isoformat(), "status": status})
    # Far contracts listed before their future: the last listed expiry of a month with no monthly yet is the monthly.
    by_month: Dict[Tuple[str, int, int], List[Dict[str, str]]] = {}
    for row in rows:
        e = dt.date.fromisoformat(row["expiry"])
        by_month.setdefault((row["symbol"], e.year, e.month), []).append(row)
    for group in by_month.values():
        if group[-1]["status"] == "listed" and not any(g["kind"] == "monthly" for g in group):
            group[-1]["kind"] = "monthly"
    moved_from = {(m["symbol"], m["from"]) for m in moved}
    unexplained = [d for d in dropped if d["symbol"] in set(strict) and d["reason"] == "no bhavcopy that day"
                   and (d["symbol"], d["expiry"]) not in moved_from]
    if unexplained:
        raise BuildRefused(f"expiries with no file on their day and no move found: {unexplained[:5]}")
    meta = {
        "source": "NSE F&O bhavcopy (legacy EXPIRY_DT until 2024-07-05, UDiFF XpryDt from 2024-07-08)",
        "coverage_start": min(sampled).isoformat(), "coverage_end": last.isoformat(),
        "weeks_sampled": len(sampled), "expiry_days_checked": len(days),
        "symbols": list(INDEX_SYMBOLS), "rows": len(rows), "dropped": dropped, "moved": moved,
    }
    return rows, meta


def check_against(previous_dir: Path, rows: List[Dict[str, str]], meta: Dict) -> None:
    """Refuses a result that knows less than the files already there: an earlier coverage end or fewer expired rows."""
    old_meta, old_csv = previous_dir / "nse_index_expiries.meta.json", previous_dir / "nse_index_expiries.csv"
    if not old_meta.exists() or not old_csv.exists():
        return
    prev = json.loads(old_meta.read_text())
    if meta["coverage_end"] < prev["coverage_end"]:
        raise BuildRefused(f"coverage would end on {meta['coverage_end']}, before the current {prev['coverage_end']}")

    def expired(rs: Iterable[Dict[str, str]], until: str) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for x in rs:
            if x["expiry"] <= until:
                out[x["symbol"]] = out.get(x["symbol"], 0) + 1
        return out
    with open(old_csv, newline="") as f:
        before = expired(csv.DictReader(f), prev["coverage_end"])
    after = expired(rows, prev["coverage_end"])
    shrunk = {s: (n, after.get(s, 0)) for s, n in before.items() if after.get(s, 0) < n}
    if shrunk:
        raise BuildRefused(f"fewer expiries than the current file up to {prev['coverage_end']}: {shrunk}")


def write(rows: List[Dict[str, str]], meta: Dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "nse_index_expiries.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "expiry", "kind", "first_seen", "status"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    (out / "nse_index_expiries.meta.json").write_text(json.dumps(meta, indent=1) + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--start", default="2016-01-01")
    p.add_argument("--end", default=dt.date.today().isoformat())
    p.add_argument("--out", default=str(Path(__file__).parent / "data"))
    p.add_argument("--cache", default=None, help="directory to keep downloaded bhavcopies")
    p.add_argument("--workers", type=int, default=3)
    a = p.parse_args(argv)
    archive = Archive(Path(a.cache) if a.cache else None)
    try:
        rows, meta = build(dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end), archive.day, a.workers,
                           log=lambda m: print(m, flush=True))
        check_against(Path(a.out), rows, meta)
    except BuildRefused as exc:
        print(f"REFUSED: {exc} (403 answers: {archive.forbidden})", file=sys.stderr)
        return 2
    meta["files_read"] = archive.files_read
    meta["forbidden"] = archive.forbidden
    write(rows, meta, Path(a.out))
    print(f"{len(rows)} expiries, {len(meta['dropped'])} dropped, coverage {meta['coverage_start']}..{meta['coverage_end']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
