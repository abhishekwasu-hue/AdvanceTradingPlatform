"""M99 B1-b: Fibonacci levels (retracement, extension, golden section), the generic price-cluster finder and the causal
per-bar wrapper with its ScreenQL functions (FibLevel, FibDistancePct, ClusterDistancePct) behind `m99_primitives`.

Synthetic numbers only. No look-ahead: a value at bar t equals the value on the bars cut at t, and a pivot is used only
from the bar that confirmed it.
"""
from __future__ import annotations

import json
import random
import uuid
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.price_action import fib_causal as fc
from app.price_action import fib_levels as fl
from app.price_action import price_clusters as pc
from app.price_action.waves.levels import WaveLevels
from app.price_action.zones.levels import cluster_prices
from app.screener.parser import parse
from app.screener.registry import FUNCTIONS, M99_FLAG, M99_FUNCTIONS
from app.screener.runtime import SymbolData, evaluate

APPROX = dict(rel=0, abs=1e-9)


# ================================================================================================ fib_levels (pure)
def test_retracement_is_measured_back_from_the_swing_end():
    assert fl.retracement(100, 200, 0.618) == pytest.approx(138.2, **APPROX)
    assert fl.retracement(100, 200, 0.5) == pytest.approx(150.0, **APPROX)
    assert fl.retracement(100, 200, 0) == 200 and fl.retracement(100, 200, 1) == 100
    assert fl.retracement(300, 250, 0.382) == pytest.approx(269.1, **APPROX)        # a falling swing: back up
    levels = fl.retracement_levels(100, 200, (0.25, 0.75), "last swing")
    assert [(x.kind, x.ratio, x.label) for x in levels] == [("retrace", 0.25, "retrace 0.25 of last swing"),
                                                             ("retrace", 0.75, "retrace 0.75 of last swing")]
    assert [x.price for x in levels] == pytest.approx([175.0, 125.0], **APPROX)


def test_extension_lays_a_to_b_from_c():
    assert fl.extension(100, 150, 130, 1.0) == pytest.approx(180.0, **APPROX)
    assert fl.extension(100, 150, 130, 1.618) == pytest.approx(210.9, **APPROX)
    assert fl.extension(400, 360, 380, 1.272) == pytest.approx(329.12, **APPROX)    # a falling A -> B: down from C
    assert [x.price for x in fl.extension_levels(100, 150, 130, (0.5, 2))] == pytest.approx([155.0, 230.0], **APPROX)


def test_golden_split_and_golden_projection():
    assert fl.golden_split(100, 200, 0.382) == pytest.approx(138.2, **APPROX)
    assert fl.golden_split(200, 100, 0.618) == pytest.approx(138.2, **APPROX)
    assert fl.golden_projection(100, 138.2, 0.382) == pytest.approx(200.0, **APPROX)
    assert fl.golden_projection(50, 80, 0.5) == pytest.approx(110.0, **APPROX)        # the even split: start + 2 x (mid - start)
    assert fl.golden_projection(500, 438, 0.62) == pytest.approx(400.0, **APPROX)
    assert [x.price for x in fl.golden_split_levels(0, 10, (0.2, 0.5))] == pytest.approx([2.0, 5.0], **APPROX)
    assert [x.kind for x in fl.golden_projection_levels(0, 10)] == ["golden_projection"] * 3
    assert fl.GOLDEN_RATIO == pytest.approx(1.6180339887, abs=1e-9)
    with pytest.raises(ValueError):
        fl.golden_projection(1, 2, 0)


def test_ratios_are_a_parameter_and_the_defaults_are_labelled_reference():
    assert fl.REFERENCE_LABEL == "reference - edit before use"
    assert [x.ratio for x in fl.retracement_levels(0, 1)] == list(fl.REFERENCE_RETRACEMENT_RATIOS)
    assert [x.ratio for x in fl.extension_levels(0, 1, 0)] == list(fl.REFERENCE_EXTENSION_RATIOS)
    assert [x.ratio for x in fl.golden_split_levels(0, 1)] == list(fl.REFERENCE_GOLDEN_RATIOS)
    assert fl.retracement_levels(0, 1, ()) == []
    for bad in (-0.1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            fl.retracement(0, 1, bad)
    with pytest.raises(ValueError):
        fl.extension(0, float("nan"), 1, 1)


def test_same_maths_as_the_wave_levels():
    """WaveLevels.retrace / projection (the wave count's own points) give what fib_levels gives for those points."""
    reading = SimpleNamespace(points=[100.0, 200.0, 150.0, 260.0], bars=[0, 10, 20, 30], direction=1, family="impulse")
    wl = WaveLevels(pd.RangeIndex(1), np.array([reading], dtype=object), np.array([260.0]), np.array([30]))
    for r in (0.382, 0.5, 1.618):
        assert wl.retrace(r)[0] == pytest.approx(fl.retracement(200.0, 150.0, r), **APPROX)
        assert wl.projection("1", r)[0] == pytest.approx(fl.extension(100.0, 200.0, 150.0, r), **APPROX)


# ================================================================================================ price_clusters
def _lv(*prices, source="fib"):
    return [pc.PriceLevel(p, f"level {i}", source) for i, p in enumerate(prices)]


def test_a_cluster_forms_inside_the_band_and_not_outside_it():
    levels = _lv(100.0, 100.2, 100.4) + _lv(105.0, source="swing")
    formed = pc.find_clusters(levels, 0.25)                                        # gaps of 0.2 % <= 0.25 %
    assert len(formed) == 1
    c = formed[0]
    assert (c.count, c.low, c.high) == (3, 100.0, 100.4) and c.centre == pytest.approx(100.2, **APPROX)
    assert c.labels == ("level 0", "level 1", "level 2") and c.sources == ("fib",)
    assert pc.find_clusters(levels, 0.1) == []                                     # gaps of 0.2 % > 0.1 %: no cluster
    assert len(pc.find_clusters(levels, 0.1, min_members=1)) == 4
    wide = pc.find_clusters(levels, 5.0)
    assert len(wide) == 1 and wide[0].count == 4 and wide[0].sources == ("fib", "swing")


def test_the_band_as_an_atr_multiple():
    levels = _lv(100.0, 100.3, 100.6)
    assert pc.find_clusters(levels, 1.0, "atr", atr=0.3)[0].count == 3              # gap 0.3 <= 1 x 0.3
    assert pc.find_clusters(levels, 0.5, "atr", atr=0.3) == []                      # gap 0.3 > 0.5 x 0.3
    assert pc.find_clusters(levels, 1.0, "atr", atr=None) == []
    assert pc.find_clusters(levels, 1.0, "atr", atr=float("nan")) == []
    with pytest.raises(ValueError):
        pc.find_clusters(levels, 1.0, "points")


def test_pct_mode_is_the_cluster_prices_rule():
    rng = random.Random(7)
    for _ in range(50):
        prices = [round(rng.uniform(90, 110), 2) for _ in range(rng.randint(1, 25))]
        tol = rng.choice((0.1, 0.3, 0.8, 2.0))
        mine = pc.find_clusters(_lv(*prices), tol, min_members=1)
        assert [(float(np.round(c.centre, 2)), c.count) for c in mine] == [(c["level"], c["touches"]) for c in cluster_prices(prices, tol)]


def test_nearest_cluster_and_non_finite_levels():
    clusters = pc.find_clusters(_lv(100.0, 100.1, 110.0, 110.1, float("nan")), 0.5)
    assert [c.count for c in clusters] == [2, 2]
    assert pc.nearest_cluster(clusters, 104.0).centre == pytest.approx(100.05, **APPROX)
    assert pc.nearest_cluster(clusters, 106.0).centre == pytest.approx(110.05, **APPROX)
    tie = pc.find_clusters(_lv(100.0, 102.0, 110.0, 112.0), 3.0)                  # centres 101 and 111
    assert pc.nearest_cluster(tie, 106.0).centre == 101.0                           # a tie: the lower one
    assert pc.nearest_cluster([], 1.0) is None


# ================================================================================================ causal wrapper
def _bars(points=(100, 140, 120, 170, 150, 200, 180, 230, 210), step=15):
    mid = []
    for a, b in zip(points, points[1:]):
        mid += list(np.linspace(a, b, step, endpoint=False))
    mid = np.array(mid + [points[-1]], dtype=float)
    idx = pd.date_range("2026-01-05 03:45", periods=len(mid), freq="15min", tz="UTC")
    return pd.DataFrame({"open": mid - 0.2, "high": mid + 0.5, "low": mid - 0.5, "close": mid + 0.2, "volume": 1000.0}, index=idx)


def test_the_levels_follow_the_confirmed_pivots():
    bars = _bars()
    pivots = fc.confirmed_pivots(bars, 0)
    assert [p.kind for p in pivots[:4]] == ["L", "H", "L", "H"]
    assert all(p.confirmed_idx > p.bar_idx for p in pivots)
    retrace = fc.fib_series(bars, "retrace", 0.618)
    extend = fc.fib_series(bars, "extend", 1.0)
    golden = fc.fib_series(bars, "golden", 0.5)
    for k in range(2, len(pivots)):
        c = pivots[k].confirmed_idx
        p3, p2, p1 = pivots[k - 2].price, pivots[k - 1].price, pivots[k].price
        assert retrace[c] == pytest.approx(p1 - 0.618 * (p1 - p2), **APPROX)
        assert extend[c] == pytest.approx(p1 + (p2 - p3), **APPROX)
        assert golden[c] == pytest.approx(p2 + 2 * (p1 - p2), **APPROX)
    assert np.isnan(retrace[: pivots[1].confirmed_idx]).all()                      # fewer than two pivots known
    assert np.isnan(extend[: pivots[2].confirmed_idx]).all()


def test_a_pivot_is_used_only_from_its_confirm_bar():
    bars = _bars()
    pivots = fc.confirmed_pivots(bars, 0)
    retrace = fc.fib_series(bars, "retrace", 0.5)
    for k in range(2, len(pivots)):
        p, c = pivots[k], pivots[k].confirmed_idx
        assert p.bar_idx < c
        before = fc.fib_value(pivots[:k], "retrace", 0.5)                          # the pivots known before p
        assert (retrace[p.bar_idx:c] == before).all()                              # the extreme bar up to the bar before confirmation
        assert retrace[c] != before


def test_truncation_gives_the_same_value():
    bars = _bars()
    full = {kind: fc.fib_series(bars, kind, 0.618) for kind in fc.FIB_KINDS}
    pct = fc.cluster_series(bars, 1.0)
    atr = fc.cluster_series(bars, 3.0, "atr")
    for t in range(20, len(bars), 7):
        cut = bars.iloc[: t + 1]
        for kind in fc.FIB_KINDS:
            np.testing.assert_array_equal(fc.fib_series(cut, kind, 0.618), full[kind][: t + 1])
        np.testing.assert_array_equal(fc.cluster_series(cut, 1.0)["centre"], pct["centre"][: t + 1])
        np.testing.assert_array_equal(fc.cluster_series(cut, 3.0, "atr")["centre"], atr["centre"][: t + 1])


def test_the_nearest_cluster_per_bar():
    bars = _bars()
    pivots = fc.confirmed_pivots(bars, 0)
    out = fc.cluster_series(bars, 1.0)
    t = len(bars) - 1
    known = [p for p in pivots if p.confirmed_idx <= t]
    clusters = pc.find_clusters(fc.level_set(known), 1.0)
    want = pc.nearest_cluster(clusters, float(bars["close"].iloc[t]))
    assert want is not None and out["count"][t] == want.count >= 2
    assert out["centre"][t] == pytest.approx(want.centre, **APPROX) and out["labels"][t] == want.labels
    assert out["low"][t] <= out["centre"][t] <= out["high"][t]
    assert np.isnan(out["centre"][: pivots[1].confirmed_idx]).all() and (out["count"][: pivots[1].confirmed_idx] == 0).all()
    labels = [lv.label for lv in fc.level_set(known)]
    assert "swing high" in labels and "retrace 0.618 of last swing" in labels and "extend 1.618 of swing before" in labels


# ================================================================================================ ScreenQL
def _ev(text, bars, tf="15m"):
    return evaluate(parse(text), SymbolData("S", {tf: bars}), base_tf=tf)


def test_the_screenql_functions_read_the_causal_series():
    bars = _bars()
    close = bars["close"].to_numpy(float)
    level = fc.fib_series(bars, "extend", 1.618)
    np.testing.assert_allclose(np.asarray(_ev('FibLevel("extend", 1.618)', bars), float), level, equal_nan=True)
    np.testing.assert_allclose(np.asarray(_ev('FibDistancePct("extend", 1.618, 0)', bars), float),
                               (close - level) / level * 100, equal_nan=True)
    centre = fc.cluster_series(bars, 0.5, "atr")["centre"]
    np.testing.assert_allclose(np.asarray(_ev('ClusterDistancePct(0.5, "atr")', bars), float),
                               (close - centre) / centre * 100, equal_nan=True)
    assert {"FibLevel", "FibDistancePct", "ClusterDistancePct"} <= set(M99_FUNCTIONS)
    assert all(FUNCTIONS[name].flag == M99_FLAG == "m99_primitives" for name in M99_FUNCTIONS)


def test_the_flag_is_off_by_default_and_unfinished():
    from app.platform import controls
    from app.platform.flag_catalog import FLAG_INFO
    from app.platform.flag_state import FLAG_STATUS
    text = "M99 shared primitives (bar classifier, Fibonacci, clusters, structure) - unfinished"
    assert controls.FEATURE_FLAGS[M99_FLAG] == text and FLAG_INFO[M99_FLAG].plain == text
    assert M99_FLAG in controls.DEFAULT_OFF_FLAGS and FLAG_STATUS[M99_FLAG].state == "not_built"


def _org(tag, on):
    from tests.test_n1_e1_execution_core import _flag
    from tests.test_trading_worker import _tenant
    t = _tenant(f"m99b1-{tag}-{uuid.uuid4().hex[:6]}@example.com", with_credentials=False)
    for name in ("screener_v2", "screener_intrabar") + ((M99_FLAG,) if on else ()):
        _flag(name, t["tenant_id"])
    return t


def test_flag_off_hides_and_refuses_and_alert_rules_do_not_take_them():
    from tests.test_auth_api import client
    off, on = _org("off", False), _org("on", True)
    entries_off = client.get("/api/screener/registry", headers=off["headers"]).json()["entries"]
    entries_on = client.get("/api/screener/registry", headers=on["headers"]).json()["entries"]
    assert not set(M99_FUNCTIONS) & set(entries_off) and set(M99_FUNCTIONS) <= set(entries_on)
    for source in ('close > FibLevel("retrace", 0.618)', 'FibDistancePct("golden", 0.5, 1) < 1', 'ClusterDistancePct(0.3) < 0.5'):
        refused = client.post("/api/screener/validate", headers=off["headers"], json={"source": source, "base_tf": "15m"}).json()
        assert not refused["ok"] and M99_FLAG in json.dumps(refused["problems"]), source
        ok = client.post("/api/screener/validate", headers=on["headers"], json={"source": source, "base_tf": "15m"}).json()
        assert ok["ok"], (source, ok)
    rule = {"name": "f", "kind": "instrument", "symbol": "ABC", "condition": 'close > FibLevel("retrace", 0.5)', "base_tf": "15m"}
    r = client.post("/api/alerts/rules", headers=on["headers"], json=rule)
    assert r.status_code == 422 and "shared primitives" in r.text
