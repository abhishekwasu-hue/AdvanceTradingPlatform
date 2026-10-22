"""Wave templates (family G) build steps 3 + 4 (docs/design/WAVE_TEMPLATES_REVIEW.md W2-W4, findings WB4, WB5, WB6, WB7,
WB11, WB13): the wave level and ratio series, the anchored wave lines and their break, and the R7 extension of
RetestEntry (`of = "wave_line"` / `"wave_end"`).

Public, synthetic tests only: must-fire / must-not per level, ratio and line on hand-made counts and bars; causality
(the value at bar t equals the value computed on the data cut at t; a later change never alters an earlier bar); mirror;
RetestEntry's older choices byte-identical (an output hash pinned from the code before this step); the flag (hidden from
the registry, refused in screens and alert rules while off); the sparse series; the words. No G template is built here.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid

import numpy as np
import pandas as pd
import pytest

from app.price_action.waves import counter as wc
from app.price_action.waves import levels as wl
from app.price_action.waves import settings as ws
from app.price_action.waves.levels import WaveLevels, cached_wave_levels, wave_levels
from app.price_action.waves.series import wave_series
from app.quality.lookahead import synthetic_bars
from app.screener import compile_screen
from app.screener import waves as waves_gate
from app.screener.breakouts import wave_breaks as wb
from app.screener.parser import parse
from app.screener.registry import FUNCTIONS, NUM, RETEST_WAVE_OF, WAVE_FUNCTIONS, WAVES_FLAG
from app.screener.runtime import SymbolData, _breakout, evaluate
from app.strategy_templates import engine, language
from tests.test_wave2_counter import C, M, _mirror_bars, build, mirror, mirror_inner, path_bars

S = ws.WAVE_DEFAULTS
NAN = float("nan")
LEVEL_FUNCTIONS = {"WaveOrigin", "WaveEnd", "WaveInvalidation", "WaveCap", "WaveBeyondOrigin", "WaveRetrace", "WaveProjection",
                   "WaveRatio", "WaveLineLevel", "WaveLineBreak"}


# --- hand-made counts as one-bar level frames -----------------------------------------------------------------------------

def at(points, inner=None, context=1, running=None):
    """The count read on hand-made swings (tests/test_wave2_counter.build) as a WaveLevels frame whose last bar holds it;
    `running` moves the running end (default: the last point)."""
    swings, end, inn = build(points, inner)
    c = wc.count(swings, end, inn, context, S)
    t = end.confirm_bar
    main = np.full(t + 1, None, dtype=object)
    main[t] = c.main
    run = np.full(t + 1, np.nan)
    run[t] = points[-1] if running is None else running
    run_bar = np.full(t + 1, -1)
    run_bar[t] = end.bar
    return WaveLevels(pd.RangeIndex(t + 1), main, run, run_bar, sorted(inn, key=lambda s: (s.bar, s.confirm_bar))), t, c


def last(values):
    return float(values[-1])


def same(a, b):
    return (math.isnan(a) and math.isnan(b)) or a == pytest.approx(b)


# (name, wave ends, inner structure, expected state, type, invalidation)
INVALIDATION = [
    ("wave two: the start of wave one", [100, 110, 104], {0: M(100, 110)}, "IMP_2", "IMPULSE", 100),
    ("wave three before it passed the end of wave one: the start of wave one", [100, 110, 104, 108], {0: M(100, 110), 1: C(110, 104)},
     "IMP_3", "IMPULSE", 100),
    ("wave three past the end of wave one: the end of wave two", [100, 110, 104, 115], {0: M(100, 110), 1: C(110, 104)},
     "IMP_3", "IMPULSE", 104),
    ("wave four of an impulse: the end of wave one", [100, 110, 104, 120, 112],
     {0: M(100, 110), 1: C(110, 104), 2: M(104, 120), 3: C(120, 112)}, "IMP_4", "IMPULSE", 110),
    ("wave four of a diagonal: the end of wave two", [100, 110, 104, 120, 108],
     {0: M(100, 110), 1: C(110, 104), 2: M(104, 120), 3: C(120, 108)}, "IMP_4", "DIAGONAL", 104),
    ("the fifth wave: the end of wave four", [100, 110, 104, 120, 112, 125],
     {0: M(100, 110), 1: C(110, 104), 2: M(104, 120), 3: C(120, 112)}, "IMP_5", "IMPULSE", 112),
    ("zigzag B: the start of A", [110, 100, 105], {0: M(110, 100)}, "COR_B", "ZIGZAG", 110),
    ("flat B: no hard level", [110, 100, 109.5], {0: C(110, 100)}, "COR_B", "FLAT", NAN),
    ("flat B beyond the start of A: no hard level", [110, 100, 112], {0: C(110, 100)}, "COR_B", "FLAT", NAN),
    ("expanded flat C: the end of B", [110, 100, 112, 98], {0: C(110, 100), 1: C(100, 112)}, "COR_C", "EXPANDED_FLAT", 112),
    ("regular flat C: the end of B", [110, 100, 109.5, 101], {0: C(110, 100), 1: C(100, 109.5)}, "COR_C", "FLAT", 109.5),
    ("triangle D: the end of B", [110, 100, 108, 102, 106], {0: C(110, 100), 1: C(100, 108), 2: C(108, 102)}, "COR_D", "TRIANGLE", 108),
    ("triangle E: the end of C", [110, 100, 108, 102, 106, 103], {0: C(110, 100), 1: C(100, 108), 2: C(108, 102), 3: C(102, 106)},
     "COR_E", "TRIANGLE", 102),
    ("double three Y: the end of X", [110, 100, 104, 95], {0: [104, 102, 106], 1: C(100, 104)}, "COR_C", "DOUBLE", 104),
    ("wave A: nothing yet", [100, 110, 96], None, "COR_A", "UNCLEAR", NAN),
]


@pytest.mark.parametrize("name, points, inner, state, typ, want", INVALIDATION, ids=[c[0] for c in INVALIDATION])
def test_invalidation_per_count_type(name, points, inner, state, typ, want):
    lv, _, c = at(points, inner)
    assert (c.state, c.type) == (state, typ), name
    assert same(last(lv.invalidation()), want), name


@pytest.mark.parametrize("name, points, inner, state, typ, want", INVALIDATION, ids=[c[0] for c in INVALIDATION])
def test_invalidation_mirror(name, points, inner, state, typ, want):
    lv, _, c = at(mirror(points), mirror_inner(inner), -1)
    assert (c.state, c.type) == (state, typ), name
    assert same(last(lv.invalidation()), -want), name


def test_wave_three_moves_its_level_bar_by_bar_as_the_running_end_passes_wave_one():
    lv, t, _ = at([100, 110, 104, 108], {0: M(100, 110), 1: C(110, 104)})
    assert last(lv.invalidation()) == 100
    lv.running[t] = 110                              # equal is not beyond
    assert last(lv.invalidation()) == 100
    lv.running[t] = 110.5
    assert last(lv.invalidation()) == 104


def test_cap_is_evidence_not_an_invalidation():
    """WB5: wave three is never the shortest - with wave three shorter than wave one the fifth wave is capped at wave
    three's length beyond the end of wave four, on its winning side (above the running fifth wave), never a void level."""
    short = [100, 110, 104, 112, 110.5, 115]
    lv, _, c = at(short, {0: M(100, 110), 1: C(110, 104), 2: M(104, 112), 3: C(112, 110.5)})
    assert (c.state, c.type) == ("IMP_5", "IMPULSE")
    assert last(lv.cap()) == pytest.approx(118.5) and last(lv.invalidation()) == 110.5
    assert last(lv.cap()) > short[-1] > last(lv.invalidation())
    long_three = [100, 110, 104, 120, 112, 125]
    lv2, _, _ = at(long_three, {0: M(100, 110), 1: C(110, 104), 2: M(104, 120), 3: C(120, 112)})
    assert math.isnan(last(lv2.cap()))
    lv3, _, _ = at([100, 110, 104, 115], {0: M(100, 110), 1: C(110, 104)})     # not a fifth wave: no cap
    assert math.isnan(last(lv3.cap()))
    flipped, _, _ = at(mirror(short), mirror_inner({0: M(100, 110), 1: C(110, 104), 2: M(104, 112), 3: C(112, 110.5)}), -1)
    assert last(flipped.cap()) == pytest.approx(-118.5)


def test_flat_b_beyond_the_start_of_a_arms_while_the_type_stays_flat():
    """WB5 / WAVE-8: EXPANDED_FLAT is decided only once C passes A's end; during B the type is FLAT, and the "B beyond
    the start of A" state is its own series (G7 arms on it)."""
    lv, _, c = at([110, 100, 112], {0: C(110, 100)})
    assert (c.state, c.type) == ("COR_B", "FLAT") and bool(lv.beyond_origin()[-1])
    lv2, _, c2 = at([110, 100, 109.5], {0: C(110, 100)})
    assert c2.type == "FLAT" and not lv2.beyond_origin()[-1]
    lv3, _, _ = at([110, 100, 112, 98], {0: C(110, 100), 1: C(100, 112)})    # wave C runs with A: never "beyond"
    assert not lv3.beyond_origin()[-1]
    flipped, _, _ = at(mirror([110, 100, 112]), mirror_inner({0: C(110, 100)}), -1)
    assert bool(flipped.beyond_origin()[-1])


def test_origin_and_wave_ends():
    lv, _, _ = at([100, 110, 104, 120, 112], {0: M(100, 110), 1: C(110, 104), 2: M(104, 120), 3: C(120, 112)})
    assert last(lv.origin()) == 100
    assert [last(lv.end(w)) for w in ("1", "2", "3")] == [110, 104, 120]
    assert math.isnan(last(lv.end("4")))                         # wave four still runs: no end yet
    assert last(lv.end("current")) == 112
    assert math.isnan(last(lv.end("A")))                         # the other family
    cor, _, _ = at([110, 100, 109.5, 101], {0: C(110, 100), 1: C(100, 109.5)})
    assert (last(cor.end("A")), last(cor.end("B"))) == (100, 109.5) and math.isnan(last(cor.end("1")))
    with pytest.raises(ValueError):
        lv.end("5")


def test_ratio_levels_and_values_take_the_ratio_from_the_caller():
    """W3: the ratio thresholds are settings - the functions only apply the ratio they are given."""
    r_half, r_one, r_more = 0.5, 1.0, 1.618
    two, _, _ = at([100, 110, 104], {0: M(100, 110)})
    assert last(two.retrace(r_half)) == pytest.approx(105) and last(two.ratio()) == pytest.approx(0.6)
    three, _, _ = at([100, 110, 104, 115], {0: M(100, 110), 1: C(110, 104)})
    assert last(three.projection("1", r_one)) == pytest.approx(114)
    assert last(three.projection("1", r_more)) == pytest.approx(104 + 16.18)
    assert last(three.ratio("1")) == pytest.approx(1.1)
    assert math.isnan(last(three.projection("A", r_one))) and math.isnan(last(three.projection("3", r_one)))
    c_leg, _, _ = at([110, 100, 105, 97], {0: M(110, 100), 1: C(100, 105)})
    assert last(c_leg.projection("A", r_one)) == pytest.approx(95)               # C = A from the end of B, downwards
    assert last(c_leg.ratio("A")) == pytest.approx(0.8)
    a_only, _, _ = at([100, 110, 96], None)
    assert math.isnan(last(a_only.retrace(r_half))) and math.isnan(last(a_only.ratio()))
    flipped, _, _ = at(mirror([110, 100, 105, 97]), mirror_inner({0: M(110, 100), 1: C(100, 105)}), -1)
    assert last(flipped.projection("A", r_one)) == pytest.approx(-95) and last(flipped.ratio("A")) == pytest.approx(0.8)


def test_the_zigzag_limit_is_a_ratio_setting_not_a_hard_level():
    """WB5: a zigzag B is void beyond the start of A (WaveInvalidation); where it stops reading as a zigzag is the B/A
    setting, reached with the retracement level of that setting."""
    lv, _, c = at([110, 100, 105], {0: M(110, 100)})
    assert c.type == "ZIGZAG" and last(lv.invalidation()) == 110
    assert last(lv.retrace(S["zz_max_ba"])) == pytest.approx(100 + 10 * S["zz_max_ba"])


# --- anchored wave lines (W4) -----------------------------------------------------------------------------------------------

def test_point_lines_through_the_counts_own_wave_ends():
    lv, t, _ = at([100, 110, 104, 115], {0: M(100, 110), 1: C(110, 104)})
    values, anchors = lv.line("zero_two")
    assert anchors[-1] == (0, 100, 20, 104) and values[-1] == pytest.approx(100 + 0.2 * t)
    assert lv.line("two_four")[1][-1] is None and math.isnan(lv.line("one_three")[0][-1])   # those ends are not finished yet
    five, _, _ = at([100, 110, 104, 120, 112, 125], {0: M(100, 110), 1: C(110, 104), 2: M(104, 120), 3: C(120, 112)})
    assert five.line("two_four")[1][-1] == (20, 104, 40, 112) and five.line("one_three")[1][-1] == (10, 110, 30, 120)


def test_triangle_sides_including_a_flat_side():
    """WB6: a triangle's A-C and B-D sides from the count's own ends; a flat side is a line of no slope (no volume needed)."""
    inner = {0: C(110, 100), 1: C(100, 108), 2: C(108, 100), 3: C(100, 106)}
    lv, t, c = at([110, 100, 108, 100, 106, 101], inner)
    assert (c.state, c.type) == ("COR_E", "TRIANGLE")
    ac_values, ac = lv.line("one_three")
    assert ac[-1] == (10, 100, 30, 100) and ac_values[-1] == 100                # flat lower side
    bd_values, bd = lv.line("two_four")
    assert bd[-1] == (20, 108, 40, 106) and bd_values[-1] == pytest.approx(108 - 0.1 * (t - 20))


def test_the_corrective_channel_is_the_waves_start_side_line_through_its_inner_swings():
    """channel_B of a rising B: from B's start through the outermost of B's inner lows (every other one on or above it)."""
    inner = {0: M(110, 100), 1: [105, 101, 107, 104]}
    lv, t, c = at([110, 100, 108, 103], inner)
    assert c.state == "COR_C"
    values, anchors = lv.line("channel_B")
    assert anchors[-1] == (10, 100, 12, 101)                                     # slope 0.5 under the low at 104 (slope 1)
    assert values[-1] == pytest.approx(100 + 0.5 * (t - 10))
    assert lv.line("channel_C")[1][-1] is None                                   # C has no confirmed inner swing yet
    assert lv.line("channel_2")[1][-1] is None                                   # the other family
    flipped, _, _ = at(mirror([110, 100, 108, 103]), mirror_inner(inner), -1)
    assert flipped.line("channel_B")[1][-1] == (10, -100, 12, -101)              # a falling B: through its highs


def test_a_channel_reads_only_inner_swings_confirmed_by_the_bar():
    inner = {0: M(110, 100), 1: [105, 101, 107, 104]}
    swings, end, inn = build([110, 100, 108, 103], inner)
    late = [s._replace(confirm_bar=end.confirm_bar + 5) if s.price == 101 else s for s in inn]
    c = wc.count(swings, end, late, 1, S)
    t = end.confirm_bar
    main = np.full(t + 1, None, dtype=object)
    main[t] = c.main
    run, run_bar = np.full(t + 1, np.nan), np.full(t + 1, -1)
    run[t], run_bar[t] = 103, end.bar
    lv = WaveLevels(pd.RangeIndex(t + 1), main, run, run_bar, sorted(late, key=lambda s: s.bar))
    assert lv.line("channel_B")[1][-1] == (10, 100, 14, 104)                     # the low at 101 is not known yet


def test_unknown_names_are_refused():
    lv, _, _ = at([100, 110, 104], {0: M(100, 110)})
    for bad in (lambda: lv.line("zero_three"), lambda: lv.projection("Z", 1.0), lambda: lv.ratio("E"), lambda: lv.end_line("current")):
        with pytest.raises(ValueError):
            bad()


# --- the per-bar series ------------------------------------------------------------------------------------------------------

FIFTH_PATH = [100, 96, 98, 80, 85, 83.5, 90, 88.5, 95, 93, 94.2, 91.5, 96, 94.5, 99, 97.5, 104, 102.5, 103.2, 101.5, 106]


def test_series_on_bars_wave_two_three_four_five():
    bars = path_bars(FIFTH_PATH)
    lv = wave_levels(bars, 1)
    inv, cap, origin = lv.invalidation(), lv.cap(), lv.origin()
    labels = [(r.label if r is not None else None) for r in lv.main]
    two = labels.index("IMP_2", next(i for i, r in enumerate(lv.main) if r is not None and r.points[0] < 85))
    assert origin[two] == pytest.approx(79.9) and inv[two] == pytest.approx(79.9)
    three = [t for t in range(two, len(bars)) if labels[t] == "IMP_3"]
    passed = [t for t in three if lv.running[t] > lv.main[t].points[1]]
    before = [t for t in three if t not in passed]
    assert before and passed
    assert all(inv[t] == pytest.approx(79.9) for t in before) and all(inv[t] == pytest.approx(91.4) for t in passed)
    four = [t for t in range(len(bars)) if labels[t] == "IMP_4" and lv.main[t].points[0] < 85]
    assert four and all(inv[t] == pytest.approx(95.1) for t in four)
    five = [t for t in range(len(bars)) if labels[t] == "IMP_5"]
    assert five and all(cap[t] == pytest.approx(114.1) and inv[t] == pytest.approx(101.4) for t in five)
    assert np.isnan(cap[: five[0]]).all()
    # the running end on the bar a count is read equals the count's own running end
    counts = [c for c in wave_series(bars, 1).counts if c.main is not None]
    assert counts and all(lv.running[c.bar] == c.main.points[-1] for c in counts)


def _frame(lv: WaveLevels, ratio: float) -> pd.DataFrame:
    cols = {"origin": lv.origin(), "inv": lv.invalidation(), "cap": lv.cap(), "beyond": lv.beyond_origin().astype(float),
            "retrace": lv.retrace(ratio), "projection": lv.projection("previous", ratio), "ratio": lv.ratio(), "ratio_a": lv.ratio("A")}
    for w in wl.END_CHOICES:
        cols[f"end_{w}"] = lv.end(w)
    for name in wl.LINES:
        values, anchors = lv.line(name)
        cols[f"line_{name}"] = values
        cols[f"anchor_{name}"] = pd.Series([repr(a) for a in anchors], dtype=object)
    return pd.DataFrame(cols, index=lv.index)


@pytest.mark.parametrize("degree", (1, 2, 3))
def test_truncation_over_many_t(degree):
    """No look-ahead: every level, ratio and line (with its anchors) at bar t equals the value computed on the data cut
    at t."""
    bars = synthetic_bars(900, seed=5)
    full = _frame(wave_levels(bars, degree), 0.5)
    for t in list(range(60, len(bars), 41)) + [len(bars) - 1]:
        cut = _frame(wave_levels(bars.iloc[: t + 1], degree), 0.5)
        pd.testing.assert_frame_equal(cut, full.iloc[: t + 1])


def test_truncation_on_the_fifth_wave_path_with_its_cap():
    bars = path_bars(FIFTH_PATH)
    full = _frame(wave_levels(bars, 1), 0.618)
    assert np.isfinite(full["cap"]).any()
    for t in range(40, len(bars), 3):
        pd.testing.assert_frame_equal(_frame(wave_levels(bars.iloc[: t + 1], 1), 0.618), full.iloc[: t + 1])


def test_a_later_change_never_alters_earlier_values():
    bars = synthetic_bars(900, seed=3)
    base = _frame(wave_levels(bars, 1), 0.5)
    for t in (300, 500, 700):
        moved = bars.copy()
        shift = np.cumsum(np.random.default_rng(t).normal(0, 0.8, len(bars) - t - 1))
        for col in ("open", "high", "low", "close"):
            moved.iloc[t + 1:, moved.columns.get_loc(col)] = bars[col].to_numpy()[t + 1:] + shift
        moved["high"] = moved[["open", "high", "close"]].max(axis=1)
        moved["low"] = moved[["open", "low", "close"]].min(axis=1)
        pd.testing.assert_frame_equal(_frame(wave_levels(moved, 1), 0.5).iloc[: t + 1], base.iloc[: t + 1])


def test_mirror_on_bars():
    bars = synthetic_bars(900, seed=11)
    pivot = 200.0
    up, down = wave_levels(bars, 1), wave_levels(_mirror_bars(bars, pivot), 1)
    for a, b in ((up.origin(), down.origin()), (up.invalidation(), down.invalidation()), (up.end("A"), down.end("A")),
                 (up.retrace(0.5), down.retrace(0.5)), (up.line("channel_current")[0], down.line("channel_current")[0]),
                 (up.line("zero_two")[0], down.line("zero_two")[0])):
        np.testing.assert_allclose(pivot - a, b, equal_nan=True)
    np.testing.assert_allclose(up.ratio(), down.ratio(), equal_nan=True)
    assert (up.beyond_origin() == down.beyond_origin()).all()


def test_cached_levels_share_one_count_per_frame_and_degree():
    bars = synthetic_bars(500, seed=2)
    assert cached_wave_levels(bars, 1) is cached_wave_levels(bars.copy(), 1)
    assert cached_wave_levels(bars, 1) is not cached_wave_levels(bars.iloc[:-1], 1)
    with pytest.raises(ValueError):
        cached_wave_levels(bars, 0)


# --- the break of a wave line (W4) and the retest (R7) -----------------------------------------------------------------------

def _bars(closes, wick=0.2):
    c = np.asarray(closes, float)
    o = np.r_[c[0], c[:-1]]
    idx = pd.date_range("2026-01-05 03:45", periods=len(c), freq="15min", tz="UTC")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + wick, "low": np.minimum(o, c) - wick, "close": c}, index=idx)


def _breakout_bars(f):
    from app.screener.breakouts.params import settings as bp_settings
    from app.screener.breakouts.screens import Bars
    b = Bars(f, bp_settings())
    return b, b.side(1), b.side(-1)


def test_line_break_fires_once_per_line_after_arming_with_no_volume():
    closes = [100.0] * 30 + [100.5, 101, 103, 104, 103, 104.5, 99, 103.5]
    f = _bars(closes)                                                     # no volume column at all
    b, up, down = _breakout_bars(f)
    flat = (0, 102.0, 1, 102.0)                                            # a flat side at 102
    values, anchors = np.full(b.n, 102.0), [flat] * b.n
    hits = wb.line_breaks(b, up, values, anchors, 0)
    assert [t for t, _ in hits] == [32]                                   # the first close above, once for the line
    assert wb.break_series(b.n, hits).sum() == 1
    assert [t for t, _ in wb.line_breaks(b, down, values, anchors, 0)] == [36]
    big = wb.line_breaks(b, up, values, anchors, 50.0)                    # by k ATR: never that far
    assert big == []


def test_a_line_already_beyond_when_it_becomes_known_waits_for_a_close_back():
    closes = [100.0] * 30 + [105, 106, 101, 104]
    b, up, _ = _breakout_bars(_bars(closes))
    line = (0, 102.0, 1, 102.0)
    anchors = [None] * 31 + [line] * (b.n - 31)                         # known from bar 31, when the close is 106
    values = np.where([a is not None for a in anchors], 102.0, np.nan)
    assert [t for t, _ in wb.line_breaks(b, up, values, anchors, 0)] == [33]


def test_retest_of_a_broken_line_reads_the_broken_lines_value_at_each_bar():
    rising = (0, 100.0, 10, 101.0)                                      # +0.1 per bar
    closes = [100.0] * 30 + [101.0, 104.0, 107.0, 108.0, 106.5, 105.0, 104.6, 106.0, 108.0]
    f = _bars(closes, wick=0.1)
    b, up, _ = _breakout_bars(f)
    values = np.array([wl.project(rising, t) for t in range(b.n)])
    breaks = wb.line_breaks(b, up, values, [rising] * b.n, 0)
    assert breaks[0][0] == 31
    out = wb.retests(b, up, breaks, 10, False, 1.0)
    hits = list(np.flatnonzero(out))
    assert len(hits) == 1 and closes[hits[0]] >= wl.project(rising, hits[0])
    # a close back below the line before the retest ends the watch
    failed = closes[:34] + [102.0, 106.0, 105.0]
    fb, fup, _ = _breakout_bars(_bars(failed, wick=0.1))
    fvalues = np.array([wl.project(rising, t) for t in range(fb.n)])
    assert not wb.retests(fb, fup, wb.line_breaks(fb, fup, fvalues, [rising] * fb.n, 0), 10, False, 1.0).any()


def test_wave_functions_through_screenql_equal_the_levels():
    df = synthetic_bars(900, seed=9)
    lv = wave_levels(df, 1)
    data = SymbolData("S", {"5m": df})

    def run(text):
        return evaluate(parse(text), data, base_tf="5m").to_numpy()
    np.testing.assert_allclose(run("WaveOrigin(1)"), lv.origin(), equal_nan=True)
    np.testing.assert_allclose(run('WaveEnd(1, "B")'), lv.end("B"), equal_nan=True)
    np.testing.assert_allclose(run("WaveInvalidation(1)"), lv.invalidation(), equal_nan=True)
    np.testing.assert_allclose(run("WaveRetrace(1, 0.5)"), lv.retrace(0.5), equal_nan=True)
    np.testing.assert_allclose(run('WaveProjection(1, "A", 1)'), lv.projection("A", 1.0), equal_nan=True)
    np.testing.assert_allclose(run('WaveRatio(1, "previous")'), lv.ratio(), equal_nan=True)
    np.testing.assert_allclose(run('WaveLineLevel(1, "channel_B")'), lv.line("channel_B")[0], equal_nan=True)
    assert (run("WaveBeyondOrigin(1)") == lv.beyond_origin()).all()
    breaks = run('WaveLineBreak(1, "channel_current", "up", 0)')
    assert breaks.dtype == bool and breaks.any()
    with_param = compile_screen("WaveRetrace(1, $r) > 0", base_tf="5m", params={"r": 0.5})[1]
    assert with_param.ok
    for bad in ("WaveRetrace(1, close)", 'WaveEnd(1, "E")', 'WaveLineLevel(1, "zero_three")', "WaveOrigin(0)", "WaveRetrace(1)",
                'WaveLineBreak(1, "zero_two", "sideways", 0)'):
        assert not compile_screen(bad, base_tf="5m")[1].ok, bad


def test_wave_retest_through_screenql_is_causal():
    df = synthetic_bars(700, seed=7)
    text = 'RetestEntry("up", "wave_line", 20, FALSE, 1, "channel_current", "A", 0)'
    full = evaluate(parse(text), SymbolData("S", {"5m": df}), base_tf="5m").to_numpy()
    assert full.any()
    for t in range(200, len(df), 47):
        cut = evaluate(parse(text), SymbolData("S", {"5m": df.iloc[: t + 1]}), base_tf="5m").to_numpy()
        assert (cut == full[: t + 1]).all(), t
    end = 'RetestEntry("down", "wave_end", 20, FALSE, 1, "channel_B", "B", 0)'
    assert evaluate(parse(end), SymbolData("S", {"5m": df}), base_tf="5m").dtype == bool


# --- R7: RetestEntry's older choices stay byte-identical --------------------------------------------------------------------

RETEST_CASES = ['RetestEntry("up")', 'RetestEntry("down")', 'RetestEntry("up", "range", 10, TRUE)', 'RetestEntry("down", "range", 20, FALSE)',
                'RetestEntry("up", "swing", 20, FALSE)', 'RetestEntry("down", "swing", 15, TRUE)', 'RetestEntry("up", "donchian", 20, FALSE)',
                'RetestEntry("down", "donchian", 20, TRUE)', 'RetestEntry("up", "trendline", 20, TRUE)', 'RetestEntry("down", "trendline", 20, FALSE)']
# SHA-256 of every case's signal bars on three data sets, computed with the code BEFORE this step (branch
# claude/wave2-counter-recipe, 5a435ee1): 51 retest bars in all.
RETEST_SHA256 = "94bb623ce12c18d10615dc930fa2255291455db2463da83ba49b904d7cdf44dd"


def test_retest_entry_older_choices_are_byte_identical():
    out = {}
    for seed, tf in ((7, "5m"), (11, "5m"), (3, "1d")):
        df = synthetic_bars(900, tf=tf, seed=seed)
        for case in RETEST_CASES:
            v = evaluate(parse(case), SymbolData("S", {tf: df}), base_tf=tf)
            out[f"{seed}|{tf}|{case}"] = [int(x) for x in np.flatnonzero(v.to_numpy(bool))]
    assert sum(len(v) for v in out.values()) == 51
    assert hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest() == RETEST_SHA256


def test_retest_entry_older_choices_run_the_b9_path_and_keep_their_history_need():
    df = synthetic_bars(900, seed=11)
    got = evaluate(parse('RetestEntry("up", "donchian", 20, FALSE)'), SymbolData("S", {"5m": df}), base_tf="5m")
    want = _breakout(df, "B9", "up", {"retest_of": "donchian", "retest_max_bars": 20, "retest_reversal": False})
    assert (got.to_numpy() == want.to_numpy()).all()
    old = compile_screen('RetestEntry("up", "range", 10, TRUE)', base_tf="15m")[1]
    wave = compile_screen('RetestEntry("up", "wave_line", 10, TRUE, 3, "channel_B")', base_tf="15m")[1]
    assert old.ok and wave.ok and old.lookback["15m"] >= 250 and wave.lookback["15m"] > old.lookback["15m"]
    for bad in ('RetestEntry("up", "wave_line", 10, TRUE, 0)', 'RetestEntry("up", "wave_end", 10, TRUE, 1, "channel_B", "current")',
                'RetestEntry("up", "wave_line", 10, TRUE, 1, "two_three")'):
        assert not compile_screen(bad, base_tf="15m")[1].ok, bad


# --- the flag (WB13), the sparse series (WB4), the words (WB7), the area default (WB11) ------------------------------------

def test_every_new_function_is_behind_the_flag_and_numbers_are_sparse():
    assert LEVEL_FUNCTIONS <= set(WAVE_FUNCTIONS)
    assert all(FUNCTIONS[n].flag == WAVES_FLAG for n in LEVEL_FUNCTIONS)
    numbers = {n for n in WAVE_FUNCTIONS if FUNCTIONS[n].returns == NUM}
    assert numbers and numbers <= engine.SPARSE_SERIES
    assert all(FUNCTIONS[n].degree_min_bars for n in LEVEL_FUNCTIONS)


def test_the_gate_flags_retest_entry_only_for_its_wave_choices():
    assert RETEST_WAVE_OF == wb.WAVE_RETEST_OF
    assert waves_gate.flagged_calls(parse('RetestEntry("up", "range", 10, TRUE)')) == []
    assert waves_gate.flagged_calls(parse('RetestEntry("up", "wave_line", 10, TRUE, 1, "channel_B")')) == ["RetestEntry"]
    assert waves_gate.flagged_calls(parse('RetestEntry("up", "wave_end")')) == ["RetestEntry"]
    with_param = parse('RetestEntry("up", $of)')
    assert waves_gate.flagged_calls(with_param, {"of": "swing"}) == []
    assert waves_gate.flagged_calls(with_param, {"of": "wave_end"}) == ["RetestEntry"]
    assert waves_gate.flagged_calls(with_param) == ["RetestEntry"]           # unresolved: treated as a wave choice
    legacy = waves_gate.legacy_retest_spec()
    assert [a.name for a in legacy.args] == ["side", "of", "bars", "reversal"]
    assert not set(RETEST_WAVE_OF) & set(legacy.args[1].choices) and legacy.degree_min_bars == ()
    hidden = waves_gate.hide({"RetestEntry": {}})["RetestEntry"]
    assert hidden["args"] == ["side", "of", "bars", "reversal"] and "wave" not in hidden["doc"]


def test_the_copilot_registry_hides_the_wave_choices_while_off():
    from app.screener import nl
    off, on = nl.registry_text([WAVES_FLAG]), nl.registry_text([])
    assert "WaveLineBreak" not in off and "WaveLineBreak" in on
    retest_off = next(line for line in off.splitlines() if line.startswith("- RetestEntry("))
    retest_on = next(line for line in on.splitlines() if line.startswith("- RetestEntry("))
    assert "line=" not in retest_off and "line=" in retest_on


def _org(tag, on):
    from tests.test_n1_e1_execution_core import _flag
    from tests.test_trading_worker import _tenant
    t = _tenant(f"wave3-{tag}-{uuid.uuid4().hex[:6]}@example.com", with_credentials=False)
    flags = ("screener_v2", "screener_intrabar", "screener_breakouts", "screener_breakouts_bk4") + ((WAVES_FLAG,) if on else ())
    for name in flags:
        _flag(name, t["tenant_id"])
    return t


def test_flag_off_hides_and_refuses_the_levels_lines_and_wave_retest():
    from tests.test_auth_api import client
    off, on = _org("off", False), _org("on", True)
    entries_off = client.get("/api/screener/registry", headers=off["headers"]).json()["entries"]
    entries_on = client.get("/api/screener/registry", headers=on["headers"]).json()["entries"]
    assert not LEVEL_FUNCTIONS & set(entries_off) and LEVEL_FUNCTIONS <= set(entries_on)
    assert not set(RETEST_WAVE_OF) & set(entries_off["RetestEntry"]["choices"]["of"])
    assert set(RETEST_WAVE_OF) <= set(entries_on["RetestEntry"]["choices"]["of"])
    for source in ('close > WaveInvalidation(1)', 'WaveLineBreak(1, "channel_B", "down", 0)',
                   'RetestEntry("down", "wave_line", 10, TRUE, 1, "channel_B")'):
        refused = client.post("/api/screener/validate", headers=off["headers"], json={"source": source, "base_tf": "15m"}).json()
        assert not refused["ok"] and WAVES_FLAG in json.dumps(refused["problems"]), source
        assert client.post("/api/screener/validate", headers=on["headers"], json={"source": source, "base_tf": "15m"}).json()["ok"], source
    plain = client.post("/api/screener/validate", headers=off["headers"],
                        json={"source": 'RetestEntry("down", "range", 10, TRUE)', "base_tf": "15m"}).json()
    assert plain["ok"], plain                                               # the older choices are never gated by the wave flag
    rule = {"name": "w", "kind": "instrument", "symbol": "ABC", "condition": 'close < WaveLineLevel(1, "two_four")', "base_tf": "15m"}
    r = client.post("/api/alerts/rules", headers=off["headers"], json=rule)
    assert r.status_code == 422 and WAVES_FLAG in r.text
    r = client.post("/api/alerts/rules", headers=on["headers"], json={**rule, "fire_on": "intrabar"})
    assert r.status_code == 422 and "bar close only" in r.text
    r = client.post("/api/alerts/rules", headers=on["headers"], json=rule)
    assert r.status_code in (200, 201), r.text


def test_line_and_level_words_pass_the_guard():
    assert set(wl.LINES) <= set(language.LINE_WORDS)
    for phrase in list(language.LINE_WORDS.values()) + list(language.TOKENS.values()):
        assert language.guard(phrase) == [], phrase
    assert language.wave_words("channel_B") == "the channel of wave B"
    assert language.wave_words("two_four").startswith("the line through")


def test_area_default_is_swing_zones():
    """WB11 / WAVE-3: market zones are stored snapshots behind an OFF flag and absent in older backtests, so the wave
    templates' area defaults to swing zones (computed from the bars)."""
    assert ws.AREA_SOURCE_DEFAULT == "swing_zones" and ws.AREA_SOURCE_DEFAULT in ws.AREA_SOURCES
    assert set(ws.AREA_SOURCES) == {"market_zones", "swing_zones", "none"}
