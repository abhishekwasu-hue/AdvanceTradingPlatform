"""SCR-FIX round 2 (13 Nov re-audit of the pre-built screens): the fixes, each with a positive and a negative fixture and a
no-look-ahead (truncation) check.

1. Crabel opening-range card: `first_break_only` (default 1) - only the session's first close above the opening-range
   high; 0 = every new cross (the earlier behaviour). ScreenQL `FirstInSession(cond)`.
2. Daily B8 gap breakout (daily_gap_base): one signal per leg, as the other breakouts (a run of gap days was one signal a
   day); intraday B8 stays one per session.
3. The week / month close on the NSE clock steps back over the exchange's holidays (`market_holidays`, as this process
   last read it) to the last trading session; with no list read, weekends only (as before).
4. Card texts: the net-net card says preference share capital is not subtracted; the Williams card says how often it fires.
"""
from datetime import date

import numpy as np
import pandas as pd

from app.market_data import calendar as cal
from app.screener import compile_screen, legends, library, parse
from app.screener.breakouts import params as bp
from app.screener.breakouts.screens import evaluate_screen, one_per_event
from app.screener.runtime import SymbolData, bar_close, closed_only, evaluate, resample
from tests.breakout_bk4_fixtures import rows_frame


# ------------------------------------------------------------------------------------------------------------ helpers
def _session(day: str, closes, start="03:45"):
    """5m bars of one session from 09:15 IST (03:45 UTC); high = close + 0.1, low = close - 0.1."""
    idx = pd.date_range(f"{day} {start}", periods=len(closes), freq="5min", tz="UTC")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c + 0.1, "low": c - 0.1, "close": c, "volume": 1000.0}, index=idx)


def _crabel_sessions():
    """Two sessions: a 30-minute range (6 bars, high 100.1), then crosses above it at bars 7, 10 and 13 of the first
    session and one at bar 8 of the second."""
    first = [100.0] * 6 + [99.9, 100.5, 100.6, 99.8, 100.4, 100.3, 99.7, 100.9, 101.0]
    second = [100.0] * 6 + [99.9, 99.8, 100.7, 100.8]
    return pd.concat([_session("2026-03-02", first), _session("2026-03-03", second)])


def _card(ident):
    return legends.load().card(ident)


def _crabel_series(f, **override):
    card = _card("legends.crabel_opening_range_breakout")
    params = {**card.defaults(), **override}
    ast = library.inline_params(parse(card.source), params)
    return evaluate(ast, SymbolData("S", {"5m": f}), base_tf="5m").astype(bool)


# ------------------------------------------------------------------------------------------------- 1. FirstInSession
def test_first_in_session_is_the_first_bar_of_each_session_where_cond_holds():
    f = _crabel_sessions()
    got = evaluate(parse("FirstInSession(close > 100.2)"), SymbolData("S", {"5m": f}), base_tf="5m").astype(bool)
    assert list(np.flatnonzero(got.to_numpy())) == [7, 15 + 8]           # once per session, the first bar that held
    never = evaluate(parse("FirstInSession(close > 200)"), SymbolData("S", {"5m": f}), base_tf="5m").astype(bool)
    assert not never.any()


def test_the_crabel_card_takes_the_first_break_per_session_by_default():
    f = _crabel_sessions()
    card = _card("legends.crabel_opening_range_breakout")
    assert card.defaults()["first_break_only"] == 1 and "FirstInSession(" in card.source
    assert list(np.flatnonzero(_crabel_series(f).to_numpy())) == [7, 15 + 8]
    # 0 = every new cross above the range high (the earlier behaviour)
    assert list(np.flatnonzero(_crabel_series(f, first_break_only=0).to_numpy())) == [7, 10, 13, 15 + 8]


def test_the_crabel_card_has_no_look_ahead():
    f = _crabel_sessions()
    for setting in (1, 0):
        full = _crabel_series(f, first_break_only=setting)
        for cut in range(7, len(f) + 1):
            part = _crabel_series(f.iloc[:cut], first_break_only=setting)
            assert bool(part.iloc[-1]) == bool(full.iloc[cut - 1]), (setting, cut)


def test_first_in_session_mirrors_and_compiles_on_intraday_only():
    from app.screener import mirror
    m = mirror.mirror(parse("FirstInSession(close > ORHigh(30))"))
    assert m.text is not None and "FirstInSession(" in m.text and "ORLow(30)" in m.text
    assert compile_screen("FirstInSession(close > open)", base_tf="5m")[1].ok
    assert not compile_screen("FirstInSession(close > open)", base_tf="1d")[1].ok


# ------------------------------------------------------------------------------------------------ 2. Daily B8 dedupe
def _gap_run():
    """20 quiet days (high 100.5), three gap days in a row above the 20-day high, a close back below the base, a new gap."""
    rows = [(100.0, 100.5, 99.5, 100.0, 1000.0) for _ in range(20)]
    rows += [(101.0, 101.7, 100.9, 101.5, 2000.0), (102.0, 102.7, 101.9, 102.5, 2000.0), (103.0, 103.7, 102.9, 103.5, 2000.0),
             (103.4, 103.6, 99.6, 100.0, 1000.0), (104.0, 104.6, 103.9, 104.5, 2000.0)]
    return rows_frame(rows)


def test_daily_gap_runs_give_one_signal_per_leg():
    f = _gap_run()
    out = evaluate_screen(f, "B8", bp.settings(gap_level="base", gap_base_n=20), tf="1d")["up"]
    assert list(np.flatnonzero(out["raw"].to_numpy())) == [20, 21, 22, 24]
    assert list(np.flatnonzero(out["signal"].to_numpy())) == [20, 24]        # the run is one event; after the failed break, a new one


def test_intraday_gaps_stay_one_per_session():
    from app.screener.breakouts.screens import Bars
    daily = Bars(_gap_run(), bp.settings(), tf="1d")
    m5 = Bars(_session("2026-03-02", [100.0] * 10), bp.settings(), tf="5m")
    assert not one_per_event("B8", daily) and one_per_event("B8", m5) and one_per_event("B9", daily)


def test_daily_gap_dedupe_has_no_look_ahead():
    f = _gap_run()
    p = bp.settings(gap_level="base", gap_base_n=20)
    full = evaluate_screen(f, "B8", p, tf="1d")["up"]["signal"]
    for cut in range(21, len(f) + 1):
        part = evaluate_screen(f.iloc[:cut], "B8", p, tf="1d")["up"]["signal"]
        pd.testing.assert_series_equal(part, full.iloc[:cut], check_names=False)


# -------------------------------------------------------------------------------------- 3. week close on a holiday
GOOD_FRIDAY = date(2025, 4, 18)                     # a fixture date: a Friday listed as closed


def _week_days():
    """Daily bars Mon 14 - Thu 17 Apr 2025 (Friday closed) and Mon 21 - Fri 25 Apr (00:00 IST stamps = 18:30 UTC)."""
    days = list(pd.bdate_range("2025-04-14", "2025-04-25"))
    days = [d for d in days if d.date() != GOOD_FRIDAY]
    idx = pd.DatetimeIndex(days).tz_localize("Asia/Kolkata").tz_convert("UTC")
    c = np.arange(len(idx), dtype=float) + 100.0
    return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1000.0}, index=idx)


def _ist(ts):
    return pd.Timestamp(ts).tz_convert("Asia/Kolkata").strftime("%a %d %b %H:%M")


def test_a_week_whose_friday_is_a_holiday_closes_on_its_last_session():
    weekly = resample(_week_days(), "1d", "1w", exchange="NSE")
    assert [_ist(t) for t in bar_close(weekly.index, "1w", "NSE")] == ["Fri 18 Apr 15:30", "Fri 25 Apr 15:30"]   # no list read
    cal.remember_holidays("NSE", {GOOD_FRIDAY})
    assert [_ist(t) for t in bar_close(weekly.index, "1w", "NSE")] == ["Thu 17 Apr 15:30", "Fri 25 Apr 15:30"]


def test_a_month_whose_last_weekday_is_a_holiday_closes_a_day_earlier():
    idx = pd.DatetimeIndex([pd.Timestamp("2025-10-01", tz="Asia/Kolkata")]).tz_convert("UTC")
    assert _ist(bar_close(idx, "1M", "NSE")[0]) == "Fri 31 Oct 15:30"
    cal.remember_holidays("NSE", {date(2025, 10, 31)})
    assert _ist(bar_close(idx, "1M", "NSE")[0]) == "Thu 30 Oct 15:30"


def test_a_holiday_never_moves_a_close_before_the_bar_starts():
    cal.remember_holidays("NSE", {date(2025, 4, 14 + k) for k in range(5)})        # a whole week closed (fixture)
    idx = pd.DatetimeIndex([pd.Timestamp("2025-04-14", tz="Asia/Kolkata")]).tz_convert("UTC")
    assert _ist(bar_close(idx, "1w", "NSE")[0]) == "Mon 14 Apr 15:30"


def test_the_holiday_week_is_closed_on_thursday_evening_not_before():
    cal.remember_holidays("NSE", {GOOD_FRIDAY})
    weekly = resample(_week_days(), "1d", "1w", exchange="NSE")
    thu_close = pd.Timestamp("2025-04-17 15:30", tz="Asia/Kolkata").tz_convert("UTC").to_pydatetime()
    assert len(closed_only(weekly, "1w", thu_close, "NSE")) == 1
    assert len(closed_only(weekly, "1w", thu_close - pd.Timedelta(minutes=1), "NSE")) == 0          # no look-ahead


class _Rows:
    """A stand-in session: every holiday query answers the fixture Friday."""

    def __init__(self):
        self.reads = 0

    async def scalars(self, query):
        self.reads += 1
        return [GOOD_FRIDAY]

    async def rollback(self):
        pass


def test_a_full_holiday_read_is_what_the_screener_clock_uses():
    import asyncio
    rows = _Rows()
    assert cal.known_holidays("NSE") == frozenset()
    asyncio.run(cal.load_holidays(rows, "NSE", 2025))                             # a one-year read is not the full list
    assert cal.known_holidays("NSE") == frozenset()
    asyncio.run(cal.warm_known_holidays(rows))
    assert cal.known_holidays("NSE") == {GOOD_FRIDAY} and GOOD_FRIDAY in cal.known_holidays("CDS")   # CDS closes on NSE days
    cal.forget_known_holidays()
    assert cal.known_holidays("NSE") == frozenset()


# ------------------------------------------------------------------------------------------------------ 4. card texts
def test_the_net_net_card_says_preference_capital_is_not_subtracted():
    assert "preference share capital is not subtracted" in _card("legends.graham_net_net").approximation


def test_the_williams_card_says_how_often_it_fires_and_keeps_the_source_rule():
    card = _card("legends.williams_volatility_breakout")
    assert "fires often" in card.approximation and card.source == "high >= open + $k * (high[1] - low[1])"
