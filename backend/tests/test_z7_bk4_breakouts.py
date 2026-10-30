"""Z7 / U6 BK4: B5 trendline / channel, B6 flag / pennant / triangle, B8 gap and B9 retest breakouts
(docs/specs/ATP_BREAKOUT_SCREENERS_ADDENDUM.md §8 BK4, §7 tests).

Synthetic fixtures per screen (tests/breakout_bk4_fixtures.py, no randomness) with their failing variants; the trendline
choice against the CH3b trendline port on every prefix; the closed-candle / no-look-ahead sweep; one signal per leg (B5,
B6) or per event (B8 per session, B9 per breakout); grade determinism; and parity between each template's ScreenQL text
and the engine, bar by bar."""
import numpy as np
import pandas as pd
import pytest

from app.screener import compile_screen, parse
from app.screener.breakouts import lines as L
from app.screener.breakouts import params as bp
from app.screener.breakouts import templates
from app.screener.breakouts.quality import run_breakout
from app.screener.breakouts.screens import BK4_SCREENS, SCREEN_TIMEFRAMES, Bars, evaluate_screen
from app.screener.registry import BREAKOUT_BK4_FLAG, BREAKOUT_BK4_FUNCTIONS, FUNCTIONS
from app.screener.runtime import SymbolData, evaluate, resample, run_screen
from tests.breakout_bk4_fixtures import closes_frame, daily_gap, falling_line, flag, lifted, retest_rows, rising_channel, rows_frame, trend_lead, triangle
from tests.breakout_fixtures import quiet_day, sessions_frame
from tests.sample_market import generate
from tests.test_z7_bk1_breakouts import _engine_series


def _sig(f, screen, side="up", tf=None, **params):
    return list(np.flatnonzero(evaluate_screen(f, screen, bp.settings(**params), tf=tf)[side]["signal"].to_numpy()))


def _last(f, screen, side="up", tf=None, **params):
    return evaluate_screen(f, screen, bp.settings(**params), tf=tf)[side].iloc[-1]


# ----------------------------------------------------------------------------------------------------------- registry
def test_the_bk4_functions_take_the_parameter_defaults_and_need_both_flags():
    want = {"TrendlineBreakout": {"mode": "tl_mode", "touches": "tl_min_touches", "k": "min_break_atr"},
            "PatternBreakout": {"pattern": "pattern", "v": "vol_mult", "k": "min_break_atr"},
            "GapBreakout": {"level": "gap_level", "hold": "gap_hold_min"}, "GapType": {"level": "gap_level", "hold": "gap_hold_min"},
            "RetestEntry": {"of": "retest_of", "bars": "retest_max_bars", "reversal": "retest_reversal"},
            # strategy templates step 2 (R2): the trendline / channel breakout's level companion
            "LineBreakoutRef": {"mode": "tl_mode", "touches": "tl_min_touches", "k": "min_break_atr", "stop_mult": "stop_atr_mult"},
            # strategy templates step 3 (R7): the retest's level companion
            "RetestEntryRef": {"of": "retest_of", "bars": "retest_max_bars", "reversal": "retest_reversal", "stop_mult": "stop_atr_mult"},
            # strategy templates step 4 (R2): the pattern breakout's level companion
            "PatternBreakoutRef": {"pattern": "pattern", "v": "vol_mult", "k": "min_break_atr", "stop_mult": "stop_atr_mult"},
            # strategy templates TPL-8 (R2 for the gap family): the gap breakout's level companion
            "GapBreakoutRef": {"level": "gap_level", "hold": "gap_hold_min", "stop_mult": "stop_atr_mult"},
            # idea library IDEA-12: the channel's trend-side line on every bar
            "ChannelLine": {"touches": "tl_min_touches"}}
    # R7: the named arguments RetestEntry / RetestEntryRef forward to the base breakout take its parameters' defaults too
    forward = {"k": "min_break_atr", "n": "range_n", "v": "vol_mult", "mode": "range_mode", "touches": "tl_min_touches",
               "swing": "swing_degree"}                                        # step 5 (C2): the swing breakout's degree
    for fn in ("RetestEntry", "RetestEntryRef"):
        named = {a.name: a for a in FUNCTIONS[fn].kwargs}
        assert set(named) == set(forward)
        assert all(named[arg].default == bp.DEFAULTS[param] for arg, param in forward.items()), fn
    assert set(want) == set(BREAKOUT_BK4_FUNCTIONS)
    for fn, args in want.items():
        spec = FUNCTIONS[fn]
        assert spec.flag == "screener_breakouts" and spec.also_flags == (BREAKOUT_BK4_FLAG,)
        by = {a.name: a for a in spec.args}
        for arg, param in args.items():
            assert by[arg].default == bp.DEFAULTS[param], (fn, arg)
    assert set(BK4_SCREENS) == {"B5", "B6", "B8", "B9"} == set(templates.BK4_TEMPLATE_SCREENS)
    assert SCREEN_TIMEFRAMES["B8"] == FUNCTIONS["GapBreakout"].timeframes


def test_new_functions_validate_with_choices_ranges_and_history():
    ok = lambda text, tf="1d": compile_screen(text, base_tf=tf)[1]  # noqa: E731
    assert ok('TrendlineBreakout("up", "channel", 3)').ok and ok('PatternBreakout("down", "pennant")', "15m").ok
    assert not ok('TrendlineBreakout("up", "wedge")').ok and not ok('PatternBreakout("up", "cup")').ok
    assert ok('GapBreakout("up", "base", 30)', "5m").ok and ok('GapType("down") == "GAP_FILL"', "15m").ok
    assert not ok('GapBreakout("up")', "1h").ok                                       # intraday sessions or Daily only
    assert not ok('GapBreakout("up", "prev_day", 20)', "15m").ok                     # not a whole number of 15m bars
    assert not ok('GapType("up") == "GAP_UP"').ok
    assert ok('RetestEntry("down", "trendline", 15, FALSE)').ok and not ok('RetestEntry("up", "vwap")').ok
    assert compile_screen('TrendlineBreakout("up")', base_tf="1d")[1].lookback["1d"] >= 125
    assert compile_screen('RetestEntry("up")', base_tf="1d")[1].lookback["1d"] >= 250
    assert compile_screen('GapBreakout("up")', base_tf="5m")[1].lookback["5m"] >= 150                # two whole sessions


def test_bad_bk4_parameters_fall_back():
    clean, errors = bp.validate({"flag_min_bars": 30, "flag_max_bars": 10, "tl_mode": "fan", "retest_of": "vwap"})
    assert clean["flag_min_bars"] == 5 and clean["tl_mode"] == "line" and clean["retest_of"] == "range" and len(errors) == 3


# ------------------------------------------------------------------------------------------------------------------ B5
def test_trendline_break_and_its_failing_variants():
    f = closes_frame(falling_line())
    assert len(f) - 1 in _sig(f, "B5")
    row = _last(f, "B5")
    assert row["event"] == "TRENDLINE" and row["level"] < f["close"].iloc[-1] and row["base_len"] > 20
    assert len(f) - 1 not in _sig(closes_frame(falling_line(break_by=0.5)), "B5")        # still under the line
    assert len(f) - 1 not in _sig(f, "B5", tl_min_touches=10)                         # not that many swings on any line
    assert len(f) - 1 not in _sig(f, "B5", tl_max_slope=0.1)                          # the angle filter
    assert _last(f, "B5", tl_max_bars=20)["base_len"] <= 20 < row["base_len"]          # the age filter: a younger line
    m = f.copy()
    m["high"], m["low"], m["open"], m["close"] = 300 - f["low"], 300 - f["high"], 300 - f["open"], 300 - f["close"]
    assert len(m) - 1 in _sig(m, "B5", "down") and _last(m, "B5", "down")["level"] == pytest.approx(300 - row["level"])


def test_channel_break_reports_the_measured_move():
    f = closes_frame(rising_channel())
    assert len(f) - 1 in _sig(f, "B5", tl_mode="channel") and len(f) - 1 not in _sig(f, "B5")
    row = _last(f, "B5", tl_mode="channel")
    assert row["event"] == "CHANNEL" and row["measured_move"] > row["level"] > row["base_low"]
    assert row["measured_move"] - row["level"] == pytest.approx(row["level"] - row["base_low"])     # the channel's width


@pytest.mark.parametrize("seed,t0", [(7, 60), (11, 90)])
def test_the_line_is_the_trendline_ports_choice_on_every_prefix(seed, t0):
    from app.price_action.trendlines import find_trendline, ref_range
    d = generate(320, 100.0, seed, daily=True).reset_index(drop=True)
    p = bp.settings(tl_max_bars=500)
    b = Bars(d, p)
    rr = L.ref_ranges(b.h, b.l)
    for kind, sign in (("resistance", -1), ("support", 1)):
        lines = L.Lines(b, b.side(1), p, kind, rr)
        found = 0
        for t in range(t0, 320, 5):
            pre = d.iloc[:t]
            want = find_trendline(pre, ref_range(pre), kind, sign)
            got = lines.select(t)
            assert (None if want is None else (want.a, want.b_idx)) == (None if got is None else (got.a, got.b)), (kind, t)
            found += got is not None
        assert found > 5


# ------------------------------------------------------------------------------------------------------------------ B6
def test_flag_pennant_and_triangle_breaks_with_the_measured_move():
    f = closes_frame(flag())
    row = _last(f, "B6")
    assert len(f) - 1 in _sig(f, "B6") and row["event"] == "FLAG"
    assert row["measured_move"] - row["level"] == pytest.approx(f["high"].iloc[30:36].max() - f["low"].iloc[30:31].min(), abs=0.6)
    assert len(f) - 1 not in _sig(closes_frame(flag(), last_volume=900.0), "B6")          # no volume on the break
    assert len(f) - 1 not in _sig(closes_frame(flag(retrace=9.0)), "B6")                  # gave back most of the pole
    assert len(f) - 1 not in _sig(f, "B6", pole_min_atr=15.0)                            # the pole is not big enough
    p = closes_frame(flag(pennant=True))
    assert len(p) - 1 in _sig(p, "B6", pattern="pennant") and _last(p, "B6", pattern="pennant")["event"] == "PENNANT"
    assert len(f) - 1 not in _sig(f, "B6", pattern="pennant")                            # a drifting flag does not contract
    t = closes_frame(triangle())
    row = _last(t, "B6", pattern="triangle")
    assert len(t) - 1 in _sig(t, "B6", pattern="triangle") and row["event"] == "TRIANGLE"
    assert row["measured_move"] > row["level"] > row["base_low"]


# ------------------------------------------------------------------------------------------------------------------ B8
def test_daily_gap_classes():
    go, hold, fill = daily_gap(101.5, 102.0), daily_gap(101.5, 100.9), daily_gap(101.5, 100.2)
    assert _sig(go, "B8") == [20] and _last(go, "B8")["event"] == "GAP_AND_GO" and _last(go, "B8")["level"] == 100.5
    assert _sig(hold, "B8") == [20] and _last(hold, "B8")["event"] == "GAP_HOLD"
    assert _sig(fill, "B8") == [] and _last(fill, "B8")["event"] == "GAP_FILL"
    assert _sig(daily_gap(100.4, 102.0), "B8") == []                                    # opened inside: no gap
    assert _sig(go, "B8", gap_min_atr=3.0) == []                                       # gap smaller than 3 daily ATRs
    data = SymbolData("S", {"1d": fill})
    assert list(evaluate(parse('GapType("up")'), data, base_tf="1d"))[-1] == "GAP_FILL"
    assert not bool(evaluate(parse('GapBreakout("up")'), data, base_tf="1d").iloc[-1])


def _gap_days(last):
    return sessions_frame([quiet_day() for _ in range(3)] + [last])


def test_intraday_gap_holds_through_the_first_minutes():
    go = _gap_days(quiet_day(mid=101.0))
    fade = _gap_days([(101.5, 101.6, 101.0, 101.2, 3000.0)] + [(101.2 - 0.08 * i, 101.3 - 0.08 * i, 100.9 - 0.08 * i, 101.1 - 0.08 * i, 1000.0)
                                                                 for i in range(74)])
    fill = _gap_days([(100.6, 100.7, 100.1, 100.2, 3000.0)] + quiet_day(74, 100.3))
    end = 3 * 75 + 5                                                     # the bar that ends 09:15-09:45 on the gap day
    assert _sig(go, "B8", tf="5m") == [end] and _last_event(go, end) == "GAP_AND_GO"
    assert _sig(fade, "B8", tf="5m") == [end] and _last_event(fade, end) == "GAP_HOLD"
    assert _sig(fill, "B8", tf="5m") == [] and _last_event(fill, end) == "GAP_FILL"
    assert _sig(go, "B8", tf="5m", gap_hold_min=60) == [end + 6]
    with pytest.raises(ValueError):
        evaluate_screen(go, "B8", bp.settings(gap_hold_min=32), tf="5m")
    data = SymbolData("S", {"5m": fade})
    assert evaluate(parse('GapType("up")'), data, base_tf="5m").iloc[end] == "GAP_HOLD"
    assert list(np.flatnonzero(evaluate(parse('GapBreakout("up")'), data, base_tf="5m").to_numpy(bool))) == [end]


def _last_event(f, j):
    return evaluate_screen(f, "B8", bp.settings(), tf="5m")["up"]["event"].iloc[j]


# ------------------------------------------------------------------------------------------------------------------ B9
def test_retest_after_a_range_breakout():
    f = rows_frame(retest_rows())
    assert _sig(f, "B9") == [len(f) - 1] == _sig(f, "B9", retest_reversal=False)
    row = _last(f, "B9")
    assert row["event"] == "RETEST_B1" and row["base_len"] == 6 and row["level"] == pytest.approx(f["high"].iloc[:40].iloc[-20:].max())
    assert _sig(rows_frame(retest_rows(fail=True)), "B9") == []                         # back inside first: the break failed
    weak = rows_frame(retest_rows(hammer=False))
    assert _sig(weak, "B9") == [] and _sig(weak, "B9", retest_reversal=False) == [len(weak) - 1]
    assert _sig(f, "B9", retest_max_bars=4) == []                                       # too late
    run = run_breakout("B9", {"1d": f}, "1d", within=1)
    assert len(run.signals) == 1 and run.signals[0]["level"] == pytest.approx(row["level"])


def test_retest_of_other_breakouts_runs():
    d = generate(400, 100.0, 7, daily=True)
    for base in ("swing", "donchian", "trendline"):
        out = evaluate_screen(d, "B9", bp.settings(retest_of=base, retest_reversal=False, swing_degree=0))
        assert any(out[s]["signal"].any() for s in ("up", "down")), base
        ev = {e for s in ("up", "down") for e in out[s]["event"][out[s]["signal"]]}
        assert ev == {f"RETEST_{ {'swing': 'B2', 'donchian': 'B10', 'trendline': 'B5'}[base] }".replace(" ", "")}


# ---------------------------------------------------------------------------------------- closed candles, no look-ahead
@pytest.fixture(scope="module")
def daily():
    return generate(600, 100.0, 3, daily=True)


@pytest.fixture(scope="module")
def m5():
    return resample(generate(375 * 20, 100.0, 21), "1m", "5m")


@pytest.mark.parametrize("screen,params", [
    ("B5", {}), ("B5", {"tl_mode": "channel"}), ("B6", {"vol_mult": 0.0}), ("B6", {"pattern": "triangle", "vol_mult": 0.0}),
    ("B6", {"pattern": "pennant", "vol_mult": 0.0}), ("B8", {}), ("B8", {"gap_level": "base", "gap_base_n": 3}),
    ("B9", {"retest_reversal": False}), ("B9", {"retest_of": "donchian"}),
])
def test_no_value_changes_when_later_bars_arrive(daily, m5, screen, params):
    p = bp.settings(**params)
    for f, tf, cuts in ((daily, "1d", (300, 420, 510)), (m5, "5m", (600, 900, 1200))):
        full = evaluate_screen(f, screen, p, tf=tf)
        for cut in cuts:
            part = evaluate_screen(f.iloc[:cut], screen, p, tf=tf)
            for side in ("up", "down"):
                for col in ("raw", "signal", "level", "fail_level", "base_high", "base_low", "measured_move", "event"):
                    pd.testing.assert_series_equal(part[side][col], full[side][col].iloc[:cut], check_names=False)


def test_one_signal_per_leg_and_per_event(daily):
    out = evaluate_screen(daily, "B5", bp.settings(min_break_atr=0.0))
    assert any(out[s]["raw"].sum() > out[s]["signal"].sum() for s in ("up", "down"))   # B5 dedupes per leg
    for screen in ("B8", "B9"):                                                       # one per session / per breakout
        out = evaluate_screen(daily, screen, bp.settings(retest_reversal=False), tf="1d")
        assert all((out[s]["raw"] == out[s]["signal"]).all() for s in ("up", "down"))


def test_grade_is_deterministic_and_fixed_at_the_signal_bar():
    f = lifted(flag())
    a = run_breakout("B6", {"1d": f}, "1d", within=None).signals
    assert a and a == run_breakout("B6", {"1d": f}, "1d", within=None).signals
    longer = pd.concat([f, f.iloc[-5:].set_axis(f.index[-1] + pd.offsets.BDay(1) * np.arange(1, 6))])
    c = [s for s in run_breakout("B6", {"1d": longer}, "1d", within=None).signals if s["bar"] == a[-1]["bar"]][0]
    for key in ("grade", "points", "why", "checks", "htf", "level", "measured_move", "event"):
        assert c[key] == a[-1][key], key


# ------------------------------------------------------------------------------------------- ScreenQL text <-> engine
BK4_TEMPLATES = [t for t in templates.TEMPLATES if t.screen in templates.BK4_TEMPLATE_SCREENS]


def _template_frame(t):
    if t.id == "daily_flag":
        return lifted(flag())
    if t.id == "daily_range_retest":
        return rows_frame(retest_rows(), lead=trend_lead())
    if t.id == "swing_1h_range_retest":                                # a reversal retest is rare in a random walk
        return rows_frame(retest_rows())
    if t.id == "intraday_5m_gap_and_go":
        return sessions_frame([quiet_day() for _ in range(20)] + [quiet_day(mid=101.0)] + [quiet_day(mid=100.0 + 0.1 * i) for i in range(4)])
    if t.signal_tf == "1d":
        return generate(600, 100.0, 5, daily=True)
    return resample(generate(375 * 60, 100.0, 21), "1m", t.signal_tf)


@pytest.mark.parametrize("tid", [t.id for t in BK4_TEMPLATES])
def test_template_text_matches_the_engine_bar_by_bar(tid):
    t = templates.BY_ID[tid]
    # the 1H / 5m sweeps are a few weeks long - too short for a weekly trend - so their context filter is off; the generator's
    # daily volume never reaches 1.5x its average, so the daily triangle asks for 1x (both are ScreenQL arguments)
    extra = {"htf_filter": "off"} if t.signal_tf in ("1h", "5m") else {"vol_mult": 1.0} if tid == "daily_triangle" else None
    params = templates.template_params(t, extra)
    f = _template_frame(t)
    text, why = templates.screenql(t.screen, t.signal_tf, params)
    assert why is None and text is not None
    ast, validated = compile_screen(text, base_tf=t.signal_tf)
    assert validated.ok, validated.problems
    got = evaluate(ast, SymbolData("S", {t.signal_tf: f}), base_tf=t.signal_tf).fillna(False).astype(bool).to_numpy()
    p = bp.settings(**params)
    want = _engine_series(t.screen, f, t.signal_tf, params) if tid != "intraday_5m_gap_and_go" else np.any(
        [evaluate_screen(f, "B8", p, tf="5m")[s]["signal"].to_numpy() for s in ("up", "down")], axis=0)
    assert (got == want).all()
    assert want.sum() > 0, tid
    cut = int(np.flatnonzero(want)[-1]) + 1
    match = run_screen(ast, validated, [SymbolData("S", {t.signal_tf: f.iloc[:cut]})], base_tf=t.signal_tf, check_history=False)[0]
    assert match.matched


def test_flag_and_retest_templates_match_on_the_alert_fetch_window():
    """tests/test_z7_bk1_breakouts.py::test_every_template_can_match_on_the_alert_fetch_window leaves these two out (a flag
    pole or a reversal retest is rare in a random walk); each is injected into a daily history no longer than the broker's
    272-session fetch, and the template matches with check_history."""
    for tid, f in (("daily_flag", lifted(flag())), ("daily_range_retest", rows_frame(retest_rows(), lead=trend_lead()))):
        t = templates.BY_ID[tid]
        text, _ = templates.screenql(t.screen, t.signal_tf, templates.template_params(t))
        ast, validated = compile_screen(text, base_tf="1d")
        assert len(f) <= 272 and validated.lookback["1d"] <= len(f), (tid, len(f), validated.lookback)
        m = run_screen(ast, validated, [SymbolData("S", {"1d": f})], base_tf="1d", check_history=True)[0]
        assert m.reason is None and m.matched, (tid, m.reason)


def test_catalog_without_bk4():
    on, off = templates.catalog(True, True), templates.catalog(True, False)
    ids = {t.id for t in BK4_TEMPLATES}
    assert ids <= {t["id"] for t in on["templates"]} and not (ids & {t["id"] for t in off["templates"]})
    assert not (set(BK4_SCREENS) & {b["screen"] for b in off["blocks"]["breakout"]})
    names = {p["name"] for p in off["params"]}
    assert "tl_mode" not in names and "gap_level" not in names and "retest_tol_atr" in names    # the quality overlay keeps its own
    assert "measured_move" in on["blocks"]["result_columns"]


# ------------------------------------------------------------------------------------------- the shared indicator math
def test_the_ref_range_and_swings_are_the_trendline_ports(daily):
    from app.price_action.swings import swing_indices
    from app.price_action.trendlines import ref_range
    h, lo = daily["high"].to_numpy(float), daily["low"].to_numpy(float)
    rr = L.ref_ranges(h, lo)
    for t in (5, 10, 15, 40, 300):
        want = ref_range(daily.iloc[:t])
        assert (np.isnan(want) and np.isnan(rr[t])) or rr[t] == pytest.approx(want)
    holes = daily.copy()
    holes.iloc[[3, 50], holes.columns.get_loc("high")] = np.nan
    rr2 = L.ref_ranges(holes["high"].to_numpy(float), holes["low"].to_numpy(float))
    for t in (12, 60, 90):
        assert rr2[t] == pytest.approx(ref_range(holes.iloc[:t]))
    sh, sl = swing_indices(daily, window=3)
    assert list(L.swing_positions(h, 3, True)) == sh and list(L.swing_positions(lo, 3, False)) == sl


def test_the_retest_reversal_is_reversal_at(daily):
    """retest._reversals evaluates the composite rule on the candidate bars only; it equals ReversalAt's series there."""
    from app.screener.breakouts.retest import _reversals
    from app.screener.runtime import _reversal_series
    b = Bars(daily, bp.settings())
    level = pd.Series(daily["low"].rolling(3).min().shift(1).to_numpy() + 0.05, index=daily.index)
    for sign, direction in ((1, "bullish"), (-1, "bearish")):
        s = b.side(sign)
        lv = level.to_numpy() if sign > 0 else -(daily["high"].rolling(3).max().shift(1).to_numpy() - 0.05)
        real = pd.Series(s.back(lv), index=daily.index)
        want = _reversal_series(daily, real, direction).to_numpy(bool)
        bars = list(range(20, len(daily)))
        got = _reversals(b, s, lv, bars)
        assert (got[20:] == want[20:]).all() and want.sum() > 3


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_fast_zigzag_is_causal_swings_zigzag(seed):
    """screens.zigzag_swings (the leg rule's swings, on Python floats) equals causal_swings.zigzag_pivots, holes and ties
    included."""
    from app.price_action import causal_swings as cs
    from app.price_action import pa_settings
    from app.screener.breakouts.screens import zigzag_swings
    f = resample(generate(375 * 20, 100.0, seed), "1m", "5m").reset_index(drop=True)
    f.iloc[[7, 40], f.columns.get_loc("high")] = np.nan
    f.iloc[90, f.columns.get_loc("low")] = np.nan
    f.iloc[120:124, f.columns.get_loc("high")] = f["high"].iloc[120]                  # a run of equal highs
    s = pa_settings.settings()
    seen = 0
    for degree in range(3):
        thr = cs.threshold(f, degree, s)
        thr[[3, 55]] = np.nan
        want = [(r.kind, r.pivot_bar, r.confirm_bar, r.price) for r in cs.zigzag_pivots(f, thr)]
        got = [(p.kind, p.bar_idx, p.confirmed_idx, p.price) for p in zigzag_swings(f["high"].to_numpy(float), f["low"].to_numpy(float), thr)]
        assert got == want
        seen += len(want)
    assert seen >= 3


def test_the_array_median_range_and_swing_threshold_are_the_platforms(daily):
    from app.price_action import causal_swings as cs
    from app.price_action import pa_settings
    from app.price_action.breaks import median_range
    from app.screener.breakouts.retest import fast_median_range
    f = daily.copy()
    f.iloc[[10, 200], f.columns.get_loc("high")] = np.nan
    frame = f[["open", "high", "low", "close"]].astype(float).reset_index(drop=True)
    np.testing.assert_array_equal(fast_median_range(frame["high"].to_numpy(), frame["low"].to_numpy(), 20), median_range(frame, 20))
    b = Bars(f, bp.settings())
    s = pa_settings.settings()
    for degree in range(4):
        np.testing.assert_array_equal(b.swing_threshold(degree, s), cs.threshold(frame, degree, s))


def _stairs(n=2000):
    """A stair-step series: a swing high and low every few bars, so nearly every anchor pair is a line candidate."""
    i = np.arange(n)
    c = 100 + 0.02 * i + 1.5 * np.sin(i / 2.0)
    idx = pd.bdate_range("2019-01-01", periods=n, tz="UTC")
    return pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1000.0}, index=idx)


@pytest.mark.parametrize("edges", [False, True])
def test_the_line_grid_in_chunks_equals_the_one_grid_and_bounds_memory(edges, monkeypatch):
    """BK4 review (LOW): the anchor pairs x (tl_max_bars + 1) grid is computed in row chunks - the same lines, breaks and
    channel edges as one grid, and the peak memory bounded by the chunk (it reached 8 GB on 2000 stair-step bars)."""
    import tracemalloc
    p = bp.settings(tl_max_bars=500)
    for frame in (generate(320, 100.0, 7, daily=True).reset_index(drop=True), _stairs(600)):
        b = Bars(frame, p)
        rr = L.ref_ranges(b.h, b.l)
        pairs = 0
        for kind in ("resistance", "support"):
            monkeypatch.setattr(L, "PAIR_CELLS", 10 ** 12)
            whole = L.Lines(b, b.side(1), p, kind, rr, edges)
            monkeypatch.setattr(L, "PAIR_CELLS", 777)
            chunked = L.Lines(b, b.side(1), p, kind, rr, edges)
            for name in ("A", "B", "M", "C0", "BRK", "EDGE", "EDGE_BRK"):
                np.testing.assert_array_equal(getattr(whole, name), getattr(chunked, name), err_msg=f"{kind} {name}")
            pairs += len(whole.A)
        assert pairs > 10
    b = Bars(_stairs(2000), p)
    rr = L.ref_ranges(b.h, b.l)

    def peak(cells):
        monkeypatch.setattr(L, "PAIR_CELLS", cells)
        tracemalloc.start()
        try:
            lines = L.Lines(b, b.side(1), p, "support", rr, edges)
            return lines, tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
    lines, whole_peak = peak(10 ** 12)
    _, chunk_peak = peak(100_000)
    assert len(lines.A) * (p["tl_max_bars"] + 1) > 20 * 100_000                       # the one grid: many chunks' worth
    assert chunk_peak * 5 < whole_peak, (chunk_peak, whole_peak)                      # bounded by the chunk, not the pairs
