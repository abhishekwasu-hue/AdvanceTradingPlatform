"""UI2-4a2: weekly and monthly bars, and today's daily bar, built from bars the broker already serves (pure functions).

The rule is one for every broker (Upstox's native week/month candles are not used), so "W/M match D" holds by
construction:

- **In IST, never UTC.** Each daily bar's trading date is its timestamp in Asia/Kolkata. A broker that stamps a daily bar
  at 00:00 IST sends 18:30 UTC of the day before; grouping in UTC would move every Monday into the week before.
- **Weeks** run Monday to Friday (a special session on a Saturday or Sunday joins the week of the Monday before it).
  **Months** are calendar months.
- **Stamp.** A week or month is stamped at its first trading day (the first Monday-to-Friday bar in it; a period with
  only a weekend session is stamped at that session) at 00:00 IST, so a week that starts on a holiday Monday is stamped
  at its Tuesday and no weekly bar ever lands on a Sunday.
- **Values.** Open of the first bar, highest high, lowest low, close of the last bar, volume summed.

`today_daily_bar` folds today's one-minute bars into one daily bar (the chart's forming D bar).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from app.core.models import OHLCVBar
from app.market_data.calendar import IST

PERIODS = ("week", "month")
WEEKLY_DEFAULT_DAYS = 1826          # five years of daily bars (about 260 weekly bars)
MONTHLY_DEFAULT_DAYS = 7300         # twenty years (about 240 monthly bars)
WEEKLY_MAX = 3650
MONTHLY_MAX = 7300


def ist_date(ts: datetime) -> date:
    """A bar's trading date: its timestamp in IST (a naive timestamp is UTC, as everywhere in the platform)."""
    return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(IST).date()


def ist_midnight(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=IST)


def period_start(day: date, period: str) -> date:
    """The calendar first day of the period holding `day`: its week's Monday, or its month's first day."""
    if period == "week":
        return day - timedelta(days=day.weekday())
    if period == "month":
        return day.replace(day=1)
    raise ValueError(f"unknown period {period!r}")


def next_period_start(day: date, period: str) -> date:
    start = period_start(day, period)
    if period == "week":
        return start + timedelta(days=7)
    return date(start.year + (start.month == 12), start.month % 12 + 1, 1)


def aggregate(daily: Iterable[OHLCVBar], period: str) -> List[OHLCVBar]:
    """Weekly or monthly bars from daily bars (any order, duplicates by date: the later one wins), ascending."""
    by_day: Dict[date, OHLCVBar] = {}
    for bar in daily:
        by_day[ist_date(bar.timestamp)] = bar
    groups: Dict[date, List[Tuple[date, OHLCVBar]]] = {}
    for day in sorted(by_day):
        groups.setdefault(period_start(day, period), []).append((day, by_day[day]))
    out: List[OHLCVBar] = []
    for _start, rows in sorted(groups.items()):
        weekdays = [d for d, _ in rows if d.weekday() < 5]
        stamp = weekdays[0] if weekdays else rows[0][0]
        bars = [b for _, b in rows]
        out.append(OHLCVBar(timestamp=ist_midnight(stamp), open=bars[0].open, high=max(b.high for b in bars),
                            low=min(b.low for b in bars), close=bars[-1].close, volume=float(sum(b.volume or 0.0 for b in bars))))
    return out


def today_daily_bar(minutes: Iterable[OHLCVBar], today: date) -> Optional[OHLCVBar]:
    """Today's daily bar from today's one-minute bars (others dropped), stamped 00:00 IST; None without any."""
    bars = sorted((b for b in minutes if ist_date(b.timestamp) == today), key=lambda b: b.timestamp)
    if not bars:
        return None
    return OHLCVBar(timestamp=ist_midnight(today), open=bars[0].open, high=max(b.high for b in bars), low=min(b.low for b in bars),
                    close=bars[-1].close, volume=float(sum(b.volume or 0.0 for b in bars)))


def chunks(start: date, end: date, days_per_request: int) -> List[Tuple[date, date]]:
    """Whole-date windows covering start..end (inclusive), newest first, each at most `days_per_request` days, walked back
    from `end` - so two requests ending on the same day (a W and an M chart, two users) ask for the same windows."""
    size = max(1, int(days_per_request))
    out: List[Tuple[date, date]] = []
    hi = end
    while hi >= start:
        lo = max(start, hi - timedelta(days=size - 1))
        out.append((lo, hi))
        hi = lo - timedelta(days=1)
    return out
