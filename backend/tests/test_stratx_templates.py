"""STRAT-X (owner decision 30 Oct): the white-box templates C6 (previous-day high / low break and retest, a generic
intraday template - not the owner's chart-reading method), B12 (multi-week high breakout, 52 weeks by default) and D9
(Weekly trend, Daily pullback) against the owner order's section 8 gate: must fire long and short / must not (hand-built
near misses), the signal bars equal to an independent walk of ScreenQL series written in the test, the invalidation by
value, the truncation test over many t (no look-ahead), the mirror test, scan = backtest = options backtest = worker on
one trailing window (the TPL-1 harness), property tests on random valid settings, the language guard, "no number
written into a rule", ids, the adapter's provenance and "signals only" (no target, no lots).

The fourth STRAT-X template (relative strength against NIFTY) is not here: the template engine reads one symbol's bars
only (engine.compute / TemplateStrategy.analyze get the symbol's own frames), so a ratio to a second symbol cannot be
written honestly yet (docs/OPEN_QUESTIONS.md STRATX-Q1)."""
import re

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hsettings
from hypothesis import strategies as st

from app.backtest.options_engine import OptionBacktestConfig
from app.core.enums import OptionStrategy
from app.core.models import RiskConfig
from app.quality.lookahead import IST_OPEN_UTC, synthetic_bars
from app.screener.parser import parse
from app.screener.runtime import SymbolData, evaluate
from app.strategy_templates import compile as C
from app.strategy_templates import engine, ids, language, library, parity
from app.strategy_templates.adapter import ENGINE_LABEL, TemplateStrategy
from tests.test_tpl5_templates import _texts, mirror, sessions_of, signals, whole

NEW = ("C6", "B12", "D9")
T = {tid: library.get(tid) for tid in NEW}
TF = {"C6": "15m", "B12": "1d", "D9": "1d"}
RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5)


def ev(text, df, tf=None):
    """A ScreenQL series on the bars (Daily bars read as Daily, else 15-minute)."""
    tf = tf or ("1d" if (df.index[1] - df.index[0]) >= pd.Timedelta(days=1) else "15m")
    return evaluate(parse(text), SymbolData("S", {tf: df}), base_tf=tf)


def b(text, df):
    return ev(text, df).fillna(False).to_numpy(dtype=bool)


def run(tid, df, sides="both", **params):
    template = T[tid]
    return engine.compute(C.compile_recipe(template, C.resolve_settings(template, TF[tid], sides, params)), {TF[tid]: df})


def bars(tid, seed, n=None):
    return synthetic_bars(n or (900 if TF[tid] == "15m" else 1300), tf=TF[tid], seed=seed)


def walk(s, df, setup, trigger, context=(None, None), *, expiry=None, same_bar=False, session=None, arm=None, inv=None):
    """An independent walk of S4 / S5 / S6 / S7 / R8 over boolean series written in the test: event setups (the newest
    replaces the older), the arm level fixed on the setup bar and voided by a close through it (S7), the expiry, the
    session, the trigger on a later bar (or the same one with same_bar), the context on the trigger bar, conflicts, and
    the invalidation = the extreme since the setup bar (inv None) or the given series. {bar: (side, level, setup bar)}."""
    low, high, close = (df[c].to_numpy(dtype=float) for c in ("low", "high", "close"))
    n = len(df)
    ctx = [np.ones(n, dtype=bool) if c is None else c for c in context]
    armed, ext, lv, want = {1: -1, -1: -1}, {1: np.nan, -1: np.nan}, {1: np.nan, -1: np.nan}, {}
    for t in range(s.warmup_bars, n):
        fired = {}
        for i, sign in enumerate((1, -1)):
            if setup[i][t]:
                if session is not None and session[t] < 0:
                    armed[sign] = -1
                else:
                    armed[sign], ext[sign] = t, (low[t] if sign > 0 else high[t])
                    lv[sign] = np.nan if arm is None else float(arm[i][t])
            elif armed[sign] >= 0:
                s0 = armed[sign]
                breached = np.isfinite(lv[sign]) and (close[t] < lv[sign] if sign > 0 else close[t] > lv[sign])
                if (expiry is not None and t - s0 > expiry) or (session is not None and session[t] != session[s0]) or breached:
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
        if np.isfinite(level) and ((sign > 0 and level < close[t]) or (sign < 0 and level > close[t])):
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


# ============================================================================================================== C6
def _c6_series(df, k=0.0, n=14, touch=0.1, confirm="touch"):
    atr = f"ATR({n})[1]"
    setup = (b(f"CrossAbove(close, PDH() + {k!r} * {atr})", df), b(f"CrossBelow(close, PDL() - {k!r} * {atr})", df))
    if confirm == "touch":
        trigger = (b(f"low <= PDH() + {touch!r} * {atr} AND low[1] > PDH() + {touch!r} * {atr} AND close > PDH()", df),
                   b(f"high >= PDL() - {touch!r} * {atr} AND high[1] < PDL() - {touch!r} * {atr} AND close < PDL()", df))
    else:
        trigger = (b('ReversalAt(PDH(), "bullish") AND close > PDH()', df), b('ReversalAt(PDL(), "bearish") AND close < PDL()', df))
    arm = (ev("PDH()", df).to_numpy(), ev("PDL()", df).to_numpy())
    return setup, trigger, arm


@pytest.mark.parametrize("seed, params", [(0, {}), (1, {}), (2, {"retest_confirm": "reversal"}), (3, {"k": 0.1, "touch_atr": 0.3}),
                                          (1, {"invalidation": "since_break", "retest_max_bars": 5})])
def test_c6_fires_exactly_on_the_retests_of_the_previous_day_level_within_one_session(seed, params):
    df = bars("C6", seed)
    s = run("C6", df, **params)
    setup, trigger, arm = _c6_series(df, params.get("k", 0.0), 14, params.get("touch_atr", 0.1), params.get("retest_confirm", "touch"))
    inv = None
    if params.get("invalidation", "level_buffer") == "level_buffer":
        inv = (ev("PDH() - 0.5 * ATR(14)[1]", df).to_numpy(), ev("PDL() + 0.5 * ATR(14)[1]", df).to_numpy())
    want = walk(s, df, setup, trigger, expiry=params.get("retest_max_bars", 12), session=sessions_of(df), arm=arm, inv=inv)
    expect(s, want)
    assert want


def test_c6_must_fire_long_and_short_over_the_seeds():
    got = set()
    for seed in range(4):
        got |= set(signals(run("C6", bars("C6", seed))).values())
    assert got == {1, -1}


def _session(day, rows):
    """15-minute NSE bars of one session from (open, high, low, close) rows (the rest of the session flat at the last close)."""
    start = pd.Timestamp(day, tz="UTC") + IST_OPEN_UTC
    rows = list(rows) + [(rows[-1][3],) * 4] * (25 - len(rows))
    idx = pd.DatetimeIndex([start + pd.Timedelta(minutes=15 * i) for i in range(25)])
    out = pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close"])
    out["volume"] = 1000.0
    return out


QUIET = [(105.0, 110.0, 100.0, 105.0)] + [(105.0, 106.0, 104.0, 105.0)] * 24          # the previous day: high 110, low 100
BREAK = (105.0, 111.5, 104.5, 111.0)                                                  # a close above the previous day's high
AWAY = [(111.0, 114.0, 112.0, 113.5), (113.5, 114.0, 112.5, 113.0)]                   # the market leaves the level
RETEST = (113.0, 113.0, 110.05, 111.0)                                                # back to the level; the close holds above it


def _c6_fixture(day2, day3=None):
    frames = [_session("2026-01-05", QUIET), _session("2026-01-06", QUIET), _session("2026-01-07", day2)]
    if day3 is not None:
        frames.append(_session("2026-01-08", day3))
    return pd.concat(frames)


def _at(df, day, i):
    return int(df.index.get_loc(pd.Timestamp(day, tz="UTC") + IST_OPEN_UTC + pd.Timedelta(minutes=15 * i)))


def test_c6_must_fire_on_the_hand_built_retest_and_its_mirror():
    day = [QUIET[1]] * 3 + [BREAK, *AWAY, RETEST]
    df = _c6_fixture(day)
    s = run("C6", df, atr_n=1)
    t = _at(df, "2026-01-07", 6)
    assert signals(s) == {t: 1} and s.setup_bar[t] == _at(df, "2026-01-07", 3)
    assert s.invalidation[t] == pytest.approx(110.0 - 0.5 * float(ev("ATR(1)[1]", df).iloc[t]))
    m = run("C6", mirror(df, 400.0), atr_n=1)
    assert signals(m) == {t: -1} and 400.0 - m.invalidation[t] == pytest.approx(s.invalidation[t])


@pytest.mark.parametrize("day, day3, why", [
    ([QUIET[1]] * 3 + [BREAK, *AWAY, (113.0, 113.0, 109.0, 109.5)], None, "the close went back through the level"),
    ([QUIET[1]] * 3 + [BREAK, *AWAY, (113.0, 113.0, 109.0, 109.5), RETEST], None, "a later retest of a void break"),
    ([QUIET[1]] * 3 + [BREAK, RETEST], None, "the market never left the level (the bar before was still at it)"),
    ([QUIET[1]] * 3 + [BREAK] + [AWAY[1]] * 13 + [RETEST], None, "the retest came after the bars set"),
    ([QUIET[1]] * 21 + [BREAK, *AWAY], [RETEST], "the retest came in the next session"),
    ([QUIET[1]] * 3 + [(105.0, 110.5, 104.5, 110.0)] + [(110.0, 110.4, 108.0, 109.0)] * 3, None, "the close only reached the level"),
])
def test_c6_must_not_fire_on_the_near_misses(day, day3, why):
    df = _c6_fixture(day, day3)
    assert signals(run("C6", df, atr_n=1)) == {}, why


def test_c6_exit_is_a_setting_and_the_template_is_intraday_only():
    df = bars("C6", 1)
    assert not run("C6", df).exit_long.any()
    s = run("C6", df, exit_rule="level")
    ready = np.arange(len(df)) >= s.warmup_bars
    assert (s.exit_long == ((df["close"] < ev("PDH()", df)).to_numpy() & ready)).all()
    assert (s.exit_short == ((df["close"] > ev("PDL()", df)).to_numpy() & ready)).all()
    with pytest.raises(C.TemplateError):
        C.resolve_settings(T["C6"], "1d")


# ============================================================================================================== B12
def _b12_series(df, weeks=52, v=1.5, k=0.2):
    return (b(f'NewHighBreakout("up", {weeks}, {v!r}, {k!r})', df), b(f'NewHighBreakout("down", {weeks}, {v!r}, {k!r})', df))


@pytest.mark.parametrize("seed, params", [(0, {}), (1, {"vol_mult": 0.0}), (2, {"high_weeks": 26, "k": 0.0}),
                                          (3, {"vol_mult": 0.0, "invalidation": "breakout_bar"}), (1, {"swing_degree": 0})])
def test_b12_fires_exactly_on_the_multi_week_breakouts(seed, params):
    df = bars("B12", seed)
    s = run("B12", df, **params)
    setup = _b12_series(df, params.get("high_weeks", 52), params.get("vol_mult", 1.5), params.get("k", 0.2))
    inv = None
    if params.get("invalidation", "swing") == "swing":
        deg = params.get("swing_degree", 1)
        inv = (ev(f"SwingLow({deg})", df).to_numpy(), ev(f"SwingHigh({deg})", df).to_numpy())
    want = walk(s, df, setup, setup, same_bar=True, inv=inv)
    expect(s, want)
    assert want


def test_b12_must_fire_long_and_short_over_the_seeds():
    got = set()
    for seed in range(4):
        got |= set(signals(run("B12", bars("B12", seed), vol_mult=0.0)).values())
    assert got == {1, -1}


def _yearly(break_close=104.0, break_volume=5000.0, n=300):
    """Daily bars: a year in a band whose top is 100 (one bar touches it), then one bar that closes beyond it."""
    idx = pd.bdate_range("2025-01-01", periods=n + 1, tz="UTC")
    rng = np.random.default_rng(4)
    mid = 95.0 + np.round(rng.normal(0.0, 1.0, n).clip(-3, 3) * 20) / 20
    o, c = mid, mid + 0.5
    h, lo = np.maximum(o, c) + 1.0, np.minimum(o, c) - 1.0
    h[n // 2] = 100.0
    df = pd.DataFrame({"open": np.r_[o, 99.0], "high": np.r_[h, break_close + 0.5], "low": np.r_[lo, 98.5],
                       "close": np.r_[c, break_close], "volume": np.r_[np.full(n, 1000.0), break_volume]}, index=idx)
    return df


def test_b12_must_fire_on_the_hand_built_breakout_and_its_mirror():
    df = _yearly()
    s = run("B12", df, invalidation="breakout_bar")
    assert signals(s) == {len(df) - 1: 1} and s.invalidation[len(df) - 1] == 98.5
    m = run("B12", mirror(df, 300.0), invalidation="breakout_bar")
    assert signals(m) == {len(df) - 1: -1} and m.invalidation[len(df) - 1] == pytest.approx(300.0 - 98.5)


@pytest.mark.parametrize("df, params, why", [
    (_yearly(break_volume=1000.0), {}, "no volume expansion"),
    (_yearly(break_close=100.2), {}, "the close is not far enough beyond the high"),
    (_yearly(break_close=99.5), {"k": 0.0}, "the close stayed below the high"),
    (_yearly(n=200), {"vol_mult": 0.0}, "less history than the weeks looked back"),
    (_yearly().assign(volume=0.0), {}, "an index has no volume: the volume condition never holds"),
])
def test_b12_must_not_fire_on_the_near_misses(df, params, why):
    assert signals(run("B12", df, invalidation="breakout_bar", **params)) == {}, why


def test_b12_exit_is_a_setting_and_the_template_is_daily_only():
    df = bars("B12", 1)
    assert not run("B12", df).exit_long.any()
    s = run("B12", df, exit_rule="swing")
    ready = np.arange(len(df)) >= s.warmup_bars
    assert (s.exit_long == ((df["close"] < ev("SwingLow(1)", df)).to_numpy() & ready)).all()
    assert T["B12"].sides_default == "long" and T["B12"].allowed_timeframes == ("1d",)
    with pytest.raises(C.TemplateError):
        C.resolve_settings(T["B12"], "15m")


# ============================================================================================================== D9
def _d9_series(df, by="average", period=30, slope=4, degree=0, pull=20, kind="EMA"):
    m = f'MA(close, {pull}, "{kind}")'
    setup = (b(f"low <= {m} AND low[1] > {m}[1]", df), b(f"high >= {m} AND high[1] < {m}[1]", df))
    trigger = (b(f'ReversalAt({m}, "bullish") AND close > {m}', df), b(f'ReversalAt({m}, "bearish") AND close < {m}', df))
    if by == "average":
        w = f'MA(close@1w, {period}, "{kind}")@1w'
        ctx = (b(f"close@1w > {w} AND {w} > Lag({w}, {slope})@1w", df), b(f"close@1w < {w} AND {w} < Lag({w}, {slope})@1w", df))
    else:
        ctx = (b(f'DowTrend({degree})@1w == "UP"', df), b(f'DowTrend({degree})@1w == "DOWN"', df))
    return setup, trigger, ctx


@pytest.mark.parametrize("seed, params", [(0, {}), (1, {}), (2, {"trend_by": "structure"}), (3, {"ma_type": "SMA", "trend_period": 20,
                                                                                                  "slope_bars": 2, "pullback_period": 10})])
def test_d9_fires_exactly_on_daily_pullbacks_with_the_weekly_trend(seed, params):
    df = bars("D9", seed)
    s = run("D9", df, **params)
    setup, trigger, ctx = _d9_series(df, params.get("trend_by", "average"), params.get("trend_period", 30), params.get("slope_bars", 4), 0,
                                     params.get("pullback_period", 20), params.get("ma_type", "EMA"))
    want = walk(s, df, setup, trigger, ctx, expiry=10)
    expect(s, want)
    both_sides(s)


def test_d9_must_not_fire_against_the_weekly_trend():
    df = bars("D9", 1)
    s = run("D9", df)
    setup, trigger, ctx = _d9_series(df)
    anywhere = walk(s, df, setup, trigger, expiry=10)                  # the same walk without the Weekly trend
    against = {t for t, v in anywhere.items() if not ctx[0 if v[0] > 0 else 1][t]}
    assert against and not against & set(s.signal_bars())
    for t, d in signals(s).items():
        assert ctx[0 if d > 0 else 1][t] and s.setup_bar[t] < t
    assert set(signals(run("D9", df, pullback_max_bars=1)).items()) < set(signals(s).items())
    with pytest.raises(C.TemplateError):
        C.resolve_settings(T["D9"], "15m")


def test_d9_exit_is_a_setting():
    df = bars("D9", 1)
    assert not run("D9", df).exit_long.any()
    s = run("D9", df, exit_rule="pullback_average")
    ready = np.arange(len(df)) >= s.warmup_bars
    assert (s.exit_long == ((df["close"] < ev('MA(close, 20, "EMA")', df)).to_numpy() & ready)).all()


# ====================================================================================== shared gate: every new template
CASES = [("C6", {}, 0), ("C6", {"retest_confirm": "reversal", "invalidation": "since_break"}, 1),
         ("B12", {"vol_mult": 0.0}, 1), ("B12", {"invalidation": "breakout_bar", "high_weeks": 26}, 2),
         ("D9", {}, 1), ("D9", {"trend_by": "structure"}, 2)]
IDS = [f"{tid}-{i}" for i, (tid, _, _) in enumerate(CASES)]


@pytest.mark.parametrize("tid, params, seed", CASES, ids=IDS)
def test_mirror_short_equals_long_on_mirrored_prices(tid, params, seed):
    df = whole(bars(tid, seed))
    axis = 400.0 * 20
    md = mirror(df, axis)
    s, m = run(tid, df, **params), run(tid, md, **params)
    got = signals(s)
    assert got, "the fixture must carry signals"
    assert signals(m) == {t: -d for t, d in got.items()}
    for t in got:
        assert axis - m.invalidation[t] == pytest.approx(s.invalidation[t])
    assert (m.exit_short == s.exit_long).all() and (m.exit_long == s.exit_short).all()


@pytest.mark.parametrize("tid, params, seed", CASES, ids=IDS)
def test_truncation_no_look_ahead_over_many_t(tid, params, seed):
    df = bars(tid, seed)
    full = run(tid, df, **params)
    step = 61 if TF[tid] == "15m" else 97
    marks_ = sorted(set(full.signal_bars()) | {t + 1 for t in full.signal_bars()} | set(range(full.warmup_bars, len(df), step)))
    for t in [x for x in marks_ if x < len(df)]:
        part = run(tid, df.iloc[:t + 1], **params)
        np.testing.assert_array_equal(part.direction, full.direction[:t + 1], err_msg=f"t={t}")
        np.testing.assert_allclose(part.invalidation, full.invalidation[:t + 1], equal_nan=True, err_msg=f"t={t}")
        np.testing.assert_array_equal(part.exit_long, full.exit_long[:t + 1], err_msg=f"t={t}")
        np.testing.assert_array_equal(part.exit_short, full.exit_short[:t + 1], err_msg=f"t={t}")
        assert [part.instance_id(i) for i in part.signal_bars()] == [full.instance_id(i) for i in full.signal_bars() if i <= t]


PARITY = [("C6", {}, 0), ("C6", {"retest_confirm": "reversal"}, 1), ("B12", {"vol_mult": 0.0}, 1), ("D9", {}, 1)]


@pytest.mark.parametrize("tid, params, seed", PARITY, ids=[f"{p[0]}-{i}" for i, p in enumerate(PARITY)])
def test_scan_equals_backtest_equals_options_equals_worker(tid, params, seed):
    tf = TF[tid]
    strat = TemplateStrategy(T[tid], C.resolve_settings(T[tid], tf, "both", params))
    window = bars(tid, seed).iloc[-(500 if tf == "15m" else 1000):].copy()          # a fixed TRAILING window (R6)
    window[["open", "high", "low", "close"]] *= 240.0                                # index-like prices for the option ladder
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
    assert {ts for ts, a in scan.items() if a.direction} <= {ts for ts, a in worker.items() if a.direction}


def _check_property(s, df):
    close = df["close"].to_numpy(dtype=float)
    for t, d in signals(s).items():
        assert (s.invalidation[t] < close[t]) if d > 0 else (s.invalidation[t] > close[t])
        assert 0 <= s.setup_bar[t] <= t


@hsettings(max_examples=8, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(k=st.floats(0.0, 1.0), n=st.integers(1, 30), touch=st.floats(0.0, 1.0), within=st.integers(1, 30),
       confirm=st.sampled_from(["touch", "reversal"]), seed=st.integers(0, 30))
def test_c6_property_random_valid_settings(k, n, touch, within, confirm, seed):
    df = bars("C6", seed, 500)
    s = run("C6", df, k=k, atr_n=n, touch_atr=touch, retest_max_bars=within, retest_confirm=confirm)
    _check_property(s, df)
    setup, trigger, _ = _c6_series(df, k, n, touch, confirm)
    session = sessions_of(df)
    for t, d in signals(s).items():
        i, s0 = (0 if d > 0 else 1), int(s.setup_bar[t])
        assert trigger[i][t] and setup[i][s0] and 0 < t - s0 <= within and session[s0] == session[t]


@hsettings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(weeks=st.integers(4, 60), v=st.sampled_from([0.0, 1.2, 2.0]), k=st.floats(0.0, 1.0), seed=st.integers(0, 30))
def test_b12_property_random_valid_settings(weeks, v, k, seed):
    df = bars("B12", seed, 600)
    s = run("B12", df, high_weeks=weeks, vol_mult=v, k=k, invalidation="breakout_bar")
    _check_property(s, df)
    setup = _b12_series(df, weeks, v, k)
    for t, d in signals(s).items():
        assert setup[0 if d > 0 else 1][t] and s.setup_bar[t] == t


@hsettings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(kind=st.sampled_from(["EMA", "SMA", "WMA", "HMA"]), period=st.integers(2, 40), slope=st.integers(1, 10), pull=st.integers(2, 40),
       within=st.integers(1, 20), by=st.sampled_from(["average", "structure"]), seed=st.integers(0, 30))
def test_d9_property_random_valid_settings(kind, period, slope, pull, within, by, seed):
    df = bars("D9", seed, 800)
    s = run("D9", df, ma_type=kind, trend_period=period, slope_bars=slope, pullback_period=pull, pullback_max_bars=within, trend_by=by)
    _check_property(s, df)
    setup, trigger, ctx = _d9_series(df, by, period, slope, 0, pull, kind)
    for t, d in signals(s).items():
        i, s0 = (0 if d > 0 else 1), int(s.setup_bar[t])
        assert trigger[i][t] and ctx[i][t] and setup[i][s0] and 0 < t - s0 <= within
        assert s.invalidation[t] == (df["low"].iloc[s0:t + 1].min() if d > 0 else df["high"].iloc[s0:t + 1].max())


@pytest.mark.parametrize("tid", NEW)
def test_library_template_validates_and_its_text_passes_the_guard(tid):
    template = T[tid]
    assert C.validate_template(template) == []
    for tf in template.allowed_timeframes:
        settings = C.resolve_settings(template, tf)
        for side in ("long", "short"):
            for ctx in (None, "1h", "1d", "1w"):
                text = library.signal_text(template, settings, side, ctx)
                assert text and language.guard(text) == [], text
    for p in template.params:
        if p.type in ("int", "float"):
            assert p.min is not None and p.max is not None and p.min <= p.default <= p.max
        if p.choices and p.type != "timeframe":
            assert len(p.choice_labels) == len(p.choices)
    assert template.style in ("Swing", "Intraday (session-based)")


@pytest.mark.parametrize("tid", NEW)
def test_no_number_is_written_into_a_template_rule(tid):
    assert _texts(T[tid])
    structure = re.compile(r"(== -?1\b)|(\[1\])")
    for text in _texts(T[tid]):
        assert not re.search(r"(?<![\w$])\d", structure.sub("", text)), (tid, text)


@pytest.mark.parametrize("tid", NEW)
def test_template_ids_round_trip_and_fit(tid):
    template = T[tid]
    tf = TF[tid]
    assert tf in ENGINE_LABEL
    sid, settings = ids.adopt(template, tf)
    assert ids.decode(sid) == (template, settings)
    worst = {}
    for p in template.params:
        if p.type == "int":
            worst[p.key] = int(p.max) if p.max != p.default else int(p.min)
        elif p.type == "float":
            worst[p.key] = float(p.max)
        elif p.type != "timeframe":
            worst[p.key] = max((c for c in p.choices if c != p.default), key=len, default=p.default)
    sid, settings = ids.adopt(template, tf, "short", worst)
    assert len(sid) <= ids.MAX_LENGTH and ids.decode(sid) == (template, settings)


@pytest.mark.parametrize("tid", NEW)
def test_the_adapter_reads_the_signal_with_its_provenance_and_the_flags(tid):
    df = bars(tid, 1)
    params = {"B12": {"vol_mult": 0.0}}.get(tid, {})
    strat = TemplateStrategy(T[tid], C.resolve_settings(T[tid], TF[tid], params=params))
    series = engine.compute(strat.recipe, {TF[tid]: df})
    t = series.signal_bars()[0]
    sig = strat.analyze({ENGINE_LABEL[TF[tid]]: df.iloc[:t + 1]}, "S")
    assert sig.is_tradeable and sig.stop_loss == pytest.approx(series.invalidation[t])
    assert sig.target1 is None and sig.target2 is None and sig.risk_reward is None        # exits are the user's own settings
    assert sig.provenance["template"] == tid and sig.provenance["setup"] == series.instance_id(t)
    assert all(language.guard(r) == [] for r in sig.reasons)
    want = {"C6": set(), "B12": {"screener_breakouts"}, "D9": set()}[tid]       # PDH / MA / ReversalAt need no flag
    assert strat.recipe.required_flags == want
    if tid == "D9":
        assert TemplateStrategy(T["D9"], C.resolve_settings(T["D9"], "1d", params={"trend_by": "structure"})).recipe.required_flags == {
            "screener_breakouts"}


def test_every_new_template_is_paper_only_signals_only_and_listed():
    for tid in NEW:
        data = T[tid].model_dump(by_alias=True)
        assert not {"target", "targets", "lots", "quantity", "strike", "premium", "order"} & set(data)
        for text in _texts(T[tid]):
            assert not re.search(r"\b(target|lots|strike|premium|order)\b", text, re.I)
        for word in ("profit", "win rate", "returns", "guarantee", "accuracy"):
            assert word not in T[tid].idea.lower() and all(word not in p.help.lower() for p in T[tid].params)
    listed = {t.id: t for t in library.templates()}
    assert set(NEW) <= set(listed)
    assert {tid: listed[tid].style for tid in NEW} == {"C6": "Intraday (session-based)", "B12": "Swing", "D9": "Swing"}
