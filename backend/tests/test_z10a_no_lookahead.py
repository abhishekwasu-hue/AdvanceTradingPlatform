"""Z10-a (U3 G2): the no-look-ahead harness over every shipped bar function (app/quality/causal_cases.py), the coverage
that keeps it complete, the harness's own power (it must catch planted look-ahead), the validator rule for
last-bar-only functions inside history-reading ones, and the market-structure fix."""
from __future__ import annotations

import warnings
from typing import Any, List

import pandas as pd
import pytest

from app.quality import causal_cases
from app.quality.lookahead import EVENTS, LAST_BAR, REGISTRY, SERIES, CausalCase, check, perturb_future, register, synthetic_bars
from app.screener import compile_screen
from app.screener.registry import FUNCTIONS, LAST_BAR_ONLY, READS_HISTORY

causal_cases.register_all()
CASES = sorted(REGISTRY)


@pytest.mark.parametrize("name", CASES)
def test_no_look_ahead(name):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)              # pandas object-dtype downcast notices, not ours
        violations = check(REGISTRY[name])
    assert not violations, "\n".join(str(v) for v in violations[:5])


def test_every_shipped_function_is_registered():
    assert causal_cases.coverage_gaps() == []
    assert set(causal_cases.SCREENQL_EXEMPT) <= set(FUNCTIONS)
    assert set(causal_cases.SCREENQL_EXEMPT) | set(causal_cases.SCREENQL_LAST_BAR) == set(LAST_BAR_ONLY)


@pytest.mark.parametrize("name", [n for n in CASES if REGISTRY[n].kind != LAST_BAR])
def test_each_case_produces_values(name):
    """A case whose function never produced a value (all NaN, never True, no records) would pass vacuously."""
    case = REGISTRY[name]
    if case.kind == EVENTS:
        bars = synthetic_bars(case.length or 600 if case.bars != "15m" else 400, tf=case.bars, seed=7)
        assert len(case.fn(bars)) > 0, name
        return
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        assert causal_cases.sample_values(name) > 0, name


# ------------------------------------------------------------------------------------------ the harness catches it
def _case(name: str, fn: Any, kind: str = SERIES, **kw: Any) -> CausalCase:
    return CausalCase(name=name, fn=fn, kind=kind, samples=6, **kw)


@pytest.mark.parametrize("label, fn", [
    ("centred window", lambda f: f["close"].rolling(5, center=True).mean()),
    ("next bar", lambda f: f["close"].shift(-1)),
    ("whole-frame normalisation", lambda f: f["close"] / f["close"].max()),
    ("whole-frame z-score", lambda f: (f["close"] - f["close"].mean()) / f["close"].std()),
    ("backward fill", lambda f: f["close"].where(f.index.minute == 0).bfill()),
    ("future session high", lambda f: f["high"].groupby(f.index.date).transform("max")),
])
def test_the_harness_catches_planted_look_ahead(label, fn):
    assert check(_case(label, fn)), f"{label} was not caught"


def test_the_harness_catches_an_event_dated_before_it_was_known():
    """A fractal swing reported as known at its own bar (it needs `r` bars after it) - the old market-structure bug."""
    def pivots(f: pd.DataFrame, r: int = 3) -> List[Any]:
        h = f["high"].to_numpy()
        return [(j, j) for j in range(r, len(h) - r) if h[j] == h[j - r:j + r + 1].max()]
    assert check(_case("pivot at its own bar", pivots, EVENTS, known_at=lambda p: p[0]))
    assert not check(_case("pivot when confirmed", lambda f: [(p[0] + 3, p[1]) for p in pivots(f)], EVENTS, known_at=lambda p: p[0]))


def test_the_harness_catches_a_function_that_writes_into_its_input():
    def writes(f: pd.DataFrame) -> pd.Series:
        f["close"] = f["close"] * 1.0 + 0.01
        return f["close"]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")                                  # pandas' chained-assignment notice: the point here
        found = check(_case("writes", writes))
    assert any(v.check == "immutability" for v in found)


def test_perturbations_keep_the_past_and_the_timestamps():
    bars = synthetic_bars(200, tf="5m", seed=1)
    for how in ("spike_up", "spike_down", "walk"):
        moved = perturb_future(bars, 120, how)
        pd.testing.assert_frame_equal(moved.iloc[:121], bars.iloc[:121])
        assert moved.index.equals(bars.index)
        assert not moved.iloc[121:].equals(bars.iloc[121:])
        assert (moved["high"] >= moved[["open", "close"]].max(axis=1)).all() and (moved["low"] <= moved[["open", "close"]].min(axis=1)).all()


def test_synthetic_bars_are_session_shaped_and_deterministic():
    a, b = synthetic_bars(400, tf="5m", seed=4), synthetic_bars(400, tf="5m", seed=4)
    pd.testing.assert_frame_equal(a, b)
    ist = a.index.tz_convert("Asia/Kolkata")
    assert ist.time.min().strftime("%H:%M") == "09:15" and ist.time.max().strftime("%H:%M") == "15:25"
    assert (ist.dayofweek < 5).all()
    assert (a["high"] >= a[["open", "close"]].max(axis=1)).all() and (a["low"] <= a[["open", "close"]].min(axis=1)).all()
    assert (a["volume"] == 0).any() and (a["high"] == a["low"]).any()        # the awkward bars are there


def test_register_refuses_duplicates_and_events_without_known_at():
    with pytest.raises(ValueError):
        register(CASES[0], lambda f: f["close"])
    with pytest.raises(ValueError):
        register("z10a.test.events_without_known_at", lambda f: [], kind=EVENTS)


# ------------------------------------------------------------------------------ last-bar-only functions (validator)
def test_last_bar_only_functions_cannot_be_read_back_in_time():
    """Count(5, PatternBullish()) validated and repeated today's answer on the four bars before (counting 5, or 0)."""
    inner = {"Trend": 'Trend(3) == "UPTREND"', "StructureEvent": 'StructureEvent(3) == "BOS_BULLISH"', "PatternBullish": "PatternBullish()",
             "PatternBearish": "PatternBearish()", "NearSupport": "NearSupport(0.5)", "NearResistance": "NearResistance(0.5)",
             "Rank": "Rank(close) <= 5", "PercentileRank": "PercentileRank(close) > 50", "PCR": "PCR() > 1", "ChainBias": 'ChainBias() == "BULLISH"',
             "MaxPainDistancePct": "MaxPainDistancePct() < 2", "Sector": 'Sector() == "IT"', "Industry": 'Industry() == "x"',
             "McapBucket": 'McapBucket() == "large"', "IndexMember": 'IndexMember("NIFTY 50")', "IsFnO": "IsFnO()"}
    assert set(inner) == set(LAST_BAR_ONLY)
    for name, cond in inner.items():
        ok = compile_screen(cond, base_tf="5m")[1]
        assert ok.ok, (cond, ok.problems)                                    # on its own: fine (the last bar)
        _, v = compile_screen(f"Count(5, {cond}) >= 2", base_tf="5m")
        assert not v.ok and any("last bar only" in p.message for p in v.problems), cond
    for fn in sorted(READS_HISTORY):
        text = {"Count": "Count(3, PCR() > 1) > 1", "CountStreak": "CountStreak(PCR() > 1) > 1", "CrossAbove": "CrossAbove(PCR(), 1)",
                "CrossBelow": "CrossBelow(PCR(), 1)"}.get(fn, f"{fn}(PCR(), 3) > 1")
        _, v = compile_screen(text, base_tf="5m")
        assert not v.ok and len([p for p in v.problems if "last bar only" in p.message]) == 1, (text, v.problems)
    _, v = compile_screen("SMA(SMA(PCR(), 3), 5) > 1", base_tf="5m")
    assert len([p for p in v.problems if "last bar only" in p.message]) == 1         # reported once, not per level
    for fine in ("PatternBullish() AND Count(5, close > open) >= 2", 'Count(5, Pattern("hammer")) >= 1', "Rank(SMA(close, 5)) <= 10",
                 "Greatest(close, open) > Lag(close, 1)"):
        assert compile_screen(fine, base_tf="5m")[1].ok, fine


# ------------------------------------------------------------------------------------------ market structure (fix)
def test_structure_events_up_to_any_bar_are_those_of_the_bars_up_to_it():
    """Z10-a fix: a swing counted from its own bar, `window` bars before it was confirmed, so the event history changed
    when later bars arrived. Every other bar of one frame, both windows."""
    from app.price_action.market_structure import analyze_market_structure
    f = synthetic_bars(200, tf="5m", seed=3)
    for window in (3, 5):
        full = [(e.timestamp, e.event, e.direction, e.level) for e in analyze_market_structure(f, window).events]
        assert len(full) > 5
        for t in range(2 * window + 2, len(f), 2):
            cut = [(e.timestamp, e.event, e.direction, e.level) for e in analyze_market_structure(f.iloc[:t + 1], window).events]
            assert cut == [e for e in full if e[0] <= f.index[t]], (window, t)


def test_a_break_inside_the_confirmation_window_of_a_new_swing_still_counts():
    """The case the old code lost: price breaks the previous swing high on the very bar that later turns out to be a
    new swing high. In real time the break happened (the new swing is unknown for `window` more bars); it stays."""
    from app.price_action.market_structure import analyze_market_structure
    from tests.utils import make_series
    up = [100, 102, 104, 106, 108, 110, 108, 106, 104, 102, 100, 102, 104, 106, 108, 111, 109, 107, 105, 103, 101, 99]
    f = make_series([float(x) for x in up])
    events = analyze_market_structure(f, 3).events
    at = f.index[up.index(111)]
    assert any(e.timestamp == at and e.direction == "BULLISH" and e.level == pytest.approx(110.05) for e in events)
    cut = analyze_market_structure(f.iloc[: up.index(111) + 1], 3).events
    assert [(e.timestamp, e.event) for e in cut] == [(e.timestamp, e.event) for e in events if e.timestamp <= at]


def test_structure_trend_and_swings_are_unchanged_by_the_fix():
    """Only the event timing moved; the trend and the swing list (labels included) are what they were."""
    from app.price_action.market_structure import _label_swings, _trend_from_labels, analyze_market_structure
    from app.price_action.swings import alternate_swings, find_swings
    f = synthetic_bars(300, tf="5m", seed=8)
    result = analyze_market_structure(f, 3)
    swings = alternate_swings(find_swings(f, 3))
    _label_swings(swings)
    assert [(s.timestamp, s.price, s.kind, s.label) for s in result.swings] == [(s.timestamp, s.price, s.kind, s.label) for s in swings]
    assert result.trend == _trend_from_labels(swings, len(swings) - 1)
