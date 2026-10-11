"""Z10-a (U3 G2): the no-look-ahead harness over every shipped bar function (app/quality/causal_cases.py), the coverage
that keeps it complete, the harness's own power (it must catch planted look-ahead), the validator rule for
last-bar-only functions inside history-reading ones, and the market-structure fix."""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, List

import pandas as pd
import pytest

from app.quality import causal_cases
from app.quality.lookahead import EVENTS, LAST_BAR, REGISTRY, SERIES, CausalCase, check, perturb_future, register, synthetic_bars
from app.screener import compile_screen
from app.screener.registry import FUNCTIONS, LAST_BAR_ONLY, READS_HISTORY

pytestmark = pytest.mark.lookahead                 # its own CI job (backend-lookahead); the main run skips it
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


@pytest.mark.parametrize("name", CASES)
def test_each_case_produces_values(name):
    """A case whose function never produced a value (all NaN, never True, no records, no answer on any prefix) would
    pass vacuously."""
    case = REGISTRY[name]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        if case.kind == LAST_BAR:
            answers = causal_cases.last_bar_answers(name)
            assert any(a not in (None, "", False, []) and not (isinstance(a, float) and a != a) for a in answers), (name, answers)
        elif case.kind == EVENTS:
            bars = synthetic_bars(case.length or (400 if case.bars == "15m" else 600), tf=case.bars, seed=7)
            assert len(case.fn(bars)) > 0, name
        else:
            assert causal_cases.sample_values(name) > 0, name


def test_coverage_names_exactly_what_to_add(monkeypatch):
    """M1 (review): a registry function added without a case fails with the line to add, whichever list it belongs in."""
    from app.screener import registry
    extra = {"Foo": registry.Spec("Foo", "factor", registry.NUM, "price", (registry.Arg("n", "window", 5, False),)),
             "FooNow": registry.Spec("FooNow", "filter", registry.BOOL, None, (), timeframed=False)}
    monkeypatch.setattr(registry, "FUNCTIONS", {**registry.FUNCTIONS, **extra})
    monkeypatch.setattr(registry, "LAST_BAR_ONLY", registry.LAST_BAR_ONLY | {"FooNow"})
    gaps = causal_cases.coverage_gaps()
    assert any('SCREENQL_SERIES[\'Foo\'] = ["Foo(...)"]' in g for g in gaps), gaps
    assert any('SCREENQL_LAST_BAR[\'FooNow\']' in g and "SCREENQL_EXEMPT" in g for g in gaps), gaps
    monkeypatch.setitem(causal_cases.SCREENQL_SERIES, "Foo", ["SMA(close, 5)"])
    assert any("does not call Foo" in g for g in causal_cases.coverage_gaps())


def test_every_indicator_module_function_is_covered():
    """Not only app.indicators.__all__: macd, bollinger and true_range are shipped through their modules."""
    names = {q.split(".", 1)[1] for q in causal_cases.indicator_functions()}
    assert {"macd", "bollinger", "true_range", "supertrend", "session_vwap"} <= names
    assert causal_cases.screenql_case_mismatches() == []


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


# ------------------------------------------------------------------------------- settings read once (validator, H1)
K_EXPRESSIONS = ["Count(5, close > Supertrend(10, ATR(5) / 2)) >= 2", "CrossAbove(close, BBUpper(20, RSI(14) / 20))",
                 "close > BBLower(20, Lag(close, 1) / close)", "NearSupport(ATR(14) / close * 100)", "close > BBMid(20, 1 + 0 * close)"]


def test_a_setting_read_once_must_be_a_literal_or_a_parameter():
    """Supertrend / Bollinger `k` and the zone filters' `tolerance_pct` are read once - from the LAST bar when they
    were an expression - and applied to every bar (Count(5, close > Supertrend(10, ATR(5) / 2)) read the future)."""
    for text in K_EXPRESSIONS:
        _, v = compile_screen(text, base_tf="5m")
        assert not v.ok and any("not an expression" in p.message for p in v.problems), (text, v.problems)
    for text, params in (("close > Supertrend(10, 2.5)", {}), ("close > BBUpper(20, $k)", {"k": 1.5}), ("NearSupport(0.5)", {}),
                         ("close > Supertrend(10, -1)", {}), ("NearResistance($tol, 3)", {"tol": 1})):
        assert compile_screen(text, base_tf="5m", params=params)[1].ok, text
    assert not compile_screen("close > BBUpper(20, $k)", base_tf="5m", params={"k": "wide"})[1].ok


@pytest.mark.parametrize("text", ["Count(5, close > Supertrend(10, ATR(5) / 2))", "CrossAbove(close, BBUpper(20, RSI(14) / 20))"])
def test_the_harness_catches_an_expression_setting_when_the_validator_is_skipped(text):
    """The runtime itself still reads a setting on the last bar; the validator is what keeps an expression out. The
    harness sees the look-ahead when it is bypassed (the reviewer's reproduction)."""
    from app.screener.parser import parse
    from app.screener.runtime import SymbolData, evaluate
    ast = parse(text)
    fn = lambda f: evaluate(ast, SymbolData("S", {"5m": f}), base_tf="5m")          # noqa: E731
    assert check(_case(text, fn))


def test_registered_supertrend_and_bollinger_cases_use_literal_settings():
    from app.screener import nodes
    from app.screener.parser import parse
    for name in ("Supertrend", "BBUpper", "BBMid", "BBLower"):
        for text in causal_cases.SCREENQL_SERIES[name]:
            call = next(c for c in nodes.walk(parse(text)) if isinstance(c, nodes.Call) and c.name == name)
            assert all(isinstance(a, nodes.Num) for a in call.args), text


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


BASE_STRUCTURE = Path(__file__).parent / "fixtures" / "z10a" / "structure_base_c5f6a2e.json.gz"


def test_structure_trend_and_swings_equal_the_base_code():
    """Only the event timing moved. The trend and the swing list (prices, kinds, labels) are pinned to what the code
    before Z10-a (main at c5f6a2e) gave on 48 frames - 5m and daily, windows 3 and 5, some with missing highs / lows
    (the NaN rule). The fixture was written by that code, not by the code under test."""
    import gzip
    import json

    import numpy as np

    from app.price_action.market_structure import analyze_market_structure
    with gzip.open(BASE_STRUCTURE, "rt") as fh:
        pinned = json.load(fh)
    assert len(pinned) == 48 and sum(len(p["swings"]) for p in pinned) > 1000
    for p in pinned:
        f = synthetic_bars(300, tf=p["tf"], seed=p["seed"])
        if p["nan"]:
            f = f.copy()
            f.iloc[[40, 41, 150], f.columns.get_loc("high")] = np.nan
            f.iloc[[90, 200], f.columns.get_loc("low")] = np.nan
        r = analyze_market_structure(f, p["window"])
        got = [[sw.timestamp.isoformat(), sw.price, sw.kind, sw.label] for sw in r.swings]
        assert (r.trend.value, got) == (p["trend"], p["swings"]), (p["seed"], p["tf"], p["window"])


def test_a_crossing_before_two_swings_are_known_is_not_reported_late():
    """Z10-a review: with one swing known, a close through it marks the level broken without an event; it must not
    fire later (when the second swing becomes known) as if the break were new."""
    from app.price_action.market_structure import analyze_market_structure
    from tests.utils import make_series
    # swing high 110.05 at bar 4 (known at bar 7); the close goes through it at bar 9 with one swing known; the first
    # swing low (bar 7) becomes known at bar 10, while the close is still above 110.05
    closes = [100, 102, 104, 106, 110, 107, 105, 104, 108, 112, 113, 114, 115, 116, 117]
    f = make_series([float(x) for x in closes])
    events = analyze_market_structure(f, 3).events
    assert not [e for e in events if e.direction == "BULLISH" and e.level == pytest.approx(110.05)]
