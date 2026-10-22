"""Strategy templates step 4: R2 for the other breakout families (the level companions SwingBreakoutRef, NRBreakoutRef,
VolumeBreakoutRef, DonchianBreakoutRef, MABreakoutRef, PatternBreakoutRef, VCPBreakoutRef) and the templates B2 (swing-level
breakout), B4 (trendline), B5 (Donchian), B6 (pattern), B7 (squeeze), B8 (inside bar / narrow range), B9 (volume), B11 (volatility
contraction, Daily), C5 (Donchian breakout and retest), D4 (moving-average breakout), D5 (ADX / DI), D6 (MACD), D7 (parabolic
SAR) and E4 (stochastic) against the owner order's section 8 gate - must fire long and short / must not (the near misses),
the invalidation choices by value, S5 conflicts, S6, the exits, the warm-up, the truncation test over many t, the mirror
test, scan = backtest = options backtest = worker on one trailing window (the TPL-1 harness), property tests on random
valid settings, the language guard, "no number written into a rule" and the ids."""
import re

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st

from app.backtest.options_engine import OptionBacktestConfig
from app.core.enums import OptionStrategy
from app.core.models import RiskConfig
from app.quality.lookahead import synthetic_bars
from app.screener.breakouts import params as bp
from app.screener.breakouts.quality import stop_reference
from app.screener.breakouts.screens import Bars, evaluate_screen
from app.screener.parser import parse
from app.screener.registry import FUNCTIONS
from app.screener.runtime import SymbolData, evaluate
from app.strategy_templates import compile as C
from app.strategy_templates import engine, ids, language, library, parity
from app.strategy_templates.adapter import ENGINE_LABEL, TemplateStrategy
from tests.breakout_bk3_fixtures import vcp

NEW = ("B2", "B4", "B5", "B6", "B7", "B8", "B9", "B11", "C5", "D4", "D5", "D6", "D7", "E4")
T = {tid: library.get(tid) for tid in NEW}
RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5)
K = 400.0                                                     # mirror axis: price -> K - price


def run(template, df, tf="15m", **params):
    return engine.compute(C.compile_recipe(template, C.resolve_settings(template, tf, params=params)), {tf: df})


def ev(text, df, tf="15m"):
    return evaluate(parse(text), SymbolData("S", {tf: df}), base_tf=tf)


def mirror(df, axis=K):
    out = df.copy()
    out["open"], out["close"] = axis - df["open"], axis - df["close"]
    out["high"], out["low"] = axis - df["low"], axis - df["high"]
    return out


def signals(s):
    return {t: int(s.direction[t]) for t in s.signal_bars()}


def bars(seed, n=600, tf="15m"):
    return synthetic_bars(n, tf=tf, seed=seed)


def expect(s, df, long, short, inv_long, inv_short, tf="15m"):
    """The signals a same-bar event template must give: every long / short event after the warm-up, except a bar where
    both sides fire (S5: conflict) and a bar whose invalidation is not on the losing side of the close (S6) - and with
    that invalidation as the signal's level. Returns the expected {bar: side}."""
    lo, sh = ev(long, df, tf).to_numpy(dtype=bool), ev(short, df, tf).to_numpy(dtype=bool)
    il, ish = ev(inv_long, df, tf).to_numpy(dtype=float), ev(inv_short, df, tf).to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    want = {}
    for t in range(s.warmup_bars, len(df)):
        if lo[t] and sh[t]:
            assert s.reason[t] == engine.REASON_CONFLICT, t
            continue
        if not (lo[t] or sh[t]):
            continue
        sign, inv = (1, il[t]) if lo[t] else (-1, ish[t])
        if not np.isfinite(inv):
            assert s.reason[t] == engine.REASON_NO_LEVEL, t
        elif (sign > 0 and not inv < close[t]) or (sign < 0 and not inv > close[t]):
            assert s.reason[t] == engine.REASON_GEOMETRY, t
        else:
            want[t] = sign
            assert s.invalidation[t] == pytest.approx(inv), t
    assert signals(s) == want
    return want


def both_sides(want):
    assert 1 in want.values() and -1 in want.values(), "the fixture must carry long and short signals"


# ================================================================================= R2: the new level companions
COMPANIONS = [   # (companion call without what, the breakout it follows, screen, breakout settings)
    ('SwingBreakoutRef({what}, "{side}", 0, 0.1)', 'SwingBreakout("{side}", 0, 0.1)', "B2", {"swing_degree": 0, "min_break_atr": 0.1}),
    ('NRBreakoutRef({what}, "{side}", "inside", 0, 0)', 'NRBreakout("{side}", "inside", 0, 0)', "B4",
     {"nr_setup": "inside", "vol_mult": 0.0, "min_break_atr": 0.0}),
    ('VolumeBreakoutRef({what}, "{side}", 1.5, 10)', 'VolumeBreakout("{side}", 1.5, 10)', "B14", {"shock_mult": 1.5, "range_n": 10}),
    ('DonchianBreakoutRef({what}, "{side}", 15, 0.1)', 'DonchianBreakout("{side}", 15, 0.1)', "B10", {"donchian_n": 15, "min_break_atr": 0.1}),
    ('MABreakoutRef({what}, "{side}", 20, "ema", 0)', 'MABreakout("{side}", 20, "ema", 0)', "B12",
     {"ma_n": 20, "ma_type": "ema", "min_break_atr": 0.0}),
    ('PatternBreakoutRef({what}, "{side}", "triangle", 0, 0)', 'PatternBreakout("{side}", "triangle", 0, 0)', "B6",
     {"pattern": "triangle", "vol_mult": 0.0, "min_break_atr": 0.0}),
]


@pytest.mark.parametrize("ref, base, screen, settings", COMPANIONS, ids=[c[2] for c in COMPANIONS])
def test_companions_give_the_breakouts_own_prices_on_its_signal_bars(ref, base, screen, settings):
    df = bars(4)
    p = bp.settings(**settings)
    b = Bars(df, p)
    seen = 0
    for side in ("up", "down"):
        fired = ev(base.format(side=side), df).to_numpy(dtype=bool)
        out = evaluate_screen(df, screen, p, bars=b, sides=(side,))[side]
        assert (out["signal"].to_numpy(dtype=bool) == fired).all()
        for what in ("level", "fail_level", "base_high", "base_low"):
            v = ev(ref.format(what=f'"{what}"', side=side), df).to_numpy(dtype=float)
            assert np.isnan(v[~fired]).all(), what                                  # prices on the signal bars only
            np.testing.assert_allclose(v[fired], out[what].to_numpy(dtype=float)[fired], err_msg=what)
        held = ev(ref.format(what='"held_level"', side=side), df).to_numpy(dtype=float)
        level = out["level"].to_numpy(dtype=float)
        last = np.nan
        for t in range(len(df)):                                                    # held forward until the next signal
            last = level[t] if fired[t] else last
            assert (np.isnan(held[t]) and np.isnan(last)) or held[t] == pytest.approx(last), t
        atr_prev = b.atr_prev
        for mode in ("level_atr", "base", "midpoint"):
            v = ev(ref.format(what=f'"stop_{mode}"', side=side)[:-1] + ", 1.5)", df).to_numpy(dtype=float)   # stop_mult 1.5
            for t in np.flatnonzero(fired):
                want = stop_reference(level[t], out["base_low"].iloc[t], out["base_high"].iloc[t], float(atr_prev[t]), side, mode, 1.5)
                assert v[t] == pytest.approx(want), (mode, t)
                seen += 1
    assert seen, "the fixture must carry breakouts"


def test_vcp_companion_on_the_vcp_fixture_and_its_mirror():
    f = vcp()
    out = evaluate_screen(f, "B3", bp.settings())["up"]
    j = len(f) - 1
    level = ev('VCPBreakoutRef("level", "up")', f, "1d")
    assert level.notna().sum() == 1 and level.iloc[j] == pytest.approx(out["level"].iloc[j])
    assert ev('VCPBreakoutRef("stop_base", "up")', f, "1d").iloc[j] == pytest.approx(out["base_low"].iloc[j])   # the last contraction
    m = mirror(f, 300.0)
    assert ev('VCPBreakoutRef("stop_base", "down")', m, "1d").iloc[j] == pytest.approx(300.0 - out["base_low"].iloc[j])
    assert ev('VCPBreakoutRef("level", "up", 2, 1, 1.5, 0.2, FALSE, FALSE)', f, "1d").iloc[j] == pytest.approx(out["level"].iloc[j])


def test_companions_keep_the_breakout_arguments_bounds_and_defaults():
    pairs = {"SwingBreakoutRef": "SwingBreakout", "NRBreakoutRef": "NRBreakout", "VolumeBreakoutRef": "VolumeBreakout",
             "DonchianBreakoutRef": "DonchianBreakout", "MABreakoutRef": "MABreakout", "PatternBreakoutRef": "PatternBreakout",
             "VCPBreakoutRef": "VCPBreakout"}
    for ref, base in pairs.items():
        r, b = FUNCTIONS[ref], FUNCTIONS[base]
        assert [a.name for a in r.args] == ["what", "side", *[a.name for a in b.args[1:]], "stop_mult"], ref
        for a, c in zip(r.args[2:-1], b.args[1:]):
            assert (a.type, a.default, a.choices, a.bounds) == (c.type, c.default, c.choices, c.bounds), (ref, a.name)
        assert (r.flag, r.also_flags, r.timeframes) == (b.flag, b.also_flags, b.timeframes), ref
        assert r.unit == "price" and r.min_bars == b.min_bars, ref
        assert ref in engine.SPARSE_SERIES


# ============================================================================================================== B2
def test_b2_fires_exactly_on_the_breaks_of_structure_both_sides():
    seen = {}
    for seed in (1, 5, 6, 8):
        df = bars(seed)
        s = run(T["B2"], df, swing_degree=0, k=0.0)
        seen.update(expect(s, df, 'SwingBreakType("up", 0, 0) == "BOS"', 'SwingBreakType("down", 0, 0) == "BOS"', "SwingLow(0)", "SwingHigh(0)"))
    both_sides(seen)


def test_b2_must_not_fire_on_a_change_of_character_or_a_plain_break():
    """The near miss: a close beyond the swing AGAINST the swing trend (CHOCH) or without a trend (BREAK) is no signal."""
    df = bars(5)
    s = run(T["B2"], df, swing_degree=0, k=0.0)
    other = [int(t) for side in ("up", "down") for t in np.flatnonzero(ev(f'SwingBreakType("{side}", 0, 0)', df).isin(["CHOCH", "BREAK"]).to_numpy())]
    assert other and not set(other) & set(s.signal_bars())


def test_b2_invalidation_just_beyond_the_broken_swing_and_the_exit():
    df = bars(6)
    s = run(T["B2"], df, swing_degree=0, k=0.0, invalidation="stop_level_atr", stop_mult=0.5)
    want = expect(s, df, 'SwingBreakType("up", 0, 0) == "BOS"', 'SwingBreakType("down", 0, 0) == "BOS"',
                  'SwingBreakoutRef("stop_level_atr", "up", 0, 0, 0.5)', 'SwingBreakoutRef("stop_level_atr", "down", 0, 0, 0.5)')
    assert want
    chl = (ev('SwingBreakType("down", 0, 0)', df) == "CHOCH").to_numpy() & (np.arange(len(df)) >= s.warmup_bars)
    chs = (ev('SwingBreakType("up", 0, 0)', df) == "CHOCH").to_numpy() & (np.arange(len(df)) >= s.warmup_bars)
    assert (s.exit_long == chl).all() and (s.exit_short == chs).all() and chl.any()


def test_b2_warm_up_is_the_swings_history():
    df = bars(1)
    assert run(T["B2"], df).warmup_bars == 249 and run(T["B2"], df, swing_degree=0).warmup_bars == 99


# ============================================================================================================== B4
@pytest.mark.parametrize("seed", [1, 4])
def test_b4_fires_exactly_on_the_trendline_breakouts(seed):
    df = bars(seed)
    s = run(T["B4"], df)
    want = expect(s, df, 'TrendlineBreakout("up", "line", 2, 0.2)', 'TrendlineBreakout("down", "line", 2, 0.2)',
                  'LineBreakoutRef("stop_level_atr", "up", "line", 2, 0.2, 1)', 'LineBreakoutRef("stop_level_atr", "down", "line", 2, 0.2, 1)')
    both_sides(want)
    far = run(T["B4"], df, invalidation="stop_base")
    expect(far, df, 'TrendlineBreakout("up", "line", 2, 0.2)', 'TrendlineBreakout("down", "line", 2, 0.2)',
           'LineBreakoutRef("stop_base", "up", "line", 2, 0.2)', 'LineBreakoutRef("stop_base", "down", "line", 2, 0.2)')


def test_b4_near_miss_and_exit():
    df = bars(1)
    plain = signals(run(T["B4"], df))
    fewer = signals(run(T["B4"], df, k=1.5))
    assert set(fewer.items()) <= set(plain.items()) and len(fewer) < len(plain)       # a larger break distance never adds one
    s = run(T["B4"], df)
    held = ev('LineBreakoutRef("held_level", "up", "line", 2, 0.2)', df)
    assert (s.exit_long == ((df["close"] < held).to_numpy() & (np.arange(len(df)) >= s.warmup_bars))).all()
    assert s.warmup_bars == 124


# ============================================================================================================== B5
@pytest.mark.parametrize("seed", [1, 3])
def test_b5_fires_exactly_on_the_donchian_breakouts_with_the_channel_middle(seed):
    df = bars(seed)
    s = run(T["B5"], df)
    want = expect(s, df, 'DonchianBreakout("up", 20, 0.2)', 'DonchianBreakout("down", 20, 0.2)',
                  'DonchianBreakoutRef("stop_midpoint", "up", 20, 0.2)', 'DonchianBreakoutRef("stop_midpoint", "down", 20, 0.2)')
    both_sides(want)
    hi, lo = df["high"].rolling(20).max().shift(1), df["low"].rolling(20).min().shift(1)
    for t in want:
        assert s.invalidation[t] == pytest.approx((hi.iloc[t] + lo.iloc[t]) / 2)          # the middle of the channel before the bar


def test_b5_swing_invalidation_and_the_exit_channel():
    df = bars(2)
    s = run(T["B5"], df, invalidation="swing", swing_degree=0, exit_n=5)
    expect(s, df, 'DonchianBreakout("up", 20, 0.2)', 'DonchianBreakout("down", 20, 0.2)', "SwingLow(0)", "SwingHigh(0)")
    live = np.arange(len(df)) >= s.warmup_bars
    lo5, hi5 = df["low"].rolling(5).min().shift(1), df["high"].rolling(5).max().shift(1)
    assert (s.exit_long == ((df["close"] < lo5).to_numpy() & live)).all()
    assert (s.exit_short == ((df["close"] > hi5).to_numpy() & live)).all() and s.exit_long.any()


def test_b5_near_miss_a_wider_channel_or_break_distance():
    df = bars(1)
    plain = signals(run(T["B5"], df))
    assert set(signals(run(T["B5"], df, k=1.5)).items()) < set(plain.items())


# ============================================================================================================== B6
@pytest.mark.parametrize("pattern, seed", [("triangle", 1), ("flag", 18), ("pennant", 17)])
def test_b6_fires_exactly_on_the_pattern_breakouts(pattern, seed):
    df = bars(seed)
    s = run(T["B6"], df, pattern=pattern, k=0.0)
    want = expect(s, df, f'PatternBreakout("up", "{pattern}", 0, 0)', f'PatternBreakout("down", "{pattern}", 0, 0)',
                  f'PatternBreakoutRef("stop_base", "up", "{pattern}", 0, 0)', f'PatternBreakoutRef("stop_base", "down", "{pattern}", 0, 0)')
    assert want
    if pattern == "triangle":
        both_sides(want)
        mid = run(T["B6"], df, pattern=pattern, k=0.0, invalidation="stop_midpoint")
        expect(mid, df, 'PatternBreakout("up", "triangle", 0, 0)', 'PatternBreakout("down", "triangle", 0, 0)',
               'PatternBreakoutRef("stop_midpoint", "up", "triangle", 0, 0)', 'PatternBreakoutRef("stop_midpoint", "down", "triangle", 0, 0)')


def test_b6_volume_asked_for_and_the_exit():
    df = bars(1)
    assert not run(T["B6"], df.drop(columns=["volume"]), pattern="triangle", k=0.0, vol_mult=1.5).signal_bars()   # no volume, none
    assert run(T["B6"], df.drop(columns=["volume"]), pattern="triangle", k=0.0).signal_bars()                   # off: an index signals
    s = run(T["B6"], df, pattern="triangle", k=0.0)
    held = ev('PatternBreakoutRef("held_level", "down", "triangle", 0, 0)', df)
    assert (s.exit_short == ((df["close"] > held).to_numpy() & (np.arange(len(df)) >= s.warmup_bars))).all()


# ============================================================================================================== B7
@pytest.mark.parametrize("mode, seed", [("bollinger", 2), ("ttm", 4)])
def test_b7_fires_exactly_on_the_squeeze_breakouts_with_the_band_middle(mode, seed):
    df = bars(seed)
    s = run(T["B7"], df, mode=mode)
    both_sides(expect(s, df, f'SqueezeBreakout("up", "{mode}", 20, 125, 0)', f'SqueezeBreakout("down", "{mode}", 20, 125, 0)',
                      "BBMid(20)", "BBMid(20)"))


def test_b7_exit_after_closes_back_inside_the_squeezes_own_bands():
    """The exit reads the same bands as the squeeze (the band width is the breakout platform's, BBUpper's default)."""
    assert FUNCTIONS["BBUpper"].args[1].default == bp.DEFAULTS["bb_k"]
    df = bars(4)
    s = run(T["B7"], df, exit_bars=3)
    inside = ((df["close"] < ev("BBUpper(20, 2)", df)) & (df["close"] > ev("BBLower(20, 2)", df))).to_numpy()
    streak = np.zeros(len(df), dtype=int)
    for t in range(len(df)):
        streak[t] = (streak[t - 1] + 1 if t else 1) if inside[t] else 0
    want = (streak >= 3) & (np.arange(len(df)) >= s.warmup_bars)
    assert (s.exit_long == want).all() and (s.exit_short == want).all() and want.any()


def test_b7_near_miss_volume_and_lookback():
    df = bars(2)
    assert run(T["B7"], df.drop(columns=["volume"]), vol_mult=1.5).signal_bars() == []     # volume asked for: an index never signals
    assert run(T["B7"], df.drop(columns=["volume"])).signal_bars()


# ============================================================================================================== B8
@pytest.mark.parametrize("setup", ["inside", "nr4", "nr7"])
def test_b8_fires_exactly_on_the_quiet_bar_breakouts_with_the_mother_bar(setup):
    df = bars(2)
    s = run(T["B8"], df, nr_setup=setup)
    want = expect(s, df, f'NRBreakout("up", "{setup}", 0, 0.2)', f'NRBreakout("down", "{setup}", 0, 0.2)',
                  f'NRBreakoutRef("fail_level", "up", "{setup}", 0, 0.2)', f'NRBreakoutRef("fail_level", "down", "{setup}", 0, 0.2)')
    both_sides(want)
    lows, highs = df["low"].to_numpy(), df["high"].to_numpy()
    for t, d in want.items():                                     # the opposite side of the mother bar (inside: the bar before)
        inv = s.invalidation[t]
        assert any(inv == pytest.approx((lows if d > 0 else highs)[t - back]) for back in range(1, 5)), t


def test_b8_has_no_exit_and_needs_volume_only_when_asked():
    df = bars(2)
    s = run(T["B8"], df)
    assert not s.exit_long.any() and not s.exit_short.any()
    loud = run(T["B8"], df, vol_mult=2.0)
    expect(loud, df, 'NRBreakout("up", "nr7", 2, 0.2)', 'NRBreakout("down", "nr7", 2, 0.2)',
           'NRBreakoutRef("fail_level", "up", "nr7", 2, 0.2)', 'NRBreakoutRef("fail_level", "down", "nr7", 2, 0.2)')
    assert 0 < len(loud.signal_bars()) < len(s.signal_bars())
    assert run(T["B8"], df.drop(columns=["volume"]), vol_mult=2.0).signal_bars() == []       # no volume: only with the condition off


# ============================================================================================================== B9
@pytest.mark.parametrize("seed", [1, 4])
def test_b9_fires_exactly_on_the_volume_surges(seed):
    df = bars(seed)
    s = run(T["B9"], df)
    want = expect(s, df, 'VolumeBreakout("up", 2, 20)', 'VolumeBreakout("down", 2, 20)',
                  'VolumeBreakoutRef("stop_level_atr", "up", 2, 20)', 'VolumeBreakoutRef("stop_level_atr", "down", 2, 20)')
    both_sides(want)


def test_b9_needs_volume():
    df = bars(1)
    assert run(T["B9"], df).signal_bars()
    assert run(T["B9"], df.assign(volume=100.0)).signal_bars() == []                # flat volume: no surge, no signal
    assert set(signals(run(T["B9"], df, vol_mult=4.0)).items()) < set(signals(run(T["B9"], df)).items())


# ============================================================================================================== B11
def test_b11_must_fire_on_the_vcp_both_sides_with_the_last_contraction():
    f = vcp()
    j = len(f) - 1
    s = run(T["B11"], f, "1d")
    base_low = evaluate_screen(f, "B3", bp.settings())["up"]["base_low"].iloc[j]
    assert signals(s) == {j: 1} and s.invalidation[j] == pytest.approx(base_low)
    m = run(T["B11"], mirror(f, 300.0), "1d")
    assert signals(m) == {j: -1} and m.invalidation[j] == pytest.approx(300.0 - base_low)
    pivot = run(T["B11"], f, "1d", invalidation="stop_level_atr", stop_mult=0.0)
    assert pivot.invalidation[j] == pytest.approx(evaluate_screen(f, "B3", bp.settings())["up"]["level"].iloc[j])


@pytest.mark.parametrize("kwargs, params", [({"contractions": (16.0,)}, {}), ({"dry": False}, {}), ({"trend": False}, {}),
                                            ({"wick_only": True}, {}), ({"break_volume": 500.0}, {}), ({}, {"contractions": 4}),
                                            ({}, {"ratio": 0.4})])
def test_b11_must_not_fire_without_every_rule(kwargs, params):
    assert run(T["B11"], vcp(**kwargs), "1d", **params).signal_bars() == []


def test_b11_rules_can_be_turned_off_and_it_is_daily_only():
    assert run(T["B11"], vcp(dry=False), "1d", dryup=False).signal_bars() == [len(vcp()) - 1]
    assert run(T["B11"], vcp(trend=False), "1d", trend_template=False).signal_bars() == [len(vcp(trend=False)) - 1]
    assert run(T["B11"], vcp(break_volume=500.0), "1d", vol_mult=0.0).signal_bars() == [len(vcp()) - 1]
    assert T["B11"].allowed_timeframes == ("1d",)
    with pytest.raises(C.TemplateError):
        C.resolve_settings(T["B11"], "15m")


def test_b11_truncation_and_scan_equals_backtest():
    f = vcp()
    full = run(T["B11"], f, "1d")
    for t in range(len(f) - 40, len(f)):
        part = run(T["B11"], f.iloc[:t + 1], "1d")
        np.testing.assert_array_equal(part.direction, full.direction[:t + 1])
    strat = TemplateStrategy(T["B11"], C.resolve_settings(T["B11"], "1d"))
    scan = parity.scan_answers(strat.recipe, f)
    answers, _ = parity.backtest_answers(strat, f, "S", RISK)
    assert sum(1 for a in scan.values() if a.direction) == 1 and parity.compare(scan, answers, "backtest") == []
    worker = parity.worker_answers(strat, f, "S", list(range(len(f) - 5, len(f))))
    assert parity.compare(scan, worker, "worker") == []


# ============================================================================================================== C5
def _c5(side, rest="10, FALSE", tail="k=0.2, n=20"):
    return f'RetestEntry("{side}", "donchian", {rest}, {tail})'


def _c5ref(what, side, rest="10, FALSE", tail="k=0.2, n=20", stop=""):
    return f'RetestEntryRef("{what}", "{side}", "donchian", {rest}{stop}, {tail})'


@pytest.mark.parametrize("seed", [2, 3, 7, 15])
def test_c5_fires_exactly_on_the_donchian_retests_with_the_farther_reference(seed):
    df = bars(seed)
    s = run(T["C5"], df, retest_reversal=False)
    want = {}
    for side, sign in (("up", 1), ("down", -1)):
        fired = ev(_c5(side), df).to_numpy()
        low, ref = ev(_c5ref("retest_low", side), df), ev(_c5ref("stop_level_atr", side, stop=", 1"), df)
        for t in np.flatnonzero(fired):
            if t < s.warmup_bars or s.reason[t] == engine.REASON_GEOMETRY:
                continue
            want[int(t)] = sign
            assert s.invalidation[t] == pytest.approx(min(low.iloc[t], ref.iloc[t]) if sign > 0 else max(low.iloc[t], ref.iloc[t]))
    assert want
    assert signals(s) == want


def test_c5_runs_on_the_donchian_breakouts_own_settings():
    df = bars(7)
    s = run(T["C5"], df, retest_reversal=False, n=30, k=0.1)
    want = {int(t): sign for side, sign in (("up", 1), ("down", -1))
            for t in np.flatnonzero(ev(_c5(side, tail="k=0.1, n=30"), df).to_numpy())
            if t >= s.warmup_bars and s.reason[t] != engine.REASON_GEOMETRY}
    assert signals(s) == want
    for key in ("n", "k"):
        a, b = T["C5"].param(key), T["B5"].param(key)
        assert (a.type, a.default, a.min, a.max) == (b.type, b.default, b.min, b.max)


def test_c5_invalidation_choices_exit_and_near_misses():
    df = bars(3)
    for choice, ref in (("retest_low", None), ("level_minus_stop_ref", "stop_base"), ("level_minus_stop_ref", "stop_midpoint")):
        s = run(T["C5"], df, retest_reversal=False, invalidation=choice, **({"breakout_ref": ref} if ref else {}))
        what = "retest_low" if ref is None else ref
        for t, d in signals(s).items():
            assert s.invalidation[t] == pytest.approx(ev(_c5ref(what, "up" if d > 0 else "down", stop=", 1" if ref else ""), df).iloc[t])
    s = run(T["C5"], df, retest_reversal=False)
    held = ev(_c5ref("held_level", "up"), df)
    assert (s.exit_long == ((df["close"] < held).to_numpy() & (np.arange(len(df)) >= s.warmup_bars))).all()
    plain = signals(s)
    for strict in ({"retest_max_bars": 2}, {"retest_reversal": True}):
        assert set(signals(run(T["C5"], df, **{"retest_reversal": False, **strict})).items()) < set(plain.items()), strict


# ============================================================================================================== D4
@pytest.mark.parametrize("kind, seed", [("sma", 1), ("ema", 4)])
def test_d4_fires_exactly_on_the_average_breakouts_with_the_average_less_the_buffer(kind, seed):
    df = bars(seed)
    s = run(T["D4"], df, ma_n=20, ma_type=kind)
    want = expect(s, df, f'MABreakout("up", 20, "{kind}", 0.2)', f'MABreakout("down", 20, "{kind}", 0.2)',
                  f'MABreakoutRef("stop_level_atr", "up", 20, "{kind}", 0.2)', f'MABreakoutRef("stop_level_atr", "down", 20, "{kind}", 0.2)')
    both_sides(want)
    ma = ev(f"{kind.upper()}(close, 20)", df)
    atr_prev = Bars(df, bp.settings()).atr_prev
    for t, d in want.items():
        assert s.invalidation[t] == pytest.approx(ma.iloc[t] - d * atr_prev[t])


def test_d4_buffer_and_no_exit():
    df = bars(1)
    s = run(T["D4"], df, ma_n=20, stop_mult=0.0)
    ma = ev("SMA(close, 20)", df)
    assert all(s.invalidation[t] == pytest.approx(ma.iloc[t]) for t in s.signal_bars()) and s.signal_bars()
    assert not s.exit_long.any() and not s.exit_short.any()


# ============================================================================================================== D5
D5_DI = ("CrossAbove(PlusDI(14), MinusDI(14)) AND ADX(14) > {thr} AND ADX(14) > Lag(ADX(14), 1)",
         "CrossAbove(MinusDI(14), PlusDI(14)) AND ADX(14) > {thr} AND ADX(14) > Lag(ADX(14), 1)")
D5_ADX = ("CrossAbove(ADX(14), {thr}) AND PlusDI(14) > MinusDI(14) AND ADX(14) > Lag(ADX(14), 1)",
          "CrossAbove(ADX(14), {thr}) AND MinusDI(14) > PlusDI(14) AND ADX(14) > Lag(ADX(14), 1)")


def test_d5_di_cross_with_a_strong_rising_adx():
    seen = {}
    for seed in (1, 6, 13, 19):
        df = bars(seed)
        s = run(T["D5"], df, adx_threshold=10.0, swing_degree=0)
        seen.update(expect(s, df, D5_DI[0].format(thr=10), D5_DI[1].format(thr=10), "SwingLow(0)", "SwingHigh(0)"))
    both_sides(seen)


def test_d5_adx_through_its_threshold_and_the_exits():
    df = bars(4)
    s = run(T["D5"], df, trigger_event="adx_threshold", swing_degree=0)
    both_sides(expect(s, df, D5_ADX[0].format(thr=20), D5_ADX[1].format(thr=20), "SwingLow(0)", "SwingHigh(0)"))
    live = np.arange(len(df)) >= s.warmup_bars
    assert (s.exit_long == (ev("CrossAbove(MinusDI(14), PlusDI(14))", df).to_numpy() & live)).all()
    below = run(T["D5"], df, trigger_event="adx_threshold", swing_degree=0, exit_rule="adx_below")
    fell = ev("CrossBelow(ADX(14), 20)", df).to_numpy() & live
    assert (below.exit_long == fell).all() and (below.exit_short == fell).all() and fell.any()


def test_d5_must_not_fire_when_adx_is_weak_or_falling():
    df = bars(13)
    loose = signals(run(T["D5"], df, adx_threshold=1.0, swing_degree=0))
    strict = signals(run(T["D5"], df, adx_threshold=60.0, swing_degree=0))
    assert loose and strict == {}
    longer = signals(run(T["D5"], df, adx_threshold=1.0, swing_degree=0, rise_bars=10))
    adx = ev("ADX(14)", df)
    assert all(adx.iloc[t] > adx.iloc[t - 10] for t in longer)                  # rising over the bars asked for


# ============================================================================================================== D6
def test_d6_fires_exactly_on_the_macd_crosses():
    df = bars(1)
    s = run(T["D6"], df, swing_degree=0)
    both_sides(expect(s, df, "CrossAbove(MACD(12, 26, 9), MACDSignal(12, 26, 9))", "CrossBelow(MACD(12, 26, 9), MACDSignal(12, 26, 9))",
                      "SwingLow(0)", "SwingHigh(0)"))
    live = np.arange(len(df)) >= s.warmup_bars
    assert (s.exit_long == (ev("CrossBelow(MACD(12, 26, 9), MACDSignal(12, 26, 9))", df).to_numpy() & live)).all()


def test_d6_zero_line_filter_and_the_settings():
    df = bars(2)
    s = run(T["D6"], df, swing_degree=0, zero_filter=True, fast=8, slow=21, signal=5)
    both_sides(expect(s, df, "CrossAbove(MACD(8, 21, 5), MACDSignal(8, 21, 5)) AND MACD(8, 21, 5) > 0",
                      "CrossBelow(MACD(8, 21, 5), MACDSignal(8, 21, 5)) AND MACD(8, 21, 5) < 0", "SwingLow(0)", "SwingHigh(0)"))
    with pytest.raises(C.TemplateError, match="fewer bars"):
        C.resolve_settings(T["D6"], "15m", params={"fast": 30, "slow": 26})


# ============================================================================================================== D7
def test_d7_fires_on_every_sar_flip_with_the_sar_as_invalidation():
    df = bars(1)
    s = run(T["D7"], df)
    both_sides(expect(s, df, "PSARDir() == 1 AND PSARDir()[1] == -1", "PSARDir() == -1 AND PSARDir()[1] == 1", "PSAR()", "PSAR()"))
    live = np.arange(len(df)) >= s.warmup_bars
    assert (s.exit_long == (ev("PSARDir() == -1 AND PSARDir()[1] == 1", df).to_numpy() & live)).all()


def test_d7_settings_and_must_not_fire_without_a_flip():
    df = bars(3)
    s = run(T["D7"], df, start=0.01, step=0.01, max_step=0.1)
    expect(s, df, "PSARDir(0.01, 0.01, 0.1) == 1 AND PSARDir(0.01, 0.01, 0.1)[1] == -1",
           "PSARDir(0.01, 0.01, 0.1) == -1 AND PSARDir(0.01, 0.01, 0.1)[1] == 1", "PSAR(0.01, 0.01, 0.1)", "PSAR(0.01, 0.01, 0.1)")
    trend = synthetic_bars(400, tf="15m", seed=1)
    steady = trend.assign(open=np.linspace(100, 200, 400), close=np.linspace(100.5, 200.5, 400))
    steady = steady.assign(high=steady["close"] + 0.2, low=steady["open"] - 0.2)
    assert [d for d in signals(run(T["D7"], steady)).values() if d < 0] == []       # a steady rise: no bearish flip
    with pytest.raises(C.TemplateError, match="largest step"):
        C.resolve_settings(T["D7"], "15m", params={"start": 0.3, "max_step": 0.2})


# ============================================================================================================== E4
E4_L = "CrossAbove(StochK(14, 3), StochD(14, 3, 3)) AND StochK(14, 3)[1] < {lo}"
E4_S = "CrossBelow(StochK(14, 3), StochD(14, 3, 3)) AND StochK(14, 3)[1] > {hi}"


@pytest.mark.parametrize("seed", [1, 5])
def test_e4_fires_on_the_crosses_out_of_the_zones(seed):
    df = bars(seed)
    s = run(T["E4"], df, swing_degree=0)
    both_sides(expect(s, df, E4_L.format(lo=20), E4_S.format(hi=80), "SwingLow(0)", "SwingHigh(0)"))


def test_e4_must_not_fire_on_a_cross_in_the_middle_and_the_levels_are_settings():
    df = bars(1)
    anywhere = ev("CrossAbove(StochK(14, 3), StochD(14, 3, 3))", df).to_numpy()
    s = run(T["E4"], df, swing_degree=0)
    k_prev = ev("StochK(14, 3)[1]", df).to_numpy()
    middle = [int(t) for t in np.flatnonzero(anywhere & (k_prev >= 20)) if t >= s.warmup_bars]
    assert middle and not set(middle) & set(s.signal_bars())
    wide = run(T["E4"], df, swing_degree=0, oversold=40.0, overbought=60.0)
    expect(wide, df, E4_L.format(lo=40), E4_S.format(hi=60), "SwingLow(0)", "SwingHigh(0)")
    assert len(wide.signal_bars()) > len(s.signal_bars())


def test_e4_higher_timeframe_trend_filter():
    df = bars(1, 900)
    s = run(T["E4"], df, swing_degree=0, use_trend=True, context_timeframe="1h", trend_period=20)
    trend = ev('close@1h > MA(close@1h, 20, "EMA")@1h', df).to_numpy()
    plain = signals(run(T["E4"], df, swing_degree=0))
    assert signals(s) == {t: d for t, d in plain.items() if (trend[t] if d > 0 else not trend[t]) and t >= s.warmup_bars}
    with pytest.raises(C.TemplateError, match="higher than the signal"):
        C.resolve_settings(T["E4"], "1h", params={"use_trend": True, "context_timeframe": "1h"})


# ====================================================================================== shared gate: every new template
CASES = [("B2", {"swing_degree": 0, "k": 0.0}, 1), ("B2", {}, 6), ("B4", {}, 1), ("B4", {"invalidation": "stop_base"}, 4),
         ("B5", {}, 1), ("B5", {"invalidation": "swing", "swing_degree": 0}, 2), ("B6", {"pattern": "triangle", "k": 0.0}, 1),
         ("B7", {}, 2), ("B7", {"mode": "ttm", "exit_bars": 1}, 4), ("B8", {}, 1), ("B8", {"nr_setup": "inside"}, 3), ("B9", {}, 1), ("C5", {"retest_reversal": False}, 2),
         ("D4", {"ma_n": 20}, 1), ("D5", {"trigger_event": "adx_threshold", "swing_degree": 0}, 4),
         ("D5", {"adx_threshold": 10.0, "swing_degree": 0, "exit_rule": "adx_below"}, 13), ("D6", {}, 1),
         ("D6", {"zero_filter": True, "swing_degree": 0}, 2), ("D7", {}, 1), ("E4", {}, 1), ("E4", {"swing_degree": 0}, 5)]
IDS = [f"{tid}-{i}" for i, (tid, _, _) in enumerate(CASES)]


def whole(df):
    """The bars in whole ticks (x 20: the synthetic bars move in 0.05 steps), so the mirrored prices are exact and an equal
    move up and down (the directional movement's tie) stays a tie after the mirror."""
    out = df.copy()
    out[["open", "high", "low", "close"]] = (df[["open", "high", "low", "close"]] * 20).round()
    return out


@pytest.mark.parametrize("tid, params, seed", CASES, ids=IDS)
def test_mirror_short_equals_long_on_mirrored_prices(tid, params, seed):
    df = whole(bars(seed))
    # D4 / B7 read a percentage of the price (the average's slope, the band width): mirrored exactly around zero
    axis = 0.0 if tid in ("D4", "B7") else K * 20
    s, m = run(T[tid], df, **params), run(T[tid], mirror(df, axis), **params)
    assert signals(s), "the fixture must carry signals"
    assert signals(m) == {t: -d for t, d in signals(s).items()}
    for t in s.signal_bars():
        assert axis - m.invalidation[t] == pytest.approx(s.invalidation[t])
    assert (m.exit_short == s.exit_long).all() and (m.exit_long == s.exit_short).all()


@pytest.mark.parametrize("tid, params, seed", CASES, ids=IDS)
def test_truncation_no_look_ahead_over_many_t(tid, params, seed):
    df = bars(seed)
    full = run(T[tid], df, **params)
    marks = sorted(set(full.signal_bars()) | {t + 1 for t in full.signal_bars()} | set(range(full.warmup_bars, len(df), 53)))
    for t in [x for x in marks if x < len(df)]:
        part = run(T[tid], df.iloc[:t + 1], **params)
        np.testing.assert_array_equal(part.direction, full.direction[:t + 1], err_msg=f"t={t}")
        np.testing.assert_allclose(part.invalidation, full.invalidation[:t + 1], equal_nan=True, err_msg=f"t={t}")
        np.testing.assert_array_equal(part.exit_long, full.exit_long[:t + 1], err_msg=f"t={t}")
        np.testing.assert_array_equal(part.exit_short, full.exit_short[:t + 1], err_msg=f"t={t}")
        assert [part.instance_id(i) for i in part.signal_bars()] == [full.instance_id(i) for i in full.signal_bars() if i <= t]


PARITY = [("B2", {"swing_degree": 0, "k": 0.0}, 1), ("B4", {}, 1), ("B5", {}, 1), ("B6", {"pattern": "triangle", "k": 0.0}, 1),
          ("B7", {"mode": "ttm"}, 1), ("B8", {}, 1), ("B9", {}, 4), ("C5", {"retest_reversal": False}, 2), ("D4", {"ma_n": 20}, 1),
          ("D5", {"trigger_event": "adx_threshold", "swing_degree": 0}, 4), ("D6", {}, 1), ("D7", {}, 1), ("E4", {}, 1)]


@pytest.mark.parametrize("tid, params, seed", PARITY, ids=[p[0] for p in PARITY])
def test_scan_equals_backtest_equals_options_equals_worker(tid, params, seed):
    strat = TemplateStrategy(T[tid], C.resolve_settings(T[tid], "15m", params=params))
    window = synthetic_bars(900, tf="15m", seed=seed).iloc[-500:].copy()          # a fixed TRAILING window (R6)
    window[["open", "high", "low", "close"]] *= 240.0                                # index-like prices for the option ladder
    scan = parity.scan_answers(strat.recipe, window)
    assert sum(1 for a in scan.values() if a.direction) >= 2, "the fixture must carry signals to compare"
    answers, trades = parity.backtest_answers(strat, window, "S", RISK)
    assert parity.compare(scan, answers, "backtest") == []
    assert trades and parity.compare_trades(scan, trades, lambda ts: ts) == []
    config = OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, spread_width=2)
    assert parity.compare(scan, parity.options_answers(strat, window, "NIFTY 50", config, RISK), "options") == []
    signal_at = [i for i, a in enumerate(scan.values()) if a.direction]
    at = sorted({j for i in signal_at for j in (i - 1, i, i + 1) if 0 <= j < len(window)} | set(range(0, len(window), 25)) | {len(window) - 1})
    worker = parity.worker_answers(strat, window, "S", at)
    assert parity.compare(scan, worker, "worker") == []
    assert {ts for ts, a in scan.items() if a.direction} <= {ts for ts, a in worker.items() if a.direction}


def _base_events(tid, df, p):
    """The breakout / indicator event a template's signal must sit on, per side (no signal without its trigger)."""
    fmt = {k: (str(v).upper() if isinstance(v, bool) else repr(v) if isinstance(v, float) else v) for k, v in p.items()}
    base = {"B2": ('SwingBreakType("up", {swing_degree}, {k}) == "BOS"', 'SwingBreakType("down", {swing_degree}, {k}) == "BOS"'),
            "B4": ('TrendlineBreakout("up", "line", {touches}, {k})', 'TrendlineBreakout("down", "line", {touches}, {k})'),
            "B5": ('DonchianBreakout("up", {n}, {k})', 'DonchianBreakout("down", {n}, {k})'),
            "B8": ('NRBreakout("up", "{nr_setup}", {vol_mult}, {k})', 'NRBreakout("down", "{nr_setup}", {vol_mult}, {k})'),
            "D6": ("CrossAbove(MACD({fast}, {slow}, {signal}), MACDSignal({fast}, {slow}, {signal}))",
                   "CrossBelow(MACD({fast}, {slow}, {signal}), MACDSignal({fast}, {slow}, {signal}))"),
            "E4": ("CrossAbove(StochK({k_period}, {smooth}), StochD({k_period}, {smooth}, {d_period}))",
                   "CrossBelow(StochK({k_period}, {smooth}), StochD({k_period}, {smooth}, {d_period}))")}[tid]
    return [ev(x.format(**fmt), df).to_numpy(dtype=bool) for x in base]


def _check_property(tid, df, s, p):
    lo, sh = _base_events(tid, df, p)
    for t, d in signals(s).items():
        assert (lo if d > 0 else sh)[t], (tid, t)                                   # no signal without its trigger
        assert (s.invalidation[t] < df["close"].iloc[t]) if d > 0 else (s.invalidation[t] > df["close"].iloc[t])   # S6


@hsettings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(degree=st.integers(0, 1), k=st.floats(0.0, 1.0), inval=st.sampled_from(["swing", "stop_level_atr"]), mult=st.floats(0.0, 3.0),
       seed=st.integers(0, 30))
def test_b2_property_random_valid_settings(degree, k, inval, mult, seed):
    df = bars(seed, 400)
    p = {"swing_degree": degree, "k": k, "invalidation": inval, "stop_mult": mult}
    _check_property("B2", df, run(T["B2"], df, **p), p)


@hsettings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(touches=st.integers(2, 4), k=st.floats(0.0, 1.0), inval=st.sampled_from(T["B4"].param("invalidation").choices),
       mult=st.floats(0.0, 3.0), seed=st.integers(0, 30))
def test_b4_property_random_valid_settings(touches, k, inval, mult, seed):
    df = bars(seed, 400)
    p = {"touches": touches, "k": k, "invalidation": inval, "stop_mult": mult}
    _check_property("B4", df, run(T["B4"], df, **p), p)


@hsettings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(n=st.integers(5, 60), k=st.floats(0.0, 1.0), inval=st.sampled_from(T["B5"].param("invalidation").choices), degree=st.integers(0, 1),
       exit_n=st.integers(2, 40), seed=st.integers(0, 30))
def test_b5_property_random_valid_settings(n, k, inval, degree, exit_n, seed):
    df = bars(seed, 400)
    p = {"n": n, "k": k, "invalidation": inval, "swing_degree": degree, "exit_n": exit_n}
    _check_property("B5", df, run(T["B5"], df, **p), p)


@hsettings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(setup=st.sampled_from(["inside", "nr4", "nr7"]), v=st.floats(0.0, 3.0), k=st.floats(0.0, 1.0), seed=st.integers(0, 30))
def test_b8_property_random_valid_settings(setup, v, k, seed):
    df = bars(seed, 400)
    p = {"nr_setup": setup, "vol_mult": v, "k": k}
    _check_property("B8", df, run(T["B8"], df, **p), p)


@hsettings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(fast=st.integers(2, 20), gap=st.integers(1, 30), sig=st.integers(2, 20), zero=st.booleans(), degree=st.integers(0, 1),
       seed=st.integers(0, 30))
def test_d6_property_random_valid_settings(fast, gap, sig, zero, degree, seed):
    df = bars(seed, 400)
    p = {"fast": fast, "slow": fast + gap, "signal": sig, "zero_filter": zero, "swing_degree": degree}
    _check_property("D6", df, run(T["D6"], df, **p), p)


@hsettings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(n=st.integers(3, 40), smooth=st.integers(1, 5), d=st.integers(1, 5), lo=st.floats(5.0, 45.0), hi=st.floats(55.0, 95.0),
       degree=st.integers(0, 1), seed=st.integers(0, 30))
def test_e4_property_random_valid_settings(n, smooth, d, lo, hi, degree, seed):
    df = bars(seed, 400)
    p = {"k_period": n, "smooth": smooth, "d_period": d, "oversold": lo, "overbought": hi, "swing_degree": degree}
    _check_property("E4", df, run(T["E4"], df, **p), p)


@hsettings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(period=st.integers(5, 30), thr=st.floats(5.0, 40.0), rise=st.integers(1, 5), event=st.sampled_from(["di_cross", "adx_threshold"]),
       exit_rule=st.sampled_from(["opposite_cross", "adx_below"]), seed=st.integers(0, 30))
def test_d5_d7_d4_c5_b6_b9_property_random_valid_settings(period, thr, rise, event, exit_rule, seed):
    df = bars(seed, 400)
    runs = [run(T["D5"], df, adx_period=period, adx_threshold=thr, rise_bars=rise, trigger_event=event, exit_rule=exit_rule, swing_degree=0),
            run(T["D7"], df, start=0.01 * (1 + rise), step=0.01, max_step=0.2), run(T["D4"], df, ma_n=period, stop_mult=thr / 20),
            run(T["C5"], df, n=period, retest_max_bars=rise * 3, retest_reversal=False),
            run(T["B6"], df, pattern="triangle", k=0.0, stop_mult=thr / 20), run(T["B9"], df, range_n=period, vol_mult=1 + rise / 2)]
    for s in runs:
        for t, d in signals(s).items():
            assert (s.invalidation[t] < df["close"].iloc[t]) if d > 0 else (s.invalidation[t] > df["close"].iloc[t])


@pytest.mark.parametrize("tid", NEW)
def test_library_template_validates_and_its_text_passes_the_guard(tid):
    template = T[tid]
    assert C.validate_template(template) == []
    for tf in template.allowed_timeframes:
        settings = C.resolve_settings(template, tf)
        for side in ("long", "short"):
            for ctx in (None, "1d", "1w"):
                text = library.signal_text(template, settings, side, ctx)
                assert text and language.guard(text) == [], text
    for p in template.params:
        assert language.guard(p.label) == [] or p.type in ("bool", "int", "float"), p.key
        if p.type in ("int", "float"):
            assert p.min is not None and p.max is not None and p.min <= p.default <= p.max
        if p.choices and p.type != "timeframe":
            assert len(p.choice_labels) == len(p.choices)
    if template.family in "BC":
        assert template.difficulty in ("Beginner", "Intermediate", "Advanced")


# The only literals a rule may hold besides the side / choice words: a direction value (+1 / -1), the zero line and the bar
# before ([1]) - structure, not settings. Every other number is a $setting.
_STRUCTURE = re.compile(r"(== -?1\b)|(\[1\])|([<>] 0\b)")


def _texts(template):
    out = []
    for part in (template.setup, template.invalidation, template.exit_signal, template.context):
        if part is None:
            continue
        variants = [v for v in part.variants.values() if v] if hasattr(part, "variants") else [part]
        out += [x for v in variants for x in (v.long, v.short)]
    return out


@pytest.mark.parametrize("tid", NEW)
def test_no_number_is_written_into_a_template_rule(tid):
    for text in _texts(T[tid]):
        assert not re.search(r"(?<![\w$])\d", _STRUCTURE.sub("", text)), (tid, text)


@pytest.mark.parametrize("tid", NEW)
def test_template_ids_round_trip_and_fit(tid):
    template = T[tid]
    for tf in [t for t in template.allowed_timeframes if t in ENGINE_LABEL]:
        sid, settings = ids.adopt(template, tf)
        assert ids.decode(sid) == (template, settings)
    worst = {}
    for p in template.params:
        if p.type == "int":
            worst[p.key] = int(p.max) if p.max != p.default else int(p.min)
        elif p.type == "bool":
            worst[p.key] = not p.default
        elif p.type != "float":
            worst[p.key] = max((c for c in p.choices if c != p.default), key=len, default=p.default)
    worst = {k: v for k, v in worst.items() if k not in ("fast", "start")}           # keep the order constraints
    if "use_trend" in worst:
        worst["context_timeframe"] = "1w"
    sid, settings = ids.adopt(template, template.allowed_timeframes[0], "short", worst)
    assert len(sid) <= ids.MAX_LENGTH and ids.decode(sid) == (template, settings)    # every choice and whole number fits


@pytest.mark.parametrize("tid", NEW)
def test_the_adapter_reads_the_signal_with_its_provenance_and_the_flags(tid):
    tf = "1d" if tid == "B11" else "15m"
    df = vcp() if tid == "B11" else bars(1)
    params = {"B2": {"swing_degree": 0, "k": 0.0}, "B6": {"pattern": "triangle", "k": 0.0}, "C5": {"retest_reversal": False},
              "D5": {"trigger_event": "adx_threshold", "swing_degree": 0}}.get(tid, {})
    strat = TemplateStrategy(T[tid], C.resolve_settings(T[tid], tf, params=params))
    series = engine.compute(strat.recipe, {tf: df})
    t = series.signal_bars()[0]
    sig = strat.analyze({ENGINE_LABEL[tf]: df.iloc[:t + 1]}, "S")
    assert sig.is_tradeable and sig.stop_loss == pytest.approx(series.invalidation[t]) and sig.target1 is None
    assert sig.provenance["template"] == tid and sig.provenance["setup"] == series.instance_id(t)
    assert all(language.guard(r) == [] for r in sig.reasons)
    want = {"B2": {"screener_breakouts"}, "B4": {"screener_breakouts", "screener_breakouts_bk4"},
            "B5": {"screener_breakouts", "screener_breakouts_bk3"}, "B6": {"screener_breakouts", "screener_breakouts_bk4"},
            "B7": {"screener_breakouts", "screener_breakouts_bk3"}, "B8": {"screener_breakouts"}, "B9": {"screener_breakouts"}, "B11": {"screener_breakouts", "screener_breakouts_bk3"},
            "C5": {"screener_breakouts", "screener_breakouts_bk4"}, "D4": {"screener_breakouts", "screener_breakouts_bk3"},
            "D5": set(), "D6": set(), "D7": set(), "E4": set()}[tid]                        # the indicators need no flag
    assert strat.recipe.required_flags == want


def test_every_new_template_is_paper_only_signals_only():
    """No template field can carry a target, a size, a strike, a premium or an order (section 5): the strict schema has none,
    and no rule names one."""
    for tid in NEW:
        data = T[tid].model_dump(by_alias=True)
        assert not {"target", "targets", "lots", "quantity", "strike", "premium", "order"} & set(data)
        for text in _texts(T[tid]):
            assert not re.search(r"\b(target|lots|strike|premium|order)\b", text, re.I)
    assert pd.Index(NEW).is_unique
