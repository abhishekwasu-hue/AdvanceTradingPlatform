"""NIFTY and BANKNIFTY expiries come from NSE's bhavcopies, never from a weekday rule.

Part 1 checks the builder (both bhavcopy layouts, confirmation on the expiry day, monthly = has a future) on small
hand-made files. Part 2 checks the committed data file (built by the 'NSE expiry data' workflow from the real
archive) against the backtest calendar: every BANKNIFTY expiry of every month in the coverage, and the 2024 changes.
"""
import datetime as dt
from collections import defaultdict

import pytest

from app.backtest.options import ExpiryCalendar
from app.core.enums import ExpiryRule
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


def test_a_moved_monthly_goes_to_the_day_its_future_expired_not_the_nearest_weekly():
    # NIFTY Sep 2025: the series listed for Thu 25 Sep was re-dated to Tue 30 Sep; Tue 23 Sep is a weekly.
    files = {
        D("2025-09-01"): legacy(("FUTIDX", "NIFTY", "25-Sep-2025"), ("OPTIDX", "NIFTY", "25-Sep-2025"),
                                ("OPTIDX", "NIFTY", "23-Sep-2025")),
        D("2025-09-08"): legacy(("OPTIDX", "NIFTY", "23-Sep-2025")),
        D("2025-09-15"): legacy(("OPTIDX", "NIFTY", "23-Sep-2025")),
        D("2025-09-23"): legacy(("OPTIDX", "NIFTY", "23-Sep-2025"), ("FUTIDX", "NIFTY", "30-Sep-2025")),
        D("2025-09-25"): legacy(("FUTIDX", "NIFTY", "30-Sep-2025")),
        D("2025-09-29"): legacy(("FUTIDX", "NIFTY", "30-Sep-2025")),
        D("2025-09-30"): legacy(("FUTIDX", "NIFTY", "30-Sep-2025"), ("OPTIDX", "NIFTY", "30-Sep-2025")),
    }
    rows, meta = ne.build(D("2025-09-01"), D("2025-09-30"), files.get, workers=1)
    got = {(r["expiry"], r["kind"], r["first_seen"]) for r in rows}
    assert ("2025-09-23", "weekly", "2025-09-01") in got and ("2025-09-30", "monthly", "2025-09-01") in got
    assert {"symbol": "NIFTY", "from": "2025-09-25", "to": "2025-09-30"} in meta["moved"]


def test_a_moved_weekly_goes_to_the_nearest_day_not_into_a_nearby_monthly():
    # A Tue 23 weekly (no future, listed a few weeks ahead) re-dated by a late holiday to Mon 22; the monthly (with its
    # future) expires Tue 29, six days later. The weekly lands on the 22nd and keeps its first listing date.
    files = {
        D("2026-09-01"): legacy(("OPTIDX", "NIFTY", "23-Sep-2026"), ("FUTIDX", "NIFTY", "29-Sep-2026"),
                                ("OPTIDX", "NIFTY", "29-Sep-2026")),
        D("2026-09-07"): legacy(("OPTIDX", "NIFTY", "23-Sep-2026"), ("FUTIDX", "NIFTY", "29-Sep-2026")),
        D("2026-09-14"): legacy(("OPTIDX", "NIFTY", "23-Sep-2026"), ("FUTIDX", "NIFTY", "29-Sep-2026")),
        D("2026-09-21"): legacy(("OPTIDX", "NIFTY", "22-Sep-2026"), ("FUTIDX", "NIFTY", "29-Sep-2026")),
        D("2026-09-22"): legacy(("OPTIDX", "NIFTY", "22-Sep-2026"), ("FUTIDX", "NIFTY", "29-Sep-2026")),
        D("2026-09-28"): legacy(("FUTIDX", "NIFTY", "29-Sep-2026")),
        D("2026-09-29"): legacy(("FUTIDX", "NIFTY", "29-Sep-2026"), ("OPTIDX", "NIFTY", "29-Sep-2026")),
    }
    rows, meta = ne.build(D("2026-09-01"), D("2026-09-29"), files.get, workers=1)
    got = {(r["expiry"], r["kind"], r["first_seen"]) for r in rows}
    assert ("2026-09-22", "weekly", "2026-09-01") in got and ("2026-09-29", "monthly", "2026-09-01") in got
    assert {"symbol": "NIFTY", "from": "2026-09-23", "to": "2026-09-22"} in meta["moved"]


def test_a_far_contract_gone_from_the_newest_file_is_not_listed():
    # A long-dated Thursday contract listed in 2025, re-dated to Tuesday later: the newest file only prints the Tuesday.
    files = {
        D("2025-08-04"): legacy(("OPTIDX", "NIFTY", "31-Dec-2026"), ("OPTIDX", "NIFTY", "29-Dec-2026"),
                                ("FUTIDX", "NIFTY", "28-Aug-2025")),
        D("2025-08-11"): legacy(("OPTIDX", "NIFTY", "29-Dec-2026"), ("FUTIDX", "NIFTY", "28-Aug-2025")),
    }
    rows, meta = ne.build(D("2025-08-04"), D("2025-08-11"), files.get, workers=1)
    assert [r["expiry"] for r in rows if r["expiry"] > "2026"] == ["2026-12-29"]
    assert meta["delisted"] == [{"symbol": "NIFTY", "expiry": "2026-12-31", "first_seen": "2025-08-04"}]


def test_every_data_driven_symbol_is_strict_in_the_builder():
    assert set(expiry_data.DATA_DRIVEN) <= set(ne.STRICT)


def test_build_refuses_holes_unexplained_drops_and_shrinking():
    base = {D("2024-01-02"): legacy(("OPTIDX", "BANKNIFTY", "03-Jan-2024"), ("FUTIDX", "BANKNIFTY", "25-Jan-2024")),
            D("2024-01-03"): legacy(("OPTIDX", "BANKNIFTY", "03-Jan-2024"), ("FUTIDX", "BANKNIFTY", "25-Jan-2024")),
            D("2024-01-15"): legacy(("FUTIDX", "BANKNIFTY", "25-Jan-2024"))}
    # the week of 8 Jan has no file at all (a block, not a holiday week)
    with pytest.raises(ne.BuildRefused, match="week"):
        ne.build(D("2024-01-01"), D("2024-01-16"), base.get, workers=1)
    # every week has a file, but the 3 Jan expiry has no file on its day and no move explains it
    files = {**base, D("2024-01-08"): legacy(("FUTIDX", "BANKNIFTY", "25-Jan-2024"))}
    del files[D("2024-01-03")]
    with pytest.raises(ne.BuildRefused, match="no move"):
        ne.build(D("2024-01-01"), D("2024-01-16"), files.get, workers=1)
    # a new result that knows less than the committed file is refused
    import tempfile
    from pathlib import Path
    good = {**base, D("2024-01-08"): legacy(("FUTIDX", "BANKNIFTY", "25-Jan-2024"))}
    rows, meta = ne.build(D("2024-01-01"), D("2024-01-16"), good.get, workers=1)
    with tempfile.TemporaryDirectory() as tmp:
        ne.write(rows, meta, Path(tmp))
        ne.check_against(Path(tmp), rows, meta)                        # the same result passes
        with pytest.raises(ne.BuildRefused, match="before the current"):
            ne.check_against(Path(tmp), rows, dict(meta, coverage_end="2024-01-08"))
        with pytest.raises(ne.BuildRefused, match="fewer"):
            ne.check_against(Path(tmp), rows[1:], meta)


def test_a_403_refuses_the_build_and_is_never_cached_as_no_file(monkeypatch, tmp_path):
    """ATP review 5: a 403 is a block, not a holiday - the run refuses (exit 2, nothing written) and the cache keeps
    no 'no file' marker for it; a 404 on an old day is cached, on a day that may be unpublished yet it is not."""
    import io
    import urllib.error

    def answer(code):
        def urlopen(req, timeout=60):
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(b""))
        return urlopen
    old, recent = D("2024-01-02"), dt.date.today() - dt.timedelta(days=1)
    while recent.weekday() >= 5:
        recent -= dt.timedelta(days=1)
    archive = ne.Archive(tmp_path / "cache", pause=0, retries=1)
    monkeypatch.setattr(ne.urllib.request, "urlopen", answer(403))
    assert archive.day(old) is None and archive.forbidden == 2
    assert not list((tmp_path / "cache").iterdir())                       # nothing remembered for a refused day
    monkeypatch.setattr(ne.urllib.request, "urlopen", answer(404))
    assert archive.day(old) is None and (tmp_path / "cache" / "20240102.none").exists()
    assert archive.day(recent) is None and not (tmp_path / "cache" / f"{recent:%Y%m%d}.none").exists()

    # main(): any 403 left after the retries refuses the run, even when the guards would have passed
    good = {D("2024-01-02"): legacy(("OPTIDX", "BANKNIFTY", "03-Jan-2024"), ("FUTIDX", "BANKNIFTY", "25-Jan-2024")),
            D("2024-01-03"): legacy(("OPTIDX", "BANKNIFTY", "03-Jan-2024"), ("FUTIDX", "BANKNIFTY", "25-Jan-2024")),
            D("2024-01-08"): legacy(("FUTIDX", "BANKNIFTY", "25-Jan-2024")),
            D("2024-01-15"): legacy(("FUTIDX", "BANKNIFTY", "25-Jan-2024"))}

    class Blocked(ne.Archive):
        """Every week still has a file (the guards pass), but one read was refused with a 403."""
        def day(self, d):
            if d == D("2024-01-15"):
                self.forbidden += 1
            return good.get(d)
    monkeypatch.setattr(ne, "Archive", Blocked)
    out = tmp_path / "out"
    assert ne.main(["--start", "2024-01-01", "--end", "2024-01-16", "--out", str(out), "--workers", "1"]) == 2
    assert not out.exists()


def test_check_against_compares_from_the_new_start_and_older_rows_are_kept(tmp_path):
    """ATP review 6: a run from a later --start is compared only on the expiries it read, and keeps the committed rows
    before its start (and a contract's earlier first-seen day)."""
    full = {D("2024-01-02"): legacy(("OPTIDX", "BANKNIFTY", "03-Jan-2024"), ("FUTIDX", "BANKNIFTY", "25-Jan-2024")),
            D("2024-01-03"): legacy(("OPTIDX", "BANKNIFTY", "03-Jan-2024"), ("FUTIDX", "BANKNIFTY", "25-Jan-2024")),
            D("2024-01-08"): legacy(("FUTIDX", "BANKNIFTY", "25-Jan-2024")),
            D("2024-01-15"): legacy(("FUTIDX", "BANKNIFTY", "25-Jan-2024")),
            D("2024-01-22"): legacy(("FUTIDX", "BANKNIFTY", "25-Jan-2024")),
            D("2024-01-25"): legacy(("FUTIDX", "BANKNIFTY", "25-Jan-2024")),
            D("2024-01-29"): legacy(("FUTIDX", "BANKNIFTY", "29-Feb-2024"))}
    rows, meta = ne.build(D("2024-01-01"), D("2024-01-30"), full.get, workers=1)
    ne.write(rows, meta, tmp_path)
    assert [r["expiry"] for r in rows if r["symbol"] == "BANKNIFTY"][:2] == ["2024-01-03", "2024-01-25"]
    later, later_meta = ne.build(D("2024-01-08"), D("2024-01-30"), full.get, workers=1)
    assert "2024-01-03" not in {r["expiry"] for r in later}                 # this run never read the 3 Jan expiry
    with pytest.raises(ne.BuildRefused, match="fewer"):
        ne.check_against(tmp_path, later, later_meta)                    # old rule: counts years it never read
    ne.check_against(tmp_path, later, later_meta, D("2024-01-08"))      # compared from its own start: passes
    merged = ne.merge_previous(tmp_path, later, later_meta, D("2024-01-08"))
    assert [(r["expiry"], r["first_seen"]) for r in merged if r["symbol"] == "BANKNIFTY"][:2] == \
        [("2024-01-03", "2024-01-02"), ("2024-01-25", "2024-01-02")]     # kept, and the earlier first-seen day
    assert later_meta["coverage_start"] == meta["coverage_start"] and later_meta["rows"] == len(merged)


# --- part 2: the committed data and the backtest calendar ----------------------------------------------------------------
def _rows(symbol):
    rows = expiry_data.listed(symbol)
    assert rows, f"backend/app/instruments/data/nse_index_expiries.csv must hold {symbol} (run the 'NSE expiry data' workflow)"
    return rows


def _bank():
    return _rows("BANKNIFTY")


@pytest.mark.parametrize("symbol", sorted(expiry_data.DATA_DRIVEN))
def test_data_file_covers_from_2016_and_every_expiry_is_exchange_confirmed(symbol):
    start, end = expiry_data.coverage()
    assert start <= D("2016-01-08") and end >= D("2026-09-30")
    rows = _rows(symbol)
    assert all(e.weekday() < 5 for e, _, _ in rows)
    # one monthly (the series with a future) in every month of the coverage
    monthly = defaultdict(list)
    for e, kind, _ in rows:
        if kind == "monthly":
            monthly[(e.year, e.month)].append(e)
    months = {(y, m) for y in range(start.year, end.year + 1) for m in range(1, 13) if (y, m) >= (start.year, start.month)
              and (y, m) <= (end.year, end.month)}
    assert {k for k, v in monthly.items() if len(v) == 1} >= months


@pytest.mark.parametrize("symbol", sorted(expiry_data.DATA_DRIVEN))
def test_every_month_of_every_year_matches_the_data(symbol):
    """The backtest calendar returns exactly the exchange's dates: each expiry is the next one after the previous expiry,
    and the monthly-only calendar returns each month's monthly - from the first month of the coverage to the last."""
    start, end = expiry_data.coverage()
    rows = [r for r in _rows(symbol) if r[0] <= end]
    every = ExpiryCalendar.for_underlying(symbol)
    monthly_only = ExpiryCalendar.for_underlying(symbol, weekly=False)
    checked = 0
    for (prev, _, _), (exp, kind, seen) in zip(rows, rows[1:]):
        day = prev + dt.timedelta(days=1)
        if day < start or day > end or seen > day:
            continue                                            # not yet seen listed on that day (causal)
        assert every.expiries(day, 1) == [exp], f"after {prev}"
        checked += 1
    assert checked >= 0.9 * (len(rows) - 1)
    for exp, kind, _ in rows:          # `_` = first_seen
        first = exp.replace(day=1)
        if kind == "monthly" and first >= start and _ <= first:
            assert monthly_only.expiries(first, 1) == [exp], f"monthly of {first:%b %Y}"
            assert every.select(ExpiryRule.MONTHLY, first) == exp, f"MONTHLY rule in {first:%b %Y}"
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


def test_nifty_weeklies_thursday_then_tuesday_and_holiday_moves():
    rows = _rows("NIFTY")
    weekly = [e for e, k, _ in rows if k == "weekly"]
    assert weekly[0] == D("2019-02-14")                                      # the first NIFTY weekly
    # Thursday era: a holiday moves the expiry to an earlier day, never later
    thu = [e for e, _, _ in rows if D("2019-02-14") <= e < D("2025-09-01")]
    assert all(e.weekday() <= 3 for e in thu) and sum(e.weekday() == 3 for e in thu) >= 0.9 * len(thu)
    # Tuesday from Sep 2025 (Monday where the Tuesday is a holiday)
    _, end = expiry_data.coverage()
    tue = [e for e, _, _ in rows if D("2025-09-01") <= e <= end]               # long-dated listings keep printed dates
    assert tue[0] == D("2025-09-02") and all(e.weekday() <= 1 for e in tue) and sum(e.weekday() == 1 for e in tue) >= 0.9 * len(tue)
    assert max(e for e in thu) == D("2025-08-28")
    # holiday moves the exchange made - no weekday rule produces these
    dates = {e for e, _, _ in rows}
    for moved, rule_day in ((D("2018-03-28"), D("2018-03-29")), (D("2021-11-03"), D("2021-11-04")),
                            (D("2026-10-19"), D("2026-10-20"))):
        assert moved in dates and rule_day not in dates
    assert ExpiryCalendar.for_underlying("NIFTY 50").data_symbol == "NIFTY"
    with pytest.raises(expiry_data.ExpiryDataMissing):
        expiry_calendar.expiry_weekday(D("2024-01-01"), "NIFTY")


def test_no_weekday_rule_is_left_for_banknifty_and_outside_the_data_is_an_error():
    with pytest.raises(expiry_data.ExpiryDataMissing):
        expiry_calendar.expiry_weekday(D("2024-01-01"), "BANKNIFTY")
    with pytest.raises(expiry_data.ExpiryDataMissing):
        expiry_calendar.rule_expiries(expiry_calendar.TradingCalendar([]), D("2024-01-01"), D("2024-02-01"), "BANKNIFTY")
    start, end = expiry_data.coverage()
    cal = ExpiryCalendar.for_underlying("BANKNIFTY")
    with pytest.raises(expiry_data.ExpiryDataMissing):
        cal.expiries(start - dt.timedelta(days=1))
    # after the last file read, within the grace: only what was listed by then, nothing invented
    later = end + dt.timedelta(days=expiry_data.STALE_GRACE_DAYS)
    assert cal.expiries(later, 3) == [e for e, _, seen in _bank() if e >= later][:3]
    # past the grace the file is stale: an error, never a silent "no expiry" (the backtest skips and counts the bar)
    for day in (later + dt.timedelta(days=1), D("2099-01-01")):
        with pytest.raises(expiry_data.ExpiryDataStale):
            cal.expiries(day)
        with pytest.raises(expiry_data.ExpiryDataStale):
            cal.select(ExpiryRule.NEAREST, day)
    # the far leg is chosen as listed on the bar day, also when the near expiry lies past the last file read
    near = cal.select(ExpiryRule.NEAREST, end)
    assert cal.far_expiry(near, as_of=end) == next(e for e, _, seen in _bank() if e > near and seen <= end)
    # aliases reach the same data; MONTHLY never picks a weekly that falls after the monthly (31 Jan 2024)
    assert ExpiryCalendar.for_underlying("NIFTY BANK").data_symbol == "BANKNIFTY"
    assert ExpiryCalendar.for_underlying("Bank Nifty").expiries(D("2024-01-02"), 1) == cal.expiries(D("2024-01-02"), 1)
    assert cal.select(ExpiryRule.MONTHLY, D("2024-01-02")) == D("2024-01-25")
    # the ExpiryBook (ported from Trade) reads the same data
    book = expiry_calendar.ExpiryBook(expiry_calendar.TradingCalendar([D("2024-01-02")]), underlying="NIFTY BANK")
    assert book.source == "listed" and len(book.items) == len(_bank())
    # an explicit weekday still pins a synthetic calendar (a what-if run)
    assert ExpiryCalendar.for_underlying("BANKNIFTY", weekday=3).data_symbol is None


def test_refresh_workflow_starts_ci_first_never_fails_on_the_pr_and_caches_bhavcopies():
    """ATP review 3 + 7: CI is dispatched on the data branch before the pull request is opened; a pull request Actions
    may not open is a warning (and run summary), not a failed run; the bhavcopy download directory is cached."""
    from pathlib import Path

    import yaml
    root = Path(__file__).resolve().parents[2]
    steps = yaml.safe_load((root / ".github/workflows/nse-expiries.yml").read_text())["jobs"]["build"]["steps"]
    names = [s.get("name", s.get("uses", "")) for s in steps]
    ci, pr = names.index("Start CI on the data branch"), names.index("Open the refresh pull request")
    assert ci < pr and "gh workflow run ci.yml --ref data/nse-expiries" in steps[ci]["run"]
    assert "gh workflow run" not in steps[pr]["run"] and "exit 1" not in steps[pr]["run"]
    assert "::warning" in steps[pr]["run"] and "GITHUB_STEP_SUMMARY" in steps[pr]["run"]
    cache = next(s for s in steps if s.get("uses", "").startswith("actions/cache@"))
    assert len(cache["uses"].split("@")[1]) == 40                         # pinned to a commit (P0.7)
    assert cache["with"]["path"] == "${{ runner.temp }}/bhav"
    build = next(s for s in steps if s.get("name") == "Read the bhavcopies and build the expiry file")
    assert names.index(cache.get("name", "")) < names.index(build["name"]) and '--cache "$RUNNER_TEMP/bhav"' in build["run"]
