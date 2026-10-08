"""Trade port, part B: the dated NSE expiry calendar / lot sizes (app.instruments.expiry_calendar) and the dated India
cost model (app.execution.india_costs) with its ATP integration (PaperBroker, the option backtester's calendar).

Tests ported from Trade@0df3e09d0aa5942e3352327d5b998c5f6939ac61, tests/test_elliott_e3.py (calendar / choice / DTE /
lot / costs), plus ATP integration tests."""
import datetime as dt

import pandas as pd
import pytest

from app.backtest.options import ExpiryCalendar, lot_size_on
from app.execution import india_costs as CO
from app.execution.paper_broker import PaperBroker
from app.instruments import expiry_calendar as CT


def weekdays(a, b, minus=()):
    d, out = pd.Timestamp(a).date(), []
    while d <= pd.Timestamp(b).date():
        if d.weekday() < 5 and d not in {pd.Timestamp(x).date() for x in minus}:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def D(x):
    return pd.Timestamp(x).date()


# --- expiry calendar / choice (ported) -----------------------------------------------------------------------------------
def test_rule_expiries_tuesday_era_and_holiday_shift():
    cal = CT.TradingCalendar(weekdays("2026-09-01", "2026-11-30", minus=["2026-10-02", "2026-10-20"]))
    ex = dict(CT.rule_expiries(cal, "2026-09-15", "2026-10-31"))
    assert ex[D("2026-09-29")] == "monthly" and ex[D("2026-10-06")] == "weekly"
    assert D("2026-10-19") in ex and D("2026-10-20") not in ex                     # Tuesday holiday -> the trading day before
    assert ex[D("2026-10-27")] == "monthly"


def test_rule_expiries_thursday_era_and_pre_weekly():
    cal = CT.TradingCalendar(weekdays("2018-01-01", "2024-12-31"))
    ex = {d: k for d, k in CT.rule_expiries(cal, "2018-03-01", "2018-04-30") if D("2018-03-01") <= d <= D("2018-04-30")}
    assert set(ex) == {D("2018-03-29"), D("2018-04-26")} and set(ex.values()) == {"monthly"}   # monthly only before weeklies
    ex2 = {d: k for d, k in CT.rule_expiries(cal, "2024-10-01", "2024-10-31") if d.month == 10 and d.year == 2024}
    assert D("2024-10-03") in ex2 and ex2[D("2024-10-31")] == "monthly" and all(d.weekday() == 3 for d in ex2)


def test_choose_expiry_today_moves_to_the_next():
    cal = CT.TradingCalendar(weekdays("2026-09-01", "2026-11-30", minus=["2026-10-02"]))
    book = CT.ExpiryBook(cal, start="2026-09-01", end="2026-11-30")
    assert book.choose(pd.Timestamp("2026-09-28 10:00"))[0] == D("2026-09-29")
    assert book.choose(pd.Timestamp("2026-09-29 13:00"))[0] == D("2026-10-06")     # expiry today -> the next one
    assert book.choose(pd.Timestamp("2026-10-06 09:45"))[0] == D("2026-10-13")
    assert book.choose(pd.Timestamp("2026-10-05 12:30"))[0] == D("2026-10-06")
    assert book.choose(pd.Timestamp("2026-10-05 12:30"), skip=1)[0] == D("2026-10-13")
    assert book.choose(pd.Timestamp("2026-10-05"), min_dte=2)[0] == D("2026-10-13")


def test_listed_book_respects_listing_date():
    cal = CT.TradingCalendar(weekdays("2026-09-01", "2026-11-30"))
    listed = pd.DataFrame({"expiry": [D("2026-10-06"), D("2026-10-13")], "kind": ["weekly", "weekly"],
                           "first_seen": [D("2026-09-01"), D("2026-10-07")]})
    book = CT.ExpiryBook(cal, listed)
    assert book.source == "listed"
    assert book.choose(pd.Timestamp("2026-10-06 10:00")) is None                    # 13 Oct not listed yet (causal)
    assert book.choose(pd.Timestamp("2026-10-07 10:00"))[0] == D("2026-10-13")


def test_dte_days_and_fraction():
    cal = CT.TradingCalendar(weekdays("2026-09-01", "2026-11-30", minus=["2026-10-02"]))
    assert CT.dte_days(cal, D("2026-10-05"), D("2026-10-06")) == 1                  # Monday -> Tuesday
    assert CT.dte_frac(cal, pd.Timestamp("2026-10-05 12:30"), D("2026-10-06")) == pytest.approx((180 + 375) / 375)
    assert CT.dte_frac(cal, pd.Timestamp("2026-10-05 12:30"), D("2026-10-06"), "whole_days") == 1.0
    assert CT.dte_days(cal, D("2026-10-01"), D("2026-10-06")) == 2                  # 2 Oct holiday -> only 5 and 6 Oct
    assert CT.dte_days(cal, D("2026-10-06"), D("2026-10-06")) == 0


def test_lot_size_by_expiry_boundaries_and_listed_override():
    assert CT.lot_size("2019-06-27") == 75 and CT.lot_size("2022-06-30") == 50
    assert CT.lot_size("2024-04-25") == 50 and CT.lot_size("2024-05-02") == 25
    assert CT.lot_size("2024-11-14") == 25 and CT.lot_size("2024-11-21") == 75
    assert CT.lot_size("2025-12-30") == 75 and CT.lot_size("2026-01-06") == 65          # keyed by expiry (from the 6 Jan weekly)
    assert CT.lot_size("2026-10-06", listed_lots={D("2026-10-06"): 75.0}) == 75


def test_calendar_from_spot_drops_muhurat_and_weekend_sessions():
    rows = []
    for d in weekdays("2021-10-25", "2021-11-12", minus=["2021-11-05"]):
        n = 60 if d == D("2021-11-04") else 375                                   # 4 Nov 2021 = Diwali muhurat (one hour)
        rows += [pd.Timestamp(d) + pd.Timedelta(hours=9, minutes=15 + i) for i in range(n)]
    rows += [pd.Timestamp("2021-10-31 18:15") + pd.Timedelta(minutes=i) for i in range(60)]   # a Sunday special session
    cal = CT.TradingCalendar.from_spot(pd.DataFrame({"timestamp": rows}))
    assert not cal.is_trading(D("2021-11-04")) and not cal.is_trading(D("2021-10-31"))
    ex = dict(CT.rule_expiries(cal, "2021-10-25", "2021-11-12"))
    assert D("2021-11-03") in ex and D("2021-11-04") not in ex                      # the actual expiry was 3 Nov
    book = CT.ExpiryBook(cal, start="2021-10-25", end="2021-11-12")
    assert book.choose(pd.Timestamp("2021-11-03 10:00"))[0] == D("2021-11-11")
    assert CT.dte_days(cal, D("2021-11-01"), D("2021-11-03")) == 2


def test_rule_mode_weekly_not_before_listing():
    cal = CT.TradingCalendar(weekdays("2019-01-01", "2019-03-31"))
    book = CT.ExpiryBook(cal, start="2019-01-01", end="2019-03-31")
    assert book.choose(pd.Timestamp("2019-02-08 10:00"))[0] == D("2019-02-28")     # the 14 Feb weekly was listed on 11 Feb
    assert book.choose(pd.Timestamp("2019-02-12 10:00"))[0] == D("2019-02-14")


def test_sensex_weekday_by_date_and_unknown_underlying():
    assert CT.expiry_weekday("2024-06-03", "SENSEX") == 4 and CT.expiry_weekday("2025-03-03", "SENSEX") == 1
    assert CT.expiry_weekday("2026-10-05", "BSE SENSEX") == 3 and CT.strike_step("NIFTY 50") == 50
    with pytest.raises(KeyError):
        CT.expiry_weekday("2026-10-05", "RELIANCE")


# --- costs (ported + extended) -------------------------------------------------------------------------------------------
def test_stt_dates_and_sides():
    assert CO.rates("2016-05-31")["stt_sell"] == 0.00017 and CO.rates("2016-06-01")["stt_sell"] == 0.0005
    assert CO.rates("2026-03-31")["stt_sell"] == 0.001 and CO.rates("2026-04-01")["stt_sell"] == 0.0015
    sell = CO.leg_cost("2026-10-05", "sell", 40.0, 65)
    buy = CO.leg_cost("2026-10-05", "buy", 40.0, 65)
    assert sell["stt"] == pytest.approx(0.0015 * 40 * 65) and buy["stt"] == 0.0           # Rs 40 x 65 -> ~Rs 3.9
    assert buy["stamp"] > 0 and sell["stamp"] == 0.0 and sell["brokerage"] == 20.0
    o = CO.spread_cost("2026-10-05", 20.0, 12.0, 65, opening=True)
    assert o["stt"] == pytest.approx(0.0015 * 20 * 65) and o["brokerage"] == 40.0
    c = CO.spread_cost("2026-10-05", 20.0, 12.0, 65, opening=False)
    assert c["stt"] == pytest.approx(0.0015 * 12 * 65)                                     # closing: the long is sold


def test_segment_rates_and_delivery_both_sides():
    assert CO.rates("2026-10-05", "FUTURE")["stt_sell"] == 0.0005 and CO.rates("2025-06-02", "FUTURE")["stt_sell"] == 0.0002
    d_buy = CO.leg_cost("2026-10-05", "buy", 100.0, 10, "EQUITY_DELIVERY")
    i_buy = CO.leg_cost("2026-10-05", "buy", 100.0, 10, "EQUITY_INTRADAY")
    assert d_buy["stt"] == pytest.approx(1.0) and i_buy["stt"] == 0.0
    assert CO.segment_for("UNDERLYING") == "EQUITY_INTRADAY" and CO.segment_for(None, delivery=True) == "EQUITY_DELIVERY"
    assert CO.leg_cost("2017-06-30", "buy", 100.0, 10, "OPTION", 20.0)["tax"] == pytest.approx(0.15 * (20 + 0.5 + 0.001), abs=1e-6)   # service tax 15% before GST
    with pytest.raises(ValueError):
        CO.rates("2026-10-05", "CRYPTO")
    with pytest.raises(ValueError):
        CO.leg_cost("2026-10-05", "short", 1.0, 1)


def test_exercise_stt_by_date():
    assert CO.exercise_cost("2026-04-01", 40.0, 24540.0, 65) == pytest.approx(0.0015 * 40 * 65)
    assert CO.exercise_cost("2025-06-02", 40.0, 24540.0, 65) == pytest.approx(0.00125 * 40 * 65)
    assert CO.exercise_cost("2016-05-31", 40.0, 8000.0, 75) == pytest.approx(0.00125 * 8000 * 75)   # before Jun 2016: on settlement
    assert CO.exercise_cost("2026-04-01", 0.0, 24540.0, 65) == 0.0


# --- ATP integration ------------------------------------------------------------------------------------------------------
def test_paper_broker_stt_on_the_sold_leg_whichever_side_opened():
    """The old ATP equity profile charged STT on both legs; now STT follows the executed sell order."""
    pb = PaperBroker()
    day = {"entry_date": D("2026-10-05"), "exit_date": D("2026-10-05")}
    long_ = pb.estimate_round_trip_costs(100.0, 110.0, 100, **day)
    short = pb.estimate_round_trip_costs(100.0, 110.0, 100, sold_first=True, **day)
    stt_long, stt_short = 0.00025 * 110 * 100, 0.00025 * 100 * 100            # intraday STT on the sell leg only
    stamp_long, stamp_short = 0.00003 * 100 * 100, 0.00003 * 110 * 100
    assert long_ - short == pytest.approx((stt_long + stamp_long) - (stt_short + stamp_short), abs=0.02)
    delivery = pb.estimate_round_trip_costs(100.0, 110.0, 100, delivery=True, **day)
    assert delivery > long_                                                     # delivery STT is on both sides


def test_paper_broker_uses_the_trade_dates():
    pb = PaperBroker()
    before = pb.estimate_round_trip_costs(120.0, 150.0, 65, "OPTION", entry_date=D("2026-03-31"), exit_date=D("2026-03-31"))
    after = pb.estimate_round_trip_costs(120.0, 150.0, 65, "OPTION", entry_date=D("2026-03-31"), exit_date=D("2026-04-01"))
    assert after - before == pytest.approx(0.0005 * 150 * 65, abs=0.02)        # the exit leg pays the rate of its own day
    ist_late = dt.datetime(2026, 3, 31, 19, 0, tzinfo=dt.timezone.utc)          # 00:30 IST on 1 Apr
    assert pb.exercise_charges(40.0, 65, trade_date=ist_late) == pytest.approx(0.0015 * 40 * 65)


def test_option_backtest_calendar_follows_the_weekday_of_each_date():
    nifty = ExpiryCalendar.for_underlying("NIFTY")
    assert nifty.expiries(D("2025-08-18"), 4) == [D("2025-08-21"), D("2025-08-28"), D("2025-09-02"), D("2025-09-09")]
    assert nifty.expiries(D("2026-10-05"), 2) == [D("2026-10-06"), D("2026-10-13")]
    pinned = ExpiryCalendar.for_underlying("NIFTY", weekday=3)                  # a pinned weekday keeps the old behaviour
    assert pinned.dated_underlying is None and pinned.expiries(D("2026-10-05"), 1) == [D("2026-10-08")]
    holiday = ExpiryCalendar.for_underlying("NIFTY", holidays=[D("2024-10-31")])
    assert D("2024-10-30") in holiday.expiries(D("2024-10-28"), 2)              # Thursday holiday -> Wednesday
    assert lot_size_on("NIFTY", D("2023-01-02")) == 50 and lot_size_on("BANKNIFTY", D("2026-10-05")) == 35


def test_lot_is_keyed_by_the_contract_expiry_and_explicit_weekly_is_honoured():
    from app.backtest.options import lot_size_for
    assert lot_size_for("NIFTY 50", D("2025-12-31"), D("2026-01-06")) == 65     # the Jan 2026 series, entered in Dec 2025
    assert lot_size_for("NIFTY 50", D("2025-12-31"), D("2025-12-30")) == 75
    assert lot_size_for("BANKNIFTY", D("2026-10-05"), D("2026-10-27")) == lot_size_on("BANKNIFTY", D("2026-10-05"))
    forced = ExpiryCalendar.for_underlying("BANKNIFTY", weekly=True)
    assert forced.expiries(D("2023-03-06"), 2) == [D("2023-03-09"), D("2023-03-16")]   # weekly Thursdays as asked
    monthly = ExpiryCalendar.for_underlying("BANKNIFTY")
    assert monthly.expiries(D("2023-03-06"), 1) == [D("2023-03-30")]                  # monthly only: the last Thursday
