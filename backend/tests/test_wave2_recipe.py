"""Wave templates (family G) step 2: the recipe extensions (docs/design/WAVE_TEMPLATES_REVIEW.md WB3, WB4, WB7, WB13).

* WB3 - family G, `variants` per part keyed by an enum setting (only the chosen one is compiled and counts for the
  warm-up), `@{timeframe_setting}` placeholders validated like a written timeframe; D1 stays byte-identical.
* WB4 - the wave level series are sparse: they never hold the warm-up back.
* WB7 - wave labels in words, the wave / higher timeframe placeholders, the guard over every token.
* WB13 - the wave ScreenQL functions behind the new flag `screener_waves` (OFF): hidden from the registry, refused in
  screens and alert rules while off, bar close only in alerts; the runtime equals the counter.
No G template is built here: the templates below exist only in the tests."""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.platform import controls
from app.platform.flag_catalog import FLAG_INFO
from app.price_action.waves import wave_series
from app.quality.lookahead import synthetic_bars
from app.screener import compile_screen
from app.screener import waves as waves_gate
from app.screener.parser import parse
from app.screener.registry import FUNCTIONS, WAVE_FUNCTIONS, WAVES_FLAG
from app.screener.runtime import SymbolData, evaluate
from app.strategy_templates import compile as C
from app.strategy_templates import engine, language, library
from app.strategy_templates.schema import Template
from tests.test_tpl1_recipe_core import bars, signals

G = {
    "schema": "atp.template/1", "id": "G99", "version": 1, "name": "Wave test recipe", "family": "G",
    "idea": "A recipe for the tests of the family G extensions.", "difficulty": "Intermediate", "style": "Both",
    "allowed_timeframes": ["15m", "1h"],
    "params": [
        {"key": "entry_style", "label": "Entry style", "help": "How the trigger reads.", "type": "enum", "default": "aggressive",
         "choices": ["aggressive", "breakout"]},
        {"key": "window", "label": "Window", "help": "One opportunity per wave or every pullback.", "type": "enum", "default": "first",
         "choices": ["first", "every"]},
        {"key": "trend_relation", "label": "Trend relation", "help": "With the higher trend or any.", "type": "enum", "default": "any",
         "choices": ["with_higher_trend", "any"]},
        {"key": "wave_tf", "label": "Wave timeframe", "help": "Where the waves are counted.", "type": "timeframe", "default": "1h",
         "choices": ["15m", "1h", "1d"]},
        {"key": "higher_tf", "label": "Higher timeframe", "help": "The main trend.", "type": "timeframe", "default": "1d",
         "choices": ["1h", "1d", "1w"]},
        {"key": "expiry", "label": "Expiry", "help": "Bars after the setup.", "type": "int", "default": 5, "min": 0, "max": 50, "step": 1},
    ],
    "constraints": [
        {"left": "wave_tf", "op": ">=", "right": "signal_timeframe", "message": "The wave timeframe must not be finer than the signal timeframe."},
        {"left": "higher_tf", "op": ">=", "right": "wave_tf", "message": "The higher timeframe must not be finer than the wave timeframe."},
    ],
    "context": {"by": "trend_relation", "variants": {
        "with_higher_trend": {"long": "close@{higher_tf} > open@{higher_tf}", "short": "close@{higher_tf} < open@{higher_tf}"},
        "any": None}},
    "setup": {"by": "window", "variants": {
        "first": {"kind": "state", "long": "oi == 1", "short": "oi == 2", "expiry_bars": "$expiry"},
        "every": {"kind": "event", "long": "oi == 1", "short": "oi == 2", "expiry_bars": "$expiry", "same_bar": True}}},
    "trigger": {"by": "entry_style", "variants": {
        "aggressive": {"long": "volume == 1", "short": "volume == 2"},
        "breakout": {"long": "volume == 1 AND close > Max(high, 120)[1]", "short": "volume == 2 AND close < Min(low, 120)[1]"}}},
    "invalidation": {"long": "low", "short": "high"},
    "signal_text": {"long": [{"token": "wave_two_pullback_ended"}, {"token": "minor_trend_resumed"}],
                    "short": [{"token": "wave_b_pullback_ended"}, {"token": "counter_trend_c_leg"}]},
}
D1_SHA256 = "b1446b18bc17fc9abcfa7f45b9890d013188533c9ce74bb9877fd430d5177253"     # templates/D1.json on main before family G


def g(**changes) -> Template:
    body = copy.deepcopy(G)
    for key, value in changes.items():
        body[key] = value
    return Template.from_data(body)


def compiled(template, tf="15m", **params):
    return C.compile_recipe(template, C.resolve_settings(template, tf, "both", params))


# --- WB3: schema -----------------------------------------------------------------------------------------------------------

def test_family_g_is_accepted_and_other_families_are_not():
    assert g().family == "G" and g(id="G1").id == "G1"
    for change in ({"family": "H"}, {"id": "H1"}, {"id": "G100"}):
        with pytest.raises(ValidationError):
            g(**change)


@pytest.mark.parametrize("part, value, fragment", [
    ("trigger", {"by": "entry_style", "variants": {"aggressive": {"long": "close > 0", "short": "close > 0"}}}, "cover exactly"),
    ("trigger", {"by": "expiry", "variants": {"aggressive": None}}, "enum setting"),
    ("trigger", {"by": "nope", "variants": {"aggressive": None}}, "enum setting"),
    ("invalidation", {"by": "entry_style", "variants": {"aggressive": {"long": "low", "short": "high"}, "breakout": None}}, "invalidation variant"),
    ("setup", {"by": "window", "variants": {"first": {"kind": "state", "long": "oi == 1", "short": "oi == 2", "expiry_bars": "$nope"},
                                            "every": {"kind": "event", "long": "oi == 1", "short": "oi == 2"}}}, "expiry_bars"),
])
def test_variant_refusals(part, value, fragment):
    with pytest.raises(ValidationError, match=fragment):
        g(**{part: value})


def test_without_a_trigger_every_setup_variant_must_be_the_signal():
    with pytest.raises(ValidationError, match="without a trigger"):
        g(trigger=None)
    sw = {"by": "entry_style", "variants": {"aggressive": None, "breakout": {"long": "volume == 1", "short": "volume == 2"}}}
    template = g(trigger=sw)                                       # a null trigger variant: the chosen setup must be the signal
    with pytest.raises(C.TemplateError, match="without a trigger"):
        compiled(template)                                          # window "first" is a state setup
    assert compiled(template, window="every").has_trigger is False
    assert compiled(template, entry_style="breakout").has_trigger is True


def test_only_the_chosen_variant_is_compiled_and_counts_for_the_warm_up():
    template = g()
    aggressive, breakout = compiled(template), compiled(template, entry_style="breakout")
    assert aggressive.part("trigger", "long").text == "volume == 1"
    assert "Max(high, 120)" in breakout.part("trigger", "long").text
    assert aggressive.min_bars < 120 <= breakout.min_bars                    # the unchosen breakout never holds the warm-up
    broken = copy.deepcopy(G)
    broken["trigger"]["variants"]["breakout"] = {"long": "NoSuchFunction(1) > 0", "short": "close > 0"}
    t = Template.from_data(broken)
    compiled(t)                                                              # the unchosen variant is never compiled
    with pytest.raises(C.TemplateError, match="NoSuchFunction"):
        compiled(t, entry_style="breakout")
    assert any("NoSuchFunction" in p for p in C.validate_template(t))       # but the library check covers every variant
    assert C.validate_template(template) == []


def test_the_setup_variant_decides_state_or_event():
    template = g()
    df = bars(setups={5: 1, 6: 1, 7: 1, 8: 1}, triggers={6: 1, 8: 1})
    first = engine.compute(compiled(template), {"15m": df})
    every = engine.compute(compiled(template, window="every"), {"15m": df})
    assert signals(first) == {6: 1}                                          # one opportunity per state instance
    assert signals(every) == {6: 1, 8: 1}                                    # each pullback is a new instance
    assert compiled(template).chosen_setup.kind == "state" and compiled(template, window="every").chosen_setup.kind == "event"


def test_a_null_context_variant_is_no_filter():
    template = g()
    assert compiled(template).context_on is False and compiled(template).part("context", "long") is None
    on = compiled(template, trend_relation="with_higher_trend")
    assert on.context_on and "@1d" in on.part("context", "long").text


def test_timeframe_placeholders_are_filled_and_validated_like_a_written_timeframe():
    template = g(trigger={"long": "close@{wave_tf} > open@{wave_tf}", "short": "close@{wave_tf} < open@{wave_tf}"})
    r = compiled(template, wave_tf="1h")
    assert r.part("trigger", "long").text == "close@1h > open@1h" and "1h" in r.lookback
    assert compiled(template, wave_tf="1d").part("trigger", "long").text == "close@1d > open@1d"
    for text, fragment in (("close@{nope} > 0", "timeframe setting"), ("close@{expiry} > 0", "timeframe setting")):
        with pytest.raises(C.TemplateError, match=fragment):
            compiled(g(trigger={"long": text, "short": "close > 0"}))
    free = copy.deepcopy(G)
    free["constraints"] = []
    free["trigger"] = {"long": "close@{wave_tf} > 0", "short": "close > 0"}
    with pytest.raises(C.TemplateError, match="finer"):
        compiled(Template.from_data(free), "1h", wave_tf="15m")              # the validator's own refusal of a finer timeframe
    with pytest.raises(C.TemplateError, match="wave timeframe must not be finer"):
        compiled(template, "1h", wave_tf="15m")                              # the template's constraint says it first


def test_d1_stays_byte_identical():
    path = library.TEMPLATES_DIR / "D1.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == D1_SHA256
    d1 = library.get("D1")
    for tf in d1.allowed_timeframes:
        s = C.resolve_settings(d1, tf)
        body = {"template": "D1", "version": 1, "schema": "atp.template/1", "engine": C.ENGINE_VERSION, "timeframe": tf,
                "sides": s.sides, "params": s.params, "pa_settings": C._pa_settings_fingerprint()}
        assert C.params_hash(d1, s) == hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:20]
        r = C.compile_recipe(d1, s)
        assert r.chosen_setup == d1.setup and r.has_trigger is False
        assert r.part("invalidation", "long").text == d1.invalidation.long
    assert C.validate_template(d1) == []


def test_family_g_params_hash_records_the_wave_settings():
    template = g()
    s = C.resolve_settings(template, "15m")
    assert C.params_hash(template, s) != C.params_hash(g(family="D", id="D99"), s)
    custom = copy.deepcopy(G)
    custom["params"].append({"key": "zz_max_ba", "label": "Zigzag limit", "help": "Largest B of a zigzag.", "type": "float",
                             "default": 0.9, "min": 0.5, "max": 1.0, "step": 0.01})
    t = Template.from_data(custom)
    a, b = C.resolve_settings(t, "15m"), C.resolve_settings(t, "15m", params={"zz_max_ba": 0.8})
    assert C._wave_settings_fingerprint(a) != C._wave_settings_fingerprint(b)


# --- WB4: sparse wave series -------------------------------------------------------------------------------------------------

def test_wave_level_series_are_sparse_and_do_not_hold_the_warm_up(monkeypatch):
    assert "WaveAlt" in engine.SPARSE_SERIES and set(engine.WAVE_SPARSE_SERIES) <= engine.SPARSE_SERIES
    assert set(engine.WAVE_SPARSE_SERIES) <= set(FUNCTIONS)                    # every one registered since W2-W4 (step 3 / 4)
    template = g(setup={"kind": "event", "long": 'oi == 1 OR WaveAlt(1) == "COR_B" OR close > WaveInvalidation(1)', "short": "oi == 2",
                        "same_bar": True})
    df = bars(setups={290: 1}, triggers={290: 1}, n=300)                     # flat bars: no swing, no alternate ever
    r = compiled(template)
    assert WAVES_FLAG in r.required_flags and r.min_bars >= 250               # the degree's history (SWING_DEGREE_MIN_BARS)
    assert signals(engine.compute(r, {"15m": df})) == {290: 1}
    monkeypatch.setattr(engine, "SPARSE_SERIES", frozenset())
    held = engine.compute(compiled(template), {"15m": df})
    assert signals(held) == {} and held.reason[290] == engine.REASON_WARMUP


# --- WB7: signal text ------------------------------------------------------------------------------------------------------

def test_every_token_and_wave_word_passes_the_guard():
    for key, phrase in language.TOKENS.items():
        assert language.guard(phrase) == [], key
    for table in (language.WAVE_WORDS, language.TYPE_WORDS, language.CLARITY_WORDS):
        for key, phrase in table.items():
            assert language.guard(phrase) == [] and not any(ch.isdigit() for ch in phrase), key
    from app.price_action.waves import counter
    assert set(language.WAVE_WORDS) == set(counter.STATES) and set(language.TYPE_WORDS) == set(counter.TYPES)
    assert set(language.CLARITY_WORDS) == set(counter.CLARITY)
    assert language.wave_words("IMP_2") == "wave two" and language.wave_words("COR_B") == "wave B" and language.wave_words(None) == ""
    assert language.guard("Wave 2 ended") != []                              # why the labels are in words (WAVE-4)


def test_wave_text_renders_the_wave_and_higher_timeframes_in_words():
    template = g(signal_text={"long": [{"token": "wave_b_pullback_ended"}, {"token": "higher_trend_agrees"}],
                              "short": [{"token": "wave_two_pullback_ended"}]})
    s = C.resolve_settings(template, "15m", params={"wave_tf": "1h", "higher_tf": "1d"})
    text = library.signal_text(template, s, "long")
    assert text == "The wave B pullback ended at an area on the 1-hour chart; the Daily trend agrees."
    assert language.guard(text) == []
    assert "{" not in language.render(["wave_two_pullback_ended", "void_beyond_start_of_wave_one"], timeframe="5m")


def test_a_token_naming_the_wave_timeframe_needs_the_setting():
    body = copy.deepcopy(G)
    body["params"] = [p for p in body["params"] if p["key"] not in ("wave_tf", "higher_tf")]
    body["constraints"], body["context"] = [], None
    problems = C.validate_template(Template.from_data(body))
    assert any("needs the timeframe setting 'wave_tf'" in p for p in problems)


# --- WB13: the ScreenQL functions and their flag -------------------------------------------------------------------------------

def test_the_flag_is_registered_off_with_a_catalog_entry():
    assert WAVES_FLAG == "screener_waves" == waves_gate.FLAG
    assert WAVES_FLAG in controls.FEATURE_FLAGS and WAVES_FLAG in controls.DEFAULT_OFF_FLAGS and WAVES_FLAG in FLAG_INFO
    assert set(WAVE_FUNCTIONS) == {"WaveState", "WaveType", "WaveAlt", "WaveClarity",
                                   # build steps 3 / 4 (tests/test_wave3_levels_lines.py)
                                   "WaveOrigin", "WaveEnd", "WaveInvalidation", "WaveCap", "WaveBeyondOrigin", "WaveRetrace",
                                   "WaveProjection", "WaveRatio", "WaveLineLevel", "WaveLineBreak",
                                   # the companions (tests/test_wave6_companions.py)
                                   "WaveVolumeLower", "SwingZoneEdge"}
    assert all(FUNCTIONS[n].flag == WAVES_FLAG for n in WAVE_FUNCTIONS)
    ast = parse('WaveState(1) == "IMP_2" AND WaveClarity(1) != "UNCLEAR" AND close > open')
    assert waves_gate.flagged_calls(ast) == ["WaveState", "WaveClarity"]
    assert "screener_waves" in waves_gate.refusal(["WaveState"]) and "bar close only" in waves_gate.intrabar_refusal(["WaveState"])


def test_the_runtime_equals_the_counter_and_degrees_are_one_to_three():
    df = synthetic_bars(700, seed=9)
    want = wave_series(df, 1)
    for name, column in (("WaveState", want.state), ("WaveType", want.type), ("WaveAlt", want.alt), ("WaveClarity", want.clarity)):
        got = evaluate(parse(f"{name}(1)"), SymbolData("S", {"5m": df}), base_tf="5m")
        assert list(got) == list(column), name
    hit = evaluate(parse('WaveState(1) == "IMP_3"'), SymbolData("S", {"5m": df}), base_tf="5m")
    assert list(hit) == [v == "IMP_3" for v in want.state]
    for bad in ("WaveState(0)", "WaveType(4)", 'WaveState(1) == "WAVE_9"'):
        _, validated = compile_screen(bad, base_tf="5m")
        assert not validated.ok, bad


def _org(tag, on):
    from tests.test_n1_e1_execution_core import _flag
    from tests.test_trading_worker import _tenant
    t = _tenant(f"wave2-{tag}-{uuid.uuid4().hex[:6]}@example.com", with_credentials=False)
    for name in ("screener_v2", "screener_intrabar") + ((WAVES_FLAG,) if on else ()):
        _flag(name, t["tenant_id"])
    return t


def test_flag_off_hides_and_refuses_the_wave_functions():
    from tests.test_auth_api import client
    off, on = _org("off", False), _org("on", True)
    source = 'WaveState(1) == "IMP_2"'
    entries_off = client.get("/api/screener/registry", headers=off["headers"]).json()["entries"]
    entries_on = client.get("/api/screener/registry", headers=on["headers"]).json()["entries"]
    assert not set(WAVE_FUNCTIONS) & set(entries_off) and set(WAVE_FUNCTIONS) <= set(entries_on)
    refused = client.post("/api/screener/validate", headers=off["headers"], json={"source": source, "base_tf": "15m"}).json()
    assert not refused["ok"] and WAVES_FLAG in json.dumps(refused["problems"])
    assert client.post("/api/screener/validate", headers=on["headers"], json={"source": source, "base_tf": "15m"}).json()["ok"]
    rule = {"name": "w", "kind": "instrument", "symbol": "ABC", "condition": source, "base_tf": "15m"}
    r = client.post("/api/alerts/rules", headers=off["headers"], json=rule)
    assert r.status_code == 422 and WAVES_FLAG in r.text
    r = client.post("/api/alerts/rules", headers=on["headers"], json={**rule, "fire_on": "intrabar"})
    assert r.status_code == 422 and "bar close only" in r.text
    r = client.post("/api/alerts/rules", headers=on["headers"], json=rule)
    assert r.status_code in (200, 201), r.text


def test_the_new_modules_are_english_and_public_safe():
    root = Path(__file__).resolve().parents[1]
    for rel in ("app/price_action/waves/counter.py", "app/price_action/waves/series.py", "app/screener/waves.py",
                "app/price_action/waves/levels.py", "app/screener/breakouts/wave_breaks.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert text.isascii(), rel
