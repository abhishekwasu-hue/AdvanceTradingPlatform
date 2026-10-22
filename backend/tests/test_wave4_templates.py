"""Wave templates (family G) build steps 6 + 7 (docs/design/WAVE_TEMPLATES_REVIEW.md section 7): the G parity harness on
the TPL-1 harness, and the first templates G1 (wave three after wave two), G3 (wave C after wave B) and G8 (correction
complete, the trend resumes).

Public, synthetic tests only (hand-made wave paths pinned here, plus the random-walk bars of the look-ahead harness):
must-fire per trigger style, must-not (an unclear count, a disagreeing alternate, the higher trend, the Fibonacci zone,
wave A's structure), mirror, truncation over many t, scan = backtest = options backtest = worker on one trailing window
with the wave and higher timeframe frames, no ratio literal in a G recipe, every number a setting with the review's
defaults, the language guard, the exit signals per WB9, the strategy id within its length, and the flags (a G template
is listed only while `strategy_templates` and `screener_waves` are both on). Signals only, PAPER only.
"""
from __future__ import annotations

import json
import math
import re

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st

from app.backtest.options_engine import OptionBacktestConfig
from app.core.enums import OptionStrategy
from app.core.models import RiskConfig
from app.quality.lookahead import synthetic_bars
from app.screener import nodes as n
from app.screener.parser import parse
from app.screener.runtime import SymbolData, evaluate
from app.strategy_templates import compile as C
from app.strategy_templates import engine, ids, language, library, wave_parity
from app.strategy_templates.adapter import TemplateStrategy
from app.strategy_templates.schema import ContextSwitch, SetupSwitch, Switch
from tests.test_wave2_counter import _mirror_bars, path_bars

G1, G3, G8 = library.get("G1"), library.get("G3"), library.get("G8")
TEMPLATES = (G1, G3, G8)
STYLES = ("aggressive", "candle", "choch", "breakout", "retest")
RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5)
PIVOT = 200.0                                                  # mirror axis: price -> PIVOT - price
LEAD = 260                                                     # flat bars before a path: the wave degree's history

# --- the hand-made fixtures: a wave path (15-minute bars), bar edits (open, high, low, close) and what each style gives ----
# G1: a fall that sets the larger low, wave one in five, wave two in three, then wave three - with a pullback to the broken
# channel of wave two (the retest bar is a rejection candle).
G1_PATH = [100, 96, 98, 80, 85, 83.5, 90, 88.5, 95, 93, 94.2, 91.5, 94.5, 93.0, 97, 95.5, 103]
G1_EDITS = {369: (93.5, 93.55, 92.5, 93.45)}
G1_WANT = {"aggressive": (357, {}), "candle": (355, {"zone_width": 2.0}), "choch": (366, {}), "breakout": (364, {}),
           "retest": (369, {})}
G1_INVALIDATION = 79.9                                         # the start of wave one
# G3: an advance, then a falling wave A, the wave B pullback up and wave C down (the short side).
G3_PATH = [90.0, 93.19, 91.7, 96.73, 95.51, 97.47, 96.58, 97.79, 95.39, 96.69, 94.91, 96.04, 94.66, 96.82, 95.59, 96.88,
           93.98, 95.17, 93.05, 95.6, 94.03, 97.63]
G3_EDITS: dict = {}
G3_WANT = {"aggressive": (310, {}), "candle": (310, {}), "choch": (318, {}), "breakout": (316, {})}
G3_INVALIDATION = 97.89                                        # the start of wave A
# G8: an advance, a zigzag A-B-C down whose C passed the end of A, then the advance resumes - with a pullback to the broken
# channel of wave C (the retest bar is a rejection candle).
G8_PATH = [90.0, 93.13, 91.98, 96.42, 95.2, 99.05, 97.62, 98.84, 96.36, 97.6, 95.6, 97.05, 95.78, 97.67, 96.59, 97.78, 95.06,
           96.36, 94.29, 96.6, 94.6, 100.3]
G8_EDITS = {337: (94.75, 95.5, 94.3, 95.45)}
G8_WANT = {"aggressive": (323, {}), "candle": (323, {}), "choch": (333, {}), "breakout": (331, {}), "retest": (337, {})}
FIXTURES = {"G1": (G1, G1_PATH, G1_EDITS, G1_WANT, 1), "G3": (G3, G3_PATH, G3_EDITS, G3_WANT, -1),
            "G8": (G8, G8_PATH, G8_EDITS, G8_WANT, 1)}
# single-frame fixtures: the waves counted on the signal chart, the higher trend not asked (G3: wave C may be counter-trend)
BASE = {"G1": {"wave_tf": "15m", "higher_tf": "1h", "trend_relation": "any"},
        "G3": {"wave_tf": "15m", "higher_tf": "1h", "trend_relation": "counter", "a_structure": "any"},
        "G8": {"wave_tf": "15m", "higher_tf": "1h", "trend_relation": "any"}}
CASES = [(tid, style) for tid, (_, _, _, want, _) in FIXTURES.items() for style in want]


def bars_of(path, edits):
    bars = path_bars(path, lead=LEAD)
    for j, row in edits.items():
        bars.iloc[j, :4] = row
    return bars


def recipe(template, tf="15m", **params):
    return C.compile_recipe(template, C.resolve_settings(template, tf, params=params))


def run(template, bars, tf="15m", frames=None, **params):
    return engine.compute(recipe(template, tf, **params), {tf: bars, **(frames or {})})


def signals(s):
    return {t: int(s.direction[t]) for t in s.signal_bars()}


def fixture(tid, style):
    template, path, edits, want, side = FIXTURES[tid]
    bar, extra = want[style]
    return template, bars_of(path, edits), {**BASE[tid], "entry_style": style, **extra}, bar, side


def ev(text, bars, tf="15m"):
    return evaluate(parse(text), SymbolData("S", {tf: bars}), base_tf=tf)


def part_series(template, params, name, side, bars, tf="15m"):
    """One compiled part of the recipe (its parsed expression and settings) evaluated by the ScreenQL runtime."""
    part = recipe(template, tf, **params).part(name, side)
    return evaluate(part.ast, SymbolData("S", {tf: bars}), base_tf=tf, params=part.params)


def expressions(template):
    """Every expression of a template, every variant included."""
    out = []
    for part in (template.context, template.setup, template.trigger, template.invalidation, template.exit_signal):
        if part is None:
            continue
        variants = [v for v in part.variants.values() if v is not None] if isinstance(part, (Switch, SetupSwitch, ContextSwitch)) else [part]
        for v in variants:
            out += [v.long, v.short]
            arm = getattr(v, "arm_level", None)
            if arm is not None:
                out += [arm.long, arm.short]
    out += [x for e in template.evidence for x in (e.long, e.short)]
    return out


# ============================================================================================================ the library
def test_the_three_templates_are_family_g_and_validate_against_the_registry():
    for t in TEMPLATES:
        assert t.family == "G" and t.version == 1 and C.validate_template(t) == [], t.id
        r = recipe(t)
        assert r.required_flags == {"screener_waves", "screener_breakouts"}, t.id    # the default trigger style (CHoCH)
        assert recipe(t, entry_style="retest").required_flags >= {"screener_waves", "screener_breakouts", "screener_breakouts_bk4"}
        assert r.params_hash != recipe(t, wave_degree=2, area_degree=1).params_hash


def test_no_ratio_or_other_number_literal_in_a_g_recipe():
    """Section 7: ratios only from parameters - and no other number either (numbers live only in settings): the only
    number nodes in any expression of any variant are the one-bar offset of "a new smaller swing since the bar before"
    (`Lag(x, 1)`) and the zero of "seen at least once in the watch" (`Count(...) > 0`); no other digit is written outside
    a quoted wave label."""
    for t in TEMPLATES:
        for text in expressions(t):
            ast = parse(text.replace("@{wave_tf}", "@1h").replace("@{higher_tf}", "@1d"))
            lag_offsets = {id(c.args[1]) for c in n.walk(ast) if isinstance(c, n.Call) and c.name == "Lag"
                           and isinstance(c.args[1], n.Num) and c.args[1].value == 1}
            seen_once = {id(c.right) for c in n.walk(ast) if isinstance(c, n.Compare) and c.op == ">" and isinstance(c.left, n.Call)
                         and c.left.name == "Count" and isinstance(c.right, n.Num) and c.right.value == 0}
            allowed = lag_offsets | seen_once
            assert not [x for x in n.walk(ast) if isinstance(x, n.Num) and id(x) not in allowed], (t.id, text)
            outside_words = re.sub(r'"[^"]*"', "", text)            # wave labels ("IMP_2", "1") are words, not numbers
            outside_words = re.sub(r"Lag\((\w+\([^()]*\)@\{wave_tf\}), 1\)", r"Lag(\1)", outside_words)
            outside_words = re.sub(r"\) > 0$", ")", outside_words)
            assert not re.search(r"\d", outside_words), (t.id, text)


def test_every_setting_has_the_reviews_default():
    """WAVE-2 (trend relation per template), WAVE-3 (swing zones), WAVE-5 (clarity: exclude unclear only), WAVE-11 (no
    exit signal), WAVE-12 (the first opportunity per wave), the Fibonacci zone off, the count's own invalidation."""
    want_trend = {"G1": "with", "G3": "counter", "G8": "with"}
    for t in TEMPLATES:
        d = {p.key: p.default for p in t.params}
        assert d["trend_relation"] == want_trend[t.id]
        assert (d["area_source"], d["min_clarity"], d["window"], d["fib_zone"], d["exit_signal"]) == ("swing", "any", "first", "none", "none")
        assert d["invalidation"] == ("extreme" if t.id == "G8" else "wave_rule")
        assert (d["wave_tf"], d["higher_tf"], d["wave_degree"], d["area_degree"]) == ("1h", "1d", 1, 0)
        assert d["entry_style"] == "choch" and t.param("entry_style").choices == STYLES
        for p in t.params:                                     # every number is a bounded setting
            if p.type in ("int", "float"):
                assert p.min is not None and p.max is not None and p.min <= p.default <= p.max, (t.id, p.key)
    assert G3.param("a_structure").default == "changed"


def test_the_settings_constraints_in_plain_words():
    for t in TEMPLATES:
        for bad, words in (({"wave_tf": "15m"}, "finer than your signal timeframe"),
                           ({"higher_tf": "1h", "wave_tf": "1d"}, "higher timeframe must not be finer"),
                           ({"area_degree": 1, "wave_degree": 1}, "smaller than the waves"),
                           ({"fib_from": 0.7, "fib_to": 0.6}, "start before it ends"),
                           ({"area_source": "none"}, "Pick an area or turn on the Fibonacci zone")):
            with pytest.raises(C.TemplateError) as exc:
                C.resolve_settings(t, "1h" if "wave_tf" in bad and bad["wave_tf"] == "15m" else "15m", params=bad)
            assert any(words in p for p in exc.value.problems), (t.id, bad, exc.value.problems)
        C.resolve_settings(t, "15m", params={"area_source": "none", "fib_zone": "zone"})


def test_the_strategy_id_round_trips_and_stays_within_its_length():
    heavy = {"wave_tf": "15m", "higher_tf": "1h", "entry_style": "aggressive", "area_source": "none", "fib_zone": "zone",
             "fib_from": 0.382, "fib_to": 0.618, "min_clarity": "agree", "window": "every", "invalidation": "last_swing",
             "exit_signal": "choch", "k": 0.35, "zone_width": 1.5, "expiry_bars": 120, "retest_bars": 25}
    for t in TEMPLATES:
        sid, settings = ids.adopt(t, "15m")
        assert ids.decode(sid) == (t, settings)
        sid, settings = ids.adopt(t, "15m", "short", heavy)
        assert len(sid) <= ids.MAX_LENGTH and ids.decode(sid) == (t, settings)
        assert ",0," in sid or sid.count(",") == len(t.params) - 1           # choices written as their position
        with pytest.raises(KeyError):
            ids.decode(sid.replace(":short:", ":short:9", 1))
    sid, _ = ids.adopt(library.get("D2"), "15m", params={"invalidation": "slow_average"})
    assert sid == "template:D2:1:15m:both:,,,,,slow_average,"                   # other families: unchanged


# ============================================================================================================== must fire
@pytest.mark.parametrize("tid, style", CASES)
def test_must_fire_per_trigger_style(tid, style):
    template, bars, params, bar, side = fixture(tid, style)
    s = run(template, bars, **params)
    assert signals(s) == {bar: side}, (tid, style)
    if tid == "G1":
        assert s.invalidation[bar] == pytest.approx(G1_INVALIDATION)
    elif tid == "G3":
        assert s.invalidation[bar] == pytest.approx(G3_INVALIDATION)
    close = bars["close"].iloc[bar]
    assert (s.invalidation[bar] < close) if side > 0 else (s.invalidation[bar] > close)       # S6
    for name in ("setup", "trigger"):                          # the recipe's parts are the ScreenQL expressions themselves
        assert bool(part_series(template, params, name, "long" if side > 0 else "short", bars).iloc[bar]), name


def test_g8_invalidation_is_the_extreme_of_the_corrections_last_wave():
    bars = bars_of(G8_PATH, G8_EDITS)
    low = bars["low"].to_numpy()
    for style, (bar, extra) in G8_WANT.items():
        s = run(G8, bars, **BASE["G8"], entry_style=style, **extra)
        c_start = 300                                          # the end of wave B is before this bar on the path
        assert s.invalidation[bar] == pytest.approx(low[c_start:bar + 1].min()), style


def test_g3_retest_is_the_screenql_retest_inside_the_window():
    """G3's retest style: the fixture's channel of wave B is too steep for a retest before wave C runs on, so the style
    is checked as its expression - the window and RetestEntry of the broken channel of wave B - bar by bar."""
    bars = bars_of(G3_PATH, G3_EDITS)
    params = {**BASE["G3"], "entry_style": "retest"}
    s = run(G3, bars, **params)
    retest = ev('RetestEntry("down", "wave_line", 10, true, 1, "channel_B", "A", 0.2)', bars).to_numpy()
    assert not retest.any() and signals(s) == {}
    assert recipe(G3, **params).part("trigger", "short").text.count("RetestEntry(\"down\", \"wave_line\"") == 1


# ============================================================================================================ must not fire
def test_an_unclear_count_never_signals():
    """The first count on the G1 path is UNCLEAR (wave one has no inner swings): the touch is there, the signal is not."""
    bars = bars_of(G1_PATH, G1_EDITS)
    clarity = ev("WaveClarity(1)", bars)
    params = {**BASE["G1"], "entry_style": "aggressive"}
    touch = part_series(G1, params, "trigger", "long", bars)
    early = [t for t in np.flatnonzero(touch.to_numpy()) if clarity.iloc[t] == "UNCLEAR"]
    assert early, "the fixture must carry a touch inside an unclear count"
    s = run(G1, bars, **params)
    assert not any(t in signals(s) for t in early)
    assert all(clarity.iloc[t] != "UNCLEAR" for t in s.signal_bars())


@pytest.mark.parametrize("tid", ["G1", "G3", "G8"])
def test_a_disagreeing_alternate_blocks_when_asked(tid):
    template, bars, params, bar, _ = fixture(tid, "choch" if tid != "G3" else "choch")
    clarity = ev("WaveClarity(1)", bars)
    assert clarity.iloc[bar] == "ALT_DISAGREES" or tid == "G8"
    if tid != "G8":
        assert signals(run(template, bars, **params, min_clarity="agree")) == {}
    assert signals(run(template, bars, **params)) != {}


def _higher_frame(direction: int, start: pd.Timestamp, hours: int) -> pd.DataFrame:
    """Hourly bars that zigzag up (direction 1) or down (-1): higher highs and higher lows (or the mirror)."""
    legs = []
    price = 100.0
    for i in range(hours // 6 + 2):
        price += direction * (6.0 if i % 2 == 0 else -3.0)
        legs.append(price)
    bars = path_bars([100.0] + legs, step=1.0, lead=60, start=str(start - pd.Timedelta(hours=60)))
    bars.index = pd.date_range(start - pd.Timedelta(hours=60), periods=len(bars), freq="1h", tz="UTC")
    return bars


@pytest.mark.parametrize("tid", ["G1", "G8"])
def test_with_the_higher_trend_keeps_only_signals_that_agree(tid):
    template, bars, params, bar, side = fixture(tid, "choch")
    params = {**params, "trend_relation": "with", "trend_degree": 0}
    assert signals(run(template, bars, **params)) == {}        # the higher trend is not known on this chart: no signal
    hours = len(bars) // 4 + 2
    up, down = _higher_frame(1, bars.index[0], hours), _higher_frame(-1, bars.index[0], hours)
    trend_up = ev("DowTrend(0)", up, "1h")
    assert trend_up.dropna().iloc[-1] == "UP"
    assert signals(run(template, bars, frames={"1h": up if side > 0 else down}, **params)) == {bar: side}
    assert signals(run(template, bars, frames={"1h": down if side > 0 else up}, **params)) == {}


def test_g3_allows_the_counter_trend_c_leg_by_default_and_can_ask_for_the_higher_trend():
    template, bars, params, bar, side = fixture("G3", "choch")
    hours = len(bars) // 4 + 2
    up = _higher_frame(1, bars.index[0], hours)
    assert signals(run(template, bars, frames={"1h": up}, **params)) == {bar: side}          # short C leg, higher trend up
    assert signals(run(template, bars, frames={"1h": up}, **{**params, "trend_relation": "with", "trend_degree": 0})) == {}


def test_g3_wave_a_must_change_the_structure_by_default():
    bars = bars_of(G3_PATH, G3_EDITS)
    changed = {k: v for k, v in BASE["G3"].items() if k != "a_structure"}
    for style in ("aggressive", "candle", "breakout"):             # wave A left the minor trend intact during wave B
        assert signals(run(G3, bars, **changed, entry_style=style)) == {}, style
    assert signals(run(G3, bars, **changed, entry_style="choch")) == {318: -1}                 # by then the trend had turned
    assert ev("DowTrend(1)", bars).iloc[310] == "UP" and ev("DowTrend(1)", bars).iloc[318] != "UP"


def test_the_fibonacci_zone_filters_the_depth_of_the_pullback():
    bars = bars_of(G1_PATH, G1_EDITS)
    params = {**BASE["G1"], "entry_style": "aggressive"}
    ratio = ev("WaveRatio(1)", bars).iloc[357]
    assert 0.1 < ratio < 0.2                                   # a shallow wave two at the touch
    assert signals(run(G1, bars, **params, fib_zone="zone")) == {}                              # default zone 0.5-0.786
    assert signals(run(G1, bars, **params, fib_zone="zone", fib_from=0.1, fib_to=0.4)) == {357: 1}
    assert signals(run(G1, bars, **params, area_source="none", fib_zone="zone", fib_from=0.1, fib_to=0.4)) != {}


def test_every_new_smaller_pullback_is_its_own_opportunity():
    for tid, style in (("G1", "aggressive"), ("G8", "aggressive")):
        template, bars, params, bar, side = fixture(tid, style)
        first, every = signals(run(template, bars, **params)), signals(run(template, bars, **params, window="every"))
        assert first == {bar: side} and every and all(d == side for d in every.values())


def test_the_last_smaller_swing_as_invalidation_and_s6():
    for tid, style in (("G1", "choch"), ("G8", "choch"), ("G3", "choch")):
        template, bars, params, bar, side = fixture(tid, style)
        s = run(template, bars, **params, invalidation="last_swing")
        swing = ev("SwingLow(0)" if side > 0 else "SwingHigh(0)", bars).iloc[bar]
        close = bars["close"].iloc[bar]
        if (swing < close) if side > 0 else (swing > close):
            assert signals(s) == {bar: side} and s.invalidation[bar] == pytest.approx(swing)
        else:
            assert signals(s) == {} and s.reason[bar] == engine.REASON_GEOMETRY


# ===================================================================================================== exits (WB9) and words
def test_exit_signals_never_fire_on_the_signal_bar_and_end_the_idea_later():
    template, _, params, bar, side = fixture("G1", "choch")
    bars = bars_of(G1_PATH + [99, 101.5, 93], G1_EDITS)         # wave three ends and wave four runs
    s = run(G1, bars, **params, exit_signal="leaves")
    state = ev("WaveState(1)", bars)
    assert signals(s) == {bar: side} and not s.exit_long[bar]
    later = [t for t in range(bar, len(bars)) if state.iloc[t] not in ("IMP_2", "IMP_3")]
    assert later and s.exit_long[later[0]] and not s.exit_long[bar:later[0]].any()
    none = run(G1, bars, **params)
    assert not none.exit_long.any() and not none.exit_short.any()       # default: no exit signal (WAVE-11)
    for tid in ("G3", "G8"):
        template, bars, params, bar, side = fixture(tid, "choch")
        s = run(template, bars, **params, exit_signal="choch")
        assert not (s.exit_long if side > 0 else s.exit_short)[bar]


def test_the_signal_text_is_structure_words_only():
    for t in TEMPLATES:
        for side in ("long", "short"):
            for tf in ("5m", "15m", "1h"):
                s = C.Settings(tf, "both", {p.key: p.default for p in t.params})
                text = library.signal_text(t, s, side)
                assert language.guard(text) == [] and "1-hour chart" in text and "void" in text, text
                assert not any(w in text.lower() for w in ("wave 2", "wave 3", "target", "stop"))
    long = library.signal_text(G1, C.Settings("15m", "both", {"wave_tf": "1h"}), "long")
    assert long == ("The wave two pullback on the 1-hour chart may be over; an upward wave three may be starting; "
                    "the count is void beyond the start of wave one.")


def test_the_adapter_signal_carries_the_words_and_no_internal_ids():
    template, bars, params, bar, side = fixture("G8", "choch")
    strat = TemplateStrategy(G8, C.resolve_settings(G8, "15m", params=params))
    sig = strat.analyze({"15min": bars.iloc[:bar + 1]}, "S")
    assert sig.is_tradeable and sig.target1 is None and sig.stop_loss == pytest.approx(94.19)
    text = " ".join(sig.reasons)
    assert "correction on the 15-minute chart may be complete" in text and language.guard(text) == []
    for internal in ("template:", "WaveState", "COR_", "IMP_", "@", strat.recipe.params_hash):
        assert internal not in text


# ====================================================================================================== mirror, truncation
@pytest.mark.parametrize("tid, style", CASES)
def test_mirror_gives_the_other_side_on_the_same_bars(tid, style):
    template, bars, params, bar, side = fixture(tid, style)
    s, m = run(template, bars, **params), run(template, _mirror_bars(bars, PIVOT), **params)
    assert signals(m) == {t: -d for t, d in signals(s).items()} == {bar: -side}
    assert PIVOT - m.invalidation[bar] == pytest.approx(s.invalidation[bar])


@pytest.mark.parametrize("tid, style", [("G1", "aggressive"), ("G1", "retest"), ("G3", "breakout"), ("G8", "choch"), ("G8", "retest")])
def test_truncation_no_look_ahead_over_many_t(tid, style):
    template, bars, params, bar, side = fixture(tid, style)
    full = run(template, bars, **params)
    marks = sorted({bar - 1, bar, bar + 1, len(bars) - 1} | set(range(LEAD, len(bars), 23)))
    for t in marks:
        part = run(template, bars.iloc[:t + 1], **params)
        np.testing.assert_array_equal(part.direction, full.direction[:t + 1], err_msg=f"t={t}")
        np.testing.assert_allclose(part.invalidation, full.invalidation[:t + 1], equal_nan=True, err_msg=f"t={t}")
        np.testing.assert_array_equal(part.exit_long, full.exit_long[:t + 1], err_msg=f"t={t}")
        assert [part.instance_id(i) for i in part.signal_bars()] == [full.instance_id(i) for i in full.signal_bars() if i <= t]


@pytest.mark.parametrize("tid, style, seed", [("G1", "aggressive", 1), ("G8", "aggressive", 3), ("G3", "choch", 1)])
def test_truncation_on_random_walk_bars(tid, style, seed):
    template = library.get(tid)
    params = {**BASE[tid], "entry_style": style}
    df = synthetic_bars(1500, tf="15m", seed=seed)
    full = run(template, df, **params)
    assert full.signal_bars(), "the fixture must carry signals"
    marks = sorted({t for b in full.signal_bars() for t in (b - 1, b, b + 1)} | set(range(full.warmup_bars, len(df), 97)))
    for t in [x for x in marks if x < len(df)]:
        part = run(template, df.iloc[:t + 1], **params)
        np.testing.assert_array_equal(part.direction, full.direction[:t + 1], err_msg=f"t={t}")
        np.testing.assert_allclose(part.invalidation, full.invalidation[:t + 1], equal_nan=True, err_msg=f"t={t}")


@hsettings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(tid=st.sampled_from(["G1", "G3", "G8"]), style=st.sampled_from(STYLES), window=st.sampled_from(["first", "every"]),
       inval=st.sampled_from(["a", "last_swing"]), clarity=st.sampled_from(["any", "agree"]), k=st.floats(0.0, 1.0),
       seed=st.integers(0, 20))
def test_property_random_valid_settings(tid, style, window, inval, clarity, k, seed):
    template = library.get(tid)
    inval = template.param("invalidation").default if inval == "a" else inval
    params = {**BASE[tid], "entry_style": style, "window": window, "invalidation": inval, "min_clarity": clarity, "k": k}
    df = synthetic_bars(700, tf="15m", seed=seed)
    s = run(template, df, **params)
    state = ev("WaveState(1)", df)
    for t, d in signals(s).items():
        close = df["close"].iloc[t]
        assert (s.invalidation[t] < close) if d > 0 else (s.invalidation[t] > close)           # S6
        assert state.iloc[t] != "UNCLEAR" and t >= s.warmup_bars
    ids_seen = [s.instance_id(t) for t in s.signal_bars()]
    assert len(ids_seen) == len(set(ids_seen))                                                  # one signal per instance


# ================================================================================================== the G parity harness
@pytest.mark.parametrize("tid, style", [("G1", "aggressive"), ("G1", "retest"), ("G3", "choch"), ("G8", "choch"), ("G8", "breakout")])
def test_parity_on_the_fixtures(tid, style):
    template, bars, params, bar, side = fixture(tid, style)
    strat = TemplateStrategy(template, C.resolve_settings(template, "15m", params=params))
    report = wave_parity.run(strat, bars, "S", RISK, worker_bars=sorted({bar - 1, bar, bar + 1, LEAD, len(bars) - 1}))
    assert report.problems == [] and report.signals == [bars.index[bar]]
    assert report.asked["backtest"] > 50 and report.trades == 1


@pytest.mark.parametrize("tid, style, seed", [("G1", "choch", 6), ("G8", "aggressive", 1)])
def test_parity_with_the_wave_and_higher_timeframe_frames(tid, style, seed):
    """The waves on the 1-hour chart and the higher trend asked on the Daily chart, a 15-minute signal chart: the scan
    with the context frames handed in = the scan that resamples = both backtests = the worker, on one trailing window."""
    template = library.get(tid)
    params = {"wave_tf": "1h", "higher_tf": "1d", "trend_relation": "any", "entry_style": style}
    window = synthetic_bars(3000, tf="15m", seed=seed)          # the fixed trailing window W (R6) the paths are given
    strat = TemplateStrategy(template, C.resolve_settings(template, "15m", params=params))
    assert wave_parity.wave_history_ok(strat.recipe, window)
    frames = wave_parity.context_frames(strat.recipe, window)
    assert set(frames) == {"1h"} and wave_parity.check_frames(window, "15m", frames) == []
    scan = wave_parity.scan_with_frames(strat.recipe, window, frames)
    at = [i for i, a in enumerate(scan.values()) if a.direction]
    assert at, "the window must carry signals"
    worker = sorted({j for i in at for j in (i - 1, i, i + 1)} | {len(window) - 1})
    config = OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, spread_width=2)
    report = wave_parity.run(strat, window, "S", RISK, worker_bars=worker, options_config=config)
    assert report.problems == [] and len(report.signals) == len(at)
    assert report.asked["options"] > 0 and report.asked["worker"] == len(worker)


def test_the_harness_refuses_context_frames_with_other_history():
    df = synthetic_bars(1200, tf="15m", seed=2)
    window = df.iloc[-800:]
    strat = TemplateStrategy(G1, C.resolve_settings(G1, "15m", params={"wave_tf": "1h", "higher_tf": "1d"}))
    good = wave_parity.context_frames(strat.recipe, window)
    assert set(good) == {"1h", "1d"} and wave_parity.check_frames(window, "15m", good) == []
    from app.screener.runtime import resample
    longer = {"1h": resample(df, "15m", "1h")}                 # more history than the window: refused
    assert any("starts before the window" in p for p in wave_parity.check_frames(window, "15m", longer))
    later = {"1h": resample(synthetic_bars(1400, tf="15m", seed=2), "15m", "1h")}
    assert any("closes after" in p for p in wave_parity.check_frames(df, "15m", later))
    assert not wave_parity.wave_history_ok(strat.recipe, window.iloc[-300:])


def test_fetch_covers_the_wave_timeframe_history():
    from app.strategy_templates import parity
    for t in TEMPLATES:
        r = recipe(t)
        assert r.lookback["1h"] >= 250 and parity.fetch_bars(r) >= r.lookback["1h"] * 4 * parity.SAFETY_FACTOR
        strat = TemplateStrategy(t, C.resolve_settings(t, "15m"))
        assert strat.history_days() >= math.ceil(r.lookback["1h"] / 6)      # 6 hourly bars to a session at least


# ============================================================================================================== the flags
def test_g_templates_are_listed_only_with_strategy_templates_and_screener_waves():
    from tests.test_tpl2_routes import _org, _set_flag, client
    org = _org("waves", "strategy_templates")
    ids_listed = {c["id"] for c in client.get("/api/strategy-templates", headers=org["headers"]).json()["templates"]}
    assert {"D2", "B1"} <= ids_listed and not ids_listed & {"G1", "G3", "G8"}
    assert client.post("/api/strategy-templates/G1/adopt", headers=org["headers"], json={"timeframe": "15m"}).status_code == 404
    _set_flag("screener_waves", org["tenant_id"])
    cards = {c["id"]: c for c in client.get("/api/strategy-templates", headers=org["headers"]).json()["templates"]}
    assert {"G1", "G3", "G8"} <= set(cards) and cards["G8"]["family_name"] == "Waves"
    assert not cards["G1"]["available"]                        # its structure functions (the breakout screens) are still off
    _set_flag("screener_breakouts", org["tenant_id"])
    cards = {c["id"]: c for c in client.get("/api/strategy-templates", headers=org["headers"]).json()["templates"]}
    assert cards["G1"]["available"] and cards["G3"]["available"] and cards["G8"]["available"]
    text = json.dumps(cards["G1"])
    assert "screener_waves" not in text and "WaveState" not in text and "$" not in text
    r = client.post("/api/strategy-templates/G1/adopt", headers=org["headers"], json={"timeframe": "15m"})
    assert r.status_code == 200 and r.json()["deployment"]["mode"] == "PAPER"
    assert r.json()["strategy_id"].startswith("template:G1:1:15m:both:")
    other = _org("waves-off", "strategy_templates", "screener_breakouts")
    assert client.post("/api/strategy-templates/G3/adopt", headers=other["headers"], json={}).status_code == 404


def test_a_g_strategy_id_needs_the_wave_flag_to_run():
    import asyncio

    from app.custom_strategies import resolver
    import app.platform.controls as controls

    class _User:
        tenant_id = 424242

    flags = {"strategy_templates": True, "screener_breakouts": True, "screener_waves": False}

    async def fake_flag(session, name, tenant_id=None):
        return flags.get(name, False)

    original = controls.flag_enabled
    controls.flag_enabled = fake_flag
    try:
        sid, _ = ids.adopt(G8, "15m")
        with pytest.raises(KeyError, match="not switched on"):
            asyncio.run(resolver.resolve_strategy(sid, _User(), None))
        flags["screener_waves"] = True
        assert isinstance(asyncio.run(resolver.resolve_strategy(sid, _User(), None)), TemplateStrategy)
    finally:
        controls.flag_enabled = original
