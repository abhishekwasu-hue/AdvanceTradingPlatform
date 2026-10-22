"""Wave templates (family G) step 2, W1: the causal wave counter (docs/design/WAVE_TEMPLATES_REVIEW.md WB1, WB2, WB12,
WB14; owner questions WAVE-1, WAVE-5, WAVE-6).

Public, synthetic tests only (WB14): the generic rules and `read_counter` per count type on hand-made point sequences;
the counter's main / alternate / clarity per count type on hand-made swings (must label / must not label); the per-bar
series on bars built from a hand-made path and on the no-look-ahead harness's session bars - truncation over many t, a
later change never rewrites an earlier bar, recompute only on a newly confirmed swing, mirror; the import allow-list
(WB2) and "no ratio literal in the reading rules" (WAVE-6)."""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
import pytest

from app.price_action.daily_trend import rules as R
from app.price_action.daily_trend.legs import read_counter
from app.price_action.daily_trend.settings import PATTERN_DEFAULTS
from app.price_action.waves import counter as wc
from app.price_action.waves import settings as ws
from app.price_action.waves.series import WAVE_DEGREES, _pivot_frame, _swings, cached_wave_series, wave_series
from app.quality.lookahead import synthetic_bars

WAVES_DIR = Path(__file__).resolve().parents[1] / "app" / "price_action" / "waves"
S = ws.WAVE_DEFAULTS
STEP = 10


# --- hand-made swings ---------------------------------------------------------------------------------------------------

def motive_inner(a: float, b: float) -> List[float]:
    """Four inner points that make a -> b a five-wave impulse one degree lower."""
    return [a + (b - a) * f for f in (0.4, 0.25, 0.7, 0.55)]


def corrective_inner(a: float, b: float) -> List[float]:
    """Two inner points that make a -> b a three-wave zigzag one degree lower."""
    return [a + (b - a) * f for f in (0.6, 0.3)]


def build(points: Sequence[float], inner: Optional[Dict[int, List[float]]] = None):
    """Wave ends (the last one is the running end) -> (confirmed swings, running end, inner swings). Wave i spans bars
    i*STEP .. (i+1)*STEP; its inner swings sit inside it, each confirmed on the next bar; the count is read two bars after
    the running end's bar."""
    sw = []
    for i, p in enumerate(points):
        up = (points[1] < points[0]) if i == 0 else (p > points[i - 1])
        sw.append(wc.Swing("H" if up else "L", float(p), i * STEP, i * STEP + 3))
    t = (len(points) - 1) * STEP + 2
    end = wc.Swing(sw[-1].kind, sw[-1].price, sw[-1].bar, t)
    inn = []
    for w, prices in (inner or {}).items():
        prev = sw[w].price
        for j, p in enumerate(prices):
            bar = sw[w].bar + 1 + j
            inn.append(wc.Swing("H" if p > prev else "L", float(p), bar, bar + 1))
            prev = p
    return sw[:-1], end, sorted(inn, key=lambda s: s.bar)


def read(points, inner=None, context=0, settings=S) -> wc.WaveCount:
    swings, end, inn = build(points, inner)
    return wc.count(swings, end, inn, context, settings)


def mirror(points):
    return [-p for p in points]


def mirror_inner(inner):
    return {k: [-p for p in v] for k, v in (inner or {}).items()}


M, C = motive_inner, corrective_inner
# (name, wave ends, inner structure, context, expected state, type, clarity, alternate)
CASES = [
    ("wave two after a clear wave one", [100, 110, 104], {0: M(100, 110)}, 1, "IMP_2", "IMPULSE", "ALT_DISAGREES", "COR_B"),
    ("wave three", [100, 110, 104, 115], {0: M(100, 110), 1: C(110, 104)}, 1, "IMP_3", "IMPULSE", "ALT_DISAGREES", "COR_C"),
    ("wave four", [100, 110, 104, 120, 112], {0: M(100, 110), 1: C(110, 104), 2: M(104, 120), 3: C(120, 112)}, 1,
     "IMP_4", "IMPULSE", "ALT_AGREES", "IMP_2"),
    ("wave five", [100, 110, 104, 120, 112, 125], {0: M(100, 110), 1: C(110, 104), 2: M(104, 120), 3: C(120, 112)}, 1,
     "IMP_5", "IMPULSE", "ALT_AGREES", "IMP_3"),
    ("diagonal: wave four overlaps wave one", [100, 110, 104, 120, 108], {0: M(100, 110), 1: C(110, 104), 2: M(104, 120),
                                                                          3: C(120, 108)}, 1, "IMP_4", "DIAGONAL", "ALT_AGREES", "IMP_2"),
    ("zigzag wave B against the larger trend", [110, 100, 105], {0: M(110, 100)}, 1, "COR_B", "ZIGZAG", "ALT_DISAGREES", "IMP_2"),
    ("flat wave B (a three-wave A, B near A's start)", [110, 100, 109.5], {0: C(110, 100)}, 1, "COR_B", "FLAT", "ALT_AGREES", "COR_B"),
    ("flat wave B beyond the start of A", [110, 100, 112], {0: C(110, 100)}, 1, "COR_B", "FLAT", "CLEAR", None),
    ("expanded flat: C beyond A's end", [110, 100, 112, 98], {0: C(110, 100), 1: C(100, 112)}, 1, "COR_C", "EXPANDED_FLAT",
     "ALT_DISAGREES", "COR_B"),
    ("regular flat wave C", [110, 100, 109.5, 101], {0: C(110, 100), 1: C(100, 109.5)}, 1, "COR_C", "FLAT", "ALT_DISAGREES", "COR_B"),
    ("contracting triangle wave D", [110, 100, 108, 102, 106], {0: C(110, 100), 1: C(100, 108), 2: C(108, 102)}, 1,
     "COR_D", "TRIANGLE", "ALT_AGREES", "COR_B"),
    ("contracting triangle wave E", [110, 100, 108, 102, 106, 103], {0: C(110, 100), 1: C(100, 108), 2: C(108, 102), 3: C(102, 106)}, 1,
     "COR_E", "TRIANGLE", "ALT_AGREES", "COR_C"),
    ("double three: Y after a complete W and a three-wave X", [110, 100, 104, 95], {0: [104, 102, 106], 1: C(100, 104)}, 1,
     "COR_C", "DOUBLE", "ALT_DISAGREES", "COR_B"),
]


@pytest.mark.parametrize("name, points, inner, context, state, typ, clarity, alt", CASES, ids=[c[0] for c in CASES])
def test_count_per_type_must_label(name, points, inner, context, state, typ, clarity, alt):
    c = read(points, inner, context)
    assert (c.state, c.type, c.clarity, c.alt_state) == (state, typ, clarity, alt), name
    assert c.main is not None and c.main.points[-1] == points[-1]


@pytest.mark.parametrize("name, points, inner, context, state, typ, clarity, alt", CASES, ids=[c[0] for c in CASES])
def test_mirror_gives_the_mirrored_count(name, points, inner, context, state, typ, clarity, alt):
    up, down = read(points, inner, context), read(mirror(points), mirror_inner(inner), -context)
    assert (down.state, down.type, down.clarity, down.alt_state) == (up.state, up.type, up.clarity, up.alt_state)
    assert down.main is not None and up.main is not None
    assert (down.main.direction, down.main.trend) == (-up.main.direction, -up.main.trend)


def test_must_not_label():
    # wave two beyond the start of wave one: never wave two (the impulse rule), whatever the context
    assert "IMP_2" not in {r.label for r in read([100, 110, 99], {0: M(100, 110)}, 1).readings}
    # wave four into wave one's territory: never an impulse (a diagonal at most)
    c = read([100, 110, 104, 120, 108], {0: M(100, 110), 1: C(110, 104), 2: M(104, 120), 3: C(120, 108)}, 1)
    assert not any(r.label == "IMP_4" and r.type == "IMPULSE" for r in c.readings)
    # a zigzag B at or beyond the zigzag limit of A is not a zigzag (but a flat B may go beyond A's start)
    assert not any(r.type == "ZIGZAG" for r in read([110, 100, 109.5], {0: M(110, 100)}, 1).readings)
    assert read([110, 100, 112], {0: C(110, 100)}, 1).type == "FLAT"
    # a wave one whose inner structure reads three waves is not wave one of an impulse (5-3-5-3-5)
    assert not any(r.type == "IMPULSE" and r.label == "IMP_2" for r in read([100, 110, 104], {0: C(100, 110)}, 1).readings)
    # a double three needs a complete correction for W: a five-wave W is not a W
    assert not any(r.type == "DOUBLE" for r in read([110, 100, 104, 95], {0: M(110, 100), 1: C(100, 104)}, 1).readings)
    # X still running: a double three is only ever the alternate
    assert read([110, 100, 104], {0: [104, 102, 106]}, 1).type != "DOUBLE"


def test_context_decides_and_a_tie_is_unclear():
    """WAVE-1: with the larger-degree swing up a first leg up is wave one, against it wave A; with no larger-degree swing
    known, wave two and wave B from the same origin imply opposite larger directions: UNCLEAR (templates never fire on it)."""
    pts, inner = [100, 110, 104], {0: M(100, 110)}
    assert read(pts, inner, 1).state == "IMP_2"
    assert read(pts, inner, -1).state == "COR_B"
    tie = read(pts, inner, 0)
    assert (tie.state, tie.type, tie.clarity) == ("UNCLEAR", "UNCLEAR", "UNCLEAR") and tie.main is None
    assert tie.alternate is not None and tie.alternate.label == "IMP_2"         # the top reading stays visible as evidence


def test_the_oldest_valid_origin_wins():
    c = read([100, 110, 104, 120, 112], {0: M(100, 110), 1: C(110, 104), 2: M(104, 120), 3: C(120, 112)}, 1)
    assert c.main is not None and c.main.points[0] == 100 and c.alt_state == "IMP_2"


def test_clarity_needs_the_inner_structure_of_every_finished_wave():
    """WAVE-5: a wave with no inner swings fits either role but is not clear."""
    assert read([100, 110, 104], {0: M(100, 110)}, 1).clarity == "ALT_DISAGREES"
    bare = read([100, 110, 104], None, 1)
    assert (bare.state, bare.clarity) == ("IMP_2", "UNCLEAR")


def test_wave_a_has_no_type_yet():
    c = read([100, 110, 96], None, 1)
    assert (c.state, c.type) == ("COR_A", "UNCLEAR")


def test_inner_swings_are_read_only_once_confirmed():
    swings, end, inner = build([100, 110, 104], {0: M(100, 110)})
    late = [s._replace(confirm_bar=end.confirm_bar + 1) for s in inner]          # known only after the bar
    assert wc.count(swings, end, late, 1, S).clarity == "UNCLEAR"
    assert wc.count(swings, end, inner, 1, S).clarity == "ALT_DISAGREES"


def test_the_ratios_are_settings():
    """WAVE-6: B at 0.85 of A is a zigzag with today's values and a flat with a lower zigzag limit."""
    pts = [110, 100, 108.5]
    assert read(pts, {0: M(110, 100)}, 1).type == "ZIGZAG"
    tight = ws.settings({"zz_max_ba": 0.8})
    assert not any(r.type == "ZIGZAG" for r in read(pts, {0: M(110, 100)}, 1, tight).readings)


# --- the generic rules and read_counter per count type (public, WB14) ------------------------------------------------

@pytest.mark.parametrize("prices, family", [
    ([0, 10, 5, 20, 15, 25], "impulse"),
    ([0, 6, 4, 10, 8, 14, 10, 22, 18, 30, 25, 36, 32, 40, 37, 44, 41, 48], "impulse"),
    ([0, 10, 5, 20, 16, 24, 20, 35, 30, 40], "impulse"),                       # an extended (nine-wave) impulse
    ([0, 10, 4, 12, 8, 13], "diagonal"),                                        # contracting
    ([0, 5, 2, 9, 4, 14], "diagonal"),                                          # expanding
    ([10, 0, 5, -3], "zigzag"),
    ([10, 0, 9.5, -0.5], "flat"),                                               # regular
    ([10, 0, 11, 2], "flat"),                                                   # running
    ([10, 0, 11, -2], "flat"),                                                  # expanded
    ([10, 0, 8, 2, 6, 4], "triangle"),                                          # contracting
    ([10, 8, 11, 6, 13, 4], "triangle"),                                        # expanding
    ([100, 90, 95, 85, 92, 82, 87, 77], "combination"),
    ([0, 1], "single"),
])
def test_read_counter_per_count_type(prices, family):
    assert read_counter(prices)["family"] == family
    assert read_counter([-p for p in prices])["family"] == family              # mirror


@pytest.mark.parametrize("x, sub", [([10, 0, 9.5, -0.5], "regular"), ([10, 0, 11, 2], "running"), ([10, 0, 11, -2], "expanded")])
def test_flat_sub_types(x, sub):
    assert R.flat(x, True)[2]["sub"] == sub


def test_hard_rules_per_count_type():
    # impulse: wave two may reach (not pass) wave one's start; wave four must stay beyond wave one's end
    assert R.impulse([100, 110, 100], done=False) and not R.impulse([100, 110, 99.9], done=False)
    assert R.impulse([100, 110, 104, 120, 110.1], done=False) and not R.impulse([100, 110, 104, 120, 110], done=False)
    # wave three is never the shortest: checked once five waves exist (a cap on wave five, not a level)
    assert R.impulse([100, 110, 104, 112, 110.5], done=False) and R.impulse([100, 110, 104, 112, 110.5, 117], done=False)
    assert not R.impulse([100, 110, 104, 112, 110.5, 120], done=False)          # wave five longer than a shorter wave three
    # a diagonal's wave four may overlap wave one but never passes wave two's end
    assert R.wedge([100, 110, 104, 120, 108], done=False, kind="expanding")
    assert not R.wedge([100, 110, 104, 120, 103], done=False, kind="expanding")
    # a zigzag's B stays below the zigzag limit of A (a setting); a flat's B has no upper limit
    assert R.zigzag([110, 100, 108.9], False, PATTERN_DEFAULTS)[0] and not R.zigzag([110, 100, 109], False, PATTERN_DEFAULTS)[0]
    assert R.flat([110, 100, 125, 90], False, PATTERN_DEFAULTS)[0]
    # an expanded flat is known only once C has gone beyond A's end
    assert R.flat([110, 100, 111, 101], False, PATTERN_DEFAULTS)[2]["sub"] != "expanded"
    assert R.flat([110, 100, 111, 99], False, PATTERN_DEFAULTS)[2]["sub"] == "expanded"


def test_read_counter_settings_argument_keeps_the_daily_trend_unchanged():
    borderline = [10, 0, 8.5, -3]                                               # B/A 0.85
    assert read_counter(borderline) == read_counter(borderline, PATTERN_DEFAULTS) == {"family": "zigzag", "n": 3}
    assert read_counter(borderline, {**PATTERN_DEFAULTS, "zz_max_ba": 0.8}) == {"family": "flat", "n": 3}
    assert read_counter(borderline) == {"family": "zigzag", "n": 3}             # the memo keeps them apart


def test_wave_settings_are_todays_values_with_bounds():
    assert ws.WAVE_DEFAULTS == PATTERN_DEFAULTS
    assert ws.validate({"zz_max_ba": 0.8})[1] == []
    for bad in ({"zz_max_ba": 1.5}, {"flat_min_ba": "x"}, {"nope": 1.0}, {"zz_max_ba": 0.5, "flat_min_ba": 0.6}):
        assert ws.validate(bad)[1], bad
    with pytest.raises(ValueError):
        ws.settings({"flat_b_max": 0.0})


# --- the per-bar series ---------------------------------------------------------------------------------------------------

def path_bars(points, step=0.5, wick=0.1, lead=30, start="2026-01-05 03:45"):
    """Closed 15-minute bars walking the polyline `points` (`step` per bar), each bar `wick` above / below its body."""
    closes = [float(points[0])] * lead
    for a, b in zip(points, points[1:]):
        k = max(1, int(round(abs(b - a) / step)))
        closes += list(np.linspace(a, b, k + 1)[1:])
    c = np.array(closes, float)
    o = np.r_[c[0], c[:-1]]
    idx = pd.date_range(start, periods=len(c), freq="15min", tz="UTC")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + wick, "low": np.minimum(o, c) - wick, "close": c, "volume": 1000.0},
                        index=idx)


# A fall that sets a larger-degree low, then wave one in five inner waves, wave two in three, and wave three under way.
IMPULSE_PATH = [100, 96, 98, 80, 85, 83.5, 90, 88.5, 95, 93, 94.2, 91.5, 97]


def _at(series, t):
    return series.state[t], series.type[t], series.clarity[t], series.alt[t]


def test_series_labels_wave_two_then_wave_three_on_bars():
    bars = path_bars(IMPULSE_PATH)
    s = wave_series(bars, 1)
    wave_two = next(c for c in s.counts if c.state == "IMP_2" and c.context == 1)
    assert wave_two.main is not None and wave_two.main.points[:2] == pytest.approx((79.9, 95.1))
    assert wave_two.clarity == "ALT_DISAGREES" and wave_two.alt_state == "COR_B"          # wave one read as five waves
    assert s.state[-1] == "IMP_3" and s.state[wave_two.bar] == "IMP_2"
    flipped = wave_series(_mirror_bars(bars), 1)
    assert list(flipped.state) == list(s.state) and list(flipped.type) == list(s.type)    # mirror


def _mirror_bars(bars: pd.DataFrame, pivot: float = 200.0) -> pd.DataFrame:
    return pd.DataFrame({"open": pivot - bars["open"], "high": pivot - bars["low"], "low": pivot - bars["high"],
                         "close": pivot - bars["close"], "volume": bars["volume"]}, index=bars.index)


def _degree_confirm_bars(bars, degree):
    from app.price_action import pa_settings
    return {s.confirm_bar for s in _swings(_pivot_frame(bars), degree, pa_settings.settings())}


@pytest.mark.parametrize("degree", WAVE_DEGREES)
def test_recompute_only_on_a_newly_confirmed_swing(degree):
    bars = synthetic_bars(1500, seed=11)
    s = wave_series(bars, degree)
    confirms = _degree_confirm_bars(bars, degree)
    assert s.counts and set(s.event_bars) <= confirms
    frame = s.frame()
    changed = [t for t in range(1, len(bars)) if not frame.iloc[t].equals(frame.iloc[t - 1])]
    assert set(changed) <= confirms, "a value changed on a bar without a newly confirmed swing"


@pytest.mark.parametrize("degree", WAVE_DEGREES)
def test_truncation_over_many_t(degree):
    """No look-ahead: the count at bar t equals the count computed on the data cut at t."""
    bars = synthetic_bars(900, seed=5)
    full = wave_series(bars, degree).frame()
    for t in list(range(60, len(bars), 37)) + [len(bars) - 1]:
        cut = wave_series(bars.iloc[: t + 1], degree).frame()
        pd.testing.assert_frame_equal(cut, full.iloc[: t + 1])


def test_a_later_change_never_alters_earlier_values():
    bars = synthetic_bars(900, seed=3)
    base = wave_series(bars, 1).frame()
    for t in (300, 500, 700):
        moved = bars.copy()
        rng = np.random.default_rng(t)
        shift = np.cumsum(rng.normal(0, 0.8, len(bars) - t - 1))
        for col in ("open", "high", "low", "close"):
            moved.iloc[t + 1:, moved.columns.get_loc(col)] = bars[col].to_numpy()[t + 1:] + shift
        moved["high"] = moved[["open", "high", "close"]].max(axis=1)
        moved["low"] = moved[["open", "low", "close"]].min(axis=1)
        other = wave_series(moved, 1).frame()
        pd.testing.assert_frame_equal(other.iloc[: t + 1], base.iloc[: t + 1])


def test_unclear_and_missing_values_and_the_degree_choice():
    bars = synthetic_bars(600, seed=7)
    s = wave_series(bars, 1)
    first = s.counts[0].bar
    assert all(v is None for v in s.state[:first]) and s.state[first] is not None
    assert set(s.state[first:]) <= set(wc.STATES) and set(s.clarity[first:]) <= set(wc.CLARITY)
    assert set(s.type[first:]) <= set(wc.TYPES) and set(x for x in s.alt if x is not None) <= set(wc.STATES)
    for bad in (0, 4):
        with pytest.raises(ValueError):
            wave_series(bars, bad)
    assert cached_wave_series(bars, 1) is cached_wave_series(bars.copy(), 1)
    assert cached_wave_series(bars, 1) is not cached_wave_series(bars.iloc[:-1], 1)


def test_left_truncation_agrees_once_the_degree_has_its_history():
    """WB12: the count from a later start agrees with the full history's once the swings agree (degree 1 within its
    SWING_DEGREE_MIN_BARS of history); degrees 2 and 3 are documented as fetch-dependent."""
    from app.screener.registry import SWING_DEGREE_MIN_BARS
    bars = synthetic_bars(1500, seed=13)
    full = wave_series(bars, 1).frame()
    cut_at = 200
    late = wave_series(bars.iloc[cut_at:], 1).frame()
    tail = len(bars) - cut_at - SWING_DEGREE_MIN_BARS[1] * 2
    agree = (late.iloc[-tail:].fillna("-").to_numpy() == full.iloc[-tail:].fillna("-").to_numpy()).all(axis=1).mean()
    assert agree > 0.9, agree


def test_incremental_within_the_performance_budget():
    bars = synthetic_bars(5000, seed=2)
    started = time.perf_counter()
    for degree in WAVE_DEGREES:
        wave_series(bars, degree)
    assert time.perf_counter() - started < 5.0


# --- WB2: generic method only; WAVE-6: no ratio literal -------------------------------------------------------------------

ALLOWED_IMPORTS = {
    "__future__", "collections", "dataclasses", "hashlib", "typing", "numpy", "pandas",
    "app.price_action.causal_swings", "app.price_action.pa_settings",
    "app.price_action.daily_trend.rules", "app.price_action.daily_trend.legs",
    "app.price_action.waves", "app.price_action.waves.counter", "app.price_action.waves.series", "app.price_action.waves.settings",
}
ALLOWED_FROM_LEGS = {"CORRECTIVE", "MOTIVE", "leg_points", "read_counter", "reduce_counted", "reduce_waves", "motive_segs"}


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, None
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for a in node.names:
                full = f"{mod}.{a.name}"
                yield (full if full in ALLOWED_IMPORTS else mod), a.name


def test_the_wave_modules_import_only_the_generic_pieces():
    files = sorted(WAVES_DIR.glob("*.py"))
    assert {f.name for f in files} >= {"__init__.py", "counter.py", "series.py", "settings.py"}
    for path in files:
        for mod, name in _imports(path):
            assert mod in ALLOWED_IMPORTS, f"{path.name} imports {mod}"
            if mod == "app.price_action.daily_trend.legs":
                assert name in ALLOWED_FROM_LEGS, f"{path.name} imports {name} from the Daily trend legs"


def test_importing_the_waves_never_loads_the_daily_trend_fold():
    code = ("import sys, app.price_action.waves, app.screener.registry;"
            "bad = [m for m in sys.modules if m.startswith(('app.price_action.daily_trend.fold', 'app.price_action.daily_trend.context',"
            " 'app.price_action.level_gate'))]; print(bad); sys.exit(1 if bad else 0)")
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr


def test_no_ratio_literal_in_the_reading_rules():
    """Ratios are settings (WAVE-6): a decimal number appears only in settings.py, where the defaults are declared."""
    literal = re.compile(r"(?<![\w.])\d*\.\d+(?![\w.])")
    for path in WAVES_DIR.glob("*.py"):
        if path.name == "settings.py":
            continue
        code = "\n".join(line.split("#", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
        code = re.sub(r'"""[\s\S]*?"""', "", code)
        assert not literal.findall(code), f"{path.name}: {literal.findall(code)}"


def test_no_owner_method_names_in_the_wave_modules():
    text = " ".join(p.read_text(encoding="utf-8").lower() for p in WAVES_DIR.glob("*.py"))
    for word in ("model c", "fold_impulse", "corr_label", "unfolded_123", "abc_fix", "q49", "trade revision"):
        assert word not in text, word
