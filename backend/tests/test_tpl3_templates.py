"""Strategy templates step 3: R7 (RetestEntry forwards the base breakout's own settings, `of = "channel"` with the edge
projected to each bar, the RetestEntryRef companion) and the templates C1 (range breakout and retest), C3 (channel breakout
and retest), A2 (range edges) and A5 (structure flip retest) against the owner order's section 8 gate - must fire / must
not (failure before the retest, a weak retest, expiry, a break distance not met, the context), the invalidation choices,
S6, the exits, the warm-up, the truncation test over many t, the mirror test, scan = backtest = options backtest = worker
on one trailing window (the TPL-1 harness), property tests on random valid settings, the language guard and "no number
written into a rule"."""
import re

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st

from app.backtest.options_engine import OptionBacktestConfig
from app.core.enums import OptionStrategy
from app.core.models import RiskConfig
from app.quality.lookahead import synthetic_bars
from app.screener.breakouts import params as bp
from app.screener.breakouts.retest import retest_events
from app.screener.breakouts.screens import Bars, evaluate_screen
from app.screener.parser import parse
from app.screener.runtime import SymbolData, evaluate
from app.strategy_templates import compile as C
from app.strategy_templates import engine, ids, language, library, parity
from app.strategy_templates.adapter import ENGINE_LABEL, TemplateStrategy
from tests.breakout_bk4_fixtures import retest_rows, rows_frame, trend_lead

C1, C3, A2, A5 = library.get("C1"), library.get("C3"), library.get("A2"), library.get("A5")
B1, B3 = library.get("B1"), library.get("B3")
RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5)
K = 400.0                                                     # mirror axis: price -> K - price
TOUCH = {"retest_reversal": False}


def run(template, df, tf="15m", **params):
    return engine.compute(C.compile_recipe(template, C.resolve_settings(template, tf, params=params)), {tf: df})


def ev(text, df, tf="15m"):
    return evaluate(parse(text), SymbolData("S", {tf: df}), base_tf=tf)


def mirror(df):
    out = df.copy()
    out["open"], out["close"] = K - df["open"], K - df["close"]
    out["high"], out["low"] = K - df["low"], K - df["high"]
    return out


def signals(s):
    return {t: int(s.direction[t]) for t in s.signal_bars()}


def retest_frame(**kw):
    """The BK4 range retest fixture (a 40-bar range, a breakout, the market away, a pull back, the retest bar) after the
    BK3 uptrend lead-in, so the retest's history (250 bars) is there: the retest is the last bar."""
    return rows_frame(retest_rows(**kw), lead=trend_lead())


def range_level(f):
    """The fixture's broken range edge: the highest high of the 20 bars before the breakout bar."""
    return float(f["high"].iloc[-27:-7].max())


# ============================================================================================================ R7 itself
def test_r7_retest_entry_forwards_the_base_breakouts_own_settings():
    """C1 runs on the user's range breakout settings: every retest follows a RangeBreakout signal with the same settings;
    left out, the settings are the breakout platform's defaults (volume 1.5x - never on an index without volume)."""
    df = synthetic_bars(900, tf="15m", seed=7).drop(columns=["volume"])          # an index: no volume
    plain = ev('RetestEntry("up", "range", 20, FALSE)', df)
    forwarded = ev('RetestEntry("up", "range", 20, FALSE, k=0.2, n=20, v=0, mode="auto")', df)
    assert not plain.any() and forwarded.any()
    p = bp.settings(retest_of="range", retest_max_bars=20, retest_reversal=False, min_break_atr=0.2, range_n=20, vol_mult=0.0,
                    range_mode="auto")
    b = Bars(df, p)
    breaks = ev('RangeBreakout("up", 20, 0.2, 0, "auto")', df).to_numpy()
    events = retest_events(b, b.side(1), p)
    assert [e.bar for e in events] == [int(t) for t in np.flatnonzero(forwarded.to_numpy())]
    assert events and all(breaks[e.breakout] for e in events)                    # the user's own breakout, not the default one


def test_r7_named_arguments_left_out_are_the_older_calls():
    df = synthetic_bars(900, tf="15m", seed=11)
    for of in ("range", "swing", "donchian", "trendline"):
        old = ev(f'RetestEntry("down", "{of}", 20, FALSE)', df)
        named = ev(f'RetestEntry("down", "{of}", 20, FALSE, k=0.2, n=20, touches=2)' if of != "swing" else
                   f'RetestEntry("down", "{of}", 20, FALSE, k=0.2)', df)
        assert old.equals(named), of


def test_r7_named_arguments_are_validated_like_positional_ones():
    from app.screener.validator import validate_study
    for text, fragment in (('RetestEntry("up", "range", 10, TRUE, n=1000)', "n must be between"),
                           ('RetestEntry("up", "range", 10, TRUE, mode="wide")', "mode 'wide' is not one of"),
                           ('RetestEntry("up", "range", 10, TRUE, k=ATR(14))', "k must be a number or a $parameter"),
                           ('RetestEntry("up", "range", 10, TRUE, 1, "channel_B", "A", 0.2, k=0.3)', "given twice"),
                           ('RetestEntry("up", "range", 10, TRUE, width=2)', "has no argument")):
        problems = [p.message for p in validate_study(parse(text), base_tf="15m", params={}).validated.problems]
        assert any(fragment in p for p in problems), (text, problems)


@pytest.mark.parametrize("seed", [1, 2, 5, 12])
def test_r7_channel_retest_reads_the_broken_edge_projected_to_the_retest_bar(seed):
    """`of = "channel"`: the B5 channel breakout, and the level at the retest bar is the broken edge's line there (its value
    at the breakout bar is the breakout's own level), not the value frozen at the breakout bar."""
    df = synthetic_bars(600, tf="15m", seed=seed)
    p = bp.settings(retest_of="channel", retest_max_bars=10, retest_reversal=False)
    b = Bars(df, p)
    seen = 0
    for side, sign in (("up", 1), ("down", -1)):
        broke = ev(f'LineBreakoutRef("level", "{side}", "channel", 2, 0.2)', df)
        level = ev(f'RetestEntryRef("level", "{side}", "channel", 10, FALSE)', df)
        held = ev(f'RetestEntryRef("held_level", "{side}", "channel", 10, FALSE)', df)
        fired = ev(f'RetestEntry("{side}", "channel", 10, FALSE)', df)
        events = retest_events(b, b.side(sign), p)
        assert [e.bar for e in events] == [int(t) for t in np.flatnonzero(fired.to_numpy())]
        assert (level.notna() == fired).all()
        for e in events:
            seen += 1
            assert sign * e.level_at(e.breakout) == pytest.approx(broke.iloc[e.breakout])          # the breakout's own edge
            assert level.iloc[e.bar] == pytest.approx(sign * e.level_at(e.bar))                  # projected to the retest bar
            assert e.slope is not None and e.slope > 0                                            # rising up / falling down
            assert sign * (level.iloc[e.bar] - broke.iloc[e.breakout]) > 0                        # not the frozen value
            nxt = next((x.bar for x in events if x.bar > e.bar), len(df))
            for x in range(e.bar, nxt):                                                           # held along the line
                assert held.iloc[x] == pytest.approx(sign * e.level_at(x))
    assert seen, "the fixture must carry channel retests"


def test_r7_the_b9_screen_follows_the_channel_too():
    df = synthetic_bars(600, tf="15m", seed=2)
    out = evaluate_screen(df, "B9", bp.settings(retest_of="channel", retest_reversal=False))
    fired = {s: [int(t) for t in np.flatnonzero(out[s]["signal"].to_numpy())] for s in ("up", "down")}
    assert fired["up"] or fired["down"]
    for side in ("up", "down"):
        assert fired[side] == [int(t) for t in np.flatnonzero(ev(f'RetestEntry("{side}", "channel", 10, FALSE)', df).to_numpy())]
        assert set(out[side]["event"][out[side]["signal"]]) <= {"RETEST_B5"}


# ============================================================================================================== C1
def test_c1_must_fire_on_the_retest_both_sides_with_the_default_invalidation():
    f = retest_frame()
    j = len(f) - 1
    s = run(C1, f, "1d", range_mode="fixed")
    assert signals(s) == {j: 1}
    level = range_level(f)
    pullback_low = float(f["low"].iloc[-6:].min())                               # the bars after the breakout up to the retest
    atr_prev = float(ev("ATR(14)", f, "1d").iloc[j - 1])
    assert s.invalidation[j] == pytest.approx(min(pullback_low, level - atr_prev))      # whichever of the two is farther
    m = run(C1, mirror(f), "1d", range_mode="fixed")
    assert signals(m) == {j: -1} and K - m.invalidation[j] == pytest.approx(s.invalidation[j])
    assert signals(run(C1, f, "1d", range_mode="fixed", **TOUCH)) == {j: 1}


def test_c1_invalidation_choices():
    f = retest_frame()
    j = len(f) - 1
    level, pullback_low = range_level(f), float(f["low"].iloc[-6:].min())
    atr_prev = float(ev("ATR(14)", f, "1d").iloc[j - 1])
    base_low = float(f["low"].iloc[-27:-7].min())
    want = {("retest_low", "stop_level_atr"): pullback_low, ("level_minus_stop_ref", "stop_level_atr"): level - atr_prev,
            ("level_minus_stop_ref", "stop_base"): base_low, ("level_minus_stop_ref", "stop_midpoint"): (level + base_low) / 2,
            ("lower_of_both", "stop_base"): min(pullback_low, base_low)}
    for (choice, ref), value in want.items():
        s = run(C1, f, "1d", range_mode="fixed", invalidation=choice, breakout_ref=ref)
        assert s.invalidation[j] == pytest.approx(value), (choice, ref)
    s = run(C1, f, "1d", range_mode="fixed", invalidation="level_minus_stop_ref", stop_mult=2.5)
    assert s.invalidation[j] == pytest.approx(level - 2.5 * atr_prev)


def test_c1_must_not_fire_on_a_failure_before_the_retest_a_weak_retest_or_too_late():
    assert run(C1, retest_frame(fail=True), "1d", range_mode="fixed").signal_bars() == []       # back inside first
    weak = retest_frame(hammer=False)
    assert run(C1, weak, "1d", range_mode="fixed").signal_bars() == []                          # no reversal at the level
    assert signals(run(C1, weak, "1d", range_mode="fixed", **TOUCH)) == {len(weak) - 1: 1}       # a touch is enough
    assert run(C1, retest_frame(), "1d", range_mode="fixed", retest_max_bars=4).signal_bars() == []   # expiry
    assert run(C1, retest_frame(), "1d", range_mode="fixed", k=2.0).signal_bars() == []          # the breakout's own distance
    assert run(C1, retest_frame(), "1d", range_mode="fixed", vol_mult=5.0).signal_bars() == []   # the breakout's own volume


def test_c1_s6_a_retest_closing_on_the_level_has_no_room():
    """S6: with the invalidation on the level itself (no buffer) and a retest bar closing exactly there, no signal."""
    rows = retest_rows(hammer=False)
    f0 = rows_frame(rows, lead=trend_lead())
    level = range_level(f0)
    lift = float(f0["close"].iloc[-1]) - rows[-1][3]
    o, h, lo, _, v = rows[-1]
    rows[-1] = (o, h, lo, level - lift, v)
    f = rows_frame(rows, lead=trend_lead())
    j = len(f) - 1
    assert f["close"].iloc[j] == pytest.approx(level)
    s = run(C1, f, "1d", range_mode="fixed", invalidation="level_minus_stop_ref", stop_mult=0.0, **TOUCH)
    assert s.signal_bars() == [] and s.reason[j] == engine.REASON_GEOMETRY


def test_c1_exit_signal_is_a_close_back_through_the_level():
    rows = retest_rows()
    tail = rows[-1][3]
    rows += [(tail, tail + 0.6, tail - 0.3, tail + 0.4, 1000.0), (tail, tail + 0.2, tail - 2.0, tail - 1.8, 1000.0)]
    f = rows_frame(rows, lead=trend_lead())
    s = run(C1, f, "1d", range_mode="fixed")
    j = len(f) - 3
    assert signals(s) == {j: 1}
    assert [bool(x) for x in s.exit_long[j:]] == [False, False, True]


def test_c1_and_c3_warm_up_is_the_retests_history():
    df = synthetic_bars(400, tf="15m", seed=3)
    for template in (C1, C3):
        s = run(template, df)
        assert s.warmup_bars == 249 and set(s.reason[:249]) == {engine.REASON_WARMUP}
        assert all(t >= 249 for t in s.signal_bars())


@pytest.mark.parametrize("seed", [1, 4, 7, 11])
def test_c1_fires_exactly_on_the_retests_with_the_users_settings(seed):
    df = synthetic_bars(600, tf="15m", seed=seed)
    for params in ({}, TOUCH, {"range_mode": "fixed", "range_n": 15, "k": 0.1, **TOUCH}):
        s = run(C1, df, **params)
        st_ = C.resolve_settings(C1, "15m", params=params).params
        tail = f'{str(st_["retest_reversal"]).upper()}, k={st_["k"]!r}, n={st_["range_n"]}, v=0, mode="{st_["range_mode"]}"'
        want = {}
        for side, sign in (("up", 1), ("down", -1)):
            fired = ev(f'RetestEntry("{side}", "range", 10, {tail})', df)
            for t in np.flatnonzero(fired.to_numpy()):
                if t >= s.warmup_bars and s.reason[t] != engine.REASON_GEOMETRY:
                    want[int(t)] = sign
        assert signals(s) == want, params


def test_c1_a_larger_break_distance_or_a_shorter_retest_window_never_adds_a_signal():
    df = synthetic_bars(900, tf="15m", seed=7)
    plain = signals(run(C1, df, **TOUCH))
    assert plain
    for strict in ({"k": 1.5}, {"retest_max_bars": 3}):
        fewer = signals(run(C1, df, **TOUCH, **strict))
        assert set(fewer.items()) <= set(plain.items()) and len(fewer) < len(plain), strict


# ============================================================================================================== C3
@pytest.mark.parametrize("seed", [1, 2, 7, 12])
def test_c3_fires_exactly_on_the_channel_retests(seed):
    df = synthetic_bars(600, tf="15m", seed=seed)
    s = run(C3, df, **TOUCH)
    want = {}
    for side, sign in (("up", 1), ("down", -1)):
        fired = ev(f'RetestEntry("{side}", "channel", 10, FALSE, k=0.2, touches=2)', df)
        low = ev(f'RetestEntryRef("retest_low", "{side}", "channel", 10, FALSE)', df)
        ref = ev(f'RetestEntryRef("stop_level_atr", "{side}", "channel", 10, FALSE, 1)', df)
        for t in np.flatnonzero(fired.to_numpy()):
            if t < s.warmup_bars:
                continue
            inv = min(low.iloc[t], ref.iloc[t]) if sign > 0 else max(low.iloc[t], ref.iloc[t])
            if s.reason[t] != engine.REASON_GEOMETRY:
                want[int(t)] = sign
                assert s.invalidation[t] == pytest.approx(inv)
    assert want, "the fixture must carry channel retests"
    assert signals(s) == want


def test_c3_exit_follows_the_projected_edge():
    df = synthetic_bars(600, tf="15m", seed=12)
    s = run(C3, df, **TOUCH)
    for side, attr, sign in (("up", "exit_long", 1), ("down", "exit_short", -1)):
        held = ev(f'RetestEntryRef("held_level", "{side}", "channel", 10, FALSE)', df)
        beyond = (df["close"] < held) if sign > 0 else (df["close"] > held)
        want = beyond.to_numpy() & (np.arange(len(df)) >= s.warmup_bars)
        assert (getattr(s, attr) == want).all(), side


def test_c3_touches_is_the_channel_breakouts_own_setting():
    """The line touches reach the channel breakout (another line can be chosen, so not a subset): the signals are the
    retests of the channel breakout with those touches."""
    df = synthetic_bars(600, tf="15m", seed=12)
    plain, strict = run(C3, df, **TOUCH), run(C3, df, touches=4, **TOUCH)
    assert signals(plain) != signals(strict)
    want = {}
    for side, sign in (("up", 1), ("down", -1)):
        fired = ev(f'RetestEntry("{side}", "channel", 10, FALSE, touches=4)', df).to_numpy()
        want.update({int(t): sign for t in np.flatnonzero(fired) if t >= strict.warmup_bars and strict.reason[t] != engine.REASON_GEOMETRY})
    assert signals(strict) == want


# ============================================================================================================== A2
@pytest.mark.parametrize("degree, seed", [(0, 1), (0, 4), (1, 1), (1, 12)])
def test_a2_fires_on_a_rejection_at_a_range_edge_only_in_a_range(degree, seed):
    df = synthetic_bars(600, tf="15m", seed=seed)
    s = run(A2, df, swing_degree=degree)
    rng = (ev(f"DowTrend({degree})", df) == "RANGE").to_numpy()
    want = {}
    for direction, edge, sign in (("bullish", "SwingLow", 1), ("bearish", "SwingHigh", -1)):
        fired = ev(f'ReversalAt({edge}({degree}), "{direction}")', df).to_numpy()
        level = ev(f"{edge}({degree})", df)
        for t in np.flatnonzero(fired):
            if t < s.warmup_bars:
                continue
            if not rng[t]:
                assert int(t) not in signals(s)                                   # the context: a range only
                continue
            if s.reason[t] not in (engine.REASON_GEOMETRY, engine.REASON_CONFLICT):
                want[int(t)] = sign
                assert s.invalidation[t] == pytest.approx(level.iloc[t])           # the edge itself
    assert want, "the fixture must carry range-edge rejections"
    assert signals(s) == want


@pytest.mark.parametrize("seed", [1, 3])
def test_a2_must_not_fire_on_a_rejection_outside_a_range(seed):
    """The near miss: the same rejection at the latest swing low / high while the structure is trending is no signal."""
    df = synthetic_bars(600, tf="15m", seed=seed)
    s = run(A2, df, swing_degree=0)
    rng = (ev("DowTrend(0)", df) == "RANGE").to_numpy()
    rejections = ev('ReversalAt(SwingLow(0), "bullish")', df).to_numpy() | ev('ReversalAt(SwingHigh(0), "bearish")', df).to_numpy()
    outside = [int(t) for t in np.flatnonzero(rejections & ~rng) if t >= s.warmup_bars]
    assert outside, "the fixture must carry rejections outside a range"
    assert not set(outside) & set(s.signal_bars()) and all(rng[t] for t in s.signal_bars())


def test_a2_has_no_exit_signal_and_its_warm_up_is_the_swings():
    df = synthetic_bars(600, tf="15m", seed=1)
    s = run(A2, df)
    assert not s.exit_long.any() and not s.exit_short.any()
    assert s.warmup_bars >= 249                                                 # the degree-one swings' history
    assert run(A2, df, swing_degree=0).warmup_bars >= 99


# ============================================================================================================== A5
@pytest.mark.parametrize("of, seed", [("swing", 8), ("range", 5), ("trendline", 9), ("channel", 1)])
def test_a5_fires_on_the_reversal_retest_of_what_broke(of, seed):
    df = synthetic_bars(600, tf="15m", seed=seed)
    s = run(A5, df, retest_of=of)
    tail = 'k=0.2, n=20, v=0, mode="auto", touches=2'
    want = {}
    for side, sign in (("up", 1), ("down", -1)):
        fired = ev(f'RetestEntry("{side}", "{of}", 10, TRUE, {tail})', df)
        level = ev(f'RetestEntryRef("level", "{side}", "{of}", 10, TRUE, {tail})', df)
        for t in np.flatnonzero(fired.to_numpy()):
            if t >= s.warmup_bars and s.reason[t] != engine.REASON_GEOMETRY:
                want[int(t)] = sign
                assert s.invalidation[t] == pytest.approx(level.iloc[t])            # no buffer: acceptance back through the level
    assert want, "the fixture must carry retests"
    assert signals(s) == want


def test_a5_range_retest_on_the_fixture_and_the_buffer():
    f = retest_frame()
    j = len(f) - 1
    s = run(A5, f, "1d", retest_of="range", range_mode="fixed")
    assert signals(s) == {j: 1} and s.invalidation[j] == pytest.approx(range_level(f))
    atr_prev = float(ev("ATR(14)", f, "1d").iloc[j - 1])
    s = run(A5, f, "1d", retest_of="range", range_mode="fixed", stop_mult=0.5)
    assert s.invalidation[j] == pytest.approx(range_level(f) - 0.5 * atr_prev)
    assert run(A5, retest_frame(hammer=False), "1d", retest_of="range", range_mode="fixed").signal_bars() == []   # a rejection only


# ============================================================================================ shared gate: all four
CASES = [(C1, TOUCH, 1), (C1, {"invalidation": "retest_low", **TOUCH}, 2), (C3, TOUCH, 12), (C3, {"invalidation": "level_minus_stop_ref", **TOUCH}, 2),
         (A2, {"swing_degree": 0}, 1), (A2, {}, 1), (A5, {"retest_of": "range"}, 5), (A5, {"retest_of": "trendline"}, 9)]
IDS = [f"{t.id}-{i}" for i, (t, _, _) in enumerate(CASES)]


@pytest.mark.parametrize("template, params, seed", CASES, ids=IDS)
def test_mirror_short_equals_long_on_mirrored_prices(template, params, seed):
    df = synthetic_bars(600, tf="15m", seed=seed)
    s, m = run(template, df, **params), run(template, mirror(df), **params)
    assert signals(s), "the fixture must carry signals"
    assert signals(m) == {t: -d for t, d in signals(s).items()}
    for t in s.signal_bars():
        assert K - m.invalidation[t] == pytest.approx(s.invalidation[t])
    assert (m.exit_short == s.exit_long).all() and (m.exit_long == s.exit_short).all()


@pytest.mark.parametrize("template, params, seed", CASES, ids=IDS)
def test_truncation_no_look_ahead_over_many_t(template, params, seed):
    df = synthetic_bars(600, tf="15m", seed=seed)
    full = run(template, df, **params)
    marks = sorted(set(full.signal_bars()) | {t + 1 for t in full.signal_bars()} | set(range(full.warmup_bars, len(df), 41)))
    for t in [x for x in marks if x < len(df)]:
        part = run(template, df.iloc[:t + 1], **params)
        np.testing.assert_array_equal(part.direction, full.direction[:t + 1], err_msg=f"t={t}")
        np.testing.assert_allclose(part.invalidation, full.invalidation[:t + 1], equal_nan=True, err_msg=f"t={t}")
        np.testing.assert_array_equal(part.exit_long, full.exit_long[:t + 1], err_msg=f"t={t}")
        assert [part.instance_id(i) for i in part.signal_bars()] == [full.instance_id(i) for i in full.signal_bars() if i <= t]


@pytest.mark.parametrize("template, params, seed", [(C1, TOUCH, 2), (C1, {}, 17), (C3, TOUCH, 2), (A2, {"swing_degree": 0}, 1),
                                                    (A2, {}, 3), (A5, {"retest_of": "trendline"}, 5), (A5, {}, 7)],
                         ids=["C1-touch", "C1", "C3", "A2-0", "A2", "A5-line", "A5-swing"])
def test_scan_equals_backtest_equals_options_equals_worker(template, params, seed):
    strat = TemplateStrategy(template, C.resolve_settings(template, "15m", params=params))
    window = synthetic_bars(900, tf="15m", seed=seed).iloc[-500:].copy()          # a fixed TRAILING window (R6)
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


@hsettings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(mode=st.sampled_from(["fixed", "auto"]), n=st.integers(5, 40), k=st.floats(0.0, 1.0), bars=st.integers(1, 30),
       rev=st.booleans(), inval=st.sampled_from(C1.param("invalidation").choices), ref=st.sampled_from(C1.param("breakout_ref").choices),
       mult=st.floats(0.0, 3.0), seed=st.integers(0, 30))
def test_c1_property_random_valid_settings(mode, n, k, bars, rev, inval, ref, mult, seed):
    df = synthetic_bars(400, tf="15m", seed=seed)
    s = run(C1, df, range_mode=mode, range_n=n, k=k, retest_max_bars=bars, retest_reversal=rev, invalidation=inval, breakout_ref=ref,
            stop_mult=mult)
    for side, sign in (("up", 1), ("down", -1)):
        fired = ev(f'RetestEntry("{side}", "range", {bars}, {str(rev).upper()}, k={k!r}, n={n}, v=0, mode="{mode}")', df).to_numpy()
        for t, d in signals(s).items():
            if d == sign:
                assert fired[t]                                                   # no signal without its retest
                assert (s.invalidation[t] < df["close"].iloc[t]) if d > 0 else (s.invalidation[t] > df["close"].iloc[t])


@hsettings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(touches=st.integers(2, 4), k=st.floats(0.0, 1.0), bars=st.integers(1, 30), rev=st.booleans(),
       inval=st.sampled_from(C3.param("invalidation").choices), seed=st.integers(0, 30))
def test_c3_property_random_valid_settings(touches, k, bars, rev, inval, seed):
    df = synthetic_bars(400, tf="15m", seed=seed)
    s = run(C3, df, touches=touches, k=k, retest_max_bars=bars, retest_reversal=rev, invalidation=inval)
    for side, sign in (("up", 1), ("down", -1)):
        fired = ev(f'RetestEntry("{side}", "channel", {bars}, {str(rev).upper()}, k={k!r}, touches={touches})', df).to_numpy()
        for t, d in signals(s).items():
            if d == sign:
                assert fired[t]
                assert (s.invalidation[t] < df["close"].iloc[t]) if d > 0 else (s.invalidation[t] > df["close"].iloc[t])


@hsettings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(degree=st.integers(0, 1), of=st.sampled_from(A5.param("retest_of").choices), mult=st.floats(0.0, 2.0), seed=st.integers(0, 30))
def test_a2_a5_property_random_valid_settings(degree, of, mult, seed):
    df = synthetic_bars(400, tf="15m", seed=seed)
    for s in (run(A2, df, swing_degree=degree), run(A5, df, retest_of=of, stop_mult=mult)):
        for t, d in signals(s).items():
            assert (s.invalidation[t] < df["close"].iloc[t]) if d > 0 else (s.invalidation[t] > df["close"].iloc[t])


@pytest.mark.parametrize("template", [C1, C3, A2, A5], ids=["C1", "C3", "A2", "A5"])
def test_library_template_validates_and_its_text_passes_the_guard(template):
    assert C.validate_template(template) == []
    for tf in template.allowed_timeframes:
        settings = C.resolve_settings(template, tf)
        for side in ("long", "short"):
            text = library.signal_text(template, settings, side)
            assert text and language.guard(text) == [], text
    for p in template.params:
        assert p.default is not None
        assert language.guard(p.label) == [] or p.type in ("bool", "int", "float")
        if p.type in ("int", "float"):
            assert p.min is not None and p.max is not None and p.min <= p.default <= p.max
        if p.choices:
            assert len(p.choice_labels) == len(p.choices)


def _texts(template):
    out = []
    for part in (template.setup, template.invalidation, template.exit_signal, template.context):
        if part is None:
            continue
        variants = [v for v in part.variants.values() if v] if hasattr(part, "variants") else [part]
        out += [x for v in variants for x in (v.long, v.short)]
    return out


@pytest.mark.parametrize("template", [C1, C3, A2, A5], ids=["C1", "C3", "A2", "A5"])
def test_no_number_is_written_into_a_template_rule(template):
    for text in _texts(template):
        assert not re.search(r"(?<![\w$])\d", text), (template.id, text)


def test_c1_and_c3_use_the_breakout_templates_own_settings():
    """TPL-Q8: the breakout part of C1 / C3 is B1 / B3's - the same keys, labels, defaults and bounds."""
    for retest, breakout, keys in ((C1, B1, ("range_mode", "range_n", "k", "vol_mult", "stop_mult")), (C3, B3, ("touches", "k", "stop_mult"))):
        for key in keys:
            a, b = retest.param(key), breakout.param(key)
            assert (a.type, a.default, a.min, a.max, a.choices) == (b.type, b.default, b.min, b.max, b.choices), (retest.id, key)
        assert retest.param("breakout_ref").choices == breakout.param("invalidation").choices


@pytest.mark.parametrize("template_id", ["C1", "C3", "A2", "A5"])
def test_template_ids_round_trip_and_fit(template_id):
    template = library.get(template_id)
    for tf in [t for t in template.allowed_timeframes if t in ENGINE_LABEL]:
        sid, settings = ids.adopt(template, tf)
        assert ids.decode(sid) == (template, settings)
    worst = {}
    for p in template.params:
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
    except C.TemplateError as exc:
        assert exc.problems == ["These settings are too long to save: use fewer decimals"], exc.problems
    else:
        assert len(sid) <= ids.MAX_LENGTH
    sid, _ = ids.adopt(template, template.allowed_timeframes[0], "short", {k: v for k, v in worst.items()
                                                                          if template.param(k).type != "float"})
    assert len(sid) <= ids.MAX_LENGTH                                         # every choice and whole number fits


def test_the_adapter_reads_the_retest_signal_and_its_provenance():
    df = synthetic_bars(600, tf="15m", seed=1)
    strat = TemplateStrategy(C1, C.resolve_settings(C1, "15m", params=TOUCH))
    series = engine.compute(strat.recipe, {"15m": df})
    t = series.signal_bars()[0]
    sig = strat.analyze({"15min": df.iloc[:t + 1]}, "S")
    assert sig.is_tradeable and sig.stop_loss == pytest.approx(series.invalidation[t]) and sig.target1 is None
    assert sig.provenance["template"] == "C1" and sig.provenance["setup"] == series.instance_id(t)
    assert all(language.guard(r) == [] for r in sig.reasons)
    assert strat.recipe.required_flags == {"screener_breakouts", "screener_breakouts_bk4"}


def test_a2_needs_no_breakout_flag():
    strat = TemplateStrategy(A2, C.resolve_settings(A2, "15m"))
    assert strat.recipe.required_flags == {"screener_breakouts"}                  # DowTrend is a breakout-family function
