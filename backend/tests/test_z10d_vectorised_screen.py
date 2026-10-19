"""Z10-d: the vectorised screen (app/screener/panel.py) gives the per-symbol runtime's answers, only faster.

- every kernel equals pandas bit for bit (rolling mean / std / max / min / count, exponential means), with gaps,
  leading gaps, repeated values and negative values, on both of its paths (row by row, and pandas for few symbols);
- every vectorised function's series equals the per-symbol series for each symbol, bit for bit;
- a seeded fuzz of screens over a mixed universe (two bar indexes, a symbol alone, one without bars, one too short)
  gives the same matches and reasons;
- a screen it does not cover, and a group whose evaluation fails, go through the per-symbol runtime unchanged;
- the result cache: keys, TTL, bound, copies;
- the operator flag `screener_vectorized` is off by default and reaches the screener run and the bar-close engine.
"""
import asyncio
import random

import numpy as np
import pandas as pd
import pytest

from app.screener import compile_screen, panel, parse
from app.screener.result_cache import ResultCache, cache_key, namespace
from app.screener.runtime import Match, SymbolData, evaluate, run_screen


def _walk(seed, index, vol=0.02, gaps=0.0):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, vol, len(index))))
    open_ = np.r_[close[0], close[:-1]]
    df = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.003, "low": np.minimum(open_, close) * 0.997, "close": close,
                       "volume": rng.integers(100, 10_000, len(index)).astype(float)}, index=index)
    if len(df) > 26:
        df.iloc[20:26, df.columns.get_loc("close")] = df["close"].iloc[20]      # a flat stretch (repeated values)
    if gaps:
        mask = rng.random(df.shape) < gaps
        df = df.mask(mask)
    return df


IDX_A = pd.bdate_range("2025-01-01", periods=260, tz="UTC")
IDX_B = pd.bdate_range("2025-03-03", periods=240, tz="UTC")


def _matrix(seed, rows=260, cols=40):
    rng = np.random.default_rng(seed)
    x = np.cumsum(rng.normal(0, 1, (rows, cols)), axis=0)               # crosses zero: negative values too
    x[30:40, 2] = 7.0                                                     # repeated values
    x[50:52, 3] = -0.0
    return x


def _with_gaps(x, seed, share=0.03):
    y = x.copy()
    y[np.random.default_rng(seed).random(x.shape) < share] = np.nan
    return y


def _leading(x, k=9):
    y = x.copy()
    y[:k] = np.nan
    return y


VARIANTS = [("dense", lambda x: x), ("gaps", lambda x: _with_gaps(x, 5)), ("leading", _leading), ("leading+gaps", lambda x: _with_gaps(_leading(x), 6))]


@pytest.mark.parametrize("variant", [v[0] for v in VARIANTS])
@pytest.mark.parametrize("cols", [40, 400])                               # 40 x 260: pandas path; 400: row by row
def test_every_kernel_equals_pandas_bit_for_bit(variant, cols):
    x = dict(VARIANTS)[variant](_matrix(1, cols=cols))
    df = pd.DataFrame(x)
    for w in (1, 2, 5, 20, 100, 300):
        np.testing.assert_array_equal(panel._rolling_mean(x, w), df.rolling(w).mean().to_numpy(), err_msg=f"mean {w}")
        np.testing.assert_array_equal(panel._rolling_std(x, w), df.rolling(w).std().to_numpy(), err_msg=f"std {w}")
        np.testing.assert_array_equal(panel._rolling_std(x, w, ddof=0), df.rolling(w).std(ddof=0).to_numpy(), err_msg=f"std0 {w}")  # AMW-3
        np.testing.assert_array_equal(panel._rolling_extreme(x, w, np.maximum), df.rolling(w).max().to_numpy(), err_msg=f"max {w}")
        np.testing.assert_array_equal(panel._rolling_extreme(x, w, np.minimum), df.rolling(w).min().to_numpy(), err_msg=f"min {w}")
    for span in (2, 5, 9, 20, 200):
        np.testing.assert_array_equal(panel._ewm_mean(x, span=span, min_periods=span), df.ewm(span=span, adjust=False, min_periods=span).mean().to_numpy())
    for period in (3, 7, 14):
        np.testing.assert_array_equal(panel._ewm_mean(x, alpha=1 / period, min_periods=period),
                                      df.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().to_numpy())
    flags = (x > 0).astype(float)
    for w in (1, 3, 20, 300):
        np.testing.assert_array_equal(panel._rolling_count(flags, w), pd.DataFrame(flags).rolling(w).sum().to_numpy())


FUNCTION_CASES = [
    "SMA(close, 20)", "SMA(volume, 5)", "EMA(close, 9)", "EMA(close, 50)", "RSI(14)", "RSI(3)", "ATR(14)", "ATR(5)", "ADX(14)", "PlusDI(7)",
    "MinusDI(14)", "Max(high, 20)", "Min(low, 52)", "Greatest(open, close, high)", "Least(open, close)", "Count(10, close > open)",
    "CountStreak(close > close[1])", "CrossAbove(EMA(close, 9), EMA(close, 21))", "CrossBelow(close, SMA(close, 20))",
    "PctChange(close, 5)", "ZScore(close, 20)", "ZScore(volume, 50)", "Lag(close, 3)", "EMA(PctChange(close, 1), 10)",
    "SMA(close - open, 10)", "close[2] / close - 1", "Max(close, 20)[3]",
    "StDev(close, 20)", "StDev(volume, 1)", "StDev(close - open, 5)", "StDev(RSI(14), 10)",            # AMW-3 (population stdev)
]


@pytest.mark.parametrize("gaps", [0.0, 0.02])
@pytest.mark.parametrize("text", FUNCTION_CASES)
def test_every_vectorised_function_equals_the_per_symbol_series(text, gaps):
    data = [SymbolData(f"S{i}", {"1d": _walk(i, IDX_A, gaps=gaps)}) for i in range(6)]
    expr = parse(text)
    ev = panel._PanelEval(panel.Panel([d.frames["1d"] for d in data], [d.symbol for d in data]), "1d", {})
    got = ev.ev(expr, "1d")
    for d in data:
        want = evaluate(expr, d, base_tf="1d")
        col = got[d.symbol]
        if want.dtype == bool:
            assert col.dtype == bool and (col.to_numpy() == want.to_numpy()).all(), (text, d.symbol)
        else:
            np.testing.assert_array_equal(col.to_numpy(dtype=float), want.to_numpy(dtype=float), err_msg=f"{text} {d.symbol}")


ATOMS = ["close", "open", "high", "low", "close[1]", "SMA(close, {w})", "EMA(close, {w})", "Max(high, {w})", "Min(low, {w})",
         "Lag(close, {k})", "ATR({p})", "Greatest(open, close)", "Least(open, close)"]
SCALARS = ["RSI({p})", "ADX({p})", "PlusDI({p})", "MinusDI({p})", "ZScore(close, {w})", "PctChange(close, {k})", "Count({w}, close > open)",
           "CountStreak(close > open)"]


def _random_screen(rng):
    def fill(t):
        return t.format(w=rng.choice([2, 5, 10, 20, 50]), k=rng.choice([1, 3, 5]), p=rng.choice([5, 14]))

    def condition():
        kind = rng.random()
        if kind < 0.45:
            a, b = fill(rng.choice(ATOMS)), fill(rng.choice(ATOMS))
            return f"{a} {rng.choice(['>', '>=', '<', '<='])} {b}{rng.choice(['', ' * 1.01', ' * 0.99'])}"
        if kind < 0.75:
            return f"{fill(rng.choice(SCALARS))} {rng.choice(['>', '<', '>='])} {rng.choice([-1, 0, 1, 2, 30, 50, 70])}"
        if kind < 0.85:
            return f"{fill(rng.choice(SCALARS))} BETWEEN {rng.choice([-2, 0, 20])} AND {rng.choice([2, 60, 80])}"
        return rng.choice(["CrossAbove(EMA(close, 5), EMA(close, 20))", "CrossBelow(close, SMA(close, 10))", "NOT close > open",
                           "close > open OR volume > SMA(volume, 10)"])
    parts = [condition() for _ in range(rng.randint(1, 5))]
    return f" {rng.choice(['AND', 'OR'])} ".join(f"({p})" for p in parts)


def _mixed_universe(gaps=0.0):
    data = [SymbolData(f"A{i}", {"1d": _walk(i, IDX_A, gaps=gaps)}) for i in range(8)]
    data += [SymbolData(f"B{i}", {"1d": _walk(50 + i, IDX_B, gaps=gaps)}) for i in range(5)]
    data.insert(3, SymbolData("ALONE", {"1d": _walk(99, pd.bdate_range("2025-02-03", periods=200, tz="UTC"))}))
    data.insert(6, SymbolData("NOBARS", {"1d": pd.DataFrame()}))
    data.append(SymbolData("SHORT", {"1d": _walk(98, pd.bdate_range("2025-12-01", periods=8, tz="UTC"))}))
    data.append(SymbolData("SHORT2", {"1d": _walk(97, pd.bdate_range("2025-12-01", periods=8, tz="UTC"))}))
    data.append(SymbolData("A0", {"1d": _walk(0, IDX_A, gaps=gaps)}))                       # a repeated symbol
    return data


def _as_tuples(matches):
    return [(m.symbol, m.matched, m.reason) for m in matches]


@pytest.mark.parametrize("gaps", [0.0, 0.01])
def test_seeded_fuzz_same_matches_and_reasons(gaps):
    rng = random.Random(20261011)
    data = _mixed_universe(gaps)
    covered = 0
    for _ in range(60):
        text = _random_screen(rng)
        ast, validated = compile_screen(text, base_tf="1d", cost_cap=1e9)
        assert validated.ok, (text, validated.problems)
        covered += panel.supported(ast, validated, "1d")
        for check in (True, False):
            want = run_screen(ast, validated, data, base_tf="1d", check_history=check)
            got = run_screen(ast, validated, data, base_tf="1d", check_history=check, vectorized=True)
            assert _as_tuples(got) == _as_tuples(want), text
    assert covered == 60                                                       # the fuzz only uses vectorised functions


@pytest.mark.parametrize("text", ["close > SMA(close, 5)@1w", "Rank(close) <= 3", "Trend() == \"UPTREND\"", "close > VWAP()",
                                  "Supertrend(10, 3) < close", "PatternBullish()"])
def test_a_screen_it_does_not_cover_runs_per_symbol_unchanged(text):
    data = _mixed_universe()
    ast, validated = compile_screen(text, base_tf="1d", cost_cap=1e9)
    assert validated.ok, validated.problems
    assert not panel.supported(ast, validated, "1d")
    assert _as_tuples(run_screen(ast, validated, data, base_tf="1d", vectorized=True)) == _as_tuples(run_screen(ast, validated, data, base_tf="1d"))


def test_a_group_that_fails_falls_back_to_per_symbol_reasons(monkeypatch):
    data = _mixed_universe()
    ast, validated = compile_screen("close > SMA(close, 20)", base_tf="1d")

    def boom(self, node, tf):
        raise RuntimeError("vectorised path broke")
    monkeypatch.setattr(panel._PanelEval, "ev", boom)
    assert _as_tuples(run_screen(ast, validated, data, base_tf="1d", vectorized=True)) == _as_tuples(run_screen(ast, validated, data, base_tf="1d"))


def test_a_constant_screen_and_an_empty_universe():
    data = _mixed_universe()
    for text in ("1 > 0", "2 < 1"):
        ast, validated = compile_screen(text, base_tf="1d")
        assert _as_tuples(run_screen(ast, validated, data, base_tf="1d", vectorized=True)) == _as_tuples(run_screen(ast, validated, data, base_tf="1d"))
    ast, validated = compile_screen("close > 1", base_tf="1d")
    assert run_screen(ast, validated, [], base_tf="1d", vectorized=True) == []


def test_symbols_are_grouped_by_identical_index_in_universe_order():
    data = _mixed_universe()
    groups = panel._groups(data, "1d")
    flat = sorted(i for g in groups for i in g)
    assert flat == [i for i, d in enumerate(data) if d.symbol != "NOBARS"]
    by_symbol = [[data[i].symbol for i in g] for g in groups]
    assert ["A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A0"] in by_symbol and ["ALONE"] in by_symbol and ["SHORT", "SHORT2"] in by_symbol


# --- result cache ---------------------------------------------------------------------------------------------------

def test_the_result_cache_key_follows_the_namespace_the_screen_the_universe_and_every_bar():
    data = _mixed_universe()
    ast, validated = compile_screen("close > SMA(close, 20)", base_tf="1d")
    other, _ = compile_screen("close > SMA(close, 21)", base_tf="1d")
    ns = namespace(1, source="broker:upstox")
    base = cache_key(ns, ast, "1d", {}, data)
    assert base == cache_key(ns, ast, "1d", {}, data)
    assert base != cache_key(namespace(2, source="broker:upstox"), ast, "1d", {}, data)                  # another organisation
    assert base != cache_key(namespace(1, source="lake:eod"), ast, "1d", {}, data)                        # another source / tier
    assert cache_key(namespace(1, user_id=7, per_user=True, source="broker:upstox"), ast, "1d", {}, data) != \
        cache_key(namespace(1, user_id=8, per_user=True, source="broker:upstox"), ast, "1d", {}, data)    # BT1: per user
    assert namespace(1, user_id=7, source="s") == namespace(1, source="s")                                # shared tier: the user does not split it
    with pytest.raises(ValueError):
        namespace(1, per_user=True, source="s")
    with pytest.raises(ValueError):
        cache_key((), ast, "1d", {}, data)
    assert base != cache_key(ns, other, "1d", {}, data)
    assert base != cache_key(ns, ast, "1d", {"n": 1}, data)
    assert base != cache_key(ns, ast, "1d", {}, data[:-1])
    for row in (-1, 5):                                                       # a revised last bar, and a revised OLD bar
        revised = [SymbolData(d.symbol, {"1d": d.frames["1d"].copy()}) for d in data]
        frame = revised[0].frames["1d"]
        frame.iloc[row, frame.columns.get_loc("close")] += 0.01
        assert base != cache_key(ns, ast, "1d", {}, revised), row
    stamped = cache_key(ns, ast, "1d", {}, data, as_of="2026-10-09")
    assert stamped == cache_key(ns, ast, "1d", {}, revised, as_of="2026-10-09") != cache_key(ns, ast, "1d", {}, data, as_of="2026-10-12")


def test_the_result_cache_serves_copies_until_the_ttl_and_stays_bounded():
    data = _mixed_universe()
    ast, validated = compile_screen("close > SMA(close, 20)", base_tf="1d")
    ns = namespace(1, source="broker:upstox")
    cache = ResultCache(max_entries=2)
    first, hit = cache.run(ast, validated, data, namespace=ns, base_tf="1d", vectorized=True)
    again, hit2 = cache.run(ast, validated, data, namespace=ns, base_tf="1d", vectorized=True)
    assert (hit, hit2) == (False, True) and _as_tuples(first) == _as_tuples(again) == _as_tuples(run_screen(ast, validated, data, base_tf="1d"))
    assert (cache.hits, cache.misses) == (1, 1)
    assert cache.run(ast, validated, data, namespace=namespace(2, source="broker:upstox"), base_tf="1d")[1] is False   # never across tenants
    again[0].matched = not again[0].matched                                  # a caller changing its copy changes nothing
    assert _as_tuples(cache.run(ast, validated, data, namespace=ns, base_tf="1d")[0]) == _as_tuples(first)
    with pytest.raises(TypeError):
        cache.run(ast, validated, data, base_tf="1d")                                                     # the namespace is required
    cache.put("k", [Match("X", True)], ttl_seconds=10, now=100.0)
    assert cache.get("k", now=105.0)[0].matched and cache.get("k", now=111.0) is None
    for key in ("a", "b", "c"):
        cache.put(key, [], ttl_seconds=60)
    assert len(cache._entries) == 2 and cache.get("a") is None


# --- the flag -------------------------------------------------------------------------------------------------------

def test_the_flag_is_off_by_default_and_reaches_the_run_and_the_bar_close_engine(monkeypatch):
    from app.alerts import barclose
    from app.platform import controls
    from app.screener import routes
    assert panel.FLAG in controls.FEATURE_FLAGS and panel.FLAG in controls.DEFAULT_OFF_FLAGS
    assert routes.VECTOR_FLAG == barclose.VECTOR_FLAG == panel.FLAG
    seen = []
    real = barclose.run_screen

    def spy(*args, **kwargs):
        seen.append(kwargs.get("vectorized"))
        return real(*args, **kwargs)
    monkeypatch.setattr(barclose, "run_screen", spy)
    from tests.test_s4a_barclose import TUE, FakeFetch, _bars, _evaluate, _rule, ist
    from tests.test_phase_l_ai import _owner
    _, me = _owner("z10d-flag@example.com")
    rid = _rule(me["tenant_id"])
    _evaluate(rid, ist(TUE, 9, 27), FakeFetch({"TCS": _bars(ist(TUE, 9, 15), [99, 105, 50])}))
    assert seen == [False]                                                    # evaluate_rule's default: per symbol

    async def flagged():
        from tests.test_auth_api import _session_factory
        async with _session_factory() as session:
            return await controls.flag_enabled(session, panel.FLAG, me["tenant_id"])
    assert asyncio.run(flagged()) is False


def test_a_missing_column_is_missing_for_a_field_and_an_error_for_an_indicator_as_per_symbol():
    data = [SymbolData(f"A{i}", {"1d": _walk(i, IDX_A)}) for i in range(4)]
    data[1] = SymbolData("A1", {"1d": data[1].frames["1d"].drop(columns=["high"])})
    for text in ("high > 0", "ATR(14) > 0", "close > SMA(close, 5)"):
        ast, validated = compile_screen(text, base_tf="1d")
        assert _as_tuples(run_screen(ast, validated, data, base_tf="1d", vectorized=True)) == _as_tuples(run_screen(ast, validated, data, base_tf="1d")), text


def test_a_group_fallback_is_logged_as_a_warning_with_the_exception_type(monkeypatch, caplog):
    data = _mixed_universe()
    ast, validated = compile_screen("close > SMA(close, 20)", base_tf="1d")

    def boom(self, node, tf):
        raise ZeroDivisionError("kernel broke")
    monkeypatch.setattr(panel._PanelEval, "ev", boom)
    with caplog.at_level("WARNING", logger="app.screener.panel"):
        run_screen(ast, validated, data, base_tf="1d", vectorized=True)
    warnings = [r for r in caplog.records if r.name == "app.screener.panel" and r.levelname == "WARNING"]
    assert warnings and all("ZeroDivisionError: kernel broke" in r.getMessage() for r in warnings)


# --- U6 BK1: breakouts and the daily context ------------------------------------------------------------------------

IDX_5M = pd.DatetimeIndex([d + pd.Timedelta(hours=3, minutes=45) + pd.Timedelta(minutes=5 * i)
                           for d in pd.bdate_range("2026-02-02", periods=12, tz="UTC") for i in range(75)])
IDX_1D = pd.bdate_range("2025-01-01", periods=290, tz="UTC") + pd.Timedelta(hours=3, minutes=45)


def _with_daily(n_symbols=6):
    """Symbols the way the bar-close engine hands them over since BK1: the screen's 5m bars plus a daily context frame."""
    return [SymbolData(f"D{i}", {"5m": _walk(200 + i, IDX_5M, vol=0.003), "1d": _walk(300 + i, IDX_1D)}) for i in range(n_symbols)]


@pytest.mark.parametrize("text", ['RangeBreakout("up")', 'RangeBreakout("down", 10) OR close > SMA(close, 20)', "RelVolume(20) > 1",
                                  'NRBreakout("up")', "ATRPercentile(14, 100) > 20", 'DowTrend() == "UP"',
                                  'close > SMA(close, 20) AND SwingBreakout("up")'])
def test_breakout_functions_run_per_symbol_under_the_flag(text):
    data = _with_daily()
    ast, validated = compile_screen(text, base_tf="5m", cost_cap=1e9)
    assert validated.ok, validated.problems
    assert not panel.supported(ast, validated, "5m")
    assert _as_tuples(run_screen(ast, validated, data, base_tf="5m", vectorized=True)) == _as_tuples(run_screen(ast, validated, data, base_tf="5m"))


@pytest.mark.parametrize("text", ["close > SMA(close, 20) AND RSI(14) > 30", "close > EMA(close, 20)@1d", "close > Max(high, 4)@1w",
                                  "CrossAbove(EMA(close, 9), EMA(close, 21)) OR volume > SMA(volume, 20)"])
def test_the_daily_context_frame_changes_nothing_on_either_path(text):
    """A 5m-only screen stays vectorised (the daily frame is not read); one that reads 1d / 1w uses the daily context
    per symbol, exactly as without the flag."""
    data = _with_daily()
    ast, validated = compile_screen(text, base_tf="5m", cost_cap=1e9)
    assert validated.ok, validated.problems
    assert panel.supported(ast, validated, "5m") == ("@" not in text)
    assert _as_tuples(run_screen(ast, validated, data, base_tf="5m", vectorized=True)) == _as_tuples(run_screen(ast, validated, data, base_tf="5m"))


def test_the_bar_close_engine_fires_the_same_with_and_without_the_flag():
    """BK1's cycle cache (lookback per timeframe) and daily attach, then the screen: vectorised or not, the same events."""
    import json as _json

    from app.alerts import barclose
    from app.db.models import AlertEventRecord, AlertRuleRecord, ScreenRecord
    from app.market_data.calendar import IST
    from app.screener import nodes as n
    from sqlalchemy import select
    from tests.test_auth_api import _session_factory
    from tests.test_phase_l_ai import _owner

    data = _with_daily(8)
    frames = {d.symbol: d.frames for d in data}
    last = IDX_5M[-1].tz_convert(IST)
    now = last.to_pydatetime().replace(hour=15, minute=31)
    _, me = _owner("z10d-bk1-barclose@example.com")
    text = "close > SMA(close, 20) AND RSI(14) > 30"

    async def fetch(session, tenant_id, symbols, exchange, base_tf, lookback, **kw):
        return [barclose.SymbolData(s, dict(frames[s])) for s in symbols if s in frames], {}, "synthetic"

    async def go():
        async with _session_factory() as session:
            ast, _ = compile_screen(text, base_tf="5m")
            screen = ScreenRecord(tenant_id=me["tenant_id"], name="z10d", source_text=text, ast_json=_json.dumps(n.to_json(ast)), base_tf="5m",
                                  params_json="{}")
            session.add(screen)
            await session.flush()
            rules = []
            for name in ("per-symbol", "vectorised"):
                rule = AlertRuleRecord(tenant_id=me["tenant_id"], name=name, kind="screen", screen_id=screen.id, universe_json=_json.dumps(list(frames)),
                                       base_tf="5m", priority="normal", cooldown_minutes=0, mode="instant", digest_every="hourly", status="active",
                                       created_at=now, updated_at=now)
                session.add(rule)
                rules.append(rule)
            await session.flush()
            states = [await barclose.evaluate_rule(session, rule, now, fetch, (), barclose.CycleCache(), vectorized=(rule.name == "vectorised"))
                      for rule in rules]
            await session.commit()
            events = []
            for rule in rules:
                rows = await session.scalars(select(AlertEventRecord).where(AlertEventRecord.rule_id == rule.id).order_by(AlertEventRecord.symbol))
                events.append([(e.symbol, e.values_json) for e in rows])
            return states, events
    states, (plain, vectorised) = asyncio.run(go())
    assert [s for s, _ in states] == ["done", "done"] and states[0][1] == states[1][1] > 0
    assert plain == vectorised
