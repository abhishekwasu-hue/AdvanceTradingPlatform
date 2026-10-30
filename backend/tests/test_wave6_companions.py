"""Wave templates (family G): the companions - the volume, zone-edge and divergence confirmations of G1-G11 (owner order
ATP_SESSION_WAVE_TEMPLATES.md section 2 "optional filters" and "invalidation"; review WAVE_TEMPLATES_REVIEW.md, the RSI
divergence `kind`, WaveVolumeLower and the zone-edge companion; open questions WAVE-14, WAVE-15, WAVE-27, WAVE-29, WAVE-33).

* Version 2 of every G template adds them as optional, user-switchable settings, all off by default; version 1 is kept word
  for word in `templates/archive/` and an id adopted on version 1 keeps exactly its behaviour. With the companions off,
  version 2 compiles to the same parts and gives the same signals as version 1 (parity).
* The new ScreenQL pieces: RsiDivergence / RsiDivergenceRef `kind=` (regular when left out: byte-identical; hidden),
  WaveVolumeLower (a comparison of two waves' mean volume, no number) and SwingZoneEdge (the far side of the area).
* Must-fire / must-not per companion on the pinned synthetic wave paths of tests/test_wave4_templates.py and
  tests/test_wave5_templates.py, mirror, truncation, parity (scan = backtest = options backtest = worker), the strategy id
  within its length, the flags, and no number literal in any expression. Public synthetic tests only; signals only, PAPER only.
"""
from __future__ import annotations

import hashlib
import re

import numpy as np
import pandas as pd
import pytest

from app.price_action.waves.levels import cached_wave_levels
from app.quality.lookahead import synthetic_bars
from app.screener import nodes as n
from app.screener.parser import parse
from app.screener.registry import FUNCTIONS, WAVE_FUNCTIONS
from app.screener.runtime import SymbolData, _rsi, _swing_pivots, evaluate
from app.strategy_templates import compile as C
from app.strategy_templates import engine, ids, library, wave_parity
from app.strategy_templates.adapter import TemplateStrategy
from tests import test_wave4_templates as w4
from tests import test_wave5_templates as w5
from tests.test_wave2_counter import _mirror_bars
from tests.test_wave4_templates import LEAD, PIVOT, RISK, ev, run, signals

# REV-S1: these fixtures run around the clock or hold a trade overnight to reach the exit under test; on the 24x7 venue the
# backtest has no INTRADAY cut-off / square-off (the NSE session rules are tested in tests/test_rev1_s1_s2_backtest_intraday.py).
VENUE_24X7 = "BTCINR"

G_IDS = ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "G10", "G11")
# version 1 of each G template as merged before the companions (main, #396)
V1_SHA256 = {"G1": "22ac657cd82b83a11d592471c9f14f103021abc0161cdca1756085b2082a6826",
             "G2": "96bd027c2d7130fd4b1ccfd6bb923d044213729f35617651a9ee53a4121b44bb",
             "G3": "1056b4bcf6f13ba232e7dd5a537799acfaa59e451ae1e3d70c82e91f8af02d33",
             "G4": "333926c7e4b72946eda6f19618dcbfd10b628dfe525416fbb2da3e0b279b4c02",
             "G5": "1eb8b5e7c94c55dad80db474680586c68fd8c473410440b0b55bc3dceceb3d5c",
             "G6": "d30405391519a3260fac1a30068fff4e2e57125b1055307c1e97c1bf8a6bd991",
             "G7": "f45cbf87e715ef7d18fe367b45ee8b61c3e06fcdba6942766dc4fdd2b34e8e74",
             "G8": "2efa4ded041b827d5dc7f1af839f645f7c442d1ee15330c8ec560cc01499dc30",
             "G9": "d1af7457bf046075b340b5d6d2412cc4bc8d2e7b2c092090957afa6a4889ffc2",
             "G10": "ac8eb91ae32d6acbad1b1e25bd047be00f0fd15d5b33b0b3ec22dc8c986a46f8",
             "G11": "aa7d9e74d8d1b9692b42b57b534539140fdee3ea84a3f634220cd6908ba2ac4c"}
COMPANION_KEYS = ("divergence", "div_degree", "div_bars", "rsi_period", "volume", "vol_mult")
AREA_TEMPLATES = ("G1", "G2", "G3", "G8")              # the templates that read an area: the zone-edge invalidation
# every pinned fixture of the earlier G tests: (key, style) -> (template, bars, settings, signal bar, side)
CASES = [("w4", k, s) for k, s in w4.CASES] + [("w5", k, s) for k, s in w5.CASES]


def fixture(mod, key, style):
    return (w4 if mod == "w4" else w5).fixture(key, style)


def ids_of(case):
    return f"{case[1]}-{case[2]}"


def same_series(a, b):
    np.testing.assert_array_equal(a.direction, b.direction)
    np.testing.assert_allclose(a.invalidation, b.invalidation, equal_nan=True)
    np.testing.assert_array_equal(a.exit_long, b.exit_long)
    np.testing.assert_array_equal(a.exit_short, b.exit_short)
    assert list(a.reason) == list(b.reason) and a.warmup_bars == b.warmup_bars
    assert [a.instance_id(t) for t in a.signal_bars()] == [b.instance_id(t) for t in b.signal_bars()]


def v1(tid):
    return library.get_version(tid, 1)


def v1_settings(tid, settings):
    """The same choices on version 1 (the companions left out)."""
    old = v1(tid)
    keys = {p.key for p in old.params}
    return C.resolve_settings(old, settings.timeframe, settings.sides, {k: v for k, v in settings.params.items() if k in keys})


# ======================================================================================== version 1 kept, version 2 parity
def test_version_one_of_each_g_template_is_kept_word_for_word():
    for tid, digest in V1_SHA256.items():
        path = library.ARCHIVE_DIR / f"{tid}.v1.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, tid
        old, new = v1(tid), library.get(tid)
        assert old.version == 1 and new.version == 2 and old.confirmations == () and C.validate_template(old) == []
        assert [p.key for p in new.params][:len(old.params)] == [p.key for p in old.params]        # the new settings at the end
        assert [p.key for p in new.params][len(old.params):] == list(COMPANION_KEYS)
        assert [c.by for c in new.confirmations] == ["divergence", "volume"]
    assert [(t.id, t.version) for t in library.archived() if t.family == "G"] == sorted((tid, 1) for tid in G_IDS)
    assert {t.id: t.version for t in library.templates() if t.family == "G"} == {tid: 2 for tid in G_IDS}   # the newest only


def test_the_companions_are_off_by_default_and_every_setting_is_bounded():
    for tid in G_IDS:
        t = library.get(tid)
        d = {p.key: p.default for p in t.params}
        assert (d["divergence"], d["volume"], d["div_bars"], d["rsi_period"], d["vol_mult"]) == ("off", "off", 15, 14, 1.0), tid
        assert d["div_degree"] == (1 if tid in ("G9", "G10") else 0), tid      # the fifth wave against the third: the waves
        assert t.param("divergence").choices == ("off", "regular", "hidden")
        assert t.param("volume").choices == ("off", "wave", "trigger", "both")
        for c in t.confirmations:
            assert c.variants[str(d[c.by])] is None                                # off by default (the schema refuses otherwise)
        for p in t.params:
            if p.type in ("int", "float"):
                assert p.min is not None and p.max is not None and p.min <= p.default <= p.max, (tid, p.key)
        inval = t.param("invalidation")
        old = v1(tid).param("invalidation")
        assert inval.default == old.default and inval.choices[:len(old.choices)] == old.choices
        assert inval.choices[len(old.choices):] == (("area", "further") if tid in AREA_TEMPLATES else ())


@pytest.mark.parametrize("tid", G_IDS)
def test_with_the_companions_off_version_two_compiles_to_version_one(tid):
    """Every variant of every version 1 switch, with the companions off: the same parts (expression for expression), the
    same flags and the same lookback; the params hash differs only because the version is in the provenance."""
    old, new = v1(tid), library.get(tid)
    paths = [{}] + C._switch_variants(old)
    for tf in ("15m", "1h"):
        for params in paths:
            try:
                settings = C.resolve_settings(new, tf, params=params)
            except C.TemplateError:
                continue
            a, b = C.compile_recipe(old, v1_settings(tid, settings)), C.compile_recipe(new, settings)
            assert {k: p.text for k, p in a.parts.items()} == {k: p.text for k, p in b.parts.items()}, (tid, params)
            assert a.required_flags == b.required_flags and a.lookback == b.lookback and a.expiry_bars == b.expiry_bars
            assert a.params_hash != b.params_hash


@pytest.mark.parametrize("case", CASES, ids=ids_of)
def test_with_the_companions_off_the_signals_are_version_ones(case):
    template, bars, params, bar, side = fixture(*case)
    settings = C.resolve_settings(template, "15m", params=params)
    old = engine.compute(C.compile_recipe(v1(template.id), v1_settings(template.id, settings)), {"15m": bars})
    new = engine.compute(C.compile_recipe(template, settings), {"15m": bars})
    same_series(old, new)
    assert signals(new) == {bar: side}
    assert library.signal_text(template, settings, "long") == library.signal_text(v1(template.id), v1_settings(template.id, settings), "long")


@pytest.mark.parametrize("tid, seed", [("G1", 1), ("G3", 2), ("G8", 3), ("G4", 1), ("G9", 0), ("G11", 8)])
def test_version_one_and_two_agree_on_random_walk_bars(tid, seed):
    df = synthetic_bars(1200, tf="15m", seed=seed)
    params = {**(w5.base(tid) if tid in w5.TEMPLATES else w4.BASE[tid])}
    new = library.get(tid)
    settings = C.resolve_settings(new, "15m", params=params)
    same_series(engine.compute(C.compile_recipe(v1(tid), v1_settings(tid, settings)), {"15m": df}),
                engine.compute(C.compile_recipe(new, settings), {"15m": df}))


def test_a_version_one_id_keeps_resolving_to_exactly_its_behaviour():
    sid = "template:G1:1:15m:both:15m,1h,,,1,,,,,,,,,,,,,,"                       # 19 values: version 1's settings
    template, settings = ids.decode(sid)
    assert template.version == 1 and template is library.get_version("G1", 1) and ids.encode(template, settings) == sid
    bars = w4.bars_of(w4.G1_PATH, w4.G1_EDITS)
    old = engine.compute(C.compile_recipe(template, settings), {"15m": bars})
    new_t = library.get("G1")
    new = engine.compute(C.compile_recipe(new_t, C.resolve_settings(new_t, "15m", params=settings.params)), {"15m": bars})
    same_series(old, new)
    assert signals(old) == {366: 1}
    assert C.params_hash(template, settings) == C.params_hash(library.get_version("G1", 1), settings)


# ================================================================================================ the strategy id (80 chars)
def test_the_version_two_id_leaves_out_trailing_defaults_and_stays_within_its_length():
    for tid in G_IDS:
        t = library.get(tid)
        sid, settings = ids.adopt(t, "15m")
        assert sid == f"template:{tid}:2:15m:both:" and ids.decode(sid) == (t, settings)
        sid, settings = ids.adopt(t, "15m", params={"divergence": "hidden"})
        assert sid.endswith(",2") and ids.decode(sid) == (t, settings)
        assert sid.count(",") == [p.key for p in t.params].index("divergence")
        with pytest.raises(KeyError):                                     # canonical: the trailing defaults are never written
            ids.decode(sid + ",")
        with pytest.raises(KeyError):
            ids.decode(sid + ",,,,,,,,,,,,,,,,,,,,,,,,,,,,,,")
    heavy = {"wave_tf": "15m", "higher_tf": "1h", "entry_style": "aggressive", "area_source": "none", "fib_zone": "zone",
             "fib_from": 0.382, "fib_to": 0.618, "min_clarity": "agree", "window": "every", "invalidation": "last_swing",
             "exit_signal": "choch", "k": 0.35, "zone_width": 1.5, "expiry_bars": 120, "retest_bars": 25}
    for tid in ("G1", "G3", "G8"):                                      # the earlier heavy settings still fit
        t = library.get(tid)
        sid, settings = ids.adopt(t, "15m", "short", heavy)
        assert len(sid) <= ids.MAX_LENGTH and ids.decode(sid) == (t, settings)
    t = library.get("G2")                                                # the companions themselves fit
    sid, settings = ids.adopt(t, "15m", "short", {"wave_tf": "15m", "higher_tf": "1h", "divergence": "regular", "div_degree": 1,
                                                  "div_bars": 40, "rsi_period": 21, "volume": "both", "vol_mult": 1.5,
                                                  "invalidation": "further"})
    assert len(sid) <= ids.MAX_LENGTH and ids.decode(sid) == (t, settings)
    sid, _ = ids.adopt(library.get("D2"), "15m", params={"invalidation": "slow_average"})
    assert sid == "template:D2:1:15m:both:,,,,,slow_average,"            # every template without confirmations: unchanged


# ===================================================================================================== the schema
def test_the_schema_keeps_a_confirmation_off_by_default_and_needs_a_trigger():
    data = library.get("G1").model_dump(mode="json", by_alias=True)
    bad = {**data, "confirmations": [{**data["confirmations"][0], "variants": {**data["confirmations"][0]["variants"],
                                                                               "off": data["confirmations"][0]["variants"]["regular"]}}]}
    with pytest.raises(ValueError, match="off by default"):
        type(library.get("G1")).from_data(bad)
    with pytest.raises(ValueError, match="its own setting"):
        type(library.get("G1")).from_data({**data, "confirmations": [data["confirmations"][0]] * 2})
    with pytest.raises(ValueError, match="cover exactly the choices"):
        type(library.get("G1")).from_data({**data, "confirmations": [{"by": "volume", "variants": {"off": None}}]})


def test_a_chosen_confirmation_is_and_ed_to_the_chosen_trigger():
    t = library.get("G3")
    r = C.compile_recipe(t, C.resolve_settings(t, "15m", params={"volume": "trigger", "divergence": "hidden"}))
    plain = C.compile_recipe(t, C.resolve_settings(t, "15m"))
    for side in ("long", "short"):
        text = r.part("trigger", side).text
        assert text.startswith("(" + plain.part("trigger", side).text + ") AND (RsiDivergence(")
        assert text.endswith(' kind="hidden")@1h) AND (RelVolume() >= $vol_mult)')
    assert r.part("setup", "long").text == plain.part("setup", "long").text                   # only the trigger changes
    assert r.required_flags == plain.required_flags | {"screenql_amw_functions"}               # RsiDivergence's own flag


def test_no_number_literal_in_any_companion_expression():
    """Numbers live only in settings: the companions' expressions hold no number but the zero of `Count(...) > 0`."""
    for tid in G_IDS:
        t = library.get(tid)
        texts = [x for c in t.confirmations for v in c.variants.values() if v is not None for x in (v.long, v.short)]
        texts += [x for k in ("area", "further") if k in t.invalidation.variants
                  for x in (t.invalidation.variants[k].long, t.invalidation.variants[k].short)]
        for text in texts:
            ast = parse(text.replace("@{wave_tf}", "@1h"))
            seen_once = {id(c.right) for c in n.walk(ast) if isinstance(c, n.Compare) and c.op == ">" and isinstance(c.left, n.Call)
                         and c.left.name == "Count" and isinstance(c.right, n.Num) and c.right.value == 0}
            assert not [x for x in n.walk(ast) if isinstance(x, n.Num) and id(x) not in seen_once], (tid, text)
            assert not re.search(r"\d", re.sub(r"\) > 0", ")", re.sub(r'"[^"]*"', "", text))), (tid, text)


# ======================================================================================= RsiDivergence kind (regular / hidden)
def _oracle(df, side, kind, lookback, degree=0, period=14):
    """The divergence read independently: the last two confirmed swings of the side and the RSI at their extremes."""
    rsi = _rsi(df["close"].astype(float), period).to_numpy(float)
    k = "L" if side == "bullish" else "H"
    out = np.zeros(len(df), dtype=bool)
    swings = [p for p in _swing_pivots(df, degree) if p.kind == k]
    for a, b, nxt in zip(swings, swings[1:], swings[2:] + [None]):
        r0, r1 = rsi[int(a.bar_idx)], rsi[int(b.bar_idx)]
        if not (np.isfinite(r0) and np.isfinite(r1)):
            continue
        lower, higher = b.price < a.price, b.price > a.price
        if kind == "regular":
            hit = (lower and r1 > r0) if k == "L" else (higher and r1 < r0)
        else:
            hit = (higher and r1 < r0) if k == "L" else (lower and r1 > r0)
        if hit:
            stop = int(nxt.confirmed_idx) if nxt is not None else len(df)
            for t in range(int(b.confirmed_idx), stop):
                out[t] = t - int(b.bar_idx) <= lookback
    return out


@pytest.mark.parametrize("seed", [0, 3, 7])
def test_regular_is_the_divergence_as_before_and_hidden_reads_the_other_pair(seed):
    df = synthetic_bars(900, tf="15m", seed=seed)
    data = SymbolData("S", {"15m": df})
    for side in ("bullish", "bearish"):
        before = evaluate(parse(f'RsiDivergence("{side}", 30, 0)'), data, base_tf="15m")
        regular = evaluate(parse(f'RsiDivergence("{side}", 30, 0, kind="regular")'), data, base_tf="15m")
        hidden = evaluate(parse(f'RsiDivergence("{side}", 30, 0, kind="hidden")'), data, base_tf="15m")
        pd.testing.assert_series_equal(before, regular)                                 # left out = regular, byte-identical
        np.testing.assert_array_equal(regular.to_numpy(bool), _oracle(df, side, "regular", 30))
        np.testing.assert_array_equal(hidden.to_numpy(bool), _oracle(df, side, "hidden", 30))
        assert hidden.any() and not (hidden & regular).any()               # a pair of swings is one kind or the other
        ref_before = evaluate(parse(f'RsiDivergenceRef("extreme", "{side}", 0)'), data, base_tf="15m")
        ref_regular = evaluate(parse(f'RsiDivergenceRef("extreme", "{side}", 0, kind="regular")'), data, base_tf="15m")
        pd.testing.assert_series_equal(ref_before, ref_regular)
        ref_hidden = evaluate(parse(f'RsiDivergenceRef("extreme", "{side}", 0, kind="hidden")'), data, base_tf="15m")
        assert np.isfinite(ref_hidden.to_numpy(float)).any()
        lows = _swing_prices(df, "L" if side == "bullish" else "H")
        assert set(ref_hidden.dropna().round(9)) <= lows                    # the divergent swing's own extreme


def _swing_prices(df, kind):
    return {round(float(p.price), 9) for p in _swing_pivots(df, 0) if p.kind == kind}


def test_the_kind_is_validated_and_never_an_expression():
    from app.screener.validator import validate_study
    ok = validate_study(parse('RsiDivergence("bullish", 30, 0, kind="hidden")'), base_tf="15m")
    assert ok.validated.ok
    bad = validate_study(parse('RsiDivergence("bullish", 30, 0, kind="sideways")'), base_tf="15m")
    assert not bad.validated.ok
    assert [a.name for a in FUNCTIONS["RsiDivergence"].kwargs] == ["period", "kind"]
    assert [a.name for a in FUNCTIONS["RsiDivergenceRef"].kwargs] == ["period", "kind"]


# ============================================================================================== WaveVolumeLower (the volume)
def _g1_reading():
    bars = w4.bars_of(w4.G1_PATH, w4.G1_EDITS)
    levels = cached_wave_levels(bars, 1)
    r = levels.main[366]                                                  # wave three under way: waves one and two finished
    assert r.family == "impulse" and len(r.points) - 1 == 3
    return bars, r


def _vol(bars, spans):
    out = bars.copy()
    out["volume"] = 1000.0
    for (lo, hi), v in spans.items():
        out.iloc[lo:hi + 1, out.columns.get_loc("volume")] = v
    return out


def test_wave_volume_lower_compares_the_two_waves_mean_volume():
    bars, r = _g1_reading()
    one, two = (int(r.bars[0]) + 1, int(r.bars[1])), (int(r.bars[1]) + 1, int(r.bars[2]))
    fn = 'WaveVolumeLower(1, "2", "1")'
    assert ev(fn, _vol(bars, {one: 3000.0})).iloc[366]                   # wave one heavier: wave two is lower
    assert not ev(fn, _vol(bars, {two: 3000.0})).iloc[366]               # wave two heavier
    assert not ev(fn, _vol(bars, {})).iloc[366]                          # equal: not lower (a comparison, no tolerance)
    assert ev('WaveVolumeLower(1, "2")', _vol(bars, {one: 3000.0})).iloc[366]     # previous = the wave before it
    assert not ev('WaveVolumeLower(1, "1", "2")', _vol(bars, {one: 3000.0})).iloc[366]
    # the running wave (three) from the bar after its start to the bar before this one, against wave two
    assert ev('WaveVolumeLower(1, "current", "2")', _vol(bars, {two: 3000.0})).iloc[366]
    assert not ev('WaveVolumeLower(1, "current", "2")', _vol(bars, {(two[1] + 1, 365): 3000.0})).iloc[366]
    assert ev('WaveVolumeLower(1, "current", "2")', _vol(bars, {two: 3000.0, (366, 366): 1e9})).iloc[366]   # this bar is not counted
    # a wave the reading does not have (yet): false, never an error
    assert not ev('WaveVolumeLower(1, "4", "3")', _vol(bars, {one: 3000.0})).iloc[366]
    assert not ev('WaveVolumeLower(1, "B", "A")', _vol(bars, {one: 3000.0})).iloc[366]


def test_wave_volume_lower_is_false_without_volume():
    bars, r = _g1_reading()
    one = (int(r.bars[0]) + 1, int(r.bars[1]))
    index_like = _vol(bars, {one: 3000.0})
    index_like["volume"] = 0.0                                           # an index: no volume
    assert not ev('WaveVolumeLower(1, "2", "1")', index_like).any()
    gap = _vol(bars, {one: 3000.0})
    gap.iloc[int(r.bars[1]) + 2, gap.columns.get_loc("volume")] = np.nan     # one bar of wave two without volume
    assert not ev('WaveVolumeLower(1, "2", "1")', gap).iloc[366]
    assert not ev('WaveVolumeLower(1, "2", "1")', bars.drop(columns=["volume"])).any()


def test_wave_volume_lower_never_reads_ahead():
    df = synthetic_bars(1200, tf="15m", seed=5)
    full = ev('WaveVolumeLower(1, "current")', df)
    assert full.any() and not full.all()
    for t in range(LEAD, len(df), 97):
        np.testing.assert_array_equal(ev('WaveVolumeLower(1, "current")', df.iloc[:t + 1]).to_numpy(bool), full.iloc[:t + 1].to_numpy(bool))


def test_the_new_functions_carry_the_wave_flag_and_are_sparse_where_they_should_be():
    assert {"WaveVolumeLower", "SwingZoneEdge"} <= set(WAVE_FUNCTIONS)
    assert "SwingZoneEdge" in engine.SPARSE_SERIES and "WaveVolumeLower" not in engine.SPARSE_SERIES   # yes / no: never missing
    assert FUNCTIONS["RsiDivergence"].flag == "screenql_amw_functions"   # unchanged: a divergence keeps its own flag


# ============================================================================================ SwingZoneEdge (the area's far side)
@pytest.mark.parametrize("seed", [1, 4])
def test_the_zone_edge_is_the_far_side_of_the_band_the_area_reads(seed):
    df = synthetic_bars(700, tf="15m", seed=seed)
    for side in ("low", "high"):
        dist = ev(f'SwingZoneDistance("{side}", 0)', df)
        mid = ev(f'SwingZoneEdge("{side}", 0, 0)', df)
        for w in (0.5, 1.0, 2.5):
            edge = ev(f'SwingZoneEdge("{side}", 0, {w})', df)
            assert edge.isna().equals(dist.isna())
            gap = (edge - mid).abs()
            np.testing.assert_allclose(gap.dropna().to_numpy() / w, (gap / w).dropna().to_numpy())
            close = df["close"]
            inside = (dist >= -w + 0.002) if side == "low" else (dist <= w - 0.002)   # dist_mr is kept to 3 decimals
            beyond = (dist < -w - 0.002) if side == "low" else (dist > w + 0.002)
            above = close >= edge if side == "low" else close <= edge
            assert above[inside].all() and not above[beyond].any()
        # the zone's own mid: the close's distance from it, in the same median ranges
        assert ((df["close"] - mid)[dist.notna()] * dist[dist.notna()] >= -1e-9).all()


@pytest.mark.parametrize("case", [c for c in CASES if c[1] in AREA_TEMPLATES], ids=ids_of)
def test_the_area_and_the_further_invalidation(case):
    template, bars, params, bar, side = fixture(*case)
    default = run(template, bars, **params)
    edge = ev(f'SwingZoneEdge("{"low" if side > 0 else "high"}", 0, {params.get("zone_width", 1.0)})', bars).iloc[bar]
    area = run(template, bars, **params, invalidation="area")
    further = run(template, bars, **params, invalidation="further")
    assert signals(area) == signals(further) == {bar: side}
    assert area.invalidation[bar] == pytest.approx(edge)
    pick = min if side > 0 else max
    assert further.invalidation[bar] == pytest.approx(pick(default.invalidation[bar], edge))
    close = bars["close"].iloc[bar]
    assert (area.invalidation[bar] < close) if side > 0 else (area.invalidation[bar] > close)          # S6
    m = run(template, _mirror_bars(bars, PIVOT), **params, invalidation="area")
    assert signals(m) == {bar: -side} and PIVOT - m.invalidation[bar] == pytest.approx(area.invalidation[bar])


def test_only_the_area_templates_offer_the_zone_edge():
    for tid in G_IDS:
        texts = [x for v in library.get(tid).invalidation.variants.values() for x in (v.long, v.short)]
        assert any("SwingZoneEdge" in x for x in texts) == (tid in AREA_TEMPLATES), tid


# ======================================================================================= the volume confirmation per template
@pytest.mark.parametrize("case", CASES, ids=ids_of)
def test_volume_confirmation_must_fire_and_must_not(case):
    template, bars, params, bar, side = fixture(*case)
    falling = bars.copy()
    falling["volume"] = np.linspace(2e6, 1e6, len(bars))                 # every later wave on lower volume
    assert signals(run(template, falling, **params, volume="wave")) == {bar: side}
    assert signals(run(template, bars, **params, volume="wave")) == {}   # equal volume: never lower
    assert signals(run(template, bars, **params, volume="trigger")) == {bar: side}          # the average volume: x 1.0
    assert signals(run(template, bars, **params, volume="trigger", vol_mult=1.5)) == {}
    spike = falling.copy()
    spike.iloc[bar, spike.columns.get_loc("volume")] *= 2.5             # above twice its average, the wave still lower
    assert signals(run(template, spike, **params, volume="both", vol_mult=2.0)) == {bar: side}
    assert signals(run(template, falling, **params, volume="both", vol_mult=2.0)) == {}
    m = run(template, _mirror_bars(spike, PIVOT), **params, volume="both", vol_mult=2.0)
    assert signals(m) == {bar: -side}                                     # mirror: volume is the same on both sides


# =================================================================================== the divergence confirmation per template
# (module, fixture, style, settings) -> fires on the fixture's signal bar; the other kind does not
DIVERGENCE_FIRES = [
    ("w5", "G4", "breakout", {"divergence": "regular"}), ("w5", "G7", "reclaim", {"divergence": "regular"}),
    ("w5", "G9-choch", "choch", {"divergence": "regular"}), ("w5", "G10", "retest", {"divergence": "regular"}),
    ("w5", "G2", "breakout", {"divergence": "hidden", "div_degree": 1}),
    ("w4", "G3", "aggressive", {"divergence": "regular", "div_degree": 1, "div_bars": 60}),
    ("w4", "G8", "aggressive", {"divergence": "hidden", "div_degree": 1, "div_bars": 60}),
    ("w5", "G5", "breakout", {"divergence": "hidden", "div_degree": 1, "div_bars": 60}),
]
DIVERGENCE_NONE = [("w4", "G1", "choch"), ("w4", "G1", "retest"), ("w5", "G6", "breakout"), ("w5", "G11", "retest"),
                   ("w5", "G11-choch", "choch")]


@pytest.mark.parametrize("mod, key, style, extra", DIVERGENCE_FIRES, ids=[f"{c[1]}-{c[2]}" for c in DIVERGENCE_FIRES])
def test_divergence_confirmation_must_fire(mod, key, style, extra):
    template, bars, params, bar, side = fixture(mod, key, style)
    s = run(template, bars, **params, **extra)
    assert signals(s) == {bar: side}
    other = "hidden" if extra["divergence"] == "regular" else "regular"
    assert signals(run(template, bars, **params, **{**extra, "divergence": other})) == {}
    m = run(template, _mirror_bars(bars, PIVOT), **params, **extra)
    assert signals(m) == {bar: -side}
    deg = extra.get("div_degree", template.param("div_degree").default)
    kind = extra["divergence"]
    rsi_div = ev(f'RsiDivergence("{"bullish" if side > 0 else "bearish"}", {extra.get("div_bars", 15)}, {deg}, kind="{kind}")', bars)
    assert rsi_div.iloc[bar]                                              # the confirmation holds on the trigger bar


@pytest.mark.parametrize("mod, key, style", DIVERGENCE_NONE, ids=[f"{c[1]}-{c[2]}" for c in DIVERGENCE_NONE])
def test_divergence_confirmation_must_not_fire_without_one(mod, key, style):
    template, bars, params, bar, side = fixture(mod, key, style)
    for kind in ("regular", "hidden"):
        for deg in (0, 1):
            assert signals(run(template, bars, **params, divergence=kind, div_degree=deg)) == {}, (kind, deg)


@pytest.mark.parametrize("tid, style, seed", [("G1", "choch", 2), ("G3", "choch", 4), ("G4", "breakout", 1), ("G9", "choch", 0)])
def test_every_confirmed_signal_is_a_plain_trigger_with_the_confirmation_true(tid, style, seed):
    """Random-walk bars: with a companion on, each signal bar has the plain trigger and the companion true (an AND, never a
    new kind of signal), S6 holds, and nothing reads ahead (truncation)."""
    template = library.get(tid)
    params = {**(w5.base(tid) if tid in w5.TEMPLATES else w4.BASE[tid]), "entry_style": style}
    df = synthetic_bars(1500, tf="15m", seed=seed)
    for extra in ({"divergence": "regular"}, {"divergence": "hidden", "div_degree": 1}, {"volume": "both", "vol_mult": 0.8}):
        s = run(template, df, **params, **extra)
        r = w4.recipe(template, "15m", **params, **extra)
        plain = w4.recipe(template, "15m", **params)
        for t, d in signals(s).items():
            side = "long" if d > 0 else "short"
            for rr in (r, plain):
                part = rr.part("trigger", side)
                assert evaluate(part.ast, SymbolData("S", {"15m": df}), base_tf="15m", params=part.params).iloc[t], (extra, t)
            close = df["close"].iloc[t]
            assert (s.invalidation[t] < close) if d > 0 else (s.invalidation[t] > close)
        marks = sorted({b for x in s.signal_bars() for b in (x - 1, x, x + 1)} | set(range(s.warmup_bars, len(df), 211)))
        for t in [x for x in marks if x < len(df)]:
            part_s = run(template, df.iloc[:t + 1], **params, **extra)
            np.testing.assert_array_equal(part_s.direction, s.direction[:t + 1], err_msg=f"{extra} t={t}")


# ========================================================================================================= parity + flags
@pytest.mark.parametrize("mod, key, style, extra", [
    ("w5", "G4", "breakout", {"divergence": "regular", "volume": "trigger"}),
    ("w5", "G9-choch", "choch", {"divergence": "regular"}),
    ("w4", "G1", "retest", {"volume": "trigger", "invalidation": "further"}),
    ("w4", "G8", "breakout", {"invalidation": "area", "volume": "trigger"})])
def test_parity_with_the_companions_on(mod, key, style, extra):
    template, bars, params, bar, side = fixture(mod, key, style)
    strat = TemplateStrategy(template, C.resolve_settings(template, "15m", params={**params, **extra}))
    report = wave_parity.run(strat, bars, VENUE_24X7, RISK, worker_bars=sorted({bar - 1, bar, bar + 1, LEAD, len(bars) - 1}))
    assert report.problems == [] and report.signals == [bars.index[bar]] and report.trades == 1


def test_the_companions_need_no_new_flag():
    """The same gates as before: the G templates are listed with strategy_templates + screener_waves; a strategy runs with
    the flags of the functions it reads (the divergence: RsiDivergence's own, as E2; the volume and the zone edge: the
    flags G already reads)."""
    from app.platform import controls
    t = library.get("G1")
    base = C.compile_recipe(t, C.resolve_settings(t, "15m")).required_flags
    for extra, more in (({"volume": "both"}, set()), ({"invalidation": "further"}, set()), ({"divergence": "hidden"}, {"screenql_amw_functions"})):
        assert C.compile_recipe(t, C.resolve_settings(t, "15m", params=extra)).required_flags == base | more, extra
    assert {"strategy_templates", "screener_waves", "screenql_amw_functions"} <= set(controls.FEATURE_FLAGS)
    assert {"strategy_templates", "screener_waves"} <= set(controls.DEFAULT_OFF_FLAGS)
