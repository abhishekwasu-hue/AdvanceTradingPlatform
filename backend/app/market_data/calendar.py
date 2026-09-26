"""Exchange session calendar - "is the market open right now?" for the autonomous worker.

NSE/BSE equity and F&O trade 09:15-15:30 IST, Monday to Friday, except exchange-declared
holidays (the `market_holidays` table, seeded from NSE's own annual circular - see the Phase A1
migration). The worker must not evaluate strategies or place orders outside that window: a
"signal" on a stale Friday-close candle at 2am Sunday is noise, and a LIVE order then is at best
rejected by the broker and at worst queued as an unintended pre-open order.

Everything here is computed in IST regardless of the host's timezone, and the pure functions
take an explicit `now` so the tests (and the worker's own logs) never depend on wall-clock time.
Timings here are the regular session only - Muhurat trading and any special/extended session NSE
notifies by circular are deliberately not modelled: the worker simply stays idle then.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Optional, Set
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MarketHolidayRecord

IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


@dataclass(frozen=True)
class SessionStatus:
    is_open: bool
    reason: str
    # Next regular session open (IST) when closed; None while open.
    next_open: Optional[datetime]


def to_ist(now: Optional[datetime] = None) -> datetime:
    """Any aware datetime (or "now") expressed in IST. A naive datetime is treated as UTC, which
    is what every timestamp the platform itself produces is."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(IST)


def is_trading_day(day: date, holidays: Iterable[date] = ()) -> bool:
    return day.weekday() < 5 and day not in set(holidays)


def next_trading_day(day: date, holidays: Iterable[date] = ()) -> date:
    holiday_set = set(holidays)
    candidate = day + timedelta(days=1)
    # Bounded: even a pathological holiday table can't spin this forever.
    for _ in range(60):
        if is_trading_day(candidate, holiday_set):
            return candidate
        candidate += timedelta(days=1)
    return candidate


def session_status(now: Optional[datetime] = None, holidays: Iterable[date] = ()) -> SessionStatus:
    """Pure decision: given the instant and the holiday set, is the regular NSE session open?"""
    holiday_set = set(holidays)
    now_ist = to_ist(now)
    today = now_ist.date()

    def _open_on(day: date) -> datetime:
        return datetime.combine(day, MARKET_OPEN, tzinfo=IST)

    if not is_trading_day(today, holiday_set):
        why = "exchange holiday" if today in holiday_set else "weekend"
        return SessionStatus(False, f"Market closed: {why} ({today.isoformat()})", _open_on(next_trading_day(today, holiday_set)))

    current = now_ist.time()
    if current < MARKET_OPEN:
        return SessionStatus(False, f"Market not yet open (opens {MARKET_OPEN.strftime('%H:%M')} IST)", _open_on(today))
    if current >= MARKET_CLOSE:
        return SessionStatus(
            False, f"Market closed for the day (closed {MARKET_CLOSE.strftime('%H:%M')} IST)",
            _open_on(next_trading_day(today, holiday_set)),
        )
    return SessionStatus(True, "Regular session open", None)


async def load_holidays(session: AsyncSession, exchange: str = "NSE", year: Optional[int] = None) -> Set[date]:
    query = select(MarketHolidayRecord.holiday_date).where(MarketHolidayRecord.exchange == exchange)
    if year is not None:
        query = query.where(
            MarketHolidayRecord.holiday_date >= date(year, 1, 1), MarketHolidayRecord.holiday_date <= date(year, 12, 31)
        )
    rows = await session.scalars(query)
    return set(rows)


async def market_session_status(
    session: AsyncSession, now: Optional[datetime] = None, exchange: str = "NSE"
) -> SessionStatus:
    """DB-backed variant the worker calls once per cycle."""
    return session_status(now, await load_holidays(session, exchange))
