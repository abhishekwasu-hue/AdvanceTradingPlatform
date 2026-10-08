"""BANKNIFTY expiries come from NSE's bhavcopies, never from a weekday rule.

Part 1 checks the builder (both bhavcopy layouts, confirmation on the expiry day, monthly = has a future) on small
hand-made files. Part 2 checks the committed data file (built by the 'NSE expiry data' workflow from the real
archive) against the backtest calendar: every BANKNIFTY expiry of every month in the coverage, and the 2024 changes.
"""
import datetime as dt
from collections import defaultdict

import pytest

from app.backtest.options import ExpiryCalendar
from app.instruments import expiry_calendar, expiry_data, nse_expiries as ne

D = dt.date.fromisoformat

LEGACY_HEAD = "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,\n"
UDIFF_HEAD = "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp\n"


def legacy(*rows):
    return LEGACY_HEAD + "".join(f"{i},{s},{e},0,XX,1,1,1,1,1,1,1,1,1,X,\n" for i, s, e in rows)


def udiff(*rows):
    return UDIFF_HEAD + "".join(f"2024-07-08,2024-07-08,FO,NSE,{t},1,,{s},,{e},{e},0,CE\n" for t, s, e in rows)


# --- part 1: the builder ------------------------------------------------------------------------------------------------
def test_both_bhavcopy_layouts_parse_index_contracts_only():
    got = ne.parse_bhavcopy(legacy(("FUTIDX", "BANKNIFTY", "25-Jan-2024"), ("OPTIDX", "BANKNIFTY", "03-Jan-2024"),
                                   ("OPTSTK", "SBIN", "25-Jan-2024"), ("OPTIDX", "NIFTY", "04-JAN-2024")))
    assert got == {("BANKNIFTY", D("2024-01-25"), True), ("BANKNIFTY", D("2024-01-03"), False), ("NIFTY", D("2024-01-04"), False)}
    got = ne.parse_bhavcopy(udiff(("IDO", "BANKNIFTY", "2024-07-10"), ("IDF", "BANKNIFTY", "2024-07-31"), ("STO", "SBIN", "2024-07-25")))
    assert got == {("BANKNIFTY", D("2024-07-10"), False), ("BANKNIFTY", D("2024-07-31"), True)}
    with pytest.raises(ValueError):
        ne.parse_bhavcopy("a,b\n1,2\n")
    assert ne.legacy_url(D("2024-01-01")).endswith("/DERIVATIVES/2024/JAN/fo01JAN2024bhav.csv.zip")
    assert ne.udiff_url(D("2024-07-08")).endswith("/content/fo/BhavCopy_NSE_FO_0_0_0_20240708_F_0000.csv.zip")


def test_build_confirms_each_expiry_on_its_own_day_and_reads_the_kind_from_futures():
    # Week 1 (Mon 1 Jan 2024 a holiday here -> Tue 2 Jan read): weekly 3 Jan, monthly 25 Jan (has a future) and a
    # weekly first printed as 11 Jan that the exchange moved to 10 Jan (the 10 Jan file shows it under 10 Jan).
    # Week 3 prints a weekly for Thu 18 Jan; a late holiday moved it to Wed 17 Jan, which no weekly sample shows -
    # the 17 Jan file does, so it is found. A far contract (Mar) has no future yet: the last of its month = monthly.
    files = {
        D("2024-01-02"): legacy(("OPTIDX", "BANKNIFTY", "03-Jan-2024"), ("OPTIDX", "BANKNIFTY", "11-Jan-2024"),
                                ("FUTIDX", "BANKNIFTY", "25-Jan-2024"), ("OPTIDX", "BANKNIFTY", "25-Jan-2024")),
        D("2024-01-03"): legacy(("OPTIDX", "BANKNIFTY", "03-Jan-2024"), ("FUTIDX", "BANKNIFTY", "25-Jan-2024")),
        D("2024-01-08"): legacy(("OPTIDX", "BANKNIFTY", "10-Jan-2024"), ("FUTIDX", "BANKNIFTY", "25-Jan-2024")),
        D("2024-01-10"): legacy(("OPTIDX", "BANKNIFTY", "10-Jan-2024"), ("FUTIDX", "BANKNIFTY", "25-Jan-2024")),
        D("2024-01-15"): legacy(("FUTIDX", "BANKNIFTY", "25-Jan-2024"), ("OPTIDX", "BANKNIFTY", "18-Jan-2024")),
        D("2024-01-17"): legacy(("OPTIDX", "BANKNIFTY", "17-Jan-2024"), ("FUTIDX", "BANKNIFTY", "25-Jan-2024")),
        D("2024-01-22"): legacy(("FUTIDX", "BANKNIFTY", "25-Jan-2024"), ("OPTIDX", "BANKNIFTY", "27-Mar-2024"),
                                ("OPTIDX", "BANKNIFTY", "20-Mar-2024")),
    }
    rows, meta = ne.build(D("2024-01-01"), D("2024-01-23"), files.get, workers=1)
    assert [(r["expiry"], r["kind"], r["first_seen"], r["status"]) for r in rows] == [
        ("2024-01-03", "weekly", "2024-01-02", "expired"),
        ("2024-01-10", "weekly", "2024-01-02", "expired"),      # listed since the 11 Jan contract was first seen
        ("2024-01-17", "weekly", "2024-01-15", "expired"),      # found on the day it really expired
        ("2024-01-25", "monthly", "2024-01-02", "listed"),     # after the last file read: listed, not confirmed yet
        ("2024-03-20", "weekly", "2024-01-22", "listed"),
        ("2024-03-27", "monthly", "2024-01-22", "listed"),     # no future yet, last listed expiry of March
    ]
    assert meta["dropped"] == [{"symbol": "BANKNIFTY", "expiry": "2024-01-11", "reason": "no bhavcopy that day"},
                               {"symbol": "BANKNIFTY", "expiry": "2024-01-18", "reason": "no bhavcopy that day"}]
    assert meta["moved"] == [{"symbol": "BANKNIFTY", "from": "2024-01-11", "to": "2024-01-10"},
                             {"symbol": "BANKNIFTY", "from": "2024-01-18", "to": "2024-01-17"}]
    assert (meta["coverage_start"], meta["coverage_end"]) == ("2024-01-02", "2024-01-22")


# --- part 2: the committed data and the backtest calendar ----------------------------------------------------------------
def _bank():
    rows = expiry_data.listed("BANKNIFTY")
    assert rows, "backend/app/instruments/data/nse_index_expiries.csv must hold BANKNIFTY (run the 'NSE expiry data' workflow)"
    return rows


def test_data_file_covers_banknifty_from_2016_and_every_expiry_is_exchange_confirmed():
    start, end = expiry_data.coverage()
    assert start <= D("2016-01-08") and end >= D("2026-09-30")
    rows = _bank()
    assert all(e.weekday() < 5 for e, _, _ in rows)
    # one monthly (the series with a future) in every month of the coverage
    monthly = defaultdict(list)
    for e, kind, _ in rows:
        if kind == "monthly":
            monthly[(e.year, e.month)].append(e)
    months = {(y, m) for y in range(start.year, end.year + 1) for m in range(1, 13) if (y, m) >= (start.year, start.month)
              and (y, m) <= (end.year, end.month)}
    assert {k for k, v in monthly.items() if len(v) == 1} >= months


def test_every_month_of_every_year_matches_the_data():
    """The backtest calendar returns exactly the exchange's dates: each expiry is the next one after the previous expiry,
    and the monthly-only calendar returns each month's monthly - from the first month of the coverage to the last."""
    start, end = expiry_data.coverage()
    rows = [r for r in _bank() if r[0] <= end]
    every = ExpiryCalendar.for_underlying("BANKNIFTY")
    monthly_only = ExpiryCalendar.for_underlying("BANKNIFTY", weekly=False)
    checked = 0
    for (prev, _, _), (exp, kind, seen) in zip(rows, rows[1:]):
        day = prev + dt.timedelta(days=1)
        if day < start or day > end or seen > day + dt.timedelta(days=6 - day.weekday()):
            continue                                            # not listed yet on that day (weekly precision)
        assert every.expiries(day, 1) == [exp], f"after {prev}"
        checked += 1
    assert checked >= 0.9 * (len(rows) - 1)
    for exp, kind, _ in rows:
        first = exp.replace(day=1)
        if kind == "monthly" and first >= start:
            assert monthly_only.expiries(first, 1) == [exp], f"monthly of {first:%b %Y}"
            assert every.expiries(exp, 1) == [exp]


def test_the_2024_changes_are_in_the_data():
    rows = _bank()
    weekly_2024 = [e for e, k, _ in rows if k == "weekly" and e.year == 2024]
    # Weekly BANKNIFTY expired on Wednesdays in 2024 (a holiday moves it to the day before, never later)...
    assert weekly_2024 and all(e.weekday() <= 2 for e in weekly_2024)
    assert sum(e.weekday() == 2 for e in weekly_2024) >= 0.8 * len(weekly_2024)
    # ...and the weekly series ended in November 2024: monthlies only afterwards.
    last_weekly = max(e for e, k, _ in rows if k == "weekly")
    assert D("2024-11-01") <= last_weekly <= D("2024-11-30")
    assert all(k == "monthly" for e, k, _ in rows if e > last_weekly)
    # The monthly expiry day changed more than once from 2024 on (read from the data, reported in WORK_LOG).
    monthly_days = {e.weekday() for e, k, _ in rows if k == "monthly" and D("2023-01-01") <= e <= D("2026-09-30")
                    and (e + dt.timedelta(days=7)).month != e.month}    # not holiday-shifted into the week before
    assert len(monthly_days) >= 2


def test_no_weekday_rule_is_left_for_banknifty_and_outside_the_data_is_an_error():
    with pytest.raises(expiry_data.ExpiryDataMissing):
        expiry_calendar.expiry_weekday(D("2024-01-01"), "BANKNIFTY")
    with pytest.raises(expiry_data.ExpiryDataMissing):
        expiry_calendar.rule_expiries(expiry_calendar.TradingCalendar([]), D("2024-01-01"), D("2024-02-01"), "BANKNIFTY")
    start, end = expiry_data.coverage()
    with pytest.raises(expiry_data.ExpiryDataMissing):
        ExpiryCalendar.for_underlying("BANKNIFTY").expiries(end + dt.timedelta(days=1))
    with pytest.raises(expiry_data.ExpiryDataMissing):
        ExpiryCalendar.for_underlying("BANKNIFTY").expiries(start - dt.timedelta(days=1))
    # the ExpiryBook (ported from Trade) reads the same data
    book = expiry_calendar.ExpiryBook(expiry_calendar.TradingCalendar([D("2024-01-02")]), underlying="NIFTY BANK")
    assert book.source == "listed" and len(book.items) == len(_bank())
    # an explicit weekday still pins a synthetic calendar (a what-if run)
    assert ExpiryCalendar.for_underlying("BANKNIFTY", weekday=3).data_symbol is None
