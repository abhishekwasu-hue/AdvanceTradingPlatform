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
    assert set(cp.pattern_masks(_frame([(1, 2, 0, 1)] * 3))) == set(PATTERN_NAMES)
    assert len(SWING_DEGREES) == len(pa_settings.settings()["swing_atr_mult"])
    assert {"Pattern", "SwingHigh", "SwingLow", "SwingDirection", "MedianRange"} <= set(FUNCTIONS)


# ------------------------------------------------------------------------------------------------------------ patterns
# Written out here, not read from the code under test: which detector (and direction) each screen name means.
EXPECTED = {
    "doji": ("detect_doji", None), "hammer": ("detect_hammer", None), "shooting_star": ("detect_shooting_star", None),
    "bullish_engulfing": ("detect_bullish_engulfing", None), "bearish_engulfing": ("detect_bearish_engulfing", None),
    "morning_star": ("detect_morning_star", None), "evening_star": ("detect_evening_star", None),
    "bullish_pin_bar": ("detect_pin_bar", "BULLISH"), "bearish_pin_bar": ("detect_pin_bar", "BEARISH"),
    "inside_bar": ("detect_inside_bar", None),
    "bullish_outside_bar": ("detect_outside_bar", "BULLISH"), "bearish_outside_bar": ("detect_outside_bar", "BEARISH"),
    "bullish_rejection": ("detect_strong_rejection", "BULLISH"), "bearish_rejection": ("detect_strong_rejection", "BEARISH"),
}


def test_pattern_series_matches_the_detectors_bar_by_bar():
    # the sample market plus hand-built shapes, so every pattern occurs (a parity check on all-False would prove nothing)
    flat = [(100, 101, 99, 100)] * 2
    shapes = [(100, 100.3, 95, 100.3), (100, 105, 99.9, 100.1), (100, 100.2, 98, 98.2), (98, 101.5, 97.8, 101.4),
              (101, 101.2, 99, 99.1), (99, 99.2, 96.6, 96.8), (96.6, 96.9, 96.4, 96.7), (96.8, 99, 96.7, 98.9),
              (100, 101, 99, 100.05), (99.5, 100.5, 99.2, 100.4), (100.2, 102, 98, 99.9), (100, 100.1, 96, 99.8),
              (100, 104, 99.95, 100.3), (99, 100.5, 98.8, 100.4), (100.6, 100.8, 98.5, 98.7)]
    hand = _frame(flat + shapes * 3)
    for f in (_sample(400), hand):
        for name, (fn, direction) in EXPECTED.items():
            got = _ev(f'Pattern("{name}")', f, "5m" if f is hand else "1m")
            want = [False, False] + [(m := getattr(cp, fn)(f, i)) is not None and (direction is None or m.direction == direction)
                                     for i in range(2, len(f))]
            assert got.tolist() == want, name
    shown = {name for name in EXPECTED if _ev(f'Pattern("{name}")', hand, "5m").any() or _ev(f'Pattern("{name}")', _sample(400)).any()}
    assert shown == set(EXPECTED), set(EXPECTED) - shown                              # every name was really compared


def test_pin_bar_rejection_outside_and_shooting_star_by_hand():
    flat = [(100, 101, 99, 100)] * 3
    rows = lambda bar: _frame(flat + [bar])                                        # noqa: E731
    last = lambda name, bar: bool(_ev(f'Pattern("{name}")', rows(bar), "5m").iloc[-1])   # noqa: E731
    long_lower = (100, 100.2, 96, 99.9)                                            # long lower wick, small body
    long_upper = (100, 104, 99.8, 100.1)                                           # long upper wick, small body
    assert last("bullish_pin_bar", long_lower) and not last("bearish_pin_bar", long_lower)
    assert last("bearish_pin_bar", long_upper) and not last("bullish_pin_bar", long_upper)
    assert last("bullish_rejection", long_lower) and not last("bearish_rejection", long_lower)
    assert last("bearish_rejection", long_upper) and not last("bullish_rejection", long_upper)
    assert last("shooting_star", (100, 104, 99.95, 100.3)) and not last("hammer", (100, 104, 99.95, 100.3))
    assert last("bullish_outside_bar", (99.5, 102, 98, 101.5)) and not last("bearish_outside_bar", (99.5, 102, 98, 101.5))
    assert last("bearish_outside_bar", (100.5, 102, 98, 98.5)) and not last("bullish_outside_bar", (100.5, 102, 98, 98.5))
    assert last("inside_bar", (100, 100.5, 99.5, 100.2))


def test_patterns_cost_little_on_a_long_frame():
    import time
    f = _sample(3000)
    t = time.perf_counter()
    for name in PATTERN_NAMES:
        _ev(f'Pattern("{name}")', f)
    assert time.perf_counter() - t < 2.0                                           # bar-by-bar this was ~0.2 s per name


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
    assert np.isnan(hi.iloc[: min(p.confirmed_idx for p in highs)]).all() and d.iloc[:first].isna().all()
    last = pivots[-1]
    assert d.iloc[-1] == ("DOWN" if last.kind == "H" else "UP")
    assert set(d.dropna().unique()) == {"UP", "DOWN"}
    # missing never matches, whichever way it is written
    assert not _ev('SwingDirection() != "DOWN"', f).iloc[:first].any()
    assert not _ev('SwingDirection() IN ("UP", "DOWN")', f).iloc[:first].any()
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
    expected = bool(_ev(text, f).iloc[-1])
    assert out[0].matched == expected
    flipped = run_screen(*compile_screen(f"NOT ({text})", base_tf="1m"), [SymbolData("S", {"1m": f})], base_tf="1m")
    assert flipped[0].matched == (not expected)                                       # one of the two runs matches


def test_a_daily_swing_on_an_intraday_screen_says_it_lacks_history():
    f = _sample(900)                                                                  # under two weeks of 1m bars
    ast, v = compile_screen("close > SwingLow(0)@1d", base_tf="1m")
    assert v.ok and v.lookback["1d"] >= 100
    out = run_screen(ast, v, [SymbolData("S", {"1m": f})], base_tf="1m")
    assert out[0].matched is False and out[0].reason == "not enough history on 1d"


def test_fetch_days_count_each_timeframe_in_its_own_minutes():
    from app.screener.routes import MAX_INTRADAY_FETCH_DAYS, fetch_days
    assert fetch_days({"5m": 21}, "5m") == 6                                          # 1 session -> 2 days + holiday room
    assert fetch_days({"1h": 100}, "1h") == 27                                        # 16 sessions -> 23 days + holiday room
    assert fetch_days({"5m": 21, "1d": 100}, "5m") == MAX_INTRADAY_FETCH_DAYS          # 100 sessions: capped, then reported


def test_classifier_values_are_checked():
    ok = lambda text: compile_screen(text, base_tf="5m")[1]                          # noqa: E731
    assert "never matches" in ok('SwingDirection() == "up"').problems[0].message
    assert "never matches" in ok('Trend() IN ("UPTREND", "SIDEWAYS")').problems[0].message
    assert "never matches" in ok('"bullish" == ChainBias()').problems[0].message
    assert ok('SwingDirection() == "UP"').ok and ok('Trend() IN ("UPTREND", "RANGE")').ok and ok('StructureEvent() == ""').ok
    assert describe()["SwingDirection"]["values"] == ["UP", "DOWN"]
