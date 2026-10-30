"""M55 T5: "Pullback to a demand / supply area" (strategy template A1) - the ported level gate (scan Then) as a template.

Public, synthetic tests only (the random-walk bars of the look-ahead harness, NSE-shaped 15-minute sessions): the series
is the gate itself (each entry equals `gate.safe_evaluate` on the closed bars up to that candle, as the runner calls it),
must-fire / must-not-fire, mirror (the short side mirrors the long side), no look-ahead (truncation and future
perturbations, cached and uncached), scan = backtest = worker (the TPL-1 / G parity harness), every number a setting with
the ported default, no rule nobody stated (no target / size field, the R:R room check only as the gate's own setting),
and the signal text in structure words. Signals only, PAPER only.
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd
import pytest

from app.core.models import RiskConfig
from app.price_action.level_gate import gate as G
from app.price_action.level_gate import series as S
from app.price_action.level_gate.settings import BREAK_DEFAULTS, GATE_DEFAULTS, METHOD_DEFAULTS
from app.quality import lookahead as LA
from app.screener.parser import parse
from app.screener.runtime import SymbolData, evaluate
from app.screener.then import templates as then_templates
from app.strategy_templates import compile as C
from app.strategy_templates import engine, language, library, parity, wave_parity
from app.strategy_templates.adapter import TemplateStrategy

VENUE_24X7 = "BTCINR"          # as tests/test_wave4_templates.py: no INTRADAY cut-off / square-off in the backtest
RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5)
A1 = library.get("A1")
PIVOT = 200.0
# Fixtures: the look-ahead harness's random walk (NSE-shaped 15-minute sessions); the gate fires once on each.
LONG_SEED, LONG_BAR, LONG_STOP = 2, 908, 96.38
SHORT_SEED, SHORT_BAR = 1, 1353
N_BARS = 1500


def bars_of(seed, n=N_BARS):
    return LA.synthetic_bars(n, tf="15m", seed=seed)


def mirror(bars):
    return pd.DataFrame({"open": PIVOT - bars["open"], "high": PIVOT - bars["low"], "low": PIVOT - bars["high"],
                         "close": PIVOT - bars["close"], "volume": bars["volume"]}, index=bars.index)


def recipe(tf="15m", **params):
    return C.compile_recipe(A1, C.resolve_settings(A1, tf, params=params))


def signals(bars, tf="15m", **params):
    s = engine.compute(recipe(tf, **params), {tf: bars})
    return {t: (int(s.direction[t]), round(float(s.invalidation[t]), 6)) for t in s.signal_bars()}


@pytest.fixture(autouse=True)
def _fresh_cache():
    S.clear_cache()
    yield
    S.CACHE_ON = True
    S.clear_cache()


# ============================================================================================ must fire / must not fire
def test_must_fire_long_and_short():
    assert signals(bars_of(LONG_SEED)) == {LONG_BAR: (1, LONG_STOP)}
    got = signals(bars_of(SHORT_SEED))
    assert list(got) == [SHORT_BAR] and got[SHORT_BAR][0] == -1
    close = float(bars_of(SHORT_SEED)["close"].iloc[SHORT_BAR])
    assert got[SHORT_BAR][1] > close                                    # the invalidation is on the losing side


def _ported_entry(bars, t):
    """What the scan Then runner computes on the closed bars up to t: every area's gate result, as it calls it."""
    f = S._zone_frame(bars.iloc[: t + 1])
    day = f["timestamp"].iloc[t].normalize()
    s0 = int(np.flatnonzero((f["timestamp"].dt.normalize() == day).to_numpy())[0])
    gs, s3, es = S.split_settings(None)
    ctx = S._contexts(f, [day], gs)[day]
    areas = S._areas(S._zone_frames(bars.iloc[: t + 1], "15m"), pd.Timestamp(f["timestamp"].iloc[s0]))
    out = []
    for area in areas:
        r = G.safe_evaluate(f, area, ctx, [a for a in areas if a != area], s0, gs, s3, es)
        if r.get("entry"):
            out.append((r["side"], r["sl"]))
    return ctx, out


def test_the_entry_is_the_ported_gate_on_the_closed_bars():
    """The series' entry equals gate.safe_evaluate on ALL the closed bars up to the candle (no margin cut), as the runner."""
    bars = bars_of(LONG_SEED)
    ctx, found = _ported_entry(bars, LONG_BAR)
    assert ctx["ok"] and G.BULL in ctx["sides"]
    assert found == [(G.BULL, LONG_STOP)]
    for t in (LONG_BAR - 1, LONG_BAR + 1):
        assert _ported_entry(bars, t)[1] == []


def test_must_not_fire():
    bars = bars_of(LONG_SEED)
    # the room check is the gate's own setting: more room asked than there is -> no signal
    assert signals(bars, min_rr=10.0) == {}
    # the commitment candle closes back inside the area instead -> no signal on that candle
    edited = bars.copy()
    o = float(edited["open"].iloc[LONG_BAR])
    edited.iloc[LONG_BAR, :4] = (o, o + 0.05, o - 0.3, o - 0.25)
    assert LONG_BAR not in signals(edited)
    # fewer than 30 Daily candles before the day: the trend is unclear, no entries at all
    assert signals(bars.iloc[LONG_BAR - 25 * 26: LONG_BAR + 5]) == {}
    # the Daily structure against the side: the mirrored history allows only the other side at that candle
    assert all(d == -1 for d, _ in signals(mirror(bars)).values())


def test_mirror_short_mirrors_long():
    for seed in (LONG_SEED, SHORT_SEED):
        bars = bars_of(seed)
        got, mirrored = signals(bars), signals(mirror(bars))
        assert got and mirrored == {t: (-d, round(PIVOT - stop, 6)) for t, (d, stop) in got.items()}


# ============================================================================================ causal: no look-ahead
def _case(name, text, min_t, samples):
    ast = parse(text)
    return LA.CausalCase(name=name, fn=lambda f: evaluate(ast, SymbolData("S", {"15m": f}), base_tf="15m"), bars="15m",
                         samples=samples, min_t=min_t)


@pytest.mark.parametrize("text", ['AreaGate("up")', 'AreaGateRef("invalidation", "up")'])
def test_no_lookahead_cached(text):
    bars = bars_of(LONG_SEED, 1000)
    assert LA.check(_case("A1 " + text, text, LONG_BAR - 3, 6), bars) == []


def test_no_lookahead_uncached():
    S.CACHE_ON = False
    bars = bars_of(SHORT_SEED, SHORT_BAR + 40)
    assert LA.check(_case("A1 uncached", 'AreaGateRef("invalidation", "down")', SHORT_BAR - 1, 2), bars) == []


def test_a_cache_hit_equals_a_recomputation():
    bars = bars_of(LONG_SEED)
    first = S.gate_series(bars, "15m")
    S.CACHE_ON = False
    again = S.gate_series(bars, "15m")
    for a, b in zip((first.long, first.short, first.long_stop, first.short_stop), (again.long, again.short, again.long_stop, again.short_stop)):
        assert np.array_equal(a, b, equal_nan=True)


# ============================================================================================ scan = backtest = worker
@pytest.mark.parametrize("seed, bar", [(LONG_SEED, LONG_BAR), (SHORT_SEED, SHORT_BAR)])
def test_parity_scan_backtest_worker(seed, bar):
    bars = bars_of(seed)
    strat = TemplateStrategy(A1, C.resolve_settings(A1, "15m"))
    report = wave_parity.run(strat, bars, VENUE_24X7, RISK, worker_bars=sorted({bar - 1, bar, bar + 1, 800, len(bars) - 1}))
    assert report.problems == [] and report.signals == [bars.index[bar]]
    assert report.asked["backtest"] > 500 and report.trades == 1


def test_the_history_to_fetch_covers_the_daily_trend():
    for tf, per_session in (("15m", 25), ("5m", 75)):
        r = recipe(tf)
        assert r.lookback[tf] >= 31 * per_session and parity.fetch_bars(r) >= r.lookback[tf] * parity.SAFETY_FACTOR


# ============================================================================================ settings and rules
def test_every_number_is_a_setting_with_the_ported_default():
    by_key = {p.key: p for p in A1.params}
    want = {**{k: GATE_DEFAULTS[k] for k in S.GATE_KEYS}, **{k: METHOD_DEFAULTS[k] for k in S.METHOD_KEYS}, **BREAK_DEFAULTS}
    assert set(by_key) == set(want) == set(S.SETTING_KEYS)
    for key, default in want.items():
        assert by_key[key].default == default, key
    for key, bound in then_templates.GATE_BOUNDS.items():          # the scan Then template's bounds for the gate's own keys
        if bound is not bool:
            assert (by_key[key].min, by_key[key].max) == bound, key
    # no number in the recipe that is not a setting
    for part in (A1.setup.long, A1.setup.short, A1.invalidation.long, A1.invalidation.short):
        assert not re.search(r"=\s*-?\d", part), part
        assert set(re.findall(r"(\w+)=\$(\w+)", part)) == {(k, k) for k in S.SETTING_KEYS}


def test_a_setting_reaches_the_gate():
    assert S.split_settings({"min_rr": 2, "commit_beyond": "extreme", "break_accept_closes": 4})[0]["min_rr"] == 2.0
    assert S.split_settings({"commit_beyond": "extreme"})[1]["commit_beyond"] == "extreme"
    assert S.split_settings({"break_accept_closes": 4})[2]["break_accept_closes"] == 4
    assert S.split_settings(None) == (G.gate_settings(None), dict(METHOD_DEFAULTS), dict(BREAK_DEFAULTS))


def test_no_rule_nobody_stated():
    raw = json.loads((library.TEMPLATES_DIR / "A1.json").read_text(encoding="utf-8"))
    text = json.dumps(raw).lower()
    for word in ("target", "lot", "strike", "premium", "quantity"):
        assert not re.search(rf"\b{word}s?\b", text), word
    assert raw["trigger"] is None and raw["exit_signal"] is None and raw.get("context") is None
    assert A1.allowed_timeframes == S.SIGNAL_TFS == tuple(then_templates.CONFIRM_TFS)
    # the inert clock keys and the sizing / retry keys are not settings (and not read)
    assert not set(S.METHOD_INERT + S.GATE_NOT_SIGNAL) & {p.key for p in A1.params}


def test_signal_text_in_structure_words():
    settings = C.resolve_settings(A1, "15m")
    for side in ("long", "short"):
        text = library.signal_text(A1, settings, side)
        assert language.guard(text) == []
        assert not re.search(r"\d", text.replace("15-minute", ""))          # no price: only the timeframe's name
        assert not re.search(r"\b(buy|sell|target|stop|stop-loss|entry|exit)\b", text.lower())
        assert ("demand" if side == "long" else "supply") in text and "15-minute" in text


def test_the_template_is_listed_and_compiles_on_its_timeframes():
    assert "A1" in {t.id for t in library.templates()}
    for tf in A1.allowed_timeframes:
        r = recipe(tf)
        assert r.required_flags == set()
    with pytest.raises(C.TemplateError):
        recipe("1h")
