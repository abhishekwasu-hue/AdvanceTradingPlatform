"""M99 P5: the Fibonacci + Elliott pack - the rule checklist, the one evaluation, its ScreenQL functions, scanners, chart
layer and L templates (flag fib_ew_pack, off by default).

Synthetic shapes only, with made-up numbers (one per rule figure): a valid impulse, an impulse whose wave 3 is the
shortest, a wave 4 inside wave 1, a contracting diagonal, a zigzag, a contracting triangle and its thrust, a price
cluster; bars walking those shapes for the readings; no look-ahead by truncation at many bars; the one evaluation
shared by the scanner, the chart layer and the template (so the bot). Signals only - nothing here places an order.
"""
from __future__ import annotations

import random

import numpy as np
import pandas as pd
import pytest

from app.price_action import causal_swings as cs
from app.price_action import elliott_rules as er
from app.price_action import fib_ew
from app.price_action.daily_trend import rules as R
from app.quality.lookahead import synthetic_bars


def path_bars(points, step=1.0, wick=0.2, lead=30, start="2026-01-05 03:45"):
    """Closed 15-minute bars walking the polyline `points` (`step` per bar), each bar `wick` above / below its body."""
    closes = [float(points[0])] * lead
    for a, b in zip(points, points[1:]):
        k = max(1, int(round(abs(b - a) / step)))
        closes += list(np.linspace(a, b, k + 1)[1:])
    c = np.array(closes, float)
    o = np.r_[c[0], c[:-1]]
    idx = pd.date_range(start, periods=len(c), freq="15min", tz="UTC")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + wick, "low": np.minimum(o, c) - wick, "close": c, "volume": 0.0},
                        index=idx)


def mirror_bars(bars: pd.DataFrame, pivot: float = 400.0) -> pd.DataFrame:
    return pd.DataFrame({"open": pivot - bars["open"], "high": pivot - bars["low"], "low": pivot - bars["high"],
                         "close": pivot - bars["close"], "volume": bars["volume"]}, index=bars.index)


# Bars: an impulse down, then wave 1 up in five smaller waves, wave 2 in three, wave 3 starting ...
WAVE3_PATH = [200, 192, 196, 160, 170, 167, 180, 177, 190, 186, 188.4, 183, 194]
# ... running on through waves 3, 4 and 5, then a fall that confirms the end of wave 5 and goes through wave 4
FIFTH_PATH = [200, 192, 196, 160, 170, 167, 180, 177, 190, 186, 188.4, 183, 192, 189, 198, 195, 208, 205, 206.4, 203, 212,
              209, 210.4, 200, 203, 194]
# A rise, then a falling zigzag A-B-C whose wave C goes beyond the end of wave A, then a rise back over wave B's end
ZIGZAG_PATH = [160, 168, 165, 180, 177, 192, 189, 200, 194, 196.4, 188, 190.4, 184, 190, 187.6, 193, 187, 189.2, 181, 182.6, 178,
               186, 184, 194, 192, 200]
# A rise, then a contracting triangle and the move out of it
TRIANGLE_PATH = [160, 170, 166, 186, 181, 200, 190, 193, 182, 192, 189, 197, 190, 192, 186, 191, 189, 194, 191, 192, 189, 196, 194,
                 205, 202, 212]


def events(r: fib_ew.FibEwResult, name: str | None = None):
    return [(e.bar, e.name, e.side) for e in r.events if name is None or e.name == name]


# ----------------------------------------------------------------------------------------------- the rule checklist
def status(v: er.Validation) -> dict[str, str]:
    return {c.rule: c.status for c in v.checks}


def test_a_valid_impulse_keeps_every_hard_rule():
    v = er.validate([100, 110, 104, 125, 117, 130], er.IMPULSE, done=True)
    assert v.valid and not v.violations and not v.truncated and v.direction == 1
    assert status(v) == {"w2_within_w1": "pass", "w3_beyond_w1": "pass", "w4_out_of_w1": "pass", "w3_not_shortest": "pass",
                         "w5_beyond_w3": "pass"}
    assert R.impulse([100, 110, 104, 125, 117, 130], True)


def test_wave_three_the_shortest_breaks_the_count_and_is_listed_alone():
    x = [100, 110, 104, 112, 110.5, 125]                       # waves 10, 8, 14.5: wave 3 the shortest
    v = er.validate(x, er.IMPULSE, done=True)
    assert [c.rule for c in v.violations] == ["w3_not_shortest"]
    assert next(c for c in v.checks if c.rule == "w3_not_shortest").level == pytest.approx(118.5)   # wave 5 longer than 3 past here
    assert not R.impulse(x, True)


def test_wave_four_inside_wave_one_breaks_the_count_at_the_end_of_wave_one():
    x = [100, 110, 104, 125, 108, 130]
    v = er.validate(x, er.IMPULSE, done=True)
    assert [c.rule for c in v.violations] == ["w4_out_of_w1"]
    assert v.violations[0].level == 110
    while_running = er.validate([100, 110, 104, 125, 113], er.IMPULSE)          # wave 4 still running above the line
    assert [(c.rule, c.level) for c in while_running.open_lines()] == [("w4_out_of_w1", 110)]


def test_wave_two_beyond_the_start_of_wave_one_and_a_truncated_fifth():
    assert [c.rule for c in er.validate([100, 110, 99], er.IMPULSE).violations] == ["w2_within_w1"]
    v = er.validate([100, 110, 104, 125, 117, 123], er.IMPULSE, done=True)     # wave 5 stops short of wave 3's end
    assert v.valid and v.truncated and [c.rule for c in v.notes] == ["w5_beyond_w3"]
    assert not R.impulse([100, 110, 104, 125, 117, 123], True)                   # the generic function refuses a truncation


def test_a_contracting_diagonal_and_its_broken_size_rule():
    x = [100, 110, 104, 112, 107, 113.5]                       # 10, 6, 8, 5, 6.5: each shorter, wave 4 into wave 1
    d = er.validate(x, er.DIAGONAL, done=True)
    assert d.valid and R.wedge(x, True, "contracting")
    assert not er.validate(x, er.IMPULSE, done=True).valid            # as an impulse wave 4 overlaps wave 1
    longer = er.validate([100, 110, 104, 112, 107, 116], er.DIAGONAL, done=True)      # wave 5 (9) longer than wave 3 (8)
    assert [c.rule for c in longer.violations] == ["w5_vs_w3"]
    running = er.validate([100, 110, 104, 112, 107, 110], er.DIAGONAL)
    assert ("w5_vs_w3", 115.0) in [(c.rule, c.level) for c in running.open_lines()]  # where wave 5 would pass wave 3's length


def test_a_zigzag_its_truncated_c_and_b_beyond_the_start_of_a():
    v = er.validate([100, 90, 96, 86], er.ZIGZAG, done=True)
    assert v.valid and not v.truncated
    assert er.validate([100, 90, 96, 91], er.ZIGZAG, done=True).truncated
    assert [c.rule for c in er.validate([100, 90, 101], er.ZIGZAG).violations] == ["b_within_a"]
    assert [(c.rule, c.level) for c in er.validate([100, 90, 96, 88], er.ZIGZAG).open_lines()] == [("c_within_b", 96)]


def test_a_contracting_triangle_and_its_thrust_projection():
    x, bars = [100, 90, 98, 92, 96.5, 93.5], [0, 10, 20, 30, 40, 50]
    assert er.validate(x, er.TRIANGLE, done=True).valid
    assert [c.rule for c in er.validate([100, 90, 98, 92, 99], er.TRIANGLE).violations] == ["d_within_b"]
    # A-C line through (10, 90) and (30, 92): 89 at bar 0; B-D line through (20, 98) and (40, 96.5): 99.5 at bar 0
    assert er.thrust_width(x, bars) == pytest.approx(10.5)
    assert er.thrust_projection(x, bars, 93.5, trend=1) == pytest.approx(104.0)
    assert er.thrust_width([100, 90, 98, 88, 100], bars) is None                 # lines that open up: no thrust measured
    assert er.validate([100, 90, 98, 88, 100, 86], er.TRIANGLE, done=True, shape=er.EXPANDING).valid


def test_mirrored_shapes_read_the_same():
    for kind, x in ((er.IMPULSE, [100, 110, 104, 112, 110.5, 125]), (er.DIAGONAL, [100, 110, 104, 112, 107, 113.5]),
                    (er.ZIGZAG, [100, 90, 101]), (er.TRIANGLE, [100, 90, 98, 92, 99])):
        a, b = er.validate(x, kind, done=True), er.validate([-p for p in x], kind, done=True)
        assert status(a) == status(b) and a.direction == -b.direction


def test_the_checklist_agrees_with_the_generic_rule_functions():
    rng = random.Random(5)
    for _ in range(4000):
        n, d, x = rng.randint(2, 5), rng.choice([1, -1]), [100.0]
        for i in range(n):
            x.append(round(x[-1] + rng.uniform(1, 20) * (d if i % 2 == 0 else -d), 1))
        for done in (False, True):
            v = er.validate(x, er.IMPULSE, done)
            assert R.impulse(x, done) == (v.valid and not v.truncated)
            for shape in (er.CONTRACTING, er.EXPANDING):
                assert R.wedge(x, done, shape) == er.validate(x, er.DIAGONAL, done, shape).valid
            c_inside = not (n >= 3 and R.beyond(x[3], x[1], d))                 # the classic rule for C the generic one leaves out
            assert (R.triangle(x, done, "contracting")[0] and c_inside) == er.validate(x, er.TRIANGLE, done).valid
            assert R.triangle(x, done, "expanding")[0] == er.validate(x, er.TRIANGLE, done, er.EXPANDING).valid


def test_unknown_kinds_and_flat_waves_are_refused():
    with pytest.raises(ValueError):
        er.validate([100, 110], "flat")
    with pytest.raises(ValueError):
        er.validate([100, 100, 104], er.IMPULSE)


# ----------------------------------------------------------------------------------------------- clusters (pure)
def pivot(kind: str, price: float, bar: int) -> cs.Pivot:
    ts = pd.Timestamp("2026-01-05", tz="UTC") + pd.Timedelta(minutes=15 * bar)
    return cs.Pivot(1, kind, price, bar, ts, bar + 2, ts, "confirmed", "15m")


def test_a_cluster_needs_levels_from_different_swings():
    known = [pivot("L", 80, 0), pivot("H", 120, 10), pivot("L", 90, 20), pivot("H", 110, 30)]     # the open swing falls from 110
    s = fib_ew.FibEwSettings(band=1.5, band_mode="pct")
    levels, direction = fib_ew.cluster_levels(known, s)
    assert direction == -1 and all(lv.price < 110 for lv in levels)
    assert {lv.source for lv in levels} == {"last swing", "larger swing", "swing before", "golden section", "swing end"}
    found = fib_ew.qualifying(levels, s, None)
    near_100 = next(c for c in found if c.low <= 100 <= c.high)
    assert "retrace 0.5 of the last swing" in near_100.labels and "golden projection 0.5 of the swing before" in near_100.labels
    assert all(c.count >= 3 and len(c.sources) >= 2 for c in found)
    strict = fib_ew.qualifying(levels, fib_ew.FibEwSettings(band=1.5, band_mode="pct", min_sources=4), None)
    assert len(strict) < len(found)
    assert fib_ew.cluster_levels(known[:2], s) == ([], 0)


# ----------------------------------------------------------------------------------------------- settings
def test_settings_are_reference_values_with_bounds():
    s = fib_ew.FibEwSettings()
    for name, kind, ref, rule, label in fib_ew.SETTINGS_TABLE:
        assert getattr(s, name) == ref and label
        if kind in ("int", "bars", "num"):
            assert rule[0] <= ref <= rule[1]
    assert fib_ew.REFERENCE_LABEL == "reference - edit before use"
    with pytest.raises(ValueError):
        fib_ew.FibEwSettings(c_min=2.0, c_max=1.0)
    with pytest.raises(ValueError):
        fib_ew.FibEwSettings(band_mode="ticks")
    with pytest.raises(ValueError):
        fib_ew.settings_from({"reward_risk": 2})                               # no such setting: no R:R filter exists
    assert fib_ew.settings_from({"degree": 2.0, "zz_trigger": "wave_b"}) == fib_ew.FibEwSettings(degree=2, zz_trigger="wave_b")


# ----------------------------------------------------------------------------------------------- readings on bars
def test_wave_three_from_the_end_of_wave_two():
    bars = path_bars(WAVE3_PATH)
    r = fib_ew.evaluate(bars)
    up = [e for e in r.events if e.name == fib_ew.WAVE3 and e.side == "up"]
    assert len(up) == 1
    t = up[0].bar
    trig = dict(up[0].levels)["trigger"]
    assert bars["close"].iloc[t] > trig >= bars["close"].iloc[t - 1]                 # the first close beyond the level
    assert r.level("wave3_void")[t] == pytest.approx(159.8)                          # the start of wave 1 (its low)
    assert np.isnan(r.level("wave3_void")[t - 1]) and fib_ew.COUNT_NOTE in up[0].why
    other = fib_ew.evaluate(bars, fib_ew.FibEwSettings(w3_trigger="wave_1_end"))
    later = [e.bar for e in other.events if e.name == fib_ew.WAVE3 and e.side == "up"]
    assert not later or later[0] > t                                                  # the end of wave 1 is further away
    deep = fib_ew.evaluate(bars, fib_ew.FibEwSettings(w2_zone=True, w2_min=0.7, w2_max=0.786))
    assert not [e for e in deep.events if e.name == fib_ew.WAVE3 and e.side == "up"]   # wave 2 was shallower than the zone


def test_the_mirror_reads_the_mirror():
    bars = path_bars(WAVE3_PATH)
    a, b = fib_ew.evaluate(bars), fib_ew.evaluate(mirror_bars(bars))
    flip = {"up": "down", "down": "up"}
    assert sorted((t, n, flip[s]) for t, n, s in events(a)) == sorted(events(b))


def test_five_waves_complete_then_the_count_void():
    r = fib_ew.evaluate(path_bars(FIFTH_PATH))
    done = [e for e in r.events if e.name == fib_ew.IMPULSE_DONE]
    assert [(e.side) for e in done] == ["up"]
    assert "wave 3 is not the shortest of waves 1, 3 and 5" in done[0].why and fib_ew.COUNT_NOTE in done[0].why
    voids = [e for e in r.events if e.name == fib_ew.VOID and e.bar > done[0].bar]
    assert voids and voids[0].side == "down"


def test_zigzag_wave_c_complete():
    bars = path_bars(ZIGZAG_PATH)
    r = fib_ew.evaluate(bars)
    zz = [e for e in r.events if e.name == fib_ew.ZIGZAG_C]
    assert [(e.side) for e in zz] == ["up"]
    t, lv = zz[0].bar, dict(zz[0].levels)
    assert bars["close"].iloc[t] > lv["trigger"] >= bars["close"].iloc[t - 1]
    assert r.level("zigzag_c_void")[t] == pytest.approx(178 - 0.2)                     # the extreme of wave C (its low)
    assert "wave C went beyond the end of wave A" in zz[0].why
    narrow = fib_ew.evaluate(bars, fib_ew.FibEwSettings(c_zone=True, c_min=1.2, c_max=1.618))
    assert not [e for e in narrow.events if e.name == fib_ew.ZIGZAG_C]                # wave C shorter than the zone


def test_a_contracting_triangle_gives_a_thrust_projection_only():
    r = fib_ew.evaluate(path_bars(TRIANGLE_PATH))
    thrust = r.level("thrust")
    ts = np.flatnonzero(np.isfinite(thrust))
    assert len(ts)
    t = int(ts[0])
    m = r.waves.main[t]
    assert m.type == "TRIANGLE" and m.label == "COR_E"
    assert thrust[t] == pytest.approx(er.thrust_projection(m.points, m.bars, float(r.waves.running[t]), m.trend))
    assert not [e for e in r.events if "thrust" in e.name]                            # a projection, never a signal


def test_a_cluster_touch_is_the_first_reach_only():
    r = fib_ew.evaluate(path_bars(WAVE3_PATH))
    touches = [e for e in r.events if e.name == fib_ew.CLUSTER]
    assert touches and all(e.side in ("up", "down") for e in touches)
    for e in touches:
        lv = dict(e.levels)
        assert lv["cluster_low"] <= lv["cluster_high"]
    assert len({(e.origin, dict(e.levels)["cluster_low"]) for e in touches}) == len(touches)


# ----------------------------------------------------------------------------------------------- no look-ahead
@pytest.mark.parametrize("bars", [pytest.param(path_bars(FIFTH_PATH), id="fifth"), pytest.param(path_bars(ZIGZAG_PATH), id="zigzag"),
                                  pytest.param(path_bars(TRIANGLE_PATH), id="triangle"),
                                  pytest.param(synthetic_bars(700, seed=17), id="random")])
def test_truncation_at_many_bars(bars):
    """Every reading and level at bar t equals the one computed on the bars cut at t."""
    full = fib_ew.evaluate(bars)
    rng = random.Random(3)
    cuts = sorted(set(rng.sample(range(40, len(bars)), 25)) | {e.bar for e in full.events})
    for t in cuts:
        cut = fib_ew.evaluate(bars.iloc[: t + 1])
        for key, flag in full.flags.items():
            assert np.array_equal(flag[: t + 1], cut.flags[key]), (t, key)
        for what in fib_ew.LEVELS:
            assert np.allclose(full.level(what)[: t + 1], cut.level(what), equal_nan=True), (t, what)
        assert [(e.bar, e.name, e.side, e.why) for e in full.events if e.bar <= t] == [(e.bar, e.name, e.side, e.why) for e in cut.events]


# ----------------------------------------------------------------------------------------------- one evaluation for all
def _screen(text: str, bars: pd.DataFrame, tf: str = "15m") -> np.ndarray:
    from app.screener import parse
    from app.screener.runtime import SymbolData, _Eval
    ev = _Eval(SymbolData("S", {tf: bars}), tf, {}, {}, memo={})
    return np.asarray(ev.ev(parse(text), tf), dtype=float)


def test_screenql_reads_the_same_evaluation():
    bars = path_bars(FIFTH_PATH)
    r = fib_ew.evaluate(bars)
    for name in fib_ew.NAMES:
        for side in fib_ew.SIDE_CHOICES:
            assert np.array_equal(_screen(f'FibEw("{name}", "{side}")', bars), r.value(name, side)), (name, side)
    for what in fib_ew.LEVELS:
        assert np.allclose(_screen(f'FibEwLevel("{what}")', bars), r.level(what), equal_nan=True), what
    other = fib_ew.evaluate(bars, fib_ew.FibEwSettings(degree=2, void_on="wick"))
    assert np.array_equal(_screen('FibEw("count_void", degree=2, void_on="wick")', bars), other.value("count_void"))


def test_the_registry_holds_the_settings_behind_the_flag():
    from app.screener.registry import FIB_EW_FLAG, FIB_EW_FUNCTIONS, FUNCTIONS
    assert FIB_EW_FLAG == "fib_ew_pack" and set(FIB_EW_FUNCTIONS) == {"FibEw", "FibEwLevel"}
    for name in FIB_EW_FUNCTIONS:
        spec = FUNCTIONS[name]
        assert spec.flag == FIB_EW_FLAG
        kws = {a.name: a for a in spec.kwargs}
        for setting, kind, ref, rule, _ in fib_ew.SETTINGS_TABLE:
            assert kws[setting].default == ref
            if kind in ("int", "bars", "num"):
                assert kws[setting].bounds == (float(rule[0]), float(rule[1]))
            if kind == "choice":
                assert kws[setting].choices == tuple(rule)
    from app.screener import parse
    from app.screener.validator import validate
    assert not validate(parse('FibEw("wave3_entry", break_k=9)'), base_tf="1h", params={}).ok
    assert not validate(parse('FibEw("wave3_entry", w2_min=0.8, w2_max=0.6)'), base_tf="1h", params={}).ok


def test_the_gate_names_the_functions_and_alerts_fire_on_close_only():
    from app.screener import fib_ew_gate, parse
    ast = parse('FibEw("wave3_entry") AND close > FibEwLevel("cluster_high")')
    assert fib_ew_gate.flagged_calls(ast) == ["FibEw", "FibEwLevel"]
    assert "fib_ew_pack" in fib_ew_gate.refusal(["FibEw"]) and "bar close only" in fib_ew_gate.intrabar_refusal(["FibEw"])


def test_the_flag_is_off_by_default_and_listed():
    from app.platform import controls, flag_catalog, flag_state, trader_pack
    assert "fib_ew_pack" in controls.FEATURE_FLAGS and "fib_ew_pack" not in controls.DEFAULT_ON_FLAGS
    assert flag_catalog.FLAG_INFO["fib_ew_pack"].status == "ready" and flag_catalog.FLAG_GROUP["fib_ew_pack"] == "screen_library"
    assert flag_state.FLAG_STATUS["fib_ew_pack"].state == "ready"
    assert "fib_ew_pack" in trader_pack.EXCLUDED_REASONS


def test_scanners_read_fib_ew_with_reference_values_and_are_filed():
    import json
    from pathlib import Path

    from app.screener import fib_ew_scanners as fes
    from app.screener import parse
    from app.screener import taxonomy as tx
    from app.screener.validator import validate
    filed = [s for s in json.loads((Path(__file__).resolve().parents[1] / "app/screener/data/taxonomy.json").read_text())["screens"]
             if s["source"] == fes.SOURCE]
    assert filed == fes.taxonomy_rows()
    for s in fes.SCANNERS:
        assert s.text.startswith("FibEw(") and all(p.ref for p in s.params)
        assert validate(parse(s.text), base_tf=s.base_tf, params=s.defaults()).ok, s.id
        assert not tx.advice_words(s.name) and not tx.advice_words(s.definition)
    assert fib_ew.COUNT_NOTE in fes.payload()["footer"]
    bars = path_bars(FIFTH_PATH)
    hit = _screen(fes.BY_ID["fib_ew.impulse_up_done"].text.replace("$degree", "1"), bars)
    assert np.array_equal(hit, fib_ew.evaluate(bars).value(fib_ew.IMPULSE_DONE, "up"))


def test_the_chart_layer_marks_are_the_evaluations_events():
    from app.charts import fib_ew_layer as layer
    bars = path_bars(FIFTH_PATH)
    got = layer.layer_of(bars, fib_ew.FibEwSettings(), max_bars=len(bars))
    r = fib_ew.evaluate(bars)
    assert [(m["name"], m["side"], m["text"]) for m in got["marks"]] == [(e.name, e.side, e.why) for e in r.events]
    assert fib_ew.COUNT_NOTE in layer.NOTE and "not a target" in layer.THRUST_WORDS
    assert got["available"] and got["fib"] and all(f["kind"] in ("retrace", "extend", "golden_projection") for f in got["fib"])
    count = got["count"]
    assert count is not None and count["note"] == fib_ew.COUNT_NOTE and count["labels"][0] == "0"
    tri = layer.layer_of(path_bars(TRIANGLE_PATH[:21]), fib_ew.FibEwSettings())
    if tri["count"] and tri["count"]["thrust"]:
        assert tri["count"]["thrust"]["words"] == layer.THRUST_WORDS


@pytest.mark.parametrize("tid,event,void", [("L1", fib_ew.ZIGZAG_C, "zigzag_c_void"), ("L2", fib_ew.WAVE3, "wave3_void")])
def test_the_templates_signal_on_the_evaluations_events(tid, event, void):
    from app.strategy_templates import engine, library
    from app.strategy_templates.compile import compile_recipe, resolve_settings
    t = library.get(tid)
    assert t.family == "L" and t.reference_values and t.sides_default == "both" and t.trigger is None
    rec = compile_recipe(t, resolve_settings(t, "15m"))
    assert rec.required_flags == {"fib_ew_pack"}
    path = ZIGZAG_PATH if tid == "L1" else WAVE3_PATH
    bars = path_bars(path, lead=300)                                       # past the warm-up of the wave degree
    sig = engine.compute(rec, {"15m": bars})
    r = fib_ew.evaluate(bars)
    want = {e.bar: (1 if e.side == "up" else -1) for e in r.events if e.name == event}
    assert want and {int(i): int(sig.direction[i]) for i in np.flatnonzero(sig.direction)} == want
    for i in want:
        assert sig.invalidation[i] == pytest.approx(r.level(void)[i])


def test_family_l_is_listed_only_behind_its_flag_and_its_words_are_clean():
    from app.strategy_templates import language, routes
    assert routes.FAMILY_FLAGS["L"] == "fib_ew_pack" and routes.FAMILY_NAMES["L"] == "Fibonacci + Elliott"
    for token in ("zigzag_c_complete_fall", "zigzag_c_complete_rise", "wave_three_up_may_start", "wave_three_down_may_start",
                  "count_is_an_interpretation"):
        assert not language.guard(language.TOKENS[token]), token
    for tid in ("L1", "L2"):
        from app.strategy_templates import library
        t = library.get(tid)
        for side in ("long", "short"):
            text = language.render([p.token for p in getattr(t.signal_text, side)], timeframe="1h")
            assert not language.guard(text) and "interpretation" in text
