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
    stamps = pd.DatetimeIndex(f.index).tz_convert(None)
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


def test_the_bar_clock_does_not_depend_on_the_time_zone():
    """The origin is looked up by timestamp: an IST index must give the same series as UTC."""
    f = generate(320, 100.0, 11)
    ist = f.tz_convert("Asia/Kolkata")
    for side in ("low", "high"):
        np.testing.assert_allclose(_ev(f'SwingZoneStrength("{side}")', ist).to_numpy(float),
                                   _ev(f'SwingZoneStrength("{side}")', f).to_numpy(float), equal_nan=True)


def test_validation_and_units():
    ok = lambda text: compile_screen(text, base_tf="5m")[1]                          # noqa: E731
    assert ok('SwingZoneStrength("low") > 60').ok and ok('SwingZoneDistance("high", 2) > -1').ok
    assert "is not one of" in ok('SwingZoneStrength("support") > 60').problems[0].message
    assert "is not one of" in ok('SwingZoneStrength("low", 7) > 60').problems[0].message
    assert not ok('SwingZoneStrength("low") > close').ok                           # a 0-100 score is not a price
    assert ok('SwingZoneStrength("low") > 60').lookback["5m"] >= 100               # the swing warm-up


def test_cost_on_a_long_frame():
    f = generate(500, 100.0, 5)
    t0 = time.perf_counter()
    _ev('SwingZoneStrength("low") > 50 AND SwingZoneDistance("low") < 2', f)
    assert time.perf_counter() - t0 < 2.0


def test_without_leg_labels_and_time_at_price_the_score_runs_5_to_70():
    """Documented scale (SCREENER.md, SC-15): the origin-strength part scores 0 and time at price is unknown (half)."""
    assert ls.strength_score({"departure_mr": 9, "base_bars": 1, "recency": 1.0, "role_reversal": True}) == 70.0
    assert ls.strength_score({"recency": 0.0}) == 5.0
    f = generate(320, 100.0, 11)
    got = _ev('SwingZoneStrength("high")', f).dropna()
    assert got.between(5.0, 70.0).all()
