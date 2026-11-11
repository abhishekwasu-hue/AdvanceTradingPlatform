"""M99 B1-c: the structure facade (structure_series.py), the level store (level_store.py), the trend-line facade
(lines_helper.py) and the ScreenQL StructureEventSeries / RegimeTag (flag m99_primitives).

Synthetic shapes with this file's own numbers: straight legs between turning prices, one bar per step. No look-ahead:
a frame cut at t gives the same values up to t; a swing shows only from its confirm bar. Parity: the facade agrees with
structure_tracker.run on the same frame."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.price_action import level_store as ls
from app.price_action import lines_helper as lh
from app.price_action import structure_series as ss
from app.price_action import structure_tracker as st
from app.quality import causal_cases
from app.quality.lookahead import synthetic_bars
from app.screener import compile_screen
from app.screener.registry import FUNCTIONS, LAST_BAR_ONLY, M99_FLAG, M99_FUNCTIONS

# up-trend (HH / HL), a close below the last HL (CHoCH), a lower high, a close below the low since the CHoCH (reversal),
# a close below the last swing low (BOS down), then a sideways tail
TREND_TURNS = [100, 104, 102, 106, 104, 110, 105, 116, 111, 122, 108, 115, 100, 106, 95, 101, 96, 101, 96, 101, 96, 101, 96]
RANGE_TURNS = [100, 104, 100, 104, 100, 104, 100, 104, 100, 104, 100]


def shape(turns, seg: int = 6, wick: float = 0.6, start: str = "2026-01-05 09:15") -> pd.DataFrame:
    closes = [float(turns[0])]
    for a, b in zip(turns, turns[1:]):
        closes += list(np.linspace(a, b, seg + 1)[1:])
    c = np.array(closes)
    o = np.r_[c[0], c[:-1]]
    idx = pd.date_range(start, periods=len(c), freq="5min")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + wick / 2, "low": np.minimum(o, c) - wick / 2, "close": c,
                         "volume": 1000.0}, index=idx)


@pytest.fixture(scope="module")
def trend() -> pd.DataFrame:
    return shape(TREND_TURNS)


@pytest.fixture(scope="module")
def trend_series(trend) -> ss.StructureSeries:
    return ss.structure_series(trend, "5m")


# ------------------------------------------------------------------------------------------------ structure facade
def test_up_trend_labels_then_choch_then_bos(trend, trend_series):
    s = trend_series
    trend_close = trend["close"].to_numpy().__getitem__
    events = [(t, e) for t, e in enumerate(s.event) if e]
    kinds = [e for _, e in events]
    assert kinds[:3] == ["BOS_BULLISH"] * 3
    choch = kinds.index("CHOCH_BEARISH")
    assert kinds[choch + 1:] == ["BOS_BEARISH"]
    t_choch, t_bos = events[choch][0], events[-1][0]
    # the up-trend's swings before the CHoCH are higher highs and higher lows
    highs = {s.swing_high_label[t] for t in range(t_choch) if s.swing_high_label[t]}
    lows = {s.swing_low_label[t] for t in range(t_choch) if s.swing_low_label[t]}
    assert highs == {"HH"} and lows == {"HL"}
    # the CHoCH is a close below the protected HL: the up-trend turns weak, then the reversal makes it a down-trend
    assert trend_close(t_choch) < s.protected[t_choch - 1] <= trend_close(t_choch - 1)
    assert s.regime[t_choch - 1] == "UP" and s.regime[t_choch] == "UP_WEAK" and s.state[t_choch] == st.UP_WEAK
    assert s.regime[t_bos] == "DOWN" and "DOWN" in set(s.regime[t_choch:t_bos])
    rev = [e for e in s.tracker.events if e["type"] == "REVERSAL_CONFIRMED"]
    assert len(rev) == 1 and t_choch < rev[0]["bar_idx"] < t_bos
    # after the CHoCH the swings read lower lows / lower highs
    assert s.swing_low_label[t_bos] == "LL" and s.swing_high_label[t_bos] == "LH"


def test_range_regime():
    r = ss.structure_series(shape(RANGE_TURNS), "5m")
    known = [x for x in r.regime if x]
    assert known and set(known) == {"RANGE"}
    assert [e["type"] for e in r.tracker.events if e["type"] != "SWING"] == ["INIT_RANGE"]
    assert not any(r.event)


def test_range_width_is_optional_and_tags_a_narrow_tail(trend):
    base = ss.structure_series(trend, "5m")
    assert base.regime[-1] == "DOWN"                                   # the tracker alone: still the down-trend
    narrow = ss.structure_series(trend, "5m", ss.settings(range_width=5.0))
    assert narrow.regime[-1] == "RANGE"
    # where it does not apply, the tag is the tracker's own
    rr, swings = narrow.ref_range, narrow.tracker.swings
    for t in range(len(trend)):
        if narrow.regime[t] != "RANGE":
            assert narrow.regime[t] == base.regime[t]
    assert np.isfinite(rr[-1]) and len(swings) >= 4


def test_a_swing_shows_only_from_its_confirm_bar(trend, trend_series):
    s = trend_series
    r = st.StructureConfig().pivot_n["5m"]
    for side in ("high", "low"):
        bar, conf = getattr(s, f"swing_{side}_bar"), getattr(s, f"swing_{side}_confirm")
        for t in range(len(trend)):
            if conf[t] >= 0:
                assert conf[t] <= t and conf[t] - bar[t] == r
    swings = s.tracker.swings
    assert len(swings) >= 10
    for sw in swings:                                                  # absent on the bar before its confirm bar, there on it
        side = "high" if sw.kind == "H" else "low"
        bar = getattr(s, f"swing_{side}_bar")
        assert bar[sw.confirmed_idx] == sw.idx and bar[sw.confirmed_idx - 1] != sw.idx


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return (np.isnan(a) and np.isnan(b)) or a == b
    return a == b


def test_truncation_at_every_bar_gives_the_same_values(trend, trend_series):
    full = trend_series.frame()
    for t in range(len(trend)):
        cut = ss.structure_series(trend.iloc[:t + 1], "5m").frame()
        for col in full.columns:
            assert _same(full[col].iloc[t], cut[col].iloc[t]), (t, col)


def test_parity_with_structure_tracker_run(trend, trend_series):
    ref = st.run(trend.assign(bar_end=trend.index), "5m")
    got = trend_series.tracker
    assert [(e["bar_idx"], e["type"], e["price"]) for e in got.events] == [(e["bar_idx"], e["type"], e["price"]) for e in ref.events]
    assert [(w.kind, w.idx, w.price, w.confirmed_idx, w.label) for w in got.swings] == \
           [(w.kind, w.idx, w.price, w.confirmed_idx, w.label) for w in ref.swings]
    assert got.snapshot() == ref.snapshot()
    # every BOS / CHoCH of the run is on the facade's bar, with its direction
    tags = {(e["bar_idx"], ss._EVENT_OF[(e["type"], e["to_state"])]) for e in ref.events if e["type"] in ("BOS", "CHOCH")}
    assert tags == {(t, e) for t, e in enumerate(trend_series.event) if e}
    # the state at each bar equals a run cut there (sampled)
    for t in range(0, len(trend), 9):
        cut = st.run(trend.iloc[:t + 1].assign(bar_end=trend.index[:t + 1]), "5m")
        assert trend_series.state[t] == cut.state and _same(trend_series.protected[t], np.nan if cut.protected_level() is None else cut.protected_level())


def test_every_setting_is_a_labelled_reference_with_the_tracker_defaults():
    assert all(p.label == ss.REFERENCE == "reference - edit before use" for p in ss.PARAMS.values())
    cfg = ss.tracker_config("5m")
    assert cfg == st.StructureConfig()
    tuned = ss.tracker_config("15m", ss.settings(pivot_n=4, swing_k=1.0, eq_tol=0.2))
    assert tuned.pivot_n["15m"] == 4 and tuned.swing_k["15m"] == 1.0 and tuned.eq_tol == 0.2
    assert tuned.pivot_n["5m"] == st.StructureConfig().pivot_n["5m"]                  # one timeframe only
    assert st.StructureConfig().pivot_n["15m"] == 3                                    # the shared defaults never move
    with pytest.raises(ValueError):
        ss.settings(no_such_setting=1)


def test_causal_pivot_series_matches_the_screener_swing_label():
    from app.screener.runtime import _swing_label
    f = synthetic_bars(600, seed=3)
    p = ss.causal_pivot_series(f, 0)
    for side in ("high", "low"):
        want = _swing_label(f, side, 0)
        got = getattr(p, f"swing_{side}_label")
        for t in range(len(f)):
            if want.iloc[t] is not None:
                assert got[t] == want.iloc[t], (side, t)
        conf = getattr(p, f"swing_{side}_confirm")
        assert all(conf[t] <= t for t in range(len(f)))
    cut = ss.causal_pivot_series(f.iloc[:400], 0)
    assert np.array_equal(cut.swing_high_confirm, p.swing_high_confirm[:400])


# ----------------------------------------------------------------------------------------------------- level store
def test_level_store_reads_as_of_a_bar():
    store = ls.LevelStore("15m")
    a = store.add(5, "swap", "swap_high", 110.0)
    b = store.add(8, "fib", "0.618", 104.0, 103.0)
    assert store.as_of(4) == [] and [lv.id for lv in store.as_of(5)] == [a]
    zone = store.get(b, 8)
    assert zone is not None and zone.low == 103.0 and zone.high == 104.0 and zone.is_zone and zone.price == 103.5
    store.update(a, 12, state=ls.TESTED)
    store.update(a, 20, state=ls.BROKEN)
    assert store.get(a, 11).state == ls.ACTIVE and store.get(a, 12).state == ls.TESTED and store.get(a, 99).state == ls.BROKEN
    assert [lv.id for lv in store.as_of(15, states=[ls.ACTIVE])] == [b]
    assert [lv.id for lv in store.as_of(15, source="swap")] == [a]
    assert store.nearest(15, 106.0, side="above").id == a and store.nearest(15, 106.0, side="below").id == b
    assert store.nearest(15, 103.5).id == b                              # inside the zone
    assert [r.state for r in store.history(a)] == [ls.ACTIVE, ls.TESTED, ls.BROKEN]


def test_level_store_refuses_back_dating_and_keeps_levels_apart():
    store = ls.LevelStore()
    a = store.add(10, "swing", "high", 100.0)
    with pytest.raises(ValueError):
        store.add(9, "swing", "high", 101.0)
    with pytest.raises(ValueError):
        store.update(a, 3, state=ls.BROKEN)
    with pytest.raises(ValueError):
        store.add(11, "swing", "high", 100.0, state="nonsense")
    b = store.add(11, "cluster", "high", 100.0)                          # same price, another source: its own level
    assert a != b and len(store.as_of(11)) == 2
    store.update(b, 11, state=ls.TESTED)                                 # two changes at one close: the later one counts
    store.update(b, 11, state=ls.FLIPPED)
    assert store.get(b, 11).state == ls.FLIPPED and len(store.history(b)) == 1


# ------------------------------------------------------------------------------------------------------ lines helper
@pytest.fixture(scope="module")
def rising() -> pd.DataFrame:
    return shape([100, 106, 102, 108, 104, 110, 106, 112, 108, 114, 110, 116, 112], seg=5)


def test_rising_line_through_confirmed_lows_with_its_channel(rising):
    out = lh.line_series(rising, "rising")
    order = lh.params_doc()["tl_order"]["default"]
    known = np.flatnonzero(np.isfinite(out.value))
    assert len(known)
    for t in known:
        assert out.anchor_a[t] < out.anchor_b[t] <= t - 1 - order and out.slope[t] > 0
        assert out.channel[t] > out.value[t]
    assert all(v["label"] == lh.REFERENCE for v in lh.params_doc().values())


def test_falling_line_is_the_mirror(rising):
    flipped = rising.copy()
    flipped["open"], flipped["close"] = -rising["open"], -rising["close"]
    flipped["high"], flipped["low"] = -rising["low"], -rising["high"]
    up, down = lh.line_series(rising, "rising"), lh.line_series(flipped, "falling")
    assert np.allclose(np.nan_to_num(up.value), np.nan_to_num(-down.value))
    assert np.allclose(np.nan_to_num(up.channel), np.nan_to_num(-down.channel))


def test_lines_helper_matches_the_chart_finder_and_is_causal(rising):
    full = lh.line_series(rising, "rising", tl_max_bars=500)
    checked = 0
    for t in range(20, len(rising)):
        ref = lh.trendline_at(rising, t, "rising")
        if ref is None:
            assert np.isnan(full.value[t])
            continue
        assert full.value[t] == pytest.approx(ref["value"]) and (full.anchor_a[t], full.anchor_b[t]) == (ref["anchor_a"], ref["anchor_b"])
        cut = lh.line_series(rising.iloc[:t + 1], "rising", tl_max_bars=500)
        assert _same(cut.value[t], full.value[t]) and _same(cut.channel[t], full.channel[t])
        checked += 1
    assert checked > 10
    with pytest.raises(ValueError):
        lh.line_series(rising, "sideways")
    with pytest.raises(ValueError):
        lh.line_series(rising, "rising", tl_mode="channel")


# --------------------------------------------------------------------------------------------------------- ScreenQL
def test_screenql_functions_are_per_bar_and_flagged():
    assert set(M99_FUNCTIONS) >= {"StructureEventSeries", "RegimeTag"} and M99_FLAG == "m99_primitives"
    for name in ("StructureEventSeries", "RegimeTag"):
        assert FUNCTIONS[name].flag == M99_FLAG and name not in LAST_BAR_ONLY
    for text in ('Count(20, StructureEventSeries() == "BOS_BULLISH") >= 1', 'RegimeTag() == "UP" AND Lag(close, 1) < close',
                 'StructureEventSeries(pivot_n=3, swing_k=1) == "CHOCH_BEARISH"', 'RegimeTag(range_width=4, range_swings=4)@15m == "RANGE"'):
        assert compile_screen(text, base_tf="5m")[1].ok, text
    assert not compile_screen('RegimeTag(range_width=close) == "RANGE"', base_tf="5m")[1].ok      # a setting is read once
    assert not compile_screen('RegimeTag() == "SIDEWAYS"', base_tf="5m")[1].ok


def test_screenql_series_equal_the_facade(trend, trend_series):
    ev = causal_cases._screenql("StructureEventSeries()")(trend)
    rg = causal_cases._screenql("RegimeTag()")(trend)
    assert list(ev) == list(trend_series.event)
    assert [x or "" for x in rg] == list(trend_series.regime)
    assert rg.iloc[0] is None                                           # missing in the warm-up, not ""
