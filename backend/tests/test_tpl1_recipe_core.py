"""Strategy templates step 1, the recipe core (owner order sections 1, 3, 4; R1, R3, R8, R9): the schema, the binding
to the real ScreenQL registry, the settings, the params hash, the signal-text guard and the setup / trigger state
machine.

The state machine is driven by a test recipe whose setup and trigger read control columns (`oi` arms a side, `volume`
triggers it), so every case below - expiry, same bar, replacement, conflict, invalid geometry, gaps through the arm
level, context, state setups, warm-up - is a few marks on flat synthetic bars, and each expected bar is stated."""
import copy

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st
from pydantic import ValidationError

from app.screener.registry import FUNCTIONS, LAST_BAR_ONLY
from app.strategy_templates import compile as C
from app.strategy_templates import engine, language, library
from app.strategy_templates.schema import Template

BASE = {
    "schema": "atp.template/1", "id": "D9", "version": 1, "name": "Test recipe", "family": "D", "idea": "A recipe for the tests.",
    "difficulty": "Beginner", "style": "Both", "allowed_timeframes": ["15m", "1h"],
    "params": [{"key": "expiry", "label": "Expiry", "help": "Bars after the setup.", "type": "int", "default": 3, "min": 0, "max": 50,
                "step": 1}],
    "setup": {"kind": "event", "long": "oi == 1", "short": "oi == 2", "expiry_bars": "$expiry"},
    "trigger": {"long": "volume == 1 OR volume == 3", "short": "volume == 2 OR volume == 3"},
    "invalidation": {"long": "low", "short": "high"},
    "exit_signal": {"long": "volume == 9", "short": "volume == 8"},
    "evidence": [{"name": "green_bar", "long": "close > open", "short": "close < open"}],
    "signal_text": {"long": [{"token": "supertrend_turned_bullish"}], "short": [{"token": "supertrend_turned_bearish"}]},
}
N = 30


def tpl(**changes) -> Template:
    body = copy.deepcopy(BASE)
    for key, value in changes.items():
        if "." in key:
            outer, inner = key.split(".")
            body[outer][inner] = value
        elif value is None and key in body:
            body[key] = None
        else:
            body[key] = value
    return Template.from_data(body)


def bars(setups=None, triggers=None, closes=None, lows=None, opens=None, highs=None, n=N):
    idx = pd.date_range("2026-03-02 03:45", periods=n, freq="15min", tz="UTC")
    c = np.full(n, 100.0)
    for i, v in (closes or {}).items():
        c[i] = v
    o = c.copy()
    for i, v in (opens or {}).items():
        o[i] = v
    h, lo = np.maximum(o, c) + 0.5, np.minimum(o, c) - 0.5
    for i, v in (lows or {}).items():
        lo[i] = v
    for i, v in (highs or {}).items():
        h[i] = v
    oi, vol = np.zeros(n), np.zeros(n)
    for i, v in (setups or {}).items():
        oi[i] = v
    for i, v in (triggers or {}).items():
        vol[i] = v
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": vol, "oi": oi}, index=idx)


def run(template, df, sides="both", **params):
    s = C.resolve_settings(template, "15m", sides, params)
    return engine.compute(C.compile_recipe(template, s), {"15m": df})


def signals(series):
    return {t: int(series.direction[t]) for t in series.signal_bars()}


# --- the state machine (S4, S5, S6, S7, R8) ---------------------------------------------------------------------------

def test_setup_then_trigger_fires_once_with_its_instance():
    df = bars({5: 1}, {7: 1, 8: 1})
    s = run(tpl(), df)
    assert signals(s) == {7: 1}                              # one signal per instance: bar 8 has nothing left to trigger
    assert s.setup_bar[7] == 5 and s.invalidation[7] == df["low"].iloc[7] < df["close"].iloc[7]
    assert s.instance_id(7) == f"long@{df.index[5].isoformat()}"


def test_short_side_and_sides_setting():
    df = bars({4: 2}, {6: 2})
    assert signals(run(tpl(), df)) == {6: -1}
    assert run(tpl(), df).invalidation[6] == df["high"].iloc[6]
    assert signals(run(tpl(), df, sides="long")) == {}
    assert signals(run(tpl(), df, sides="short")) == {6: -1}


def test_trigger_on_the_setup_bar_needs_same_bar():
    df = bars({5: 1}, {5: 1, 6: 1})
    assert signals(run(tpl(), df)) == {6: 1}
    assert signals(run(tpl(**{"setup.same_bar": True}), df)) == {5: 1}


def test_expiry_counts_signal_bars_from_the_setup_bar():
    assert signals(run(tpl(), bars({5: 1}, {8: 1}))) == {8: 1}       # 3 bars after: still armed
    assert signals(run(tpl(), bars({5: 1}, {9: 1}))) == {}           # 4 bars after: expired
    assert signals(run(tpl(), bars({5: 1}, {9: 1}), expiry=4)) == {9: 1}
    assert signals(run(tpl(**{"setup.expiry_bars": None}), bars({5: 1}, {25: 1}))) == {25: 1}   # no expiry


def test_a_new_setup_replaces_the_older_instance():
    assert signals(run(tpl(), bars({5: 1}, {10: 1}))) == {}
    s = run(tpl(), bars({5: 1, 7: 1}, {10: 1}))
    assert signals(s) == {10: 1} and s.setup_bar[10] == 7


def test_conflict_gives_no_signal_and_consumes_both_sides():
    df = bars({4: 1, 5: 2}, {6: 3, 7: 1, 8: 2})
    s = run(tpl(), df)
    assert signals(s) == {} and s.reason[6] == engine.REASON_CONFLICT


def test_invalid_geometry_and_missing_level_give_no_signal_and_consume():
    s = run(tpl(invalidation={"long": "high", "short": "low"}), bars({5: 1}, {6: 1, 7: 1}))
    assert signals(s) == {} and s.reason[6] == engine.REASON_GEOMETRY and s.reason[7] == ""
    s = run(tpl(), bars({5: 1}, {6: 1, 7: 1}, lows={6: np.nan}))
    assert signals(s) == {} and s.reason[6] == engine.REASON_NO_LEVEL
    s = run(tpl(invalidation={"long": "close", "short": "close"}), bars({5: 1}, {6: 1}))
    assert s.reason[6] == engine.REASON_GEOMETRY             # on the close is not on the losing side


ARM = {"setup.arm_level": {"long": "low", "short": "high"}}


def test_arm_level_breached_before_the_trigger_voids_the_setup():
    assert signals(run(tpl(**ARM), bars({5: 1}, {7: 1}))) == {7: 1}
    assert signals(run(tpl(**ARM), bars({5: 1}, {7: 1}, closes={6: 99.0}))) == {}             # a close below the setup bar's low
    wick = {**ARM, "setup.breach_on": "wick"}
    assert signals(run(tpl(**ARM), bars({5: 1}, {7: 1}, lows={6: 99.4}))) == {7: 1}            # close mode: a wick is not a breach
    assert signals(run(tpl(**wick), bars({5: 1}, {7: 1}, lows={6: 99.4}))) == {}               # wick mode: it is
    # S7: a gap that opens through the level and closes back above it voids the setup in wick mode, never fills later
    assert signals(run(tpl(**wick), bars({5: 1}, {7: 1}, opens={6: 98.0}))) == {}
    short = bars({5: 2}, {7: 2}, closes={6: 101.0})
    assert signals(run(tpl(**ARM), short)) == {}


def test_context_is_read_on_the_trigger_bar_and_keeps_the_instance_armed():
    ctx = {"context": {"long": "close > 100.2", "short": "close < 99.8"}}
    df = bars({5: 1}, {6: 1, 7: 1}, closes={7: 101.0})
    s = run(tpl(**ctx), df)
    assert signals(s) == {7: 1}
    assert signals(run(tpl(**ctx), bars({5: 1}, {6: 1}))) == {}


def test_state_setup_arms_on_its_rising_edge_and_ends_with_the_state():
    state = {"setup.kind": "state"}
    assert signals(run(tpl(**state), bars({5: 1, 6: 1, 7: 1}, {7: 1}))) == {7: 1}
    assert signals(run(tpl(**state), bars({5: 1, 6: 1, 7: 1}, {8: 1}))) == {}                  # the state ended on bar 8
    always = bars({i: 1 for i in range(N)}, {6: 1})
    assert signals(run(tpl(**state), always)) == {}         # true from the first bar: no rising edge was seen


def test_warm_up_is_no_signal():
    t = tpl(invalidation={"long": "Min(low, 5)", "short": "Max(high, 5)"})
    s = run(t, bars({2: 1}, {3: 1, 4: 1}))
    assert list(s.reason[:4]) == [engine.REASON_WARMUP] * 4 and s.warmup_bars == 4 and signals(s) == {}
    assert signals(run(t, bars({5: 1}, {6: 1}))) == {6: 1}


def test_exit_signal_and_evidence():
    df = bars({5: 1}, {7: 1, 12: 9}, closes={7: 100.4})
    df.loc[df.index[7], "open"] = 100.0
    s = run(tpl(), df)
    assert s.evidence[7] == ("green_bar",) and bool(s.exit_long[12]) and not s.exit_long[7] and not s.exit_short.any()
    s2 = run(tpl(), bars({5: 1}, {7: 1}))
    assert s2.evidence[7] == ()


def test_series_is_a_pure_function_of_bars():
    df = bars({3: 1, 9: 2, 15: 1}, {4: 1, 11: 2, 16: 3, 18: 1})
    a, b = run(tpl(), df), run(tpl(), df)
    assert np.array_equal(a.direction, b.direction) and list(a.reason) == list(b.reason)


marks = st.dictionaries(st.integers(0, N - 1), st.sampled_from([1, 2]), max_size=8)
trig_marks = st.dictionaries(st.integers(0, N - 1), st.sampled_from([1, 2, 3]), max_size=10)


@hsettings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(setups=marks, triggers=trig_marks, expiry=st.integers(0, 6), same_bar=st.booleans())
def test_property_every_signal_has_its_setup_and_trigger_and_no_look_ahead(setups, triggers, expiry, same_bar):
    t = tpl(**{"setup.same_bar": same_bar})
    df = bars(setups, triggers)
    s = run(t, df, expiry=expiry)
    for bar, d in signals(s).items():
        side_setup, side_trig = (1, (1, 3)) if d > 0 else (2, (2, 3))
        s0 = int(s.setup_bar[bar])
        assert triggers.get(bar) in side_trig and setups.get(s0) == side_setup
        assert 0 <= bar - s0 <= expiry and (same_bar or bar > s0)
        assert not any(setups.get(j) == side_setup for j in range(s0 + 1, bar + 1))   # the newest instance (R8)
        assert (s.invalidation[bar] < df["close"].iloc[bar]) if d > 0 else (s.invalidation[bar] > df["close"].iloc[bar])
    for cut in (5, 12, 20, 29):                                                          # truncation: no look-ahead
        part = run(t, df.iloc[:cut + 1], expiry=expiry)
        assert signals(part) == {k: v for k, v in signals(s).items() if k <= cut}


# --- binding to the registry (R1) --------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(LAST_BAR_ONLY))
def test_last_bar_only_functions_are_refused(name):
    t = tpl(**{"setup.long": f'{name}() == "x"'})
    with pytest.raises(C.TemplateError) as exc:
        C.compile_recipe(t, C.resolve_settings(t, "15m"))
    assert any("last bar only" in p for p in exc.value.problems)
    assert C.validate_template(t)


def test_last_bar_only_refused_in_every_part():
    for change in ({"trigger": {"long": "PatternBullish()", "short": "PatternBearish()"}},
                   {"invalidation": {"long": "low", "short": "high + 0 * PCR()"}},
                   {"context": {"long": 'Trend() == "UPTREND"', "short": "close > 0"}},
                   {"exit_signal": {"long": 'StructureEvent() == "BOS_BEARISH"', "short": "close > 0"}},
                   {"evidence": [{"name": "near", "long": "NearSupport(0.5)", "short": "close > 0"}]}):
        t = tpl(**change)
        with pytest.raises(C.TemplateError) as exc:
            C.compile_recipe(t, C.resolve_settings(t, "15m"))
        assert any("last bar only" in p for p in exc.value.problems), change


@pytest.mark.parametrize("text, fragment", [
    ("Foo(3) > 1", "unknown function"),
    ("RSI(14, 3) > 50", "takes 0-1 argument"),
    ("RSI(n=14) > 50", "has no argument"),
    ("Supertrend(10, ATR(5)) < close", "one value for the whole series"),
    ("SupertrendDir(10, 30) == 1", "between 0.1"),
    ("MACD(26, 12) > 0", "fast must be below slow"),
    ("RangeBreakout(\"sideways\")", "not one of"),
    ("close > RSI(14)", "compares price with index"),
    ("$nope > 1", "not a setting"),
    ("close", "must be a condition"),
    ("close >", "at character"),
])
def test_unknown_names_arguments_and_types_are_refused(text, fragment):
    t = tpl(**{"setup.long": text})
    with pytest.raises(C.TemplateError) as exc:
        C.compile_recipe(t, C.resolve_settings(t, "15m"))
    assert any(fragment in p for p in exc.value.problems), exc.value.problems


def test_invalidation_must_be_a_price_and_switches_stay_out_of_expressions():
    t = tpl(invalidation={"long": "RSI(14)", "short": "high"})
    with pytest.raises(C.TemplateError, match="must be a price"):
        C.compile_recipe(t, C.resolve_settings(t, "15m"))
    params = BASE["params"] + [{"key": "flag", "label": "Flag", "help": "A switch.", "type": "bool", "default": True}]
    t = tpl(params=params, **{"setup.long": "$flag"})
    with pytest.raises(C.TemplateError, match="cannot be read inside an expression"):
        C.compile_recipe(t, C.resolve_settings(t, "15m"))


def test_the_library_validates_against_the_registry():
    assert library.templates(), "the library is empty"
    for t in library.templates():
        assert C.validate_template(t) == [], t.id
        for part in (t.setup, t.trigger, t.invalidation, t.exit_signal, t.context):
            if part is None:
                continue
            for text in (part.long, part.short):
                for name in [w for w in FUNCTIONS if f"{w}(" in text]:
                    assert name not in LAST_BAR_ONLY


# --- the schema ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("change", [
    {"target": "2R"},                                   # templates have no target / size / order fields (section 5)
    {"stop_loss_atr_mult": 1.0},
    {"setup.order": "market"},
    {"id": "Z1"},
    {"version": 0},
    {"family": "G"},
    {"allowed_timeframes": ["2h"]},
    {"trigger": None},                                  # no trigger needs an event setup with same_bar
    {"setup.expiry_bars": "$missing"},
    {"setup.expiry_bars": -1},
    {"params": BASE["params"] * 2},
    {"params": [{**BASE["params"][0], "default": 99}]},
    {"params": [{**BASE["params"][0], "min": None}]},
    {"evidence": BASE["evidence"] * 2},
    {"signal_text": {"long": [], "short": [{"token": "supertrend_turned_bearish"}]}},
])
def test_schema_is_strict(change):
    body = copy.deepcopy(BASE)
    for key, value in change.items():
        if "." in key:
            outer, inner = key.split(".")
            body[outer][inner] = value
        else:
            body[key] = value
    with pytest.raises(ValidationError):
        Template.from_data(body)


# --- settings, constraints, the params hash (R9) ------------------------------------------------------------------------

CTX_PARAMS = BASE["params"] + [
    {"key": "fast", "label": "Fast", "help": "Fast average.", "type": "int", "default": 5, "min": 1, "max": 100, "step": 1},
    {"key": "slow", "label": "Slow", "help": "Slow average.", "type": "int", "default": 20, "min": 2, "max": 200, "step": 1},
    {"key": "kind", "label": "Average", "help": "The average.", "type": "ma_type", "default": "EMA", "choices": ["EMA", "SMA"]},
    {"key": "use_ctx", "label": "Filter", "help": "Use the filter.", "type": "bool", "default": False},
    {"key": "ctx_tf", "label": "Context", "help": "Its timeframe.", "type": "timeframe", "default": "1h", "choices": ["15m", "1h", "1d"]},
]
CONSTRAINTS = [{"left": "fast", "op": "<", "right": "slow", "message": "The fast average must be shorter than the slow one."},
               {"left": "ctx_tf", "op": ">", "right": "signal_timeframe", "when": "use_ctx",
                "message": "The context timeframe must be higher than the signal timeframe."}]


def ctx_tpl():
    return tpl(params=CTX_PARAMS, constraints=CONSTRAINTS,
               context={"long": "EMA(close, $fast) > EMA(close, $slow)", "short": "EMA(close, $fast) < EMA(close, $slow)",
                        "timeframe_param": "ctx_tf", "enabled_by": "use_ctx"})


@pytest.mark.parametrize("params, message", [
    ({"fast": 0}, "Fast must be between 1 and 100"),
    ({"fast": 2.5}, "Fast must be a whole number"),
    ({"fast": "5"}, "Fast must be a number"),
    ({"kind": "WMA"}, "Average must be one of EMA, SMA"),
    ({"use_ctx": 1}, "Filter must be yes or no"),
    ({"fast": 30, "slow": 20}, "The fast average must be shorter than the slow one."),
    ({"use_ctx": True, "ctx_tf": "15m"}, "The context timeframe must be higher than the signal timeframe."),
    ({"other": 1}, "unknown setting(s): other"),
])
def test_settings_bounds_and_constraints(params, message):
    with pytest.raises(C.TemplateError) as exc:
        C.resolve_settings(ctx_tpl(), "15m", params=params)
    assert message in exc.value.problems


def test_settings_accepted_and_context_runs_on_its_timeframe():
    t = ctx_tpl()
    assert C.resolve_settings(t, "15m", params={"ctx_tf": "15m"}).params["ctx_tf"] == "15m"    # the filter is off
    with pytest.raises(C.TemplateError, match="timeframe must be one of"):
        C.resolve_settings(t, "1d")
    s = C.resolve_settings(t, "15m", params={"use_ctx": True, "ctx_tf": "1h"})
    r = C.compile_recipe(t, s)
    assert r.context_timeframe == "1h" and "1h" in r.lookback and r.lookback["1h"] >= 20
    assert all(node.tf == "1h" for node in __import__("app.screener.nodes", fromlist=["walk"]).walk(r.part("context", "long").ast)
               if getattr(node, "name", None) in ("EMA", "close"))
    assert C.validate_template(t) == []


def test_params_hash_is_deterministic_and_covers_settings_and_pa_settings(monkeypatch):
    t = ctx_tpl()
    a = C.params_hash(t, C.resolve_settings(t, "15m"))
    assert a == C.params_hash(t, C.resolve_settings(t, "15m", params={"fast": 5}))
    assert a != C.params_hash(t, C.resolve_settings(t, "15m", params={"fast": 6}))
    assert a != C.params_hash(t, C.resolve_settings(t, "1h"))
    assert a != C.params_hash(t, C.resolve_settings(t, "15m", sides="long"))
    from app.price_action import pa_settings
    original = pa_settings.settings
    monkeypatch.setattr(pa_settings, "settings", lambda **kw: {**original(**kw), "reversal_mode": "score100"})
    assert a != C.params_hash(t, C.resolve_settings(t, "15m"))


# --- the signal text (policy 11l) ---------------------------------------------------------------------------------------

def test_every_token_passes_the_language_guard():
    for token, phrase in language.TOKENS.items():
        assert language.guard(phrase) == [], token
        for tf in language.TIMEFRAME_WORDS:
            text = language.render([token], timeframe=tf, context_timeframe="1d")
            assert language.guard(text) == [], (token, text)


@pytest.mark.parametrize("text", ["Buy the dip", "close above 1450", "stop-loss below the low", "the target is near",
                                  "RSI at 70", "go long now", "price {strike}", "trend turned bullish →"])
def test_the_guard_catches_advice_numbers_and_prices(text):
    assert language.guard(text)


def test_rendered_text_is_also_clean_for_the_ai_output_filter():
    from app.ai.output_filter import findings
    for t in library.templates():
        for tf in t.allowed_timeframes:
            for side in ("long", "short"):
                for ctx in (None, "1d"):
                    s = C.Settings(tf, "both", {p.key: (True if p.type == "bool" else p.default) for p in t.params})
                    text = library.signal_text(t, s, side, ctx)
                    assert text and language.guard(text) == [] and findings(text) == [], text
