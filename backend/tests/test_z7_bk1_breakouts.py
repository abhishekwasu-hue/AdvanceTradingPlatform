"""Z7 / U6 BK1: breakout screens B1 / B2 / B4 / B7 / B13 / B14, the quality overlay and the style / timeframe selector.

Spec docs/specs/ATP_BREAKOUT_SCREENERS_ADDENDUM.md §7: synthetic fixtures per screen (true breakout, wick-only fake,
low-volume break, break against the higher-timeframe trend, retest held / failed, NR7 follow-through), closed-candle /
no-look-ahead tests on the multi-timeframe context, one signal per leg, grade determinism, and parity between the
ScreenQL text of a template and the engine. Fixtures: tests/breakout_fixtures.py (bar by bar, no randomness) and
tests/sample_market.py (the seeded session generator) for the invariance and parity sweeps."""
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.screener import compile_screen, parse
from app.screener.breakouts import params as bp
from app.screener.breakouts import style, templates
from app.screener.breakouts.quality import atr_percentile, atr_percentile_series, context_trends, htf_pass, run_breakout
from app.screener.breakouts.screens import INTRADAY_SCREENS, SCREENS, Bars, evaluate_screen
from app.screener.registry import BREAKOUT_FUNCTIONS, FUNCTIONS
from app.screener.runtime import SymbolData, evaluate, resample, run_screen
from tests.breakout_fixtures import (HTF_DOWN, HTF_UP, LOW_VOLUME_BREAK, TRUE_BREAK, UPTREND, WICK_ONLY, base_then, box, context_frame,
                                     frame, zigzag)
from tests.sample_market import generate

RESEARCH = Path(__file__).resolve().parents[2] / "docs" / "research" / "breakouts.md"


def _signals(screen, f, tf="1d", **params):
    frames = f if isinstance(f, dict) else {tf: f}
    return run_breakout(screen, frames, tf, params=params, within=None).signals


def _one(rows, side="up"):
    rows = [r for r in rows if r["side"] == side]
    assert len(rows) == 1, rows
    return rows[0]


# ----------------------------------------------------------------------------------------------------------- parameters
def test_every_parameter_has_a_default_a_note_and_a_research_line():
    doc = RESEARCH.read_text(encoding="utf-8")
    for p in bp.PARAMS:
        assert p.doc and p.note, p.name
        assert f"`{p.name}`" in doc, f"{p.name} missing from docs/research/breakouts.md"
        assert p.note in doc, f"the research note of {p.name} differs from docs/research/breakouts.md"
        if p.choices:
            assert p.default in p.choices
        elif p.low is not None:
            assert p.low <= p.default <= p.high
    assert bp.settings() == bp.DEFAULTS


def test_bad_parameters_fall_back_or_raise():
    clean, errors = bp.validate({"vol_mult": -1, "range_mode": "magic", "nope": 1, "range_n": 2.5, "base_contraction": "yes"})
    assert clean["vol_mult"] == bp.DEFAULTS["vol_mult"] and clean["range_mode"] == "fixed" and clean["range_n"] == 20
    assert clean["base_contraction"] is True
    assert any("unknown breakout parameter 'nope'" in e for e in errors) and len(errors) == 5
    _, errors = bp.validate({"atr_pct_low": 80.0, "atr_pct_high": 20.0, "grade_b_min": 6, "grade_a_min": 2})
    assert len(errors) == 2
    _, errors = bp.validate({"context_ladder": {"15m": ["5m", "1d"]}})
    assert errors and "coarser" in errors[0]
    with pytest.raises(ValueError):
        bp.settings(vol_mult=float("nan"))


def test_screenql_argument_defaults_are_the_parameter_defaults():
    want = {"RangeBreakout": {"n": "range_n", "k": "min_break_atr", "v": "vol_mult", "mode": "range_mode"},
            "SwingBreakout": {"degree": "swing_degree", "k": "min_break_atr"}, "SwingBreakType": {"degree": "swing_degree", "k": "min_break_atr"},
            "NRBreakout": {"setup": "nr_setup", "v": "vol_mult", "k": "min_break_atr"},
            "NewHighBreakout": {"weeks": "high_weeks", "v": "vol_mult", "k": "min_break_atr"},
            "RangeExpansion": {"k": "expansion_k", "v": "vol_mult"}, "VolumeBreakout": {"v": "shock_mult", "n": "range_n"},
            "RelVolume": {"n": "vol_avg_n"}, "ATRPercentile": {"n": "atr_n", "lookback": "atr_pct_lookback"}, "DowTrend": {"degree": "htf_degree"},
            # strategy templates step 2 (R2): the range breakout's level companion takes the same defaults
            "RangeBreakoutRef": {"n": "range_n", "k": "min_break_atr", "v": "vol_mult", "mode": "range_mode", "stop_mult": "stop_atr_mult"},
            # step 4 (R2 for the other families): the swing, inside / NR and volume breakouts' companions
            "SwingBreakoutRef": {"degree": "swing_degree", "k": "min_break_atr", "stop_mult": "stop_atr_mult"},
            "NRBreakoutRef": {"setup": "nr_setup", "v": "vol_mult", "k": "min_break_atr", "stop_mult": "stop_atr_mult"},
            "VolumeBreakoutRef": {"v": "shock_mult", "n": "range_n", "stop_mult": "stop_atr_mult"}}
    bk2 = {"ORBreakout", "DayLevelBreak", "DayExtremeBreak", "RelVolumeTOD", "SessionVWAP"}   # tests/test_z7_bk2_intraday.py
    bk3 = {"VCPBreakout", "VCPReady", "DonchianBreakout", "SqueezeBreakout", "MABreakout", "RegimeBreakout",
           "DonchianBreakoutRef", "MABreakoutRef", "VCPBreakoutRef"}   # tests/test_z7_bk3_breakouts.py (the Refs: templates step 4)
    bk4 = {"TrendlineBreakout", "PatternBreakout", "GapBreakout", "GapType", "RetestEntry", "LineBreakoutRef",
           "RetestEntryRef", "PatternBreakoutRef", "GapBreakoutRef"}   # tests/test_z7_bk4_breakouts.py (RetestEntryRef: strategy templates step 3, R7)
    assert set(want) | bk2 | bk3 | bk4 == set(BREAKOUT_FUNCTIONS)
    for fn, args in want.items():
        spec = {a.name: a for a in FUNCTIONS[fn].args}
        for arg, param in args.items():
            assert spec[arg].default == bp.DEFAULTS[param], (fn, arg)
        assert FUNCTIONS[fn].flag == "screener_breakouts"


# ------------------------------------------------------------------------------------------------------ style selector
def test_style_and_context_ladder():
    assert style.context_timeframes("15m") == ("1h", "1d") and style.context_timeframes("1h") == ("1d", "1w")
    assert style.style_of("5m") == "intraday" and style.style_of("4h") == "swing" and style.style_of("1w") == "swing"
    assert style.context_timeframes("1w") == ("1M",)
    for tf in style.STYLES["intraday"] + style.STYLES["swing"]:
        ctx = style.context_timeframes(tf)
        steps = [bp.TF_MINUTES[tf]] + [bp.TF_MINUTES[c] for c in ctx]
        assert all(b > a for a, b in zip(steps, steps[1:]))
    with pytest.raises(ValueError):
        style.style_of("1M")
    assert style.selector()["default_universe"] == {"intraday": "FNO", "swing": "NIFTY500"}
    assert style.context_timeframes("15m", {"15m": ["30m", "1d"]}) == ("30m", "1d")


def test_2h_and_4h_bars_are_session_anchored_and_closed():
    df = generate(375 * 3, 100.0, 5)
    two = style.resample(df, "1m", "2h")
    assert [t.strftime("%H:%M") for t in two.index[:4]] == ["03:45", "05:45", "07:45", "09:45"]
    assert style.bar_close(two.index, "2h")[-1] <= df.index[-1] + pd.Timedelta(minutes=1)
    four = style.resample(df, "1m", "4h")
    assert four["high"].iloc[0] == df["high"].iloc[:240].max()


# ------------------------------------------------------------------------------------------------------------------ B1
def test_b1_true_breakout_is_graded_and_accepted():
    f = base_then([TRUE_BREAK, (103.8, 105.5, 103.5, 105.2, 1500.0)])
    s = _one(_signals("B1", f))
    assert s["bar"] == len(f) - 2 and s["level"] == 102.0 and s["base_len"] == 20 and s["rvol"] == 3.0
    assert s["base_height_pct"] == pytest.approx(2.0) and 0.5 < s["dist_atr"] < 1.2
    assert s["status"] == "accepted" and s["decided_bar"] == len(f) - 1
    assert "volume 3.0x the 20-bar average" in s["why"] and s["checks"]["base_dry_up"] is True


def test_b1_wick_only_fake_and_low_volume_break_are_not_breakouts():
    for bar in (WICK_ONLY, LOW_VOLUME_BREAK):
        f = base_then([bar])
        assert _signals("B1", f) == []
    up = evaluate_screen(base_then([WICK_ONLY]), "B1", bp.settings())["up"]
    assert bool(up["wick"].iloc[-1]) and not bool(up["raw"].iloc[-1])
    # with the volume condition off (an index without volume) the same bar breaks, and loses the volume point
    f = base_then([LOW_VOLUME_BREAK])
    s = _one([r for r in _signals("B1", f, vol_mult=0.0) if r["bar"] == len(f) - 1])
    assert s["checks"]["volume_expansion"] is False


def test_b1_auto_base_finds_the_consolidation():
    f = base_then([TRUE_BREAK], box_n=12)
    s = _one([r for r in _signals("B1", f, range_mode="auto") if r["bar"] == len(f) - 1])
    assert 12 <= s["base_len"] <= 15 and s["level"] == 102.0                 # the box plus the flat top of the advance
    # a base taller than the cap is a trend leg, not a consolidation
    assert [r for r in _signals("B1", f, range_mode="auto", base_max_height_atr=0.5) if r["bar"] == len(f) - 1] == []


def test_b1_prior_advance_filter():
    f = base_then([TRUE_BREAK])
    assert [r for r in _signals("B1", f, prior_advance_pct=20.0, prior_advance_n=30) if r["bar"] == len(f) - 1]
    assert not [r for r in _signals("B1", f, prior_advance_pct=80.0, prior_advance_n=30) if r["bar"] == len(f) - 1]


def test_breakdown_is_the_mirror_of_the_breakout():
    up = base_then([TRUE_BREAK])
    mirrored = pd.DataFrame({"open": 300 - up["open"], "high": 300 - up["low"], "low": 300 - up["high"], "close": 300 - up["close"],
                             "volume": up["volume"]}, index=up.index)
    a, b = _signals("B1", up), _signals("B1", mirrored)
    assert [(r["bar"], r["side"]) for r in a] == [(r["bar"], "up") for r in b]
    assert [300 - r["level"] for r in b] == [r["level"] for r in a]


# ------------------------------------------------------------------------------------------------------------------ B2
def test_b2_bos_with_the_trend_and_choch_against_it():
    up = frame(zigzag([60, 75, 66, 82, 73, 90, 81, 98, 89, 105], 5))
    rows = [r for r in _signals("B2", up, swing_degree=0) if r["side"] == "up"]
    # no trend read in the first 40 bars (DOW_MIN_BARS): a break there is a plain BREAK, then BOS with the uptrend
    assert [r["event"] for r in rows] == ["BREAK", "BREAK", "BOS"]
    assert rows[-1]["level"] == 98.5                                       # the last confirmed swing high (plus the spread)
    down = frame(zigzag([140, 125, 134, 119, 128, 113, 122, 107, 116, 101, 125], 5))
    last = _signals("B2", down, swing_degree=0)[-1]
    assert (last["side"], last["event"], last["level"]) == ("up", "CHOCH", 116.5)   # a downtrend's swing high broken
    # the series and the classifier agree with the engine
    sd = SymbolData("S", {"1d": down})
    sig = evaluate(parse('SwingBreakout("up", 0)'), sd, base_tf="1d")
    kind = evaluate(parse('SwingBreakType("up", 0)'), sd, base_tf="1d")
    assert list(np.flatnonzero(sig.to_numpy())) == [last["bar"]] and kind.iloc[last["bar"]] == "CHOCH" and kind.iloc[0] == ""


def test_b2_swing_must_be_confirmed_before_the_break():
    up = frame(zigzag([60, 75, 66, 82, 73, 90, 81, 98, 89, 105], 5))
    b = Bars(up, bp.settings())
    for r in _signals("B2", up, swing_degree=0):
        known = [pv for pv in b.pivots(0) if pv.kind == "H" and pv.confirmed_idx <= r["bar"] - 1]
        assert known and known[-1].price == r["level"]


# ------------------------------------------------------------------------------------------------------------------ B4
_NR = [(100, 101.5, 98.5, 100.5, 1000), (100.5, 101.4, 98.6, 99.8, 1000), (99.8, 101.3, 98.8, 100.9, 1000), (100.9, 101.2, 99.0, 100.2, 1000),
       (100.2, 101.1, 99.2, 100.8, 1000), (100.8, 101.0, 99.4, 100.3, 1000), (100.3, 100.6, 99.9, 100.4, 1000)]


def test_b4_nr7_follow_through_and_no_follow_through():
    warm = zigzag(UPTREND, 5)
    f = frame(warm + _NR + [(100.4, 102.5, 100.3, 102.2, 2000)])
    s = _one(_signals("B4", f))
    assert s["bar"] == len(f) - 1 and s["event"] == "NR7" and s["level"] == 100.6
    assert _signals("B4", frame(warm + _NR + [(100.4, 100.55, 100.0, 100.3, 2000)])) == []
    # the break must come on the next bar (nr_trigger_bars = 1); a later break needs a wider window
    late = frame(warm + _NR + [(100.4, 100.6, 99.5, 100.1, 1000), (100.1, 102.5, 100.0, 102.2, 3000)])
    assert _signals("B4", late) == [] and _one(_signals("B4", late, nr_trigger_bars=2))["level"] == 100.6


def test_b4_inside_bar_mother_rule():
    warm = zigzag(UPTREND, 5)
    mother, inside = (100.0, 103.0, 97.0, 101.0, 1000), (101.0, 102.0, 99.0, 101.5, 1000)
    brk = (101.5, 103.0, 101.4, 102.8, 2500)
    ok = frame(warm + [mother, inside, brk, (102.8, 103.5, 100.0, 100.5, 1000), (100.5, 104, 100.2, 103.8, 1000)])
    s = _one([r for r in _signals("B4", ok, nr_setup="inside") if r["side"] == "up"])
    assert s["event"] == "INSIDE" and s["level"] == 102.0 and s["status"] != "failed"     # back inside the inside bar is not failure
    fail = frame(warm + [mother, inside, brk, (102.8, 103.0, 96.0, 96.5, 1000)])
    assert _one([r for r in _signals("B4", fail, nr_setup="inside") if r["side"] == "up"])["status"] == "failed"   # below the mother low


# ------------------------------------------------------------------------------------------------------------------ B7
def _year_then(last_bar):
    bars = box(120, 94, 98) + zigzag([95, 104, 96, 103, 97, 102], 30) + box(40, 97, 102) + [last_bar]
    return frame(bars)


def test_b7_52_week_high_break_fresh_with_days_since():
    f = _year_then((101.5, 108.0, 101.3, 107.5, 2500.0))
    s = _one(_signals("B7", f))
    t = f.index[-1]
    window = f[(f.index >= t - pd.Timedelta(days=364)) & (f.index < t)]
    assert s["level"] == window["high"].max()
    old = int(np.flatnonzero(f["high"].to_numpy()[:-1] == window["high"].max())[-1])
    assert s["days_since"] == (f.index[-1] - f.index[old]).days and s["event"] == "FRESH"     # calendar days (L3)
    assert _one(_signals("B7", f, fresh_min_days=s["days_since"] + 1))["event"] == "EXTENDED"


def test_b7_needs_the_whole_window_and_a_close_beyond():
    full = _year_then((101.5, 108.0, 101.3, 107.5, 2500.0))
    assert _signals("B7", full.iloc[-200:]) == []                             # 200 sessions do not cover 52 weeks
    assert _signals("B7", _year_then((101.5, 108.0, 101.3, 103.0, 2500.0))) == []   # a wick through the old high
    with pytest.raises(ValueError):
        run_breakout("B7", {"1h": full}, "1h")


# ------------------------------------------------------------------------------------------------------- B13 and B14
def test_b13_range_expansion_needs_a_directional_close():
    warm = zigzag(UPTREND, 5) + box(25, 100, 102)
    s = _one(_signals("B13", frame(warm + [(101, 107.5, 100.8, 107.0, 2500)])))
    assert s["level"] == pytest.approx((107.5 + 100.8) / 2)
    assert _signals("B13", frame(warm + [(101, 107.5, 100.8, 104.0, 2500)])) == []        # closed mid-bar
    assert _signals("B13", frame(warm + [(101, 103.5, 100.8, 103.4, 2500)])) == []        # not wide enough


def test_b14_volume_shock_at_and_through_the_level():
    warm = zigzag(UPTREND, 5) + box(25, 100, 102)
    at = _one(_signals("B14", frame(warm + [(100.5, 101.9, 100.4, 101.8, 3000)])))
    through = _one(_signals("B14", frame(warm + [(101, 103.5, 100.9, 103.4, 3000)])))
    assert at["through"] is False and at["level"] == 100.4 and through["through"] is True and through["level"] == 102.0
    assert _signals("B14", frame(warm + [(101, 103.5, 100.9, 103.4, 1500)])) == []        # no shock


# ---------------------------------------------------------------------------------------------- overlay: HTF, status
TWO_CONTEXTS = {"1d": ["1w", "1M"]}                      # the editable ladder: weekly and monthly context on a daily signal


def _with_context(points):
    f = base_then([TRUE_BREAK, (103.8, 105.5, 103.5, 105.2, 1500.0)])
    return {"1d": f, "1w": context_frame(points, f.index[0], "W-MON"), "1M": context_frame(points, f.index[0], "MS")}


def test_break_against_the_higher_timeframe_trend_is_capped_at_c():
    against = _one(_signals("B1", _with_context(HTF_DOWN), context_ladder=TWO_CONTEXTS))
    agree = _one(_signals("B1", _with_context(HTF_UP), context_ladder=TWO_CONTEXTS))
    assert against["htf"] == {"1w": "DOWN", "1M": "DOWN"} and against["grade"] == "C" and against["htf_pass"] is False
    assert agree["htf"] == {"1w": "UP", "1M": "UP"} and agree["grade"] == "A" and agree["htf_pass"] is True
    assert agree["points"] == against["points"] + 2
    assert any("against the trend on 1w, 1M" == w for w in against["why"])


def test_retest_held_and_failed_break():
    held = base_then([TRUE_BREAK, (103.8, 104.6, 103.6, 104.3, 1200), (104.3, 104.4, 102.2, 103.6, 900), (103.6, 105, 103.4, 104.8, 1000)])
    s = _one(_signals("B1", held, acceptance_closes=5))
    assert s["retest"] == "held" and s["status"] == "accepted"
    failed = base_then([TRUE_BREAK, (103.8, 104.0, 101.0, 101.3, 2000), (101.3, 101.8, 100.5, 100.8, 1000), (100.8, 101.5, 100.4, 101.0, 1000)])
    s = _one(_signals("B1", failed))
    assert s["status"] == "failed" and s["decided_bar"] == len(failed) - 3
    # failure after the window is not a failed break
    late = base_then([TRUE_BREAK] + [(103.8, 105.0, 103.5, 104.5, 1000)] * 3 + [(104.5, 104.6, 100.0, 100.5, 1000)])
    assert _one(_signals("B1", late))["status"] == "accepted"
    assert _one(_signals("B1", base_then([TRUE_BREAK])))["status"] == "pending"


def test_room_to_the_next_opposing_level():
    f = frame(box(120, 94, 98) + zigzag([95, 110, 96, 103, 97, 102], 30) + box(40, 97, 102) + [(101.5, 108.0, 101.3, 105.0, 2500.0)])
    s = _one(_signals("B1", f))                            # breaks the 102 box; the 110 swing high sits less than 2R above
    assert s["checks"]["next_level"] is not None and s["checks"]["next_level"] > s["close"]
    assert s["checks"]["room_ok"] is False and any("next opposing level close" in w for w in s["why"])
    assert _one(_signals("B1", f, room_min_r=0.0))["checks"]["room_ok"] is True


def test_liquidity_floor_excludes_thin_names():
    f = base_then([TRUE_BREAK])
    run = run_breakout("B1", {"1d": f}, "1d", params={"liq_min_value": 1e9}, within=None)
    assert run.signals == [] and run.excluded == 1


def test_an_index_without_volume_screens_with_the_volume_condition_off():
    f = base_then([TRUE_BREAK]).assign(volume=0.0)                    # indices carry no volume
    assert _signals("B1", f) == []                                      # the volume condition cannot pass
    s = [r for r in _signals("B1", f, vol_mult=0.0) if r["bar"] == len(f) - 1][0]
    assert s["rvol"] is None and "no volume data (volume checks skipped)" in s["why"] and s["checks"]["base_dry_up"] is False


def test_atr_percentile_and_regime():
    f = frame(zigzag(UPTREND * 3, 5) + box(60, 100, 101))          # volatility contracts into the box
    b = Bars(f, bp.settings())
    series = atr_percentile_series(b.atr_prev, b.c, 100)
    assert series[-1] == atr_percentile(b, len(f) - 1, 100) and series[-1] <= 100 / 3
    assert np.isnan(series[50])


# ---------------------------------------------------------------------------------------------- dedupe and determinism
def test_one_signal_per_leg():
    run = [TRUE_BREAK] + [(103.8 + 1.5 * i, 105.5 + 1.5 * i, 103.5 + 1.5 * i, 105.3 + 1.5 * i, 3000) for i in range(5)]
    top = 105.3 + 1.5 * 4
    pull = [(a, b, c, d, 3000.0) for a, b, c, d, _ in zigzag([top, top - 9, top + 4], 4)[1:]]
    f = base_then(run + pull)
    e = evaluate_screen(f, "B1", bp.settings(vol_mult=0.0))["up"]
    raw, sig = list(np.flatnonzero(e["raw"].to_numpy())), list(np.flatnonzero(e["signal"].to_numpy()))
    brk = len(f) - len(run) - len(pull)
    assert {brk, brk + 1, brk + 2, brk + 5} <= set(raw)
    assert [s for s in sig if s >= brk] == [brk, len(f) - 1]           # once in the run, again after the confirmed pullback
    # without the pullback a sideways stretch as long as leg_rearm_bars (a new base) re-arms the side
    flat = base_then(run + box(20, top - 3, top + 1.5) + [(top, top + 5, top - 0.5, top + 4.5, 3000.0)])
    e = evaluate_screen(flat, "B1", bp.settings(vol_mult=0.0))["up"]
    assert [s for s in np.flatnonzero(e["signal"].to_numpy()) if s >= brk] == [brk, len(flat) - 1]
    e = evaluate_screen(flat, "B1", bp.settings(vol_mult=0.0, leg_rearm_bars=22))["up"]
    assert [s for s in np.flatnonzero(e["signal"].to_numpy()) if s >= brk] == [brk]


def test_grade_is_deterministic_and_fixed_at_the_signal_bar():
    frames = _with_context(HTF_UP)
    a = run_breakout("B1", frames, "1d", params={"context_ladder": TWO_CONTEXTS}, within=None).signals
    b = run_breakout("B1", frames, "1d", params={"context_ladder": TWO_CONTEXTS}, within=None).signals
    assert a == b
    s = a[-1]
    cut = {k: (v.iloc[:s["bar"] + 1] if k == "1d" else v) for k, v in frames.items()}
    c = run_breakout("B1", cut, "1d", params={"context_ladder": TWO_CONTEXTS}, within=None).signals[-1]
    for key in ("grade", "points", "why", "checks", "htf", "level", "rvol", "dist_atr"):
        assert c[key] == s[key], key
    assert c["status"] == "pending"


# ---------------------------------------------------------------------------------------- closed candles, no look-ahead
@pytest.fixture(scope="module")
def m15():
    return resample(generate(375 * 30, 100.0, 21), "1m", "15m")


@pytest.mark.parametrize("screen", ["B1", "B2", "B4", "B13", "B14"])
def test_no_value_changes_when_later_bars_arrive(m15, screen):
    p = bp.settings(swing_degree=0)
    full = evaluate_screen(m15, screen, p)
    for cut in (400, 550, 700):
        part = evaluate_screen(m15.iloc[:cut], screen, p)
        for side in ("up", "down"):
            for col in ("raw", "signal", "level", "fail_level", "wick"):
                pd.testing.assert_series_equal(part[side][col], full[side][col].iloc[:cut], check_names=False)


def test_context_trend_is_read_from_closed_higher_timeframe_bars_only(m15):
    p = bp.settings()
    frames = style.frames_for({"15m": m15}, "15m", ("1h", "1d"))
    trends = context_trends(frames, "15m", ("1h", "1d"), p)
    daily = frames["1d"]
    dow = pd.Series(Bars(daily, p).dow(0), index=daily.index)
    day_of = m15.index.normalize()
    for i in range(0, len(m15), 7):
        prev_days = dow[dow.index < day_of[i]]
        want = prev_days.iloc[-1] if len(prev_days) else None
        assert trends["1d"].iloc[i] == want                     # never the trend of the day still trading
    # and appending bars never changes what an earlier bar saw
    cut = 500
    part = context_trends(style.frames_for({"15m": m15.iloc[:cut]}, "15m", ("1h", "1d")), "15m", ("1h", "1d"), p)
    for tf in ("1h", "1d"):
        assert list(part[tf]) == list(trends[tf].iloc[:cut])


# ------------------------------------------------------------------------------------------- ScreenQL text <-> engine
def _engine_series(screen, f, tf, params):
    p = bp.settings(**params)
    ctx = style.context_timeframes(tf, p["context_ladder"])
    frames = style.frames_for({tf: f}, tf, ctx)
    trends = context_trends(frames, tf, ctx, p)
    res = evaluate_screen(f, screen, p)
    sides = ("up", "down") if p["direction"] == "both" else (p["direction"],)
    out = np.zeros(len(f), dtype=bool)
    for side in sides:
        ok = np.array([htf_pass([trends[c].iloc[j] for c in ctx], side, p["htf_filter"]) for j in range(len(f))])
        out |= res[side]["signal"].to_numpy() & ok
    return out


@pytest.mark.parametrize("screen,tf,params", [
    ("B1", "15m", {}), ("B1", "15m", {"htf_filter": "off", "range_n": 30}), ("B2", "15m", {"swing_degree": 0}),
    ("B2", "5m", {"swing_degree": 0, "htf_filter": "agree"}), ("B1", "5m", {"htf_filter": "agree"}),
    ("B4", "15m", {}), ("B4", "15m", {"nr_setup": "inside", "htf_filter": "off"}), ("B13", "15m", {"htf_filter": "off", "vol_mult": 1.0}),
    ("B14", "5m", {"htf_filter": "off"}), ("B14", "15m", {"direction": "down"}),
])
def test_template_text_matches_the_engine_bar_by_bar(screen, tf, params):
    df = generate(375 * 60, 100.0, 21)                         # 60 sessions: the daily trend is read after 40
    f = resample(df, "1m", tf)
    text, why = templates.screenql(screen, tf, params)
    assert why is None and text is not None
    ast, validated = compile_screen(text, base_tf=tf)
    assert validated.ok, validated.problems
    got = evaluate(ast, SymbolData("S", {tf: f}), base_tf=tf).fillna(False).astype(bool).to_numpy()
    want = _engine_series(screen, f, tf, params)
    assert (got == want).all()
    assert want.sum() > 0                                       # every sweep has matches (not vacuous)
    # the last-bar screen run says the same
    cut = int(np.flatnonzero(want)[-1]) + 1 if want.any() else len(f)
    match = run_screen(ast, validated, [SymbolData("S", {tf: f.iloc[:cut]})], base_tf=tf, check_history=False)[0]
    assert match.matched == bool(want[cut - 1])


def test_template_text_round_trips_and_the_builder_tree_is_the_same_screen():
    for t in templates.TEMPLATES:
        text, why = templates.screenql(t.screen, t.signal_tf, templates.template_params(t))
        assert text is not None, (t.id, why)
        ast, validated = compile_screen(text, base_tf=t.signal_tf)
        assert validated.ok, (t.id, validated.problems)
        from app.screener import nodes
        assert nodes.to_text(parse(nodes.to_text(ast))) == text
        tree_ast, tree_ok = compile_screen(nodes.to_json(templates.screenql_ast(t.screen, t.signal_tf, templates.template_params(t))[0]),
                                           base_tf=t.signal_tf)
        assert tree_ok.ok and nodes.to_text(tree_ast) == text


def test_engine_only_templates_say_why():
    text, why = templates.screenql("B1", "30m")                     # 30m -> 2h + 1d context: no @2h in screenql/1
    assert text is None and "@2h" in why
    text, why = templates.screenql("B1", "1h", {"atr_n": 20})
    assert text is None and "atr_n" in why
    assert templates.screenql("B1", "1h", {"range_n": 30})[0].startswith('RangeBreakout("up", 30, ')


def test_catalog_lists_blocks_templates_and_every_parameter():
    cat = templates.catalog()
    assert {b["screen"] for b in cat["blocks"]["breakout"]} == set(SCREENS)
    assert {f["name"] for f in cat["blocks"]["quality"]["fields"]} >= {"htf_filter", "acceptance_closes", "failed_break_bars", "room_min_r",
                                                                        "liq_min_value", "atr_pct_lookback", "dryup_n"}
    assert {p["name"] for p in cat["params"]} == set(bp.DEFAULTS)
    ids = {t["id"]: t for t in cat["templates"]}
    assert ids["swing_1h_range"]["context_tfs"] == ["1d", "1w"] and ids["weekly_52w_high"]["screenql"].startswith("NewHighBreakout")
    assert "base_len" in cat["blocks"]["result_columns"] and "grade" in cat["blocks"]["result_columns"]


def test_new_functions_validate_with_choices_and_lookback():
    ok = lambda text: compile_screen(text, base_tf="15m")[1]  # noqa: E731
    assert ok('RangeBreakout("up")').ok and ok('NRBreakout("down", "nr4")').ok and ok('DowTrend(0)@1d == "UP"').ok
    assert not ok('RangeBreakout("long")').ok and not ok('NRBreakout("up", "nr9")').ok
    assert not ok('DowTrend() == "BULL"').ok and not ok("RelVolume() > RSI(14)").ok
    assert ok('SwingBreakType("up") == "BOS"').ok and ok("ATRPercentile() < 33").ok
    assert compile_screen('SwingBreakout("up", 2)', base_tf="15m")[1].lookback["15m"] >= 600
    assert compile_screen('RangeBreakout("up", 40)', base_tf="15m")[1].lookback["15m"] >= 50
    assert re.search(r"RangeBreakout", " ".join(BREAKOUT_FUNCTIONS))


def test_screenql_factors_match_the_engine(m15):
    sd = SymbolData("S", {"15m": m15})
    b = Bars(m15, bp.settings())
    rv = evaluate(parse("RelVolume(20)"), sd, base_tf="15m").to_numpy(float)
    assert np.allclose(rv, b.rvol, equal_nan=True)
    ap = evaluate(parse("ATRPercentile(14, 100)"), sd, base_tf="15m").to_numpy(float)
    assert np.allclose(ap, atr_percentile_series(b.atr_prev, b.c, 100), equal_nan=True)
    assert list(evaluate(parse("DowTrend(0)"), sd, base_tf="15m")) == list(b.dow(0))


# ------------------------------------------------------------------------------------------------- review fixes (H1, M4-M6, L)
def _alert_shaped(t, seed):
    """The frames a saved screen / alert evaluates (`routes.fetch_frames`): the signal timeframe's fetch sized by the
    validator's FETCH lookback (`fetch_days`, intraday capped at 60 days), plus the broker's daily history (~272
    sessions) as the "1d" frame when the screen reads a daily-or-coarser timeframe. A daily-or-coarser signal is the
    daily fetch itself. The daily series ends on a 15-session advance on volume so a 52-week break can occur."""
    from app.screener.routes import fetch_days
    text, _ = templates.screenql(t.screen, t.signal_tf, templates.template_params(t))
    _, validated = compile_screen(text, base_tf=t.signal_tf)
    d = generate(272, 100.0, seed, daily=True).copy()
    ramp = np.linspace(1.0, 1.25, 15)
    for c in ("open", "high", "low", "close"):
        d.iloc[-15:, d.columns.get_loc(c)] *= ramp
    d.iloc[-15:, d.columns.get_loc("volume")] *= 3
    if t.signal_tf in ("1d", "1w"):
        return {t.signal_tf: d if t.signal_tf == "1d" else resample(d, "1d", "1w")}, validated
    days = fetch_days(validated.fetch_lookback, t.signal_tf)
    sessions = int(np.busday_count(np.datetime64("2026-01-01"), np.datetime64("2026-01-01") + np.timedelta64(days, "D")))
    m1 = generate(375 * sessions, 100.0, seed)
    frames = {t.signal_tf: resample(m1, "1m", t.signal_tf)}
    if any(bp.TF_MINUTES[tf] >= bp.TF_MINUTES["1d"] for tf in validated.fetch_lookback):
        frames["1d"] = generate(272, 100.0, seed + 100, daily=True, end=m1.index[0].to_pydatetime())   # completed sessions before
    return frames, validated


@pytest.mark.parametrize("tid", [t.id for t in templates.TEMPLATES])
def test_every_template_can_match_on_the_alert_fetch_window(tid):
    t = templates.BY_ID[tid]
    text, _ = templates.screenql(t.screen, t.signal_tf, templates.template_params(t))
    ast, _ = compile_screen(text, base_tf=t.signal_tf)
    matched = 0
    for seed in (1, 2, 3):
        frames, validated = _alert_shaped(t, seed)
        data = SymbolData("S", frames)
        m = run_screen(ast, validated, [data], base_tf=t.signal_tf, check_history=True)[0]
        assert m.reason is None, (tid, m.reason)                        # never "not enough history"
        matched += int(evaluate(ast, data, base_tf=t.signal_tf).fillna(False).astype(bool).sum())
    if t.screen in INTRADAY_SCREENS:
        return    # U6 BK2: these need a break on time-of-day volume; tests/test_z7_bk2_intraday.py injects one and proves the match
    if t.screen in ("B3", "B15") or t.id in ("daily_flag", "daily_range_retest"):
        return    # U6 BK3 / BK4: a VCP base, an RSI-ADX compression, a flag pole or a reversal retest is not in a random walk;
        #           tests/test_z7_bk3_breakouts.py / test_z7_bk4_breakouts.py inject one into the same daily window and prove the match
    assert matched > 0, tid


def test_dow_trend_fetch_is_sized_but_never_refuses():
    _, v = compile_screen('DowTrend(0)@1h != "DOWN"', base_tf="15m")
    assert v.lookback["1h"] == 1 and v.fetch_lookback["1h"] == 100
    _, v = compile_screen('DowTrend(1)@1d == "UP"', base_tf="15m")
    assert v.lookback["1d"] == 1 and v.fetch_lookback["1d"] == 250


def test_unknown_context_blocks_not_against():
    assert not htf_pass([None, "UP"], "up", "not_against") and not htf_pass(["DOWN", "UP"], "up", "not_against")
    assert htf_pass(["RANGE", "UP"], "up", "not_against") and not htf_pass([None, "UP"], "up", "agree")
    d = generate(272, 100.0, 1, daily=True)                             # a daily signal: 57 weeks, the weekly trend is read
    run = run_breakout("B2", {"1d": d}, "1d", params=templates.template_params(templates.BY_ID["daily_swing_break"], {"swing_degree": 0}),
                       within=None)
    for s in run.signals:
        assert s["htf_pass"] == (s["htf"]["1w"] is not None and s["htf"]["1w"] != ("DOWN" if s["side"] == "up" else "UP"))


def test_alert_text_never_fires_a_break_that_run_hides_as_counter_trend():
    """Reviewer repro (BK1 verification A): counter-trend NR7 breaks hidden by /run must not match the template text on
    the frames an alert evaluates (intraday fetch window + the daily history before that day)."""
    from app.screener.routes import fetch_days
    t = templates.BY_ID["intraday_15m_nr7"]
    params = templates.template_params(t, {"vol_mult": 0.0})
    text, _ = templates.screenql(t.screen, t.signal_tf, params)
    ast, v = compile_screen(text, base_tf="15m")
    days = fetch_days(v.fetch_lookback, "15m")
    hidden = matched = 0
    for seed in (1, 2, 3):
        m15 = resample(generate(375 * 70, 100.0, seed), "1m", "15m")
        daily = resample(m15, "15m", "1d")
        for s in run_breakout("B4", {"15m": m15, "1d": daily}, "15m", params=params, within=None).signals:
            if s["htf_pass"] or s["bar"] < 25 * 45:
                continue
            hidden += 1
            end = m15.index[s["bar"]]
            win = m15[(m15.index > end - pd.Timedelta(days=days)) & (m15.index <= end)]
            frames = {"15m": win, "1d": daily[style.bar_close(daily.index, "1d") <= end]}
            matched += run_screen(ast, v, [SymbolData("S", frames)], base_tf="15m")[0].matched
    assert hidden > 0 and matched == 0


def test_a_month_is_never_visible_before_its_last_session():
    d = generate(400, 100.0, 3, daily=True)
    for idx in (d.index, d.index.tz_convert("Asia/Kolkata").normalize().tz_convert("UTC"), d.index.tz_convert("Asia/Kolkata").normalize()):
        base = pd.DataFrame(d.to_numpy(), columns=d.columns, index=idx)
        months = resample(base, "1d", "1M")
        seen = evaluate(parse("close@1M"), SymbolData("S", {"1d": base}), base_tf="1d")
        for i, (label, close) in enumerate(zip(months.index[:-1], months["close"].iloc[:-1])):
            nxt = months.index[i + 1]
            members = base[(base.index >= label) & (base.index < nxt)]
            assert (seen.loc[members.index[:-1]] != close).all()          # never during the month before its last session
            assert seen.loc[base.index[base.index >= nxt][0]] == close   # and read by the next month's first session


def test_dow_trend_waits_for_its_warm_up():
    from app.screener.breakouts.screens import DOW_MIN_BARS
    f = frame(zigzag(UPTREND * 2, 5))
    dow = Bars(f, bp.settings()).dow(0)
    assert all(x is None for x in dow[:DOW_MIN_BARS[0]]) and dow[-1] == "UP"


def test_dry_up_window_never_wraps_and_must_fit_the_average():
    _, errors = bp.validate({"dryup_n": 30, "vol_avg_n": 20})
    assert errors and "dryup_n" in errors[0]
    with pytest.raises(ValueError):
        run_breakout("B13", {"1d": frame(zigzag(UPTREND, 5))}, "1d", params={"dryup_n": 30, "vol_avg_n": 20})
    # an early signal (bar 26 < vol_avg_n + dryup_n) grades the same whatever comes after it
    warm = zigzag([60, 66, 63, 70], 8) + [(70.5, 78.0, 70.3, 77.8, 2500.0)]
    f = frame(warm + [(77.8, 79, 77, 78.5, 1000.0)] * 5)
    a = [s for s in _signals("B13", f, vol_mult=1.0) if s["bar"] == len(warm) - 1]
    b = [s for s in _signals("B13", f.iloc[:len(warm)], vol_mult=1.0) if s["bar"] == len(warm) - 1]
    assert a and b and (a[0]["grade"], a[0]["checks"]) == (b[0]["grade"], b[0]["checks"])


def test_session_anchoring_on_ist_aware_bars():
    df = generate(375 * 3, 100.0, 5)
    ist = df.tz_convert("Asia/Kolkata")
    for tf, want in (("1h", ["09:15", "10:15", "11:15"]), ("2h", ["09:15", "11:15", "13:15", "15:15"]), ("4h", ["09:15", "13:15"])):
        got = style.resample(ist, "1m", tf)
        assert [t.strftime("%H:%M") for t in got.index[:len(want)]] == want, tf
        assert got.index.equals(style.resample(df, "1m", tf).index.tz_convert("Asia/Kolkata"))      # same bars either way
    assert [t.strftime("%H:%M") for t in resample(ist, "1m", "15m").index[:2]] == ["09:15", "09:30"]


def test_weeks_and_months_are_exchange_calendar_buckets():
    d = generate(60, 100.0, 4, daily=True)
    d.index = d.index.tz_convert("Asia/Kolkata").normalize().tz_convert("UTC")      # 00:00 IST = 18:30 UTC the day before
    w = resample(d, "1d", "1w")
    for label in w.index:
        start = label.tz_convert("Asia/Kolkata")
        assert start.weekday() == 0 and start.hour == 0                                 # Monday 00:00 IST
        members = d[(d.index >= label) & (d.index < label + pd.Timedelta(days=7))]
        assert {t.tz_convert("Asia/Kolkata").isocalendar()[1] for t in members.index} == {start.isocalendar()[1]}
    # a weekly value reaches the daily bars only after the week's last session closed
    trend = pd.Series(range(len(w)), index=w.index, dtype=float)
    seen = style.align_closed(trend, "1w", d.index, "1d")
    for i, t in enumerate(d.index):
        done = w.index[w.index + pd.Timedelta(days=7) <= t + pd.Timedelta(days=1)]
        want = trend[done[-1]] if len(done) else np.nan
        assert (np.isnan(want) and np.isnan(seen.iloc[i])) or seen.iloc[i] == want
    m = resample(d, "1d", "1M")
    assert all(t.tz_convert("Asia/Kolkata").day == 1 for t in m.index)


def test_screenql_arguments_are_range_checked_and_new_highs_are_daily_or_weekly():
    ok = lambda text, tf="1d": compile_screen(text, base_tf=tf)[1]  # noqa: E731
    assert "k must be between 0 and 5" in ok('RangeBreakout("up", 20, -1)').problems[0].message
    assert "n must be between 2 and 500" in ok('RangeBreakout("up", 1)').problems[0].message
    assert "v must be between 0.5 and 50" in ok('VolumeBreakout("up", 0.1)').problems[0].message
    assert "@1d / @1w bars only" in ok('NewHighBreakout("up")', "15m").problems[0].message
    assert ok('NewHighBreakout("up")').lookback["1d"] == 260 and ok('NewHighBreakout("up")', "1w").lookback["1w"] == 52
    assert ok("ATRPercentile(14, 100) < 30").lookback["1d"] == 114
    v = compile_screen('RangeBreakout("up", 20, $k)', base_tf="1d", params={"k": 9})[1]
    assert not v.ok and "k must be between" in v.problems[0].message
