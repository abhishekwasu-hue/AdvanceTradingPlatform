"""M94 PR-1 (docs/design/M94_INTRADAY_PACK.md section 2): the intraday pack's ScreenQL session functions, PatternBreakout's
named flag / pennant arguments and the `intraday_pack` flag.

* SessionHigh / SessionLow / SessionVolume / SessionCount: the session's bars up to and including the bar, on the exchange
  calendar's session; missing on pre-open bars; they start again at each session.
* PreOpenHigh / PreOpenLow / PreOpenVolume: the pre-open bars of the session's date, known from its first bar; missing
  (never 0) without pre-open bars, and the run then says "pre-open data not available" (M94-Q4).
* PatternBreakout(Ref)'s pole= / pole_bars= / flag_min= / flag_max= / retrace=: left out, every call is byte-identical.
* Cut-bar causality (the value read bar by bar equals the run on bars cut at each bar) and the `@1d` reads on an intraday
  base (F24). Fixtures: tests/breakout_fixtures.py (bar by bar) and the Z10-a synthetic sessions."""
import numpy as np
import pandas as pd
import pytest

from app.platform import controls, flag_catalog, flag_state, trader_pack
from app.quality.causal_cases import with_preopen
from app.quality.lookahead import synthetic_bars
from app.screener import compile_screen, nodes, parse
from app.screener.breakouts import gate
from app.screener.breakouts import params as bp
from app.screener.breakouts import session as ses
from app.screener.breakouts.screens import Bars, evaluate_screen
from app.screener.registry import FUNCTIONS, INTRADAY_FLAG, INTRADAY_FUNCTIONS, INTRADAY_SERIES_TFS, PATTERN_SHAPE_KWARGS, describe
from app.screener.runtime import SymbolData, evaluate, run_screen
from tests.breakout_fixtures import SESSION_5M, quiet_day, sessions_frame

PRE = [(100.0, 101.0, 99.5, 100.5, 500.0), (100.5, 102.0, 100.0, 101.0, 700.0)]          # 09:05 and 09:10 IST
DAY1 = [(101.0, 103.0, 100.5, 102.0, 1000.0), (102.0, 104.0, 101.0, 103.0, 2000.0), (103.0, 103.5, 99.0, 100.0, 1500.0)]
DAY2 = [(100.0, 100.5, 98.0, 99.0, 800.0), (99.0, 105.0, 98.5, 104.0, 900.0)]
NAN = float("nan")


def _with_pre():
    return sessions_frame([PRE + DAY1, PRE + DAY2], starts={0: "03:35", 1: "03:35"})


def _ev(text, f, tf="5m", **params):
    return evaluate(parse(text), SymbolData("S", {tf: f}), base_tf=tf, params=params)


def _same(got, want):
    np.testing.assert_array_equal(np.asarray(got, dtype=float), np.asarray(want, dtype=float))


# ---------------------------------------------------------------------------------------------------------- registry
def test_the_new_functions_are_registered_behind_the_two_flags():
    assert set(INTRADAY_FUNCTIONS) == {"SessionHigh", "SessionLow", "SessionVolume", "SessionCount", "PreOpenHigh", "PreOpenLow", "PreOpenVolume"}
    for name in INTRADAY_FUNCTIONS:
        spec = FUNCTIONS[name]
        assert spec.flag == "screener_breakouts" and spec.also_flags == (INTRADAY_FLAG,), name
        assert spec.kind == "factor" and spec.timeframes == INTRADAY_SERIES_TFS and spec.timeframed, name
        assert name in gate.SESSION_FUNCTIONS, name
    assert {FUNCTIONS[n].unit for n in ("SessionHigh", "SessionLow", "PreOpenHigh", "PreOpenLow")} == {"price"}
    assert {FUNCTIONS[n].unit for n in ("SessionVolume", "PreOpenVolume")} == {"volume"}
    assert FUNCTIONS["SessionCount"].unit == "count" and [a.name for a in FUNCTIONS["SessionCount"].args] == ["cond"]


def test_the_flag_is_registered_off_with_its_description():
    assert INTRADAY_FLAG == "intraday_pack"
    assert INTRADAY_FLAG in controls.FEATURE_FLAGS and INTRADAY_FLAG in controls.DEFAULT_OFF_FLAGS
    assert "places no orders" in controls.FEATURE_FLAGS[INTRADAY_FLAG]
    info = flag_catalog.FLAG_INFO[INTRADAY_FLAG]
    assert info.depends_on == ("screener_breakouts",) and info.status == "unfinished"
    assert flag_catalog.FLAG_GROUP[INTRADAY_FLAG] == "screen_library" and "M94" in flag_catalog.FINISH[INTRADAY_FLAG]
    assert flag_state.FLAG_STATUS[INTRADAY_FLAG].state == "not_built"
    assert INTRADAY_FLAG not in trader_pack.PACK and "M94" in trader_pack.excluded()[INTRADAY_FLAG]


def test_pattern_shape_arguments_take_the_breakout_parameters_defaults_and_bounds():
    for fn in ("PatternBreakout", "PatternBreakoutRef"):
        named = {a.name: a for a in FUNCTIONS[fn].kwargs}
        assert set(named) == set(PATTERN_SHAPE_KWARGS), fn
        for arg, param in PATTERN_SHAPE_KWARGS.items():
            p = bp.BY_NAME[param]
            assert named[arg].default == p.default and named[arg].bounds == (p.low, p.high) and not named[arg].required, (fn, arg)
        assert [a.name for a in FUNCTIONS[fn].args] == (["side", "pattern", "v", "k"] if fn == "PatternBreakout"
                                                        else ["what", "side", "pattern", "v", "k", "stop_mult"])


# ------------------------------------------------------------------------------------------------- session functions
def test_session_high_low_volume_and_count_on_a_hand_written_fixture():
    f = _with_pre()                      # rows: 2 pre-open + 3 regular (day 1), 2 pre-open + 2 regular (day 2)
    _same(_ev("SessionHigh()", f), [NAN, NAN, 103.0, 104.0, 104.0, NAN, NAN, 100.5, 105.0])
    _same(_ev("SessionLow()", f), [NAN, NAN, 100.5, 100.5, 99.0, NAN, NAN, 98.0, 98.0])
    _same(_ev("SessionVolume()", f), [NAN, NAN, 1000.0, 3000.0, 4500.0, NAN, NAN, 800.0, 1700.0])
    _same(_ev("SessionCount(close > open)", f), [NAN, NAN, 1.0, 2.0, 2.0, NAN, NAN, 0.0, 1.0])
    # negative: a condition that never holds counts 0 on every session bar (never missing there)
    _same(_ev("SessionCount(close > high)", f), [NAN, NAN, 0.0, 0.0, 0.0, NAN, NAN, 0.0, 0.0])
    # the pre-open 102 high and 99.5 low never enter the session's own extremes
    assert _ev("SessionHigh()", f).max() == 105.0 and _ev("SessionLow()", f).min() == 98.0


def test_session_extremes_include_this_bar_and_agree_with_the_breakouts_before_this_bar():
    f = synthetic_bars(900, tf="5m", seed=3)
    b = Bars(f, bp.settings(), tf="5m")
    before_hi, before_lo = ses.session_extremes_before(b.h, b.l, b.sessions())
    hi, lo = _ev("SessionHigh()", f).to_numpy(), _ev("SessionLow()", f).to_numpy()
    _same(hi, np.where(np.isnan(before_hi), b.h, np.fmax(before_hi, b.h)))
    _same(lo, np.where(np.isnan(before_lo), b.l, np.fmin(before_lo, b.l)))
    new_high = _ev("high >= SessionHigh()", f).to_numpy(dtype=bool)
    assert new_high.any() and not new_high.all()                    # both new session highs and bars below the high


def test_session_volume_is_missing_without_volume_data_and_counts_a_real_zero():
    f = _with_pre()
    g = f.assign(volume=np.nan)
    assert _ev("SessionVolume()", g).isna().all()
    z = f.copy()
    z.iloc[2, z.columns.get_loc("volume")] = 0.0
    assert _ev("SessionVolume()", z).iloc[2] == 0.0                 # a real zero is volume data


def test_session_functions_follow_the_exchange_calendar_and_refuse_a_daily_timeframe():
    # a bar after the regular close (15:30 IST) on a session date is outside the session: no value, nothing counted
    f = _with_pre()
    after = pd.DataFrame([(104.0, 110.0, 97.0, 109.0, 5000.0)], columns=f.columns, index=[f.index[4] + pd.Timedelta(hours=6, minutes=5)])
    g = pd.concat([f.iloc[:5], after, f.iloc[5:]])                     # 15:30 IST on the first date
    for text in ("SessionHigh()", "SessionLow()", "SessionVolume()", "SessionCount(close > open)"):
        got = _ev(text, g)
        assert np.isnan(got.iloc[5]), text
        _same(got.drop(g.index[5]), _ev(text, f))                       # and the other bars read as without it
    for text in ("SessionHigh() > 0", "PreOpenVolume() > 0", "SessionCount(close > open) > 0"):
        assert not compile_screen(text, base_tf="1d")[1].ok, text
        assert compile_screen(text, base_tf="5m")[1].ok, text


def test_session_count_refuses_a_last_bar_only_condition():
    _, v = compile_screen("SessionCount(PatternBullish()) >= 1", base_tf="5m")
    assert not v.ok and any("last bar" in p.message for p in v.problems)


# ------------------------------------------------------------------------------------------------- pre-open functions
def test_pre_open_levels_and_volume_with_pre_open_bars():
    f = _with_pre()
    _same(_ev("PreOpenHigh()", f), [NAN, NAN, 102.0, 102.0, 102.0, NAN, NAN, 102.0, 102.0])
    _same(_ev("PreOpenLow()", f), [NAN, NAN, 99.5, 99.5, 99.5, NAN, NAN, 99.5, 99.5])
    _same(_ev("PreOpenVolume()", f), [NAN, NAN, 1200.0, 1200.0, 1200.0, NAN, NAN, 1200.0, 1200.0])
    b = Bars(f, bp.settings(), tf="5m")                            # the same levels B17 "premarket" reads
    hi, lo = ses.premarket_levels(b.h, b.l, b.sessions())
    _same(_ev("PreOpenHigh()", f), hi)
    _same(_ev("PreOpenLow()", f), lo)


def test_without_pre_open_bars_the_value_is_missing_never_zero():
    f = sessions_frame([DAY1, DAY2])
    for text in ("PreOpenHigh()", "PreOpenLow()", "PreOpenVolume()"):
        got = _ev(text, f)
        assert got.isna().all() and not (got == 0).any(), text
    # one date with pre-open bars, the next without: only the first has values
    g = sessions_frame([PRE + DAY1, DAY2], starts={0: "03:35"})
    vol = _ev("PreOpenVolume()", g).to_numpy()
    _same(vol, [NAN, NAN, 1200.0, 1200.0, 1200.0, NAN, NAN])


def test_a_run_says_pre_open_data_is_not_available_and_never_matches_on_it():
    with_pre, without = SymbolData("A", {"5m": _with_pre()}), SymbolData("B", {"5m": sessions_frame([DAY1, DAY2])})
    ast, v = compile_screen("PreOpenVolume() >= 1000", base_tf="5m")
    a, b = run_screen(ast, v, [with_pre, without], base_tf="5m", check_history=False)
    assert a.matched and a.reason is None
    assert not b.matched and b.reason == "Not checked: pre-open data not available for this symbol." and b.reason.code == "no_preopen"
    # a screen that matches on another part stays a match; one that fails everywhere says why
    ast, v = compile_screen("close > PreOpenHigh() OR close > 0", base_tf="5m")
    assert run_screen(ast, v, [without], base_tf="5m", check_history=False)[0].matched
    ast, v = compile_screen("close > PreOpenHigh() OR close < 0", base_tf="5m")
    assert run_screen(ast, v, [without], base_tf="5m", check_history=False)[0].reason.code == "no_preopen"
    # with pre-open data a plain no-match stays a plain no-match
    ast, v = compile_screen("PreOpenVolume() >= 5000", base_tf="5m")
    got = run_screen(ast, v, [with_pre], base_tf="5m", check_history=False)[0]
    assert not got.matched and got.reason is None


def test_an_index_without_volume_says_volume_not_available_first():
    f = sessions_frame([PRE + DAY1, PRE + DAY2], starts={0: "03:35", 1: "03:35"}).assign(volume=np.nan)
    ast, v = compile_screen("SessionVolume() > 0", base_tf="5m")
    got = run_screen(ast, v, [SymbolData("S", {"5m": f})], base_tf="5m", check_history=False)[0]
    assert not got.matched and got.reason.code == "no_volume"


# ------------------------------------------------------------------------------------------------- cut-bar causality
@pytest.mark.parametrize("text", ["SessionHigh()", "SessionLow()", "SessionVolume()", "SessionCount(close > open)", "PreOpenHigh()",
                                  "PreOpenLow()", "PreOpenVolume()", "SessionHigh()@15m"])
def test_read_bar_by_bar_equals_the_run_on_bars_cut_at_each_bar(text):
    f = with_preopen(synthetic_bars(400, tf="5m", seed=5))
    full = _ev(text, f).to_numpy()
    assert np.isfinite(full).sum() > 100
    for t in list(range(70, 90)) + [150, 151, 152, 225, 226, 399]:      # session edges included (75 bars a session)
        cut = _ev(text, f.iloc[: t + 1]).to_numpy()
        _same(cut, full[: t + 1])


# --------------------------------------------------------------------------------------- PatternBreakout named arguments
def _b6(f, side, overrides):
    return evaluate_screen(f, "B6", bp.settings(**overrides), sides=(side,), columns=("signal",))[side]["signal"].astype(bool)


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_existing_pattern_calls_are_byte_identical(seed):
    """Left out, the named arguments change nothing: the call equals B6 with every other parameter at its default (what
    the runtime ran before M94), and equals the call with every default written out."""
    f = synthetic_bars(900, tf="5m", seed=seed)
    defaults = ", ".join(f"{arg}={bp.DEFAULTS[param]}" for arg, param in PATTERN_SHAPE_KWARGS.items())
    fired = 0
    for side in ("up", "down"):
        for pattern in ("flag", "pennant", "triangle"):
            old = _ev(f'PatternBreakout("{side}", "{pattern}", 0, 0)', f)
            assert old.equals(_b6(f, side, {"pattern": pattern, "vol_mult": 0.0, "min_break_atr": 0.0})), (side, pattern)
            assert old.equals(_ev(f'PatternBreakout("{side}", "{pattern}", 0, 0, {defaults})', f)), (side, pattern)
            ref = _ev(f'PatternBreakoutRef("base_low", "{side}", "{pattern}", 0, 0)', f)
            assert ref.equals(_ev(f'PatternBreakoutRef("base_low", "{side}", "{pattern}", 0, 0, 1, {defaults})', f))
            fired += int(old.sum())
    assert _ev('PatternBreakout("up")', f).equals(_b6(f, "up", {}))       # every argument left out
    assert fired or seed                                                  # seed 0 carries patterns (not vacuous)


def test_named_arguments_reach_the_flag_rule():
    f = synthetic_bars(900, tf="5m", seed=0)
    loose = _ev('PatternBreakout("up", "flag", 0, 0, pole=1, flag_min=3, flag_max=12, retrace=0.8)', f)
    assert loose.equals(_b6(f, "up", {"pattern": "flag", "vol_mult": 0.0, "min_break_atr": 0.0, "pole_min_atr": 1.0,
                                      "flag_min_bars": 3, "flag_max_bars": 12, "flag_max_retrace": 0.8}))
    base = _ev('PatternBreakout("up", "flag", 0, 0)', f)
    assert loose.sum() > base.sum()                                        # positive: looser settings find more flags
    assert not _ev('PatternBreakout("up", "flag", 0, 0, pole=20)', f).any()  # negative: a 20-ATR pole never happens here
    ref = _ev('PatternBreakoutRef("base_low", "up", "flag", 0, 0, 1, pole=1, flag_min=3, flag_max=12, retrace=0.8)', f)
    assert (ref.notna() == loose).all() and (ref[loose] < f["close"][loose]).all()


def test_named_arguments_are_checked_like_positional_ones():
    ok = 'PatternBreakout("up", "flag", 0, 0, pole=2, pole_bars=8, flag_min=4, flag_max=$fm, retrace=0.4)'
    assert compile_screen(ok, base_tf="5m", params={"fm": 10})[1].ok
    for bad in ('PatternBreakout("up", "flag", pole=0.1)',               # below the parameter's range (0.5 .. 20)
                'PatternBreakout("up", "flag", flag_max=7.5)',           # a bar count is a whole number
                'PatternBreakout("up", "flag", flag_min=9, flag_max=6)',  # fewest above most
                'PatternBreakout("up", "flag", flag_max=4)',             # the default fewest (5) above most
                'PatternBreakout("up", "flag", retrace=ATR(14))',        # one value for the whole series
                'PatternBreakout("up", "flag", poles=2)'):               # no such argument
        assert not compile_screen(bad, base_tf="5m")[1].ok, bad


# --------------------------------------------------------------------------------------------------------- flag gate
def test_the_gate_needs_intraday_pack_for_the_functions_and_the_named_arguments():
    def needs(text):
        return gate.extra_flags_needed(parse(text))
    assert needs("SessionHigh() > close") == {"intraday_pack": ["SessionHigh"]}
    assert needs('PatternBreakout("up", "flag")') == {"screener_breakouts_bk4": ["PatternBreakout"]}       # as before M94
    assert needs('PatternBreakout("up", "flag", pole=2)') == {"screener_breakouts_bk4": ["PatternBreakout"],
                                                              "intraday_pack": ["PatternBreakout"]}
    assert gate.required_flags(["PatternBreakout"]) == ["screener_breakouts", "screener_breakouts_bk4"]
    tree = parse('PatternBreakout("up", "flag", retrace=0.4) AND PreOpenLow() < close')
    assert gate.required_flags(gate.flagged_calls(tree), tree) == ["screener_breakouts", "screener_breakouts_bk4", "intraday_pack"]
    assert "intraday_pack" in gate.refusal(["PatternBreakout"], parse('PatternBreakout("up", "flag", pole=2)'))
    assert gate.session_calls(parse("SessionCount(close > open) > 2")) == ["SessionCount"]
    assert "intraday_pack" in gate.EXTRA_FLAGS


def test_the_registry_view_hides_the_named_arguments_while_the_flag_is_off():
    entries = describe()
    assert set(entries["PatternBreakout"]["kwargs"]) == set(PATTERN_SHAPE_KWARGS)
    hidden = gate.hide(entries)
    for fn in gate.PATTERN_SHAPE_CALLS:
        assert hidden[fn]["kwargs"] == [] and "pole=" not in hidden[fn]["doc"]
        assert hidden[fn]["args"] == entries[fn]["args"]
    assert hidden["SessionHigh"] == entries["SessionHigh"]


def test_a_strategy_recipe_reading_the_named_arguments_needs_the_flag():
    call = next(x for x in nodes.walk(parse('PatternBreakout("up", "flag", flag_max=10)')) if isinstance(x, nodes.Call))
    assert gate.call_extra_flags(call) == ["screener_breakouts_bk4", "intraday_pack"]
    plain = next(x for x in nodes.walk(parse('PatternBreakout("up", "flag")')) if isinstance(x, nodes.Call))
    assert gate.call_extra_flags(plain) == ["screener_breakouts_bk4"]


# ------------------------------------------------------------------------------------------- F24: @1d on an intraday base
def test_daily_reads_on_an_intraday_base_are_the_closed_sessions():
    """F24: on 5m bars `high@1d` is the high of the latest CLOSED daily bar - during session D the previous session's (its
    last bar closes with the day, so there it is D's own) - and `Lag(high, 1)@1d` = `high@1d[1]` the one before it.
    PDH() is the previous session's high on every bar of D."""
    days = [quiet_day(mid=100.0 + k) for k in range(4)]                  # session k: every high is 100.25 + k
    f = sessions_frame(days)
    r = {t: _ev(t, f).to_numpy() for t in ("high@1d", "Lag(high, 1)@1d", "high@1d[1]", "PDH()", "SessionHigh()")}
    s = SESSION_5M
    for d in (2, 3):
        mid, last = d * s + 10, (d + 1) * s - 1
        assert r["high@1d"][mid] == 100.25 + d - 1 and r["PDH()"][mid] == 100.25 + d - 1
        assert r["Lag(high, 1)@1d"][mid] == r["high@1d[1]"][mid] == 100.25 + d - 2
        assert r["high@1d"][last] == 100.25 + d and r["Lag(high, 1)@1d"][last] == 100.25 + d - 1   # the day closed with this bar
        assert r["PDH()"][last] == 100.25 + d - 1 and r["SessionHigh()"][last] == 100.25 + d
    assert np.isnan(r["high@1d"][s - 2]) and r["high@1d"][s - 1] == 100.25         # nothing before the first close
    # pre-open bars are part of the date's daily bar built from the base (and of PDH), not of the session's own values
    pre = [(110.0, 110.6, 109.9, 110.2, 500.0)]
    g = sessions_frame([pre + x for x in days], starts={k: "03:40" for k in range(4)})
    at = (s + 1) + 1 + 5                                                     # the sixth session bar of the second date
    assert _ev("high@1d", g).iloc[at] == 110.6 and _ev("PDH()", g).iloc[at] == 110.6
    assert _ev("SessionHigh()", g).iloc[at] == 101.25 and _ev("PreOpenHigh()", g).iloc[at] == 110.6
