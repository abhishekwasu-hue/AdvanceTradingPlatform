"""Strategy templates step 1: the proof template D1 (Supertrend flip) end to end - owner order section 7 D1 and the
section 8 gate: must-fire / must-not fixtures, the truncation test (no look-ahead over many t), the mirror test,
warm-up, the higher-timeframe filter, the settings bounds with property tests on random valid settings, the language
guard, and the adapter (scan series = what analyze() returns bar by bar, computed once per backtest run, no
build_signal, no targets)."""
import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st

from app.backtest.windows import WindowCursor, decision_time
from app.core.enums import SignalDirection
from app.indicators.prefix_cache import prefix_cache
from app.indicators.volatility import supertrend, supertrend_dir
from app.quality.lookahead import synthetic_bars
from app.screener.runtime import SymbolData, evaluate, resample
from app.screener.parser import parse
from app.strategy_templates import compile as C
from app.strategy_templates import engine, language, library
from app.strategy_templates.adapter import TemplateStrategy

D1 = library.get("D1")


def recipe(tf="15m", **params):
    return C.compile_recipe(D1, C.resolve_settings(D1, tf, params=params))


def run(df, tf="15m", **params):
    return engine.compute(recipe(tf, **params), {tf: df})


def _flips(df, period=10, mult=3.0):
    d = supertrend_dir(df, period, mult).to_numpy()
    prev = np.r_[np.nan, d[:-1]]
    return {int(t): int(d[t]) for t in np.flatnonzero((d != prev) & ~np.isnan(d) & ~np.isnan(prev))}


def leg_bars(path, start="2026-03-02 03:45", freq="15min"):
    """Bars along a hand-made close path (high / low half a point away)."""
    c = np.asarray(path, dtype=float)
    idx = pd.date_range(start, periods=len(c), freq=freq, tz="UTC")
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.5, "low": np.minimum(o, c) - 0.5, "close": c, "volume": 100.0}, index=idx)


# --- must fire / must not ---------------------------------------------------------------------------------------------

def test_must_fire_long_and_short_on_the_flip_bar():
    path = list(np.linspace(130, 100, 40)) + list(np.linspace(100, 135, 40)) + list(np.linspace(135, 105, 40))
    df = leg_bars(path)
    s = run(df)
    sig = {t: int(s.direction[t]) for t in s.signal_bars()}
    assert 1 in sig.values() and -1 in sig.values()
    assert sig == _flips(df)                                   # exactly the flip bars, the side of the new direction
    line = supertrend(df, 10, 3.0)["supertrend"]
    for t, d in sig.items():
        assert s.invalidation[t] == pytest.approx(line.iloc[t])  # the Supertrend line at the signal bar
        assert (s.invalidation[t] < df["close"].iloc[t]) if d > 0 else (s.invalidation[t] > df["close"].iloc[t])
        assert bool(s.exit_short[t]) if d > 0 else bool(s.exit_long[t])   # the flip ends the opposite idea
    first_long = min(t for t, d in sig.items() if d > 0)
    assert 40 <= first_long <= 55                              # after the turn up, not before


def test_must_not_fire_in_a_steady_trend_or_on_noise_that_never_flips():
    s = run(leg_bars(list(np.linspace(100, 160, 120))))
    assert s.signal_bars() == []
    calm = leg_bars([100 + (0.3 if i % 2 else -0.3) for i in range(120)])   # tiny noise inside the bands: no flip
    assert _flips(calm) == {} and run(calm).signal_bars() == []


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_signals_are_exactly_the_direction_changes(seed):
    """The near misses included: a close that touches or crosses the previous bar's line without the line switching
    sides gives nothing; every switch gives one signal (no conflict and no invalid geometry is possible for D1)."""
    df = synthetic_bars(500, tf="15m", seed=seed)
    s = run(df)
    assert {t: int(s.direction[t]) for t in s.signal_bars()} == _flips(df)
    assert len(s.signal_bars()) >= 3
    assert not ({engine.REASON_CONFLICT, engine.REASON_GEOMETRY} & set(s.reason))


def test_warm_up_is_no_trade():
    df = synthetic_bars(300, tf="15m", seed=2)
    r = recipe(period=20)
    s = engine.compute(r, {"15m": df})
    assert r.min_bars == 20 + 2 and s.warmup_bars == 21          # the line from bar 20, its previous value from bar 21
    assert set(s.reason[:21]) == {engine.REASON_WARMUP} and s.reason[21] != engine.REASON_WARMUP
    short = df.iloc[:15]
    assert engine.compute(r, {"15m": short}).signal_bars() == []


# --- the higher-timeframe filter ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [3, 6])
def test_context_filter_keeps_only_agreeing_flips(seed):
    df = synthetic_bars(800, tf="15m", seed=seed)
    plain = run(df)
    filtered = run(df, use_context=True, context_timeframe="1h")
    ctx = evaluate(parse("SupertrendDir(10, 3)@1h"), SymbolData("S", {"15m": df}), base_tf="15m")
    kept = set(filtered.signal_bars())
    assert kept <= set(plain.signal_bars()) and 0 < len(kept) < len(plain.signal_bars())
    for t in plain.signal_bars():
        agrees = ctx.iloc[t] == plain.direction[t]
        assert (t in kept) == bool(agrees), t
    # the 1h value is the last CLOSED hour's (S1): recomputed from the hours closed by each bar's close
    hourly = resample(df, "15m", "1h")
    hour_close = hourly.index + pd.Timedelta(hours=1)
    dirs = supertrend_dir(hourly, 10, 3.0)
    for t in sorted(kept):
        visible = dirs[hour_close <= df.index[t] + pd.Timedelta(minutes=15)]
        assert visible.iloc[-1] == filtered.direction[t]


def test_context_has_its_own_warm_up():
    df = synthetic_bars(200, tf="15m", seed=4)
    s = run(df, use_context=True, context_timeframe="1h")
    r = recipe(use_context=True, context_timeframe="1h")
    hours_needed = r.lookback["1h"]                              # the 1h Supertrend direction: 11 closed hours
    hour_close = resample(df, "15m", "1h").index + pd.Timedelta(hours=1)
    closed = np.array([(hour_close <= ts + pd.Timedelta(minutes=15)).sum() for ts in df.index])
    assert s.warmup_bars == int(np.argmax(closed >= hours_needed)) and s.warmup_bars > run(df).warmup_bars


# --- no look-ahead, determinism, mirror ---------------------------------------------------------------------------------

@pytest.mark.parametrize("params", [{}, {"period": 7, "multiplier": 2.0}, {"use_context": True, "context_timeframe": "1h"}])
def test_truncation_no_look_ahead_over_many_t(params):
    df = synthetic_bars(600, tf="15m", seed=11)
    full = run(df, **params)
    assert len(full.signal_bars()) >= 2
    for t in range(30, len(df), 9):
        part = run(df.iloc[:t + 1], **params)
        for j in [b for b in full.signal_bars() if b <= t] + [t]:
            assert part.direction[j] == full.direction[j], (t, j)
            assert np.isnan(part.invalidation[j]) == np.isnan(full.invalidation[j])
            assert part.instance_id(j) == full.instance_id(j)
        assert part.signal_bars() == [b for b in full.signal_bars() if b <= t]


def test_instance_ids_are_times_not_bar_numbers():
    df = synthetic_bars(400, tf="15m", seed=1)
    full = run(df)
    t = full.signal_bars()[-1]
    trailing = run(df.iloc[100:])                               # a trailing window: other bar numbers, the same instances
    assert trailing.instance_id(t - 100) == full.instance_id(t)


def test_mirror_short_equals_long_on_reflected_bars():
    """On prices reflected through a level (p -> C - p; highs and lows swap) the short signals are the long ones. The
    Supertrend's first direction is seeded long on both (app/indicators/volatility.supertrend), so the two agree from
    the first bar their directions mirror each other (asserted early) - from there on, bar by bar."""
    for seed in (1, 2, 3):
        df = synthetic_bars(500, tf="15m", seed=seed)
        df[["open", "high", "low", "close"]] = (df[["open", "high", "low", "close"]] * 20).round()      # whole ticks: exact
        c = float(df["high"].max() + df["low"].min())
        mirror = df.copy()
        mirror["open"], mirror["close"] = c - df["open"], c - df["close"]
        mirror["high"], mirror["low"] = c - df["low"], c - df["high"]
        a, b = supertrend_dir(df, 10, 3.0), supertrend_dir(mirror, 10, 3.0)
        synced = np.flatnonzero((a == -b).to_numpy())
        start = int(synced[0])
        assert start < 120 and ((a == -b).iloc[start:]).all()
        sa, sb = run(df), run(mirror)
        compared = 0
        for t in range(start + 1, len(df)):
            assert sa.direction[t] == -sb.direction[t], (seed, t)
            if sa.direction[t]:
                assert sa.invalidation[t] == pytest.approx(c - sb.invalidation[t])
                compared += 1
        assert compared >= 3


# --- settings: bounds, constraints, property tests ---------------------------------------------------------------------

@pytest.mark.parametrize("params, message", [
    ({"period": 0}, "Supertrend period must be between 1 and 100"),
    ({"period": 101}, "Supertrend period must be between 1 and 100"),
    ({"period": 7.5}, "Supertrend period must be a whole number"),
    ({"multiplier": 0.4}, "Supertrend multiplier must be between 0.5 and 10"),
    ({"multiplier": 11}, "Supertrend multiplier must be between 0.5 and 10"),
    ({"use_context": True, "context_timeframe": "15m"}, "The context timeframe must be higher than the signal timeframe."),
    ({"context_timeframe": "2h"}, "Context timeframe must be one of 15m, 30m, 1h, 1d, 1w"),
])
def test_settings_refused(params, message):
    with pytest.raises(C.TemplateError) as exc:
        C.resolve_settings(D1, "15m", params=params)
    assert message in exc.value.problems


def test_settings_at_their_bounds_run():
    df = synthetic_bars(300, tf="15m", seed=8)
    for params in ({"period": 1, "multiplier": 0.5}, {"period": 100, "multiplier": 10.0}, {"use_context": True, "context_timeframe": "1w"}):
        s = run(df, **params)
        assert set(np.unique(s.direction)) <= {-1, 0, 1}
    assert C.validate_template(D1) == []


TF_PAIRS = [(tf, ctx) for tf in D1.allowed_timeframes for ctx in D1.param("context_timeframe").choices
            if C._tf_minutes(ctx) > C._tf_minutes(tf)]


@hsettings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(period=st.integers(1, 100), mult=st.floats(0.5, 10.0, allow_nan=False), use_ctx=st.booleans(), pair=st.sampled_from(TF_PAIRS),
       seed=st.integers(0, 50))
def test_property_random_valid_settings(period, mult, use_ctx, pair, seed):
    tf, ctx = pair
    settings = C.resolve_settings(D1, tf, params={"period": period, "multiplier": mult, "use_context": use_ctx, "context_timeframe": ctx})
    df = synthetic_bars(260, tf=tf if tf != "1w" else "1d", seed=seed)
    base = df if tf != "1w" else resample(df, "1d", "1w")
    s = engine.compute(C.compile_recipe(D1, settings), {tf: base})
    flips = _flips(base, period, mult)
    for t in s.signal_bars():                                 # no signal without its flip, on the losing side of the close
        assert flips.get(t) == s.direction[t]
        close = base["close"].iloc[t]
        assert (s.invalidation[t] < close) if s.direction[t] > 0 else (s.invalidation[t] > close)
    if not use_ctx:                                           # every flip signals, unless S6 refused its geometry (a flip
        # bar can close beyond the new line when the multiplier is small: found by this property test in step 2)
        want = {t: d for t, d in flips.items() if s.reason[t] != engine.REASON_GEOMETRY}
        assert {t: int(s.direction[t]) for t in s.signal_bars()} == want


# --- the signal text -----------------------------------------------------------------------------------------------------

def test_signal_text_is_structure_words_only():
    for tf in D1.allowed_timeframes:
        for use_ctx in (False, True):
            s = C.Settings(tf, "both", {"period": 10, "multiplier": 3.0, "use_context": use_ctx, "context_timeframe": "1w"})
            for side, word in (("long", "bullish"), ("short", "bearish")):
                text = library.signal_text(D1, s, side, "1w" if use_ctx else None)
                assert word in text and language.guard(text) == []
                assert ("Weekly Supertrend direction agrees" in text) == use_ctx
    s = C.Settings("15m", "both", {"period": 10, "multiplier": 3.0, "use_context": False, "context_timeframe": "1d"})
    assert library.signal_text(D1, s, "long") == "Supertrend turned bullish on the 15-minute chart."


# --- the adapter: series = analyze(), once per run, no build_signal -----------------------------------------------------

def test_adapter_reads_the_series_bar_by_bar(monkeypatch):
    from app.strategy_engine.base import BaseStrategy

    def forbidden(*a, **k):
        raise AssertionError("templates never call build_signal (section 5)")
    monkeypatch.setattr(BaseStrategy, "build_signal", forbidden)
    df = synthetic_bars(400, tf="15m", seed=5)
    strat = TemplateStrategy(D1, C.resolve_settings(D1, "15m", params={"use_context": True, "context_timeframe": "1h"}))
    series = engine.compute(strat.recipe, {"15m": df})
    assert strat.timeframes == ["15min"] and strat.min_history() == {"15min": strat.recipe.min_bars}
    frames = {"15min": df}
    cursor = WindowCursor(frames, ["15min"])
    seen = []
    with prefix_cache(frames.values()):
        for i in range(len(df)):
            sig = strat.analyze(cursor.at(decision_time(df.index[i], "15min")), "S")
            seen.append(sig)
    assert strat.computations == 1                              # R3: one series per run, not one per bar
    for i, sig in enumerate(seen):
        d = int(series.direction[i])
        assert sig.direction == (SignalDirection.LONG if d > 0 else SignalDirection.SHORT if d < 0 else SignalDirection.NO_TRADE), i
        if d:
            assert sig.stop_loss == pytest.approx(series.invalidation[i]) and sig.entry == df["close"].iloc[i]
            assert sig.target1 is None and sig.target2 is None and sig.risk_reward is None
            assert sig.reasons[0].startswith("Supertrend turned") and "agrees" in sig.reasons[0]
            # TPL-Q2 (step 2): the provenance is on the signal for the server, never in the reasons a user reads
            assert sig.provenance is not None and sig.provenance["settings"] == strat.recipe.params_hash
            assert sig.provenance["setup"] == series.instance_id(i) and sig.provenance["strategy_id"] == strat.id
            assert not any(strat.recipe.params_hash in r or "Template" in r for r in sig.reasons)
            assert "provenance" not in sig.model_dump() and "provenance" not in sig.model_dump_json()
    assert sum(1 for x in seen if x.is_tradeable) == len(series.signal_bars()) >= 1
    assert seen[0].reasons == ["Not enough history for this template yet"]


def test_adapter_outside_a_run_computes_the_bars_given():
    df = synthetic_bars(300, tf="15m", seed=2)
    strat = TemplateStrategy(D1, C.resolve_settings(D1, "15m"))
    series = engine.compute(strat.recipe, {"15m": df})
    t = series.signal_bars()[-1]
    sig = strat.analyze({"15min": df.iloc[:t + 1]}, "S")
    assert sig.is_tradeable and sig.stop_loss == pytest.approx(series.invalidation[t])
    side = sig.direction
    later = series.exit_long if side == SignalDirection.LONG else series.exit_short
    exits = [j for j in range(t + 1, len(df)) if later[j]]
    if exits:
        assert strat.exit_signal({"15min": df.iloc[:exits[0] + 1]}, side)
    with pytest.raises(ValueError, match="scans only"):
        TemplateStrategy(D1, C.resolve_settings(D1, "1w"))
