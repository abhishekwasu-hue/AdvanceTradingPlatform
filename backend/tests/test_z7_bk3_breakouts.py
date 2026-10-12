"""Z7 / U6 BK3: B3 VCP (+ Watchlist) and the indicator breakouts B10 Donchian, B11 Bollinger / TTM squeeze, B12 moving
average and B15 RSI / ADX regime (docs/specs/ATP_BREAKOUT_SCREENERS_ADDENDUM.md §8 BK3, §7 tests).

Synthetic fixtures per screen (tests/breakout_bk3_fixtures.py: straight legs, no randomness): a VCP with three
contractions and its failing variants (too few, not contracting, no dry-up, no trend template, a wick, a weak-volume
break), the Watchlist state before the break; a Donchian break and its mirror; a squeeze with and without volume; a
moving-average break after a flat base against one under a falling line; a regime break out of an RSI / ADX compression.
Then the closed-candle / no-look-ahead sweep (appending bars never changes an earlier value), one signal per leg, grade
determinism, and parity between each template's ScreenQL text and the engine, bar by bar."""
import numpy as np
import pandas as pd
import pytest

from app.screener import compile_screen
from app.screener.breakouts import indicator_breaks as ib
from app.screener.breakouts import params as bp
from app.screener.breakouts import templates
from app.screener.breakouts import vcp as V
from app.screener.breakouts.quality import run_breakout
from app.screener.breakouts.screens import BK3_SCREENS, SCREEN_TIMEFRAMES, Bars, evaluate_screen
from app.screener.registry import BREAKOUT_BK3_FLAG, BREAKOUT_BK3_FUNCTIONS, FUNCTIONS
from app.screener.runtime import SymbolData, evaluate, resample, run_screen
from tests.breakout_bk3_fixtures import frame, path, vcp
from tests.sample_market import generate
from tests.test_z7_bk1_breakouts import _engine_series


def _sig(f, screen, side="up", **params):
    return list(np.flatnonzero(evaluate_screen(f, screen, bp.settings(**params))[side]["signal"].to_numpy()))


# ----------------------------------------------------------------------------------------------------------- registry
def test_the_bk3_functions_take_the_parameter_defaults_and_need_both_flags():
    want = {"VCPBreakout": {"contractions": "vcp_min_contractions", "ratio": "vcp_ratio", "v": "vol_mult", "k": "min_break_atr",
                            "template": "tt_filter", "dryup": "vcp_vol_dryup"},
            "VCPReady": {"contractions": "vcp_min_contractions", "ratio": "vcp_ratio", "pct": "vcp_ready_pct", "template": "tt_filter",
                         "dryup": "vcp_vol_dryup"},
            "DonchianBreakout": {"n": "donchian_n", "k": "min_break_atr"},
            "SqueezeBreakout": {"mode": "squeeze_mode", "n": "bb_n", "lookback": "squeeze_lookback", "v": "vol_mult"},
            "MABreakout": {"n": "ma_n", "type": "ma_type", "k": "min_break_atr"},
            "RegimeBreakout": {"band": "rsi_band", "n": "regime_n", "adx": "adx_low", "k": "min_break_atr"}}
    assert set(want) == set(BREAKOUT_BK3_FUNCTIONS)
    for fn, args in want.items():
        spec = FUNCTIONS[fn]
        assert spec.flag == "screener_breakouts" and spec.also_flags == (BREAKOUT_BK3_FLAG,)
        by = {a.name: a for a in spec.args}
        for arg, param in args.items():
            assert by[arg].default == bp.DEFAULTS[param], (fn, arg)
    assert FUNCTIONS["VCPBreakout"].timeframes == ("1d",) == SCREEN_TIMEFRAMES["B3"]
    assert set(BK3_SCREENS) == {"B3", "B10", "B11", "B12", "B15"} == set(templates.BK3_TEMPLATE_SCREENS)


def test_new_functions_validate_with_choices_ranges_and_history():
    ok = lambda text, tf="1d": compile_screen(text, base_tf=tf)[1]  # noqa: E731
    assert ok('VCPBreakout("up")').ok and ok('VCPReady("up", 3, 0.5, 5, FALSE, FALSE)').ok
    assert not ok('VCPBreakout("up")', "15m").ok                                    # a daily rule
    assert "contractions must be between 2 and 8" in ok('VCPBreakout("up", 1)').problems[0].message
    assert not ok('SqueezeBreakout("up", "keltner")').ok and not ok('MABreakout("up", 50, "wma")').ok
    assert ok('DonchianBreakout("down", 55)', "15m").ok and ok('RegimeBreakout("up")', "5m").ok
    assert compile_screen('VCPBreakout("up")', base_tf="1d")[1].lookback["1d"] >= 251               # trend template: a year of sessions
    assert compile_screen('SqueezeBreakout("up")', base_tf="1d")[1].lookback["1d"] >= 145           # band width + its six-month low
    assert compile_screen('MABreakout("up", 100)', base_tf="1d")[1].lookback["1d"] >= 111           # the line + its slope / base window
    assert compile_screen('RegimeBreakout("up", 10, 60)', base_tf="1d")[1].lookback["1d"] >= 90     # the window + the ADX warm-up


def test_bad_bk3_parameters_fall_back():
    clean, errors = bp.validate({"tt_ma_fast": 200, "vcp_min_bars": 300, "vcp_max_bars": 100, "squeeze_mode": "keltner"})
    assert clean["tt_ma_fast"] == 50 and clean["vcp_min_bars"] == 15 and clean["squeeze_mode"] == "bollinger" and len(errors) == 3
    _, errors = bp.validate({"ma_stack": True, "ma_stack_fast": 60})
    assert errors and "stack" in errors[0]


# ------------------------------------------------------------------------------------------------------------------ B3
def test_vcp_breakout_with_three_contractions_volume_dry_up_and_the_trend_template():
    f = vcp()
    out = evaluate_screen(f, "B3", bp.settings())["up"]
    assert list(np.flatnonzero(out["signal"].to_numpy())) == [len(f) - 1]
    row = out.iloc[-1]
    assert row["event"] == "VCP3" and row["level"] == pytest.approx(f["high"].iloc[-12:-1].max())     # the pivot: the last high
    assert row["base_low"] < row["level"] and row["base_len"] > 15
    run = run_breakout("B3", {"1d": f}, "1d", within=1)
    assert len(run.signals) == 1 and run.signals[0]["htf_pass"] and run.signals[0]["status"] == "pending"
    piv = V.zigzag(*(lambda s, b: (s.h, s.l, b.atr_prev))(Bars(f, bp.settings()).side(1), Bars(f, bp.settings())))
    base = V.base_at(piv, len(piv), len(f) - 1, bp.settings())
    depths = [d for _, _, d in base.contractions] if base is not None else []
    assert len(depths) == 3 and depths[0] > depths[1] > depths[2] and depths[0] < 35


@pytest.mark.parametrize("kwargs,params", [
    ({"contractions": (16.0,)}, {}),                                           # one contraction: not a VCP
    ({}, {"vcp_min_contractions": 4}),                                         # fewer than asked
    ({}, {"vcp_ratio": 0.4}),                                                  # 9 % after 16 % is not below 0.4 x
    ({"contractions": (16.0, 4.0, 8.0)}, {}),                                  # the last one is not shallower
    ({"contractions": (40.0, 20.0, 10.0)}, {"vcp_max_bars": 250}),             # the base's left side too deep
    ({"dry": False}, {}),                                                      # volume rising through the base
    ({"trend": False}, {}),                                                    # a base after a sideways year: no trend template
    ({"wick_only": True}, {}),                                                 # a wick above the pivot, the close under it
    ({"break_volume": 500.0}, {}),                                             # a break on ordinary volume
])
def test_vcp_needs_every_rule(kwargs, params):
    assert _sig(vcp(**kwargs), "B3", **params) == []


def test_vcp_rules_can_be_turned_off():
    assert _sig(vcp(dry=False), "B3", vcp_vol_dryup=False) == [len(vcp()) - 1]
    f = vcp(trend=False)
    assert _sig(f, "B3", tt_filter=False) == [len(f) - 1]
    assert _sig(vcp(break_volume=500.0), "B3", vol_mult=0.0) == [len(vcp()) - 1]


def test_vcp_watchlist_lists_ready_bases_until_the_break():
    f = vcp()
    w = evaluate_screen(f, "B3", bp.settings(vcp_mode="watchlist"))["up"]
    ready = list(np.flatnonzero(w["signal"].to_numpy()))
    assert ready and ready[-1] == len(f) - 2                                   # every bar ready up to the one before the break
    assert all(w["raw"].iloc[j] and w["level"].iloc[j] >= f["close"].iloc[j] for j in ready)
    tail = [j for j in ready if j > len(f) - 8]
    assert tail == list(range(tail[0], len(f) - 1))                            # a state: not deduplicated per leg
    run = run_breakout("B3", {"1d": f.iloc[:-1]}, "1d", params={"vcp_mode": "watchlist"}, within=1)
    assert len(run.signals) == 1 and run.signals[0]["status"] == "ready"
    assert evaluate_screen(f, "B3", bp.settings(vcp_mode="watchlist", vcp_ready_pct=0.1))["up"]["signal"].iloc[-2] == False  # noqa: E712


def test_vcp_down_side_is_the_mirror():
    f = vcp()
    m = f.copy()
    m["high"], m["low"] = 300.0 - f["low"], 300.0 - f["high"]
    m["open"], m["close"] = 300.0 - f["open"], 300.0 - f["close"]
    up = evaluate_screen(f, "B3", bp.settings())["up"]
    down = evaluate_screen(m, "B3", bp.settings())["down"]
    assert list(up["signal"]) == list(down["signal"])
    assert down["level"].iloc[-1] == pytest.approx(300.0 - up["level"].iloc[-1])


# ----------------------------------------------------------------------------------------------------------------- B10
def test_donchian_break_and_its_mirror():
    jump = frame([100.0, 100.4] * 30 + [103.0])
    assert _sig(jump, "B10") == [len(jump) - 1]
    assert _sig(jump, "B10", donchian_n=55) == [len(jump) - 1]
    assert _sig(jump.iloc[:-1], "B10") == []
    drop = frame([100.0, 100.4] * 30 + [97.0])
    assert _sig(drop, "B10", "down") == [len(drop) - 1] and _sig(drop, "B10") == []
    out = evaluate_screen(jump, "B10", bp.settings())["up"]
    assert out["level"].iloc[-1] == pytest.approx(100.9) and out["base_len"].iloc[-1] == 20
    short = frame([100.0, 100.4] * 10 + [103.0])                               # 20 prior bars: no 55-bar channel yet
    assert _sig(short, "B10", donchian_n=55) == []


def test_donchian_one_signal_per_leg():
    run = path(100.0, [(40, 0.0)]) + path(103.0, [(6, 6.0)])
    f = frame([c + (0.2 if i % 2 else 0.0) for i, c in enumerate(run)])
    out = evaluate_screen(f, "B10", bp.settings(min_break_atr=0.0))["up"]
    assert out["raw"].iloc[-6:].all() and int(out["signal"].iloc[-8:].sum()) == 1


# ----------------------------------------------------------------------------------------------------------------- B11
def _squeeze(break_volume=3000.0):
    wide = [100.0 + (1.0 if i % 2 else -1.0) for i in range(150)]
    narrow = [100.0 + (0.2 if i % 2 else -0.2) for i in range(10)]
    vols = [1000.0] * 160 + [break_volume]
    return frame(wide + narrow + [102.5], vols, half=0.3)


def test_bollinger_squeeze_then_a_close_outside_the_band_on_volume():
    f = _squeeze()
    out = evaluate_screen(f, "B11", bp.settings())["up"]
    assert list(np.flatnonzero(out["signal"].to_numpy())) == [len(f) - 1]
    mid, up, _ = ib.bollinger(f["close"].to_numpy(float), 20, 2.0)
    assert out["level"].iloc[-1] == pytest.approx(up[-1]) and out["fail_level"].iloc[-1] == pytest.approx(mid[-1])
    assert out["event"].iloc[-1] == "BB_SQUEEZE" and out["base_len"].iloc[-1] == 1
    assert _sig(_squeeze(800.0), "B11") == []                                 # no volume: not the spec's expansion
    assert _sig(_squeeze(800.0), "B11", vol_mult=0.0) == [len(f) - 1]
    assert _sig(_squeeze(), "B11", squeeze_lookback=10) == [len(f) - 1]       # the narrow bars are also a 10-bar low
    assert _sig(_squeeze(), "B11", squeeze_within=1, squeeze_lookback=300) == []   # too little history for a 300-bar low


def test_ttm_fire_is_the_release_with_momentum_on_the_side():
    m15 = resample(generate(375 * 30, 100.0, 21), "1m", "15m")
    p = bp.settings(squeeze_mode="ttm")
    b = Bars(m15, p)
    s = b.side(1)
    _, up, lo = ib.bollinger(s.c, 20, 2.0)
    ema = pd.Series(s.c).ewm(span=20, adjust=False, min_periods=20).mean().to_numpy(float)
    from app.indicators.volatility import atr
    width = 1.5 * atr(m15[["open", "high", "low", "close"]].astype(float), 20).to_numpy(float)
    with np.errstate(invalid="ignore"):
        on = (up < ema + width) & (lo > ema - width)
    mom = ib.ttm_momentum(s, 20)
    out = evaluate_screen(m15, "B11", p)["up"]
    fired = list(np.flatnonzero(out["raw"].to_numpy()))
    assert fired
    for j in fired:
        assert on[j - 1] and not on[j] and mom[j] > 0 and out["event"].iloc[j] == "TTM_FIRE"


def test_linear_regression_value_is_the_least_squares_end_point():
    x = np.array([1.0, 3.0, 2.0, 5.0, 4.0, 6.0, 9.0])
    got = ib.linreg_value(x, 4)
    for j in range(3, len(x)):
        slope, icpt = np.polyfit(np.arange(4), x[j - 3:j + 1], 1)
        assert got[j] == pytest.approx(icpt + slope * 3)
    assert np.isnan(got[:3]).all()


# ----------------------------------------------------------------------------------------------------------------- B12
def test_ma_break_after_a_flat_base_around_the_line():
    quiet = [100.0 + 0.1 * np.sin(i * 0.9) + 0.05 * np.sin(i * 2.3) for i in range(70)]
    f = frame(quiet + [101.6], half=0.4)
    assert _sig(f, "B12") == [len(f) - 1] == _sig(f, "B12", ma_type="ema")
    out = evaluate_screen(f, "B12", bp.settings())["up"]
    assert out["level"].iloc[-1] == pytest.approx(f["close"].iloc[-50:].mean()) and out["base_len"].iloc[-1] == 10
    falling = frame(path(130.0, [(70, -30.0)]) + [99.0, 102.0], half=0.4)        # under a falling 50-day line
    assert _sig(falling, "B12") == [] and _sig(falling, "B12", ma_flat_pct=10.0) == []
    v = frame(path(120.0, [(60, 0.0), (10, -15.0)]) + [121.5], half=0.4)        # the closes before were far below the line
    assert _sig(v, "B12", ma_flat_pct=10.0) == [] and _sig(v, "B12", ma_flat_pct=10.0, ma_band_atr=10.0) == [len(v) - 1]
    chop = frame([100.0 + 0.4 * np.sin(i * 0.9) for i in range(70)] + [100.1], half=0.4)   # inside the base: not above its closes
    assert _sig(chop, "B12")[-1:] != [len(chop) - 1]


# ----------------------------------------------------------------------------------------------------------------- B15
FLAT = [100.0 + 0.4 * np.sin(i * 0.9) + 0.2 * np.sin(i * 2.3) for i in range(80)]


def test_regime_break_out_of_an_rsi_and_adx_compression():
    f = frame(FLAT + [102.0], half=0.4)
    assert _sig(f, "B15") == [len(f) - 1]
    out = evaluate_screen(f, "B15", bp.settings())["up"]
    assert out["level"].iloc[-1] == pytest.approx(f["high"].iloc[-21:-1].max()) and out["base_len"].iloc[-1] == 20
    trend = frame(path(60.0, [(80, 40.0)]) + [102.5], half=0.4)                # a trend: RSI was never compressed
    assert _sig(trend, "B15") == []
    assert _sig(frame(FLAT + [101.0], half=0.4), "B15") == []                  # inside the window's high + 0.2 ATR
    assert _sig(f, "B15", adx_low=5.0) == []                                   # ADX never that low in the window
    m = frame([200.0 - c for c in FLAT] + [98.0], half=0.4)
    assert _sig(m, "B15", "down") == [len(m) - 1] and _sig(m, "B15") == []


# ---------------------------------------------------------------------------------------- closed candles, no look-ahead
@pytest.fixture(scope="module")
def daily():
    return generate(600, 100.0, 3, daily=True)


@pytest.fixture(scope="module")
def m15():
    return resample(generate(375 * 30, 100.0, 21), "1m", "15m")


@pytest.mark.parametrize("screen,params", [
    ("B3", {"tt_filter": False, "vol_mult": 0.0}), ("B3", {"tt_filter": False, "vcp_mode": "watchlist"}), ("B10", {}), ("B10", {"donchian_n": 55}),
    ("B11", {"vol_mult": 0.0}), ("B11", {"squeeze_mode": "ttm"}), ("B12", {}), ("B12", {"ma_type": "ema", "ma_n": 20}), ("B15", {}),
])
def test_no_value_changes_when_later_bars_arrive(daily, m15, screen, params):
    p = bp.settings(**params)
    for f, cuts in ((daily, (300, 420, 510)), (m15, (400, 550, 700))):
        full = evaluate_screen(f, screen, p)
        assert any(full[side]["raw"].any() for side in ("up", "down")) or screen == "B3"
        for cut in cuts:
            part = evaluate_screen(f.iloc[:cut], screen, p)
            for side in ("up", "down"):
                for col in ("raw", "signal", "level", "fail_level", "base_high", "base_low"):
                    pd.testing.assert_series_equal(part[side][col], full[side][col].iloc[:cut], check_names=False)


def test_the_vcp_signal_is_the_same_on_the_fixture_cut_at_any_later_bar():
    f = vcp()
    longer = frame(list(f["close"]) + [f["close"].iloc[-1] + i for i in range(1, 6)], list(f["volume"]) + [3000.0] * 5)
    longer.iloc[:len(f)] = f.to_numpy()
    a = evaluate_screen(f, "B3", bp.settings())["up"]
    b = evaluate_screen(longer, "B3", bp.settings())["up"].iloc[:len(f)]
    assert list(a["signal"]) == list(b["signal"]) and a["signal"].iloc[-1]


def test_grade_is_deterministic_and_fixed_at_the_signal_bar():
    f = vcp()
    longer = frame(list(f["close"]) + [f["close"].iloc[-1] + 0.5 * i for i in range(1, 6)], list(f["volume"]) + [900.0] * 5)
    longer.iloc[:len(f)] = f.to_numpy()
    a = run_breakout("B3", {"1d": f}, "1d", within=None).signals
    assert a == run_breakout("B3", {"1d": f}, "1d", within=None).signals
    c = [s for s in run_breakout("B3", {"1d": longer}, "1d", within=None).signals if s["bar"] == a[-1]["bar"]][0]
    for key in ("grade", "points", "why", "checks", "htf", "level", "rvol", "dist_atr", "event"):
        assert c[key] == a[-1][key], key


# ------------------------------------------------------------------------------------------- ScreenQL text <-> engine
BK3_TEMPLATES = [t for t in templates.TEMPLATES if t.screen in templates.BK3_TEMPLATE_SCREENS]


@pytest.mark.parametrize("tid", [t.id for t in BK3_TEMPLATES])
def test_template_text_matches_the_engine_bar_by_bar(tid):
    t = templates.BY_ID[tid]
    # the generator's daily volume never reaches 1.5x its average: the daily squeeze sweep asks for 1x (still a ScreenQL argument)
    params = templates.template_params(t, {"vol_mult": 1.0} if tid == "daily_bb_squeeze" else None)
    if t.signal_tf == "1d":
        f = vcp() if t.screen == "B3" else generate(600, 100.0, 5, daily=True)
    else:
        f = resample(generate(375 * 60, 100.0, 21), "1m", t.signal_tf)
    text, why = templates.screenql(t.screen, t.signal_tf, params)
    assert why is None and text is not None
    ast, validated = compile_screen(text, base_tf=t.signal_tf)
    assert validated.ok, validated.problems
    got = evaluate(ast, SymbolData("S", {t.signal_tf: f}), base_tf=t.signal_tf).fillna(False).astype(bool).to_numpy()
    want = _engine_series(t.screen, f, t.signal_tf, params)
    assert (got == want).all()
    assert want.sum() > 0, tid                                                  # not vacuous
    cut = int(np.flatnonzero(want)[-1]) + 1
    match = run_screen(ast, validated, [SymbolData("S", {t.signal_tf: f.iloc[:cut]})], base_tf=t.signal_tf, check_history=False)[0]
    assert match.matched


def test_the_watchlist_template_is_its_own_function_and_hides_unused_settings():
    text, why = templates.screenql("B3", "1d", {"vcp_mode": "watchlist", "direction": "up", "vol_mult": 3.0})
    assert why is None and text.startswith('VCPReady("up", 2, 1, 5, TRUE, TRUE)')
    text, why = templates.screenql("B3", "1d", {"direction": "up", "vcp_pivot_atr": 2.0})
    assert text is None and "vcp_pivot_atr" in why
    assert templates.screenql("B11", "15m", {"squeeze_mode": "ttm", "kc_mult": 2.0})[0] is None


def test_vcp_and_regime_templates_match_on_the_alert_fetch_window():
    """tests/test_z7_bk1_breakouts.py::test_every_template_can_match_on_the_alert_fetch_window leaves these out (a VCP and an
    RSI / ADX compression do not occur in a random walk); here each is injected into a 272-session daily history - the
    broker's daily fetch - and the template's ScreenQL matches on it with check_history (never 'not enough history')."""
    regime = frame(path(30.0, [(20, 30.0), (12, -18.0)] * 5 + [(20, 30.0)])[:-1] + [c + 30.0 for c in FLAT] + [132.0], half=0.4)
    for tid, f in (("daily_vcp", vcp()), ("daily_vcp_watchlist", vcp().iloc[:-1]), ("daily_rsi_adx_regime", regime)):
        t = templates.BY_ID[tid]
        text, _ = templates.screenql(t.screen, t.signal_tf, templates.template_params(t))
        ast, validated = compile_screen(text, base_tf="1d")
        assert len(f) <= 272 and validated.lookback["1d"] <= len(f), (tid, validated.lookback)
        m = run_screen(ast, validated, [SymbolData("S", {"1d": f})], base_tf="1d", check_history=True)[0]
        assert m.reason is None and m.matched, (tid, m.reason)


def test_catalog_with_and_without_bk3():
    on, off = templates.catalog(True), templates.catalog(False)
    on_ids, off_ids = {t["id"] for t in on["templates"]}, {t["id"] for t in off["templates"]}
    assert {t.id for t in BK3_TEMPLATES} <= on_ids and not ({t.id for t in BK3_TEMPLATES} & off_ids)
    assert {b["screen"] for b in on["blocks"]["breakout"]} >= set(BK3_SCREENS)
    assert not ({b["screen"] for b in off["blocks"]["breakout"]} & set(BK3_SCREENS))
    assert {p["name"] for p in on["params"]} == set(bp.DEFAULTS)
    off_params = {p["name"] for p in off["params"]}
    assert "vcp_ratio" not in off_params and "bb_n" not in off_params and "vol_mult" in off_params and "min_break_atr" in off_params
    b3 = [b for b in on["blocks"]["breakout"] if b["screen"] == "B3"][0]
    assert b3["function"] == "VCPBreakout" and b3["watchlist_function"] == "VCPReady"


# ------------------------------------------------------------------------------------------- the shared indicator math
def test_the_engine_indicators_equal_the_platform_indicators(m15):
    """The breakout engine computes ATR / ADX on one numpy true range (performance); the values are the platform's."""
    from app.indicators.directional import adx
    from app.indicators.momentum import rsi
    from app.indicators.volatility import atr
    f = m15.copy()
    f.iloc[5, f.columns.get_loc("high")] = np.nan                               # a hole: skipped as pandas skips it
    b = Bars(f, bp.settings())
    np.testing.assert_array_equal(b.atr, atr(f[["open", "high", "low", "close"]].astype(float), 14).to_numpy(float))
    ref = adx(f, 14)
    for side, (plus, minus) in (("up", ("plus_di", "minus_di")), ("down", ("minus_di", "plus_di"))):
        a, p_, m_ = ib.adx(b, b.side(1 if side == "up" else -1), 14)
        np.testing.assert_allclose(a, ref["adx"].to_numpy(float), rtol=1e-12, equal_nan=True)
        np.testing.assert_allclose(p_, ref[plus].to_numpy(float), rtol=1e-12, equal_nan=True)
        np.testing.assert_allclose(m_, ref[minus].to_numpy(float), rtol=1e-12, equal_nan=True)
    np.testing.assert_allclose(ib.rsi(b.c, 14), rsi(f["close"], 14).to_numpy(float), equal_nan=True)
    np.testing.assert_allclose(ib.rsi(-b.c, 14), 100.0 - rsi(f["close"], 14).to_numpy(float), equal_nan=True, atol=1e-9)


def test_the_engine_swings_are_the_causal_swing_engine_s(m15):
    """Bars.pivots builds the zigzag on its own arrays (performance); the swings are causal_swings.degree_pivots'."""
    from app.price_action import causal_swings as cs
    from app.price_action import pa_settings
    s = pa_settings.settings()
    frame = pd.DataFrame({k: m15[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
    frame["timestamp"] = frame["bar_end"] = m15.index
    b = Bars(m15, bp.settings())
    for degree in range(4):
        want = [(p.kind, p.price, p.bar_idx, p.confirmed_idx) for p in cs.degree_pivots(frame, degree, s)]
        assert [(p.kind, p.price, p.bar_idx, p.confirmed_idx) for p in b.pivots(degree)] == want
        assert want or degree == 3
