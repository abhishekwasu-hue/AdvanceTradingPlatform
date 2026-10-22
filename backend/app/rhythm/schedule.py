"""When each digest is due, on the IST market calendar (NSE holidays from `market_holidays`).

* In the app, the Dashboard shows the digest of the moment (`phase`): before 09:15 on a trading day the pre-market brief,
  during the session the pulse, after 15:30 the closing wrap; on Saturday and Sunday the weekly review; on a weekday
  holiday the pre-market brief for the next session.
* On Telegram, each digest goes out once in its window (`due_kinds`), on trading days only (the weekly review on the
  weekend). The window is wide enough that a worker restart or a slow cycle still sends it; `period_key` names the day
  (or ISO week) a send counts for, so it is never sent twice.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable, List, Set

from app.market_data.calendar import IST, MARKET_CLOSE, MARKET_OPEN, is_trading_day, next_trading_day

PRE_MARKET_FROM = time(8, 30)
MID_SESSION_FROM, MID_SESSION_UNTIL = time(12, 30), time(13, 30)
CLOSING_FROM, CLOSING_UNTIL = time(15, 45), time(19, 0)
WEEKLY_FROM = time(9, 0)              # Saturday, until Sunday WEEKLY_UNTIL
WEEKLY_UNTIL = time(21, 0)


def _ist(now: datetime) -> datetime:
    return now.astimezone(IST)


def phase(now: datetime, holidays: Iterable[date] = ()) -> str:
    """The digest the Dashboard shows now."""
    ist = _ist(now)
    hol: Set[date] = set(holidays)
    if ist.weekday() >= 5:
        return "weekly"
    if not is_trading_day(ist.date(), hol):
        return "pre_market"
    t = ist.time()
    if t < MARKET_OPEN:
        return "pre_market"
    if t < MARKET_CLOSE:
        return "mid_session"
    return "closing"


def due_kinds(now: datetime, holidays: Iterable[date] = ()) -> List[str]:
    """The digests whose Telegram window is open now."""
    ist = _ist(now)
    t, day = ist.time(), ist.date()
    if (ist.weekday() == 5 and t >= WEEKLY_FROM) or (ist.weekday() == 6 and t < WEEKLY_UNTIL):
        return ["weekly"]
    if not is_trading_day(day, set(holidays)):
        return []
    if PRE_MARKET_FROM <= t < MARKET_OPEN:
        return ["pre_market"]
    if MID_SESSION_FROM <= t < MID_SESSION_UNTIL:
        return ["mid_session"]
    if CLOSING_FROM <= t < CLOSING_UNTIL:
        return ["closing"]
    return []


def period_key(kind: str, now: datetime) -> str:
    """The day ("2026-10-22") or, for the weekly review, the ISO week ("2026-W43") a send counts for."""
    day = _ist(now).date()
    if kind == "weekly":
        iso = day.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    return day.isoformat()


def next_session(now: datetime, holidays: Iterable[date] = ()) -> date:
    """The next trading day after today (today itself before the open on a trading day)."""
    ist = _ist(now)
    hol = set(holidays)
    if ist.time() < MARKET_OPEN and is_trading_day(ist.date(), hol):
        return ist.date()
    return next_trading_day(ist.date(), hol)


def previous_session(day: date, holidays: Iterable[date] = ()) -> date:
    """The trading day before `day`."""
    hol = set(holidays)
    d = day - timedelta(days=1)
    for _ in range(15):
        if is_trading_day(d, hol):
            return d
        d -= timedelta(days=1)
    return d


def week_trading_days(monday: date, holidays: Iterable[date] = ()) -> List[date]:
    hol = set(holidays)
    return [monday + timedelta(days=i) for i in range(5) if is_trading_day(monday + timedelta(days=i), hol)]


def session_start(day: date) -> datetime:
    return datetime.combine(day, MARKET_OPEN, tzinfo=IST)


def day_start(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=IST)

