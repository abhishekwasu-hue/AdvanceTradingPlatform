"""S5-A4: SwingZoneStrength(side, degree) / SwingZoneDistance(side, degree) - the zone at the last confirmed swing low
(support) or high (resistance), measured by the trade-port level_strength module as known at each bar.

The zone is the pivot candle from its extreme to its body; its origin is the pivot bar, used only from the bar that
confirmed the pivot. The reference below finds that pivot independently (the last one confirmed by each bar) and calls
level_strength directly."""
import time

import numpy as np
import pandas as pd
import pytest

from app.price_action import causal_swings as cs
from app.price_action import level_strength as ls
from app.price_action import pa_settings
from app.screener import compile_screen, parse
from app.screener.runtime import SymbolData, evaluate
from tests.sample_market import generate


def _ev(text, frame, tf="1m"):
    return evaluate(parse(text), SymbolData("S", {tf: frame}), base_tf=tf)


def _reference(f, side, degree):
    s = pa_settings.settings()
    stamps = pd.to_datetime(np.arange(len(f)), unit="s")                         # any unique increasing stamps
    frame = pd.DataFrame({k: f[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
    frame["timestamp"] = frame["bar_end"] = stamps
    pivots = [p for p in cs.degree_pivots(frame, degree, s) if p.kind == ("L" if side == "low" else "H")]
    strength, distance = np.full(len(f), np.nan), np.full(len(f), np.nan)
    for t in range(len(f)):
        known = [p for p in pivots if p.confirmed_idx is not None and p.confirmed_idx <= t]
        if not known:
            continue
        b = known[-1].bar_idx
        row = frame.iloc[b]
        body_lo, body_hi = min(row["open"], row["close"]), max(row["open"], row["close"])
        zone = ({"low": row["low"], "high": max(row["low"], body_lo), "kind": "SUPPORT"} if side == "low"
                else {"low": min(row["high"], body_hi), "high": row["high"], "kind": "RESISTANCE"})
        zone["formed_at"] = stamps[b]
        feats = ls.strength_features(zone, frame, t, symbol="DEFAULT", settings=s)
        strength[t], distance[t] = ls.strength_score(feats), feats["dist_mr"]
    return strength, distance


@pytest.mark.parametrize("side", ["low", "high"])
def test_matches_level_strength_on_the_last_pivot_confirmed_by_each_bar(side):
    f = generate(320, 100.0, 11)
    want_s, want_d = _reference(f, side, 0)
    got_s = _ev(f'SwingZoneStrength("{side}")', f).to_numpy(float)
    got_d = _ev(f'SwingZoneDistance("{side}")', f).to_numpy(float)
    np.testing.assert_allclose(got_s, want_s, equal_nan=True)
    np.testing.assert_allclose(got_d, want_d, equal_nan=True)
    assert np.isfinite(want_s).sum() > 100 and np.nanstd(want_s) > 0                # not vacuous: scores vary
    first = int(np.flatnonzero(np.isfinite(want_s))[0])
    assert np.isnan(got_s[:first]).all() and first > 0                               # missing before the first pivot


def test_distance_sign_follows_the_side_of_the_zone():
    f = generate(320, 100.0, 11)
    lo_zone = _ev('SwingZoneDistance("low")', f)
    hi_zone = _ev('SwingZoneDistance("high")', f)
    close = f["close"]
    lo_mid = _ev('SwingLow()', f)                                                    # the zone starts at the swing low
    above = lo_zone.notna() & (close > lo_mid + 5 * _ev("MedianRange(20)", f))
    assert above.any() and (lo_zone[above] > 0).all()
    hi_px = _ev('SwingHigh()', f)
    below = hi_zone.notna() & (close < hi_px - 5 * _ev("MedianRange(20)", f))
    assert below.any() and (hi_zone[below] < 0).all()


@pytest.mark.parametrize("degree", [0, 1])
def test_truncation_invariant(degree):
    f = generate(400, 100.0, 23)
    for what in ("SwingZoneStrength", "SwingZoneDistance"):
        full = _ev(f'{what}("low", {degree})', f)
        assert full.notna().any()
        for j in range(160, len(f), 37):
            part = _ev(f'{what}("low", {degree})', f.iloc[: j + 1]).iloc[-1]
            assert (np.isnan(part) and np.isnan(full.iloc[j])) or part == pytest.approx(full.iloc[j]), (what, degree, j)


def test_the_index_time_zone_and_duplicate_stamps_do_not_move_the_origin():
    """level_strength finds the origin by timestamp; the series must not depend on what the index holds."""
    f = generate(320, 100.0, 11)
    want = {side: _ev(f'SwingZoneStrength("{side}")', f).to_numpy(float) for side in ("low", "high")}
    ist = f.tz_convert("Asia/Kolkata")
    naive_ist = ist.tz_localize(None)
    doubled = f.set_axis(f.index[::2].repeat(2)[: len(f)])                      # every stamp twice
    assert doubled.index.has_duplicates
    for frame in (ist, naive_ist, doubled):
        for side in ("low", "high"):
            np.testing.assert_allclose(_ev(f'SwingZoneStrength("{side}")', frame).to_numpy(float), want[side], equal_nan=True)


def test_flat_bars_give_missing_values_not_an_error():
    """A suspended or circuit-locked stock: every bar at one price. Zero median range - nothing to measure in."""
    f = generate(400, 100.0, 11)
    f.iloc[120:, :4] = 101.0
    for what in ("SwingZoneStrength", "SwingZoneDistance"):
        got = _ev(f'{what}("low")', f)
        assert got.iloc[-1:].isna().all() and got.iloc[:120].notna().any(), what


def test_one_pass_serves_strength_and_distance(monkeypatch):
    from app.screener import runtime
    calls = []
    original = runtime._swing_zone_arrays
    monkeypatch.setattr(runtime, "_swing_zone_arrays", lambda *a: calls.append(a[1:]) or original(*a))
    f = generate(320, 100.0, 12)
    _ev('SwingZoneStrength("low") > 30 AND SwingZoneDistance("low") < 3', f)
    assert calls == [("low", 0)]
    f.iloc[-1, f.columns.get_loc("close")] += 0.5                              # the frame changed: computed again
    _ev('SwingZoneStrength("low") > 30', f)
    assert len(calls) == 2


@pytest.mark.parametrize("degree", [0, 1])
def test_the_answer_does_not_depend_on_history_beyond_the_lookback(degree):
    """Evaluated on exactly the validator's lookback, the last bar matches the long history (S5-A4 per-degree minimum)."""
    text = f'SwingZoneDistance("low", {degree})'
    lb = compile_screen(text, base_tf="1m")[1].lookback["1m"]
    bad = total = 0
    for seed in (301, 302, 303):
        f = generate(1200, 100.0, seed)
        full = _ev(text, f)
        for j in range(len(f) - 1, len(f) - 200, -25):
            part = _ev(text, f.iloc[j + 1 - lb: j + 1]).iloc[-1]
            total += 1
            bad += not ((np.isnan(part) and np.isnan(full.iloc[j])) or part == pytest.approx(full.iloc[j]))
    assert total == 24 and bad == 0, (bad, total)


def test_validation_and_units():
    ok = lambda text: compile_screen(text, base_tf="5m")[1]                          # noqa: E731
    assert ok('SwingZoneStrength("low") > 60').ok and ok('SwingZoneDistance("high", 2) > -1').ok
    assert "is not one of" in ok('SwingZoneStrength("support") > 60').problems[0].message
    assert "is not one of" in ok('SwingZoneStrength("low", 7) > 60').problems[0].message
    assert not ok('SwingZoneStrength("low") > close').ok                           # a 0-100 score is not a price
    assert ok('SwingZoneStrength("low") > 60').lookback["5m"] >= 100               # the swing warm-up
    for degree, bars in enumerate((100, 250, 600, 1200)):                           # more history for a larger degree
        assert ok(f'SwingZoneStrength("low", {degree}) > 60').lookback["5m"] >= bars
        assert ok(f'SwingLow({degree}) > close').lookback["5m"] >= bars
    assert compile_screen('SwingLow($d) > close', base_tf="5m", params={"d": 2})[1].lookback["5m"] >= 600


def test_cost_on_a_long_frame():
    f = generate(500, 100.0, 5)
    t0 = time.perf_counter()
    _ev('SwingZoneStrength("low") > 50 AND SwingZoneDistance("low") < 2', f)
    assert time.perf_counter() - t0 < 0.6                                            # measured ~0.05 s


def test_without_leg_labels_and_time_at_price_the_score_runs_5_to_70():
    """Documented scale (SCREENER.md, SC-15): the origin-strength part scores 0 and time at price is unknown (half)."""
    assert ls.strength_score({"departure_mr": 9, "base_bars": 1, "recency": 1.0, "role_reversal": True}) == 70.0
    assert ls.strength_score({"recency": 0.0}) == 5.0
    f = generate(320, 100.0, 11)
    got = _ev('SwingZoneStrength("high")', f).dropna()
    assert got.between(5.0, 70.0).all()


def test_an_unexpected_error_on_one_symbol_does_not_abort_the_universe(monkeypatch):
    from app.screener import runtime
    original = runtime._swing_zone_arrays

    def flaky(f, side, degree):
        if float(f["close"].iloc[0]) > 150:                                         # only the second symbol's data
            raise ZeroDivisionError("bad bars")
        return original(f, side, degree)
    monkeypatch.setattr(runtime, "_swing_zone_arrays", flaky)
    universe = [SymbolData("A", {"1m": generate(320, 100.0, 11)}), SymbolData("B", {"1m": generate(320, 200.0, 12)})]
    ast, v = compile_screen('SwingZoneStrength("low") >= 0', base_tf="1m")
    a, b = runtime.run_screen(ast, v, universe, base_tf="1m")
    assert a.reason is None and b.matched is False and "ZeroDivisionError" in b.reason
