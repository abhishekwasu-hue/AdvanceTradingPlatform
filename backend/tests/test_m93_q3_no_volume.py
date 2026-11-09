"""M93-Q3 (owner decision, "Default ho"): a missing or 0 volume is no volume data - the volume condition is not applied
and the result says "volume not available" (never a silent pass or a silent no-match); fundamentals screens with no
stored figures say so.

The audit (scanner audit, legends + breakouts reports) found three silent answers on real data:
  A. daily index bars from the NSE files carry volume 0: `volume >= 1.5 * SMA(volume, 50)[1]` was `0 >= 0` = a match;
     the breakout engines' volume confirmation failed instead (an index never matched);
  B. intraday index bars take the near-month future's volume, 0 where the future has no bar: the time-of-day relative
     volume averaged those zeros as traded volume (inflated, the confirmation nearly always passed);
  C. fundamentals screens returned matched=False with no reason when a company had no stored figures.
Each is covered here, and stocks with real volume are shown to give exactly the same answers as before."""
import numpy as np
import pandas as pd
import pytest

from app.market_data.index_volume import is_index, screen_frame
from app.screener import compile_screen, reasons
from app.screener import legends as lg
from app.screener.breakouts import params as bp
from app.screener.breakouts.quality import run_breakout
from app.screener.breakouts.screens import VOLUME_REQUIRED_NOTE, VOLUME_SKIPPED_NOTE, Bars, evaluate_screen, skip_missing_volume
from app.screener.runtime import SymbolData, _last, evaluate, last_match, resample, run_screen
from app.screener.watch_screen import screen_marks
from tests.breakout_fixtures import SESSION_5M, TRUE_BREAK, base_then, history, orb_day, sessions_frame

ORB_BAR = 21 * SESSION_5M + 6


def _card(card_id):
    card = lg.load().card(card_id)
    ast, validated = compile_screen(card.source, base_tf=card.base_tf, params=card.defaults())
    assert validated.ok, validated.problems
    return ast, validated, card.defaults()


def _darvas_frame(volume_last=3000.0, volume=1000.0, n=300):
    """A flat year, then a close out of the 20-bar box to a new high on `volume_last` (a Darvas breakout for a stock)."""
    c = np.full(n, 100.0)
    c[-1] = 103.0
    idx = pd.date_range("2025-01-06", periods=n, freq="B", tz="UTC") - pd.Timedelta(hours=5, minutes=30)
    v = np.full(n, volume)
    v[-1] = volume_last
    return pd.DataFrame({"open": c - 0.2, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": v}, index=idx)


def _run(ast, validated, universe, params, **kw):
    return run_screen(ast, validated, universe, base_tf="1d", params=params, **kw)


# ---------------------------------------------------------------------------------------------- A: daily index volume 0
def test_index_names_are_recognised_and_stocks_are_not():
    for name in ("NIFTY 50", "NIFTY50", "NIFTY", "NIFTY BANK", "NIFTYBANK", "BANKNIFTY", "SENSEX", "INDIA VIX"):
        assert is_index(name), name
    for name in ("RELIANCE", "TCS", "NIFTYBEES", ""):
        assert not is_index(name), name


def test_an_index_daily_volume_screen_is_not_checked_never_a_match_from_zero_against_zero():
    ast, validated, params = _card("legends.darvas_box_breakout")
    zero = _darvas_frame(volume_last=0.0, volume=0.0)
    # the silent pass the audit found: the same bars read as volume (0 >= 0) match
    assert bool(_last(evaluate(ast, SymbolData("ZEROVOL", {"1d": zero}), base_tf="1d", params=params)))
    for vectorized in (False, True):
        got = _run(ast, validated, [SymbolData("NIFTY50", {"1d": zero}, exchange="NSE"),
                                    SymbolData("NIFTYBANK", {"1d": zero.copy()}, exchange="NSE")], params, vectorized=vectorized)
        for m in got:
            assert not m.matched and m.reason is not None, (vectorized, m)
            assert reasons.as_reason(m.reason).code == reasons.NO_VOLUME
            assert "volume not available" in m.reason
            row = reasons.row(m.symbol, m.matched, m.reason, m.note)
            assert row["code"] == reasons.NO_VOLUME and reasons.unread_rows([row]) == [row]     # counted apart, not "did not match"


def test_a_weekly_index_volume_screen_is_not_checked():
    ast, validated, params = _card("legends.weinstein_stage2_breakout")
    weekly = resample(_darvas_frame(volume_last=0.0, volume=0.0), "1d", "1w")             # the old 0 a weekly bucket summed to
    [m] = run_screen(ast, validated, [SymbolData("NIFTY 50", {"1w": weekly})], base_tf="1w", params=params)
    assert not m.matched and reasons.as_reason(m.reason).code == reasons.NO_VOLUME


def test_a_price_only_screen_on_an_index_is_answered_as_before():
    ast, validated = compile_screen("close > Max(high, 20)[1]", base_tf="1d")
    zero = _darvas_frame(volume_last=0.0, volume=0.0)
    [m] = _run(ast, validated, [SymbolData("NIFTY50", {"1d": zero})], {})
    assert m.matched and m.reason is None and m.note is None


def test_stocks_with_real_volume_give_the_same_answers_before_and_after():
    """Before = the old per-symbol answer (`last_match` of the evaluated screen, no M93-Q3 rules); after = run_screen."""
    screens = [_card("legends.darvas_box_breakout"), _card("legends.livermore_pivotal_breakout")]
    texts = ["volume > 1.2 * SMA(volume, 20)[1] AND close > SMA(close, 20)", 'RangeBreakout("up")', "RelVolume(20) >= 1.5",
             "OBV() > OBV()[5]", 'VolumeBreakout("up")', "VWAP() < close"]
    for text in texts:
        ast, validated = compile_screen(text, base_tf="1d")
        assert validated.ok, (text, validated.problems)
        screens.append((ast, validated, {}))
    frames = {"UPVOL": _darvas_frame(), "LOWVOL": _darvas_frame(volume_last=900.0), "RNG": base_then([TRUE_BREAK]),
              "RNGQ": base_then([TRUE_BREAK[:4] + (1000.0,)])}
    for ast, validated, params in screens:
        universe = [SymbolData(s, {"1d": f}) for s, f in frames.items()]
        before = [last_match(d.symbol, _last(evaluate(ast, d, base_tf="1d", params=params))) for d in universe]
        for vectorized in (False, True):
            after = run_screen(ast, validated, [SymbolData(s, {"1d": f}) for s, f in frames.items()], base_tf="1d", params=params,
                               check_history=False, vectorized=vectorized)
            assert [(m.symbol, m.matched, m.reason, m.note) for m in after] == [(m.symbol, m.matched, m.reason, None) for m in before]


def test_weekly_bars_of_missing_volume_stay_missing_and_stock_weekly_sums_are_unchanged():
    daily = _darvas_frame(volume_last=0.0, volume=0.0)
    weekly = resample(screen_frame(daily), "1d", "1w")
    assert len(weekly) and weekly["volume"].isna().all()                                   # never a weekly 0 again
    stock = _darvas_frame()
    stock["volume"] = np.arange(len(stock), dtype=float) + 1000.0
    w = resample(stock, "1d", "1w")
    old_rule = stock["volume"].tz_convert("Asia/Kolkata").resample("W-MON", label="left", closed="left").sum()   # the old plain sum
    old_rule.index = old_rule.index.tz_convert("UTC")
    assert w["volume"].dtype == stock["volume"].dtype and w["volume"].notna().all()
    assert np.array_equal(w["volume"].to_numpy(), old_rule.reindex(w.index).to_numpy())


def test_the_watch_list_reads_the_index_as_the_screener_does():
    ast, validated, params = _card("legends.darvas_box_breakout")
    out = screen_marks(ast, validated, SymbolData("NIFTY50", {"1d": _darvas_frame(volume_last=0.0, volume=0.0)}), base_tf="1d",
                       params=params)
    assert not out["matched"] and reasons.as_reason(out["reason"]).code == reasons.NO_VOLUME


# ----------------------------------------------------------------------------------- the breakout volume confirmations
def test_a_breakout_volume_confirmation_without_volume_data_is_not_applied_and_says_so():
    f = base_then([TRUE_BREAK])
    j = len(f) - 1
    nan = f.assign(volume=np.nan)
    p = bp.settings()
    # outside the screener's rule (the strategy templates): exactly as before - no volume, no confirmation, no signal
    assert not bool(evaluate_screen(nan, "B1", p)["up"]["signal"].iloc[j])
    with skip_missing_volume() as notes:
        b = Bars(nan, p)
        up = evaluate_screen(nan, "B1", p, bars=b)["up"]
    assert bool(up["signal"].iloc[j]) and b.volume_skipped[j] and notes.skipped and not notes.required
    run = run_breakout("B1", {"1d": nan}, "1d", within=1)
    assert [s["bar"] for s in run.signals] == [j] and VOLUME_SKIPPED_NOTE in run.notes
    assert run.signals[0]["rvol"] is None and any("no volume data" in w for w in run.signals[0]["why"])
    # a stock's real volume: the confirmation decides as always, no note
    assert [s["bar"] for s in run_breakout("B1", {"1d": f}, "1d", within=1).signals] == [j]
    quiet = base_then([TRUE_BREAK[:4] + (1000.0,)])
    r = run_breakout("B1", {"1d": quiet}, "1d", within=1)
    assert r.signals == [] and VOLUME_SKIPPED_NOTE not in r.notes


def test_the_breakout_engine_is_unchanged_on_real_volume():
    f = base_then([TRUE_BREAK])
    p = bp.settings()
    for screen in ("B1", "B4", "B7", "B13", "B14"):
        plain = evaluate_screen(f, screen, p)
        with skip_missing_volume() as notes:
            ruled = evaluate_screen(f, screen, p)
        for side in ("up", "down"):
            pd.testing.assert_frame_equal(plain[side], ruled[side])
        assert not notes.skipped and not notes.required


def test_screenql_breakout_on_an_index_carries_the_note():
    ast, validated = compile_screen('RangeBreakout("up")', base_tf="1d")
    f = base_then([TRUE_BREAK])
    index = f.assign(volume=0.0)
    [m] = run_screen(ast, validated, [SymbolData("NIFTY50", {"1d": index})], base_tf="1d", check_history=False)
    assert m.matched and m.reason is None and m.note == VOLUME_SKIPPED_NOTE
    row = reasons.row(m.symbol, m.matched, m.reason, m.note)
    assert row["note"] == VOLUME_SKIPPED_NOTE and "code" not in row
    [s] = run_screen(ast, validated, [SymbolData("STOCK", {"1d": f})], base_tf="1d", check_history=False)
    assert s.matched and s.note is None


def test_a_volume_breakout_on_an_index_cannot_be_checked():
    ast, validated = compile_screen('VolumeBreakout("up")', base_tf="1d")
    index = base_then([TRUE_BREAK]).assign(volume=0.0)
    [m] = run_screen(ast, validated, [SymbolData("NIFTY50", {"1d": index})], base_tf="1d", check_history=False)
    assert not m.matched and reasons.as_reason(m.reason).code == reasons.NO_VOLUME
    run = run_breakout("B14", {"1d": index.assign(volume=np.nan)}, "1d", within=1)
    assert run.signals == [] and VOLUME_REQUIRED_NOTE in run.notes


# ------------------------------------------------------------------------- B: intraday index volume from the future
def _index_minutes(real_sessions=10, sessions=30, missing=np.nan):
    """An index's 5m bars: volume only for the last `real_sessions` sessions (the future's bars), `missing` before."""
    days = [[(100.0, 100.25, 99.75, 100.0, 1000.0) for _ in range(SESSION_5M)] for _ in range(sessions)]
    f = sessions_frame(days)
    first_real = (sessions - real_sessions) * SESSION_5M
    f.iloc[:first_real, f.columns.get_loc("volume")] = missing
    return f


def test_time_of_day_relative_volume_skips_bars_without_volume():
    p = bp.settings()
    zero = _index_minutes(missing=0.0)
    nan = _index_minutes()
    last = len(nan) - 1
    # the audit's artefact: zeros averaged as traded volume inflate the ratio (9 real sessions of the 20 averaged: 2.2x)
    assert Bars(zero, p, tf="5m").tod_rvol()[last] == pytest.approx(1000.0 / (9 * 1000.0 / 20))
    # missing data is skipped: the same-time average of the sessions that had volume (1000) -> 1.0
    assert Bars(nan, p, tf="5m").tod_rvol()[last] == pytest.approx(1.0)
    assert np.allclose(Bars(screen_frame(zero), p, tf="5m").tod_rvol(), Bars(nan, p, tf="5m").tod_rvol(), equal_nan=True)
    # the first sessions with volume (fewer than tod_min_sessions before them) have no value - and it is "no volume data"
    b = Bars(nan, p, tf="5m")
    early = (30 - 10 + 2) * SESSION_5M + 3
    assert np.isnan(b.tod_rvol()[early]) and b.volume_missing(True)[early] and not b.volume_missing(True)[last]


def test_the_plain_volume_average_never_mixes_bars_without_volume():
    p = bp.settings()
    nan = _index_minutes(real_sessions=1, sessions=3)
    b = Bars(nan, p, tf="5m")
    j = 2 * SESSION_5M + 5                    # 5 bars with volume, 15 without in its 20-bar window (the audit's 30 Sep bar)
    assert np.isnan(b.vol_avg[j]) and np.isnan(b.rvol[j]) and b.volume_missing(False)[j]
    k = 2 * SESSION_5M + 25                   # 20 bars with volume: a real average
    assert b.vol_avg[k] == pytest.approx(1000.0) and not b.volume_missing(False)[k]


def test_an_intraday_breakout_on_an_index_before_the_future_volume_is_not_confirmed_by_zeros():
    days = history() + [orb_day()]
    f = sessions_frame(days)
    p = bp.settings()
    assert [s["bar"] for s in run_breakout("B16", {"5m": f}, "5m", within=None).signals] == [ORB_BAR]     # a stock: as always
    nan = f.assign(volume=np.nan)
    assert not evaluate_screen(nan, "B16", p, tf="5m")["up"]["signal"].any()                            # old rule: never
    run = run_breakout("B16", {"5m": nan}, "5m", within=None)
    assert [s["bar"] for s in run.signals] == [ORB_BAR] and VOLUME_SKIPPED_NOTE in run.notes          # not applied, said so
    assert all(s["vwap"] is None for s in run.signals)


# ------------------------------------------------------------------------------------- C: fundamentals with no figures
@pytest.mark.parametrize("card_id", ["legends.graham_net_net", "legends.graham_number_value", "legends.oneil_growth_breakout"])
def test_a_fundamentals_screen_with_no_stored_figures_says_so(card_id):
    ast, validated, params = _card(card_id)
    [m] = run_screen(ast, validated, [SymbolData("NOFIGS", {"1d": _darvas_frame()}, fundamentals=[])], base_tf="1d", params=params)
    assert not m.matched and reasons.as_reason(m.reason).code == reasons.NO_FIGURES and "no figures stored" in m.reason


def test_greenblatt_ranks_say_which_company_has_no_figures():
    ast, validated, params = _card("legends.greenblatt_yield_and_return")
    universe = [SymbolData("NOFIGS", {"1d": _darvas_frame()}, fundamentals=[]), SymbolData("NOFIGS2", {"1d": _darvas_frame()}, fundamentals=[])]
    for m in run_screen(ast, validated, universe, base_tf="1d", params=params):
        assert not m.matched and reasons.as_reason(m.reason).code == reasons.NO_FIGURES
