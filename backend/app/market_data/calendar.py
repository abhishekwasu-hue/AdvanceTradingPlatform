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
from typing import Dict, Iterable, Optional, Set
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MarketHolidayRecord

IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


@dataclass(frozen=True)
class ExchangeSession:
    """Phase O2 / section 57-61: regular hours of one exchange family, all in IST. `always_open`
    covers 24x7 venues (crypto). `no_new_entries_after` / `square_off_at` are the worker's intraday
    cut-offs for that venue (NSE: 15:00 / 15:15; MCX: 23:00 / 23:15; crypto: never)."""

    name: str
    open: time
    close: time
    no_new_entries_after: Optional[time]
    square_off_at: Optional[time]
    weekdays_only: bool = True
    always_open: bool = False
    holiday_calendar: str = "NSE"   # which `market_holidays.exchange` rows apply


EXCHANGE_SESSIONS = {
    "NSE": ExchangeSession("NSE", time(9, 15), time(15, 30), time(15, 0), time(15, 15), holiday_calendar="NSE"),
    "MCX": ExchangeSession("MCX", time(9, 0), time(23, 30), time(23, 0), time(23, 15), holiday_calendar="MCX"),
    "CRYPTO": ExchangeSession("CRYPTO", time(0, 0), time(23, 59, 59), None, None, weekdays_only=False, always_open=True,
                              holiday_calendar="CRYPTO"),
}
# Segments that trade on an exchange family's clock and calendar.
_SESSION_ALIASES = {"NSE": "NSE", "BSE": "NSE", "NFO": "NSE", "BFO": "NSE", "NSE_EQ": "NSE", "NSE_FO": "NSE", "BSE_EQ": "NSE",
                    "MCX": "MCX", "MCX_FO": "MCX", "CRYPTO": "CRYPTO", "BINANCE": "CRYPTO", "COINDCX": "CRYPTO", "WAZIRX": "CRYPTO"}


def session_family(exchange: Optional[str]) -> str:
    """NFO/BFO/BSE -> NSE clock; MCX segments -> MCX; crypto venues -> CRYPTO; unknown -> NSE."""
    return _SESSION_ALIASES.get((exchange or "NSE").upper(), "NSE")


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


def trading_day_start(now: Optional[datetime] = None) -> datetime:
    """P0.5 / T6: the start of the current Indian trading day (00:00 IST) as an aware UTC instant. "Today's"
    trades, losses and counts are bounded by this, never by UTC midnight (05:30 IST), which split one session
    in two and let a pre-05:30 loss vanish from the daily limit."""
    ist_midnight = to_ist(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return ist_midnight.astimezone(timezone.utc)


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


def session_status(now: Optional[datetime] = None, holidays: Iterable[date] = (), exchange: str = "NSE") -> SessionStatus:
    """Pure decision: given the instant and the holiday set, is the regular session of this
    exchange family open? Defaults to NSE, the clock every earlier caller meant."""
    spec = EXCHANGE_SESSIONS[session_family(exchange)]
    holiday_set = set(holidays)
    now_ist = to_ist(now)
    today = now_ist.date()
    if spec.always_open:
        return SessionStatus(True, f"{spec.name} trades around the clock", None)

    def _is_day(day: date) -> bool:
        return (day.weekday() < 5 or not spec.weekdays_only) and day not in holiday_set

    def _next_day(day: date) -> date:
        candidate = day + timedelta(days=1)
        for _ in range(60):
            if _is_day(candidate):
                return candidate
            candidate += timedelta(days=1)
        return candidate

    def _open_on(day: date) -> datetime:
        return datetime.combine(day, spec.open, tzinfo=IST)

    if not _is_day(today):
        why = "exchange holiday" if today in holiday_set else "weekend"
        return SessionStatus(False, f"{spec.name} closed: {why} ({today.isoformat()})", _open_on(_next_day(today)))

    current = now_ist.time()
    if current < spec.open:
        return SessionStatus(False, f"{spec.name} not yet open (opens {spec.open.strftime('%H:%M')} IST)", _open_on(today))
    if current >= spec.close:
        return SessionStatus(False, f"{spec.name} closed for the day (closed {spec.close.strftime('%H:%M')} IST)", _open_on(_next_day(today)))
    return SessionStatus(True, f"{spec.name} regular session open", None)


def intraday_cutoffs(exchange: Optional[str]) -> tuple[Optional[time], Optional[time]]:
    """(no_new_entries_after, square_off_at) for the venue's clock; (None, None) for 24x7 venues."""
    spec = EXCHANGE_SESSIONS[session_family(exchange)]
    return spec.no_new_entries_after, spec.square_off_at


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
    family = session_family(exchange)
    return session_status(now, await load_holidays(session, EXCHANGE_SESSIONS[family].holiday_calendar), family)


async def all_session_statuses(session: AsyncSession, now: Optional[datetime] = None) -> Dict[str, SessionStatus]:
    """Phase O2: one status per exchange family - the worker processes whichever venues are open
    and the status endpoint shows all of them."""
    return {name: await market_session_status(session, now, name) for name in EXCHANGE_SESSIONS}
