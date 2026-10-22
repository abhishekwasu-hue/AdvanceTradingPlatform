"""Strategy templates step 2: the templates D2 (moving-average crossover), B1 (range breakout) and B3 (channel breakout)
against the owner order's section 8 gate - must fire / must not, warm-up, the truncation test (no look-ahead over many t),
the mirror test, scan = backtest = options backtest = worker on one trailing window (the TPL-1 harness), property tests
on random valid settings, the language guard - plus the step's wiring: the template strategy id (canonical, the stored
provenance), the provenance kept off the user-visible reasons (TPL-Q2), the resolver behind the flag, the worker's
history fetch (R6) and the adoption pre-fill."""
import asyncio

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st

from app.backtest.options_engine import OptionBacktestConfig
from app.core.enums import OptionStrategy
from app.core.models import RiskConfig
from app.quality.lookahead import synthetic_bars
from app.screener.parser import parse
from app.screener.runtime import SymbolData, evaluate
from app.strategy_templates import compile as C
from app.strategy_templates import engine, ids, language, library, parity
from app.strategy_templates.adapter import ENGINE_LABEL, MAX_HISTORY_DAYS, TemplateStrategy

D2, B1, B3 = library.get("D2"), library.get("B1"), library.get("B3")
RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5)
K = 400.0                                                     # mirror axis: price -> K - price (a 0.05 tick stays on the tick)


def run(template, df, tf="15m", **params):
    return engine.compute(C.compile_recipe(template, C.resolve_settings(template, tf, params=params)), {tf: df})


def ev(text, df, tf="15m"):
    return evaluate(parse(text), SymbolData("S", {tf: df}), base_tf=tf)


def mirror(df):
    out = df.copy()
    out["open"], out["close"] = K - df["open"], K - df["close"]
    out["high"], out["low"] = K - df["low"], K - df["high"]
    return out


def path_bars(path, start="2026-03-02 03:45", freq="15min", wick=0.5, volume=100.0):
    c = np.asarray(path, dtype=float)
    idx = pd.date_range(start, periods=len(c), freq=freq, tz="UTC")
    o = np.r_[c[0], c[:-1]]
    vol = np.full(len(c), volume) if np.isscalar(volume) else np.asarray(volume, dtype=float)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + wick, "low": np.minimum(o, c) - wick, "close": c, "volume": vol},
                        index=idx)


def signals(s):
    return {t: int(s.direction[t]) for t in s.signal_bars()}


# ============================================================================================================== D2
D2_FAST = {"invalidation": "slow_average"}                    # no swing warm-up: the fixtures stay short


def _crosses(df, fast=9, slow=21, kind="EMA"):
    f, s = (ev(f'MA(close, {n}, "{kind}")', df) for n in (fast, slow))
    up = (f.shift(1) <= s.shift(1)) & (f > s)
    down = (f.shift(1) >= s.shift(1)) & (f < s)
    return {int(t): 1 for t in np.flatnonzero(up.to_numpy())} | {int(t): -1 for t in np.flatnonzero(down.to_numpy())}


def test_d2_must_fire_on_the_cross_bar_with_the_slow_average_as_invalidation():
    df = path_bars(list(np.linspace(130, 100, 40)) + list(np.linspace(100, 135, 40)) + list(np.linspace(135, 105, 40)))
    s = run(D2, df, **D2_FAST)
    got = signals(s)
    assert 1 in got.values() and -1 in got.values()
    assert got == {t: d for t, d in _crosses(df).items() if t >= s.warmup_bars}
    slow = ev('MA(close, 21, "EMA")', df)
    for t, d in got.items():
        assert s.invalidation[t] == pytest.approx(slow.iloc[t])
        assert bool(s.exit_short[t]) if d > 0 else bool(s.exit_long[t])          # the cross ends the opposite idea
    assert 40 <= min(t for t, d in got.items() if d > 0) <= 55


@pytest.mark.parametrize("kind", ["EMA", "SMA", "WMA", "HMA"])
def test_d2_every_average_type_fires_on_its_own_crosses(kind):
    df = synthetic_bars(500, tf="15m", seed=4)
    s = run(D2, df, ma_type=kind, fast=5, slow=20, **D2_FAST)
    crosses = _crosses(df, 5, 20, kind)
    assert signals(s) and set(signals(s).items()) <= set(crosses.items())
    for t, d in crosses.items():                               # every cross after the warm-up signals unless S6 refused it
        if t >= s.warmup_bars:
            assert t in signals(s) or s.reason[t] == engine.REASON_GEOMETRY, t


def test_d2_must_not_fire_without_a_cross():
    assert run(D2, path_bars(list(np.linspace(100, 160, 150))), **D2_FAST).signal_bars() == []      # a steady trend
    flat = path_bars([100.0] * 150)                                                                   # equal averages: no cross
    assert run(D2, flat, **D2_FAST).signal_bars() == []


def test_d2_swing_invalidation_and_s6():
    """The latest confirmed swing low (long) / high (short) on the cross bar; when it is not on the losing side of the
    close there is no signal (never an older swing instead)."""
    for seed in (1, 2, 3, 5):
        df = synthetic_bars(800, tf="15m", seed=seed)
        s = run(D2, df, swing_degree=0)
        low, high = ev("SwingLow(0)", df), ev("SwingHigh(0)", df)
        assert s.warmup_bars >= 99                             # the degree's history (SWING_DEGREE_MIN_BARS[0])
        for t, d in _crosses(df).items():
            if t < s.warmup_bars:
                continue
            level = (low if d > 0 else high).iloc[t]
            ok = np.isfinite(level) and (level < df["close"].iloc[t] if d > 0 else level > df["close"].iloc[t])
            if ok:
                assert signals(s).get(t) == d and s.invalidation[t] == pytest.approx(level)
            else:
                assert t not in signals(s) and s.reason[t] in (engine.REASON_GEOMETRY, engine.REASON_NO_LEVEL)


def test_d2_trend_filter_keeps_only_crosses_on_the_trend_side():
    df = synthetic_bars(900, tf="15m", seed=6)
    plain = signals(run(D2, df, **D2_FAST))
    kept = signals(run(D2, df, use_trend=True, trend_period=100, **D2_FAST))
    trend = ev('MA(close, 100, "EMA")', df)
    assert kept and set(kept.items()) <= set(plain.items()) and len(kept) < len(plain)
    for t, d in plain.items():
        if t >= 99:
            agrees = df["close"].iloc[t] > trend.iloc[t] if d > 0 else df["close"].iloc[t] < trend.iloc[t]
            assert (t in kept) == bool(agrees), t


def test_d2_settings_constraints():
    with pytest.raises(C.TemplateError) as exc:
        C.resolve_settings(D2, "15m", params={"fast": 30, "slow": 20})
    assert "The fast average must have fewer bars than the slow one." in exc.value.problems
    with pytest.raises(C.TemplateError) as exc:
        C.resolve_settings(D2, "15m", params={"use_trend": True, "slow": 50, "trend_period": 40})
    assert "The trend average must have more bars than the slow one." in exc.value.problems
    C.resolve_settings(D2, "15m", params={"use_trend": False, "slow": 50, "trend_period": 40})     # the rule applies only when on
    for bad in ({"ma_type": "KAMA"}, {"invalidation": "target"}, {"swing_degree": 4}, {"fast": 1}):
        with pytest.raises(C.TemplateError):
            C.resolve_settings(D2, "15m", params=bad)


@hsettings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(kind=st.sampled_from(["EMA", "SMA", "WMA", "HMA"]), fast=st.integers(2, 30), gap=st.integers(1, 40),
       inval=st.sampled_from(["swing", "slow_average"]), degree=st.integers(0, 1), trend=st.booleans(), seed=st.integers(0, 30))
def test_d2_property_random_valid_settings(kind, fast, gap, inval, degree, trend, seed):
    params = {"ma_type": kind, "fast": fast, "slow": fast + gap, "invalidation": inval, "swing_degree": degree,
              "use_trend": trend, "trend_period": fast + gap + 10}
    df = synthetic_bars(400, tf="15m", seed=seed)
    s = run(D2, df, **params)
    crosses = _crosses(df, fast, fast + gap, kind)
    for t, d in signals(s).items():                            # no signal without its cross, on the losing side of the close
        assert crosses.get(t) == d
        assert (s.invalidation[t] < df["close"].iloc[t]) if d > 0 else (s.invalidation[t] > df["close"].iloc[t])


# ============================================================================================================== B1
def _range_then(move, n=60, base=100.0, volume=None):
    """A flat range (closes at `base`, high / low one point away), then the closes in `move`."""
    path = [base] * n + list(move)
    return path_bars(path, wick=1.0, volume=volume if volume is not None else 100.0)


@pytest.mark.parametrize("mode", ["fixed", "auto"])
def test_b1_must_fire_on_the_first_close_beyond_the_range_both_sides(mode):
    up = _range_then([108.0, 109.0, 110.0])
    s = run(B1, up, range_mode=mode)
    assert signals(s) == {60: 1}                               # one signal per leg: the first close beyond only
    level = ev(f'RangeBreakoutRef("level", "up", 20, 0.2, 0, "{mode}")', up).iloc[60]
    atr_prev = ev("ATR(14)", up).iloc[59]
    assert s.invalidation[60] == pytest.approx(level - 1.0 * atr_prev)        # default: just beyond the broken edge
    down = _range_then([92.0, 91.0, 90.0])
    assert signals(run(B1, down, range_mode=mode)) == {60: -1}
    assert signals(run(B1, mirror(up), range_mode=mode)) == {60: -1}


def test_b1_invalidation_choices_are_the_breakout_stop_references():
    df = _range_then([108.0, 109.0])
    for choice, want in (("stop_base", 99.0), ("stop_midpoint", 100.0)):
        s = run(B1, df, range_mode="fixed", invalidation=choice)
        assert s.invalidation[60] == pytest.approx(want), choice
    s = run(B1, df, range_mode="fixed", stop_mult=0.0)
    assert s.invalidation[60] == pytest.approx(101.0)          # no buffer: the broken edge itself


def test_b1_must_not_fire_on_a_wick_a_near_miss_or_without_the_volume_asked_for():
    wick = _range_then([100.0, 100.0])
    wick.loc[wick.index[60], "high"] = 112.0                   # a wick through the top, the close back inside
    assert run(B1, wick, range_mode="fixed").signal_bars() == []
    near = _range_then([101.2])                                # beyond the range high (101) by less than k = 0.2 ATR (ATR 2)
    assert run(B1, near, range_mode="fixed").signal_bars() == []
    assert signals(run(B1, near, range_mode="fixed", k=0.0)) == {60: 1}
    quiet = _range_then([108.0], volume=[100.0] * 61)          # volume asked for (2x average), the break has none
    assert run(B1, quiet, range_mode="fixed", vol_mult=2.0).signal_bars() == []
    loud = _range_then([108.0], volume=[100.0] * 60 + [500.0])
    assert signals(run(B1, loud, range_mode="fixed", vol_mult=2.0)) == {60: 1}


def test_b1_exit_signal_is_a_close_back_inside_the_range():
    df = _range_then([108.0, 109.0, 100.5, 108.0])
    s = run(B1, df, range_mode="fixed")
    assert signals(s) == {60: 1}
    assert [bool(x) for x in s.exit_long[60:]] == [False, False, True, False]


def test_b1_warm_up():
    df = synthetic_bars(300, tf="15m", seed=3)
    s = run(B1, df)
    assert s.warmup_bars == 49 and set(s.reason[:49]) == {engine.REASON_WARMUP}
    assert all(t >= 49 for t in s.signal_bars())


@hsettings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(mode=st.sampled_from(["fixed", "auto"]), n=st.integers(5, 60), k=st.floats(0.0, 1.0), inval=st.sampled_from(B1.param("invalidation").choices),
       mult=st.floats(0.0, 3.0), seed=st.integers(0, 30))
def test_b1_property_random_valid_settings(mode, n, k, inval, mult, seed):
    df = synthetic_bars(400, tf="15m", seed=seed)
    s = run(B1, df, range_mode=mode, range_n=n, k=k, invalidation=inval, stop_mult=mult)
    for side, sign in (("up", 1), ("down", -1)):
        fired = ev(f'RangeBreakout("{side}", {n}, {k!r}, 0, "{mode}")', df).to_numpy()
        for t, d in signals(s).items():
            if d == sign:
                assert fired[t]                                # no signal without its breakout
                assert (s.invalidation[t] < df["close"].iloc[t]) if d > 0 else (s.invalidation[t] > df["close"].iloc[t])


# ============================================================================================================== B3
@pytest.mark.parametrize("seed", [1, 2, 3, 7])
def test_b3_fires_exactly_on_the_channel_breakouts(seed):
    df = synthetic_bars(600, tf="15m", seed=seed)
    s = run(B3, df)
    want = {}
    for side, sign in (("up", 1), ("down", -1)):
        fired = ev(f'TrendlineBreakout("{side}", "channel", 2, 0.2)', df)
        stop = ev(f'LineBreakoutRef("stop_level_atr", "{side}", "channel", 2, 0.2, 1)', df)
        for t in np.flatnonzero(fired.to_numpy()):
            if t >= s.warmup_bars:
                want[int(t)] = sign
                if t in signals(s):
                    assert s.invalidation[t] == pytest.approx(stop.iloc[t])
    assert want, "the fixture must carry channel breakouts"
    assert {t: d for t, d in signals(s).items()} == {t: d for t, d in want.items() if s.reason[t] != engine.REASON_GEOMETRY}


def test_b3_near_miss_a_larger_break_distance_never_adds_a_signal():
    df = synthetic_bars(600, tf="15m", seed=2)
    plain, strict = signals(run(B3, df)), signals(run(B3, df, k=3.0))
    assert set(strict.items()) <= set(plain.items()) and len(strict) < len(plain)


def test_b3_exit_and_warm_up():
    df = synthetic_bars(600, tf="15m", seed=1)
    s = run(B3, df)
    assert s.warmup_bars == 124 and all(t >= 124 for t in s.signal_bars())
    held = ev('LineBreakoutRef("held_level", "up", "channel", 2, 0.2)', df)
    want = (df["close"] < held).to_numpy() & (np.arange(len(df)) >= s.warmup_bars)
    assert (s.exit_long == want).all()


# ============================================================================================ shared gate: all three
CASES = [(D2, {}), (D2, D2_FAST), (D2, {"swing_degree": 0, "use_trend": True}), (B1, {}), (B1, {"range_mode": "fixed", "invalidation": "stop_base"}),
         (B3, {}), (B3, {"invalidation": "stop_midpoint"})]
IDS = [f"{t.id}-{i}" for i, (t, _) in enumerate(CASES)]


@pytest.mark.parametrize("template, params", CASES, ids=IDS)
@pytest.mark.parametrize("seed", [1, 2])
def test_mirror_short_equals_long_on_mirrored_prices(template, params, seed):
    df = synthetic_bars(600, tf="15m", seed=seed)
    s, m = run(template, df, **params), run(template, mirror(df), **params)
    assert signals(s), "the fixture must carry signals"
    assert signals(m) == {t: -d for t, d in signals(s).items()}
    for t in s.signal_bars():
        assert K - m.invalidation[t] == pytest.approx(s.invalidation[t])
    assert (m.exit_short == s.exit_long).all() and (m.exit_long == s.exit_short).all()


@pytest.mark.parametrize("template, params", CASES, ids=IDS)
def test_truncation_no_look_ahead_over_many_t(template, params):
    df = synthetic_bars(520, tf="15m", seed=3)
    full = run(template, df, **params)
    marks = sorted(set(full.signal_bars()) | {t + 1 for t in full.signal_bars()} | set(range(full.warmup_bars, len(df), 37)))
    for t in [x for x in marks if x < len(df)]:
        part = run(template, df.iloc[:t + 1], **params)
        np.testing.assert_array_equal(part.direction, full.direction[:t + 1], err_msg=f"t={t}")
        np.testing.assert_allclose(part.invalidation, full.invalidation[:t + 1], equal_nan=True, err_msg=f"t={t}")
        assert [part.instance_id(i) for i in part.signal_bars()] == [full.instance_id(i) for i in full.signal_bars() if i <= t]


@pytest.mark.parametrize("template, params, seed", [(D2, D2_FAST, 3), (D2, {"swing_degree": 0}, 4), (B1, {}, 5), (B3, {}, 1)],
                         ids=["D2-slow", "D2-swing", "B1", "B3"])
def test_scan_equals_backtest_equals_options_equals_worker(template, params, seed):
    strat = TemplateStrategy(template, C.resolve_settings(template, "15m", params=params))
    window = synthetic_bars(900, tf="15m", seed=seed).iloc[-500:]                 # a fixed TRAILING window (R6)
    window = window.copy()
    window[["open", "high", "low", "close"]] *= 240.0                                # index-like prices for the option ladder
    scan = parity.scan_answers(strat.recipe, window)
    assert sum(1 for a in scan.values() if a.direction) >= 2, "the fixture must carry signals to compare"
    answers, trades = parity.backtest_answers(strat, window, "S", RISK)
    assert parity.compare(scan, answers, "backtest") == []
    assert trades and parity.compare_trades(scan, trades, lambda ts: ts) == []
    config = OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, spread_width=2)
    assert parity.compare(scan, parity.options_answers(strat, window, "NIFTY 50", config, RISK), "options") == []
    signal_at = [i for i, a in enumerate(scan.values()) if a.direction]
    bars = sorted({j for i in signal_at for j in (i - 1, i, i + 1) if 0 <= j < len(window)} | set(range(0, len(window), 25)) | {len(window) - 1})
    worker = parity.worker_answers(strat, window, "S", bars)
    assert parity.compare(scan, worker, "worker") == []
    assert {ts for ts, a in scan.items() if a.direction} <= {ts for ts, a in worker.items() if a.direction}


@pytest.mark.parametrize("template", [D2, B1, B3], ids=["D2", "B1", "B3"])
def test_library_template_validates_and_its_text_passes_the_guard(template):
    assert C.validate_template(template) == []
    for tf in template.allowed_timeframes:
        for p in template.params:
            assert language.guard(p.label) == [] or p.type != "bool"            # labels are words (numbers only in values)
        settings = C.resolve_settings(template, tf, params={"use_trend": True} if template.id == "D2" else {})
        for side in ("long", "short"):
            text = library.signal_text(template, settings, side)
            assert text and language.guard(text) == [], text
    assert all(p.default is not None for p in template.params)                    # every number is a setting with a default
    for p in template.params:
        if p.type in ("int", "float"):
            assert p.min is not None and p.max is not None and p.min <= p.default <= p.max
        if p.choices:
            assert len(p.choice_labels) == len(p.choices)                       # the form shows words, not internal values


def test_no_number_is_written_into_a_template_rule():
    """Owner rule: no fixed numbers in reading rules - every number is a setting ($name). Only the side words and the
    companion's own choice of output are literals."""
    import re
    for template in (D2, B1, B3):
        texts = []
        for part in (template.setup, template.invalidation, template.exit_signal, template.context):
            if part is None:
                continue
            variants = [v for v in part.variants.values() if v] if hasattr(part, "variants") else [part]
            texts += [x for v in variants for x in (v.long, v.short)]
        for text in texts:
            assert not re.search(r"(?<![\w$])\d", text), (template.id, text)


# ============================================================================================ wiring: ids, provenance
@pytest.mark.parametrize("template_id", ["D1", "D2", "B1", "B3"])
def test_template_ids_round_trip_and_are_canonical(template_id):
    template = library.get(template_id)
    for tf in [t for t in template.allowed_timeframes if t in ENGINE_LABEL]:
        sid, settings = ids.adopt(template, tf)
        assert sid.startswith(f"template:{template_id}:{template.version}:{tf}:both:")
        assert ids.decode(sid) == (template, settings)
    worst = {}
    for p in template.params:                                  # the longest each value can be written
        if p.type == "float":
            worst[p.key] = round(p.max - 1 / 3, 6) if p.max - 1 / 3 >= p.min else p.max
        elif p.type == "int":
            worst[p.key] = int(p.max) if p.max != p.default else int(p.min)
        elif p.type == "bool":
            worst[p.key] = not p.default
        else:
            worst[p.key] = max((c for c in p.choices if c != p.default), key=len, default=p.default)
    try:
        sid, _ = ids.adopt(template, template.allowed_timeframes[0], "short", worst)
    except C.TemplateError as exc:                             # a constraint between the extremes: still within the length
        assert "too long" not in " ".join(exc.problems)
    else:
        assert len(sid) <= ids.MAX_LENGTH


def test_template_ids_refuse_what_encode_would_not_write():
    sid, _ = ids.adopt(D2, "15m", params={"fast": 5})
    assert sid == "template:D2:1:15m:both:,5,,,,,"
    for bad in ("template:D2:1:15m:both:,9,,,,,",                # the default written out: not canonical
                "template:D2:2:15m:both:,,,,,,",                  # another version: adopt again
                "template:D2:1:15m:both:,,,,,",                   # a value missing
                "template:D2:1:2h:both:,,,,,,",                   # a timeframe the template does not allow
                "template:D2:1:15m:both:,30,,,,,",                # fast above slow: the constraint
                "template:Z9:1:15m:both:", "template:D2", "custom:12"):
        with pytest.raises(KeyError):
            ids.decode(bad)
    with pytest.raises(KeyError, match="adopt it again"):
        ids.decode("template:D2:2:15m:both:,,,,,,")


def test_floats_are_kept_to_six_decimals_and_long_ids_are_refused():
    sid, settings = ids.adopt(B1, "15m", params={"k": 0.1 + 0.2})
    assert settings.params["k"] == 0.3 and ",0.3," in sid
    assert ids.decode(sid)[1] == settings


def test_signal_provenance_is_off_the_reasons_and_never_serialised():
    """TPL-Q2: no internal id, settings hash or setup instance in what a user reads; the server keeps them on the signal
    (and the strategy id, stored on every deployment / order / trade, carries the template, version and settings)."""
    df = synthetic_bars(600, tf="15m", seed=2)
    strat = TemplateStrategy(B1, C.resolve_settings(B1, "15m"))
    series = engine.compute(strat.recipe, {"15m": df})
    t = series.signal_bars()[0]
    sig = strat.analyze({"15min": df.iloc[:t + 1]}, "S")
    assert sig.is_tradeable and sig.strategy_id == strat.id == "template:B1:1:15m:both:,,,,,"
    assert sig.provenance == {"template": "B1", "version": "1", "settings": strat.recipe.params_hash,
                              "setup": series.instance_id(t), "strategy_id": strat.id}
    text = " ".join(sig.reasons)
    for internal in ("Template", strat.recipe.params_hash, "@", "template:", "setup"):
        assert internal not in text
    assert all(language.guard(r) == [] for r in sig.reasons)
    assert "provenance" not in sig.model_dump() and "provenance" not in sig.model_dump_json()


def test_history_days_cover_the_fetch_bars_r6():
    for template, tf, params in ((D2, "15m", {}), (D2, "1h", {}), (B1, "5m", {}), (B3, "30m", {}), (D2, "1d", {})):
        strat = TemplateStrategy(template, C.resolve_settings(template, tf, params=params))
        bars, days = strat.history_bars(), strat.history_days()
        assert bars == parity.fetch_bars(strat.recipe) >= strat.recipe.min_bars * parity.SAFETY_FACTOR
        per_session = max(1, 375 // {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 375}[tf])
        trading_days = days * 5 / 7
        assert days == MAX_HISTORY_DAYS or trading_days * per_session >= bars, (template.id, tf)
        assert days <= MAX_HISTORY_DAYS


def test_worker_fetches_a_template_with_its_own_lookback():
    from app.market_data.service import MarketDataService
    from app.workers.trading_worker import _history_source
    service = MarketDataService(broker=object())               # the broker is never called here
    strat = TemplateStrategy(D2, C.resolve_settings(D2, "15m"))
    longer = _history_source(service, strat)
    assert longer is not service and longer.lookback_days == strat.history_days() > service.lookback_days
    assert longer.broker is service.broker and service.lookback_days == 5
    short = TemplateStrategy(library.get("D1"), C.resolve_settings(library.get("D1"), "5m"))
    roomy = MarketDataService(broker=object(), lookback_days=30)
    assert short.history_days() < 30 and _history_source(roomy, short) is roomy    # its needs fit the service's lookback
    assert _history_source(service, object()) is service         # any other strategy: unchanged


def test_resolver_needs_the_flag_and_the_functions_flags():
    from app.custom_strategies import resolver

    class _User:
        tenant_id = 987654

    flags = {"strategy_templates": False, "screener_breakouts": True}

    async def fake_flag(session, name, tenant_id=None):
        return flags.get(name, False)

    import app.platform.controls as controls
    original = controls.flag_enabled
    controls.flag_enabled = fake_flag
    try:
        sid, _ = ids.adopt(B1, "15m")
        with pytest.raises(KeyError):
            asyncio.run(resolver.resolve_strategy(sid, _User(), None))
        flags["strategy_templates"] = True
        strat = asyncio.run(resolver.resolve_strategy(sid, _User(), None))
        assert isinstance(strat, TemplateStrategy) and strat.id == sid and strat.timeframes == ["15min"]
        flags["screener_breakouts"] = False
        with pytest.raises(KeyError, match="not switched on"):
            asyncio.run(resolver.resolve_strategy(sid, _User(), None))
        d2, _ = ids.adopt(D2, "1d")
        assert asyncio.run(resolver.resolve_strategy(d2, _User(), None)).timeframes == ["day"]
    finally:
        controls.flag_enabled = original
    assert resolver.TEMPLATE_PREFIX == ids.PREFIX
