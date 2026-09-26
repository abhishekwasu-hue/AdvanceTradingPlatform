from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.market_data.calendar import (
    IST, is_trading_day, next_trading_day, session_status, to_ist,
)

REPUBLIC_DAY_2026 = date(2026, 1, 26)


def _ist(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def test_weekday_inside_session_is_open():
    status = session_status(_ist(2026, 9, 25, 10, 30))  # Friday
    assert status.is_open
    assert status.next_open is None


def test_open_boundaries_are_inclusive_at_open_and_exclusive_at_close():
    assert session_status(_ist(2026, 9, 25, 9, 15)).is_open
    assert not session_status(_ist(2026, 9, 25, 9, 14)).is_open
    assert session_status(_ist(2026, 9, 25, 15, 29)).is_open
    assert not session_status(_ist(2026, 9, 25, 15, 30)).is_open


def test_after_close_points_to_next_trading_day_open():
    status = session_status(_ist(2026, 9, 25, 16, 0))  # Friday evening
    assert not status.is_open
    assert status.next_open == _ist(2026, 9, 28, 9, 15)  # Monday


def test_weekend_is_closed():
    status = session_status(_ist(2026, 9, 26, 11, 0))  # Saturday
    assert not status.is_open
    assert "weekend" in status.reason
    assert status.next_open == _ist(2026, 9, 28, 9, 15)


def test_exchange_holiday_is_closed_even_midday():
    status = session_status(_ist(2026, 1, 26, 11, 0), holidays={REPUBLIC_DAY_2026})
    assert not status.is_open
    assert "holiday" in status.reason
    assert status.next_open == _ist(2026, 1, 27, 9, 15)


def test_next_trading_day_skips_weekend_and_holiday_run():
    # Fri 23-Jan-2026 -> Sat/Sun -> Mon 26-Jan holiday -> Tue 27-Jan.
    assert next_trading_day(date(2026, 1, 23), holidays={REPUBLIC_DAY_2026}) == date(2026, 1, 27)
    assert is_trading_day(date(2026, 1, 27))
    assert not is_trading_day(date(2026, 1, 25))


def test_utc_input_is_converted_to_ist_before_deciding():
    # 04:00 UTC == 09:30 IST -> open; 10:30 UTC == 16:00 IST -> closed.
    assert session_status(datetime(2026, 9, 25, 4, 0, tzinfo=timezone.utc)).is_open
    assert not session_status(datetime(2026, 9, 25, 10, 30, tzinfo=timezone.utc)).is_open


def test_naive_datetime_is_treated_as_utc():
    assert to_ist(datetime(2026, 9, 25, 4, 0)).hour == 9
    assert to_ist(datetime(2026, 9, 25, 4, 0)).tzinfo == ZoneInfo("Asia/Kolkata")
