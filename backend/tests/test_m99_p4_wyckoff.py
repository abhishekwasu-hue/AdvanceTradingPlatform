"""M99 P4: the Wyckoff pack - the range reader (app/price_action/wyckoff.py) on synthetic shapes (our own numbers: an
accumulation-shaped range with a shake under it, its mirror, a range left without a shake, and negatives), no look-ahead,
no volume data, the ScreenQL functions, the scanners, the chart layer, the K templates and the flag."""
from __future__ import annotations

import json
import uuid

import numpy as np
import pandas as pd
import pytest

from app.charts import wyckoff_layer
from app.platform import controls, flag_catalog, flag_state, trader_pack
from app.price_action import wyckoff
from app.screener import parse, wyckoff_gate
from app.screener import taxonomy as tx
from app.screener import wyckoff_scanners as wys
from app.screener.registry import FUNCTIONS, WYCKOFF_FLAG, WYCKOFF_FUNCTIONS, WYCKOFF_KWARGS
from app.screener.runtime import SymbolData, evaluate, run_screen
from app.screener.validator import validate
from app.signals.language import STRICT_WORDS
from app.strategy_templates import compile as C
from app.strategy_templates import engine, language, library
from app.strategy_templates.routes import FAMILY_FLAGS, FAMILY_NAMES
from tests.wyckoff_shapes import Path, accumulation, broken_without_shake, deep_break_down, falling_market, mirror

S, W = wyckoff.STRENGTH, wyckoff.WEAKNESS
TF = "15m"


def _events(f: pd.DataFrame, skip: tuple[str, ...] = ("significant_bar",), **settings) -> list[tuple[int, str, str, int]]:
    r = wyckoff.evaluate(f, wyckoff.settings_from(settings) if settings else None, TF)
    return [(e.bar, e.name, e.side, e.origin) for e in r.events if e.name not in skip]


def _names(f: pd.DataFrame, **settings) -> list[str]:
    return [e[1] for e in _events(f, **settings)]


# --------------------------------------------------------------------------------------------- the shapes
def test_an_accumulation_shape_reads_phase_a_to_e_with_each_event_on_its_confirming_bar():
    f, at = accumulation()
    r = wyckoff.evaluate(f, None, TF)
    ev = {e.name: e for e in r.events if e.name != "significant_bar"}
    assert [e for e in _names(f)] == ["climax", "ar", "st", "shake", "shake_test", "last_point", "break", "back_up"]
    assert ev["climax"].bar == at["climax"] and ev["climax"].side == S and ev["climax"].variant == "a climax bar"
    # the reaction's swing is known pivot_n bars after its own bar, never on it
    assert abs(ev["ar"].origin - at["ar"]) <= 1 and ev["ar"].bar == ev["ar"].origin + wyckoff.WyckoffSettings().pivot_n
    assert abs(ev["st"].origin - at["st"]) <= 1 and ev["st"].bar > ev["st"].origin
    assert ev["shake"].bar == ev["shake"].origin == at["shake"] and ev["shake"].side == S
    assert ev["shake_test"].bar == at["test"] and ev["shake_test"].variant == "no supply"
    assert abs(ev["last_point"].origin - at["last_point"]) <= 1 and ev["last_point"].side == S
    assert ev["break"].origin == at["break_start"] and ev["break"].bar == at["break_start"] + 2      # k_back closes in a row
    assert ev["break"].variant == "after a shake on the other side"
    assert ev["break"].bar < ev["back_up"].bar <= at["back_up"]
    # the phases as known bar by bar, and the range's own levels
    first = {p: int(np.flatnonzero(r.phase == p)[0]) for p in wyckoff.PHASES}
    assert first["A"] == at["climax"] and first["B"] == ev["st"].bar and first["C"] == ev["shake"].bar
    assert first["D"] == ev["break"].bar and first["E"] == ev["back_up"].bar
    assert r.top[ev["ar"].bar] == pytest.approx(f["high"].iloc[ev["ar"].origin])
    assert r.bottom[ev["ar"].bar] == pytest.approx(f["low"].iloc[at["climax"]])
    assert np.isnan(r.top[ev["ar"].bar - 1])                                  # unknown before the reaction is known
    assert r.shake_extreme[ev["shake_test"].bar] == pytest.approx(f["low"].iloc[at["shake"]])
    assert r.lean[ev["break"].bar] == S and r.bias[-1] == S


def test_the_mirror_shape_reads_the_same_bars_with_the_other_side():
    f, _ = accumulation()
    up = _events(f)
    down = _events(mirror(f))
    assert [(b, n, o) for b, n, _, o in up] == [(b, n, o) for b, n, _, o in down]
    assert all(su != sd for (_, _, su, _), (_, _, sd, _) in zip(up, down, strict=True))
    r = wyckoff.evaluate(mirror(f), None, TF)
    ev = {e.name: e for e in r.events}
    assert ev["climax"].variant == "a climax bar" and ev["climax"].side == W and ev["shake"].side == W and ev["shake_test"].variant == "no demand"
    assert ev["break"].side == W and r.bias[-1] == W


def test_a_range_left_without_a_shake_reads_a_break_from_phase_b():
    f, _ = broken_without_shake()
    names = _names(f)
    assert "shake" not in names and "shake_test" not in names and "break" in names
    r = wyckoff.evaluate(f, None, TF)
    brk = next(e for e in r.events if e.name == "break")
    assert brk.variant == "without a shake" and r.phase[brk.bar - 1] == "B"


def test_a_break_under_the_range_is_a_sign_of_weakness():
    f, at = deep_break_down()
    ev = [e for e in _events(f) if e[1] == "break"]
    k = wyckoff.WyckoffSettings().k_back
    assert len(ev) == 1 and ev[0][2] == W and at["break_start"] <= ev[0][3] and ev[0][0] == ev[0][3] + k - 1
    assert f["close"].iloc[ev[0][3]:ev[0][0] + 1].max() < wyckoff.evaluate(f, None, TF).bottom[ev[0][0]]


def test_negatives_no_climax_heavy_secondary_test_short_phase_b_and_no_quiet_test():
    assert _names(falling_market()) == []                                    # a fall without a climax: no range
    f, _ = accumulation(st_volume=500.0)                                     # the swing back is heavier than the climax
    assert _names(f) == ["climax", "ar"]
    f, _ = accumulation()
    names = _names(f, b_vs_a=3.0)                                            # Phase B too short: an edge test, not a shake
    assert "edge_test" in names and "shake" not in names
    edge = next(e for e in _events(f, b_vs_a=3.0) if e[1] == "edge_test")
    assert edge[2] == W                                                      # a test under the range reads as weakness
    f, _ = accumulation(quiet_test=False)                                    # the test bar on heavy volume
    assert "shake" in _names(f) and "shake_test" not in _names(f)


def test_in_phase_c_a_poke_at_the_same_edge_is_a_new_shake_only_beyond_the_last_shakes_extreme():
    f, at = accumulation(poke=139.0)                                         # under the bottom, above the spring's low
    shakes = [e for e in _events(f) if e[1] == "shake"]
    assert [e[0] for e in shakes] == [at["shake"]]
    f, at = accumulation(poke=137.5)                                         # beyond the spring's low: a new shake
    shakes = [e for e in _events(f) if e[1] == "shake"]
    assert [e[0] for e in shakes] == [at["shake"], at["poke"]]
    r = wyckoff.evaluate(f, None, TF)
    assert r.shake_extreme[at["poke"]] == pytest.approx(137.5)


def test_after_the_break_a_heavy_back_up_warns_and_a_close_back_inside_fails_the_break():
    f, _ = accumulation(then="heavy")
    names = _names(f)
    assert "heavy_back_up" in names and "back_up" not in names[: names.index("heavy_back_up") + 1]
    f, at = accumulation(then="fail")
    ev = _events(f)
    failed = [e for e in ev if e[1] == "failed_break"]
    assert failed and failed[0][0] == at["fail"] and failed[0][2] == W
    r = wyckoff.evaluate(f, None, TF)
    assert r.phase[at["fail"]] == "B" and r.lean[at["fail"]] == ""
    f, _ = accumulation(then="away")                                         # no back-up: Phase E after bu_bars
    r = wyckoff.evaluate(f, None, TF)
    assert "back_up" not in _names(f) and r.phase[-1] == "E"


def test_the_shake_class_by_depth_volume_and_spread():
    s = wyckoff.WyckoffSettings()
    assert wyckoff._shake_class(s, 1.0, 2.5, 2.5).startswith("class 1")
    assert wyckoff._shake_class(s, 0.2, 0.6, 0.6).startswith("class 3")
    assert wyckoff._shake_class(s, 0.2, 1.5, 0.6).startswith("class 2")
    assert wyckoff._shake_class(s, float("nan"), 1.0, 1.0) == "class unknown"


def test_the_significant_bar_closes_beyond_the_last_swing_on_a_wide_spread_and_volume():
    f, _ = accumulation()
    r = wyckoff.evaluate(f, None, TF)
    for e in (e for e in r.events if e.name == "significant_bar"):
        i = e.bar
        assert e.info["rel_range"] > 1.3 and e.info["rel_volume"] >= 1.0
        assert (f["close"].iloc[i] > f["close"].iloc[i - 1]) or e.side == W


# --------------------------------------------------------------------------------------------- no look-ahead
def _random(seed: int, n: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    p = Path(100.0)
    for _ in range(n):
        o = p.last
        c = o + rng.normal(0, 1.0)
        v = float(rng.lognormal(4.6, 0.6))
        if rng.random() < 0.03:
            v *= 4
            c = o + rng.normal(0, 4.0)
        p.bar(o, c, v, wick=abs(rng.normal(0, 0.6)))
    return p.frame()


@pytest.mark.parametrize("make", [lambda: accumulation()[0], lambda: mirror(accumulation()[0]), lambda: accumulation(then="fail")[0],
                                  lambda: _random(3), lambda: _random(8)])
def test_cut_bars_give_the_same_readings_as_the_full_run(make):
    f = make()
    full = wyckoff.evaluate(f, None, TF)
    for t in range(40, len(f)):
        cut = wyckoff.evaluate(f.iloc[:t + 1], None, TF)
        assert [(e.bar, e.name, e.side, e.origin, e.variant) for e in cut.events] == \
               [(e.bar, e.name, e.side, e.origin, e.variant) for e in full.events if e.bar <= t], t
        for arr in ("phase", "lean", "bias"):
            assert list(getattr(cut, arr)) == list(getattr(full, arr)[:t + 1]), (t, arr)
        for arr in ("top", "bottom", "shake_extreme", "break_base"):
            assert np.array_equal(getattr(cut, arr), getattr(full, arr)[:t + 1], equal_nan=True), (t, arr)


def test_the_warm_up_is_unknown_never_a_no():
    f, _ = accumulation()
    r = wyckoff.evaluate(f, None, TF)
    w = wyckoff.WyckoffSettings().warmup()
    assert np.isnan(r.value("shake")[: w - 1]).all() and not np.isnan(r.value("shake")[w:]).any()


# --------------------------------------------------------------------------------------------- no volume data
def test_no_volume_data_reads_no_range_and_no_event_only_the_structure_lean():
    f, _ = accumulation()
    g = f.assign(volume=0.0)
    r = wyckoff.evaluate(g, None, TF)
    assert not r.has_volume and r.events == [] and (r.phase == "").all()
    for name in wyckoff.EVENTS + ("phase_a", "phase_c", "in_range"):
        assert np.isnan(r.value(name)).all()
    assert not np.isnan(r.value("bias")[-1])                                 # the lean is read from the structure alone
    assert wyckoff_layer.layer_of(g, wyckoff.WyckoffSettings(), TF) == {
        "available": False, "reason": wyckoff.NO_VOLUME, "marks": [], "ranges": [], "phases": [], "bars": len(g)}


def test_an_index_without_its_futures_volume_is_not_checked():
    sc = wys.scanner("wyckoff.spring")
    ast = parse(sc.text)
    validated = validate(ast, base_tf=TF, params=sc.defaults())
    f, _ = accumulation()
    got = run_screen(ast, validated, [SymbolData("NIFTY 50", {TF: f.assign(volume=0.0)}), SymbolData("ABC", {TF: f})],
                     base_tf=TF, params=sc.defaults())
    assert got[0].matched is False and "volume not available" in str(got[0].reason)
    assert got[1].reason is None


# --------------------------------------------------------------------------------------------- settings, registry, ScreenQL
def test_settings_reference_values_bounds_and_kinds():
    assert {r["name"]: r["value"] for r in wyckoff.reference_settings()} == {name: ref for name, _, ref, _ in wyckoff.SETTINGS_TABLE}
    assert all(r["label"] == "reference - edit before use" for r in wyckoff.reference_settings())
    assert wyckoff.settings_from({"k_back": 2, "deep": 1.0}).k_back == 2
    for bad in ({"k_back": 0}, {"st_below": 2}, {"a_bars": 2.5}, {"nope": 1}, {"confirm": True}, {"swing_k": "x"}):
        with pytest.raises(ValueError):
            wyckoff.settings_from(bad)


def test_the_registry_mirrors_the_settings():
    assert set(WYCKOFF_FUNCTIONS) == {"Wyckoff", "WyckoffLevel"}
    spec = FUNCTIONS["Wyckoff"]
    assert spec.flag == WYCKOFF_FLAG and set(spec.args[0].choices) == set(wyckoff.NAMES_ALL)
    assert set(FUNCTIONS["WyckoffLevel"].args[0].choices) == set(wyckoff.LEVELS) and FUNCTIONS["WyckoffLevel"].unit == "price"
    table = {name: (kind, ref) for name, kind, ref, _ in wyckoff.SETTINGS_TABLE}
    assert set(WYCKOFF_KWARGS.values()) == set(table)
    for arg in spec.kwargs:
        kind, ref = table[WYCKOFF_KWARGS[arg.name]]
        assert arg.default == ref and not arg.required
        assert arg.type == {"bars": "window", "flag": "bool", "num": "num"}[kind]


def test_screenql_reads_the_one_evaluation_and_the_levels():
    f, at = accumulation()
    d = SymbolData("S", {TF: f})
    r = wyckoff.evaluate(f, None, TF)
    for name, side in (("shake", "strength"), ("break", "any"), ("phase_c", "any"), ("bias", "strength"), ("in_range", "any")):
        got = evaluate(parse(f'Wyckoff("{name}", "{side}")'), d, base_tf=TF).to_numpy()
        assert np.array_equal(got, r.value(name, side), equal_nan=True), name
    assert evaluate(parse('Wyckoff("shake", "weakness")'), d, base_tf=TF).iloc[at["shake"]] == 0.0
    top = evaluate(parse('WyckoffLevel("range_top")'), d, base_tf=TF).to_numpy()
    assert np.array_equal(top, r.top, equal_nan=True)
    assert evaluate(parse('Wyckoff("shake", b_vs_a=3)'), d, base_tf=TF).iloc[at["shake"]] == 0.0


def test_the_history_a_call_asks_for_covers_the_warm_up():
    need = validate(parse('Wyckoff("shake")'), base_tf="1d").lookback["1d"]
    assert need >= wyckoff.WyckoffSettings().warmup()
    need = validate(parse('Wyckoff("shake", n=60, trend_bars=40)'), base_tf="1d").lookback["1d"]
    assert need >= wyckoff.settings_from({"avg_n": 60, "trend_bars": 40}).warmup()


# --------------------------------------------------------------------------------------------- the scanners
def test_every_scanner_validates_with_its_reference_values_and_matches_its_shape():
    f, _ = accumulation()
    g = mirror(f)
    r_up, r_dn = wyckoff.evaluate(f, None, TF), wyckoff.evaluate(g, None, TF)
    for s in wys.SCANNERS:
        assert all(p.ref and p.value is not None for p in s.params)
        validated = validate(parse(s.text), base_tf=TF, params=s.defaults())
        assert validated.ok, (s.id, [p.message for p in validated.problems])
        reading, side = wys.READING[s.id.split(".", 1)[1]]
        frame, r = (g, r_dn) if side == W else (f, r_up)
        got = evaluate(parse(s.text), SymbolData("S", {TF: frame}), base_tf=TF, params=s.defaults()).to_numpy()
        want = r.value(reading, side)
        assert np.array_equal(got, want, equal_nan=True), s.id
        assert np.nansum(got) >= 1, s.id
    assert wys.payload()["chip"] == "Wyckoff"


def test_the_scanners_are_filed_in_the_taxonomy_behind_the_flag():
    tax = tx.load()
    filed = {s.id: s for s in tax.screens if s.source == "wyckoff"}
    assert set(filed) == set(wys.BY_ID)
    assert filed["wyckoff.spring"].tags["direction"] == "long" and filed["wyckoff.utad"].tags["direction"] == "short"
    assert tx.sources()["wyckoff"].flag == WYCKOFF_FLAG and tx.lint() == []
    raw = json.loads(tx.DATA_FILE.read_text())
    assert [row for row in raw["screens"] if row["source"] == "wyckoff"] == wys.taxonomy_rows()


# --------------------------------------------------------------------------------------------- the chart layer
def test_the_chart_marks_are_the_scanners_matches_on_bars_cut_at_each_mark():
    f, _ = accumulation()
    got = wyckoff_layer.layer_of(f, wyckoff.WyckoffSettings(), TF)
    assert got["available"] and got["marks"] and len(got["ranges"]) == 1
    box = got["ranges"][0]
    assert box["after"] == "fall" and box["open"] and box["top"] > box["bottom"]
    assert [p["phase"] for p in got["phases"]] == ["A", "B", "C", "D", "E"]
    for m in got["marks"]:
        i = int(m["bar"])
        cut = f.iloc[:i + 1]
        value = evaluate(parse(f'Wyckoff("{m["name"]}", "{m["side"]}")'), SymbolData("S", {TF: cut}), base_tf=TF).iloc[-1]
        assert value == 1.0, m["name"]


def test_the_why_text_is_structure_words_with_no_trade_wording_and_no_price():
    f, _ = accumulation()
    for frame in (f, mirror(f), accumulation(then="fail")[0], accumulation(then="heavy")[0], _random(5)):
        marks = wyckoff_layer.layer_of(frame, wyckoff.WyckoffSettings(), TF)["marks"]
        words = {w.lower() for m in marks for w in pd.Series(m["text"]).str.findall(r"[A-Za-z]+")[0]}
        assert not words & set(STRICT_WORDS) - {"long", "short", "price", "order", "should", "must", "sure"}
        assert not words & {"buy", "buying", "sell", "selling", "target", "stop", "stoploss", "sl", "entry", "profit"}
        for m in marks:
            for col in ("close", "high", "low"):
                assert f'{frame[col].iloc[m["bar"]]:.2f}' not in m["text"]
            assert m["side"] in (S, W) and m["short"]


# --------------------------------------------------------------------------------------------- the K templates
def _run(tid: str, f: pd.DataFrame, sides: str = "both", **params):
    t = library.get(tid)
    return engine.compute(C.compile_recipe(t, C.resolve_settings(t, TF, sides, params)), {TF: f})


def test_the_k_templates_compile_and_their_texts_pass_the_guard():
    assert FAMILY_NAMES["K"] == "Wyckoff" and FAMILY_FLAGS["K"] == WYCKOFF_FLAG
    want = {"K1": "long", "K2": "short", "K3": "both"}
    for tid, sides in want.items():
        t = library.get(tid)
        assert t.family == "K" and t.reference_values and t.sides_default == sides and t.exit_signal is None
        assert C.validate_template(t) == []
        for tf in t.allowed_timeframes:
            settings = C.resolve_settings(t, tf, "both", {"context_timeframe": "1w" if tf == "1d" else "1d"})
            recipe = C.compile_recipe(t, settings)
            assert WYCKOFF_FLAG in recipe.required_flags
            for side in ("long", "short"):
                text = library.signal_text(t, settings, side)
                assert text and language.guard(text) == [], text
        for p in t.params:                                                  # every number a user setting, labelled
            assert p.type in ("bool", "enum", "timeframe") or "Reference" in p.help
    for token in ("range_spring_then_test", "range_upthrust_then_test", "range_last_point_of_support", "range_last_point_of_supply",
                  "range_back_up_after_break_up", "range_back_up_after_break_down"):
        assert language.guard(language.TOKENS[token]) == []


@pytest.mark.parametrize("tid,event", [("K1", "shake_test"), ("K2", "last_point"), ("K3", "back_up")])
def test_each_k_template_signals_on_its_event_both_ways_with_the_idea_wrong_beyond_the_range_level(tid, event):
    f, _ = accumulation()
    for frame, direction, side in ((f, 1, S), (mirror(f), -1, W)):
        r = wyckoff.evaluate(frame, None, TF)
        bar = next(e.bar for e in r.events if e.name == event and e.side == side)
        s = _run(tid, frame)
        assert {int(t): int(s.direction[t]) for t in s.signal_bars()} == {bar: direction}
        level = r.shake_extreme[bar] if tid != "K3" else r.break_base[bar]
        assert s.invalidation[bar] == pytest.approx(level)
        assert (s.invalidation[bar] < frame["close"].iloc[bar]) == (direction == 1)
        assert len(_run(tid, frame.assign(volume=0.0)).signal_bars()) == 0   # no volume data: no signal, never a guess


def test_the_larger_structure_filter_is_off_by_default_and_reads_closed_higher_bars_when_on():
    f, _ = accumulation()
    t = library.get("K1")
    assert next(p for p in t.params if p.key == "htf_mode").default == "off"
    on = _run("K1", f, htf_mode="with", context_timeframe="1h")
    off = _run("K1", f)
    assert len(off.signal_bars()) == 1
    assert set(int(x) for x in on.signal_bars()) <= set(int(x) for x in off.signal_bars())


# --------------------------------------------------------------------------------------------- the flag
def test_the_flag_is_registered_off_with_catalog_state_and_pack_entries():
    assert WYCKOFF_FLAG == "wyckoff_pack" == wyckoff_gate.FLAG == wyckoff_layer.FLAG == wys.FLAG
    assert WYCKOFF_FLAG in controls.FEATURE_FLAGS and WYCKOFF_FLAG in controls.DEFAULT_OFF_FLAGS
    assert "places no orders" in controls.FEATURE_FLAGS[WYCKOFF_FLAG]
    assert flag_catalog.FLAG_GROUP[WYCKOFF_FLAG] == "screen_library" and WYCKOFF_FLAG in flag_state.FLAG_STATUS
    assert WYCKOFF_FLAG in trader_pack.EXCLUDED_REASONS
    ast = parse('Wyckoff("shake") AND close > WyckoffLevel("range_bottom")')
    assert wyckoff_gate.flagged_calls(ast) == ["Wyckoff", "WyckoffLevel"]
    assert WYCKOFF_FLAG in wyckoff_gate.refusal(["Wyckoff"]) and "bar close only" in wyckoff_gate.intrabar_refusal(["Wyckoff"])


def _org(tag, on):
    from tests.test_n1_e1_execution_core import _flag
    from tests.test_trading_worker import _tenant
    t = _tenant(f"m99p4-{tag}-{uuid.uuid4().hex[:6]}@example.com", with_credentials=False)
    for name in ("screener_v2", "screener_intrabar", "screener_gallery") + ((WYCKOFF_FLAG,) if on else ()):
        _flag(name, t["tenant_id"])
    return t


def test_flag_off_hides_and_refuses_the_pack():
    from tests.test_auth_api import client
    off, on = _org("off", False), _org("on", True)
    source = 'Wyckoff("shake")'
    entries_off = client.get("/api/screener/registry", headers=off["headers"]).json()["entries"]
    entries_on = client.get("/api/screener/registry", headers=on["headers"]).json()["entries"]
    assert "Wyckoff" not in entries_off and "Wyckoff" in entries_on and "WyckoffLevel" in entries_on
    refused = client.post("/api/screener/validate", headers=off["headers"], json={"source": source, "base_tf": "1h"}).json()
    assert not refused["ok"] and WYCKOFF_FLAG in json.dumps(refused["problems"])
    assert client.post("/api/screener/validate", headers=on["headers"], json={"source": source, "base_tf": "1h"}).json()["ok"]
    rule = {"name": "wy", "kind": "instrument", "symbol": "ABC", "condition": source, "base_tf": "1h"}
    r = client.post("/api/alerts/rules", headers=off["headers"], json=rule)
    assert r.status_code == 422 and WYCKOFF_FLAG in r.text
    r = client.post("/api/alerts/rules", headers=on["headers"], json={**rule, "fire_on": "intrabar"})
    assert r.status_code == 422 and "bar close only" in r.text
    assert client.get("/api/screener/wyckoff", headers=off["headers"]).status_code in (403, 404, 503)
    lib = client.get("/api/screener/wyckoff", headers=on["headers"])
    assert lib.status_code == 200 and len(lib.json()["scanners"]) == len(wys.SCANNERS)
    layer = client.get("/api/charts/layers/wyckoff", headers=off["headers"], params={"symbol": "ABC"})
    assert layer.status_code in (403, 404, 503)
