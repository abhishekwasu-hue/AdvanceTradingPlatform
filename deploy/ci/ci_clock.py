#!/usr/bin/env python3
"""CI staging stack: which clock the stack's API, worker and fake broker run on (deploy/ci/stack.sh writes the result
into the stack's throwaway env file).

The acceptance flow needs an open NSE session: the worker watches positions, and the bar-close alert engine evaluates
rules, only while one is open (09:15-15:30 IST on trading days). A CI run can start at any hour, so:

* now is inside a session with time to spare (09:30-14:00 IST on a trading day) -> the real clock, offset 0;
* otherwise -> the most recent trading day at 10:00 IST: every app container gets the same fixed shift through
  libfaketime (FAKETIME, monotonic time left real) and the fake broker the same shift for its data.

Trading days: Monday-Friday minus the NSE holidays the migrations seed (read from backend/alembic/versions, so the
picked day is one the app's own calendar calls open). `--mode real` keeps the real clock whatever the hour (the load
test does not need a session); `--at 2026-10-14T10:00` picks an instant (IST) by hand.

Prints KEY=VALUE lines: ATP_CI_OFFSET_SECONDS, ATP_CI_FAKETIME, ATP_CI_LD_PRELOAD, ATP_CI_CLOCK_NOTE.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional, Set

IST = timezone(timedelta(hours=5, minutes=30))
LIBFAKETIME = "/usr/local/lib/libfaketimeMT.so.1"          # deploy/ci/app.Dockerfile links it here
WINDOW = (time(9, 30), time(14, 0))                         # real clock only with room for the whole flow before 15:15
TARGET = time(10, 0)
_DATE = re.compile(r"date\((\d{4}),\s*(\d{1,2}),\s*(\d{1,2})\)")


def nse_holidays(versions: Path) -> Set[date]:
    """Every `date(Y, M, D)` in the NSE holiday lists the migrations seed."""
    days: Set[date] = set()
    for path in sorted(versions.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for block in re.findall(r"NSE_HOLIDAYS\w*\s*=\s*\[(.*?)\]", text, re.S):
            days.update(date(int(y), int(m), int(d)) for y, m, d in _DATE.findall(block))
    return days


def trading_day(day: date, holidays: Set[date]) -> bool:
    return day.weekday() < 5 and day not in holidays


def pick(now: datetime, holidays: Set[date], mode: str = "auto", at: Optional[datetime] = None) -> datetime:
    """The instant the stack's clock should show at `now` (real UTC)."""
    if at is not None:
        return at
    local = now.astimezone(IST)
    if mode == "real" or (trading_day(local.date(), holidays) and WINDOW[0] <= local.time() <= WINDOW[1]):
        return now
    day = local.date()
    if not (trading_day(day, holidays) and local.time() > TARGET):
        day -= timedelta(days=1)
    for _ in range(30):
        if trading_day(day, holidays):
            return datetime.combine(day, TARGET, IST)
        day -= timedelta(days=1)
    raise SystemExit("no trading day in the last 30 days - check the holiday list")


def settings(now: datetime, target: datetime) -> dict:
    offset = int(round((target - now).total_seconds()))
    if abs(offset) < 5:
        return {"ATP_CI_OFFSET_SECONDS": "0", "ATP_CI_FAKETIME": "", "ATP_CI_LD_PRELOAD": "",
                "ATP_CI_CLOCK_NOTE": f"real clock ({now.astimezone(IST):%Y-%m-%d %H:%M} IST, inside the NSE session)"}
    return {"ATP_CI_OFFSET_SECONDS": str(offset), "ATP_CI_FAKETIME": f"{offset:+d}", "ATP_CI_LD_PRELOAD": LIBFAKETIME,
            "ATP_CI_CLOCK_NOTE": (f"shifted {offset:+d} s: the stack's clock starts at {target.astimezone(IST):%Y-%m-%d %H:%M} IST "
                                  f"(real {now.astimezone(IST):%Y-%m-%d %H:%M} IST is outside the NSE session)")}


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--versions", default=str(Path(__file__).resolve().parents[2] / "backend" / "alembic" / "versions"))
    parser.add_argument("--mode", choices=("auto", "real"), default="auto")
    parser.add_argument("--at", default=None, help="an instant in IST, e.g. 2026-10-14T10:00")
    parser.add_argument("--now", default=None, help=argparse.SUPPRESS)              # tests: the real clock, ISO with offset
    args = parser.parse_args(argv)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    at = datetime.fromisoformat(args.at).replace(tzinfo=IST) if args.at else None
    target = pick(now, nse_holidays(Path(args.versions)), args.mode, at)
    for key, value in settings(now, target).items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
