"""S5-A: price action in ScreenQL - Pattern(name), SwingHigh / SwingLow / SwingDirection(degree), MedianRange(n).

All are series computed bar by bar from closed bars only, so they work with offsets, @timeframe, Count and alerts. The
checks: each matches the engine it wraps (app/price_action), a swing counts only from the bar that CONFIRMED it, and
every value at bar j is the same whether or not later bars exist (truncation invariance = no look-ahead). The series
come from tests/sample_market.py (the session-shaped generator the trade-port tests use; no BANKNIFTY bars are in
this repo - SC-12)."""
import numpy as np
import pandas as pd
import pytest

from app.price_action import candlestick_patterns as cp
from app.price_action import causal_swings as cs
from app.price_action import pa_settings
from app.screener import compile_screen, parse
from app.screener.registry import FUNCTIONS, PATTERN_NAMES, SWING_DEGREES, describe
from app.screener.runtime import SymbolData, evaluate, run_screen
from app.screener.runtime import _PATTERNS
from tests.sample_market import generate


def _sample(count=900, seed=11):
    return generate(count, 100.0, seed)


def _ev(text, frame, tf="1m", **params):
    return evaluate(parse(text), SymbolData("S", {tf: frame}), base_tf=tf, params=params)


def _frame(rows, start="2026-03-02 03:45", freq="5min"):
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=100.0)


# --------------------------------------------------------------------------------------------- registry and validation
def test_names_and_degrees_are_checked_before_anything_runs():
    ok = lambda text, **p: compile_screen(text, base_tf="5m", params=p)[1]  # noqa: E731
    assert ok('Pattern("hammer")').ok and ok("SwingHigh(2) > 0").ok and ok("SwingLow() > 0").ok
    assert ok('Pattern($p)', p="bullish_engulfing").ok
    bad = ok('Pattern("hamer")')
    assert not bad.ok and "'hamer' is not one of" in bad.problems[0].message
    assert "is not one of" in ok('Pattern($p)', p="nope").problems[0].message
    assert "is not one of" in ok("SwingHigh(4) > 0").problems[0].message
    assert "is not one of" in ok("SwingHigh(1.5) > 0").problems[0].message
    assert "must be one of" in ok("SwingHigh(close) > 0").problems[0].message
    assert not ok("SwingHigh(true) > 0").ok
    # the unit is price: a swing high against an oscillator is refused, against a price is fine
    assert not ok("SwingHigh() > RSI(14)").ok and ok("close > SwingHigh()").ok
    assert not ok('SwingDirection() > 1').ok and ok('SwingDirection() == "UP"').ok


def test_lookback_covers_the_swing_warm_up_and_the_bar_before_a_median_range():
    v = compile_screen("close > SwingHigh()", base_tf="5m")[1]
    assert v.lookback["5m"] >= 100
    assert compile_screen("MedianRange(20) > 0", base_tf="5m")[1].lookback["5m"] == 21
    assert compile_screen("MedianRange() > 0", base_tf="5m")[1].lookback["5m"] == 21
    assert compile_screen("CrossAbove(close, open)", base_tf="5m")[1].lookback["5m"] == 2              # unchanged
    assert compile_screen('Pattern("doji")[2]', base_tf="5m")[1].lookback["5m"] == 5


def test_describe_lists_the_choices_and_every_pattern_has_a_detector():
    d = describe()
    assert d["Pattern"]["choices"] == {"name": list(PATTERN_NAMES)}
    assert d["SwingHigh"]["choices"] == {"degree": list(SWING_DEGREES)}
    assert set(_PATTERNS) == set(PATTERN_NAMES)
    assert all(hasattr(cp, fn) for fn, _ in _PATTERNS.values())
    assert len(SWING_DEGREES) == len(pa_settings.settings()["swing_atr_mult"])
    assert {"Pattern", "SwingHigh", "SwingLow", "SwingDirection", "MedianRange"} <= set(FUNCTIONS)


# ------------------------------------------------------------------------------------------------------------ patterns
def test_pattern_series_matches_the_detectors_bar_by_bar():
    f = _sample(400)
    for name in PATTERN_NAMES:
        fn, direction = _PATTERNS[name]
        got = _ev(f'Pattern("{name}")', f)
        want = [False, False] + [(m := getattr(cp, fn)(f, i)) is not None and (direction is None or m.direction == direction)
                                 for i in range(2, len(f))]
        assert got.tolist() == want, name
    assert _ev('Pattern("doji")', f).any()                                       # the comparison was not vacuous


def test_a_hand_built_hammer_and_engulfing_show_on_their_own_bar_only():
    flat = [(100, 101, 99, 100)] * 5
    hammer = _frame(flat + [(100, 100.3, 95, 100.3)] + flat)
    s = _ev('Pattern("hammer")', hammer, "5m")
    assert s.tolist() == [False] * 5 + [True] + [False] * 5
    assert _ev('Pattern("hammer")[1]', hammer, "5m").iloc[6] and _ev('Count(3, Pattern("hammer")) == 1', hammer, "5m").iloc[7]
    engulf = _frame(flat + [(100, 100.2, 98, 98.2), (98, 101.5, 97.8, 101.4)])
    assert _ev('Pattern("bullish_engulfing")', engulf, "5m").tolist()[-2:] == [False, True]
    assert not _ev('Pattern("bearish_engulfing")', engulf, "5m").iloc[-1]


def test_a_pattern_never_wraps_around_to_the_end_of_the_frame():
    # the last bar would make bar 0 an "engulfing" if a detector read df.iloc[-1] as the bar before bar 0
    f = _frame([(98, 101.5, 97.8, 101.4), (100, 101, 99, 100), (100, 100.2, 98, 98.2)])
    assert not _ev('Pattern("bullish_engulfing")', f, "5m").iloc[:2].any()


# -------------------------------------------------------------------------------------------------------------- swings
def test_a_swing_counts_from_the_bar_that_confirmed_it_not_from_the_extreme():
    f = _sample(900)
    s = pa_settings.settings()
    frame = pd.DataFrame({k: f[k].to_numpy(float) for k in ("open", "high", "low", "close")})
    frame["timestamp"] = frame["bar_end"] = f.index
    pivots = cs.degree_pivots(frame, 0, s)
    highs = [p for p in pivots if p.kind == "H"]
    assert len(highs) >= 3 and all(p.confirmed_idx > p.bar_idx for p in highs)
    hi = _ev("SwingHigh(0)", f)
    for prev, p in zip(highs, highs[1:]):
        assert hi.iloc[p.confirmed_idx] == p.price
        if prev.confirmed_idx < p.confirmed_idx:                                # until its confirmation the PREVIOUS high
            assert hi.iloc[p.confirmed_idx - 1] == prev.price                   # is shown, even though this extreme existed
    assert any(p.price != prev.price for prev, p in zip(highs, highs[1:]))
    lo, d = _ev("SwingLow()", f), _ev("SwingDirection()", f)
    first = pivots[0].confirmed_idx
    assert np.isnan(hi.iloc[: min(p.confirmed_idx for p in highs)]).all() and (d.iloc[:first] == "").all()
    last = pivots[-1]
    assert d.iloc[-1] == ("DOWN" if last.kind == "H" else "UP")
    assert set(d.unique()) == {"", "UP", "DOWN"}
    assert (lo.dropna() > 0).all()


@pytest.mark.parametrize("degree", [0, 1])
def test_swings_and_median_range_are_truncation_invariant(degree):
    f = _sample(900, seed=5)
    full = {t: _ev(t, f) for t in (f"SwingHigh({degree})", f"SwingLow({degree})", f"SwingDirection({degree})", "MedianRange(20)")}
    for j in range(150, len(f), 37):
        cut = f.iloc[: j + 1]
        for text, series in full.items():
            a, b = series.iloc[j], _ev(text, cut).iloc[-1]
            assert (a == b) or (pd.isna(a) and pd.isna(b)), (text, j)
    assert full[f"SwingHigh({degree})"].notna().sum() > 100                   # pivots were found: not vacuous


def test_a_larger_degree_moves_less_often():
    f = _sample(900, seed=3)
    changes = [_ev(f"SwingHigh({d})", f).diff().fillna(0).ne(0).sum() for d in (0, 2)]
    assert changes[0] > changes[1]


def test_median_range_excludes_the_current_bar():
    rows = [(100, 100 + r, 100 - r, 100) for r in (1, 2, 3, 4, 50)]
    m = _ev("MedianRange(3)", _frame(rows), "5m")
    assert np.isnan(m.iloc[:3]).all()
    assert m.iloc[3] == 4.0 and m.iloc[4] == 6.0                                # ranges 2,4,6 then 4,6,8 (never 100)


def test_a_screen_with_price_action_runs_end_to_end():
    f = _sample(900, seed=9)
    text = 'close > SwingLow() AND SwingDirection() IN ("UP", "DOWN") AND (high - low) < 5 * MedianRange(20)'
    ast, v = compile_screen(text, base_tf="1m")
    assert v.ok, v.problems
    out = run_screen(ast, v, [SymbolData("S", {"1m": f})], base_tf="1m")
    assert len(out) == 1 and out[0].reason is None
