"""FN2-f: weekly (1w) and monthly (1M) bars on the exchange calendar, for the bar-close engine and the stale-data gate.

A week runs Monday to Sunday and a month from its first day to its last, in exchange time - the screener's own buckets
(`app.screener.runtime.resample`: W-MON / MS, labelled by the period's first day). A period's bar closes at the close of
its LAST trading session (`is_trading_day`: weekends and the exchange holidays skipped); a period with no session has no
bar. Nothing here looks past `now`: a period counts as closed only once its last session has closed.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Optional, Tuple

from app.market_data.calendar import IST, MARKET_CLOSE, is_trading_day

PERIODS = ("1w", "1M")
# operational walk bound: how many periods back `latest_closed` looks (a longer run of periods without one session: no bar)
MAX_PERIODS_BACK = 12


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def period_start(day: date, tf: str) -> date:
    """The first day of the week (Monday) or month that holds `day`."""
    return day - timedelta(days=day.weekday()) if tf == "1w" else day.replace(day=1)


def period_end(day: date, tf: str) -> date:
    """The last calendar day of the week (Sunday) or month that holds `day`."""
    if tf == "1w":
        return period_start(day, tf) + timedelta(days=6)
    first_of_next = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_of_next - timedelta(days=1)


def last_session(day: date, tf: str, holidays: Iterable[date] = ()) -> Optional[date]:
    """The last trading session of the period that holds `day`; None when the period has none."""
    hol = set(holidays)
    start, d = period_start(day, tf), period_end(day, tf)
    while d >= start:
        if is_trading_day(d, hol):
            return d
        d -= timedelta(days=1)
    return None


def label(day: date, tf: str) -> datetime:
    """The bar label (UTC) of the period that holds `day`: its first day at 00:00 exchange time, as `resample` labels it."""
    return datetime.combine(period_start(day, tf), time.min, IST).astimezone(timezone.utc)


def _closed(session_day: date, at: datetime) -> bool:
    return datetime.combine(session_day, MARKET_CLOSE, IST) <= at


def latest_closed(now: datetime, tf: str, holidays: Iterable[date] = ()) -> Optional[Tuple[datetime, date]]:
    """(label, last session) of the latest period whose last session had closed by `now`; None if none in reach."""
    hol = set(holidays)
    at = _utc(now).astimezone(IST)
    day = at.date()
    for _ in range(MAX_PERIODS_BACK):
        last = last_session(day, tf, hol)
        if last is not None and _closed(last, at):
            return label(last, tf), last
        day = period_start(day, tf) - timedelta(days=1)
    return None


def closed_after(bar_start: datetime, tf: str, now: datetime, holidays: Iterable[date] = (), cap: int = 64) -> int:
    """How many periods after the one starting at `bar_start` had closed by `now` (counting stops at `cap`)."""
    hol = set(holidays)
    at = _utc(now).astimezone(IST)
    day = period_end(_utc(bar_start).astimezone(IST).date(), tf) + timedelta(days=1)
    count = 0
    while count < cap and day <= at.date():
        last = last_session(day, tf, hol)
        count += int(last is not None and _closed(last, at))
        day = period_end(day, tf) + timedelta(days=1)
    return count


__all__ = ["PERIODS", "MAX_PERIODS_BACK", "closed_after", "label", "last_session", "latest_closed", "period_end", "period_start"]
