"""Wave templates (family G) build step 7, second part (docs/design/WAVE_TEMPLATES_REVIEW.md section 7): G4 (wave C on the
break of wave B's channel), G5 (the thrust out of a triangle), G6 (wave C's close beyond the end of wave A), G7 (the
expanded-flat sweep: wave B beyond the start of wave A and back, WB5 / WAVE-8), G2 (wave five after wave four), G9 (the end
of the fifth wave), G10 (the ending diagonal's line) and G11 (wave Y after wave X of a double three).

The same gates as the first templates (tests/test_wave4_templates.py), on public synthetic wave paths pinned here: must-fire
per trigger style, must-not (the wrong count type, the higher trend, the clarity, the Fibonacci zone, the window), mirror,
truncation over many t (fixtures and random-walk bars), scan = backtest = options backtest = worker through the G parity
harness, no ratio or other number literal in a recipe (only the `Lag(x, 1)` offset and the zero of `Count(...) > 0`),
every number a bounded setting, the language guard, the exit signals per WB9 (never on the signal bar; not acted on until
R5), the strategy id within its length, and the flags. Signals only, PAPER only; no projection target (section 5).
"""
from __future__ import annotations

import re

import numpy as np
import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hsettings
from hypothesis import strategies as st

from app.backtest.options_engine import OptionBacktestConfig
from app.core.enums import OptionStrategy
from app.quality.lookahead import synthetic_bars
from app.screener import nodes as n
from app.screener.parser import parse
from app.strategy_templates import compile as C
from app.strategy_templates import engine, ids, language, library, wave_parity
from app.strategy_templates.adapter import TemplateStrategy
from tests.test_wave2_counter import _mirror_bars
from tests.test_wave4_templates import LEAD, PIVOT, RISK, _higher_frame, bars_of, ev, expressions, part_series, recipe, run, signals

NEW = ("G2", "G4", "G5", "G6", "G7", "G9", "G10", "G11")
TEMPLATES = {tid: library.get(tid) for tid in NEW}

# --- the hand-made fixtures (15-minute bars; the waves counted on the signal chart) ------------------------------------------
# An impulse up: a fall that sets the larger low, wave one in five, wave two in three, wave three in five, wave four in three.
IMPULSE = [100, 96, 98, 80, 85, 83.5, 90, 88.5, 95, 93, 94.2, 91.5, 94.5, 93.0, 97, 95.5, 103, 100.5, 101.8, 99.3]
# A contracting diagonal up (wave four overlaps wave one), its fifth wave, the break of its 2-4 line and a pullback to it.
DIAGONAL = [90, 92.3, 91.2, 94, 92.4, 93.5, 90.8, 92.9, 91.8, 94.6, 93.2, 94.3, 92.5, 94.2, 93.6, 94.8, 91.4, 93.6, 89]
# An advance, a falling wave A in five, the wave B pullback up in three (G3's path up to the end of wave B).
ZIGZAG_TO_B = [90.0, 93.19, 91.7, 96.73, 95.51, 97.47, 96.58, 97.79, 95.39, 96.69, 94.91, 96.04, 94.66, 96.82, 95.59, 96.88]
# key: (template id, path, bar edits (open, high, low, close), {style: (signal bar, extra settings)}, side)
FIXTURES = {
    # wave four ends, the fifth wave starts: touch / candle in wave four, the break of wave four's channel and its retest
    "G2": ("G2", IMPULSE + [101.9, 100.2, 106], {416: (100.3, 100.8, 99.9, 100.7)},
           {"aggressive": (405, {}), "candle": (402, {"zone_width": 2.0}), "breakout": (412, {}), "retest": (416, {})}, 1),
    "G2-choch": ("G2", IMPULSE + [106], {}, {"choch": (414, {})}, 1),
    # wave B ends; wave C down breaks the channel of wave B, comes back to it and turns down
    "G4": ("G4", ZIGZAG_TO_B + [95.4, 96.4, 93.0, 95.6, 94.03, 97.63], {319: (96.0, 96.85, 95.8, 95.8)},
           {"breakout": (316, {}), "retest": (320, {})}, -1),
    # an advance, a contracting triangle A-B-C-D-E (A down), the thrust up through its B-D side and the retest of that side
    "G5": ("G5", [90, 94, 92.6, 97, 95.8, 100, 97.5, 98.7, 96.2, 98.2, 97.1, 99.6, 97.7, 98.8, 96.4, 97.9, 96.8, 98.6, 96.6, 97.9,
                  97.6, 99.4, 98.1, 101], {341: (97.6, 98.5, 97.5, 98.46), 347: (98.6, 98.6, 97.7, 98.3)},
           {"breakout": (341, {}), "retest": (348, {})}, 1),
    # wave C down closes beyond the end of wave A, comes back to it and turns down
    "G6": ("G6", ZIGZAG_TO_B + [93.5, 94.5, 92.5, 93.2, 91], {}, {"breakout": (320, {}), "retest": (325, {})}, -1),
    # a flat: wave A down in three, wave B up in three beyond the start of wave A, then back below it (the short side)
    "G7": ("G7", [90, 94.5, 93.2, 97, 95.2, 96.4, 94.0, 96.9, 95.8, 97.3, 95.5, 96.8, 93], {},
           {"reclaim": (302, {}), "candle": (303, {}), "choch": (305, {})}, -1),
    # the end of the diagonal's fifth wave: the break of its 2-4 line and the retest (G9 reads both motive types)
    "G9": ("G9", DIAGONAL, {320: (93.5, 93.8, 92.9, 92.95)}, {"breakout": (313, {}), "retest": (320, {})}, -1),
    "G9-choch": ("G9", IMPULSE + [102.5, 101.4, 105.5, 104.3, 106.5, 101], {}, {"choch": (435, {})}, -1),
    "G10": ("G10", DIAGONAL, {320: (93.5, 93.8, 92.9, 92.95)}, {"breakout": (313, {}), "retest": (320, {})}, -1),
    # a fall in three, then a double three up: W up in three, a shallow X down in three, Y up
    "G11": ("G11", [100, 96, 97.5, 90, 92.8, 91.6, 93.7, 92.4, 93.5, 91.6, 93.7, 93.3, 96],
            {311: (93.7, 93.78, 93.6, 93.65), 312: (93.65, 93.7, 93.5, 93.55), 313: (93.55, 93.6, 93.35, 93.4), 314: (93.3, 93.78, 92.9, 93.7)},
            {"breakout": (310, {}), "retest": (314, {})}, 1),
    "G11-choch": ("G11", [100, 96, 97.5, 90, 92.8, 91.6, 93.7, 92.2, 93.2, 91.6, 96, 95, 97], {}, {"choch": (309, {})}, 1),
}
# The count's own limit at the signal (the invalidation default) per fixture
INVALIDATION = {"G2": 95.1, "G2-choch": 95.1, "G4": 97.89, "G6": 96.98, "G7": 97.4, "G9": 94.9, "G9-choch": 106.6, "G10": 94.9,
                "G11": 91.5, "G11-choch": 91.5}
CASES = [(key, style) for key, (_, _, _, want, _) in FIXTURES.items() for style in want]


def base(tid):
    """Single-frame fixtures: the waves on the signal chart, the higher trend not asked."""
    relation = "any" if "any" in TEMPLATES[tid].param("trend_relation").choices else "counter"
    return {"wave_tf": "15m", "higher_tf": "1h", "trend_relation": relation}


def fixture(key, style):
    tid, path, edits, want, side = FIXTURES[key]
    bar, extra = want[style]
    return TEMPLATES[tid], bars_of(path, edits), {**base(tid), "entry_style": style, **extra}, bar, side


# ============================================================================================================ the library
def test_the_new_templates_are_family_g_and_validate_against_the_registry():
    for tid, t in TEMPLATES.items():
        assert t.family == "G" and t.version == 2 and C.validate_template(t) == [], tid   # version 2: the companions
        for style in t.param("entry_style").choices:
            flags = recipe(t, entry_style=style).required_flags
            assert "screener_waves" in flags, (tid, style)
            if style == "retest":
                assert {"screener_breakouts", "screener_breakouts_bk4"} <= flags, tid
        assert recipe(t).params_hash != recipe(t, wave_degree=2, area_degree=1).params_hash


def test_no_ratio_or_other_number_literal_in_a_new_recipe():
    """Ratios only from parameters, and no other number either: the only number nodes are the one-bar offset of `Lag(x, 1)`
    (a new smaller swing since the bar before; the ATR of the bars before, BK1) and the zero of `Count(...) > 0`."""
    for tid, t in TEMPLATES.items():
        for text in expressions(t):
            ast = parse(text.replace("@{wave_tf}", "@1h").replace("@{higher_tf}", "@1d"))
            allowed = {id(c.args[1]) for c in n.walk(ast) if isinstance(c, n.Call) and c.name == "Lag"
                       and isinstance(c.args[1], n.Num) and c.args[1].value == 1}
            allowed |= {id(c.right) for c in n.walk(ast) if isinstance(c, n.Compare) and c.op == ">" and isinstance(c.left, n.Call)
                        and c.left.name == "Count" and isinstance(c.right, n.Num) and c.right.value == 0}
            assert not [x for x in n.walk(ast) if isinstance(x, n.Num) and id(x) not in allowed], (tid, text)
            outside = re.sub(r'"[^"]*"', "", text)
            outside = re.sub(r", 1\)", ")", outside)
            outside = re.sub(r"\) > 0$", ")", outside)
            assert not re.search(r"\d", outside), (tid, text)


def test_every_setting_has_the_reviews_default():
    """WAVE-2 (trend relation: with for G2; counter-trend allowed for G4-G7 and G11; any for G9 / G10), WAVE-5 (clarity),
    WAVE-11 (no exit signal), the count's own invalidation first, every number a bounded setting."""
    want_trend = {"G2": "with", "G4": "counter", "G5": "counter", "G6": "counter", "G7": "counter", "G9": "any", "G10": "any",
                  "G11": "counter"}
    want_style = {"G2": "choch", "G4": "breakout", "G5": "breakout", "G6": "breakout", "G7": "reclaim", "G9": "choch",
                  "G10": "breakout", "G11": "choch"}
    for tid, t in TEMPLATES.items():
        d = {p.key: p.default for p in t.params}
        assert d["trend_relation"] == want_trend[tid] and d["entry_style"] == want_style[tid], tid
        assert (d["min_clarity"], d["exit_signal"]) == ("any", "none")
        assert d["invalidation"] == t.param("invalidation").choices[0] and "last_swing" in t.param("invalidation").choices
        assert (d["wave_tf"], d["higher_tf"], d["wave_degree"], d["area_degree"]) == ("1h", "1d", 1, 0)
        if "fib_zone" in d:
            assert d["fib_zone"] == "none"
        for p in t.params:
            if p.type in ("int", "float"):
                assert p.min is not None and p.max is not None and p.min <= p.default <= p.max, (tid, p.key)
    assert TEMPLATES["G2"].param("area_source").default == "swing" and TEMPLATES["G2"].param("window").default == "first"


def test_the_settings_constraints_in_plain_words():
    for tid, t in TEMPLATES.items():
        cases = [({"wave_tf": "15m"}, "finer than your signal timeframe", "1h"),
                 ({"higher_tf": "1h", "wave_tf": "1d"}, "higher timeframe must not be finer", "15m"),
                 ({"area_degree": 1, "wave_degree": 1}, "smaller than the waves", "15m")]
        if "fib_zone" in {p.key for p in t.params}:
            cases.append(({"fib_from": 0.7, "fib_to": 0.6}, "start before it ends", "15m"))
        for bad, words, tf in cases:
            with pytest.raises(C.TemplateError) as exc:
                C.resolve_settings(t, tf, params=bad)
            assert any(words in p for p in exc.value.problems), (tid, bad, exc.value.problems)
    with pytest.raises(C.TemplateError):
        C.resolve_settings(TEMPLATES["G2"], "15m", params={"area_source": "none"})


def test_the_strategy_id_round_trips_and_stays_within_its_length():
    for tid, t in TEMPLATES.items():
        sid, settings = ids.adopt(t, "15m")
        assert ids.decode(sid) == (t, settings)
        keys = {p.key for p in t.params}
        heavy = {"wave_tf": "15m", "higher_tf": "1h", "entry_style": t.param("entry_style").choices[-1], "min_clarity": "agree",
                 "invalidation": "last_swing", "exit_signal": "choch", "k": 0.35, "trend_degree": 2}
        heavy.update({k: v for k, v in {"fib_zone": "zone", "fib_from": 0.382, "fib_to": 0.618, "retest_bars": 25, "hold_bars": 120,
                                       "atr_n": 21, "window": "every", "expiry_bars": 120, "zone_width": 1.5,
                                       "area_source": "swing"}.items() if k in keys})
        sid, settings = ids.adopt(t, "15m", "short", heavy)
        assert len(sid) <= ids.MAX_LENGTH and ids.decode(sid) == (t, settings), (tid, sid)


# ============================================================================================================== must fire
@pytest.mark.parametrize("key, style", CASES)
def test_must_fire_per_trigger_style(key, style):
    template, bars, params, bar, side = fixture(key, style)
    s = run(template, bars, **params)
    assert signals(s) == {bar: side}, (key, style)
    if key in INVALIDATION:
        assert s.invalidation[bar] == pytest.approx(INVALIDATION[key]), (key, style)
    close = bars["close"].iloc[bar]
    assert (s.invalidation[bar] < close) if side > 0 else (s.invalidation[bar] > close)       # S6
    for name in ("setup", "trigger"):                          # the recipe's parts are the ScreenQL expressions themselves
        assert bool(part_series(template, params, name, "long" if side > 0 else "short", bars).iloc[bar]), name


def test_g5_invalidation_is_the_far_end_of_the_triangle():
    template, bars, params, bar, side = fixture("G5", "breakout")
    s = run(template, bars, **params)
    assert s.invalidation[bar] == pytest.approx(96.3)          # the end of wave C (a contracting triangle: below wave E's)
    assert ev("WaveEnd(1, \"current\")", bars).iloc[bar] == pytest.approx(96.5)


def test_g7_arms_on_a_flat_wave_b_beyond_the_start_of_wave_a():
    """WB5 / WAVE-8: the type stays FLAT during wave B (expanded only once wave C passes the end of wave A); the arm is
    WaveBeyondOrigin, and the invalidation is the extreme of the sweep."""
    template, bars, params, bar, side = fixture("G7", "reclaim")
    assert ev("WaveType(1)", bars).iloc[bar] == "FLAT" and ev("WaveState(1)", bars).iloc[bar] == "COR_B"
    beyond = ev("WaveBeyondOrigin(1)", bars).to_numpy()
    assert beyond[bar] and not beyond[:bar - 3].any()
    assert run(template, bars, **params).invalidation[bar] == pytest.approx(bars["high"].iloc[LEAD:bar + 1].max())


def test_g6_the_break_is_the_close_beyond_the_end_of_wave_a_by_k_atr():
    template, bars, params, bar, side = fixture("G6", "breakout")
    a_end = ev('WaveEnd(1, "A")', bars).iloc[bar]
    close = bars["close"].to_numpy()
    assert close[bar] < a_end and close[bar - 1] >= a_end - 0.2 * ev("Lag(ATR(14), 1)", bars).iloc[bar - 1]
    far = signals(run(template, bars, **{**params, "k": 2.0}))
    assert far == {} or min(far) > bar                         # a larger break distance: later or not at all


# ============================================================================================================ must not fire
@pytest.mark.parametrize("tid, key", [("G5", "G10"), ("G10", "G9-choch"), ("G2", "G10"), ("G11", "G6"), ("G7", "G6"), ("G6", "G11-choch"),
                                      ("G4", "G11"), ("G9", "G4")])
def test_must_not_fire_on_another_count_type(tid, key):
    """G5 needs a triangle, G10 a diagonal (not an impulse), G2 an impulse (wave four not into wave one), G11 a double three,
    G7 a flat B beyond A's start, G6 / G4 not a double three's X / Y, G9 a fifth wave."""
    _, path, edits, want, _ = FIXTURES[key]
    t = TEMPLATES[tid]
    for style in t.param("entry_style").choices:
        assert signals(run(t, bars_of(path, edits), **base(tid), entry_style=style)) == {}, (tid, key, style)


@pytest.mark.parametrize("key", ["G4", "G6", "G7", "G11-choch"])
def test_a_disagreeing_alternate_blocks_when_asked(key):
    style = next(iter(FIXTURES[key][3]))
    template, bars, params, bar, _ = fixture(key, style)
    assert ev("WaveClarity(1)", bars).iloc[bar] == "ALT_DISAGREES"
    assert signals(run(template, bars, **params, min_clarity="agree")) == {}
    assert signals(run(template, bars, **params)) != {}


@pytest.mark.parametrize("key", ["G2", "G10"])
def test_with_the_higher_trend_keeps_only_signals_that_agree(key):
    style = next(iter(FIXTURES[key][3]))
    template, bars, params, bar, side = fixture(key, style)
    params = {**params, "trend_relation": "with", "trend_degree": 0}
    hours = len(bars) // 4 + 2
    up, down = _higher_frame(1, bars.index[0], hours), _higher_frame(-1, bars.index[0], hours)
    assert signals(run(template, bars, frames={"1h": up if side > 0 else down}, **params)) == {bar: side}
    assert signals(run(template, bars, frames={"1h": down if side > 0 else up}, **params)) == {}


@pytest.mark.parametrize("key", ["G4", "G7"])
def test_counter_trend_c_leg_allowed_by_default_and_can_ask_for_the_higher_trend(key):
    style = next(iter(FIXTURES[key][3]))
    template, bars, params, bar, side = fixture(key, style)
    hours = len(bars) // 4 + 2
    against = _higher_frame(-side, bars.index[0], hours)
    assert signals(run(template, bars, frames={"1h": against}, **params)) == {bar: side}
    assert signals(run(template, bars, frames={"1h": against}, **{**params, "trend_relation": "with", "trend_degree": 0})) == {}


def test_the_fibonacci_zone_filters_the_depth_of_the_pullback():
    template, bars, params, bar, side = fixture("G2", "aggressive")
    ratio = ev("WaveRatio(1)", bars).iloc[bar]
    assert 0.2 < ratio < 0.5
    assert signals(run(template, bars, **params, fib_zone="zone", fib_from=0.5, fib_to=0.786)) == {}
    assert signals(run(template, bars, **params, fib_zone="zone", fib_from=0.2, fib_to=0.5)) == {bar: side}
    template, bars, params, bar, side = fixture("G4", "breakout")
    depth = ev("WaveRatio(1)", bars).iloc[bar]                 # wave B's depth of wave A at the break
    assert signals(run(template, bars, **params, fib_zone="zone", fib_from=0.0, fib_to=depth / 2)) == {}
    assert signals(run(template, bars, **params, fib_zone="zone", fib_from=depth / 2, fib_to=1.0)) == {bar: side}


def test_g2_every_new_smaller_pullback_is_its_own_opportunity():
    template, bars, params, bar, side = fixture("G2", "aggressive")
    every = signals(run(template, bars, **params, window="every"))
    assert every and all(d == side for d in every.values())


def test_the_last_smaller_swing_as_invalidation_and_s6():
    for key, style in (("G2", "breakout"), ("G4", "breakout"), ("G6", "breakout"), ("G7", "reclaim"), ("G11-choch", "choch")):
        template, bars, params, bar, side = fixture(key, style)
        s = run(template, bars, **params, invalidation="last_swing")
        swing = ev("SwingLow(0)" if side > 0 else "SwingHigh(0)", bars).iloc[bar]
        close = bars["close"].iloc[bar]
        if (swing < close) if side > 0 else (swing > close):
            assert signals(s) == {bar: side} and s.invalidation[bar] == pytest.approx(swing), key
        else:
            assert signals(s) == {} and s.reason[bar] == engine.REASON_GEOMETRY, key


# ===================================================================================================== exits (WB9) and words
@pytest.mark.parametrize("key", ["G2", "G4", "G6", "G7", "G11-choch"])
def test_exit_signals_never_fire_on_the_signal_bar(key):
    style = next(iter(FIXTURES[key][3]))
    template, bars, params, bar, side = fixture(key, style)
    for choice in template.param("exit_signal").choices[1:]:
        s = run(template, bars, **params, exit_signal=choice)
        assert signals(s) == {bar: side}
        assert not (s.exit_long if side > 0 else s.exit_short)[bar], (key, choice)
    none = run(template, bars, **params)
    assert not none.exit_long.any() and not none.exit_short.any()       # default: no exit signal (WAVE-11)


def test_the_leaves_exit_ends_the_idea_when_the_count_moves_on():
    template, _, params, bar, side = fixture("G6", "breakout")
    tid, path, edits, _, _ = FIXTURES["G6"]
    bars = bars_of(path + [94.5, 90], edits)                   # wave C ends and the count moves on
    s = run(template, bars, **params, exit_signal="leaves")
    assert signals(s) == {bar: side}
    state = ev("WaveState(1)", bars)
    later = [t for t in range(bar, len(bars)) if state.iloc[t] != "COR_C"]
    assert later and s.exit_short[later[0]] and not s.exit_short[bar:later[0]].any()


def test_the_signal_text_is_structure_words_only():
    for tid, t in TEMPLATES.items():
        for side in ("long", "short"):
            for tf in ("5m", "15m", "1h"):
                s = C.Settings(tf, "both", {p.key: p.default for p in t.params})
                text = library.signal_text(t, s, side)
                assert language.guard(text) == [] and "1-hour chart" in text and "void" in text, text
                low = text.lower()
                assert not any(w in low for w in ("wave 2", "wave 5", "target", "stop", "buy", "sell", "projection")), text
    g7 = library.signal_text(TEMPLATES["G7"], C.Settings("15m", "both", {"wave_tf": "1h"}), "short")
    assert g7 == ("Wave B of a flat on the 1-hour chart went beyond the start of wave A and came back; a downward wave C may be "
                  "starting; the count is void beyond the extreme of wave B.")


def test_the_adapter_signal_carries_the_words_and_no_internal_ids():
    template, bars, params, bar, side = fixture("G6", "breakout")
    strat = TemplateStrategy(template, C.resolve_settings(template, "15m", params=params))
    sig = strat.analyze({"15min": bars.iloc[:bar + 1]}, "S")
    assert sig.is_tradeable and sig.target1 is None and sig.stop_loss == pytest.approx(96.98)
    text = " ".join(sig.reasons)
    assert "closed beyond the end of wave A" in text and language.guard(text) == []
    for internal in ("template:", "WaveState", "COR_", "IMP_", "@", strat.recipe.params_hash):
        assert internal not in text


# ====================================================================================================== mirror, truncation
@pytest.mark.parametrize("key, style", CASES)
def test_mirror_gives_the_other_side_on_the_same_bars(key, style):
    template, bars, params, bar, side = fixture(key, style)
    s, m = run(template, bars, **params), run(template, _mirror_bars(bars, PIVOT), **params)
    assert signals(m) == {t: -d for t, d in signals(s).items()} == {bar: -side}
    assert PIVOT - m.invalidation[bar] == pytest.approx(s.invalidation[bar])


@pytest.mark.parametrize("key, style", [("G2", "retest"), ("G4", "retest"), ("G5", "breakout"), ("G5", "retest"), ("G6", "retest"),
                                        ("G7", "reclaim"), ("G9-choch", "choch"), ("G10", "retest"), ("G11", "retest")])
def test_truncation_no_look_ahead_over_many_t(key, style):
    template, bars, params, bar, side = fixture(key, style)
    full = run(template, bars, **params)
    marks = sorted({bar - 1, bar, bar + 1, len(bars) - 1} | set(range(LEAD, len(bars), 29)))
    for t in marks:
        part = run(template, bars.iloc[:t + 1], **params)
        np.testing.assert_array_equal(part.direction, full.direction[:t + 1], err_msg=f"t={t}")
        np.testing.assert_allclose(part.invalidation, full.invalidation[:t + 1], equal_nan=True, err_msg=f"t={t}")
        assert [part.instance_id(i) for i in part.signal_bars()] == [full.instance_id(i) for i in full.signal_bars() if i <= t]


@pytest.mark.parametrize("tid, style, seed", [("G4", "breakout", 1), ("G6", "breakout", 4), ("G7", "reclaim", 2), ("G2", "aggressive", 5),
                                              ("G5", "breakout", 7), ("G9", "choch", 0), ("G11", "choch", 8)])
def test_truncation_on_random_walk_bars(tid, style, seed):
    template = TEMPLATES[tid]
    params = {**base(tid), "entry_style": style}
    df = synthetic_bars(1500, tf="15m", seed=seed)
    full = run(template, df, **params)
    assert full.signal_bars(), "the random walk must carry signals"
    marks = sorted({t for b in full.signal_bars() for t in (b - 1, b, b + 1)} | set(range(full.warmup_bars, len(df), 131)))
    for t in [x for x in marks if x < len(df)]:
        part = run(template, df.iloc[:t + 1], **params)
        np.testing.assert_array_equal(part.direction, full.direction[:t + 1], err_msg=f"t={t}")
        np.testing.assert_allclose(part.invalidation, full.invalidation[:t + 1], equal_nan=True, err_msg=f"t={t}")


@hsettings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(tid=st.sampled_from(list(NEW)), style_i=st.integers(0, 4), inval=st.sampled_from(["a", "last_swing"]),
       clarity=st.sampled_from(["any", "agree"]), k=st.floats(0.0, 1.0), seed=st.integers(0, 20))
def test_property_random_valid_settings(tid, style_i, inval, clarity, k, seed):
    template = TEMPLATES[tid]
    styles = template.param("entry_style").choices
    inval = template.param("invalidation").default if inval == "a" else inval
    params = {**base(tid), "entry_style": styles[style_i % len(styles)], "invalidation": inval, "min_clarity": clarity, "k": k}
    df = synthetic_bars(700, tf="15m", seed=seed)
    s = run(template, df, **params)
    clear, state = ev("WaveClarity(1)", df), ev("WaveState(1)", df)
    for t, d in signals(s).items():
        close = df["close"].iloc[t]
        assert (s.invalidation[t] < close) if d > 0 else (s.invalidation[t] > close)           # S6
        assert t >= s.warmup_bars and state.iloc[t] != "UNCLEAR"
        if tid not in ("G5", "G9", "G10"):                     # a hold window (as G8) reads the count it last saw
            assert clear.iloc[t] != "UNCLEAR"
    seen = [s.instance_id(t) for t in s.signal_bars()]
    assert len(seen) == len(set(seen))                                                          # one signal per instance


# ================================================================================================== the G parity harness
@pytest.mark.parametrize("key, style", [("G2", "retest"), ("G4", "breakout"), ("G5", "retest"), ("G6", "retest"), ("G7", "reclaim"),
                                        ("G9-choch", "choch"), ("G10", "breakout"), ("G11-choch", "choch")])
def test_parity_on_the_fixtures(key, style):
    template, bars, params, bar, side = fixture(key, style)
    strat = TemplateStrategy(template, C.resolve_settings(template, "15m", params=params))
    report = wave_parity.run(strat, bars, "S", RISK, worker_bars=sorted({bar - 1, bar, bar + 1, LEAD, len(bars) - 1}))
    assert report.problems == [] and report.signals == [bars.index[bar]]
    assert report.asked["backtest"] > 50 and report.trades == 1


@pytest.mark.parametrize("tid, style, seed", [("G4", "breakout", 3), ("G7", "reclaim", 1)])
def test_parity_with_the_wave_and_higher_timeframe_frames(tid, style, seed):
    """The waves on the 1-hour chart and the higher trend asked on the Daily chart, a 15-minute signal chart: the scan with
    the context frames handed in = the scan that resamples = both backtests = the worker, on one trailing window."""
    template = TEMPLATES[tid]
    params = {"wave_tf": "1h", "higher_tf": "1d", "trend_relation": "with", "trend_degree": 0, "entry_style": style}
    window = synthetic_bars(3000, tf="15m", seed=seed)
    strat = TemplateStrategy(template, C.resolve_settings(template, "15m", params=params))
    assert wave_parity.wave_history_ok(strat.recipe, window)
    frames = wave_parity.context_frames(strat.recipe, window)
    assert set(frames) == {"1h", "1d"} and wave_parity.check_frames(window, "15m", frames) == []
    scan = wave_parity.scan_with_frames(strat.recipe, window, frames)
    at = [i for i, a in enumerate(scan.values()) if a.direction]
    worker = sorted({j for i in at for j in (i - 1, i, i + 1)} | {len(window) - 1, len(window) // 2})
    config = OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, spread_width=2)
    report = wave_parity.run(strat, window, "S", RISK, worker_bars=worker, options_config=config)
    assert report.problems == [] and len(report.signals) == len(at)
    assert report.asked["worker"] == len(worker)


# ============================================================================================================== the flags
def test_the_new_templates_are_listed_only_with_screener_waves():
    from tests.test_tpl2_routes import _org, _set_flag, client
    org = _org("waves5", "strategy_templates", "screener_breakouts", "screener_breakouts_bk4")
    listed = {c["id"] for c in client.get("/api/strategy-templates", headers=org["headers"]).json()["templates"]}
    assert not listed & set(NEW)
    assert client.post("/api/strategy-templates/G7/adopt", headers=org["headers"], json={"timeframe": "15m"}).status_code == 404
    _set_flag("screener_waves", org["tenant_id"])
    cards = {c["id"]: c for c in client.get("/api/strategy-templates", headers=org["headers"]).json()["templates"]}
    assert set(NEW) <= set(cards) and all(cards[t]["family_name"] == "Waves" and cards[t]["available"] for t in NEW)
    r = client.post("/api/strategy-templates/G6/adopt", headers=org["headers"], json={"timeframe": "15m"})
    assert r.status_code == 200 and r.json()["deployment"]["mode"] == "PAPER"
    assert r.json()["strategy_id"].startswith("template:G6:2:15m:both:")


def test_no_projection_target_in_any_new_template():
    """Section 5 (targets as exit settings) is not built: no template reads a projection, and none chooses a target."""
    for tid, t in TEMPLATES.items():
        assert not any("WaveProjection" in x or "WaveCap" in x for x in expressions(t)), tid
        assert "target" not in t.model_dump_json().lower(), tid

