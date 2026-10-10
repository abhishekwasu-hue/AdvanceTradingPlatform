"""S5-A2: ReversalAt(level, direction) - the trade-port logical reversal rule as a ScreenQL series.

Checks: the series equals `evaluate_reversal` on the candles up to each bar (so the touch prefilter changes nothing and
nothing after the bar is used), it is truncation invariant, a hand-built hammer at support passes, the level must be a
price, the direction one of two words, and a universe of symbols costs well under a second each."""
import time

import numpy as np
import pandas as pd

from app.price_action import pa_settings
from app.price_action.reversal import evaluate_reversal
from app.screener import compile_screen, parse
from app.screener.runtime import SymbolData, evaluate, run_screen
from tests.sample_market import generate


def _ev(text, frame, tf="1m"):
    return evaluate(parse(text), SymbolData("S", {tf: frame}), base_tf=tf)


def _frame(rows, start="2026-03-02 03:45", freq="5min"):
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=100.0)


def _reference(f, levels, direction):
    s = pa_settings.settings()
    out = []
    plain = f[["open", "high", "low", "close"]].reset_index(drop=True)
    for j in range(len(f)):
        lv = levels[j]
        out.append(bool(np.isfinite(lv)) and bool(evaluate_reversal(plain.iloc[: j + 1], float(lv), direction, s)["valid"]))
    return out


def test_the_series_equals_the_reversal_rule_on_the_candles_up_to_each_bar():
    f = generate(500, 100.0, 21)
    for text, level_text, direction in (('ReversalAt(SwingLow(), "bullish")', "SwingLow()", "bullish"),
                                        ('ReversalAt(SwingHigh(), "bearish")', "SwingHigh()", "bearish")):
        got = _ev(text, f)
        levels = _ev(level_text, f).to_numpy(float)
        assert got.tolist() == _reference(f, levels, direction), text
    assert _ev('ReversalAt(SwingLow(), "bullish")', f).any() or _ev('ReversalAt(SwingHigh(), "bearish")', f).any()


def test_a_constant_level_and_a_hand_built_hammer_at_support():
    base = [(100, 101, 99, 100)] * 30
    hammer = (99.4, 99.6, 97.0, 99.5)                                              # dips to 97, closes back above 98
    f = _frame(base + [(100, 100.2, 99.0, 99.2), hammer])
    s = _ev('ReversalAt(98, "bullish")', f, "5m")
    assert s.iloc[-1] and evaluate_reversal(f.reset_index(drop=True), 98.0, "bullish")["valid"]
    assert not s.iloc[:-1].any()
    assert not _ev('ReversalAt(98, "bearish")', f, "5m").iloc[-1]                  # the wrong side never passes


def test_truncation_invariance():
    f = generate(400, 100.0, 8)
    full = _ev('ReversalAt(SwingLow(), "bullish")', f)
    for j in range(120, len(f), 23):
        assert full.iloc[j] == _ev('ReversalAt(SwingLow(), "bullish")', f.iloc[: j + 1]).iloc[-1], j


def test_validation_the_level_is_a_price_and_the_direction_one_of_two_words():
    ok = lambda text: compile_screen(text, base_tf="5m")[1]                        # noqa: E731
    assert ok('ReversalAt(SwingLow(), "bullish")').ok and ok('ReversalAt(PDL(), "bullish")').ok and ok('ReversalAt(100, "bearish")').ok
    bad = ok('ReversalAt(RSI(14), "bullish")')
    assert not bad.ok and "level must be a price" in bad.problems[0].message
    assert "is not one of" in ok('ReversalAt(close, "up")').problems[0].message
    assert ok('ReversalAt(close, "bullish")').lookback["5m"] >= 25
    assert ok('Count(5, ReversalAt(SwingLow(), "bullish")) >= 1').ok


def test_a_universe_costs_little():
    universe = [SymbolData(f"S{i}", {"1m": generate(500, 100.0 + i, 100 + i)}) for i in range(20)]
    ast, v = compile_screen('ReversalAt(SwingLow(), "bullish") OR ReversalAt(SwingHigh(), "bearish")', base_tf="1m")
    t = time.perf_counter()
    out = run_screen(ast, v, universe, base_tf="1m")
    per_symbol = (time.perf_counter() - t) / len(universe)
    assert len(out) == 20 and all(m.reason is None for m in out)
    assert per_symbol < 0.5, per_symbol


def test_the_touch_prefilter_holds_with_a_touch_tolerance_too(monkeypatch):
    """With touch_tol_mr > 0 a level counts as touched from a distance; the prefilter must widen the same way on both sides."""
    original = pa_settings.settings
    tolerant = original(touch_tol_mr=0.5)
    monkeypatch.setattr(pa_settings, "settings", lambda **kw: tolerant if not kw else original(**kw))
    f = generate(500, 100.0, 33)
    plain = f[["open", "high", "low", "close"]].reset_index(drop=True)
    for text, level_text, direction in (('ReversalAt(SwingLow(), "bullish")', "SwingLow()", "bullish"),
                                        ('ReversalAt(SwingHigh(), "bearish")', "SwingHigh()", "bearish")):
        got = _ev(text, f)
        levels = _ev(level_text, f).to_numpy(float)
        want = [bool(np.isfinite(levels[j])) and bool(evaluate_reversal(plain.iloc[: j + 1], float(levels[j]), direction, tolerant)["valid"])
                for j in range(len(f))]
        assert got.tolist() == want, text
