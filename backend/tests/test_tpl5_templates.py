"""Strategy templates step 5: the engine piece "the extreme since the setup bar" (SetupExtreme: the lowest low / highest
high from the setup instance's bar to the trigger bar, restarted by a newer setup), a setup bound to its session
(`session_bound`, the exchange calendar's session of each bar's own timestamp), the swing degree forwarded to RetestEntry
as the named argument `swing=` (R7 for C2), and the templates C2 (swing breakout and retest), D3 (moving-average pullback
in trend), E1 (RSI pullback in trend), E3 (Bollinger mean reversion in a range) and E5 (session VWAP reclaim / reject)
against the owner order's section 8 gate - must fire long and short / must not, the invalidation by value, S5 conflicts,
S6, the exits, the warm-up, the truncation test over many t, the mirror test, scan = backtest = options backtest = worker
on one trailing window (the TPL-1 harness), property tests on random valid settings, the language guard, "no number
written into a rule" and the ids."""
import re

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hsettings
from hypothesis import strategies as st
from pydantic import ValidationError

from app.backtest.options_engine import OptionBacktestConfig
from app.core.enums import OptionStrategy
from app.core.models import RiskConfig
from app.quality.lookahead import synthetic_bars
from app.screener.breakouts import params as bp
from app.screener.breakouts import session as ses
from app.screener.breakouts.retest import retest_events
from app.screener.breakouts.screens import Bars
from app.screener.parser import parse
from app.screener.runtime import SymbolData, evaluate
from app.screener.validator import validate_study
from app.strategy_templates import compile as C
from app.strategy_templates import engine, ids, language, library, parity
from app.strategy_templates.adapter import ENGINE_LABEL, TemplateStrategy
from app.strategy_templates.schema import SetupExtreme, Template
from tests.test_tpl1_recipe_core import BASE, tpl
from tests.test_tpl1_recipe_core import bars as marks

NEW = ("C2", "D3", "E1", "E3", "E5")
LATER = ("C6", "B12", "D9")        # STRAT-X templates (after step 5) may use the step-5 pieces; they are not "earlier"
T = {tid: library.get(tid) for tid in NEW}
RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5)
K = 400.0
EXTREME = {"extreme": "since_setup"}


def run(template, df, tf="15m", **params):
    return engine.compute(C.compile_recipe(template, C.resolve_settings(template, tf, params=params)), {tf: df})


def run_marks(template, df, sides="both", **params):
    return engine.compute(C.compile_recipe(template, C.resolve_settings(template, "15m", sides, params)), {"15m": df})


def ev(text, df, tf="15m"):
    return evaluate(parse(text), SymbolData("S", {tf: df}), base_tf=tf)


def b(text, df):
    return ev(text, df).fillna(False).to_numpy(dtype=bool)


def mirror(df, axis=K):
    out = df.copy()
    out["open"], out["close"] = axis - df["open"], axis - df["close"]
    out["high"], out["low"] = axis - df["low"], axis - df["high"]
    return out


def signals(s):
    return {t: int(s.direction[t]) for t in s.signal_bars()}


def bars(seed, n=900, tf="15m"):
    return synthetic_bars(n, tf=tf, seed=seed)


def sessions_of(df, tf="15m"):
    return ses.map_sessions(df.index, float(int(tf[:-1])), ses.calendar_for("NSE")).session


def reference(s, df, setup, trigger, context=(None, None), *, expiry=None, same_bar=False, session=None, inv=None):
    """An independent walk of the owner order's S4 / S5 / S6 / R8 rules over boolean series written in the test: event
    setups (the newest replaces the older), the trigger on a later bar (or the same one with same_bar), the context read on
    the trigger bar, conflicts, and the invalidation = the extreme since the setup bar (or the given series). Returns
    {bar: (side, invalidation, setup bar)}."""
    low, high, close = (df[c].to_numpy(dtype=float) for c in ("low", "high", "close"))
    n = len(df)
    ctx = [np.ones(n, dtype=bool) if c is None else c for c in context]
    armed, ext, want = {1: -1, -1: -1}, {1: np.nan, -1: np.nan}, {}
    for t in range(s.warmup_bars, n):
        fired = {}
        for i, sign in enumerate((1, -1)):
            if setup[i][t]:
                if session is not None and session[t] < 0:
                    armed[sign] = -1
                else:
                    armed[sign], ext[sign] = t, (low[t] if sign > 0 else high[t])
            elif armed[sign] >= 0:
                s0 = armed[sign]
                if (expiry is not None and t - s0 > expiry) or (session is not None and session[t] != session[s0]):
                    armed[sign] = -1
                else:
                    ext[sign] = min(ext[sign], low[t]) if sign > 0 else max(ext[sign], high[t])
            s0 = armed[sign]
            if s0 >= 0 and (t > s0 or same_bar) and trigger[i][t] and ctx[i][t]:
                fired[sign] = s0
        if len(fired) == 2:
            assert s.reason[t] == engine.REASON_CONFLICT, t
            armed = {1: -1, -1: -1}
            continue
        if not fired:
            continue
        sign, s0 = next(iter(fired.items()))
        armed[sign] = -1
        level = ext[sign] if inv is None else float(inv[0 if sign > 0 else 1][t])
        if (sign > 0 and level < close[t]) or (sign < 0 and level > close[t]):
            want[t] = (sign, level, s0)
        else:
            assert s.reason[t] in (engine.REASON_GEOMETRY, engine.REASON_NO_LEVEL), t
    return want


def expect(s, want):
    assert signals(s) == {t: v[0] for t, v in want.items()}
    for t, (_, level, s0) in want.items():
        assert s.invalidation[t] == pytest.approx(level), t
        assert s.setup_bar[t] == s0, t


def both_sides(s):
    assert 1 in signals(s).values() and -1 in signals(s).values(), "the fixture must carry long and short signals"


# ================================================================================= the engine piece: the extreme since the setup
def test_extreme_since_setup_is_the_lowest_low_from_the_setup_bar_to_the_trigger_bar():
    t = tpl(invalidation=EXTREME)
    s = run_marks(t, marks({5: 1}, {8: 1}, lows={4: 90.0, 6: 97.0, 7: 98.0}))
    assert signals(s) == {8: 1} and s.invalidation[8] == 97.0          # bar 4 (before the setup) is not part of it
    s = run_marks(t, marks({5: 1}, {8: 1}, lows={5: 96.0, 6: 97.0}))
    assert s.invalidation[8] == 96.0                                   # the setup bar itself counts
    s = run_marks(t, marks({5: 1}, {8: 1}, lows={6: 97.0, 8: 95.0}))
    assert s.invalidation[8] == 95.0                                   # and the trigger bar
    s = run_marks(t, marks({5: 2}, {8: 2}, highs={4: 110.0, 6: 103.0, 7: 102.0}))
    assert signals(s) == {8: -1} and s.invalidation[8] == 103.0        # short: the highest high


def test_extreme_restarts_with_a_newer_setup_and_reads_no_later_bar():
    t = tpl(invalidation=EXTREME)
    s = run_marks(t, marks({5: 1, 7: 1}, {9: 1}, lows={6: 90.0, 8: 98.0}))
    assert signals(s) == {9: 1} and s.setup_bar[9] == 7 and s.invalidation[9] == 98.0   # bar 6 belonged to the older instance
    full = run_marks(t, marks({5: 1}, {8: 1}, lows={6: 97.0, 9: 80.0}))
    assert full.invalidation[8] == 97.0                                 # a lower low after the trigger is not read
    s = run_marks(tpl(invalidation=EXTREME, **{"setup.same_bar": True}), marks({5: 1}, {5: 1}, lows={5: 96.0}))
    assert signals(s) == {5: 1} and s.invalidation[5] == 96.0           # same bar: the setup bar's own extreme


def test_extreme_since_setup_keeps_s6_and_conflicts():
    t = tpl(invalidation=EXTREME)
    s = run_marks(t, marks({5: 1}, {7: 1}, lows={7: 100.0}, closes={7: 100.0}, opens={7: 100.0}))
    assert s.reason[7] in ("", engine.REASON_GEOMETRY)
    flat = marks({5: 1}, {7: 1}, closes={i: 100.0 for i in range(30)})
    flat.loc[flat.index[5:8], "low"] = 100.0
    s = run_marks(t, flat)
    assert signals(s) == {} and s.reason[7] == engine.REASON_GEOMETRY   # the extreme on the close: not on the losing side
    s = run_marks(t, marks({4: 1, 5: 2}, {6: 3}))
    assert signals(s) == {} and s.reason[6] == engine.REASON_CONFLICT


def test_extreme_since_setup_is_a_schema_choice_for_the_invalidation_only():
    assert isinstance(tpl(invalidation=EXTREME).invalidation, SetupExtreme)
    sw = {"by": "pick", "variants": {"a": EXTREME, "b": {"long": "low", "short": "high"}}}
    pick = {"key": "pick", "label": "Pick", "help": "Which.", "type": "enum", "default": "a", "choices": ["a", "b"]}
    t = tpl(invalidation=sw, params=[*BASE["params"], pick])
    assert C.compile_recipe(t, C.resolve_settings(t, "15m")).invalidation_since_setup
    assert not C.compile_recipe(t, C.resolve_settings(t, "15m", params={"pick": "b"})).invalidation_since_setup
    for where in ("trigger", "exit_signal"):
        with pytest.raises(ValidationError):
            tpl(**{where: EXTREME})
    with pytest.raises(ValidationError):
        tpl(invalidation={"extreme": "since_breakout"})
    for old in library.templates():                                     # every earlier template reads its expression as before
        if old.id not in NEW + LATER:
            recipe = C.compile_recipe(old, C.resolve_settings(old, old.allowed_timeframes[0]))
            assert not recipe.invalidation_since_setup and not recipe.chosen_setup.session_bound


@hsettings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(setups=st.dictionaries(st.integers(1, 59), st.sampled_from([1, 2]), max_size=12),
       triggers=st.dictionaries(st.integers(1, 59), st.sampled_from([1, 2, 3]), max_size=12),
       lows=st.dictionaries(st.integers(0, 59), st.floats(90.0, 99.4), max_size=20),
       highs=st.dictionaries(st.integers(0, 59), st.floats(100.6, 110.0), max_size=20), expiry=st.integers(0, 10))
def test_extreme_since_setup_matches_its_definition_and_has_no_look_ahead(setups, triggers, lows, highs, expiry):
    t = tpl(invalidation=EXTREME)
    df = marks(setups, triggers, lows=lows, highs=highs, n=60)
    full = run_marks(t, df, expiry=expiry)
    for i in full.signal_bars():
        s0 = int(full.setup_bar[i])
        want = df["low"].iloc[s0:i + 1].min() if full.direction[i] > 0 else df["high"].iloc[s0:i + 1].max()
        assert full.invalidation[i] == want
        later = [j for j in range(s0 + 1, i + 1) if df["oi"].iloc[j] == (1 if full.direction[i] > 0 else 2)]
        assert not later                                                # the newest instance, and it restarts the extreme
    for cut in range(1, 60):
        part = run_marks(t, df.iloc[:cut + 1], expiry=expiry)
        np.testing.assert_array_equal(part.direction, full.direction[:cut + 1])
        np.testing.assert_array_equal(part.invalidation, full.invalidation[:cut + 1])


def _two_sessions():
    """Two NSE sessions of 15-minute bars (25 each), stamped as the exchange clock gives them (UTC bar starts)."""
    days = [pd.Timestamp("2026-03-02 03:45", tz="UTC"), pd.Timestamp("2026-03-03 03:45", tz="UTC")]
    idx = pd.DatetimeIndex([d + pd.Timedelta(minutes=15 * k) for d in days for k in range(25)])
    df = marks(n=50)
    df.index = idx
    return df


def test_a_session_bound_setup_ends_with_its_session():
    bound = tpl(invalidation=EXTREME, **{"setup.session_bound": True, "setup.expiry_bars": None})
    free = tpl(invalidation=EXTREME, **{"setup.expiry_bars": None})
    df = _two_sessions()
    df.loc[df.index[23], "oi"], df.loc[df.index[26], "volume"] = 1, 1    # set up late in the first session, triggered in the next
    assert signals(run_marks(free, df)) == {26: 1}
    assert signals(run_marks(bound, df)) == {}
    df.loc[df.index[24], "volume"] = 1                                   # the same session: it fires
    assert signals(run_marks(bound, df)) == {24: 1}
    with pytest.raises(ValidationError, match="intraday"):
        Template.from_data({**BASE, "allowed_timeframes": ["15m", "1d"], "setup": {**BASE["setup"], "session_bound": True}})


# ============================================================================================ R7: the swing degree for C2
def test_r7_retest_entry_forwards_the_swing_degree_as_a_named_argument():
    df = bars(2)
    for degree in (0, 2):
        forwarded = ev(f'RetestEntry("up", "swing", 10, FALSE, swing={degree})', df).to_numpy()
        p = bp.settings(retest_of="swing", retest_max_bars=10, retest_reversal=False, swing_degree=degree)
        bb = Bars(df, p)
        assert [e.bar for e in retest_events(bb, bb.side(1), p)] == [int(t) for t in np.flatnonzero(forwarded)]
    zero, one = (ev(f'RetestEntry("down", "swing", 10, FALSE, swing={d})', df) for d in (0, 1))
    assert not zero.equals(one)                                         # the degree reaches the swing breakout


def test_r7_swing_left_out_is_the_older_call_and_other_bases_ignore_it():
    df = bars(11)
    for side in ("up", "down"):
        old = ev(f'RetestEntry("{side}", "swing", 20, FALSE)', df)
        assert old.equals(ev(f'RetestEntry("{side}", "swing", 20, FALSE, swing=1)', df))      # 1 = the breakout default
        assert ev(f'RetestEntryRef("level", "{side}", "swing", 20, FALSE)', df).equals(
            ev(f'RetestEntryRef("level", "{side}", "swing", 20, FALSE, swing=1)', df))
        for of in ("range", "donchian"):
            assert ev(f'RetestEntry("{side}", "{of}", 20, FALSE)', df).equals(ev(f'RetestEntry("{side}", "{of}", 20, FALSE, swing=0)', df))


def test_r7_swing_is_validated_and_sets_the_history():
    problems = [p.message for p in validate_study(parse('RetestEntry("up", "swing", 10, TRUE, swing=4)'), base_tf="15m",
                                                  params={}).validated.problems]
    assert any("swing must be between" in p for p in problems), problems
    need = {d: validate_study(parse(f'RetestEntry("up", "swing", 10, TRUE, swing={d})'), base_tf="15m", params={}).validated.lookback["15m"]
            for d in range(4)}
    assert need == {0: 250, 1: 250, 2: 600, 3: 1200}
    assert validate_study(parse('RetestEntry("up", "swing", 10, TRUE)'), base_tf="15m", params={}).validated.lookback["15m"] == 250


# ============================================================================================================== C2
def _c2(side, tail="k=0.2, swing=0"):
    return f'RetestEntry("{side}", "swing", 10, FALSE, {tail})'


def _c2ref(what, side, stop="", tail="k=0.2, swing=0"):
    return f'RetestEntryRef("{what}", "{side}", "swing", 10, FALSE{stop}, {tail})'


@pytest.mark.parametrize("seed", [2, 3, 7])
def test_c2_fires_exactly_on_the_swing_retests_with_the_farther_reference(seed):
    df = bars(seed)
    s = run(T["C2"], df, swing_degree=0, retest_reversal=False)
    want = {}
    for side, sign in (("up", 1), ("down", -1)):
        low, ref = ev(_c2ref("retest_low", side), df), ev(_c2ref("stop_level_atr", side, ", 1"), df)
        for t in np.flatnonzero(ev(_c2(side), df).to_numpy()):
            if t < s.warmup_bars or s.reason[t] == engine.REASON_GEOMETRY:
                continue
            want[int(t)] = sign
            assert s.invalidation[t] == pytest.approx(min(low.iloc[t], ref.iloc[t]) if sign > 0 else max(low.iloc[t], ref.iloc[t]))
    assert want
    assert signals(s) == want


def test_c2_runs_on_the_swing_breakouts_own_settings_and_its_choices():
    df = bars(7)
    for key in ("swing_degree", "k"):
        a, c = T["C2"].param(key), library.get("B2").param(key)
        assert (a.type, a.default, a.min, a.max) == (c.type, c.default, c.min, c.max)
    s = run(T["C2"], df, swing_degree=0, k=0.1, retest_reversal=False)
    want = {int(t): sign for side, sign in (("up", 1), ("down", -1)) for t in np.flatnonzero(ev(_c2(side, "k=0.1, swing=0"), df).to_numpy())
            if t >= s.warmup_bars and s.reason[t] != engine.REASON_GEOMETRY}
    assert want and signals(s) == want
    for choice, what in (("retest_low", "retest_low"), ("level_minus_stop_ref", "stop_level_atr"), ("swing", None)):
        s = run(T["C2"], df, swing_degree=0, retest_reversal=False, invalidation=choice)
        for t, d in signals(s).items():
            side = "up" if d > 0 else "down"
            text = ("SwingLow(0)" if d > 0 else "SwingHigh(0)") if what is None else _c2ref(what, side, ", 1" if what != "retest_low" else "")
            assert s.invalidation[t] == pytest.approx(ev(text, df).iloc[t])
    s = run(T["C2"], df, swing_degree=0, retest_reversal=False)
    held = ev(_c2ref("held_level", "up"), df)
    assert (s.exit_long == ((df["close"] < held).to_numpy() & (np.arange(len(df)) >= s.warmup_bars))).all()
    plain = signals(s)
    assert set(signals(run(T["C2"], df, swing_degree=0, retest_reversal=False, retest_max_bars=2)).items()) < set(plain.items())
    strict = run(T["C2"], df, swing_degree=0)                           # a reversal at the level asked for: only those retests
    want = {int(t): sign for side, sign in (("up", 1), ("down", -1))
            for t in np.flatnonzero(ev(_c2(side).replace("FALSE", "TRUE"), df).to_numpy())
            if t >= strict.warmup_bars and strict.reason[t] != engine.REASON_GEOMETRY}
    assert signals(strict) == want and signals(strict) != plain


# ============================================================================================================== D3
def _d3_series(df, fast=20, slow=50, slope=5, kind="EMA"):
    f, sl = f'MA(close, {fast}, "{kind}")', f'MA(close, {slow}, "{kind}")'
    setup = (b(f"close > {sl} AND {sl} > Lag({sl}, {slope}) AND low <= {f} AND low[1] > {f}[1]", df),
             b(f"close < {sl} AND {sl} < Lag({sl}, {slope}) AND high >= {f} AND high[1] < {f}[1]", df))
    trigger = (b(f'ReversalAt({f}, "bullish") AND close > {f}', df), b(f'ReversalAt({f}, "bearish") AND close < {f}', df))
    return setup, trigger


@pytest.mark.parametrize("seed, params", [(1, {}), (2, {}), (3, {"trigger_bar": "same"}), (4, {"ma_type": "SMA", "fast": 10, "slow": 30})])
def test_d3_fires_exactly_on_the_pullback_reversals_with_the_pullback_extreme(seed, params):
    df = bars(seed)
    s = run(T["D3"], df, **params)
    setup, trigger = _d3_series(df, params.get("fast", 20), params.get("slow", 50), 5, params.get("ma_type", "EMA"))
    want = reference(s, df, setup, trigger, expiry=20, same_bar=params.get("trigger_bar") == "same")
    expect(s, want)
    both_sides(s)


def test_d3_must_not_fire_without_the_trend_late_or_on_the_pullback_bar():
    df = bars(1)
    s = signals(run(T["D3"], df))
    setup, trigger = _d3_series(df)
    later = run(T["D3"], df)
    assert all(later.setup_bar[t] < t for t in s)                        # "later": never on the pullback bar itself
    for t, d in s.items():
        assert setup[0 if d > 0 else 1][later.setup_bar[t]] and trigger[0 if d > 0 else 1][t]
    quick = signals(run(T["D3"], df, pullback_max_bars=1))
    assert set(quick.items()) < set(s.items())                          # a reversal too long after the pullback: no signal
    steep = signals(run(T["D3"], df, slope_bars=100))
    assert steep != s                                                   # the trend average's slope is read over the setting
    with pytest.raises(C.TemplateError, match="shorter"):
        C.resolve_settings(T["D3"], "15m", params={"fast": 50, "slow": 50})


def test_d3_exit_is_a_setting():
    df = bars(1)
    s = run(T["D3"], df)
    slow = ev('MA(close, 50, "EMA")', df)
    assert (s.exit_long == ((df["close"] < slow).to_numpy() & (np.arange(len(df)) >= s.warmup_bars))).all()
    assert not run(T["D3"], df, exit_rule="none").exit_long.any()


# ============================================================================================================== E1
def _e1_series(df, n=14, lo=30.0, hi=70.0):
    rsi = f"RSI({n})"
    return ((b(f"CrossBelow({rsi}, {lo})", df), b(f"CrossAbove({rsi}, {hi})", df)),
            (b(f"CrossAbove({rsi}, {lo})", df), b(f"CrossBelow({rsi}, {hi})", df)))


@pytest.mark.parametrize("seed, params, ctx", [
    (1, {"trend_filter": "supertrend"}, ("SupertrendDir(10, 3.0)@1h == 1", "SupertrendDir(10, 3.0)@1h == -1")),
    (2, {"trend_period": 20, "oversold": 40.0, "overbought": 60.0},
     ('close@1h > MA(close@1h, 20, "EMA")@1h', 'close@1h < MA(close@1h, 20, "EMA")@1h'))])
def test_e1_fires_exactly_on_the_rsi_turning_back_with_the_trend_and_the_dip_extreme(seed, params, ctx):
    df = bars(seed)
    s = run(T["E1"], df, context_timeframe="1h", **params)
    setup, trigger = _e1_series(df, 14, params.get("oversold", 30.0), params.get("overbought", 70.0))
    want = reference(s, df, setup, trigger, (b(ctx[0], df), b(ctx[1], df)))
    expect(s, want)
    both_sides(s)


def test_e1_must_not_fire_against_the_trend_and_the_exits():
    df = bars(2)
    p = {"context_timeframe": "1h", "trend_period": 20, "oversold": 40.0, "overbought": 60.0}
    s = run(T["E1"], df, **p)
    setup, trigger = _e1_series(df, 14, 40.0, 60.0)
    unfiltered = reference(s, df, setup, trigger)
    assert set(signals(s).items()) < {(t, v[0]) for t, v in unfiltered.items()}    # some turns were against the trend
    rsi = ev("RSI(14)", df)
    ready = np.arange(len(df)) >= s.warmup_bars
    assert (s.exit_long == ((rsi >= 60.0).to_numpy() & ready)).all() and (s.exit_short == ((rsi <= 40.0).to_numpy() & ready)).all()
    o = run(T["E1"], df, exit_rule="opposite_trigger", **p)
    assert (o.exit_long == (trigger[1] & ready)).all() and (o.exit_short == (trigger[0] & ready)).all()
    with pytest.raises(C.TemplateError, match="higher than the signal"):
        C.resolve_settings(T["E1"], "1h", params={"context_timeframe": "1h"})


# ============================================================================================================== E3
@pytest.mark.parametrize("seed, params, ctx", [(1, {}, "ADX(14) < 20.0"), (2, {"bb_k": 1.5, "adx_threshold": 25.0}, "ADX(14) < 25.0"),
                                               (3, {"regime": "structure", "swing_degree": 0}, 'DowTrend(0) == "RANGE"')])
def test_e3_fires_exactly_on_the_close_back_inside_in_a_range_with_the_excursion_extreme(seed, params, ctx):
    df = bars(seed)
    s = run(T["E3"], df, **params)
    k = params.get("bb_k", 2.0)
    setup = (b(f"CrossBelow(close, BBLower(20, {k}))", df), b(f"CrossAbove(close, BBUpper(20, {k}))", df))
    trigger = (b(f"CrossAbove(close, BBLower(20, {k}))", df), b(f"CrossBelow(close, BBUpper(20, {k}))", df))
    regime = b(ctx, df)
    want = reference(s, df, setup, trigger, (regime, regime))
    expect(s, want)
    both_sides(s)


def test_e3_must_not_fire_in_a_trend_and_the_exits():
    df = bars(1)
    plain = signals(run(T["E3"], df))
    assert set(signals(run(T["E3"], df, adx_threshold=10.0)).items()) < set(plain.items())     # a trending market: fewer / none
    s = run(T["E3"], df)
    ready = np.arange(len(df)) >= s.warmup_bars
    assert (s.exit_long == ((df["high"] >= ev("BBMid(20, 2.0)", df)).to_numpy() & ready)).all()
    o = run(T["E3"], df, exit_at="opposite")
    assert (o.exit_long == ((df["high"] >= ev("BBUpper(20, 2.0)", df)).to_numpy() & ready)).all()
    assert (o.exit_short == ((df["low"] <= ev("BBLower(20, 2.0)", df)).to_numpy() & ready)).all()


# ============================================================================================================== E5
def _e5_series(df):
    return ((b("CrossBelow(close, SessionVWAP())", df), b("CrossAbove(close, SessionVWAP())", df)),
            (b("CrossAbove(close, SessionVWAP())", df), b("CrossBelow(close, SessionVWAP())", df)))


@pytest.mark.parametrize("seed, params", [(1, {"use_trend": False}), (2, {"trend_period": 20}),
                                          (3, {"use_trend": False, "invalidation": "vwap_buffer", "stop_mult": 0.5})])
def test_e5_fires_exactly_on_the_vwap_reclaims_within_one_session(seed, params):
    df = bars(seed)
    s = run(T["E5"], df, **params)
    setup, trigger = _e5_series(df)
    ctx = (None, None)
    if params.get("use_trend", True):
        ctx = (b('close@1h > MA(close@1h, 20, "EMA")@1h', df), b('close@1h < MA(close@1h, 20, "EMA")@1h', df))
    inv = None
    if params.get("invalidation") == "vwap_buffer":
        inv = (ev("SessionVWAP() - 0.5 * ATR(14)[1]", df).to_numpy(), ev("SessionVWAP() + 0.5 * ATR(14)[1]", df).to_numpy())
    want = reference(s, df, setup, trigger, ctx, session=sessions_of(df), inv=inv)
    expect(s, want)
    both_sides(s)


def test_e5_must_not_fire_on_a_cross_from_the_previous_session_or_without_volume():
    df = bars(1)
    s = run(T["E5"], df, use_trend=False)
    session = sessions_of(df)
    assert all(session[s.setup_bar[t]] == session[t] for t in s.signal_bars())
    setup, trigger = _e5_series(df)
    unbound = reference(s, df, setup, trigger)                          # the same walk without the session rule
    crossing = {t for t, v in unbound.items() if session[v[2]] != session[t]}
    assert crossing and not crossing & set(s.signal_bars())             # a cross-under yesterday does not arm today's reclaim
    assert not signals(run(T["E5"], df.assign(volume=0.0), use_trend=False))      # an index: no session VWAP, no signal
    ready = np.arange(len(df)) >= s.warmup_bars
    vwap = ev("SessionVWAP()", df)
    assert (s.exit_long == ((df["close"] < vwap).to_numpy() & ready)).all()
    with pytest.raises(C.TemplateError):
        C.resolve_settings(T["E5"], "1d")                               # session-based: intraday only


# ====================================================================================== shared gate: every new template
CASES = [("C2", {"swing_degree": 0, "retest_reversal": False}, 2), ("C2", {"swing_degree": 0, "invalidation": "swing"}, 3),
         ("D3", {}, 1), ("D3", {"trigger_bar": "same", "ma_type": "SMA"}, 2),
         ("E1", {"context_timeframe": "1h", "trend_filter": "supertrend"}, 1),
         ("E1", {"context_timeframe": "1h", "trend_period": 20, "oversold": 40.0, "overbought": 60.0, "exit_rule": "opposite_trigger"}, 2),
         ("E3", {}, 1), ("E3", {"regime": "structure", "swing_degree": 0, "exit_at": "opposite"}, 2),
         ("E5", {"use_trend": False}, 1), ("E5", {"trend_period": 20, "invalidation": "vwap_buffer"}, 2)]
IDS = [f"{tid}-{i}" for i, (tid, _, _) in enumerate(CASES)]


def whole(df):
    """The bars in whole ticks (x 20: the synthetic bars move in 0.05 steps), so the mirrored prices are exact."""
    out = df.copy()
    out[["open", "high", "low", "close"]] = (df[["open", "high", "low", "close"]] * 20).round()
    return out


# TPL-F6: the swing structure mirrors on ties too (an equal high is not higher, an equal low not lower), so the
# structure regime's case is in the mirror test like every other (before the fix it was left out).
MIRROR = list(CASES)


def _settled(tid, params, df, md):
    """The first bar from which the two runs' context agrees by construction: the Supertrend's first direction is seeded
    long on both series (app/indicators/volatility.supertrend), so a Supertrend filter mirrors from the first bar where the
    two directions are opposite (as the D1 mirror test)."""
    if params.get("trend_filter") != "supertrend":
        return 0
    a, c = (ev("SupertrendDir(10, 3.0)@1h", x).to_numpy() for x in (df, md))
    agree = np.flatnonzero(np.isfinite(a) & (a == -c))
    return int(agree[0]) if len(agree) else len(df)


@pytest.mark.parametrize("tid, params, seed", MIRROR, ids=[f"{c[0]}-{i}" for i, c in enumerate(MIRROR)])
def test_mirror_short_equals_long_on_mirrored_prices(tid, params, seed):
    df = whole(bars(seed))
    axis = K * 20
    md = mirror(df, axis)
    s, m = run(T[tid], df, **params), run(T[tid], md, **params)
    start = _settled(tid, params, df, md)
    assert start < len(df) // 2
    got = {t: d for t, d in signals(s).items() if t >= start}
    assert got, "the fixture must carry signals"
    assert {t: d for t, d in signals(m).items() if t >= start} == {t: -d for t, d in got.items()}
    for t in got:
        assert axis - m.invalidation[t] == pytest.approx(s.invalidation[t])
    assert (m.exit_short == s.exit_long).all() and (m.exit_long == s.exit_short).all()


@pytest.mark.parametrize("tid, params, seed", CASES, ids=IDS)
def test_truncation_no_look_ahead_over_many_t(tid, params, seed):
    df = bars(seed, 700)
    full = run(T[tid], df, **params)
    marks_ = sorted(set(full.signal_bars()) | {t + 1 for t in full.signal_bars()} | set(range(full.warmup_bars, len(df), 53)))
    for t in [x for x in marks_ if x < len(df)]:
        part = run(T[tid], df.iloc[:t + 1], **params)
        np.testing.assert_array_equal(part.direction, full.direction[:t + 1], err_msg=f"t={t}")
        np.testing.assert_allclose(part.invalidation, full.invalidation[:t + 1], equal_nan=True, err_msg=f"t={t}")
        np.testing.assert_array_equal(part.exit_long, full.exit_long[:t + 1], err_msg=f"t={t}")
        np.testing.assert_array_equal(part.exit_short, full.exit_short[:t + 1], err_msg=f"t={t}")
        assert [part.instance_id(i) for i in part.signal_bars()] == [full.instance_id(i) for i in full.signal_bars() if i <= t]


PARITY = [("C2", {"swing_degree": 0, "retest_reversal": False}, 2), ("D3", {}, 1),
          ("E1", {"context_timeframe": "1h", "trend_filter": "supertrend"}, 1), ("E3", {}, 1), ("E5", {"use_trend": False}, 1),
          ("E5", {"trend_period": 20}, 2)]


@pytest.mark.parametrize("tid, params, seed", PARITY, ids=[f"{p[0]}-{i}" for i, p in enumerate(PARITY)])
def test_scan_equals_backtest_equals_options_equals_worker(tid, params, seed):
    strat = TemplateStrategy(T[tid], C.resolve_settings(T[tid], "15m", params=params))
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
    at = sorted({j for i in signal_at for j in (i - 1, i, i + 1) if 0 <= j < len(window)} | set(range(0, len(window), 25)) | {len(window) - 1})
    worker = parity.worker_answers(strat, window, "S", at)
    assert parity.compare(scan, worker, "worker") == []
    assert {ts for ts, a in scan.items() if a.direction} <= {ts for ts, a in worker.items() if a.direction}


def _check_property(s, df):
    """No signal without its trigger on that bar, the extreme since the setup by definition, S6."""
    close = df["close"].to_numpy(dtype=float)
    for t, d in signals(s).items():
        assert (s.invalidation[t] < close[t]) if d > 0 else (s.invalidation[t] > close[t])
        assert s.setup_bar[t] >= 0 and s.setup_bar[t] <= t


@hsettings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(kind=st.sampled_from(["EMA", "SMA", "WMA", "HMA"]), fast=st.integers(3, 30), gap=st.integers(1, 40), slope=st.integers(1, 20),
       same=st.sampled_from(["later", "same"]), within=st.integers(1, 40), seed=st.integers(0, 30))
def test_d3_property_random_valid_settings(kind, fast, gap, slope, same, within, seed):
    df = bars(seed, 400)
    p = {"ma_type": kind, "fast": fast, "slow": fast + gap, "slope_bars": slope, "trigger_bar": same, "pullback_max_bars": within}
    s = run(T["D3"], df, **p)
    _check_property(s, df)
    setup, trigger = _d3_series(df, fast, fast + gap, slope, kind)
    for t, d in signals(s).items():
        i = 0 if d > 0 else 1
        s0 = int(s.setup_bar[t])
        assert trigger[i][t] and setup[i][s0] and t - s0 <= within and (t > s0 or same == "same")
        assert s.invalidation[t] == (df["low"].iloc[s0:t + 1].min() if d > 0 else df["high"].iloc[s0:t + 1].max())


@hsettings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(n=st.integers(3, 30), lo=st.floats(20.0, 45.0), hi=st.floats(55.0, 80.0), filt=st.sampled_from(["average", "supertrend"]),
       seed=st.integers(0, 30))
def test_e1_property_random_valid_settings(n, lo, hi, filt, seed):
    df = bars(seed, 400)
    s = run(T["E1"], df, rsi_period=n, oversold=lo, overbought=hi, trend_filter=filt, context_timeframe="1h", trend_period=10)
    _check_property(s, df)
    setup, trigger = _e1_series(df, n, lo, hi)
    for t, d in signals(s).items():
        assert trigger[0 if d > 0 else 1][t] and setup[0 if d > 0 else 1][s.setup_bar[t]]


@hsettings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(n=st.integers(5, 40), k=st.floats(0.5, 3.0), thr=st.floats(10.0, 40.0), regime=st.sampled_from(["adx", "structure"]),
       seed=st.integers(0, 30))
def test_e3_e5_c2_property_random_valid_settings(n, k, thr, regime, seed):
    df = bars(seed, 400)
    s = run(T["E3"], df, bb_n=n, bb_k=k, adx_threshold=thr, regime=regime, swing_degree=0)
    _check_property(s, df)
    for t, d in signals(s).items():
        band = ev(f"BBLower({n}, {k!r})" if d > 0 else f"BBUpper({n}, {k!r})", df)
        assert (df["close"].iloc[t] > band.iloc[t]) if d > 0 else (df["close"].iloc[t] < band.iloc[t])
    e5 = run(T["E5"], df, use_trend=False, invalidation="vwap_buffer" if regime == "adx" else "session_extreme", stop_mult=k)
    _check_property(e5, df)
    session = sessions_of(df)
    assert all(session[e5.setup_bar[t]] == session[t] for t in e5.signal_bars())
    _check_property(run(T["C2"], df, swing_degree=0, k=k / 3, retest_max_bars=int(thr / 2), retest_reversal=False), df)


@pytest.mark.parametrize("tid", NEW)
def test_library_template_validates_and_its_text_passes_the_guard(tid):
    template = T[tid]
    assert C.validate_template(template) == []
    for tf in template.allowed_timeframes:
        settings = C.resolve_settings(template, tf, params={"context_timeframe": "1w"} if tid == "E1" and tf == "1d" else {})
        for side in ("long", "short"):
            for ctx in (None, "1h", "1d", "1w"):
                text = library.signal_text(template, settings, side, ctx)
                assert text and language.guard(text) == [], text
    for p in template.params:
        assert language.guard(p.label) == [] or p.type in ("bool", "int", "float"), p.key
        if p.type in ("int", "float"):
            assert p.min is not None and p.max is not None and p.min <= p.default <= p.max
        if p.choices and p.type != "timeframe":
            assert len(p.choice_labels) == len(p.choices)


_STRUCTURE = re.compile(r"(== -?1\b)|(\[1\])")


def _texts(template):
    out = []
    for part in (template.setup, template.trigger, template.invalidation, template.exit_signal, template.context):
        if part is None:
            continue
        variants = [v for v in part.variants.values() if v] if hasattr(part, "variants") else [part]
        out += [x for v in variants for x in ((v.long, v.short) if hasattr(v, "long") else ())]
    return out


@pytest.mark.parametrize("tid", NEW)
def test_no_number_is_written_into_a_template_rule(tid):
    assert _texts(T[tid])
    for text in _texts(T[tid]):
        assert not re.search(r"(?<![\w$])\d", _STRUCTURE.sub("", text)), (tid, text)


@pytest.mark.parametrize("tid", NEW)
def test_template_ids_round_trip_and_fit(tid):
    template = T[tid]
    for tf in [t for t in template.allowed_timeframes if t in ENGINE_LABEL]:
        params = {"context_timeframe": "1w"} if tid == "E1" and tf == "1d" else {}
        sid, settings = ids.adopt(template, tf, params=params) if params else ids.adopt(template, tf)
        assert ids.decode(sid) == (template, settings)
    worst = {}
    for p in template.params:
        if p.type == "int":
            worst[p.key] = int(p.max) if p.max != p.default else int(p.min)
        elif p.type == "bool":
            worst[p.key] = not p.default
        elif p.type == "timeframe":
            worst[p.key] = "1w" if "1w" in p.choices else "1d"
        elif p.type != "float":
            worst[p.key] = max((c for c in p.choices if c != p.default), key=len, default=p.default)
    worst.pop("fast", None)                                             # keep fast below slow
    sid, settings = ids.adopt(template, template.allowed_timeframes[0], "short", worst)
    assert len(sid) <= ids.MAX_LENGTH and ids.decode(sid) == (template, settings)


@pytest.mark.parametrize("tid", NEW)
def test_the_adapter_reads_the_signal_with_its_provenance_and_the_flags(tid):
    df = bars({"C2": 2}.get(tid, 1))
    params = {"C2": {"swing_degree": 0, "retest_reversal": False}, "E1": {"context_timeframe": "1h", "trend_filter": "supertrend"},
              "E5": {"use_trend": False}}.get(tid, {})
    strat = TemplateStrategy(T[tid], C.resolve_settings(T[tid], "15m", params=params))
    series = engine.compute(strat.recipe, {"15m": df})
    t = series.signal_bars()[0]
    sig = strat.analyze({ENGINE_LABEL["15m"]: df.iloc[:t + 1]}, "S")
    assert sig.is_tradeable and sig.stop_loss == pytest.approx(series.invalidation[t]) and sig.target1 is None
    assert sig.provenance["template"] == tid and sig.provenance["setup"] == series.instance_id(t)
    assert all(language.guard(r) == [] for r in sig.reasons)
    want = {"C2": {"screener_breakouts", "screener_breakouts_bk4"}, "D3": set(), "E1": set(), "E3": {"screener_breakouts"},
            "E5": {"screener_breakouts"}}[tid]                         # ReversalAt / RSI / MA need no flag; DowTrend / VWAP do
    assert strat.recipe.required_flags == (want if tid != "E3" else set())
    if tid == "E3":
        e3 = TemplateStrategy(T["E3"], C.resolve_settings(T["E3"], "15m", params={"regime": "structure"}))
        assert e3.recipe.required_flags == want


def test_every_new_template_is_paper_only_signals_only():
    for tid in NEW:
        data = T[tid].model_dump(by_alias=True)
        assert not {"target", "targets", "lots", "quantity", "strike", "premium", "order"} & set(data)
        for text in _texts(T[tid]):
            assert not re.search(r"\b(target|lots|strike|premium|order)\b", text, re.I)
    assert pd.Index(NEW).is_unique
