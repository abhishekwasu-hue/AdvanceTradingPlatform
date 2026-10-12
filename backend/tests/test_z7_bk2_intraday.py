"""Z7 / U6 BK2: intraday breakout screens B16 (opening range), B17 (first hour / previous day / first candle / pre-market),
B18 (new high / low of day) and the series they need - session VWAP and relative volume by time of day.

Spec docs/specs/ATP_BREAKOUT_SCREENERS_ADDENDUM.md §7: ORB with and without VWAP agreement, time-of-day relative volume
(correct, and from prior sessions only), one alert per leg, first-hour / previous-day / new-high-of-day breaks, the
skip-first-N-minutes option, half days and special sessions, and parity between the ScreenQL text, the engine and the
templates. Fixtures: tests/breakout_fixtures.py (bar by bar, no randomness) and tests/sample_market.py (seeded sessions)."""
import asyncio
from dataclasses import replace
from datetime import date, time, timedelta

import numpy as np
import pandas as pd
import pytest

from app.market_data import calendar as mcal
from app.screener import compile_screen, parse
from app.screener.breakouts import params as bp
from app.screener.breakouts import session as ses
from app.screener.breakouts import style, templates
from app.screener.breakouts.quality import context_trends, htf_pass, run_breakout
from app.screener.breakouts.screens import INTRADAY_SCREENS, Bars, evaluate_screen
from app.screener.registry import FUNCTIONS
from app.screener.runtime import SymbolData, evaluate, resample, run_screen
from tests.breakout_fixtures import (OR_BARS, ORB_BREAK, SESSION_5M, above, history, orb_day, profile, quiet_day, sessions_frame)
from tests.sample_market import generate

ORB_BAR = 21 * SESSION_5M + 6                      # the 09:45 bar of the test day after 21 quiet sessions


def _signals(screen, f, tf="5m", calendar=None, **params):
    return run_breakout(screen, {tf: f}, tf, params=params, within=None, calendar=calendar).signals


def _bars_of(rows, side=None):
    return [r["bar"] for r in rows if side is None or r["side"] == side]


# ----------------------------------------------------------------------------------------------------------- parameters
def test_intraday_parameters_validate():
    _, errors = bp.validate({"tod_min_sessions": 30, "tod_sessions": 10})
    assert errors == ["tod_min_sessions must not be above tod_sessions - defaults used"]
    clean, errors = bp.validate({"day_level": "lunch", "tod_mode": "x", "vwap_agree": "yes", "or_minutes": 0})
    assert len(errors) == 4 and clean["day_level"] == "first_hour" and clean["vwap_agree"] is True and clean["or_minutes"] == 15
    assert {p.name for p in bp.PARAMS if p.group in ("intraday", "B16", "B17")} == {
        "skip_first_min", "tod_window_end", "tod_sessions", "tod_min_sessions", "tod_mode", "tod_vol_mult", "vwap_agree",
        "intraday_max_per_session", "or_minutes", "day_level", "first_hour_min"}
    assert "midpoint" in bp.BY_NAME["stop_mode"].choices


def test_screenql_argument_defaults_are_the_intraday_parameter_defaults():
    want = {"ORBreakout": {"minutes": "or_minutes", "v": "tod_vol_mult", "vwap": "vwap_agree", "skip": "skip_first_min", "k": "min_break_atr"},
            "DayLevelBreak": {"level": "day_level", "v": "tod_vol_mult", "vwap": "vwap_agree", "skip": "skip_first_min", "k": "min_break_atr"},
            "DayExtremeBreak": {"v": "tod_vol_mult", "vwap": "vwap_agree", "skip": "skip_first_min", "k": "min_break_atr"},
            "RelVolumeTOD": {"sessions": "tod_sessions", "mode": "tod_mode"}}
    for fn, args in want.items():
        spec = {a.name: a for a in FUNCTIONS[fn].args}
        for arg, param in args.items():
            assert spec[arg].default == bp.DEFAULTS[param], (fn, arg)
            if bp.BY_NAME[param].low is not None:                            # and their range is the parameter's
                assert spec[arg].bounds == (bp.BY_NAME[param].low, bp.BY_NAME[param].high), (fn, arg)
            assert tuple(spec[arg].choices) == tuple(bp.BY_NAME[param].choices), (fn, arg)
        assert FUNCTIONS[fn].flag == "screener_breakouts"
    assert FUNCTIONS["ORBreakout"].min_sessions == bp.DEFAULTS["tod_sessions"] + 1
    assert set(FUNCTIONS["ORBreakout"].timeframes) == set(style.STYLES["intraday"])


# ------------------------------------------------------------------------------------------------- session from settings
def test_the_session_clock_comes_from_the_exchange_calendar(monkeypatch):
    nse = ses.calendar_for("NSE")
    assert (nse.open, nse.close, nse.cutoff) == (mcal.EXCHANGE_SESSIONS["NSE"].open, mcal.EXCHANGE_SESSIONS["NSE"].close,
                                                 mcal.EXCHANGE_SESSIONS["NSE"].no_new_entries_after)
    assert ses.calendar_for("NFO").name == "NSE" and ses.calendar_for("MCX").open == time(9, 0)
    with pytest.raises(ValueError):
        ses.calendar_for("BINANCE")
    monkeypatch.setitem(mcal.EXCHANGE_SESSIONS, "NSE", replace(mcal.EXCHANGE_SESSIONS["NSE"], open=time(9, 0)))
    assert ses.calendar_for("NSE").open == time(9, 0)                 # nothing here hardcodes 09:15
    # the opening range follows the calendar: a 09:00 open (03:30 UTC) puts the range at 09:00-09:15
    early = ses.calendar_for("NSE")
    f = sessions_frame(history() + [orb_day()], starts={k: "03:30" for k in range(22)})
    rows = _signals("B16", f, calendar=early)
    assert _bars_of(rows) == [ORB_BAR] and rows[0]["clock"] == "09:30" and rows[0]["minutes_from_open"] == 30


def test_session_map_places_bars_by_their_start_in_exchange_time():
    f = sessions_frame(history(2))
    sm = ses.map_sessions(f.index, 5, ses.DEFAULT_CALENDAR)
    assert len(sm.dates) == 2 and sm.minute[0] == 0 and sm.minute[SESSION_5M - 1] == 370 and not sm.special.any()
    assert list(np.flatnonzero(sm.first_of_session())) == [0, SESSION_5M]
    assert sm.window_end[0] == 345                                     # 15:00 no-new-entries = 345 minutes after 09:15


# ---------------------------------------------------------------------------------------------------------------- B16
def test_orb_with_vwap_agreement_is_a_match_and_the_stop_is_the_range_midpoint():
    f = sessions_frame(history() + [orb_day()])
    s = _signals("B16", f, stop_mode="midpoint")
    assert len(s) == 1
    s = s[0]
    assert (s["bar"], s["side"], s["event"], s["level"], s["clock"]) == (ORB_BAR, "up", "OR15", 101.0, "09:45")
    assert s["rvol"] == 2.5 and s["vwap"] < s["close"] and s["stop_ref"] == pytest.approx(100.45)
    assert "volume 2.5x the same-time average of the last 20 sessions (volume)" in s["why"]
    assert s["base_len"] == 3 and s["status"] == "accepted"
    assert _signals("B16", f, stop_mode="base")[0]["stop_ref"] == 99.9     # the opposite side of the range


def test_orb_without_vwap_agreement_is_not_a_match():
    f = sessions_frame(history() + [orb_day(wick=True)])            # a heavy wick drags VWAP above the break's close
    assert _signals("B16", f) == []
    s = _signals("B16", f, vwap_agree=False)
    assert _bars_of(s) == [ORB_BAR] and s[0]["vwap"] > s[0]["close"]
    up = evaluate_screen(f, "B16", bp.settings(), tf="5m")["up"]
    assert bool(up["wick"].iloc[ORB_BAR - 2])                           # the wick bar itself: beyond the range, closed inside


def test_orb_needs_time_of_day_volume_and_a_whole_number_of_bars():
    quiet_break = orb_day(rest=above(SESSION_5M - 7))
    quiet_break[6] = ORB_BREAK[:4] + (1000.0,)                          # the usual volume for 09:45
    f = sessions_frame(history() + [quiet_break])
    assert _signals("B16", f) == [] and _bars_of(_signals("B16", f, tod_vol_mult=0.0)) == [ORB_BAR]
    # the same bar on a plain 20-bar average would read 1000 / ~1500 (the U-shaped morning): the time of day is the point
    assert Bars(f, bp.settings(), tf="5m").rvol[ORB_BAR] < 1.0
    # windows: 30 minutes ends at 09:45 (the break bar is the first bar after it); 60 minutes swallows the break
    f = sessions_frame(history() + [orb_day()])
    assert [(r["bar"], r["event"], r["level"]) for r in _signals("B16", f, or_minutes=30)] == [(ORB_BAR, "OR30", 101.0)]
    assert ORB_BAR not in _bars_of(_signals("B16", f, or_minutes=60))
    with pytest.raises(ValueError):
        _signals("B16", f, or_minutes=7)
    with pytest.raises(ValueError):
        run_breakout("B16", {"1h": resample(f, "5m", "1h")}, "1h")


def test_orb_down_is_the_mirror():
    f = sessions_frame(history() + [orb_day()])
    m = pd.DataFrame({"open": 200 - f["open"], "high": 200 - f["low"], "low": 200 - f["high"], "close": 200 - f["close"], "volume": f["volume"]},
                     index=f.index)
    s = _signals("B16", m, stop_mode="midpoint")
    assert [(r["bar"], r["side"], r["level"]) for r in s] == [(ORB_BAR, "down", 99.0)] and s[0]["stop_ref"] == pytest.approx(99.55)


# ---------------------------------------------------------------------------------------------------------------- B17
def _day(bars_by_slot, base=None):
    day = list(base or quiet_day())
    for slot, bar in bars_by_slot.items():
        day[slot] = bar
    return day


def test_first_hour_break():
    brk = (100.1, 100.9, 100.0, 100.8, 2500.0)
    f = sessions_frame(history() + [_day({14: brk})])
    s = _signals("B17", f, day_level="first_hour", stop_mode="base")
    assert [(r["bar"], r["event"], r["level"], r["clock"]) for r in s] == [(21 * SESSION_5M + 14, "FIRST_HOUR", 100.25, "10:25")]
    assert s[0]["stop_ref"] == 99.75 and s[0]["base_len"] == 12
    # the same bar inside the first hour is not a first-hour break (the level is not known until 10:15)
    early = sessions_frame(history() + [_day({8: brk})])
    assert _signals("B17", early, day_level="first_hour") == []


def test_previous_day_high_and_low_breaks():
    up = sessions_frame(history() + [_day({8: (100.1, 100.9, 100.0, 100.8, 2500.0)}, quiet_day(mid=100.0))])
    s = _signals("B17", up, day_level="prev_day")
    assert [(r["bar"], r["side"], r["event"], r["level"]) for r in s] == [(21 * SESSION_5M + 8, "up", "PDH", 100.25)]
    down = sessions_frame(history() + [_day({8: (99.9, 100.0, 99.1, 99.2, 2500.0)})])
    s = _signals("B17", down, day_level="prev_day")
    assert [(r["bar"], r["side"], r["event"], r["level"]) for r in s] == [(21 * SESSION_5M + 8, "down", "PDL", 99.75)]
    # the previous day is the previous session in the data, not the calendar day before (a weekend in between is fine)
    sm = ses.map_sessions(up.index, 5, ses.DEFAULT_CALENDAR)
    assert (sm.dates[21] - sm.dates[20]).days in (1, 3)


def test_first_candle_and_premarket_levels():
    f = sessions_frame(history() + [orb_day()])
    s = _signals("B17", f, day_level="first_candle")
    assert [(r["bar"], r["event"], r["level"]) for r in s] == [(ORB_BAR, "FIRST_CANDLE", 100.8)]
    # no pre-open bars in the data: no pre-market level, and the run says why
    run = run_breakout("B17", {"5m": f}, "5m", params={"day_level": "premarket"}, within=None)
    assert run.signals == [] and any("no pre-open bars" in n for n in run.notes)
    # with two pre-open bars (09:05, 09:10) every day: their high is the level; they stay out of the range and VWAP
    pre = [(100.0, 100.6, 99.9, 100.2, 500.0), (100.2, 100.9, 100.1, 100.3, 500.0)]
    days = [pre + d for d in history()] + [pre + orb_day()]
    g = sessions_frame(days, starts={k: "03:35" for k in range(22)})
    s = _signals("B17", g, day_level="premarket")
    assert [(r["bar"], r["event"], r["level"]) for r in s] == [(ORB_BAR + 2 * 22, "PREMARKET", 100.9)]
    spike = [pre[0], (100.2, 102.0, 100.1, 100.3, 500.0)]                # a pre-open spike above the range
    g = sessions_frame([spike + d for d in history()] + [spike + orb_day()], starts={k: "03:35" for k in range(22)})
    orb = _signals("B16", g)
    assert [(r["bar"], r["level"]) for r in orb] == [(ORB_BAR + 2 * 22, 101.0)]        # the pre-open 102 is not the range
    b = Bars(g, bp.settings(), tf="5m")
    first = 21 * (SESSION_5M + 2) + 2
    assert np.isnan(b.vwap()[first - 1]) and b.vwap()[first] == pytest.approx(sum(OR_BARS[0][1:4]) / 3)


# ---------------------------------------------------------------------------------------------------------------- B18
def test_new_high_of_day_once_per_leg_and_again_next_session():
    day = _day({6: (100.1, 100.8, 100.0, 100.7, 2500.0), 7: (100.7, 101.2, 100.6, 101.1, 2500.0)}, quiet_day())
    day[8:] = [(101.0, 101.3, 100.9, 101.1, profile(i)) for i in range(8, SESSION_5M)]
    nxt = _day({6: (101.0, 101.8, 100.9, 101.7, 2500.0)}, quiet_day(mid=101.0))
    f = sessions_frame(history() + [day, nxt])
    e = evaluate_screen(f, "B18", bp.settings(), tf="5m")["up"]
    first = 21 * SESSION_5M
    assert bool(e["raw"].iloc[first + 6]) and bool(e["raw"].iloc[first + 7])          # two new-high closes in one leg ...
    s = _signals("B18", f)
    assert [(r["bar"], r["event"]) for r in s if r["side"] == "up"] == [(first + 6, "HOD"), (first + SESSION_5M + 6, "HOD")]   # ... one signal
    assert s[0]["level"] == 100.25 and s[0]["base_len"] == 6
    # the low of day is the mirror
    m = pd.DataFrame({"open": 200 - f["open"], "high": 200 - f["low"], "low": 200 - f["high"], "close": 200 - f["close"], "volume": f["volume"]},
                     index=f.index)
    assert [(r["bar"], r["event"]) for r in _signals("B18", m) if r["side"] == "down"] == [(first + 6, "LOD"), (first + SESSION_5M + 6, "LOD")]


def test_no_signal_after_the_no_new_entries_time():
    last = SESSION_5M - 6                                                # the 15:00-15:05 bar closes after 15:00
    f = sessions_frame(history() + [_day({last: (100.1, 100.9, 100.0, 100.8, 6000.0)})])
    assert _signals("B18", f) == []
    assert _bars_of(_signals("B18", f, tod_window_end="close")) == [21 * SESSION_5M + last]
    ok = sessions_frame(history() + [_day({last - 1: (100.1, 100.9, 100.0, 100.8, 6000.0)})])    # 14:55-15:00: in
    assert _bars_of(_signals("B18", ok)) == [21 * SESSION_5M + last - 1]


# ----------------------------------------------------------------------------------------------------- skip first N
def test_skip_first_minutes():
    gap = _day({1: (100.1, 100.9, 100.0, 100.8, 9000.0), 2: (100.8, 101.0, 100.6, 100.9, 3000.0), 3: (100.9, 101.1, 100.7, 101.0, 3000.0)})
    gap[4:] = [(101.0, 101.2, 100.8, 101.0, profile(i)) for i in range(4, SESSION_5M)]
    f = sessions_frame(history() + [gap])
    first = 21 * SESSION_5M
    assert _bars_of(_signals("B17", f, day_level="prev_day", skip_first_min=0)) == [first + 1]      # 09:20, on 3x volume
    # skipping 15 minutes: the break held, so the first bar after the skip (09:30) is the signal - when it has the volume
    assert _signals("B17", f, day_level="prev_day") == []
    assert _bars_of(_signals("B17", f, day_level="prev_day", tod_vol_mult=0.0)) == [first + 3]
    # a break that is back inside by the end of the skip is no signal at all
    back = list(gap)
    back[2:] = [(100.0, 100.2, 99.8, 100.0, profile(i)) for i in range(2, SESSION_5M)]
    g = sessions_frame(history() + [back])
    assert _signals("B17", g, day_level="prev_day", tod_vol_mult=0.0) == []
    assert _bars_of(_signals("B17", g, day_level="prev_day", skip_first_min=0)) == [first + 1]
    # B18 waits the same way; B16 waits for the longer of the range and the skip
    f = sessions_frame(history() + [orb_day()])
    assert _bars_of(_signals("B16", f, skip_first_min=60, tod_vol_mult=0.0)) == [21 * SESSION_5M + 12]


# ------------------------------------------------------------------------------------- relative volume by time of day
def _varied(n_days=30, slots=SESSION_5M):
    days = []
    for d in range(n_days):
        days.append([(100.0, 100.25, 99.75, 100.0, 1000.0 + 37.0 * ((d * 7 + s * 13) % 11) + 5.0 * d) for s in range(slots)])
    return days


def test_time_of_day_relative_volume_is_the_same_slot_average_of_prior_sessions():
    days = _varied()
    f = sessions_frame(days)
    vol = np.array([[b[4] for b in day] for day in days])
    p = bp.settings()
    rv = Bars(f, p, tf="5m").tod_rvol()
    for d in range(len(days)):
        for s in (0, 7, 40, SESSION_5M - 1):
            got = rv[d * SESSION_5M + s]
            if d < p["tod_min_sessions"]:
                assert np.isnan(got)
            else:
                want = vol[d, s] / vol[max(0, d - p["tod_sessions"]):d, s].mean()
                assert got == pytest.approx(want), (d, s)
    cum = Bars(f, bp.settings(tod_mode="cumulative"), tf="5m").tod_rvol()
    csum = vol.cumsum(axis=1)
    assert cum[25 * SESSION_5M + 9] == pytest.approx(csum[25, 9] / csum[5:25, 9].mean())


def test_time_of_day_average_has_no_look_ahead():
    days = _varied()
    f = sessions_frame(days)
    p = bp.settings()
    base = Bars(f, p, tf="5m").tod_rvol()
    # a huge volume later in the same session, or in a later session, never changes an earlier bar's value
    g = f.copy()
    j = 25 * SESSION_5M + 30
    g.iloc[j:, g.columns.get_loc("volume")] *= 50
    after = Bars(g, p, tf="5m").tod_rvol()
    assert np.allclose(base[:j], after[:j], equal_nan=True) and not np.isclose(base[j], after[j])
    # a session's own volume does not enter its own average (the bar's ratio scales with it exactly)
    h = f.copy()
    h.iloc[j, h.columns.get_loc("volume")] *= 3
    assert Bars(h, p, tf="5m").tod_rvol()[j] == pytest.approx(3 * base[j])
    # and appending bars never changes what was computed (the engine-wide guarantee)
    for screen in INTRADAY_SCREENS:
        full = evaluate_screen(f, screen, bp.settings(tod_vol_mult=0.0), tf="5m")
        part = evaluate_screen(f.iloc[:j], screen, bp.settings(tod_vol_mult=0.0), tf="5m")
        for side in ("up", "down"):
            for col in ("raw", "signal", "level"):
                pd.testing.assert_series_equal(part[side][col], full[side][col].iloc[:j], check_names=False)


@pytest.fixture(scope="module")
def m5():
    return resample(generate(375 * 30, 100.0, 21), "1m", "5m")


@pytest.mark.parametrize("screen,params", [("B16", {}), ("B16", {"or_minutes": 30}), ("B17", {"day_level": "prev_day"}),
                                           ("B17", {"day_level": "first_hour"}), ("B18", {})])
def test_no_intraday_value_changes_when_later_bars_arrive(m5, screen, params):
    p = bp.settings(tod_vol_mult=1.0, **params)
    full = evaluate_screen(m5, screen, p, tf="5m")
    for cut in (1700, 1733, 2000):                                     # mid-session cuts included
        part = evaluate_screen(m5.iloc[:cut], screen, p, tf="5m")
        for side in ("up", "down"):
            for col in ("raw", "signal", "level", "fail_level", "wick"):
                pd.testing.assert_series_equal(part[side][col], full[side][col].iloc[:cut], check_names=False)
    b_full, b_part = Bars(m5, p, tf="5m"), Bars(m5.iloc[:1733], p, tf="5m")
    assert np.allclose(b_part.vwap(), b_full.vwap()[:1733], equal_nan=True)
    assert np.allclose(b_part.tod_rvol(), b_full.tod_rvol()[:1733], equal_nan=True)


# ------------------------------------------------------------------------------------- half days and special sessions
def test_half_day_session():
    days = [[(100.0, 100.25, 99.75, 100.0, 1000.0 + 10.0 * d)] * SESSION_5M for d in range(21)]
    days[10] = days[10][:45]                                             # 09:15-13:00: an early close
    days.append(orb_day())
    f = sessions_frame(days)
    sm = ses.map_sessions(f.index, 5, ses.DEFAULT_CALENDAR)
    assert not sm.special.any() and len(sm.dates) == 22                 # a half day keeps the regular clock
    p = bp.settings()
    rv = Bars(f, p, tf="5m").tod_rvol()
    start = 20 * SESSION_5M + 45
    late = [1000.0 + 10.0 * d for d in range(21) if d != 10]            # 15:15 (slot 72): the half day never got there
    early = [1000.0 + 10.0 * d for d in range(1, 21)]                   # 09:30 (slot 3): the last 20 sessions, half day in
    assert rv[start + 72] == pytest.approx(days[21][72][4] / np.mean(late))
    assert rv[start + 3] == pytest.approx(days[21][3][4] / np.mean(early))
    # the ORB of the day after works, and the previous day of the session after the half day is the half day
    assert _bars_of(_signals("B16", f)) == [start + 6]
    prev_hi, _ = ses.previous_session_levels(f["high"].to_numpy(), f["low"].to_numpy(), sm)
    assert prev_hi[11 * SESSION_5M - 30] == 100.25
    # an early close from an exchange circular: bars after it are outside the session, and signals stop at the close
    d10 = sm.dates[10]
    cal = ses.calendar_for("NSE", special={d10: (time(9, 15), time(12, 0))})
    sm2 = ses.map_sessions(f.index, 5, cal)
    assert not sm2.special[10] and sm2.window_end[10] == 165 and np.isnan(sm2.minute[10 * SESSION_5M + 40])


def test_special_session_with_a_late_open():
    days = history() + [orb_day()]
    f = sessions_frame(days, starts={21: "04:45"})                       # the test day opens at 10:15 IST
    assert _signals("B16", f) == []                                     # on the regular clock its range never formed
    special = {f.index[-1].tz_convert(mcal.IST).date(): (time(10, 15), time(15, 30))}
    s = _signals("B16", f, calendar=ses.calendar_for("NSE", special=special))
    assert [(r["bar"], r["clock"], r["minutes_from_open"], r["level"]) for r in s] == [(ORB_BAR, "10:45", 30, 101.0)]


def test_evening_special_session_is_detected_and_left_out_of_averages_and_previous_day():
    muhurat = [(100.0, 103.0, 97.0, 100.0, 90000.0)] * 12                # 18:15-19:15 IST, wide and heavy
    days = history(10) + [muhurat] + [quiet_day()]
    f = sessions_frame(days, starts={10: "12:45"})
    sm = ses.map_sessions(f.index, 5, ses.DEFAULT_CALENDAR)
    assert list(sm.special) == [False] * 10 + [True, False] and sm.minute[10 * SESSION_5M] == 0
    rv = Bars(f, bp.settings(), tf="5m").tod_rvol()
    assert rv[10 * SESSION_5M + 12 + 2] == pytest.approx(1.0)          # the next day's 09:25 bar vs regular days only
    prev_hi, prev_lo = ses.previous_session_levels(f["high"].to_numpy(), f["low"].to_numpy(), sm)
    assert (prev_hi[-1], prev_lo[-1]) == (100.25, 99.75)               # not the evening session's 103 / 97
    run = run_breakout("B18", {"5m": f}, "5m", within=None)
    assert any("special session" in n for n in run.notes)


# ------------------------------------------------------------------------------------------------ one alert per leg
def test_one_signal_per_side_per_session_and_a_new_leg_after_a_close_back_inside():
    whipsaw = orb_day(rest=[(101.6, 101.7, 100.4, 100.5, 1000.0), (100.5, 101.9, 100.4, 101.7, 2500.0)] + above(SESSION_5M - 9, first_slot=9))
    f = sessions_frame(history() + [whipsaw])
    raw = evaluate_screen(f, "B16", bp.settings(), tf="5m")["up"]["raw"].to_numpy()
    assert list(np.flatnonzero(raw)) == [ORB_BAR, ORB_BAR + 2]           # two legs: break, back inside, break again
    assert _bars_of(_signals("B16", f)) == [ORB_BAR]                     # default: the first break of the session only
    assert _bars_of(_signals("B16", f, intraday_max_per_session=2)) == [ORB_BAR, ORB_BAR + 2]
    # staying above the range is never a new leg
    held = sessions_frame(history() + [orb_day(rest=above(SESSION_5M - 7, volume=5000.0))])
    assert _bars_of(_signals("B16", held, intraday_max_per_session=5)) == [ORB_BAR]


def test_one_alert_per_leg_through_the_bar_close_engine():
    from app.db.models import AlertEventRecord, AlertRuleRecord
    from app.alerts import barclose
    from sqlalchemy import select
    from tests.test_auth_api import _session_factory
    from tests.test_phase_l_ai import _owner
    from tests.test_s4a_barclose import FakeFetch

    f = sessions_frame(history() + [orb_day()])
    _, me = _owner("bk2-alert@example.com")

    async def go():
        async with _session_factory() as session:
            day = f.index[-1].tz_convert(mcal.IST).date()
            rule = AlertRuleRecord(tenant_id=me["tenant_id"], name="orb", kind="instrument", symbol="TCS", condition_text='ORBreakout("up")',
                                   base_tf="5m", priority="normal", cooldown_minutes=0, mode="instant", digest_every="hourly", status="active",
                                   created_at=f.index[0], updated_at=f.index[0])
            session.add(rule)
            await session.commit()
            fetch = FakeFetch({"TCS": f})
            states = []
            for k in range(3, 16):                                   # 09:30 .. 10:30 closes on the test day
                now = f.index[21 * SESSION_5M + k] + timedelta(minutes=5, seconds=30)
                states.append(await barclose.evaluate_rule(session, rule, now.to_pydatetime(), fetch))
                await session.commit()
            events = list(await session.scalars(select(AlertEventRecord).where(AlertEventRecord.rule_id == rule.id)))
            rule.status = "paused"                                       # leave nothing active for other modules' run_due tests
            await session.commit()
            return day, states, events
    day, states, events = asyncio.run(go())
    assert all(s[0] == "done" for s in states), states
    assert len(events) == 1 and pd.Timestamp(events[0].bar_time).tz_localize(None) == f.index[ORB_BAR].tz_localize(None)
    assert isinstance(day, date)


# ------------------------------------------------------------------------------------ ScreenQL <-> engine <-> templates
def test_session_series_in_screenql_match_the_engine(m5):
    sd = SymbolData("S", {"5m": m5})
    b = Bars(m5, bp.settings(), tf="5m")
    assert np.allclose(evaluate(parse("SessionVWAP()"), sd, base_tf="5m").to_numpy(float), b.vwap(), equal_nan=True)
    assert np.allclose(evaluate(parse("SessionVWAP()"), sd, base_tf="5m").to_numpy(float),
                       evaluate(parse("VWAP()"), sd, base_tf="5m").to_numpy(float), equal_nan=True)   # regular session: the same VWAP
    assert np.allclose(evaluate(parse("RelVolumeTOD(20)"), sd, base_tf="5m").to_numpy(float), b.tod_rvol(), equal_nan=True)
    cum = Bars(m5, bp.settings(tod_mode="cumulative", tod_sessions=10), tf="5m").tod_rvol()
    assert np.allclose(evaluate(parse('RelVolumeTOD(10, "cumulative")'), sd, base_tf="5m").to_numpy(float), cum, equal_nan=True)
    v = compile_screen("RelVolumeTOD(10) > 2", base_tf="5m")[1]
    assert v.ok and v.lookback["5m"] >= 11 * 75
    assert not compile_screen('ORBreakout("up")', base_tf="1d")[1].ok and not compile_screen("SessionVWAP() > 1", base_tf="1d")[1].ok


def _engine_series(screen, f, tf, params):
    p = bp.settings(**params)
    ctx = style.context_timeframes(tf, p["context_ladder"])
    frames = style.frames_for({tf: f}, tf, ctx)
    trends = context_trends(frames, tf, ctx, p)
    res = evaluate_screen(f, screen, p, tf=tf)
    sides = ("up", "down") if p["direction"] == "both" else (p["direction"],)
    out = np.zeros(len(f), dtype=bool)
    for side in sides:
        ok = np.array([htf_pass([trends[c].iloc[j] for c in ctx], side, p["htf_filter"]) for j in range(len(f))])
        out |= res[side]["signal"].to_numpy() & ok
    return out


@pytest.mark.parametrize("screen,tf,params", [
    ("B16", "5m", {"tod_vol_mult": 1.0, "htf_filter": "off"}), ("B16", "15m", {"or_minutes": 30, "tod_vol_mult": 0.0, "htf_filter": "off"}),
    ("B16", "5m", {"tod_vol_mult": 0.0, "vwap_agree": False, "skip_first_min": 30, "min_break_atr": 0.0}),
    ("B17", "15m", {"day_level": "first_hour", "tod_vol_mult": 0.0, "htf_filter": "off"}),
    ("B17", "5m", {"day_level": "prev_day", "tod_vol_mult": 1.0, "htf_filter": "off"}),
    ("B17", "5m", {"day_level": "first_candle", "tod_vol_mult": 0.0, "skip_first_min": 0, "htf_filter": "off"}),
    ("B18", "5m", {"tod_vol_mult": 1.0, "htf_filter": "off"}), ("B18", "3m", {"tod_vol_mult": 1.0, "direction": "down", "htf_filter": "off"}),
])
def test_intraday_template_text_matches_the_engine_bar_by_bar(screen, tf, params):
    f = resample(generate(375 * 30, 100.0, 21), "1m", tf)
    text, why = templates.screenql(screen, tf, params)
    assert why is None and text is not None
    ast, validated = compile_screen(text, base_tf=tf)
    assert validated.ok, validated.problems
    got = evaluate(ast, SymbolData("S", {tf: f}), base_tf=tf).fillna(False).astype(bool).to_numpy()
    want = _engine_series(screen, f, tf, params)
    assert (got == want).all()
    assert want.sum() > 0
    # the graded engine run lists the same bars when the higher-timeframe filter is off
    if params.get("htf_filter") == "off":
        rows = run_breakout(screen, {tf: f}, tf, params=params, within=None).signals
        assert sorted({r["bar"] for r in rows}) == list(np.flatnonzero(want))
    # and the last-bar screen run (what an alert evaluates at a bar close) says the same
    cut = int(np.flatnonzero(want)[-1]) + 1
    match = run_screen(ast, validated, [SymbolData("S", {tf: f.iloc[:cut]})], base_tf=tf, check_history=False)[0]
    assert match.matched


def test_intraday_templates_are_in_the_catalog_with_their_text():
    cat = templates.catalog()
    ids = {t["id"]: t for t in cat["templates"]}
    orb = ids["intraday_5m_orb15"]
    assert orb["screen"] == "B16" and orb["style"] == "intraday" and orb["context_tfs"] == ["15m", "1h"]
    assert orb["screenql"].startswith('ORBreakout("up", 15, 1.5, TRUE, 15, 0.2)') and orb["params"]["stop_mode"] == "midpoint"
    assert ids["intraday_5m_prev_day"]["screenql"].startswith('DayLevelBreak("up", "prev_day"')
    assert ids["intraday_5m_day_extreme"]["screenql"].startswith('DayExtremeBreak("up"')
    assert {b["screen"]: b["timeframes"] for b in cat["blocks"]["breakout"]}["B16"] == ["1m", "3m", "5m", "15m"]
    assert {f["name"] for f in cat["blocks"]["intraday"]} >= {"skip_first_min", "tod_vol_mult", "vwap_agree", "tod_sessions"}
    # a template that changes a parameter its ScreenQL call does not take runs through the engine only
    text, why = templates.screenql("B16", "5m", {"tod_sessions": 10})
    assert text is None and "tod_sessions" in why
    # the template, its text and the engine agree on the ORB fixture (the template's own grading adds the stop)
    f = sessions_frame(history() + [orb_day()])
    t = templates.BY_ID["intraday_5m_orb15"]
    p = templates.template_params(t, {"htf_filter": "off"})
    rows = run_breakout(t.screen, {"5m": f}, t.signal_tf, params=p, within=None).signals
    ast, validated = compile_screen(templates.screenql(t.screen, t.signal_tf, p)[0], base_tf="5m")
    series = evaluate(ast, SymbolData("S", {"5m": f}), base_tf="5m").fillna(False).astype(bool).to_numpy()
    assert [r["bar"] for r in rows] == list(np.flatnonzero(series)) == [ORB_BAR] and rows[0]["stop_ref"] == pytest.approx(100.45)


# ------------------------------------------------------------------------------------------------------------------ API
def test_intraday_run_api_fetches_whole_sessions_and_uses_the_exchange_clock(monkeypatch):
    from app.screener.breakouts import routes as bk_routes
    from tests.test_auth_api import client
    from tests.test_phase_l_ai import _owner
    from tests.test_z7_bk1_api import _set

    _set(screener_v2=True, screener_breakouts=True)
    try:
        headers, _ = _owner("bk2-api@example.com")
        reg = client.get("/api/screener/registry", headers=headers).json()["entries"]
        assert reg["ORBreakout"]["timeframes"] == ["1m", "3m", "5m", "15m"] and reg["DayLevelBreak"]["choices"]["level"][1] == "prev_day"
        f = sessions_frame(history() + [orb_day()])
        asked = []

        async def fake_fetch(session, tenant_id, symbols, exchange, base_tf, lookback, now=None, include_forming=False, **kw):
            asked.append((base_tf, dict(lookback), exchange))
            return [SymbolData(s, {base_tf: f}) for s in symbols], {}, "broker:fake"
        monkeypatch.setattr(bk_routes, "fetch_frames", fake_fetch)
        out = client.post("/api/screener/breakouts/run", json={"template_id": "intraday_5m_orb15", "symbols": ["tcs"], "within": 500,
                                                               "params": {"htf_filter": "off"}}, headers=headers)
        assert out.status_code == 200, out.text
        body = out.json()
        assert asked == [("5m", {"5m": 21 * 75}, "NSE")]                      # 20 prior sessions + today, in 5m bars
        assert body["style"] == "intraday" and body["context_tfs"] == ["15m", "1h"] and "vwap" in body["columns"]
        row = body["rows"][0]
        assert (row["symbol"], row["side"], row["event"], row["clock"], row["level"]) == ("TCS", "up", "OR15", "09:45", 101.0)
        assert row["stop_ref"] == pytest.approx(100.45) and row["vwap"] < row["close"]
        bad = client.post("/api/screener/breakouts/run", json={"screen": "B16", "signal_tf": "1h", "symbols": ["TCS"]}, headers=headers)
        assert bad.status_code == 422 and "1m / 3m / 5m / 15m" in bad.text
        v = client.post("/api/screener/validate", json={"source": 'ORBreakout("up") AND SessionVWAP() < close', "base_tf": "5m"},
                        headers=headers).json()
        assert v["ok"], v
        _set(screener_breakouts=False)
        v = client.post("/api/screener/validate", json={"source": 'DayExtremeBreak("up")', "base_tf": "5m"}, headers=headers).json()
        assert not v["ok"] and "DayExtremeBreak()" in v["problems"][-1]["message"]
    finally:
        _set(screener_v2=False, screener_breakouts=False)


# ------------------------------------------------------------------------------- IST-aware indexes (broker-shaped data)
def _ist(f):
    return f.set_axis(f.index.tz_convert("Asia/Kolkata"))


@pytest.mark.parametrize("screen,params", [("B16", {}), ("B17", {"day_level": "prev_day"}), ("B17", {"day_level": "first_hour"}), ("B18", {})])
def test_an_ist_aware_index_gives_the_same_sessions_vwap_volume_and_signals(m5, screen, params):
    # brokers (Kite, Fyers, Dhan) deliver IST-aware stamps: nothing may depend on the index's time zone
    p = bp.settings(tod_vol_mult=1.0, **params)
    utc, ist = Bars(m5, p, tf="5m"), Bars(_ist(m5), p, tf="5m")
    su, si = utc.sessions(), ist.sessions()
    assert su.dates == si.dates and (su.session == si.session).all() and np.array_equal(su.minute, si.minute, equal_nan=True)
    assert np.allclose(utc.vwap(), ist.vwap(), equal_nan=True) and np.allclose(utc.tod_rvol(), ist.tod_rvol(), equal_nan=True)
    a, b = evaluate_screen(m5, screen, p, tf="5m"), evaluate_screen(_ist(m5), screen, p, tf="5m")
    for side in ("up", "down"):
        assert (a[side]["signal"].to_numpy() == b[side]["signal"].to_numpy()).all()
    # the higher-timeframe bars built from an IST-aware index start at 09:15 IST like the UTC ones
    hour = resample(_ist(m5), "5m", "1h")
    assert hour.index[0].strftime("%H:%M") == "09:15" and (hour.index.tz_convert("UTC") == resample(m5, "5m", "1h").index).all()
    ra = run_breakout(screen, {"5m": m5}, "5m", params={**params, "tod_vol_mult": 1.0}, within=None).signals
    rb = run_breakout(screen, {"5m": _ist(m5)}, "5m", params={**params, "tod_vol_mult": 1.0}, within=None).signals
    assert [(r["bar"], r["side"], r["htf"], r["grade"], r["clock"]) for r in ra] == [(r["bar"], r["side"], r["htf"], r["grade"], r["clock"]) for r in rb]


def test_session_dates_are_exchange_dates_across_the_utc_midnight_boundary():
    # a 24-hour venue clock (00:00-23:59 IST) puts bars of one IST day on two UTC dates: 18:30 UTC is IST midnight
    cal = ses.SessionCalendar("X", time(0, 0), time(23, 59), None)
    idx = pd.DatetimeIndex(["2026-03-02 18:00", "2026-03-02 18:25", "2026-03-02 18:30", "2026-03-02 18:35", "2026-03-03 18:30"], tz="UTC")
    f = pd.DataFrame({"open": 100.0, "high": [101.0, 102.0, 103.0, 104.0, 105.0], "low": 99.0, "close": 100.0, "volume": 1.0}, index=idx)
    for index in (idx, idx.tz_convert("Asia/Kolkata")):
        sm = ses.map_sessions(index, 5, cal)
        assert [str(d) for d in sm.dates] == ["2026-03-02", "2026-03-03", "2026-03-04"] and list(sm.session) == [0, 0, 1, 1, 2]
        hi, _ = ses.previous_session_levels(f["high"].to_numpy(), f["low"].to_numpy(), sm)
        assert list(hi[2:]) == [102.0, 102.0, 104.0]                    # the previous IST day, not the previous UTC date
        vw = ses.session_vwap(f.set_axis(index), sm)
        assert vw[2] == pytest.approx((103.0 + 99.0 + 100.0) / 3)       # VWAP resets at IST midnight
    # daily bars (labelled 18:30 UTC = IST midnight by some brokers) are not session bars
    daily = f.set_axis(pd.DatetimeIndex(["2026-03-01 18:30", "2026-03-02 18:30", "2026-03-03 18:30", "2026-03-04 18:30", "2026-03-05 18:30"],
                                        tz="UTC"))
    with pytest.raises(ValueError):
        Bars(daily, bp.settings()).sessions()


# ------------------------------------------------------------------ production-shaped history: the real fetch windows
@pytest.fixture(scope="module")
def broker_history():
    """A year of 1-minute sessions with an exchange holiday, delivered as a broker does: IST-aware 5m / 15m bars, and daily
    bars labelled at IST midnight (18:30 UTC)."""
    m1 = generate(375 * 260, 100.0, 7)
    days = sorted(set(m1.index.normalize()))
    m1 = m1[m1.index.normalize() != days[-25]]                           # a holiday inside the intraday window
    daily = resample(m1, "1m", "1d")
    daily.index = daily.index.tz_localize(None).tz_localize("Asia/Kolkata")
    return {"5m": _ist(resample(m1, "1m", "5m")), "15m": _ist(resample(m1, "1m", "15m")), "1d": daily}


@pytest.mark.parametrize("template_id", [t.id for t in templates.TEMPLATES if t.screen in INTRADAY_SCREENS])
def test_every_intraday_template_can_match_within_the_real_fetch_windows(broker_history, template_id):
    """What the breakout run fetches: the signal timeframe for `fetch_days(lookback)` calendar days (60 at most) plus daily
    history when the context ladder reaches 1d. Every template must be able to match on exactly that - its ScreenQL text
    with check_history=True (nothing 'too short') and the graded engine run, context ladder included."""
    from app.screener.routes import MAX_INTRADAY_FETCH_DAYS, fetch_days

    t = templates.BY_ID[template_id]
    tf, params = t.signal_tf, templates.template_params(t)
    text, why = templates.screenql(t.screen, tf, params)
    ast, validated = compile_screen(text, base_tf=tf)
    days = fetch_days(validated.fetch_lookback, tf)                   # what the alert / saved-screen path fetches
    assert days <= MAX_INTRADAY_FETCH_DAYS
    full = broker_history[tf]
    recent = full[full.index >= full.index[-1].normalize() - pd.Timedelta(days=90)]
    context = style.context_timeframes(tf)
    daily_ctx = [c for c in context if bp.TF_MINUTES[c] >= bp.TF_MINUTES["1d"]]

    def fetched(j):
        """The broker fetch made at the close of bar j: `days` calendar days of signal bars, daily bars closed before."""
        cut = recent.iloc[:j + 1]
        out = {tf: cut[cut.index >= cut.index[-1].normalize() - pd.Timedelta(days=days)].copy()}
        if daily_ctx:
            out["1d"] = broker_history["1d"][broker_history["1d"].index < cut.index[-1].normalize()]
        return out

    # a candidate break in the last sessions (ignoring volume) on a side the context allows, then the volume a real break
    # would carry, evaluated on exactly what the fetch at that bar returns
    loose = evaluate_screen(recent, t.screen, bp.settings(**{**params, "tod_vol_mult": 0.0}), tf=tf)
    last_days = recent.index.normalize().unique()[-20:]
    found = None
    for side in ("up", "down"):
        for j in np.flatnonzero(loose[side]["signal"].to_numpy() & recent.index.normalize().isin(last_days))[::-1]:
            fr = fetched(int(j))
            fr[tf].iloc[-1, fr[tf].columns.get_loc("volume")] *= 6
            rows = run_breakout(t.screen, fr, tf, params=params, within=1).signals
            if rows and rows[0]["side"] == side and rows[0]["htf_pass"]:
                found = (fr, rows[0])
                break
        if found:
            break
    assert found is not None, "no bar in the fetch window can carry this template's match"
    fr, row = found
    assert row["rvol"] is not None and row["rvol"] >= bp.DEFAULTS["tod_vol_mult"] and None not in row["htf"].values()
    match = run_screen(ast, validated, [SymbolData("S", fr)], base_tf=tf, check_history=True)[0]
    assert match.matched and match.reason is None, match.reason


def test_screenql_arguments_are_checked_against_the_parameter_ranges_when_validated():
    def problems(text, **params):
        return [p.message for p in compile_screen(text, base_tf="5m", params=params)[1].problems]
    assert problems('ORBreakout("up", 0)') == ["ORBreakout: minutes must be between 1 and 240, got 0"]
    assert problems('DayLevelBreak("up", "first_hour", 1.5, TRUE, 300) AND close > 0') == [
        "DayLevelBreak: skip must be between 0 and 240, got 300"]
    assert problems('DayLevelBreak("up", "lunch")') and problems('RelVolumeTOD(2, "daily") > 1')        # choices
    assert problems('DayExtremeBreak("up", -1)') == ["DayExtremeBreak: v must be between 0 and 20, got -1"]
    assert problems('RelVolumeTOD(1) > 2') == ["RelVolumeTOD: sessions must be between 2 and 120, got 1"]
    assert problems('ORBreakout("up", $m)', m=500) and not problems('ORBreakout("up", $m)', m=30)        # screen parameters too
    assert not problems('ORBreakout("up", 30, 2, FALSE, 0, 0)') and not problems('RelVolumeTOD(3) > 2')
    assert problems('ORBreakout("up")', ) == [] and problems('ORBreakout("up")@1h')                     # intraday timeframes only


# --------------------------------------------------------------------------- alert gating of the BK2 functions (M1-M3)
def test_intraday_breakout_alerts_are_gated_bar_close_only_and_pause_while_off():
    from app.alerts import barclose
    from app.db.models import AlertEventRecord, AlertRuleRecord
    from sqlalchemy import select
    from tests.test_auth_api import _session_factory, client
    from tests.test_phase_l_ai import _owner
    from tests.test_s4a_barclose import FakeFetch, _rule
    from tests.test_z7_bk1_api import _save_breakout_screen, _set

    _set(screener_v2=True, screener_breakouts=False)
    try:
        headers, me = _owner("bk2-gate@example.com")
        sid = _save_breakout_screen(headers, source='DayExtremeBreak("up")', tf="5m")
        r = client.post("/api/alerts/rules", json={"name": "s", "kind": "screen", "screen_id": sid, "symbols": ["TCS"]}, headers=headers)
        assert r.status_code == 422 and "DayExtremeBreak()" in str(r.json())                    # a screen rule while off
        _set(screener_breakouts=True, screener_intrabar=True)
        r = client.post("/api/alerts/rules", json={"name": "i", "kind": "instrument", "symbol": "TCS", "condition": 'ORBreakout("up")',
                                                   "base_tf": "5m", "fire_on": "intrabar"}, headers=headers)
        assert r.status_code == 422 and "bar close only" in str(r.json())                       # never on a forming bar
        _set(screener_breakouts=False, screener_intrabar=False)

        f = sessions_frame(history() + [orb_day()])
        rid = _rule(me["tenant_id"], symbol="TCS", condition='ORBreakout("up")', tf="5m")
        fetch = FakeFetch({"TCS": f.iloc[:ORB_BAR + 1]})
        now = (f.index[ORB_BAR] + timedelta(minutes=6)).to_pydatetime()

        def cycle(at):
            async def go():
                async with _session_factory() as session:
                    await barclose.run_due(session, now=at, fetch=fetch, holidays=set())
                    rule = await session.get(AlertRuleRecord, rid)
                    events = list(await session.scalars(select(AlertEventRecord).where(AlertEventRecord.rule_id == rid)))
                    out = (rule.status, rule.last_problem, len(events))
                    rule.status = "paused"
                    await session.commit()
                    return out
            return asyncio.run(go())

        async def reactivate():
            async with _session_factory() as session:
                (await session.get(AlertRuleRecord, rid)).status = "active"
                await session.commit()
        status, problem, fired = cycle(now)
        assert status == "active" and "ORBreakout()" in problem and fired == 0                  # paused by the flag, not dropped
        asyncio.run(reactivate())
        _set(screener_breakouts=True)
        status, problem, fired = cycle(now + timedelta(minutes=2))
        assert status == "active" and problem is None and fired == 1                            # resumes on the same bar
    finally:
        _set(screener_v2=False, screener_breakouts=False, screener_intrabar=False)


# ------------------------------------------------------------------------- review of #181: exchange clock, cost, guards
MCX_DAY = 174                                                           # 5m bars from 09:00 to 23:30 IST


def _mcx_frame():
    days = [quiet_day(MCX_DAY) for _ in range(21)] + [orb_day(rest=above(MCX_DAY - 7))]
    return sessions_frame(days, starts={k: "03:30" for k in range(22)})  # 09:00 IST


def test_currency_derivatives_have_their_own_clock_in_the_screener_and_the_worker():
    cds = ses.calendar_for("CDS")
    assert (cds.name, cds.open, cds.close, cds.cutoff) == ("CDS", time(9, 0), time(17, 0), time(16, 30))
    assert ses.calendar_for("BCD").name == ses.calendar_for("NSE_CD").name == "CDS"
    # BK2-8: the screener reads the platform calendar's CDS family - the trading worker's clock (tests/test_bk2_8_cds_worker.py)
    assert mcal.session_family("CDS") == mcal.session_family("NSE_CD") == "CDS" and "CDS" in mcal.EXCHANGE_SESSIONS
    assert mcal.intraday_cutoffs("CDS") == (time(16, 30), time(16, 45)) and mcal.intraday_cutoffs("NSE") == (time(15, 0), time(15, 15))


def test_mcx_screenql_and_the_breakout_run_read_the_same_exchange_clock(monkeypatch):
    f = _mcx_frame()
    bar = 21 * MCX_DAY + 6                                               # starts 09:30 IST: the 15-minute range was 09:00-09:15
    mcx = SymbolData("CRUDEOIL", {"5m": f}, exchange="MCX")
    got = evaluate(parse('ORBreakout("up")'), mcx, base_tf="5m").fillna(False).astype(bool).to_numpy()
    assert list(np.flatnonzero(got)) == [bar]
    # on the NSE clock the same bars would open at 09:15 (09:00-09:15 pre-open): a 30-minute range then swallows the break
    thirty = 'ORBreakout("up", 30)'
    assert bar in np.flatnonzero(evaluate(parse(thirty), mcx, base_tf="5m").fillna(False).astype(bool).to_numpy())
    nse = evaluate(parse(thirty), SymbolData("CRUDEOIL", {"5m": f}), base_tf="5m").fillna(False).astype(bool).to_numpy()
    assert bar not in np.flatnonzero(nse)
    assert np.isnan(evaluate(parse("SessionVWAP()"), SymbolData("CRUDEOIL", {"5m": f}), base_tf="5m").iloc[21 * MCX_DAY])   # pre-open on NSE
    engine = run_breakout("B16", {"5m": f}, "5m", params={"htf_filter": "off"}, within=None, calendar=ses.calendar_for("MCX")).signals
    assert [(r["bar"], r["clock"], r["minutes_from_open"]) for r in engine] == [(bar, "09:30", 30)]
    # the session series follow the clock too: VWAP and time-of-day volume restart at 09:00
    assert evaluate(parse("SessionVWAP()"), mcx, base_tf="5m").iloc[21 * MCX_DAY] == pytest.approx(sum(OR_BARS[0][1:4]) / 3)
    # and the breakout run API on MCX lists the same bar
    from app.screener.breakouts import routes as bk_routes
    from tests.test_auth_api import client
    from tests.test_phase_l_ai import _owner
    from tests.test_z7_bk1_api import _set

    async def fake_fetch(session, tenant_id, symbols, exchange, base_tf, lookback, now=None, include_forming=False, **kw):
        assert exchange == "MCX"
        return [SymbolData(s, {base_tf: f}, exchange=exchange) for s in symbols], {}, "broker:fake"
    monkeypatch.setattr(bk_routes, "fetch_frames", fake_fetch)
    _set(screener_v2=True, screener_breakouts=True)
    try:
        headers, _ = _owner("bk2-mcx@example.com")
        out = client.post("/api/screener/breakouts/run", json={"screen": "B16", "signal_tf": "5m", "exchange": "MCX", "within": 500,
                                                               "params": {"htf_filter": "off"}, "symbols": ["CRUDEOIL"]}, headers=headers)
        assert out.status_code == 200, out.text
        assert [(r["bar"], r["clock"]) for r in out.json()["rows"]] == [(bar, "09:30")]
        # alerts keep the NSE bar-close clock: an MCX alert rule on a session function is refused with the reason
        r = client.post("/api/alerts/rules", json={"name": "m", "kind": "instrument", "symbol": "CRUDEOIL", "exchange": "MCX",
                                                   "condition": 'ORBreakout("up")', "base_tf": "5m"}, headers=headers)
        assert r.status_code == 422 and "NSE session clock" in str(r.json())
        ok = client.post("/api/alerts/rules", json={"name": "n", "kind": "instrument", "symbol": "TCS", "exchange": "NSE",
                                                    "condition": 'ORBreakout("up")', "base_tf": "5m"}, headers=headers)
        assert ok.status_code == 201, ok.text
    finally:
        _set(screener_v2=False, screener_breakouts=False)


def test_daily_context_of_the_15m_templates_is_fetched_on_the_saved_and_alert_path():
    from app.screener.routes import MAX_INTRADAY_FETCH_DAYS, fetch_days
    for tid in ("intraday_15m_orb30", "intraday_15m_first_hour"):
        t = templates.BY_ID[tid]
        _, validated = compile_screen(templates.screenql(t.screen, t.signal_tf, templates.template_params(t))[0], base_tf=t.signal_tf)
        assert validated.fetch_lookback["1d"] >= bp.DEFAULTS["dow_min_bars"]          # the daily history is fetched for the trend
        assert validated.fetch_lookback["15m"] >= (bp.DEFAULTS["tod_sessions"] + 1) * 25  # and whole sessions for the averages
        assert fetch_days(validated.fetch_lookback, t.signal_tf) <= MAX_INTRADAY_FETCH_DAYS


def test_time_of_day_volume_matches_the_plain_definition_with_gaps_and_special_sessions():
    days = _varied(28)
    days[9] = days[9][:40]                                               # a half day
    f = sessions_frame(days, starts={14: "12:45"})                       # an evening session
    f.iloc[::17, f.columns.get_loc("volume")] = np.nan                   # missing volumes
    for mode in ("bar", "cumulative"):
        sm = ses.map_sessions(f.index, 5, ses.DEFAULT_CALENDAR)
        got = ses.time_of_day_rvol(f, sm, 20, 5, mode)
        v = f["volume"].to_numpy(float)
        if mode == "cumulative":
            v = pd.Series(np.where(sm.in_session, v, np.nan)).groupby(pd.Series(sm.session)).cumsum().to_numpy(float)
        want = np.full(len(f), np.nan)
        hist = {}
        for j in np.flatnonzero(sm.in_session):                          # the definition, one bar at a time
            h = hist.setdefault(sm.minute[j], [])
            last = h[-20:]
            if len(last) >= 5 and np.mean(last) > 0 and np.isfinite(v[j]):
                want[j] = v[j] / np.mean(last)
            if not sm.special[sm.session[j]] and np.isfinite(v[j]):
                h.append(v[j])
        assert np.allclose(got, want, equal_nan=True), mode


def test_a_screen_builds_its_sessions_once(monkeypatch):
    calls = []
    real = ses.map_sessions
    monkeypatch.setattr(ses, "map_sessions", lambda *a, **k: calls.append(1) or real(*a, **k))
    f = sessions_frame(history() + [orb_day()])
    out = evaluate(parse('ORBreakout("up") AND SessionVWAP() < close AND RelVolumeTOD() > 2 AND DayExtremeBreak("up", 0, FALSE)'),
                   SymbolData("S", {"5m": f}), base_tf="5m")
    assert bool(out.iloc[ORB_BAR]) and len(calls) == 1


def test_wall_clock_stamps_without_a_time_zone_are_flagged():
    f = sessions_frame(history() + [orb_day()])
    naive_ist = f.set_axis(f.index.tz_convert("Asia/Kolkata").tz_localize(None))   # 09:15 written as 09:15, no zone
    run = run_breakout("B16", {"5m": naive_ist}, "5m", within=None)
    assert run.signals == [] and any("wall-clock" in n for n in run.notes)
    assert not any("wall-clock" in n for n in run_breakout("B16", {"5m": f}, "5m", within=None).notes)


def test_the_range_must_be_a_whole_number_of_bars_at_validation():
    def problems(text, tf):
        return [p.message for p in compile_screen(text, base_tf=tf)[1].problems]
    assert problems('ORBreakout("up", 20)', "15m") == ['ORBreakout: minutes 20 is not a whole number of @15m bars (15 minutes each)']
    assert problems('ORBreakout("up", 20)', "5m") == [] and problems('ORBreakout("up")', "15m") == []
    assert problems('ORBreakout("up", 30)', "15m") == [] and problems('ORBreakout("up", 15)', "3m") == []


def test_the_alert_engine_rechecks_the_clock_and_keeps_a_failing_screen_on_its_rule(monkeypatch):
    from app.alerts import barclose
    from app.db.models import AlertRuleRecord
    from tests.test_auth_api import _session_factory
    from tests.test_phase_l_ai import _owner
    from tests.test_s4a_barclose import FakeFetch, _rule

    _, me = _owner("bk2-engine-guards@example.com")
    f = sessions_frame(history() + [orb_day()])
    fetch = FakeFetch({"TCS": f.iloc[:ORB_BAR + 1]})
    now = (f.index[ORB_BAR] + timedelta(minutes=6)).to_pydatetime()

    def evaluate_once(rid):
        async def go():
            async with _session_factory() as session:
                rule = await session.get(AlertRuleRecord, rid)
                out = await barclose.evaluate_rule(session, rule, now, fetch)
                problem = rule.last_problem
                rule.status = "paused"
                await session.commit()
                return out, problem
        return asyncio.run(go())
    # a rule stored before the API refused it (or edited in the database): the engine refuses MCX session functions too
    mcx = _rule(me["tenant_id"], symbol="TCS", condition='ORBreakout("up")', tf="5m", exchange="MCX")
    (state, fired), problem = evaluate_once(mcx)
    assert (state, fired) == ("unsupported", 0) and "NSE session clock" in problem
    # a screen that fails at run time is that rule's problem (state error), not an exception out of the cycle
    monkeypatch.setattr(barclose, "run_screen", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    nse = _rule(me["tenant_id"], symbol="TCS", condition='ORBreakout("up")', tf="5m")
    (state, fired), problem = evaluate_once(nse)
    assert (state, fired) == ("error", 0) and "boom" in problem
