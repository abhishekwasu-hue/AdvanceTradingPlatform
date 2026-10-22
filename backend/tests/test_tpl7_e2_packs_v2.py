"""Strategy templates TPL-7: the RSI period on RsiDivergence (`period=`, left out = RSI 14, byte-identical), the
divergence's prices (`RsiDivergenceRef`: its extreme and the confirming bar), the template E2 (RSI divergence, generic:
the rising edge of a regular divergence, then a change of character on smaller swings or a close beyond the confirming
bar, the divergence's extreme as the invalidation) against the owner order's section 8 gate, and the family F packs
version 2 (F1 + D3, F2 + E3 as new `source` choices; nested switches, the signal text per `source`, the timeframes per
`source`) with version 1 kept word for word so an adopted version 1 id keeps exactly its behaviour."""
import asyncio
import hashlib
import re
from typing import Any, List, Optional

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st
from pydantic import ValidationError

from app.backtest.options_engine import OptionBacktestConfig
from app.core.enums import OptionStrategy
from app.core.models import RiskConfig
from app.quality.lookahead import synthetic_bars
from app.screener import runtime as R
from app.screener.parser import parse
from app.screener.runtime import SymbolData, evaluate
from app.screener.validator import validate_study
from app.strategy_templates import compile as C
from app.strategy_templates import engine, ids, language, library, packs, parity
from app.strategy_templates.adapter import ENGINE_LABEL, TemplateStrategy
from app.strategy_templates.schema import Template, leaves
from tests.test_tpl1_recipe_core import BASE, tpl

E2 = library.get("E2")
F1, F2, D1, D3, A2, E3 = (library.get(t) for t in ("F1", "F2", "D1", "D3", "A2", "E3"))
RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5)
K = 400.0
# the version 1 pack files exactly as they were on main before version 2 (#399): a kept version never changes
V1_SHA256 = {"F1": "fe0eaf45ee95a6ff8fb1895e64437a91b404cebb4eba1c96f58a5a4ef6f5a10a",
             "F2": "9b51ecf7038af627869b988793f9f85b809febd1b1f34991874e5341ace226e4"}


def bars(seed, n=1500, tf="15m"):
    return synthetic_bars(n, tf=tf, seed=seed)


def ev(text, df, tf="15m"):
    return evaluate(parse(text), SymbolData("S", {tf: df}), base_tf=tf)


def run(template, df, tf="15m", **params):
    return engine.compute(C.compile_recipe(template, C.resolve_settings(template, tf, params=params)), {tf: df})


def signals(s):
    return {t: int(s.direction[t]) for t in s.signal_bars()}


def mirror(df, axis=K):
    out = df.copy()
    out["open"], out["close"] = axis - df["open"], axis - df["close"]
    out["high"], out["low"] = axis - df["low"], axis - df["high"]
    return out


def same_series(a, b):
    np.testing.assert_array_equal(a.direction, b.direction)
    np.testing.assert_allclose(a.invalidation, b.invalidation, equal_nan=True)
    np.testing.assert_array_equal(a.exit_long, b.exit_long)
    np.testing.assert_array_equal(a.exit_short, b.exit_short)
    assert list(a.reason) == list(b.reason) and a.warmup_bars == b.warmup_bars
    assert [a.instance_id(t) for t in a.signal_bars()] == [b.instance_id(t) for t in b.signal_bars()]


# =========================================================================== RsiDivergence(period=) and RsiDivergenceRef
def _reference_divergence(f, side, lookback, degree, period=14):
    """RsiDivergence exactly as AMW-3 wrote it (main before TPL-7), with the RSI period as an argument."""
    k = "L" if side == "bullish" else "H"
    rsi = R._rsi(f["close"].astype(float), period).to_numpy(float)
    n_ = len(f)
    div = np.zeros(n_, dtype=bool)
    last_bar = np.full(n_, -1, dtype=np.int64)
    prev: Optional[Any] = None
    held = {"extreme": np.full(n_, np.nan), "confirm_bar": np.full(n_, np.nan)}
    far = f["high" if side == "bullish" else "low"].to_numpy(float)
    for p in R._swing_pivots(f, degree):
        if p.kind != k:
            continue
        c, b = int(p.confirmed_idx), int(p.bar_idx)
        state = False
        if prev is not None:
            r0, r1 = rsi[int(prev.bar_idx)], rsi[b]
            if np.isfinite(r0) and np.isfinite(r1):
                state = bool(p.price < prev.price and r1 > r0) if k == "L" else bool(p.price > prev.price and r1 < r0)
        div[c:], last_bar[c:] = state, b
        if state:
            held["extreme"][c:], held["confirm_bar"][c:] = p.price, far[c]
        prev = p
    age = np.arange(n_) - last_bar
    return div & (last_bar >= 0) & (age <= lookback), held


@pytest.mark.parametrize("seed, side, lookback, degree", [(1, "bullish", 15, 0), (2, "bearish", 30, 0), (3, "bullish", 60, 1),
                                                          (4, "bearish", 15, 1), (5, "bullish", 5, 0)])
def test_rsi_divergence_without_a_period_is_byte_identical_to_rsi_fourteen(seed, side, lookback, degree):
    df = bars(seed, 900)
    want, _ = _reference_divergence(df, side, lookback, degree)
    plain = ev(f'RsiDivergence("{side}", {lookback}, {degree})', df)
    np.testing.assert_array_equal(plain.to_numpy(), want)
    assert plain.dtype == bool and plain.any(), "the fixture must carry divergences"
    np.testing.assert_array_equal(ev(f'RsiDivergence("{side}", {lookback}, {degree}, period=14)', df).to_numpy(), want)
    np.testing.assert_array_equal(R._rsi_divergence(df, side, lookback, degree), want)            # the default argument


@pytest.mark.parametrize("period", [2, 5, 9, 21, 50])
def test_rsi_divergence_reads_the_rsi_of_its_period(period):
    df = bars(7, 900)
    for side in ("bullish", "bearish"):
        want, held = _reference_divergence(df, side, 40, 0, period)
        np.testing.assert_array_equal(ev(f'RsiDivergence("{side}", 40, 0, period={period})', df).to_numpy(), want)
        for what in ("extreme", "confirm_bar"):
            got = ev(f'RsiDivergenceRef("{what}", "{side}", 0, period={period})', df).to_numpy(float)
            np.testing.assert_allclose(got, held[what], equal_nan=True)
    assert any(not np.array_equal(_reference_divergence(df, s, 40, 0, period)[0], _reference_divergence(df, s, 40, 0)[0])
               for s in ("bullish", "bearish")) or period == 14                      # the period changes the reading


def test_the_divergence_prices_are_the_swing_and_its_confirming_bar_held_until_the_next_divergence():
    df = bars(3, 900)
    for side, extreme_col, far_col in (("bullish", "low", "high"), ("bearish", "high", "low")):
        k = "L" if side == "bullish" else "H"
        ext = ev(f'RsiDivergenceRef("extreme", "{side}")', df).to_numpy(float)
        conf = ev(f'RsiDivergenceRef("confirm_bar", "{side}")', df).to_numpy(float)
        starts = [d for d in R._divergence_swings(df, side, 0, 14) if d.diverges]
        assert starts, side
        assert np.isnan(ext[:starts[0].confirmed]).all() and np.isfinite(ext[starts[0].confirmed:]).all()
        for d in starts:
            assert ext[d.confirmed] == pytest.approx(df[extreme_col].iloc[d.bar])           # the swing's own extreme
            assert conf[d.confirmed] == pytest.approx(df[far_col].iloc[d.confirmed])         # the confirming bar's far side
        pivots = [p for p in R._swing_pivots(df, 0) if p.kind == k]
        assert len(pivots) > len(starts)                                                # swings that do not diverge leave it


def test_the_period_argument_is_validated_and_asks_for_its_history():
    def check(text):
        return validate_study(parse(text), base_tf="5m").validated
    assert check('RsiDivergence("bullish")').lookback["5m"] == 100                       # as before (AMW-3)
    assert check('RsiDivergence("bullish", 15, 0, period=14)').lookback["5m"] == 100
    assert check('RsiDivergence("bullish", 15, 0, period=300)').lookback["5m"] == 300
    assert check('RsiDivergenceRef("extreme", "bearish", 2)').lookback["5m"] == 600
    for bad in ('RsiDivergence("bullish", 15, 0, period=0)', 'RsiDivergence("bullish", 15, 0, rsi=9)',
                'RsiDivergenceRef("level", "bullish")', 'RsiDivergenceRef("extreme", "up")', 'RsiDivergenceRef("extreme", "bullish", 5)'):
        assert not check(bad).ok, bad
    assert check('RsiDivergenceRef("extreme", "bullish")').ok


@pytest.mark.parametrize("text", ['RsiDivergence("bullish", 30, 0, period=7)', 'RsiDivergenceRef("extreme", "bullish", 0, period=9)',
                                  'RsiDivergenceRef("confirm_bar", "bearish", 1)'])
def test_the_divergence_series_read_no_later_bar(text):
    df = bars(2, 700)
    full = ev(text, df)
    for t in range(150, len(df), 37):
        part = ev(text, df.iloc[:t + 1])
        np.testing.assert_array_equal(np.asarray(part, dtype=float), np.asarray(full.iloc[:t + 1], dtype=float), err_msg=f"t={t}")


# ================================================================================================= E2 RSI divergence
def _e2_series(df, p):
    """The E2 parts written out in the test: the rising edge of the divergence state, the arm level / invalidation (the
    divergence's extreme), and the trigger (a lower-degree change of character, or a close beyond the confirming bar)."""
    n, out = len(df), {}
    for i, (side, up) in enumerate((("bullish", "up"), ("bearish", "down"))):
        state, held = _reference_divergence(df, side, p["divergence_bars"], p["swing_degree"], p["rsi_period"])
        edge = state & ~np.r_[False, state[:-1]]
        if p["trigger"] == "choch":
            trig = (ev(f'SwingBreakType("{up}", {p["trigger_degree"]}, {p["break_k"]!r})', df) == "CHOCH").to_numpy()
        else:
            close = df["close"].to_numpy(float)
            trig = (close > held["confirm_bar"]) if i == 0 else (close < held["confirm_bar"])
        out[i] = (edge, trig, held["extreme"])
    assert len(out[0][0]) == n
    return out


E2_DEFAULTS = {p.key: p.default for p in E2.params}


def e2_reference(s, df, p):
    """An independent walk of S4 / S5 / S6 / R8 for E2: event setups (the newest replaces the older), the arm level (the
    divergence's extreme) breached by a close voids the instance, the expiry in bars, the trigger on the setup bar or later,
    conflicts, and the invalidation read on the trigger bar. Returns {bar: (side, invalidation, setup bar)}."""
    series = _e2_series(df, p)
    close = df["close"].to_numpy(dtype=float)
    armed, lv, want = {0: -1, 1: -1}, {0: np.nan, 1: np.nan}, {}
    for t in range(s.warmup_bars, len(df)):
        fired = {}
        for i in (0, 1):
            edge, trig, extreme = series[i]
            if edge[t]:
                armed[i], lv[i] = t, extreme[t]
            elif armed[i] >= 0:
                breached = np.isfinite(lv[i]) and (close[t] < lv[i] if i == 0 else close[t] > lv[i])
                if t - armed[i] > p["confirm_max_bars"] or breached:
                    armed[i] = -1
            if armed[i] >= 0 and trig[t]:
                fired[i] = armed[i]
        if len(fired) == 2:
            assert s.reason[t] == engine.REASON_CONFLICT, t
            armed = {0: -1, 1: -1}
            continue
        if not fired:
            continue
        i, s0 = next(iter(fired.items()))
        armed[i] = -1
        level = float(series[i][2][t])
        if (i == 0 and level < close[t]) or (i == 1 and level > close[t]):
            want[t] = (1 if i == 0 else -1, level, s0)
        else:
            assert s.reason[t] in (engine.REASON_GEOMETRY, engine.REASON_NO_LEVEL), t
    return want


def expect(s, want):
    assert signals(s) == {t: v[0] for t, v in want.items()}
    for t, (_, level, s0) in want.items():
        assert s.invalidation[t] == pytest.approx(level), t
        assert s.setup_bar[t] == s0, t


E2_CASES = [({}, 1), ({}, 7), ({"trigger": "confirm_bar"}, 1), ({"trigger": "confirm_bar"}, 6),
            ({"rsi_period": 7, "divergence_bars": 40, "confirm_max_bars": 30}, 2),
            ({"swing_degree": 2, "trigger_degree": 1, "break_k": 0.0, "confirm_max_bars": 40, "divergence_bars": 60}, 6),
            ({"rsi_period": 21, "trigger": "confirm_bar", "confirm_max_bars": 3}, 5)]


@pytest.mark.parametrize("params, seed", E2_CASES)
def test_e2_fires_exactly_on_the_confirmed_turn_after_a_divergence(params, seed):
    df = bars(seed, 2500 if params.get("swing_degree") == 2 else 1500)
    p = {**E2_DEFAULTS, **params}
    s = run(E2, df, **params)
    want = e2_reference(s, df, p)
    expect(s, want)
    assert want, "the fixture must carry signals"


def test_e2_both_sides_on_the_fixtures():
    seen = set()
    for params, seed in E2_CASES[:4]:
        seen |= set(signals(run(E2, bars(seed), **params)).values())
    assert seen == {1, -1}


def test_e2_must_not_fire_without_a_regular_divergence_or_late_or_after_the_extreme_broke():
    df = bars(1)
    s = run(E2, df)
    for t, d in signals(s).items():
        s0 = int(s.setup_bar[t])
        side = "bullish" if d > 0 else "bearish"
        state, held = _reference_divergence(df, side, 15, 1)
        assert state[s0] and not state[s0 - 1]                                       # the rising edge, regular only
        assert t - s0 <= E2_DEFAULTS["confirm_max_bars"]
        closes = df["close"].iloc[s0 + 1:t + 1]
        assert ((closes >= held["extreme"][s0]) if d > 0 else (closes <= held["extreme"][s0])).all()
    quick = run(E2, df, confirm_max_bars=1)                                          # too late: only a confirmation within a bar
    assert all(t - quick.setup_bar[t] <= 1 for t in quick.signal_bars())
    assert any(t - s.setup_bar[t] > 1 for t in s.signal_bars()) and set(signals(quick)) != set(signals(s))
    calm = df.copy()
    calm[["open", "high", "low", "close"]] = 100.0 + 0.0 * calm[["open", "high", "low", "close"]]
    calm["high"] += 0.5
    calm["low"] -= 0.5
    assert run(E2, calm).signal_bars() == []                                         # no swings, no divergence, no signal


def test_e2_settings_refused_in_plain_words_and_the_warm_up_by_swing_size():
    with pytest.raises(C.TemplateError, match="smaller swing size must be smaller"):
        C.resolve_settings(E2, "15m", params={"swing_degree": 1, "trigger_degree": 1})
    with pytest.raises(C.TemplateError, match="between 1 and 3"):
        C.resolve_settings(E2, "15m", params={"swing_degree": 0})
    with pytest.raises(C.TemplateError, match="between 2 and 100"):
        C.resolve_settings(E2, "15m", params={"rsi_period": 1})
    df = bars(1, 900)
    assert run(E2, df).warmup_bars == 250 and set(run(E2, df).reason[:250]) == {engine.REASON_WARMUP}
    assert run(E2, df, swing_degree=2, trigger_degree=1).warmup_bars == 600
    recipe = C.compile_recipe(E2, C.resolve_settings(E2, "15m", params={"rsi_period": 9}))
    assert all(p.params.get("rsi_period") == 9 for k, p in recipe.parts.items() if "rsi_period" in p.params)
    assert recipe.part("invalidation", "long").params["rsi_period"] == 9 and recipe.has_trigger
    assert recipe.chosen_setup.arm_level is not None and recipe.chosen_setup.same_bar


@pytest.mark.parametrize("params, seed", [({}, 1), ({"trigger": "confirm_bar"}, 2), ({"rsi_period": 9, "confirm_max_bars": 25}, 3)])
def test_e2_mirror_short_equals_long_on_mirrored_prices(params, seed):
    df = bars(seed, 1500)
    df[["open", "high", "low", "close"]] = (df[["open", "high", "low", "close"]] * 20).round()      # whole ticks: exact mirror
    axis = K * 20
    s, m = run(E2, df, **params), run(E2, mirror(df, axis), **params)
    assert signals(s), "the fixture must carry signals"
    assert signals(m) == {t: -d for t, d in signals(s).items()}
    for t in signals(s):
        assert axis - m.invalidation[t] == pytest.approx(s.invalidation[t]) and m.setup_bar[t] == s.setup_bar[t]


@pytest.mark.parametrize("params, seed", [({}, 1), ({"trigger": "confirm_bar"}, 6)])
def test_e2_truncation_no_look_ahead_over_many_t(params, seed):
    df = bars(seed, 1200)
    full = run(E2, df, **params)
    assert full.signal_bars()
    marks = sorted(set(full.signal_bars()) | {t + 1 for t in full.signal_bars()} | set(range(full.warmup_bars, len(df), 61)))
    for t in [x for x in marks if x < len(df)]:
        part = run(E2, df.iloc[:t + 1], **params)
        np.testing.assert_array_equal(part.direction, full.direction[:t + 1], err_msg=f"t={t}")
        np.testing.assert_allclose(part.invalidation, full.invalidation[:t + 1], equal_nan=True, err_msg=f"t={t}")
        assert [part.instance_id(i) for i in part.signal_bars()] == [full.instance_id(i) for i in full.signal_bars() if i <= t]


def _window(seed, n=1400, keep=900):
    window = synthetic_bars(n, tf="15m", seed=seed).iloc[-keep:].copy()            # a fixed TRAILING window (R6)
    window[["open", "high", "low", "close"]] *= 240.0                                # index-like prices for the option ladder
    return window


PARITY = [("E2", {}, 1), ("E2", {"trigger": "confirm_bar"}, 1), ("F1", {"source": "ma_pullback"}, 2),
          ("F2", {"source": "band_reversion"}, 2)]


@pytest.mark.parametrize("tid, params, seed", PARITY, ids=[f"{p[0]}-{i}" for i, p in enumerate(PARITY)])
def test_scan_equals_backtest_equals_options_equals_worker(tid, params, seed):
    template = library.get(tid)
    strat = TemplateStrategy(template, C.resolve_settings(template, "15m", params=params))
    window = _window(seed)
    scan = parity.scan_answers(strat.recipe, window)
    assert sum(1 for a in scan.values() if a.direction) >= 2, "the fixture must carry signals to compare"
    answers, trades = parity.backtest_answers(strat, window, "S", RISK)
    assert parity.compare(scan, answers, "backtest") == []
    assert trades and parity.compare_trades(scan, trades, lambda ts: ts) == []
    config = OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, spread_width=2)
    assert parity.compare(scan, parity.options_answers(strat, window, "NIFTY 50", config, RISK), "options") == []
    signal_at = [i for i, a in enumerate(scan.values()) if a.direction]
    at = sorted({j for i in signal_at for j in (i - 1, i, i + 1) if 0 <= j < len(window)} | set(range(0, len(window), 40)) | {len(window) - 1})
    worker = parity.worker_answers(strat, window, "S", at)
    assert parity.compare(scan, worker, "worker") == []


@hsettings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(period=st.integers(2, 40), degree=st.integers(1, 2), trig=st.sampled_from(["choch", "confirm_bar"]), within=st.integers(1, 40),
       div_bars=st.integers(5, 80), k=st.floats(0.0, 1.0), seed=st.integers(0, 30))
def test_e2_property_random_valid_settings(period, degree, trig, within, div_bars, k, seed):
    df = bars(seed, 900)
    p = {**E2_DEFAULTS, "rsi_period": period, "swing_degree": degree, "trigger_degree": degree - 1, "trigger": trig,
         "confirm_max_bars": within, "divergence_bars": div_bars, "break_k": k}
    s = run(E2, df, **{key: v for key, v in p.items()})
    close = df["close"].to_numpy(float)
    series = _e2_series(df, p)
    for t, d in signals(s).items():
        edge, trig_s, extreme = series[0 if d > 0 else 1]
        s0 = int(s.setup_bar[t])
        assert trig_s[t] and edge[s0] and 0 <= t - s0 <= within                     # no signal without its trigger
        assert s.invalidation[t] == pytest.approx(extreme[t])
        assert (s.invalidation[t] < close[t]) if d > 0 else (s.invalidation[t] > close[t])     # S6


def test_e2_validates_its_text_passes_the_guard_and_no_number_is_written_into_a_rule():
    assert C.validate_template(E2) == [] and E2.family == "E"
    for tf in E2.allowed_timeframes:
        for trig in ("choch", "confirm_bar"):
            settings = C.resolve_settings(E2, tf, params={"trigger": trig})
            for side in ("long", "short"):
                text = library.signal_text(E2, settings, side)
                assert text and language.guard(text) == [], text
                assert "divergence" in text and ("changed character" in text) == (trig == "choch")
    for p in E2.params:
        assert language.guard(p.label) == [] or p.type in ("bool", "int", "float"), p.key
        if p.choices:
            assert len(p.choice_labels) == len(p.choices)
    texts = _texts(E2)
    assert texts
    for text in texts:
        assert not re.search(r"(?<![\w$])\d", re.sub(r"\[1\]", "", text)), text               # only the bar before
        assert not re.search(r"\b(target|lots|strike|premium|order|buy|sell)\b", text, re.I)


def _texts(template):
    out: List[str] = []
    for part in (template.setup, template.trigger, template.invalidation, template.exit_signal, template.context):
        if part is None:
            continue
        for v in leaves(part):
            if v is not None and hasattr(v, "long"):
                out += [v.long, v.short]
            if getattr(v, "arm_level", None) is not None:
                out += [v.arm_level.long, v.arm_level.short]
    return out


def test_e2_ids_round_trip_and_fit_and_the_adapter_signal_with_provenance_and_flags():
    for tf in [t for t in E2.allowed_timeframes if t in ENGINE_LABEL]:
        sid, settings = ids.adopt(E2, tf)
        assert ids.decode(sid) == (E2, settings) and sid.startswith("template:E2:1:")
    worst = {"rsi_period": 100, "swing_degree": 3, "divergence_bars": 200, "trigger": "confirm_bar", "trigger_degree": 2,
             "break_k": 4.95, "confirm_max_bars": 200}
    sid, settings = ids.adopt(E2, "5m", "short", worst)
    assert len(sid) <= ids.MAX_LENGTH and ids.decode(sid) == (E2, settings)
    df = bars(1)
    strat = TemplateStrategy(E2, C.resolve_settings(E2, "15m"))
    series = engine.compute(strat.recipe, {"15m": df})
    t = series.signal_bars()[0]
    sig = strat.analyze({ENGINE_LABEL["15m"]: df.iloc[:t + 1]}, "S")
    assert sig.is_tradeable and sig.stop_loss == pytest.approx(series.invalidation[t]) and sig.target1 is None and sig.risk_reward is None
    assert sig.provenance["template"] == "E2" and sig.provenance["setup"] == series.instance_id(t)
    assert all(language.guard(r) == [] for r in sig.reasons)
    assert strat.recipe.required_flags == {"screenql_amw_functions", "screener_breakouts"}       # the divergence; the CHoCH
    bar_only = TemplateStrategy(E2, C.resolve_settings(E2, "15m", params={"trigger": "confirm_bar"}))
    assert bar_only.recipe.required_flags == {"screenql_amw_functions"}
    data = E2.model_dump(by_alias=True)
    assert not {"target", "targets", "lots", "quantity", "strike", "premium", "order"} & set(data)


# ======================================================================== schema: nested switches, text and timeframes
def _nested(**over):
    params = BASE["params"] + [
        {"key": "outer", "label": "Outer", "help": "A choice.", "type": "enum", "default": "a", "choices": ["a", "b"]},
        {"key": "inner", "label": "Inner", "help": "A choice.", "type": "enum", "default": "x", "choices": ["x", "y"]}]
    trigger = {"by": "outer", "variants": {"a": None, "b": {"by": "inner", "variants": {
        "x": {"long": "close > open", "short": "close < open"}, "y": {"long": "close > high[1]", "short": "close < low[1]"}}}}}
    data = {**over}
    return tpl(params=params, trigger=trigger, **{"setup.same_bar": True}, **data)


def test_a_nested_switch_compiles_the_innermost_chosen_variant():
    t = _nested()
    for outer, inner, want in (("a", "x", None), ("b", "x", "close > open"), ("b", "y", "close > high[1]")):
        recipe = C.compile_recipe(t, C.resolve_settings(t, "15m", params={"outer": outer, "inner": inner}))
        got = recipe.part("trigger", "long")
        assert (got.text if got else None) == want
    assert C.validate_template(t) == []
    assert C.chosen(t.trigger, C.resolve_settings(t, "15m", params={"outer": "b", "inner": "y"})).long == "close > high[1]"


def test_the_schema_refuses_a_nested_switch_that_repeats_its_setting_or_misses_a_choice():
    params = BASE["params"] + [{"key": "outer", "label": "Outer", "help": "A choice.", "type": "enum", "default": "a", "choices": ["a", "b"]}]
    sided = {"long": "close > open", "short": "close < open"}
    with pytest.raises(ValidationError, match="another setting"):
        tpl(params=params, trigger={"by": "outer", "variants": {"a": sided, "b": {"by": "outer", "variants": {"a": sided, "b": sided}}}})
    two = params + [{"key": "inner", "label": "Inner", "help": "A choice.", "type": "enum", "default": "x", "choices": ["x", "y"]}]
    with pytest.raises(ValidationError, match="cover exactly"):
        tpl(params=two, trigger={"by": "outer", "variants": {"a": sided, "b": {"by": "inner", "variants": {"x": sided}}}})
    with pytest.raises(ValidationError, match="every invalidation variant"):
        tpl(params=two, invalidation={"by": "outer", "variants": {"a": {"long": "low", "short": "high"},
                                                                   "b": {"by": "inner", "variants": {"x": None, "y": {"long": "low", "short": "high"}}}}})
    text = {"long": [{"token": "supertrend_turned_bullish"}], "short": [{"token": "supertrend_turned_bearish"}]}
    with pytest.raises(ValidationError, match="signal text variants"):
        tpl(params=params, signal_text={"by": "outer", "variants": {"a": text}})
    ok = tpl(params=params, signal_text={"by": "outer", "variants": {"a": text, "b": text}})
    assert library.signal_text(ok, C.resolve_settings(ok, "15m", params={"outer": "b"}), "long").startswith("Supertrend turned bullish")
    with pytest.raises(ValidationError, match="timeframes_by"):
        tpl(params=params, timeframes_by={"by": "outer", "allowed": {"a": ["1w"]}})
    narrow = tpl(params=params, timeframes_by={"by": "outer", "allowed": {"b": ["15m"]}})
    assert C.resolve_settings(narrow, "1h", params={"outer": "a"}).timeframe == "1h"
    with pytest.raises(C.TemplateError, match="runs on the 15-minute timeframes only"):
        C.resolve_settings(narrow, "1h", params={"outer": "b"})


def test_every_earlier_template_reads_exactly_as_before():
    """TPL-7 changed the schema (nested switches, a signal text switch, timeframes per choice), not the templates: every
    library template other than the new E2 and the packs' version 2 has neither, and its chosen parts are its own."""
    for t in library.templates():
        if t.id in ("E2", "F1", "F2"):
            continue
        assert t.timeframes_by is None and not hasattr(t.signal_text, "by"), t.id
        for part in (t.context, t.setup, t.trigger, t.invalidation, t.exit_signal):
            for v in getattr(part, "variants", {}).values():
                assert not hasattr(v, "variants"), t.id                                     # no nested switch


# ===================================================================================== the packs, version 2 and version 1
def test_version_one_of_each_pack_is_kept_word_for_word():
    for tid, digest in V1_SHA256.items():
        path = library.ARCHIVE_DIR / f"{tid}.v1.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, tid
        old = library.get_version(tid, 1)
        assert old.version == 1 and library.get(tid).version == 2 and library.get_version(tid, 2) is library.get(tid)
        assert old.param(packs.SOURCE).choices == (next(iter(packs.MEMBERS[tid])),)          # one member in version 1
        assert C.validate_template(old) == []
    assert [(t.id, t.version) for t in library.archived() if t.family == "F"] == [("F1", 1), ("F2", 1)]   # G: the wave companions
    assert {t.id: t.version for t in library.templates()}["F1"] == 2 and len({t.id for t in library.templates()}) == len(library.templates())
    with pytest.raises(KeyError):
        library.get_version("F1", 3)


@pytest.mark.parametrize("sid, member, params, seed", [
    ("template:F1:1:15m:both:,,,,", "D1", {}, 1),
    ("template:F1:1:15m:both:,7,2.5,1,1h", "D1", {"period": 7, "multiplier": 2.5, "use_context": True, "context_timeframe": "1h"}, 3),
    ("template:F2:1:15m:both:,0", "A2", {"swing_degree": 0}, 1),
    ("template:F2:1:1h:long:,", "A2", {}, 2)])
def test_a_version_one_id_keeps_resolving_to_exactly_its_behaviour(sid, member, params, seed):
    template, settings = ids.decode(sid)
    assert template.version == 1 and template == library.get_version(template.id, 1) and ids.encode(template, settings) == sid
    tf = settings.timeframe
    df = bars(seed, 900, tf=tf)
    old = engine.compute(C.compile_recipe(template, settings), {tf: df})
    m = library.get(member)
    same_series(old, engine.compute(C.compile_recipe(m, C.resolve_settings(m, tf, settings.sides, params)), {tf: df}))
    new = library.get(template.id)                                                       # version 2, its first source
    new_settings = C.resolve_settings(new, tf, settings.sides, params)
    same_series(old, engine.compute(C.compile_recipe(new, new_settings), {tf: df}))
    assert C.params_hash(template, settings) != C.params_hash(new, new_settings)          # the version is in the provenance
    assert C.params_hash(template, settings) == C.params_hash(library.get_version(template.id, 1), settings)
    assert library.signal_text(template, settings, "long") == library.signal_text(new, new_settings, "long")


def test_a_version_one_id_resolves_through_the_strategy_resolver(monkeypatch):
    from app.custom_strategies import resolver
    import app.platform.controls as controls

    class _User:
        tenant_id = 987655

    async def fake_flag(session, name, tenant_id=None):
        return True

    monkeypatch.setattr(controls, "flag_enabled", fake_flag)
    strat = asyncio.run(resolver.resolve_strategy("template:F1:1:15m:both:,,,,", _User(), None))
    assert isinstance(strat, TemplateStrategy) and strat.template.version == 1 and strat.id == "template:F1:1:15m:both:,,,,"
    new = asyncio.run(resolver.resolve_strategy("template:F1:2:15m:both:ma_pullback,,,,,,,,,,,", _User(), None))
    assert new.template.version == 2 and new.settings.params["source"] == "ma_pullback"
    with pytest.raises(KeyError, match="adopt it again"):
        ids.decode("template:F1:9:15m:both:,,,,")
    with pytest.raises(KeyError, match="adopt it again"):
        ids.decode("template:D2:2:15m:both:,,,,,,")
    with pytest.raises(KeyError):
        ids.decode("template:F1:01:15m:both:,,,,")
    with pytest.raises(KeyError):                                                       # a version 1 id cannot name a version 2 member
        ids.decode("template:F1:1:15m:both:ma_pullback,,,,")


def test_the_new_members_run_exactly_as_on_their_own():
    for pack, choice, member, params in ((F1, "ma_pullback", D3, {"ma_type": "WMA", "fast": 15, "slow": 40}),
                                         (F2, "band_reversion", E3, {"regime": "structure", "swing_degree": 0})):
        df = bars(2, 900)
        settings = C.resolve_settings(pack, "15m", params={"source": choice, **params})
        mine = engine.compute(C.compile_recipe(pack, settings), {"15m": df})
        theirs = engine.compute(C.compile_recipe(member, C.resolve_settings(member, "15m", params=params)), {"15m": df})
        same_series(mine, theirs)
        assert mine.signal_bars(), (pack.id, choice)
        got, member_settings = packs.member(pack, settings)
        assert got.id == member.id and set(member_settings.params) == {p.key for p in member.params}
        text = library.signal_text(pack, settings, "long")
        assert text.startswith(library.signal_text(member, member_settings, "long")[:-1])
    assert "option structure" in library.signal_text(F2, C.resolve_settings(F2, "15m", params={"source": "band_reversion"}), "short")


def test_a1_is_not_a_member_yet():
    """F1's order names A1 too; A1 (areas) is not in the library yet (TPL-Q23), so it is not a `source` (TPL-Q50)."""
    with pytest.raises(KeyError):
        library.get("A1")
    assert "A1" not in packs.MEMBERS["F1"].values() and set(packs.MEMBERS["F1"].values()) == {"D1", "D3"}
    assert set(packs.MEMBERS["F2"].values()) == {"A2", "E3"}


def test_the_pack_is_still_a_template_with_no_option_field():
    words = r"\b(strike|strikes|premium|expiry|lots?|spread|put|call|condor|order|quantity|target)\b"
    for t in (F1, F2):
        assert set(t.model_dump(by_alias=True)) == set(Template.model_fields) - {"schema_"} | {"schema"}
        for p in t.params:
            assert not re.search(words, p.key + " " + p.label, re.I), p.key
        for text in _texts(t):
            assert not re.search(words, text, re.I)


def test_the_cards_offer_the_new_members_and_the_adopt_pre_fill_has_no_option_field():
    from tests.test_contract_rules import _load_master
    from tests.test_tpl2_routes import FLAG, _org, client
    _load_master()
    org = _org("packs2", FLAG)
    cards = {c["id"]: c for c in client.get("/api/strategy-templates", headers=org["headers"]).json()["templates"]}
    assert len(cards["F1"]["params"][0]["choices"]) == 2 and len(cards["F2"]["params"][0]["choices"]) == 2
    assert "E2" in cards and cards["E2"]["family"] == "E"
    body = client.post("/api/strategy-templates/F1/adopt", headers=org["headers"],
                       json={"timeframe": "1h", "params": {"source": "ma_pullback"}}).json()
    assert body["strategy_id"] == "template:F1:2:1h:both:ma_pullback,,,,,,,,,,,"
    assert set(body["deployment"]) == {"strategy_id", "timeframe", "holding", "mode"} and body["deployment"]["mode"] == "PAPER"
    assert packs.STRUCTURE_NOTE in body["notes"]
    weekly = client.post("/api/strategy-templates/F1/adopt", headers=org["headers"],      # Weekly is for scans only (any template)
                         json={"timeframe": "1w", "params": {"source": "ma_pullback"}})
    assert weekly.status_code == 422
