"""Index expiry dates as NSE listed them (built from the F&O bhavcopies by `app.instruments.nse_expiries`).

The file holds every index expiry the exchange printed (EXPIRY_DT / XpryDt) from the first week read to the last
(`coverage_start` .. `coverage_end` in the meta file), each confirmed by the bhavcopy of its own day, with its kind
("monthly" when a futures contract expired that day, else "weekly") and the week it was first seen. Underlyings in
`DATA_DRIVEN` take their backtest expiries from here only - no weekday rule. A date before the coverage raises
`ExpiryDataMissing`. A date up to STALE_GRACE_DAYS after the last day read (the weekly refresh's gap) gets the
contracts already listed by then (monthlies are listed months ahead) and nothing invented; a later date raises
`ExpiryDataStale` - the file is out of date and silently answering from it would hide that (the backtest skips such
bars and counts them). A contract counts from the
day it was first seen (causal; `first_seen` is the weekly sample, so a contract listed mid-week counts a few days
late, never early). Live trading never reads this file (it uses the broker instrument master).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DATA_DIR = Path(__file__).parent / "data"
CSV_PATH = DATA_DIR / "nse_index_expiries.csv"
META_PATH = DATA_DIR / "nse_index_expiries.meta.json"

# Underlyings whose backtest calendar is this data (and nothing else).
DATA_DRIVEN = frozenset({"NIFTY", "BANKNIFTY"})

# (expiry, kind, first_seen)
Listed = Tuple[dt.date, str, dt.date]


# Days after `coverage_end` the file is still trusted (it is rebuilt weekly; a refresh pull request waits for review).
STALE_GRACE_DAYS = int(os.environ.get("EXPIRY_DATA_STALE_GRACE_DAYS", "10") or 10)


class ExpiryDataMissing(ValueError):
    """No exchange data for the date asked - refresh the file instead of guessing an expiry."""


class ExpiryDataStale(ExpiryDataMissing):
    """The date is more than STALE_GRACE_DAYS after the last day the file read."""


@lru_cache(maxsize=1)
def _load() -> Tuple[Dict[str, List[Listed]], Dict]:
    if not CSV_PATH.exists() or not META_PATH.exists():
        return {}, {}
    by_symbol: Dict[str, List[Listed]] = {}
    with open(CSV_PATH, newline="") as f:
        for row in csv.DictReader(f):
            by_symbol.setdefault(row["symbol"], []).append(
                (dt.date.fromisoformat(row["expiry"]), row["kind"], dt.date.fromisoformat(row["first_seen"])))
    for rows in by_symbol.values():
        rows.sort()
    return by_symbol, json.loads(META_PATH.read_text())


def coverage() -> Optional[Tuple[dt.date, dt.date]]:
    meta = _load()[1]
    if not meta:
        return None
    return dt.date.fromisoformat(meta["coverage_start"]), dt.date.fromisoformat(meta["coverage_end"])


def has(symbol: str) -> bool:
    return bool(_load()[0].get(symbol.upper()))


def data_driven(symbol: str) -> bool:
    return symbol.upper() in DATA_DRIVEN


def listed(symbol: str) -> List[Listed]:
    """Every expiry of the underlying in the file, oldest first."""
    return list(_load()[0].get(symbol.upper(), []))


def expiries(symbol: str, on_or_after: dt.date, count: int = 6, monthly_only: bool = False,
             as_of: Optional[dt.date] = None) -> List[dt.date]:
    """The next `count` expiries on/after `on_or_after` that were listed on `as_of` (default: the same day), i.e. first
    seen on or before it. Raises ExpiryDataMissing before the coverage or for an underlying not in the file, and
    ExpiryDataStale more than STALE_GRACE_DAYS after it."""
    cov = coverage()
    rows = _load()[0].get(symbol.upper())
    if cov is None or not rows:
        raise ExpiryDataMissing(f"no NSE expiry data for {symbol.upper()} (file {CSV_PATH.name} missing or empty)")
    start, end = cov
    day = as_of or on_or_after
    if day < start:
        raise ExpiryDataMissing(
            f"NSE expiry data for {symbol.upper()} starts on {start}; {day} is before it.")
    if day > end + dt.timedelta(days=STALE_GRACE_DAYS):
        raise ExpiryDataStale(
            f"NSE expiry data for {symbol.upper()} was read through {end}; {day} is more than {STALE_GRACE_DAYS} days later "
            "- refresh the file (the 'NSE expiry data' workflow).")
    out = [e for e, kind, seen in rows
           if e >= on_or_after and seen <= day and (kind == "monthly" or not monthly_only)]
    return out[:count]


def stale_after() -> Optional[dt.date]:
    """The last day the file read; later bar days see only what was listed by then."""
    cov = coverage()
    return cov[1] if cov else None
