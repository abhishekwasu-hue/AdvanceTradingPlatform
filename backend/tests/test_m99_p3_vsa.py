"""M99 P3: the volume spread pack (app/price_action/vsa.py) - shape fixtures (our own numbers) with a positive and a
negative case per reading, the background and confirmation switches, no look-ahead (cut bars), no volume data, the
ScreenQL function, the "Volume spread" scanners, the chart layer, the J1 template and the flag."""
from __future__ import annotations

import json
import uuid

import numpy as np
import pandas as pd
import pytest

from app.charts import vsa_layer
from app.platform import controls, flag_catalog, flag_state, trader_pack
from app.price_action import vsa
from app.screener import parse, vsa_gate
from app.screener import taxonomy as tx
from app.screener import vsa_scanners as vss
from app.screener.registry import FUNCTIONS, VSA_FLAG, VSA_FUNCTIONS, VSA_KWARGS
from app.screener.runtime import SymbolData, evaluate, run_screen
from app.screener.validator import validate
from app.signals.language import STRICT_WORDS
from app.strategy_templates import compile as C
from app.strategy_templates import engine, language, library
from app.strategy_templates.routes import FAMILY_FLAGS, FAMILY_NAMES


# --------------------------------------------------------------------------------------------- shape builder
class Bars:
    """Bars appended in order: (open, high, low, close, volume); the background bars have a range of about 2 and 1,000
    volume, so a bar's spread and volume read against them."""

    def __init__(self) -> None:
        self.rows: list[tuple[float, float, float, float, float]] = []

    @property
    def c(self) -> float:
        return self.rows[-1][3] if self.rows else 100.0

    def bar(self, o: float, h: float, lo: float, c: float, v: float = 1000.0) -> Bars:
        self.rows.append((o, h, lo, c, v))
        return self

    def rel(self, up: float, down: float, close: float, v: float = 1000.0) -> Bars:
        """A bar from the last close: high = close + up, low = close - down, close = last close + close."""
        c = self.c
        return self.bar(c, c + up, c - down, c + close, v)

    def side(self, n: int = 30) -> Bars:
        for k in range(n):
            c = 100.0 + (0.3 if k % 2 else -0.3)
            self.bar(self.c, c + 1, c - 1, c)
        return self

    def trend(self, n: int, step: float) -> Bars:
        for _ in range(n):
            c = self.c + step
            self.bar(self.c, max(self.c, c) + 0.8, min(self.c, c) - 0.8, c)
        return self

    def frame(self) -> pd.DataFrame:
        a = np.array(self.rows, float)
        idx = pd.date_range("2025-01-01", periods=len(a), freq="D", tz="UTC")
        return pd.DataFrame({"open": a[:, 0], "high": a[:, 1], "low": a[:, 2], "close": a[:, 3], "volume": a[:, 4]}, index=idx)


def fall() -> Bars:
    return Bars().side(30).trend(25, -0.5)


def rise() -> Bars:
    return Bars().side(30).trend(25, 0.5)


def hits(f: pd.DataFrame, name: str, **kw) -> list[int]:
    s = vsa.evaluate(f, vsa.settings_from(kw))
    return [int(i) for i in np.flatnonzero(s.hit[name])]


def _test_shape(volume: float) -> pd.DataFrame:
    """A rise with a high-volume bar six bars back, then a down bar whose low reaches that bar's range and closes high."""
    b = Bars().side(30).trend(19, 0.5).rel(1.3, 0.8, 0.5, 2000).trend(5, 0.5)
    area_high = b.rows[49][1]
    c = b.c
    return b.bar(c, c + 0.1, area_high - 0.2, c - 0.05, volume).frame()


def _shake(volume: float) -> pd.DataFrame:
    """A wide down bar after a rise, then a quiet bar back inside its range that holds its low."""
    return rise().rel(0.3, 3.7, -3.5, 1500).rel(1.5, 0.1, 1.2, volume).frame()


# (name, positive frame, negative frame, the bar the positive one is read on: -1 = the last)
CASES = {
    "selling_climax": (lambda: fall().rel(0.5, 5.5, -2.0, 3000).frame(), lambda: fall().rel(0.5, 5.5, -5.4, 3000).frame()),
    "stopping_volume": (lambda: fall().rel(0.3, 1.5, -0.1, 3000).frame(), lambda: fall().rel(0.3, 1.5, -1.4, 3000).rel(0.2, 1, -0.5).frame()),
    "buying_climax": (lambda: rise().rel(5.5, 0.5, 2.0, 3000).frame(), lambda: rise().rel(5.5, 0.5, 5.4, 3000).frame()),
    "up_thrust": (lambda: rise().rel(3.5, 0.5, -0.3).frame(), lambda: rise().rel(3.5, 0.5, 3.3).frame()),
    "reverse_up_thrust": (lambda: fall().rel(0.5, 3.5, 0.3, 2000).frame(), lambda: fall().rel(0.5, 3.5, 0.3, 1000).frame()),
    "absorption": (lambda: Bars().side(55).rel(3.5, 0.3, 3.3, 2000).frame(), lambda: Bars().side(55).rel(3.5, 0.3, 3.3, 1000).frame()),
    "shake_out": (lambda: _shake(500), lambda: _shake(2000)),
    "falling_pressure": (lambda: fall().rel(0.3, 3.7, -3.5, 500).frame(), lambda: fall().rel(0.3, 3.7, -3.5, 1500).frame()),
    "no_demand": (lambda: fall().rel(0.6, 0.2, 0.3, 500).frame(), lambda: fall().rel(0.6, 0.2, 0.3, 1200).frame()),
    "no_supply": (lambda: rise().rel(0.2, 1.3, -0.3, 500).frame(), lambda: rise().rel(0.2, 1.3, -1.25, 500).frame()),
    "test": (lambda: _test_shape(500), lambda: _test_shape(2600)),
    "failed_test": (lambda: _test_shape(2600), lambda: _test_shape(500)),
    "effort_vs_result": (lambda: rise().rel(0.6, 0.2, 0.3, 2000).frame(), lambda: rise().rel(0.6, 0.2, 0.3, 1000).frame()),
}


@pytest.mark.parametrize("name", vsa.DETECTORS)
def test_each_reading_on_its_shape_and_not_on_the_near_miss(name):
    pos, neg = CASES[name]
    f = pos()
    assert hits(f, name) == [len(f) - 1], name
    g = neg()
    assert len(g) - 1 not in hits(g, name) and len(g) - 2 not in hits(g, name), name


def test_stopping_volume_closing_low_is_read_on_the_next_bar_when_it_closes_level_or_higher():
    up = fall().rel(0.3, 1.5, -1.4, 3000).rel(1, 0.5, 0.5).frame()
    s = vsa.evaluate(up)
    assert list(np.flatnonzero(s.hit["stopping_volume"])) == [56] and s.origin["stopping_volume"][56] == 55
    assert "next bar" in s.variant["stopping_volume"][56]
    assert hits(fall().rel(0.3, 1.5, -1.4, 3000).frame(), "stopping_volume") == []        # not known before the next bar


def test_the_sides_are_descriptions_and_effort_vs_result_takes_the_bars_direction():
    f = rise().rel(0.6, 0.2, 0.3, 2000).frame()
    s = vsa.evaluate(f)
    assert s.side["effort_vs_result"][-1] == vsa.WEAKNESS and s.variant["effort_vs_result"][-1] == "effort without result"
    assert s.value("effort_vs_result", vsa.STRENGTH)[-1] == 0.0 and s.value("effort_vs_result", vsa.WEAKNESS)[-1] == 1.0
    g = fall().rel(0.2, 0.6, -0.3, 2000).frame()
    assert vsa.evaluate(g).side["effort_vs_result"][-1] == vsa.STRENGTH


def test_the_background_switch():
    # a climax shape with no fall before it: read only with the background off
    f = Bars().side(55).rel(0.5, 5.5, -2.0, 3000).frame()
    assert hits(f, "selling_climax") == [] and hits(f, "selling_climax", background=False) == [55]
    # no demand in a rise (strong structure): only with the background off
    g = rise().rel(0.6, 0.2, 0.3, 500).frame()
    assert hits(g, "no_demand") == [] and hits(g, "no_demand", background=False) == [55]
    # a recent climax at the end of a rise makes a weak background for no demand inside the rise
    h = rise().rel(5.5, 0.5, 2.0, 3000).rel(0.6, 0.6, -0.4).rel(0.6, 0.2, 0.3, 300).frame()
    s = vsa.evaluate(h)
    assert s.weak_bg[57] and 57 in hits(h, "no_demand")


def test_next_bar_confirmation_shows_the_reading_on_the_next_bar_only_when_it_closes_the_readings_way():
    yes = fall().rel(0.5, 5.5, -2.0, 3000).rel(1.2, 0.3, 0.8).frame()
    no = fall().rel(0.5, 5.5, -2.0, 3000).rel(0.3, 1.2, -0.8).frame()
    assert hits(yes, "selling_climax", confirm=True) == [56] and hits(no, "selling_climax", confirm=True) == []
    s = vsa.evaluate(yes, vsa.settings_from({"confirm": True}))
    assert s.origin["selling_climax"][56] == 55 and "confirmed by the next close" in vsa.why(s, "selling_climax", 56)["text"]
    nd_yes = fall().rel(0.6, 0.2, 0.3, 500).rel(0.2, 0.8, -0.5).frame()
    assert hits(nd_yes, "no_demand", confirm=True) == [56]


# --------------------------------------------------------------------------------------------- no look-ahead
def _random(seed: int, n: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0, 1, n))
    o = c + rng.normal(0, 0.3, n)
    h = np.maximum(o, c) + rng.random(n) * rng.choice([1, 1, 1, 4], n)
    lo = np.minimum(o, c) - rng.random(n) * rng.choice([1, 1, 1, 4], n)
    v = rng.integers(300, 5000, n).astype(float)
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": v},
                        index=pd.date_range("2025-01-01", periods=n, freq="D", tz="UTC"))


@pytest.mark.parametrize("seed", [1, 7, 42])
@pytest.mark.parametrize("cfg", [{}, {"background": False}, {"confirm": True}, {"avg_n": 10, "trend_bars": 5, "shake_bars": 3}])
def test_cut_bars_give_the_same_readings_as_the_full_run(seed, cfg):
    f = _random(seed)
    s = vsa.settings_from(cfg)
    full = vsa.evaluate(f, s)
    for cut in (60, 120, 200, len(f) - 1):
        part = vsa.evaluate(f.iloc[:cut + 1], s)
        for name in vsa.DETECTORS:
            a, b = full.value(name)[:cut + 1], part.value(name)
            assert np.array_equal(np.nan_to_num(a, nan=-1.0), np.nan_to_num(b, nan=-1.0)), (name, cut)


def test_the_warm_up_is_unknown_never_a_no():
    s = vsa.evaluate(_random(3))
    w = vsa.VsaSettings().warmup()
    for name in vsa.DETECTORS:
        assert np.isnan(s.value(name)[: w - 1]).all() and not np.isnan(s.value(name)[w:]).all()


# --------------------------------------------------------------------------------------------- no volume data
def test_no_volume_data_is_unknown_never_a_guess():
    f = _random(5).assign(volume=0.0)
    s = vsa.evaluate(f)
    assert not s.has_volume
    for name in vsa.DETECTORS:
        assert np.isnan(s.value(name)).all() and not s.hit[name].any()
    assert vsa_layer.marks_of(f, vsa.VsaSettings()) == {"available": False, "reason": vsa.NO_VOLUME, "marks": [], "bars": len(f)}
    g = _random(5)
    g.iloc[150, g.columns.get_loc("volume")] = np.nan                      # one bar without volume data
    s2 = vsa.evaluate(g)
    assert all(np.isnan(s2.value(name)[150]) for name in vsa.DETECTORS)
    assert all(np.isnan(s2.value(name)[160]) for name in vsa.DETECTORS)  # its window's average cannot be read either


def test_an_index_without_its_futures_volume_is_not_checked():
    sc = vss.scanner("vsa.no_demand")
    ast = parse(sc.text)
    validated = validate(ast, base_tf="1d", params=sc.defaults())
    f = _random(9)
    got = run_screen(ast, validated, [SymbolData("NIFTY 50", {"1d": f.assign(volume=0.0)}), SymbolData("ABC", {"1d": f})],
                     base_tf="1d", params=sc.defaults())
    assert got[0].matched is False and "volume not available" in str(got[0].reason)
    assert got[1].reason is None


# --------------------------------------------------------------------------------------------- settings, registry, ScreenQL
def test_settings_reference_values_bounds_and_kinds():
    d = vsa.VsaSettings()
    assert {r["name"]: r["value"] for r in vsa.reference_settings()} == {name: ref for name, _, ref, _ in vsa.SETTINGS_TABLE}
    assert all(r["label"] == "reference - edit before use" for r in vsa.reference_settings())
    assert d.background is True and d.confirm is False and d.prev_bars == 2
    for bad in ({"prev_bars": 0}, {"area_tol": -1}, {"background": "yes"}, {"shake_bars": 2.5}, {"nope": 1},
                {"prev_bars": 5, "avg_n": 4}, {"narrow": 1.5}):
        with pytest.raises(ValueError):
            vsa.settings_from(bad)


def test_the_registry_mirrors_the_settings():
    spec = FUNCTIONS["VSA"]
    assert set(VSA_FUNCTIONS) == {"VSA"} and spec.flag == VSA_FLAG
    assert set(spec.args[0].choices) == set(vsa.DETECTORS) and set(spec.args[1].choices) == set(vsa.SIDE_CHOICES)
    table = {name: (kind, ref, bounds) for name, kind, ref, bounds in vsa.SETTINGS_TABLE}
    assert set(VSA_KWARGS.values()) == set(table)
    for arg in spec.kwargs:
        kind, ref, bounds = table[VSA_KWARGS[arg.name]]
        assert arg.default == ref and not arg.required
        assert arg.type == {"bars": "window", "flag": "bool", "num": "num"}[kind]


def test_screenql_reads_the_one_evaluation():
    f = fall().rel(0.5, 5.5, -2.0, 3000).frame()
    d = SymbolData("S", {"1d": f})
    got = evaluate(parse('VSA("selling_climax")'), d, base_tf="1d")
    assert got.iloc[-1] == 1.0 and got.iloc[-2] == 0.0 and np.isnan(got.iloc[0])
    assert evaluate(parse('VSA("selling_climax", "weakness")'), d, base_tf="1d").iloc[-1] == 0.0
    assert evaluate(parse('VSA("selling_climax", background=FALSE, vol_ultra=4)'), d, base_tf="1d").iloc[-1] == 0.0
    assert bool(evaluate(parse('VSA("selling_climax") AND close < open'), d, base_tf="1d").iloc[-1])
    assert np.array_equal(np.nan_to_num(got.to_numpy(), nan=-1), np.nan_to_num(vsa.evaluate(f).value("selling_climax"), nan=-1))


# --------------------------------------------------------------------------------------------- the scanners
def test_every_scanner_validates_with_its_reference_values_and_matches_its_shape():
    assert [s.id for s in vss.SCANNERS] == [f"vsa.{n}" for n in vsa.DETECTORS]
    for s in vss.SCANNERS:
        name = s.id.split(".", 1)[1]
        assert all(p.ref and p.value is not None for p in s.params) and s.unset() == []
        validated = validate(parse(s.text), base_tf="1d", params=s.defaults())
        assert validated.ok, (s.id, [p.message for p in validated.problems])
        f = CASES[name][0]()
        got = evaluate(parse(s.text), SymbolData("S", {"1d": f}), base_tf="1d", params=s.defaults())
        assert got.iloc[-1] == 1.0, s.id
    assert vss.payload()["chip"] == "Volume spread"


def test_the_scanners_are_filed_in_the_taxonomy_behind_the_flag():
    tax = tx.load()
    filed = {s.id: s for s in tax.screens if s.source == "vsa"}
    assert set(filed) == set(vss.BY_ID)
    assert all(s.method == "volume_flow" for s in filed.values())
    assert filed["vsa.no_demand"].tags["direction"] == "short" and filed["vsa.test"].tags["direction"] == "long"
    assert tx.sources()["vsa"].flag == VSA_FLAG and tx.lint() == []


# --------------------------------------------------------------------------------------------- the chart layer
def test_the_chart_marks_are_the_scanners_matches_on_bars_cut_at_each_mark():
    f = _random(11, 220)
    got = vsa_layer.marks_of(f, vsa.VsaSettings())
    assert got["available"] and got["marks"]
    for m in got["marks"][-12:]:
        i = int(m["bar"])
        cut = f.iloc[:i + 1]
        value = evaluate(parse(f'VSA("{m["name"]}")'), SymbolData("S", {"1d": cut}), base_tf="1d").iloc[-1]
        assert value == 1.0, m["name"]


def test_the_why_text_is_structure_words_with_no_trade_wording_and_no_price():
    f = _random(13, 220)
    marks = vsa_layer.marks_of(f, vsa.VsaSettings())["marks"]
    words = {w.lower() for m in marks for w in pd.Series(m["text"]).str.findall(r"[A-Za-z]+")[0]}
    assert not words & set(STRICT_WORDS) - {"long", "short", "price", "order", "should", "must", "sure"}
    assert not words & {"buy", "buying", "sell", "selling", "target", "stop", "stoploss", "sl"}
    for m in marks:
        assert f'{f["close"].iloc[m["bar"]]:.2f}' not in m["text"]
        assert m["short"] in vsa.SHORT.values() and m["side"] in vsa.SIDES


# --------------------------------------------------------------------------------------------- the J1 template
def _run_j1(f: pd.DataFrame, tf: str = "1d", **params):
    t = library.get("J1")
    return engine.compute(C.compile_recipe(t, C.resolve_settings(t, tf, None, params)), {tf: f})


def test_j1_compiles_on_every_timeframe_and_its_text_passes_the_guard():
    t = library.get("J1")
    assert t.family == "J" and FAMILY_NAMES["J"] == "Volume spread" and FAMILY_FLAGS["J"] == VSA_FLAG and t.reference_values
    assert C.validate_template(t) == []
    for tf in t.allowed_timeframes:
        recipe = C.compile_recipe(t, C.resolve_settings(t, tf))
        assert VSA_FLAG in recipe.required_flags
        for side in ("long", "short"):
            text = library.signal_text(t, C.resolve_settings(t, tf), side)
            assert text and language.guard(text) == [], text
    for token in ("climax_fall_then_test", "climax_rise_then_no_demand"):
        assert language.guard(language.TOKENS[token]) == []


def test_j1_long_after_a_climax_at_the_end_of_a_fall_then_a_test_and_short_after_a_climax_at_the_end_of_a_rise():
    b = Bars().side(100).trend(25, -0.5).rel(0.5, 5.5, -2.0, 3000)          # the climax (also the high-volume area)
    sc_high, sc_low = b.rows[-1][1], b.rows[-1][2]
    b.rel(1.0, 0.5, 1.0).rel(1.0, 0.5, 1.0).rel(1.0, 0.5, 1.0)
    c = b.c
    b.bar(c, c + 0.1, sc_high - 0.3, c - 0.05, 300)                        # the test into the climax area
    f = b.frame()
    s = _run_j1(f)
    last = len(f) - 1
    assert {int(t): int(s.direction[t]) for t in s.signal_bars()} == {last: 1}
    assert s.invalidation[last] == pytest.approx(sc_low)                   # the extreme since the climax
    assert len(_run_j1(f.assign(volume=0.0)).signal_bars()) == 0           # no volume data: no signal, never a guess
    g = Bars().side(100).trend(25, 0.5).rel(5.5, 0.5, 2.0, 3000).rel(0.6, 0.6, -0.4).rel(0.6, 0.2, 0.3, 300).frame()
    s2 = _run_j1(g)
    assert {int(t): int(s2.direction[t]) for t in s2.signal_bars()} == {len(g) - 1: -1}


# --------------------------------------------------------------------------------------------- the flag
def test_the_flag_is_registered_off_with_catalog_state_and_pack_entries():
    assert VSA_FLAG == "vsa_pack" == vsa_gate.FLAG == vsa_layer.FLAG == vss.FLAG
    assert VSA_FLAG in controls.FEATURE_FLAGS and VSA_FLAG in controls.DEFAULT_OFF_FLAGS
    assert "places no orders" in controls.FEATURE_FLAGS[VSA_FLAG]
    assert flag_catalog.FLAG_GROUP[VSA_FLAG] == "screen_library" and VSA_FLAG in flag_state.FLAG_STATUS
    assert VSA_FLAG in trader_pack.EXCLUDED_REASONS
    ast = parse('VSA("test") AND close > open')
    assert vsa_gate.flagged_calls(ast) == ["VSA"]
    assert VSA_FLAG in vsa_gate.refusal(["VSA"]) and "bar close only" in vsa_gate.intrabar_refusal(["VSA"])


def _org(tag, on):
    from tests.test_n1_e1_execution_core import _flag
    from tests.test_trading_worker import _tenant
    t = _tenant(f"m99p3-{tag}-{uuid.uuid4().hex[:6]}@example.com", with_credentials=False)
    for name in ("screener_v2", "screener_intrabar", "screener_gallery") + ((VSA_FLAG,) if on else ()):
        _flag(name, t["tenant_id"])
    return t


def test_flag_off_hides_and_refuses_the_pack():
    from tests.test_auth_api import client
    off, on = _org("off", False), _org("on", True)
    source = 'VSA("no_demand")'
    entries_off = client.get("/api/screener/registry", headers=off["headers"]).json()["entries"]
    entries_on = client.get("/api/screener/registry", headers=on["headers"]).json()["entries"]
    assert "VSA" not in entries_off and "VSA" in entries_on
    refused = client.post("/api/screener/validate", headers=off["headers"], json={"source": source, "base_tf": "1d"}).json()
    assert not refused["ok"] and VSA_FLAG in json.dumps(refused["problems"])
    assert client.post("/api/screener/validate", headers=on["headers"], json={"source": source, "base_tf": "1d"}).json()["ok"]
    rule = {"name": "vsa", "kind": "instrument", "symbol": "ABC", "condition": source, "base_tf": "1d"}
    r = client.post("/api/alerts/rules", headers=off["headers"], json=rule)
    assert r.status_code == 422 and VSA_FLAG in r.text
    r = client.post("/api/alerts/rules", headers=on["headers"], json={**rule, "fire_on": "intrabar"})
    assert r.status_code == 422 and "bar close only" in r.text
    assert client.get("/api/screener/vsa", headers=off["headers"]).status_code in (403, 404, 503)
    lib = client.get("/api/screener/vsa", headers=on["headers"])
    assert lib.status_code == 200 and len(lib.json()["scanners"]) == len(vsa.DETECTORS)
    layer = client.get("/api/charts/layers/vsa", headers=off["headers"], params={"symbol": "ABC"})
    assert layer.status_code in (403, 404, 503)


def test_the_history_a_call_asks_for_covers_the_warm_up_of_any_settings():
    rng = np.random.default_rng(0)
    windows = [name for name, kind, *_ in vsa.SETTINGS_TABLE if kind == "bars"]
    inv = {v: k for k, v in VSA_KWARGS.items()}
    bounds = {name: b for name, _, _, b in vsa.SETTINGS_TABLE}
    for _ in range(200):
        values = {name: int(rng.integers(int(bounds[name][0]), int(min(bounds[name][1], 120)) + 1)) for name in windows}
        values["prev_bars"] = min(values["prev_bars"], values["avg_n"])
        s = vsa.settings_from(values)
        text = 'VSA("test", ' + ", ".join(f"{inv[k]}={v}" for k, v in values.items()) + ")"
        need = validate(parse(text), base_tf="1d").lookback["1d"]
        assert need >= s.warmup(), (values, need, s.warmup())
