"""S5-A3: RealBreak(level, side, n) - a real break of a level (not a wick, not a reclaimed close) confirmed within the
last n bars, as known at each bar (the trade-port break rule, app/price_action/breaks.py).

Hand-built bars on a quiet base (every bar's range 10, so the median range is 10 and the close buffer 2.5):
a wick through the level is no break, a strong close beyond it breaks on that bar (displacement), a plain close beyond it
breaks one bar later only if the next close stays beyond (acceptance), and a reclaim the next bar is a false break."""
import numpy as np
import pandas as pd

from app.price_action import pa_settings
from app.price_action.breaks import first_real_break, median_range
from app.price_action.reversal import retest_fn
from app.screener import compile_screen, parse
from app.screener.runtime import SymbolData, break_scan_start, evaluate
from tests.sample_market import generate

BASE = [(100, 105, 95, 100)] * 30


def _frame(rows):
    idx = pd.date_range("2026-03-02 03:45", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=100.0)


def _ev(text, frame, tf="5m"):
    return evaluate(parse(text), SymbolData("S", {tf: frame}), base_tf=tf)


def test_a_wick_is_not_a_break_and_a_strong_close_breaks_on_its_own_bar():
    wick = _frame(BASE + [(100, 101, 85, 99)])
    assert not _ev('RealBreak(90, "below")', wick).any()
    strong = _frame(BASE + [(100, 100.5, 80, 81)])                               # range 20.5, closes at its low
    s = _ev('RealBreak(90, "below")', strong)
    assert s.iloc[-1] and not s.iloc[:-1].any()
    assert not _ev('RealBreak(90, "above")', strong).any()                       # price was above all along: no crossing
    assert not _ev('RealBreak(110, "above")', strong).any()


def test_acceptance_confirms_one_bar_later_and_a_reclaim_is_a_false_break():
    close_below = (95, 96, 86, 87)                                               # beyond by the buffer, not a displacement
    held = _frame(BASE + [close_below, (87, 89, 85, 86)])
    s = _ev('RealBreak(90, "below")', held)
    assert s.tolist()[-2:] == [False, True]                                      # not known on the break bar itself
    reclaimed = _frame(BASE + [close_below, (87, 93, 86, 92)])
    assert not _ev('RealBreak(90, "below")', reclaimed).iloc[-2:].any()
    above = _frame(BASE + [(105, 114, 104, 113), (113, 115, 111, 114)])          # the mirror image
    assert _ev('RealBreak(110, "above")', above).iloc[-1]


def test_the_window_forgets_an_old_break():
    s = _ev('RealBreak(90, "below", 3)', _frame(BASE + [(100, 100.5, 80, 81)] + [(81, 86, 76, 81)] * 4))
    assert s.tolist()[-5:] == [True, True, True, False, False]


def test_the_series_matches_the_rule_and_is_truncation_invariant():
    f = generate(500, 100.0, 41)
    s = pa_settings.settings()
    plain = f[["open", "high", "low", "close"]].reset_index(drop=True)
    mr = median_range(plain, s["median_range_n"])
    retest = retest_fn(plain, s, mr)
    for side, level_text in (("below", "SwingLow()"), ("above", "SwingHigh()")):
        got = _ev(f'RealBreak({level_text}, "{side}", 10)', f, "1m")
        levels = _ev(level_text, f, "1m").to_numpy(float)
        closes = plain["close"].to_numpy(float)
        want = []
        for j in range(len(f)):
            start = break_scan_start(closes, j, float(levels[j]), side, 10) if np.isfinite(levels[j]) else None
            want.append(start is not None and first_real_break(plain, start, float(levels[j]), side, s, mr=mr, end=j, retest_fn=retest) is not None)
        assert got.tolist() == want, side
        assert got.any(), side                                                   # breaks happened: not vacuous
        for j in range(150, len(f), 41):
            assert got.iloc[j] == _ev(f'RealBreak({level_text}, "{side}", 10)', f.iloc[: j + 1], "1m").iloc[-1], (side, j)


def test_validation():
    ok = lambda text: compile_screen(text, base_tf="5m")[1]                      # noqa: E731
    assert ok('RealBreak(PDH(), "above")').ok and ok('RealBreak(SwingLow(), "below", 5)').ok
    assert "must be a price" in ok('RealBreak(RSI(14), "above")').problems[0].message
    assert "is not one of" in ok('RealBreak(close, "up")').problems[0].message
    assert ok('RealBreak(close, "above", 40)').lookback["5m"] >= 40
    s = pa_settings.settings()
    for n_ in (5, 20, 40):                                                       # the window, the bar before it, the warm-up
        assert ok(f'RealBreak(close, "above", {n_})').lookback["5m"] >= n_ + 1 + s["median_range_n"]


def test_a_break_is_a_crossing_from_the_near_side():
    closes = np.array([100.0] * 5 + [80.0] * 5)
    assert break_scan_start(closes, 9, 90.0, "below", 3) is None                 # beyond all window: an old break
    assert break_scan_start(closes, 6, 90.0, "below", 3) == 4                    # bar 3 was above: scan from bar 4
    assert break_scan_start(closes, 4, 90.0, "above", 3) is None                 # never at or below 90: nothing to cross


def test_a_failed_retest_also_confirms_a_break(monkeypatch):
    """A close beyond the level that is reclaimed, then a retest from the broken side that is rejected, is a real break
    too (`break_retest_confirm`). The sample below (found by search) has one such bar; turning the rule off changes it."""
    f = generate(500, 100.0, 56)
    text = 'RealBreak(SwingLow(), "below", 10)'
    with_retest = _ev(text, f, "1m")
    s = pa_settings.settings()
    plain = f[["open", "high", "low", "close"]].reset_index(drop=True)
    mr = median_range(plain, s["median_range_n"])
    retest = retest_fn(plain, s, mr)
    levels = _ev("SwingLow()", f, "1m").to_numpy(float)
    closes = plain["close"].to_numpy(float)
    want = []
    for j in range(len(f)):
        start = break_scan_start(closes, j, float(levels[j]), "below", 10) if np.isfinite(levels[j]) else None
        want.append(start is not None and first_real_break(plain, start, float(levels[j]), "below", s, mr=mr, end=j, retest_fn=retest) is not None)
    assert with_retest.tolist() == want
    original = pa_settings.settings
    monkeypatch.setattr(pa_settings, "settings", lambda **kw: original(break_retest_confirm=False) if not kw else original(**kw))
    without = _ev(text, f, "1m")
    assert int((with_retest != without).sum()) >= 1


def test_a_close_exactly_at_level_minus_buffer_on_tick_prices_is_not_dropped_by_the_prefilter():
    """Tick prices can put a close exactly at level - buffer, where close + buffer < level and close < level - buffer
    round differently; the break must still be found (the rule decides, the prefilter only skips)."""
    base = [(64.5, 65.0, 63.24, 64.5)] * 30                                     # median range 1.76, buffer 0.44
    f = _frame(base + [(65.0, 65.5, 63.2, 63.68)])
    plain = f[["open", "high", "low", "close"]].reset_index(drop=True)
    s = pa_settings.settings()
    mr = median_range(plain, s["median_range_n"])
    assert first_real_break(plain, 30, 64.12, "below", s, mr=mr, end=30) == 30   # the rule: a displacement break
    assert _ev('RealBreak(64.12, "below")', f).iloc[-1]


def test_a_confirmed_break_reads_true_for_the_window_even_after_a_reclaim():
    """RealBreak is an event in the window (like Count): a break confirmed and then reclaimed stays true until it is
    older than n bars. AND it with the close for "still beyond"."""
    strong = (100, 100.5, 80, 81)                                                # displacement below 90
    f = _frame(BASE + [strong, (81, 96, 80, 95), (95, 97, 94, 96)])              # reclaimed on the next bar
    s = _ev('RealBreak(90, "below", 5)', f)
    assert s.tolist()[-3:] == [True, True, True]
    assert not _ev('RealBreak(90, "below", 5) AND close < 90', f).iloc[-1]
    later = _frame(BASE + [strong] + [(95, 97, 94, 96)] * 6)
    assert not _ev('RealBreak(90, "below", 5)', later).iloc[-1]                  # older than the window: gone


def test_the_answer_does_not_depend_on_history_beyond_the_lookback():
    """Evaluated on exactly the validator's lookback, the last bar gives the same answer as on the whole frame."""
    f = generate(500, 100.0, 56)
    level = float(f["close"].median())
    for side, n_ in (("below", 10), ("above", 20)):
        text = f'RealBreak({level:.2f}, "{side}", {n_})'
        lb = compile_screen(text, base_tf="1m")[1].lookback["1m"]
        full = _ev(text, f, "1m")
        assert full.any(), side
        for j in range(lb, len(f), 7):
            assert _ev(text, f.iloc[j + 1 - lb: j + 1], "1m").iloc[-1] == full.iloc[j], (side, j)
