"""M99 P6: the price-action pattern pack (app/price_action/pa_pack.py), its scanners, chart layer and M templates.

Synthetic shapes with this file's own numbers: straight lines between turning points, one close per bar, a small wick.
Every reading has a positive and a negative shape, and every shape's mirror (prices negated) gives the mirror reading.
No look-ahead: a frame cut at t gives the same values up to t. Parity: the candles equal the existing masks
(candlestick_patterns.pattern_masks) at the reference values; the ScreenQL function, the chart layer and the templates
equal the one evaluation."""
from __future__ import annotations

import json
from dataclasses import fields

import numpy as np
import pandas as pd
import pytest

from app.charts import pa_layer
from app.price_action import candlestick_patterns as cp
from app.price_action import pa_pack as pa
from app.quality.lookahead import synthetic_bars
from app.screener import pa_scanners as pas
from app.screener import parse
from app.screener.registry import FUNCTIONS, PA_FLAG, PA_FUNCTIONS, PA_SETTINGS
from app.screener.runtime import SymbolData, evaluate
from app.strategy_templates import compile as C
from app.strategy_templates import engine, library
from app.strategy_templates.routes import FAMILY_FLAGS, FAMILY_NAMES

PRE = [(0, 108), (8, 100)]


def path(points: list[tuple[int, float]], wick: float = 0.2) -> pd.DataFrame:
    """Bars along straight lines between (bar, close) points: open = the close before, high / low = body +- wick."""
    closes = [float(points[0][1])]
    for (b0, p0), (b1, p1) in zip(points, points[1:]):
        closes += list(np.linspace(p0, p1, b1 - b0 + 1)[1:])
    c = np.array(closes)
    o = np.r_[c[0], c[:-1]]
    idx = pd.date_range("2026-01-05", periods=len(c), freq="D", tz="UTC")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + wick, "low": np.minimum(o, c) - wick, "close": c,
                         "volume": 1000.0}, index=idx)


def add(f: pd.DataFrame, rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range(f.index[-1] + pd.Timedelta(days=1), periods=len(rows), freq="D", tz="UTC")
    return pd.concat([f, pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1000.0)])


def mirror(f: pd.DataFrame) -> pd.DataFrame:
    """Prices negated around 300 (highs and lows swapped): the mirror shape."""
    return pd.DataFrame({"open": 300 - f["open"], "high": 300 - f["low"], "low": 300 - f["high"], "close": 300 - f["close"],
                         "volume": f["volume"]}, index=f.index)


def hits(f: pd.DataFrame, name: str, side: str = pa.ANY, **kw: object) -> list[int]:
    return [int(i) for i in np.flatnonzero(np.nan_to_num(pa.evaluate(f, pa.settings_from(kw)).value(name, side)) == 1.0)]


def flip(side: str) -> str:
    return pa.DOWN if side == pa.UP else pa.UP


def both(f: pd.DataFrame, name: str, side: str, **kw: object) -> list[int]:
    """The reading on the shape and its mirror on the mirrored shape: the same bars."""
    a, b = hits(f, name, side, **kw), hits(mirror(f), name, flip(side), **kw)
    assert a == b, (name, a, b)
    return a


# --------------------------------------------------------------------------------------------- settings and registry
def test_the_settings_table_is_the_dataclass_and_the_registry_arguments():
    d = pa.PaSettings()
    assert [f.name for f in fields(d)] == list(pa.SETTING_NAMES) == list(PA_SETTINGS)
    for name, _, ref, _, _ in pa.SETTINGS_TABLE:
        assert getattr(d, name) == ref, name
    for fn in ("PricePattern", "PricePatternLevel"):
        spec = FUNCTIONS[fn]
        assert spec.flag == PA_FLAG and fn in PA_FUNCTIONS
        assert {a.name: a.default for a in spec.kwargs} == {n: ref for n, _, ref, _, _ in pa.SETTINGS_TABLE}
    e = pa.EvidenceSettings()
    assert e.layers == () and [row[0] for row in pa.EVIDENCE_TABLE] == [f.name for f in fields(e)][1:]
    for name, _, ref, _, _ in pa.EVIDENCE_TABLE:
        assert getattr(e, name) == ref, name


@pytest.mark.parametrize("given", [{"nope": 1}, {"trap_max": 2.0}, {"degree": 1.5}, {"entry": "limit"}, {"eq_tol": 1.0, "at_tol": 0.5}])
def test_settings_outside_their_bounds_are_refused(given):
    with pytest.raises(ValueError):
        pa.settings_from(given)


def test_evidence_layers_are_off_by_default_and_unknown_ones_refused():
    assert pa.evidence_from().layers == ()
    with pytest.raises(ValueError):
        pa.evidence_from(["astrology"])


# ------------------------------------------------------------------------------------------ the four extreme types
def _second_high(second: float) -> pd.DataFrame:
    return path(PRE + [(18, 120), (26, 110), (34, second), (44, 104)])


def test_fault_double_and_trap_at_a_swing_high_by_the_second_extreme():
    assert both(_second_high(118), pa.FAULT, pa.DOWN) == [36]
    assert both(_second_high(120), pa.DOUBLE, pa.DOWN) == [36]
    assert both(_second_high(121), pa.TRAP, pa.DOWN) == [36]           # 1 beyond, about 9 % of its own leg; back under it
    for second, absent in ((118, (pa.DOUBLE, pa.TRAP)), (120, (pa.FAULT, pa.TRAP)), (121, (pa.FAULT, pa.DOUBLE))):
        for name in absent:
            assert hits(_second_high(second), name, pa.DOWN) == [], (second, name)


def test_a_wide_overshoot_is_a_break_not_a_trap():
    assert hits(_second_high(125), pa.TRAP, pa.DOWN) == []             # about 33 % of the leg: beyond trap_max
    assert hits(_second_high(125), pa.TRAP, pa.DOWN, trap_max=0.4) == [37]     # known on the close back under it


def test_climax_is_the_leg_given_back_in_about_its_own_time():
    fast = path(PRE + [(18, 120), (28, 108), (38, 130), (48, 106), (58, 115)])
    assert 48 in both(fast, pa.CLIMAX, pa.DOWN)
    slow = path(PRE + [(18, 120), (28, 108), (38, 130), (70, 106), (80, 115)])     # the give-back takes 3x the leg's bars
    assert [b for b in hits(slow, pa.CLIMAX, pa.DOWN) if b > 40] == []


# ------------------------------------------------------------------------------------------------- the missing link
ML = PRE + [(18, 110), (24, 105), (34, 115), (40, 110), (50, 120), (56, 114), (62, 118), (72, 106), (78, 115), (86, 104),
            (94, 112), (100, 103), (110, 116)]


def test_missing_link_after_a_fault_breaks_the_three_touch_line():
    f = path(ML)
    assert both(f, pa.MISSING_LINK, pa.UP) == [106]
    r = pa.evaluate(f)
    ev = next(e for e in r.events if e.name == pa.MISSING_LINK)
    assert ev.variant == pa.FAULT and "3 touches" in ev.why
    line = r.level("missing_link_line")
    assert np.isnan(line[:96]).all() and np.isfinite(line[96:107]).all()           # watched from the third touch's confirmation
    assert f["close"].iloc[106] > line[106] and (f["close"].iloc[96:106] <= line[96:106]).all()


def test_missing_link_needs_the_trend_and_the_touches():
    two = PRE + [(18, 110), (24, 105), (34, 120), (40, 114), (46, 118), (56, 106), (62, 115), (70, 104), (78, 112), (84, 103), (94, 116)]
    assert hits(path(two), pa.MISSING_LINK) == []                         # only two rising highs before the end
    off = [(78, 111) if p == (78, 115) else p for p in ML]
    assert hits(path(off), pa.MISSING_LINK) == []                         # the middle swing is off the line: two touches
    assert hits(path(off), pa.MISSING_LINK, pa.UP, touches=2)            # with two touches set, it is read


# ------------------------------------------------------------------------------------- pullback after an acceleration
BASTON = PRE + [(18, 110), (26, 104), (34, 126), (37, 121), (41, 130), (49, 116), (53, 124), (60, 118)]


def test_pullback_after_an_acceleration_turns_out_of_the_area():
    f = path(BASTON)
    assert both(f, pa.BASTON, pa.UP) == [52]
    r = pa.evaluate(f)
    lo, hi = r.level("baston_zone_low"), r.level("baston_zone_high")
    assert np.isfinite(hi[52]) and f["low"].iloc[45:53].min() <= hi[52] and f["close"].iloc[52] > hi[52]
    assert hits(f, pa.BASTON, pa.UP, entry="touch")[0] < 52               # the touch comes first
    assert np.isfinite(r.level("baston_void")[52]) and r.level("baston_void")[52] < lo[52]


def test_a_deep_pullback_or_a_small_run_is_not_a_baston():
    deep = PRE + [(18, 110), (26, 104), (34, 126), (40, 111), (44, 130), (52, 116), (56, 124), (60, 118)]
    assert hits(path(deep), pa.BASTON) == []                              # the pullback took about 70 % of the run
    assert hits(path(BASTON), pa.BASTON, acc_k=30.0) == []


# ---------------------------------------------------------------------------------------------- trigger at a level
LEVEL = path([(0, 108), (8, 100), (20, 115), (26, 106)])                  # a swing low near 100 (its low 99.8)


@pytest.mark.parametrize("rows,form", [
    ([(106, 106.3, 99.9, 100.4), (100.5, 102.0, 100.6, 101.6), (101.6, 103.0, 101.0, 102.5)], "inside / double"),
    ([(106, 106.3, 100.6, 101.0), (101.0, 101.2, 99.4, 100.2), (100.0, 103.0, 99.3, 102.8), (102.8, 103.5, 102.0, 103.0)],
     "outside / trap"),
    ([(106, 106.3, 103.0, 103.5), (103.4, 103.8, 100.3, 103.6), (103.6, 104.5, 103.0, 104.2)], "tail / fault"),
])
def test_trigger_candle_at_a_validated_level(rows, form):
    f = add(LEVEL, rows)
    got = both(f, pa.TRIGGER, pa.UP)
    assert len(got) == 1
    ev = next(e for e in pa.evaluate(f).events if e.name == pa.TRIGGER)
    assert ev.variant == form and "a swing low" in ev.why
    assert "a swing high" in next(e for e in pa.evaluate(mirror(f)).events if e.name == pa.TRIGGER).why     # the mirror's words
    assert hits(f, pa.TRIGGER, pa.UP, trigger=form.split(" ")[0]) == got


def test_no_trigger_far_from_the_levels():
    f = add(LEVEL, [(106, 106.3, 103.9, 104.4), (104.5, 105.0, 104.6, 104.8), (104.8, 105.5, 104.5, 105.2)])
    assert hits(f, pa.TRIGGER) == []


# ------------------------------------------------------------------------------------------------------ the candles
def test_inside_outside_and_hammer_equal_the_existing_masks():
    f = synthetic_bars(500, tf="15m", seed=3)
    pm = cp.pattern_masks(f)
    r = pa.evaluate(f, pa.settings_from({"after_move": False}))
    assert np.array_equal(r.flags[(pa.INSIDE, "")], pm["inside_bar"])
    assert np.array_equal(r.flags[(pa.OUTSIDE, pa.UP)], pm["bullish_outside_bar"])
    assert np.array_equal(r.flags[(pa.OUTSIDE, pa.DOWN)], pm["bearish_outside_bar"])
    assert np.array_equal(r.flags[(pa.HAMMER, pa.UP)], pm["hammer"])
    assert np.array_equal(r.flags[(pa.HAMMER, pa.DOWN)], pm["shooting_star"])


def test_hammer_after_a_fall_only():
    fall = add(LEVEL, [(106, 106.2, 104.0, 104.2), (104.0, 104.25, 101.0, 104.2), (104.2, 105.0, 104.0, 104.8)])
    assert hits(fall, pa.HAMMER, pa.UP) == [28]
    rise = add(path([(0, 108), (8, 100), (26, 115)]), [(115, 115.2, 114.0, 115.1), (115.2, 115.45, 112.0, 115.4)])
    assert hits(rise, pa.HAMMER, pa.UP) == [] and hits(rise, pa.HAMMER, pa.UP, after_move=False) == [28]


def test_two_bar_reversal_and_its_size_setting():
    f = add(LEVEL, [(106, 106.1, 103.0, 103.05), (103.05, 106.2, 102.95, 106.15), (106, 106.5, 105.5, 106.2)])
    assert 28 in both(f, pa.TWO_BAR, pa.UP)
    assert 28 not in hits(f, pa.TWO_BAR, pa.UP, tall_k=8.0)
    weak = add(LEVEL, [(106, 106.1, 103.0, 103.05), (103.05, 104.6, 102.95, 104.5), (104.5, 105.0, 104.0, 104.6)])
    assert 28 not in hits(weak, pa.TWO_BAR, pa.UP)                      # the second bar does not get back to the first open


# ------------------------------------------------------------------------------------- head and shoulders, cup and handle
HS = [(0, 100), (6, 90), (16, 110), (23, 101), (33, 121), (41, 101), (49, 111), (60, 92), (64, 96)]
CUP = [(0, 95), (6, 90), (16, 120), (31, 100), (46, 120), (50, 113), (54, 126), (58, 120)]


def test_head_and_shoulders_on_the_neckline_close_and_its_projection():
    f = path(HS)
    assert both(f, pa.HEAD_SHOULDERS, pa.DOWN) == [55]
    r = pa.evaluate(f)
    proj = r.level("hs_projection")
    assert np.flatnonzero(np.isfinite(proj)).tolist() == [55]            # a projection on the signal bar only, never a target
    assert proj[55] < r.level("neckline")[55] < f["close"].iloc[54]


def test_no_head_and_shoulders_when_the_right_shoulder_passes_the_head_or_the_shoulders_differ():
    assert hits(path([(0, 100), (6, 90), (16, 110), (23, 101), (33, 121), (41, 101), (49, 123), (60, 92)]), pa.HEAD_SHOULDERS) == []
    lop = [(49, 104.5) if p == (49, 111) else p for p in HS]             # shoulders about a quarter of the head's height apart
    assert hits(path(lop), pa.HEAD_SHOULDERS) and hits(path(lop), pa.HEAD_SHOULDERS, shoulder_tol=0.2) == []


def test_cup_and_handle_and_its_limits():
    assert both(path(CUP), pa.CUP, pa.UP) == [53]
    assert hits(path(CUP), pa.CUP, cup_bars=40) == []                   # a cup narrower than the setting
    deep = [(0, 95), (6, 90), (16, 120), (31, 100), (46, 120), (52, 106), (60, 126)]
    assert hits(path(deep), pa.CUP) == []                                # a handle deeper than half the cup


def test_cup_volume_switch_reads_volume_or_answers_not_checked():
    f = path(CUP)
    assert hits(f, pa.CUP, pa.UP, vol_confirm=True, vol_k=1.5) == []    # flat volume: not 1.5 x its average
    loud = f.copy()
    loud.iloc[53, loud.columns.get_loc("volume")] = 5000.0
    assert hits(loud, pa.CUP, pa.UP, vol_confirm=True, vol_k=1.5) == [53]
    none = f.assign(volume=np.nan)
    v = pa.evaluate(none, pa.settings_from({"vol_confirm": True})).value(pa.CUP)
    assert np.isnan(v[53])                                              # no volume data: not checked, never a guess


# --------------------------------------------------------------------------------------------------- no look-ahead
@pytest.mark.parametrize("frame", ["random", "shapes"])
def test_a_frame_cut_at_t_gives_the_same_values_up_to_t(frame):
    if frame == "random":
        f = synthetic_bars(220, tf="15m", seed=11)
        cuts = range(20, len(f))
    else:
        f = pd.concat([path(ML), mirror(path(HS)).set_axis(pd.date_range("2026-06-01", periods=len(path(HS)), freq="D", tz="UTC"))])
        cuts = range(10, len(f))
    full = pa.evaluate(f)
    for t in cuts:
        part = pa.evaluate(f.iloc[:t + 1])
        for name in pa.NAMES:
            for side in pa.SIDE_CHOICES:
                np.testing.assert_array_equal(part.value(name, side), full.value(name, side)[:t + 1], err_msg=f"{name} {side} at {t}")
        for lv in pa.LEVELS:
            np.testing.assert_array_equal(part.level(lv), full.level(lv)[:t + 1], err_msg=f"{lv} at {t}")


# ----------------------------------------------------------------------------- one evaluation: scanners, chart, templates
def test_every_scanner_is_the_one_evaluation():
    f = synthetic_bars(400, tf="1d", seed=5)
    r = pa.evaluate(f)
    seen = 0
    for sc in pas.SCANNERS:
        reading, side = pas.READING[sc.id.split(".", 1)[1]]
        got = evaluate(parse(sc.text), SymbolData("S", {"1d": f}), base_tf="1d", params=sc.defaults()).to_numpy(float)
        np.testing.assert_array_equal(got, r.value(reading, side), err_msg=sc.id)
        seen += int(np.nansum(got))
        assert "$" in sc.text or not sc.params
    assert seen > 0 and len(pas.SCANNERS) == 25


def test_the_taxonomy_files_every_scanner():
    from pathlib import Path
    data = json.loads((Path(pas.__file__).parent / "data" / "taxonomy.json").read_text())
    filed = [s for s in data["screens"] if s["source"] == pas.SOURCE]
    assert filed == pas.taxonomy_rows()


def test_levels_through_screenql_and_the_settings_reach_the_evaluation():
    f = path(HS)
    got = evaluate(parse('PricePatternLevel("hs_projection", shoulder_tol=0.5)'), SymbolData("S", {"1d": f}), base_tf="1d", params={})
    np.testing.assert_array_equal(got.to_numpy(float), pa.evaluate(f).level("hs_projection"))
    loose = evaluate(parse('PricePattern("head_shoulders", "down", shoulder_tol=0.01)'), SymbolData("S", {"1d": f}), base_tf="1d",
                     params={})
    assert np.nansum(loose.to_numpy(float)) == 0


def test_the_chart_marks_are_the_rows_and_evidence_never_changes_them():
    f = synthetic_bars(400, tf="1d", seed=5)
    plain = pa_layer.marks_of(f, pa.PaSettings())
    rows = [e for e in pa.evaluate(f).rows() if e.bar >= len(f) - pa_layer.MAX_BARS]
    assert [(m["bar"], m["name"], m["side"]) for m in plain["marks"]] == [(e.bar, e.name, e.side) for e in rows]
    assert all(m["evidence"] == [] for m in plain["marks"])
    full = pa_layer.marks_of(f, pa.PaSettings(), pa.evidence_from(pa.EVIDENCE_LAYERS))
    assert [(m["bar"], m["name"]) for m in full["marks"]] == [(m["bar"], m["name"]) for m in plain["marks"]]
    with_rows = [m for m in full["marks"] if m["side"]]
    assert with_rows and all(len(m["evidence"]) == len(pa.EVIDENCE_LAYERS) for m in with_rows)
    assert all(isinstance(x["agrees"], (bool, type(None))) for m in with_rows for x in m["evidence"])
    for m in full["marks"]:
        for word in ("buy", "sell", "target", "stop"):
            assert word not in m["text"].lower(), m["text"]
    assert {lv["kind"] for lv in plain["levels"]} <= {"support", "resistance"}


def test_the_layer_refuses_an_unknown_evidence_layer():
    from fastapi import HTTPException
    assert pa_layer.parse_evidence("adx, macd,adx") == ("adx", "macd")
    with pytest.raises(HTTPException):
        pa_layer.parse_evidence("adx,tarot")


TEMPLATES = {"M1": (pa.MISSING_LINK, ML, "1d"), "M2": (pa.BASTON, BASTON, "1d"), "M4": (pa.HEAD_SHOULDERS, HS, "1d"),
             "M5": (pa.CUP, CUP, "1d")}


@pytest.mark.parametrize("tid", ["M1", "M2", "M3", "M4", "M5"])
def test_family_m_templates_signal_on_the_readings(tid):
    t = library.get(tid)
    assert t.family == "M" and t.reference_values and FAMILY_NAMES["M"] == "Price patterns" and FAMILY_FLAGS["M"] == "pa_pack"
    if tid == "M3":
        f = pd.concat([synthetic_bars(400, tf="1d", seed=5)])
        name = pa.TRIGGER
    else:
        name, pts, _ = TEMPLATES[tid]
        body = path(pts)
        pad = synthetic_bars(150, tf="1d", seed=2)                       # history before the shape (the engine's warm-up)
        pad = pad * (body["close"].iloc[0] / pad["close"].iloc[-1])
        pad["volume"] = 1000.0
        body = body.set_axis(pd.date_range(pad.index[-1] + pd.Timedelta(days=1), periods=len(body), freq="D", tz="UTC"))
        f = pd.concat([pad, body])
    s = engine.compute(C.compile_recipe(t, C.resolve_settings(t, "1d")), {"1d": f})
    got = sorted((int(i), int(s.direction[i])) for i in s.signal_bars())
    r = pa.evaluate(f)
    sides: dict[int, set[int]] = {}
    for e in r.events:
        if e.name == name and e.bar >= s.warmup_bars:
            sides.setdefault(e.bar, set()).add(1 if e.side == pa.UP else -1)
    want = sorted((b, next(iter(d))) for b, d in sides.items() if len(d) == 1)    # both sides on one bar: the engine's conflict
    assert got == want and got, tid
    for i, d in got:
        void = r.level({pa.MISSING_LINK: "missing_link_void", pa.BASTON: "baston_void", pa.TRIGGER: "trigger_void",
                        pa.HEAD_SHOULDERS: "hs_void", pa.CUP: "cup_void"}[name])[i]
        assert (void < f["close"].iloc[i]) if d == 1 else (void > f["close"].iloc[i])
